"""Task 14 账务层（Broker）契约测试。

这一组测试**不经过 runner**，直接对 Broker 下单，目的就是把交易规则钉死：
T+1、次日开盘价成交、涨停不可买/跌停不可卖、佣金万 2.5 双边且最低 5 元、
印花税千 1 只在卖出端收。
"""

from __future__ import annotations

import pytest

from chanlun.backtest.broker import Bar, Broker, Order


def bar(code="600000", ts="2024-01-03", open_=10.0, high=None, low=None, close=None) -> Bar:
    return Bar(
        code=code, ts=ts, open=open_,
        high=open_ if high is None else high,
        low=open_ if low is None else low,
        close=open_ if close is None else close,
        volume=1000.0, amount=open_ * 1000.0, index=0,
    )


def test_next_open_price_is_used_and_no_slippage_by_default():
    b = Broker(100_000.0)
    b.submit(Order("600000", "buy", qty=100), ts="2024-01-02")
    b.begin_bar("2024-01-03")
    out = b.execute_open(bar(open_=10.0), prev_close=9.5)
    assert len(out) == 1
    fill = out[0]
    assert fill.price == 10.0, "默认不加滑点、不做价格取整：成交价就是 bar 的 open"
    assert fill.qty == 100
    assert fill.ts == "2024-01-03", "委托在（提交日之后的）下一根 bar 开盘成交"


def test_t_plus_1_blocks_same_day_sell():
    b = Broker(100_000.0)
    b.submit(Order("600000", "buy", qty=100), ts="2024-01-02")
    b.begin_bar("2024-01-03")
    b.execute_open(bar(ts="2024-01-03", open_=10.0), prev_close=9.5)
    assert b.position("600000").qty == 100

    # 同一交易日内立刻挂卖单（模拟「当日买当日卖」）→ 必须被 T+1 拦下
    b.submit(Order("600000", "sell", qty=100), ts="2024-01-03")
    out = b.execute_open(bar(ts="2024-01-03", open_=10.5), prev_close=10.0)
    assert len(out) == 1
    assert out[0].__class__.__name__ == "Rejection"
    assert "T+1" in out[0].reason
    assert b.position("600000").qty == 100

    # 隔日（新 bar ts）再卖 → 放行
    b.submit(Order("600000", "sell", qty=100), ts="2024-01-03")
    out = b.execute_open(bar(ts="2024-01-04", open_=10.5), prev_close=10.5)
    assert out[0].__class__.__name__ == "Fill"
    assert b.position("600000") is None, "清仓后不应留下 0 股的空持仓"


def test_limit_up_blocks_buy():
    b = Broker(100_000.0)
    b.submit(Order("600000", "buy", qty=100), ts="2024-01-02")
    # 前收 10.00 → 涨停 11.00；开盘价顶在涨停价上，买单不允许成交
    out = b.execute_open(bar(open_=11.00, high=11.00, low=11.00, close=11.00), prev_close=10.00)
    assert out[0].__class__.__name__ == "Rejection"
    assert "涨停" in out[0].reason
    assert b.cash == pytest.approx(100_000.0)


def test_limit_down_blocks_sell():
    b = Broker(100_000.0)
    b.submit(Order("600000", "buy", qty=100), ts="2024-01-02")
    b.execute_open(bar(ts="2024-01-03", open_=10.00), prev_close=9.5)
    b.submit(Order("600000", "sell", qty=100), ts="2024-01-03")
    # 前收 10.00 → 跌停 9.00
    out = b.execute_open(bar(ts="2024-01-04", open_=9.00, high=9.00, low=9.00, close=9.00),
                         prev_close=10.00)
    assert out[0].__class__.__name__ == "Rejection"
    assert "跌停" in out[0].reason
    assert b.position("600000").qty == 100


def test_commission_has_five_yuan_floor():
    b = Broker(100_000.0)
    b.submit(Order("600000", "buy", qty=100), ts="2024-01-02")
    fill = b.execute_open(bar(open_=10.0), prev_close=9.5)[0]
    # 100 股 × 10 元 = 1000 元，万 2.5 = 0.25 元 → 触发最低 5 元
    assert fill.commission == pytest.approx(5.0)
    assert fill.stamp_tax == 0.0, "印花税只在卖出端收"


def test_commission_rate_applies_above_floor_and_stamp_tax_on_sell_only():
    b = Broker(1_000_000.0)
    b.submit(Order("600000", "buy", qty=10_000), ts="2024-01-02")
    buy = b.execute_open(bar(open_=10.0), prev_close=9.5)[0]
    assert buy.amount == pytest.approx(100_000.0)
    assert buy.commission == pytest.approx(100_000.0 * 2.5e-4)  # 25 元 > 5 元下限
    assert buy.stamp_tax == 0.0

    b.submit(Order("600000", "sell", qty=10_000), ts="2024-01-02")
    sell = b.execute_open(bar(ts="2024-01-04", open_=11.0), prev_close=10.0)[0]
    assert sell.commission == pytest.approx(110_000.0 * 2.5e-4)
    assert sell.stamp_tax == pytest.approx(110_000.0 * 1e-3), "印花税千 1，仅卖出"


def test_buy_cash_is_conserved_and_rounded_to_lot():
    b = Broker(10_000.0)
    b.submit(Order("600000", "buy", qty=0), ts="2024-01-02")  # qty=0 → 用足现金
    fill = b.execute_open(bar(open_=31.0), prev_close=30.0)[0]
    assert fill.qty % 100 == 0, "买入必须是 100 股整数倍（A 股一手）"
    assert b.cash >= 0
    # 现金 + 持仓成本（含费）应等于初始资金
    assert b.cash + fill.qty * fill.price + fill.commission == pytest.approx(10_000.0)


def test_insufficient_cash_rejects_instead_of_going_negative():
    b = Broker(1_000.0)
    b.submit(Order("600000", "buy", qty=0), ts="2024-01-02")
    out = b.execute_open(bar(open_=50.0), prev_close=49.0)  # 一手要 5000 元
    assert out[0].__class__.__name__ == "Rejection"
    assert "现金" in out[0].reason
    assert b.cash == pytest.approx(1_000.0)


def test_sell_more_than_available_is_clipped_to_available():
    b = Broker(100_000.0)
    b.submit(Order("600000", "buy", qty=100), ts="2024-01-02")
    b.execute_open(bar(ts="2024-01-03", open_=10.0), prev_close=9.5)
    b.submit(Order("600000", "sell", qty=500), ts="2024-01-03")
    fill = b.execute_open(bar(ts="2024-01-04", open_=10.0), prev_close=10.0)[0]
    assert fill.qty == 100, "卖出数量不得超过可卖数量，不足部分不生成委托"


def test_round_trip_pnl_nets_out_all_fees():
    b = Broker(100_000.0)
    b.submit(Order("600000", "buy", qty=1000, signal_kind="b1"), ts="2024-01-02")
    b.execute_open(bar(ts="2024-01-03", open_=10.0), prev_close=9.5)
    b.submit(Order("600000", "sell", qty=1000, signal_kind="s1"), ts="2024-01-03")
    b.execute_open(bar(ts="2024-01-04", open_=11.0), prev_close=10.0)

    assert len(b.round_trips) == 1
    rt = b.round_trips[0]
    gross = (11.0 - 10.0) * 1000
    assert rt.pnl == pytest.approx(gross - rt.fees)
    # 买入佣金 max(5, 10000*2.5e-4=2.5) = 5；卖出佣金 max(5, 11000*2.5e-4=2.75) = 5；
    # 印花税 11000*1e-3 = 11 → 合计 21 元。两端的「最低 5 元」都真实生效。
    assert rt.fees == pytest.approx(5.0 + 5.0 + 11_000 * 1e-3)
    assert rt.signal_kind == "b1", "回合按买入信号类型归类"
    assert rt.exit_signal_kind == "s1"
