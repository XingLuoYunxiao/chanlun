"""回填工具的**探针**：连续零行到底是「被限流」还是「这批标的源上没有」。

这个判据搞错过一次，代价是第一次 `--only-missing` 续跑**一只没拉到**就停了 ——
缺失池的**前缀恰好全是退市股**，被误判成限流。而随机抽 40 只问源，36 只有数据。
所以探针必须有回归保护：它区分的是「**源还活着吗**」，不是「这只票有数据吗」。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "optimizer" / "tools"))

import backfill_one_minute as B  # noqa: E402


def test_canary_true_when_source_still_serves(monkeypatch):
    """源还活着 ⇒ `True`（于是回填判「零行是标的问题」，清零继续）。"""
    from chanlun.data import pytdx_source
    monkeypatch.setattr(pytdx_source, "reset", lambda: None)
    monkeypatch.setattr(pytdx_source, "fetch_bars",
                        lambda *a, **k: pd.DataFrame({"ts": ["2026-10-15"]}))
    assert B._canary_alive(None) is True


def test_canary_false_when_source_returns_nothing(monkeypatch):
    """源也拿不到 ⇒ `False`（确认限流，停批）。"""
    from chanlun.data import pytdx_source
    monkeypatch.setattr(pytdx_source, "reset", lambda: None)
    monkeypatch.setattr(pytdx_source, "fetch_bars", lambda *a, **k: pd.DataFrame())
    assert B._canary_alive(None) is False


def test_canary_false_when_source_raises(monkeypatch):
    """源连不上（抛异常）也算不可用 —— **不许把异常当「活着」**。"""
    from chanlun.data import pytdx_source
    monkeypatch.setattr(pytdx_source, "reset", lambda: None)

    def boom(*a, **k):
        raise RuntimeError("连不上任何一台已知服务器")

    monkeypatch.setattr(pytdx_source, "fetch_bars", boom)
    assert B._canary_alive(None) is False


def test_canary_probes_a_liquid_stock_not_a_missing_one():
    """★ 探针必须是**必定有数据的票**。

    拿「正在回填的那只」当探针是本末倒置：它有没有数据**正是我们搞不清楚的事**。
    """
    assert B.CANARY == "600030"
    assert B.CANARY not in {"000004", "000005", "000018"}, "别用退市股当探针"
