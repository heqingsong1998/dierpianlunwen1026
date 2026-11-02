"""IMU1 合成动力学、真实协议到 CSV 和界面路由；不连接硬件。"""
import csv
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.imu_velocity import ImuVelocityEstimator, VelocitySettings, body_to_world
from core.data_recorder import DataRecorder, IMU1_COLUMNS
from drivers.HWT606_song_1.chs.jy901s_device import JY901SDevice


def sample(seq, acceleration=(0,0,0), angles=(0,0,0), bias=(0,0,0), chip=True):
    g = 9.80665
    raw = body_to_world(angles).T @ (np.asarray(acceleration)+[0,0,g])+bias
    data = {'sample_sequence':seq, 'timestamp_ms':1700000000000+seq*10,
            'host_received_ns':10**12, 'chip_time_ms':seq*10 if chip else None,
            'frame_complete':True}
    for keys, values in [(('acc_x','acc_y','acc_z'), raw/g),
                         (('gyro_x','gyro_y','gyro_z'), (0.,0.,0.)),
                         (('angle_x','angle_y','angle_z'), angles)]:
        data.update(zip(keys, map(float, values)))
    return data


def calibrated(angles=(0,0,0), bias=(0,0,0)):
    estimator = ImuVelocityEstimator()
    estimator.request_zero()
    for seq in range(1, 211):
        result = estimator.update(sample(seq, angles=angles, bias=bias))
    assert result['velocity_valid'], result
    return estimator


def packet(kind, payload):
    prefix = bytes([0x55,kind])+payload
    return prefix+bytes([sum(prefix)&255])


def wire_cycle(seq, acceleration=0., omit_gyro=False):
    ms = seq*10
    hour, rest = divmod(ms, 3_600_000)
    minute, rest = divmod(rest, 60_000)
    second, ms = divmod(rest, 1000)
    cycle = packet(0x50, bytes([26,11,2,hour,minute,second])+struct.pack('<H',ms))
    cycle += packet(0x51, struct.pack('<hhhh',round(acceleration/9.80665/16*32768),0,2048,2500))
    if not omit_gyro:
        cycle += packet(0x52, struct.pack('<hhhh',0,0,0,0))
    return cycle+packet(0x53, struct.pack('<hhhh',0,0,0,1))


class VelocityAlgorithmTests(unittest.TestCase):
    def test_tilted_rest_bias_compensation_and_rotation_without_linear_motion(self):
        angles = (30, -20, 125)
        bias = (.025, -.015, .01)
        estimator = calibrated(angles, bias)
        for seq in range(211, 411):
            result = estimator.update(sample(seq, angles=angles, bias=bias))
        np.testing.assert_allclose([result['vel_'+axis] for axis in 'xyz'],0,atol=1e-12)
        # 任意姿态下，按当前姿态旋转重力；不把重力投影积分成速度。
        for seq in range(411, 511):
            result = estimator.update(sample(seq, angles=(60,30,125), bias=bias))
        self.assertLess(result['speed_mps'],1e-12)
        np.testing.assert_allclose([result['bias_'+axis] for axis in 'xyz'],bias,atol=1e-12)

    def test_accelerate_then_coast_preserves_speed_and_uses_initial_heading(self):
        estimator = calibrated(angles=(0,0,90))
        for seq in range(211,311):
            result = estimator.update(sample(seq,acceleration=(0,.1,0),angles=(0,0,90)))
        for seq in range(311, 511):
            result = estimator.update(sample(seq,angles=(0,0,90)))
        self.assertAlmostEqual(result['vel_x'],.1,places=10)
        self.assertAlmostEqual(result['vel_y'],0.,places=10)
        self.assertAlmostEqual(result['speed_mps'],.1,places=10)
        self.assertEqual(result['integration_time_source'],'chip')

    def test_needs_explicit_zero_and_moving_calibration_does_not_finish(self):
        estimator = ImuVelocityEstimator()
        result = estimator.update(sample(1))
        self.assertFalse(result['velocity_valid']); self.assertIsNone(result['vel_x'])
        estimator.request_zero()
        for seq in range(2,302):
            data = sample(seq); data['gyro_z'] = 8.
            result = estimator.update(data)
        self.assertEqual(result['velocity_status'],'hold_still')
        self.assertFalse(result['velocity_valid'])

    def test_duplicate_gap_invalid_time_and_rezero_lifecycle(self):
        estimator = calibrated()
        self.assertIsNone(estimator.update(sample(210)))
        result = estimator.update(sample(212))
        self.assertEqual(result['velocity_status'],'gap_requires_zero')
        self.assertIsNone(result['speed_mps'])
        self.assertFalse(estimator.update(sample(213))['velocity_valid'])
        estimator.request_zero()
        for seq in range(214,425):
            result = estimator.update(sample(seq))
        self.assertTrue(result['velocity_valid'])
        self.assertEqual(result['velocity_generation'],2)
        result = estimator.update(dict(sample(425),chip_time_ms=None))
        self.assertFalse(result['velocity_valid'])

    def test_chip_midnight_wrap_and_explicit_nominal_time_fallback(self):
        estimator = ImuVelocityEstimator()
        estimator.update(dict(sample(1),chip_time_ms=86_399_995))
        result = estimator.update(dict(sample(2),chip_time_ms=5))
        self.assertAlmostEqual(result['integration_dt_s'],.01)
        estimator = ImuVelocityEstimator(VelocitySettings(time_source='nominal'))
        estimator.request_zero()
        for seq in range(1,211):
            result = estimator.update(sample(seq,chip=False))
        self.assertTrue(result['velocity_valid'])
        self.assertEqual(result['integration_time_source'],'nominal')


class ImuPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_split_real_packets_once_per_cycle_and_incomplete_cycle_is_invalid(self):
        device = JY901SDevice(port='MOCK')
        snapshots = []
        device.register_callback(lambda model:snapshots.append(dict(model.imu_sample)))
        payload = wire_cycle(1,.1)+wire_cycle(2)+wire_cycle(3,omit_gyro=True)
        for value in payload:
            device.device.onDataReceived(bytes([value]))
        self.assertEqual([row['sample_sequence'] for row in snapshots],[1,2,3])
        self.assertEqual([row['chip_time_ms'] for row in snapshots],[10,20,30])
        self.assertEqual([row['frame_complete'] for row in snapshots],[True,True,False])
        self.assertEqual(snapshots[0]['acc_x'],21/32768*16)
        self.assertIsNot(device.device.protocolResolver.TempBytes,
                         JY901SDevice(port='OTHER').device.protocolResolver.TempBytes)

    def test_protocol_to_velocity_csv_without_six_axis_and_gui_does_not_duplicate(self):
        from gui.main_window import MainWindow
        window = MainWindow(str(ROOT/'config/default.yaml'))
        manager = window.sensor_manager
        device = JY901SDevice(port='MOCK')
        manager.hwt606_1 = device
        manager.connection_status['hwt606_1'] = True
        device.register_callback(lambda model:manager._on_imu1_sample(device,model))
        with tempfile.TemporaryDirectory() as temp:
            window.data_recorder.base_dir = temp
            try:
                with patch('gui.main_window.QMessageBox.information'):
                    window._on_start_recording()
                self.assertTrue(window.data_recorder.is_recording)
                path = Path(window.data_recorder.session_dir)
                manager.zero_imu1_velocity()
                # 模拟串口集中交付：接收时刻几乎相同，积分仍用芯片周期。
                with patch('time.perf_counter_ns', return_value=10**12):
                    for seq in range(1, 311):
                        device.device.onDataReceived(wire_cycle(seq, .1 if seq >= 211 else 0.))
                latest = manager.latest_data['hwt606_1']
                self.assertTrue(latest['velocity_valid'])
                self.assertAlmostEqual(latest['vel_x'],.1,delta=.005)
                for _ in range(10):
                    manager._update_display()
                self.assertIn('--',window.data_display_panel.hwt606_1_widget.vel_x_label.text())
                window.data_recorder.stop_recording()
                with (path/'hwt606_1.csv').open(encoding='utf8') as handle:
                    reader = csv.DictReader(handle); rows = list(reader)
                    self.assertEqual(reader.fieldnames,['timestamp',*IMU1_COLUMNS])
                self.assertEqual(len(rows),310)
                self.assertEqual(rows[0]['vel_x'],'')
                self.assertEqual(rows[0]['velocity_valid'],'False')
                self.assertEqual(rows[-1]['velocity_valid'],'True')
                self.assertAlmostEqual(float(rows[-1]['vel_x']),latest['vel_x'])
                self.assertEqual(json.loads((path/'imu1_velocity_config.json').read_text(encoding='utf8'))
                                 ['estimator']['initial_velocity_mps'],[0,0,0])
                first_path = path
                self.assertTrue(window.data_recorder.start_recording())
                window.data_recorder.stop_recording()
                with (Path(window.data_recorder.session_dir)/'hwt606_1.csv').open(encoding='utf8') as handle:
                    self.assertEqual(len(list(csv.DictReader(handle))),0)
                self.assertTrue((first_path/'hwt606_1.csv').is_file())
            finally:
                if window.data_recorder.is_recording:
                    window.data_recorder.stop_recording()
                manager.hwt606_1 = None
                manager.display_timer.stop(); manager.controller.stop()
                window.stats_timer.stop(); window.recording_panel.duration_timer.stop()
                window.log_widget.stop_capture(); window.deleteLater(); self.app.processEvents()

    def test_port_settings_zero_control_and_reconnect_reset(self):
        from gui.main_window import MainWindow
        window = MainWindow(str(ROOT/'config/default.yaml'))
        manager = window.sensor_manager
        try:
            panel = window.connection_panel.sensor_widgets['hwt606_1']
            panel.port_combo.setCurrentText('COM99'); panel.baud_combo.setCurrentText('115200')
            panel.clock_combo.setCurrentIndex(1)
            with patch.object(manager,'connect_hwt606_1',return_value=True):
                self.assertTrue(window._connect_imu1())
            self.assertEqual(manager.config['sensor']['hwt606_1']['port'],'COM99')
            self.assertEqual(manager.config['sensor']['hwt606_1']['velocity']['time_source'],'nominal')
            window._on_sensor_status_changed('hwt606_1',True)
            self.assertFalse(panel.port_combo.isEnabled())
            self.assertTrue(window.data_display_panel.hwt606_1_widget.zero_button.isEnabled())
            manager.connection_status['hwt606_1'] = True
            window.data_display_panel.hwt606_1_widget.zero_button.click()
            self.assertEqual(manager.imu1_velocity.snapshot()['state'],'calibrating')
            with patch('core.sensor_manager.JY901SDevice1') as driver, \
                    patch('core.sensor_manager.write_cfg_1') as configure:
                manager.connection_status['hwt606_1'] = False
                self.assertTrue(manager.connect_hwt606_1())
                self.assertEqual(manager.imu1_velocity.snapshot()['state'],'needs_zero')
                self.assertIsNone(manager._unified_thread)
                self.assertEqual(configure.call_args.kwargs['output_content'],15)
                self.assertEqual(configure.call_args.kwargs['rate'],9)
                self.assertTrue(driver.return_value.register_callback.called)
                manager.disconnect_hwt606_1()
            self.assertIsNone(manager.latest_data['hwt606_1'])
        finally:
            manager.display_timer.stop(); manager.controller.stop()
            window.log_widget.stop_capture(); window.deleteLater(); self.app.processEvents()

    def test_serial_open_failure_leaves_no_connected_sensor(self):
        from core.sensor_manager import SensorManager
        from serial import SerialException
        manager = SensorManager(str(ROOT/'config/default.yaml'))
        try:
            with patch('drivers.HWT606_song_1.chs.lib.device_model.serial.Serial',
                       side_effect=SerialException('port busy')):
                self.assertFalse(manager.connect_hwt606_1())
            self.assertFalse(manager.connection_status['hwt606_1'])
            self.assertIsNone(manager.hwt606_1)
            self.assertIsNone(manager.latest_data['hwt606_1'])
        finally:
            manager.display_timer.stop(); manager.controller.stop(); manager.deleteLater()


if __name__ == '__main__':
    unittest.main()
