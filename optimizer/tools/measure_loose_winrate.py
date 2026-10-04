"""口径胜率验收：严格 vs 非严格（全市场抽样）。

**这不是优化器轮次**，是用户要求的可失败测量（AGENTS.md §5「证据质量规则」）。
它不改主干、不提案、不写 journal。

复现：
    cd <本项目根目录>
    PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_loose_winrate.py \
        --n 150 --start 2018-01-01 --out /tmp/winrate.json

为什么要把行情预读进内存：`runner.run` 每根 bar 都 `store.read(code, period, end=ts)`
（物理截断，见 D-34），N 只票 × M 根 bar 次 parquet 读取会把测量拖到不可接受。
这里把每只票的整帧读一次，再替换 `store.read` 为内存切片 —— **切片语义与
`store.read` 的 `start/end/limit` 必须一致**，否则测的不是同一个回测。
`_mem_read` 的签名与 `data/store.py::read` 逐字相同，切片语义与 `types.py::truncate`
逐条对齐（`start` ⇒ `ts >= start`、`end` ⇒ `ts <= end`、`limit` ⇒ `tail(limit)`、
恒 `reset_index(drop=True)`）。

**★ 缓存键必须是 `store.path_for(code, period)`，不能是调用方传进来的 `code` 字符串。**
理由是一个实测踩到的坑：**runner 传给 `store.read` 的是裸代码**
（`'601952'`），而抽样器收集到的是**带市场前缀**的代码（`'sh.601952'`）。
若按调用方的字符串做键，runner 每次都查不到 ⇒ 静默拿到空 DataFrame ⇒ **0 笔成交**，
而且不会报任何错。按 `path_for` 做键则两种写法解析到同一个 `Path`，与
`store.read` 的解析逻辑**逐字一致**。
配套：查不到就**大声报错**，绝不静默返回空帧。

**票池来源是硬写的 `ROOT / "data"`**（不是 `load_config()`），与
`/tmp/exp/sig_count.py` 的既有口径一致；输出里会显式声明，免得读者误判。

墙钟：`runner.run` 是 O(bars²)（`runner.py:204` 在 `for ts in all_ts` 里每根 bar
重读一次 `full()`，没有按票缓存快照）。实测 100 只票 strict 1353 s + loose 1561 s。
**用 `--n 150`（≈82 分钟）就够**：固定 p0 = 0.4255 推算，100/150/200/300/400 只票的
95% CI 上界依次是 56.7%/54.5%/52.6%/50.7%/49.6%，**全部 < 70%** ——
加样本只会收紧 CI，改变不了「达不到 70~80%」这个结论。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
# ★ 本脚本**必须**待在 `chanlun/optimizer/tools/` 下：`parents[2]` 靠这个位置才等于项目根。
# 放到别处（例如 /tmp）会让 ROOT 解析成 `/`，票池变空、**静默抽到 0 只票** ——
# 那样跑出来的「胜率」全是垃圾却看不出错。所以这里大声失败，不静默降级。
if not (ROOT / "data" / "day").is_dir() or not (ROOT / "src" / "chanlun").is_dir():
    raise SystemExit(
        f"★ 项目根解析错误：ROOT={ROOT} 下找不到 data/day 或 src/chanlun。\n"
        f"  本脚本必须放在 chanlun/optimizer/tools/ 内运行（当前 {Path(__file__).resolve()}）。"
    )
sys.path.insert(0, str(ROOT / "src"))

from chanlun.backtest import runner as runner_mod          # noqa: E402
from chanlun.backtest.metrics import metrics               # noqa: E402
from chanlun.backtest.runner import run                    # noqa: E402
from chanlun.backtest.strategy import ChanSignalStrategy   # noqa: E402
from chanlun.data import store                             # noqa: E402

# ★ 键是 `store.path_for(code, period)`，不是调用方的代码字符串 —— 见模块 docstring。
FRAMES: dict[Path, pd.DataFrame] = {}


def _mem_read(code, period, start=None, end=None, limit=None):
    """与 `store.read` 同签名、同切片语义、同代码解析的内存替身。"""
    df = FRAMES.get(store.path_for(code, period))
    if df is None:
        # 绝不静默返回空帧：那会产出「0 笔成交」这种看着像结论的垃圾。
        raise SystemExit(
            f"★ 未预读的标的：code={code!r} period={period!r} "
            f"-> {store.path_for(code, period)}。先修抽样器，不要继续测量。"
        )
    if start:
        df = df[df["ts"] >= start]
    if end:
        df = df[df["ts"] <= end]
    if limit:
        df = df.tail(limit)
    return df.reset_index(drop=True)


def pick(n: int, min_bars: int, seed: int) -> list[str]:
    """确定性抽样：`sorted` 列文件 → `seed` 洗牌 → 顺序取够 n 只。

    与 `/tmp/exp/sig_count.py` 的口径一致（seed=7、`>=250` 根日线）。
    """
    store.DATA_ROOT = ROOT / "data"
    codes: list[str] = []
    for market in ("sh", "sz"):
        codes += [f"{market}.{p.stem}"
                  for p in sorted((store.DATA_ROOT / "day" / market).glob("*.parquet"))]
    if not codes:
        raise SystemExit(f"★ 票池为空：{(store.DATA_ROOT / 'day')} 下没有 parquet。")
    random.seed(seed)
    random.shuffle(codes)
    out: list[str] = []
    for c in codes:
        df = store.read(c, "day")
        if len(df) < min_bars:
            continue
        FRAMES[store.path_for(c, "day")] = df.reset_index(drop=True)
        out.append(c)
        if len(out) >= n:
            break
    if not out:
        raise SystemExit(f"★ 抽到 0 只票（>= {min_bars} 根日线）。测量无意义，停止。")
    return out


def one(mode: str, codes: list[str], start: str | None) -> dict:
    t0 = time.perf_counter()
    res = run(ChanSignalStrategy(), codes, start=start, period="day",
              benchmark_code=None, mode=mode)
    wall = time.perf_counter() - t0
    m = metrics(res)
    return {
        "mode": mode,
        "wall_seconds": round(wall, 1),
        "trades": len(res.trades),
        "round_trips": len(res.round_trips),
        "win_rate": m.get("win_rate"),
        "payoff_ratio": m.get("payoff_ratio"),
        "expectancy_pct": m.get("expectancy_pct"),
        "expectancy": m.get("expectancy"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "avg_holding_bars": m.get("avg_holding_bars"),
        "total_return": m.get("total_return"),
        "per_signal_type": m.get("per_signal_type"),
        "notes": list(res.notes),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150, help="抽样标的数（150 足够，见模块 docstring）")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="/tmp/winrate.json")
    args = ap.parse_args()

    codes = pick(args.n, args.min_bars, args.seed)
    lens = sorted(len(FRAMES[store.path_for(c, "day")]) for c in codes)
    print(f"抽样 {len(codes)} 只（票池 {ROOT / 'data'} 硬写；>= {args.min_bars} 根日线；"
          f"seed={args.seed}；start={args.start}）")
    print(f"bar 数 min={lens[0]} median={lens[len(lens) // 2]} max={lens[-1]}")

    store.read = _mem_read
    runner_mod.store.read = _mem_read          # 同一对象，冗余但无害

    rows = [one(m, codes, args.start) for m in ("strict", "loose")]
    # ★ 单位坑（2026-10-02 控制者复核发现）：`expectancy_pct` 是**比率**不是百分数
    #   （`broker.py` 的 `pnl_pct = pnl / (entry_price*qty + 费用分摊)`；
    #   `metrics.py` 的 `expectancy_pct = _mean([r.pnl_pct for r in rts])`）。
    #   旧表头写「期望%」却直接打 0.19866 ⇒ 表面看是 +0.20%，真值是 **+19.87%**，
    #   **少 100 倍**。凡引用期望必须连单位一起写：比率 0.19866 == 每回合 +19.87%。
    #   `None`（没有回合）一律打「—」，不许当 0 平均进去。
    print(f"\n{'口径':<8}{'成交':>7}{'回合':>7}{'胜率':>9}{'赔率':>9}"
          f"{'期望%/回合':>11}{'持仓bar':>9}{'秒':>8}")
    for r in rows:
        wr = "—" if r["win_rate"] is None else f"{r['win_rate']:.4f}"
        ep = "—" if r["expectancy_pct"] is None else f"{r['expectancy_pct'] * 100:.2f}"
        po = "—" if r["payoff_ratio"] is None else f"{r['payoff_ratio']:.2f}"
        print(f"{r['mode']:<8}{r['trades']:>7}{r['round_trips']:>7}{wr:>9}"
              f"{po:>9}{ep:>11}"
              f"{(r['avg_holding_bars'] or 0):>9.1f}{r['wall_seconds']:>8.0f}")
    for r in rows:
        print(f"\n[{r['mode']}] 分类：{json.dumps(r['per_signal_type'], ensure_ascii=False)}")
        if r["notes"]:
            print(f"[{r['mode']}] notes：{json.dumps(r['notes'], ensure_ascii=False)}")

    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
