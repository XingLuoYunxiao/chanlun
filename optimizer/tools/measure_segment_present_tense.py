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

变体（两个根因 × 各自的开/关，用来分解各自的贡献）：

  L0   旧主干：全序列 argmax 左端 + **前视**缺口确认
  F1   热身冻结左端（只用最前面 W 笔取 argmax）+ 前视缺口
  F2   热身冻结左端 + 「向前首个满足」目标 + 前视缺口
  L1   起点固定第 0 笔 + 「向前首个满足」目标 + 前视缺口
  D3   因果左端 + 因果缺口确认 —— **这是已落主干的口径**（D-40）
  D3-  只修缺口（左端仍取全序列 argmax）
  D3+  只修左端（缺口仍前视）

`gap_full` / `gap_causal` 都是**自带**的复刻实现，所以 L0 那一行在 D-40 落地之后
仍然量的是**落地前**的口径 —— 工具不会因为主干被改而偷偷换掉基线。
L0 与 D3 的差就是本次修复的全部效果。

用法：

    cd <本项目根目录>
    # 全 24 只票（复现 D-40 记录里的数字）
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_segment_present_tense.py --sample 24 --stride 40

    # 顺带校验「增量推进」与「逐前缀重算」两种因果实现等价
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_segment_present_tense.py --sample 3 --verify-gap

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


def gap_full(strokes, from_idx, direction):
    """**前视**缺口确认：一次拿全序列的合并特征序列找分型（旧主干口径）。

    这就是 D3 的第一个根因：`_merge_feature` 的最后一个元素可能还没封闭，
    新笔一到就被并进前一个元素，早先成立的分型随之消失。
    """
    sub = S._feature_seq(strokes, from_idx, -direction)
    if len(sub) < 3:
        return None
    std = S._merge_feature(sub, -direction)
    for j in range(1, len(std) - 1):
        if S._is_fractal_at(std, j, -direction):
            return std[j + 1][2]
    return None


def gap_causal(strokes, from_idx, direction):
    """**因果**缺口确认：逐笔推进，某个前缀上一旦成立就定死（第 71 课「当下完成」）。

    增量推进用的是 `_merge_push`。它与「每个前缀都重新 `_merge_feature` 一次」是否
    等价是可测的，不是推理 —— `--verify-gap` 就是那把尺子。
    """
    merge_dir = -direction
    std: list[list[float | int]] = []
    for i in range(from_idx, len(strokes)):
        stroke = strokes[i]
        if stroke.direction != direction:
            continue
        S._merge_push(std, stroke.high, stroke.low, i, merge_dir)
        if len(std) >= 3 and S._is_fractal_at(std, len(std) - 2, merge_dir):
            return int(std[-1][2])
    return None


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
        elif mode == "causal":
            # 最早的前缀 t 上、最小的可行起点 —— 只看 strokes[:t]，不前视。
            start, found = 0, False
            for t in range(self.min_strokes, n + 1):
                head = strokes[:t]
                for s in range(t):
                    if next(iter(self.candidates(head, s)), None) is not None:
                        start, found = s, True
                        break
                if found:
                    break
        else:
            raise ValueError(mode)
        return walk(self, strokes, start)
    return classify


# (标签, 走法, 左端口径, 缺口口径)
VARIANTS = [
    ("L0 旧主干：argmax+前视缺口", walk_dp, "trunk", gap_full),
    ("F1 热身冻结+前视缺口", walk_dp, "frozen", gap_full),
    ("F2 冻结+首个满足+前视缺口", walk_first, "frozen", gap_full),
    ("L1 起点0+首个满足+前视缺口", walk_first, "first0", gap_full),
    ("D3 因果左端+因果缺口（已落主干）", walk_dp, "causal", gap_causal),
    ("D3- 只修缺口（左端仍argmax）", walk_dp, "trunk", gap_causal),
    ("D3+ 只修左端（缺口仍前视）", walk_dp, "causal", gap_full),
]


def _patch(variant):
    """装上变体的 `classify` 与 `_confirm_gap`，返回还原用的 (classify, gap)。"""
    _, walk, mode, gap = variant
    orig_cls = S.Lesson6768Policy.classify
    orig_gap = S.Lesson6768Policy._confirm_gap
    S.Lesson6768Policy.classify = make_classify(walk, mode)
    S.Lesson6768Policy._confirm_gap = staticmethod(gap)
    return orig_cls, orig_gap


def _restore(saved):
    orig_cls, orig_gap = saved
    S.Lesson6768Policy.classify = orig_cls
    # 主干的 `_confirm_gap` 是 @staticmethod；还原时也必须包回去，
    # 否则普通函数会被当成实例方法绑定，调用时多收一个 self。
    S.Lesson6768Policy._confirm_gap = staticmethod(orig_gap)


# ---------------------------------------------------------------- 测量

def confirmed_ids(snap):
    return [object_id(s) for s in snap.segments if s.status is Status.CONFIRMED]


def structure(codes):
    """每变体的结构：段数 / 确认段数 / 最长段笔数。"""
    print("=== M2 结构对比（最长段越小越不塌）===")
    print(f"{'code':10s}{'笔':>6s} | " + " | ".join(f"{v[0]:>26s}" for v in VARIANTS))
    for code in codes:
        bars = store.read(code, "day")
        if bars is None or len(bars) == 0:
            continue
        strokes = ChanEngine(code, "day").full(bars).strokes
        cells = []
        for variant in VARIANTS:
            saved = _patch(variant)
            try:
                segs = S.build_segments(strokes)
            finally:
                _restore(saved)
            conf = sum(1 for s in segs if s.status is Status.CONFIRMED)
            mx = max((s.stroke_count for s in segs), default=0)
            cells.append(f"{len(segs):3d}段/{conf:3d}确/{mx:4d}长")
        print(f"{code:10s}{len(strokes):6d} | " + " | ".join(f"{c:>26s}" for c in cells))


def monotonicity(codes, stride):
    """真指标：相邻前缀之间消失的**中间段**确认线段数。"""
    print(f"\n=== M1 第 71 课单调性违规（真指标，stride={stride}）===")
    print("「消失中间」= 已确认线段在下一前缀消失、且当时不是上一前缀的末段")
    print(f"{'variant':30s} {'累计确认':>8s} {'消失总':>7s} {'消失中间':>9s} {'末段(正常)':>11s}")
    for variant in VARIANTS:
        label = variant[0]
        saved = _patch(variant)
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
            print(f"{label:30s} {tot:8d} {step:7d} {step_mid:9d} "
                  f"{step - step_mid:11d}")
        finally:
            _restore(saved)
    print("\n→ L0 与 D3 两行的差就是本次修复的全部效果（累计确认 / 消失中间）。")
    print("→ D3- 与 D3+ 两行是单根因对照：两条都修才拿到最好的「消失中间」。")
    print("→ 无状态规则做不到 0：末段之外的残留需要跨次调用锁定已宣布分界点。")


def violations(codes, stride):
    """列出 D3（已落主干）残留的「消失中间」逐条明细，便于人工复核。"""
    variant = next(v for v in VARIANTS if v[0].startswith("D3 "))
    print(f"\n=== M6 残留违规逐条（{variant[0]}）===")
    saved = _patch(variant)
    total = 0
    try:
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
                if prev is not None:
                    tail = prev_ids[-1] if prev_ids else None
                    for g in sorted(prev - cur):
                        if g != tail:
                            total += 1
                            print(f"  {code:10s} k={k:5d}  消失的中间段 {g}")
                prev, prev_ids = cur, ids
    finally:
        _restore(saved)
    print(f"→ 共 {total} 条。这些是第 71 课性质 1 的残留违反，需要跨次调用锁定"
          f"已宣布分界点才能清掉。")


def verify_gap(codes):
    """校验 `gap_causal` 的增量推进 == 每个前缀都重新合并的逐前缀重算。"""
    print("\n=== M5 缺口确认：增量推进 vs 逐前缀重算 ===")

    def batch(strokes, from_idx, direction):
        for t in range(from_idx + 3, len(strokes) + 1):
            sub = S._feature_seq(strokes[:t], from_idx, -direction)
            if len(sub) < 3:
                continue
            std = S._merge_feature(sub, -direction)
            for j in range(1, len(std) - 1):
                if S._is_fractal_at(std, j, -direction):
                    return std[j + 1][2]
        return None

    checked = bad = 0
    for code in codes:
        bars = store.read(code, "day", start=START, end=END)
        if bars is None or len(bars) == 0:
            continue
        strokes = ChanEngine(code, "day").full(bars).strokes
        for from_idx in range(len(strokes) - 3):
            for direction in (1, -1):
                checked += 1
                if gap_causal(strokes, from_idx, direction) != batch(
                        strokes, from_idx, direction):
                    bad += 1
    print(f"{checked} 组 (code, from_idx, direction)  checked={checked} "
          f"mismatches={bad}")
    print("→ mismatches 必须为 0，否则 D3 的增量实现与因果语义不一致。")


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
                    help="M1/M3 用前 N 只 AUDIT_CODES（默认 5；0 = 全部 24 只）")
    ap.add_argument("--stride", type=int, default=40, help="前缀步长（默认 40）")
    ap.add_argument("--structure-codes", default="600180,603777,sh.600000,sh.000001",
                    help="M2 用哪些标的（逗号分隔）")
    ap.add_argument("--per-bar", default="",
                    help="M4 用哪些标的（逗号分隔；留空则跳过，很慢）")
    ap.add_argument("--verify-gap", action="store_true",
                    help="M5 校验增量推进 == 逐前缀重算（用 --sample 指定的标的）")
    ap.add_argument("--violations", action="store_true",
                    help="M6 逐条列出 D3 残留的「消失中间」")
    args = ap.parse_args()

    codes = list(AUDIT_CODES) if args.sample <= 0 else list(AUDIT_CODES)[:args.sample]
    structure_codes = [c.strip() for c in args.structure_codes.split(",") if c.strip()]

    print(f"审计标的（M1/M3）：{codes}")
    structure(structure_codes)
    monotonicity(codes, args.stride)
    candidates_growth(codes, args.stride)
    if args.verify_gap:
        verify_gap(codes)
    if args.violations:
        violations(codes, args.stride)
    if args.per_bar:
        per_bar([c.strip() for c in args.per_bar.split(",") if c.strip()])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
