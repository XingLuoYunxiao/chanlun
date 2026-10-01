"""单只票单周期的取数与落库（Task 30）。

看盘页的「同步这个周期」按钮和命令行 `python -m chanlun sync` 必须**共用同一份逻辑**：
增量起点算错会在 parquet 里留洞（缠论结构跨着洞算，比少几年历史危险），复权口径算错
会让指数被标成「前复权」（而除权参考价折算对指数根本不成立）。所以这里逐条钉住。

日历用**固定桩**：`_start_for` 的语义里「已是最新就跳过」依赖「今天」，跟着真实日期漂
的测试会在某个周末自己变红。
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pandas as pd
import pytest

from chanlun.config import load_config
from chanlun.data import meta, store, sync


class _FixedCal:
    """把"今天"钉在 2026-10-01（最后交易日 2026-09-30）。"""

    END = dt.date(2026, 9, 30)

    def last_trading_day(self, d) -> dt.date:  # noqa: ANN001 - 测试桩
        return self.END

    def next_trading_day(self, d) -> dt.date:  # noqa: ANN001
        table = {
            "2026-09-28": dt.date(2026, 9, 29),
            "2026-09-29": dt.date(2026, 9, 30),
            "2026-09-30": dt.date(2026, 10, 1),  # 超过 END → "已是最新"
        }
        key = str(d)[:10]
        if key not in table:
            raise AssertionError(f"测试桩没定义这个日期的下一交易日: {d}")
        return table[key]


CAL = _FixedCal()


def _bars(ts_list: list[str], base: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": ts_list,
            "open": [base + i for i in range(len(ts_list))],
            "high": [base + i + 0.5 for i in range(len(ts_list))],
            "low": [base + i - 0.5 for i in range(len(ts_list))],
            "close": [base + i + 0.2 for i in range(len(ts_list))],
            "volume": [1000.0 + i for i in range(len(ts_list))],
            "amount": [1e5 + i for i in range(len(ts_list))],
        }
    )


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(store, "DATA_ROOT", root)
    base = load_config()
    return dataclasses.replace(base, data=dataclasses.replace(base.data, root=root))


@pytest.fixture()
def conn(cfg):
    c = meta.init(cfg.data.meta_db)
    yield c
    c.close()


# ---------------- 增量起点 ----------------
def test_start_for_starts_from_the_beginning_when_local_is_empty(cfg):
    assert sync.start_for("600000", "day", False, CAL, CAL.END) == sync.FULL_START


def test_start_for_honours_since_only_for_a_code_without_local_data(cfg):
    since = dt.date(2026, 8, 1)
    assert sync.start_for("600000", "day", False, CAL, CAL.END, since) == "2026-08-01"


def test_start_for_is_incremental_once_local_data_exists(cfg):
    store.upsert("600000", "day", _bars(["2026-09-25", "2026-09-28"]))
    assert sync.start_for("600000", "day", False, CAL, CAL.END) == "2026-09-29"


def test_start_for_ignores_since_for_a_code_that_already_has_data(cfg):
    """`--since` 只作用于本地没有数据的票。

    若把起点强行压到 `since`，本地数据停在上古年份的票就会被跳过中间几年、
    在文件里留下空洞 —— 那比少几年历史危险得多。
    """
    store.upsert("600000", "day", _bars(["2026-09-25", "2026-09-28"]))
    since = dt.date(2026, 1, 1)
    assert sync.start_for("600000", "day", False, CAL, CAL.END, since) == "2026-09-29"


def test_start_for_returns_none_when_already_latest(cfg):
    store.upsert("600000", "day", _bars(["2026-09-30"]))
    assert sync.start_for("600000", "day", False, CAL, CAL.END) is None


def test_start_for_full_ignores_local_data(cfg):
    store.upsert("600000", "day", _bars(["2026-09-30"]))
    assert sync.start_for("600000", "day", True, CAL, CAL.END) == sync.FULL_START


# ---------------- 复权口径 ----------------
def test_adjust_for_forces_raw_on_an_index(cfg):
    """指数没有除权除息，落库必须写 `3`（不复权）。"""
    assert sync.adjust_for("sh.000001", cfg) == "3"
    assert sync.adjust_for("sh.000300", cfg) == "3"


def test_adjust_for_keeps_the_configured_mode_on_a_stock(cfg):
    assert sync.adjust_for("600000", cfg) == str(cfg.bs_adjust)


# ---------------- sync_one ----------------
def test_sync_one_writes_parquet_and_sync_state(cfg, conn, monkeypatch):
    seen: dict[str, object] = {}

    def fake_fetch(code, period, start, end, adjust="2"):  # noqa: ANN001
        seen.update(code=code, period=period, start=start, end=end, adjust=adjust)
        return _bars(["2026-09-29", "2026-09-30"])

    monkeypatch.setattr(sync, "fetch_bars", fake_fetch)
    out = sync.sync_one(conn, cfg, "600000", "day", cal=CAL)

    assert out.status == "ok", out.error
    assert out.rows == 2
    assert out.start_ts == "2026-09-29" and out.end_ts == "2026-09-30"
    assert seen["code"] == "sh.600000", "取数要用带市场前缀的代码"
    assert seen["start"] == sync.FULL_START
    assert seen["adjust"] == str(cfg.bs_adjust)

    stored = store.read("600000", "day")
    assert len(stored) == 2, "parquet 必须落在**落库键**上"
    assert store.path_for("600000", "day").exists()
    row = meta.get_sync(conn, "600000", "day")
    assert int(row["rows"]) == 2
    assert row["start_ts"] == "2026-09-29" and row["end_ts"] == "2026-09-30"
    assert row["error"] is None


def test_sync_one_records_an_empty_source_as_skipped(cfg, conn, monkeypatch):
    """真空区间是合法的：源返回零行不是错误，但必须记成 0 行。"""
    monkeypatch.setattr(sync, "fetch_bars", lambda *a, **k: _bars([]))
    out = sync.sync_one(conn, cfg, "600000", "day", cal=CAL)
    assert out.status == "skipped"
    assert out.rows == 0
    row = meta.get_sync(conn, "600000", "day")
    assert int(row["rows"]) == 0 and row["error"] is None


def test_sync_one_records_a_failure_without_raising(cfg, conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("baostock 断线了")

    monkeypatch.setattr(sync, "fetch_bars", boom)
    out = sync.sync_one(conn, cfg, "600000", "day", cal=CAL)
    assert out.status == "failed"
    assert "baostock 断线了" in out.error
    row = meta.get_sync(conn, "600000", "day")
    assert "baostock 断线了" in str(row["error"]), "失败原因必须留在库里，页面要显示它"


def test_sync_one_keeps_the_previous_rows_on_failure(cfg, conn, monkeypatch):
    """失败不能把已有数据说成 0 行：页面的「本地有多少根」跟着这个字段走。"""
    monkeypatch.setattr(sync, "fetch_bars", lambda *a, **k: _bars(["2026-09-28"]))
    sync.sync_one(conn, cfg, "600000", "day", cal=CAL)
    assert int(meta.get_sync(conn, "600000", "day")["rows"]) == 1

    def boom(*a, **k):
        raise RuntimeError("超时")

    monkeypatch.setattr(sync, "fetch_bars", boom)
    out = sync.sync_one(conn, cfg, "600000", "day", cal=CAL)
    assert out.status == "failed"
    assert int(meta.get_sync(conn, "600000", "day")["rows"]) == 1


def test_sync_one_records_raw_adjust_for_an_index(cfg, conn, monkeypatch):
    monkeypatch.setattr(sync, "fetch_bars", lambda *a, **k: _bars(["2026-09-30"]))
    out = sync.sync_one(conn, cfg, "sh.000001", "day", cal=CAL)
    assert out.status == "ok"
    assert out.adjust == "3"
    assert str(meta.get_sync(conn, "sh.000001", "day")["adjust"]) == "3"
    assert store.path_for("sh.000001", "day").exists(), "指数带前缀落库"


def test_sync_one_skips_without_touching_the_source_when_already_latest(cfg, conn, monkeypatch):
    store.upsert("600000", "day", _bars(["2026-09-30"]))
    called: list[int] = []
    monkeypatch.setattr(sync, "fetch_bars", lambda *a, **k: called.append(1) or _bars([]))
    out = sync.sync_one(conn, cfg, "600000", "day", cal=CAL)
    assert out.status == "skipped"
    assert called == [], "已是最新就不该再打网络"
