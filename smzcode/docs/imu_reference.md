# IMU 驱动与解析代码复用

主界面仅连接和显示黏附脚、六轴力。“连接所有”、数据采集、数据统计均只处理这两类设备；新建会话不生成 IMU CSV 或速度配置快照。

保留以下独立模块，供以后接入 IMU 时复用：

| 路径 | 内容 |
| --- | --- |
| `drivers/HWT606_song_1/chs/` | 第一套 JY901S/HWT606 设备封装、串口通信、校准与数据解析 |
| `drivers/HWT606_song_2/chs/` | 第二套旧驱动及数据解析 |
| 各驱动的 `lib/protocol_resolver/roles/` | WIT 和 485 协议解析、寄存器读写 |
| 各驱动的 `lib/data_processor/` | 解析数据的更新回调 |
| `core/imu_velocity.py` | 独立的姿态补偿、静止标定和短时速度估计算法 |
| `config/imu_reference.yaml` | 原来的 IMU 端口、波特率和输出参数；主程序不加载 |
| `apps/debug_hwt606_song.py` | 独立的终端调试入口，只读取并打印结果 |

两套驱动的数据记录器、记录接口，以及调试示例中的 CSV/TXT 数据写入代码均已移除。设备的 `save()` 用于保存传感器寄存器配置，保留供通信配置使用。

## 单独使用解析模块

在 `smzcode` 目录下运行自己的脚本。下面展示第一套驱动的使用方式，串口和波特率应按实际设备修改：

```python
from drivers.HWT606_song_1.chs import JY901SDevice


def on_sample(model):
    sample = dict(model.imu_sample)
    print(sample['acc_x'], sample['gyro_y'], sample['angle_z'])


sensor = JY901SDevice(port='COM12', baud=921600)
sensor.register_callback(on_sample)
try:
    sensor.open()
    sensor.write_config(rate=9, output_content=0x000F)
    input('按回车结束读取\n')
finally:
    sensor.close()
```

第一套 WIT 解析器在每个角度包结束时输出 `imu_sample`，其中包含加速度（g）、角速度（°/s）、角度（°）、温度、芯片时间、主机接收时间、帧号和 `frame_complete`。缺少本周期的新加速度或角速度时，会标记该周期不完整。第二套驱动保留原来的 `model.getDeviceData('accX')` 等访问接口。

速度估计可另行创建 `ImuVelocityEstimator`，确认设备静止后调用 `request_zero()`，再把第一套驱动的快照交给 `update(sample)`。默认周期 10 ms，使用芯片时间；`vel_y_mm_s` 是初始水平坐标系中的 Y 向速度，有正负号，单位 mm/s。该算法仍保留软件测试，但主程序不运行或保存其结果。

## 无硬件验证

在 `smzcode` 下执行：

```powershell
python -B -X utf8 -m unittest discover -s tests -v
```

测试保留协议拆包、完整周期判定和合成动力学检查，并验证主界面只连接两类设备、采集会话只生成足端、六轴力及控制记录。
