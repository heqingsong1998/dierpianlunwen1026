"""独立控制线程：真实新帧 -> 标定 -> AND 阈值 -> 带样本编号的命令。

send_action(action, frame, B_ns) 返回驱动 PendingCommand；event_callback 只做异步记录。
不依赖 Qt，也不从同步采集缓存/显示定时器取数。
"""
from collections import OrderedDict
from dataclasses import asdict
import json
import queue
import threading
import time

from .calibration import Calibration
from .signal_processing import process_signal
from .threshold import ThresholdSettings, threshold_reached


class FootController:
    MAX_FRAME_AGE_NS = 500_000_000
    ACK_TIMEOUT_NS = 1_000_000_000

    def __init__(self, send_action, event_callback=None, calibration=None, settings=None):
        self.calibration = calibration or Calibration()
        self.settings = settings or ThresholdSettings.load()
        self._send_action = send_action
        self._event_callback = event_callback
        self._lock = threading.RLock()
        self._queue = queue.Queue(maxsize=8)
        self._stop_event = threading.Event()
        self._thread = None
        self._active = False
        self._state = 'stopped'
        self._message = '未启动'
        self._settings_revision = 1
        self._last_input_key = None
        self._last_values = {}
        self._trigger_key = None
        self._trigger_submitted_ns = None
        self._acks = OrderedDict()
        self._started_ns = 0
        self._generation = None
        self._dropped_inputs = 0
        self._processed = 0

    def snapshot(self):
        with self._lock:
            return {
                'active': self._active, 'state': self._state, 'message': self._message,
                'settings': asdict(self.settings), 'settings_revision': self._settings_revision,
                'calibration': self.calibration.snapshot(), 'baseline_generation': self._generation,
                'dropped_inputs': self._dropped_inputs, 'processed': self._processed,
                **self._last_values,
            }

    def _emit(self, event, **values):
        if self._event_callback:
            row = {
                'event': event, 'event_ns': time.perf_counter_ns(),
                'sensor_ts_ms': int(time.time() * 1000), **values,
            }
            self._event_callback(row)

    def configure(self, settings):
        with self._lock:
            self.settings = settings
            self._settings_revision += 1
            revision = self._settings_revision
        self._emit('settings', settings_revision=revision,
                   config_json=json.dumps(asdict(settings), ensure_ascii=False))

    def reload_calibration(self, path=None):
        calibration = Calibration(path)  # 参数无效时保留旧模型和运行状态。
        self.stop('标定已重载，请重新布防')
        with self._lock:
            self.calibration = calibration
            self._settings_revision += 1
        self._emit('calibration', config_json=json.dumps(calibration.snapshot(), ensure_ascii=False))

    def start(self, baseline_generation):
        self.stop('重新布防')
        with self._lock:
            self._active = True
            self._state = 'armed'
            self._message = '已布防，等待两组法向力同时达到阈值'
            self._last_input_key = None
            self._trigger_key = None
            self._trigger_submitted_ns = None
            self._acks.clear()
            self._last_values = {}
            self._generation = baseline_generation
            self._started_ns = time.perf_counter_ns()
            self._dropped_inputs = self._processed = 0
            self._stop_event.clear()
        self._emit('start', config_json=json.dumps(self.snapshot(), ensure_ascii=False))
        self._thread = threading.Thread(target=self._run, name='foot-controller', daemon=True)
        self._thread.start()

    def request_stop(self, message='已停止，保持当前输出', error=False):
        with self._lock:
            was_active = self._active
            self._active = False
            self._state = 'error' if error else 'stopped'
            self._message = message
            self._stop_event.set()
        if was_active:
            self._emit('error' if error else 'stop', message=message)

    def stop(self, message='已停止，保持当前输出'):
        self.request_stop(message)
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)
            if thread.is_alive():
                raise RuntimeError('控制器发送尚未结束，请稍后重试')
        self._thread = None
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def observe_generation(self, generation):
        with self._lock:
            changed = self._active and generation != self._generation
        if changed:
            self.request_stop('下位机零点已改变，请重新布防')

    def enqueue(self, frame):
        """串口接收线程调用，仅快照入队；B 从这里开始，包含排队时间。"""
        with self._lock:
            if not self._active:
                return False
            key = (frame['sample_sequence'], frame['ready_us'])
            if key[0] == 0 or key == self._last_input_key:
                return False
            self._last_input_key = key
            frame = dict(frame, raw=tuple(frame['raw']))
            if self._queue.full():
                try:
                    self._queue.get_nowait()  # 过载时丢弃最旧的待处理输入。
                    self._dropped_inputs += 1
                except queue.Empty:
                    pass  # 工作线程刚好取走了最后一项。
            input_ns = time.perf_counter_ns()
            self._queue.put_nowait((frame, input_ns, self.settings, self._settings_revision,
                                    self.calibration))
        return True

    def on_feedback(self, feedback):
        # 包括“回执早于 serial.write 返回”的情况；下一步发送返回后再匹配。
        with self._lock:
            if (not self._active or not feedback.get('input_received_ns') or
                    feedback.get('submitted_ns', 0) < self._started_ns):
                return
            key = (feedback['command_id'], feedback['sample_sequence'])
            self._acks[key] = dict(feedback)
            while len(self._acks) > 64:
                self._acks.popitem(last=False)
            if feedback['status'] != 0:
                self.request_stop(f"下位机拒绝命令，状态码 {feedback['status']}，请重新布防", error=True)
            elif key == self._trigger_key:
                self._confirm_trigger(feedback)

    def _confirm_trigger(self, feedback):
        # 调用者持有 RLock。GPIO_WRITTEN 表示执行了写引脚，不代表机械动作结束。
        if not feedback['flags'] & 2:
            self.request_stop('触发回执没有 GPIO 写入标志', error=True)
            return
        self._state = 'triggered'
        self._message = ('已触发，保持输出；下一次请重新布防' if feedback.get('gpio_changed') else
                         '已确认：PA4 原本就是目标电平，本次无电平变化')
        self._trigger_submitted_ns = None
        self._trigger_key = None
        self._emit('trigger_confirmed', command_id=feedback['command_id'],
                   sample_sequence=feedback['sample_sequence'], message=self._message)

    def _check_ack_timeout(self):
        with self._lock:
            if (self._trigger_submitted_ns is not None and
                    time.perf_counter_ns() - self._trigger_submitted_ns > self.ACK_TIMEOUT_NS):
                self.request_stop('触发命令 1 秒内未收到回执，请检查连接后重新布防', error=True)

    def _run(self):
        try:
            while not self._stop_event.is_set():
                self._check_ack_timeout()
                if self._stop_event.is_set():
                    break
                try:
                    item = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                self._process(*item)
        except Exception as exc:
            self.request_stop(f'控制器异常：{exc}', error=True)

    def _process(self, frame, input_ns, settings, revision, calibration):
        if self._stop_event.is_set():
            return
        now_ns = time.perf_counter_ns()
        received_ns = frame.get('host_received_ns', input_ns)
        if now_ns - received_ns > self.MAX_FRAME_AGE_NS or received_ns > now_ns:
            with self._lock:
                self._dropped_inputs += 1
            self._emit('stale_input', sample_sequence=frame['sample_sequence'], ready_us=frame['ready_us'])
            return
        processing_started_ns = time.perf_counter_ns()
        signal = process_signal(frame['raw'], calibration)
        reached = threshold_reached(signal, settings)
        with self._lock:
            trigger = self._state == 'armed' and reached
        action = settings.trigger_action if trigger else 2  # HOLD，便于对每帧测传输/处理时延。
        if self._stop_event.is_set():
            return
        pending = self._send_action(action, frame, input_ns)
        if pending is None:
            self.request_stop('命令发送失败，请检查连接后重新布防', error=True)
            return
        with self._lock:
            self._processed += 1
            self._last_values = {
                'fz1_n': signal['fz1_total_cal'], 'fz2_n': signal['fz2_total_cal'],
                'sample_sequence': frame['sample_sequence'],
                'controller_us': (pending.submitted_ns - input_ns) / 1000,
            }
            if trigger and self._active:
                self._state = 'waiting_ack'
                self._message = '阈值已达到，等待下位机确认'
                self._trigger_key = (pending.command_id, pending.sample_sequence)
                self._trigger_submitted_ns = pending.submitted_ns
                ack = self._acks.get(self._trigger_key)
                if ack:
                    self._confirm_trigger(ack)
            state = self._state
            dropped = self._dropped_inputs
        self._emit('decision', sample_sequence=frame['sample_sequence'], ready_us=frame['ready_us'],
                   host_received_ns=received_ns, input_received_ns=input_ns,
                   processing_started_ns=processing_started_ns, submitted_ns=pending.submitted_ns,
                   controller_us=(pending.submitted_ns - input_ns) / 1000,
                   fz1_n=signal['fz1_total_cal'], fz2_n=signal['fz2_total_cal'],
                   threshold_fz1_n=settings.threshold_fz1_n, threshold_fz2_n=settings.threshold_fz2_n,
                   threshold_reached=reached, is_trigger=trigger, action=action,
                   command_id=pending.command_id, state=state, settings_revision=revision,
                   calibration_id=calibration.model_id, dropped_inputs=dropped)
