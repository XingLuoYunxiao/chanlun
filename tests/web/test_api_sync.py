"""「同步这个周期」按钮的接口契约（Task 31）。

按钮按下去要等 30 秒（30分）到 2 分半（5分，实测 600759 拉回 78432 根用了 149.4 秒），
所以接口**不能**同步等待 —— 浏览器会挂死。契约是：

1. `POST /api/sync` 立刻返回（202），真正的取数在后台线程里；
2. 同一只票同一个周期在跑的时候连点，**不会**叠加第二个任务；
3. `GET /api/sync/status` 如实汇报 `running/done/skipped/error`，失败原因必须带出来；
4. 派生周期（周/月）映射到日线：周线是本地聚合出来的，同步 `week` 落不了库；
5. 指数 + 分钟直接拒绝：baostock 对指数不返回分钟线（实测 `sh.000001`/`sh.000300`/
   `sh.000688` 的 30 分都是 0 行），与其让用户等 30 秒看 0 根，不如立刻说清。
"""

from __future__ import annotations

import dataclasses
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chanlun.config import load_config
from chanlun.data import meta, store, sync as sync_mod
from chanlun.web import api as api_mod
from chanlun.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
INDEX = "sh.000001"


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(store, "DATA_ROOT", root)
    base = load_config()
    return dataclasses.replace(base, data=dataclasses.replace(base.data, root=root))


@pytest.fixture()
def client(cfg):
    api_mod.clear_cache()
    api_mod.clear_sync_tasks()
    yield TestClient(create_app(cfg))
    api_mod.clear_sync_tasks()


def _wait(client, code, period, *, timeout=5.0):
    """轮询到不再 running（或超时）。返回最后一次响应体。"""
    deadline = time.time() + timeout
    body = {}
    while time.time() < deadline:
        body = client.get("/api/sync/status", params={"code": code, "period": period}).json()
        if body["state"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError(f"同步一直没结束: {body}")


def test_sync_runs_in_the_background_and_reports_done(client, monkeypatch):
    seen: dict[str, object] = {}

    def fake(conn, cfg, code, period, **kw):
        seen.update(code=code, period=period, kw=kw)
        return sync_mod.SyncOneResult(code, period, "ok", rows=13072,
                                      start_ts="2020-01-02 10:00",
                                      end_ts="2026-09-30 15:00", adjust="2")

    monkeypatch.setattr(sync_mod, "sync_one", fake)
    resp = client.post("/api/sync", json={"code": "600759", "period": "30"})
    assert resp.status_code == 202, resp.text
    body = _wait(client, "600759", "30")
    assert body["state"] == "done"
    assert body["rows"] == 13072
    assert body["end_ts"] == "2026-09-30 15:00"
    assert body["error"] == ""
    assert seen["code"] == "600759" and seen["period"] == "30"
    assert body["elapsed"] >= 0


def test_sync_is_idempotent_while_running(client, monkeypatch):
    gate = threading.Event()
    calls: list[int] = []

    def slow(conn, cfg, code, period, **kw):
        calls.append(1)
        gate.wait(5)
        return sync_mod.SyncOneResult(code, period, "ok", rows=1)

    monkeypatch.setattr(sync_mod, "sync_one", slow)
    first = client.post("/api/sync", json={"code": "600759", "period": "30"})
    assert first.status_code == 202
    try:
        # 后台线程可能还没跑到打桩函数，先等它真的进去，否则下面的断言是竞态
        deadline = time.time() + 5
        while not calls and time.time() < deadline:
            time.sleep(0.01)
        second = client.post("/api/sync", json={"code": "600759", "period": "30"})
        assert second.status_code == 202, "重复点不该报错，要返回同一个任务"
        assert second.json()["started_at"] == first.json()["started_at"]
        assert calls == [1], "同一只票同一个周期不能同时跑两个任务"
    finally:
        # 断言失败也要放行，否则这个线程会一直占着全局同步锁，把后面的测试全拖红
        gate.set()
    assert _wait(client, "600759", "30")["state"] == "done"


def test_sync_can_rerun_after_it_finished(client, monkeypatch):
    """失败后按钮要能「重试」，所以跑完的任务不能把入口焊死。"""
    calls: list[int] = []

    def once(conn, cfg, code, period, **kw):
        calls.append(1)
        return sync_mod.SyncOneResult(code, period, "failed", error="baostock 断线了")

    monkeypatch.setattr(sync_mod, "sync_one", once)
    client.post("/api/sync", json={"code": "600759", "period": "30"})
    assert _wait(client, "600759", "30")["state"] == "error"
    client.post("/api/sync", json={"code": "600759", "period": "30"})
    assert _wait(client, "600759", "30")["state"] == "error"
    assert calls == [1, 1], "第二次点击要真的再跑一次"


def test_sync_maps_a_derived_period_to_day(client, monkeypatch):
    seen: list[str] = []

    def fake(conn, cfg, code, period, **kw):
        seen.append(period)
        return sync_mod.SyncOneResult(code, period, "ok", rows=2)

    monkeypatch.setattr(sync_mod, "sync_one", fake)
    resp = client.post("/api/sync", json={"code": "600000", "period": "week"})
    assert resp.status_code == 202, resp.text
    assert resp.json()["period"] == "day"
    assert resp.json()["requested"] == "week"
    body = _wait(client, "600000", "day")
    assert body["state"] == "done"
    assert seen == ["day"], "周线是本地聚合出来的，不能去同步 week"


def test_sync_refuses_minute_periods_on_an_index(client, monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(sync_mod, "sync_one", lambda *a, **k: called.append(1))
    resp = client.post("/api/sync", json={"code": INDEX, "period": "30"})
    assert resp.status_code == 400, resp.text
    assert "指数" in resp.json()["detail"]
    assert called == [], "拒绝要发生在取数之前，不能空转 30 秒"


def test_sync_still_allows_daily_bars_on_an_index(client, monkeypatch):
    monkeypatch.setattr(
        sync_mod, "sync_one",
        lambda conn, cfg, code, period, **kw: sync_mod.SyncOneResult(code, period, "ok", rows=5),
    )
    resp = client.post("/api/sync", json={"code": INDEX, "period": "day"})
    assert resp.status_code == 202, resp.text
    assert _wait(client, INDEX, "day")["state"] == "done"


@pytest.mark.parametrize("period", ["1", "d", "DAY", ""])
def test_sync_rejects_an_unknown_period(client, period):
    resp = client.post("/api/sync", json={"code": "600000", "period": period})
    assert resp.status_code == 400, resp.text
    assert "周期" in resp.json()["detail"]


@pytest.mark.parametrize("code", ["abc", "sh.300059", "", "60000000"])
def test_sync_rejects_a_malformed_code(client, code):
    resp = client.post("/api/sync", json={"code": code, "period": "day"})
    assert resp.status_code == 400, resp.text


def test_sync_status_is_idle_before_anything_ran(client):
    body = client.get("/api/sync/status", params={"code": "600000", "period": "day"}).json()
    assert body["state"] == "idle"
    assert body["rows"] == 0


def test_sync_status_rejects_an_unknown_period(client):
    resp = client.get("/api/sync/status", params={"code": "600000", "period": "1"})
    assert resp.status_code == 400


def test_sync_reports_a_failure(client, monkeypatch):
    monkeypatch.setattr(
        sync_mod, "sync_one",
        lambda conn, cfg, code, period, **kw: sync_mod.SyncOneResult(
            code, period, "failed", error="baostock 断线了"
        ),
    )
    client.post("/api/sync", json={"code": "600759", "period": "5"})
    body = _wait(client, "600759", "5")
    assert body["state"] == "error"
    assert "baostock 断线了" in body["error"]


def test_sync_reports_an_empty_source_as_skipped(client, monkeypatch):
    monkeypatch.setattr(
        sync_mod, "sync_one",
        lambda conn, cfg, code, period, **kw: sync_mod.SyncOneResult(code, period, "skipped"),
    )
    client.post("/api/sync", json={"code": "600759", "period": "30"})
    body = _wait(client, "600759", "30")
    assert body["state"] == "skipped"
    assert body["rows"] == 0


# ---------------- 404 时要如实说「这个周期到底能不能补」 ----------------
def test_structure_404_says_an_index_minute_period_can_never_be_synced(client, cfg):
    """指数 + 分钟：404 里必须带 `syncable=false` 和原因。

    自选池现在全是指数，切到 30分/5分 必然 404。这时如果只回一句「请先同步」，
    页面就会给出一个**注定失败**的「同步这个周期」按钮 —— 点下去 400，白等一次往返，
    而且等于骗用户"能补"。
    """
    resp = client.get("/api/structure", params={"code": INDEX, "period": "30"})
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["syncable"] is False
    assert "指数" in body["sync_hint"]
    # 不许再让用户去跑一条注定空跑的命令
    assert "请先同步" not in body["detail"]


def test_structure_404_still_offers_a_sync_for_a_stock_without_minute_data(client, cfg):
    """股票没分钟数据是**能补**的（实测 600000 30分 拉回 13088 根）—— 这时要给按钮。"""
    resp = client.get("/api/structure", params={"code": "600759", "period": "30"})
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["syncable"] is True
    assert body["sync_hint"] == ""
    assert "请先同步" in body["detail"]


def test_structure_404_on_an_index_daily_still_offers_a_sync(client, cfg):
    """日线是指数**能拿**的（走 baostock 或本地通达信整包），不能连这个也拒了。"""
    body = client.get("/api/structure", params={"code": INDEX, "period": "day"}).json()
    assert body["syncable"] is True
    assert body["sync_hint"] == ""


def test_the_404_hint_and_the_sync_rejection_are_the_same_sentence(client, cfg):
    """两处口径必须同源：否则页面说「可以补」，点下去接口说「补不了」。"""
    hint = client.get("/api/structure", params={"code": INDEX, "period": "30"}).json()["sync_hint"]
    post = client.post("/api/sync", json={"code": INDEX, "period": "30"})
    assert post.status_code == 400, post.text
    assert hint in post.json()["detail"]

