"""两组法向力同时达到各自阈值，触发一次。未达到时保持输出。"""
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import tempfile

SETTINGS_PATH = Path(__file__).with_name('settings.json')


@dataclass(frozen=True)
class ThresholdSettings:
    threshold_fz1_n: float = 1.5
    threshold_fz2_n: float = 1.5
    trigger_action: int = 1  # PA4: 0=LOW, 1=HIGH

    def __post_init__(self):
        for value in (self.threshold_fz1_n, self.threshold_fz2_n):
            if not math.isfinite(value) or not -10000 <= value <= 10000:
                raise ValueError('力阈值必须在 -10000 到 10000 N 之间')
        if self.trigger_action not in (0, 1):
            raise ValueError('触发动作只能为 PA4 LOW 或 HIGH')

    @classmethod
    def load(cls, path=SETTINGS_PATH):
        with Path(path).open(encoding='utf-8') as handle:
            return cls(**json.load(handle))

    def save(self, path=SETTINGS_PATH):
        path = Path(path)
        # 替换整个文件，避免中途退出留下半个 JSON。
        fd, temporary = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=str(path.parent))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(asdict(self), handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write('\n')
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def threshold_reached(signal, settings):
    return (signal['fz1_total_cal'] >= settings.threshold_fz1_n and
            signal['fz2_total_cal'] >= settings.threshold_fz2_n)
