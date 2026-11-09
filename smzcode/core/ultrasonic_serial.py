"""DM-MC02 超声波串口接收。"""
import re
import time

from PyQt5.QtCore import QIODevice, QObject, QTimer, pyqtSignal
from PyQt5.QtSerialPort import QSerialPort


_SAMPLE_LINE = re.compile(
    rb"distance_mm=(NA|\d{1,9}),speed_mm_s=(NA|[+-]?\d{1,9})"
    rb"(?:,status=([A-Z][A-Z_]{0,31}))?"
)


class UltrasonicSerial(QObject):
    sample_received = pyqtSignal(dict)
    connection_changed = pyqtSignal(bool)
    data_timeout = pyqtSignal()
    error_occurred = pyqtSignal(str)

    MAX_LINE_BYTES = 128

    def __init__(self, parent=None):
        super().__init__(parent)
        self._port = QSerialPort(self)
        self._port.setReadBufferSize(4096)
        self._port.readyRead.connect(self._read_available)
        self._port.errorOccurred.connect(self._on_port_error)
        self._pending = b""

        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(1000)
        self._watchdog.timeout.connect(self.data_timeout.emit)

    @property
    def is_open(self):
        return self._port.isOpen()

    def open(self, port_name: str, baud: int):
        if self.is_open:
            return
        self._pending = b""
        self._port.setPortName(port_name)
        self._port.setBaudRate(baud)
        self._port.setDataBits(QSerialPort.Data8)
        self._port.setParity(QSerialPort.NoParity)
        self._port.setStopBits(QSerialPort.OneStop)
        self._port.setFlowControl(QSerialPort.NoFlowControl)
        if not self._port.open(QIODevice.ReadOnly):
            raise OSError(self._port.errorString())

        self._watchdog.start()
        self.connection_changed.emit(True)

    def close(self):
        self._watchdog.stop()
        self._pending = b""
        if self.is_open:
            self._port.close()
            self.connection_changed.emit(False)

    def _on_port_error(self, error):
        if self.is_open and error in (QSerialPort.ResourceError, QSerialPort.ReadError):
            message = self._port.errorString()
            self.close()
            self.error_occurred.emit(message)

    def _read_available(self):
        self._consume(bytes(self._port.readAll()))

    def _consume(self, chunk: bytes):
        # None means an overlong line is being discarded until its newline.
        if self._pending is None:
            _, separator, chunk = chunk.partition(b"\n")
            if not separator:
                return
            self._pending = b""

        *lines, tail = (self._pending + chunk).split(b"\n")
        self._pending = tail if len(tail) <= self.MAX_LINE_BYTES else None

        for line in lines:
            if len(line) > self.MAX_LINE_BYTES:
                continue
            match = _SAMPLE_LINE.fullmatch(line.removesuffix(b"\r"))
            if match is None:
                continue

            distance, speed, status = match.groups()
            self._watchdog.start()
            self.sample_received.emit({
                "timestamp_ms": time.time_ns() // 1_000_000,
                "host_received_ns": time.perf_counter_ns(),
                "distance_mm": None if distance == b"NA" else int(distance),
                "speed_mm_s": None if speed == b"NA" else int(speed),
                "status": status.decode("ascii") if status else ("WARMUP" if speed == b"NA" else "OK"),
            })
