"""控制器的标定、单次触发、线程停止及真实协议到 CSV 的回归测试。"""
import csv
from dataclasses import asdict
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from controller.algorithms.calibration import Calibration, RAW_FIELDS, CALIBRATED_FIELDS
from controller.algorithms.threshold import ThresholdSettings, threshold_reached
from controller.worker import FootController
from drivers.nianfujiao_song.protocol import (
    BOARD_ARM, BOARD_COMMIT, BOARD_DISARM, GPIO_WRITTEN, PendingCommand,
)
from test_nianfujiao_protocol import device, feed, wire, channels, pairs
from core.sensor_manager import SensorManager
from core.data_recorder import DataRecorder


def frame(sequence, x1=1500, x2=1600):
    raw = [0] * 24
    raw[2] = raw[14] = x1
    raw[9] = raw[15] = x2
    return {'raw': raw, 'sample_sequence': sequence, 'ready_us': sequence * 16000,
            'host_received_ns': time.perf_counter_ns()}


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError('未在时限内收到预期控制器事件')


class CalibrationTests(unittest.TestCase):
    def test_all_models_match_original_calibration_including_sign_branches(self):
        from drivers.nianfujiao_song import utils
        model = Calibration()
        for value in (-32768, -1200, -1, 0, 1, 1200, 32767):
            result = model.apply([value] * 24, rounded=True)['calibrated']
            for name in CALIBRATED_FIELDS:
                self.assertEqual(result[RAW_FIELDS.index(name)], getattr(utils, 'cal_' + name)(value))

    def test_total_threshold_is_inclusive_and_does_not_take_absolute_value(self):
        settings = ThresholdSettings(3.0)
        for force, expected in ((3.0, True), (2.99999, False), (4, True), (-4, False)):
            self.assertEqual(threshold_reached({'fz_total_cal': force}, settings), expected)
        for values in ((float('nan'), 1), (float('inf'), 1), (-1, 1), (1, 2)):
            with self.assertRaises(ValueError):
                ThresholdSettings(*values)

    def test_joint_force_uses_raw_inputs_and_reference_sign_without_legacy_scaling(self):
        result = Calibration().apply(frame(1, 1493, 967)['raw'])
        # 实验5253行的固定联合模型结果；不乘旧模型的9.8或1.6。
        self.assertAlmostEqual(result['fz_total_reference_n'], -2.5920250181976336)
        self.assertAlmostEqual(result['fz_total_cal'], 2.5920250181976336)
        tension = Calibration().apply(frame(2, -2000, -2000)['raw'])
        self.assertLess(tension['fz_total_cal'], 0)
        self.assertFalse(threshold_reached(tension, ThresholdSettings()))

    def test_old_settings_migrate_to_one_initial_total_threshold(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'settings.json'
            path.write_text(json.dumps({'threshold_fz1_n': 1.2, 'threshold_fz2_n': 1.8,
                                        'trigger_action': 0}), encoding='utf-8')
            migrated = ThresholdSettings.load(path)
            self.assertEqual(migrated, ThresholdSettings(3.0, 0))
            migrated.save(path)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),
                             asdict(ThresholdSettings(3.0, 0)))

    def test_settings_persist_and_custom_calibration_loads(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = ThresholdSettings(2.1, 0)
            path = Path(temp) / 'settings.json'
            settings.save(path)
            self.assertEqual(ThresholdSettings.load(path), settings)
            cfg = Calibration().snapshot()
            cfg['model_id'] = 'experiment_custom'
            cfg['coefficients']['Fz1_plus'] = [0, 1, 0]
            cfg_path = Path(temp) / 'calibration.json'
            cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
            result = Calibration(cfg_path).apply(frame(1)['raw'])
            self.assertAlmostEqual(result['fz1_total_cal'], 1500 / 1000 * 9.8 * 1.6)
            self.assertEqual(result['calibration_id'], 'experiment_custom')
            cfg['coefficients']['Fz1_plus'][0] = float('nan')
            cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
            with self.assertRaises(ValueError):
                Calibration(cfg_path)
            cfg = Calibration().snapshot()
            cfg['normal_force']['coefficients'][1] = float('nan')
            cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '总法向力'):
                Calibration(cfg_path)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.sent = []
        self.early_ack = True
        self.ack_status = 0

        def send(action, data, input_ns):
            pending = PendingCommand(action, len(self.sent) + 1, data['sample_sequence'],
                                     data['ready_us'], input_ns, time.perf_counter_ns(), 0)
            self.sent.append(pending)
            if self.early_ack:
                self.controller.on_feedback({**pending.to_dict(), 'status': self.ack_status,
                                             'flags': GPIO_WRITTEN if action < 2 else 0,
                                             'gpio_changed': action < 2})
            return pending

        self.controller = FootController(send, self.events.append, settings=ThresholdSettings())
        self.addCleanup(self.controller.stop)

    def decisions(self):
        return [row for row in self.events if row['event'] == 'decision']

    def put(self, sequence, x1=1500, x2=1600):
        count = len(self.decisions())
        self.assertTrue(self.controller.enqueue(frame(sequence, x1, x2)))
        wait_for(lambda: len(self.decisions()) > count)

    def test_bound_new_frames_single_trigger_hold_and_rearm(self):
        self.assertFalse(self.controller.enqueue(frame(1)))
        self.controller.start(3)
        self.put(1, 100, 1300)
        self.put(2)
        self.assertEqual(self.controller.snapshot()['state'], 'triggered')
        self.assertFalse(self.controller.enqueue(frame(2)))
        self.put(3)
        self.assertEqual([pending.action for pending in self.sent], [2, 1, 2])
        self.assertEqual([pending.sample_sequence for pending in self.sent], [1, 2, 3])
        for row in self.decisions():
            self.assertGreaterEqual(row['controller_us'], 0)
            self.assertLessEqual(row['input_received_ns'], row['processing_started_ns'])
            self.assertLessEqual(row['processing_started_ns'], row['submitted_ns'])
        self.controller.start(3)
        self.put(4)
        self.assertEqual(self.sent[-1].action, 1)

    def test_new_threshold_and_low_output_apply_to_new_inputs(self):
        self.controller.start(3)
        self.controller.configure(ThresholdSettings(10, 0))
        self.put(1)
        self.assertEqual(self.sent[-1].action, 2)
        self.controller.configure(ThresholdSettings(2, 0))
        self.put(2)
        self.assertEqual(self.sent[-1].action, 0)
        self.assertEqual(self.decisions()[-1]['settings_revision'], 3)
        self.assertEqual(self.controller.snapshot()['state'], 'triggered')

    def test_total_force_can_trigger_with_one_old_per_chip_force_below_threshold(self):
        self.controller.start(3)
        self.put(1, 0, 2000)
        self.assertEqual(self.sent[-1].action, 1)
        row = self.decisions()[-1]
        self.assertGreater(row['fz_total_n'], 3)
        self.assertEqual(row['threshold_total_n'], 3)
        self.assertAlmostEqual(row['fz_total_reference_n'], -row['fz_total_n'])
        self.assertIn('normal_force_model_id', row)

    def test_ack_after_send_returns_and_no_second_trigger_while_waiting(self):
        self.early_ack = False
        self.controller.start(3)
        self.put(1)
        self.assertEqual(self.controller.snapshot()['state'], 'waiting_ack')
        self.put(2)
        self.assertEqual([pending.action for pending in self.sent], [1, 2])
        self.controller.on_feedback({**self.sent[0].to_dict(), 'status': 0,
                                     'flags': GPIO_WRITTEN, 'gpio_changed': False})
        self.assertEqual(self.controller.snapshot()['state'], 'triggered')
        self.assertIn('无电平变化', self.controller.snapshot()['message'])

    def test_rejected_ack_stops_without_overwriting_error(self):
        self.ack_status = 2
        self.controller.start(3)
        self.put(1)
        status = self.controller.snapshot()
        self.assertFalse(status['active'])
        self.assertEqual(status['state'], 'error')
        self.assertFalse(self.controller.enqueue(frame(2)))

    def test_missing_trigger_ack_times_out(self):
        self.early_ack = False
        self.controller.ACK_TIMEOUT_NS = 20_000_000
        self.controller.start(3)
        self.put(1)
        wait_for(lambda: self.controller.snapshot()['state'] == 'error')
        self.assertFalse(self.controller.snapshot()['active'])

    def test_generation_change_and_stale_input_do_not_actuate(self):
        self.controller.start(3)
        data = frame(1)
        data['host_received_ns'] -= 600_000_000
        self.controller.enqueue(data)
        wait_for(lambda: any(row['event'] == 'stale_input' for row in self.events))
        self.assertFalse(self.sent)
        self.controller.observe_generation(4)
        self.assertFalse(self.controller.snapshot()['active'])
        self.assertFalse(self.controller.enqueue(frame(2)))

    def test_stop_waits_for_inflight_send_and_discards_queued_decisions(self):
        entered = threading.Event()
        release = threading.Event()
        original = self.controller._send_action

        def blocked_send(*args):
            entered.set()
            release.wait(1)
            return original(*args)

        self.controller._send_action = blocked_send
        self.controller.start(3)
        self.controller.enqueue(frame(1, 100, 100))
        self.assertTrue(entered.wait(1))
        self.controller.enqueue(frame(2))
        self.controller.request_stop()
        release.set()
        self.controller.stop()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0].action, 2)
        self.assertFalse(self.controller.snapshot()['active'])

    def test_send_failure_is_visible_and_stops_worker(self):
        self.controller._send_action = lambda *args: None
        self.controller.start(3)
        self.controller.enqueue(frame(1))
        wait_for(lambda: self.controller.snapshot()['state'] == 'error')
        self.assertIn('发送失败', self.controller.snapshot()['message'])

    def test_overload_keeps_recent_frames_and_parameters_are_captured_on_enqueue(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        original = self.controller._send_action

        def blocked_send(*args):
            if not entered.is_set():
                entered.set()
                release.wait(1)
            return original(*args)

        self.controller._send_action = blocked_send
        self.controller.configure(ThresholdSettings(100, 1))
        self.controller.start(3)
        self.controller.enqueue(frame(1))
        self.assertTrue(entered.wait(1))
        for sequence in range(2, 14):
            self.controller.enqueue(frame(sequence))
        self.controller.configure(ThresholdSettings(0, 0))
        self.controller.enqueue(frame(14))
        release.set()
        wait_for(lambda: any(row.get('sample_sequence') == 14 for row in self.decisions()))
        self.assertEqual(self.controller.snapshot()['dropped_inputs'], 5)
        self.assertEqual([pending.sample_sequence for pending in self.sent], [1, *range(7, 15)])
        self.assertEqual([pending.action for pending in self.sent], [*([2] * 8), 0])

    def test_reload_stops_worker_and_invalid_model_preserves_existing_model(self):
        self.controller.start(3)
        previous = self.controller.calibration
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bad.json'
            path.write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                self.controller.reload_calibration(path)
            self.assertIs(self.controller.calibration, previous)
            self.assertTrue(self.controller.snapshot()['active'])
        self.controller.reload_calibration()
        self.assertFalse(self.controller.snapshot()['active'])
        self.assertIsNot(self.controller.calibration, previous)


class ControllerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def make_manager(self):
        manager = SensorManager(str(ROOT / 'config' / 'default.yaml'))
        manager.nianfujiao = device()
        manager.nianfujiao.register_callback(manager._on_nianfujiao_callback)
        manager.connection_status['nianfujiao'] = True
        manager.controller.configure(ThresholdSettings(2.5))
        self.addCleanup(manager.disconnect_nianfujiao)
        self.addCleanup(manager.display_timer.stop)
        self.revision = 0
        self.generation = 3
        self.strategy = 1
        self.state = 0

        def respond(packet):
            magic, action, command_id, value = struct.unpack('<BBHI', packet[7:15])
            if magic == 0xC2:
                if action == BOARD_COMMIT:
                    self.revision += 1
                    self.strategy = value & 0xff
                elif action == BOARD_ARM:
                    self.assertEqual(value, (self.revision << 16) | self.generation)
                    self.state = 1
                elif action == BOARD_DISARM:
                    self.state = 0
                payload = struct.pack('<4B2H5I2H', 1, 0, action, 0, command_id, 0,
                                      0, self.revision, 0, 0, 0, self.generation, 0)
                feed(manager.nianfujiao, wire(0x125, payload))
            elif magic == 0xC1 and action == 3:
                self.state = 0

        manager.nianfujiao.serial_conn.on_write = respond
        self.feed_status(manager)
        return manager

    def feed_status(self, manager, sequence=1, ready=100000, trigger=0):
        payload = struct.pack('<4B2H5I3f2I', 1, self.strategy, self.state, int(self.state == 2),
                              self.generation, self.revision, sequence, ready,
                              trigger, ready, ready + 120, 3.5, 4.0, 2.5, 118,
                              manager.controller.model['model_tag'])
        feed(manager.nianfujiao, wire(0x126, payload))

    def test_board_status_and_config_to_csv_without_host_frame_commands(self):
        manager = self.make_manager()
        dev = manager.nianfujiao
        manager.controller.configure(ThresholdSettings(2.5, strategy=2))
        with tempfile.TemporaryDirectory() as temp:
            recorder = DataRecorder(temp)
            manager.record_callback_nianfujiao_aux = recorder.write_nianfujiao_aux
            manager.record_callback_nianfujiao = recorder.write_nianfujiao_data
            recorder.start_recording()
            try:
                feed(dev, wire(0x124, pairs()))
                manager.start_controller()
                self.feed_status(manager)
                recorder.write_controller_snapshot(manager.controller.snapshot())
                self.assertEqual(dev.serial_conn.writes[-1][7:9], bytes((0xC2, BOARD_ARM)))
                before = len(dev.serial_conn.writes)
                feed(dev, wire(0x123, channels(2, 200000)))
                self.state = 2
                self.feed_status(manager, 2, 200000, 2)
                manager._update_display()
                manager._update_display()
                feed(dev, wire(0x123, channels(3, 216000)))
                self.assertEqual(len(dev.serial_conn.writes), before)
                manager.controller.stop()
            finally:
                recorder.stop_recording()
            path = Path(recorder.session_dir)

            def rows(kind):
                with (path / f'nianfujiao_{kind}.csv').open(encoding='utf-8', newline='') as handle:
                    return list(csv.DictReader(handle))

            self.assertEqual([row['event'] for row in rows('controller')],
                             ['board_config', 'board_arm', 'board_disarm'])
            self.assertEqual(len(rows('board')), 2)
            self.assertEqual(rows('board')[-1]['trigger_latency_us'], '120')
            self.assertEqual(rows('board')[-1]['strategy'], '2')
            self.assertEqual(len(rows('frames')), 2)
            snapshot = json.loads((path / 'controller_config.json').read_text(encoding='utf-8'))
            self.assertEqual(snapshot['settings']['threshold_total_n'], 2.5)
            self.assertIn('coefficients', snapshot['calibration'])
            self.assertIn('normal_force', snapshot['calibration'])
            for row in rows('frames'):
                self.assertEqual(row['normal_force_model_id'], manager.controller.model['calibration']['model_id'])

    def test_manual_zero_stops_controller_and_requires_new_baseline_before_start(self):
        manager = self.make_manager()
        dev = manager.nianfujiao
        feed(dev, wire(0x124, pairs()))
        manager.start_controller()
        self.feed_status(manager)
        self.assertTrue(manager.controller.snapshot()['active'])
        self.assertTrue(manager.nianfujiao_zero())
        self.feed_status(manager)
        self.assertFalse(manager.controller.snapshot()['active'])
        with self.assertRaisesRegex(ValueError, '清零'):
            manager.start_controller()
        payload = bytearray(pairs(2, 200000))
        struct.pack_into('<H', payload, 24, 4)
        self.generation = 4
        feed(dev, wire(0x124, payload))
        manager.start_controller()
        self.feed_status(manager)
        self.assertTrue(manager.controller.snapshot()['active'])
        manager.disconnect_nianfujiao()
        self.assertFalse(manager.controller.snapshot()['active'])

    def test_gui_applies_thresholds_and_start_stop_without_real_hardware(self):
        from gui.main_window import MainWindow
        window = MainWindow(str(ROOT / 'config' / 'default.yaml'))
        try:
            panel = window.control_panel
            self.assertFalse(panel.controller_start_btn.isEnabled())
            panel.threshold_total.setValue(2.4)
            panel.trigger_action.setCurrentIndex(0)
            with patch.object(ThresholdSettings, 'save') as save:
                panel.controller_apply_btn.click()
                save.assert_called_once()
            self.assertEqual(window.sensor_manager.controller.settings, ThresholdSettings(2.4, 0))
            self.assertIn('等待板端', panel.controller_applied_label.text())
            self.assertIn('参数已保存', window.statusBar().currentMessage())
            raw = frame(1, 1493, 967)['raw']
            values = Calibration().apply(raw, rounded=True)
            window.data_display_panel.update_nianfujiao_data(0, dict(values, raw=raw))
            self.assertIn('2.5920', window.data_display_panel.nianfujiao_widget.total_force_label.text())
            with patch.object(window.sensor_manager, 'start_controller') as start, \
                    patch.object(ThresholdSettings, 'save'):
                panel.update_button_states({'nianfujiao': True})
                panel.controller_start_btn.click()
                start.assert_called_once()
            window._on_controller_stop()
            self.assertFalse(window.sensor_manager.controller.snapshot()['active'])
        finally:
            window.sensor_manager.controller.stop()
            window.sensor_manager.display_timer.stop()
            window.log_widget.stop_capture()
            window.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
