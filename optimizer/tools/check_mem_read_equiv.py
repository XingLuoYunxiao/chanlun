"""`_mem_read` 与真实 `store.read` 的等价性检查（Task 9 的硬门槛）。

`measure_loose_winrate.py` 为了把 O(bars²) 的 `runner.run` 拉回可接受墙钟，
把 `store.read` 换成了内存切片替身 `_mem_read`。**替换必须逐字段等价**，
否则跑出来的「胜率」测的不是同一个回测 —— 整个测量无效。

做法：同一批票（`--n 3`、`seed=7`、`>=250` 根日线）分别用真实 `store.read`
与 `_mem_read` 各跑一次 `runner.run(mode="loose")`，把结果压成指纹
（逐笔成交的 ts/side/price/kind + 逐回合的 kind/entry_ts/exit_ts/pnl/holding_bars）
**逐字段比较**。不一致就返回 1，先修 `_mem_read`，不要继续测量。

复现：
    cd /Users/zzz/workspace/chanlun
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/check_mem_read_equiv.py 2>&1 | grep -v '^normalize:'

**★ 零样本护栏（必读）**：本检查自己也有过一次「假 ✅」——
项目根解析错 ⇒ 票池为空 ⇒ 两边都是 **0 笔 == 0 笔** ⇒ 脚本报了通过。
`0 == 0` 什么也证明不了，所以 `_assert_nonzero` 对空样本**大声失败**，
两条分支都实测会响：① 抽到 0 只票；② 票池非空但真实侧 0 笔交易。
**这类检查必须对空样本大声失败，否则它担保的是一份垃圾。**

来源：本文件由 `/tmp/exp/equiv_check.py` **移入**（测量逻辑不变，只改路径与文件头）。
原先它只是控制者的临时探针；按 AGENTS.md §5「数字要能从写下的命令复现」，
**担保测量的检查本身也必须入库**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# ★ 与 measure_loose_winrate.py 同样的守卫：本脚本必须待在 chanlun/optimizer/tools/ 下，
# `parents[2]` 才等于项目根。放到别处会让票池变空 ⇒ 0 笔 == 0 笔 ⇒ 假通过。
if not (ROOT / "data" / "day").is_dir() or not (ROOT / "src" / "chanlun").is_dir():
    raise SystemExit(
        f"★ 项目根解析错误：ROOT={ROOT} 下找不到 data/day 或 src/chanlun。\n"
        f"  本脚本必须放在 chanlun/optimizer/tools/ 内运行（当前 {Path(__file__).resolve()}）。"
    )
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "optimizer" / "tools"))

import measure_loose_winrate as M  # noqa: E402

from chanlun.backtest.runner import run                    # noqa: E402
from chanlun.backtest.strategy import ChanSignalStrategy   # noqa: E402
from chanlun.data import store                             # noqa: E402


def sig(res):
    """把结果压成可比较的指纹：逐回合的 (kind, 买卖日, 价, 盈亏, 持仓根数)。"""
    return {
        "n_trades": len(res.trades),
        "n_round_trips": len(res.round_trips),
        "trades": [
            (str(getattr(t, "ts", "")), str(getattr(t, "side", "")),
             round(float(getattr(t, "price", 0.0)), 6), str(getattr(t, "kind", "")))
            for t in res.trades
        ],
        "rts": [
            (str(getattr(r, "signal_kind", "")), str(getattr(r, "entry_ts", "")),
             str(getattr(r, "exit_ts", "")), round(float(getattr(r, "pnl", 0.0)), 6),
             int(getattr(r, "holding_bars", 0)))
            for r in res.round_trips
        ],
    }


def _assert_nonzero(tag: str, fp: dict) -> None:
    """★ 零样本护栏。

    这条护栏是踩坑加的：曾经 `pick()` 因为项目根解析错而返回空票池，
    于是两边都是 0 笔交易、`real == mem` 成立，脚本**报了个假的 ✅**。
    0 笔 == 0 笔 什么也证明不了，必须大声失败。
    """
    if fp["n_trades"] == 0 or fp["n_round_trips"] == 0:
        raise SystemExit(
            f"★ 零样本 —— {tag} 只有 {fp['n_trades']} 笔交易 / "
            f"{fp['n_round_trips']} 个回合。\n"
            "  0 笔 == 0 笔 不能证明任何等价性。\n"
            "  先查：票池是否为空？选出的票是否都不足 250 根日线？"
            "`--start` 是否晚于全部数据？\n"
            "  不要把这个结果当成通过。"
        )


def main() -> int:
    codes = M.pick(3, 250, 7)
    print(f"等价性检查用 {len(codes)} 只：{codes}")
    if not codes:
        raise SystemExit("★ 抽到 0 只票 —— 等价性检查无意义，停止。")

    # ① 真实 store.read
    real = sig(run(ChanSignalStrategy(), codes, start="2018-01-01", period="day",
                   benchmark_code=None, mode="loose"))
    _assert_nonzero("真实 store.read", real)

    # ② 内存替身
    store.read = M._mem_read
    mem = sig(run(ChanSignalStrategy(), codes, start="2018-01-01", period="day",
                  benchmark_code=None, mode="loose"))
    _assert_nonzero("内存替身 _mem_read", mem)

    same = real == mem
    print(f"trades      : 真实 {real['n_trades']}  vs  内存 {mem['n_trades']}")
    print(f"round_trips : 真实 {real['n_round_trips']}  vs  内存 {mem['n_round_trips']}")
    print(f"逐字段完全相同: {same}")
    if not same:
        for k in ("trades", "rts"):
            if real[k] != mem[k]:
                print(f"\n★ 差异在 {k}：")
                print(f"  真实前 3: {real[k][:3]}")
                print(f"  内存前 3: {mem[k][:3]}")
        print("\n★ 等价性不成立 —— 先修 _mem_read，不要继续测量。")
        return 1
    print(f"\n✅ 等价性成立（非零样本：{real['n_trades']} 笔交易 / {real['n_round_trips']} 个回合，逐字段相同）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
