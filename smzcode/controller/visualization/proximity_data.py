"""接近觉显示的数据与位置配置。保持与内部 0x123 视图和 CSV 通道一致。"""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile

CHANNELS = ('JJJ1_1', 'JJJ1_2', 'JJJ2_1', 'JJJ2_2')
RAW_INDICES = (5, 6, 12, 13)
POSITION_NAMES = ('左上', '右上', '左下', '右下')
# 用户确认的足端俯视位置：第一组在左列，第二组在右列。
DEFAULT_POSITIONS = ('JJJ1_1', 'JJJ2_1', 'JJJ1_2', 'JJJ2_2')
LAYOUT_PATH = Path(__file__).resolve().parents[1] / 'config' / 'proximity_layout.json'


@dataclass(frozen=True)
class ProximitySample:
    values: tuple
    sample_sequence: int
    ready_us: int
    host_received_ns: int

    @classmethod
    def from_frame(cls, frame):
        raw = frame.get('raw', ())
        if len(raw) < 14:
            raise ValueError('接近觉输入缺少完整的四个通道')
        values = tuple(float(raw[index]) for index in RAW_INDICES)
        if not all(math.isfinite(value) for value in values):
            raise ValueError('接近觉通道包含无效数值')
        return cls(values, int(frame.get('sample_sequence', 0)), int(frame.get('ready_us', 0)),
                   int(frame.get('host_received_ns', 0)))

    def at_positions(self, positions):
        validate_positions(positions)
        by_channel = dict(zip(CHANNELS, self.values))
        return tuple(by_channel[channel] for channel in positions)


def validate_positions(positions):
    if len(positions) != 4 or set(positions) != set(CHANNELS):
        raise ValueError('四个位置必须各使用一路 JJJ 通道，不能重复或遗漏')


def load_positions(path=LAYOUT_PATH):
    path = Path(path)
    if not path.exists():
        return DEFAULT_POSITIONS
    with path.open(encoding='utf-8') as handle:
        data = json.load(handle)
    positions = tuple(data['positions'])
    validate_positions(positions)
    return positions


def save_positions(positions, path=LAYOUT_PATH):
    validate_positions(positions)
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump({'view': 'top', 'position_order': ['upper_left', 'upper_right',
                       'lower_left', 'lower_right'], 'positions': list(positions)},
                      handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def signal_differences(values):
    """按左上、右上、左下、右下计算信号差，不转换为距离或角度。"""
    upper_left, upper_right, lower_left, lower_right = values
    return {
        'right_minus_left': (upper_right + lower_right - upper_left - lower_left) / 2,
        'upper_minus_lower': (upper_left + upper_right - lower_left - lower_right) / 2,
        'range': max(values) - min(values),
    }
