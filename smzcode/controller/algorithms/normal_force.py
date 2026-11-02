"""两片原始法向信号联合预测总法向力，输出直接为 N。"""
import math


class JointNormalForce:
    def __init__(self, config):
        if not isinstance(config, dict):
            raise ValueError('标定文件缺少 normal_force 联合总法向力模型')
        self.model_id = config.get('model_id')
        if not isinstance(self.model_id, str) or not self.model_id:
            raise ValueError('总法向力模型缺少 model_id')
        coefficients = config.get('coefficients')
        if (not isinstance(coefficients, list) or len(coefficients) != 3 or
                not all(isinstance(x, (int, float)) and math.isfinite(x)
                        for x in coefficients)):
            raise ValueError('总法向力需要三个有限系数 [a, b, c]')
        if config.get('reference_sign') != 'compression_negative':
            raise ValueError('总法向力参考符号必须为 compression_negative')
        self.a, self.b, self.c = coefficients

    def apply(self, raw):
        # raw[2]/raw[9] 是两片 Fz_raw（各自四个阵列点之和）。
        # 直接使用清零后上传的原值，不再减会话基线，不经过旧的单片标定。
        reference_n = self.a * raw[2] + self.b * raw[9] + self.c
        if not math.isfinite(reference_n):
            raise ValueError('联合总法向力结果非有限数值')
        return {
            'fz_total_reference_n': reference_n,  # 沿用六轴符号：压缩为负。
            'fz_total_cal': -reference_n,        # 控制判据：压缩为正，拉伸为负。
            'normal_force_model_id': self.model_id,
        }
