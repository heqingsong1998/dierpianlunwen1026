"""主界面两类传感器的连接与采集流程；不连接硬件。"""
import csv
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton
from gui.main_window import MainWindow


class HostSensorScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(str(ROOT / 'config' / 'default.yaml'))
        self.window.log_widget.stop_capture()
        self.addCleanup(self.close_window)

    def close_window(self):
        window = self.window
        if window.data_recorder.is_recording:
            window.data_recorder.stop_recording()
        window.stats_timer.stop()
        window.sensor_manager.display_timer.stop()
        window.sensor_manager.controller.stop()
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_visible_controls_and_connect_all_only_include_foot_and_six_axis(self):
        window = self.window
        manager = window.sensor_manager
        self.assertEqual(set(window.connection_panel.sensor_widgets), {'nianfujiao', 'liuzhouli'})
        self.assertEqual(set(manager.get_all_status()), {'nianfujiao', 'liuzhouli'})
        for widget in window.findChildren(QLabel) + window.findChildren(QPushButton):
            self.assertNotRegex(widget.text(), r'IMU|HWT606|加速度|角速度|Y轴速度|积分时间|接近觉 2×2')
        with patch.object(manager, 'connect_nianfujiao', return_value=True) as foot, \
                patch.object(manager, 'connect_liuzhouli', return_value=True) as force, \
                patch.object(QMessageBox, 'information') as message:
            window._on_connect_all()
        foot.assert_called_once_with()
        force.assert_called_once_with()
        self.assertIn('所有传感器连接成功', message.call_args.args[2])

    def test_main_recording_keeps_force_and_foot_data_without_imu_files(self):
        window = self.window
        manager = window.sensor_manager
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(QMessageBox, 'information'):
            window.data_recorder.base_dir = temp
            manager.connection_status['nianfujiao'] = True
            window._on_start_recording()
            self.assertTrue(window.data_recorder.is_recording)
            session = Path(window.data_recorder.session_dir)
            manager.record_callback_nianfujiao(0, {
                'raw': [0] * 24, 'calibrated': [0.] * 24,
                'timestamp_ms': 1700000000000,
            })
            manager.record_callback_liuzhouli({'Fz': -1.25, 'timestamp_ms': 1700000000000})
            window._on_stop_recording()
            self.assertEqual({path.name for path in session.iterdir()}, {
                'nianfujiao_style0.csv', 'nianfujiao_frames.csv', 'liuzhouli.csv',
                'nianfujiao_stage_pairs.csv', 'nianfujiao_commands.csv',
                'nianfujiao_feedback.csv', 'nianfujiao_controller.csv', 'controller_config.json',
            })
            for filename in ('nianfujiao_style0.csv', 'liuzhouli.csv'):
                with (session / filename).open(encoding='utf-8', newline='') as handle:
                    self.assertEqual(len(list(csv.DictReader(handle))), 1)
            window.recording_panel.update_data_counts(window.data_recorder.data_counts)
            self.assertEqual(window.recording_panel.count_label.text(), '粘附脚: 1 | 六轴力: 1')


if __name__ == '__main__':
    unittest.main()
