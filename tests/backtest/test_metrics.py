"""Task 14 指标口径测试。

指标全部**从 BacktestResult 的原始数据现算**（不缓存中间态），所以这里手工
构造一个最小结果对象，把口径钉死：胜率、盈亏比、期望、最大回撤、平均持仓
周期、分信号类型统计、基准对比。
"""

from __future__ import annotations

import pytest

from chanlun.backtest.broker import EquityPoint, Fill, Position, RoundTrip
from chanlun.backtest.metrics import max_drawdown, metrics
from chanlun.backtest.runner import BacktestResult


def _rt(pnl, kind="b1", bars=2, exit_kind="s1") -> RoundTrip:
    entry = 10.0
    qty = 100
    return RoundTrip(
        code="600000",
        entry_ts="2024-01-02",
        exit_ts="2024-01-04",
        qty=qty,
        entry_price=entry,
        exit_price=entry + pnl / qty,
        gross_pnl=pnl + 6.0,
        fees=6.0,
        pnl=pnl,
        pnl_pct=pnl / (entry * qty),
        holding_bars=bars,
        signal_kind=kind,
        exit_signal_kind=exit_kind,
        reason_entry="测试",
        reason_exit="测试",
    )


def _fill(ts, side, price, qty=100) -> Fill:
    amount = price * qty
    return Fill(
        ts=ts, code="600000", side=side, price=price, qty=qty, amount=amount,
        commission=5.0, stamp_tax=0.0 if side == "buy" else amount * 1e-3,
        fee_total=5.0, cash_after=0.0, signal_kind=None, reason="",
    )


def _result(round_trips=(), equity=(), initial_cash=100_000.0, benchmark=None,
            positions=(), trades=()) -> BacktestResult:
    return BacktestResult(
        codes=("600000",), period="day", start="2024-01-01", end="2024-06-30",
        initial_cash=initial_cash, strategy="test",
        trades=tuple(trades), round_trips=tuple(round_trips), equity=tuple(equity),
        positions=tuple(positions), rejections=(), pending=(),
        benchmark=benchmark, notes=(), data_span=None, metrics={},
    )


def _eq(points) -> tuple[EquityPoint, ...]:
    return tuple(
        EquityPoint(ts=ts, cash=eq, market_value=0.0, equity=eq) for ts, eq in points
    )


def test_max_drawdown_uses_peak_to_trough_of_equity_curve():
    curve = _eq([("2024-01-02", 100_000.0), ("2024-01-03", 110_000.0),
                 ("2024-01-04", 99_000.0), ("2024-01-05", 105_000.0)])
    dd, dd_pct = max_drawdown(curve)
    assert dd == pytest.approx(11_000.0)
    assert dd_pct == pytest.approx(0.10)


def test_metrics_include_per_signal_type_winrate():
    rts = [_rt(500, "b1"), _rt(-200, "b1"), _rt(-100, "b2"), _rt(300, "b2"),
           _rt(1000, "b3")]
    m = metrics(_result(rts, _eq([("2024-01-02", 100_000.0)])))
    assert m["n_trades"] == 5
    assert m["win_rate"] == pytest.approx(3 / 5)
    per = m["per_signal_type"]
    assert per["b1"]["n"] == 2 and per["b1"]["win_rate"] == pytest.approx(0.5)
    assert per["b2"]["n"] == 2 and per["b2"]["win_rate"] == pytest.approx(0.5)
    assert per["b3"]["n"] == 1 and per["b3"]["win_rate"] == pytest.approx(1.0)
    assert per["b3"]["payoff_ratio"] is None, "无亏损样本时盈亏比没有定义，返回 None"


def test_payoff_ratio_and_expectancy_are_defined_as_documented():
    rts = [_rt(600, "b1"), _rt(200, "b1"), _rt(-400, "b1")]
    m = metrics(_result(rts, _eq([("2024-01-02", 100_000.0)])))
    assert m["avg_win"] == pytest.approx(400.0)
    assert m["avg_loss"] == pytest.approx(-400.0)
    assert m["payoff_ratio"] == pytest.approx(1.0)      # 平均盈利 / 平均亏损（绝对值）
    assert m["profit_factor"] == pytest.approx(800 / 400)
    assert m["expectancy"] == pytest.approx((600 + 200 - 400) / 3)
    assert m["expectancy_pct"] == pytest.approx(m["expectancy"] / 1000.0)


def test_metrics_with_zero_trades_do_not_crash():
    m = metrics(_result((), _eq([("2024-01-02", 100_000.0), ("2024-01-03", 100_000.0)])))
    assert m["n_trades"] == 0
    assert m["win_rate"] is None and m["payoff_ratio"] is None
    assert m["expectancy"] is None
    assert m["max_drawdown"] == 0.0
    assert m["avg_holding_bars"] is None
    assert m["per_signal_type"] == {}


def test_avg_holding_period_reported_in_bars():
    rts = [_rt(100, "b1", bars=2), _rt(100, "b1", bars=4)]
    m = metrics(_result(rts, _eq([("2024-01-02", 100_000.0)])))
    assert m["avg_holding_bars"] == pytest.approx(3.0)


def test_benchmark_comparison_reports_excess_return():
    rts = [_rt(10_000, "b1")]
    bench = {"code": "000300", "start": "2024-01-02", "end": "2024-06-28",
             "close_start": 4000.0, "close_end": 4200.0, "return": 0.05}
    m = metrics(_result(rts, _eq([("2024-01-02", 100_000.0), ("2024-06-28", 110_000.0)]),
                         benchmark=bench))
    assert m["total_return"] == pytest.approx(0.10)
    assert m["benchmark"]["return"] == pytest.approx(0.05)
    assert m["benchmark"]["excess_return"] == pytest.approx(0.05)


def test_metrics_uses_total_return_from_equity_curve():
    m = metrics(_result([], _eq([("2024-01-02", 100_000.0), ("2024-06-28", 90_000.0)])))
    assert m["total_return"] == pytest.approx(-0.10)
    assert m["max_drawdown"] == pytest.approx(10_000.0)
    assert m["max_drawdown_pct"] == pytest.approx(0.10)


def test_metrics_survives_empty_equity_curve():
    m = metrics(_result())
    assert m["total_return"] is None or m["total_return"] == 0.0
    assert m["max_drawdown"] == 0.0
