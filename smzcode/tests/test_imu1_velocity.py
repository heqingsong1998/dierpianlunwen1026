"""保留的 IMU 解析与速度估计算法；不连接硬件、不保存数据。"""
from pathlib import Path
import struct
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.imu_velocity import ImuVelocityEstimator, VelocitySettings, body_to_world
from drivers.HWT606_song_1.chs.jy901s_device import JY901SDevice


def sample(seq, acceleration=(0,0,0), angles=(0,0,0), bias=(0,0,0), chip=True):
    g = 9.80665
    raw = body_to_world(angles).T @ (np.asarray(acceleration)+[0,0,g])+bias
    data = {'sample_sequence':seq, 'timestamp_ms':1700000000000+seq*10,
            'host_received_ns':10**12, 'chip_time_ms':seq*10 if chip else None,
            'frame_complete':True}
    for keys, values in [(('acc_x','acc_y','acc_z'), raw/g),
                         (('gyro_x','gyro_y','gyro_z'), (0.,0.,0.)),
                         (('angle_x','angle_y','angle_z'), angles)]:
        data.update(zip(keys, map(float, values)))
    return data


def calibrated(angles=(0,0,0), bias=(0,0,0)):
    estimator = ImuVelocityEstimator()
    estimator.request_zero()
    for seq in range(1, 211):
        result = estimator.update(sample(seq, angles=angles, bias=bias))
    assert result['velocity_valid'], result
    return estimator


def packet(kind, payload):
    prefix = bytes([0x55,kind])+payload
    return prefix+bytes([sum(prefix)&255])


def wire_cycle(seq, acceleration=0., omit_gyro=False):
    ms = seq*10
    hour, rest = divmod(ms, 3_600_000)
    minute, rest = divmod(rest, 60_000)
    second, ms = divmod(rest, 1000)
    cycle = packet(0x50, bytes([26,11,2,hour,minute,second])+struct.pack('<H',ms))
    cycle += packet(0x51, struct.pack('<hhhh',0,round(acceleration/9.80665/16*32768),2048,2500))
    if not omit_gyro:
        cycle += packet(0x52, struct.pack('<hhhh',0,0,0,0))
    return cycle+packet(0x53, struct.pack('<hhhh',0,0,0,1))


class VelocityAlgorithmTests(unittest.TestCase):
    def test_tilted_rest_bias_compensation_and_rotation_without_linear_motion(self):
        angles = (30, -20, 125)
        bias = (.025, -.015, .01)
        estimator = calibrated(angles, bias)
        for seq in range(211, 411):
            result = estimator.update(sample(seq, angles=angles, bias=bias))
        self.assertAlmostEqual(result['vel_y_mm_s'],0.,places=9)
        # 任意姿态下，按当前姿态旋转重力；不把重力投影积分成速度。
        for seq in range(411, 511):
            result = estimator.update(sample(seq, angles=(60,30,125), bias=bias))
        self.assertAlmostEqual(result['vel_y_mm_s'],0.,places=9)
        np.testing.assert_allclose([result['bias_'+axis] for axis in 'xyz'],bias,atol=1e-12)

    def test_accelerate_then_coast_preserves_speed_and_uses_initial_heading(self):
        estimator = calibrated(angles=(0,0,90))
        for seq in range(211,311):
            result = estimator.update(sample(seq,acceleration=(-.1,0,0),angles=(0,0,90)))
        for seq in range(311, 511):
            result = estimator.update(sample(seq,angles=(0,0,90)))
        self.assertAlmostEqual(result['vel_y_mm_s'],100.,places=8)
        for old_key in ('vel_x','vel_y','vel_z','speed_mps'):
            self.assertNotIn(old_key,result)
        self.assertEqual(result['integration_time_source'],'chip')

    def test_needs_explicit_zero_and_moving_calibration_does_not_finish(self):
        estimator = ImuVelocityEstimator()
        result = estimator.update(sample(1))
        self.assertFalse(result['velocity_valid']); self.assertIsNone(result['vel_y_mm_s'])
        estimator.request_zero()
        for seq in range(2,302):
            data = sample(seq); data['gyro_z'] = 8.
            result = estimator.update(data)
        self.assertEqual(result['velocity_status'],'hold_still')
        self.assertFalse(result['velocity_valid'])

    def test_duplicate_gap_invalid_time_and_rezero_lifecycle(self):
        estimator = calibrated()
        self.assertIsNone(estimator.update(sample(210)))
        result = estimator.update(sample(212))
        self.assertEqual(result['velocity_status'],'gap_requires_zero')
        self.assertIsNone(result['vel_y_mm_s'])
        self.assertFalse(estimator.update(sample(213))['velocity_valid'])
        estimator.request_zero()
        for seq in range(214,425):
            result = estimator.update(sample(seq))
        self.assertTrue(result['velocity_valid'])
        self.assertEqual(result['velocity_generation'],2)
        result = estimator.update(dict(sample(425),chip_time_ms=None))
        self.assertFalse(result['velocity_valid'])

    def test_chip_midnight_wrap_and_explicit_nominal_time_fallback(self):
        estimator = ImuVelocityEstimator()
        estimator.update(dict(sample(1),chip_time_ms=86_399_995))
        result = estimator.update(dict(sample(2),chip_time_ms=5))
        self.assertAlmostEqual(result['integration_dt_s'],.01)
        estimator = ImuVelocityEstimator(VelocitySettings(time_source='nominal'))
        estimator.request_zero()
        for seq in range(1,211):
            result = estimator.update(sample(seq,chip=False))
        self.assertTrue(result['velocity_valid'])
        self.assertEqual(result['integration_time_source'],'nominal')


class ImuParsingTests(unittest.TestCase):
    def test_both_driver_packages_parse_and_notify_without_data_recorders(self):
        from drivers.HWT606_song_2.chs import JY901SDevice as LegacyDevice
        for driver in (JY901SDevice, LegacyDevice):
            with self.subTest(driver=driver.__module__):
                sensor = driver(port='MOCK')
                snapshots = []
                sensor.register_callback(lambda model: snapshots.append(model.getDeviceData('accY')))
                payload = wire_cycle(1, .1) + packet(0x54, struct.pack('<hhhh', 1, 2, 3, 4))
                for value in payload:
                    sensor.device.onDataReceived(bytes([value]))
                self.assertAlmostEqual(sensor.device.getDeviceData('accY'), 21/32768*16, places=4)
                self.assertTrue(snapshots)
                self.assertFalse(hasattr(sensor, 'recorder'))
                self.assertFalse(hasattr(sensor, 'start_recording'))
                self.assertFalse(hasattr(sensor, 'stop_recording'))

    def test_split_real_packets_once_per_cycle_and_incomplete_cycle_is_invalid(self):
        device = JY901SDevice(port='MOCK')
        snapshots = []
        device.register_callback(lambda model:snapshots.append(dict(model.imu_sample)))
        payload = wire_cycle(1,.1)+wire_cycle(2)+wire_cycle(3,omit_gyro=True)
        for value in payload:
            device.device.onDataReceived(bytes([value]))
        self.assertEqual([row['sample_sequence'] for row in snapshots],[1,2,3])
        self.assertEqual([row['chip_time_ms'] for row in snapshots],[10,20,30])
        self.assertEqual([row['frame_complete'] for row in snapshots],[True,True,False])
        self.assertEqual(snapshots[0]['acc_y'],21/32768*16)
        self.assertIsNot(device.device.protocolResolver.TempBytes,
                         JY901SDevice(port='OTHER').device.protocolResolver.TempBytes)


if __name__ == '__main__':
    unittest.main()
