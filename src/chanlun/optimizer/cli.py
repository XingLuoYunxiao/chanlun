"""优化器命令行：``python -m chanlun.optimizer.cli --rounds N``。

退出码是有语义的，systemd 靠它决定「重启」还是「报警」：

- ``0``   正常跑完，没有熔断；
- ``3``   熔断：不许自动重启，必须人来看（``RestartPreventExitStatus=3``）；
- ``1``   自身出错（补丁没法 apply、测量脚本挂了等）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import AUDIT_SCRIPT, PROBE_SCRIPTS, Optimizer, default_probes, probe_script
from .journal import read_all

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CIRCUIT_BREAK = 3

#: 项目根：``src/chanlun/optimizer/cli.py`` → parents[3] == ``chanlun/``
DEFAULT_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chanlun-optimizer",
        description="缠论系统 7x24 优化器（只提案，不改主干算法）",
    )
    parser.add_argument("--root", default=str(DEFAULT_ROOT),
                        help="chanlun/ 项目根目录")
    parser.add_argument("--rounds", type=int, default=1,
                        help="本次最多跑几轮（默认 1；熔断会提前结束）")
    parser.add_argument("--propose-only", action="store_true", default=True,
                        help="只提案（默认且唯一支持的模式）")
    parser.add_argument("--measure", action="store_true",
                        help="在影子目录里真跑前后对比（慢，但数字才算数）")
    parser.add_argument("--list-probes", action="store_true",
                        help="列出已登记的问题观测点后退出")
    parser.add_argument("--audit", action="store_true",
                        help="在影子目录里跑一次全市场审计并打印一行 JSON 后退出")
    parser.add_argument("--patch", default="",
                        help="配合 --audit：先把这个补丁打进影子，再审计")
    parser.add_argument("--probe", default="",
                        help="配合 --audit：用这个观测点的尺子量"
                             f"（可选：{', '.join(sorted(PROBE_SCRIPTS))}；"
                             "默认是缠论流水线审计脚本）")
    parser.add_argument("--reset-breaker", action="store_true",
                        help="人工确认后清除熔断状态，继续跑")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()

    try:
        optimizer = Optimizer(
            root, propose_only=bool(args.propose_only), measure=args.measure
        )
    except ValueError as exc:
        print(f"优化器配置错误：{exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.list_probes:
        for probe in default_probes(root):
            patch = "有补丁" if probe.patch_text else "无补丁"
            print(f"{probe.rid}\t{probe.kind}\t{patch}\t{probe.finding}")
        print(f"理论库条目：{len(optimizer.theory)} 条（{optimizer.theory_dir}）")
        return EXIT_OK

    if args.audit:
        # 只读审计：主干 + 可选补丁各量一次，同一脚本、同一行情、同一口径。
        # 这是补丁头 `# evidence:` 里那串原始输出的出处，任何人都能重跑复核。
        patch_text = ""
        if args.patch:
            patch_path = Path(args.patch)
            if not patch_path.is_file():
                print(f"补丁不存在：{patch_path}", file=sys.stderr)
                return EXIT_ERROR
            patch_text = patch_path.read_text(encoding="utf-8")
        # 尺子按观测点选：G1/G2/G3 看流水线口径，G4/G5 看各自那几个数字。
        # 不指定 --probe 就用默认的流水线审计脚本。
        script = probe_script(optimizer.root, args.probe) if args.probe else AUDIT_SCRIPT
        try:
            result = optimizer.measure(script, patch_text or None)
        except (RuntimeError, OSError) as exc:
            print(f"审计失败：{exc}", file=sys.stderr)
            return EXIT_ERROR
        print(json.dumps(result, ensure_ascii=False))
        return EXIT_OK

    history = read_all(optimizer.journal_dir)
    state = optimizer.state
    if state.should_break and not args.reset_breaker:
        print(
            f"已处于熔断状态：{state.reason}\n"
            f"累计无有效产出轮数={state.barren_streak}，测试转红次数={state.red_transitions}\n"
            f"拒绝自动重启。请人工 review {optimizer.journal_dir} 后用 "
            f"--reset-breaker 恢复。",
            file=sys.stderr,
        )
        return EXIT_CIRCUIT_BREAK

    if args.rounds < 1:
        print(f"--rounds 必须 >=1，实际 {args.rounds}", file=sys.stderr)
        return EXIT_ERROR

    print(
        f"起点：第 {max((e.round for e in history), default=0) + 1} 轮，"
        f"历史 {len(history)} 轮，理论库 {len(optimizer.theory)} 条"
    )
    try:
        recorded = optimizer.run(rounds=args.rounds)
    except (RuntimeError, OSError) as exc:
        print(f"本轮执行失败：{exc}", file=sys.stderr)
        return EXIT_ERROR

    if not recorded:
        print("没有可回访的观测点（optimizer/patches/ 为空）", file=sys.stderr)
        return EXIT_OK

    for entry in recorded:
        moved = "数字有变化" if entry.effective else "无有效产出"
        print(
            f"round {entry.round:03d} [{entry.kind}/{entry.status}] "
            f"{entry.finding} —— {moved}"
        )

    if optimizer.state.should_break:
        print(f"熔断：{optimizer.state.reason}", file=sys.stderr)
        return EXIT_CIRCUIT_BREAK
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
