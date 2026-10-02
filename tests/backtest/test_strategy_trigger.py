"""D-34 回归：回测触发判据必须是「首次可见」，不是「结构确认时刻」。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from chanlun.backtest.runner import run
from chanlun.backtest.strategy import ChanSignalStrategy
from chanlun.chan.engine import ChanEngine
from chanlun.chan.signal import find_signals
from chanlun.chan.state import backtestable

FIXTURE = Path(__file__).resolve().parents[1] / "chan" / "fixtures" / "bars.parquet"


def _fixture_bars() -> pd.DataFrame:
    return pd.read_parquet(FIXTURE)


def test_fresh_trigger_produces_trades(seed_store):
    """旧判据（confirmed_at == bar.ts）在结构上永不成立 ⇒ 恒 0 笔成交。"""
    seed_store("600000", _fixture_bars())
    res = run(ChanSignalStrategy(), ["600000"], period="day", benchmark_code=None)
    assert len(res.trades) > 0, (
        "首次可见触发判据必须产生成交；若为 0，说明 on_bar 仍在用 confirmed_at == bar.ts"
    )


def test_confirmed_at_never_equals_bar_ts(seed_store):
    """守住根因：confirmed_at 是结构自身的确认完成时刻，系统性早于首次可见。

    若本测试开始失败（hits > 0），说明 confirmed_at 的语义变了，
    D-34 的论证需要重新做一遍，不能默默保留旧结论。
    """
    df = _fixture_bars()
    total = hits = 0
    for k in range(250, len(df) + 1, 5):
        frame = df.iloc[:k]
        ts = str(frame.iloc[-1]["ts"])
        eng = ChanEngine("600000", "day", signal_fn=find_signals, level="day")
        for sig in backtestable(eng.full(frame).signals, ts):
            total += 1
            if sig.confirmed_at == ts:
                hits += 1
    assert total > 0, "夹具上没有可见信号，本测试没有在测量任何东西"
    assert hits == 0, f"{hits}/{total} 个信号的 confirmed_at 等于当日 ts —— D-34 根因不再成立"
