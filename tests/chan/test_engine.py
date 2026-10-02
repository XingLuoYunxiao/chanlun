"""引擎不变量：`full()` 与 `step()`，以及「全量 ≡ 增量」。

用真实日线（`fixtures/bars.parquet`，4 只各 1212 根）而不是合成数据 —— 引擎是
各层的拼装点，合成数据掩盖的恰好是层与层之间的口径不一致。
"""
import dataclasses
import json
from pathlib import Path

import pandas as pd
import pytest

from chanlun.chan.engine import ChanEngine, Snapshot
from chanlun.chan.pivot import validate_pivots
from chanlun.chan.segment import validate_segments
from chanlun.chan.state import backtestable
from chanlun.chan.trend import classify_trends, validate_trends
from chanlun.chan.types import Status

BARS = Path(__file__).parent / "fixtures" / "bars.parquet"
GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "real_strokes.json").read_text(encoding="utf-8")
)
CODES = sorted(GOLDEN)
_ALL = pd.read_parquet(BARS)

#: 逐根 bar 走 step() 的窗口长度（越界会显著拖慢 chan 套件）。
WALK = 90
#: step() 等价性检查的起算长度。
PREFIX = len(_ALL[_ALL["code"] == CODES[0]])


def bars(code: str, upto: int | None = None) -> pd.DataFrame:
    df = _ALL[_ALL["code"] == code].drop(columns=["code"]).reset_index(drop=True)
    return df.iloc[:upto].reset_index(drop=True) if upto else df


def snap_of(code: str) -> Snapshot:
    return ChanEngine(code, "day").full(bars(code))


def _confirmed_by_id(snap: Snapshot) -> dict[str, tuple]:
    """已确认结构的身份指纹：几何 + 确认时间。"""
    from chanlun.chan.state import object_id

    out: dict[str, tuple] = {}
    for name in ("strokes", "segments", "pivots", "signals"):
        for item in getattr(snap, name):
            if item.status is Status.CONFIRMED:
                out[object_id(item)] = (
                    item.confirmed_at,
                    getattr(item, "high", None),
                    getattr(item, "low", None),
                    getattr(item, "src_start", None),
                    getattr(item, "src_end", None),
                )
    return out


# ---- full() ----


@pytest.mark.parametrize("code", CODES)
def test_full_fills_every_stage(code):
    snap = snap_of(code)
    assert snap.code == code and snap.period == "day" and snap.version == 1
    assert snap.as_of == bars(code)["ts"].iloc[-1]
    for name in ("merged", "fractals", "strokes", "segments", "pivots"):
        seq = getattr(snap, name)
        assert isinstance(seq, tuple), f"{name} 必须是 tuple"
        assert len(seq) > 0, f"{name} 不能为空"


@pytest.mark.parametrize("code", CODES)
def test_full_reproduces_golden_pipeline(code):
    """引擎算出的笔必须与 golden 夹具逐笔一致。"""
    snap = snap_of(code)
    entry = GOLDEN[code]
    assert len(snap.strokes) == len(entry["strokes"])
    assert len(bars(code)) == entry["bars"]
    first = entry["expected_first_confirmed"]
    seg = next(s for s in snap.segments if s.status is Status.CONFIRMED)
    assert seg.start_stroke_idx == first["start_stroke_idx"]
    assert seg.end_stroke_idx == first["end_stroke_idx"]
    assert seg.direction == first["direction"]
    assert len([s for s in snap.segments if s.status is Status.CONFIRMED]) == \
        entry["expected_confirmed_count"]
    assert len(snap.segments) == entry["expected_total_segments"]


@pytest.mark.parametrize("code", CODES)
def test_full_structures_pass_their_validators(code):
    snap = snap_of(code)
    assert validate_segments(snap.segments, snap.strokes) == []
    assert validate_pivots(snap.pivots) == []
    assert validate_trends(classify_trends(snap.pivots, snap.level)) == []


@pytest.mark.parametrize("code", CODES)
def test_full_is_deterministic_and_frozen(code):
    a, b = snap_of(code), snap_of(code)
    assert a == b
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.version = 2  # type: ignore[misc]


@pytest.mark.parametrize("code", CODES)
def test_confirmed_objects_are_point_in_time(code):
    """确认时间必须落在 [自身终点, 快照终点] 内，且不能倒退。"""
    snap = snap_of(code)
    for name in ("strokes", "segments", "pivots"):
        seen = ""
        for item in getattr(snap, name):
            if item.status is not Status.CONFIRMED:
                continue
            assert item.confirmed_at is not None, f"{name} 已确认却没有确认时间"
            assert item.confirmed_at <= snap.as_of, f"{name} 确认时间在未来"
            assert item.confirmed_at >= seen, f"{name} 确认时间倒退"
            seen = item.confirmed_at


@pytest.mark.parametrize("code", CODES)
def test_full_pivots_confirmed_only_when_closed(code):
    snap = snap_of(code)
    # end_idx 是**入参线段列表**的下标（见 pivot.py），不能用确认段的数量去比。
    last_confirmed_pos = max(
        i for i, s in enumerate(snap.segments) if s.status is Status.CONFIRMED
    )
    for p in snap.pivots:
        if p.end_idx == last_confirmed_pos:
            assert p.status is Status.TENTATIVE
            assert p.confirmed_at is None
        else:
            assert p.status is Status.CONFIRMED
            assert p.confirmed_at is not None


def test_full_rejects_empty_frame():
    with pytest.raises(ValueError):
        ChanEngine("sh.600000", "day").full(pd.DataFrame())


# ---- step() ----


def test_step_without_prev_equals_full():
    eng = ChanEngine("sh.600000", "day")
    df = bars("sh.600000", 600)
    assert eng.step(None, df) == eng.full(df)


@pytest.mark.parametrize("code", [CODES[0], CODES[3]])
def test_step_equals_full_on_structure(code):
    eng = ChanEngine(code, "day")
    df = bars(code)
    prev = eng.full(df.iloc[: PREFIX - WALK])
    nxt = eng.step(prev, df.iloc[: PREFIX - WALK + 1])
    fresh = eng.full(df.iloc[: PREFIX - WALK + 1])
    assert _strip(nxt) == _strip(fresh)
    assert nxt.version == prev.version + 1


def _strip(snap: Snapshot) -> Snapshot:
    """去掉版本号、INVALIDATED 留痕与确认时间，只比结构与状态。

    `confirmed_at` 单独比（见 `test_step_stamps_are_never_later_than_full`）：
    step 保留的是**首次观测到**的确认时间，而 full 用的是当前重算值 —— 处于
    末端的确认分型可能被更极端的后续分型顶替，于是 full 的值只会往后挪，
    step 的旧值必然 `<=` 它。两者都是合法的 point-in-time 值，不该要求相等。
    """
    keep = {}
    for name in ("strokes", "segments", "pivots", "signals"):
        keep[name] = tuple(
            dataclasses.replace(x, confirmed_at=None)
            for x in getattr(snap, name) if x.status is not Status.INVALIDATED
        )
    return dataclasses.replace(snap, version=0, **keep)


def _stamps(snap: Snapshot) -> dict[str, str | None]:
    from chanlun.chan.state import object_id

    return {
        object_id(x): x.confirmed_at
        for name in ("strokes", "segments", "pivots", "signals")
        for x in getattr(snap, name)
        if x.status is not Status.INVALIDATED
    }


@pytest.mark.parametrize("code", [CODES[0]])
def test_incremental_walk_is_equivalent_to_full(code):
    """逐根 bar 走：每步的结构都必须与从零重算完全一致（核心不变量）。"""
    eng = ChanEngine(code, "day")
    df = bars(code)
    start = PREFIX - WALK
    cur = eng.full(df.iloc[:start])
    for k in range(start + 1, PREFIX + 1):
        cur = eng.step(cur, df.iloc[:k])
        fresh = eng.full(df.iloc[:k])
        assert _strip(cur) == _strip(fresh), f"第 {k} 根开始偏离全量"
        mine, theirs = _stamps(cur), _stamps(fresh)
        for oid, stamp in mine.items():
            # step 里的确认时间只能早于或等于全量重算值（永不倒退成未来值）
            other = theirs.get(oid)
            if stamp is not None and other is not None:
                assert stamp <= other, f"{oid} 的确认时间在 step 里反而更晚"
        assert cur.version == k - start + 1


@pytest.mark.parametrize("code", [CODES[0]])
def test_incremental_walk_never_revises_confirmed_structure(code):
    """已确认的结构在后续 bar 里不得被改写：这是 point-in-time 的根。"""
    eng = ChanEngine(code, "day")
    df = bars(code)
    start = PREFIX - WALK
    cur = eng.full(df.iloc[:start])
    known = _confirmed_by_id(cur)
    invalidated = 0
    for k in range(start + 1, PREFIX + 1):
        cur = eng.step(cur, df.iloc[:k])
        now = _confirmed_by_id(cur)
        for oid, fingerprint in known.items():
            assert oid in now, f"已确认结构 {oid} 在第 {k} 根消失了"
            assert now[oid] == fingerprint, f"已确认结构 {oid} 在第 {k} 根被改写"
        invalidated += sum(1 for x in cur.strokes + cur.segments + cur.pivots
                           if x.status is Status.INVALIDATED)
        known = now
    assert cur.as_of == df["ts"].iloc[-1]
    # 被推翻的旧结构必须留痕，否则「当时它长什么样」就查不回来了。
    assert invalidated > 0


@pytest.mark.parametrize("code", [CODES[0]])
def test_step_keeps_confirmed_at_immutable(code):
    from chanlun.chan.state import object_id

    eng = ChanEngine(code, "day")
    df = bars(code)
    prev = eng.full(df.iloc[: PREFIX - WALK])
    # 必须按身份（src 区间）比对：列表下标会随新对象插入而整体错位。
    stamps = {object_id(x): x.confirmed_at for x in prev.segments if x.confirmed_at}
    cur = prev
    for k in range(PREFIX - WALK + 1, PREFIX + 1):
        cur = eng.step(cur, df.iloc[:k])
        for seg in cur.segments:
            oid = object_id(seg)
            if oid in stamps and seg.confirmed_at != stamps[oid]:
                assert False, f"线段 {oid} 的确认时间被改写"


@pytest.mark.parametrize("code", [CODES[0]])
def test_step_is_idempotent_without_a_new_bar(code):
    eng = ChanEngine(code, "day")
    df = bars(code)
    prev = eng.full(df)
    again = eng.step(prev, df)
    assert _strip(again) == _strip(prev)
    assert again.version == prev.version + 1


def test_step_rejects_shrinking_frame():
    eng = ChanEngine(CODES[0], "day")
    df = bars(CODES[0], 800)
    prev = eng.full(df)
    with pytest.raises(ValueError, match="早于上一快照"):
        eng.step(prev, df.iloc[:700])


def test_step_rejects_empty_frame():
    eng = ChanEngine(CODES[0], "day")
    prev = eng.full(bars(CODES[0], 400))
    with pytest.raises(ValueError):
        eng.step(prev, pd.DataFrame())


# ---- 与状态机的接合 ----


def test_backtestable_excludes_tentative_and_future():
    snap = snap_of(CODES[0])
    segs = backtestable(snap.segments, snap.as_of)
    assert segs, "真实数据应当有可回测的确认线段"
    assert len(segs) <= len(snap.segments)
    assert all(s.status is Status.CONFIRMED and s.confirmed_at <= snap.as_of for s in segs)
    # 尾段永远未确认，必须被挡在回测之外
    assert snap.segments[-1].status is Status.TENTATIVE
    assert snap.segments[-1] not in segs


def test_pivots_are_backtestable_on_real_data():
    """中枢也要能进 point-in-time 回测，否则买卖点没得算。"""
    snap = snap_of(CODES[0])
    got = backtestable(snap.pivots, snap.as_of)
    assert got, "真实数据应当有已确认中枢"
    assert all(p.confirmed_at is not None for p in got)


def test_signal_hook_is_plugged_in_and_reconciled():
    from chanlun.chan.signal import Signal

    def fake(bars_df, segments, pivots, level):
        return [
            Signal(idx=0, kind="b3", ts=bars_df["ts"].iloc[-1], price=1.0,
                   level=level, pivot_idx=0, reason="测试")
        ] if pivots else []

    eng = ChanEngine(CODES[0], "day", signal_fn=fake)
    snap = eng.full(bars(CODES[0], 900))
    assert len(snap.signals) == 1
    assert snap.signals[0].level == "day"
    assert snap.as_dict()["signals"][0]["kind"] == "b3"


# ------------------------------------------------ 脏数据不得改变线段划分
#: 注入全 0 K 线的位置。这一段是实测里塌陷最重的地方：
#: 修复前 sh.600000 由 11 段/最长 15 笔变成 7 段/最长 41 笔。
ZERO_AT = 475


def test_zero_price_bars_do_not_change_the_segmentation():
    """全 0 K 线不是行情，喂进引擎必须被中和掉，划分结果与干净数据一致。

    回归（2026-10-01 用户报告「深证成指日线只剩 1 个向下线段，调到 4000 根
    还是只有 1 个」）：真实数据里 0 价行来自停市占位或数据源缺值。
    `normalize` 原先把无法解析的价格 `fillna(0.0)`，于是 0.0 成了全序列最低点，
    被分型/笔当成真实极值——sz.399001 日线因此从 50 段塌成 4 段（其中 1 个
    向下线段横跨 1994-09 ~ 2026-09、592 笔），sh.600759 的 5 分钟线从
    168 段/28 中枢塌成 126 段/20 中枢。

    `ChanEngine.full()` 入口即 `normalize`，所以本用例走的是完整生产链路。
    这里断言的是「脏数据不改变结果」这条不变量本身，而不是某个段数常量。
    """
    clean = bars("sh.600000")
    snap_clean = ChanEngine("sh.600000", "day").full(clean)
    # 前提校验：干净数据本身得分得开，否则下面比的是两个「都塌了」的结果。
    assert len(snap_clean.segments) == 11, "基准已变，本用例的注入位置需要重新标定"

    dirty = clean.copy()
    dirty.loc[ZERO_AT : ZERO_AT + 4, ["open", "high", "low", "close"]] = 0.0
    snap_dirty = ChanEngine("sh.600000", "day").full(dirty)

    assert len(snap_dirty.strokes) == len(snap_clean.strokes)
    assert len(snap_dirty.segments) == len(snap_clean.segments)
    assert [s.start_stroke_idx for s in snap_dirty.segments] == [
        s.start_stroke_idx for s in snap_clean.segments
    ]
    assert [s.end_stroke_idx for s in snap_dirty.segments] == [
        s.end_stroke_idx for s in snap_clean.segments
    ]
    assert [s.high for s in snap_dirty.segments] == [s.high for s in snap_clean.segments]
    assert [s.low for s in snap_dirty.segments] == [s.low for s in snap_clean.segments]


def test_zero_price_bars_are_really_zeroed_in_the_source():
    """前提校验：本用例注入的确实是全 0 行，否则上面那条测试是空的。"""
    df = bars("sh.600000")
    assert (df.loc[ZERO_AT : ZERO_AT + 4, ["open", "high", "low", "close"]] > 0).all().all()


# ------------------------------------------------ 背驰：派生数据，不进状态机
#: 背驰夹具用的票。**实测四只夹具票的背驰全是盘整背驰，趋势背驰一只都没有**：
#: sh.600000=5 / sh.601088=5 / sz.300059=2 / sz.300750=7，逐只都是
#: `{'consolidation': n}`（trend=0）。这不是夹具选取失误，是这四只 2020-2024
#: 日线上确实没走出「两个同向中枢 + 离开段力度收缩」的形态。取背驰最多的
#: sz.300750；窗口 `[ts[600], ts[-1]]` 内剩 4 个，够两条用例用。
DIVERGENCE_CODE = "sz.300750"


def test_snapshot_carries_divergences_when_fn_given():
    from chanlun.chan.divergence import find_divergences
    from chanlun.chan.signal import find_signals

    df = bars(DIVERGENCE_CODE)
    plain = ChanEngine(DIVERGENCE_CODE, "day", signal_fn=find_signals,
                       level="day").full(df)
    rich = ChanEngine(DIVERGENCE_CODE, "day", signal_fn=find_signals, level="day",
                      divergence_fn=find_divergences).full(df)
    assert plain.divergences == (), "没给 divergence_fn 时不应凭空产出背驰"
    assert rich.divergences, "给了 divergence_fn 就必须有背驰"
    assert plain.signals == rich.signals, "背驰是派生数据，不得影响买卖点"


def test_clipped_to_keeps_only_visible_divergences():
    from chanlun.chan.divergence import find_divergences
    from chanlun.chan.signal import find_signals

    df = bars(DIVERGENCE_CODE)
    snap = ChanEngine(DIVERGENCE_CODE, "day", signal_fn=find_signals, level="day",
                      divergence_fn=find_divergences).full(df)
    lo, hi = str(df["ts"].iloc[600]), str(df["ts"].iloc[-1])
    cut = snap.clipped_to(lo, hi)
    assert cut.divergences, "窗口内应有背驰"
    assert all(lo <= d.ts <= hi for d in cut.divergences)
    assert len(cut.divergences) <= len(snap.divergences)
