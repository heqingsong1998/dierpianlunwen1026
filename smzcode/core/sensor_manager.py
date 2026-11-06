"""
传感器管理器 - 管理黏附脚、六轴力传感器的连接和数据流
"""
import yaml
import os
from typing import Dict, Optional

from collections import deque

from PyQt5.QtCore import QObject, pyqtSignal, QTimer

import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from drivers.nianfujiao_song import NianFuJiaoDevice
from drivers.sensors.utils import create_sensor, initialize_sensor
from controller.algorithms.calibration import raw_from_sample
from controller.worker import FootController


class SensorManager(QObject):
    """传感器管理器"""

    # 信号定义（用于 GUI 显示）
    nianfujiao_data_updated = pyqtSignal(int, dict)  # (style, data)
    liuzhouli_data_updated = pyqtSignal(dict)
    nianfujiao_protocol_updated = pyqtSignal(dict)
    controller_updated = pyqtSignal(dict)

    # 数据记录信号（目前设计是通过回调直接写 CSV，此处可不用）
    nianfujiao_data_record = pyqtSignal(int, dict)
    liuzhouli_data_record = pyqtSignal(dict)

    connection_status_changed = pyqtSignal(str, bool)
    error_occurred = pyqtSignal(str, str)

    def __init__(self, config_path: str = "config/default.yaml"):
        super().__init__()

        # 加载配置
        self.config = self._load_config(config_path)

        # 传感器实例
        self.nianfujiao: Optional[NianFuJiaoDevice] = None
        self.liuzhouli = None

        # （可选）六轴力最近一条样本缓存
        self._liuzhouli_last_sample = None

        self._liuzhouli_sample_queue = deque(maxlen=1000)
        self._liuzhouli_last_sample = None

        # 连接状态
        self.connection_status: Dict[str, bool] = {
            'nianfujiao': False,
            'liuzhouli': False,
        }

        # 显示刷新定时器（10Hz）
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self._update_display)
        self.display_timer.setInterval(100)

        # 采集线程记录
        self.acquisition_threads: Dict[str, object] = {}

        # 统一采集线程相关
        self._unified_thread = None
        self._unified_running = False
        self._unified_loop_hz = 200  # 统一采集频率（100Hz）

        # 统一时间轴：帧计数 & 起始时间戳（ms）
        self._frame_index = 0
        self._record_start_ts_ms: Optional[int] = None

        # 最新数据缓存（用于 GUI 10Hz 显示）
        self.latest_data = {
            'nianfujiao': {0: None},
            'liuzhouli': None,
        }
        # 六轴力最新原始样本（来自驱动 drain_samples）
        self._liuzhouli_last_sample = None

        self.record_callback_nianfujiao = None
        self.record_callback_liuzhouli = None
        self.record_callback_nianfujiao_aux = None
        self.nianfujiao_frame_callback = None  # Optional observer of each real frame.
        self.latest_nianfujiao_protocol = {'stage_pairs': None, 'feedback': None}
        self.controller = FootController(self.nianfujiao_send_timed_action, self._on_controller_event)
        self._foot_zero_wait_generation = None
        # 粘附脚去重：记录每个 style 最近一次已写入的 sensor_ts_ms
        self._nianfujiao_last_written_sensor_ts_ms = {}
        liuzhouli_cfg = self.config.get('sensor', {}).get('m8128b1', {})
        # False: 与六轴力一一对应写入（允许粘附脚重复值）；
        # True: 仅粘附脚数据变化时写入（避免重复，降低队列压力）
        self._nianfujiao_on_change_only = bool(
            liuzhouli_cfg.get('nianfujiao_on_change_only', False)
        )

    # ==================== 配置 ====================

    def _load_config(self, config_path: str) -> dict:
        """加载配置文件"""
        full_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            config_path
        )
        with open(full_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    # ================ 统一采集线程相关 ================

    def _ensure_unified_thread_started(self):
        """若有需要采集的传感器且统一线程未启动，则启动统一采集线程"""
        from threading import Thread
        if self._unified_thread and self._unified_thread.is_alive():
            return

        # 没有任何需要统一采集的传感器则不启动
        if not (self.connection_status['liuzhouli'] or
                self.connection_status['nianfujiao']):
            return

        self._unified_running = True
        self._frame_index = 0
        self._record_start_ts_ms = None

        self._unified_thread = Thread(
            target=self._unified_acquisition_loop,
            daemon=True,
        )
        self._unified_thread.start()
        self.acquisition_threads['unified'] = self._unified_thread
        print(f"[INFO] 统一采集线程已启动，频率={self._unified_loop_hz} Hz")

    def _maybe_stop_unified_thread(self):
        """当所有需要统一采集的传感器都断开时，停止统一采集线程"""
        if (self.connection_status['liuzhouli'] or
                self.connection_status['nianfujiao']):
            return

        self._unified_running = False
        t = self._unified_thread
        if t and t.is_alive():
            try:
                t.join(timeout=1.0)
            except Exception:
                pass
        self._unified_thread = None
        self.acquisition_threads.pop('unified', None)
        print("[INFO] 统一采集线程已停止")

    def _unified_acquisition_loop(self):
        """
        统一采集循环（以六轴力为主时钟）：
        - 以六轴力传感器的数据帧为“主时钟事件”
        - 每拿到一条六轴样本，就在同一个对齐时间戳下抓取其他传感器的最新值并写记录
        - 六轴力：使用 drain_samples() 或 read_data_with_timestamp() 拉取所有新样本
        - timestamp_ms 使用“raw_timestamp_ns 对齐到一次 wall-clock”后的推算时间轴（与 apps/debug_liuzhouli.py 一致）
        """
        import time
        import traceback
        last_cycle_end = time.perf_counter_ns()
        log_file = open("cycle_log.txt", "a", encoding="utf-8")

        # 以第一条六轴力样本对齐一次 wall-clock，后续用 raw_timestamp_ns 推算时间轴
        t0_wall_ms = None
        t0_ns = None
        while self._unified_running:

            cycle_start = time.perf_counter_ns()

            try:
                samples = []

                # -------- 六轴力：作为主时钟源，先尝试拉取所有新样本 --------
                try:
                    if self.liuzhouli and self.connection_status['liuzhouli']:
                        device = self.liuzhouli

                        if hasattr(device, "drain_samples"):
                            # 推荐接口：返回列表，每个元素：
                            # (frame_ts_ns, pkg_no, group_index, Fx, Fy, Fz, Mx, My, Mz)
                            samples = device.drain_samples() or []
                        elif hasattr(device, "read_data_with_timestamp"):
                            # 兼容旧接口：frames 结构为 (pkg_no, frame_ts_ns, groups)
                            frames = device.read_data_with_timestamp() or []
                            for pkg_no, t_ns, groups in frames:
                                for idx, (Fx, Fy, Fz, Mx, My, Mz) in enumerate(groups):
                                    samples.append(
                                        (t_ns, pkg_no, idx, Fx, Fy, Fz, Mx, My, Mz)
                                    )
                except Exception as e:
                    print(f"[ERROR] 统一线程 六轴力采集失败: {e}")
                    traceback.print_exc()
                    samples = []

                # 若当前没有任何六轴新样本，稍作休眠再继续，避免空转占用过高 CPU
                if not samples:
                    time.sleep(0.001)
                    continue

                # 对每条六轴样本执行一次“对齐写入”
                for sample in samples:
                    (t_ns, pkg_no, group_idx,
                     Fx, Fy, Fz, Mx, My, Mz) = sample

                    # 当前对齐时间（主机毫秒）：与 raw_timestamp_ns 同轴，仅做整体平移
                    try:
                        t_ns_int = int(t_ns)
                    except Exception:
                        t_ns_int = int(time.perf_counter_ns())

                    if t0_wall_ms is None:
                        t0_wall_ms = int(time.time() * 1000)
                        t0_ns = t_ns_int
                        if self._record_start_ts_ms is None:
                            self._record_start_ts_ms = t0_wall_ms

                    now_ms = int(t0_wall_ms + (t_ns_int - int(t0_ns)) / 1_000_000.0)

                    # -------- 六轴力：构造数据并写记录 --------
                    try:
                        force_data = {
                            'Fx': float(Fx),
                            'Fy': float(Fy),
                            'Fz': float(Fz),
                            'Mx': float(Mx),
                            'My': float(My),
                            'Mz': float(Mz),
                            # 设备原始采集时间戳（纳秒）
                            'raw_timestamp_ns': t_ns_int,
                            'pkg_no': int(pkg_no),
                            'group_index': int(group_idx),
                            # 对齐时间（主机毫秒）
                            'timestamp_ms': now_ms,
                        }

                        # GUI 显示用“最新一条”
                        self.latest_data['liuzhouli'] = force_data
                        self._liuzhouli_last_sample = force_data

                        if self.record_callback_liuzhouli:
                            self.record_callback_liuzhouli(force_data)
                    except Exception as e:
                        print(f"[ERROR] 统一线程 六轴力写入失败: {e}")
                        traceback.print_exc()

                    # -------- 粘附脚：用缓存 + 对齐时间写入 --------
                    try:
                        if self.nianfujiao and self.connection_status['nianfujiao']:
                            for style, last_data in self.latest_data['nianfujiao'].items():
                                if last_data is None:
                                    continue

                                if self._nianfujiao_on_change_only:
                                    frame_key = (last_data.get('sample_sequence'),
                                                 last_data.get('ready_us'),
                                                 last_data.get('host_received_ns'))
                                    if frame_key[-1] is not None:
                                        last_written = self._nianfujiao_last_written_sensor_ts_ms.get(style)
                                        # 若缓存未更新（仍是同一条回调数据），则跳过，避免按六轴频率重复写盘
                                        if last_written == frame_key:
                                            continue
                                        self._nianfujiao_last_written_sensor_ts_ms[style] = frame_key

                                data_nf = dict(last_data)
                                data_nf['timestamp_ms'] = now_ms

                                if self.record_callback_nianfujiao:
                                    self.record_callback_nianfujiao(style, data_nf)
                    except Exception as e:
                        print(f"[ERROR] 统一线程 粘附脚处理失败: {e}")
                        traceback.print_exc()
                    
                    
                    cycle_end = time.perf_counter_ns()

                    # 计算时间（ms）
                    cost_ms = (cycle_end - cycle_start) / 1_000_000
                    interval_ms = (cycle_end - last_cycle_end) / 1_000_000

                    # 写入文件（避免频繁 flush）
                    log_file.write(f"{cost_ms:.3f},{interval_ms:.3f}\n")

            

                    last_cycle_end = cycle_end

            except Exception as e:
                print(f"[ERROR] 统一采集循环异常: {e}")
                traceback.print_exc()




    # ==================== 连接管理 ====================

    # -------- 粘附脚（回调采集） --------

    def connect_nianfujiao(self) -> bool:
        """连接粘附脚传感器"""
        try:
            config = self.config['sensor']['nianfujiao']
            self.nianfujiao = NianFuJiaoDevice(
                port=config['port'],
                baud=config['baud'],
                enable_csv=False,
                print_data=False,
                enable_calibration=config.get('enable_calibration', True),
            )
            # Register before opening: do not lose first metadata/ACK frames.
            self.nianfujiao.register_callback(self._on_nianfujiao_callback)
            self.latest_nianfujiao_protocol = {'stage_pairs': None, 'feedback': None}
            self.nianfujiao.open()
            if not self.nianfujiao.is_open:
                raise RuntimeError('黏附脚串口未能打开')

            self.connection_status['nianfujiao'] = True
            self.latest_data['nianfujiao'][0] = None
            self._foot_zero_wait_generation = None
            self.connection_status_changed.emit('nianfujiao', True)
            self._nianfujiao_last_written_sensor_ts_ms.clear()

            self._ensure_unified_thread_started()

            if not self.display_timer.isActive():
                self.display_timer.start()

            print(f"[INFO] 粘附脚传感器已连接: {config['port']}，回调采集模式已启动")
            return True

        except Exception as e:
            error_msg = f"连接失败: {str(e)}"
            print(f"[ERROR] 粘附脚 {error_msg}")
            self.connection_status['nianfujiao'] = False
            self.connection_status_changed.emit('nianfujiao', False)
            self.error_occurred.emit('nianfujiao', error_msg)
            return False

    def _on_nianfujiao_callback(self, data_type: str, data):
        """
        粘附脚数据回调：
        - 真正的 0x123 新帧独立提供给控制器入口，且单独留存原始接收记录。
        - 原有六轴主时钟配对记录继续使用缓存，不重复触发控制器。
        """
        import traceback
        import time
        try:
            if data_type == 'style0':
                style = 0
                # 入队在显示标定/CSV 回调之前，控制时延包括实际排队、算法和发送提交。
                self.controller.enqueue({
                    'raw': raw_from_sample(data), 'sample_sequence': data.sample_sequence,
                    'ready_us': data.ready_us, 'host_received_ns': data.host_received_ns,
                })
                data_dict = self._convert_style0_data(data)
                data_dict['sensor_ts_ms'] = data.sensor_ts_ms or int(time.time() * 1000)
                data_dict['host_received_ns'] = data.host_received_ns or time.perf_counter_ns()
                data_dict['timestamp_ms'] = data_dict['sensor_ts_ms']
                self.latest_data['nianfujiao'][style] = data_dict
                if self.record_callback_nianfujiao_aux:
                    self.record_callback_nianfujiao_aux('frames', data_dict)
                if self.nianfujiao_frame_callback:
                    self.nianfujiao_frame_callback(dict(data_dict))
                # Standalone foot recording still works without the six-axis clock.
                if not self.connection_status['liuzhouli'] and self.record_callback_nianfujiao:
                    self.record_callback_nianfujiao(style, data_dict)
            elif data_type == 'stage_pairs':
                self.latest_nianfujiao_protocol['stage_pairs'] = dict(data)
                generation = data['baseline_generation']
                if (self._foot_zero_wait_generation is not None and
                        generation != self._foot_zero_wait_generation):
                    self._foot_zero_wait_generation = None
                self.controller.observe_generation(generation)
                cached = self.latest_data['nianfujiao'].get(0)
                if cached and (cached.get('sample_sequence'), cached.get('ready_us')) == (
                        data['sample_sequence'], data['ready_us']):
                    merged = dict(cached)
                    merged.update(baseline_generation=data['baseline_generation'],
                                  protocol_version=data['protocol_version'])
                    self.latest_data['nianfujiao'][0] = merged
                if self.record_callback_nianfujiao_aux:
                    self.record_callback_nianfujiao_aux('stage_pairs', data)
            elif data_type == 'command_ack':
                self.latest_nianfujiao_protocol['feedback'] = dict(data)
                self.controller.on_feedback(data)
                if self.record_callback_nianfujiao_aux:
                    self.record_callback_nianfujiao_aux('feedback', data)
            elif data_type == 'command_sent':
                if self.record_callback_nianfujiao_aux:
                    self.record_callback_nianfujiao_aux('commands', data)
            else:
                return

        except Exception as e:
            print(f"[ERROR] 粘附脚回调处理失败: {e}")
            traceback.print_exc()

    # -------- 六轴力 --------

    def connect_liuzhouli(self) -> bool:
        """连接六轴力传感器"""
        try:
            config = self.config['sensor']['m8128b1']
            self.liuzhouli = create_sensor(config['type'], config)

            if not initialize_sensor(self.liuzhouli):
                raise Exception("传感器初始化失败")

            # 启动数据流
            if hasattr(self.liuzhouli, 'start_stream'):
                started = False
                try:
                    started = self.liuzhouli.start_stream()
                except Exception as e:
                    print(f"[WARN] 启动六轴力数据流时报错: {e}")
                if not started:
                    raise Exception("启动六轴力数据流失败")

            self.connection_status['liuzhouli'] = True
            self.connection_status_changed.emit('liuzhouli', True)

            self._liuzhouli_last_sample = None

            self._liuzhouli_sample_queue.clear()
            self._liuzhouli_last_sample = None


            self._ensure_unified_thread_started()

            if not self.display_timer.isActive():
                self.display_timer.start()

            transport = str(config.get('transport', 'serial')).strip().lower()
            if transport in ('ethernet', 'tcp', 'eth'):
                target = f"{config.get('ip', 'UNKNOWN')}:{config.get('tcp_port', 4008)}"
                mode = "以太网"
            else:
                target = config.get('port', 'UNKNOWN')
                mode = "串口"

            print(f"[INFO] 六轴力传感器已连接: {mode} {target}，由统一采集线程接管采集")
            print(f"[INFO] 粘附脚写入策略: {'仅变化写入' if self._nianfujiao_on_change_only else '与六轴力一一对应写入'}")
            return True

        except Exception as e:
            error_msg = f"连接失败: {str(e)}"
            print(f"[ERROR] 六轴力 {error_msg}")
            # 清理已创建的六轴力对象（避免串口句柄泄漏）
            try:
                if self.liuzhouli:
                    try:
                        if hasattr(self.liuzhouli, 'stop_stream'):
                            self.liuzhouli.stop_stream()
                    except Exception:
                        pass
                    try:
                        if hasattr(self.liuzhouli, 'disconnect'):
                            self.liuzhouli.disconnect()
                        elif hasattr(self.liuzhouli, 'close'):
                            self.liuzhouli.close()
                    except Exception:
                        pass
            finally:
                self.liuzhouli = None

            self.connection_status['liuzhouli'] = False
            self.connection_status_changed.emit('liuzhouli', False)
            self.error_occurred.emit('liuzhouli', error_msg)
            return False

    # -------- 断开连接 --------

    def disconnect_nianfujiao(self):
        """断开粘附脚传感器"""
        self.controller.stop('黏附脚已断开，控制器已停止')
        if self.nianfujiao:
            try:
                self.nianfujiao.close()
            except Exception:
                pass
            self.nianfujiao = None
            self.connection_status['nianfujiao'] = False
            self.connection_status_changed.emit('nianfujiao', False)
            self._nianfujiao_last_written_sensor_ts_ms.clear()
            self.latest_data['nianfujiao'][0] = None
            self.latest_nianfujiao_protocol = {'stage_pairs': None, 'feedback': None}
            self._foot_zero_wait_generation = None
            print("[INFO] 粘附脚传感器已断开")

        self._maybe_stop_unified_thread()

    def disconnect_liuzhouli(self):
        """断开六轴力传感器"""
        if self.liuzhouli:
            try:
                if hasattr(self.liuzhouli, 'disconnect'):
                    self.liuzhouli.disconnect()
                elif hasattr(self.liuzhouli, 'close'):
                    self.liuzhouli.close()
            except Exception:
                pass
            self.liuzhouli = None
            self.connection_status['liuzhouli'] = False
            self.connection_status_changed.emit('liuzhouli', False)
            print("[INFO] 六轴力传感器已断开")

            self._liuzhouli_last_sample = None
            self._liuzhouli_sample_queue.clear()
            self._liuzhouli_last_sample = None


        self._maybe_stop_unified_thread()

    def disconnect_all(self):
        """断开所有传感器"""
        self.disconnect_nianfujiao()
        self.disconnect_liuzhouli()

        if self.display_timer.isActive():
            self.display_timer.stop()

        print("[INFO] 所有传感器已断开")

    # ==================== 显示更新（10Hz） ====================

    def _update_display(self):
        """更新显示（10Hz，在主线程中执行）"""
        for style, data in self.latest_data['nianfujiao'].items():
            if data:
                self.nianfujiao_data_updated.emit(style, data)

        if self.latest_data['liuzhouli']:
            self.liuzhouli_data_updated.emit(self.latest_data['liuzhouli'])
        self.nianfujiao_protocol_updated.emit(dict(self.latest_nianfujiao_protocol))
        self.controller_updated.emit(self.controller.snapshot())

    # ==================== 数据转换与工具 ====================

    def _convert_style0_data(self, data) -> dict:
        """
        转换粘附脚 Style0Data 为字典格式：
        - raw: 原始 24 个字段
        - calibrated: 如启用标定，则为标定后的 24 个值，否则全 0
        """
        raw_values = raw_from_sample(data)

        device = self.nianfujiao
        enable_cal = getattr(device, "enable_calibration", False)

        result = self.controller.calibration.apply(raw_values, rounded=True)
        if enable_cal:
            calibrated_values = result['calibrated']
        else:
            calibrated_values = [0.0] * 24
        # ★ 计算两个 Fz 合力（标定值）
        # 索引含义见上面的说明
        fz1_total_cal = calibrated_values[2] + calibrated_values[14]
        fz2_total_cal = calibrated_values[9] + calibrated_values[15]

        return {
            'raw': raw_values,
            'calibrated': calibrated_values,
            'fz1_total_cal': fz1_total_cal,  # Fz1+_cal + FZ1-_cal
            'fz2_total_cal': fz2_total_cal,  # Fz2+_cal + FZ2-_cal
            # 联合总法向力不受旧分量表格的显示开关影响，与控制器使用同一模型。
            'fz_total_cal': result['fz_total_cal'],
            'fz_total_reference_n': result['fz_total_reference_n'],
            'normal_force_model_id': result['normal_force_model_id'],
            'sample_sequence': data.sample_sequence,
            'ready_us': data.ready_us,
            'host_received_ns': data.host_received_ns,
            'sensor_ts_ms': data.sensor_ts_ms,
            'pin_state': data.pin_state,
            'output_state': data.flag_d,
            'protocol_version': getattr(device, 'protocol_version', 0),
        }

    def nianfujiao_send_timed_action(self, action, frame, input_received_ns):
        """控制器输出绑定实际输入帧，禁止改成显示缓存中的最新样本。"""
        if not self.nianfujiao or not self.connection_status['nianfujiao']:
            return None
        return self.nianfujiao.send_timed_action(
            action, frame['sample_sequence'], frame['ready_us'], input_received_ns)

    def _on_controller_event(self, data):
        if self.record_callback_nianfujiao_aux:
            self.record_callback_nianfujiao_aux('controller', data)
        if data['event'] in ('start', 'stop', 'error', 'trigger_confirmed', 'calibration', 'settings'):
            print(f"[CTRL] {data['event']}: {data.get('message', '')}")
        if data['event'] != 'decision':
            self.controller_updated.emit(self.controller.snapshot())

    def start_controller(self):
        import time
        if not self.nianfujiao or not self.connection_status['nianfujiao']:
            raise ValueError('请先连接黏附脚')
        pairs = self.latest_nianfujiao_protocol.get('stage_pairs')
        if (not pairs or pairs.get('protocol_version') != 1 or
                time.perf_counter_ns() - pairs.get('host_received_ns', 0) > 500_000_000):
            raise ValueError('请等待新版协议的实时数据（0x124）后再启动控制器')
        if self._foot_zero_wait_generation is not None:
            raise ValueError('正在等待下位机应用新的清零基准，请稍后重新布防')
        self.controller.start(pairs['baseline_generation'])

    def nianfujiao_reply(self) -> bool:
        """粘附脚回复指令（恢复默认状态）"""
        self.controller.stop('手动回复，控制器已停止')
        if self.nianfujiao and self.connection_status['nianfujiao']:
            success = self.nianfujiao.send_reply()
            if success:
                print("[INFO] 粘附脚回复指令已发送")
            else:
                self.error_occurred.emit('nianfujiao', "回复指令发送失败")
            return success
        return False

    def nianfujiao_engage(self) -> bool:
        """粘附脚吸合指令"""
        self.controller.stop('手动吸合，控制器已停止')
        if self.nianfujiao and self.connection_status['nianfujiao']:
            success = self.nianfujiao.send_engage()
            if success:
                print("[INFO] 粘附脚吸合指令已发送")
            else:
                self.error_occurred.emit('nianfujiao', "吸合指令发送失败")
            return success
        return False

    def nianfujiao_zero(self) -> bool:
        """粘附脚清零指令"""
        self.controller.stop('黏附脚清零，完成后请重新布防')
        if self.nianfujiao and self.connection_status['nianfujiao']:
            pairs = self.latest_nianfujiao_protocol.get('stage_pairs')
            self._foot_zero_wait_generation = pairs['baseline_generation'] if pairs else None
            success = self.nianfujiao.send_zero()
            if success:
                print("[INFO] 粘附脚清零指令已发送")
            else:
                self._foot_zero_wait_generation = None
                self.error_occurred.emit('nianfujiao', "清零指令发送失败")
            return success
        return False

    def liuzhouli_zero(self, progress_callback=None) -> bool:
        """执行设备端清零，并将驱动的真实结果返回界面。"""
        device = self.liuzhouli
        if device and self.connection_status['liuzhouli']:
            try:
                success = bool(device.zero_channels(progress_callback=progress_callback))
                if not success:
                    print("[ERROR] 六轴力传感器清零失败，请查看 ZERO 日志")
                    return False
                self._liuzhouli_last_sample = None
                self._liuzhouli_sample_queue.clear()
                self.latest_data['liuzhouli'] = None
                print("[INFO] 六轴力传感器清零完成")
                return True
            except Exception as e:
                error_msg = f"清零失败: {str(e)}"
                print(f"[ERROR] 六轴力 {error_msg}")
                return False
        return False

    # ==================== 状态查询 ====================

    def is_connected(self, sensor_id: str) -> bool:
        """检查某个传感器是否已连接"""
        return self.connection_status.get(sensor_id, False)

    def get_all_status(self) -> Dict[str, bool]:
        """获取所有传感器连接状态"""
        return self.connection_status.copy()
