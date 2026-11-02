"""IMU1 短时速度估计：人工确认静止、固定零偏、姿态补偿、逐帧梯形积分。"""
from dataclasses import asdict, dataclass
import math
from threading import RLock
import uuid

import numpy as np


@dataclass(frozen=True)
class VelocitySettings:
    period_ms: float = 10.0
    time_source: str = 'chip'
    gravity: float = 9.80665
    calibration_s: float = 2.0
    quiet_gyro_dps: float = 3.0
    gravity_tolerance: float = 0.6
    bias_std_limit: float = 0.08

    def __post_init__(self):
        if self.time_source not in ('chip', 'nominal'):
            raise ValueError('IMU 积分时间源必须为 chip 或 nominal')
        for key, value in asdict(self).items():
            if key != 'time_source' and (not math.isfinite(value) or value <= 0):
                raise ValueError(f'IMU 参数 {key} 必须为有限正数')


def body_to_world(angles):
    """WIT roll/pitch/yaw（度）；Rz(yaw) @ Ry(pitch) @ Rx(roll)。"""
    roll, pitch, yaw = map(math.radians, angles)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array([[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],
                     [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr],
                     [-sp, cp*sr, cp*cr]])


class ImuVelocityEstimator:
    MODEL_ID = 'imu1_attitude_y_mm_s_v2'

    def __init__(self, settings=None):
        self.settings = settings or VelocitySettings()
        self.lock = RLock()
        self.generation = 0
        self.run_id = uuid.uuid4().hex
        self._last_sequence = None
        self._last_clock = None
        self._elapsed = 0.0
        self._velocity = np.zeros(3)
        self._last_acc = None
        self._bias = np.zeros(3)
        self._yaw0 = 0.0
        self._window = []
        self.state = 'needs_zero'

    def request_zero(self):
        """只由使用者确认已静止时调用；不依据低加速度自动清零。"""
        with self.lock:
            self.generation += 1
            self._window.clear()
            self._velocity[:] = 0
            self._last_acc = None
            self.state = 'calibrating'

    def snapshot(self):
        with self.lock:
            return {'model_id': self.MODEL_ID, 'settings': asdict(self.settings),
                    'generation': self.generation, 'state': self.state,
                    'run_id': self.run_id,
                    'bias_body_mps2': self._bias.tolist(),
                    'reference_yaw_deg': self._yaw0,
                    'frame': 'local_level_initial_heading_z_up',
                    'initial_velocity_y_mm_s': 0,
                    'velocity_output': {'axis': 'Y', 'unit': 'mm/s', 'field': 'vel_y_mm_s'},
                    'automatic_zupt': False}

    def update(self, sample):
        with self.lock:
            return self._update(sample)

    def _update(self, sample):
        sequence = int(sample['sample_sequence'])
        if self._last_sequence is not None and sequence <= self._last_sequence:
            return None  # 同一周期的磁场/四元数通知不能重复记录、积分。
        sequence_gap = self._last_sequence is not None and sequence != self._last_sequence+1
        self._last_sequence = sequence
        data = dict(sample)
        clock = sample.get('chip_time_ms') if self.settings.time_source == 'chip' else sequence*self.settings.period_ms
        dt = None
        if clock is not None and self._last_clock is not None:
            delta = clock-self._last_clock
            if self.settings.time_source == 'chip' and self._last_clock > 86_399_000 and clock < 1000:
                delta += 86_400_000  # 芯片时间在午夜回绕。
            dt = delta/1000
        self._last_clock = clock
        timing_ok = clock is not None and (dt is None or 0 < dt <= 1.8*self.settings.period_ms/1000)
        if dt is not None and timing_ok:
            self._elapsed += dt
        raw = [sample.get(key) for key in ('acc_x','acc_y','acc_z','gyro_x','gyro_y','gyro_z',
                                           'angle_x','angle_y','angle_z')]
        numeric_ok = all(isinstance(v, (int,float)) and math.isfinite(v) for v in raw)
        complete = sample.get('frame_complete', False) and numeric_ok
        linear = None
        if not timing_ok or sequence_gap or not complete:
            self._window.clear()
            self._last_acc = None
            if self.state == 'tracking':
                self.state = 'gap_requires_zero'
            status = 'invalid_time' if not timing_ok else 'incomplete_frame'
        else:
            acceleration = np.asarray(raw[:3])*self.settings.gravity
            rotation = body_to_world(raw[6:9])
            gravity = np.array([0., 0., self.settings.gravity])
            residual = acceleration-rotation.T @ gravity
            status = self.state
            if self.state == 'calibrating':
                quiet = np.linalg.norm(raw[3:6]) < self.settings.quiet_gyro_dps
                quiet &= abs(np.linalg.norm(acceleration)-self.settings.gravity) < self.settings.gravity_tolerance
                if not quiet:
                    self._window.clear()
                    status = 'hold_still'
                else:
                    self._window.append((self._elapsed, residual))
                    duration = self._window[-1][0]-self._window[0][0]
                    if duration >= self.settings.calibration_s and len(self._window) >= 20:
                        values = np.array([value for _, value in self._window])
                        if np.max(values.std(axis=0)) > self.settings.bias_std_limit:
                            self._window.clear()
                            status = 'hold_still'
                        else:
                            self._bias = values.mean(axis=0)
                            self._yaw0 = raw[8]
                            self._velocity[:] = 0
                            self.state = status = 'tracking'
                            self._last_acc = None
                            self._window.clear()
            if self.state == 'tracking':
                world = rotation @ (acceleration-self._bias)-gravity
                yaw_rotation = body_to_world([0, 0, -self._yaw0])
                linear = yaw_rotation @ world
                if self._last_acc is not None and dt is not None:
                    self._velocity += (self._last_acc+linear)*0.5*dt
                self._last_acc = linear
        valid = self.state == 'tracking' and timing_ok and complete and not sequence_gap
        if self.state == 'gap_requires_zero':
            status = self.state
        duration = self._window[-1][0]-self._window[0][0] if self._window else 0
        data.update(velocity_valid=valid, velocity_status=status,
                    calibration_progress=min(1., duration/self.settings.calibration_s),
                    integration_dt_s=dt if dt is not None else '',
                    integration_time_source=self.settings.time_source,
                    integration_elapsed_s=self._elapsed,
                    velocity_generation=self.generation, velocity_model_id=self.MODEL_ID,
                    velocity_run_id=self.run_id,
                    reference_yaw_deg=self._yaw0, gravity_mps2=self.settings.gravity)
        for i, axis in enumerate('xyz'):
            data['linear_acc_'+axis] = float(linear[i]) if linear is not None and valid else None
            data['bias_'+axis] = float(self._bias[i])
        # 内部按 SI 单位积分，显示和保存只输出有正负号的 Y 轴速度（mm/s）。
        data['vel_y_mm_s'] = float(self._velocity[1])*1000. if valid else None
        return data
