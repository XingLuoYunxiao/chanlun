"""`GET /api/structure/trail`：把「当时标过、后来被证伪」的买卖点交给页面。

这个端点存在的唯一理由是**成本**：留痕要按检查点回放（每 step 根算一次全量结构），
实测 1 分钟的 2.3 万根序列一次要 3~4 秒 —— 放进 `/api/structure` 会把首屏拖垮。
所以它单独一条请求，而且**算一次存一次**（留痕是耐久产物，除非数据变了历史不会变）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from chanlun.data import meta, store


def _bars(n: int = 400) -> pd.DataFrame:
    return pd.DataFrame({
        "ts": [f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d} 15:00" for i in range(n)],
        "open": [10.0 + i * 0.01 for i in range(n)],
        "high": [10.2 + i * 0.01 for i in range(n)],
        "low": [9.8 + i * 0.01 for i in range(n)],
        "close": [10.1 + i * 0.01 for i in range(n)],
        "volume": [1.0] * n, "amount": [1.0] * n,
    })


def test_trail_returns_the_contract_shape(client, cfg):
    store.upsert("600000", "day", _bars())
    r = client.get("/api/structure/trail", params={"code": "600000", "period": "day"})
    assert r.status_code == 200, r.text
    b = r.json()
    for key in ("code", "period", "adjust", "as_of", "step", "bars",
                "checkpoints", "alive", "falsified", "cached"):
        assert key in b, key
    assert b["code"] == "600000" and b["period"] == "day"
    assert isinstance(b["falsified"], list)
    assert b["cached"] is False, "首次必须真的算，不许假装命中缓存"


def test_second_request_hits_the_cache(client, cfg):
    """第二次必须是**缓存命中**：留痕是耐久产物，不该每点一次图就回放一遍。"""
    store.upsert("600000", "day", _bars())
    first = client.get("/api/structure/trail", params={"code": "600000", "period": "day"}).json()
    second = client.get("/api/structure/trail", params={"code": "600000", "period": "day"}).json()
    assert first["cached"] is False and second["cached"] is True
    assert first["as_of"] == second["as_of"]


def test_cache_is_invalidated_when_data_changes(client, cfg):
    """★ 新鲜度判据是**最后一根 + 分辨率**。数据变了就必须重算 ——
    否则页面会继续显示一份属于旧数据的留痕。"""
    store.upsert("600000", "day", _bars(300))
    a = client.get("/api/structure/trail", params={"code": "600000", "period": "day"}).json()
    store.upsert("600000", "day", _bars(400))     # 多了一截
    b = client.get("/api/structure/trail", params={"code": "600000", "period": "day"}).json()
    assert b["cached"] is False, "最后一根变了必须重算"
    assert a["as_of"] != b["as_of"]


def test_cache_is_per_adjust(client, cfg):
    """★ 留痕是**价格的函数**：`qfq` 与 `raw` 会得出不同结构，键必须带口径。

    这与 `structure_snapshot` 只按 `(code, period)` 存不同 —— 那里的口径在 payload 里，
    这里不行：换口径不重算就会把 A 口径的留痕画到 B 口径的图上。
    """
    store.upsert("600000", "day", _bars())
    meta.init().close()
    conn = meta.init()
    try:
        meta.save_signal_trail(conn, "600000", "day", "qfq", "2026-01-01", 10, '{"x":1}')
    finally:
        conn.close()
    conn = meta.init()
    try:
        assert meta.get_signal_trail(conn, "600000", "day", "qfq") is not None
        assert meta.get_signal_trail(conn, "600000", "day", "raw") is None, "口径必须分开存"
    finally:
        conn.close()


def test_refresh_forces_a_recompute(client, cfg):
    store.upsert("600000", "day", _bars())
    client.get("/api/structure/trail", params={"code": "600000", "period": "day"})
    r = client.get("/api/structure/trail",
                   params={"code": "600000", "period": "day", "refresh": True})
    assert r.json()["cached"] is False


def test_missing_period_is_404_with_a_clear_reason(client, cfg):
    r = client.get("/api/structure/trail", params={"code": "600000", "period": "5"})
    assert r.status_code == 404
    assert "没有本地数据" in r.json()["detail"]
