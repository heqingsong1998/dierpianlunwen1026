"""算法入口。当前仅标定；后续滤波/融合算法放在这里。"""


def process_signal(raw, calibration):
    # 判据使用未四舍五入的力值；显示与旧程序一样保留小数位。
    return calibration.apply(raw, rounded=False)
