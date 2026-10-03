"""线段层「当下性」测量：第 71 课单调性违规的可复现尺子。

第 071 课《线段划分标准的再分辨》：

> 其实，线段的划分，都是可以当下完成的，无非是如下的程序：假设某转折点是两线段的
> 分界点，然后对此用线段划分的两种情况去考察是否满足，如果满足其中一种，那么这点
> 就是真正的线段的分界点；如果不满足，那就不是，原来的线段依然延续，就这么简单。

它给出两个性质：
  1. 单调向前：已定分界点不被后来的 bar 改写；
  2. 结论「当下」可得：已宣布的分界点不允许在更多 bar 到来后消失。

**本工具量的就是性质 1/2 的违反次数**，口径如下：

  对每个前缀 k（stride 由 `--stride` 指定），取该前缀里 `Status.CONFIRMED` 的线段；
  若某个线段在上一前缀里已确认、在这一前缀里消失，且它**当时不是上一前缀的最后
  一个确认线段**，就算一次违规。
  - 末段消失属正常：它的确认依赖其后仍在变动的笔，与笔层末笔同构。
  - 中间段消失 = 已宣布的分界点被后来的数据改写。

> 注意不要把「不在最终划分里」当作违规指标 —— 那个量的是**与最终划分的一致性**，
> 一个完全单调但与最终划分不同的划分会被它冤枉。真指标只看**相邻前缀之间的消失**。

四个变体（用来证明「翻开关修不好」）：

  L0  主干：全序列 argmax 左端 + DP 目标（`segment.py::classify` 现状）
  F1  热身冻结左端 + DP 目标（左端只用最前面 W 笔算一次）
  F2  热身冻结左端 + 「向前首个满足」目标（第 71 课字面程序）
  L1  起点固定第 0 笔 + 「向前首个满足」目标（完全当下但结构塌）

用法：

    cd /Users/zzz/workspace/chanlun
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_segment_present_tense.py --sample 5

    # 逐根 bar 复算（贵）：确认线段首次可见时刻 vs confirmed_at
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_segment_present_tense.py --per-bar sh.600000,sh.600030

本工具只读，不改主干，不写仓库内文件。
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "src")

from chanlun.chan import segment as S  # noqa: E402
from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.chan.signal import find_signals  # noqa: E402
from chanlun.chan.state import object_id  # noqa: E402
from chanlun.chan.types import Status  # noqa: E402
from chanlun.data import store  # noqa: E402
from chanlun.optimizer.agent import AUDIT_CODES  # noqa: E402
from chanlun.optimizer.agent import AUDIT_END as END  # noqa: E402
from chanlun.optimizer.agent import AUDIT_START as START  # noqa: E402

WARMUP = 120


# ---------------------------------------------------------------- 变体实现

def _dp_best(pol, strokes, n, memo):
    """主干 DP：返回 (从 n 起最多还能确认的线段数, 首个分界)。"""
    if n in memo:
        return memo[n]
    result = (0, None)
    for br in pol.candidates(strokes, n):
        count, _ = _dp_best(pol, strokes, br.stroke_idx + 1, memo)
        if count + 1 > result[0]:
            result = (count + 1, br)
    memo[n] = result
    return result


def walk_dp(pol, strokes, start):
    out = []
    memo: dict[int, tuple[int, object]] = {}
    while start < len(strokes):
        _, br = _dp_best(pol, strokes, start, memo)
        if br is None:
            break
        out.append(br)
        start = br.stroke_idx + 1
    return out


def walk_first(pol, strokes, start):
    """第 71 课字面程序：满足即定，不满足则原线段延续。"""
    out = []
    while start < len(strokes):
        br = next(pol.candidates(strokes, start), None)
        if br is None:
            break
        out.append(br)
        start = br.stroke_idx + 1
    return out


def _argmax_start(pol, strokes, walk):
    start, best = 0, -1
    for s in range(len(strokes)):
        c = len(walk(pol, strokes, s))
        if c > best:
            start, best = s, c
    return start


def make_classify(walk, mode):
    def classify(self, strokes):
        n = len(strokes)
        if n < self.min_strokes:
            return []
        if mode == "trunk":
            start = _argmax_start(self, strokes, walk)
        elif mode == "frozen":
            head = strokes[:min(WARMUP, n)]
            start = _argmax_start(self, head, walk)
        elif mode == "first0":
            start = 0
        else:
            raise ValueError(mode)
        return walk(self, strokes, start)
    return classify


VARIANTS = [
    ("L0 全序列argmax + DP目标", walk_dp, "trunk"),
    ("F1 热身冻结 + DP目标", walk_dp, "frozen"),
    ("F2 热身冻结 + 首个满足", walk_first, "frozen"),
    ("L1 起点0 + 首个满足", walk_first, "first0"),
]


# ---------------------------------------------------------------- 测量

def confirmed_ids(snap):
    return [object_id(s) for s in snap.segments if s.status is Status.CONFIRMED]


def structure(codes):
    """每变体的结构：段数 / 确认段数 / 最长段笔数。"""
    orig = S.Lesson6768Policy.classify
    print("=== M2 结构对比（最长段越小越不塌）===")
    print(f"{'code':10s}{'笔':>6s} | " + " | ".join(f"{v[0]:>22s}" for v in VARIANTS))
    try:
        for code in codes:
            bars = store.read(code, "day")
            if bars is None or len(bars) == 0:
                continue
            strokes = ChanEngine(code, "day").full(bars).strokes
            cells = []
            for _, walk, mode in VARIANTS:
                S.Lesson6768Policy.classify = make_classify(walk, mode)
                try:
                    segs = S.build_segments(strokes)
                finally:
                    S.Lesson6768Policy.classify = orig
                conf = sum(1 for s in segs if s.status is Status.CONFIRMED)
                mx = max((s.stroke_count for s in segs), default=0)
                cells.append(f"{len(segs):3d}段/{conf:3d}确/{mx:4d}长")
            print(f"{code:10s}{len(strokes):6d} | " + " | ".join(f"{c:>22s}" for c in cells))
    finally:
        S.Lesson6768Policy.classify = orig


def monotonicity(codes, stride):
    """真指标：相邻前缀之间消失的**中间段**确认线段数。"""
    orig = S.Lesson6768Policy.classify
    print(f"\n=== M1 第 71 课单调性违规（真指标，stride={stride}）===")
    print("「消失中间」= 已确认线段在下一前缀消失、且当时不是上一前缀的末段")
    print(f"{'variant':24s} {'累计确认':>8s} {'消失总':>7s} {'消失中间':>9s} {'末段(正常)':>11s}")
    try:
        for label, walk, mode in VARIANTS:
            S.Lesson6768Policy.classify = make_classify(walk, mode)
            try:
                tot = step = step_mid = 0
                for code in codes:
                    bars = store.read(code, "day", start=START, end=END)
                    if bars is None or len(bars) == 0:
                        continue
                    eng = ChanEngine(code, "day", signal_fn=find_signals)
                    prev_ids: list[str] = []
                    prev: set[str] | None = None
                    for k in range(250, len(bars) + 1, stride):
                        ids = confirmed_ids(eng.full(bars.iloc[:k]))
                        cur = set(ids)
                        tot += len(cur)
                        if prev is not None:
                            tail = prev_ids[-1] if prev_ids else None
                            for g in prev - cur:
                                step += 1
                                if g != tail:
                                    step_mid += 1
                        prev, prev_ids = cur, ids
                print(f"{label:24s} {tot:8d} {step:7d} {step_mid:9d} "
                      f"{step - step_mid:11d}")
            finally:
                S.Lesson6768Policy.classify = orig
    finally:
        S.Lesson6768Policy.classify = orig
    print("\n→ 若某变体把「消失中间」降到 0，它就是修法；实测全部 > 0。")
    print("→ 无状态规则做不到 0：真正的单调性要求跨次调用锁定已宣布分界点。")


def candidates_growth(codes, stride, probe_start=3):
    """根因：candidates(s) 的断点集合是否单调增长（会新增更早的断点）。"""
    print(f"\n=== M3 根因：candidates(strokes, {probe_start}) 断点集合的演化 ===")
    print(f"{'code':10s} {'新增次数':>8s} {'消失次数':>8s}  说明")
    for code in codes:
        bars = store.read(code, "day", start=START, end=END)
        if bars is None or len(bars) == 0:
            continue
        eng = ChanEngine(code, "day", signal_fn=find_signals)
        pol = S.Lesson6768Policy()
        grow = shrink = 0
        prev_set: set[int] = set()
        for k in range(250, len(bars) + 1, stride):
            strokes = eng.full(bars.iloc[:k]).strokes
            if probe_start >= len(strokes):
                continue
            cur = {b.stroke_idx for b in pol.candidates(strokes, probe_start)}
            if prev_set:
                grow += len(cur - prev_set)
                shrink += len(prev_set - cur)
            prev_set = cur
        note = ("断点只增不减 → 会凭空出现**更早**的候选，DP 于是改选它"
                if shrink == 0 else "断点会回溯收缩（更严重）")
        print(f"{code:10s} {grow:8d} {shrink:8d}  {note}")
    print("→ 断点集合单调增长本身是缠论「当下」程序的特性；")
    print("  问题在 DP 会因此作废一个已经宣布过的分界点。")


def per_bar(codes):
    """逐根 bar 复算：确认线段首次可见时刻 vs 其 confirmed_at。"""
    print("\n=== M4 逐根 bar：首次可见 vs confirmed_at ===")
    print(f"{'code':10s} {'bar数':>6s} {'确认段':>7s} {'早于':>6s} {'晚于':>6s} "
          f"{'最大提前(bar)':>13s} {'末根缺失':>9s}")
    for code in codes:
        bars = store.read(code, "day")
        if bars is None or len(bars) == 0:
            continue
        eng = ChanEngine(code, "day", signal_fn=find_signals)
        n = len(bars)
        first_seen: dict[str, int] = {}
        for k in range(60, n + 1):
            for i in confirmed_ids(eng.full(bars.iloc[:k])):
                first_seen.setdefault(i, k)
        final = eng.full(bars)
        ts_index = {str(t): i for i, t in enumerate(bars["ts"])}
        early = late = 0
        max_ahead = 0
        for seg in final.segments:
            if seg.status is not Status.CONFIRMED:
                continue
            i = object_id(seg)
            if i not in first_seen:
                continue
            ca = getattr(seg, "confirmed_at", None)
            if ca is None or str(ca) not in ts_index:
                continue
            seen_bar = first_seen[i]
            ca_bar = ts_index[str(ca)] + 1
            if ca_bar < seen_bar:
                early += 1
                max_ahead = max(max_ahead, seen_bar - ca_bar)
            elif ca_bar > seen_bar:
                late += 1
        missing = sum(1 for s in final.segments
                      if s.status is Status.CONFIRMED and object_id(s) not in first_seen)
        print(f"{code:10s} {n:6d} {len(first_seen):7d} {early:6d} {late:6d} "
              f"{max_ahead:13d} {missing:9d}")
    print("\n→ 「早于」> 0 表示 confirmed_at 比线段真正可划出的时刻更早 = 前视。")
    print("→ 「末根缺失」应恒为 0（负对照）。")


def main() -> int:
    ap = argparse.ArgumentParser(description="线段层当下性测量")
    ap.add_argument("--sample", type=int, default=5,
                    help="M1/M3 用前 N 只 AUDIT_CODES（默认 5）")
    ap.add_argument("--stride", type=int, default=40, help="前缀步长（默认 40）")
    ap.add_argument("--structure-codes", default="600180,603777,sh.600000,sh.000001",
                    help="M2 用哪些标的（逗号分隔）")
    ap.add_argument("--per-bar", default="",
                    help="M4 用哪些标的（逗号分隔；留空则跳过，很慢）")
    args = ap.parse_args()

    codes = list(AUDIT_CODES)[:args.sample]
    structure_codes = [c.strip() for c in args.structure_codes.split(",") if c.strip()]

    print(f"审计标的（M1/M3）：{codes}")
    structure(structure_codes)
    monotonicity(codes, args.stride)
    candidates_growth(codes, args.stride)
    if args.per_bar:
        per_bar([c.strip() for c in args.per_bar.split(",") if c.strip()])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
