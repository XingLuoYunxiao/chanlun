"""F2：结构是**历史**的属性，不是**可见窗口**的属性。

「根数」只该决定看得见多少，不该决定怎么划分。改动前 `snapshot_of` 先按 `limit`
截断再喂引擎，于是同一个交易日、同一只票，切一下根数就换一套划分：

- `limit=300` 的首段是 `2023-10-12 → 2024-01-18`（confirmed），
  而这个划分在 1212 根全量里**根本不存在**；
- 全量的 2 个中枢、1 个买卖点（`s3 @ 2022-01-13`）在短窗里全被切掉，
  默认 1200 根只显示全市场 45 个买卖点里的 17 个。

spec 第 123 行已经为价格定过同一条原则（某一天的后复权价是该日期的属性，与可见窗口无关）。
价格如此，结构也必须如此。这里把那条原则钉成回归：

1. **划分不随窗口变** —— 短窗里出现的每一段，必须与全量里的同 `ts` 段逐字一致；
2. **裁剪只影响看得见多少** —— 可见结构 = 全量结构 ∩ 窗口，不多不少；
3. **MACD 也走全量** —— 引擎看全量、页面看窗口算的 MACD，两边会分叉（实测最大差
   0.0958），那就等于拿一条和买卖点无关的 MACD 去解释背驰；
4. **画出来的标记必须落在返回的 K 线里** —— 前端 x 轴是 category（`data: ts`），
   坐标不在轴上会被静默丢弃或错位。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from chanlun.config import load_config
from chanlun.data import meta, store
from chanlun.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
BARS = ROOT / "tests" / "chan" / "fixtures" / "bars.parquet"
CODE = "600000"
#: 样本 1212 根（2020-01-02 → 2024-12-31）全量划分的实测值。断言写死这些数字，
#: 是为了让「窗口一变、划分就变」真的能把测试打红，而不是靠不变量空转。
FULL_BARS = 1212
FULL_SEGMENTS = 12
FULL_PIVOTS = 2
FULL_SIGNAL_TS = "2022-01-13"


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(store, "DATA_ROOT", root)
    base = load_config()
    conf = dataclasses.replace(base, data=dataclasses.replace(base.data, root=root))
    df = pd.read_parquet(BARS)
    g = df[df["code"] == f"sh.{CODE}"].drop(columns=["code"])
    store.upsert(CODE, "day", g)
    conn = meta.init(conf.data.meta_db)
    meta.upsert_universe(conn, [(CODE, f"sh.{CODE}", "样本", "sh", "", 0)])
    conn.close()
    return conf


@pytest.fixture()
def client(cfg):
    with TestClient(create_app(cfg)) as c:
        yield c


def _structure(client, limit: int) -> dict:
    r = client.get(f"/api/structure?code={CODE}&period=day&limit={limit}")
    assert r.status_code == 200, r.text
    return r.json()


def _seg_key(s: dict) -> tuple:
    """线段的身份 = 两端分型的 ts + 状态。`idx` 会随窗口重排，不能当身份。"""
    return (s["start"]["start"]["ts"], s["end"]["end"]["ts"], s["status"])


def _piv_key(p: dict) -> tuple:
    return (p["start_ts"], p["end_ts"], round(p["zg"], 4), round(p["zd"], 4), p["status"])


def test_segments_do_not_change_with_the_visible_window(client):
    small = _structure(client, 300)
    full = _structure(client, 2000)  # 样本只有 1212 根 → 等于全量
    assert len(full["bars"]) == FULL_BARS
    full_keys = {_seg_key(s) for s in full["segments"]}
    assert len(full_keys) == FULL_SEGMENTS, f"全量划分应为 {FULL_SEGMENTS} 段，实测 {len(full_keys)}"
    for s in small["segments"]:
        assert _seg_key(s) in full_keys, (
            f"短窗里的线段 {_seg_key(s)} 在全量划分里不存在 —— 划分跟着窗口变了"
        )


def test_pivots_and_signals_are_the_ones_intersecting_the_window(client):
    """可见结构 = 全量结构 ∩ 窗口。不多（不裁）也不少（不新造）。"""
    full = _structure(client, 2000)
    full_pivots = {_piv_key(p) for p in full["pivots"]}
    assert len(full_pivots) == FULL_PIVOTS
    assert [s["ts"] for s in full["signals"]] == [FULL_SIGNAL_TS]

    for limit in (300, 800):
        body = _structure(client, limit)
        first_ts = body["bars"][0]["ts"]
        want_pivots = {_piv_key(p) for p in full["pivots"] if p["end_ts"] >= first_ts}
        assert {_piv_key(p) for p in body["pivots"]} == want_pivots
        want_sigs = [s["ts"] for s in full["signals"] if s["ts"] >= first_ts]
        assert [s["ts"] for s in body["signals"]] == want_sigs
        assert want_pivots, f"limit={limit} 的窗口本应包含全量中枢（首 {first_ts}）"

    # 800 根窗口覆盖了那个买卖点：默认视图不该再把它藏起来
    assert [s["ts"] for s in _structure(client, 800)["signals"]] == [FULL_SIGNAL_TS]


def test_macd_is_computed_on_full_history_then_trimmed(client):
    """MACD 必须由**引擎看的那条全量序列**算出再裁剪。

    窗口里重算 EMA 看着更省事，但种子不同、两边的 DIF/DEA/柱子会分叉
    （实测 limit=500 最大差 0.0958），页面上就会拿一条和买卖点无关的 MACD 解释背驰。
    """
    small = _structure(client, 300)
    full = _structure(client, 2000)
    n = len(small["bars"])
    for col in ("dif", "dea", "hist"):
        assert small["macd"][col] == full["macd"][col][-n:], f"{col} 是按窗口重算的"


def test_only_structure_that_intersects_the_window_is_sent(client):
    """窗口是**裁剪**不是过滤：跨左边界的那一段要留着，否则笔/段在左边缘会断开。

    笔与线段是首尾相接铺满时间轴的，从窗口中间切一刀，必然有一段横跨左边界。
    把它裁掉，图上就会凭空少一段 —— 看起来像 bug。所以规则是「相交就送」：
    两端都在窗口里的按原样画，横跨边界的由 category 轴线性外推后被画布裁掉。

    买卖点是一个**点**，没有「横跨」一说，必须严格落在窗口内。
    """
    body = _structure(client, 300)
    first, last = body["bars"][0]["ts"], body["bars"][-1]["ts"]
    for s in body["segments"]:
        assert s["end"]["end"]["ts"] >= first, "线段整个在窗口左边，不该送"
        assert s["start"]["start"]["ts"] <= last
    for st in body["strokes"]:
        assert st["end"]["ts"] >= first
        assert st["start"]["ts"] <= last
    for p in body["pivots"]:
        assert p["end_ts"] >= first
        assert p["start_ts"] <= last
    for s in body["signals"]:
        assert first <= s["ts"] <= last, "买卖点必须落在窗口内，否则散点会被静默丢掉"

    assert body["bars_total"] == FULL_BARS
    assert body["counts_total"]["segments"] == FULL_SEGMENTS
    assert body["counts_total"]["pivots"] == FULL_PIVOTS
    assert body["counts"]["segments"] == len(body["segments"]) < FULL_SEGMENTS
