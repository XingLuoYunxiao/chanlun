"""线段划分的「全局最优」与「向前首个满足」两种目标对比。

第71课给出的判定程序是**局部、向前**的：

    假设某转折点是两线段的分界点，然后对此用线段划分的两种情况去考察是否满足
    ……如果不满足，那就不是，原来的线段依然延续，就这么简单

按这个字面读法，分界点是「从线段起点向前找到的**第一个**满足情况一/情况二的
特征序列分型」，而不是「让全序列线段数最多的那组分解」。`Lesson6768Policy.classify`
实现的却是后者（对 `best(s)` 做最大化 DP）。

本工具在 fixtures + 指数 + 全市场抽样上对比两种目标：

    T = 主干：DP 最大化线段数（左端取「可行起点中段数最多」者）
    G = 向前首个满足（左端同样取最优起点）

用于判断 `classify` 的 DP 是不是第 71 课字面规则的偏离，以及它值多少段。

用法：
    PYTHONPATH=src python optimizer/tools/measure_segment_objective.py [--sample N]
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

INDEX_CODES = ("sz.399001", "sz.399006", "sh.000001", "sh.000905", "sh.000300")
FIXTURES = ("sh.600000", "sh.601088", "sz.300059", "sz.300750")


def _plan(strokes, pol, greedy: bool):
    """返回 (start, breaks)。greedy=False 用最大化 DP，True 用向前首个满足。"""
    n = len(strokes)
    if n < pol.min_strokes:
        return 0, []
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 4 * n + 1000))
    memo: dict[int, tuple[int, object]] = {}

    def best(start):
        if start + pol.min_strokes > n:
            return 0, None
        if start in memo:
            return memo[start]
        res = (0, None)
        for br in pol.candidates(strokes, start):
            cnt, _ = best(br.stroke_idx + 1)
            if cnt + 1 > res[0]:
                res = (cnt + 1, br)
        memo[start] = res
        return res

    def walk(start: int) -> list:
        """从 start 起按规则走到底，返回分界列表。"""
        out = []
        while start < n:
            cands = list(pol.candidates(strokes, start))
            if not cands:
                break
            if greedy:
                br = cands[0]
            else:
                _, br = best(start)
                if br is None:
                    break
            out.append(br)
            start = br.stroke_idx + 1
        return out

    # 左端：两种目标都取「可行起点中段数最多」者（平局取更早）。
    start, target = 0, -1
    for s in range(n):
        if not list(pol.candidates(strokes, s)):
            continue
        cnt = len(walk(s))
        if cnt > target:
            start, target = s, cnt
    try:
        return start, walk(start)
    finally:
        sys.setrecursionlimit(old)


def _load(code: str):
    bars = store.read(code, "day")
    if bars is None or len(bars) == 0:
        return None
    return ChanEngine(code, "day").full(bars).strokes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20261001)
    args = ap.parse_args()

    codes = list(INDEX_CODES) + list(FIXTURES)
    if args.sample:
        root = store.data_root() / "day"
        allc = sorted(
            f"{p.parent.name}.{p.stem}" for p in root.glob("*/*.parquet")
        )
        rng = random.Random(args.seed)
        extra = [c for c in rng.sample(allc, min(args.sample, len(allc)))]
        codes += [c for c in extra if c not in codes]

    pol = S.Lesson6768Policy()
    T_tot = G_tot = 0
    T_long = G_long = 0
    better = worse = same = 0
    rows = []
    for code in codes:
        strokes = _load(code)
        if strokes is None or len(strokes) < 60:
            continue
        _, br_t = _plan(strokes, pol, False)
        _, br_g = _plan(strokes, pol, True)
        lt = max((b.stroke_idx - b.start_stroke_idx + 1 for b in br_t), default=0)
        lg = max((b.stroke_idx - b.start_stroke_idx + 1 for b in br_g), default=0)
        T_tot += len(br_t)
        G_tot += len(br_g)
        T_long += lt > 100
        G_long += lg > 100
        if len(br_g) > len(br_t):
            better += 1
        elif len(br_g) < len(br_t):
            worse += 1
        else:
            same += 1
        if code in INDEX_CODES or code in FIXTURES or lt > 100 or lg > 100:
            rows.append((code, len(br_t), lt, len(br_g), lg))

    print(f"\n=== 样本 {len(codes)} 只 ===")
    for code, nt, lt, ng, lg in rows:
        flag = "  <<<" if (lg < lt or ng > nt) else ""
        print(f"  {code:12s} T=段{nt:4d}/最长{lt:4d}   G=段{ng:4d}/最长{lg:4d}{flag}")
    print(f"\n  主干(DP 最大化)     总段数={T_tot:6d}  最长段>100笔的只数={T_long}")
    print(f"  向前首个满足        总段数={G_tot:6d}  最长段>100笔的只数={G_long}")
    print(f"  改向前首个满足: 段数增加 {better} / 不变 {same} / 减少 {worse}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
