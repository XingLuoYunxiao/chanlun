"""D-34 回归：回测触发判据必须是「首次可见」，不是「结构确认时刻」。

注意 D-36（`confirmed_at` 改取真实可知时刻）之后，本文件第一条测试的**理由**变了：
旧实现下 `confirmed_at` 系统性早于首次可见，`confirmed_at == bar.ts` 在结构上
永不成立；修掉 D-36 后两者会重合（本文件第 2 条测试给出实测）。触发判据仍然
用「首次可见」，因为它来自观测过程，不依赖 `confirmed_at` 恰好精确。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from chanlun.backtest.runner import run
from chanlun.backtest.strategy import ChanSignalStrategy
from chanlun.chan.engine import ChanEngine
from chanlun.chan.signal import find_signals
from chanlun.chan.state import object_id
from chanlun.chan.types import Status

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


def test_confirmed_at_is_reproducible_at_its_own_timestamp(seed_store):
    """D-36 回归：`confirmed_at` 必须是**结构自身的可知时刻**，不是极值 bar 时刻。

    可失败判据：把 K 线截断到 `confirmed_at` 当天重算，该笔必须已经**存在且已确认**。
    旧实现取锁定分型的极值 bar 时刻（系统性早 1~9 根 bar），在那个时刻重算时反向
    的那一笔还没成形 —— 实测 494 笔里 **0 笔**能通过本判据（夹具上约一半）。
    """
    df = _fixture_bars()
    pos = {str(t): i for i, t in enumerate(df["ts"].tolist())}

    eng = ChanEngine(BARE, "day", signal_fn=find_signals, level="day")
    confirmed = [
        s for s in eng.full(df).strokes
        if s.status is Status.CONFIRMED and s.confirmed_at
    ]
    assert confirmed, "夹具上没有已确认的笔，本测试没有在测量任何东西"

    seen: dict[str, set] = {}
    ok = bad = 0
    for st in confirmed:
        key = st.confirmed_at
        if key not in seen:
            k = pos.get(key)
            seen[key] = set() if k is None else {
                object_id(s)
                for s in ChanEngine(BARE, "day", signal_fn=find_signals,
                                    level="day").full(df.iloc[: k + 1]).strokes
                if s.status is Status.CONFIRMED
            }
        if object_id(st) in seen[key]:
            ok += 1
        else:
            bad += 1
    assert ok == len(confirmed), (
        f"{bad}/{len(confirmed)} 笔在自己的 confirmed_at 当天还不存在 —— "
        "confirmed_at 又退回成早于可知时刻的估计值了"
    )
