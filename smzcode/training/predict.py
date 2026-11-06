"""用 NumPy 读取已保存的权重，并对最近 16 个足端帧推理。"""

import argparse
import csv
from pathlib import Path
import sys

import numpy as np


HISTORY_LENGTH = 16
SCALES = (2, 4, 8, 16)
MODEL_FILENAME = "pyramidmlp.npz"


def pyramid_features(history, times_ms, mean, std, scales):
    standardized = ((history - mean) / std).astype(np.float32)
    parts = []
    for scale in scales:
        window = standardized[:, -scale:]
        elapsed = times_ms[:, -1] - times_ms[:, -scale]
        parts.append(window.mean(axis=1))
        parts.append((window[:, -1] - window[:, 0]) / elapsed[:, None])
    return np.concatenate(parts, axis=1).astype(np.float32)


def model_files(model_dir):
    selected = Path(model_dir) / MODEL_FILENAME
    if selected.is_file():
        return [selected]
    paths = sorted(Path(model_dir).glob("pyramidmlp_seed*.npz"))
    if not paths:
        raise ValueError(f"未找到模型：{model_dir}")
    return paths


def predict(times_ms, history, model_dir):
    """按模型输入列的顺序提供 16 帧，返回 16 个压缩为正的力估计。"""
    times = np.asarray(times_ms, dtype=float)
    history = np.asarray(history, dtype=np.float32)
    if times.shape != (HISTORY_LENGTH,) or history.ndim != 2 or len(history) != HISTORY_LENGTH:
        raise ValueError("需要连续 16 个足端帧，按从旧到新排列")
    if not np.isfinite(times).all() or not np.isfinite(history).all():
        raise ValueError("时间与输入必须为有限数值")
    if not np.allclose(np.diff(times), 16, atol=1e-6, rtol=0):
        raise ValueError("time_ms 必须使用连续 16 ms 的名义时间轴")
    estimates = []
    input_columns = horizons = None
    for path in model_files(model_dir):
        with np.load(path, allow_pickle=False) as model:
            columns = tuple(model["input_columns"])
            offsets = tuple(model["h_ms"])
            if len(columns) != history.shape[1]:
                raise ValueError(f"模型输入列：{', '.join(columns)}")
            if estimates and (columns != input_columns or offsets != horizons):
                raise ValueError("各权重文件的输入列或输出时距不一致")
            input_columns, horizons = columns, offsets
            x = pyramid_features(history[None], times[None], model["scaler_mean"],
                                 model["scaler_std"], model["scales"])[0]
            hidden = np.maximum(0, model["net.0.weight"] @ x + model["net.0.bias"])
            estimates.append(model["net.2.weight"] @ hidden + model["net.2.bias"])
    return np.asarray(horizons), np.mean(estimates, axis=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path, help="含 time_ms 和模型输入列的 16 行 CSV")
    parser.add_argument("model_dir", type=Path, help="模型保存目录")
    args = parser.parse_args()
    try:
        with np.load(model_files(args.model_dir)[0], allow_pickle=False) as model:
            columns = model["input_columns"].tolist()
        with args.history.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        times = [float(row["time_ms"]) for row in rows]
        history = [[float(row[column]) for column in columns] for row in rows]
        horizons, force = predict(times, history, args.model_dir)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(("h_ms", "target_time_ms", "predicted_Fz_N"))
    writer.writerows(zip(horizons, times[-1] + horizons, force))


if __name__ == "__main__":
    main()
