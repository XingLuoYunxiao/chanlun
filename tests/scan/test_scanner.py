"""扫描器测试（Task 15）。

覆盖三件事：
1. **数据充分性过滤**：绝不拿「只有几天的历史」硬算中枢/背驰；
2. **强度定义与排序**：背驰面积 / 中枢级别 / 信号类型权重可解释、可复算；
3. **鲁棒性**：单只股票失败不拖垮全市场扫描，且失败原因要能看到。
"""

from __future__ import annotations

import json

import pytest

from chanlun.data import meta
from chanlun.scan import scanner
from chanlun.scan.scanner import (
    LEVEL_WEIGHT,
    REQUIREMENTS,
    Outcome,
    ScanHit,
    analyze_snapshot,
    check_sufficiency,
    hit_strength,
    scan,
    scan_report,
)

from scankit import bars_from_path, chain, macd_frame, pivot_of, PIVOT_PATH, signal_of, snapshot_of, synth_bars


# ---------------------------------------------------------------- 假引擎

def _fake_source(code, period, bars):
    """合成结构：3 条线段 + 1 个中枢 + 1 个第一类买点（背驰）。

    段 0（向下，bar 0-9，低点 10.0）与段 2（向下，bar 20-29，低点 9.0）同向，
    段 2 创了新低 → 可能背驰；是否真的背驰取决于 MACD 面积（测试注入）。
    """
    segs = chain([(-1, 10.0, 20.0), (1, 12.0, 22.0), (-1, 9.0, 18.0)])
    pivots = [pivot_of(segs, 12.0, 18.0, 0, 2)]
    sigs = [signal_of(segs[2], "b1", pivot_idx=0)]
    return snapshot_of(code, period, bars, segs=segs, pivots=pivots, signals=sigs)


def _fake_quiet_source(code, period, bars):
    """同样的 3 段 + 1 个中枢，但**没有买卖点**：这类票不产生任何行。"""
    segs = chain([(-1, 10.0, 20.0), (1, 12.0, 22.0), (-1, 9.0, 18.0)])
    pivots = [pivot_of(segs, 12.0, 18.0, 0, 2)]
    return snapshot_of(code, period, bars, segs=segs, pivots=pivots, signals=[])


def _fake_macd(close):
    """段 0 面积 10、段 2 面积 4 → 背驰强度 (10-4)/10 = 0.6。

    两段都是**向下**段（`_fake_source` 里 direction=-1），按第 24 课
    「向上的看红柱子，向下看绿柱子」面积只累加绿柱，所以 hist 取负值。
    """
    return macd_frame(len(close), areas={5: -10.0, 25: -4.0})


# ---------------------------------------------------------------- 充分性

def test_requirements_are_documented_with_reason():
    for period in ("day", "30", "5", "60"):
        req = REQUIREMENTS[period]
        assert req.min_bars >= 100
        assert req.lookback_days > 0
        # 「为什么是 N 根」必须写清楚：MACD 预热、中枢最少线段数、趋势背驰两中枢
        assert "中枢" in req.reason and "背驰" in req.reason and "MACD" in req.reason
    assert REQUIREMENTS["day"].min_bars == 250
    assert REQUIREMENTS["day"].lookback_days >= 365


def test_sufficiency_ok_for_one_year_of_daily_bars():
    bars = synth_bars(260, day_step=2)          # 覆盖 518 个自然日
    suf = check_sufficiency(bars, "day", sync_start=str(bars["ts"].iloc[0]))
    assert suf.ok is True
    assert suf.reason == ""
    assert suf.warning == ""


def test_sufficiency_rejects_too_few_bars():
    bars = synth_bars(100, day_step=5)          # 跨度够，但只有 100 根
    suf = check_sufficiency(bars, "day", sync_start=str(bars["ts"].iloc[0]))
    assert suf.ok is False
    assert suf.reason.startswith("数据不足")
    assert "100" in suf.reason and "250" in suf.reason


def test_sufficiency_rejects_history_shorter_than_lookback():
    bars = synth_bars(260, day_step=1)          # 根数够（260），但只覆盖 259 个自然日
    suf = check_sufficiency(bars, "day", sync_start=str(bars["ts"].iloc[0]))
    assert suf.ok is False
    assert "自然日" in suf.reason
    assert suf.required_start < suf.start_ts


def test_sufficiency_marks_period_never_synced():
    bars = synth_bars(260, day_step=2)
    suf = check_sufficiency(bars, "day", sync_start=None)
    assert suf.ok is False
    assert suf.reason.startswith("数据不足")
    assert "未同步" in suf.reason


def test_sufficiency_unknown_period_never_guesses():
    bars = synth_bars(300, day_step=2)
    suf = check_sufficiency(bars, "tick", sync_start=str(bars["ts"].iloc[0]))
    assert suf.ok is False
    assert "未配置" in suf.reason


def test_sufficiency_checks_intraday_calendar_span():
    long_enough = synth_bars(500, per_day=48, day_step=2)   # 500 根 5 分钟 bar 跨 20 个自然日
    assert check_sufficiency(long_enough, "5", sync_start=None).ok is False  # 未同步优先
    ok = check_sufficiency(long_enough, "5", sync_start=str(long_enough["ts"].iloc[0]))
    assert ok.ok is True
    stale = synth_bars(500, per_day=48, day_step=1)         # 只覆盖最近 10 个自然日
    suf = check_sufficiency(stale, "5", sync_start=str(stale["ts"].iloc[0]))
    assert suf.ok is False
    assert "5 分钟" in suf.reason or "5" in suf.reason


def test_sufficiency_warns_when_meta_start_precedes_file_start():
    """真实数据踩过的坑：sync_state 声称 1991 年起，parquet 只有 2020 年起。"""
    bars = synth_bars(260, day_step=2)
    suf = check_sufficiency(bars, "day", sync_start="1999-11-10")
    assert suf.ok is True
    assert "不一致" in suf.warning
    assert "1999-11-10" in suf.warning


# ---------------------------------------------------------------- 强度

def test_hit_strength_is_multiplicative_and_explained():
    strength, parts = hit_strength("b1", "day", 0.6, "confirmed")
    assert strength == pytest.approx(3.0 * 2.0 * 1.0 * 1.6)
    assert dict(parts)["类型权重"] == 3.0
    assert dict(parts)["级别权重"] == 2.0
    assert dict(parts)["背驰强度"] == pytest.approx(0.6)
    weak, _ = hit_strength("b3", "5", 0.0, "tentative")
    assert weak == pytest.approx(1.5 * 1.0 * 0.6)


def test_level_weight_ranks_daily_above_minute():
    assert LEVEL_WEIGHT["day"] > LEVEL_WEIGHT["30"] > LEVEL_WEIGHT["5"]


# ---------------------------------------------------------------- 单快照分析

def test_analyze_snapshot_sorts_by_strength_desc():
    bars = synth_bars(300, day_step=2)
    segs = chain([(-1, 10.0, 20.0), (1, 12.0, 22.0), (-1, 9.0, 18.0), (1, 12.5, 19.0)])
    pivots = [pivot_of(segs, 12.0, 18.0, 0, 3)]
    sigs = [
        signal_of(segs[2], "b1", pivot_idx=0),                    # 3.0*2*(1+0.6) = 9.6
        signal_of(segs[3], "b3", pivot_idx=0),                    # 1.5*2*(1+0.0) = 3.0
        signal_of(segs[3], "s2", pivot_idx=0, status="tentative"),  # 2.0*2*0.6    = 2.4
    ]
    snap = snapshot_of("600000", "day", bars, segs=segs, pivots=pivots, signals=sigs)
    hits = analyze_snapshot(snap, bars, name="浦发银行", macd_df=_fake_macd(bars["close"]))
    assert [h.signal_kind for h in hits] == ["b1", "b3", "s2"]
    assert [round(h.strength, 2) for h in hits] == [9.6, 3.0, 2.4]
    assert hits[0].divergence_strength == pytest.approx(0.6)
    # 段 3（向上，高点 19.0）没有超过段 1（高点 22.0）→ 不算背驰，强度归 0
    assert hits[1].divergence_strength == 0.0


def test_divergence_area_is_colour_split_not_abs_sum():
    """第 24 课「向上的看红柱子，向下看绿柱子」：背驰强度只由**同色**面积决定。

    段 0 的窗口（bar 0-9）里塞一根大红柱（+50）当干扰：旧口径 `sum(abs(hist))`
    会把前段面积算成 |−10| + |+50| = 60，强度 (60−4)/60 = 0.9333；分色口径下
    两段都是向下段，只累加绿柱，前段面积 = 10，强度 (10−4)/10 = 0.6。
    """
    bars = synth_bars(300, day_step=2)
    segs = chain([(-1, 10.0, 20.0), (1, 12.0, 22.0), (-1, 9.0, 18.0)])
    pivots = [pivot_of(segs, 12.0, 18.0, 0, 2)]
    snap = snapshot_of("600000", "day", bars, segs=segs, pivots=pivots,
                       signals=[signal_of(segs[2], "b1", pivot_idx=0)])
    mixed = macd_frame(len(bars), areas={0: -10.0, 1: 50.0, 20: -4.0})
    hit = analyze_snapshot(snap, bars, macd_df=mixed)[0]
    assert hit.divergence_strength == pytest.approx(0.6)
    assert hit.divergence_strength != pytest.approx((60.0 - 4.0) / 60.0)
    # 反过来：把那根干扰柱换成同色（绿）才会真的改变前段力度。
    all_green = macd_frame(len(bars), areas={0: -10.0, 1: -50.0, 20: -4.0})
    assert analyze_snapshot(snap, bars, macd_df=all_green)[0].divergence_strength \
        == pytest.approx((60.0 - 4.0) / 60.0)


def test_analyze_snapshot_carries_contract_fields():
    bars = synth_bars(300, day_step=2)
    segs = chain([(-1, 10.0, 20.0), (1, 12.0, 22.0), (-1, 9.0, 18.0)])
    pivots = [pivot_of(segs, 12.0, 18.0, 0, 2)]
    snap = snapshot_of("600000", "day", bars, segs=segs, pivots=pivots,
                       signals=[signal_of(segs[2], "b1", pivot_idx=0)])
    hit = analyze_snapshot(snap, bars, name="浦发银行", macd_df=_fake_macd(bars["close"]))[0]
    assert isinstance(hit, ScanHit)
    assert (hit.code, hit.name, hit.period) == ("600000", "浦发银行", "day")
    assert hit.signal_kind == "b1"
    assert hit.level == "day"
    assert hit.pivot_range == (12.0, 18.0)
    assert hit.divergence_strength == pytest.approx(0.6)
    assert hit.outcome == Outcome.HIT
    assert "背驰" in hit.detail and "第一类买点" in hit.detail
    assert dict(hit.strength_parts)["类型权重"] == 3.0


def test_analyze_snapshot_ignores_unconfirmed_pivot_range_gracefully():
    """信号没有对应中枢时不能崩，pivot_range 为空元组。"""
    bars = synth_bars(300, day_step=2)
    segs = chain([(-1, 10.0, 20.0), (1, 12.0, 22.0), (-1, 9.0, 18.0)])
    snap = snapshot_of("600000", "day", bars, segs=segs, pivots=[],
                       signals=[signal_of(segs[2], "b1", pivot_idx=None)])
    hit = analyze_snapshot(snap, bars, macd_df=_fake_macd(bars["close"]))[0]
    assert hit.pivot_range == ()


# ---------------------------------------------------------------- 扫描编排

def test_scan_returns_hits_and_skips_insufficient(env):
    good = synth_bars(260, day_step=2)
    short = synth_bars(100, day_step=5)
    for code, bars in (("600000", good), ("000001", short)):
        env.write(code, "day", bars)
        env.sync(code, "day", bars)
    env.name("600000", "浦发银行")
    env.name("000001", "平安银行")

    report = scan_report(["day"], ["600000", "000001"], conn=env.conn,
                         snapshot_source=_fake_source, macd_fn=_fake_macd, save=False)
    assert [h.code for h in report.hits] == ["600000"]
    assert report.hits[0].name == "浦发银行"
    assert report.hits[0].strength == pytest.approx(9.6)
    assert [s.code for s in report.skipped] == ["000001"]
    assert report.skipped[0].outcome == Outcome.INSUFFICIENT
    assert report.skipped[0].reason.startswith("数据不足")
    assert report.tasks == 2
    assert Outcome.INSUFFICIENT == "insufficient"


def test_scan_accepts_plain_scan_helper(env):
    bars = synth_bars(260, day_step=2)
    env.write("600000", "day", bars)
    env.sync("600000", "day", bars)
    hits = scan(["day"], ["600000"], conn=env.conn, snapshot_source=_fake_source,
                macd_fn=_fake_macd, save=False)
    assert isinstance(hits, list) and len(hits) == 1
    assert isinstance(hits[0], ScanHit)


def test_scan_isolates_corrupt_file(env):
    env.corrupt("600000", "day")
    env.sync("600000", "day", start_ts="2020-01-01", end_ts="2026-09-29", rows=1635)
    bars = synth_bars(260, day_step=2)
    env.write("000001", "day", bars)
    env.sync("000001", "day", bars)

    report = scan_report(["day"], ["600000", "000001"], conn=env.conn,
                         snapshot_source=_fake_source, macd_fn=_fake_macd, save=False)
    assert [h.code for h in report.hits] == ["000001"]
    assert [e.code for e in report.errors] == ["600000"]
    assert "扫描失败" in report.errors[0].reason
    assert report.errors[0].reason.startswith("扫描失败")
    assert "Traceback" not in report.errors[0].reason


def test_scan_report_summary_is_chinese(env):
    bars = synth_bars(260, day_step=2)
    env.write("600000", "day", bars)
    env.sync("600000", "day", bars)
    report = scan_report(["day"], ["600000"], conn=env.conn,
                         snapshot_source=_fake_source, macd_fn=_fake_macd, save=False)
    text = report.summary()
    assert "命中" in text and "数据不足" in text and "失败" in text
    assert "600000" in report.all_rows()[0].code or report.all_rows()[0].code == "600000"


def test_scan_persists_results_with_strength(env):
    bars = synth_bars(260, day_step=2)
    env.write("600000", "day", bars)
    env.sync("600000", "day", bars)
    env.name("600000", "浦发银行")
    report = scan_report(["day"], ["600000"], conn=env.conn,
                         snapshot_source=_fake_source, macd_fn=_fake_macd,
                         save=True, run_date="2026-09-29")
    assert report.saved == 1
    rows = meta.get_scan_results(env.conn, "2026-09-29")
    assert len(rows) == 1
    assert rows[0]["code"] == "600000"
    assert rows[0]["strength"] == pytest.approx(9.6)
    detail = json.loads(rows[0]["detail"])
    assert detail["name"] == "浦发银行"
    assert "第一类买点" in detail["detail"]


def test_scan_uses_universe_when_codes_none(env):
    for code in ("600000", "000001", "600519"):
        env.name(code, code)
    bars = synth_bars(260, day_step=2)
    env.write("600000", "day", bars)
    env.sync("600000", "day", bars)
    report = scan_report(["day"], None, conn=env.conn, snapshot_source=_fake_source,
                         macd_fn=_fake_macd, save=False)
    assert list(report.codes) == ["000001", "600000", "600519"]
    assert report.tasks == 3
    assert len(report.hits) == 1


def test_scan_without_codes_requires_universe(env):
    with pytest.raises(ValueError, match="universe"):
        scan_report(["day"], None, conn=env.conn, snapshot_source=_fake_source, save=False)


# ---------------------------------------------------------------- 真引擎 / 多进程

def test_scan_real_engine_finds_pivot_and_signal_on_crafted_path(env):
    """真引擎端到端：这条人工路径既能划出中枢，也能给出第三类卖点。

    这里曾经断言 ``report.hits == ()``，并在注释里把 0 命中记成「Task 17 报告的
    引擎缺陷 G1/G2，如实报告而非放宽条件」。那不是如实，是把缺陷钉进了判据：
    ``_third_kind`` 当时拿 ``segs[end_idx + 1]`` 当离开段，而真实线段首尾相连，
    那一段必然是反向回抽段，于是「离开段方向向上且低点 > ZG」恒不成立。判据按
    第 20 课改成**位置**口径（离开段 = 中枢组最后一段 ``segs[end_idx]``，回试段
    = 紧随其后的 ``segs[end_idx + 1]``）之后，同一条路径照旧能出信号。
    详见 optimizer/theory/L20-THIRD-POINT-POSITION.md。
    """
    bars = bars_from_path(PIVOT_PATH, day_step=2)
    env.write("600000", "day", bars)
    env.sync("600000", "day", bars)
    report = scan_report(["day"], ["600000"], conn=env.conn, max_workers=1, save=False)
    assert report.errors == ()   # ScanReport 是 frozen dataclass，行集合按 tuple 约定
    assert report.skipped == ()  # ScanReport 是 frozen dataclass，行集合按 tuple 约定
    assert report.structure["pivots"] >= 1
    assert [h.signal_kind for h in report.hits] == ["s3"]
    assert report.hits[0].price == 7.445
    assert report.hits[0].ts == "2025-05-29 15:00:00"


def test_scan_runs_in_real_process_pool(env):
    """真 ProcessPoolExecutor + 真引擎 + 临时数据根：验证子进程数据根传递。"""
    for code in ("600000", "000001"):
        bars = synth_bars(260, day_step=2)
        env.write(code, "day", bars)
        env.sync(code, "day", bars)
    report = scan_report(["day"], ["600000", "000001"], conn=env.conn, max_workers=2)
    assert report.errors == ()   # ScanReport 是 frozen dataclass，行集合按 tuple 约定
    assert report.skipped == ()  # ScanReport 是 frozen dataclass，行集合按 tuple 约定
    assert report.structure["codes_scanned"] == 2
    assert report.structure["segments"] >= 1
    for hit in report.hits:
        assert isinstance(hit, ScanHit)
        assert hit.code in ("600000", "000001")


def test_scan_period_missing_from_store_is_insufficient(env):
    """sync_state 有 30 分钟记录但 parquet 被删（只同步了日线）→ 数据不足，不是失败。"""
    bars = synth_bars(260, day_step=2)
    env.sync("600000", "day", bars)
    env.sync("600000", "30", start_ts="2026-08-01 09:30:00", end_ts="2026-09-29 15:00:00",
             rows=480)
    report = scan_report(["30"], ["600000"], conn=env.conn, snapshot_source=_fake_source,
                         macd_fn=_fake_macd, save=False)
    assert report.errors == ()   # ScanReport 是 frozen dataclass，行集合按 tuple 约定
    assert len(report.skipped) == 1
    assert "数据不足" in report.skipped[0].reason


def test_data_quality_note_survives_even_without_any_signal_row(env):
    """元数据声称 1999 年就有数据、本地文件却是 2024 年起 → 必须留下数据质量提示。

    **这条测试证明的是报告层**：夹具（`_fake_quiet_source`）直接给了一份
    「3 段 + 1 个中枢 + 0 个买卖点」的快照，所以不产生任何 hits/skipped/errors 行。
    空行是夹具给的，不是引擎算出来的 —— 本测试不校验引擎会不会算出 0 个买卖点。
    若警告只挂在行上就会**被静默丢掉**，所以报告本身要带 notes。
    """
    bars = synth_bars(260, day_step=2)
    env.write("600000", "day", bars)
    env.sync("600000", "day", bars, start_ts="1999-11-10")
    report = scan_report(["day"], ["600000"], conn=env.conn,
                         snapshot_source=_fake_quiet_source, macd_fn=_fake_macd, save=False)
    assert report.tasks == 1
    assert report.hits == ()
    assert report.skipped == ()
    assert report.errors == ()
    assert any("不一致" in note and "1999-11-10" in note for note in report.notes)
    assert "数据质量" in report.summary()


# ---------------------------------------------------------------- 递归上下文（D-51）
def _ls(depth, level, trends=(), pivots=()):
    from chanlun.chan.recursion import LevelStructure
    return LevelStructure(level=level, depth=depth, pivots=tuple(pivots), trends=tuple(trends))


def _trend(direction, src_start, src_end, level="30"):
    from chanlun.chan.trend import Trend, TrendType
    kind = {1: TrendType.UP, -1: TrendType.DOWN}.get(direction, TrendType.CONSOLIDATION)
    return Trend(
        idx=0, kind=kind, start_idx=0, end_idx=0, start_ts="", end_ts="", level=level,
        pivot_count=1, status=_confirmed(), confirmed_at="", high=0.0, low=0.0,
        direction=direction, src_start=src_start, src_end=src_end,
    )


def _confirmed():
    from chanlun.chan.types import Status
    return Status.CONFIRMED


def _bars(n=10):
    import pandas as pd
    return pd.DataFrame({"ts": [f"d{i:03d}" for i in range(n)]})


def _sig(ts):
    import types as _t
    return _t.SimpleNamespace(ts=ts)


def test_level_context_is_empty_without_recursion():
    """没有更高级别就**不编** —— 早期 `Snapshot` 的 `levels` 是空元组。"""
    import types as _t
    snap = _t.SimpleNamespace(levels=())
    assert scanner.level_context(snap, _bars(), _sig("d005")) == ()
    snap1 = _t.SimpleNamespace(levels=(_ls(0, "5"),))
    assert scanner.level_context(snap1, _bars(), _sig("d005")) == (), "只有 L0 时不该说话"


def test_level_context_reports_the_containing_higher_level_trend():
    import types as _t
    snap = _t.SimpleNamespace(levels=(
        _ls(0, "5"),
        _ls(1, "30", trends=(_trend(1, 2, 7), _trend(-1, 8, 9))),
        _ls(2, "day", trends=(_trend(0, 0, 9, level="day"),)),
    ))
    assert scanner.level_context(snap, _bars(), _sig("d005")) == (
        "L1/30 上涨走势", "L2/day 盘整走势",
    )
    assert scanner.level_context(snap, _bars(), _sig("d008")) == (
        "L1/30 下跌走势", "L2/day 盘整走势",
    )


def test_level_context_falls_back_to_a_pivot_when_no_trend_covers_the_bar():
    import types as _t
    class _P:
        src_start, src_end = 3, 6
    snap = _t.SimpleNamespace(levels=(_ls(0, "5"), _ls(1, "30", pivots=(_P(),))))
    assert scanner.level_context(snap, _bars(), _sig("d004")) == ("L1/30 中枢内",)
    assert scanner.level_context(snap, _bars(), _sig("d000")) == ()


def test_level_context_says_nothing_when_the_ts_is_not_in_the_bars():
    """对不上就**不报**，绝不猜一个级别出来。"""
    import types as _t
    snap = _t.SimpleNamespace(levels=(_ls(0, "5"), _ls(1, "30", trends=(_trend(1, 0, 9),))))
    assert scanner.level_context(snap, _bars(), _sig("不存在")) == ()


def test_level_context_never_changes_who_is_a_hit(env):
    """**边界测试**：level_context 是纯上下文，不许动命中判定与强度。

    让递归**参与**买卖点判定属于判据变更（要 theory 条目 + 测量 + MINOR 版本位），
    不在本次范围内。这条用例就是那条边界的护栏：把 `levels` 抽掉，
    命中的**种类、时间、强度、顺序**必须逐项不变。
    """
    import dataclasses as _dc

    from chanlun.chan.engine import ChanEngine

    bars = bars_from_path(PIVOT_PATH, day_step=2)
    snap = ChanEngine("600000", "day").full(bars)
    assert len(snap.levels) >= 1, "这条路径应当至少产出 L0"

    got = scanner.analyze_snapshot(snap, bars, name="x")
    stripped = scanner.analyze_snapshot(_dc.replace(snap, levels=()), bars, name="x")
    key = lambda h: (h.signal_kind, h.ts, h.strength, h.outcome)   # noqa: E731
    assert [key(h) for h in got] == [key(h) for h in stripped]
    assert all(h.level_context == () for h in stripped)


def test_scan_stats_expose_the_recursion(env):
    """扫描摘要要看得见递归 —— 否则「只出 L0」和「出了 6 级」长得一模一样。"""
    bars = bars_from_path(PIVOT_PATH, day_step=2)
    env.write("600000", "day", bars)
    env.sync("600000", "day", bars)
    report = scan_report(["day"], ["600000"], conn=env.conn, max_workers=1, save=False)
    assert report.structure["levels"] >= 1
    assert report.structure["max_level_depth"] >= 0


# ------------------------------------------------- 区间套（第 27 课，用户指令③）
def test_quantao_participates_in_strength_but_never_zeroes():
    """区间套**降权而不是归零** —— 第 27 课说它用于「确认」，没说「不嵌套就不是买点」。

    做成硬门（×0）会强于原文：第一类买卖点的定义在第 17 课（趋势背驰），
    区间套只是「大级别转折点的精确定位」。
    """
    base, _ = hit_strength("b1", "day", 0.6, "confirmed", None)
    yes, p_yes = hit_strength("b1", "day", 0.6, "confirmed", True)
    no, p_no = hit_strength("b1", "day", 0.6, "confirmed", False)
    assert yes == pytest.approx(base)          # 确认 → 不打折
    assert no == pytest.approx(base * 0.7)     # 未确认 → 降权
    assert no > 0, "降权不许归零 —— 那会把判据强过原文"
    assert dict(p_yes)["区间套权重"] == 1.0
    assert dict(p_no)["区间套权重"] == 0.7


def test_quantao_none_means_no_comparable_higher_level_and_is_not_penalised():
    """★「**无从确认**」与「**确认失败**」是两回事。

    `levels` 只有一级时没有「上一级别」这个东西可比（第 63 课：级别名只是相对基准的
    递归深度标签）。把它当成「确认失败」去降权，是在惩罚一个**不存在**的判据。
    """
    base, parts = hit_strength("b1", "day", 0.6, "confirmed", None)
    assert base == pytest.approx(3.0 * 2.0 * 1.0 * 1.0 * 1.6)
    assert dict(parts)["区间套权重"] == 1.0


def test_quantao_only_applies_to_first_kind():
    """只有第一类买卖点带 `quantao`；其余类型传什么都是 1.0（调用方传 None）。"""
    for kind in ("b2", "b3", "s2", "s3", "pb", "ps"):
        base, _ = hit_strength(kind, "day", 0.3, "confirmed", None)
        assert base == pytest.approx(hit_strength(kind, "day", 0.3, "confirmed")[0])


# ------------------------------------------------- 1 分钟基准（用户指令②）
def test_one_minute_period_is_registered():
    """★ `"1"` 曾在**整个扫描器里缺席**，而 1 分钟数据已经能拉。

    后果是 `scan --periods 1` 把每一只都判成「数据不足」——**数据到位了、生产却用不了**。
    这是「只做了一半」最典型的形态：回填成功、`web/api.py` 的 `PERIODS` 也放行了，
    但 `REQUIREMENTS.get("1")` 查不到就按 0 根算。
    """
    from chanlun.scan.scanner import LEVEL_RANK, LEVEL_WEIGHT, REQUIREMENTS

    req = REQUIREMENTS["1"]
    assert req.min_bars == 480
    assert req.lookback_days == 3
    assert req.reason, "推导理由必须写出来（与其它周期同构）"
    assert "1" in LEVEL_WEIGHT and "1" in LEVEL_RANK


def test_one_minute_sorts_below_five_minutes():
    """级别权重与排序：1 分钟是**最小**的显微镜，权重不该高过 5 分钟。"""
    from chanlun.scan.scanner import LEVEL_RANK, LEVEL_WEIGHT

    assert LEVEL_WEIGHT["1"] < LEVEL_WEIGHT["5"]
    assert LEVEL_RANK["1"] > LEVEL_RANK["5"]
