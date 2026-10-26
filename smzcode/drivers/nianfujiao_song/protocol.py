"""与 Core/Inc/host_control_protocol.h 对应的字节协议，不依赖 GUI。"""
from __future__ import annotations

import struct
from dataclasses import asdict, dataclass
from typing import Optional

PROTOCOL_VERSION = 1
UINT32_MASK = 0xFFFFFFFF
DATA_ID = 0x123
STAGE_PAIR_ID = 0x124
ACK_ID = 0x125
PAYLOAD_LENGTHS = {DATA_ID: 48, STAGE_PAIR_ID: 32, ACK_ID: 32}
COMMAND_PREFIX = bytes.fromhex("5A 08 04 00 00 FF FE")
TIMING_VALID = 0x0001
GPIO_WRITTEN = 0x0002
ZERO_PENDING = 0x0004
LEGACY = 0x0008
GPIO_CHANGED = 0x0010
ACTION_LOW, ACTION_HIGH, ACTION_HOLD, ACTION_ZERO = range(4)


class FrameParser:
    """支持分包、粘包、混合 ID 与噪声；未知合法帧整帧跳过。"""

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk: bytes):
        self.buffer.extend(chunk)
        frames = []
        while self.buffer:
            start = self.buffer.find(b"\x5a")
            if start < 0:
                self.buffer.clear()
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < 7:
                break
            length = int.from_bytes(self.buffer[1:3], "little")
            can_id = int.from_bytes(self.buffer[4:7], "big")
            if (self.buffer[3] != 0x80 or length not in
                    (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
                    or can_id > 0x7FF
                    or (can_id in PAYLOAD_LENGTHS and length != PAYLOAD_LENGTHS[can_id])):
                del self.buffer[0]
                continue
            total = 8 + length
            if len(self.buffer) < total:
                break
            if self.buffer[total - 1] != 0xA5:
                del self.buffer[0]
                continue
            payload = bytes(self.buffer[7:total - 1])
            del self.buffer[:total]
            if can_id in PAYLOAD_LENGTHS:
                frames.append((can_id, payload))
        return frames


@dataclass(frozen=True)
class StagePairData:
    pairs: tuple
    sample_sequence: int
    ready_us: int
    baseline_generation: int
    protocol_version: int
    dropped_commands: int
    dropped_tx_frames: int
    host_received_ns: int = 0
    sensor_ts_ms: int = 0

    @classmethod
    def decode(cls, payload, host_received_ns=0, sensor_ts_ms=0):
        values = struct.unpack("<8hII4H", payload)
        return cls(tuple(values[:8]), *values[8:], host_received_ns, sensor_ts_ms)

    def to_dict(self):
        data = asdict(self)
        data.update({f"pair_{i}": value for i, value in enumerate(self.pairs)})
        return data


@dataclass(frozen=True)
class CommandAck:
    protocol_version: int
    status: int
    action: int
    pin_state: int
    command_id: int
    flags: int
    sample_sequence: int
    ready_us: int
    received_us: int
    gpio_written_us: int
    round_trip_us: int
    baseline_generation: int
    dropped_commands: int
    host_received_ns: int = 0
    sensor_ts_ms: int = 0

    @classmethod
    def decode(cls, payload, host_received_ns=0, sensor_ts_ms=0):
        return cls(*struct.unpack("<4B2H5I2H", payload), host_received_ns, sensor_ts_ms)

    def to_dict(self):
        data = asdict(self)
        data['gpio_changed'] = bool(self.flags & GPIO_CHANGED)
        data['zero_pending'] = bool(self.flags & ZERO_PENDING)
        data['gpio_wait_us'] = (
            (self.gpio_written_us - self.received_us) & UINT32_MASK
            if self.protocol_version == 1 and self.status == 0 and self.flags & GPIO_WRITTEN
            else None
        )
        return data


@dataclass(frozen=True)
class PendingCommand:
    action: int
    command_id: int
    sample_sequence: int
    ready_us: Optional[int]
    input_received_ns: Optional[int]
    submitted_ns: int
    sensor_ts_ms: int

    def to_dict(self):
        return asdict(self)


def build_command_frame(action: int, command_id: int, sample_sequence: int) -> bytes:
    if action not in range(4):
        raise ValueError("action 必须为 0=拉低、1=拉高、2=保持、3=清零")
    if not 1 <= command_id <= 0xFFFF or not 0 <= sample_sequence <= UINT32_MASK:
        raise ValueError("命令编号或采样帧编号超出协议范围")
    return COMMAND_PREFIX + struct.pack("<BBHI", 0xC1, action, command_id, sample_sequence) + b"\xa5"


def match_feedback(ack: CommandAck, pending: Optional[PendingCommand]) -> dict:
    """无效、未匹配及负值均保留原因，不把异常时延裁剪成零。"""
    data = ack.to_dict()
    data.update(input_received_ns=None, submitted_ns=None, controller_us=None,
                transport_sum_us=None, timing_valid=False, timing_error='')
    if pending is not None:
        data.update(input_received_ns=pending.input_received_ns, submitted_ns=pending.submitted_ns)
    if ack.protocol_version != PROTOCOL_VERSION:
        reason = 'unsupported_version'
    elif ack.status != 0:
        reason = f'device_rejected_{ack.status}'
    elif ack.flags & LEGACY:
        reason = 'legacy_command'
    elif pending is None:
        reason = 'unmatched_command'
    elif (ack.command_id, ack.sample_sequence, ack.action) != (
            pending.command_id, pending.sample_sequence, pending.action):
        reason = 'command_mismatch'
    elif not ack.flags & TIMING_VALID or not ack.sample_sequence:
        reason = 'unbound_command'
    elif pending.input_received_ns is None or pending.ready_us is None:
        reason = 'missing_input_timestamp'
    elif ack.ready_us != pending.ready_us:
        reason = 'sample_timestamp_mismatch'
    elif ack.round_trip_us != ((ack.received_us - ack.ready_us) & UINT32_MASK):
        reason = 'round_trip_mismatch'
    else:
        controller_us = (pending.submitted_ns - pending.input_received_ns) / 1000.0
        transport_us = ack.round_trip_us - controller_us
        data.update(controller_us=controller_us, transport_sum_us=transport_us)
        if controller_us < 0 or transport_us < 0:
            reason = 'negative_duration'
        else:
            data['timing_valid'] = True
            reason = ''
    data['timing_error'] = reason
    return data
