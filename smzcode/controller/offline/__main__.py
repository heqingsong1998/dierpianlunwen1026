"""python -m controller.offline reconstruct|fit --session 会话或父目录 --output 结果目录"""
import argparse
import json
from pathlib import Path

from .workflow import ReconstructionOptions, discover_sessions, run_analysis


def main():
    parser = argparse.ArgumentParser(description='固定周期时间重建及两路总法向力标定；不自动替换控制参数')
    parser.add_argument('mode', choices=['reconstruct','fit'])
    parser.add_argument('--session', action='append', required=True, help='会话或父目录，可重复')
    parser.add_argument('--output', required=True)
    parser.add_argument('--six-period-ms', type=float, default=1.0)
    parser.add_argument('--foot-period-ms', type=float, default=16.0)
    parser.add_argument('--offset-ms', type=float, default=0.0, help='所有选择会话的足端起点偏移；正值向后')
    args = parser.parse_args()
    try:
        paths = list(dict.fromkeys(p for root in args.session for p in discover_sessions(root)))
        options = ReconstructionOptions(args.six_period_ms, args.foot_period_ms)
        result = run_analysis(paths, args.output, options,
                              {str(Path(p).resolve()): args.offset_ms for p in paths},
                              fit=args.mode=='fit', progress=print)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc)+'\n')
    print(json.dumps({'output_dir':result['output_dir'],'formula':result['formula'],
                      'fit':result['fit']},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
