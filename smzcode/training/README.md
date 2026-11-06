# 足端法向力模型训练

训练板端预测策略使用的 PyramidMLP。输入是足端合力和四路接近觉，标签是六轴 Fz。训练在电脑端比较三个随机种子，最终保存一份入选模型的 FP32 权重。模型输出当前时刻及未来 1–15 ms 的法向力，压缩为正。

## 放入数据并运行

默认数据目录定义在 `train.py` 开头的 `DATA_ROOT`：

```text
D:\北工大_何清松\论文写作\第二篇论文\足端程序修改\修改版本1\20x30_RTOS\data\实验训练数据
```

把上位机保存的完整采集会话放到该目录下，每次实验一个子目录。每个会话需要 `nianfujiao_frames.csv` 和 `liuzhouli.csv`。足端真实帧必须包含帧号、`ready_us`、`baseline_generation`、两路法向原始数字量和四路接近觉。至少准备 3 个含有效碰撞窗口的试次，用来做整次留一验证。空载、无碰撞数据可单独保留，用于后续检查误触发。

用上位机的 Python 环境，在 `smzcode` 目录下执行：

```powershell
python -m pip install -r training/requirements.txt
python -m training.train
```

也支持直接运行 `train.py`，此时不要求当前目录为 `smzcode`。例如在项目根目录执行：

```powershell
& 'd:\Anaconda\Autocalib\python.exe' .\smzcode\training\train.py
```

临时更换数据目录或联合标定时：

```powershell
python -m training.train --data-root 'D:\你的实验数据目录' --calibration 'D:\本次联合标定.json'
```

默认使用 1103 模型训练时的联合标定。脚本从两路清零后的法向数字量重算合力，保证同一次训练里的所有试次使用同一套系数。标定文件可以是联合标定对象、上位机标定配置或采集会话的 `controller_config.json`。板端回放和部署要使用该次训练保存的系数。

## 配对、训练和输出

- 时间配对复用上位机离线模块，足端 16 ms/帧，六轴 1 ms/点，默认首点对齐。已经测到每个试次的时差时，可在 `train.py` 的 `FOOT_OFFSETS_MS` 填入足端时间轴偏移，单位 ms。正值把足端时间轴向后移。名义配对结果用于训练，实际控制响应需要另行同步测量。
- 以六轴压缩力首次超过 0.5 N 为中心，在前 100 ms 到后 350 ms 选取窗口。每窗使用同一清零版本的连续 16 帧，缺失清零版本的帧会使所在窗口失效。训练沿用 2/4/8/16 帧特征、184 单元、段内差分损失和跨段损失。默认固定训练 200 轮，可用 `--epochs` 调整。留一折只用训练试次计算标准化，最终权重由全部输入试次重训。板端对照实验用另外采集的独立试次。
- 每个留出试次按预测策略回放，优先比较误触发和漏触发总数，再比较误触发数、预测越阈时刻的绝对误差、未来 1–15 ms 的 RMSE。越阈比较采用 ±16 ms 的名义时间容差。阈值优先读取会话的 `controller_config.json`，缺省为 3 N，可用 `--threshold` 对所有试次指定同一阈值。
- 每次训练写入 `smzcode/training/trained_models/`，保存单一权重 `pyramidmlp.npz`、`training_summary.json` 和两张 PNG。摘要含标定系数、输入源摘要、候选评分和入选种子。训练成功后整套覆盖。可用 `--output-root` 指定其他专用模型目录。
- `training_loss.png` 显示入选单模型全量重训的损失。`training_effect.png` 在代表性留出试次中对比入选种子的模型预测、六轴实测曲线和黏附脚标定合力散点，并列出各试次未来 1–15 ms 的留出预测 RMSE。每个足端散点对应一帧真实采样。
- 电脑上可以用 `python -m training.predict 历史16帧.csv 模型目录` 检查输出。输入 CSV 需要 `time_ms` 和模型的五个输入列，时间使用连续的 16 ms 名义间隔。NPZ 包含标准化、权重和偏置，后续可据此导出板端 C 参数。
