"""Task 24：日线可切通达信整包数据源。

为什么要有这组用例：`daily` 原来在 `--source tdx` 下**直接报错退出**（占位符，不静默降级），
而切换数据源不是「换个 URL」——它同时改变**落库口径**（前复权 → 不复权）。口径错了不会报错，
只会让每只除权股在除权日长出一根假跳空，笔/线段/中枢跟着全错。所以这里逐条钉死：
`day` 走整包、`30/5` 仍走 baostock、`sync_state.adjust` 记成 `raw`、摘要如实报数据源。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import struct
from pathlib import Path

import pandas as pd
import pytest

from chanlun import __main__ as cli
from chanlun.config import load_config
from chanlun.data import meta, store, tdx


# ---------------- 配置 ----------------


def test_data_source_defaults_to_baostock(tmp_path, monkeypatch):
    monkeypatch.setattr("chanlun.config.PROJECT_ROOT", tmp_path)
    assert load_config().data.source == "baostock"


def test_data_source_read_from_toml(tmp_path, monkeypatch):
    monkeypatch.setattr("chanlun.config.PROJECT_ROOT", tmp_path)
    (tmp_path / "config.toml").write_text(
        '[data]\nroot = "data"\nsource = "tdx"\n', encoding="utf-8"
    )
    assert load_config().data.source == "tdx"


def test_daily_source_flag_overrides_config():
    args = cli.build_parser().parse_args(["daily", "--source", "baostock"])
    assert args.source == "baostock"


def test_config_toml_ships_the_conservative_default():
    """仓库里的 config.toml 必须还是 baostock：切源是**显式决定**，不能靠改配置文件的时机偷偷发生。"""
    text = (Path(__file__).resolve().parents[1] / "config.toml").read_text(encoding="utf-8")
    assert 'source = "baostock"' in text
    assert 'source = "tdx"' not in text


# ---------------- 周期 → 数据源 ----------------


def test_only_day_can_come_from_the_package():
    """分钟周期永远走 baostock：通达信没有公开的分钟整包。"""
    assert cli._period_source("day", "tdx") == "tdx"
    assert cli._period_source("day", "baostock") == "baostock"
    assert cli._period_source("30", "tdx") == "baostock"
    assert cli._period_source("5", "tdx") == "baostock"


# ---------------- 整包导入 ----------------


def _day_file(root: Path, key: str, closes: list[float], start="2026-01-05") -> Path:
    """写一个最小 `.day`（32 字节/条：date/open/high/low/close(分) + f32 amount + u32 volume + u32）。"""
    market, digits = key.split(".") if "." in key else ("sh", key)
    folder = root / market / "lday"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{market}{digits}.day"
    days = pd.bdate_range(start, periods=len(closes))
    blob = b"".join(
        struct.pack(
            "<IIIIIfII",
            int(d.strftime("%Y%m%d")),
            # 必须 round 不能 int：int(10.2*100) == 1019，会造出 10.19 这种假价格
            round(c * 100), round(c * 100), round(c * 100), round(c * 100),
            1.0e6, 10_000, 0,
        )
        for d, c in zip(days, closes)
    )
    path.write_bytes(blob)
    return path


@pytest.fixture
def tdx_tree(tmp_path) -> Path:
    root = tmp_path / "tdx_raw"
    _day_file(root, "sh.600030", [10.0, 10.1, 10.2, 10.3])
    return root


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path / "data")
    c = meta.init(tmp_path / "data" / "meta.db")
    yield c
    c.close()


def test_sync_day_tdx_downloads_extracts_and_marks_raw(conn, tdx_tree, monkeypatch):
    """`day` 走整包：下载 → 解压 → 导入，并且**口径记为 raw**（这是切源的全部风险所在）。"""
    calls: list[str] = []
    monkeypatch.setattr(
        tdx, "fetch_package",
        lambda dest, url=tdx.DAY_URL: (calls.append(f"fetch:{Path(dest).name}"),
                                       Path(dest) / "hsjday.zip")[1],
    )
    monkeypatch.setattr(
        tdx, "extract_package",
        lambda zip_path, dest: (calls.append("extract"), 1)[1],
    )
    out = cli._sync_day_tdx(conn, tdx_root=tdx_tree, codes=None, dry_run=False)
    assert calls == ["fetch:tdx_raw", "extract"]
    assert (out.ok, out.failed) == (1, 0)
    assert out.total == 1
    # 落库口径：raw，不是 baostock 的 "2"
    # 键用**规范化后的 store 键**："6" 开头裸码已能唯一定位沪市，store_key 就不加前缀，
    # 于是 sync_state 里这一行是 "600030" 而不是 "sh.600030"（meta.get_sync 不做归一化）
    row = meta.get_sync(conn, "600030", "day")
    assert row["adjust"] == "raw"
    df = store.read("600030", "day")
    assert list(df["close"]) == [10.0, 10.1, 10.2, 10.3]


def test_sync_day_tdx_reuses_the_local_package_when_download_is_off(conn, tdx_tree, monkeypatch):
    """`--no-download`：只解压导入，不联网。整包当天已经下过一遍时不该再拉 551 MB。"""
    monkeypatch.setattr(
        tdx, "fetch_package",
        lambda *a, **k: pytest.fail("不该联网下载"),
    )
    monkeypatch.setattr(tdx, "extract_package", lambda zip_path, dest: 1)
    out = cli._sync_day_tdx(conn, tdx_root=tdx_tree, codes=None, dry_run=False, download=False)
    assert out.ok == 1
    assert meta.get_sync(conn, "600030", "day")["adjust"] == "raw"


def test_sync_day_tdx_dry_run_writes_nothing(conn, tdx_tree, monkeypatch):
    monkeypatch.setattr(tdx, "extract_package", lambda zip_path, dest: 1)
    out = cli._sync_day_tdx(conn, tdx_root=tdx_tree, codes=None, dry_run=True, download=False)
    assert out.ok == 1
    assert meta.get_sync(conn, "600030", "day") is None
    assert not store.exists("600030", "day")


def test_sync_day_tdx_reports_a_missing_tree_instead_of_crashing(conn, tmp_path):
    out = cli._sync_day_tdx(conn, tdx_root=tmp_path / "nope", codes=None,
                            dry_run=False, download=False)
    assert (out.ok, out.total) == (0, 0)
    assert out.first_error, "空目录必须留下可读的原因，而不是安静地报成功 0"


def test_sync_day_tdx_filters_to_the_requested_codes(conn, tdx_tree, monkeypatch):
    _day_file(tdx_tree, "sz.000001", [20.0, 20.1])
    monkeypatch.setattr(tdx, "extract_package", lambda zip_path, dest: 2)
    out = cli._sync_day_tdx(conn, tdx_root=tdx_tree, codes=["600030"],
                            dry_run=False, download=False)
    assert out.ok == 1
    assert store.exists("sh.600030", "day")
    assert not store.exists("sz.000001", "day")


def test_daily_source_falls_back_to_baostock():
    """三级回落：命令行 → 配置 → `"baostock"`。最后一跳是**保守默认**，
    它保证「配置里漏了 source」不会静默改成不复权整包。"""
    from types import SimpleNamespace

    assert cli._daily_source(argparse_namespace({}), SimpleNamespace(data=SimpleNamespace())) \
        == "baostock"


def test_empty_codes_list_still_imports_the_whole_package(conn, tdx_tree, monkeypatch):
    """`codes` 为空 = 全导（不是「导 0 只」）。空列表必须和 None 同义：
    `--codes ""` 这种写法不该把整包变成一次空跑。"""
    _day_file(tdx_tree, "sh.000300", [3000.0, 3010.0])
    monkeypatch.setattr(tdx, "extract_package", lambda zip_path, dest: 2)
    out = cli._sync_day_tdx(conn, tdx_root=tdx_tree, codes=[], dry_run=False, download=False)
    assert out.ok == 2


def test_filtering_by_a_prefixed_key_reaches_the_index(conn, tdx_tree, monkeypatch):
    """`--codes sh.000300` 是用户区分「沪深300 指数」与「sz 000300」的唯一写法。

    裸码 `000300` 归深市（`markets.market_of`），指数因此只能靠 store 键选中；
    过滤器若只比裸码，这个键就永远选不中任何东西 —— 用户会看到「成功 0 只」。
    """
    _day_file(tdx_tree, "sh.000300", [3000.0, 3010.0])
    monkeypatch.setattr(tdx, "extract_package", lambda zip_path, dest: 2)
    out = cli._sync_day_tdx(conn, tdx_root=tdx_tree, codes=["sh.000300"],
                            dry_run=False, download=False)
    assert out.ok == 1
    assert store.exists("sh.000300", "day")
    assert not store.exists("sh.600030", "day")


def test_switch_note_warns_before_overwriting_the_qfq_copy(conn):
    """切源前必须把**不可逆**的那一半说出来：除权因子以后没法再由库内数据反推。

    反推需要同一只票同时有前复权与不复权两份数据；日线一旦改由整包写，前复权那份
    就被覆盖了。所以备注不是客套话，是「现在不做就再也做不了」的提示。
    """
    assert cli._switch_note(conn, "baostock") == ""
    assert cli._switch_note(conn, "tdx") == "", "库里没有前复权数据就没什么可提醒的"
    meta.set_sync(conn, "600030", "day", "2020-01-02", "2026-09-30", 100, "2")
    note = cli._switch_note(conn, "tdx")
    assert "1 只" in note, note
    assert "无法再反推" in note, note


def test_outcome_reports_each_market_newest_separately():
    """北交所实测落后沪深一整年；报一个笼统的「最新日期」会把这件事抹掉。"""
    out = cli.SyncOutcome(period="day", ok=2, total=2,
                          newest=(("bj", "2025-09-30"), ("sh", "2026-09-30")))
    text = out.summary()
    assert "bj 2025-09-30" in text
    assert "sh 2026-09-30" in text


# ---------------- 编排 ----------------


def test_the_whole_market_import_is_not_narrowed_to_the_universe(conn, tdx_tree, monkeypatch):
    """不给 `--codes` 时，整包要**全导**（个股 + 指数）：一期品种表只有 baostock 的 5471 只，
    拿它当过滤器会把通达信里多出来的指数挡在门外，而整包的钱已经花了。"""
    _day_file(tdx_tree, "sh.000300", [3000.0, 3010.0])
    monkeypatch.setattr(tdx, "extract_package", lambda zip_path, dest: 2)
    out = cli._sync_day_tdx(conn, tdx_root=tdx_tree, codes=None, dry_run=False, download=False)
    assert out.ok == 2
    assert store.exists("sh.000300", "day")


class _Cal:
    def last_trading_day(self, d):
        return d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d))


def _args(**over):
    # codes 必须显式给：不给会走 build_universe()，空库时它要联网登录 baostock，
    # 测试会挂在网络上（这跟数据源路由无关，是另一条路径）
    base = dict(dry_run=False, periods=("day",), codes="600030", since=None, workers=1,
                source="tdx", tdx_src=None, no_download=True, notify="null",
                notify_path=None, tdx_markets=None, tdx_kinds=None)
    base.update(over)
    return argparse_namespace(base)


def argparse_namespace(d):
    import argparse
    return argparse.Namespace(**d)


@pytest.fixture
def wired(monkeypatch, conn, tdx_tree, tmp_path):
    """把 `_run_daily` 里与本次无关的步骤全部桩掉，只留数据源路由可观测。"""
    cfg = dataclasses.replace(
        load_config(), data=dataclasses.replace(load_config().data, root=tmp_path / "data")
    )
    monkeypatch.setattr(cli, "get_calendar", lambda refresh=False: _Cal())
    monkeypatch.setattr(cli, "_run_quality", lambda: (0, 0, "", {}))
    monkeypatch.setattr(cli, "_snapshot_all", lambda *a, **k: (0, 0))
    monkeypatch.setattr(cli, "track_watchlist", lambda *a, **k: ())
    monkeypatch.setattr(cli, "notify_scan", lambda *a, **k: None)
    monkeypatch.setattr(cli, "notify_track", lambda *a, **k: None)
    monkeypatch.setattr(cli, "scan_report", lambda **k: cli.ScanReport(
        tasks=0, hits=(), skipped=(), errors=(), elapsed=0.0))
    monkeypatch.setattr(tdx, "extract_package", lambda zip_path, dest: 1)
    return cfg


def test_daily_with_tdx_source_runs_the_package_for_day_only(wired, conn, tdx_tree, monkeypatch):
    seen: list[str] = []

    def spy_period(conn_, cfg_, period, **kw):
        # 绝不委托真实实现：它会去连 baostock，测试会挂在网络上
        seen.append(period)
        return cli.SyncOutcome(period=period)

    monkeypatch.setattr(cli, "_sync_period", spy_period)
    # `--no-download` 必须一路传到 fetch：否则每天都重拉 551 MB
    monkeypatch.setattr(tdx, "fetch_package", lambda *a, **k: pytest.fail("--no-download 不该联网"))
    args = _args(source="tdx", periods=("day", "30"), tdx_src=str(tdx_tree))
    rep = cli._run_daily(args, wired, conn)
    assert [o.period for o in rep.sync] == ["day", "30"]
    # day 由整包写，30 分仍走 baostock
    assert seen == ["30"], "day 不该再走 baostock 逐只抓取"
    assert store.exists("600030", "day")
    assert meta.get_sync(conn, "600030", "day")["adjust"] == "raw"
    # 只换了一半的源，摘要就必须说清另一半还是 baostock
    # （断言到具体串：降级说明里也会出现「分钟」，只查两个字会假绿）
    assert "分钟=baostock 前复权" in cli._format_daily(rep)


def test_daily_summary_names_the_actual_source(wired, conn, tmp_path, monkeypatch):
    """摘要里那行「数据源」是运维唯一的线索：切了源还写「baostock 前复权」就是撒谎。"""
    monkeypatch.setattr(cli, "_sync_period", lambda *a, **k: cli.SyncOutcome(period="30"))
    rep = cli._run_daily(_args(source="tdx", periods=("day",), tdx_src=str(tmp_path)), wired, conn)
    text = cli._format_daily(rep)
    assert "通达信整包" in text
    assert "不复权" in text
    assert "baostock 前复权" not in text

    rep2 = cli._run_daily(_args(source="baostock", periods=("day",)), wired, conn)
    assert "baostock 前复权" in cli._format_daily(rep2)
