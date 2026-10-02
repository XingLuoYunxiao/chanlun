"""Task 14 CLI 契约测试：Task 16 会调用 `add_parser(sub)` / `register(sub)`。

Task 6 追加：买卖点口径开关 `--mode {strict,loose}` 的解析、CLI→runner 转发，
以及「口径真的走到了信号与成交流水上」的两条腿断言（见文末 Task 6 段）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pytest

from chanlun.backtest import cli
from chanlun.backtest.runner import BacktestResult, run
from chanlun.backtest.strategy import STRATEGIES, ChanSignalStrategy

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


# ---------------------------------------------------------------------------
# Task 6：`--mode {strict,loose}` 口径开关
#
# 合成正弦波上三类买卖点不可指望（`_synth.py` docstring：真实 24 只样本上为 0），
# 所以口径测试分两条腿：
#   腿 1 确定性 —— `mode` 有没有**归一化后**接到 `find_signals`（monkeypatch 探针）；
#   腿 2 行为性 —— 真实夹具上 loose 的成交多于 strict。
# 只测「参数能解析」是不够的：参数解析正确、底层静默退化成严格模式，照样全过。
# ---------------------------------------------------------------------------

FIXTURE = Path(__file__).resolve().parents[1] / "chan" / "fixtures" / "bars.parquet"

#: 夹具是 **4 只票的面板**，必须先按 `code` 切出单票 —— 否则
#: `data/types.py::normalize` 的 `drop_duplicates(subset=["ts"], keep="last")`
#: 会把整个面板静默压成最后一只票。
MODE_CODE = "sh.600000"
MODE_BARE = MODE_CODE.split(".", 1)[1]


def _mode_fixture_bars() -> pd.DataFrame:
    df = pd.read_parquet(FIXTURE)
    return df[df["code"] == MODE_CODE].drop(columns=["code"]).reset_index(drop=True)


def test_cli_accepts_mode_choice():
    """`--mode` 可选，缺省 `strict`（`--codes` 是 required，必须带上）。"""
    parser = _parent()
    cli.add_parser(parser._subparsers._group_actions[0])
    assert parser.parse_args(["backtest", "--codes", "600000",
                              "--mode", "loose"]).mode == "loose"
    assert parser.parse_args(["backtest", "--codes", "600000"]).mode == "strict"
    assert parser.parse_args(["backtest", "--codes", "600000",
                              "--mode", "strict"]).mode == "strict"


def test_cli_rejects_unknown_mode(capsys):
    """非法口径必须被 argparse 的 `choices` 拦住，不许静默降级成 strict。

    断言 `invalid choice` 而不是只断言 `SystemExit`：`--mode` 完全不存在时
    argparse 也会因 `unrecognized arguments` 退出，只断言退出码的测试在
    实现前就通过 —— 它没在测量任何东西。
    """
    parser = _parent()
    cli.add_parser(parser._subparsers._group_actions[0])
    with pytest.raises(SystemExit):
        parser.parse_args(["backtest", "--codes", "600000", "--mode", "no_such"])
    err = capsys.readouterr().err
    assert "invalid choice" in err, f"非法口径没有被 choices 拦住：stderr={err!r}"


def test_cli_forwards_mode_to_runner(monkeypatch):
    """`run_command` 必须把 `args.mode` 传给 `runner.run(mode=...)`。"""
    seen: dict = {}

    def _fake_run(strategy, codes, **kw):
        seen.update(kw)
        return BacktestResult(codes=tuple(codes), period="day", start=None, end=None,
                              initial_cash=100_000.0, strategy="chan")

    monkeypatch.setattr(cli, "run", _fake_run)
    cli.main(["backtest", "--codes", "600000", "--mode", "loose"])
    assert seen.get("mode") == "loose"
    cli.main(["backtest", "--codes", "600000"])
    assert seen.get("mode") == "strict"


def test_run_binds_mode_into_signal_fn(seed_store, monkeypatch):
    """腿 1：`runner.run` 必须把 `SignalMode` **成员**（不是字符串）绑进 signal_fn。

    `SignalMode` 是 `str` 枚举：`SignalMode.LOOSE == "loose"` 为 True 而
    `SignalMode.LOOSE is "loose"` 为 False，`signal.py` 内部用的是 `is`。
    字符串直通会让 `--mode loose` 静默等于严格模式，所以这里用 `is` 断言。
    """
    import chanlun.backtest.runner as runner
    from chanlun.chan.signal import SignalMode

    seed_store(MODE_BARE, _mode_fixture_bars())

    def _spy_run(mode_arg: str) -> list:
        seen: list = []

        def _spy(bars, segments, pivots, level, macd_df=None, mode=None):
            seen.append(mode)
            return ()  # 一个信号都不产生 ⇒ 回测跑完且很轻

        monkeypatch.setattr(runner, "find_signals", _spy)
        runner.run(ChanSignalStrategy(), [MODE_BARE], period="day",
                   benchmark_code=None, mode=mode_arg)
        return seen

    loose_seen = _spy_run("loose")
    assert loose_seen, "signal_fn 根本没被调用，这条测试没在测量任何东西"
    assert all(m is SignalMode.LOOSE for m in loose_seen), (
        f"mode 没有归一化后传到 find_signals：收到 {set(loose_seen)}"
    )

    strict_seen = _spy_run("strict")
    assert strict_seen, "signal_fn 根本没被调用，这条测试没在测量任何东西"
    assert all(m is SignalMode.STRICT for m in strict_seen), (
        f"mode 没有归一化后传到 find_signals：收到 {set(strict_seen)}"
    )


def test_run_rejects_invalid_mode(seed_store):
    """非法值抛 `ValueError`（`SignalMode("xx")` 天然如此），不静默降级。"""
    seed_store(MODE_BARE, _mode_fixture_bars())
    with pytest.raises(ValueError):
        run(ChanSignalStrategy(), [MODE_BARE], period="day",
            benchmark_code=None, mode="no_such")


def test_loose_mode_reaches_the_trade_tape(seed_store):
    """腿 2：口径必须真的走到成交流水上 —— loose 的成交严格多于 strict。

    夹具 STRICT 基线 0 笔 / 0 回合是**改前实测**（在 `a458e01` 的分离工作区上
    跑同一夹具，命令见 task-6-report.md），不是从当前实现反推的。
    断言用严格大于：写成 `>=` 的话，底层静默退化成严格模式也会通过。
    """
    seed_store(MODE_BARE, _mode_fixture_bars())
    strict = run(ChanSignalStrategy(), [MODE_BARE], period="day",
                 benchmark_code=None, mode="strict")
    loose = run(ChanSignalStrategy(), [MODE_BARE], period="day",
                benchmark_code=None, mode="loose")
    assert (len(strict.trades), len(strict.round_trips)) == (0, 0)
    assert len(loose.trades) > len(strict.trades), (
        f"loose 没有产生更多成交：strict={len(strict.trades)} "
        f"loose={len(loose.trades)}（口径可能静默退化成了严格模式）"
    )
