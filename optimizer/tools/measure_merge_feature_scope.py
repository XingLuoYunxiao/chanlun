"""度量「标准特征序列的非包含处理只做单趟前向合并」的影响面。

第67课把标准特征序列定义成一个**结果性质**：

> 关于特征序列，把每一元素看成是一K线，那么，如同一般K线图中找分型的方法，
> 也存在所谓的包含关系，也可以对此进行非包含处理。**经过非包含处理的特征序列，
> 成为标准特征序列。**

但第65课给出的操作是单趟向前的，而且明说传递律不成立：

> 2、结合律是有关本ID这理论中最基础的，在K线的包含关系中，当然也需要遵守，而
> 包含关系，**不符合传递律**……因此在K线包含关系的分析中，还要遵守顺序原则，
> 就是先用第1、2根K线的包含关系确认新的K线，然后用新的K线去和第三根比……

于是 `_merge_feature` 原先的实现有一个洞：把第 i、i+1 个元素合并成新元素后，
**新元素可能反过来把第 i-1 个元素包含进去**，而单趟循环从不回头检查，序列里就
留下了相邻包含关系。`_is_fractal_at` 按第62课要求「第二K线高点是相邻三K线高点中
最高的，而低点也是相邻三K线低点中最高的」，被包含的那一对永远构不成分型，
真极值被永久跳过。

后果在 `sz.300760` 日线上最极端：2020-01-01 起 1636 根 K 线，132 笔被划成
「1 笔 + 127 笔 + 4 笔」三段、0 中枢，而修复后是 15 段 / 14 确认 / 2 中枢，
最长段从 127 笔降到 27 笔。

本工具在同进程内对比两种合并（旧 = 单趟前向，新 = 做到不动点），并统计
**相邻包含关系的残留数**（新算法下应恒为 0）。旧算法在这里是本地重建的，
所以无论主干当前是什么状态，这个对比都成立。

用法：
    PYTHONPATH=src python optimizer/tools/measure_merge_feature_scope.py
    PYTHONPATH=src python optimizer/tools/measure_merge_feature_scope.py --sample 100
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun.chan import segment as S  # noqa: E402
from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.data import store  # noqa: E402

#: 24 只日线样本，窗口 2020-01-01 起 —— 覆盖用户报告的票与「修复前塌陷最重」的票。
SHORT = (
    "sz.300760", "sh.600000", "sh.601088", "sz.300059", "sz.300750", "sh.600519",
    "sz.002415", "sh.600887", "sh.600276", "sh.600900", "sz.000651", "sz.000725",
    "sh.600030", "sh.600036", "sh.601012", "sh.601166", "sh.601318", "sh.601899",
    "sz.000001", "sz.000333", "sz.000858", "sz.002594", "sz.002714", "sz.300124",
)
#: 指数走全历史 —— 用户报的「深证成指只有一个向下线段」就在这里，短窗口碰不到。
FULL = ("sz.399001", "sz.399006", "sh.000001", "sh.000905", "sh.000300")
#: 非日线级别 —— 验证缺陷不是日线特有（5 分钟线 7.8 万根，笔数近 2000）。
INTRADAY = (("sh.600759", "5"), ("sh.600759", "30"), ("sh.600519", "30"))


def combine(h, l, si, high, low, si2, direction):
    """第65课的结合法则：向上取 [max di, max gi]，向下取 [min di, min gi]。"""
    if direction == 1:
        return max(h, high), max(l, low), (si2 if high >= h else si)
    return min(h, high), min(l, low), (si2 if low <= l else si)


def merge_old(elems, direction):
    """旧实现：单趟前向合并（合并出的新元素不再回头与前一个比）。"""
    out: list[list] = []
    for high, low, si in elems:
        if out:
            h, l, psi = out[-1]
            h, l, psi = float(h), float(l), int(psi)
            if (high <= h and low >= l) or (high >= h and low <= l):
                out[-1] = list(combine(h, l, psi, high, low, si, direction))
                continue
        out.append([high, low, si])
    return [(float(h), float(l), int(si)) for h, l, si in out]


def merge_new(elems, direction):
    """新实现：合并后回退检查，直到序列里不存在相邻包含关系（不动点）。"""
    out: list[list] = []
    for high, low, si in elems:
        if out:
            h, l, psi = out[-1]
            h, l, psi = float(h), float(l), int(psi)
            if (high <= h and low >= l) or (high >= h and low <= l):
                out[-1] = list(combine(h, l, psi, high, low, si, direction))
                while len(out) >= 2:
                    h1, l1, s1 = out[-2]
                    h2, l2, s2 = out[-1]
                    h1, l1, s1 = float(h1), float(l1), int(s1)
                    h2, l2, s2 = float(h2), float(l2), int(s2)
                    if not ((h2 <= h1 and l2 >= l1) or (h2 >= h1 and l2 <= l1)):
                        break
                    out.pop()
                    out[-1] = list(combine(h1, l1, s1, h2, l2, s2, direction))
                continue
        out.append([high, low, si])
    return [(float(h), float(l), int(si)) for h, l, si in out]


def contained(a, b) -> bool:
    return (b[0] <= a[0] and b[1] >= a[1]) or (b[0] >= a[0] and b[1] <= a[1])


def resid_containment(strokes) -> tuple[int, int]:
    """在所有可行起点上统计标准特征序列的相邻包含关系残留数。"""
    bad = tot = 0
    for start in range(len(strokes)):
        if not S._first_three_overlap(strokes, start):
            continue
        direction = strokes[start].direction
        feats = S._feature_seq(strokes, start, direction)
        if len(feats) < 3:
            continue
        tot += 1
        std = S._merge_feature(feats, direction)
        bad += sum(1 for k in range(1, len(std)) if contained(std[k - 1], std[k]))
    return bad, tot


def seg_stats(code: str, period: str, bars) -> dict:
    snap = ChanEngine(code, period).full(bars)
    counts = [s.stroke_count for s in snap.segments]
    return {
        "bars": len(bars),
        "strokes": len(snap.strokes),
        "segs": len(snap.segments),
        "confirmed": sum(1 for s in snap.segments if s.status.name == "CONFIRMED"),
        "pivots": len(snap.pivots),
        "maxseg": max(counts) if counts else 0,
        "resid": resid_containment(snap.strokes)[0],
    }


def _cases(sample: int) -> list[tuple[str, str, object]]:
    cases: list[tuple[str, str, object]] = [
        (c, "day", store.read(c, "day", start="2020-01-01")) for c in SHORT
    ]
    cases += [(c, "day", store.read(c, "day")) for c in FULL]
    cases += [(c, p, store.read(c, p)) for c, p in INTRADAY]
    if sample:
        root = store.data_root() / "day" / "sh"
        codes = sorted(p.stem for p in root.glob("*.parquet"))
        codes = random.Random(20261001).sample(codes, min(sample, len(codes)))
        cases += [(c, "day", store.read(c, "day", start="2020-01-01")) for c in codes]
    return [c for c in cases if len(c[2]) > 0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="额外随机抽取 N 只沪市日线")
    args = ap.parse_args()

    orig = S._merge_feature
    print(f"{'code':11s} {'周期':>4s} {'bars':>6s} {'笔':>5s} | "
          f"{'段 旧→新':>11s} {'确认':>9s} {'中枢':>8s} {'最长段':>11s} | 残留包含")
    total_old = total_new = 0
    worst: list[tuple[int, str, str, int, int]] = []
    for code, period, bars in _cases(args.sample):
        S._merge_feature = merge_old
        old = seg_stats(code, period, bars)
        S._merge_feature = merge_new
        new = seg_stats(code, period, bars)
        S._merge_feature = orig
        total_old += old["resid"]
        total_new += new["resid"]
        worst.append((old["maxseg"] - new["maxseg"], code, period, old["maxseg"], new["maxseg"]))
        print(f"{code:11s} {period:>4s} {old['bars']:6d} {old['strokes']:5d} | "
              f"{old['segs']:4d}→{new['segs']:<4d}   {old['confirmed']:3d}→{new['confirmed']:<3d}  "
              f"{old['pivots']:2d}→{new['pivots']:<2d}  {old['maxseg']:4d}→{new['maxseg']:<4d} | "
              f"{old['resid']}→{new['resid']}")
    print(f"\n相邻包含关系残留合计：{total_old} → {total_new}")
    print("最长段收缩最多的 8 个样本（旧→新）：")
    for d, code, period, a, b in sorted(worst, reverse=True)[:8]:
        print(f"  {code} {period}: {a} → {b} 笔（-{d}）")
    if total_new != 0:
        print("\n[失败] 新算法下仍有残留包含关系，非包含处理没有做到不动点。")
        return 1
    print("\n[通过] 新算法在所有样本上都不残留相邻包含关系。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
