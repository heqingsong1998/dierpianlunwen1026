# 上位机协议 v1 使用说明

启动入口仍为 `apps/main_window.py`。对应的下位机协议定义在项目上一级的 `Core/Inc/host_control_protocol.h` 和 `docs/host_control_protocol.md`。

## 本次配套内容

- 同时解析 `0x123` 的 48 字节数据、`0x124` 的 32 字节 stage 对和、`0x125` 的 32 字节反馈。支持串口任意分包、粘包和混合接收。
- `0x123` 原有通道、标定显示和原 CSV 列保留。末尾的帧编号及 A 独立按 uint32 解析，不把四个有符号 int16 当作四个力通道。
- 界面显示帧号、MCU 数据准备时间、PA4、电平命令状态、stage 对和、基准版本、丢弃计数及命令反馈。
- 原有六轴力主时钟的配对记录保留，GUI 每 100 ms 只刷新显示，不再额外插入一行记录。
- 接收/发送中的电脑时间使用 `perf_counter_ns()`，与六轴力 CSV 中 `raw_timestamp_ns` 的计时源一致。该字段是电脑收到六轴帧头时的时间，不是六轴设备硬件时间。

## 三个按钮

收到 `0x124` 并确认协议版本为 1 后，按钮使用新的 `C1` 命令和唯一命令号：

| 按钮 | 动作 |
|---|---|
| 回复 | 拉低 PA4 |
| 吸合 | 拉高 PA4 |
| 清零 | 请求下位机在下一帧更新基准，保持 PA4 |

手动按钮发送帧编号 0，记录 C 和唯一命令号，反馈中不会产生有效的传输/控制器时延。人的等待没有被计入算法计算时间。确认稳定时刻后，可以用 C 与六轴力接收时间轴分析近似执行时延。

收到 v1 的元数据之前，三个按钮继续使用原有 `A4/A5` 命令，保证旧设备的原有操作路径。界面中的清零反馈“已请求”表示等待采集任务处理，后续基准版本变化才表示完成。切勿把 GPIO_WRITTEN、GPIO_CHANGED 或命令接受反馈当成机械动作完成。

## 实验文件

点击“开始采集”后，每次会话目录包含：

| 文件 | 用途 |
|---|---|
| `nianfujiao_style0.csv` | 保留原有与六轴力时间轴配对的黏附脚数据，允许重复使用同一帧 |
| `liuzhouli.csv` | 标准六轴力数据及原电脑接收时间 |
| `nianfujiao_frames.csv` | 每个真正收到的 `0x123` 单独保存一次，不依赖六轴力连接/刷新 |
| `nianfujiao_stage_pairs.csv` | 每个 `0x124` 的八组对和、基准版本和丢弃计数 |
| `nianfujiao_commands.csv` | 动作、命令号、关联帧、B（若有）、C、发送结果 |
| `nianfujiao_feedback.csv` | 原始 MCU 时间/状态、反馈接收时间、匹配后的时延及无效原因 |

原黏附脚 CSV 前面的列顺序不变，后面追加 `sample_sequence`、`ready_us`、`host_received_ns`、`sensor_ts_ms`、`protocol_version`、`baseline_generation`、`pin_state`、`output_state`。

`0x123` 先于同编号的 `0x124` 到达时，真实帧记录中的基准版本尚未知，会留空；按 `(sample_sequence, ready_us)` 与 stage 文件关联得到版本，不把上一帧的版本强行赋给当前帧。

没有六轴力连接时，原黏附脚文件也按真实接收节拍保存；连接六轴力后恢复原来的配对记录方式。第一帧元数据尚未确认时协议版本可能为 0，保留接收事实，不猜测版本。

## 控制器对接接口

本次实现协议和记录配套，没有自动阈值算法，也没有连续自动发命令的后台控制器。

`SensorManager.nianfujiao_frame_callback` 是真实新帧入口，每帧最多触发一次，不会被 1000 Hz 配对记录重复触发。回调运行在接收线程中，未来控制器应在这里快速入队，避免在接收线程中运行长算法。

未来控制器开始接收输入时记录 B，处理/决策完成后调用统一出口：

```python
pending = manager.nianfujiao_send_timed_action(
    action,                 # 0=低，1=高，2=保持，3=清零
    input_frame,            # 真实输入帧字典，包含编号和 A；不能临时取最新 UI 缓存代替
    input_received_ns=B_ns, # 真正入控制器队列时的 perf_counter_ns()
)
```

驱动在提交串口写入之前记录 C、登记命令，再进行发送，即使反馈在线程中先于 `write/flush` 返回也能正确匹配。

也可直接调用设备接口：

```python
pending = device.send_timed_action(
    action, input_frame['sample_sequence'], input_frame['ready_us'], B_ns)
```

只有已收到协议版本 1 的 `0x124` 才允许这个测时接口。配对成功且固件状态/标志有效时，反馈 CSV 中计算：

```text
controller_us = (submitted_ns - input_received_ns) / 1000
transport_sum_us = round_trip_us - controller_us
```

其中 `round_trip_us` 是 MCU 的 D-A，不含反馈自身的回传时间。GPIO 写入等待单独按 uint32 回绕计算。状态拒绝、旧协议、未匹配、无输入关联、时间不一致及负值均标记无效、保留原因，不能用 0 伪装成测得的时延。

上位机控制器现已接入真实新帧队列、标定和双阈值判定，界面可设阈值并启停。详见 [控制器说明](../controller/README.md)。自动命令附带输入样本和 B/C；手动按钮仍为无输入关联命令。六轴力稳定时间仍需按实验判据分析，程序不会把命令反馈时间当成切向力稳定时间。

## 验证

在 `smzcode` 目录中运行：

```powershell
python -B -X utf8 -m unittest discover -s tests -v
```

新协议测试包含串口模拟、固件字节向量、uint32 高位及回绕、跨线程早到反馈、手动与关联命令区分、真实帧入口和 CSV 保存；原有六轴力清零测试继续运行。无硬件读写，不自动连接 COM5 或六轴力设备。
