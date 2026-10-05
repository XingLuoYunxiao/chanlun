"""腾讯港股源（spec §3.2 / §5.4 / §6.3 / §7.1.1 / D-43）。

这个模块的每一条错法都是**静默**的：字段序读错会把 high 当 low、
控制器选错会把后复权价当成不复权存、翻页翻错会留下重叠或空洞、
返回键名漂移会让库里混进另一种口径。所以这些断言全部在**网络之外**跑，
只喂构造好的响应体。
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from chanlun.data import tencent_source as ts
from chanlun.data.types import DataSourceError


def _row(day: str, close: str = "439.800", **kw) -> list:
    """一行腾讯 K 线：`[ts, open, close, high, low, volume, {…}]`（**close 在 high 之前**）。"""
    return [
        day,
        kw.get("open", "441.400"),
        close,
        kw.get("high", "447.000"),
        kw.get("low", "438.600"),
        kw.get("volume", "15335550.000"),
        {},
    ]


def _payload(symbol: str, key: str, rows: list) -> dict:
    return {"code": 0, "data": {symbol: {key: rows, "qt": {}}}}


# ---------------- 第二道护栏：返回键名必须是 `day` ----------------
def test_check_key_rejects_a_reweighted_key(monkeypatch):
    """【红】`fqkline` 的复权词被忽略、键名恒为 `day`。

    一旦它开始回 `qfqday`/`hfqday`，说明数据商改成了「按复权词选口径」，
    我们落库的就不再是不复权价了 —— 这种漂移**不会报错**，只会让库里
    混进另一种口径的价格，所以必须在取数层拦死（spec §7.1.1）。
    """
    node = {"qfqday": [_row("2026-10-05")]}
    with pytest.raises(DataSourceError, match="期望 'day'"):
        ts._check_key(node, "hk00700")


def test_check_key_accepts_the_degraded_key_of_an_index(monkeypatch):
    """【负控】指数走 `hkfqkline` 时返回键会**降级**成 `day`。

    实测 `hkHSI`/`hkHSTECH` 在 `hkfqkline` 上回的键名是 `day` 而不是 `qfqday`
    （spec §3.1）。护栏只该拦「键名变了」，不该拦「指数回 `day`」——
    否则港股指数一根都取不到。这条断言是上一条的负控。
    """
    rows = [_row("2026-10-05")]
    assert ts._check_key({"day": rows}, "hkHSI") is rows


def test_check_key_reports_a_missing_day_key():
    with pytest.raises(DataSourceError, match="没有日线键"):
        ts._check_key({"qt": {}}, "hk00700")


def test_node_rejects_a_list_shaped_data_field():
    """实测 `n>=3000` 时 `data` 直接是 JSON `list`，`data[symbol]` 会抛 `TypeError`。"""
    with pytest.raises(DataSourceError, match="不是对象"):
        ts._node({"data": [1, 2, 3]}, "hk00700")


# ---------------- 行解析：字段序与成交额 ----------------
def test_rows_to_frame_reads_close_before_high_and_low():
    """腾讯行是 `[ts, open, close, high, low, volume]`。

    按 `open,high,low,close` 读会把 high 当 low，**且不会报任何错** ——
    画出来的 K 线上下影线反了。
    """
    df = ts._rows_to_frame([_row("2026-10-05", open="1", close="2", high="3", low="4", volume="5")])
    r = df.iloc[0]
    assert (float(r["open"]), float(r["close"]), float(r["high"]), float(r["low"])) == (
        1.0,
        2.0,
        3.0,
        4.0,
    )


def test_rows_to_frame_leaves_amount_zero_on_a_seven_field_row():
    """`fqkline`（本模块用的控制器）的行只有 7 位，**没有成交额**。

    所以港股 `amount` 恒为 0 —— 这不是占位符，是数据商确实没给。
    """
    df = ts._rows_to_frame([_row("2026-10-05")])
    assert float(df["amount"].iloc[0]) == 0.0


def test_rows_to_frame_scales_amount_from_wan_yuan_on_a_nine_field_row():
    """`hkfqkline` 的行是 9 位，第 8 位是成交额（**万元**）。

    实测行形如 `[…, {…}, '0.170', '677406.336']` —— 第 8 位才是成交额，
    所以它必须乘 1e4 才和 `volume` 同量纲（`×1e4/volume = 428.749`，
    落在当根 `[low, high]` 之内）。
    """
    row = _row("2026-10-05") + ["0.170", "677406.336"]
    assert len(row) == 9
    df = ts._rows_to_frame([row])
    assert float(df["amount"].iloc[0]) == pytest.approx(677406.336 * 10_000.0)


def test_rows_to_frame_skips_malformed_rows_instead_of_raising():
    rows = [_row("2026-10-05"), ["2026-10-06", "x"], "not-a-row", _row("2026-10-07")]
    df = ts._rows_to_frame(rows)
    assert list(df["ts"]) == ["2026-10-05", "2026-10-07"]


# ---------------- fetch_bars：周期与口径的守卫 ----------------
@pytest.mark.parametrize("period", ["week", "month", "30", "5", "60"])
def test_fetch_bars_rejects_every_period_but_day(period):
    """周/月由调用方从日线聚合；港股分钟线**不可得**（spec §3.2）。"""
    with pytest.raises(DataSourceError, match="只提供日线"):
        ts.fetch_bars("hk.00700", "hk00700", period, "2026-01-01", "2026-10-05", adjust="3")


@pytest.mark.parametrize("adjust", ["2", "1"])
def test_fetch_bars_refuses_to_label_raw_prices_as_adjusted(adjust):
    """只有不复权可用。**报错而不是静默给原始价** —— 静默替换口径会让库里
    混进两套价格，比直接失败危险得多（spec §5.4 / D-43）。"""
    with pytest.raises(DataSourceError, match="只有不复权价"):
        ts.fetch_bars("hk.00700", "hk00700", "day", "2026-01-01", "2026-10-05", adjust=adjust)


# ---------------- 翻页：从 `end` 往回走 ----------------
def _fake_vendor(series: list[str]):
    """造一个只认 `end` 的假数据商：回「以 `end` 结尾的最后 PAGE 根」。"""
    import pandas as pd

    calls: list[str] = []

    def fake_page(symbol: str, end: str) -> list:
        calls.append(end)
        upto = [d for d in series if d <= end]
        return [_row(d) for d in upto[-ts.PAGE :]]

    return fake_page, calls


def test_paged_day_walks_end_backwards_and_collects_every_bar(monkeypatch):
    """【红】`fqkline` 与 `hkfqkline` 一样**只把 `end` 当约束**。

    实测把 `start` 往前挪着翻会被完全无视（永远只拿到最后 640 根），
    所以翻页只能从 `end` 往回走（spec §6.3）。
    """
    days = []
    d = dt.date(2004, 6, 16)
    while d <= dt.date(2008, 6, 30):
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    assert len(days) > ts.PAGE, "构造的序列必须跨多页"

    fake_page, calls = _fake_vendor(days)
    monkeypatch.setattr(ts, "_page", fake_page)
    df = ts._paged_day("hk00700", days[0], days[-1])

    assert list(df["ts"]) == days, "不许有空洞、不许有重叠"
    assert len(calls) > 1, "跨多页的区间必须真的翻了页"
    assert calls[1] < calls[0], "第二页的 `end` 必须比第一页更早（往回走）"


def test_paged_day_measures_the_guard_before_local_trimming(monkeypatch):
    """护栏量的是**数据商给了多少**，不是**我们最后用了多少**。

    腾讯只约束 `end`，一页通常给 640 根；增量同步只要 23 个交易日就会被裁成
    23 根。**23 根是正确答案** —— 拿裁剪后的行数去比阈值会把每一次正常的
    增量同步都判成失败（实测 `hk.00700` 增量窗口 `2026-09-01..2026-10-05`
    得 24 根，被旧写法误报成「少于 30，拒绝落库」）。
    """
    days = [d for d in (dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(400))
            if d.weekday() < 5]
    iso = [d.isoformat() for d in days]
    fake_page, _ = _fake_vendor(iso)
    monkeypatch.setattr(ts, "_page", fake_page)

    out = ts._paged_day("hk00700", iso[-24], iso[-1])
    assert len(out) == 24, "裁剪后只有 24 根，但这是正确结果，不该报错"


def test_paged_day_still_rejects_a_single_bar(monkeypatch):
    """护栏真正要拦的是「返回码正常但只有 1 根」那种静默假成功（spec §3.2 (a2)）。

    通用 `fqkline` 对港股**分钟**请求返回 `code=0` 却只有 1 根 bar ——
    只看返回码就会把 1 根当「30 分钟数据」写进库，页面画出 1 根 K 线。
    """
    monkeypatch.setattr(ts, "_page", lambda symbol, end: [_row("2026-10-05")])
    with pytest.raises(DataSourceError, match="少于 30"):
        ts._paged_day("hk00700", "2026-01-01", "2026-10-05")


def test_paged_day_stops_when_the_vendor_repeats_itself(monkeypatch):
    """`end` 无效时数据商把同一段又给一遍 —— 再翻也不会前进，必须停。

    构造 40 根（> `MIN_ROWS["day"]`）才能越过行数护栏、真正走到停滞分支。
    """
    days = [(dt.date(2026, 8, 1) + dt.timedelta(days=i)).isoformat() for i in range(40)]
    rows = [_row(d) for d in days]
    calls: list[str] = []

    def fake_page(symbol: str, end: str) -> list:
        calls.append(end)
        return rows

    monkeypatch.setattr(ts, "_page", fake_page)
    df = ts._paged_day("hk00700", "2026-01-01", "2026-10-05")
    assert len(df) == 40
    assert len(calls) == 2, "第二页原样重放 ⇒ 立刻停，不许死翻到 MAX_PAGES"


def test_paged_day_raises_when_the_vendor_returns_nothing(monkeypatch):
    monkeypatch.setattr(ts, "_page", lambda symbol, end: [])
    with pytest.raises(DataSourceError, match="一根都没取到"):
        ts._paged_day("hk00700", "2026-01-01", "2026-10-05")


# ---------------- 端点分派只有一份实现 ----------------
def test_plan_reports_the_raw_controller_and_expected_key():
    url, word, key = ts.plan()
    assert url == ts.HK_RAW_KLINE
    assert url.endswith("/fqkline/get"), "必须用忽略复权词的 fqkline，不是 hkfqkline"
    assert key == "day"


def test_page_requests_the_pinned_page_size_and_a_wide_start():
    """`start` 固定成 1990-01-01：腾讯只把 `end` 当约束，宽区间无副作用。"""
    seen: dict[str, str] = {}

    def fake_get(url: str) -> dict:
        seen["url"] = url
        return _payload("hk00700", "day", [_row("2026-10-05")])

    import chanlun.data.tencent_source as mod

    orig = mod._get_json
    mod._get_json = fake_get
    try:
        mod._page("hk00700", "2026-10-05")
    finally:
        mod._get_json = orig

    assert f",{ts.PAGE}," in seen["url"], "每页行数必须是实测验证过的 640"
    assert "1990-01-01" in seen["url"]
    assert "2026-10-05" in seen["url"]


def test_get_json_reports_a_non_json_body(monkeypatch):
    class _Resp:
        def read(self) -> bytes:
            return b"<html>nope</html>"

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *a):  # noqa: ANN002
            return False

    monkeypatch.setattr(ts.urllib.request, "urlopen", lambda *a, **k: _Resp())
    with pytest.raises(DataSourceError, match="不是 JSON"):
        ts._get_json(ts.HK_RAW_KLINE)


def test_expected_key_constant_is_the_raw_key():
    assert ts.EXPECTED_KEY == "day"
    assert ts.BASE == "https://ifzq.gtimg.cn", "一律用裸域名（spec §3.4 坑 1）"
    assert json.loads(json.dumps(ts.MIN_ROWS))["day"] == 30
