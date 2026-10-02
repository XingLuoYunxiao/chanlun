"""把 strict / loose 的**逐回合明细**倒出来，回答「`expectancy` 与 `expectancy_pct` 为什么异号」。

## 为什么需要这个脚本

`cash_pct=0.2` + `initial_cash=100_000` ⇒ 每笔预算 ≈ 2 万 ⇒ 各回合 basis **本该很均匀**。
均匀的话 `mean(pnl)` 与 `mean(pnl_pct)` **不可能异号**。所以只有两种可能：

1. basis 不均匀 ⇒ 那 `expectancy_pct` 是**等权比率均值**、不是组合收益率；
2. 其中一个数字有 bug。

strict 实测 `expectancy=+1947.65 元` 而 `expectancy_pct=-1.85%` —— 必须查清是哪一种。
本脚本把每笔的**隐含 basis**（`pnl / pnl_pct`）打出来，并同时给出

- `mean(pnl)` = `expectancy`（元，等权）
- `mean(pnl_pct)` = `expectancy_pct`（比率，**等权**）
- `sum(pnl)/sum(basis)` = **组合加权收益率**（这才是「钱按仓位加权」的收益）

## 结论（2026-10-02 实测，150 只票）

| | strict | loose |
|---|---|---|
| 回合数 | 5 | 52 |
| basis min/median/max | 6977 / 12463 / 19709 | 7010 / 17623 / 28410 |
| basis max/min | 2.8× | 4.1× |
| `expectancy` | +1947.65 元 | +2932.03 元 |
| `expectancy_pct` | **−1.8483 %** | +19.8661 % |
| `sum(pnl)/sum(basis)` | **+15.7888 %** | **+17.0106 %** |

⇒ **异号是「等权比率均值」的算术后果，不是记账 bug。**
strict 的四个亏损回合比率绝对值之和（107.8%）大于赢家的比率（98.6%），
而赢家的金额（+19430 元）大于四笔亏损金额之和（−9691 元）—— 因为赢家 basis 更大。
**推论：`expectancy_pct` 会随仓位大小分布偏离真实收益，引用它必须同时给组合加权收益率。**
详见 `docs/evidence/2026-10-02-strict-loose-winrate.md` §12.1。

## 用法

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/python \
  optimizer/tools/dump_roundtrip_basis.py --n 150 --out /tmp/exp/dump_rt.json
```

★ 两个口径各跑一次全量回测，`--n 150` 约 46 分钟（strict ≈1323s + loose ≈1455s）。
★ 顺带它是头条数字的**独立复现**（AGENTS.md §5「数字必须可复现」）：
   实测逐位得到 `1947.6475044969998` / `2932.025197349976`，与胜率脚本一致。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# ★ 本脚本**必须**待在 `chanlun/optimizer/tools/` 下：`parents[2]` 靠这个位置才等于项目根。
# 放到别处（例如 /tmp）会让 ROOT 解析成 `/`，票池变空、**静默抽到 0 只票**。
# 这个坑在本项目已出现过 7 次，所以这里大声失败，不静默降级。
if not (ROOT / "data" / "day").is_dir() or not (ROOT / "src" / "chanlun").is_dir():
    raise SystemExit(
        f"★ 项目根解析错误：ROOT={ROOT} 下找不到 data/day 或 src/chanlun。\n"
        f"  本脚本必须放在 chanlun/optimizer/tools/ 内运行（当前 {Path(__file__).resolve()}）。"
    )
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import measure_loose_winrate as M  # noqa: E402  （同目录兄弟模块：抽样器与内存读替身）

from chanlun.backtest.metrics import metrics               # noqa: E402
from chanlun.backtest.runner import run                    # noqa: E402
from chanlun.backtest.strategy import ChanSignalStrategy   # noqa: E402
from chanlun.data import store                             # noqa: E402


def dump(mode: str, codes: list[str], start: str) -> dict:
    res = run(ChanSignalStrategy(), codes, start=start, period="day",
              benchmark_code=None, mode=mode)
    m = metrics(res)
    rts = res.round_trips
    print(f"\n{'=' * 100}\n[{mode}] 成交 {len(res.trades)}  回合 {len(rts)}  "
          f"initial_cash={getattr(res, 'initial_cash', '?')}", flush=True)
    print(f"  expectancy={m.get('expectancy')!r}  expectancy_pct={m.get('expectancy_pct')!r}"
          f"  win_rate={m.get('win_rate')!r}  total_return={m.get('total_return')!r}")

    rows = []
    for r in rts:
        rows.append(dict(code=r.code, entry_ts=r.entry_ts, exit_ts=r.exit_ts, qty=r.qty,
                         entry_price=r.entry_price, exit_price=r.exit_price,
                         gross_pnl=r.gross_pnl, fees=r.fees, pnl=r.pnl,
                         pnl_pct=r.pnl_pct, holding_bars=r.holding_bars,
                         signal_kind=r.signal_kind, exit_signal_kind=r.exit_signal_kind))
    rows.sort(key=lambda d: d["pnl"])
    print(f"\n  {'代码':<11}{'入场':<12}{'出场':<12}{'qty':>7}{'入场价':>9}{'出场价':>9}"
          f"{'pnl':>11}{'pnl_pct':>10}{'隐含basis':>12}")
    for d in rows:
        implied = (d["pnl"] / d["pnl_pct"]) if d["pnl_pct"] else float("nan")
        print(f"  {d['code']:<11}{d['entry_ts'][:10]:<12}{d['exit_ts'][:10]:<12}"
              f"{d['qty']:>7}{d['entry_price']:>9.3f}{d['exit_price']:>9.3f}"
              f"{d['pnl']:>11.1f}{d['pnl_pct'] * 100:>9.2f}%{implied:>12.0f}")

    if not rts:
        # ★ 0 回合绝不当结论：那可能是票池/缓存键/日期区间坏了，而不是「没有信号」。
        raise SystemExit(
            f"★ [{mode}] 回合数为 0 —— 拒绝把 0 当结论。"
            f"检查票池（{len(codes)} 只）、start={start!r}、以及 store.read 替身。"
        )

    pnls = [r.pnl for r in rts]
    pcts = [r.pnl_pct for r in rts]
    bases = [r.pnl / r.pnl_pct for r in rts if r.pnl_pct]
    if len(bases) != len(rts):
        raise SystemExit(f"★ [{mode}] 有回合的 pnl_pct 为 0，basis 反算不出（{len(bases)}/{len(rts)}）。")
    print(f"\n  回合数 {len(rts)}")
    print(f"  mean(pnl)          = {st.mean(pnls):>14.2f} 元   ← 这就是 expectancy")
    print(f"  mean(pnl_pct)      = {st.mean(pcts) * 100:>14.4f} %    ← 这就是 expectancy_pct")
    print(f"  sum(pnl)/sum(basis)= {sum(pnls) / sum(bases) * 100:>14.4f} %    ← 加权（组合）收益率")
    print(f"  basis: min={min(bases):.0f} median={st.median(bases):.0f} max={max(bases):.0f}"
          f"   max/min={max(bases) / min(bases):.1f}×")
    print(f"  ★ 异号？mean(pnl)>0 且 mean(pnl_pct)<0 ：{st.mean(pnls) > 0 and st.mean(pcts) < 0}")
    print(f"  ★ 若 basis 均匀，mean(pnl_pct) 应 ≈ mean(pnl)/median(basis)"
          f" = {st.mean(pnls) / st.median(bases) * 100:.4f} %")

    return dict(
        metrics={k: m.get(k) for k in
                 ("win_rate", "payoff_ratio", "expectancy", "expectancy_pct",
                  "total_return", "max_drawdown_pct", "avg_holding_bars")},
        trades=len(res.trades), round_trips=len(rts), rows=rows,
        basis=dict(min=min(bases), median=st.median(bases), max=max(bases)),
        weighted_return=sum(pnls) / sum(bases),
        notes=list(res.notes),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=150, help="抽样只数（默认 150）")
    ap.add_argument("--start", default="2018-01-01", help="回测起点（默认 2018-01-01）")
    ap.add_argument("--min-bars", type=int, default=250, help="最少 bar 数（默认 250）")
    ap.add_argument("--seed", type=int, default=7, help="抽样种子（默认 7）")
    ap.add_argument("--out", default="/tmp/exp/dump_rt.json", help="JSON 输出路径")
    a = ap.parse_args()

    codes = M.pick(a.n, a.min_bars, a.seed)
    if not codes:
        raise SystemExit(f"★ 抽到 0 只票（n={a.n} min_bars={a.min_bars} seed={a.seed}）。")
    print(f"样本 {len(codes)} 只", flush=True)
    store.read = M._mem_read

    out = {mode: dump(mode, codes, a.start) for mode in ("strict", "loose")}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n已写入 {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
