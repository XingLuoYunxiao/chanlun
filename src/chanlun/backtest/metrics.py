"""Task 14 指标层：全部指标从 `BacktestResult` 的原始数据现算。

口径说明（为什么这么定）
------------------------
- **胜率**：`pnl > 0` 的回合占比。`pnl` 已扣除双边佣金与印花税——按毛盈亏算
  胜率会把「赚了价差但不够手续费」的回合算成盈利，明显偏乐观。平局（pnl<=0）
  计入亏损侧，同样取保守。
- **盈亏比**：平均盈利 / |平均亏损|。只有一侧样本时无定义，返回 `None`
  而不是 `inf`——`inf` 一旦进 JSON/报告就没法看，也会掩盖「只有盈利回合」这种
  样本不足的事实。
- **期望**：每笔回合的平均净盈亏（`expectancy`）与平均净收益率
  （`expectancy_pct`）。期望才是「这套策略能不能赚钱」的核心数字，胜率单独看
  会误导。
- **最大回撤**：权益曲线（现金 + 持仓市值，按收盘价 mark-to-market）上的
  峰谷最大跌幅，同时给绝对额与百分比。
- **平均持仓周期**：以 bar 数（`holding_bars`）为主、自然日数为辅。bar 数不受
  停牌/长假影响，跨股票可比。
- **分信号类型**：按**买入信号**归类（1/2/3 类买点各自的胜率与盈亏比）。
  机械策略的 `signal_kind` 是「底分型」，会原样出现在报告里，不会伪装成买点。
- **基准**：沪深300 的区间涨幅与超额收益。存储里没有基准数据时 `benchmark=None`，
  报告显式写「缺基准数据」，绝不静默用别的指数替代。
"""

from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any, Iterable, Sequence

from .strategy import kind_label


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _mean(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def max_drawdown(equity_curve: Iterable[Any]) -> tuple[float, float]:
    """返回 `(最大回撤额, 最大回撤比例)`；曲线为空时 `(0.0, 0.0)`。"""
    peak = None
    worst = 0.0
    worst_pct = 0.0
    for point in equity_curve:
        value = float(point.equity)
        peak = value if peak is None else max(peak, value)
        drop = peak - value
        if drop > worst:
            worst = drop
            worst_pct = drop / peak if peak > 0 else 0.0
    return worst, worst_pct


def _hold_days(entry_ts: str, exit_ts: str) -> int | None:
    try:
        return (date.fromisoformat(str(exit_ts)[:10]) - date.fromisoformat(str(entry_ts)[:10])).days
    except ValueError:
        return None


def _group(round_trips: Sequence[Any]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Any]] = {}
    for rt in round_trips:
        groups.setdefault(str(rt.signal_kind or "未标注"), []).append(rt)

    out: dict[str, dict[str, Any]] = {}
    for kind, items in groups.items():
        wins = [r.pnl for r in items if r.pnl > 0]
        losses = [r.pnl for r in items if r.pnl <= 0]
        avg_win = _mean(wins)
        avg_loss = _mean(losses)
        out[kind] = {
            "label": kind_label(kind),
            "n": len(items),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": _rate(len(wins), len(items)),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "payoff_ratio": (avg_win / abs(avg_loss)) if avg_win is not None and avg_loss else None,
            "expectancy": _mean([r.pnl for r in items]),
            "total_pnl": sum(r.pnl for r in items),
            "avg_holding_bars": _mean([r.holding_bars for r in items]),
        }
    return out


def metrics(result: Any) -> dict[str, Any]:
    """把回测原始数据折算成报告需要的全部指标。"""
    rts: list[Any] = list(getattr(result, "round_trips", ()) or ())
    equity = list(getattr(result, "equity", ()) or ())
    initial = float(getattr(result, "initial_cash", 0.0) or 0.0)
    final = float(equity[-1].equity) if equity else initial

    wins = [r.pnl for r in rts if r.pnl > 0]
    losses = [r.pnl for r in rts if r.pnl <= 0]
    avg_win = _mean(wins)
    avg_loss = _mean(losses)
    dd, dd_pct = max_drawdown(equity)

    days = [d for d in (_hold_days(r.entry_ts, r.exit_ts) for r in rts) if d is not None]

    benchmark = getattr(result, "benchmark", None)
    benchmark_out: dict[str, Any] | None = None
    total_return = (final / initial - 1.0) if initial > 0 else None
    if benchmark:
        benchmark_out = dict(benchmark)
        if total_return is not None and benchmark.get("return") is not None:
            benchmark_out["excess_return"] = total_return - float(benchmark["return"])

    notes = list(getattr(result, "notes", ()) or ())
    if not rts:
        notes.append(
            "本区间没有任何已平仓回合：胜率/盈亏比/期望都无定义（报告中显示为「—」），"
            "不是 0。"
        )
    if not benchmark:
        notes.append("缺基准数据：无法计算与沪深300的超额收益。")

    return {
        "initial_cash": initial,
        "final_equity": final,
        "total_pnl": final - initial,
        "total_return": total_return,
        "n_trades": len(rts),
        "n_fills": len(getattr(result, "trades", ()) or ()),
        "n_open_positions": len(getattr(result, "positions", ()) or ()),
        "n_rejections": len(getattr(result, "rejections", ()) or ()),
        "n_pending": len(getattr(result, "pending", ()) or ()),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _rate(len(wins), len(rts)),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": (avg_win / abs(avg_loss)) if avg_win is not None and avg_loss else None,
        "profit_factor": (sum(wins) / abs(sum(losses))) if wins and losses else None,
        "expectancy": _mean([r.pnl for r in rts]),
        "expectancy_pct": _mean([r.pnl_pct for r in rts]),
        "max_drawdown": dd,
        "max_drawdown_pct": dd_pct,
        "avg_holding_bars": _mean([r.holding_bars for r in rts]),
        "avg_holding_days": _mean([float(d) for d in days]),
        "per_signal_type": _group(rts),
        "benchmark": benchmark_out,
        "notes": notes,
    }
