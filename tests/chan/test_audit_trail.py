"""**信号留痕**：当时标过、后来被走势证伪的买卖点。

用户的原话：买卖点是**当下的判断**，未确认的那些如果后来被证伪了，
不能悄悄从图上消失，要留一条记录说「这里当时标过，从某一刻起不再成立」。

这个模块的价值全在**存亡判定**上，所以测试用假引擎精确控制「哪个检查点有哪些信号」，
不依赖真实行情的运气。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pytest

from chanlun.chan import audit


@dataclass(frozen=True)
class FakeSignal:
    kind: object
    ts: str
    price: float
    src_start: int
    src_end: int
    status: object = "tentative"
    reason: str = ""


@dataclass
class FakeSnap:
    signals: list


@dataclass
class FakeEngine:
    """按**帧长**决定这一帧里有哪些信号 —— 这就是「as-of 时刻的结构」。"""

    table: dict[int, list] = field(default_factory=dict)

    def full(self, frame: pd.DataFrame) -> FakeSnap:
        return FakeSnap(list(self.table.get(len(frame), [])))


def _bars(n: int) -> pd.DataFrame:
    return pd.DataFrame({
        "ts": [f"2026-01-{i + 1:02d}" if i < 28 else f"2026-02-{i - 27:02d}" for i in range(n)],
        "open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n,
        "close": [1.0] * n, "volume": [1.0] * n, "amount": [1.0] * n,
    })


def test_identity_includes_kind(monkeypatch):
    """★ 身份必须带**类型**。

    `state.object_id()` 是 `signal@起-止`，同一段上不同类型的信号（`b1` 与 `b3`）
    会撞成同一个 ID —— 于是「一条被证伪、另一条存活」会被错误地合并成一条。
    """
    a = FakeSignal(kind="b1", ts="t", price=1.0, src_start=10, src_end=20)
    b = FakeSignal(kind="b3", ts="t", price=1.0, src_start=10, src_end=20)
    assert audit._identity(a) != audit._identity(b)
    assert audit._identity(a) == ("b1", 10, 20)


def test_a_signal_that_vanishes_is_reported_as_falsified(monkeypatch):
    """**核心用例**：某个信号在早先的 as-of 快照里有、在最终快照里没了 ⇒ 被证伪。"""
    bars = _bars(10)
    ghost = FakeSignal(kind="b3", ts="2026-01-03", price=9.5, src_start=2, src_end=4)
    survivor = FakeSignal(kind="b1", ts="2026-01-02", price=9.0, src_start=1, src_end=3)
    engine = FakeEngine(table={5: [ghost], 10: [survivor]})

    monkeypatch.setattr(audit, "ChanEngine", lambda *a, **k: engine)
    tr = audit.trail("600000", "day", step=5, bars=bars)

    assert len(tr.entries) == 2
    assert len(tr.alive) == 1 and len(tr.falsified) == 1
    gone = tr.falsified[0]
    assert gone.kind == "b3"
    assert gone.alive is False
    # 「当时标了」的那一刻 = 它第一次出现的那次 as-of
    assert gone.first_seen == "2026-01-05"
    # 「最后成立」= 它最后一次还在的那次 as-of
    assert gone.last_seen == "2026-01-05"
    # 「证伪于」= 下一个检查点，从那一刻起它不再成立
    assert gone.falsified_at == "2026-01-10"
    assert tr.alive[0].falsified_at is None


def test_a_signal_that_survives_has_no_falsified_at(monkeypatch):
    bars = _bars(10)
    s = FakeSignal(kind="b1", ts="2026-01-02", price=9.0, src_start=1, src_end=3)
    engine = FakeEngine(table={5: [s], 10: [s]})
    monkeypatch.setattr(audit, "ChanEngine", lambda *a, **k: engine)
    tr = audit.trail("600000", "day", step=5, bars=bars)
    assert len(tr.alive) == 1 and not tr.falsified
    assert tr.alive[0].first_seen == "2026-01-05"   # 首次出现比存活更早
    assert tr.alive[0].last_seen == "2026-01-10"
    assert tr.alive[0].falsified_at is None


def test_status_progression_is_recorded(monkeypatch):
    """`tentative → confirmed` 的过程要留痕（用户关心的是「当时是未确认的」）。"""
    bars = _bars(10)
    early = FakeSignal(kind="b1", ts="t", price=1.0, src_start=1, src_end=3, status="tentative")
    late = FakeSignal(kind="b1", ts="t", price=1.0, src_start=1, src_end=3, status="confirmed")
    engine = FakeEngine(table={5: [early], 10: [late]})
    monkeypatch.setattr(audit, "ChanEngine", lambda *a, **k: engine)
    tr = audit.trail("600000", "day", step=5, bars=bars)
    e = tr.alive[0]
    assert (e.first_status, e.last_status) == ("tentative", "confirmed")


def test_step_must_be_positive():
    with pytest.raises(ValueError):
        audit.trail("600000", "day", step=0, bars=_bars(5))


def test_empty_store_raises_a_clear_error():
    with pytest.raises(ValueError, match="没有本地数据"):
        audit.trail("999999", "day", step=5, bars=_bars(0))
