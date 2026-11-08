"""申万行业板块日线源（`data/sw_source.py`）—— 腾讯 `newfqkline`。

这个文件钉住的是**四个实测出来的坑**，每一个都对应一次会静默算错的写法：

1. **行是 11 字段，且 `close` 在 `high`/`low` 之前**（索引 2 是 close、3 是 high、
   4 是 low）。按 `open,high,low,close` 读会把最高价当最低价，**不报任何错**。
2. **成交额在索引 8、单位万元**，要乘 10000（与 `tencent_source` 同一约定，
   但两个源各持一份常量，因为行形状不同）。
3. **返回键名必须是 `day`**。板块没有复权；哪天它开始回 `qfqday`，说明数据商把
   板块也纳入了复权分派 —— 那种漂移不会报错，只会往库里混进另一种口径的价格。
4. **`param` 第七位（复权词）必须留空位**：`...,5`（6 段）回 `bad params`，
   `...,5,`（7 段）才有数据。

护栏还有一条**顺序**上的要求：行数护栏判在 `truncate` **之前**（腾讯只约束
`end`、不约束 `start`，一页通常给 2000 根，增量同步裁完只剩二十几根 —— 那是
正确答案）。所以下面既测「1 行要拒」，也测「增量裁完很少行但数据商给了很多行要过」。

测试**不联网**：`_get_json` 是唯一的取数缝合点。
"""

from __future__ import annotations

import pandas as pd
import pytest

from chanlun.data import sw_source
from chanlun.data.types import DataSourceError

SYMBOL = "pt01801120"


def _row(ts: str, o: float, c: float, h: float, lo: float, v: float, amt: float) -> list:
    """一条实测形状的 11 字段行。索引 6 是空对象 `{}`。"""
    return [ts, f"{o:.2f}", f"{c:.2f}", f"{h:.2f}", f"{lo:.2f}", f"{v:.2f}", {}, "1.80", f"{amt:.2f}", "0.00", "0.00"]


def _payload(rows: list, symbol: str = SYMBOL) -> dict:
    return {"code": 0, "msg": "ok", "data": {symbol: {"day": rows, "prec": "13644.44", "version": "16"}}}


def _feed(monkeypatch, payloads):
    """按调用次序喂响应。`payloads` 用完后重复最后一个。"""
    calls: list[str] = []

    def fake(url: str, timeout: int = 30) -> dict:
        calls.append(url)
        idx = min(len(calls) - 1, len(payloads) - 1)
        got = payloads[idx]
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(sw_source, "_get_json", fake)
    return calls


def _many(n: int, newest: str = "2026-09-30") -> list:
    """造 n 条**升序**的行（数据商的行是最老在前，`rows[0]` 是最老那根）。

    实测：`param=pt01801120,day,,2026-09-30,2000,` 的 `rows[0][0]` 是
    `2018-07-06`、`rows[-1][0]` 是 `2026-09-30` —— 升序，不是倒序。
    """
    out = []
    for i in range(n):
        d = pd.Timestamp(newest) - pd.Timedelta(days=n - 1 - i)
        out.append(_row(d.strftime("%Y-%m-%d"), 100 + i, 101 + i, 102 + i, 99 + i, 1000 + i, 2502343.0))
    return out


# ---------------------------------------------------------------- 行形状
def test_the_row_fields_are_read_in_the_vendors_order_not_ohlc():
    """★ `close` 在 `high`/`low` 之前。写错不报错，只会让每根K线的高低互换。"""
    df = sw_source._rows_to_frame([_row("2024-02-07", 18234.74, 18526.96, 18700.00, 18191.70, 15615500.0, 3884437.0)])
    r = df.iloc[0]
    assert r["ts"] == "2024-02-07"
    assert r["open"] == 18234.74
    assert r["close"] == 18526.96
    assert r["high"] == 18700.00
    assert r["low"] == 18191.70
    assert r["volume"] == 15615500.0
    assert r["high"] >= r["close"] >= r["low"]


def test_amount_is_at_index_eight_and_scaled_from_wan_yuan():
    """索引 8 是成交额、单位**万元** ⇒ ×10000 才是元。

    实测对照：`sh000001` 的同一字段是 `49591987.81`，即 4959 亿元。
    """
    assert sw_source._AMOUNT_IDX == 8
    assert sw_source._AMOUNT_SCALE == 10_000.0
    df = sw_source._rows_to_frame([_row("2024-02-07", 1, 2, 3, 0.5, 10.0, 3884437.0)])
    assert df.iloc[0]["amount"] == 3884437.0 * 10_000.0


def test_a_missing_or_broken_amount_is_zero_not_an_error():
    """缺成交额不是错误 —— 成交量还在，`amount` 在 BarFrame 里允许为 0。"""
    short = ["2024-02-07", "1.00", "2.00", "3.00", "0.50", "10.00"]
    df = sw_source._rows_to_frame([short])
    assert df.iloc[0]["amount"] == 0.0

    bad = _row("2024-02-07", 1, 2, 3, 0.5, 10.0, 1.0)
    bad[8] = "n/a"
    df = sw_source._rows_to_frame([bad])
    assert df.iloc[0]["amount"] == 0.0


def test_malformed_rows_are_skipped_not_fatal():
    rows = [
        _row("2024-02-07", 1, 2, 3, 0.5, 10.0, 1.0),
        "not-a-row",
        ["2024-02-08", "x", "2", "3", "0.5", "10"],
        None,
    ]
    df = sw_source._rows_to_frame(rows)
    assert list(df["ts"]) == ["2024-02-07"]


def test_no_rows_at_all_gives_an_empty_frame_with_the_right_columns():
    df = sw_source._rows_to_frame([])
    assert df.empty
    assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume", "amount"]


def test_duplicate_dates_are_collapsed():
    """翻页边界会重叠一根（`end` 含当天），靠 `normalize` 去重。"""
    r = _row("2024-02-07", 1, 2, 3, 0.5, 10.0, 1.0)
    df = sw_source._rows_to_frame([r, list(r)])
    assert len(df) == 1


# ---------------------------------------------------------------- 响应形状
def test_the_expected_key_is_day_and_a_qfqday_is_refused():
    """★ 键名漂移**不会报错**，只会往库里混进另一种口径的价格，所以拦死。"""
    assert sw_source.EXPECTED_KEY == "day"
    assert sw_source._check_key({"day": [1, 2]}, SYMBOL) == [1, 2]
    with pytest.raises(DataSourceError, match="改了口径分派"):
        sw_source._check_key({"qfqday": [1, 2]}, SYMBOL)
    with pytest.raises(DataSourceError, match="没有日线键"):
        sw_source._check_key({"prec": "1"}, SYMBOL)


def test_a_list_in_data_is_an_error_not_an_attributeerror():
    """实测 `count>=2500` 时 `data` 是一个 JSON `list`，`data[symbol]` 会抛 TypeError。"""
    with pytest.raises(DataSourceError, match="data 不是对象"):
        sw_source._node({"msg": "param error", "data": []}, SYMBOL)
    with pytest.raises(DataSourceError, match="data 不是对象"):
        sw_source._node({}, SYMBOL)
    with pytest.raises(DataSourceError, match="缺少"):
        sw_source._node({"data": {}}, SYMBOL)
    with pytest.raises(DataSourceError, match="缺少"):
        sw_source._node({"data": {SYMBOL: []}}, SYMBOL)


def test_the_request_keeps_the_trailing_comma_and_never_sends_start(monkeypatch):
    """★ 七段、末段留空才有数据（`...,5` 回 `bad params`）。`start` 被完全忽略。"""
    calls = _feed(monkeypatch, [_payload(_many(40))])
    sw_source._page(SYMBOL, "2026-09-30")
    assert len(calls) == 1
    url = calls[0]
    assert url.startswith(sw_source.BOARD_KLINE + "?param=")
    assert f"param={SYMBOL},day,,2026-09-30,{sw_source.PAGE}," in url
    assert url.endswith(","), "末段（复权词）必须留一个空位"
    assert "qfq" not in url


# ---------------------------------------------------------------- 翻页
def test_paging_walks_end_backwards_without_overlap(monkeypatch):
    """★★ `end` 含当天、行是升序，所以下一页取「上一页**最老**那根 − 1 天」。

    实测 `pt01801120`：`2018-07-06..2026-09-30`(2000) +
    `2010-04-15..2018-07-05`(2000) + `2004-03-01..2010-04-14`(1492) = **5492 根，
    一根不重**。这条测试把那三次请求的关系钉死：第二页的 `end` 必须比第一页的
    最老一根**还早一天**（不是相等 —— 相等就会重叠，也不是差两天 —— 会漏一根）。
    """
    hist = _many(2000 + 1500)  # 3500 根升序历史
    calls = _feed(monkeypatch, [_payload(hist[-2000:]), _payload(hist[:-2000])])
    df = sw_source._paged_day(SYMBOL, "1990-01-01", "2026-09-30")
    assert len(calls) == 2
    oldest = hist[-2000][0]
    nxt = (pd.Timestamp(oldest) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    assert f",day,,{nxt},{sw_source.PAGE}," in calls[1], "必须往前挪一天"
    assert len(df) == 3500
    assert df["ts"].is_unique, "两页不许重叠"
    assert list(df["ts"]) == [r[0] for r in hist]


def test_paging_stops_as_soon_as_it_passes_the_requested_start(monkeypatch):
    """增量同步只要最近这一段，不该把全历史翻完。"""
    page1 = _many(sw_source.PAGE)
    calls = _feed(monkeypatch, [_payload(page1)])
    df = sw_source._paged_day(SYMBOL, page1[0][0], "2026-09-30")
    assert len(calls) == 1, "最老一根已经不早于 start，就该停"
    assert len(df) == sw_source.PAGE


def test_paging_stops_when_the_cursor_does_not_move(monkeypatch):
    """★ 没有这条会死循环：数据商回了一页不含更早数据的东西。"""
    same = _many(sw_source.PAGE)
    calls = _feed(monkeypatch, [_payload(same)])
    df = sw_source._paged_day(SYMBOL, "1990-01-01", "2026-09-30")
    assert len(calls) == 2, "第二页最老一根没有前移 ⇒ 停下（含第一次请求）"
    assert len(df) == sw_source.PAGE


def test_paging_never_exceeds_the_page_cap(monkeypatch):
    """`MAX_PAGES` 是死循环之外的第二道保险；到顶要留 warning，不许静默。

    把 `FLOOR` 挪到很古早，好让第 5 条停止条件（撞地板）不先触发 ——
    否则测到的是地板而不是页数上限。
    """
    monkeypatch.setattr(sw_source, "FLOOR", "1900-01-01")
    calls: list[str] = []

    def fake(url: str, timeout: int = 30) -> dict:
        calls.append(url)
        # param=<sym>,day,,<end>,2000,  ⇒ 第 4 段是 end
        end = url.split("param=", 1)[1].split(",")[3]
        return _payload(_many(sw_source.PAGE, newest=end))

    monkeypatch.setattr(sw_source, "_get_json", fake)
    df = sw_source._paged_day(SYMBOL, "1800-01-01", "2026-09-30")
    assert len(calls) == sw_source.MAX_PAGES
    assert len(df) == sw_source.PAGE * sw_source.MAX_PAGES


def test_paging_stops_at_the_floor_even_if_pages_keep_coming(monkeypatch):
    """第 5 条停止条件：再往前问没有意义（数据商最早的板块数据是 `2004-03-01`）。"""
    assert sw_source.FLOOR == "1990-01-01"
    calls: list[str] = []

    def fake(url: str, timeout: int = 30) -> dict:
        calls.append(url)
        end = url.split("param=", 1)[1].split(",")[3]
        return _payload(_many(sw_source.PAGE, newest=end))

    monkeypatch.setattr(sw_source, "_get_json", fake)
    sw_source._paged_day(SYMBOL, "1800-01-01", "2026-09-30")
    assert len(calls) < sw_source.MAX_PAGES, "该被地板拦住，而不是撞到页数上限"
    assert calls[-1].split("param=", 1)[1].split(",")[3] > sw_source.FLOOR


def test_an_empty_first_page_is_refused_loudly(monkeypatch):
    _feed(monkeypatch, [_payload([])])
    with pytest.raises(DataSourceError, match="一根都没取到"):
        sw_source._paged_day(SYMBOL, "1990-01-01", "2026-09-30")


# ---------------------------------------------------------------- fetch_bars
def test_only_daily_periods_are_accepted(monkeypatch):
    """板块只有日线（实测 `mkline` 对 `pt01…` 回 `param error`）。周/月由日线本地聚合。"""
    _feed(monkeypatch, [_payload(_many(40))])
    assert not sw_source.fetch_bars("sw.801120", SYMBOL, "day", "1990-01-01", "2026-09-30").empty
    assert not sw_source.fetch_bars("sw.801120", SYMBOL, "d", "1990-01-01", "2026-09-30").empty
    for bad in ("week", "month", "30", "5", "m30"):
        with pytest.raises(DataSourceError, match="只有日线"):
            sw_source.fetch_bars("sw.801120", SYMBOL, bad, "1990-01-01", "2026-09-30")


def test_a_silently_short_response_is_refused(monkeypatch):
    """★ 护栏拦的是「返回码正常但只回 1 根」那种静默假成功。"""
    _feed(monkeypatch, [_payload(_many(1))])
    with pytest.raises(DataSourceError, match=f"少于 {sw_source.MIN_ROWS}"):
        sw_source.fetch_bars("sw.801120", SYMBOL, "day", "1990-01-01", "2026-09-30")


def test_the_row_guard_runs_before_the_trim(monkeypatch):
    """★★ 顺序就是判据本身：数据商给了 2000 根、增量只要 3 天 ⇒ **必须过**。

    拿裁剪后的行数去比阈值，会把每一次正常的增量同步都判成失败
    （同一个坑在 `tencent_source._paged_day` 已经踩过一次）。
    """
    _feed(monkeypatch, [_payload(_many(sw_source.PAGE))])
    df = sw_source.fetch_bars("sw.801120", SYMBOL, "day", "2026-09-28", "2026-09-30")
    assert len(df) == 3, "裁完只剩 3 根是正确答案，不该报错"
    assert list(df["ts"]) == ["2026-09-28", "2026-09-29", "2026-09-30"]


def test_start_and_end_are_trimmed_locally(monkeypatch):
    """腾讯只约束 `end`。多给的更早数据要按 `start` 裁掉，否则增量起点形同虚设。"""
    _feed(monkeypatch, [_payload(_many(60))])
    df = sw_source.fetch_bars("sw.801120", SYMBOL, "day", "2026-09-01", "2026-09-10")
    assert list(df["ts"]) == [f"2026-09-{d:02d}" for d in range(1, 11)]


def test_the_result_is_a_normalized_bar_frame(monkeypatch):
    _feed(monkeypatch, [_payload(_many(40))])
    df = sw_source.fetch_bars("sw.801120", SYMBOL, "day", "1990-01-01", "2026-09-30")
    assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume", "amount"]
    assert df["ts"].is_monotonic_increasing
    assert (df["high"] >= df["low"]).all()
    assert (df["amount"] > 0).all()


def test_a_timestamp_with_a_time_part_is_trimmed_to_the_date(monkeypatch):
    """`truncate` 是纯字符串比较，`2026-09-30 15:00` 会被 `end=2026-09-30` 排掉 ——
    所以取数前先截成日期（板块本来也只有日线）。"""
    calls = _feed(monkeypatch, [_payload(_many(40))])
    sw_source.fetch_bars("sw.801120", SYMBOL, "day", "2026-09-01 00:00", "2026-09-30 00:00")
    assert ",day,,2026-09-30," in calls[0]


def test_a_network_failure_is_a_datasourceerror(monkeypatch):
    _feed(monkeypatch, [DataSourceError("proxy down")])
    with pytest.raises(DataSourceError, match="proxy down"):
        sw_source.fetch_bars("sw.801120", SYMBOL, "day", "1990-01-01", "2026-09-30")
