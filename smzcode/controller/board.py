"""Configure and monitor the STM32 controller from the experiment UI."""

from dataclasses import asdict
import json
from pathlib import Path
import time

from drivers.nianfujiao_song.protocol import (
    BOARD_ARM, BOARD_BEGIN, BOARD_CAL_A, BOARD_CAL_B, BOARD_CAL_C, BOARD_COMMIT,
    BOARD_DISARM, BOARD_THRESHOLD,
)
from .algorithms.calibration import Calibration
from .algorithms.threshold import ThresholdSettings


class BoardController:
    def __init__(self, request, event_callback):
        self._request = request
        self._event_callback = event_callback
        self._host_calibration = Calibration()
        self.model = json.loads((Path(__file__).parent / "config/board_model.json").read_text(encoding="utf-8"))
        self.calibration = self._host_calibration
        self.settings = ThresholdSettings.load()
        self.applied_settings = None
        self.status = None
        self.revision = 0
        self.applied_tag = None

    def snapshot(self):
        status = self.status or {}
        state = status.get("state", 0)
        fresh = bool(status and time.perf_counter_ns() - status["host_received_ns"] <= 500_000_000)
        active = bool(status) and state in (1, 3, 5)
        messages = {
            0: "已撤防", 1: "已布防，等待触发", 2: "已触发，保持当前 PA4 电平",
            3: "已布防，等待 16 个连续足端帧", 4: "计算超过采样周期，已撤防",
            5: "等待首个全段低于阈值的预测窗口",
        }
        verified = (fresh and self.applied_settings is not None and status["config_revision"] == self.revision
                    and status["model_tag"] == self.applied_tag)
        return {
            "settings": asdict(self.settings), "active": active, "state": "error" if state == 4 else state,
            "message": (messages.get(state, "未知板端状态") if fresh else
                        "板端状态已超时，输出状态待确认" if status else "等待 STM32 控制状态（0x126）"),
            "calibration": self.calibration.snapshot(), "board": status,
            "applied_settings": asdict(self.applied_settings) if verified else None,
            "applied_config_revision": self.revision if self.applied_settings else None,
        }

    def _send(self, action, value=0):
        ack = self._request(action, value)
        if ack.protocol_version != 1:
            raise ValueError("STM32 控制协议版本不匹配")
        if ack.status:
            messages = {
                2: "清零基准已变化，请等待下一帧状态再布防",
                3: "板端已布防或正在清零，请撤防并等待清零完成",
                4: "参数或配置版本无效，请重新应用",
                5: "联合标定与固件模型不匹配",
                6: "PA4 已在目标电平，请先手动回复或吸合到相反电平",
            }
            raise ValueError(messages.get(ack.status, f"板端拒绝命令，状态码 {ack.status}"))
        return ack

    def _event(self, event):
        self._event_callback({
            "event": event, "event_ns": time.perf_counter_ns(),
            "sensor_ts_ms": time.time_ns() // 1_000_000,
            "config_json": json.dumps(self.snapshot(), ensure_ascii=False),
        })

    def configure(self, settings):
        self.settings = settings

    def _fresh_status(self):
        status = self.status
        if not status or time.perf_counter_ns() - status["host_received_ns"] > 500_000_000:
            raise ValueError("请连接已烧录板端控制固件的 STM32，并等待实时 0x126 状态")
        return status

    def apply(self):
        status = self._fresh_status()
        settings = self.settings
        if settings.threshold_total_n <= 0:
            raise ValueError("板端触发阈值必须大于 0 N")
        if (settings.strategy == 2 or settings.use_model_calibration) and status["model_tag"] != self.model["model_tag"]:
            raise ValueError("板端模型版本与上位机导出版本不同，请用同一次导出的固件")
        calibration = (self._host_calibration.with_normal_force(self.model["calibration"])
                       if settings.use_model_calibration else self._host_calibration)
        coefficients = calibration.snapshot()["normal_force"]["coefficients"]
        self._send(BOARD_DISARM)
        self._send(BOARD_BEGIN)
        for action, coefficient in zip((BOARD_CAL_A, BOARD_CAL_B, BOARD_CAL_C), coefficients):
            self._send(action, coefficient)
        self._send(BOARD_THRESHOLD, settings.threshold_total_n)
        ack = self._send(BOARD_COMMIT, settings.strategy | (settings.trigger_action << 8))
        self.revision = ack.ready_us
        self.applied_tag = status["model_tag"]
        self.applied_settings = settings
        self.calibration = calibration
        self._event("board_config")
        return ack.baseline_generation

    def arm(self, generation):
        self._send(BOARD_ARM, (self.revision << 16) | generation)
        self._event("board_arm")

    def stop(self, message=None):
        if self.status is not None:
            self._send(BOARD_DISARM)
            self._event("board_disarm")

    def reload_calibration(self, path=None):
        calibration = Calibration(path)
        self.stop()
        self._host_calibration = calibration
        if self.status is None:
            self.calibration = calibration
        self._event("calibration")

    def observe(self, status):
        if status["config_revision"] == 0 and status["model_tag"] == self.model["model_tag"]:
            if self.status is None or self.status["config_revision"] != 0:
                self.calibration = self._host_calibration.with_normal_force(self.model["calibration"])
                self.applied_settings = None
        self.status = dict(status)

    def reset_connection(self):
        self.status = None
        self.applied_settings = None
        self.revision = 0
        self.applied_tag = None
        self.calibration = self._host_calibration
