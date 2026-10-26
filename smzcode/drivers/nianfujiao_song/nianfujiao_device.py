"""
粘附脚传感器设备主类

支持 0x123 通道/测时、0x124 stage 对和、0x125 命令反馈混合接收。
"""
import struct
import time
import threading
from collections import OrderedDict
from typing import Optional

import serial

from .base import NianFuJiaoBase, Style0Data
from .protocol import (
    FrameParser, StagePairData, CommandAck, PendingCommand,
    DATA_ID, STAGE_PAIR_ID, ACK_ID, PROTOCOL_VERSION,
    build_command_frame, match_feedback,
)
from .utils import (
    CsvWriter,
    get_style0_headers,
    send_init_sequence,
    send_control_command,
    send_custom_command,
    format_style0_data,
    CONTROL_COMMANDS,
    # 标定函数
    cal_Fx1, cal_Fy1, cal_Fz1_plus, cal_Mx1, cal_My1,
    cal_Fx2, cal_Fy2, cal_Fz2_plus, cal_Mx2, cal_My2,
    cal_FZ1_minus, cal_FZ2_minus,
)


class NianFuJiaoDevice(NianFuJiaoBase):
    """粘附脚传感器设备类"""

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        enable_csv: bool = True,
        print_data: bool = True,
        enable_calibration: bool = True,
    ):
        """
        初始化粘附脚设备

        Args:
            port: 串口号
            baud: 波特率
            enable_csv: 是否启用CSV记录
            print_data: 是否打印数据
            enable_calibration: 是否启用标定计算
        """
        super().__init__(port, baud)
        self.enable_csv = enable_csv
        self.print_data = print_data
        self.enable_calibration = enable_calibration

        # CSV 写入器
        self.csv0: Optional[CsvWriter] = None

        # 接收缓冲区
        self._parser = FrameParser()
        self.rx_buf = self._parser.buffer
        self.protocol_version = 0
        self._write_lock = threading.RLock()
        self._pending_lock = threading.Lock()
        self._pending_commands = OrderedDict()
        self._command_id = 0

        # 读取线程
        self._read_thread: Optional[threading.Thread] = None
        self._stop_flag = False

    # ========= 串口控制 =========
    def open(self):
        """打开设备"""
        if self._is_open:
            print(f"[WARN] 设备已经打开: {self.port}")
            return

        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                timeout=0.1,
                write_timeout=0.5,
            )
            self._is_open = True
            self.rx_buf.clear()
            self.protocol_version = 0
            with self._pending_lock:
                self._pending_commands.clear()
            print(f"[INFO] 串口已打开: {self.port} @ {self.baud}")

            # 初始化 CSV 写入器
            if self.enable_csv:
                self._init_csv_writers()

            # 发送初始化帧
            self.send_init_frames()

            # 启动读取线程
            self._start_read_thread()

        except Exception as e:
            print(f"[ERROR] 打开串口失败: {e}")
            self._is_open = False

    def close(self):
        """关闭设备"""
        self._stop_flag = True
        # 停止读取线程
        if self._read_thread and self._read_thread.is_alive():
            self._read_thread.join(timeout=1.0)
        self._read_thread = None

        # 关闭串口
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except Exception:
                pass
        self.serial_conn = None
        self._is_open = False
        self.rx_buf.clear()
        with self._pending_lock:
            self._pending_commands.clear()

        # 关闭 CSV
        if self.csv0:
            self.csv0.close()
            self.csv0 = None

        print("[INFO] 设备已关闭")

    def _init_csv_writers(self):
        """初始化 CSV 写入器"""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.csv0 = CsvWriter(f"style0_{timestamp}.csv", get_style0_headers() + [
            'sample_sequence', 'ready_us', 'host_received_ns', 'sensor_ts_ms'])

    def _start_read_thread(self):
        """启动读取线程"""
        self._stop_flag = False
        self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._read_thread.start()

    def _read_loop(self):
        """读取线程主循环：阻塞读，按设备发送节奏消费"""
        if not self.serial_conn:
            print("[ERROR] 串口未打开，无法读取数据")
            return

        # ACK/stage frames are shorter than 0x123; never wait for 56 bytes to
        # consume a complete 40-byte ACK. The parser handles arbitrary chunks.
        min_chunk = 1
        while not self._stop_flag:
            try:
                # 阻塞读取：若有缓存则多读，否则至少读一帧长度
                to_read = self.serial_conn.in_waiting or min_chunk
                chunk = self.serial_conn.read(to_read)
                if chunk:
                    self.rx_buf.extend(chunk)
                    self._consume_buffer()
            except Exception as e:
                print(f"[ERROR] 串口读取异常: {e}")
                time.sleep(0.1)


    def _consume_buffer(self):
        """完整帧一到达就回调，独立于 GUI 与六轴力的记录节拍。"""
        for can_id, payload in self._parser.feed(b''):
            received_ns = time.perf_counter_ns()
            wall_ms = time.time_ns() // 1_000_000
            if can_id == DATA_ID:
                self._handle_style0(payload, received_ns, wall_ms)
            elif can_id == STAGE_PAIR_ID:
                pairs = StagePairData.decode(payload, received_ns, wall_ms)
                self.protocol_version = pairs.protocol_version
                self._notify_callbacks('stage_pairs', pairs.to_dict())
            elif can_id == ACK_ID:
                ack = CommandAck.decode(payload, received_ns, wall_ms)
                with self._pending_lock:
                    pending = self._pending_commands.pop(
                        (ack.command_id, ack.sample_sequence), None)
                self._notify_callbacks('command_ack', match_feedback(ack, pending))

    def _handle_style0(self, payload, received_ns, wall_ms):
        """保留原通道视图，测时字段按无符号 uint32 单独解释。"""
        fields = struct.unpack("<24h", payload)  # 24个int16小端

        # 映射到数据结构（24字段）
        data = Style0Data(
            Fx1=fields[0],
            Fy1=fields[1],
            Fz1_plus=fields[2],
            Mx1=fields[3],
            My1=fields[4],
            JJJ1_1=fields[5],
            JJJ1_2=fields[6],
            Fx2=fields[7],
            Fy2=fields[8],
            Fz2_plus=fields[9],
            Mx2=fields[10],
            My2=fields[11],
            flag1=fields[12],
            flag2=fields[13],
            FZ1_minus=fields[14],
            FZ2_minus=fields[15],
            flag_fz=fields[16],
            flag_fx=fields[17],
            flag_d=fields[18],
            reserved_1=fields[19],
            reserved_2=fields[20],
            reserved_3=fields[21],
            reserved_4=fields[22],
            reserved_5=fields[23],
            sample_sequence=struct.unpack_from('<I', payload, 40)[0],
            ready_us=struct.unpack_from('<I', payload, 44)[0],
            host_received_ns=received_ns,
            sensor_ts_ms=wall_ms,
        )

        # 计算标定值
        if self.csv0 and self.enable_calibration:
            cal_values = [
                cal_Fx1(data.Fx1),
                cal_Fy1(data.Fy1),
                cal_Fz1_plus(data.Fz1_plus),
                cal_Mx1(data.Mx1),
                cal_My1(data.My1),
                data.JJJ1_1,
                data.JJJ1_2,
                cal_Fx2(data.Fx2),
                cal_Fy2(data.Fy2),
                cal_Fz2_plus(data.Fz2_plus),
                cal_Mx2(data.Mx2),
                cal_My2(data.My2),
                data.flag1,
                data.flag2,
                cal_FZ1_minus(data.FZ1_minus),
                cal_FZ2_minus(data.FZ2_minus),
                data.flag_fz,
                data.flag_fx,
                data.flag_d,
                data.reserved_1,
                data.reserved_2,
                data.reserved_3,
                data.reserved_4,
                data.reserved_5,
            ]
        else:
            cal_values = [0.0] * 24

        # 写入 CSV（原始值 + 标定值）
        if self.csv0:
            row_data = list(fields) + cal_values + [
                data.sample_sequence, data.ready_us, received_ns, wall_ms]
            self.csv0.write_row(row_data)

        # 打印数据
        if self.print_data:
            # print(f"[STYLE0] {format_style0_data(data)}")
            if self.enable_calibration:
                # 需要的话，这里可以打印标定值
                pass

        # 通知回调
        self._notify_callbacks("style0", data)

        return True

    # ========= 初始化与控制指令 =========
    def send_init_frames(self):
        """发送初始化帧"""
        if not self.serial_conn or not self.serial_conn.is_open:
            print("[WARN] 串口未打开，无法发送初始化帧")
            return
        with self._write_lock:
            send_init_sequence(self.serial_conn)

    def send_timed_action(self, action: int, sample_sequence: int,
                          ready_us: int, input_received_ns: int):
        """控制器出口：B 是实际输入入队时间，C 在此处提交写入时记录。

        返回 PendingCommand 或 None。人工等待不得作为 B→C 的算法时间。
        """
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError('尚未收到 v1 的 0x124 帧，不能发送关联测时命令')
        if not 1 <= sample_sequence <= 0xFFFFFFFF or not 0 <= ready_us <= 0xFFFFFFFF:
            raise ValueError('测时命令需要有效的非零帧编号和 MCU 数据准备时间')
        if input_received_ns <= 0 or input_received_ns > time.perf_counter_ns():
            raise ValueError('输入时间必须来自电脑当前 perf_counter_ns 时间轴')
        return self._send_new_action(action, sample_sequence, ready_us, input_received_ns)

    def _send_new_action(self, action, sample_sequence=0, ready_us=None,
                         input_received_ns=None):
        with self._write_lock:
            if not self.serial_conn or not self.serial_conn.is_open:
                return None
            self._command_id = self._command_id % 0xFFFF + 1
            frame = build_command_frame(action, self._command_id, sample_sequence)
            pending = PendingCommand(
                action, self._command_id, sample_sequence, ready_us,
                input_received_ns, time.perf_counter_ns(), time.time_ns() // 1_000_000)
            key = (pending.command_id, sample_sequence)
            # Publish before write: an ACK can arrive on the RX thread before
            # serial.write/flush returns.
            with self._pending_lock:
                self._pending_commands[key] = pending
                while len(self._pending_commands) > 256:
                    self._pending_commands.popitem(last=False)
            try:
                if self.serial_conn.write(frame) != len(frame):
                    raise IOError('串口命令未完整写入')
                self.serial_conn.flush()
            except Exception as exc:
                with self._pending_lock:
                    self._pending_commands.pop(key, None)
                event = pending.to_dict()
                event.update(send_ok=False, error=str(exc), legacy=False)
                self._notify_callbacks('command_sent', event)
                return None
        event = pending.to_dict()
        event.update(send_ok=True, error='', legacy=False)
        self._notify_callbacks('command_sent', event)
        return pending

    def _send_button_command(self, name):
        action = {'reply': 0, 'engage': 1, 'zero': 3}[name]
        if self.protocol_version == PROTOCOL_VERSION:
            # Manual button has no controller-input B. Record C and ACK, but
            # never report human waiting as controller/transport latency.
            return self._send_new_action(action) is not None
        with self._write_lock:
            if not self.serial_conn or not self.serial_conn.is_open:
                return False
            submitted_ns = time.perf_counter_ns()
            wall_ms = time.time_ns() // 1_000_000
            ok = send_control_command(self.serial_conn, name)
        self._notify_callbacks('command_sent', {
            'action': action, 'command_id': 0, 'sample_sequence': 0,
            'ready_us': None, 'input_received_ns': None,
            'submitted_ns': submitted_ns, 'sensor_ts_ms': wall_ms,
            'send_ok': ok, 'error': '' if ok else 'legacy_send_failed', 'legacy': True})
        return ok

    def send_reply(self) -> bool:
        """发送回复指令"""
        if not self.serial_conn or not self.serial_conn.is_open:
            print("[WARN] 串口未打开，无法发送回复指令")
            return False
        return self._send_button_command("reply")

    def send_engage(self) -> bool:
        """发送吸合指令"""
        if not self.serial_conn or not self.serial_conn.is_open:
            print("[WARN] 串口未打开，无法发送吸合指令")
            return False
        return self._send_button_command("engage")

    def send_zero(self) -> bool:
        """发送清零指令"""
        if not self.serial_conn or not self.serial_conn.is_open:
            print("[WARN] 串口未打开，无法发送清零指令")
            return False
        return self._send_button_command("zero")

    # ========= 其他辅助接口 =========
    def list_available_commands(self):
        """列出可用的控制指令名称"""
        return list(CONTROL_COMMANDS.keys())

    def send_command(self, name: str) -> bool:
        """按名称发送控制指令"""
        if not self.serial_conn or not self.serial_conn.is_open:
            print("[WARN] 串口未打开，无法发送指令")
            return False
        if name not in CONTROL_COMMANDS:
            return False
        return self._send_button_command(name)

    def send_custom(self, frame: bytes, delay: float = 0.01) -> bool:
        """发送自定义完整帧"""
        if not self.serial_conn or not self.serial_conn.is_open:
            print("[WARN] 串口未打开，无法发送自定义指令")
            return False
        with self._write_lock:
            return send_custom_command(self.serial_conn, frame, delay)
