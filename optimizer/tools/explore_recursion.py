"""探索：走势类型序列的形状，以及真递归（f2）能否直接接上。

不是测量脚本（没有可失败判据），是设计前的取证。用完可留档。

跑法：
    PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/explore_recursion.py
"""
from __future__ import annotations

import glob
import os
import sys
from collections import Counter

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from chanlun.chan.engine import ChanEngine
from chanlun.chan.pivot import find_pivots
from chanlun.chan.trend import classify_trends

DATA = os.path.join(os.path.dirname(__file__), "..", "..", "data")


def load(period: str, market: str, code: str) -> pd.DataFrame:
    p = os.path.join(DATA, period, market, f"{code}.parquet")
    return pd.read_parquet(p)


def main() -> None:
    files = sorted(glob.glob(os.path.join(DATA, "day", "sh", "*.parquet")))[:12]
    kind_counter: Counter[str] = Counter()
    seq_counter: Counter[str] = Counter()
    viol = 0
    total_pairs = 0
    for f in files:
        code = os.path.basename(f).replace(".parquet", "")
        bars = pd.read_parquet(f)
        eng = ChanEngine(f"sh.{code}", period="day")
        snap = eng.full(bars)
        pivots = list(snap.pivots)
        trends = classify_trends(pivots, "day")
        kinds = [t.kind.value for t in trends]
        kind_counter.update(kinds)
        # 相邻走势类型的方向是否交替？
        dirs = []
        for t in trends:
            dirs.append({"up": 1, "down": -1, "consolidation": 0}[t.kind.value])
        for a, b in zip(dirs, dirs[1:]):
            total_pairs += 1
            if a == b and a != 0:
                viol += 1
            seq_counter[f"{a}->{b}"] += 1
        print(f"\n=== sh.{code}  bars={len(bars)} segs={len(snap.segments)} "
              f"pivots={len(pivots)} trends={len(trends)}")
        for t in trends:
            print(f"    {t.kind.value:14s} pivots[{t.start_idx}..{t.end_idx}] "
                  f"{t.start_ts} -> {t.end_ts}")
    print("\n=== 走势类型种类 ===")
    print(kind_counter)
    print(f"\n=== 相邻方向对（0=盘整）共 {total_pairs} 对，同向非零违规 {viol} ===")
    for k, v in sorted(seq_counter.items()):
        print(f"    {k:8s} {v}")


if __name__ == "__main__":
    main()
