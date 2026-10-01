"""Task 14 回测骨架（runner）契约测试。

最关键的一条：**禁止未来函数**。这里用 monkeypatch 把 `store.read` 换成
探针，断言它拿到的 `end` 参数永远等于「当前正在模拟的那根 bar 的 ts」，
且任何一次读取的返回行都不越过 `end`。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from chanlun.backtest.broker import Order
from chanlun.backtest.runner import BacktestResult, run
from chanlun.backtest.strategy import ChanSignalStrategy, FractalStrategy
from chanlun.data import store

from _synth import make_bars


@dataclass
class _RecordingStrategy:
    """记录每根 bar 能看到什么，并在 on_bar 内部就地校验无未来函数。"""

    def __post_init__(self):
        self.seen_ts: list[str] = []
        self.as_of: list[str] = []
        self.tentative_seen: list[str] = []
        self.confirmed_at: list[str] = []
        self.pairs: list[tuple[str, str]] = []

    def on_bar(self, ctx, bar, snapshot):
        self.seen_ts.append(bar.ts)
        self.as_of.append(snapshot.as_of)
        assert snapshot.as_of == bar.ts, "快照终点必须正好是当前 bar 的 ts"
        for item in (*snapshot.strokes, *snapshot.segments, *snapshot.pivots, *snapshot.signals):
            assert str(getattr(item.status, "value", item.status)) == "confirmed", \
                "tentative 结构不得进入回测快照"
            assert item.confirmed_at is not None and item.confirmed_at <= bar.ts, \
                f"结构确认时间 {item.confirmed_at} 晚于当前 bar {bar.ts}"
            self.confirmed_at.append(item.confirmed_at)
            self.pairs.append((bar.ts, item.confirmed_at))
        return []


@dataclass
class _ScriptedStrategy:
    """在第 N 根 bar 下买单、第 M 根 bar 下卖单，用于断言「次日开盘成交」。"""

    buy_at: str
    sell_at: str

    def on_bar(self, ctx, bar, snapshot):
        if bar.ts == self.buy_at:
            return [Order(bar.code, "buy", qty=0, signal_kind="manual", reason="脚本买入")]
        if bar.ts == self.sell_at and ctx.position(bar.code) is not None:
            return [Order(bar.code, "sell", qty=0, signal_kind="manual", reason="脚本卖出")]
        return []


@dataclass
class _SameBarBuySellStrategy:
    """同一根 bar 同时挂买与卖，逼出「当日买入不可当日卖出」。"""

    at: str

    def on_bar(self, ctx, bar, snapshot):
        if bar.ts == self.at:
            return [Order(bar.code, "buy", qty=0), Order(bar.code, "sell", qty=0)]
        return []


# ---------------- 无未来函数 ----------------
def test_runner_never_reads_future_bars(seed_store, monkeypatch):
    df = make_bars(n=160)
    seed_store("600000", df)
    real_read = store.read
    calls: list[tuple] = []

    def spy(code, period, start=None, end=None, limit=None):
        out = real_read(code, period, start=start, end=end, limit=limit)
        calls.append((code, period, start, end))
        if end is not None and len(out):
            assert str(out["ts"].max()) <= end, f"物理截断失效：读到了 {end} 之后的行"
        return out

    monkeypatch.setattr(store, "read", spy)

    rec = _RecordingStrategy()
    run(rec, ["600000"], "2024-01-02", "2024-06-30")

    assert calls, "runner 必须通过 store.read 取数"
    assert all(end is not None for (_, _, _, end) in calls), \
        "任何一次读取都必须带 end（物理截断），否则不是 point-in-time"
    # 建时间轴的那次读取带 start；逐 bar 的截断读不带 start（要看到回测区间之前的左侧历史）
    pit = [end for (_, _, start, end) in calls if start is None]
    bar_ts = [str(t) for t in real_read("600000", "day", start="2024-01-02",
                                        end="2024-06-30")["ts"]]
    assert pit == bar_ts, "每个 bar 恰好一次物理截断读，且 end == 该 bar 的 ts"
    assert rec.as_of == bar_ts, "策略看到的快照按 bar 顺序推进，终点就是当前 bar"


def test_runner_ignores_future_structures(seed_store):
    """策略快照里只能出现 confirmed_at <= 当前 bar 的结构（tentative 一律忽略）。"""
    seed_store("600000", make_bars(n=240))
    rec = _RecordingStrategy()
    res = run(rec, ["600000"], "2024-01-02", "2024-12-31")
    assert rec.seen_ts, "应当有 bar 被处理"
    assert res.notes is not None
    # 每根 bar 上看到的结构，其确认时间都不得晚于该 bar
    assert rec.pairs, "应当能看到一些已确认结构"
    assert all(confirmed <= seen for seen, confirmed in rec.pairs)
    assert set(rec.as_of) == set(rec.seen_ts)


# ---------------- 成交时点 ----------------
def test_order_fills_at_next_bar_open(seed_store):
    df = make_bars(n=60)
    seed_store("600000", df)
    ts = [str(t) for t in df["ts"]]
    opens = {str(t): float(o) for t, o in zip(df["ts"], df["open"])}
    strat = _ScriptedStrategy(buy_at=ts[10], sell_at=ts[20])
    res = run(strat, ["600000"], ts[0], ts[-1])

    fills = list(res.trades)
    assert [f.side for f in fills] == ["buy", "sell"]
    assert fills[0].ts == ts[11], "第 10 根 bar 下的单在第 11 根 bar 成交"
    assert fills[1].ts == ts[21], "卖出同理，T+1 天然满足"
    assert fills[0].price == pytest.approx(opens[ts[11]])
    assert fills[1].price == pytest.approx(opens[ts[21]])
    assert len(res.round_trips) == 1


def test_same_bar_buy_and_sell_is_blocked_by_t_plus_1(seed_store):
    df = make_bars(n=30)
    seed_store("600000", df)
    ts = [str(t) for t in df["ts"]]
    res = run(_SameBarBuySellStrategy(at=ts[5]), ["600000"], ts[0], ts[-1])
    assert [f.side for f in res.trades] == ["buy"], "买单成交，同日卖单被 T+1 拒绝"
    assert any("T+1" in r.reason for r in res.rejections)


def test_unfilled_orders_at_backtest_end_are_reported(seed_store):
    df = make_bars(n=20)
    seed_store("600000", df)
    ts = [str(t) for t in df["ts"]]
    res = run(_ScriptedStrategy(buy_at=ts[-1], sell_at=ts[-1]), ["600000"], ts[0], ts[-1])
    assert res.trades == ()
    assert len(res.pending) == 1 and res.pending[0].side == "buy", \
        "收盘时未成交的委托要留痕，不能静默丢弃"


# ---------------- 基准与降级 ----------------
def test_benchmark_degrades_gracefully_when_missing(seed_store):
    df = make_bars(n=40)
    seed_store("600000", df)
    ts = [str(t) for t in df["ts"]]
    res = run(_ScriptedStrategy(ts[0], ts[1]), ["600000"], ts[0], ts[-1],
              benchmark_code="000300")
    assert res.benchmark is None
    assert any("基准" in n for n in res.notes), "缺基准数据必须在 notes 里明确说明"


def test_benchmark_used_when_data_exists(seed_store):
    df = make_bars(n=40)
    bench = make_bars(n=40, base=4000.0, amp=0.0, trend=1.0)
    seed_store("600000", df)
    store.write("000300", "day", bench)
    ts = [str(t) for t in df["ts"]]
    res = run(_ScriptedStrategy(ts[0], ts[1]), ["600000"], ts[0], ts[-1],
              benchmark_code="000300")
    assert res.benchmark is not None
    expected = float(bench["close"].iloc[-1]) / float(bench["close"].iloc[0]) - 1
    assert res.benchmark["return"] == pytest.approx(expected)
    assert res.metrics["benchmark"]["return"] == pytest.approx(expected)


def test_missing_code_data_is_reported_not_silently_dropped(seed_store):
    seed_store("600000", make_bars(n=20))
    ts = [str(t) for t in make_bars(n=20)["ts"]]
    res = run(_ScriptedStrategy(ts[0], ts[1]), ["600000", "999999"], ts[0], ts[-1])
    assert any("999999" in n and "无" in n for n in res.notes)


# ---------------- 结果对象 ----------------
def test_result_is_frozen_and_carries_raw_data(seed_store):
    df = make_bars(n=200)
    seed_store("600000", df)
    ts = [str(t) for t in df["ts"]]
    res = run(FractalStrategy(), ["600000"], ts[0], ts[-1])

    assert isinstance(res, BacktestResult)
    assert res.trades and res.round_trips and res.equity
    with pytest.raises(Exception):
        res.trades = ()  # type: ignore[misc]  frozen dataclass
    assert res.metrics["n_trades"] == len(res.round_trips)
    # 权益曲线必须单调覆盖时间轴且末点 == 最终权益
    assert [p.ts for p in res.equity] == sorted(p.ts for p in res.equity)
    assert res.metrics["final_equity"] == pytest.approx(res.equity[-1].equity)


def test_mechanical_fractal_strategy_actually_trades(seed_store):
    """非缠论机械策略：底分型确认后次日买入、顶分型后卖出（只为验证账务/指标管线）。"""
    df = make_bars(n=240)
    seed_store("600000", df)
    ts = [str(t) for t in df["ts"]]
    res = run(FractalStrategy(), ["600000"], ts[0], ts[-1])
    assert len(res.round_trips) >= 3, f"正弦波上应当有多次往返，实际 {len(res.round_trips)}"
    assert all(r.pnl_pct is not None for r in res.round_trips)
    assert res.metrics["win_rate"] is not None
    assert res.metrics["per_signal_type"], "机械策略也要按信号类型归类"


def test_chan_strategy_runs_and_reports_zero_signals_on_sample(seed_store):
    """缠论信号策略在合成样本上可能 0 笔，但必须是「跑通了且如实报告 0」，不是崩溃。"""
    df = make_bars(n=240)
    seed_store("600000", df)
    ts = [str(t) for t in df["ts"]]
    res = run(ChanSignalStrategy(), ["600000"], ts[0], ts[-1])
    assert res.metrics["n_trades"] == len(res.round_trips)
    if not res.round_trips:
        assert res.metrics["win_rate"] is None
        assert any("0" in n or "无" in n for n in res.notes)
