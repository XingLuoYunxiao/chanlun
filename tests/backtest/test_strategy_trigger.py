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

#: 夹具是 **4 只票的面板**（`sh.600000` / `sh.601088` / `sz.300059` / `sz.300750`，
#: 各 1212 根，`ts` 重复 3636 行）。回测与 `ChanEngine` 都只认单票，而
#: `data/types.py::normalize` 的 `drop_duplicates(subset=["ts"], keep="last")`
#: 会把整个面板**静默压成最后一只票**（`sz.300750`），所以必须先切出单票。
#: 切法沿用 `tests/chan/test_engine.py:33-35`。
#: 选 `sz.300059` 而不是 `sh.600000`：真 600000 的 2 个「首次可见」信号都是 s3，
#: 空仓时卖点被跳过 ⇒ 修好后仍是 0 笔成交，测不出东西。
CODE = "sz.300059"
#: `seed_store` / `ChanEngine` 收不带市场前缀的裸代码（市场由存储目录表达）。
BARE = CODE.split(".", 1)[1]


def _fixture_bars() -> pd.DataFrame:
    """夹具里**单只票**的 K 线；`code` 列去掉（`tests/chan/test_engine.py:33-35`）。"""
    df = pd.read_parquet(FIXTURE)
    return df[df["code"] == CODE].drop(columns=["code"]).reset_index(drop=True)


def test_fresh_trigger_produces_trades(seed_store):
    """旧判据（confirmed_at == bar.ts）在结构上永不成立 ⇒ 恒 0 笔成交。"""
    seed_store(BARE, _fixture_bars())
    res = run(ChanSignalStrategy(), [BARE], period="day", benchmark_code=None)
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
        eng = ChanEngine(BARE, "day", signal_fn=find_signals, level="day")
        for sig in backtestable(eng.full(frame).signals, ts):
            total += 1
            if sig.confirmed_at == ts:
                hits += 1
    assert total > 0, "夹具上没有可见信号，本测试没有在测量任何东西"
    assert hits == 0, f"{hits}/{total} 个信号的 confirmed_at 等于当日 ts —— D-34 根因不再成立"
