"""三态复权：口径归一化、因子阶梯、三态换算、反推。

判据来自设计文档 §3：库里只存不复权原始价 + 除权因子阶梯 `k_t`，其余口径读取时算。
两条硬性质：**最新价前复权 == 不复权**、**首日后复权 == 不复权**。
"""
from __future__ import annotations

import pandas as pd
import pytest

from chanlun.data import adjust, meta


def _frame(closes, start="2026-01-05"):
    ts = pd.date_range(start, periods=len(closes), freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame({
        "ts": ts, "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [100.0] * len(closes), "amount": [1e5] * len(closes),
    })


def _factors(ts, ks):
    return pd.DataFrame({"ts": ts, "k": ks})


def test_normalize_adjust_maps_baostock_codes():
    assert adjust.normalize_adjust("2") == "qfq"
    assert adjust.normalize_adjust("1") == "hfq"
    assert adjust.normalize_adjust("3") == "raw"
    assert adjust.normalize_adjust("none") == "raw"
    assert adjust.normalize_adjust("raw") == "raw"
    assert adjust.normalize_adjust("QFQ") == "qfq"
    assert adjust.normalize_adjust(None) == "qfq"


def test_normalize_adjust_rejects_unknown():
    with pytest.raises(ValueError, match="未知复权口径"):
        adjust.normalize_adjust("qfq2")


def test_raw_mode_is_identity():
    df = _frame([10.0, 5.0])
    f = _factors(["2026-01-05", "2026-01-06"], [0.5, 1.0])
    out = adjust.apply_adjust(df, f, "raw")
    assert list(out["close"]) == [10.0, 5.0]


def test_two_for_one_split_qfq_and_hfq():
    df = _frame([10.0, 5.0])
    f = _factors(["2026-01-05", "2026-01-06"], [0.5, 1.0])
    assert list(adjust.apply_adjust(df, f, "qfq")["close"]) == [5.0, 5.0]
    assert list(adjust.apply_adjust(df, f, "hfq")["close"]) == [10.0, 10.0]


def test_dividend_case_scales_ohlc_but_not_volume_or_amount():
    """分红除权：除权前 20 元、除权日 19 元（每股派 1 元）。

    前复权要把除权前的价格乘 `19/20 = 0.95`，于是序列变成 `[19, 19, 19.5]` ——
    除权跳空被抹平。注意因子段的边界必须落在**除权日那根 K 线**上（01-06），
    落在次日（01-07）就会留下一个假的 -5% 缺口。
    """
    df = _frame([20.0, 19.0, 19.5])
    f = _factors(["2026-01-05", "2026-01-06"], [0.95, 1.0])
    out = adjust.apply_adjust(df, f, "qfq")
    assert list(out["close"]) == [19.0, 19.0, 19.5]
    assert list(out["volume"]) == [100.0, 100.0, 100.0]
    assert list(out["amount"]) == [1e5, 1e5, 1e5]
    assert out["ts"].tolist() == df["ts"].tolist()
    assert list(out.columns) == list(df.columns)


def test_apply_adjust_does_not_mutate_input():
    df = _frame([10.0, 5.0])
    before = df.copy(deep=True)
    adjust.apply_adjust(df, _factors(["2026-01-05", "2026-01-06"], [0.5, 1.0]), "qfq")
    pd.testing.assert_frame_equal(df, before)


def test_no_factors_is_identity_in_every_mode():
    df = _frame([10.0, 11.0])
    for mode in adjust.MODES:
        out = adjust.apply_adjust(df, None, mode)
        assert list(out["close"]) == [10.0, 11.0]
        out2 = adjust.apply_adjust(df, adjust._empty_factors(), mode)
        assert list(out2["close"]) == [10.0, 11.0]


def test_qfq_latest_equals_raw_latest_and_hfq_first_equals_raw_first():
    df = _frame([7.0, 8.0, 9.0])
    f = _factors(["2026-01-05", "2026-01-06"], [0.5, 1.0])
    q = adjust.apply_adjust(df, f, "qfq")["close"]
    h = adjust.apply_adjust(df, f, "hfq")["close"]
    raw = df["close"]
    assert q.iloc[-1] == raw.iloc[-1]
    assert h.iloc[0] == raw.iloc[0]


def test_bar_before_first_factor_segment_uses_first_k():
    """因子表从中间开始时，更早的 K 线沿用**第一段**因子（不是最后一段）。

    这条用两段阶梯才测得出来：只有一段时，`k[0]` 与 `k[-1]` 是同一个数，
    「取错一端」根本看不出来。
    """
    df = _frame([3.0, 4.0, 5.0])
    f = _factors(["2026-01-06", "2026-01-07"], [0.5, 1.0])
    assert list(adjust.apply_adjust(df, f, "qfq")["close"]) == [1.5, 2.0, 5.0]


def test_price_is_rounded_to_kill_float_noise():
    """除权因子是浮点数，乘出来的尾巴必须收敛掉：`1.1 * 0.9 = 0.9900000000000001`。"""
    df = _frame([1.1])
    f = _factors(["2026-01-05"], [0.9])
    assert list(adjust.apply_adjust(df, f, "qfq")["close"]) == [0.99]


def test_factor_ladder_is_stepwise_and_sorted_input_agnostic():
    """因子按段起始日生效：段内恒定，且乱序输入结果不变（调用方不该被顺序绊倒）。"""
    df = _frame([10.0, 10.0, 10.0, 10.0])
    f = _factors(["2026-01-07", "2026-01-05"], [1.0, 0.5])  # 故意乱序
    out = adjust.apply_adjust(df, f, "qfq")["close"]
    assert list(out) == [5.0, 5.0, 10.0, 10.0]


def test_infer_factors_recovers_known_ladder():
    raw = _frame([10.0, 10.0, 5.0, 5.0])
    f = _factors(["2026-01-05", "2026-01-07"], [0.5, 1.0])
    qfq = adjust.apply_adjust(raw, f, "qfq")
    got = adjust.infer_factors(raw, qfq)
    assert list(got["k"]) == [0.5, 1.0]
    assert list(got["ts"]) == ["2026-01-05", "2026-01-07"]


def test_infer_factors_ignores_non_positive_raw_close():
    """停牌/异常数据里 close 可能为 0；拿它做除数会得到 inf，必须在源头挡掉。"""
    raw = _frame([10.0, 0.0, 10.0])
    qfq = _frame([5.0, 0.0, 5.0])
    got = adjust.infer_factors(raw, qfq)
    assert list(got["k"]) == [0.5]
    assert got["k"].notna().all()


def test_infer_factors_then_apply_round_trips_qfq():
    raw = _frame([10.0, 10.0, 5.0, 5.0])
    qfq = _frame([5.0, 5.0, 5.0, 5.0])
    got = adjust.infer_factors(raw, qfq)
    out = adjust.apply_adjust(raw, got, "qfq")["close"]
    assert list(out) == list(qfq["close"])


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="未知复权口径"):
        adjust.apply_adjust(_frame([1.0]), None, "qfq2")


# --- meta.adjust_factor 表 ---


def test_adjust_factor_table_round_trip(tmp_path):
    conn = meta.init(tmp_path / "meta.db")
    meta.save_adjust_factors(conn, "600000", [("2026-01-05", 0.5), ("2026-01-07", 1.0)])
    got = meta.get_adjust_factors(conn, "600000")
    assert list(got.columns) == ["ts", "k"]
    assert list(got["ts"]) == ["2026-01-05", "2026-01-07"]
    assert list(got["k"]) == [0.5, 1.0]
    assert list(meta.get_adjust_factors(conn, "000001")["k"]) == []


def test_save_adjust_factors_is_upsert_not_duplicate(tmp_path):
    conn = meta.init(tmp_path / "meta.db")
    meta.save_adjust_factors(conn, "600000", [("2026-01-05", 0.5)])
    meta.save_adjust_factors(conn, "600000", [("2026-01-05", 0.25), ("2026-01-07", 1.0)])
    got = meta.get_adjust_factors(conn, "600000")
    assert list(got["ts"]) == ["2026-01-05", "2026-01-07"]
    assert list(got["k"]) == [0.25, 1.0]


def test_adjust_factors_are_per_code(tmp_path):
    conn = meta.init(tmp_path / "meta.db")
    meta.save_adjust_factors(conn, "600000", [("2026-01-05", 0.5)])
    meta.save_adjust_factors(conn, "000001", [("2026-01-05", 0.9)])
    assert list(meta.get_adjust_factors(conn, "600000")["k"]) == [0.5]
    assert list(meta.get_adjust_factors(conn, "000001")["k"]) == [0.9]


def test_adjust_factors_record_source(tmp_path):
    conn = meta.init(tmp_path / "meta.db")
    meta.save_adjust_factors(conn, "600000", [("2026-01-05", 0.5)], source="inferred")
    row = conn.execute("SELECT source FROM adjust_factor WHERE code='600000'").fetchone()
    assert row["source"] == "inferred"


# ---------------- 前复权落库的还原（一期 baostock 的 day 就是这个口径） ----------------
def test_unapply_raw_inverts_apply_qfq():
    """往返恒等：`apply` 算出的前复权价，`unapply` 必须能还原回原始价。

    这是两条公式互为逆运算的直接证据 —— 库里落的是前复权价时，
    只有走 `unapply` 才拿得到不复权，走 `apply` 会把价格乘第二遍。
    """
    df = _frame([7.0, 8.0, 9.0])
    f = _factors(["2026-01-05", "2026-01-06"], [0.5, 1.0])
    q = adjust.apply_adjust(df, f, "qfq")
    back = adjust.unapply_adjust(q, f, "raw")
    assert list(back["close"]) == list(df["close"])


def test_unapply_hfq_equals_apply_hfq():
    """后复权与落库口径无关：`q_t / k_0` 与 `raw_t × k_t / k_0` 是同一个数。"""
    df = _frame([7.0, 8.0, 9.0])
    f = _factors(["2026-01-05", "2026-01-06"], [0.5, 1.0])
    q = adjust.apply_adjust(df, f, "qfq")
    assert list(adjust.unapply_adjust(q, f, "hfq")["close"]) == list(adjust.apply_adjust(df, f, "hfq")["close"])


def test_unapply_qfq_is_identity_and_keeps_volume():
    df = _frame([7.0, 8.0, 9.0])
    f = _factors(["2026-01-05", "2026-01-06"], [0.5, 1.0])
    out = adjust.unapply_adjust(df, f, "qfq")
    assert list(out["close"]) == [7.0, 8.0, 9.0]
    assert list(out["volume"]) == [100.0] * 3 and list(out["amount"]) == [1e5] * 3


def test_unapply_without_factors_is_identity():
    df = _frame([7.0, 8.0])
    assert list(adjust.unapply_adjust(df, None, "raw")["close"]) == [7.0, 8.0]
    assert list(adjust.unapply_adjust(df, _factors([], []), "hfq")["close"]) == [7.0, 8.0]


def test_unapply_does_not_mutate_input():
    df = _frame([7.0, 8.0, 9.0])
    f = _factors(["2026-01-05", "2026-01-06"], [0.5, 1.0])
    adjust.unapply_adjust(df, f, "raw")
    assert list(df["close"]) == [7.0, 8.0, 9.0]
