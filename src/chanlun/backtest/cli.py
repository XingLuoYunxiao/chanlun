"""Task 14 命令行入口：`chanlun backtest`。

Task 16 会把 `register(sub)` 挂进 `src/chanlun/__main__.py`（本任务**不改**
`__main__.py`），因此这里同时提供两种接法：

- `add_parser(sub) -> ArgumentParser`：只注册子命令，返回 parser；
- `register(sub) -> ArgumentParser`：注册并把 `args.func` 挂成可执行回调，
  于是 Task 16 既可以 `args.func(args, cfg)` 也可以 `args.func(args)`。

报告是给人看的中文文本：先给结论（收益/回撤/胜率/盈亏比/期望），再给分信号
类型统计与基准对比，最后是备注（缺基准、0 成交原因、非缠论策略声明）。
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Sequence

from .metrics import metrics as compute_metrics
from .runner import DEFAULT_BENCHMARK, BacktestResult, run
from .strategy import STRATEGIES, kind_label

#: 与 `DataConfig.periods` 一致
PERIODS = ("day", "30", "5")


def _split_codes(raw: Any) -> list[str]:
    """`--codes 600000,000001` 或 `--codes "600000 000001"` 都接受。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts: list[str] = []
        for item in raw:
            parts.extend(str(item).replace(",", " ").split())
        return [p for p in parts if p]
    return [p for p in str(raw).replace(",", " ").split() if p]


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `backtest` 子命令（供 `__main__.py` / 测试调用）。"""
    p = sub.add_parser(
        "backtest",
        help="point-in-time 回测（T+1、次日开盘成交、涨跌停限制、含费用）",
        description=(
            "逐 bar 重放历史行情：每根 bar 都只用 store.read(end=该bar ts) 的物理"
            "截断帧重算缠论结构，只读 status=confirmed 且 confirmed_at<=当前bar 的"
            "结构，因此不存在未来函数。"
        ),
    )
    p.add_argument("--codes", required=True,
                   help="股票代码，逗号或空格分隔，如 600000,000001（可带 sh./sz. 前缀）")
    p.add_argument("--start", default=None, help="开始日期 YYYY-MM-DD（含）")
    p.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD（含）")
    p.add_argument("--period", default="day", choices=PERIODS, help="行情周期，默认 day")
    p.add_argument("--cash", type=float, default=100_000.0, help="初始资金，默认 100000")
    p.add_argument("--strategy", default="chan", choices=sorted(STRATEGIES),
                   help="策略：chan=缠论三类买卖点，fractal=底/顶分型机械策略"
                        "（非缠论，仅用于验证账务/指标管线）")
    p.add_argument("--benchmark", default=DEFAULT_BENCHMARK,
                   help=f"基准代码，默认 {DEFAULT_BENCHMARK}（沪深300）；缺数据自动降级")
    return p


def register(sub: Any) -> argparse.ArgumentParser:
    """注册子命令并挂上执行回调。返回 parser，便于 Task 16 进一步定制。"""
    p = add_parser(sub)
    p.set_defaults(func=run_command)
    return p


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def _rate_pct(value: float | None) -> str:
    """胜率/占比类不显示正号。"""
    return "—" if value is None else f"{value * 100:.2f}%"


def _pct_abs(value: float | None) -> str:
    """回撤/跌幅这类「损失幅度」按绝对值展示，不加正号以免误导。"""
    return "—" if value is None else f"{abs(value) * 100:.2f}%"


def _num(value: float | None, digits: int = 2, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:+,.{digits}f}" if signed else f"{value:,.{digits}f}"


def format_report(result: BacktestResult) -> str:
    """把 `BacktestResult` 渲染成中文报告（纯函数，方便测试与复用）。"""
    m = dict(result.metrics) or compute_metrics(result)
    spec = STRATEGIES.get(result.strategy)
    label = spec.label if spec else result.strategy
    lines: list[str] = []
    lines.append("=== chanlun 回测报告 ===")
    lines.append(f"策略: {label}（{result.strategy}）")
    if spec:
        lines.append(f"说明: {spec.note}")
    span = result.data_span
    lines.append(
        f"区间: {result.start or '不限'} ~ {result.end or '不限'}"
        + (f"（实际数据 {span[0]} ~ {span[1]}，{len(result.equity)} 个交易日）" if span else "")
    )
    lines.append(f"周期: {result.period}   代码: {', '.join(result.codes)}")
    lines.append(
        f"资金: 初始 {_num(result.initial_cash)} → 期末 {_num(m['final_equity'])}"
        f"   总收益 {_pct(m['total_return'])}   总盈亏 {_num(m['total_pnl'], signed=True)}"
    )
    buys = sum(1 for f in result.trades if f.side == "buy")
    sells = len(result.trades) - buys
    lines.append(
        f"成交: {len(result.trades)} 笔（买 {buys} / 卖 {sells}）；"
        f"已平仓回合 {m['n_trades']}；被拒委托 {m['n_rejections']}；"
        f"期末未成交委托 {m['n_pending']}；期末持仓 {m['n_open_positions']}"
    )
    lines.append(
        f"风险: 最大回撤 {_num(m['max_drawdown'])}（{_pct_abs(m['max_drawdown_pct'])}）"
    )
    lines.append(
        f"回合: 胜率 {_rate_pct(m['win_rate'])}"
        + (f"（{m['wins']} 胜 / {m['losses']} 负）" if m["n_trades"] else "")
        + f"   盈亏比 {_num(m['payoff_ratio'])}"
        + f"   期望 {_num(m['expectancy'], signed=True)}/笔（{_pct(m['expectancy_pct'])}/笔）"
        + f"   盈利因子 {_num(m['profit_factor'])}"
    )
    hold_bars = m["avg_holding_bars"]
    lines.append(
        "持仓: 平均持仓周期 "
        + ("—" if hold_bars is None
           else f"{_num(hold_bars)} 根 bar（自然日 {_num(m['avg_holding_days'])} 天）")
    )

    lines.append("分信号类型:")
    per = m["per_signal_type"] or {}
    if not per:
        lines.append("  （无已平仓回合，无法分类统计）")
    else:
        for kind, stat in sorted(per.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
            lines.append(
                f"  {kind_label(kind)}({kind}) n={stat['n']} "
                f"胜率 {_rate_pct(stat['win_rate'])} 盈亏比 {_num(stat['payoff_ratio'])} "
                f"期望 {_num(stat['expectancy'], signed=True)}"
            )

    bench = m.get("benchmark")
    if bench:
        lines.append(
            f"沪深300基准: {bench.get('code')} 区间 {_pct(bench.get('return'))}"
            f"（{bench.get('start')} ~ {bench.get('end')}）；"
            f"超额收益 {_pct(bench.get('excess_return'))}"
        )
    else:
        lines.append("沪深300基准: 缺基准数据（benchmark=None，不做基准对比）")

    notes: list[str] = []
    for note in list(result.notes) + list(m.get("notes") or []):
        if note not in notes:  # metrics 的备注包含 runner 的一部分，去重后更好读
            notes.append(note)
    if notes:
        lines.append("备注:")
        for note in notes:
            lines.append(f"  - {note}")
    return "\n".join(lines)


def run_command(args: Any, cfg: Any = None) -> int:
    """执行回测并打印报告。返回 0 表示跑出结果，非 0 表示没有可用数据。"""
    codes = _split_codes(getattr(args, "codes", None))
    if not codes:
        print("错误: --codes 不能为空", file=sys.stderr)
        return 2
    strategy = STRATEGIES[args.strategy].build()
    result = run(
        strategy, codes,
        start=getattr(args, "start", None), end=getattr(args, "end", None),
        period=getattr(args, "period", "day"), initial_cash=getattr(args, "cash", 100_000.0),
        benchmark_code=getattr(args, "benchmark", DEFAULT_BENCHMARK),
        strategy_name=args.strategy,
    )
    print(format_report(result))
    return 0 if result.equity else 1


def main(argv: Sequence[str] | None = None) -> int:
    """独立运行入口：`python -m chanlun.backtest.cli backtest --codes 600000`。"""
    parser = argparse.ArgumentParser(
        prog="python -m chanlun.backtest.cli",
        description="chanlun point-in-time 回测（Task 14）",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    register(sub)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
