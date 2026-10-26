"""
粘附脚传感器驱动包
"""
from .nianfujiao_device import NianFuJiaoDevice
from .base import Style0Data
from .protocol import StagePairData, CommandAck

__all__ = [
    "NianFuJiaoDevice",
    "Style0Data",
    "StagePairData",
    "CommandAck",
]

# 协议 v1: 0x123 数据、0x124 对和、0x125 反馈。
__version__ = "2.1.0"
