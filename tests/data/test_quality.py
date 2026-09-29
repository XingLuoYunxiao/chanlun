"""复权哨兵校验测试：两条真实网络用例 + 网络异常降级用例。"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest
import requests

from chanlun.config import Config, DataConfig
from chanlun.data import meta, quality
from chanlun.data.quality import CheckResult, check_cross_source, check_latest_equals_actual, run_all


def _frame(start: str = "2024-01-02", n: int = 5, close: float = 10.0) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": [close] * n,
            "high": [close] * n,
            "low": [close] * n,
            "close": [close] * n,
            "volume": [1.0] * n,
            "amount": [1.0] * n,
        }
    )


# ---------------- 真实网络 ----------------

def test_latest_adjusted_equals_unadjusted():
    r = check_latest_equals_actual("sh.600000")
    assert r.ok, r.detail
    assert r.kind == "latest_equals_actual"
    assert r.max_rel_err < 0.005


def test_cross_source_within_tolerance():
    """真实网络：baostock 前复权 vs 外部源（东财，不可达时腾讯 qfq），误差 < 0.5%。

    窗口取最近一个月：各家的「前复权」口径不同，价格水平会随距今时间累积漂移
    （实测 6 只票最坏 30 天 0.07% / 60 天 0.21% / 180 天 0.67%），
    长窗口下跨源比对测的是厂商口径差异而非数据错误。
    本地 HTTP 代理会突发失效，故东财失败时由腾讯（代理→直连）顶上。
    """
    end = dt.date.today()
    start = end - dt.timedelta(days=quality.CROSS_SOURCE_WINDOW_DAYS)
    r = check_cross_source("sh.600000", start.isoformat(), end.isoformat())
    assert r.ok, r.detail
    assert r.kind == "cross_source"
    assert r.max_rel_err < 0.005


# ---------------- 网络异常降级（哨兵不得中断流水线） ----------------

@pytest.fixture()
def fake_baostock(monkeypatch):
    monkeypatch.setattr(quality, "fetch_bars", lambda *a, **k: _frame())


def test_cross_source_all_sources_empty_is_fail_not_raise(monkeypatch, fake_baostock):
    monkeypatch.setattr(quality, "_eastmoney_kline", lambda *a, **k: [])
    monkeypatch.setattr(quality, "_tencent_kline", lambda *a, **k: [])
    r = check_cross_source("sh.600000", "2024-01-01", "2024-06-30")
    assert r.ok is False
    assert "东财" in r.detail and ("空" in r.detail or "腾讯" in r.detail)


def test_cross_source_timeout_is_fail_not_raise(monkeypatch, fake_baostock):
    def _timeout(*a, **k):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(quality, "_eastmoney_kline", _timeout)
    monkeypatch.setattr(quality, "_tencent_kline", _timeout)
    r = check_cross_source("sh.600000", "2024-01-01", "2024-06-30")
    assert r.ok is False
    assert "东财" in r.detail and "超时" in r.detail


def test_cross_source_falls_back_to_tencent(monkeypatch, fake_baostock):
    """实测本地代理突发失效导致东财不可达；腾讯前复权直连应能顶上。"""
    def _boom(*a, **k):
        raise requests.exceptions.ProxyError("proxy down")

    monkeypatch.setattr(quality, "_eastmoney_kline", _boom)
    frame = _frame()
    monkeypatch.setattr(
        quality, "_tencent_kline",
        lambda code, start, end, **k: [(d, 10.0) for d in frame["ts"]],
    )
    r = check_cross_source("sh.600000", "2024-01-01", "2024-06-30")
    assert r.ok is True, r.detail
    assert "腾讯" in r.detail
    assert r.max_rel_err < 0.005


# ---------------- 腾讯前复权解析 ----------------

_TENCENT_PAYLOAD = {
    "code": 0,
    "data": {
        "sh600000": {
            "qfqday": [
                ["2024-01-02", "5.479", "5.449", "5.499", "5.449", "220667.000"],
                ["2024-01-03", "", "", "", "", ""],
                ["2024-01-04", "5.489", "5.469", "5.499", "5.439", "1"],
            ],
            "qt": {},
        }
    },
}


def test_tencent_kline_parses_qfqday_and_skips_bad_rows(monkeypatch):
    seen: dict = {}

    def fake_get(url, *, params=None, direct=False, what=""):
        seen.update(url=url, param=params["param"], direct=direct)
        return _TENCENT_PAYLOAD

    monkeypatch.setattr(quality, "_http_get_json", fake_get)
    rows = quality._tencent_kline("sh.600000", "2024-01-01", "2024-06-30", direct=True)
    assert rows == [("2024-01-02", 5.449), ("2024-01-04", 5.469)]
    assert seen["url"] == quality.TENCENT_URL
    assert seen["param"].startswith("sh600000,day,2024-01-01,2024-06-30,")
    assert seen["param"].endswith(",qfq")
    assert seen["direct"] is True


def test_tencent_kline_falls_back_to_day_key(monkeypatch):
    payload = {"data": {"sh600000": {"day": [["2024-01-02", "1", "5.0", "6", "4", "1"]]}}}
    monkeypatch.setattr(quality, "_http_get_json", lambda *a, **k: payload)
    assert quality._tencent_kline("sh.600000", "2024-01-01", "2024-06-30") == [("2024-01-02", 5.0)]


def test_tencent_kline_unknown_symbol_is_empty(monkeypatch):
    payload = {"data": {"sh999999": {"day": [], "qt": {}}}}
    monkeypatch.setattr(quality, "_http_get_json", lambda *a, **k: payload)
    assert quality._tencent_kline("sh.999999", "2024-01-01", "2024-06-30") == []


def test_latest_check_baostock_failure_is_fail_not_raise(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("baostock down")

    monkeypatch.setattr(quality, "fetch_bars", _boom)
    r = check_latest_equals_actual("sh.600000")
    assert r.ok is False
    assert "baostock down" in r.detail


# ---------------- run_all 落盘 ----------------

def test_run_all_writes_report_and_quality_flags(tmp_path, monkeypatch):
    cfg = Config(data=DataConfig(root=tmp_path / "data"))
    monkeypatch.setattr(quality, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(
        quality, "check_latest_equals_actual",
        lambda code, period="day": CheckResult(code, "latest_equals_actual", True, "ok", 0.0),
    )
    monkeypatch.setattr(
        quality, "check_cross_source",
        lambda code, start, end: CheckResult(code, "cross_source", True, "ok", 0.001),
    )

    results = run_all(codes=["sh.600000", "sz.000001"], n_sample=20, seed=42)
    assert len(results) == 4

    report = json.loads((cfg.data.root / "quality_report.json").read_text(encoding="utf-8"))
    kinds = {r["kind"] for r in report["results"]}
    assert kinds == {"latest_equals_actual", "cross_source"}
    assert all({"code", "kind", "ok", "detail", "max_rel_err"} <= set(r) for r in report["results"])

    conn = meta.init(cfg.data.meta_db)
    assert len(meta.failing_quality(conn)) == 0
    conn.close()


def test_run_all_samples_reproducibly(tmp_path, monkeypatch):
    cfg = Config(data=DataConfig(root=tmp_path / "data"))
    monkeypatch.setattr(quality, "load_config", lambda *a, **k: cfg)

    pool = [{"bs_code": f"sh.{600000 + i}"} for i in range(50)]
    monkeypatch.setattr(quality.meta, "get_universe", lambda *a, **k: pool)

    seen: list[str] = []

    def _latest(code, period="day"):
        seen.append(code)
        return CheckResult(code, "latest_equals_actual", True, "ok", 0.0)

    monkeypatch.setattr(quality, "check_latest_equals_actual", _latest)
    monkeypatch.setattr(
        quality, "check_cross_source",
        lambda code, start, end: CheckResult(code, "cross_source", True, "ok", 0.0),
    )

    run_all(n_sample=5, seed=42)
    first = list(seen)
    seen.clear()
    run_all(n_sample=5, seed=42)
    assert first == seen
    assert len(set(first)) == 5
    assert set(first) <= {r["bs_code"] for r in pool}


def test_cross_source_long_old_window_exposes_vendor_drift():
    """刻画性测试：长窗口 / 远期窗口下，跨源比对测出的是厂商复权口径差异。

    计划文档示例用 2024-01-01~2024-06-30，实测 baostock 与东财/腾讯在该窗口
    的最大相对误差约 8%（2024-07-18 除息双方都正确扣除了跳空，但基准因子水平不同），
    因此该窗口下 FAIL 属于预期行为，哨兵默认不再使用这种窗口。
    """
    r = check_cross_source("sh.600000", "2024-01-01", "2024-06-30")
    if r.max_rel_err == 0.0 and "不可用" in r.detail:
        pytest.skip(f"外部源不可达: {r.detail}")
    assert r.ok is False, r.detail
    assert r.max_rel_err > 0.05, r.detail
