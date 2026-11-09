"""主界面传感器连接与采集流程；不连接硬件。"""
import csv
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

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

    def test_connect_all_without_selected_ultrasonic_port(self):
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
                'nianfujiao_board.csv',
            })
            for filename in ('nianfujiao_style0.csv', 'liuzhouli.csv'):
                with (session / filename).open(encoding='utf-8', newline='') as handle:
                    self.assertEqual(len(list(csv.DictReader(handle))), 1)
            window.recording_panel.update_data_counts(window.data_recorder.data_counts)
            self.assertEqual(window.recording_panel.count_label.text(), '粘附脚: 1 | 六轴力: 1 | 超声波: 0')

    def test_ultrasonic_serial_controls_display_and_recording(self):
        window = self.window
        reader = window.ultrasonic_serial
        connection = window.connection_panel.ultrasonic_widget
        display = window.data_display_panel
        port = Mock()
        port.isOpen.return_value = False

        def open_port(_mode):
            port.isOpen.return_value = True
            return True

        def close_port():
            port.isOpen.return_value = False

        port.open.side_effect = open_port
        port.close.side_effect = close_port
        port.readAll.side_effect = [
            b'distance_mm=123,speed_mm_s=NA\r\n',
            b'distance_mm=120,speed_',
            b'mm_s=-50\r\ndistance_mm=NA,speed_mm_s=NA,status=TIMEOUT\r\n',
            b'x' * 129,
            b'serial garbage\r\ndistance_mm=125,speed_mm_s=+50\r\n',
            b'distance_mm=126,speed_mm_s=50\r\n',
        ]

        with tempfile.TemporaryDirectory() as temp, patch.object(reader, '_port', port), \
                patch.object(QMessageBox, 'information'):
            window.data_recorder.base_dir = temp
            self.assertEqual(connection.baud_box.value(), 921600)
            connection.port_box.setEditText('COM88')
            connection.baud_box.setValue(460800)
            connection.toggle_btn.click()
            port.setPortName.assert_called_once_with('COM88')
            port.setBaudRate.assert_called_once_with(460800)
            self.assertFalse(connection.baud_box.isEnabled())

            reader._read_available()
            self.assertEqual(display.ultrasonic_distance.text(), '距离 123 mm')
            self.assertEqual(display.ultrasonic_status.text(), '等待速度估算')

            window.recording_panel.start_btn.click()
            self.assertTrue(window.data_recorder.is_recording)
            window.sensor_manager.record_callback_nianfujiao(0, {
                'raw': [0] * 24, 'calibrated': [0.] * 24,
                'timestamp_ms': 1700000000000,
            })
            window.sensor_manager.record_callback_liuzhouli({'Fz': -1.25, 'timestamp_ms': 1700000000000})
            reader._read_available()
            self.assertEqual(window.data_recorder.data_counts['ultrasonic'], 0)
            reader._read_available()
            self.assertEqual(display.ultrasonic_speed.text(), '速度 --')
            self.assertEqual(display.ultrasonic_status.text(), '回波超时')
            reader._read_available()
            reader._read_available()
            self.assertEqual(display.ultrasonic_speed.text(), '速度 +50 mm/s')
            reader.data_timeout.emit()
            self.assertEqual(display.ultrasonic_distance.text(), '距离 --')
            self.assertEqual(display.ultrasonic_status.text(), '未收到串口数据')

            window.recording_panel.stop_btn.click()
            reader._read_available()
            self.assertEqual(window.recording_panel.count_label.text(), '粘附脚: 1 | 六轴力: 1 | 超声波: 3')
            session = Path(window.data_recorder.session_dir)
            with (session / 'ultrasonic.csv').open(encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(
                [(row['distance_mm'], row['speed_mm_s'], row['status']) for row in rows],
                [('120', '-50', 'OK'), ('', '', 'TIMEOUT'), ('125', '50', 'OK')],
            )
            self.assertTrue(all(int(row['timestamp_ms']) > 0 and
                                int(row['host_received_ns']) > 0 for row in rows))

            connection.toggle_btn.click()
            port.close.assert_called_once()
            self.assertTrue(connection.baud_box.isEnabled())
            self.assertEqual(display.ultrasonic_status.text(), '未连接')


if __name__ == '__main__':
    unittest.main()
