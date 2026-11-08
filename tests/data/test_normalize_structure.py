"""`normalize` 的结构校验：倒挂剔除、越界仅警告（F-2 / D-46）。

三条不变量，都是**可失败**的：

① **倒挂行（`high < low`）必须剔除。** 它的价格区间是空的，而
   `chan/fractal.py` 的分型四条件（`b.high > a.high and b.high > c.high and
   b.low > a.low and b.low > c.low`，对偶同）与 `chan/include.py` 的包含关系
   判定（`_contains`）全部建立在 `high`/`low` 的**区间比较**上。
② **`open`/`close` 越出 `[low, high]` 的行必须保留，只警告。** 分两档是刻意的：
   `open` 不参与任何判据；`close` 不参与分型/笔/线段/中枢的极值比较，但会进
   MACD（`chan/signal.py:202`、`chan/divergence.py:357`）⇒ 影响背驰。
   剔除它会**少一根 K 线**，包含处理与分型随之重排 —— 那个代价大于
   「一个字段偏一点」，所以在拿到实测证据之前只观测。
③ **倒挂行不许进合并序列。** 注入一根倒挂行后，`ChanEngine.full()` 的
   `snap.merged` 里不许出现 `high < low`，且结果必须与「把这根行直接删掉」
   逐项相同。

判据用**严格小于**：一字板（`high == low`）不是倒挂，不许误伤 ——
那是合法行情，涨停/跌停/无成交都会产生它。
"""

from __future__ import annotations

import logging

import pandas as pd

from chanlun.chan.engine import ChanEngine
from chanlun.data.types import normalize

LOGGER = "chanlun.data.types"


def _frame(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """`rows` 每项是 `(ts, open, high, low, close)`。"""
    return pd.DataFrame(
        {
            "ts": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
            "volume": [1000.0] * len(rows),
            "amount": [1e5] * len(rows),
        }
    )


def _zigzag(n: int = 60, base: float = 10.0, leg: int = 4) -> pd.DataFrame:
    """三角波，每 `leg` 根一个方向 —— 足够让引擎真的划出分型与笔。"""
    rows = []
    for i in range(n):
        step = i % leg
        amp = float(step if (i // leg) % 2 == 0 else leg - step)
        mid = base + amp
        rows.append((f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}", mid, mid + 0.5,
                     mid - 0.5, mid))
    return _frame(rows)


# ---------------------------------------------------------------- ① 倒挂剔除


def test_normalize_drops_upside_down_bars(caplog):
    """`high < low` 的行被剔除，其余保留，并且留一条点名 ts 的警告。"""
    df = _frame(
        [
            ("2024-01-02", 10.0, 10.5, 9.5, 10.2),
            ("2024-01-03", 10.0, 9.0, 9.5, 9.4),   # 倒挂：high 9.0 < low 9.5
            ("2024-01-04", 10.4, 10.9, 9.9, 10.6),
        ]
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = normalize(df)

    assert list(out["ts"]) == ["2024-01-02", "2024-01-04"]
    assert "倒挂" in caplog.text
    assert "2024-01-03" in caplog.text, "警告要点名是哪几根，否则没法回查数据源"


def test_normalize_does_not_touch_a_limit_up_bar(caplog):
    """一字板（`high == low`）是合法行情，不许被当成倒挂误伤。"""
    df = _frame(
        [
            ("2024-01-02", 10.0, 10.5, 9.5, 10.2),
            ("2024-01-03", 11.0, 11.0, 11.0, 11.0),   # 一字板
            ("2024-01-04", 10.4, 10.9, 9.9, 10.6),
        ]
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = normalize(df)

    assert list(out["ts"]) == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert "倒挂" not in caplog.text


# ---------------------------------------------------------------- ② 越界仅警告


def test_normalize_keeps_out_of_range_bars_with_warning(caplog):
    """`open`/`close` 出了 `[low, high]`：行**保留**，但必须留下计数警告。"""
    df = _frame(
        [
            ("2024-01-02", 10.0, 10.5, 9.5, 10.2),
            # open 11.0 > high 10.5，close 9.0 < low 9.5 —— 同一根同时越界两端
            ("2024-01-03", 11.0, 10.5, 9.5, 9.0),
            ("2024-01-04", 10.4, 10.9, 9.9, 10.6),
        ]
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = normalize(df)

    assert list(out["ts"]) == ["2024-01-02", "2024-01-03", "2024-01-04"], (
        "越界行必须保留 —— 剔除会少一根 K 线，包含处理与分型跟着重排"
    )
    assert "越界" in caplog.text
    assert "不剔除" in caplog.text, "警告要写清是「保留、仅观测」，不是剔除"


# ---------------------------------------------------------------- ③ 不进合并序列


def test_upside_down_bar_cannot_reach_the_merged_sequence():
    """倒挂行剔除后，结果必须与「这根行根本不存在」逐项相同。

    注入的倒挂行取 `high=0.01, low=1e9`：它必然与左邻构成包含关系，
    于是被 `max/max`（向上）或 `min/min`（向下）吸收 —— **两个方向都会**
    产出 `high < low` 的合并K线。所以这条断言在修复前是**真的会红**，
    不是"顺手加一条不会失败的测试"。
    """
    clean = _zigzag(60)
    corrupt = clean.copy()
    k = 30
    corrupt.loc[k, ["open", "high", "low", "close"]] = [10.0, 0.01, 1e9, 10.0]
    removed = corrupt.drop(index=k).reset_index(drop=True)

    snap_corrupt = ChanEngine("test.corrupt", "day").full(corrupt)
    snap_removed = ChanEngine("test.removed", "day").full(removed)

    inverted = [m for m in snap_corrupt.merged if m.high < m.low]
    assert inverted == [], (
        "合并K线序列里出现了空区间（high < low）—— 分型四条件与包含关系判定"
        "都建立在区间比较上，空区间会让它们自相矛盾"
    )
    assert [(m.ts, m.high, m.low) for m in snap_corrupt.merged] == [
        (m.ts, m.high, m.low) for m in snap_removed.merged
    ], "剔除倒挂行之后，合并序列必须与「直接删掉这根」完全一致"
    assert len(snap_corrupt.strokes) == len(snap_removed.strokes)
    assert len(snap_corrupt.segments) == len(snap_removed.segments)
