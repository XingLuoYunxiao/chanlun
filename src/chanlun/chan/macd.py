"""动力学——MACD 指标与背驰（面积）度量（第 24/25 课）。

缠论判断背驰用的是 MACD **面积**：同向的两段走势中，后一段创了新极值，
但对应区间的 MACD 柱面积反而缩小，说明力度衰竭，背驰成立。

第 24 课原文：「这里的面积，指的是MACD柱子所围成的面积……**向上的看红柱子，
向下看绿柱子**」——面积必须**分色**累加（向上段只算红柱、向下段只算绿柱），
红绿混加会把反方向的力度也算进来，见 `hist_area`。

口径必须与通达信/同花顺一致（硬要求，面积比较对口径极其敏感）：

    EMA(X, N)_t = (2 * X_t + (N - 1) * EMA(X, N)_{t-1}) / (N + 1)
    EMA(X, N)_1 = X_1                        # 以第一个值作初值

    DIF = EMA(close, 12) - EMA(close, 26)
    DEA = EMA(DIF, 9)                        # 同样以 DIF 首值为初值
    MACD 柱 = 2 * (DIF - DEA)

注意这**不是** `pandas.Series.ewm(...).mean()` 的默认行为：`adjust=True`（默认）
以加权平均起步，前若干根柱子与通达信对不上。`ewm(span=N, adjust=False)` 在
数学上与上面的递归等价（`alpha = 2/(N+1)` 且以首值为初值），测试里用它做
**独立交叉验证**。

本模块只提供**无未来函数**的度量：第 t 行的值只依赖 `close[:t+1]`，逐点截断
重算与整体一次算出的结果逐位相等（见 `tests/chan/test_macd.py`）。指标与面积
都是**纯函数**，买卖点判定由上层（buy_sell）基于这些度量实现。

计算复杂度：EMA 为**单遍 O(n) 递归**，面积/极值为 O(区间长度)，不做重复扫描。
"""

from __future__ import annotations

import operator
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "MACD_COLUMNS",
    "ema",
    "macd",
    "hist_area",
    "dif_high",
    "dif_low",
    "dif_extreme",
    "is_divergence",
]

MACD_COLUMNS: tuple[str, str, str] = ("dif", "dea", "hist")

ArrayLike = Sequence[float] | np.ndarray | pd.Series


# ---------------------------------------------------------------- 输入处理


def _as_float_array(values: Any) -> np.ndarray:
    """统一转成一维 float64 数组（支持 list/tuple/ndarray/Series）。"""
    if isinstance(values, pd.Series):
        arr = values.to_numpy(dtype=float, copy=False)
    else:
        arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"输入必须是一维序列，收到 {arr.ndim} 维")
    return arr


def _check_period(n: Any, name: str) -> int:
    """周期参数必须是 >= 1 的整数。"""
    try:
        n = operator.index(n)
    except TypeError as exc:
        raise ValueError(f"{name} 必须是整数，收到 {n!r}") from exc
    if n < 1:
        raise ValueError(f"{name} 必须 >= 1，收到 {n}")
    return n


def _column_values(df: Any, name: str) -> np.ndarray:
    """从 MACD 结果里取出某一列。

    接受 `macd()` 返回的 DataFrame；也容忍直接给一维序列（Series/ndarray/list），
    此时视为该列本身，方便单独复用面积与极值函数。
    """
    if isinstance(df, pd.DataFrame):
        if name not in df.columns:
            raise ValueError(f"缺少列 {name!r}，实际列为 {list(df.columns)}")
        return np.asarray(df[name], dtype=float)
    if isinstance(df, pd.Series):
        return np.asarray(df, dtype=float)
    return _as_float_array(df)


def _check_range(n: int, i0: Any, i1: Any) -> tuple[int, int]:
    """校验闭区间 `[i0, i1]` 落在 `[0, n-1]` 内，越界即 ValueError。

    索引一律按**位置**解释（0 起、不允许负数回绕），与 `macd()` 返回的
    `RangeIndex` 天然一致。
    """
    if n == 0:
        raise ValueError("数据为空，没有可度量的区间")
    try:
        i0 = operator.index(i0)
        i1 = operator.index(i1)
    except TypeError as exc:
        raise ValueError(f"索引必须是整数，收到 {i0!r}, {i1!r}") from exc
    if i0 < 0 or i1 < 0 or i0 >= n or i1 >= n:
        raise ValueError(f"区间 [{i0}, {i1}] 越界（有效范围 0..{n - 1}）")
    if i0 > i1:
        raise ValueError(f"区间起点 {i0} 不能大于终点 {i1}")
    return i0, i1


# ---------------------------------------------------------------- EMA / MACD


def ema(values: ArrayLike, n: int) -> np.ndarray:
    """通达信口径的 `EMA(X, N)`：单遍 O(n) 递归，以第一个值为初值。

    `EMA_t = (2 * X_t + (N - 1) * EMA_{t-1}) / (N + 1)`，`EMA_1 = X_1`。

    返回与输入等长的 float64 数组；空输入返回空数组。因为是从左到右单向递归，
    第 t 个输出只依赖 `X[:t+1]`，天然无未来函数。
    """
    n = _check_period(n, "n")
    arr = _as_float_array(values)
    size = arr.size
    out = np.empty(size, dtype=float)
    if size == 0:
        return out

    alpha = 2.0 / (n + 1.0)
    keep = 1.0 - alpha
    # 转成 Python list 逐点递推：与直接索引 ndarray 标量相比避免每步的
    # numpy 拆包开销，5400 只股票 × 数千根日线也只需单遍。
    data = arr.tolist()
    prev = float(data[0])          # EMA_1 = X_1
    out[0] = prev
    for i in range(1, size):
        prev = alpha * float(data[i]) + keep * prev
        out[i] = prev
    return out


def macd(
    close: ArrayLike, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """按通达信/同花顺口径计算 MACD，返回列固定为 `["dif", "dea", "hist"]`。

    `DIF = EMA(close, fast) - EMA(close, slow)`；`DEA = EMA(DIF, signal)`；
    `hist = 2 * (DIF - DEA)`。

    行数与输入一致，索引为 `RangeIndex`，各列 `dtype=float`；空输入返回带三列
    的空 DataFrame（不抛异常）。第 t 行只用 `close[:t+1]`，无未来函数。
    """
    fast = _check_period(fast, "fast")
    slow = _check_period(slow, "slow")
    signal = _check_period(signal, "signal")
    values = _as_float_array(close)

    if values.size == 0:
        return pd.DataFrame({name: pd.Series(dtype="float64") for name in MACD_COLUMNS})

    dif = ema(values, fast) - ema(values, slow)
    dea = ema(dif, signal)
    return pd.DataFrame(
        {
            "dif": dif,
            "dea": dea,
            "hist": 2.0 * (dif - dea),
        }
    )


# ---------------------------------------------------------------- 面积与极值


def hist_area(macd_df: Any, i0: int, i1: int, color: int) -> float:
    """闭区间 `[i0, i1]`（**含两端**）内**同色** MACD 柱的面积。

    第 24 课原文：「这里的面积，指的是MACD柱子所围成的面积……**向上的看红柱子，
    向下看绿柱子**」。即向上段只累加红柱（`hist > 0`）、向下段只累加绿柱
    （`hist < 0`）；而且第 24 课要求两个被比较的走势类型**同向**，混色求和会把
    反方向的力度也算进来，口径就错了。

    `color=+1` 只算红柱（向上段）、`color=-1` 只算绿柱（向下段），返回**绝对值
    面积**（恒 >= 0）；窗口内没有同色柱时返回 0.0（该区间没有同向力度）。
    索引按位置解释，越界或 `i0 > i1` 抛 `ValueError`；`color` 只能是 1 或 -1。

    审计记录（`docs/evidence/2026-10-01-chanlun-strictness-audit.md` §2.9）：
    旧实现是 `sum(abs(hist))`，把 |红| + |绿| **一起相加**，与本课原文相悖，
    属审计确认的口径偏离。`color` 是**必填**参数，不允许再退回混色求和。
    """
    if color not in (1, -1):
        raise ValueError(f"color 只能是 1（红柱/向上）或 -1（绿柱/向下），收到 {color!r}")
    hist = _column_values(macd_df, "hist")
    i0, i1 = _check_range(hist.size, i0, i1)
    signed = hist[i0 : i1 + 1] * color
    return float(np.sum(np.clip(signed, 0.0, None), dtype=float))


def _dif_window(macd_df: Any, i0: int, i1: int) -> tuple[np.ndarray, int]:
    dif = _column_values(macd_df, "dif")
    i0, i1 = _check_range(dif.size, i0, i1)
    return dif[i0 : i1 + 1], i0


def dif_high(macd_df: Any, i0: int, i1: int) -> tuple[float, int]:
    """区间内 DIF 的**最大值**及其位置，用于向上走势的力度比较。

    返回 `(最大值, 位置索引)`；索引是相对整个序列的位置（与 `macd()` 的
    `RangeIndex` 一致）。最大值并列时取**最先出现**的那个（`np.argmax` 语义）。
    """
    window, offset = _dif_window(macd_df, i0, i1)
    return float(np.max(window)), offset + int(np.argmax(window))


def dif_low(macd_df: Any, i0: int, i1: int) -> tuple[float, int]:
    """区间内 DIF 的**最小值**及其位置，用于向下走势的力度比较。

    返回 `(最小值, 位置索引)`；索引是相对整个序列的位置。最小值并列时取
    **最先出现**的那个（`np.argmin` 语义）。
    """
    window, offset = _dif_window(macd_df, i0, i1)
    return float(np.min(window)), offset + int(np.argmin(window))


def dif_extreme(macd_df: Any, i0: int, i1: int, direction: int) -> tuple[float, int]:
    """按方向取 DIF 极值：`direction=1` 向上取 `dif_high`，`-1` 向下取 `dif_low`。

    这是 `dif_high` / `dif_low` 的带方向封装，语义与它们完全一致，
    方便上层按「走势方向」统一调用。`direction` 只能是 1 或 -1。
    """
    if direction == 1:
        return dif_high(macd_df, i0, i1)
    if direction == -1:
        return dif_low(macd_df, i0, i1)
    raise ValueError(f"direction 只能是 1（向上）或 -1（向下），收到 {direction!r}")


# ---------------------------------------------------------------- 背驰判定


def is_divergence(
    price_extreme_prev: float,
    price_extreme_now: float,
    area_prev: float,
    area_now: float,
    direction: int,
) -> bool:
    """背驰判定（纯函数）。

    语义：同向的两段走势（由 `direction` 指定方向，1 向上、-1 向下），
    后一段创了**新的价格极值**，但 MACD 面积**缩小**（力度衰竭）→ 背驰成立：

    - `direction=1`：要求 `price_extreme_now > price_extreme_prev` 且 `area_now < area_prev`；
    - `direction=-1`：要求 `price_extreme_now < price_extreme_prev` 且 `area_now < area_prev`。

    价格**没有创新极值就一律不算背驰**（哪怕面积明显缩小，那只是同一波段的
    延续/盘整）；面积相等（`area_now == area_prev`）也不算，必须严格缩小。
    参数校验：`direction` 只能是 1 或 -1，两个面积都必须 >= 0，否则 `ValueError`。
    """
    if direction not in (1, -1):
        raise ValueError(f"direction 只能是 1（向上）或 -1（向下），收到 {direction!r}")
    if area_prev < 0 or area_now < 0:
        raise ValueError(
            f"面积不能为负：area_prev={area_prev!r}, area_now={area_now!r}"
        )

    if direction == 1:
        new_extreme = price_extreme_now > price_extreme_prev
    else:
        new_extreme = price_extreme_now < price_extreme_prev
    return bool(new_extreme and area_now < area_prev)
