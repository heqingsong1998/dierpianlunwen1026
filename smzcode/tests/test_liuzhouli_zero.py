"""清零回归检查：本机 TCP 模拟设备，无需连接实物传感器。

运行：python -X utf8 -m unittest discover -s smzcode/tests -v
"""
from __future__ import annotations

import os
import select
import socket
import struct
import sys
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from drivers.sensors.m8128b1 import M8128B1Sensor
from core.sensor_manager import SensorManager


class TcpSensor:
    """持续输出合法六轴帧，支持不回 STOP ACK、忽略 STOP 等故障。"""

    def __init__(self, stop_ack=True, ignore_stop=False, zero_reply="ok"):
        self.stop_ack = stop_ack
        self.ignore_stop = ignore_stop
        self.zero_reply = zero_reply
        self.commands = []
        self.streaming = False
        self.zeroed = False
        self.closed = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(2)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            conn, _ = self.listener.accept()
            with conn:
                buffer = bytearray()
                pkg = 0
                while not self.closed.is_set():
                    readable, _, _ = select.select([conn], [], [], 0.001)
                    if readable:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        while b"\r\n" in buffer:
                            command, _, remainder = buffer.partition(b"\r\n")
                            buffer = bytearray(remainder)
                            self.commands.append(command.decode("ascii"))
                            if command == b"AT+GSD":
                                self.streaming = True
                            elif command == b"AT+GSD=STOP":
                                if not self.ignore_stop:
                                    self.streaming = False
                                    if self.stop_ack:
                                        # ACK 与残余二进制数据混合。
                                        conn.sendall(b"\xaa\x55\x00\x08\x00\xffACK+GSD=STOP\r\n")
                            elif command.startswith(b"AT+ADJZF="):
                                if self.zero_reply == "ok":
                                    self.zeroed = True
                                    # 刻意拆分 ACK，验证跨接收块的提取。
                                    conn.sendall(b"\xaa\x55\xffAC")
                                    time.sleep(0.005)
                                    conn.sendall(b"K+ADJZF=OK\r\n")
                                elif self.zero_reply == "error":
                                    conn.sendall(b"ACK+ADJZF=ERROR\r\n")
                    if self.streaming:
                        values = (0.0,) * 6 if self.zeroed else (10.0,) * 6
                        body = struct.pack("<6f", *values)
                        payload = struct.pack(">H", pkg % 65536) + body + bytes([sum(body) & 255])
                        conn.sendall(b"\xaa\x55" + struct.pack(">H", len(payload)) + payload)
                        pkg += 1
        except (OSError, ValueError):
            if not self.closed.is_set():
                raise

    def close(self):
        self.closed.set()
        self.listener.close()
        self.thread.join(timeout=2)


class DriverZeroTests(unittest.TestCase):
    def test_continuous_input_has_total_deadline(self):
        class ContinuousSocket:
            def __init__(self):
                self.timeout = 0.2
                self.started = time.monotonic()

            def gettimeout(self):
                return self.timeout

            def settimeout(self, value):
                self.timeout = value

            def recv(self, size):
                # 永远有数据，也永远没有换行；看门狗避免回归时测试本身卡死。
                if time.monotonic() - self.started > 0.5:
                    raise AssertionError("连续输入未在总时限内退出")
                time.sleep(0.001)
                return b"\x00" * 32

        sensor = M8128B1Sensor({"transport": "ethernet"})
        for operation in (sensor._drain_input, lambda: sensor._read_ack_from_mixed_stream(0.05)):
            sensor.sock = ContinuousSocket()
            started = time.monotonic()
            operation()
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertEqual(sensor.sock.gettimeout(), 0.2)

    def connect_sensor(self, **options):
        server = TcpSensor(**options)
        self.addCleanup(server.close)
        sensor = M8128B1Sensor({
            "transport": "ethernet", "ip": "127.0.0.1", "tcp_port": server.port,
            "timeout": 0.2, "zero_settle_s": 0.0,
            "stop_ack_timeout": 0.15, "zero_ack_timeout": 0.1,
        })
        self.addCleanup(sensor.disconnect)
        self.assertTrue(sensor.connect())
        self.assertTrue(sensor.start_stream())
        self.wait_for_frames(sensor)
        return sensor, server

    def wait_for_frames(self, sensor):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            frames = sensor.read_data_with_timestamp()
            if frames:
                return frames
            time.sleep(0.005)
        self.fail("实时数据流未恢复")

    def test_zero_with_continuous_stream_and_mixed_ack(self):
        sensor, server = self.connect_sensor()
        progress = []
        started = time.monotonic()
        self.assertTrue(sensor.zero_channels(progress_callback=progress.append))
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 2.0)  # 即使配置为 0，也等待至少 2 秒。
        self.assertLess(elapsed, 4.0)
        self.assertEqual(server.commands[:4], [
            "AT+GSD", "AT+GSD=STOP", "AT+ADJZF=1;1;1;1;1;1", "AT+GSD",
        ])
        self.assertTrue(progress)
        for _, _, groups in self.wait_for_frames(sensor):
            self.assertTrue(all(values == (0.0,) * 6 for values in groups))

    def test_stop_without_ack_accepts_quiet_stream(self):
        sensor, server = self.connect_sensor(stop_ack=False)
        with patch("drivers.sensors.m8128b1.time.sleep"):
            self.assertTrue(sensor.zero_channels())
        self.assertIn("AT+ADJZF=1;1;1;1;1;1", server.commands)
        self.wait_for_frames(sensor)

    def test_ignored_stop_times_out_without_sending_zero(self):
        sensor, server = self.connect_sensor(ignore_stop=True)
        started = time.monotonic()
        self.assertFalse(sensor.zero_channels())
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertFalse(any(cmd.startswith("AT+ADJZF=") for cmd in server.commands))
        self.wait_for_frames(sensor)

    def test_zero_error_restores_stream(self):
        sensor, _ = self.connect_sensor(zero_reply="error")
        self.assertFalse(sensor.zero_channels())
        self.assertTrue(sensor._streaming.is_set())
        self.wait_for_frames(sensor)

    def test_zero_ack_timeout_restores_stream(self):
        sensor, _ = self.connect_sensor(zero_reply="missing")
        started = time.monotonic()
        self.assertFalse(sensor.zero_channels())
        self.assertLess(time.monotonic() - started, 2.0)
        self.wait_for_frames(sensor)

    def test_restart_failure_returns_false(self):
        sensor, _ = self.connect_sensor()
        with patch.object(sensor, "start_stream", return_value=False), patch(
            "drivers.sensors.m8128b1.time.sleep"
        ):
            self.assertFalse(sensor.zero_channels())


class ManagerZeroTests(unittest.TestCase):
    def test_driver_failure_is_not_reported_as_success(self):
        device = SimpleNamespace(zero_channels=Mock(return_value=False))
        manager = SimpleNamespace(liuzhouli=device, connection_status={"liuzhouli": True})
        self.assertFalse(SensorManager.liuzhouli_zero(manager))

    def test_success_clears_old_display_data_and_forwards_progress(self):
        progress = Mock()
        device = SimpleNamespace(zero_channels=Mock(return_value=True))
        manager = SimpleNamespace(
            liuzhouli=device, connection_status={"liuzhouli": True},
            _liuzhouli_last_sample={"Fx": 10}, _liuzhouli_sample_queue=deque([1]),
            latest_data={"liuzhouli": {"Fx": 10}},
        )
        self.assertTrue(SensorManager.liuzhouli_zero(manager, progress))
        device.zero_channels.assert_called_once_with(progress_callback=progress)
        self.assertIsNone(manager.latest_data["liuzhouli"])
        self.assertFalse(manager._liuzhouli_sample_queue)


class WindowZeroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui.main_window import MainWindow
        self.window = MainWindow(str(PROJECT_ROOT / "config" / "default.yaml"))
        self.addCleanup(self.cleanup_window)
        self.window.sensor_manager.connection_status["liuzhouli"] = True
        self.window._update_liuzhouli_zero_button()
        self.warning = self.enterContext(patch("gui.main_window.QMessageBox.warning"))
        self.info = self.enterContext(patch("gui.main_window.QMessageBox.information"))

    def enterContext(self, context):
        # 兼容 Python 3.10。
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    def cleanup_window(self):
        self.window.log_widget.stop_capture()
        self.window.stats_timer.stop()
        self.window.sensor_manager.display_timer.stop()
        self.window.deleteLater()
        self.app.processEvents()

    def test_recording_blocks_zero(self):
        self.window.data_recorder.is_recording = True
        self.addCleanup(setattr, self.window.data_recorder, "is_recording", False)
        with patch.object(self.window.sensor_manager, "liuzhouli_zero") as zero:
            self.window._on_liuzhouli_zero_requested()
            zero.assert_not_called()
        self.warning.assert_called_once()

    def test_background_log_updates_document_on_gui_thread(self):
        from PyQt5.QtCore import QThread
        threads = []
        log = self.window.log_widget
        log.log_text.document().contentsChanged.connect(
            lambda: threads.append(QThread.currentThread())
        )
        worker = threading.Thread(target=lambda: log.stdout_redirector.write("test background log"))
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.app.processEvents()
        self.assertTrue(threads)
        self.assertTrue(all(thread == self.app.thread() for thread in threads))

    def test_failure_finishes_on_gui_thread_and_unlocks_controls(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def zero(progress_callback):
            progress_callback("test zero progress")
            release.wait(timeout=1)
            raise RuntimeError("simulated zero failure")

        with patch.object(self.window.sensor_manager, "liuzhouli_zero", side_effect=zero) as operation:
            self.window._on_liuzhouli_zero_requested()
            self.assertTrue(self.window._liuzhouli_zero_busy)
            self.assertFalse(self.window.connection_panel.isEnabled())
            self.assertFalse(self.window.recording_panel.start_btn.isEnabled())
            self.window._on_liuzhouli_zero_requested()
            release.set()
            deadline = time.monotonic() + 2
            while self.window._liuzhouli_zero_busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.005)
            self.assertFalse(self.window._liuzhouli_zero_busy)
            operation.assert_called_once()
        self.assertTrue(self.window.connection_panel.isEnabled())
        self.assertTrue(self.window.recording_panel.start_btn.isEnabled())
        self.assertTrue(self.window.control_panel.liuzhouli_zero_btn.isEnabled())
        self.warning.assert_called_once()
        self.info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
