"""31 列足端 CSV，以及保存端 0x123/0x124 基准版本配对。无需实物。"""
import csv
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from controller.calibration import Calibration
from core.data_recorder import DataRecorder
from core.sensor_manager import SensorManager
from test_nianfujiao_protocol import channels, device, feed, pairs, wire


# 对照已确认的导出规范，而不是从实现中的表头复制期望值。
EXPECTED_COLUMNS = [
    'timestamp', 'timestamp_ms',
    'Fx1_raw', 'Fy1_raw', 'Fz1_raw', 'Mx1_raw', 'My1_raw', 'JJJ1_1_raw', 'JJJ1_2_raw',
    'Fx2_raw', 'Fy2_raw', 'Fz2_raw', 'Mx2_raw', 'My2_raw', 'JJJ2_1_raw', 'JJJ2_2_raw',
    'Fx1_cal', 'Fy1_cal', 'Fz1_cal', 'Mx1_cal', 'My1_cal',
    'Fx2_cal', 'Fy2_cal', 'Fz2_cal', 'Mx2_cal', 'My2_cal',
    'sample_sequence', 'ready_us', 'host_received_ns', 'baseline_generation', 'pin_state',
]


def sample(sequence=1, ready_us=100000, first_z=1200, second_z=-1200):
    raw = [-12, 34, first_z, -2, 3, 4, 5, -6, 7, second_z, -8, 9, 10, 11,
           first_z, second_z, 0, 0, 2, 1, -1, -32767, -32767, -2]
    return {
        'raw': raw, **Calibration().apply(raw, rounded=True),
        'timestamp_ms': 1700000000123, 'sensor_ts_ms': 1700000000123,
        'host_received_ns': 1234567890123456789,
        'sample_sequence': sequence, 'ready_us': ready_us,
        'pin_state': 1, 'output_state': 2, 'protocol_version': 1,
    }


def metadata(sequence=1, ready_us=100000, generation=3):
    return {
        'sample_sequence': sequence, 'ready_us': ready_us,
        'baseline_generation': generation, 'protocol_version': 1,
        'sensor_ts_ms': 1700000000127,
    }


def read_rows(path):
    with Path(path).open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if reader.fieldnames != EXPECTED_COLUMNS:
            raise AssertionError(reader.fieldnames)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise AssertionError('数据行长度与 31 列表头不一致')
        return rows


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.recorder = DataRecorder(self.temp.name)
        self.assertTrue(self.recorder.start_recording())
        self.addCleanup(self.stop_recording)

    def stop_recording(self):
        if self.recorder.is_recording:
            self.recorder.stop_recording()

    def rows(self, kind):
        return read_rows(Path(self.recorder.session_dir) / f'nianfujiao_{kind}.csv')

    def test_both_files_export_31_columns_raw_channels_moments_and_full_signed_fz(self):
        data = sample()
        data['baseline_generation'] = 0  # 版本 0 是有效版本，不能当作未知值。
        self.recorder.write_nianfujiao_data(0, data)
        self.recorder.write_nianfujiao_aux('frames', data)
        self.stop_recording()
        for kind in ('style0', 'frames'):
            rows = self.rows(kind)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(len(row), 31)
            for name, expected in zip(EXPECTED_COLUMNS[2:16], data['raw'][:14]):
                self.assertEqual(int(row[name]), expected)
            self.assertEqual(float(row['Fz1_cal']), 1.6476)
            self.assertEqual(float(row['Fz2_cal']), -1.4716)
            for name, index in (('Fx1_cal', 0), ('Fy1_cal', 1), ('Mx1_cal', 3), ('My1_cal', 4),
                                ('Fx2_cal', 7), ('Fy2_cal', 8), ('Mx2_cal', 10), ('My2_cal', 11)):
                self.assertEqual(float(row[name]), data['calibrated'][index])
            self.assertEqual(int(row['host_received_ns']), 1234567890123456789)
            self.assertEqual(row['baseline_generation'], '0')
            self.assertEqual(row['pin_state'], '1')
        self.assertEqual(len(data['raw']), 24)
        self.assertEqual(len(data['calibrated']), 24)
        self.assertEqual(data['raw'][12:14], [10, 11])  # 内部字段及标定输入不改变。

    def test_negative_first_group_and_fz_fallback_merge(self):
        data = sample(first_z=-1200, second_z=1200)
        del data['fz1_total_cal']
        del data['fz2_total_cal']
        self.recorder.write_nianfujiao_aux('frames', data)
        self.stop_recording()
        row = self.rows('frames')[0]
        self.assertEqual(float(row['Fz1_cal']), -1.7621)
        self.assertEqual(float(row['Fz2_cal']), 1.5513)

    def test_pairs_after_main_data_fill_both_files_without_changing_timestamps(self):
        data = sample()
        aligned = dict(data, timestamp_ms=data['timestamp_ms'] + 50)
        self.recorder.write_nianfujiao_aux('frames', data)
        self.recorder.write_nianfujiao_data(0, aligned)
        self.assertEqual(self.recorder.data_counts.get('nianfujiao_frames', 0), 0)
        self.recorder.write_nianfujiao_aux('stage_pairs', metadata())
        self.stop_recording()
        frames = self.rows('frames')
        paired = self.rows('style0')
        self.assertEqual((len(frames), len(paired)), (1, 1))
        for row, original in ((frames[0], data), (paired[0], aligned)):
            self.assertEqual(row['baseline_generation'], '3')
            self.assertEqual(int(row['timestamp_ms']), original['timestamp_ms'])
            self.assertEqual(int(row['host_received_ns']), original['host_received_ns'])
        self.assertNotEqual(frames[0]['timestamp'], paired[0]['timestamp'])
        self.assertNotIn('baseline_generation', data)  # 不修改共享的采集/控制器输入字典。

    def test_pairs_before_main_data_are_matched_and_duplicate_pairs_do_not_duplicate_rows(self):
        self.recorder.write_nianfujiao_aux('stage_pairs', metadata(generation=0))
        self.recorder.write_nianfujiao_aux('frames', sample())
        self.recorder.write_nianfujiao_aux('stage_pairs', metadata(generation=0))
        self.stop_recording()
        rows = self.rows('frames')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['baseline_generation'], '0')

    def test_both_sequence_and_ready_time_must_match(self):
        self.recorder.write_nianfujiao_aux('stage_pairs', metadata(sequence=2, generation=8))
        self.recorder.write_nianfujiao_aux('stage_pairs', metadata(ready_us=100001, generation=9))
        self.recorder.write_nianfujiao_aux('frames', sample())
        self.stop_recording()
        self.assertEqual(self.rows('frames')[0]['baseline_generation'], '')

    def test_later_metadata_cannot_reorder_real_frames_or_remove_synchronized_repeats(self):
        for sequence in (1, 2):
            data = sample(sequence, sequence * 16000)
            self.recorder.write_nianfujiao_aux('frames', data)
            self.recorder.write_nianfujiao_data(0, data)
            if sequence == 1:
                self.recorder.write_nianfujiao_data(0, dict(data, timestamp_ms=data['timestamp_ms'] + 1))
        self.recorder.write_nianfujiao_aux('stage_pairs', metadata(2, 32000, 4))
        self.recorder.write_nianfujiao_aux('stage_pairs', metadata(1, 16000, 3))
        self.stop_recording()
        frames, paired = self.rows('frames'), self.rows('style0')
        self.assertEqual([int(row['sample_sequence']) for row in frames], [1, 2])
        self.assertEqual([int(row['sample_sequence']) for row in paired], [1, 1, 2])
        self.assertEqual([row['baseline_generation'] for row in frames], ['3', '4'])
        self.assertEqual([row['baseline_generation'] for row in paired], ['3', '3', '4'])

    def test_missing_metadata_expires_without_more_input_and_late_pair_keeps_single_row(self):
        self.recorder.FOOT_PAIR_WAIT_S = 0.02
        self.recorder.write_nianfujiao_aux('frames', sample())
        deadline = time.monotonic() + 1
        while self.recorder.data_counts.get('nianfujiao_frames', 0) == 0 and time.monotonic() < deadline:
            time.sleep(0.002)
        self.assertEqual(self.recorder.data_counts.get('nianfujiao_frames'), 1)
        self.recorder.write_nianfujiao_aux('stage_pairs', metadata())
        self.stop_recording()
        rows = self.rows('frames')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['baseline_generation'], '')

    def test_stop_flushes_unmatched_tail(self):
        self.recorder.FOOT_PAIR_WAIT_S = 60
        self.recorder.write_nianfujiao_aux('frames', sample())
        self.recorder.write_nianfujiao_data(0, sample())
        self.stop_recording()
        for kind in ('frames', 'style0'):
            rows = self.rows(kind)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['baseline_generation'], '')

    def test_new_session_does_not_reuse_previous_generation(self):
        self.recorder.write_nianfujiao_aux('stage_pairs', metadata(generation=9))
        self.recorder.write_nianfujiao_aux('frames', sample())
        first_path = self.recorder.session_dir
        self.stop_recording()
        self.assertTrue(self.recorder.start_recording())
        self.recorder.write_nianfujiao_aux('frames', sample())
        self.stop_recording()
        self.assertEqual(read_rows(Path(first_path) / 'nianfujiao_frames.csv')[0]['baseline_generation'], '9')
        self.assertEqual(self.rows('frames')[0]['baseline_generation'], '')

    def test_pending_buffer_is_bounded_and_overflow_preserves_rows_in_order(self):
        self.recorder.FOOT_PENDING_LIMIT = 2
        self.recorder.FOOT_PAIR_WAIT_S = 60
        for sequence in range(1, 4):
            self.recorder.write_nianfujiao_aux('frames', sample(sequence, sequence * 16000))
        self.assertLessEqual(len(self.recorder._pending_foot_rows['nianfujiao_frames']), 2)
        self.recorder.write_nianfujiao_aux('stage_pairs', metadata(2, 32000, 4))
        self.recorder.write_nianfujiao_aux('stage_pairs', metadata(3, 48000, 4))
        self.stop_recording()
        rows = self.rows('frames')
        self.assertEqual([int(row['sample_sequence']) for row in rows], [1, 2, 3])
        self.assertEqual([row['baseline_generation'] for row in rows], ['', '4', '4'])

    def test_metadata_cache_is_bounded_and_unknown_protocol_is_not_used(self):
        self.recorder.FOOT_PAIR_CACHE_SIZE = 2
        for sequence in range(1, 4):
            self.recorder.write_nianfujiao_aux('stage_pairs', metadata(sequence, sequence * 16000, 8))
        self.assertEqual(len(self.recorder._foot_generations), 2)
        self.recorder.write_nianfujiao_aux('frames', sample(1, 16000))
        self.recorder.write_nianfujiao_aux('stage_pairs', dict(metadata(1, 16000, 9), protocol_version=0))
        self.stop_recording()
        self.assertEqual(self.rows('frames')[0]['baseline_generation'], '')

    def test_legacy_frame_without_sample_key_does_not_wait(self):
        self.recorder.FOOT_PAIR_WAIT_S = 60
        self.recorder.write_nianfujiao_aux('frames', sample(sequence=0, ready_us=0))
        self.assertEqual(self.recorder.data_counts.get('nianfujiao_frames'), 1)
        self.stop_recording()
        self.assertEqual(self.rows('frames')[0]['baseline_generation'], '')

    def test_receive_and_synchronized_recording_threads_can_join_metadata_concurrently(self):
        self.recorder.FOOT_PAIR_WAIT_S = 60
        begin = threading.Barrier(2)
        errors = []

        def data_thread():
            try:
                begin.wait(timeout=2)
                for sequence in range(1, 101):
                    data = sample(sequence, sequence * 16000)
                    self.recorder.write_nianfujiao_aux('frames', data)
                    self.recorder.write_nianfujiao_data(0, data)
            except Exception as exc:
                errors.append(exc)

        def metadata_thread():
            try:
                begin.wait(timeout=2)
                for sequence in range(1, 101):
                    self.recorder.write_nianfujiao_aux(
                        'stage_pairs', metadata(sequence, sequence * 16000, 3 + sequence // 50))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=entry) for entry in (data_thread, metadata_thread)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.stop_recording()
        for kind in ('style0', 'frames'):
            rows = self.rows(kind)
            self.assertEqual([int(row['sample_sequence']) for row in rows], list(range(1, 101)))
            self.assertEqual([int(row['baseline_generation']) for row in rows],
                             [3 + sequence // 50 for sequence in range(1, 101)])


class RecordingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_true_frame_callback_is_immediate_while_csv_waits_for_matching_stage_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            recorder = DataRecorder(temp)
            manager = SensorManager(str(ROOT / 'config' / 'default.yaml'))
            manager.nianfujiao = device()
            manager.connection_status['nianfujiao'] = True
            manager.record_callback_nianfujiao = recorder.write_nianfujiao_data
            manager.record_callback_nianfujiao_aux = recorder.write_nianfujiao_aux
            observer = Mock()
            manager.nianfujiao_frame_callback = observer
            manager.nianfujiao.register_callback(manager._on_nianfujiao_callback)
            self.assertTrue(recorder.start_recording())
            try:
                feed(manager.nianfujiao, wire(0x123, channels(0xFEDCBA98, 0xFFFFFF80)))
                observer.assert_called_once()
                original = observer.call_args[0][0]
                self.assertEqual(len(original['raw']), 24)
                feed(manager.nianfujiao, wire(0x124, pairs(0xFEDCBA98, 0xFFFFFF80)))
                manager._update_display()
                manager._update_display()
                observer.assert_called_once()
            finally:
                recorder.stop_recording()
                manager.display_timer.stop()
            for kind in ('style0', 'frames'):
                rows = read_rows(Path(recorder.session_dir) / f'nianfujiao_{kind}.csv')
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['baseline_generation'], '3')
                self.assertEqual(int(rows[0]['host_received_ns']), original['host_received_ns'])
                self.assertEqual(int(rows[0]['timestamp_ms']), original['sensor_ts_ms'])


if __name__ == '__main__':
    unittest.main()
