"""MACD 与背驰度量测试（Task 12 动力学部分）。

覆盖三类硬性要求：
1. **口径**：与通达信/同花顺一致（首值作初值的递归 EMA，柱 = 2*(DIF-DEA)），
   并用 `pandas.ewm(span=N, adjust=False)` 作为**独立实现**交叉验证；
2. **无未来函数**：逐点截断重算与整体一次算出的对应位置完全相等；
3. **真实数据**：一段固定的小序列（真实日线片段，数字直接写进本文件，离线）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chanlun.chan.macd import (
    MACD_COLUMNS,
    dif_extreme,
    dif_high,
    dif_low,
    ema,
    hist_area,
    is_divergence,
    macd,
)

# 一段真实日线的收盘价片段（600000，2021-04-27 ~ 2021-06-10，30 个交易日），
# 直接写死在此，测试完全离线。期望值由 pandas ewm(adjust=False) 独立算出。
REAL_CLOSES = [
    7.9229, 7.9075, 8.1312, 7.7532, 7.7918, 7.7378, 7.676, 7.7532, 7.8072,
    7.784, 7.8612, 7.8766, 7.8843, 7.8072, 7.838, 7.784, 7.784, 7.9615,
    7.9846, 7.9383, 7.9846, 7.9229, 7.8612, 7.8843, 7.892, 7.892, 7.892,
    7.9538, 7.8766, 7.892,
]
REAL_DIF = [
    0.0, -0.001228, 0.015668, -0.001426, -0.011724, -0.023966, -0.038214,
    -0.042783, -0.041568, -0.041992, -0.035688, -0.029114, -0.023017,
    -0.024128, -0.022267, -0.024863, -0.026613, -0.013522, -0.001268,
    0.004653, 0.012933, 0.014351, 0.010376, 0.008986, 0.008409, 0.007862,
    0.007343, 0.011783, 0.008968, 0.00789,
]
REAL_DEA = [
    0.0, -0.000246, 0.002937, 0.002064, -0.000693, -0.005348, -0.011921,
    -0.018093, -0.022788, -0.026629, -0.028441, -0.028576, -0.027464,
    -0.026797, -0.025891, -0.025685, -0.025871, -0.023401, -0.018974,
    -0.014249, -0.008812, -0.00418, -0.001269, 0.000782, 0.002308,
    0.003419, 0.004203, 0.005719, 0.006369, 0.006673,
]
REAL_HIST = [
    0.0, -0.001966, 0.025462, -0.006981, -0.022061, -0.037236, -0.052586,
    -0.049379, -0.037559, -0.030727, -0.014495, -0.001077, 0.008894,
    0.005337, 0.007247, 0.001645, -0.001485, 0.019759, 0.035413, 0.037805,
    0.043491, 0.037061, 0.023289, 0.016408, 0.012203, 0.008886, 0.006279,
    0.012127, 0.005199, 0.002433,
]


def _fake(hist: list[float], dif: list[float] | None = None) -> pd.DataFrame:
    """构造一个带 hist（和可选 dif）列的 MACD 结果，供面积/极值函数使用。"""
    n = len(hist)
    if dif is None:
        dif = [0.0] * n
    return pd.DataFrame({"dif": dif, "dea": [0.0] * n, "hist": hist})


def _walk(n: int = 90, seed: int = 20240501) -> np.ndarray:
    """确定性随机游走（不联网、不依赖外部数据）。"""
    rng = np.random.default_rng(seed)
    return np.round(100.0 + np.cumsum(rng.normal(0.0, 1.0, size=n)), 4)


# ------------------------------------------------ 1. 口径：递归 EMA

def test_ema_seeds_with_first_value():
    """手算基准：EMA(1,2,3,4,5; N=2)，alpha = 2/3，以首值作初值。"""
    out = ema([1.0, 2.0, 3.0, 4.0, 5.0], 2)
    assert out[0] == pytest.approx(1.0)                                  # EMA_1 = X_1
    assert out[1] == pytest.approx((2 * 2 + 1 * 1) / 3)                  # 5/3
    assert out[2] == pytest.approx((2 * 3 + (5 / 3)) / 3)                # 7.666../3
    assert out[3] == pytest.approx(3.5185185185185185)
    assert out[4] == pytest.approx(4.506172839506173)
    assert out == pytest.approx([1.0, 5 / 3, 2.5555555555555554,
                                 3.5185185185185185, 4.506172839506173])
    # 不是 SMA、不是以 0 起步：SMA 口径第 2 个值会是 1.5，以 0 起步会是 4/3。
    assert out[1] != pytest.approx(1.5)
    assert out[1] != pytest.approx(4 / 3)


def test_ema_is_linear_time_and_monotone_window():
    """长序列也能一遍算完，且输出与输入等长、纯 float。"""
    vals = _walk(2000)
    out = ema(vals, 26)
    assert out.shape == vals.shape
    assert out.dtype == np.float64
    # 常量序列的 EMA 恒等于该常量（递归收敛性与初值口径的直接推论）。
    assert ema([7.5] * 50, 12) == pytest.approx([7.5] * 50)


def test_ema_rejects_bad_period():
    with pytest.raises(ValueError):
        ema([1.0, 2.0], 0)
    with pytest.raises(ValueError):
        ema([1.0, 2.0], 1.5)


# ------------------------------------------------ 2. MACD 结构

def test_macd_columns_and_index():
    df = macd(REAL_CLOSES)
    assert list(df.columns) == list(MACD_COLUMNS) == ["dif", "dea", "hist"]
    assert isinstance(df.index, pd.RangeIndex)
    assert len(df) == len(REAL_CLOSES)
    assert all(df[c].dtype == np.float64 for c in MACD_COLUMNS)
    assert np.isfinite(df.to_numpy()).all()


def test_macd_accepts_list_tuple_ndarray_series():
    """四种输入容器结果一致。"""
    ref = macd(REAL_CLOSES)
    for other in (
        tuple(REAL_CLOSES),
        np.asarray(REAL_CLOSES),
        pd.Series(REAL_CLOSES),
    ):
        got = macd(other)
        assert np.allclose(got.to_numpy(), ref.to_numpy(), rtol=0, atol=0)


def test_hist_equals_two_times_dif_minus_dea():
    df = macd(_walk(120))
    assert np.allclose(df["hist"].to_numpy(),
                       2.0 * (df["dif"] - df["dea"]).to_numpy(), rtol=0, atol=0)
    # 反过来确认系数确实是 2：去掉系数会留下正好一半的偏差。
    assert not np.allclose(df["hist"].to_numpy(),
                           (df["dif"] - df["dea"]).to_numpy(), rtol=0, atol=1e-12)


def test_empty_input_returns_empty_frame_with_columns():
    for empty in ([], (), np.asarray([]), pd.Series(dtype="float64")):
        df = macd(empty)
        assert list(df.columns) == ["dif", "dea", "hist"]
        assert len(df) == 0
        assert isinstance(df.index, pd.RangeIndex)
        assert all(df[c].dtype == np.float64 for c in MACD_COLUMNS)
    # 单点输入：首值即全部，三列都是 0，不抛异常。
    one = macd([12.0])
    assert len(one) == 1
    assert one.iloc[0].to_dict() == {"dif": 0.0, "dea": 0.0, "hist": 0.0}


# ------------------------------------------------ 3. 无未来函数

def test_no_future_function_per_point_truncation():
    """第 t 行只能用 close[:t+1] 算出：逐点截断重算 == 整体一次算出的第 t 行。"""
    for closes in (REAL_CLOSES, _walk(90).tolist()):
        full = macd(closes)
        for t in range(len(closes)):
            part = macd(closes[: t + 1])
            assert len(part) == t + 1
            # 逐位相等（RTOL=ATOL=0）：前缀递归与整体递归在同一位置路径完全一致。
            assert np.allclose(part.to_numpy(), full.iloc[: t + 1].to_numpy(),
                               rtol=0, atol=0)
            for col in MACD_COLUMNS:
                assert part.iloc[-1][col] == pytest.approx(
                    full.iloc[t][col], rel=0, abs=0
                )


def test_no_future_function_for_plausible_future_shift():
    """反证：若把未来数据拼在后面，历史前缀的 MACD 不该改变。"""
    prefix = REAL_CLOSES[:20]
    base = macd(prefix)
    injected = macd(prefix + [99.0, 1.0, 50.0])
    assert np.allclose(injected.iloc[:20].to_numpy(), base.to_numpy(), rtol=0, atol=0)


# ------------------------------------------------ 4. 独立交叉验证与真实数据

def test_matches_pandas_ewm_cross_check():
    """与 pandas ewm(span=N, adjust=False) 交叉验证（数学上等价）。"""
    for closes in (REAL_CLOSES, _walk(150)):
        s = pd.Series(closes, dtype="float64")
        dif_ref = (s.ewm(span=12, adjust=False).mean()
                   - s.ewm(span=26, adjust=False).mean())
        dea_ref = dif_ref.ewm(span=9, adjust=False).mean()
        hist_ref = 2.0 * (dif_ref - dea_ref)

        df = macd(closes)
        assert np.allclose(df["dif"].to_numpy(), dif_ref.to_numpy())
        assert np.allclose(df["dea"].to_numpy(), dea_ref.to_numpy())
        assert np.allclose(df["hist"].to_numpy(), hist_ref.to_numpy())
        # 顺带确认自定义周期也等价。
        df2 = macd(closes, fast=5, slow=13, signal=4)
        d2 = (s.ewm(span=5, adjust=False).mean()
              - s.ewm(span=13, adjust=False).mean())
        assert np.allclose(df2["dif"].to_numpy(), d2.to_numpy())
        assert np.allclose(df2["dea"].to_numpy(),
                           d2.ewm(span=4, adjust=False).mean().to_numpy())


def test_macd_matches_reference_on_fixed_real_series():
    """真实日线片段（离线写死）与独立算出的期望值逐点对齐。"""
    df = macd(REAL_CLOSES)
    assert len(df) == 30
    assert df["dif"].to_numpy() == pytest.approx(REAL_DIF, abs=1e-6)
    assert df["dea"].to_numpy() == pytest.approx(REAL_DEA, abs=1e-6)
    assert df["hist"].to_numpy() == pytest.approx(REAL_HIST, abs=1e-6)
    assert hist_area(df, 0, 29) == pytest.approx(0.564488, abs=1e-6)
    # 该片段 DIF/柱都出现过负值，说明样本确实覆盖了绿柱区间。
    assert (df["dif"] < 0).any() and (df["hist"] < 0).any()


# ------------------------------------------------ 5. 面积

def test_hist_area_is_sum_of_absolute_values():
    df = _fake([1.5, -2.0, 3.0, -0.5])
    assert hist_area(df, 0, 3) == pytest.approx(7.0)          # |1.5|+|−2|+|3|+|−0.5|
    assert hist_area(df, 0, 3) != pytest.approx(2.0)          # 不是算术和
    assert hist_area(df, 0, 1) == pytest.approx(3.5)          # 红绿相消会得到 −0.5
    assert all(hist_area(df, i, i) >= 0 for i in range(4))
    # 真实数据上也成立（该区间红绿柱混杂）。
    real = macd(REAL_CLOSES)
    abs_sum = float(np.sum(np.abs(real["hist"].to_numpy())))
    assert hist_area(real, 0, 29) == pytest.approx(abs_sum, abs=1e-12)


def test_hist_area_closed_interval_boundaries():
    """闭区间含两端：右端点的柱子必须计入。"""
    df = _fake([1.0, 2.0, 4.0, 8.0, 16.0])
    assert hist_area(df, 0, 0) == pytest.approx(1.0)
    assert hist_area(df, 4, 4) == pytest.approx(16.0)
    assert hist_area(df, 1, 3) == pytest.approx(14.0)         # 2+4+8，含 i1=3
    assert hist_area(df, 0, 4) == pytest.approx(31.0)
    # 右端点放一根大柱：闭区间必须把它算进去。
    spike = _fake([0.0, 0.0, 0.0, 5.0])
    assert hist_area(spike, 0, 2) == pytest.approx(0.0)
    assert hist_area(spike, 0, 3) == pytest.approx(5.0)


def test_hist_area_rejects_bad_range():
    df = _fake([1.0, 2.0, 3.0])
    for i0, i1 in [(2, 1), (0, 3), (-1, 1), (0, -1), (3, 3)]:
        with pytest.raises(ValueError):
            hist_area(df, i0, i1)
    with pytest.raises(ValueError):
        hist_area(macd([]), 0, 0)
    with pytest.raises(ValueError):
        hist_area(pd.DataFrame({"dif": []}), 0, 0)


# ------------------------------------------------ 6. DIF 极值

def test_dif_high_low_locations():
    df = _fake([0.0] * 6, dif=[0.5, -0.2, 0.9, -0.7, 0.9, -1.2])
    assert dif_high(df, 0, 5) == (pytest.approx(0.9), 2)   # 并列取先出现者
    assert dif_low(df, 0, 5) == (pytest.approx(-1.2), 5)
    assert dif_high(df, 3, 5) == (pytest.approx(0.9), 4)
    assert dif_low(df, 0, 2) == (pytest.approx(-0.2), 1)
    assert dif_high(df, 1, 1) == (pytest.approx(-0.2), 1)
    # 与 numpy 的朴素实现一致（真实数据）。
    real = macd(REAL_CLOSES)
    v, i = dif_high(real, 3, 25)
    assert (v, i) == (pytest.approx(float(np.max(real["dif"].iloc[3:26]))),
                      3 + int(np.argmax(real["dif"].iloc[3:26].to_numpy())))
    for bad in [(2, 1), (0, 30), (-1, 2)]:
        with pytest.raises(ValueError):
            dif_high(real, *bad)
        with pytest.raises(ValueError):
            dif_low(real, *bad)


def test_dif_extreme_dispatches_by_direction():
    df = _fake([0.0] * 6, dif=[0.5, -0.2, 0.9, -0.7, 0.9, -1.2])
    assert dif_extreme(df, 0, 5, 1) == dif_high(df, 0, 5)
    assert dif_extreme(df, 0, 5, -1) == dif_low(df, 0, 5)
    with pytest.raises(ValueError):
        dif_extreme(df, 0, 5, 0)


# ------------------------------------------------ 7. 背驰判定

def test_divergence_requires_new_price_extreme():
    """价格没创新极值 → 一律不是背驰，即使面积明显缩小。"""
    # 向上：价格走平或更低，面积再小也不算背驰。
    assert is_divergence(10.0, 10.0, 5.0, 1.0, 1) is False
    assert is_divergence(10.0, 9.9, 5.0, 1.0, 1) is False
    # 向下：价格走平或更高，同样不算。
    assert is_divergence(10.0, 10.0, 5.0, 1.0, -1) is False
    assert is_divergence(10.0, 10.1, 5.0, 1.0, -1) is False
    # 方向反了也不算（向上段却去比新低）。
    assert is_divergence(10.0, 9.0, 5.0, 1.0, 1) is False
    # 只有真的创新极值 + 面积缩小才成立。
    assert is_divergence(10.0, 10.5, 5.0, 1.0, 1) is True
    assert is_divergence(10.0, 9.5, 5.0, 1.0, -1) is True


def test_divergence_requires_shrinking_area():
    """面积没缩小（相等或变大）→ 不背驰。严格小于才成立。"""
    assert is_divergence(10.0, 11.0, 5.0, 5.0, 1) is False    # 面积相等
    assert is_divergence(10.0, 11.0, 5.0, 6.0, 1) is False    # 面积放大
    assert is_divergence(10.0, 9.0, 5.0, 5.0, -1) is False
    assert is_divergence(10.0, 9.0, 5.0, 7.0, -1) is False
    assert is_divergence(10.0, 11.0, 5.0, 4.999, 1) is True
    assert is_divergence(10.0, 9.0, 5.0, 4.999, -1) is True
    # 零面积也符合「缩小」的语义边界：0 < 1 成立。
    assert is_divergence(10.0, 11.0, 1.0, 0.0, 1) is True


def test_divergence_rejects_bad_direction():
    for bad in (0, 2, -2, None, "up"):
        with pytest.raises(ValueError):
            is_divergence(10.0, 11.0, 5.0, 1.0, bad)
    with pytest.raises(ValueError):
        is_divergence(10.0, 11.0, -1.0, 1.0, 1)
    with pytest.raises(ValueError):
        is_divergence(10.0, 11.0, 1.0, -0.5, 1)


def test_divergence_integration_with_area_and_extremes():
    """把度量函数串起来：后一段价格创新低、MACD 面积缩小 → 背驰。"""
    df = _fake(hist=[5.0, 4.0, 3.0, 2.0, 1.0, 0.5],
               dif=[-1.0, -2.0, -3.0, -3.5, -3.4, -3.2])
    price_prev, price_now = 8.0, 7.5                 # 后一段创了新低
    area_prev = hist_area(df, 0, 2)                  # 12.0
    area_now = hist_area(df, 3, 5)                   # 3.5
    assert (area_prev, area_now) == (pytest.approx(12.0), pytest.approx(3.5))
    assert is_divergence(price_prev, price_now, area_prev, area_now, -1) is True
    # 价格没创新低时，同一组面积不构成背驰。
    assert is_divergence(price_now, price_now, area_prev, area_now, -1) is False
    # 向下段的 DIF 低点可由 dif_low 定位，用于圈定背驰段区间。
    assert dif_low(df, 3, 5) == (pytest.approx(-3.5), 3)
