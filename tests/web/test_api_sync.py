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
