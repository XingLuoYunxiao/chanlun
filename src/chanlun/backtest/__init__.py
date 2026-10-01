"""Task 14 point-in-time 回测框架。

模块划分
--------
- `broker`：T+1、次日开盘成交、涨跌停限制、佣金/印花税、回合盈亏（FIFO）。
- `strategy`：`Strategy` 协议、`Context`、缠论信号策略、非缠论机械策略、注册表。
- `runner`：逐 bar 循环，用 `store.read(..., end=bar.ts)` 做**物理截断**保证无未来函数。
- `metrics`：胜率/盈亏比/期望/最大回撤/持仓周期/分信号类型/基准对比。
- `cli`：`add_parser(sub)` / `register(sub)` 钩子，供 Task 16 挂进 `__main__.py`。

为什么单独一个包
----------------
回测与「盘中扫描」共享同一套「只看 confirmed 结构」的约束（`chan.state.backtestable`），
但两者的时间推进方式完全不同：扫描是每次对最新快照做一次判断，回测必须逐 bar
重放并维护账户。混在一起会让账务逻辑渗进扫描路径，所以分开放。
"""

from __future__ import annotations

from .broker import (
    COMMISSION_MIN,
    COMMISSION_RATE,
    LOT_SIZE,
    STAMP_TAX_RATE,
    Bar,
    Broker,
    EquityPoint,
    Fill,
    Order,
    Position,
    Rejection,
    RoundTrip,
    limit_pct,
    limit_prices,
)
from .metrics import max_drawdown, metrics
from .runner import DEFAULT_BENCHMARK, BacktestResult, run
from .strategy import (
    STRATEGIES,
    ChanSignalStrategy,
    Context,
    FractalStrategy,
    Strategy,
    StrategySpec,
    confirmed_fractals,
    get_strategy,
    kind_label,
)

__all__ = [
    "COMMISSION_MIN", "COMMISSION_RATE", "LOT_SIZE", "STAMP_TAX_RATE",
    "Bar", "Broker", "EquityPoint", "Fill", "Order", "Position", "Rejection",
    "RoundTrip", "limit_pct", "limit_prices",
    "max_drawdown", "metrics",
    "DEFAULT_BENCHMARK", "BacktestResult", "run",
    "STRATEGIES", "ChanSignalStrategy", "Context", "FractalStrategy", "Strategy",
    "StrategySpec", "confirmed_fractals", "get_strategy", "kind_label",
]
