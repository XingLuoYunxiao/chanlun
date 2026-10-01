"""除权因子入库的测试（全部离线：合成 `.day` + 合成前复权行情）。

这里刻意不联网、也不碰 `data/`：因子反推要同时读「通达信原始价」和「库内前复权价」，
两份数据都得是测试自己造的，否则「反推对不对」就变成了「生产数据对不对」。
"""

from __future__ import annotations

import struct

import pandas as pd
import pytest

from chanlun.data import factors, meta, store


def _rec(date: int, close_cents: int) -> bytes:
    return struct.pack(
        "<IIIIIfII", date, close_cents, close_cents, close_cents, close_cents, 1e6, 1000, 0
    )


def _write_day(root, market: str, code: str, rows) -> None:
    d = root / market / "lday"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{market}{code}.day").write_bytes(b"".join(_rec(d, c) for d, c in rows))


def _frame(closes, start="2026-01-05"):
    ts = pd.date_range(start, periods=len(closes), freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame({
        "ts": ts, "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [100.0] * len(closes), "amount": [1e5] * len(closes),
    })


def _days(n: int, start=20260105):
    return [int(d.strftime("%Y%m%d")) for d in pd.date_range(str(start), periods=n, freq="D")]


# 24 根：前 12 根原始价 10.00 元、后 12 根 5.00 元（2 拆 1）。
# 前复权把除权前的那段乘 0.5，于是整条前复权序列恒为 5.00 —— 阶梯应为 2 段：首日 k=0.5、除权日 k=1.0。
SPLIT_DAY = 12
RAW_CENTS = [1000] * SPLIT_DAY + [500] * SPLIT_DAY
QFQ_PRICE = [5.0] * (2 * SPLIT_DAY)


@pytest.fixture()
def conn(tmp_path):
    return meta.init(tmp_path / "meta.db")


@pytest.fixture()
def raw_root(tmp_path):
    root = tmp_path / "tdx_raw"
    _write_day(root, "sh", "600030", zip(_days(len(RAW_CENTS)), RAW_CENTS))
    return root


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """行情根是 `store.DATA_ROOT` 这个模块级全局 —— 不隔离就会写进生产 `data/`。"""
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path / "store")


def _seed_qfq(code="600030", prices=None, conn=None, adjust="2"):
    """写库内行情并记录落库口径。一期 `day` 就是 baostock 前复权（`adjust='2'`），
    不记这一笔的话反推会以为库里是不复权价。"""
    store.write(code, "day", _frame(prices if prices is not None else QFQ_PRICE))
    if conn is not None:
        meta.set_sync(conn, code, "day", None, None, 0, adjust)


# ---------------- store key → .day 文件 ----------------
def test_tdx_day_path_resolves_store_keys(raw_root):
    _write_day(raw_root, "sz", "000001", [(20260102, 1000)])
    _write_day(raw_root, "sh", "000300", [(20260102, 1000)])  # 指数：store key 带前缀
    assert factors.tdx_day_path(raw_root, "600030") == raw_root / "sh" / "lday" / "sh600030.day"
    assert factors.tdx_day_path(raw_root, "000001") == raw_root / "sz" / "lday" / "sz000001.day"
    assert factors.tdx_day_path(raw_root, "sh.000300") == raw_root / "sh" / "lday" / "sh000300.day"


def test_tdx_day_path_reports_missing(raw_root):
    assert factors.tdx_day_path(raw_root, "999999") is None


def test_tdx_day_path_wont_cross_markets(tmp_path):
    """`600030` 是上交所的票。同名文件落在 sz 目录里也不认 —— 认了就会拿错市场的原始价去反推因子。"""
    root = tmp_path / "cross"
    _write_day(root, "sz", "600030", [(20260102, 1000)])
    assert factors.tdx_day_path(root, "600030") is None
    _write_day(root, "sh", "600030", [(20260102, 1000)])
    assert factors.tdx_day_path(root, "600030") == root / "sh" / "lday" / "sh600030.day"


# ---------------- 反推并入库 ----------------
def test_build_one_writes_inferred_ladder(conn, raw_root):
    _seed_qfq(conn=conn)
    res = factors.build_one(conn, "600030", tdx_root=raw_root)
    assert res.status == "written" and res.segments == 2 and res.overlap == 2 * SPLIT_DAY
    got = meta.get_adjust_factors(conn, "600030")
    days = _days(len(RAW_CENTS))
    assert list(got["ts"]) == [str(pd.to_datetime(str(days[0])).date()),
                              str(pd.to_datetime(str(days[SPLIT_DAY])).date())]
    assert list(got["k"]) == [0.5, 1.0]
    assert res.first_ts == got["ts"].iloc[0]
    src = conn.execute("SELECT DISTINCT source FROM adjust_factor").fetchall()
    assert [r["source"] for r in src] == ["inferred"]


def test_build_one_is_upsert_not_duplicate(conn, raw_root):
    _seed_qfq(conn=conn)
    factors.build_one(conn, "600030", tdx_root=raw_root)
    factors.build_one(conn, "600030", tdx_root=raw_root)
    assert len(meta.get_adjust_factors(conn, "600030")) == 2


def test_build_one_dry_run_writes_nothing(conn, raw_root):
    _seed_qfq(conn=conn)
    res = factors.build_one(conn, "600030", tdx_root=raw_root, dry_run=True)
    assert res.status == "dry_run" and res.segments == 2
    assert meta.get_adjust_factors(conn, "600030").empty
    assert meta.all_adjust_codes(conn) == []


def test_build_one_reports_missing_raw(conn, tmp_path):
    _seed_qfq(conn=conn)
    res = factors.build_one(conn, "600030", tdx_root=tmp_path / "empty")
    assert res.status == "no_raw" and res.segments == 0
    assert meta.get_adjust_factors(conn, "600030").empty


def test_build_one_reports_missing_qfq(conn, raw_root):
    res = factors.build_one(conn, "600030", tdx_root=raw_root)
    assert res.status == "no_qfq"
    assert meta.get_adjust_factors(conn, "600030").empty


def test_build_one_reports_thin_overlap_without_writing(conn, raw_root):
    """重叠太少时反推出来的阶梯是噪声：宁可不写，也不能写一条假的除权日。"""
    _seed_qfq(prices=[5.0] * 3 + [4.0] * 3, conn=conn)  # 只有 6 根重叠，且价格关系不成立
    res = factors.build_one(conn, "600030", tdx_root=raw_root, min_overlap=20)
    assert res.status == "thin_overlap" and res.overlap == 6
    assert meta.get_adjust_factors(conn, "600030").empty


def test_build_one_no_change_when_prices_agree(conn, raw_root):
    """两份数据逐笔同价 = 这段区间没除权，不该写出一段恒为 1 的阶梯。"""
    _seed_qfq(prices=[p / 100 for p in RAW_CENTS], conn=conn)
    res = factors.build_one(conn, "600030", tdx_root=raw_root)
    assert res.status == "no_change" and res.segments == 0
    assert meta.get_adjust_factors(conn, "600030").empty


def test_build_one_ignores_non_positive_raw_close(conn, raw_root):
    """停牌等异常数据的 0 价做除数会得到 inf，一个 inf 因子能把整段价格污染成 inf。"""
    _write_day(raw_root, "sh", "600030", zip(_days(len(RAW_CENTS)),
                                             [1000] * 2 + [0] + [1000] * (SPLIT_DAY - 3)
                                             + [500] * SPLIT_DAY))
    _seed_qfq(conn=conn)
    res = factors.build_one(conn, "600030", tdx_root=raw_root)
    assert res.status == "written"
    assert list(meta.get_adjust_factors(conn, "600030")["k"]) == [0.5, 1.0]

def test_build_one_refuses_when_store_is_no_longer_qfq(conn, raw_root):
    """`day` 切到通达信整包之后库里就是不复权价：这时反推会把 k 全算成 1，
    拿它覆盖掉真因子等于把三态复权悄悄降级成单态。宁可拒绝。"""
    _seed_qfq(conn=conn, adjust="3")  # 不复权落库
    res = factors.build_one(conn, "600030", tdx_root=raw_root)
    assert res.status == "stored_not_qfq" and "不是前复权" in res.note
    assert meta.get_adjust_factors(conn, "600030").empty


def test_stored_adjust_falls_back_to_raw(conn, raw_root):
    """没有 sync_state 记录时按不复权处理 —— 这是保守的一侧：不会拿两份不复权价去相除。"""
    _seed_qfq(conn=None)
    res = factors.build_one(conn, "600030", tdx_root=raw_root)
    assert res.status == "stored_not_qfq"
