"""`import-text` 子命令：**默认合并，绝不销毁已有数据**。

最后一条是重点。这个命令起先用 `store.write`（整份替换），结果拿一个 2 行的测试
文件把 `data/5/sh/600030.parquet` 的 **78432 根真数据**覆写掉了 —— 而命令只打印
「已写入」，看不出任何异常。这正是 D-19 那条「`store.write` 是换源不是补充」。
导入一段导出**不该**有抹掉历史的权力。
"""
from __future__ import annotations

import pytest

from chanlun import __main__ as cli
from chanlun.data import store

DAILY = "日期,开盘,最高,最低,收盘,成交量\n2026-10-13,1.0,1.2,0.9,1.1,100\n"
DAILY2 = "日期,开盘,最高,最低,收盘,成交量\n2026-10-14,1.1,1.3,1.0,1.2,200\n"
MINUTE = "日期,时间,开盘,最高,最低,收盘,成交量\n2026-10-13,0935,1.0,1.2,0.9,1.1,100\n"


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_dry_run_parses_but_does_not_write(tmp_path, capsys):
    p = _write(tmp_path, "a.csv", DAILY)
    assert cli.main(["import-text", "--src", str(p), "--code", "sh.600030",
                     "--period", "day", "--dry-run"]) == 0
    assert "没有写库" in capsys.readouterr().out
    assert store.read("600030", "day").empty


def test_writes_and_reads_back(tmp_path, capsys):
    p = _write(tmp_path, "a.csv", DAILY)
    assert cli.main(["import-text", "--src", str(p), "--code", "sh.600030",
                     "--period", "day"]) == 0
    out = capsys.readouterr().out
    assert "合并" in out and "现共 1 行" in out
    df = store.read("600030", "day")
    assert list(df["ts"]) == ["2026-10-13"] and float(df["close"].iloc[0]) == 1.1


def test_default_merges_and_never_destroys(tmp_path):
    """★ 两次导入必须**累加**，不能第二次把第一次抹掉。"""
    a = _write(tmp_path, "a.csv", DAILY)
    b = _write(tmp_path, "b.csv", DAILY2)
    for p in (a, b):
        assert cli.main(["import-text", "--src", str(p), "--code", "600030",
                         "--period", "day"]) == 0
    assert list(store.read("600030", "day")["ts"]) == ["2026-10-13", "2026-10-14"]


def test_replace_is_opt_in(tmp_path):
    """要整份替换必须显式 `--replace` —— 那是会**销毁**数据的操作。"""
    a = _write(tmp_path, "a.csv", DAILY)
    b = _write(tmp_path, "b.csv", DAILY2)
    cli.main(["import-text", "--src", str(a), "--code", "600030", "--period", "day"])
    cli.main(["import-text", "--src", str(b), "--code", "600030", "--period", "day",
              "--replace"])
    assert list(store.read("600030", "day")["ts"]) == ["2026-10-14"]


def test_minute_data_requires_an_explicit_period(tmp_path, capsys):
    """带时刻的数据**必须**给 `--period`。

    从时间戳只能看出「是不是分钟」，看不出「几分钟」。猜错会把 5 分钟静默存成日线，
    而两边看起来都「导入成功」—— 正是这个项目反复防的那种静默错误。
    """
    p = _write(tmp_path, "m.csv", MINUTE)
    rc = cli.main(["import-text", "--src", str(p), "--code", "600030"])
    assert rc == 2
    assert "看不出是几分钟" in capsys.readouterr().err
    assert store.read("600030", "day").empty, "拒绝必须发生在写库之前"


def test_minute_data_goes_to_the_named_period(tmp_path):
    p = _write(tmp_path, "m.csv", MINUTE)
    assert cli.main(["import-text", "--src", str(p), "--code", "600030",
                     "--period", "5"]) == 0
    assert list(store.read("600030", "5")["ts"]) == ["2026-10-13 09:35"]


def test_a_missing_file_is_a_clean_error(tmp_path, capsys):
    rc = cli.main(["import-text", "--src", str(tmp_path / "nope.csv"),
                   "--code", "600030", "--period", "day"])
    assert rc == 2 and "找不到文件" in capsys.readouterr().err


def test_an_unparsable_file_is_a_clean_error(tmp_path, capsys):
    p = _write(tmp_path, "x.csv", "这不是行情数据\n随便几行\n")
    rc = cli.main(["import-text", "--src", str(p), "--code", "600030", "--period", "day"])
    assert rc == 2 and "读不成 K 线" in capsys.readouterr().err


def test_adjust_is_recorded_not_guessed(tmp_path, capsys):
    """口径照实记录 —— 猜口径比不导入危险得多。"""
    from chanlun.data import meta
    p = _write(tmp_path, "a.csv", DAILY)
    cli.main(["import-text", "--src", str(p), "--code", "600030",
              "--period", "day", "--adjust", "2"])
    conn = meta.init()
    try:
        row = meta.get_sync(conn, "600030", "day")
    finally:
        conn.close()
    assert row is not None and str(row["adjust"]) == "2"
