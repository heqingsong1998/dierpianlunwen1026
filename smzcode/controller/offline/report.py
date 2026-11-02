"""离线结果导出：原始文件只读，六轴连续线与足端真实帧散点。"""
import base64
import csv
import html
import re
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties, fontManager

from .workflow import predict, write_json


def _font():
    fonts = {item.name: item.fname for item in fontManager.ttflist}
    for name in ('Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC'):
        if name in fonts:
            return FontProperties(fname=fonts[name])
    return FontProperties(family='DejaVu Sans')


def _csv(path, columns, rows):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _plot(session, coefficients, destination, fit):
    font = _font()
    prediction = predict(session.raw, coefficients)
    contact = np.flatnonzero(session.reference < -session.options.active_force_n)
    center = session.six_ms[contact[0]] if len(contact) else 0.0
    sx, fx = session.six_ms-center, session.foot_ms-center
    figure = Figure(figsize=(13, 8), dpi=140, layout='constrained')
    FigureCanvasAgg(figure)
    for i, full in enumerate((False, True)):
        ax = figure.add_subplot(2, 1, i+1)
        lo, hi = (min(sx[0], fx[0]), max(sx[-1], fx[-1])) if full else (-100, 350)
        sm, fm = (sx >= lo) & (sx <= hi), (fx >= lo) & (fx <= hi)
        ax.plot(sx[sm], session.reference[sm], color='#2563eb', lw=1.0,
                label='六轴Fz原值')
        ax.scatter(fx[fm], prediction[fm], color='#dc2626', s=7 if full else 30,
                   edgecolors='white', linewidths=.2, zorder=3,
                   label='联合拟合总力' if fit else '当前联合模型总力')
        ax.set_xlim(lo, hi)
        ax.axhline(0, color='#9ca3af', lw=.6)
        ax.grid(axis='y', alpha=.15)
        ax.set_xlabel('名义时间 (ms；相对六轴首次明显压缩，两路同时平移)', fontproperties=font)
        ax.set_ylabel('Fz (N；压缩为负)', fontproperties=font)
        ax.set_title(session.path.name+(' · 全程' if full else ' · 碰撞局部'),
                     fontproperties=font, fontsize=13)
        ax.legend(prop=font, loc='best', fontsize=9)
        ax.ticklabel_format(axis='x', style='plain', useOffset=False)
    figure.suptitle('固定采样周期重建；首点对齐加手动偏移，尚未验证物理同步',
                    fontproperties=font, fontsize=14)
    figure.savefig(str(destination)+'.png', dpi=140)
    figure.savefig(str(destination)+'.svg')
    figure.clear()


def export_report(sessions, result, config, progress):
    out = Path(result['output_dir'])
    fitted = result['fit']
    chosen = fitted if fitted else config['normal_force']
    coefficients = chosen['coefficients']
    paired_rows, sections = [], []
    for i, session in enumerate(sessions):
        progress(f'导出数据与图 {i+1}/{len(sessions)}：{session.path.name}')
        slug = f'{i+1:02d}_' + re.sub(r'[^\w.-]+', '_', session.path.name)
        folder = out/slug
        folder.mkdir()
        prediction = predict(session.raw, coefficients)
        foot_columns = list(session.foot_rows[0])+[
            'analysis_nominal_ms', 'analysis_reference_available', 'analysis_six_Fz_N',
            'analysis_joint_reference_N', 'analysis_joint_compression_N', 'analysis_model_id',
        ]
        def foot_rows():
            for j, source in enumerate(session.foot_rows):
                yield {**source, 'analysis_nominal_ms': session.foot_ms[j],
                       'analysis_reference_available': bool(session.matched[j]),
                       'analysis_six_Fz_N': session.target[j] if session.matched[j] else '',
                       'analysis_joint_reference_N': prediction[j],
                       'analysis_joint_compression_N': -prediction[j],
                       'analysis_model_id': chosen['model_id']}
        _csv(folder/'foot_reconstructed.csv', foot_columns, foot_rows())
        _csv(folder/'six_reconstructed.csv', list(session.six_rows[0])+['analysis_nominal_ms'],
             ({**row, 'analysis_nominal_ms': float(session.six_ms[j])}
              for j,row in enumerate(session.six_rows)))
        for j in np.flatnonzero(session.matched):
            paired_rows.append({
                'session': str(session.path), 'sample_sequence': session.foot_rows[j]['sample_sequence'],
                'ready_us': session.foot_rows[j]['ready_us'], 'nominal_ms': session.foot_ms[j],
                'Fz1_raw': session.raw[j,0], 'Fz2_raw': session.raw[j,1],
                'six_Fz_interpolated_N': session.target[j],
                'joint_reference_N': prediction[j], 'joint_compression_N': -prediction[j],
                'model_id': chosen['model_id'],
            })
        _plot(session, coefficients, folder/'force_comparison', fitted is not None)
        result['sessions'][i]['plot_png'] = str(folder/'force_comparison.png')
        source = base64.b64encode((folder/'force_comparison.png').read_bytes()).decode()
        warnings = ''.join('<li>'+html.escape(w)+'</li>' for w in session.summary['warnings'])
        sections.append('<h2>'+html.escape(session.path.name)+'</h2><ul>'+warnings+'</ul>'+
                        '<img loading="lazy" src="data:image/png;base64,'+source+'">')
    _csv(out/'paired_for_calibration.csv',
         ['session','sample_sequence','ready_us','nominal_ms','Fz1_raw','Fz2_raw',
          'six_Fz_interpolated_N','joint_reference_N','joint_compression_N','model_id'], paired_rows)
    formula = f'Fz参考 = {coefficients[0]:.12g} × Fz1_raw {coefficients[1]:+.12g} × Fz2_raw {coefficients[2]:+.12g} N'
    result['formula'] = formula
    summary_columns = ['session','six_points','foot_frames','matched_frames','missing_sequence_slots',
                       'duplicate_foot_rows_removed','foot_offset_ms','six_span_ms','foot_span_ms',
                       'span_difference_ms']
    _csv(out/'session_summary.csv', summary_columns,
         ({k:session.summary[k] for k in summary_columns} for session in sessions))
    metrics_html = ''
    if fitted:
        write_json(out/'joint_fit.json', fitted)
        candidate = dict(config)
        candidate['model_id'] = 'foot_axes_'+fitted['model_id']
        candidate['normal_force'] = {
            'model_id': fitted['model_id'], 'status': 'trial',
            'reference_sign': 'compression_negative', 'coefficients': coefficients,
            'source': str(out/'analysis.json'),
        }
        write_json(out/'calibration_candidate.json', candidate)
        rows = []
        for key, label in [('training_all','训练：全部配对点'),
                           ('training_active','训练：明显受力点'),
                           ('leave_one_session_out_all','整次留出：全部可预测点'),
                           ('leave_one_session_out_active','整次留出：明显受力点')]:
            m = fitted[key]
            cells = (f'{m["n"]}</td><td>{m["mae_n"]:.4f}</td><td>{m["rmse_n"]:.4f}</td><td>{m["max_abs_n"]:.4f}'
                     if m else '无</td><td>—</td><td>—</td><td>—')
            rows.append('<tr><td>'+label+'</td><td>'+cells+'</td></tr>')
        metrics_html = '<h2>误差（N）</h2><table><tr><th>范围</th><th>点数</th><th>MAE</th><th>RMSE</th><th>最大绝对误差</th></tr>'+''.join(rows)+'</table>'
        if not fitted['validation_complete']:
            metrics_html += '<p class="note">完整逐实验留出验证不可用或存在失败折，请查看 joint_fit.json 的 folds。单次实验不能提供跨实验验证。</p>'
    options = result['options']
    doc = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+
           '<title>离线重建与联合标定</title><style>body{max-width:1400px;margin:30px auto;padding:0 22px;font:16px/1.8 "Microsoft YaHei",sans-serif;color:#1f2937}img{width:100%;height:auto}table{border-collapse:collapse;width:100%}th,td{padding:8px;border-bottom:1px solid #ddd;text-align:left}.note{padding:14px;background:#fff7ed;border-left:4px solid #d97706}</style>'+
           '<h1>固定采样周期重建与两路联合总力</h1><p class="note">假设六轴无漏点、重复点，且两端起点按设定偏移对齐；没有用峰值校正。原始文件保留。本结果不是硬件同步证明，也不是独立新实验的精度保证。</p>'+
           f'<p>六轴 {options["six_period_ms"]:g} ms／点；足端 {options["foot_period_ms"]:g} ms／帧。帧号缺口保留空位。六轴参考力按名义时间线性插值，不外推，不额外去零。</p>'+
           '<p>'+html.escape(formula)+'。控制总力取其相反数，压缩为正。</p>'+
           '<p>'+('当前红点使用本批数据拟合，属于拟合内展示；跨实验误差另见留出验证。' if fitted else '红点沿用当前配置的联合系数，本次没有重新拟合。')+'</p>'+
           f'<p>明显受力误差分组：|六轴Fz| &gt; {options["active_force_n"]:g} N，仅用于评价，未筛选训练数据。</p>'+
           metrics_html+''.join(sections)+'</html>')
    (out/'report.html').write_text(doc, encoding='utf8')
    result['report_html'] = str(out/'report.html')
