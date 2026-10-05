"""新浪美股源（spec §3.1 / §3.4 / §5.4 / D-43）。

美股走的是与腾讯完全不同的数据商与响应格式，而这个格式有两处**静默**陷阱：
JSONP 里的 `null` 是合法响应（不是错误），以及分钟 `ts` 自带秒
（会让 `truncate(end=日期)` 把最后一天整段切掉）。这两条都在这里钉住。
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from chanlun.data import sina_source as ss
from chanlun.data.types import DataSourceError


def _row(d: str, o: float = 100.0, h: float = 101.0, low: float = 99.0, c: float = 100.5) -> dict:
    return {"d": d, "o": o, "h": h, "l": low, "c": c, "v": 1234, "a": 5678}


def _jsonp(rows: list | None) -> str:
    return (
        "/*<script>location.href='//sina.com';</script>*/\n"
        f"var x=({json.dumps(rows)});"
    )


# ---------------- JSONP 外壳 ----------------
def test_parse_jsonp_accepts_a_null_body_as_zero_rows():
    """**`var x=(null);` 是合法响应**，不是错误。

    实测裸 `DJI`（不带点前缀）回的就是它 —— 代表「这个符号没有数据」。
    按零行处理，让 `sync` 记成 `skipped`，而不是让整只票崩成 `failed`
    （spec §3.4 坑 3）。
    """
    assert ss._parse_jsonp(_jsonp(None), "u") == []


def test_parse_jsonp_strips_the_location_href_prefix():
    rows = [_row("2026-10-05")]
    assert ss._parse_jsonp(_jsonp(rows), "u") == rows


def test_parse_jsonp_rejects_an_unexpected_shape():
    with pytest.raises(DataSourceError, match="不是预期的 JSONP 形状"):
        ss._parse_jsonp("<html>502 Bad Gateway</html>", "u")


def test_parse_jsonp_rejects_a_non_array_payload():
    with pytest.raises(DataSourceError, match="不是数组"):
        ss._parse_jsonp("var x=({});", "u")


# ---------------- 行解析 ----------------
def test_rows_to_frame_maps_the_short_field_names():
    df = ss._rows_to_frame([_row("2026-10-05", o=1.0, h=3.0, low=4.0, c=2.0)])
    r = df.iloc[0]
    assert (float(r["open"]), float(r["close"]), float(r["high"]), float(r["low"])) == (
        1.0,
        2.0,
        3.0,
        4.0,
    )
    assert float(r["volume"]) == 1234.0
    assert float(r["amount"]) == 5678.0


def test_rows_to_frame_trims_the_seconds_off_a_minute_ts():
    """分钟线 `ts` 口径必须与 A 股一致：`YYYY-MM-DD HH:MM`。

    新浪给 `"2026-06-11 12:00:00"`，而落库的 A 股 30 分文件实测是
    `['2020-01-02 10:00', …]`。留着秒不会当场出错，但
    `types.truncate(df, end="2026-10-05")` 是按字符串比的 ——
    `"2026-10-05 16:00:00" <= "2026-10-05"` 是 False，最后一天的分时会整段消失。
    """
    df = ss._rows_to_frame([_row("2026-10-05 16:00:00")])
    assert str(df["ts"].iloc[0]) == "2026-10-05 16:00"


def test_a_us_minute_frame_matches_the_a_share_ts_convention():
    """上一条的**后果**：同一个 `(code, period)` 在不同市场必须是同一个 `ts` 口径。

    A 股 30 分的 `ts` 是 `baostock_source._rows_to_frame` 拼的
    `f"{date_s} {hm}"`（`hm` 只有 `HH:MM`）。美股如果不切秒，同一个周期就有
    两种宽度的时间戳 —— 任何按字符串比时间的下游（`types.truncate`、
    `store.read(start=…, end=…)`）都会对两个市场给出不同答案。

    这里同时钉住一条**既有**性质：`truncate(end=<纯日期>)` 对**任何**市场的
    分时 bar 都留不住当天那几根（`'2026-10-05 09:30' <= '2026-10-05'` 是 False，
    因为比到第 10 位后短串更小）。这不是美股引入的缺陷，所以两个市场必须
    **同样**表现 —— 断言写成两边一致，而不是单边期望。
    """
    from chanlun.data import baostock_source as bs, types

    us = ss._rows_to_frame([_row("2026-10-05 09:30:00"), _row("2026-10-05 16:00:00")])
    a = bs._rows_to_frame([["2026-10-05", "20261005093000000", "1", "2", "0.5", "1.5", "10", "20"]],
                          is_minute=True)
    assert [len(str(t)) for t in us["ts"]] == [16, 16]
    assert [len(str(t)) for t in a["ts"]] == [16]

    kept_us = list(types.truncate(us, end="2026-10-05")["ts"])
    kept_a = list(types.truncate(a, end="2026-10-05")["ts"])
    assert kept_us == kept_a == [], "日期上界留不住分时 bar —— 两个市场一致地留不住"


def test_rows_to_frame_leaves_a_day_ts_alone():
    df = ss._rows_to_frame([_row("2026-10-05")])
    assert str(df["ts"].iloc[0]) == "2026-10-05"


def test_rows_to_frame_skips_malformed_rows():
    df = ss._rows_to_frame([_row("2026-10-05"), {"o": 1}, "nope", _row("2026-10-06")])
    assert list(df["ts"]) == ["2026-10-05", "2026-10-06"]


# ---------------- fetch_bars：口径与周期 ----------------
@pytest.mark.parametrize("adjust", ["2", "1"])
def test_fetch_bars_refuses_to_label_raw_prices_as_adjusted(adjust):
    """实测 AAPL 在 2020-08-31（4:1）与 2014-06-09（7:1）拆股日**价格直接跳变**，
    说明新浪没有做任何回溯调整 ⇒ 只有不复权可用。"""
    with pytest.raises(DataSourceError, match="只有不复权价"):
        ss.fetch_bars("us.aapl", "AAPL", "day", "2026-01-01", "2026-10-05", adjust=adjust)


@pytest.mark.parametrize("period", ["week", "month", "w", "m"])
def test_fetch_bars_rejects_derived_periods(period):
    with pytest.raises(DataSourceError, match="派生周期"):
        ss.fetch_bars("us.aapl", "AAPL", period, "2026-01-01", "2026-10-05", adjust="3")


def test_fetch_bars_rejects_an_unknown_period():
    with pytest.raises(DataSourceError, match="不支持周期"):
        ss.fetch_bars("us.aapl", "AAPL", "120", "2026-01-01", "2026-10-05", adjust="3")


def _stub(monkeypatch, rows: list, seen: dict | None = None) -> None:
    def fake_get_text(url: str, timeout: int = 30) -> str:
        if seen is not None:
            seen["url"] = url
        return _jsonp(rows)

    monkeypatch.setattr(ss, "_get_text", fake_get_text)


def test_fetch_bars_requests_getdailyk_for_day(monkeypatch):
    seen: dict[str, str] = {}
    _stub(monkeypatch, [_row(f"2026-09-{d:02d}") for d in range(1, 31)], seen)
    ss.fetch_bars("us.aapl", "AAPL", "day", "2026-09-01", "2026-09-30", adjust="3")
    assert "US_MinKService.getDailyK" in seen["url"]
    assert "symbol=AAPL" in seen["url"] and "type=240" in seen["url"]


def test_fetch_bars_requests_getmink_with_the_period_as_type(monkeypatch):
    seen: dict[str, str] = {}
    _stub(monkeypatch, [_row(f"2026-09-{d:02d} 10:00:00") for d in range(1, 31)], seen)
    ss.fetch_bars("us.aapl", "AAPL", "30", "2026-09-01", "2026-09-30", adjust="3")
    assert "US_MinKService.getMinK" in seen["url"]
    assert "type=30" in seen["url"]


def test_fetch_bars_keeps_the_final_day_of_minute_bars(monkeypatch):
    """区间裁剪必须按**日期**比，不能整串比。

    `"2026-10-05 12:00:00" <= "2026-10-05"` 是 False —— 整串比会把结束日
    当天的全部分钟判成越界，表现为「最后一天的分时整段消失」。
    """
    rows = [_row(f"2026-09-{d:02d} 10:00:00") for d in range(1, 31)]
    rows.append(_row("2026-10-05 09:30:00"))
    rows.append(_row("2026-10-05 16:00:00"))
    _stub(monkeypatch, rows)
    out = ss.fetch_bars("us.aapl", "AAPL", "30", "2026-09-01", "2026-10-05", adjust="3")
    assert str(out["ts"].iloc[-1]) == "2026-10-05 16:00"


def test_fetch_bars_measures_the_row_guard_before_local_trimming(monkeypatch):
    """`getDailyK` 总是给全历史、`getMinK` 的区间参数被忽略 ⇒ 区间只能本地裁。

    阈值判在裁**之前**：一个 3 天的增量窗口裁完只剩 3 根，那是正确答案。
    """
    rows = [_row(f"2026-09-{d:02d}") for d in range(1, 31)]
    _stub(monkeypatch, rows)
    out = ss.fetch_bars("us.aapl", "AAPL", "day", "2026-09-28", "2026-09-30", adjust="3")
    assert list(out["ts"]) == ["2026-09-28", "2026-09-29", "2026-09-30"]


def test_fetch_bars_still_rejects_a_near_empty_response(monkeypatch):
    """护栏要拦的是「返回码正常但只有几根」的静默假成功。"""
    _stub(monkeypatch, [_row("2026-10-05")])
    with pytest.raises(DataSourceError, match="少于 30"):
        ss.fetch_bars("us.aapl", "AAPL", "day", "2026-01-01", "2026-10-05", adjust="3")


def test_fetch_bars_treats_a_null_body_as_a_row_guard_failure(monkeypatch):
    """`var x=(null);` ⇒ 零行 ⇒ 走行数护栏，而不是让 `normalize` 吃一个空表。"""
    _stub(monkeypatch, None)
    with pytest.raises(DataSourceError, match="只回 0 行"):
        ss.fetch_bars("us.dji", ".DJI", "day", "2026-01-01", "2026-10-05", adjust="3")


def test_minute_hard_cap_is_the_measured_number():
    """实测 `datalen`/`num`/`date`/`from`/`limit`/`p`/`page` 怎么传都返回
    **逐字节相同**的 1023 根窗口 ⇒ 美股分钟约 3.8 个月，**不能翻页**。
    页面上要如实标注这个上限。"""
    assert ss.MINUTE_HARD_CAP == 1023


def test_fetch_bars_accepts_a_date_object_for_start_and_end(monkeypatch):
    _stub(monkeypatch, [_row(f"2026-09-{d:02d}") for d in range(1, 31)])
    out = ss.fetch_bars(
        "us.aapl", "AAPL", "day", dt.date(2026, 9, 28), dt.date(2026, 9, 30), adjust="3"
    )
    assert list(out["ts"]) == ["2026-09-28", "2026-09-29", "2026-09-30"]
