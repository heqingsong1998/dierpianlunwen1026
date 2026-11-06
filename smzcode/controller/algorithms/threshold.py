"""压缩为正的总法向力达到单一阈值时触发一次。"""
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import tempfile

SETTINGS_PATH = Path(__file__).resolve().parents[1] / 'config' / 'settings.json'


@dataclass(frozen=True)
class ThresholdSettings:
    threshold_total_n: float = 3.0
    trigger_action: int = 1  # PA4: 0=LOW, 1=HIGH
    strategy: int = 1
    use_model_calibration: bool = True

    def __post_init__(self):
        if not math.isfinite(self.threshold_total_n) or not 0 <= self.threshold_total_n <= 10000:
            raise ValueError('总法向力阈值必须在 0 到 10000 N 之间（压缩为正）')
        if self.trigger_action not in (0, 1):
            raise ValueError('触发动作只能为 PA4 LOW 或 HIGH')
        if self.strategy not in (1, 2) or not isinstance(self.use_model_calibration, bool):
            raise ValueError('板端策略或标定来源无效')

    @classmethod
    def load(cls, path=SETTINGS_PATH):
        with Path(path).open(encoding='utf-8') as handle:
            values = json.load(handle)
        # 旧配置的两阈值之和仅作为迁移后的初始设置，不等价于旧 AND 判据。
        legacy_keys = {'threshold_fz1_n', 'threshold_fz2_n'}
        if legacy_keys & values.keys():
            if ('threshold_total_n' in values or not legacy_keys <= values.keys() or
                    values.keys() - legacy_keys - {'trigger_action'}):
                raise ValueError('阈值配置混用了旧双阈值与新总力字段')
            values['threshold_total_n'] = values.pop('threshold_fz1_n') + values.pop('threshold_fz2_n')
        return cls(**values)

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
    return signal['fz_total_cal'] >= settings.threshold_total_n
