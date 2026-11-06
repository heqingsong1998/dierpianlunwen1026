"""保存训练损失和整次留一验证图。"""

import numpy as np


def save_training_plots(directory, losses, trials, scores, predictions, horizons_ms):
    from matplotlib import rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.font_manager import fontManager

    available_fonts = {font.name for font in fontManager.ttflist}
    font = next((name for name in ("Microsoft YaHei", "Noto Sans CJK SC", "SimHei")
                 if name in available_fonts), "DejaVu Sans")
    style = {
        "font.family": font,
        "axes.unicode_minus": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.alpha": 0.23,
    }
    with rc_context(style):
        loss_figure = Figure(figsize=(8, 4.8), layout="constrained")
        plot_losses(loss_figure, losses)
        FigureCanvasAgg(loss_figure).print_figure(directory / "training_loss.png", dpi=160)

        effect_figure = Figure(figsize=(12, max(4.6, 2.2 + 0.26 * len(trials))), layout="constrained")
        plot_validation(effect_figure, trials, scores, predictions, horizons_ms)
        FigureCanvasAgg(effect_figure).print_figure(directory / "training_effect.png", dpi=160)


def plot_losses(figure, losses):
    axis = figure.subplots()
    epochs = np.arange(1, len(losses) + 1)
    for values, label, color in zip(
        losses.T,
        ("总损失", "逐点力误差", "段内差分误差", "跨段差分误差"),
        ("#203D60", "#007D79", "#CE641E", "#8057A1"),
    ):
        axis.plot(epochs, values, label=label, color=color, linewidth=1.8,
                  marker="o" if len(epochs) == 1 else None)
    axis.set(title="入选单模型的全量训练损失", xlabel="训练轮数",
             ylabel=r"加权平方损失 ($\mathrm{N}^2$)", ylim=(0, None))
    axis.legend()


def plot_validation(figure, trials, scores, predictions, horizons_ms):
    trace, bars = figure.subplots(1, 2, width_ratios=(1.75, 1))
    rmse = np.array([score["forecast_rmse_n"] for score in scores])
    # Select the trial closest to the median forecast error.
    index = int(np.argmin(np.abs(rmse - np.median(rmse))))
    trial = trials[index]
    times_ms = (trial.times_ms[:, -1, None] - trial.collision_time_ms + horizons_ms).ravel()
    target = trial.target.ravel()
    predicted = predictions[index].ravel()
    breaks = np.flatnonzero(np.diff(times_ms) > 1.000001) + 1
    for group, points in enumerate(np.split(np.arange(len(times_ms)), breaks)):
        trace.plot(times_ms[points], target[points], color="#222222", linewidth=1.8,
                   label="六轴 Fz 实测（插值）" if group == 0 else None)
        trace.plot(times_ms[points], predicted[points], color="#007D79", linewidth=1.5,
                   label="PyramidMLP 留出预测" if group == 0 else None)
    trace.scatter(trial.foot_times_ms - trial.collision_time_ms, trial.foot_force_n,
                  s=29, facecolors="white", edgecolors="#BD4C28", linewidths=1.25,
                  zorder=4, label="黏附脚实测（标定合力）")
    trace.axvline(0, color="#687785", linewidth=1, linestyle=":")
    trace.set(title=f"留出示例 · {trial.name}",
              xlabel="相对六轴接触起点的名义时间 (ms)",
              ylabel="法向力 (N，压缩为正)")
    trace.legend()

    positions = np.arange(len(trials))
    bars.barh(positions, rmse, color="#6686A4")
    bars.set_yticks(positions, [trial.name for trial in trials])
    bars.invert_yaxis()
    bars.axvline(rmse.mean(), color="#CE641E", linestyle="--", linewidth=1.4)
    bars.set(title=f"各留出试次的预测误差\n试次等权平均 {rmse.mean():.2f} N",
             xlabel="未来 1–15 ms RMSE (N)")
    bars.yaxis.grid(False)
    figure.suptitle("训练效果 · 按整次实验留一验证", fontsize=15)
