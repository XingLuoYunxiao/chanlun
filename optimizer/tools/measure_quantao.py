"""测量：**区间套**（第 27 课）进强度判定后，生产排序变了多少。

用户指令③「让级别递归参与买卖点判定」。这个脚本是它的**可失败测量** ——
AGENTS.md §5 第 4 步要求「给出可失败的测量」，而且数字要能从写下的命令复现。

量三件事：

1. **上一级背驰段存不存在**（`find_divergences` 在 `levels[1]` 上找得到吗）。
   这一项如果恒为 0，说明区间套**永远不会确认**，那它就是个恒假的判据。
2. **第一类买卖点里有几个被确认**（`Signal.quantao is True`）。
3. **强度与 top-N 排序变不变**。区间套不是装饰 —— 它必须真的改到 `scan` 的产出。

跑法：
    cd <项目根>
    ../.venv-chanlun/bin/python optimizer/tools/measure_quantao.py --period 1 --sample 24

退出码 0 = 区间套**确实产生了区分度**（有确认的、且排序变了）；1 = 没有区分度。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from chanlun.chan.divergence import find_divergences  # noqa: E402
from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.chan.recursion import build_levels  # noqa: E402
from chanlun.chan.signal import find_signals  # noqa: E402
from chanlun.data import store  # noqa: E402
from chanlun.scan import scanner as S  # noqa: E402


def _keys(period: str, sample: int) -> list[str]:
    out: list[str] = []
    for mkt in ("sh", "sz"):
        for f in sorted(glob.glob(str(ROOT / "data" / period / mkt / "*.parquet"))):
            bare = Path(f).stem
            key = f"{mkt}.{bare}" if (mkt == "sh" and bare.startswith(("000", "399"))) else bare
            out.append(key)
            if len(out) >= sample:
                return out
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="1", help="基准显微镜周期（数据目录名）")
    ap.add_argument("--sample", type=int, default=24)
    ap.add_argument("--top", type=int, default=10, help="比较排序变化的前 N 名")
    args = ap.parse_args()

    keys = _keys(args.period, args.sample)
    if not keys:
        print(f"没有 {args.period} 数据，先回填："
              f"python optimizer/tools/backfill_one_minute.py --scope market")
        return 1

    n = hi_div = tot_first = tot_q = 0
    without: list[tuple] = []
    withq: list[tuple] = []
    for key in keys:
        bars = store.read(key, args.period)
        if bars.empty:
            continue
        n += 1
        snap = ChanEngine(key, args.period, signal_fn=find_signals).full(bars)
        levels = build_levels(snap.segments, bars=bars, base_level=args.period, max_depth=2)
        if len(levels) >= 2 and find_divergences(
            bars, list(levels[0].trends), levels[1].pivots, levels[1].level
        ):
            hi_div += 1
        first = [s for s in snap.signals if s.kind.value in ("b1", "s1")]
        tot_first += len(first)
        tot_q += sum(1 for s in first if s.quantao is True)
        for h in S.analyze_snapshot(snap, bars, name=key):
            sig = next((s for s in snap.signals
                        if s.ts == h.ts and s.kind.value == h.signal_kind), None)
            q = getattr(sig, "quantao", None)
            without.append((key, h.signal_kind, h.ts,
                            round(S.hit_strength(h.signal_kind, h.level,
                                                 h.divergence_strength, h.status)[0], 4)))
            withq.append((key, h.signal_kind, h.ts,
                          round(S.hit_strength(h.signal_kind, h.level,
                                               h.divergence_strength, h.status, q)[0], 4)))

    print(f"=== 区间套测量（{args.period} 基准，{n} 只）===")
    print(f"  有「上一级背驰段」的票：{hi_div}/{n}")
    print(f"  第一类买卖点 {tot_first} 个，其中**区间套确认** {tot_q} 个"
          f"（{100 * tot_q / max(tot_first, 1):.0f}%）")
    chg = sum(1 for a, b in zip(without, withq) if abs(a[3] - b[3]) > 1e-9)
    print(f"  强度发生变化的信号：{chg}/{len(without)}")
    tb = [x[:3] for x in sorted(without, key=lambda r: -r[3])[: args.top]]
    tn = [x[:3] for x in sorted(withq, key=lambda r: -r[3])[: args.top]]
    moved = sum(1 for a, b in zip(tb, tn) if a != b)
    print(f"  top{args.top} 排序变化：{moved}/{args.top} 个位次变了")
    for a, b in zip(tb, tn):
        if a != b:
            print(f"      {a[0]:10s}{a[1]}  ←  {b[0]:10s}{b[1]}")

    ok = tot_q > 0 and chg > 0 and moved > 0
    print("\n  ⇒ " + ("**区间套确实产生了区分度**（有确认、有强度变化、有排序变化）"
                      if ok else
                      "**没有区分度** —— 它要么恒假（上一级永远没有背驰段），"
                      "要么恒真（所有信号都一样），两种情况都说明这个判据没在测量任何东西"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
