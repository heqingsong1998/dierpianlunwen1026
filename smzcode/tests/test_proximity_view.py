"""接近觉物理位置、符号、数据时效及只读 GUI 接入的无硬件验证。"""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication
from controller.visualization.proximity_data import (
    CHANNELS, DEFAULT_POSITIONS, ProximitySample, load_positions, save_positions,
    signal_differences,
)
from controller.visualization.proximity_view import ProximityViewDialog


def frame(values=(100, -50, 300, 10), sequence=1):
    raw = [0] * 24
    for index, value in zip((5, 6, 12, 13), values):
        raw[index] = value
    return {'raw': raw, 'sample_sequence': sequence, 'ready_us': sequence * 16000,
            'host_received_ns': time.perf_counter_ns()}


class ProximityDataTests(unittest.TestCase):
    def test_signed_values_follow_confirmed_physical_positions_without_mutating_input(self):
        data = frame()
        original = list(data['raw'])
        sample = ProximitySample.from_frame(data)
        values = sample.at_positions(DEFAULT_POSITIONS)
        self.assertEqual(sample.values, (100, -50, 300, 10))
        self.assertEqual(values, (100, 300, -50, 10))
        self.assertEqual(signal_differences(values), {
            'right_minus_left': 130, 'upper_minus_lower': 220, 'range': 350})
        self.assertEqual(data['raw'], original)
        data['raw'][5] = 900
        self.assertEqual(sample.values[0], 100)

    def test_invalid_values_and_duplicate_positions_are_rejected(self):
        for raw in ([1] * 13, frame((1, float('nan'), 3, 4))['raw'],
                    frame((float('inf'), 2, 3, 4))['raw']):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ProximitySample.from_frame({'raw': raw})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'layout.json'
            self.assertEqual(load_positions(path), DEFAULT_POSITIONS)
            save_positions(CHANNELS, path)
            self.assertEqual(load_positions(path), CHANNELS)
            with self.assertRaises(ValueError):
                save_positions(('JJJ1_1',) * 4, path)
            self.assertEqual(load_positions(path), CHANNELS)


class ProximityViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.dialog = ProximityViewDialog(layout_path=Path(self.temporary.name) / 'layout.json')
        self.addCleanup(self.cleanup_dialog)

    def cleanup_dialog(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def display(self, data):
        self.dialog.set_connected(True)
        self.dialog.update_sample(data)
        self.dialog.refresh_view()

    def test_signed_bars_include_negative_axis_and_disconnection_clears_values(self):
        dialog = self.dialog
        self.display(frame())
        self.assertEqual(dialog.last_displayed_values, (100, 300, -50, 10))
        self.assertEqual([label.text() for label in dialog.value_labels], ['+100', '+300', '-50', '+10'])
        self.assertLess(dialog.axes.get_zlim()[0], -50)
        self.assertGreater(dialog.axes.get_zlim()[1], 300)
        self.assertIn('+130.0', dialog.difference_label.text())
        dialog.set_connected(False)
        self.assertIsNone(dialog.last_displayed_values)
        self.assertTrue(all(label.text() == '--' for label in dialog.value_labels))
        dialog.set_connected(True)
        self.assertIsNone(dialog.last_displayed_values)

    def test_repeated_cached_frame_does_not_mask_staleness_and_invalid_input_clears_plot(self):
        data = frame()
        self.display(data)
        with patch('controller.visualization.proximity_view.time.perf_counter_ns',
                   return_value=data['host_received_ns'] + 2_000_000_000):
            self.dialog.update_sample(data)
            self.dialog.refresh_view()
            self.assertIn('超时', self.dialog.status_label.text())
        self.dialog.update_sample(frame((1, float('nan'), 3, 4)))
        self.dialog.refresh_view()
        self.assertIsNone(self.dialog.last_displayed_values)
        self.assertIn('无效', self.dialog.status_label.text())

    def test_pause_freezes_display_and_hidden_window_stops_timer(self):
        dialog = self.dialog
        dialog.show()
        self.display(frame())
        self.assertTrue(dialog.refresh_timer.isActive())
        dialog.pause_checkbox.setChecked(True)
        dialog.update_sample(frame((200, 400, 600, 800), sequence=2))
        dialog.refresh_view()
        self.assertEqual(dialog.last_displayed_values, (100, 300, -50, 10))
        dialog.pause_checkbox.setChecked(False)
        self.assertEqual(dialog.last_displayed_values, (200, 600, 400, 800))
        dialog.close()
        self.assertFalse(dialog.refresh_timer.isActive())
        dialog.show()
        self.assertTrue(dialog.refresh_timer.isActive())

    def test_corner_swap_remains_unique_and_saved_mapping_survives_reopen(self):
        self.display(frame())
        self.dialog.position_boxes[0].setCurrentText('JJJ2_2')
        expected = ('JJJ2_2', 'JJJ2_1', 'JJJ1_2', 'JJJ1_1')
        self.assertEqual(self.dialog.positions, expected)
        self.assertEqual(self.dialog.last_displayed_values, (10, 300, -50, 100))
        self.dialog.save_layout_btn.click()
        self.assertEqual(load_positions(self.dialog._layout_path), expected)
        self.dialog.default_layout_btn.click()
        self.assertEqual(self.dialog.positions, DEFAULT_POSITIONS)
        self.assertEqual(load_positions(self.dialog._layout_path), expected)

    def test_main_button_reuses_window_and_display_emits_no_control_or_recording_actions(self):
        from gui.main_window import MainWindow
        window = MainWindow(str(ROOT / 'config' / 'default.yaml'))
        try:
            manager = window.sensor_manager
            with patch.object(manager, 'nianfujiao_send_timed_action') as send, \
                    patch.object(manager.controller, 'start') as start, \
                    patch.object(manager.controller, 'stop') as stop, \
                    patch.object(window.data_recorder, 'write_nianfujiao_data') as write:
                window.control_panel.proximity_view_btn.click()
                dialog = window._proximity_dialog
                self.assertIsNotNone(dialog)
                self.assertIsNone(dialog.last_displayed_values)
                manager.connection_status['nianfujiao'] = True
                manager.connection_status_changed.emit('nianfujiao', True)
                data = frame()
                manager.latest_data['nianfujiao'][0] = data
                manager.nianfujiao_data_updated.emit(0, data)
                dialog.refresh_view()
                self.assertEqual(dialog.last_displayed_values, (100, 300, -50, 10))
                window.control_panel.proximity_view_btn.click()
                self.assertIs(window._proximity_dialog, dialog)
                dialog.close()
                self.assertFalse(dialog.refresh_timer.isActive())
                manager.latest_data['nianfujiao'][0] = frame((2, 3, 4, 5), sequence=2)
                window.control_panel.proximity_view_btn.click()
                self.assertIs(window._proximity_dialog, dialog)
                self.assertEqual(dialog.last_displayed_values, (2, 4, 3, 5))
                manager.connection_status['nianfujiao'] = False
                manager.connection_status_changed.emit('nianfujiao', False)
                self.assertIsNone(dialog.last_displayed_values)
                for method in (send, start, stop, write):
                    method.assert_not_called()
        finally:
            if window._proximity_dialog is not None:
                window._proximity_dialog.close()
            window.sensor_manager.controller.stop()
            window.sensor_manager.display_timer.stop()
            window.log_widget.stop_capture()
            window.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
