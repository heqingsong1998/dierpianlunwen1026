# 上位机协议 v1 使用说明

启动入口仍为 `apps/main_window.py`。对应的下位机协议定义在项目上一级的 `Core/Inc/host_control_protocol.h` 和 `docs/host_control_protocol.md`。

## 本次配套内容

- 同时解析 `0x123` 的 48 字节数据、`0x124` 的 32 字节 stage 对和、`0x125` 的 32 字节反馈。支持串口任意分包、粘包和混合接收。
- `0x123` 的原有通道及标定显示保留；两个足端 CSV 统一导出 31 列，详见 [数据记录说明](data_recording.md)。末尾的帧编号及 A 独立按 uint32 解析，不把四个有符号 int16 当作四个力通道。
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
| `nianfujiao_frames.csv` | 每个真正收到的 `0x123` 单独保存一次，与 style0 共用 31 列，不依赖六轴力连接/刷新 |
| `nianfujiao_stage_pairs.csv` | 每个 `0x124` 的八组对和、基准版本和丢弃计数 |
| `nianfujiao_commands.csv` | 动作、命令号、关联帧、B（若有）、C、发送结果 |
| `nianfujiao_feedback.csv` | 原始 MCU 时间/状态、反馈接收时间、匹配后的时延及无效原因 |

两个足端 CSV 使用相同的 31 列：2 个时间字段、14 个原始信号、10 个标定结果，以及 `sample_sequence`、`ready_us`、`host_received_ns`、`baseline_generation`、`pin_state`。Mx/My 原始值和标定值保留；`flag1_raw/flag2_raw` 导出为 `JJJ2_1_raw/JJJ2_2_raw`；完整法向力导出为 `Fz1_cal/Fz2_cal`，合并原来的正负标定分支。旧的重复法向原始量、JJJ 的伪标定副本、停用标志和 reserved 视图不再写入这两个 CSV。协议版本仍在 stage/反馈文件中保留。修改只影响新会话，已有 CSV 不覆盖。

保存端按 `(sample_sequence, ready_us)` 匹配 `0x124` 的基准版本，支持补充帧先到或后到。尚未匹配的行最多等待 250 ms，按各文件原入队顺序输出；接收线程和控制器不等待补帧。若补帧缺失、等待缓存达到上限或停止采集，仍保留主数据行，未知版本留空，不使用上一帧版本替代。`timestamp_ms`、`host_received_ns` 始终保留原来的配对/接收时刻，不因等待而重新计时。

没有六轴力连接时，原黏附脚文件也按真实接收节拍保存；连接六轴力后恢复原来的配对记录方式。第一帧元数据尚未确认时协议版本可能为 0，保留接收事实，不猜测版本。

## 控制器对接接口

当前已接入独立的标定与双阈值控制器，详见 [控制器说明](../controller/README.md)。CSV 精简只改变保存视图，内部 24 字段协议视图及控制器输入保持原有格式。

`SensorManager.nianfujiao_frame_callback` 是可选的真实新帧观察入口，每帧最多触发一次，不会被 1000 Hz 配对记录重复触发。回调运行在接收线程中，应快速处理。现有控制器通过独立的新帧入队路径接收输入，不等待 CSV 保存端的基准版本匹配。

控制器开始接收输入时记录 B，处理/决策完成后调用统一出口：

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
