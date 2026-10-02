"""严格 / 非严格买卖点口径（D-32 / D-33 / D-35）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from chanlun.chan.engine import ChanEngine
from chanlun.chan.signal import SignalKind, SignalMode, find_signals

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bars.parquet"

# 夹具是**四只票的横截面**（4848 行 = 4 × 1212，`ts` 有 3636 个重复值），
# 必须先按 code 过滤再喂引擎 —— 四只票交错喂进去等于把四只票的 bar 混成一只。
# 口径同 `test_engine.py:33-35`。`ChanEngine` 的 `code` 只是标签，bars 才是输入。
_ALL = pd.read_parquet(FIXTURE)
CODE = "sh.600000"


def _bars_of(code: str) -> pd.DataFrame:
    return _ALL[_ALL["code"] == code].drop(columns=["code"]).reset_index(drop=True)


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return _bars_of(CODE)


@pytest.fixture(scope="module")
def strict(bars):
    return ChanEngine(CODE, "day", signal_fn=find_signals, level="day").full(bars)


def test_strict_mode_matches_baseline(bars, strict):
    """D-32：严格模式的买卖点必须与不传 mode 时逐项相同。"""
    again = find_signals(bars, strict.segments, strict.pivots, "day")
    assert again == list(strict.signals)


def test_loose_adds_but_never_removes(bars, strict):
    """非严格是**放宽**：严格模式有的买卖点一个都不能少。"""
    loose = find_signals(bars, strict.segments, strict.pivots, "day",
                         mode=SignalMode.LOOSE)
    key = lambda s: (s.kind.value, s.ts, round(s.price, 6))
    assert {key(s) for s in strict.signals} <= {key(s) for s in loose}


def test_loose_emits_consolidation_kinds(bars, strict):
    loose = find_signals(bars, strict.segments, strict.pivots, "day",
                         mode=SignalMode.LOOSE)
    assert any(s.kind is SignalKind.PB for s in loose) or \
           any(s.kind is SignalKind.PS for s in loose), \
        "夹具上应至少有一个盘整背驰买卖点"


def test_strict_never_emits_pb_ps(bars, strict):
    assert not [s for s in strict.signals if s.kind in (SignalKind.PB, SignalKind.PS)]


def test_pb_name_is_not_first_kind():
    """第 60 课：「盘整背驰无所谓第一类买点，只是这样来类比」。"""
    assert "第一类" not in SignalKind.PB.name_cn
    assert "第一类" not in SignalKind.PS.name_cn
    assert SignalKind.PB.is_buy and not SignalKind.PS.is_buy


def test_third_tolerance_is_loose_only(bars, strict):
    """D-35：第三类的容忍度只在非严格模式生效。"""
    from chanlun.chan.signal import _third_kind

    a = _third_kind(list(strict.segments), strict.pivots, "day", SignalMode.STRICT)
    b = _third_kind(list(strict.segments), strict.pivots, "day", SignalMode.LOOSE)
    assert len(b) >= len(a)
    assert all(0.0 < x.price for x in b)
