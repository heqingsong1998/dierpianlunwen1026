"""
数据记录器 - 黏附脚、六轴力与控制事件的异步 CSV 写入
"""
import os
import csv
import time
from datetime import datetime
from typing import Dict, Any, Optional
from threading import Lock, Thread
import queue
import json
from collections import OrderedDict, deque

FOOT_RAW_COLUMNS = [
    'Fx1_raw', 'Fy1_raw', 'Fz1_raw', 'Mx1_raw', 'My1_raw', 'JJJ1_1_raw', 'JJJ1_2_raw',
    'Fx2_raw', 'Fy2_raw', 'Fz2_raw', 'Mx2_raw', 'My2_raw', 'JJJ2_1_raw', 'JJJ2_2_raw',
]
FOOT_CAL_COLUMNS = [
    'Fx1_cal', 'Fy1_cal', 'Fz1_cal', 'Mx1_cal', 'My1_cal',
    'Fx2_cal', 'Fy2_cal', 'Fz2_cal', 'Mx2_cal', 'My2_cal',
]
FOOT_METADATA_COLUMNS = [
    'sample_sequence', 'ready_us', 'host_received_ns', 'baseline_generation', 'pin_state',
]
FOOT_CSV_COLUMNS = ['timestamp', 'timestamp_ms', *FOOT_RAW_COLUMNS,
                    *FOOT_CAL_COLUMNS, *FOOT_METADATA_COLUMNS]
FOOT_BASELINE_INDEX = FOOT_CSV_COLUMNS.index('baseline_generation')
PROTOCOL_COLUMNS = {
    'controller': ['sensor_ts_ms', 'event', 'event_ns', 'sample_sequence', 'ready_us',
                   'host_received_ns', 'input_received_ns', 'processing_started_ns',
                   'submitted_ns', 'controller_us', 'fz1_n', 'fz2_n',
                   'threshold_fz1_n', 'threshold_fz2_n', 'threshold_reached', 'is_trigger',
                   'action', 'command_id', 'state', 'settings_revision', 'calibration_id',
                   'dropped_inputs', 'message', 'config_json'],
    'stage_pairs': ['sensor_ts_ms', 'host_received_ns', 'protocol_version',
                    'sample_sequence', 'ready_us', 'baseline_generation',
                    *[f'pair_{i}' for i in range(8)], 'dropped_commands', 'dropped_tx_frames'],
    'commands': ['sensor_ts_ms', 'command_id', 'action', 'sample_sequence', 'ready_us',
                 'input_received_ns', 'submitted_ns', 'send_ok', 'legacy', 'error'],
    'feedback': ['sensor_ts_ms', 'host_received_ns', 'protocol_version', 'status',
                 'command_id', 'action', 'pin_state', 'flags', 'sample_sequence',
                 'ready_us', 'received_us', 'gpio_written_us', 'round_trip_us',
                 'baseline_generation', 'dropped_commands', 'input_received_ns',
                 'submitted_ns', 'controller_us', 'transport_sum_us', 'gpio_wait_us',
                 'gpio_changed', 'zero_pending', 'timing_valid', 'timing_error'],
}


class DataRecorder:
    """统一的数据记录器"""

    # 仅推迟 CSV 写入，不阻塞接收或控制器；缺失的补充帧不能丢掉主数据。
    FOOT_PAIR_WAIT_S = 0.25
    FOOT_PAIR_CACHE_SIZE = 256
    FOOT_PENDING_LIMIT = 1000

    def __init__(self, base_dir: str = "data/sessions"):
        """初始化数据记录器"""
        self.base_dir = base_dir
        self.session_dir: Optional[str] = None
        self.is_recording = False

        self.csv_files: Dict[str, Any] = {}
        self.csv_writers: Dict[str, Any] = {}

        # 数据队列（异步写入）
        self.data_queues = {
            'nianfujiao_style0': queue.Queue(maxsize=5000),
            'liuzhouli': queue.Queue(maxsize=10000),
            'nianfujiao_frames': queue.Queue(maxsize=5000),
            **{f'nianfujiao_{kind}': queue.Queue(maxsize=2000) for kind in PROTOCOL_COLUMNS},
        }

        # 写入线程
        self.write_thread = None
        self.write_thread_running = False
        self.write_batch_size = 200
        self.write_idle_sleep_s = 0.001
        self.flush_interval_s = 0.2

        # 保护跨接收线程/同步线程的足端记录与 0x124 基准版本配对。
        self.lock = Lock()
        self._foot_generations = OrderedDict()
        self._pending_foot_rows = {
            'nianfujiao_style0': deque(),
            'nianfujiao_frames': deque(),
        }

        self.data_counts = {
            'nianfujiao': 0,
            'liuzhouli': 0,
        }

        self.start_time: Optional[float] = None

    def _current_timestamp(self) -> str:
        """返回带毫秒和单位的时间戳字符串，避免被表格软件自动格式化"""
        now = datetime.now()
        return f"{now:%Y-%m-%d %H:%M:%S}.{now.microsecond // 1000:03d} ms"

    def _timestamp_ms_to_str(self, timestamp_ms: Any) -> str:
        """将 timestamp_ms（毫秒）转换为人类可读时间戳字符串。"""
        try:
            ts_ms_int = int(timestamp_ms)
        except Exception:
            return self._current_timestamp()

        dt = datetime.fromtimestamp(ts_ms_int / 1000.0)
        dt = dt.replace(microsecond=(ts_ms_int % 1000) * 1000)
        return f"{dt:%Y-%m-%d %H:%M:%S}.{ts_ms_int % 1000:03d} ms"

    # ==================== 启停 ====================

    def start_recording(self) -> bool:
        """开始记录"""
        if self.is_recording:
            print("[WARN] 已经在记录中")
            return False

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.session_dir = os.path.join(self.base_dir, f"session_{timestamp}")
        os.makedirs(self.session_dir, exist_ok=True)

        try:
            self._create_nianfujiao_csv()
            self._create_liuzhouli_csv()
            self._create_nianfujiao_csv('nianfujiao_frames')
            for kind in PROTOCOL_COLUMNS:
                self._create_protocol_csv(kind)
            self.data_counts = {key: 0 for key in self.data_counts}

            with self.lock:
                self._foot_generations.clear()
                for pending in self._pending_foot_rows.values():
                    pending.clear()
                self.is_recording = True
            self.start_time = time.time()

            # 启动异步写入线程
            self.write_thread_running = True
            self.write_thread = Thread(target=self._write_loop, daemon=True)
            self.write_thread.start()

            print(f"[INFO] 开始记录数据到: {self.session_dir}")
            print("[INFO] 分通道队列与后台批量写入已启动")
            return True

        except Exception as e:
            print(f"[ERROR] 启动记录失败: {e}")
            import traceback
            traceback.print_exc()
            self._close_all_files()
            return False

    def stop_recording(self) -> bool:
        """停止记录"""
        if not self.is_recording:
            print("[WARN] 未在记录中")
            return False

        with self.lock:
            self.is_recording = False
            # 先把尚未等到 0x124 的行也入写盘队列，版本留空，原时间戳不变。
            self._release_foot_rows_locked(force=True)
            self._foot_generations.clear()

        # 停止写入线程
        print("[INFO] 正在停止写入线程...")
        self.write_thread_running = False
        if self.write_thread:
            self.write_thread.join(timeout=3.0)

        # 写完队列中剩余数据
        self._flush_all_queues()

        self._close_all_files()

        duration = 0.0
        if self.start_time is not None:
            duration = time.time() - self.start_time

        print(f"[INFO] 记录已停止，时长: {duration:.2f}秒")
        print(f"[INFO] 数据统计: {self.data_counts}")

        # ✅ 不再做离线对齐，简单结束
        return True

    def write_controller_snapshot(self, snapshot):
        """实验开始时保存实际阈值、标定系数及控制器状态。"""
        if self.is_recording and self.session_dir:
            path = os.path.join(self.session_dir, 'controller_config.json')
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(snapshot, handle, ensure_ascii=False, indent=2, allow_nan=False)

    # ==================== 异步写入线程 ====================

    def _write_loop(self):
        """异步写入循环（后台线程）"""
        print("[INFO] 数据写入线程已启动")
        last_flush_time = time.perf_counter()

        while self.write_thread_running or any(not q.empty() for q in self.data_queues.values()):
            try:
                with self.lock:
                    self._release_foot_rows_locked()
                wrote_any = False
                # 从每个队列取数据并写入
                for sensor_id, q in self.data_queues.items():
                    batch_count = 0
                    while not q.empty() and batch_count < self.write_batch_size:
                        try:
                            row = q.get_nowait()
                            if sensor_id in self.csv_writers:
                                self.csv_writers[sensor_id].writerow(row)
                                batch_count += 1
                                wrote_any = True
                        except queue.Empty:
                            break

                # 定期刷新文件（降低 flush 频率，提升吞吐）
                now = time.perf_counter()
                if wrote_any and (now - last_flush_time) >= self.flush_interval_s:
                    for file in self.csv_files.values():
                        file.flush()
                    last_flush_time = now

                if not wrote_any:
                    time.sleep(self.write_idle_sleep_s)

            except Exception as e:
                print(f"[ERROR] 写入循环异常: {e}")
                time.sleep(0.1)

        # 退出前确保落盘
        for file in self.csv_files.values():
            file.flush()
        print("[INFO] 数据写入线程已停止")

    def _flush_all_queues(self):
        """清空所有队列"""
        print("[INFO] 正在写入剩余数据...")
        for sensor_id, q in self.data_queues.items():
            count = 0
            while not q.empty():
                try:
                    row = q.get_nowait()
                    if sensor_id in self.csv_writers:
                        self.csv_writers[sensor_id].writerow(row)
                        count += 1
                except queue.Empty:
                    break
            if count > 0:
                print(f"[INFO] {sensor_id} 写入剩余 {count} 条数据")

        for file in self.csv_files.values():
            file.flush()

    # ==================== 写入接口（给 SensorManager 调） ====================

    def write_hwt606_data(self, sensor_id: str, data: Dict[str, Any]):
        """
        写入 HWT606 传感器数据（放入队列）

        约定：
        - data['timestamp_ms'] 由 SensorManager 统一线程给出（统一时间轴）
        - 这里额外生成一个人类可读的 timestamp 字符串
        """
        if not self.is_recording:
            return

        try:
            ts_ms = data.get('timestamp_ms', '')
            timestamp_str = self._timestamp_ms_to_str(ts_ms)

            row = [
                timestamp_str,     # 人类可读时间
                ts_ms,             # 统一帧时间戳（ms）
                data.get('acc_x', 0.0),
                data.get('acc_y', 0.0),
                data.get('acc_z', 0.0),
                data.get('gyro_x', 0.0),
                data.get('gyro_y', 0.0),
                data.get('gyro_z', 0.0),
                data.get('angle_x', 0.0),
                data.get('angle_y', 0.0),
                data.get('angle_z', 0.0),
                data.get('temperature', 0.0),
                data.get('vel_x', 0.0),   # ★ 新增：速度
                data.get('vel_y', 0.0),
                data.get('vel_z', 0.0),
            ]
            self.data_queues[sensor_id].put_nowait(row)
            self.data_counts[sensor_id] += 1
        except queue.Full:
            print(f"[WARN] {sensor_id} 数据队列已满，丢弃数据")
        except Exception as e:
            print(f"[ERROR] 写入{sensor_id}数据失败: {e}")

    def write_nianfujiao_data(self, style: int, data: Dict[str, Any],
                              queue_key: str = 'nianfujiao_style0'):
        """
        两个足端文件共用 31 列；内部仍保留原 24 字段协议视图。

        style0 的 timestamp_ms 为六轴主时间轴配对时间，frames 为足端接收时间。
        等待基准版本只发生在保存端，保留原始接收时间及同步配对顺序。
        """
        if not self.is_recording:
            return
        try:
            if style != 0:
                return
            if queue_key not in self._pending_foot_rows:
                raise ValueError(f'未知足端记录队列: {queue_key}')
            ts_ms = data.get('timestamp_ms', '')
            timestamp_str = self._timestamp_ms_to_str(ts_ms)
            raw = data.get('raw', [])
            calibrated = data.get('calibrated', [])
            if len(raw) != 24 or len(calibrated) != 24:
                raise ValueError('足端导出需要完整的 24 字段原始/标定内部视图')
            fz1 = data.get('fz1_total_cal')
            fz2 = data.get('fz2_total_cal')
            if fz1 is None:
                fz1 = calibrated[2] + calibrated[14]
            if fz2 is None:
                fz2 = calibrated[9] + calibrated[15]
            row = ([timestamp_str, ts_ms] + list(raw[:14]) + [
                calibrated[0], calibrated[1], float(fz1), calibrated[3], calibrated[4],
                calibrated[7], calibrated[8], float(fz2), calibrated[10], calibrated[11],
            ] + [data.get(key, '') for key in FOOT_METADATA_COLUMNS])
            with self.lock:
                if not self.is_recording:
                    return
                key = self._foot_sample_key(data)
                self._pending_foot_rows[queue_key].append((key, row, time.monotonic()))
                self._release_foot_rows_locked()
        except Exception as e:
            print(f"[ERROR] 写入粘附脚数据失败: {e}")

    @staticmethod
    def _foot_sample_key(data):
        sequence, ready_us = data.get('sample_sequence'), data.get('ready_us')
        if sequence in (None, '', 0) or ready_us in (None, ''):
            return None
        return sequence, ready_us

    def _release_foot_rows_locked(self, force=False):
        """持有 self.lock；各文件严格按原入队顺序写出，避免补帧改变行顺序。"""
        now = time.monotonic()
        for queue_key, pending in self._pending_foot_rows.items():
            while pending:
                key, row, queued_at = pending[0]
                if row[FOOT_BASELINE_INDEX] in (None, '') and key in self._foot_generations:
                    row[FOOT_BASELINE_INDEX] = self._foot_generations[key]
                missing = row[FOOT_BASELINE_INDEX] in (None, '')
                if (missing and key is not None and not force and
                        now - queued_at < self.FOOT_PAIR_WAIT_S and
                        len(pending) <= self.FOOT_PENDING_LIMIT):
                    break
                pending.popleft()
                try:
                    self.data_queues[queue_key].put_nowait(row)
                    count_key = 'nianfujiao' if queue_key == 'nianfujiao_style0' else queue_key
                    self.data_counts[count_key] = self.data_counts.get(count_key, 0) + 1
                except queue.Full:
                    print(f'[WARN] {queue_key} 数据队列已满，丢弃数据')

    def write_nianfujiao_aux(self, kind: str, data: Dict[str, Any]):
        """异步记录真实新帧/对和/发送事件/反馈，不依赖六轴力或 GUI 刷新。"""
        if not self.is_recording:
            return
        if kind == 'frames':
            self.write_nianfujiao_data(0, data, 'nianfujiao_frames')
            return
        if kind not in PROTOCOL_COLUMNS:
            raise ValueError(f'未知黏附脚记录类型: {kind}')
        key = f'nianfujiao_{kind}'
        row = [self._timestamp_ms_to_str(data.get('sensor_ts_ms'))]
        row.extend(data.get(column, '') for column in PROTOCOL_COLUMNS[kind])
        try:
            with self.lock:
                if not self.is_recording:
                    return
                if kind == 'stage_pairs' and data.get('protocol_version') == 1:
                    sample_key = self._foot_sample_key(data)
                    generation = data.get('baseline_generation')
                    if sample_key is not None and generation is not None:
                        self._foot_generations[sample_key] = generation
                        self._foot_generations.move_to_end(sample_key)
                        while len(self._foot_generations) > self.FOOT_PAIR_CACHE_SIZE:
                            self._foot_generations.popitem(last=False)
                        self._release_foot_rows_locked()
                self.data_queues[key].put_nowait(row)
                self.data_counts[key] = self.data_counts.get(key, 0) + 1
        except queue.Full:
            print(f'[WARN] {key} 数据队列已满，丢弃数据')

    def write_liuzhouli_data(self, data: Dict[str, Any]):
        """
        写入六轴力传感器数据（放入队列）

        约定：
        - data['timestamp_ms']：统一帧时间戳（ms）
        - data['raw_timestamp_ns']：本机 perf_counter_ns 接收时间（不是设备硬件时间）
        """
        if not self.is_recording:
            return

        try:
            ts_ms = data.get('timestamp_ms', '')
            timestamp_str = self._timestamp_ms_to_str(ts_ms)
            raw_ns = data.get('raw_timestamp_ns')

            row = [
                timestamp_str,                     # 人类可读
                ts_ms,                             # 统一帧时间
                raw_ns if raw_ns is not None else '',
                float(data.get('Fx', 0.0)),
                float(data.get('Fy', 0.0)),
                float(data.get('Fz', 0.0)),
                float(data.get('Mx', 0.0)),
                float(data.get('My', 0.0)),
                float(data.get('Mz', 0.0)),
            ]
            self.data_queues['liuzhouli'].put_nowait(row)
            self.data_counts['liuzhouli'] += 1
        except queue.Full:
            print(f"[WARN] liuzhouli 数据队列已满，丢弃数据")
        except Exception as e:
            print(f"[ERROR] 写入六轴力数据失败: {e}")

    # ==================== CSV 创建 ====================

    def _create_hwt606_csv(self, sensor_id: str):
        """创建 HWT606 传感器 CSV 文件"""
        filepath = os.path.join(self.session_dir, f"{sensor_id}.csv")
        file = open(filepath, 'w', newline='', encoding='utf-8')
        writer = csv.writer(file)

        # 表头：增加 timestamp_ms 列（统一时间轴）
        writer.writerow([
            'timestamp',        # 人类可读
            'timestamp_ms',     # 统一帧时间戳（ms）
            'acc_x', 'acc_y', 'acc_z',
            'gyro_x', 'gyro_y', 'gyro_z',
            'angle_x', 'angle_y', 'angle_z',
            'temperature',
            'vel_x', 'vel_y', 'vel_z',   # ★ 新增：速度
        ])

        self.csv_files[sensor_id] = file
        self.csv_writers[sensor_id] = writer

    def _create_nianfujiao_csv(self, queue_key='nianfujiao_style0'):
        """创建两个同结构的足端 CSV，统一为 31 列。"""
        filepath0 = os.path.join(self.session_dir, f"{queue_key}.csv")
        file0 = open(filepath0, 'w', newline='', encoding='utf-8')
        writer0 = csv.writer(file0)

        writer0.writerow(FOOT_CSV_COLUMNS)

        self.csv_files[queue_key] = file0
        self.csv_writers[queue_key] = writer0

    def _create_protocol_csv(self, kind):
        key = f'nianfujiao_{kind}'
        file = open(os.path.join(self.session_dir, f'{key}.csv'), 'w', newline='', encoding='utf-8')
        writer = csv.writer(file)
        writer.writerow(['timestamp'] + PROTOCOL_COLUMNS[kind])
        self.csv_files[key] = file
        self.csv_writers[key] = writer

    def _create_liuzhouli_csv(self):
        """创建六轴力传感器 CSV 文件"""
        filepath = os.path.join(self.session_dir, "liuzhouli.csv")
        file = open(filepath, 'w', newline='', encoding='utf-8')
        writer = csv.writer(file)

        writer.writerow([
            'timestamp',        # 人类可读
            'timestamp_ms',     # 统一帧时间戳（ms）
            'raw_timestamp_ns',  # 设备高精度采集时刻（纳秒，可选）
            'Fx', 'Fy', 'Fz',
            'Mx', 'My', 'Mz',
        ])

        self.csv_files['liuzhouli'] = file
        self.csv_writers['liuzhouli'] = writer

    # ==================== 收尾 & 状态 ====================

    def _close_all_files(self):
        """关闭所有 CSV 文件"""
        for file in self.csv_files.values():
            try:
                file.close()
            except Exception:
                pass

        self.csv_files.clear()
        self.csv_writers.clear()

    def get_recording_duration(self) -> float:
        """获取记录时长（秒）"""
        if not self.is_recording or self.start_time is None:
            return 0.0
        return time.time() - self.start_time

    def get_session_path(self) -> Optional[str]:
        """获取当前会话目录路径"""
        return self.session_dir
