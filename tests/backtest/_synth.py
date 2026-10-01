"""合成行情：Task 14 回测测试专用（离线、确定性）。

用合成正弦波而不是真实数据的理由：真实 24 只样本上三类买卖点为 0
（引擎缺陷 G1），没法验证「有成交时账务与指标是否正确」。正弦波的波峰波谷
间隔固定、分型必然交替，于是可以精确断言「第几根 bar 成交、成交价是不是
次日开盘价」。
"""

from __future__ import annotations

import math

import pandas as pd


def make_bars(
    n: int = 200,
    start: str = "2024-01-02",
    base: float = 10.0,
    amp: float = 2.0,
    wave: float = 24.0,
    trend: float = 0.0,
) -> pd.DataFrame:
    """生成 n 根日线。`trend` 是每根 bar 的线性漂移，用来造趋势/基准。"""
    ts = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d")
    rows = []
    prev_close = base
    for i, t in enumerate(ts):
        c = base + amp * math.sin(2 * math.pi * i / wave) + trend * i
        o = prev_close + (c - prev_close) * 0.3  # 留跳空，避免 open==close
        hi = max(o, c) + 0.05
        lo = min(o, c) - 0.05
        rows.append((t, o, hi, lo, c, 1000.0, c * 1000.0))
        prev_close = c
    return pd.DataFrame(
        rows, columns=["ts", "open", "high", "low", "close", "volume", "amount"]
    )
