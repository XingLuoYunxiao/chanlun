"""通知层测试（Task 15）。

约束是「可插拔 + 不联网」，所以这里既验证输出内容，也**静态**验证实现里
没有引入任何网络库——「不联网」不能只靠约定，得让它在测试里可检查。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chanlun import notify as notify_mod
from chanlun.scan.watchlist import TrackChange, TrackChangeKind, TrackRow

from test_scanner import _fake_macd  # 复用假 MACD，保证强度数字一致
from scankit import chain, macd_frame, pivot_of, signal_of, snapshot_of, synth_bars
from chanlun.scan.scanner import analyze_snapshot


def _hit():
    bars = synth_bars(300, day_step=2)
    segs = chain([(-1, 10.0, 20.0), (1, 12.0, 22.0), (-1, 9.0, 18.0)])
    snap = snapshot_of("600000", "day", bars, segs=segs,
                       pivots=[pivot_of(segs, 12.0, 18.0, 0, 2)],
                       signals=[signal_of(segs[2], "b1", pivot_idx=0)])
    return analyze_snapshot(snap, bars, name="浦发银行", macd_df=_fake_macd(bars["close"]))[0]


def test_console_notifier_prints_chinese_line(capsys):
    n = notify_mod.ConsoleNotifier()
    n.send("缠论扫描", "600000 浦发银行 第一类买点")
    out = capsys.readouterr().out
    assert "缠论扫描" in out
    assert "600000" in out and "第一类买点" in out


def test_file_notifier_appends_utf8(tmp_path):
    path = tmp_path / "notify.log"
    n = notify_mod.FileNotifier(path)
    n.send("t1", "第一类买点")
    n.send("t2", "底背驰")
    text = path.read_text(encoding="utf-8")
    assert text.count("第一类买点") == 1
    assert text.count("底背驰") == 1
    assert "t1" in text and "t2" in text
    assert text.index("t1") < text.index("t2")


def test_multi_notifier_fans_out(tmp_path, capsys):
    path = tmp_path / "n.log"
    multi = notify_mod.MultiNotifier([notify_mod.ConsoleNotifier(),
                                      notify_mod.FileNotifier(path)])
    multi.send("标题", "正文")
    assert "正文" in capsys.readouterr().out
    assert "正文" in path.read_text(encoding="utf-8")


def test_null_notifier_is_silent(capsys):
    notify_mod.NullNotifier().send("标题", "正文")
    assert capsys.readouterr().out == ""


def test_build_notifier_kinds(tmp_path):
    assert isinstance(notify_mod.build_notifier("console"), notify_mod.ConsoleNotifier)
    assert isinstance(notify_mod.build_notifier("null"), notify_mod.NullNotifier)
    file_n = notify_mod.build_notifier("file", path=tmp_path / "x.log")
    assert isinstance(file_n, notify_mod.FileNotifier)
    with pytest.raises(ValueError):
        notify_mod.build_notifier("email")


def test_notify_scan_renders_hits(tmp_path):
    path = tmp_path / "scan.log"
    notify_mod.notify_scan(notify_mod.FileNotifier(path), [_hit()],
                           title="缠论扫描命中", run_date="2026-09-29")
    text = path.read_text(encoding="utf-8")
    assert "缠论扫描命中" in text
    assert "2026-09-29" in text
    assert "600000" in text and "浦发银行" in text
    assert "第一类买点" in text
    assert "9.6" in text


def test_notify_scan_with_no_hits_still_reports(tmp_path):
    path = tmp_path / "scan.log"
    notify_mod.notify_scan(notify_mod.FileNotifier(path), [], run_date="2026-09-29")
    text = path.read_text(encoding="utf-8")
    assert "无命中" in text


def test_notify_track_renders_changes(tmp_path):
    path = tmp_path / "track.log"
    row = TrackRow(code="600000", name="浦发银行", period="day", as_of="2026-09-29",
                   changes=(TrackChange(kind=TrackChangeKind.NEW_PIVOT, level="day",
                                        ts="2026-09-29", detail="新中枢 [20.00, 26.00]",
                                        strength=0.0),))
    notify_mod.notify_track(notify_mod.FileNotifier(path), [row])
    text = path.read_text(encoding="utf-8")
    assert "600000" in text and "新中枢" in text


def test_notify_module_has_no_network_dependency():
    src = Path(notify_mod.__file__).read_text(encoding="utf-8")
    for bad in ("import requests", "import urllib", "import socket", "import http",
                "smtplib", "websocket"):
        assert bad not in src, f"通知层不得引入网络依赖：{bad}"
