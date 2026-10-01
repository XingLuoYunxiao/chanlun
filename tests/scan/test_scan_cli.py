"""扫描子命令测试（Task 15）。

Task 16 要把这个子命令挂进 `__main__.py`，所以这里同时锁死两个钩子的形状：
`add_parser(sub) -> ArgumentParser` 与 `register(sub) -> ArgumentParser`（并设置 `func`）。
"""

from __future__ import annotations

import argparse
import contextlib

import pytest

from chanlun.scan import cli
from chanlun.scan.scanner import Outcome, ScanHit, ScanReport


def _hit(code="600000", kind="b1", period="day", strength=9.6, outcome=Outcome.HIT):
    reason = {
        Outcome.HIT: "",
        Outcome.INSUFFICIENT: "数据不足：只有 100 根",
        Outcome.ERROR: "扫描失败：坏 parquet",
    }[outcome]
    return ScanHit(code=code, name="浦发银行", period=period, signal_kind=kind,
                   level=period, ts="2026-09-29 15:00:00", price=9.0,
                   pivot_range=(12.0, 18.0), divergence_strength=0.6,
                   strength=strength, status="confirmed", detail="合成第一类买点",
                   outcome=outcome, reason=reason, strength_parts=(("类型权重", 3.0),))


def _report():
    return ScanReport(
        hits=(_hit(), _hit(code="600519", kind="s2", period="30", strength=2.4)),
        skipped=(_hit(code="000001", outcome=Outcome.INSUFFICIENT, strength=0.0),),
        errors=(_hit(code="300750", outcome=Outcome.ERROR, strength=0.0),),
        tasks=4, codes=("600000", "000001", "300750", "600519"), periods=("day",),
        elapsed=1.25, structure={"codes_scanned": 4, "pivots": 1, "segments": 3},
        saved=2, run_date="2026-09-29",
        notes=("meta.sync_state 起始 1999-11-10 与本地 bar 起始 2024-01-01 不一致（本地更短），"
               "按本地起始计算（1 个任务）",),
    )


@pytest.fixture
def patched(env, monkeypatch):
    """把 CLI 的落库连接与扫描实现替换掉，专测「参数 → 调用 → 输出」。"""
    calls: dict[str, tuple] = {}

    def fake_scan_report(*args, **kwargs):
        calls["scan"] = (args, kwargs)
        return _report()

    def fake_track(*args, **kwargs):
        calls["track"] = (args, kwargs)
        return []

    monkeypatch.setattr(cli, "_conn_scope", lambda: contextlib.nullcontext(env.conn))
    monkeypatch.setattr(cli, "scan_report", fake_scan_report)
    monkeypatch.setattr(cli, "track_watchlist", fake_track)
    return type("P", (), {"calls": calls})


def test_add_parser_returns_parser_with_contract_options():
    top = argparse.ArgumentParser()
    sub = top.add_subparsers(dest="command", required=True)
    parser = cli.add_parser(sub)
    assert isinstance(parser, argparse.ArgumentParser)
    args = top.parse_args([
        "scan", "--periods", "day,30", "--codes", "600000,000001",
        "--workers", "4", "--top", "10", "--no-save", "--run-date", "2026-09-29",
    ])
    assert args.command == "scan"
    assert args.periods == "day,30"
    assert args.codes == "600000,000001"
    assert args.workers == 4
    assert args.top == 10
    assert args.save is False
    assert args.run_date == "2026-09-29"


def test_register_sets_func_hook():
    top = argparse.ArgumentParser()
    sub = top.add_subparsers(dest="command", required=True)
    cli.register(sub)
    args = top.parse_args(["scan", "--periods", "day"])
    assert callable(args.func)
    assert args.func is cli.run


def test_register_defaults_match_contract():
    top = argparse.ArgumentParser()
    sub = top.add_subparsers(dest="command", required=True)
    cli.register(sub)
    args = top.parse_args(["scan"])
    assert args.periods == "day,30,5"
    assert args.workers == 8
    assert args.codes is None
    assert args.watch is False


def test_run_prints_chinese_table_summary_and_returns_zero(patched, capsys):
    rc = cli.run(argparse.Namespace(periods="day,30", codes="600000,600519",
                                    workers=2, top=20, watch=False, save=True,
                                    run_date="2026-09-29", notify_file=None))
    out = capsys.readouterr().out
    assert rc == 0
    assert "代码" in out and "名称" in out and "周期" in out and "强度" in out
    assert "600000" in out and "浦发银行" in out
    assert "第一类买点" in out
    assert "命中" in out and "数据不足" in out and "失败" in out
    args, kwargs = patched.calls["scan"]
    assert kwargs["periods"] == ["day", "30"] or args[0] == ["day", "30"]
    assert "600000" in str(kwargs.get("codes") or args[1])
    assert kwargs.get("max_workers") == 2 or args[2] == 2


def test_run_top_limits_printed_rows(patched, capsys):
    cli.run(argparse.Namespace(periods="day", codes=None, workers=1, top=1, watch=False,
                               save=False, run_date=None, notify_file=None))
    out = capsys.readouterr().out
    assert "600000" in out
    assert "600519" not in out


def test_run_watch_mode_calls_track_watchlist(patched):
    rc = cli.run(argparse.Namespace(periods="day", codes="600000", workers=1, top=20,
                                    watch=True, save=True, run_date=None, notify_file=None))
    assert rc == 0
    assert "track" in patched.calls
    assert "scan" not in patched.calls


def test_run_returns_nonzero_when_every_task_failed(env, monkeypatch, capsys):
    bad = ScanReport(hits=(), skipped=(), errors=(_hit(outcome=Outcome.ERROR),) * 2,
                     tasks=2, codes=("600000", "000001"), periods=("day",))
    monkeypatch.setattr(cli, "_conn_scope", lambda: contextlib.nullcontext(env.conn))
    monkeypatch.setattr(cli, "scan_report", lambda *a, **k: bad)
    rc = cli.run(argparse.Namespace(periods="day", codes=None, workers=1, top=20,
                                    watch=False, save=False, run_date=None,
                                    notify_file=None))
    assert rc == 1
    assert "全部失败" in capsys.readouterr().out


def test_format_hits_table_empty_state():
    assert "无命中" in cli.format_hits_table([])


def test_main_dispatches_scan_subcommand(patched, capsys):
    rc = cli.main(["scan", "--periods", "day", "--no-save"])
    assert rc == 0
    assert "命中" in capsys.readouterr().out


def test_notify_file_option_writes_sink(patched, tmp_path):
    log = tmp_path / "cli.log"
    cli.run(argparse.Namespace(periods="day", codes=None, workers=1, top=20, watch=False,
                               save=False, run_date=None, notify_file=str(log)))
    text = log.read_text(encoding="utf-8")
    assert "600000" in text


def test_run_prints_data_quality_notes(patched, capsys):
    """数据质量提示必须打到终端：报告里全是被跳过的票时，提示更不能被吞掉。"""
    ns = argparse.Namespace(periods="day", codes=None, workers=1, top=20, watch=False,
                            save=False, run_date=None, notify_file=None)
    assert cli.run(ns) == 0
    out = capsys.readouterr().out
    assert "数据质量" in out and "1999-11-10" in out
