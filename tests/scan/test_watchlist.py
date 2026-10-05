"""自选池跟踪测试（Task 15）。

`track_watchlist` 的验收点不是「算出结构」，而是「**说清楚相对上一次快照变了什么**」：
新中枢、中枢突破、买卖点触发、背驰出现、以及「什么都没变」也要显式给一行。
所以每个用例都固定「上一次快照 → 这一次快照」的一个差异，并断言中文说明里
带上了可核对的具体数字。
"""

from __future__ import annotations

import pytest

from chanlun.data import meta
from chanlun.scan.watchlist import (
    TrackChangeKind,
    TrackRow,
    track_watchlist,
)

from scankit import chain, macd_frame, pivot_of, signal_of, snapshot_of, synth_bars


def _segs():
    return chain([(-1, 10.0, 20.0), (1, 12.0, 22.0), (-1, 9.0, 18.0)])


def _snap_plain(code, period, bars):
    """基线：1 个中枢、无买卖点；MACD 前后同向面积相等 → 无背驰。"""
    segs = _segs()
    return snapshot_of(code, period, bars, segs=segs,
                       pivots=[pivot_of(segs, 12.0, 18.0, 0, 2)])


def _snap_new_pivot(code, period, bars):
    segs = _segs()
    return snapshot_of(code, period, bars, segs=segs,
                       pivots=[pivot_of(segs, 12.0, 18.0, 0, 2),
                               pivot_of(segs, 20.0, 26.0, 1, 2)])


def _snap_with_signal(code, period, bars):
    segs = _segs()
    return snapshot_of(code, period, bars, segs=segs,
                       pivots=[pivot_of(segs, 12.0, 18.0, 0, 2)],
                       signals=[signal_of(segs[2], "b1", pivot_idx=0)])


# 比较的两段都是向下段（`_segs` 里 direction=-1）→ 按第 24 课只算绿柱，hist 取负。
EQUAL_AREAS = lambda close: macd_frame(len(close), areas={5: -4.0, 25: -4.0})  # noqa: E731
SHRINKING_AREAS = lambda close: macd_frame(len(close), areas={5: -10.0, 25: -4.0})  # noqa: E731


def _run(env, source, bars, *, macd_fn=EQUAL_AREAS):
    env.write("600000", "day", bars)
    env.sync("600000", "day", bars)
    return track_watchlist(["600000"], conn=env.conn, snapshot_source=source,
                           macd_fn=macd_fn, save=True)


def test_track_first_snapshot_explains_baseline(env):
    bars = synth_bars(300, day_step=2, close=12.0)
    rows = _run(env, _snap_plain, bars)
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, TrackRow)
    assert row.status == "ok" and row.error == ""
    assert [c.kind for c in row.changes] == [TrackChangeKind.FIRST_SNAPSHOT]
    assert "首次快照" in row.changes[0].detail
    assert "中枢 1 个" in row.changes[0].detail
    assert row.prev_as_of is None
    assert "600000" in row.summary()


def test_track_detects_new_pivot_with_previous_comparison(env):
    bars = synth_bars(300, day_step=2, close=12.0)
    _run(env, _snap_plain, bars)
    rows = _run(env, _snap_new_pivot, bars)
    kinds = [c.kind for c in rows[0].changes]
    assert TrackChangeKind.NEW_PIVOT in kinds
    new = next(c for c in rows[0].changes if c.kind is TrackChangeKind.NEW_PIVOT)
    assert "新中枢" in new.detail
    assert "20.00" in new.detail and "26.00" in new.detail
    assert rows[0].prev_as_of is not None
    assert rows[0].as_of != ""


def test_track_detects_pivot_breakout(env):
    inside = synth_bars(300, day_step=2, close=12.0)
    above = synth_bars(300, day_step=2, close=40.0)
    _run(env, _snap_plain, inside)
    rows = _run(env, _snap_plain, above)
    kinds = [c.kind for c in rows[0].changes]
    assert TrackChangeKind.PIVOT_BREAKOUT in kinds
    brk = next(c for c in rows[0].changes if c.kind is TrackChangeKind.PIVOT_BREAKOUT)
    assert "上破" in brk.detail and "18.00" in brk.detail
    assert TrackChangeKind.NO_CHANGE not in kinds


def test_track_detects_signal_and_divergence(env):
    bars = synth_bars(300, day_step=2, close=12.0)
    _run(env, _snap_plain, bars, macd_fn=EQUAL_AREAS)
    rows = _run(env, _snap_with_signal, bars, macd_fn=SHRINKING_AREAS)
    kinds = [c.kind for c in rows[0].changes]
    assert TrackChangeKind.SIGNAL in kinds
    assert TrackChangeKind.DIVERGENCE in kinds
    sig = next(c for c in rows[0].changes if c.kind is TrackChangeKind.SIGNAL)
    assert "第一类买点" in sig.detail
    div = next(c for c in rows[0].changes if c.kind is TrackChangeKind.DIVERGENCE)
    assert "底背驰" in div.detail
    assert div.strength == pytest.approx(0.6)


def test_track_reports_no_change_explicitly(env):
    bars = synth_bars(300, day_step=2, close=12.0)
    _run(env, _snap_plain, bars)
    rows = _run(env, _snap_plain, bars)
    assert [c.kind for c in rows[0].changes] == [TrackChangeKind.NO_CHANGE]
    assert "无变化" in rows[0].changes[0].detail


def test_track_persists_snapshot_for_next_run(env):
    bars = synth_bars(300, day_step=2, close=12.0)
    _run(env, _snap_plain, bars)
    payload = meta.get_structure_snapshot(env.conn, "600000", "day")
    assert payload is not None
    assert payload["version"] >= 1
    assert len(payload["pivots"]) == 1
    assert payload["pivots"][0]["zg"] == 18.0
    assert payload["close"] == pytest.approx(12.0)


def test_track_reads_watchlist_table_when_codes_none(env):
    env.name("600000", "浦发银行")
    meta.add_watch(env.conn, "600000", "浦发银行")
    bars = synth_bars(300, day_step=2, close=12.0)
    env.write("600000", "day", bars)
    env.sync("600000", "day", bars)
    rows = track_watchlist(conn=env.conn, snapshot_source=_snap_plain,
                           macd_fn=EQUAL_AREAS, save=True)
    assert [r.code for r in rows] == ["600000"]
    assert rows[0].name == "浦发银行"


def test_track_codes_path_also_resolves_builtin_index_names(env):
    """`--codes` 指定的**指数**也要有名字。

    `codes=None` 走自选池表（名字存在 `watchlist.name` 里），但 `--codes`
    早先只查 `meta.get_universe` —— 指数不在品种表里，于是
    `python -m chanlun scan --watch --codes hk.hsi` 的跟踪行只剩一串代码
    （推送文案里写成 `- hk.hsi  [day]`，中间两个空格）。
    修法是改走 `meta.name_of`（品种表 → 自选池 → 内置指数名）。
    """
    bars = synth_bars(300, day_step=2, close=12.0)
    env.write("hk.hsi", "day", bars)
    env.sync("hk.hsi", "day", bars)
    rows = track_watchlist(["hk.hsi"], conn=env.conn, snapshot_source=_snap_plain,
                           macd_fn=EQUAL_AREAS, save=False)
    assert rows[0].name == "恒生指数", "内置指数名没兜住，推送文案会缺名字"


def test_track_reports_insufficient_data_without_crashing(env):
    rows = track_watchlist(["600000"], conn=env.conn, snapshot_source=_snap_plain,
                           macd_fn=EQUAL_AREAS, save=False)
    assert rows[0].status == "insufficient"
    assert "数据不足" in rows[0].error
    assert rows[0].changes == ()


def test_track_isolates_corrupt_file(env):
    env.corrupt("600000", "day")
    env.sync("600000", "day", start_ts="2020-01-01", end_ts="2026-09-29", rows=1635)
    bars = synth_bars(300, day_step=2, close=12.0)
    env.write("000001", "day", bars)
    env.sync("000001", "day", bars)
    rows = track_watchlist(["600000", "000001"], conn=env.conn,
                           snapshot_source=_snap_plain, macd_fn=EQUAL_AREAS, save=False)
    by_code = {r.code: r for r in rows}
    assert by_code["600000"].status == "error"
    assert "跟踪失败" in by_code["600000"].error
    assert by_code["000001"].status == "ok"
    assert by_code["000001"].changes[0].kind is TrackChangeKind.FIRST_SNAPSHOT


def test_track_change_kinds_are_stable_values():
    assert TrackChangeKind.NEW_PIVOT.value == "new_pivot"
    assert TrackChangeKind.PIVOT_BREAKOUT.value == "pivot_breakout"
    assert TrackChangeKind.SIGNAL.value == "signal"
    assert TrackChangeKind.DIVERGENCE.value == "divergence"
    assert TrackChangeKind.NO_CHANGE.value == "no_change"
