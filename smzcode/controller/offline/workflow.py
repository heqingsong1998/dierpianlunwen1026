"""可由 GUI 或命令行复用的离线分析流程。仅写入新建的结果目录。"""
from dataclasses import asdict, dataclass
from datetime import datetime
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import uuid

import numpy as np

from ..algorithms.calibration import CALIBRATION_PATH, Calibration


@dataclass(frozen=True)
class ReconstructionOptions:
    six_period_ms: float = 1.0
    foot_period_ms: float = 16.0
    active_force_n: float = 0.5  # 只用于误差分组，不筛选训练点。

    def __post_init__(self):
        if any(not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError('采样周期和受力误差分组阈值必须为有限正数')


@dataclass
class ReconstructedSession:
    path: Path
    options: ReconstructionOptions
    offset_ms: float
    foot_rows: list
    six_rows: list
    foot_ms: np.ndarray
    six_ms: np.ndarray
    raw: np.ndarray
    reference: np.ndarray
    target: np.ndarray
    matched: np.ndarray
    summary: dict
    sources: list


def discover_sessions(directory):
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError('请选择采集会话目录或其父目录')
    if (directory / 'nianfujiao_frames.csv').is_file():
        candidates = [directory]
    else:
        candidates = sorted({p.parent for p in directory.rglob('nianfujiao_frames.csv')})
    return [p for p in candidates if (p / 'liuzhouli.csv').is_file()]


def _read_csv(path, required):
    payload = path.read_bytes()
    reader = csv.DictReader(io.StringIO(payload.decode('utf-8-sig')))
    if not required <= set(reader.fieldnames or []):
        raise ValueError(f'{path.name} 缺少列：{", ".join(sorted(required-set(reader.fieldnames or [])))}')
    rows = list(reader)
    if not rows:
        raise ValueError(f'{path.name} 没有数据，请先完成采集')
    return rows, {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest(),
                  'rows': len(rows)}


def reconstruct_session(path, options=None, offset_ms=0.0):
    options = options or ReconstructionOptions()
    if not math.isfinite(offset_ms):
        raise ValueError('足端起点偏移必须为有限数值')
    path = Path(path).resolve()
    foot, fs = _read_csv(path / 'nianfujiao_frames.csv',
                         {'sample_sequence', 'ready_us', 'Fz1_raw', 'Fz2_raw'})
    six, ss = _read_csv(path / 'liuzhouli.csv', {'Fz'})
    unique, raw, sequences, ready, seen = [], [], [], [], {}
    try:
        for row in foot:
            seq, stamp = int(row['sample_sequence']), int(row['ready_us'])
            values = (float(row['Fz1_raw']), float(row['Fz2_raw']))
            if not 1 <= seq <= 0xFFFFFFFF or not 0 <= stamp <= 0xFFFFFFFF:
                raise ValueError('帧号或ready_us超出uint32范围')
            if not all(math.isfinite(v) for v in values):
                raise ValueError('足端原始值不是有限数值')
            key = seq, stamp
            if key in seen:
                if seen[key] != values:
                    raise ValueError('相同帧号和ready_us对应不同原始值')
                continue
            seen[key] = values
            unique.append(row); raw.append(values); sequences.append(seq); ready.append(stamp)
        reference = np.array([float(row['Fz']) for row in six])
        if not np.isfinite(reference).all():
            raise ValueError('六轴Fz不是有限数值')
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f'{path.name} 数据格式错误：{exc}') from exc
    if len(reference) < 2:
        raise ValueError('六轴至少需要两个点才能进行时间配对')
    steps = []
    for previous, current in zip(sequences, sequences[1:]):
        delta = current-previous
        if previous > 0xF0000000 and current < 0x0FFFFFFF:
            delta = (current-previous) % (2**32) - 1  # 固件回绕时跳过0号帧。
        if delta <= 0:
            raise ValueError(f'{path.name} 帧号倒退或单片机重启，请分段后再分析')
        steps.append(delta)
    slots = np.r_[0, np.cumsum(steps)]
    foot_ms = slots * options.foot_period_ms + offset_ms
    six_ms = np.arange(len(reference)) * options.six_period_ms
    matched = (foot_ms >= six_ms[0]) & (foot_ms <= six_ms[-1])
    target = np.full(len(unique), np.nan)
    target[matched] = np.interp(foot_ms[matched], six_ms, reference)
    warnings = []
    missing = sum(delta-1 for delta in steps)
    if missing:
        warnings.append(f'足端原始记录缺少{missing}个帧号；保留相应时间空位，不补造数据')
    span_difference = float(slots[-1] * options.foot_period_ms-six_ms[-1])
    if abs(span_difference) > max(100.0, 2*options.foot_period_ms):
        warnings.append(f'两端名义首末跨度相差{span_difference:+.1f} ms，请检查覆盖区间、采样率或漏点')
    if not matched.all():
        warnings.append(f'{int((~matched).sum())}个足端帧在六轴时间范围外，保留绘图但不外推参考力')
    ready_elapsed = np.r_[0, np.cumsum(np.diff(np.array(ready, dtype=np.int64)) % (2**32))]/1000
    summary = {
        'session': path.name, 'path': str(path), 'six_points': len(six),
        'foot_frames': len(unique), 'duplicate_foot_rows_removed': len(foot)-len(unique),
        'missing_sequence_slots': missing, 'matched_frames': int(matched.sum()),
        'foot_offset_ms': offset_ms, 'six_span_ms': float(six_ms[-1]),
        'foot_span_ms': float(slots[-1]*options.foot_period_ms),
        'foot_ready_span_ms': float(ready_elapsed[-1]),
        'span_difference_ms': span_difference,
        'nominal_vs_ready_max_deviation_ms': float(np.max(abs(slots*options.foot_period_ms-ready_elapsed))),
        'warnings': warnings,
    }
    return ReconstructedSession(path, options, offset_ms, unique, six, foot_ms, six_ms,
                                np.array(raw), reference, target, matched, summary, [fs, ss])


def metrics(target, prediction):
    if not len(target):
        return None
    error = prediction-target
    denominator = np.sum((target-target.mean())**2)
    return {'n': len(target), 'mae_n': float(np.mean(abs(error))),
            'rmse_n': float(np.sqrt(np.mean(error**2))),
            'max_abs_n': float(np.max(abs(error))),
            'r2': float(1-np.sum(error**2)/denominator) if denominator > 0 else None}


def _fit(raw, target):
    if len(target) < 3:
        raise ValueError('至少需要3个有效配对点')
    matrix = np.column_stack((raw/1000, np.ones(len(raw))))
    if np.linalg.matrix_rank(matrix) < 3:
        raise ValueError('两路输入不能独立确定三个系数，请增加不同载荷分配的数据')
    condition = float(np.linalg.cond(matrix))
    if condition > 1e8:
        raise ValueError('输入高度相关，联合系数不稳定，请增加独立变化的数据')
    coefficients = np.linalg.lstsq(matrix, target, rcond=None)[0]
    return [float(coefficients[0]/1000), float(coefficients[1]/1000),
            float(coefficients[2])], condition


def predict(raw, coefficients):
    return raw @ np.asarray(coefficients[:2]) + coefficients[2]


def fit_sessions(sessions, options):
    if not sessions or any(not s.matched.any() for s in sessions):
        raise ValueError('每个勾选会话都需要存在重叠的有效配对点')
    raw = np.concatenate([s.raw[s.matched] for s in sessions])
    target = np.concatenate([s.target[s.matched] for s in sessions])
    groups = np.concatenate([np.full(int(s.matched.sum()), i) for i,s in enumerate(sessions)])
    coefficients, condition = _fit(raw, target)
    prediction = predict(raw, coefficients)
    active = abs(target) > options.active_force_n
    oof = np.full(len(target), np.nan)
    folds = []
    if len(sessions) > 1:
        for i, session in enumerate(sessions):
            test = groups == i
            try:
                fold_coefficients, _ = _fit(raw[~test], target[~test])
                oof[test] = predict(raw[test], fold_coefficients)
                folds.append({'session': str(session.path), 'coefficients': fold_coefficients,
                              'all': metrics(target[test], oof[test]),
                              'active': metrics(target[test & active], oof[test & active])})
            except ValueError as exc:
                folds.append({'session': str(session.path), 'error': str(exc)})
    valid = np.isfinite(oof)
    model_id = 'joint_nominal_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    return {
        'model_id': model_id, 'status': 'trial', 'reference_sign': 'compression_negative',
        'coefficients': coefficients, 'condition_number_scaled': condition,
        'training_all': metrics(target, prediction),
        'training_active': metrics(target[active], prediction[active]),
        'leave_one_session_out_all': metrics(target[valid], oof[valid]),
        'leave_one_session_out_active': metrics(target[valid & active], oof[valid & active]),
        'validation_predicted_points': int(valid.sum()), 'training_points': len(target),
        'validation_complete': len(sessions) > 1 and bool(valid.all()),
        'folds': folds, 'options': asdict(options),
        'method': 'nominal periods; per-session first-point alignment plus manual foot offset; linear interpolation of six-axis Fz; no extra zeroing; equal weight for each matched foot frame',
        'physical_synchronization_verified': False,
        'independent_new_test_used': False,
    }


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n',
                          encoding='utf-8')


def atomic_write(path, payload):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_result(result, config_path=CALIBRATION_PATH, reload_callback=None):
    if result['fit'] is None:
        raise ValueError('请先完成联合标定')
    config_path = Path(config_path)
    original = config_path.read_bytes()
    config = json.loads(original.decode('utf-8-sig'))
    fitted = result['fit']
    config['model_id'] = 'foot_axes_' + fitted['model_id']
    config['normal_force'] = {
        'model_id': fitted['model_id'], 'coefficients': fitted['coefficients'],
        'status': 'trial', 'reference_sign': 'compression_negative',
        'source': str(Path(result['output_dir'])/'analysis.json'),
    }
    payload = (json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf8')
    # 先验证完整候选配置，再替换当前配置；保留其他分量的原参数。
    fd, temporary = tempfile.mkstemp(suffix='.json', dir=result['output_dir'])
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
        Calibration(temporary)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    backup = Path(result['output_dir'])/('calibration_before_apply_'+uuid.uuid4().hex[:8]+'.json')
    backup.write_bytes(original)
    try:
        atomic_write(config_path, payload)
        if reload_callback:
            reload_callback(config_path)
    except Exception:
        atomic_write(config_path, original)
        raise
    write_json(Path(result['output_dir'])/'last_application.json',
               {'config_path': str(config_path.resolve()), 'backup_path': str(backup),
                'model_id': fitted['model_id'], 'applied_at': datetime.now().isoformat()})
    return backup


def run_analysis(paths, output_root, options=None, offsets=None, fit=False,
                 progress=None, config_path=CALIBRATION_PATH):
    options = options or ReconstructionOptions()
    offsets = offsets or {}
    progress = progress or (lambda message: None)
    paths = [Path(p).resolve() for p in paths]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError('请至少选择一个会话，同一会话不能重复')
    output_root = Path(output_root).resolve()
    if any(output_root == p or p in output_root.parents for p in paths):
        raise ValueError('结果目录请选择在原始会话目录之外')
    config = Calibration(config_path).snapshot()
    sessions = []
    for i, path in enumerate(paths):
        progress(f'重建 {i+1}/{len(paths)}：{path.name}')
        sessions.append(reconstruct_session(path, options, float(offsets.get(str(path), 0.0))))
    progress('计算联合模型与逐实验留出误差' if fit else '准备当前模型的重建图')
    fitted = fit_sessions(sessions, options) if fit else None
    for session in sessions:
        for source in session.sources:
            if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() != source['sha256']:
                raise ValueError('原始文件在读取过程中发生变化，请停止采集后重试')
    output_root.mkdir(parents=True, exist_ok=True)
    out = output_root/('analysis_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid.uuid4().hex[:6])
    out.mkdir()
    result = {
        'output_dir': str(out), 'options': asdict(options), 'fit': fitted,
        'sessions': [s.summary for s in sessions],
        'sources': [src for s in sessions for src in s.sources],
        'current_model': config['normal_force'],
        'physical_synchronization_verified': False,
        'first_points_assumed_simultaneous_before_manual_offset': True,
    }
    from .report import export_report
    export_report(sessions, result, config, progress)
    write_json(out/'analysis.json', result)
    return result
