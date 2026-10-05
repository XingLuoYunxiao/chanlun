"""腾讯行情源：港股日线（含翻页），**一律不复权**。

**为什么只落不复权** —— 这是实测逼出来的，不是偷懒。

腾讯有三个港股日线控制器，实测（2026-10-05，命令见 spec §3.3 / §5.4）：

===================================  ==========  ==============================
请求                                  返回键       实测结论
===================================  ==========  ==============================
`hkfqkline` + `qfq`                    `qfqday`    **前复权价在 2013-05 之前变成负数**
`hkfqkline` + `hfq`                    `hfqday`    `hfq/raw` **每天都在漂**，不是因子阶梯
`fqkline` + 任意第五个 token            `day`       **不复权**，全历史为正
===================================  ==========  ==============================

三条实测细节：

1. **`qfqday` 不可用。** 腾讯的前复权是**减法**口径（`qfq_t = raw_t - 累计股息`），
   而腾讯 22 年累计股息大于 2004 年的股价，于是价格穿零：
   实测 5499 根里 **2193 根收盘价 <= 0**（2004-06-16 收盘 `-54.956`）。
   `types.normalize` 会把这些行**静默丢掉**，全历史取数只剩 3299 根、
   首根变成 `2013-03-08` 收 `1.59`。落 qfq 等于把 40% 的历史丢掉。

2. **`hfqday` 不能当因子阶梯。** `adjust.py` 的三态模型要求
   `hfq_t = raw_t × k_t / k_0`，也就是 `hfq/raw` **必须是分段常数**。
   实测 5499 根里 `hfq/raw` 有 **5171 个"台阶"**（=几乎每根都在变），
   比值从 `1.000000` 连续漂到 `5.812090`。它既不是 `raw × k`，也不是
   `raw + C`（`hfq-raw` 有 3009 个台阶）。**它根本不能表示成 `k` 阶梯**，
   所以「用 hfq 反推因子表」这条路也被堵死。

3. **`fqkline` 的复权词被完全忽略。** `qfq`/`hfq`/空/`bfq` 四种请求的响应体
   **逐字节相同**（sha256 都是 `aaaa411d6a16bcba`）。它回的就是原始价，
   而且**全历史 5499 根全部为正**。

所以港股的落库口径是**不复权**，没有因子表，页面上只有「不复权」一个口径可选。
**不要**给它加前复权/后复权标签：`adjust.unapply_adjust`/`apply_adjust` 在因子表
为空时**直接返回入参**（`adjust.py:112` / `adjust.py:89`），三个口径会显示同一条
价格序列 —— 那个标签是**假的**。这条约束由 spec §5.4 与 ARCHITECTURE.md D-43 记录。

指数（`hkHSI`/`hkHSTECH`）无除权，不复权就是它的正确口径，与本模块一致。

证据：`docs/superpowers/specs/2026-10-05-hk-us-markets-design.md` §3.2 (a3) / §5.4。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.request

import pandas as pd

from .types import DataSourceError, empty_frame, normalize

log = logging.getLogger(__name__)

#: **一律用裸域名。** `web.ifzq.gtimg.cn` 在 `fqkline`/`hkMinute` 上碰巧能用，
#: 但 `mkline` 那条路径会 301 跳到 `web3.ifzq.gtimg.cn`，而它在本机 NXDOMAIN
#: （spec §3.4 坑 1）。裸域名全路径 200。
BASE = "https://ifzq.gtimg.cn"
HK_RAW_KLINE = BASE + "/appstock/app/fqkline/get"

#: 返回键名必须恰好是它。腾讯的 `fqkline` 忽略复权词，键名恒为 `day`；
#: 一旦它开始回 `qfqday`/`hfqday`，说明数据商改了行为、落库口径会被**静默**替换，
#: 必须报错而不是照存。这是 spec §7.1.1 的第二道护栏。
EXPECTED_KEY = "day"

#: 复权词。`fqkline` 会忽略它，写 `qfq` 只是为了与 A 股请求形状保持一致。
_ADJUST_WORD = "qfq"

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) chanlun/0.5"

#: 每页行数。**不要改大**：实测 `n=1500` 能返回 1500 行，但 `n=2000` 会**静默降级成 640 行**
#: （同一个 640，不是 2000），`n>=3000` 直接 `code=1` 且 `data` 变成一个 JSON `list`
#: （0 行）。`640` 是唯一被完整验证过的深度（spec §3.1 / §6.3）。
PAGE = 640

#: 翻页上限。5499 根历史需要 9 页；留 40 页余量防死循环。
MAX_PAGES = 40

#: 港股日线的最早一根（实测 `2004-06-16`）。翻到这个日期之前就停。
HK_DAY_FLOOR = "2004-06-16"

#: 每个周期至少要有几根，才认为「取到了数据」而不是数据商改坏了行为。
#: 依据：腾讯通用 `fqkline` 对港股**分钟**请求返回 `code=0` 却只有 **1 根** bar
#: （spec §3.2 (a2)）。只看返回码就会把 1 根当「30 分钟数据」写进库，
#: 页面画出 1 根 K 线，用户看到一片空白却没有任何报错。
MIN_ROWS = {"day": 30, "week": 8, "month": 4, "60": 30, "30": 30, "15": 30, "5": 30}

#: 腾讯 K 线数组的字段序。**注意 close 在 high/low 之前** ——
#: 实测行形如 `["2026-09-28","441.400","439.800","447.000","438.600","15335550.000",{…},…]`，
#: 第 2 位是 close、第 3 位才是 high。按 `open,high,low,close` 读会把 high 当 low，
#: 且不会报任何错。
_COLS = ("ts", "open", "close", "high", "low", "volume")

#: 成交额在行里的位置与单位。**只有 `hkfqkline` 的行有这个字段** ——
#: 实测 `fqkline`（本模块用的控制器）的行只有 **7 位**：
#: `["2026-09-28","441.400","439.800","447.000","438.600","15335550.000",{…}]`，
#: 没有第 8 位；`hkfqkline` 的行是 9 位，第 8 位才是成交额（万元）。
#: 所以港股落库的 `amount` 恒为 0 —— 这不是占位符，是数据商确实没给。
#: 全仓库唯一的 `amount` 消费者是 `web/api.py` 的 `_bars_payload`，
#: 而前端根本不渲染它（`grep amount web/static/app.js` 零命中），所以这个 0
#: 不会被用户看到；将来若要显示，只能另找港股成交额源，**不许用 量×均价 编**。
_AMOUNT_IDX = 8
_AMOUNT_SCALE = 10_000.0


def _get_json(url: str, timeout: int = 30) -> dict:
    """取一个 JSON 端点。网络/解析失败一律转成 `DataSourceError`。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定 https 域名
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 - 统一成 DataSourceError 交给 sync 吞
        raise DataSourceError(f"腾讯取数失败 url={url}: {exc}") from exc
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"腾讯返回的不是 JSON url={url}") from exc


def _node(payload: dict, symbol: str) -> dict:
    """从 `{"data": {<symbol>: {...}}}` 里取出这只票的节点。

    **必须判类型**：实测 `n>=3000` 时 `data` 直接是一个 JSON `list`，
    `data[symbol]` 会抛 `TypeError`（spec §3.4 坑 12）。
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DataSourceError(f"腾讯响应 data 不是对象（{type(data).__name__}）symbol={symbol}")
    node = data.get(symbol)
    if not isinstance(node, dict):
        raise DataSourceError(f"腾讯响应缺少 {symbol} 节点（{type(node).__name__}）")
    return node


def _check_key(node: dict, symbol: str) -> list:
    """取出 K 线数组，并断言返回键名就是 `day`。

    `fqkline` 的复权词被忽略、键名恒为 `day`。若哪天它开始回 `qfqday`/`hfqday`，
    说明腾讯改成了「按复权词选口径」，我们落库的就不再是不复权价了。
    这种漂移**不会报错**，只会让库里混进另一种口径的价格，所以在这里拦死。
    """
    keys = [k for k in node if k.endswith("day")]
    if not keys:
        raise DataSourceError(f"腾讯响应里没有日线键 symbol={symbol} keys={sorted(node)[:8]}")
    key = keys[0]
    if key != EXPECTED_KEY:
        raise DataSourceError(
            f"腾讯港股日线返回键名是 {key!r}，期望 {EXPECTED_KEY!r} "
            f"—— 数据商改了口径分派，拒绝落库 symbol={symbol}"
        )
    return node[key]


def plan() -> tuple[str, str, str]:
    """返回 `(端点, 复权词, 期望键名)`，给 `sources.endpoint` 与页面展示用。

    港股只有一个口径（不复权），所以这里没有参数 —— 留着这个函数是为了让
    「端点/口径分派」在代码里仍然只有**一个**出处，别处不许再写一份。
    """
    return HK_RAW_KLINE, _ADJUST_WORD, EXPECTED_KEY


def _page(symbol: str, end: str) -> list:
    """取一页：`param=<sym>,day,<start>,<end>,<n>,qfq`。

    `start` 固定成 `1990-01-01`（腾讯的地板是 `2004-06-16`，比它早即可）：
    实测 `fqkline` 与 `hkfqkline` 一样**只把 `end` 当约束**，
    给它一个宽区间它会回「以 `end` 结尾的最后 `n` 根」，所以翻页只能从 `end` 往回走。
    """
    url = (
        f"{HK_RAW_KLINE}?param={symbol},day,1990-01-01,{end},{PAGE},{_ADJUST_WORD}"
    )
    return _check_key(_node(_get_json(url), symbol), symbol)


def _rows_to_frame(rows: list) -> pd.DataFrame:
    """腾讯的位置数组 → BarFrame。字段序见 `_COLS` 的注释（**close 在 high 之前**）。"""
    if not rows:
        return empty_frame()
    recs = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 6:
            continue
        try:
            rec = {
                "ts": str(r[0]),
                "open": float(r[1]),
                "close": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "volume": float(r[5]),
            }
        except (TypeError, ValueError):
            continue
        # 缺成交额不是错误，成交量仍在（`amount` 在 BarFrame 里允许为 0）。
        rec["amount"] = 0.0
        if len(r) > _AMOUNT_IDX:
            try:
                rec["amount"] = float(r[_AMOUNT_IDX]) * _AMOUNT_SCALE
            except (TypeError, ValueError):
                rec["amount"] = 0.0
        recs.append(rec)
    if not recs:
        return empty_frame()
    df = pd.DataFrame(recs)
    return normalize(df[list(_COLS) + ["amount"]])


def _paged_day(symbol: str, start: str, end: str) -> pd.DataFrame:
    """港股日线翻页：**固定 `n=640`，把 `end` 往前挪到上一页首行的前一天**。

    这是实测唯一正确的翻法（spec §6.3）：9 页拿到 `2004-06-16..2026-10-05`
    共 **5499 根不重复** bar，零重叠、全部为正。两种错法已实测排除：

    - 按「2 年一段」或「5 年一段」切窗口 ⇒ 控制器会越过 `start` 往回多给，
      段与段之间大量重叠；
    - 把 `start` 往前挪着翻 ⇒ 被完全无视（它只约束 `end`），永远只拿到最后 640 根。
    """
    by_ts: dict[str, dict] = {}
    cursor = end
    pages = 0
    for _ in range(MAX_PAGES):
        rows = _page(symbol, cursor)
        pages += 1
        batch = _rows_to_frame(rows)
        if len(batch) == 0:
            break
        fresh = 0
        for rec in batch.to_dict("records"):
            ts = str(rec["ts"])
            if ts not in by_ts:
                by_ts[ts] = rec
                fresh += 1
        first = str(batch["ts"].iloc[0])
        if fresh == 0:
            # 数据商把同一段又给了一遍（或 `end` 无效）—— 再翻也不会前进。
            log.warning("腾讯港股日线翻页停滞 symbol=%s end=%s，已取 %d 根", symbol, cursor, len(by_ts))
            break
        if first <= start:
            break
        nxt = (dt.date.fromisoformat(first[:10]) - dt.timedelta(days=1)).isoformat()
        if nxt < HK_DAY_FLOOR:
            break
        cursor = nxt
    else:
        log.warning("腾讯港股日线翻页达到上限 %d 页 symbol=%s", MAX_PAGES, symbol)

    if not by_ts:
        raise DataSourceError(
            f"{symbol} 的日线一根都没取到（end={end}）—— 数据商可能改了行为，拒绝落库"
        )
    df = pd.DataFrame(list(by_ts.values()))
    df = df[list(_COLS) + ["amount"]]
    df = normalize(df)

    # ★ 行数护栏判在**裁剪之前**。
    #
    # 腾讯只约束 `end`、不约束 `start`，所以一页通常给 640 根，
    # 增量同步只要 23 个交易日就会被裁成 23 根。**23 根是正确答案**，
    # 不是数据商坏了 —— 拿裁剪后的行数去比阈值会把每一次正常的增量同步
    # 都判成失败（实测：`hk.00700` 增量窗口 `2026-09-01..2026-10-05` 得 24 根，
    # 被旧写法误报成「少于 30，拒绝落库」）。
    #
    # 护栏真正要拦的是「返回码正常但只有 1 根」那种静默假成功（spec §3.2 (a2)），
    # 所以它必须量**数据商给了多少**，而不是**我们最后用了多少**。
    if len(df) < MIN_ROWS["day"]:
        raise DataSourceError(
            f"{symbol} 的日线只回 {len(df)} 行（少于 {MIN_ROWS['day']}）"
            f"，数据商可能改了行为 —— 拒绝落库"
        )

    # 区间在本地裁：腾讯只约束 `end`，多给的更早数据要按 `start` 裁掉，
    # 否则 `sync` 的增量起点形同虚设（每次都把全历史重取一遍再 upsert）。
    keep = (df["ts"].astype(str).str[:10] >= str(start)[:10]) & (
        df["ts"].astype(str).str[:10] <= str(end)[:10]
    )
    out = normalize(df[keep])
    log.info("腾讯港股日线 symbol=%s 页数=%d 取回=%d 裁后=%d", symbol, pages, len(df), len(out))
    return out


def fetch_bars(
    code: str,
    symbol: str,
    period: str,
    start: str,
    end: str,
    adjust: str = "3",
) -> pd.DataFrame:
    """港股取数入口。`code` 是落库键（判指数用），`symbol` 是腾讯写法（`hk00700`/`hkHSI`）。"""
    from .adjust import normalize_adjust  # 局部导入：adjust 不依赖本模块，避免环

    if str(period) not in ("day", "d"):
        # 周/月是派生周期（`periods.DERIVED`），由调用方从日线聚合；
        # 分钟线港股**不可得**（spec §3.2：`hkfqkline` 对 m30/m5 回 `bad params`，
        # 通用 `fqkline` 回 `code=0` 但只有 1 根）。
        raise DataSourceError(f"腾讯港股源只提供日线，不提供 {period}（spec §3.2）")

    # 只有不复权可用（模块 docstring 的三条实测）。前/后复权**报错而不是静默给原始价** ——
    # 静默替换口径会让库里混进两套价格，比直接失败危险得多。
    mode = normalize_adjust(adjust)
    if mode != "raw":
        raise DataSourceError(
            f"腾讯港股源只有不复权价（实测前复权在 2013-05 前为负、后复权不是因子阶梯），"
            f"无法提供 {mode} —— 拒绝把原始价标成复权价（spec §5.4 / D-43）"
        )

    # 行数护栏在 `_paged_day` 内部、裁剪之前判（见那里的注释）——
    # 这里不能再判一次裁剪后的行数，否则正常的短增量窗口会被误杀。
    return _paged_day(symbol, str(start)[:10], str(end)[:10])
