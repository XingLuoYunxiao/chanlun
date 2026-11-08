"""测量：真递归（f2）与「每个周期各自用线段构中枢」的差异。

这是 D-51 的取证脚本。它要回答三个可失败的问题：

1. **真递归能不能跑出多级结构？** —— 若每级中枢数恒为 0 或 1，说明递归没接上。
2. **真递归的「30 分钟级别中枢」与现有「30 分钟图上的中枢」是不是一回事？**
   —— 第 63 课说「什么级别的图和什么级别的中枢没有任何必然关系」，所以预期
   **不是**一回事。两个数字若逐项相同，说明我的递归退化成了旧口径（no-op）。
3. **走势类型的方向到底交不交替？** —— `find_pivots` 的注释假设「相邻确认段
   方向必然交替」，但走势类型（尤其 `consolidation`）不一定满足。这个数字决定
   D-51 里方向口径那条该怎么写。

跑法（本机）：
    cd <项目根>
    PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_recursion.py

判据（可失败）：
    - `--sample N` 只跑前 N 只；`--period 5|30|day` 选基准显微镜。
    - 退出码 0 = 全部样本递归不变量通过；1 = 有样本违规（打印明细）。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import Counter

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from chanlun.chan.engine import ChanEngine
from chanlun.chan.recursion import build_levels, validate_levels

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
DATA = os.path.join(ROOT, "data")


def _files(period: str, sample: int | None) -> list[str]:
    out: list[str] = []
    for market in ("sh", "sz"):
        out += sorted(glob.glob(os.path.join(DATA, period, market, "*.parquet")))
    return out[:sample] if sample else out


def _tag(path: str) -> str:
    return f"{os.path.basename(os.path.dirname(path))}.{os.path.basename(path)[:-8]}"


def _span_sensitivity(files: list[str], period: str, max_depth: int) -> int:
    """跨度约定敏感性：A（连续 bar 跨度，现状） vs C（不传 `bars`，退化到 gg/dd 并集）。

    走势类型的 `high`/`low` 用什么跨度算，是本实现的一条**工程判据**（原文没有
    规定「两段走势类型之间那段连接走势归谁」）。这个测量要回答：换一种约定，
    上一级的中枢数会差多少？L0 必然相同（L0 不吃走势类型的区间）。
    """
    print("=== 跨度约定敏感性（A = 连续 bar 跨度 / C = 不传 bars 的退化口径）===")
    same = tot = 0
    for path in files:
        tag = _tag(path)
        bars = pd.read_parquet(path)
        segments = ChanEngine(tag, period=period).full(bars).segments
        a = build_levels(segments, bars=bars, base_level=period, max_depth=max_depth)
        c = build_levels(segments, bars=None, base_level=period, max_depth=max_depth)
        for i in range(max(len(a), len(c))):
            la = a[i] if i < len(a) else None
            lc = c[i] if i < len(c) else None
            av = f"{len(la.pivots)}/{len(la.trends)}" if la else "-"
            cv = f"{len(lc.pivots)}/{len(lc.trends)}" if lc else "-"
            eq = av == cv
            tot += 1
            same += eq
            level = (la or lc).level
            print(f"  {tag:14s} L{i}/{level:6s} A={av:>10s}  C={cv:>10s}  "
                  f"{'相同' if eq else '不同'}")
    print(f"  ⇒ 逐级相同 {same}/{tot}"
          + ("（全部相同 ⇒ 这条约定不影响任何产出）" if same == tot else ""))
    return 0


def _invariants(files: list[str], period: str, max_depth: int) -> int:
    """全市场扫一遍，逐只校验递归不变量，并给出级别深度分布。

    **为什么要跑全市场**：用户要的是「所有股票都改成递归算法」。抽样只能说明
    「抽样里没炸」，全市场才知道**广度**。日线基准 5471 只约 0.5 分钟，跑得起。

    校验的不变量（任何一条违反都是 bug，不是「口径差异」）：

    1. **`levels[0]` 必须与 `snap.pivots` 逐个相同。** ★ 最重要的一条：
       f2 只**往上加级**，碰最低级别就是错的。它是「递归是旧行为的超集」
       这句话的唯一保证 —— 没有它，`scan`/`backtest` 沿用 `snap.pivots`
       就不再等价于「用 L0」，而没人会知道。
    2. `validate_levels()` 全过（深度连续、级别名上升、走势类型只吃本级中枢…）。
    3. 每个级别的 `src_start/src_end` 落在 `[0, len(bars))` 且不倒置。
    4. 除最后一级外，每一级至少有一个中枢（「遇到空级别就停」的推论）。
    """
    depth_hist: Counter[int] = Counter()
    bad = 0
    checked = 0
    for path in files:
        tag = _tag(path)
        bars = pd.read_parquet(path)
        if bars.empty:
            continue
        checked += 1
        snap = ChanEngine(tag, period=period).full(bars)
        levels = build_levels(
            snap.segments, bars=bars, base_level=period, max_depth=max_depth
        )
        problems = list(validate_levels(levels))

        if not levels:
            problems.append("levels 为空 —— 至少应当有 depth=0")
        else:
            got = [(p.idx, round(p.zg, 6), round(p.zd, 6)) for p in levels[0].pivots]
            want = [(p.idx, round(p.zg, 6), round(p.zd, 6)) for p in snap.pivots]
            if got != want:
                problems.append(
                    f"★ L0 与 snap.pivots 不一致：{len(got)} vs {len(want)} 个"
                )
            for lv in levels:
                for p in list(lv.pivots) + list(lv.trends):
                    if not (0 <= int(p.src_start) <= int(p.src_end) < len(bars)):
                        problems.append(
                            f"{lv.level}: src 跨度 {p.src_start}..{p.src_end} "
                            f"越界（共 {len(bars)} 根）"
                        )
            for lv in levels[:-1]:
                if not lv.pivots:
                    problems.append(f"{lv.level}（非末级）没有任何中枢")

        depth_hist[len(levels)] += 1
        if problems:
            bad += 1
            if bad <= 10:                       # 只印前 10 只，余下计数
                print(f"[违规] {tag}")
                for p in problems[:4]:
                    print(f"        {p}")

    print(f"=== 递归不变量：全市场 {checked} 只 ===")
    print(f"  违规 {bad} 只 / {checked} 只")
    print("  级别数分布（levels 条数 → 只数）：")
    for n in sorted(depth_hist):
        pct = 100.0 * depth_hist[n] / checked if checked else 0.0
        print(f"    {n} 级：{depth_hist[n]:5d} 只（{pct:5.1f}%）")
    print(
        "  ★ L0 == snap.pivots 这一条是「递归是旧行为的超集」的唯一保证；"
        "它过了，沿用 `snap.pivots` 的 scan/backtest 才等价于「用 L0」。"
    )
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="5", help="基准显微镜周期（数据目录名）")
    ap.add_argument("--sample", type=int, default=None)
    ap.add_argument("--max-depth", type=int, default=6)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--span-sensitivity", action="store_true",
                    help="只跑跨度约定敏感性，其余判据不跑")
    ap.add_argument("--invariants", action="store_true",
                    help="只跑不变量全市场扫描（含 L0==snap.pivots 与级别数分布）")
    args = ap.parse_args()

    files = _files(args.period, args.sample)
    if not files:
        print(f"没有 {args.period} 分钟数据，换个 --period")
        return 1

    if args.invariants:
        return _invariants(files, args.period, args.max_depth)

    if args.span_sensitivity:
        return _span_sensitivity(files, args.period, args.max_depth)

    bad = 0
    alt_viol = Counter()
    alt_total = Counter()
    #: 真递归 L(k) 的中枢数 vs 旧口径「那个周期的图上」的中枢数
    cross: dict[str, list[tuple[int, int, str]]] = {}

    for path in files:
        tag = _tag(path)
        bars = pd.read_parquet(path)
        eng = ChanEngine(tag, period=args.period)
        snap = eng.full(bars)
        levels = build_levels(
            snap.segments, bars=bars, base_level=args.period, max_depth=args.max_depth
        )
        problems = validate_levels(levels)
        if problems:
            bad += 1
            print(f"[违规] {tag}")
            for p in problems[:5]:
                print(f"        {p}")

        if args.verbose:
            print(f"=== {tag}  bars={len(bars)} segs={len(snap.segments)}")
        for lv in levels:
            # 方向交替统计
            dirs = [t.direction for t in lv.trends]
            for a, b in zip(dirs, dirs[1:]):
                alt_total[lv.depth] += 1
                if a == b:
                    alt_viol[lv.depth] += 1
            if args.verbose:
                print(f"    L{lv.depth} {lv.level:6s} pivots={len(lv.pivots):3d} "
                      f"trends={len(lv.trends):3d}")
            # 与旧口径对比：拿 L(k) 的 level 名当周期，独立算一遍
            if lv.depth >= 1:
                try:
                    old_bars = pd.read_parquet(
                        os.path.join(
                            DATA, lv.level,
                            os.path.basename(os.path.dirname(path)),
                            os.path.basename(path),
                        )
                    )
                except FileNotFoundError:
                    continue
                old = ChanEngine(tag, period=lv.level).full(old_bars)
                cross.setdefault(lv.level, []).append(
                    (len(lv.pivots), len(old.pivots), tag)
                )

    print("\n=== 1) 递归层级收敛 ===")
    for path in files[:6]:
        tag = _tag(path)
        bars = pd.read_parquet(path)
        levels = build_levels(
            ChanEngine(tag, period=args.period).full(bars).segments,
            bars=bars,
            base_level=args.period,
            max_depth=args.max_depth,
        )
        chain = "  ".join(
            f"L{l.depth}/{l.level}:p{len(l.pivots)}t{len(l.trends)}" for l in levels
        )
        print(f"  {tag:14s} {chain}")

    print("\n=== 2) 真递归 vs 旧口径（同一只票、同一级别名）===")
    if not cross:
        print("  没有可对比的周期数据")
    for lv, rows in sorted(cross.items()):
        same = sum(1 for a, b, _ in rows if a == b)
        print(f"  [{lv}] {len(rows)} 只，真递归中枢数 / 旧口径中枢数：")
        for a, b, tag in rows:
            flag = "  <-- 相同" if a == b else ""
            print(f"      {tag:14s} {a:4d} / {b:4d}{flag}")
        print(f"      ⇒ 逐项相同 {same}/{len(rows)}")
        if same == len(rows):
            print("      ⚠ 全部相同 = 递归是 no-op，没在测量任何东西")

    print("\n=== 3) 走势类型方向交替率（`find_pivots` 的隐含假设）===")
    for depth in sorted(alt_total):
        tot = alt_total[depth]
        v = alt_viol[depth]
        if tot:
            print(f"  L{depth}: 相邻对 {tot}，同向 {v} ({v / tot:.1%})")

    print(f"\n=== 递归不变量：{len(files) - bad}/{len(files)} 只通过 ===")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
