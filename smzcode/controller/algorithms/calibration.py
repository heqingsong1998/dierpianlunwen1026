"""原有二次多项式标定。参数位于 controller/config/calibration.json。"""
import copy
import json
import math
from pathlib import Path

RAW_FIELDS = (
    'Fx1', 'Fy1', 'Fz1_plus', 'Mx1', 'My1', 'JJJ1_1', 'JJJ1_2',
    'Fx2', 'Fy2', 'Fz2_plus', 'Mx2', 'My2', 'flag1', 'flag2',
    'FZ1_minus', 'FZ2_minus', 'flag_fz', 'flag_fx', 'flag_d',
    'reserved_1', 'reserved_2', 'reserved_3', 'reserved_4', 'reserved_5',
)
CALIBRATED_FIELDS = RAW_FIELDS[:5] + RAW_FIELDS[7:12] + RAW_FIELDS[14:16]
CALIBRATION_PATH = Path(__file__).resolve().parents[1] / 'config' / 'calibration.json'


def raw_from_sample(sample):
    return [getattr(sample, name) for name in RAW_FIELDS]


class Calibration:
    def __init__(self, path=None):
        path = Path(path) if path else CALIBRATION_PATH
        with path.open(encoding='utf-8') as handle:
            self._config = json.load(handle)
        cfg = self._config
        if not isinstance(cfg.get('model_id'), str) or not cfg['model_id']:
            raise ValueError('标定模型缺少 model_id')
        for key in ('gravity', 'force_scale', 'moment_divisor'):
            if not math.isfinite(float(cfg[key])) or float(cfg[key]) <= 0:
                raise ValueError(f'标定参数 {key} 必须为正数')
        for name in CALIBRATED_FIELDS:
            coefficients = cfg['coefficients'][name]
            if len(coefficients) != 3 or not all(math.isfinite(float(x)) for x in coefficients):
                raise ValueError(f'{name} 需要三个有限的多项式系数')

    @property
    def model_id(self):
        return self._config['model_id']

    def snapshot(self):
        return copy.deepcopy(self._config)

    def apply(self, raw, rounded=False):
        if len(raw) != 24 or not all(math.isfinite(float(x)) for x in raw):
            raise ValueError('标定输入必须为 24 个有限数值')
        values = list(raw)
        cfg = self._config
        for name in CALIBRATED_FIELDS:
            index = RAW_FIELDS.index(name)
            x = raw[index]
            if (name.endswith('_plus') and x < 0) or (name.endswith('_minus') and x >= 0):
                values[index] = 0.0
                continue
            a, b, c = cfg['coefficients'][name]
            value = (a * x * x + b * x + c) / 1000 * cfg['gravity']
            is_moment = name.startswith(('Mx', 'My'))
            value = value / cfg['moment_divisor'] if is_moment else value * cfg['force_scale']
            if not math.isfinite(value):
                raise ValueError(f'{name} 标定结果非有限数值')
            values[index] = round(value, 6 if is_moment else 4) if rounded else value
        return {
            'calibrated': values,
            'fz1_total_cal': values[2] + values[14],
            'fz2_total_cal': values[9] + values[15],
            'calibration_id': self.model_id,
        }
