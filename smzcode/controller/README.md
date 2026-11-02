# 黏附足上位机控制器

入口仍为 `apps/main_window.py`。控制器默认停止，连接黏附脚后由用户启动。

## 当前算法

```text
下位机 AD7142 采样、清零、组合信号
  → 真实 0x123 新帧
  → 控制器有界队列（记录 B）
  → algorithms/signal_processing.py：两路原始法向信号联合标定
  → algorithms/threshold.py：总法向力 ≥ 单一阈值（压缩为正）
  → 提交绑定原始样本编号的命令（驱动记录 C）
  → 下位机执行 GPIO 并返回 0x125
```

输入 `raw` 是下位机清零后的组合电容信号，不是原始 20 个 Stage 的绝对电容，也不是标准六轴力、界面显示值或同步配对重复行。控制器每个新帧重新标定，算法在独立工作线程中执行，串口接收回调只做输入快照和入队。显示、同步记录继续独立运行。

初始总法向力阈值为 **3.0 N**，默认触发电平为 **PA4 HIGH**。3 N 取旧的两个 1.5 N 阈值之和，仅作为新设置的起点，并不等价于原先“两片同时达标”的 AND 条件。实际释放对应的电平由驱动电路决定，在界面的“触发 PA4 电平”中选择。

总法向力直接取两片清零后上传的 `Fz1_raw`、`Fz2_raw`（内部 raw[2]、raw[9]），不经过单片旧标定：

```text
Fz_reference = -0.0005788864179388159 * Fz1_raw
               -0.0017128280366230562 * Fz2_raw
               -0.071442884800486                  # N，压缩为负，与六轴同号
Fz_total = -Fz_reference                          # N，压缩为正，控制输入
触发条件：Fz_total >= threshold_total_n
```

当前配置仍是前一轮 21 次实验按原始配对行得到的联合试拟合。现在可使用项目内离线工具按固定采样周期重新配对并求新系数；仅计算不会自动替换当前配置，也不代表已经完成独立验证。参数放在 `config/calibration.json` 的 `normal_force` 中。输出已是 N，不再乘旧的 9.8、1.6，不额外减会话基线，也不取绝对值。判定使用完整精度，界面显示四位小数。Fx/Fy/Mx/My 和单片 Fz 的旧标定继续作为观察数据保留；它们不再参与阈值判断。

达到阈值只触发一次；未达到、等待触发回执、触发后均发送 HOLD，保持引脚。回执确认 GPIO 写入后显示“已触发”；若引脚原本就是目标电平，会明确显示“无电平变化”。下一轮实验需要先把装置设为初始状态，再点击“启动 / 重新布防”。重新布防本身不复位引脚。

## 界面操作

1. 连接黏附脚和六轴力，完成清零，等待实时数据。下位机清零后的新基准尚未上传时，暂不允许布防。
2. 设置一个总法向力阈值（压缩为正）和触发电平，点击“应用并保存参数”。也可直接点击“启动 / 重新布防”，会先应用界面当前参数。
3. 开始数据采集以保存本次实验；采集与控制器各自独立启停。建议先开始记录，再布防，以保留完整事件。
4. 加载至总法向力达标时触发，界面显示回执确认状态。停止控制器只停止后续决策，保持当前引脚。

运行中应用阈值，对**之后进入队列**的帧生效，已入队帧使用入队时的参数快照。调整参数不会自动解除“已触发”的锁存，需要手动重新布防。手动“回复/吸合”、黏附脚清零、断开连接或退出时会停止控制器并清空待处理输入。检测到下位机零点基准变化也会停止，等待重新布防。

## 文件与参数

控制器主文件 `worker.py` 保留在外层，其余按算法、配置、显示和说明分类：

```text
controller/
├── worker.py                     # 控制器主文件
├── __init__.py
├── README.md
├── algorithms/                   # 信号处理与决策算法
│   ├── __init__.py
│   ├── signal_processing.py
│   ├── calibration.py
│   ├── normal_force.py
│   └── threshold.py
├── config/                       # 实验参数
│   ├── calibration.json
│   ├── settings.json
│   └── proximity_layout.json
├── offline/                      # 固定周期重建、联合标定、报告及命令行
│   ├── __init__.py
│   ├── __main__.py
│   ├── workflow.py
│   └── report.py
├── visualization/                # 接近觉显示和离线分析窗口
│   ├── __init__.py
│   ├── proximity_data.py
│   ├── proximity_view.py
│   └── offline_workbench.py
└── docs/
    ├── proximity_view.md
    └── offline_analysis.md
```

| 文件 | 用途 |
| --- | --- |
| `worker.py` | 独立线程、队列、状态、命令关联和回执处理 |
| `algorithms/signal_processing.py` | 信号处理入口；后续滤波、融合等算法在此扩展 |
| `algorithms/calibration.py` | 旧分量标定及联合总法向力的统一入口 |
| `algorithms/normal_force.py` | 两路 raw 联合计算、总力符号约定与参数校验 |
| `config/calibration.json` | 旧分量参数、normal_force 联合系数及模型编号 |
| `algorithms/threshold.py` | 总法向力单阈值判据及参数读写 |
| `config/settings.json` | 总法向力阈值 threshold_total_n 和触发电平 |
| `visualization/proximity_data.py` | 四路接近觉数据提取、四角位置及信号差计算 |
| `visualization/proximity_view.py` | 独立的接近觉 2×2 三维柱状图窗口 |
| `config/proximity_layout.json` | 接近觉俯视图的四角通道位置配置 |
| `offline/workflow.py` | 固定周期重建、线性插值配对、整次实验留出、备份应用配置 |
| `offline/report.py` | 重建 CSV、候选配置、PNG/SVG 及 HTML 报告 |
| `visualization/offline_workbench.py` | 选择多个会话、设置周期和偏移、一键标定与手动应用 |

主界面“粘附脚传感器”下的“接近觉 2×2 三维图”按钮可打开实时显示，用于手动调整姿态。显示操作与控制器启停、数据采集独立，使用方法及信号含义见 [接近觉显示说明](docs/proximity_view.md)。

主界面“数据采集控制”下的 **“离线分析：时间轴重建／总力标定”** 可打开离线工具。停止采集和控制器后，选择会话，设置六轴／足端周期及每次实验的起点偏移，即可重建、绘图和导出；也可重新计算联合系数、查看训练和整次实验留出误差，再手动应用。原始文件保留，计算结果另存。详细步骤、导出列及假设见 [离线分析说明](docs/offline_analysis.md)。

修改标定只需编辑 `config/calibration.json`，点击“重载标定”，然后重新布防；无需烧录单片机。总力参数在 `normal_force.coefficients=[a,b,c]`，直接计算 `a*raw[2]+b*raw[9]+c`，参考符号为压缩负；控制输入取反。旧分量参数仍在 `coefficients`，继续使用原来的二次多项式和单位换算。无效参数不会替换当前模型。修改总力系数时同时更新 `normal_force.model_id`，修改整个配置时更新顶层 `model_id`，便于追溯实验。

旧 settings.json 若只包含两片阈值，加载时会将两者之和作为初始总阈值；点击保存后只保留新字段。迁移值不保证与旧控制行为相同，需要重新设定实验阈值。

三个配置文件统一保存在 `config/`，加载路径相对于模块文件定位，不依赖启动时的工作目录。保存阈值和保存显示位置也会写回该目录。

输入队列最多 8 帧，过载时丢最旧待处理帧并计数；超过 500 ms 的旧输入不会发送命令。串口发送失败、下位机拒绝命令或触发命令 1 秒无回执会停止控制器，在界面显示原因。启动要求最近 500 ms 收到过版本 1 的 0x124 帧。Windows/Python 的线程调度不提供硬实时保证，应使用实验记录评估实际时延。

## 实验记录与时延

原有 `nianfujiao_style0.csv` 与 `liuzhouli.csv` 同步记录保留。

足端同步表和 `nianfujiao_frames.csv` 目前统一为 34 列：原 31 列顺序保留，末尾新增 `Fz_total_cal`（联合总力，压缩正）、`Fz_total_ref_cal`（六轴符号参考，压缩负）和 `normal_force_model_id`。新列直接记录显示和控制器共用模型的完整精度结果，不用旧单片力相加代替。仅新建会话使用新表头，已有实验文件不覆盖。字段明细见 [数据记录说明](../docs/data_recording.md)。

其余主要实验记录：

- `controller_config.json`：开始记录时实际采用的阈值、全部标定参数及控制状态。
- `nianfujiao_controller.csv`：启停、参数变化、触发确认，以及每个判定的总法向力、六轴符号参考、总阈值、模型编号、输入样本、命令 ID、B/C 和处理时延。中途参数更改也记录 JSON 事件，可还原参数历史。
- `nianfujiao_commands.csv` / `nianfujiao_feedback.csv`：保留驱动发送/固件反馈，按命令 ID 和样本编号关联控制器判定。不同线程可能使回执行先于判定行写入，应按键关联，不能按 CSV 行顺序配对。

`controller_us = (C - B)/1000`，包含排队、标定、阈值判断、构建命令和取得串口写锁等提交前开销。`processing_started_ns` 额外保留工作线程开始算法的时间，当前不再细分指标。

`transport_sum_us = MCU(D-A) - controller_us`，在有效关联回执中给出；HOLD 命令也能测量。上传部分包括数据就绪 A 至实际控制器入队 B 的前置开销；下传部分包括提交 C 至 MCU 解析 D 的传输开销。无效结果保留 `timing_error`，不能当作 0 时延使用。

近似执行时延仍用**实际触发电平变化的命令 C → 六轴力稳定**；六轴 `raw_timestamp_ns` 与 C 使用同一电脑 `perf_counter_ns()` 时间轴。回执只证明命令/GPIO处理，不代表电磁铁或切向力已稳定。当前没有自动计算力稳定时间，需根据实验定义的稳定判据分析六轴数据。原手动按钮仍是无输入关联命令，不用于统计控制器算法时间。

## 无硬件验证

在 `smzcode` 下运行：

```powershell
python -B -X utf8 -m unittest discover -s tests -v
```

控制器测试覆盖旧分量标定保持一致、联合总力与符号、单阈值边界、旧配置迁移、动态阈值、LOW/HIGH、单次触发、早到/迟到回执、拒绝/超时、清零换代、停止清队列、真实协议到控制器到 CSV，以及界面参数应用。使用模拟串口和本地回环，不会连接实物。

离线测试另外覆盖帧号缺口、手动偏移、不外推、已知系数恢复、整次实验留出、原文件不变、配置备份／失败回滚，以及后台计算、图像预览、参数改变使旧结果失效和手动应用入口。
