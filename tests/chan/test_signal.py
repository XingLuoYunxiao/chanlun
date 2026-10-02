"""三类买卖点。合成用例固定结构，真实数据跑不变量。

合成用例的线段一律**首尾相连**（`_zigzag` 用转折点造段，并断言相邻段的端点
价格与时间戳重合）。真实线段就是这样：80 只票 × 624 对相邻确认段实测 0 处
不连续。老版本的合成夹具允许段与段之间留缺口，于是「把回抽段当成离开段」
的错误判据在合成数据上通过、在真实数据上恒不成立（详见
`optimizer/theory/L20-THIRD-POINT-POSITION.md`）。
"""
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from chanlun.chan.pivot import find_pivots
from chanlun.chan.signal import (
    Signal,
    SignalKind,
    find_signals,
    validate_signals,
)
from chanlun.chan.types import Fractal, FractalKind, Segment, Status, Stroke

BARS = Path(__file__).parent / "fixtures" / "bars.parquet"
GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "real_strokes.json").read_text(encoding="utf-8")
)


def _fr(kind, ts, price, src):
    return Fractal(kind=kind, midx=src, ts=ts, high=price, low=price,
                   price=price, src_idx=src)


def _seg(i, direction, low, high, s0, s1, confirmed=True):
    ts0, ts1 = f"d{s0:04d}", f"d{s1:04d}"
    if direction == 1:
        start, end = _fr(FractalKind.BOTTOM, ts0, low, s0), _fr(FractalKind.TOP, ts1, high, s1)
    else:
        start, end = _fr(FractalKind.TOP, ts0, high, s0), _fr(FractalKind.BOTTOM, ts1, low, s1)
    st = Stroke(idx=i, direction=direction, start=start, end=end, high=high, low=low,
                src_start=s0, src_end=s1)
    return Segment(
        idx=i, direction=direction, start=st, end=st, high=high, low=low,
        start_stroke_idx=i, end_stroke_idx=i, stroke_count=3,
        status=Status.CONFIRMED if confirmed else Status.TENTATIVE,
        confirmed_at=ts1 if confirmed else None,
    )


def _zigzag(points, last_tentative=False):
    """按转折点造一串**首尾相连**的线段：第 i 段 = `points[i] → points[i+1]`。

    第 i 段占 bar `9i … 9i+9`，所以第 i+1 段从 `9i+9` 开始 —— 相邻两段共用
    同一个转折 bar，价格与时间戳都重合，与真实线段一致。
    """
    segs = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        assert a != b, "转折点不能相等"
        lo, hi = (a, b) if a < b else (b, a)
        segs.append(_seg(i, 1 if b > a else -1, float(lo), float(hi),
                         9 * i, 9 * i + 9,
                         confirmed=not (last_tentative and i == len(points) - 2)))
    for a, b in zip(segs, segs[1:]):
        assert a.end.end.price == b.start.start.price, "合成线段必须首尾相连（价格）"
        assert a.end.end.ts == b.start.start.ts, "合成线段必须首尾相连（时间）"
    return segs


def _macd(n: int, areas: dict[int, float] | None = None, color: int = 1):
    """构造指定区间 |hist| 之和的假 MACD：每个 src 位置给单位 hist。

    `color` 与 `hist_area` 同义：`+1` 放红柱（向上段的力度）、`-1` 放绿柱
    （向下段的力度）。买卖点比较的是**同向**两段（第 24 课「向上的看红柱子，
    向下看绿柱子」），所以第一类买点的向下离开段必须传 `color=-1`；
    否则两段都量不出面积、背驰恒不成立，测试会「假通过」。
    """
    hist = [0.0] * n
    for pos, total in (areas or {}).items():
        hist[pos] = color * total
    return pd.DataFrame({"dif": [0.0] * n, "dea": [0.0] * n, "hist": hist})


def _positional_problems(segs, pivots, signals) -> list[str]:
    """独立复算第 20 课的位置判据：离开段 = 中枢组最后一段，回试段 = 紧随其后。

    ZG/ZD 从原始线段列表**重新算**（不看 `Pivot.zg`），这样这个校验器不是
    实现的镜像。
    """
    segs, pivots = list(segs), list(pivots)
    problems = []
    for s in signals:
        if s.kind not in (SignalKind.B3, SignalKind.S3):
            continue
        p = pivots[s.pivot_idx]
        if p.end_idx + 1 >= len(segs):
            problems.append(f"{s.kind.value}@{s.ts}: 中枢 {p.idx} 之后没有回试段")
            continue
        group = [x for x in segs[p.start_idx:p.end_idx + 1]
                 if x.status is Status.CONFIRMED]
        zg = min(x.high for x in group)
        zd = max(x.low for x in group)
        leave, back = segs[p.end_idx], segs[p.end_idx + 1]
        if s.kind is SignalKind.B3:
            ok = (leave.direction == 1 and leave.high > zg
                  and back.direction == -1 and back.low > zg)
        else:
            ok = (leave.direction == -1 and leave.low < zd
                  and back.direction == 1 and back.high < zd)
        if not ok:
            problems.append(
                f"{s.kind.value}@{s.ts}: 离开段 {leave.direction}/{leave.low}~{leave.high} "
                f"回试段 {back.direction}/{back.low}~{back.high} 对 [zd={zd}, zg={zg}] 不成立")
    return problems


# ---- 第三类买卖点 ----

# 第 20 课：「一个次级别走势类型向上离开缠中说禅走势中枢，然后以一个次级别
# 走势类型回试，其低点不跌破ZG，则构成第三类买点；……并不是任何回调回抽都是
# 第三类买卖点，必须是第一次」。
#
# 注意下面 seg3 的起点 12 仍在 [12, 20] 里 —— 真实线段首尾相连，把价格带出
# 区间的那一段必然与中枢区间有重叠，于是按「有重叠就并入」被算进中枢组
# （`end_idx = 3`）。**离开段就是这一段**，回试段是紧随其后的 seg4。


def _b3_segments():
    return _zigzag([
        20.0,   # 0 起点
        10.0,   # 1  ↓
        22.0,   # 2  ↑
        12.0,   # 3  ↓ 前三段 → 中枢 [12, 20]
        25.0,   # 4  ↑ 向上离开：终点 25 > ZG 20，并入中枢组（end_idx = 3）
        21.0,   # 5  ↓ 回抽低点 21 > ZG 20，整段在区间之上 → 第三类买点
    ])


def test_third_buy_point_after_leaving_pivot():
    segs = _b3_segments()
    pivots = find_pivots(segs, "day")
    assert [(p.zd, p.zg, p.end_idx) for p in pivots] == [(12.0, 20.0, 3)]
    # 位置口径：离开段是中枢组最后一段（它自己与区间重叠），回试段紧随其后。
    assert pivots[0].end_idx == 3
    assert segs[3].direction == 1 and segs[3].high > pivots[0].zg
    assert segs[4].direction == -1 and segs[4].low > pivots[0].zg
    sigs = find_signals(pd.DataFrame(), segs, pivots, "day", macd_df=_macd(46))
    assert [s.kind for s in sigs] == [SignalKind.B3]
    s = sigs[0]
    assert s.price == 21.0 and s.ts == "d0045"
    assert s.pivot_idx == 0 and s.status is Status.CONFIRMED
    assert s.confirmed_at == "d0045"
    assert s.src_start == 36 and s.src_end == 45
    assert _positional_problems(segs, pivots, sigs) == []


def test_third_sell_point_is_symmetric():
    segs = _zigzag([
        10.0,   # 0 起点
        20.0,   # 1  ↑
        8.0,    # 2  ↓
        18.0,   # 3  ↑ 前三段 → 中枢 [10, 18]
        5.0,    # 4  ↓ 向下离开：终点 5 < ZD 10，并入中枢组（end_idx = 3）
        9.0,    # 5  ↑ 回抽高点 9 < ZD 10 → 第三类卖点
    ])
    pivots = find_pivots(segs, "day")
    assert [(p.zd, p.zg, p.end_idx) for p in pivots] == [(10.0, 18.0, 3)]
    sigs = find_signals(pd.DataFrame(), segs, pivots, "day", macd_df=_macd(46))
    assert [s.kind for s in sigs] == [SignalKind.S3]
    assert sigs[0].price == 9.0 and sigs[0].ts == "d0045"


def test_leaving_segment_is_the_pivot_last_segment_not_the_next_one():
    """把「离开段」取成中枢之后那一段，在首尾相连的真实线段上必然失效。

    中枢之后那一段是**反向回抽段**：它若整段在 ZG 之上，方向必然向下。
    所以「离开段方向向上且低点 > ZG」这条老判据在真实数据上恒不成立 ——
    曾经在 148 只票 / 193 个中枢上产出 0 个信号。
    """
    segs = _b3_segments()
    pivots = find_pivots(segs, "day")
    after = segs[pivots[0].end_idx + 1]
    assert after.direction == -1 and after.low > pivots[0].zg   # 方向与位置相反
    # 老判据（拿 after 当离开段）在这里必然为假：
    assert not (after.direction == 1 and after.low > pivots[0].zg)


def test_third_point_reads_segments_by_input_index():
    """`Pivot.end_idx` 是**入参列表**的下标，含作废段也不能先过滤再取段。

    这里在最前面塞一段 `INVALIDATED`：`find_pivots` 会跳过它（中枢组仍然是
    seg1..seg4），但 `end_idx` 是 4 而不是 3。若 `find_signals` 先把作废段
    滤掉，`segs[end_idx]` 就会取到回试段，第三类买点消失。
    """
    dead = replace(_seg(-1, -1, 5.0, 6.0, 200, 209),
                   status=Status.INVALIDATED, confirmed_at=None)
    segs = [dead] + _b3_segments()
    pivots = find_pivots(segs, "day")
    assert [(p.start_idx, p.end_idx) for p in pivots] == [(1, 4)]
    sigs = find_signals(pd.DataFrame(), segs, pivots, "day", macd_df=_macd(46))
    assert [s.kind for s in sigs] == [SignalKind.B3]
    assert sigs[0].price == 21.0 and sigs[0].ts == "d0045"


def test_no_third_point_when_pullback_reenters_pivot():
    """回抽回到区间里 → 这一段按「有重叠」被中枢延伸吸收，离开还没完成。"""
    segs = _zigzag([
        20.0, 10.0, 22.0, 12.0,   # 前三段 → 中枢 [12, 20]
        25.0,                     # 向上离开，并入中枢组
        18.0,                     # 回抽到 18 < ZG 20 → 又被并入，end_idx = 4
    ])
    pivots = find_pivots(segs, "day")
    assert [(p.zd, p.zg, p.end_idx) for p in pivots] == [(12.0, 20.0, 4)]
    assert find_signals(pd.DataFrame(), segs, pivots, "day",
                        macd_df=_macd(46)) == []


def test_no_third_point_without_pullback_yet():
    segs = _zigzag([20.0, 10.0, 22.0, 12.0, 25.0])
    pivots = find_pivots(segs, "day")
    assert pivots[0].end_idx == 3
    assert find_signals(pd.DataFrame(), segs, pivots, "day",
                        macd_df=_macd(46)) == []


# ---- 第一类买卖点（趋势背驰）----

# 两个依次下降的中枢 A[14, 19]、B[12, 12.8]（B 整段在 A 之下 → 下跌趋势）。
# A 的离开段 = seg4（17 → 9，低点 9 < ZD 14）；B 的离开段 = seg8（10 → 6，
# 低点 6 < ZD 12，创新低 6 < 9）。力度看这两段的 MACD 面积。


def _b1_segments(last_tentative=False):
    """转折点 → 11 段：seg0..seg4 构成中枢 A，seg5..seg8 构成中枢 B。

    线段编号 = 转折点之间那一段：seg4 = 17→9（A 的离开段）、seg8 = 10→6
    （B 的离开段）、seg10 = 8→7.5（第二类买点的回抽）。
    """
    return _zigzag([
        22.0,   # 转折点 0
        13.0,   # 1   seg0 ↓ 22→13
        19.0,   # 2   seg1 ↑ 13→19
        14.0,   # 3   seg2 ↓ 19→14   前三段 → 中枢 A [14, 19]
        17.0,   # 4   seg3 ↑ 14→17   并入 A
        9.0,    # 5   seg4 ↓ 17→9    向下离开 A（低点 9 < ZD 14）→ A 结束
        12.0,   # 6   seg5 ↑ 9→12    高点 12 < ZD 14，整段在 A 之下
        7.0,    # 7   seg6 ↓ 12→7
        10.0,   # 8   seg7 ↑ 7→10    前三段 seg5,6,7 → 中枢 B [9, 10]
        6.0,    # 9   seg8 ↓ 10→6    向下离开 B（低点 6 < ZD 9，创新低 6 < 9）
        8.0,    # 10  seg9 ↑ 6→8     回抽（高点 8 < ZD 9）
        7.5,    # 11  seg10 ↓ 8→7.5  不破 6 → 第二类买点
    ], last_tentative=last_tentative)


def test_first_buy_point_requires_shrinking_macd_area():
    segs = _b1_segments()
    pivots = find_pivots(segs, "day")
    assert [(p.zd, p.zg, p.end_idx) for p in pivots[:2]] == [(14.0, 19.0, 4),
                                                             (9.0, 10.0, 8)]
    # src 36..45 是离开中枢 A 的下跌段，72..81 是离开中枢 B 的下跌段
    sigs = find_signals(pd.DataFrame(), segs, pivots, "day",
                        macd_df=_macd(100, {36: 10.0, 72: 1.0}, color=-1))
    kinds = [s.kind for s in sigs]
    assert SignalKind.B1 in kinds
    b1 = next(s for s in sigs if s.kind is SignalKind.B1)
    assert b1.price == 6.0 and b1.ts == "d0081" and b1.pivot_idx == 1
    assert "趋势背驰" in b1.reason
    assert "1.0000" in b1.reason and "10.0000" in b1.reason


def test_no_first_buy_point_when_area_grows():
    segs = _b1_segments()
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(100, {36: 1.0, 72: 10.0}, color=-1))
    assert SignalKind.B1 not in [s.kind for s in sigs]


def test_no_first_buy_point_without_new_extreme():
    segs = _zigzag([
        22.0, 13.0, 19.0, 14.0,   # 中枢 A [14, 19]
        17.0,                     # 并入 A
        9.0,                      # 向下离开 A（低点 9）
        13.0,                     # 整段在 A 之下 → A 结束
        12.0, 12.8,               # 中枢 B [12, 12.8]（仍在 A 之下 → 下跌趋势）
        10.5,                     # 向下离开 B：低点 10.5 **没有**创新低
        11.5,                     # 回抽
    ])
    pivots = find_pivots(segs, "day")
    assert [(p.zd, p.zg, p.end_idx) for p in pivots] == [(14.0, 19.0, 4),
                                                         (12.0, 12.8, 8)]
    sigs = find_signals(pd.DataFrame(), segs, pivots, "day",
                        macd_df=_macd(100, {36: 10.0, 72: 1.0}, color=-1))
    assert SignalKind.B1 not in [s.kind for s in sigs]


def test_no_first_point_in_a_single_pivot_consolidation():
    """一个中枢只有盘整背驰；本实现按第 24 课只做趋势背驰，故不出信号。"""
    segs = _zigzag([22.0, 13.0, 19.0, 14.0, 17.0, 12.0, 16.0, 13.0])
    pivots = find_pivots(segs, "day")
    assert len(pivots) == 1
    sigs = find_signals(pd.DataFrame(), segs, pivots, "day", macd_df=_macd(70))
    assert SignalKind.B1 not in [s.kind for s in sigs]


def test_first_sell_point_requires_new_high_and_shrinking_area():
    """上升趋势里的第一类卖点：创新高 + MACD 面积衰竭。

    夹具的两个中枢必须满足第 20 课**中心定理二**的上涨条件「后DD〉前GG」——
    比的是**围绕中枢波动的区间 `[DD, GG]`**（只遍历同向 Z 走势段、不含离开段），
    不是中枢区间 `[ZD, ZG]`。中枢 A 的 `[DD, GG] = [10, 20]`、中枢 B 的
    `[DD, GG] = [24, 30]`，`24 > 20` 才构成上涨趋势；两个中枢的**离开段**都是
    向上段（seg4 高点 30、seg8 高点 40），才能比「创新高 + 力度衰竭」。
    """
    segs = _zigzag([
        10.0,   # 0
        20.0,   # 1  ↑ seg0 前三段 seg0~2 → 中枢 A [16, 19]
        16.0,   # 2  ↓ seg1
        19.0,   # 3  ↑ seg2
        15.0,   # 4  ↓ seg3 低点 15 仍与 [16, 19] 重叠 → 并入 A
        30.0,   # 5  ↑ seg4 向上离开 A（高点 30 > ZG 19）
        24.0,   # 6  ↓ seg5 低点 24 > ZG 19 → 整段在 A 之上，A 结束（end_idx = 4）
        27.0,   # 7  ↑ seg6 前三段 seg5~7 → 中枢 B [24, 27]
        23.0,   # 8  ↓ seg7
        40.0,   # 9  ↑ seg8 向上离开 B（高点 40 > ZG 27），创新高 40 > 30
        36.0,   # 10 ↓ seg9 回抽（低点 36 > ZG 27）
    ])
    pivots = find_pivots(segs, "day")
    assert [(p.zd, p.zg, p.gg, p.dd, p.end_idx) for p in pivots[:2]] == [
        (16.0, 19.0, 20.0, 10.0, 4),    # A：Z 走势段 seg0、seg2 → GG 20 / DD 10
        (24.0, 27.0, 30.0, 24.0, 8),    # B：Z 走势段只有 seg5（离开段 seg8 不算）
    ]
    # 中心定理二：「后DD〉前GG等价于上涨及其延续」→ 24 > 20
    sigs = find_signals(pd.DataFrame(), segs, pivots, "day",
                        macd_df=_macd(100, {36: 10.0, 72: 1.0}))
    kinds = [s.kind for s in sigs]
    assert SignalKind.S1 in kinds
    s1 = next(s for s in sigs if s.kind is SignalKind.S1)
    assert s1.price == 40.0 and s1.ts == "d0081"


# ---- 第二类买卖点 ----


def test_second_buy_point_after_first_buy_point():
    segs = _b1_segments()
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(100, {36: 10.0, 72: 1.0}, color=-1))
    b2 = [s for s in sigs if s.kind is SignalKind.B2]
    assert len(b2) == 1
    assert b2[0].price == 7.5 and b2[0].ts == "d0099"
    assert "不破" in b2[0].reason


def test_no_second_buy_point_when_pullback_breaks_the_low():
    segs = _b1_segments()
    segs[10] = _seg(10, -1, 5.5, 8.0, 90, 99)   # 回抽破第一类买点的低点 6
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(100, {36: 10.0, 72: 1.0}, color=-1))
    assert SignalKind.B2 not in [s.kind for s in sigs]


def test_signals_are_sorted_and_renumbered():
    segs = _b1_segments()
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(100, {36: 10.0, 72: 1.0}, color=-1))
    assert [s.idx for s in sigs] == list(range(len(sigs)))
    assert [s.ts for s in sigs] == sorted(s.ts for s in sigs)
    assert validate_signals(sigs) == []


def test_tentative_trigger_segment_yields_tentative_signal():
    """第三类买点的回抽段还在走时，信号必须是未确认的。"""
    segs = _zigzag([20.0, 10.0, 22.0, 12.0, 25.0, 21.0], last_tentative=True)
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(46))
    assert len(sigs) == 1
    assert sigs[0].status is Status.TENTATIVE and sigs[0].confirmed_at is None


def test_kind_helpers_and_names():
    assert SignalKind.B1.is_buy and not SignalKind.S1.is_buy
    assert SignalKind.B1.name_cn == "第一类买点"
    assert SignalKind.S3.name_cn == "第三类卖点"
    sig = Signal(idx=0, kind=SignalKind.B2, ts="d1", price=1.0, level="day")
    assert sig.is_buy and sig.kind == "b2"


# ---- 真实数据 ----


@pytest.mark.parametrize("code", sorted(GOLDEN))
def test_real_signals_satisfy_invariants(code):
    from chanlun.chan.engine import ChanEngine
    from chanlun.chan.signal import find_signals as fs

    df = pd.read_parquet(BARS)
    bars = df[df["code"] == code].drop(columns=["code"]).reset_index(drop=True)
    snap = ChanEngine(code, "day", signal_fn=fs).full(bars)
    assert validate_signals(snap.signals, bars, snap.segments) == []
    for s in snap.signals:
        assert s.ts in set(bars["ts"])
        assert s.price > 0
        assert s.src_start <= s.src_end


def test_real_data_must_produce_signals():
    """真实数据上必须真的出信号 —— 这一条是「判据活着」的硬断言。

    曾经这里写着「故意不断言至少有一个买卖点」，把 0 信号解释成「线段划分
    过粗」。那是错的：4 只样本 2020—2024 日线上有 2 个中枢、相邻确认段首尾
    相连，位置口径下每只都能出 1—2 个三类买卖点。0 信号的真正原因是判据把
    「中枢之后那一段」当成了离开段，而它必然是反向回抽段。所以这里反过来
    断言：**每只票至少 1 个信号**，并且每个三类买卖点都要通过独立复算的位置
    判据（ZG/ZD 从原始线段重算，不看 `Pivot` 缓存值）。
    """
    from chanlun.chan.engine import ChanEngine
    from chanlun.chan.signal import find_signals as fs

    df = pd.read_parquet(BARS)
    total = {}
    for code in sorted(GOLDEN):
        bars = df[df["code"] == code].drop(columns=["code"]).reset_index(drop=True)
        snap = ChanEngine(code, "day", signal_fn=fs).full(bars)
        total[code] = len(snap.signals)
        assert _positional_problems(snap.segments, snap.pivots, snap.signals) == []
        for s in snap.signals:
            if s.confirmed_at is not None:
                assert s.confirmed_at <= snap.as_of
            assert s.status is Status.TENTATIVE or s.confirmed_at is not None
    assert set(total) == set(GOLDEN)
    assert all(n >= 1 for n in total.values()), total
