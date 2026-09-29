"""三类买卖点。合成用例固定结构，真实数据跑不变量。"""
import json
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


def _chain(spec, last_tentative=False):
    """spec: [(direction, low, high), ...]，每段占 10 根原始 bar。"""
    out = []
    for i, (d, lo, hi) in enumerate(spec):
        out.append(_seg(i, d, lo, hi, i * 10, i * 10 + 9,
                        confirmed=not (last_tentative and i == len(spec) - 1)))
    return out


def _macd(n: int, areas: dict[int, float] | None = None):
    """构造指定区间 |hist| 之和的假 MACD：每个 src 位置给单位 hist。"""
    hist = [0.0] * n
    for pos, total in (areas or {}).items():
        hist[pos] = total
    return pd.DataFrame({"dif": [0.0] * n, "dea": [0.0] * n, "hist": hist})


# ---- 第三类买卖点 ----


def _b3_segments():
    return _chain([
        (-1, 10.0, 20.0),   # 0 进中枢
        (1, 12.0, 22.0),    # 1
        (-1, 11.0, 18.0),   # 2 → 中枢 [12, 18]
        (1, 19.0, 25.0),    # 3 向上离开（低点 19 > ZG 18）
        (-1, 19.5, 24.0),   # 4 回抽不回中枢 → 第三类买点
    ])


def test_third_buy_point_after_leaving_pivot():
    segs = _b3_segments()
    pivots = find_pivots(segs, "day")
    assert [(p.zd, p.zg, p.end_idx) for p in pivots] == [(12.0, 18.0, 2)]
    sigs = find_signals(pd.DataFrame(), segs, pivots, "day", macd_df=_macd(60))
    assert [s.kind for s in sigs] == [SignalKind.B3]
    s = sigs[0]
    assert s.price == 19.5 and s.ts == "d0049"
    assert s.pivot_idx == 0 and s.status is Status.CONFIRMED
    assert s.confirmed_at == "d0049"
    assert s.src_start == 40 and s.src_end == 49


def test_third_sell_point_is_symmetric():
    segs = _chain([
        (1, 10.0, 20.0),
        (-1, 12.0, 22.0),
        (1, 13.0, 19.0),     # 中枢 [13, 19]
        (-1, 8.0, 12.0),     # 向下离开
        (1, 9.0, 12.5),      # 回抽不回中枢 → 第三类卖点
    ])
    pivots = find_pivots(segs, "day")
    sigs = find_signals(pd.DataFrame(), segs, pivots, "day", macd_df=_macd(60))
    assert [s.kind for s in sigs] == [SignalKind.S3]
    assert sigs[0].price == 12.5


def test_no_third_point_when_pullback_reenters_pivot():
    segs = _chain([
        (-1, 10.0, 20.0),
        (1, 12.0, 22.0),
        (-1, 11.0, 18.0),
        (1, 19.0, 25.0),
        (-1, 15.0, 24.0),    # 回抽到 15 < ZG 18 → 又回到中枢，不成第三类买点
    ])
    assert find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(60)) == []


def test_no_third_point_without_pullback_yet():
    segs = _chain([
        (-1, 10.0, 20.0), (1, 12.0, 22.0), (-1, 11.0, 18.0), (1, 19.0, 25.0),
    ])
    assert find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(60)) == []


# ---- 第一类买卖点（趋势背驰）----


def _b1_segments(last_tentative=False):
    """两个依次下降的中枢；离开第二个中枢的下跌创新低且力度衰竭。"""
    return _chain([
        (-1, 16.0, 20.0),   # 0 ┐ 中枢 A [16, 17]
        (1, 13.0, 18.0),    # 1 │
        (-1, 12.0, 17.0),   # 2 ┘
        (-1, 10.0, 15.0),   # 3 向下离开 A（高点 15 < 16）
        (1, 11.0, 14.0),    # 4 ┐ 中枢 B [11, 13]
        (-1, 9.0, 13.0),    # 5 ┘
        (-1, 7.0, 10.0),    # 6 向下离开 B，创新低 7 < 10，面积衰竭
        (1, 8.0, 12.0),     # 7 反弹
        (-1, 7.5, 11.0),    # 8 回抽不破 7 → 第二类买点
    ], last_tentative=last_tentative)


def test_first_buy_point_requires_shrinking_macd_area():
    segs = _b1_segments()
    pivots = find_pivots(segs, "day")
    assert [p.end_idx for p in pivots[:2]] == [2, 5]
    # src 30..39 是离开中枢 A 的下跌段，60..69 是离开中枢 B 的下跌段
    sigs = find_signals(pd.DataFrame(), segs, pivots, "day",
                        macd_df=_macd(90, {30: 10.0, 60: 1.0}))
    kinds = [s.kind for s in sigs]
    assert SignalKind.B1 in kinds
    b1 = next(s for s in sigs if s.kind is SignalKind.B1)
    assert b1.price == 7.0 and b1.ts == "d0069" and b1.pivot_idx == 1
    assert "趋势背驰" in b1.reason
    assert "1.0000" in b1.reason and "10.0000" in b1.reason


def test_no_first_buy_point_when_area_grows():
    segs = _b1_segments()
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(90, {30: 1.0, 60: 10.0}))
    assert SignalKind.B1 not in [s.kind for s in sigs]


def test_no_first_buy_point_without_new_extreme():
    segs = _chain([
        (-1, 16.0, 20.0), (1, 13.0, 18.0), (-1, 12.0, 17.0),
        (-1, 11.0, 15.0),                      # 离开 A 的低点 11
        (1, 12.0, 14.0), (-1, 12.5, 13.0),     # 中枢 B [12.5, 13]：底抬高
        (-1, 11.0, 12.0),                      # 离开 B 但低点 11 未创新低
    ])
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(90, {30: 10.0, 60: 1.0}))
    assert SignalKind.B1 not in [s.kind for s in sigs]


def test_no_first_point_in_a_single_pivot_consolidation():
    """一个中枢只有盘整背驰；本实现按第 24 课只做趋势背驰，故不出信号。"""
    segs = _chain([
        (-1, 16.0, 20.0), (1, 13.0, 18.0), (-1, 12.0, 17.0),
        (-1, 7.0, 15.0), (1, 8.0, 14.0),
    ])
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(90, {30: 10.0, 60: 1.0}))
    assert SignalKind.B1 not in [s.kind for s in sigs]


def test_first_sell_point_requires_new_high_and_shrinking_area():
    segs = _chain([
        (1, 10.0, 14.0),     # 0 ┐ 中枢 A [13, 14]
        (-1, 12.0, 17.0),    # 1 │
        (1, 13.0, 18.0),     # 2 ┘   （zg=min(14,17,18)=14, zd=max(10,12,13)=13）
        (1, 19.0, 24.0),     # 3 向上离开 A
        (-1, 18.0, 21.0),    # 4 ┐ 中枢 B [19, 20]
        (1, 19.0, 23.0),     # 5 ┘
        (1, 25.0, 28.0),     # 6 向上离开 B，创新高且面积衰竭
    ])
    pivots = find_pivots(segs, "day")
    sigs = find_signals(pd.DataFrame(), segs, pivots, "day",
                        macd_df=_macd(80, {30: 10.0, 60: 1.0}))
    kinds = [s.kind for s in sigs]
    assert SignalKind.S1 in kinds
    s1 = next(s for s in sigs if s.kind is SignalKind.S1)
    assert s1.price == 28.0 and s1.ts == "d0069"


# ---- 第二类买卖点 ----


def test_second_buy_point_after_first_buy_point():
    segs = _b1_segments()
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(90, {30: 10.0, 60: 1.0}))
    b2 = [s for s in sigs if s.kind is SignalKind.B2]
    assert len(b2) == 1
    assert b2[0].price == 7.5 and b2[0].ts == "d0089"
    assert "不破" in b2[0].reason


def test_no_second_buy_point_when_pullback_breaks_the_low():
    segs = _b1_segments()
    segs[8] = _seg(8, -1, 6.0, 11.0, 80, 89)   # 回抽破前低 7
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(90, {30: 10.0, 60: 1.0}))
    assert SignalKind.B2 not in [s.kind for s in sigs]


def test_signals_are_sorted_and_renumbered():
    segs = _b1_segments()
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(90, {30: 10.0, 60: 1.0}))
    assert [s.idx for s in sigs] == list(range(len(sigs)))
    assert [s.ts for s in sigs] == sorted(s.ts for s in sigs)
    assert validate_signals(sigs) == []


def test_tentative_trigger_segment_yields_tentative_signal():
    """第三类买点的回抽段还在走时，信号必须是未确认的。"""
    segs = _chain([
        (-1, 10.0, 20.0), (1, 12.0, 22.0), (-1, 11.0, 18.0),
        (1, 19.0, 25.0), (-1, 19.5, 24.0),
    ], last_tentative=True)
    sigs = find_signals(pd.DataFrame(), segs, find_pivots(segs, "day"), "day",
                        macd_df=_macd(60))
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


def test_real_signals_are_point_in_time():
    """真实数据上买卖点必须是 point-in-time 的。

    这里**故意不断言「至少有一个买卖点」**。实测（4 只样本 2020—2024 日线）：
    每只只切出 9—12 段、2 个中枢，单段最长 43 笔、跨 500 多根 bar；三类买卖点
    要求「离开中枢 → 回抽不回中枢」的相邻三段组合，在这种粒度下确实凑不出来，
    于是信号数为 0。零信号是**线段划分过粗**的症状（已交给 Task 17 优化师，
    判据是第 67/78 课的特征序列划分），不是买卖点规则的错误 —— 规则本身在合成
    数据上有正反例覆盖（见上面的合成用例）。把它写成硬断言只会逼着以后的人去
    编造信号。
    """
    from chanlun.chan.engine import ChanEngine
    from chanlun.chan.signal import find_signals as fs

    df = pd.read_parquet(BARS)
    total = {}
    for code in sorted(GOLDEN):
        bars = df[df["code"] == code].drop(columns=["code"]).reset_index(drop=True)
        snap = ChanEngine(code, "day", signal_fn=fs).full(bars)
        total[code] = len(snap.signals)
        for s in snap.signals:
            if s.confirmed_at is not None:
                assert s.confirmed_at <= snap.as_of
            assert s.status is Status.TENTATIVE or s.confirmed_at is not None
    assert set(total) == set(GOLDEN)
