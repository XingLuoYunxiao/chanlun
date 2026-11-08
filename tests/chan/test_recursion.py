"""级别递归 `f2(an)=an+1`（第 17 / 35 / 63 / 84 / 102 课）。

这个文件钉的是三件事：

1. **f1 没被动过。** 最低级别的中枢仍然完全由线段决定，`build_levels` 的第 0 层
   与 `find_pivots(segments)` 逐字段相等。递归是**加在**它上面，不是替换它。
2. **f2 真的接上了。** 上一级中枢的 `[ZD, ZG]` 必须恰好等于它吃进去的下级前三个
   走势类型区间的交集 —— 这是「中枢由次级别走势类型构出」的唯一可判定含义。
3. **段可以是线段，也可以是走势类型。** `LevelSegment` 协议让 `find_pivots` 一份
   实现服务两级；这是第 84 课「关于第二条的中枢过程规则，是一直没有任何改变的」
   在代码上的落点。

合成序列用**确定性**多尺度正弦 + 固定种子噪声：它是本仓库唯一能离线稳定产出
三级结构（L0→L1→L2）的输入，真实离线夹具（`bars.parquet`，4 只各 1212 根日线）
只够一级 —— 1212 根日线切不出三个互相重叠的走势类型。
"""
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from chanlun.chan.engine import ChanEngine
from chanlun.chan.pivot import Pivot, find_pivots
from chanlun.chan.recursion import (
    DEFAULT_BASE_LEVEL,
    MAX_DEPTH,
    LevelStructure,
    build_levels,
    level_chain,
    validate_levels,
)
from chanlun.chan.trend import Trend, TrendType, classify_trends
from chanlun.chan.types import LevelSegment, Segment, Status

BARS = Path(__file__).parent / "fixtures" / "bars.parquet"


# ---------------------------------------------------------------- 构造工具
def _series(seed: int, n: int = 24000) -> pd.DataFrame:
    """确定性的多尺度序列。振幅按尺度成比例，保证每级都有可重叠的结构。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    base = 100.0 + sum(
        a * np.sin(t / p)
        for a, p in [(40, 1600), (20, 400), (10, 110), (5, 32), (2.5, 10), (1.2, 3.5)]
    )
    base = base + rng.normal(0, 0.25, n).cumsum() * 0.15
    hi = base + np.abs(rng.normal(0, 0.25, n))
    lo = base - np.abs(rng.normal(0, 0.25, n))
    return pd.DataFrame(
        {
            "ts": pd.date_range("2000-01-01", periods=n).astype(str),
            "open": base,
            "high": np.maximum(hi, base),
            "low": np.minimum(lo, base),
            "close": base,
            "volume": 1.0,
            "amount": 1.0,
        }
    )


@pytest.fixture(scope="module")
def synthetic():
    """(bars, segments) —— 只算一次，引擎在这个长度上约 1 秒。"""
    bars = _series(0)
    snap = ChanEngine("synth", "day").full(bars)
    return bars, snap.segments


@pytest.fixture(scope="module")
def levels(synthetic):
    bars, segments = synthetic
    return build_levels(segments, bars=bars, base_level="day")


def _trend(idx, low, high, direction, src_start, src_end, level="day"):
    """只在中枢计算上有意义的走势类型：区间、方向、bar 跨度是真的。"""
    return Trend(
        idx=idx,
        kind={1: TrendType.UP, -1: TrendType.DOWN}.get(direction, TrendType.CONSOLIDATION),
        start_idx=idx,
        end_idx=idx,
        start_ts=f"d{src_start:05d}",
        end_ts=f"d{src_end:05d}",
        level=level,
        pivot_count=1,
        status=Status.CONFIRMED,
        confirmed_at=f"d{src_end:05d}",
        high=high,
        low=low,
        direction=direction,
        src_start=src_start,
        src_end=src_end,
    )


# ---------------------------------------------------------------- 1. 级别链
def test_level_chain_from_default_base():
    assert level_chain(DEFAULT_BASE_LEVEL) == ["5", "30", "day", "week", "month"]


def test_level_chain_from_day():
    assert level_chain("day") == ["day", "week", "month"]


def test_l0_is_exactly_the_engine_pivots():
    """**递归是旧行为的超集** —— `levels[0]` 必须与 `snap.pivots` 逐个相同。

    f2 只**往上加级**；碰最低级别就是错的。这条不变量是「`scan`/`backtest`
    沿用 `snap.pivots` 等价于『用 L0』」这句话的**唯一保证** —— 没有它，
    沿用旧字段就不再等价，而且**没人会知道**（两边看起来都「有结果」）。

    实测全市场 5478 只全部成立：
    `optimizer/tools/measure_recursion.py --invariants --period day`。
    """
    bars = _series(0)
    snap = ChanEngine("synth", "day").full(bars)
    levels = build_levels(snap.segments, bars=bars, base_level="day")
    key = lambda p: (p.idx, round(p.zg, 6), round(p.zd, 6))   # noqa: E731
    assert [key(p) for p in levels[0].pivots] == [key(p) for p in snap.pivots]
    assert len(snap.pivots) > 0, "这条用例要有中枢才有意义"


def test_every_levels_src_span_is_inside_the_bars():
    """各级的 `src_start/src_end` 必须落在 bar 帧内且不倒置。

    `validate_levels` 校验不了这条 —— 它拿不到 `bars`。而越界的 src 跨度会让
    作图与跨快照追踪指到不存在的 bar 上（不抛错，只是画错/追踪错）。
    """
    bars = _series(20261013)
    snap = ChanEngine("synth", "1").full(bars)
    levels = build_levels(snap.segments, bars=bars, base_level="1")
    for lv in levels:
        for obj in list(lv.pivots) + list(lv.trends):
            assert 0 <= obj.src_start <= obj.src_end < len(bars), (
                f"{lv.level}: src 跨度 {obj.src_start}..{obj.src_end} 越界（共 {len(bars)} 根）"
            )


def test_level_chain_from_one_minute():
    """**用户要的「以 1 分钟为基准往上递归」** —— 链必须一路走到底。

    这条曾经是 `["1"]`（`LEVEL_UP` 没有 `"1"`，`next_level` 退化成 `"1+"`，
    链在第一步就 break）。**失败无声**：不抛错，只是 1 分钟基准永远只出 L0。
    对齐关系：1 分钟 → 5 → 30 → 日 → 周 → 月，每档正好是行情的下一档。
    """
    assert level_chain("1") == ["1", "5", "30", "day", "week", "month"]


def test_one_minute_base_actually_recurses():
    """机制验证：1 分钟基准要真的产出多于一级，而不是只有 L0。

    用合成序列（数据源那 1 个交易日的 1 分钟线根本不够划出线段，
    所以这里验的是**机制**、不是数据）。
    """
    bars = _series(20261013)
    segs = ChanEngine("synth", "1").full(bars).segments
    levels = build_levels(segs, bars=bars, base_level="1")
    assert len(levels) >= 2, f"1 分钟基准只出了 {len(levels)} 级"
    assert [lv.level for lv in levels[:3]] == ["1", "5", "30"]
    assert [lv.depth for lv in levels] == list(range(len(levels)))


def test_level_chain_stops_on_unknown_level():
    """未知级别 `next_level` 会退化成 `<level>+`，链必须在这里截断而不是无限长。"""
    assert level_chain("quarter") == ["quarter"]
    assert level_chain("year") == ["year"]


def test_level_chain_respects_max_depth():
    assert level_chain("5", max_depth=3) == ["5", "30", "day"]


# ---------------------------------------------------------------- 2. 协议
def test_segment_and_trend_both_satisfy_level_segment():
    seg = Segment(
        idx=0, direction=1,
        start=_stroke_stub(0), end=_stroke_stub(1),
        high=11.0, low=10.0, start_stroke_idx=0, end_stroke_idx=0, stroke_count=1,
        status=Status.CONFIRMED, confirmed_at="t1",
    )
    assert isinstance(seg, LevelSegment)
    assert isinstance(_trend(0, 10.0, 11.0, 1, 0, 5), LevelSegment)


def test_bare_object_is_not_level_segment():
    class _Nope:
        pass

    assert not isinstance(_Nope(), LevelSegment)


def _stroke_stub(i):
    """满足 `Segment.start`/`end` 的最小占位笔（只为协议检查服务）。"""
    from chanlun.chan.types import Fractal, FractalKind, Stroke

    def _f(ts, price, kind, idx):
        return Fractal(
            kind=kind, midx=idx, ts=ts, high=price, low=price, price=price, src_idx=idx
        )

    lo = _f(f"t{i}", 10.0, FractalKind.BOTTOM, i)
    hi = _f(f"t{i}b", 11.0, FractalKind.TOP, i + 1)
    return Stroke(
        idx=i, direction=1, start=lo, end=hi, high=11.0, low=10.0,
        src_start=i, src_end=i + 1, status=Status.CONFIRMED, confirmed_at=f"t{i}b",
    )


# ---------------------------------------------------------------- 3. f2 核心
def test_parent_pivot_is_intersection_of_three_child_trends():
    """上一级中枢 = 三个连续下级走势类型区间的交集（第 35 课）。"""
    trends = [
        _trend(0, 10.0, 20.0, 1, 0, 9),
        _trend(1, 12.0, 18.0, -1, 10, 19),
        _trend(2, 11.0, 19.0, 1, 20, 29),
        # 第四段整段在交集之上 → 离开段，把中枢封口
        _trend(3, 30.0, 40.0, 1, 30, 39),
    ]
    pivots = find_pivots(trends, "week")
    assert len(pivots) == 1
    p = pivots[0]
    assert p.zd == max(10.0, 12.0, 11.0) == 12.0
    assert p.zg == min(20.0, 18.0, 19.0) == 18.0
    assert (p.start_idx, p.end_idx) == (0, 2)
    assert p.level == "week"


def test_no_overlap_no_parent_pivot():
    """三个连续走势类型区间无交集 → 不成枢。"""
    trends = [
        _trend(0, 10.0, 11.0, 1, 0, 9),
        _trend(1, 12.0, 13.0, -1, 10, 19),
        _trend(2, 14.0, 15.0, 1, 20, 29),
    ]
    assert find_pivots(trends, "week") == []


def test_directionless_trends_do_not_crash_and_use_whole_group():
    """三段全是盘整（`direction=0`）时没有「同向」可言，GG/DD 取整个组。

    这是 D-51 的显式口径：`find_pivots` 原先写死 `direction = trio[0].direction`
    并断言「相邻确认段方向必然交替」，而走势类型不满足（实测相邻同向率 51%，
    见 `optimizer/tools/measure_recursion.py`）。
    """
    trends = [
        _trend(0, 10.0, 30.0, 0, 0, 9),
        _trend(1, 12.0, 28.0, 0, 10, 19),
        _trend(2, 11.0, 25.0, 0, 20, 29),
    ]
    pivots = find_pivots(trends, "week")
    assert len(pivots) == 1
    p = pivots[0]
    assert (p.zd, p.zg) == (12.0, 25.0)
    # 无方向 → 整个组都是「中枢中」的段，但离开段（组内最后一段）仍被排除（D-37）
    assert p.dd == min(10.0, 12.0) == 10.0
    assert p.gg == max(30.0, 28.0) == 30.0


def test_mixed_direction_uses_first_directional_segment():
    trends = [
        _trend(0, 10.0, 30.0, 0, 0, 9),      # 盘整，无方向
        _trend(1, 12.0, 28.0, -1, 10, 19),   # 向下
        _trend(2, 11.0, 25.0, -1, 20, 29),   # 向下
    ]
    pivots = find_pivots(trends, "week")
    assert len(pivots) == 1
    # 方向取到 -1：Z 走势段 = 那两段向下的，组内最后一段被排除 → 只剩 -1 那段
    assert pivots[0].dd == 12.0 and pivots[0].gg == 28.0


# ---------------------------------------------------------------- 4. 逐级上推
def test_build_levels_produces_three_levels(synthetic, levels):
    bars, segments = synthetic
    assert len(segments) > 150, "合成序列太短，测不出多级"
    assert [lv.depth for lv in levels] == [0, 1, 2]
    assert [lv.level for lv in levels] == ["day", "week", "month"]
    # 每级都比上一级粗：中枢数与走势类型数都应收缩
    assert len(levels[0].pivots) > len(levels[1].pivots) > 0
    assert len(levels[1].trends) >= 3, "L1 走势类型不足 3 个，L2 就成了空壳"


def test_build_levels_golden_counts(synthetic, levels):
    """golden：这套确定性序列（种子 0）下的**精确**产出。

    这条不是「不变量」而是「回归钉」：判据、跨度约定或任何一处无意改动都会让它
    变红，从而逼改动者显式确认「数字确实变了」。当前值来自
    `optimizer/tools/measure_recursion.py` 的同款输入。
    """
    bars, segments = synthetic
    assert len(segments) == 214
    assert [(len(lv.pivots), len(lv.trends)) for lv in levels] == [
        (39, 23),   # L0/day：线段直接构成的最低级别
        (5, 3),     # L1/week：由 23 个 day 级走势类型递归而来
        (1, 0),     # L2/month：由 3 个 week 级走势类型递归而来
    ]


def test_build_levels_invariants_hold(synthetic, levels):
    assert validate_levels(levels) == []


def test_build_levels_is_pure(synthetic):
    bars, segments = synthetic
    a = build_levels(segments, bars=bars, base_level="day")
    b = build_levels(segments, bars=bars, base_level="day")
    assert a == b


def test_level0_is_untouched_by_recursion(synthetic, levels):
    """f1 必须原样保留：第 0 层就是 `find_pivots(segments)` + 走势类型分类。"""
    bars, segments = synthetic
    l0 = levels[0]
    assert tuple(find_pivots(segments, "day")) == l0.pivots
    assert tuple(classify_trends(find_pivots(segments, "day"), "day", bars=bars)) == l0.trends


def test_every_parent_pivot_matches_child_intersection(levels):
    """把 `validate_levels` 的那条核心不变量再直接断言一遍，免得它被改软。"""
    for prev, cur in zip(levels, levels[1:]):
        for p in cur.pivots:
            trio = list(prev.trends[p.start_idx : p.end_idx + 1])[:3]
            assert len(trio) == 3
            assert p.zd == pytest.approx(max(t.low for t in trio))
            assert p.zg == pytest.approx(min(t.high for t in trio))


def test_build_levels_stops_when_level_has_no_pivots(synthetic):
    bars, segments = synthetic
    one = build_levels(segments, bars=bars, base_level="day", max_depth=1)
    assert [lv.level for lv in one] == ["day"]


def test_build_levels_handles_empty_segments():
    """空输入仍然给出一个空的 depth=0 —— 「第 0 层一定存在」是刻意的，
    调用方不必为「没有结构」和「没有第 0 层」写两种分支。"""
    levels = build_levels([], base_level="day")
    assert [lv.level for lv in levels] == ["day"]
    assert levels[0].pivots == () and levels[0].trends == ()
    assert validate_levels(levels) == []


# ---------------------------------------------------------------- 5. 校验器可失败
def test_validator_flags_wrong_depth(levels):
    corrupted = [replace(levels[0], depth=3)] + list(levels[1:])
    problems = validate_levels(corrupted)
    assert any("深度" in p for p in problems)


def test_validator_flags_trend_of_wrong_level(levels):
    bad_trend = replace(levels[0].trends[0], level="month")
    corrupted = [replace(levels[0], trends=(bad_trend,) + levels[0].trends[1:])] + list(levels[1:])
    problems = validate_levels(corrupted)
    assert any("level=month" in p for p in problems)


def test_validator_flags_widened_parent_pivot(levels):
    """把上一级中枢区间撑大 → 必须被抓到，否则这条不变量等于没测。"""
    cur = levels[1]
    bad = replace(cur.pivots[0], zd=cur.pivots[0].zd - 1.0)
    corrupted = [levels[0], replace(cur, pivots=(bad,) + cur.pivots[1:])] + list(levels[2:])
    problems = validate_levels(corrupted)
    assert any("不等于下级前三个走势类型的交集" in p for p in problems)


def test_validator_flags_missing_child_trends(levels):
    """下级走势类型被掏空 → 上一级中枢就失去支撑。"""
    cur = levels[1]
    bad = replace(cur.pivots[0], start_idx=0, end_idx=1)
    corrupted = [levels[0], replace(cur, pivots=(bad,))] 
    problems = validate_levels(corrupted)
    assert any("少于 3 个" in p for p in problems)


# ---------------------------------------------------------------- 6. Trend 增量字段
def test_consolidation_has_no_direction_trend_has_one():
    pivots = [
        Pivot(idx=0, zg=12.0, zd=10.0, gg=12.0, dd=10.0, start_idx=0, end_idx=2,
              start_ts="d0", end_ts="d2", level="day", status=Status.CONFIRMED,
              confirmed_at="d3", src_start=0, src_end=5),
        Pivot(idx=1, zg=22.0, zd=20.0, gg=22.0, dd=20.0, start_idx=3, end_idx=5,
              start_ts="d6", end_ts="d8", level="day", status=Status.CONFIRMED,
              confirmed_at="d9", src_start=10, src_end=15),
    ]
    trends = classify_trends(pivots, "day")
    assert [t.kind for t in trends] == [TrendType.UP]
    assert trends[0].direction == 1

    solo = classify_trends(pivots[:1], "day")
    assert solo[0].kind is TrendType.CONSOLIDATION
    assert solo[0].direction == 0


def test_trend_spans_are_contiguous(synthetic):
    """走势类型的 bar 跨度必须**不留洞**地铺满时间轴，上级 `find_pivots`
    的区间计算依赖这个性质。

    注意不是严格要求 `start == prev.end + 1`：相邻中枢可以共享边界那一根 bar
    （一个中枢的 `src_end` 等于下一个的 `src_start` 是常见现象），此时两段
    走势类型共享同一根 bar，是坐标取整的产物，不是洞。
    """
    bars, segments = synthetic
    trends = classify_trends(find_pivots(segments, "day"), "day", bars=bars)
    for prev, nxt in zip(trends, trends[1:]):
        assert nxt.src_start > prev.src_start, f"走势类型 #{nxt.idx} 没有前进"
        assert nxt.src_start <= prev.src_end + 1, (
            f"走势类型 #{nxt.idx} 的起点 {nxt.src_start} 与"
            f" #{prev.idx} 的终点 {prev.src_end} 之间留了洞"
        )
        assert nxt.src_end >= prev.src_end, f"走势类型 #{nxt.idx} 的终点回退"


def test_trend_range_covers_constituent_pivot_amplitude(synthetic):
    """有 bars 时区间取原始 bar 极值，必须**含住**成分中枢的 GG/DD（不会更窄）。"""
    bars, segments = synthetic
    pivots = find_pivots(segments, "day")
    trends = classify_trends(pivots, "day", bars=bars)
    for t in trends:
        members = pivots[t.start_idx : t.end_idx + 1]
        assert t.high >= max(p.gg for p in members) - 1e-9
        assert t.low <= min(p.dd for p in members) + 1e-9


def test_trend_range_without_bars_falls_back_to_pivot_amplitude(synthetic):
    bars, segments = synthetic
    pivots = find_pivots(segments, "day")
    no_bars = classify_trends(pivots, "day")
    with_bars = classify_trends(pivots, "day", bars=bars)
    # 分类结果一致；只有区间字段不同
    assert [t.kind for t in no_bars] == [t.kind for t in with_bars]
    assert [t.start_idx for t in no_bars] == [t.start_idx for t in with_bars]
    for a, b in zip(no_bars, with_bars):
        members = pivots[a.start_idx : a.end_idx + 1]
        assert a.high == max(p.gg for p in members)
        assert a.low == min(p.dd for p in members)
        assert b.high >= a.high - 1e-9 and b.low <= a.low + 1e-9


# ---------------------------------------------------------------- 7. 真实离线夹具
@pytest.fixture(scope="module")
def real_snap():
    df = pd.read_parquet(BARS)
    code = sorted(df["code"].unique())[0]
    bars = df[df["code"] == code].drop(columns=["code"]).reset_index(drop=True)
    return code, bars, ChanEngine(code, "day").full(bars)


def test_real_fixture_level0_matches_engine(real_snap):
    code, bars, snap = real_snap
    levels = build_levels(snap.segments, bars=bars, base_level="day")
    assert levels[0].pivots == snap.pivots
    assert validate_levels(levels) == []


def test_real_fixture_recursion_never_invents_pivots(real_snap):
    """递归只能在有足够下级走势类型时成枢；不足就必须停，不能硬造。"""
    code, bars, snap = real_snap
    levels = build_levels(snap.segments, bars=bars, base_level="day")
    for prev, cur in zip(levels, levels[1:]):
        assert len(prev.trends) >= 3, "下级走势类型不足 3 个却还有上一级"
        assert len(cur.pivots) >= 1


# ---------------------------------------------------------------- 8. 接进引擎
def test_snapshot_carries_recursive_levels(synthetic):
    """引擎的产出必须带级别递归结构，且第 0 层与既有的 `pivots` 是同一份。"""
    bars, _ = synthetic
    snap = ChanEngine("synth", "day").full(bars)
    assert snap.levels, "Snapshot 必须带 levels，否则递归没有接进生产链路"
    assert snap.levels[0].level == "day"
    assert [lv.depth for lv in snap.levels] == list(range(len(snap.levels)))
    assert snap.levels[0].pivots == snap.pivots, (
        "第 0 层就是 find_pivots(segments)，递归不该另算一套"
    )


def test_snapshot_levels_do_not_disturb_existing_fields(synthetic):
    """接入是**纯增量**：既有字段一个都不变。"""
    bars, segments = synthetic
    snap = ChanEngine("synth", "day").full(bars)
    assert snap.pivots == tuple(find_pivots(segments, "day"))
    assert snap.segments == tuple(segments)


def test_clipped_to_does_not_touch_levels(synthetic):
    """`levels` 是**唯一不随窗口裁剪**的字段。

    递归的高级别天然跨越数年（`day` 基准下 `week` 级中枢可能覆盖 8 年），
    拿 200 根的显示窗口去裁必然三级全空 —— 那样它就没法回答「这个窗口里的
    结构处在哪个更高级别里」。可见/全史的区分由 `counts` / `counts_total`
    那一对表达，这里沿用同一个约定。
    """
    bars, _ = synthetic
    snap = ChanEngine("synth", "day").full(bars)
    lo, hi = str(bars["ts"].iloc[-2000]), str(bars["ts"].iloc[-1])
    cut = snap.clipped_to(lo, hi)
    assert cut.levels == snap.levels, "levels 不该被窗口裁掉"
    # 对照：`pivots` 确实被裁了，否则这条测试什么都没测到
    assert len(cut.pivots) <= len(snap.pivots)
    assert snap.levels, "合成序列必须有级别，否则测不到东西"


def test_step_and_full_agree_on_levels(real_snap):
    """`levels` 由**新鲜算出**的 segments 决定，所以 step 与 full 必须逐字段相等。

    它不进 `state.py::_FIELDS`：reconcile 会往 `segments` 里塞 INVALIDATED 留痕，
    拿那份去递归会让 step 与 full 分叉。这与 `divergences` 是同一类派生字段。
    """
    code, bars, _ = real_snap
    eng = ChanEngine(code, "day")
    n = len(bars)
    cur = eng.full(bars.iloc[: n - 20])
    for k in range(n - 19, n + 1):
        cur = eng.step(cur, bars.iloc[:k])
    assert cur.levels == eng.full(bars).levels


def test_as_dict_serializes_levels(synthetic):
    """`as_dict()` 走 `to_jsonable`，嵌套的 dataclass 必须能出来。"""
    bars, _ = synthetic
    payload = ChanEngine("synth", "day").full(bars).as_dict()
    assert "levels" in payload
    assert payload["levels"][0]["level"] == "day"
    assert isinstance(payload["levels"][0]["pivots"], list)
    if payload["levels"][0]["pivots"]:
        assert "zd" in payload["levels"][0]["pivots"][0]
