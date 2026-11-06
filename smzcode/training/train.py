"""从足端和六轴的采集会话重训 PyramidMLP。支持直接运行或 python -m training.train。"""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import tempfile

import numpy as np

# 直接运行本文件时，将 smzcode 加入搜索路径，并为相对导入指定包名。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "training"

from controller.algorithms.normal_force import JointNormalForce
from controller.offline.workflow import discover_sessions, reconstruct_session
from .plots import save_training_plots
from .predict import HISTORY_LENGTH, MODEL_FILENAME, SCALES, pyramid_features


# 修改此处即可更换默认训练数据目录，也可以传入 --data-root。
DATA_ROOT = Path(r"D:\北工大_何清松\论文写作\第二篇论文\足端程序修改\修改版本1\20x30_RTOS\data\实验训练数据")
OUTPUT_ROOT = Path(__file__).resolve().parent / "trained_models"

# 1103 三种子模型对应的联合标定快照。其他标定用 --calibration 指定。
DEFAULT_CALIBRATION = {
    "model_id": "joint_nominal_20261102_220006_663111",
    "reference_sign": "compression_negative",
    "coefficients": [-0.0001250350686552863, -0.0021437948772073767, -0.10909380228453669],
}
# 可选。键为相对于 DATA_ROOT 的会话目录，例如 "批次1/试次1"。
FOOT_OFFSETS_MS = {}
INPUT_COLUMNS = ("Fz_total_cal", "JJJ1_1_raw", "JJJ1_2_raw", "JJJ2_1_raw", "JJJ2_2_raw")
HORIZONS_MS = np.arange(16)
SEEDS = (2026, 2027, 2028)
HIDDEN_WIDTH = 184
LEARNING_RATE = 0.001
DEFAULT_EPOCHS = 200
COLLISION_WINDOW_MS = (-100.0, 350.0)
CONTACT_REFERENCE_N = 0.5
LABEL_MARGIN_MS = 8.0
DEFAULT_THRESHOLD_N = 3.0
TIMING_TOLERANCE_MS = 16.0


@dataclass
class Trial:
    name: str
    history: np.ndarray
    times_ms: np.ndarray
    target: np.ndarray
    earlier: np.ndarray
    collision_time_ms: float
    foot_times_ms: np.ndarray
    foot_force_n: np.ndarray
    threshold_n: float
    reference_crossing_ms: float | None
    audit: dict


def read_calibration(path):
    if path is None:
        return DEFAULT_CALIBRATION.copy()
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    for key in ("calibration", "normal_force"):
        if not isinstance(config, dict):
            raise ValueError("标定 JSON 应为对象")
        config = config.get(key, config)
    return config


def crossing_ms(values, times, threshold):
    hits = np.flatnonzero(values >= threshold)
    if not len(hits):
        return None
    index = hits[0]
    if index == 0:
        return float(times[0])
    return float(np.interp(threshold, values[index - 1:index + 1], times[index - 1:index + 1]))


def read_trial(path, name, calibration, threshold_n=None):
    try:
        session = reconstruct_session(path, offset_ms=FOOT_OFFSETS_MS.get(name, 0.0))
        rows = session.foot_rows
        baseline_values = [row["baseline_generation"].strip() for row in rows]
        baselines = np.array([int(value) if value else -1 for value in baseline_values])
        if any(value and not 0 <= baseline <= 65535
               for value, baseline in zip(baseline_values, baselines)):
            raise ValueError("清零版本超出 uint16 范围")
        proximity = np.array([[float(row[column]) for column in INPUT_COLUMNS[1:]] for row in rows])
        force = -(calibration.a * session.raw[:, 0] + calibration.b * session.raw[:, 1] + calibration.c)
        signals = np.column_stack((force, proximity)).astype(np.float32)
        if not np.isfinite(signals).all():
            raise ValueError("传感数据必须为有限数值")
        reference = -session.reference
        contacts = np.flatnonzero(reference > CONTACT_REFERENCE_N)
        if not len(contacts):
            raise ValueError(f"六轴压缩力未超过 {CONTACT_REFERENCE_N:g} N，无法定位碰撞窗口")

        times = session.foot_ms
        reference_times = session.six_ms
        collision_time = reference_times[contacts[0]]
        start, stop = collision_time + np.array(COLLISION_WINDOW_MS)
        if threshold_n is None:
            config_path = path / "controller_config.json"
            config = (json.loads(config_path.read_text(encoding="utf-8-sig"))
                      if config_path.is_file() else {})
            threshold_n = float(config.get("settings", {}).get("threshold_total_n", DEFAULT_THRESHOLD_N))
        if not 0 < threshold_n <= 10000:
            raise ValueError("验证用法向力阈值必须大于 0 且不超过 10000 N")
        reference_window = (reference_times >= start) & (reference_times <= stop + HORIZONS_MS[-1])
        reference_crossing = crossing_ms(reference[reference_window], reference_times[reference_window], threshold_n)
        collision_frames = (times >= start) & (times <= stop)
        frame_period = session.options.foot_period_ms
        adjacent = np.isclose(np.diff(times), frame_period, rtol=0, atol=1e-6)
        issue_indices = []
        for index in np.flatnonzero(collision_frames):
            first = index - (HISTORY_LENGTH - 1)
            if first < 0 or baselines[index] < 0:
                continue
            history = slice(first, index + 1)
            if (np.all(baselines[history] == baselines[index])
                    and np.all(adjacent[first:index])
                    and reference_times[0] <= times[index] - LABEL_MARGIN_MS
                    and times[index] + frame_period + LABEL_MARGIN_MS <= reference_times[-1]):
                issue_indices.append(index)
        if not issue_indices:
            raise ValueError("没有满足连续 16 帧、同一清零基准和六轴覆盖条件的训练窗口")

        windows = np.array(issue_indices)[:, None] + np.arange(1 - HISTORY_LENGTH, 1)
        issue_times = times[issue_indices]
        issue_baselines = baselines[issue_indices]
        earlier = np.flatnonzero(np.isclose(np.diff(issue_times), frame_period, rtol=0, atol=1e-6)
                                 & (issue_baselines[1:] == issue_baselines[:-1]))
        if not len(earlier):
            raise ValueError("没有可用于跨段损失的连续训练窗口")
        target = np.interp(issue_times[:, None] + HORIZONS_MS, reference_times, reference)
        audit = dict(session.summary, trial=name, windows=len(windows), seam_pairs=len(earlier),
                     frames_missing_baseline=int(np.count_nonzero(baselines == -1)),
                     sources=session.sources)
        return Trial(name, signals[windows], times[windows], target.astype(np.float32),
                     earlier, collision_time, times[collision_frames], force[collision_frames],
                     threshold_n, reference_crossing, audit)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{name}：{exc}") from exc


def training_batch(trials, features, torch):
    targets = [trial.target for trial in trials]
    point_weights = [np.full(len(target), 1 / (len(trials) * len(target)), np.float32)
                     for target in targets]
    previous, seam_weights = [], []
    start = 0
    for trial in trials:
        previous.append(start + trial.earlier)
        seam_weights.append(np.full(len(trial.earlier), 1 / (len(trials) * len(trial.earlier)), np.float32))
        start += len(trial.target)
    return tuple(torch.from_numpy(np.concatenate(parts)) for parts in
                 (features, targets, point_weights, previous, seam_weights))


def fit_candidates(trials, epochs, torch, seeds, *, record_losses=False):
    flat = np.concatenate([trial.history for trial in trials]).reshape(-1, len(INPUT_COLUMNS))
    mean, std = flat.mean(axis=0), flat.std(axis=0)
    std[std < 1e-8] = 1.0
    features = [pyramid_features(trial.history, trial.times_ms, mean, std, SCALES)
                for trial in trials]
    x, y, point_weights, previous, seam_weights = training_batch(trials, features, torch)
    next_frame = previous + 1
    seam_target = y[next_frame, 0] - y[previous, -1]
    segment_target = y.diff(dim=1)
    models = []
    losses = np.empty((len(seeds), epochs, 4)) if record_losses else None
    for index, seed in enumerate(seeds):
        torch.manual_seed(seed)
        layers = torch.nn.Sequential(
            torch.nn.Linear(x.shape[1], HIDDEN_WIDTH), torch.nn.ReLU(),
            torch.nn.Linear(HIDDEN_WIDTH, len(HORIZONS_MS)),
        )
        optimizer = torch.optim.Adam(layers.parameters(), lr=LEARNING_RATE)
        for epoch in range(epochs):
            prediction = layers(x)
            point_loss = (point_weights * (prediction - y).square().mean(dim=1)).sum()
            segment_loss = (point_weights * (prediction.diff(dim=1) - segment_target).square().mean(dim=1)).sum()
            seam_error = prediction[next_frame, 0] - prediction[previous, -1] - seam_target
            seam_loss = (seam_weights * seam_error.square()).sum()
            loss = point_loss + segment_loss + seam_loss
            if losses is not None:
                losses[index, epoch] = [term.item() for term in (loss, point_loss, segment_loss, seam_loss)]
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
        if not all(torch.isfinite(parameter).all().item() for parameter in layers.parameters()):
            raise ValueError(f"种子 {seed} 训练产生非有限权重，请检查输入和标定")
        models.append(layers.eval())
    return mean, std, models, losses


def score_trial(trial, prediction):
    error = prediction - trial.target
    seam = error[trial.earlier + 1, 0] - error[trial.earlier, -1]
    continuous = np.zeros(len(prediction), dtype=bool)
    continuous[trial.earlier + 1] = True
    armed = False
    trigger_ms = forecast_ms = None
    for issue_ms, curve, adjacent in zip(trial.times_ms[:, -1], prediction, continuous):
        if not adjacent:
            armed = False
        crossing = crossing_ms(curve, HORIZONS_MS, trial.threshold_n)
        if crossing is None:
            armed = True
        elif armed:
            trigger_ms = float(issue_ms)
            forecast_ms = trigger_ms + crossing
            break
    reference = trial.reference_crossing_ms
    forecast_error = None if reference is None or forecast_ms is None else forecast_ms - reference
    early = forecast_error is not None and forecast_error < -TIMING_TOLERANCE_MS
    miss = reference is not None and (forecast_error is None or abs(forecast_error) > TIMING_TOLERANCE_MS)
    return dict(trial=trial.name, windows=len(error), threshold_n=trial.threshold_n,
                reference_crossing_ms=reference, trigger_issue_ms=trigger_ms,
                forecast_crossing_ms=forecast_ms, crossing_error_ms=forecast_error,
                false_trigger=forecast_ms is not None and (reference is None or early),
                missed_trigger=miss,
                current_rmse_n=float(np.sqrt(np.mean(error[:, 0] ** 2))),
                forecast_rmse_n=float(np.sqrt(np.mean(error[:, 1:] ** 2))),
                seam_rmse_n=float(np.sqrt(np.mean(seam ** 2))))


def validate(trials, epochs, torch):
    candidates = {seed: ([], []) for seed in SEEDS}
    for index, held_out in enumerate(trials):
        mean, std, models, _ = fit_candidates(trials[:index] + trials[index + 1:], epochs, torch, SEEDS)
        x = torch.from_numpy(pyramid_features(held_out.history, held_out.times_ms, mean, std, SCALES))
        with torch.no_grad():
            for seed, model in zip(SEEDS, models):
                prediction = model(x).numpy()
                scores, predictions = candidates[seed]
                scores.append(score_trial(held_out, prediction))
                predictions.append(prediction)
        print(f"已验证留出试次 {held_out.name}", flush=True)

    rankings = []
    for seed, (scores, _) in candidates.items():
        timing = [abs(score["crossing_error_ms"]) for score in scores if score["crossing_error_ms"] is not None]
        rankings.append(dict(seed=seed, false_triggers=sum(s["false_trigger"] for s in scores),
                             missed_triggers=sum(s["missed_trigger"] for s in scores),
                             crossing_mae_ms=float(np.mean(timing)) if timing else None,
                             forecast_rmse_n=float(np.mean([s["forecast_rmse_n"] for s in scores]))))
    rankings.sort(key=lambda row: (row["false_triggers"] + row["missed_triggers"], row["false_triggers"],
                                   float("inf") if row["crossing_mae_ms"] is None else row["crossing_mae_ms"],
                                   row["forecast_rmse_n"], row["seed"]))
    seed = rankings[0]["seed"]
    print(f"选定种子 {seed}，按误触发/漏触发、越阈误差和预测 RMSE 排序", flush=True)
    return seed, candidates[seed], rankings


def save_run(output_root, data_root, calibration, trials, selection, epochs, fit, torch):
    mean, std, (model,), losses = fit
    seed, validation, rankings = selection
    scores, predictions = validation
    names = [trial.name for trial in trials]
    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".training_", dir=output_root.parent) as temporary:
        run = Path(temporary) / "models"
        run.mkdir()
        state = {f"net.{key}": value.detach().numpy() for key, value in model.state_dict().items()}
        np.savez_compressed(run / MODEL_FILENAME, **state, scaler_mean=mean, scaler_std=std,
                            h_ms=HORIZONS_MS, scales=SCALES, input_columns=INPUT_COLUMNS,
                            training_trials=names, seed=seed)
        summary = dict(data_root=str(data_root), calibration=calibration, input_columns=INPUT_COLUMNS,
                       epochs=epochs, selected_seed=seed, candidate_scores=rankings,
                       timing_tolerance_ms=TIMING_TOLERANCE_MS, hidden_width=HIDDEN_WIDTH,
                       learning_rate=LEARNING_RATE, difference_weight=1.0, seam_weight=1.0,
                       horizons_ms=HORIZONS_MS.tolist(), collision_window_ms=COLLISION_WINDOW_MS,
                       contact_reference_n=CONTACT_REFERENCE_N, label_margin_ms=LABEL_MARGIN_MS,
                       torch_version=str(torch.__version__), numpy_version=np.__version__,
                       trials=[trial.audit for trial in trials], leave_one_trial_out=scores)
        (run / "training_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        save_training_plots(run, losses[0], trials, scores, predictions, HORIZONS_MS)
        replace_model_set(run, output_root, Path(temporary).with_name(Path(temporary).name + "_previous"))
    return output_root


def replace_model_set(staged, destination, backup):
    if not destination.exists():
        staged.rename(destination)
        return

    filenames = {file.name for file in staged.iterdir()}
    previous_files = list(destination.iterdir())
    if any(not file.is_file() or (file.name not in filenames
           and not re.fullmatch(r"pyramidmlp_seed\d+\.npz", file.name)) for file in previous_files):
        raise ValueError(f"输出目录含其他文件，请指定仅保存训练模型的目录：{destination}")

    # Rename the set as a whole so a failed save cannot leave mixed weights.
    destination.rename(backup)
    try:
        staged.rename(destination)
    except BaseException:
        if not destination.exists():
            backup.rename(destination)
        raise
    for file in previous_files:
        (backup / file.name).unlink()
    backup.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT, help="采集会话所在目录")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT, help="模型输出目录，成功后覆盖上一套权重")
    parser.add_argument("--calibration", type=Path, help="联合标定、完整标定或采集配置 JSON")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS, help="每个种子的训练轮数")
    parser.add_argument("--threshold", type=float, help="所有试次统一使用的模型选优阈值（N）")
    args = parser.parse_args()
    if args.epochs < 1:
        parser.error("--epochs 至少为 1")
    root = args.data_root.resolve()
    print(f"训练数据目录：{root}", flush=True)
    try:
        sessions = discover_sessions(root)
        if len(sessions) < 3:
            raise ValueError(f"找到 {len(sessions)} 个完整会话，整次留一验证至少需要 3 个；"
                             "每个会话应同时包含 nianfujiao_frames.csv 和 liuzhouli.csv")
        calibration = read_calibration(args.calibration)
        joint_force = JointNormalForce(calibration)
        trials = [read_trial(path, path.relative_to(root).as_posix(), joint_force, args.threshold)
                  for path in sessions]
        print(f"标定：{joint_force.model_id}；{len(trials)} 个试次，"
              f"{sum(len(trial.target) for trial in trials)} 个窗口", flush=True)
        import torch

        torch.set_num_threads(1)
        selection = validate(trials, args.epochs, torch)
        fit = fit_candidates(trials, args.epochs, torch, (selection[0],), record_losses=True)
        destination = save_run(args.output_root.resolve(), root, calibration, trials, selection,
                               args.epochs, fit, torch)
    except ModuleNotFoundError as exc:
        if exc.name not in {"torch", "matplotlib"}:
            raise
        parser.exit(1, f"缺少 {exc.name}，请运行 python -m pip install -r training/requirements.txt\n")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"训练停止：{exc}\n")
    print(f"模型已保存：{destination}")


if __name__ == "__main__":
    main()
