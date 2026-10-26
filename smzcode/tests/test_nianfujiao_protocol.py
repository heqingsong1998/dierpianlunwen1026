"""协议/接收/发送/记录回归，无需串口或实物传感器。"""
from __future__ import annotations

import csv
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drivers.nianfujiao_song import NianFuJiaoDevice
from drivers.nianfujiao_song.protocol import (
    FrameParser, StagePairData, CommandAck, PendingCommand,
    build_command_frame, match_feedback, TIMING_VALID, GPIO_WRITTEN, GPIO_CHANGED,
)
from core.data_recorder import DataRecorder
from core.sensor_manager import SensorManager


def wire(can_id, payload):
    return b'\x5a' + len(payload).to_bytes(2, 'little') + b'\x80' + can_id.to_bytes(3, 'big') + payload + b'\xa5'


def channels(sequence=1, ready_us=100000):
    values = [-12, 34, 1200, -2, 3, 4, 5, -6, 7, 1300, -8, 9, 10, 11,
              1200, 1300, 0, 0, 2, 1]
    return struct.pack('<20hII', *values, sequence, ready_us)


def pairs(sequence=1, ready_us=100000):
    return struct.pack('<8hII4H', -1, 2, -3, 4, -5, 6, -7, 8,
                       sequence, ready_us, 3, 1, 4, 5)


# Literal golden ACK from firmware protocol: ID 0x1234, seq 1,
# A=100000, D=105000, GPIO=106000, D-A=5000, generation=3.
ACK_BYTES = bytes.fromhex(
    '01 00 01 01 34 12 13 00 01 00 00 00 A0 86 01 00 '
    '28 9A 01 00 10 9E 01 00 88 13 00 00 03 00 04 00')


class FakeSerial:
    is_open = True

    def __init__(self):
        self.writes = []
        self.on_write = None
        self.short_write = False

    def write(self, data):
        self.writes.append(bytes(data))
        if self.on_write:
            self.on_write(data)
        return 1 if self.short_write else len(data)

    def flush(self):
        pass

    def close(self):
        self.is_open = False


def device():
    dev = NianFuJiaoDevice('FAKE', enable_csv=False, print_data=False)
    dev.serial_conn = FakeSerial()
    return dev


def feed(dev, packet):
    dev.rx_buf.extend(packet)
    dev._consume_buffer()


class ProtocolTests(unittest.TestCase):
    def test_command_matches_firmware_literal(self):
        self.assertEqual(build_command_frame(1, 1, 1), bytes.fromhex(
            '5A 08 04 00 00 FF FE C1 01 01 00 01 00 00 00 A5'))
        for args in ((4, 1, 1), (1, 0, 1), (1, 65536, 1), (1, 1, -1)):
            with self.assertRaises(ValueError):
                build_command_frame(*args)

    def test_split_at_every_boundary(self):
        packet = wire(0x123, channels())
        for split in range(1, len(packet)):
            parser = FrameParser()
            self.assertEqual(parser.feed(packet[:split]), [])
            self.assertEqual(parser.feed(packet[split:]), [(0x123, channels())])
            self.assertFalse(parser.buffer)

    def test_mixed_frames_and_noise(self):
        parser = FrameParser()
        expected = [(0x123, channels()), (0x124, pairs()), (0x125, ACK_BYTES)]
        stream = b'noise\x5a\xff\xffbad' + b''.join(wire(*item) for item in expected)
        actual = []
        for value in stream:
            actual.extend(parser.feed(bytes([value])))
        self.assertEqual(actual, expected)

    def test_unknown_frame_skipped_whole_and_bad_tail_recovers(self):
        embedded = wire(0x123, channels()).ljust(64, b'\x00')
        parser = FrameParser()
        self.assertEqual(parser.feed(wire(0x222, embedded)), [])
        corrupted = wire(0x123, channels())[:-1] + b'\x00'
        self.assertEqual(parser.feed(corrupted + wire(0x125, ACK_BYTES)), [(0x125, ACK_BYTES)])

    def test_ack_golden_fields_and_latency(self):
        ack = CommandAck.decode(ACK_BYTES, 2_001_000_000, 123456)
        self.assertEqual((ack.command_id, ack.sample_sequence, ack.ready_us), (0x1234, 1, 100000))
        self.assertEqual((ack.received_us, ack.gpio_written_us, ack.round_trip_us), (105000, 106000, 5000))
        pending = PendingCommand(1, 0x1234, 1, 100000, 1_999_000_000, 2_000_000_000, 123455)
        result = match_feedback(ack, pending)
        self.assertTrue(result['timing_valid'])
        self.assertEqual((result['controller_us'], result['transport_sum_us'], result['gpio_wait_us']),
                         (1000, 4000, 1000))

    def test_invalid_and_manual_timing_is_not_zero(self):
        ack = CommandAck.decode(ACK_BYTES)
        pending = PendingCommand(1, 0x1234, 1, 100000, 1_990_000_000, 2_000_000_000, 0)
        result = match_feedback(ack, pending)
        self.assertFalse(result['timing_valid'])
        self.assertEqual(result['timing_error'], 'negative_duration')
        self.assertEqual(result['transport_sum_us'], -5000)
        self.assertEqual(match_feedback(ack, None)['timing_error'], 'unmatched_command')
        bad = bytearray(ACK_BYTES); bad[1] = 2
        self.assertEqual(match_feedback(CommandAck.decode(bad), pending)['timing_error'], 'device_rejected_2')
        bad = bytearray(ACK_BYTES); bad[0] = 2
        self.assertEqual(match_feedback(CommandAck.decode(bad), pending)['timing_error'], 'unsupported_version')
        manual = CommandAck(1, 0, 1, 1, 1, GPIO_WRITTEN, 0, 0xFFFFFFFF,
                            100, 110, 0xFFFFFFFF, 3, 0)
        pending = PendingCommand(1, 1, 0, None, None, 1234, 0)
        result = match_feedback(manual, pending)
        self.assertEqual(result['timing_error'], 'unbound_command')
        self.assertIsNone(result['controller_us'])
        self.assertIsNone(result['transport_sum_us'])

    def test_microsecond_wrap(self):
        ack = CommandAck(1, 0, 0, 0, 1, TIMING_VALID | GPIO_WRITTEN, 7,
                         0xFFFFFF00, 0x100, 0x200, 512, 0, 0)
        pending = PendingCommand(0, 1, 7, 0xFFFFFF00, 1_000_000, 1_100_000, 0)
        result = match_feedback(ack, pending)
        self.assertTrue(result['timing_valid'])
        self.assertEqual((result['transport_sum_us'], result['gpio_wait_us']), (412, 256))


class DeviceTests(unittest.TestCase):
    def test_mixed_callbacks_and_unsigned_metadata(self):
        dev = device(); events = []
        dev.register_callback(lambda kind, data: events.append((kind, data)))
        packet = (wire(0x123, channels(0xFEDCBA98, 0xFFFFFF80)) +
                  wire(0x124, pairs(0xFEDCBA98, 0xFFFFFF80)) + wire(0x125, ACK_BYTES))
        feed(dev, packet[:13]); self.assertFalse(events)
        feed(dev, packet[13:])
        self.assertEqual([item[0] for item in events], ['style0', 'stage_pairs', 'command_ack'])
        sample = events[0][1]
        self.assertEqual((sample.Fx1, sample.Fy1, sample.sample_sequence, sample.ready_us),
                         (-12, 34, 0xFEDCBA98, 0xFFFFFF80))
        self.assertGreater(sample.host_received_ns, 0)
        self.assertEqual(events[1][1]['pair_0'], -1)
        self.assertEqual(dev.protocol_version, 1)

    def test_ack_before_serial_write_returns_can_match(self):
        dev = device(); events = []
        dev.register_callback(lambda kind, data: events.append((kind, data)))
        feed(dev, wire(0x124, pairs()))
        dev._command_id = 0x1233
        dev.serial_conn.on_write = lambda _: feed(dev, wire(0x125, ACK_BYTES))
        with patch('drivers.nianfujiao_song.nianfujiao_device.time.perf_counter_ns', return_value=2_000_000_000):
            pending = dev.send_timed_action(1, 1, 100000, 1_999_000_000)
        self.assertIsNotNone(pending)
        ack = next(data for kind, data in events if kind == 'command_ack')
        self.assertTrue(ack['timing_valid'])
        self.assertEqual(ack['transport_sum_us'], 4000)
        self.assertFalse(dev._pending_commands)

    def test_buttons_use_unbound_new_command_after_version_confirmed(self):
        dev = device(); events = []
        dev.register_callback(lambda kind, data: events.append((kind, data)))
        feed(dev, wire(0x124, pairs()))
        self.assertTrue(dev.send_reply()); self.assertTrue(dev.send_engage()); self.assertTrue(dev.send_zero())
        self.assertEqual([data[8] for data in dev.serial_conn.writes], [0, 1, 3])
        self.assertTrue(all(data[7] == 0xC1 and data[11:15] == b'\x00' * 4
                            for data in dev.serial_conn.writes))
        self.assertTrue(all(data['input_received_ns'] is None
                            for kind, data in events if kind == 'command_sent'))

    def test_legacy_fallback_and_send_failure(self):
        dev = device()
        with patch('drivers.nianfujiao_song.utils.time.sleep'):
            self.assertTrue(dev.send_zero())
        self.assertEqual(dev.serial_conn.writes[-1], bytes.fromhex(
            '5A 08 04 00 00 FF FE A4 02 01 00 00 00 00 B7 A5'))
        with self.assertRaises(ValueError):
            dev.send_timed_action(1, 1, 100000, time.perf_counter_ns())
        feed(dev, wire(0x124, pairs()))
        dev.serial_conn.short_write = True
        self.assertIsNone(dev.send_timed_action(1, 1, 100000, time.perf_counter_ns()))
        self.assertFalse(dev._pending_commands)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_all_protocol_data_reaches_csv_and_original_columns_remain(self):
        with tempfile.TemporaryDirectory() as temp:
            recorder = DataRecorder(temp)
            manager = SensorManager(str(ROOT / 'config' / 'default.yaml'))
            manager.nianfujiao = device()
            manager.connection_status['nianfujiao'] = True
            manager.record_callback_nianfujiao = recorder.write_nianfujiao_data
            manager.record_callback_nianfujiao_aux = recorder.write_nianfujiao_aux
            controller_input = Mock()
            manager.nianfujiao_frame_callback = controller_input
            manager.nianfujiao.register_callback(manager._on_nianfujiao_callback)
            self.assertTrue(recorder.start_recording())
            try:
                feed(manager.nianfujiao, wire(0x123, channels(0xFEDCBA98, 0xFFFFFF80)))
                feed(manager.nianfujiao, wire(0x124, pairs(0xFEDCBA98, 0xFFFFFF80)))
                self.assertTrue(manager.nianfujiao.send_engage())
                feed(manager.nianfujiao, wire(0x125, ACK_BYTES))
                manager._update_display(); manager._update_display()
                controller_input.assert_called_once()
            finally:
                recorder.stop_recording()
                manager.display_timer.stop()
            path = Path(recorder.session_dir)
            for filename in ('nianfujiao_style0.csv', 'nianfujiao_frames.csv'):
                with (path / filename).open(encoding='utf-8', newline='') as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), 1)
                self.assertEqual(int(rows[0]['sample_sequence']), 0xFEDCBA98)
                self.assertEqual(int(rows[0]['ready_us']), 0xFFFFFF80)
                self.assertEqual(int(rows[0]['Fx1_raw']), -12)
                self.assertTrue(rows[0]['host_received_ns'])
            for kind in ('stage_pairs', 'commands', 'feedback'):
                with (path / f'nianfujiao_{kind}.csv').open(encoding='utf-8', newline='') as handle:
                    self.assertEqual(len(list(csv.DictReader(handle))), 1)

    def test_gui_refresh_never_writes_duplicate_rows(self):
        from gui.main_window import MainWindow
        window = SimpleNamespace(data_display_panel=Mock(), data_recorder=Mock(is_recording=True))
        MainWindow._on_nianfujiao_data_updated(window, 0, {'raw': []})
        MainWindow._on_liuzhouli_data_updated(window, {'Fx': 1})
        window.data_recorder.write_nianfujiao_data.assert_not_called()
        window.data_recorder.write_liuzhouli_data.assert_not_called()


if __name__ == '__main__':
    unittest.main()
