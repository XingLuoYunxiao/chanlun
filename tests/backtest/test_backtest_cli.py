"""Task 14 CLI 契约测试：Task 16 会调用 `add_parser(sub)` / `register(sub)`。"""

from __future__ import annotations

import argparse

import pytest

from chanlun.backtest import cli
from chanlun.backtest.strategy import STRATEGIES

from _synth import make_bars


def _parent() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m chanlun")
    parser.add_subparsers(dest="command")
    return parser


def test_add_parser_returns_configured_parser():
    parser = _parent()
    p = cli.add_parser(parser._subparsers._group_actions[0])
    assert p.prog.endswith("backtest")
    args = parser.parse_args(["backtest", "--codes", "600000,000001",
                              "--start", "2024-01-02", "--end", "2024-06-30",
                              "--period", "day", "--cash", "200000",
                              "--strategy", "fractal"])
    assert args.command == "backtest"
    assert args.codes == "600000,000001"
    assert args.cash == 200000.0
    assert args.strategy == "fractal"
    assert args.period == "day"


def test_register_is_idempotent_hook_and_executable():
    parser = _parent()
    p = cli.register(parser._subparsers._group_actions[0])
    args = parser.parse_args(["backtest", "--codes", "600000"])
    assert callable(args.func), "register 必须挂上可执行回调，方便 Task 16 直接 args.func(args)"
    assert set(STRATEGIES) >= {"chan", "fractal"}


def test_cli_prints_readable_chinese_report(seed_store, capsys):
    df = make_bars(n=240)
    seed_store("600000", df)
    ts = [str(t) for t in df["ts"]]
    rc = cli.main(["backtest", "--codes", "600000", "--start", ts[0], "--end", ts[-1],
                   "--strategy", "fractal", "--cash", "100000"])
    out = capsys.readouterr().out
    assert rc == 0
    for token in ("回测报告", "胜率", "盈亏比", "期望", "最大回撤", "平均持仓周期",
                  "沪深300", "非缠论"):
        assert token in out, f"报告里缺少 {token}"
    assert "已平仓回合 0" not in out, "机械策略在正弦波上应当有成交，报告数字不应是 0"


def test_cli_returns_nonzero_when_no_data(seed_store, capsys):
    rc = cli.main(["backtest", "--codes", "600000", "--strategy", "chan"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "无" in out and "数据" in out


def test_cli_rejects_unknown_strategy(seed_store):
    with pytest.raises(SystemExit):
        cli.main(["backtest", "--codes", "600000", "--strategy", "no_such"])
