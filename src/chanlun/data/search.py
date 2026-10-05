"""按名称找代码 —— 本地品种表 + 数据商联想词。

页面上的「搜索」有两种来源，优先级不同：

1. **本地品种表**（`meta.get_universe`，由通达信整包导入）。A 股的**全量**名单在这里，
   所以 A 股先查本地：名称精确、代码完整、离线可用。数据商的联想词只给十来条，
   拿它当 A 股搜索的主源会让「搜得到」变成一件看数据商心情的事。
2. **数据商联想词**（腾讯 `smartbox`）。港股/美股没有本地名单 —— 香港几千只、
   美股上万只，本系统不落它们的品种表。所以这两个市场**只有**这一条路。

## 为什么必须把「不支持的市场」显式剔掉并回报

`smartbox` 的返回里混着基金（`jj~...`）、港股权证（`QZ`）、以及本期明确不做的
标普 500 / 纳斯达克 100 / 国企指数 / 美股个股。如果只是安静地丢掉它们，用户搜
「标普」得到零条，会以为**搜索坏了**，而真相是**这类标的没被纳入本期范围**
（spec §2.2）。所以 `search()` 除了命中项，还要回报 `skipped` 列表 ——
「被剔掉了什么、为什么」必须能显示出来。
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import markets, sources
from .types import DataSourceError

log = logging.getLogger(__name__)

#: 腾讯联想词接口。`t=all` 一次覆盖 A 股 / 港股 / 美股 / 基金。
SMARTBOX = "https://smartbox.gtimg.cn/s3/"

#: 与 `tencent_source` / `sina_source` 同一套路数：数据商对裸 `python-urllib` 不友好。
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) chanlun/0.5"

#: `smartbox` 用 `~` 分隔字段、`^` 分隔条目；名称是 `\uXXXX` 转义（整段包在双引号里）。
_FIELD = "~"
_ENTRY = "^"

#: 本期**不做**、但数据商确实有的标的（spec §2.2，用户 2026-10-04 划定）。
#: 键是小写落库键。将来要放开只改这张表。
OUT_OF_SCOPE: dict[str, str] = {
    "us.inx": "标普 500 本期不做（spec §2.2）",
    "us.ndx": "纳斯达克 100 本期不做（spec §2.2）",
    "hk.hscei": "国企指数本期不做（spec §2.2）",
}

#: 本期不做的**类别**，按 `(市场, 数据商的类别字段)` 判。类别字段实测取值：
#: `GP` / `GP-A`（股票）、`ZS`（指数）、`QZ`（港股权证）、以及各种 ETF/LOF 后缀。
_OUT_OF_SCOPE_KIND: dict[tuple[str, str], str] = {
    ("us", "GP"): "美股个股本期不做（spec §2.2；数据源实测有全历史日线，是范围决策不是能力限制）",
    ("us", "GP-A"): "美股个股本期不做（spec §2.2；数据源实测有全历史日线，是范围决策不是能力限制）",
}


@dataclass(frozen=True)
class Hit:
    """一条搜索命中。`code` 是**落库键**，页面拿它直接取数。"""

    code: str
    name: str
    market: str
    kind: str
    source: str  # "universe" | "smartbox"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code, "name": self.name, "market": self.market,
            "kind": self.kind, "source": self.source,
        }


@dataclass
class Result:
    """命中 + 被剔掉的东西。`skipped` 是给用户看的一句话，不是日志。"""

    hits: list[Hit] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self, limit: int) -> dict[str, object]:
        return {
            "count": len(self.hits[:limit]),
            "items": [h.as_dict() for h in self.hits[:limit]],
            "skipped": self.skipped,
            "error": self.error,
        }


def _get_text(url: str, timeout: int = 20) -> str:
    """取联想词原文。与两个行情源一样走 `urllib.request`（能穿过本机代理）。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定 https 域名
            return resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 网络异常一律翻成 DataSourceError 给调用方
        raise DataSourceError(f"腾讯联想词取数失败 url={url}: {exc}") from exc


def _decode(body: str, url: str = "") -> str:
    """`v_hint="..."` → 解好转义的字符串。

    整段是 JSON 字符串字面量（`\\uXXXX` 转义），所以直接交给 `json.loads`；
    自己写反转义会漏掉 `\\"`、`\\\\` 这些转义序列。

    **取第一个引号到最后一个引号之间**，而不是 `split("=")[1].strip()`：无结果时
    数据商回的是 `v_hint="N";`，**末尾多一个分号**（实测无结果查询一律如此），
    直接 `json.loads` 会抛 `JSONDecodeError`，于是「搜不到」被报成「接口坏了」。
    """
    text = body.strip()
    first, last = text.find('"'), text.rfind('"')
    if first == -1 or last <= first:
        raise DataSourceError(f"腾讯联想词响应里没有引号包起来的载荷 url={url} head={text[:60]!r}")
    try:
        decoded = json.loads(text[first:last + 1])
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"腾讯联想词响应不是 JSON 字符串 url={url} head={text[:60]!r}") from exc
    if not isinstance(decoded, str):
        # 防御性分支：按上面的切法，切片必然以 `"` 开头、以 `"` 结尾，
        # 所以 `json.loads` 只要成功就必然回 `str`（实测也确认走不到这里）。
        # 留着是为了将来有人改切片方式时，别把 list/dict 当成字段串往下传。
        raise DataSourceError(f"腾讯联想词响应不是字符串（{type(decoded).__name__}）url={url}")
    # `v_hint="N";` 是「无匹配」的**哨兵值**，不是错误。实测：`zzzznope` / `qqqqqq` /
    # `!!!` / 单个全角空格 / `%` / `选择` 一律回这一串。把它当成畸形响应抛出去，
    # 页面就会在每一次「搜不到」时弹一个网络错误 —— 那是谎报。
    if decoded in ("N", "N;"):
        return ""
    # 形状变了要响：合法响应的每条记录都用 `~` 分隔 5 个字段，
    # 非空却一个 `~` 都没有 ⇒ 数据商改了格式，不是「搜不到」。
    if _FIELD not in decoded:
        raise DataSourceError(
            f"腾讯联想词响应里没有 {_FIELD!r} 分隔符（数据商可能改了格式）url={url} head={decoded[:80]!r}")
    return decoded


def _store_key(market: str, symbol: str) -> str:
    """数据商的 `(市场, 符号)` → 本系统的落库键。

    美股个股的符号带交易所后缀（`aapl.oq` / `tcehy.ps`），**必须切掉**：
    `us.aapl.oq` 不是本系统的代码，`sources.vendor_symbol` 会把它原样发给新浪
    而拿到零行。指数（`dji`/`ixic`）本来就没有后缀。
    """
    sym = symbol.strip()
    if market == "us":
        sym = sym.split(".", 1)[0]
    if market in ("sh", "sz", "bj"):
        return markets.store_key(f"{market}.{sym}")
    return f"{market}.{sym.lower()}"


def _classify(market: str, symbol: str, kind: str) -> tuple[str, str]:
    """**返回 `("", 剔掉的原因)` 表示被范围剔掉**；返回 `(code, "")` 表示保留。"""
    if market == "jj":
        return "", "基金代码本期不纳入（本系统不落基金品种表）"
    if market not in markets.MARKETS:
        return "", f"市场 {market!r} 本期不纳入"
    code = _store_key(market, symbol)
    base_kind = kind.split("-")[0]
    reason = _OUT_OF_SCOPE_KIND.get((market, base_kind))
    if reason:
        return "", reason
    if code in OUT_OF_SCOPE:
        return "", OUT_OF_SCOPE[code]
    if market in ("hk", "us"):
        is_index = base_kind == "ZS"
        if is_index and code.split(".", 1)[1] not in markets.INDEX_CODES[market]:
            where = "港股" if market == "hk" else "美股"
            allowed = "、".join(sorted(markets.INDEX_CODES[market]))
            return "", f"{where}其他指数本期不纳入（只做 {allowed}）"
        if not is_index:
            if market == "hk":
                # **港股个股在范围里**（spec §2.1：用户要「看一些具体的股票」），
                # 数据层也实测拉得全历史日线（`hk.00700` 落库 5499 行）。
                # 这里只剔权证 —— 数据商把它们也标成普通标的但类别是 `QZ`。
                #
                # 2026-10-05 修正：早先这一支把**所有**非指数港股都剔掉了，
                # 连「腾讯控股」都报成「港股权证等非个股/指数标的本期不纳入」——
                # 用户按名称搜港股个股会得到零命中，而理由还是假的。
                if base_kind == "GP":
                    return code, ""
                return "", "港股非个股/指数标的本期不纳入（权证、ETF 等）"
            return "", "标普 500 / 纳斯达克 100 以外的美股标的本期不纳入"
    return code, ""


def smartbox(query: str, limit: int = 20) -> Result:
    """数据商联想词 → 本系统代码。**网络异常不抛出**，记在 `Result.error` 里。

    调用方（`/api/search`）必须能只用本地品种表也把页面撑起来：联想词是**增强**，
    不是唯一来源。所以这里把 `DataSourceError` 收进返回值而不是往外丢。
    """
    q = str(query or "").strip()
    if not q:
        return Result()
    url = f"{SMARTBOX}?q={urllib.parse.quote(q)}&t=all"
    try:
        raw = _decode(_get_text(url), url)
    except DataSourceError as exc:
        log.warning("联想词不可用 q=%r: %s", q, exc)
        return Result(error=str(exc))

    out = Result()
    seen: set[str] = set()
    seen_reasons: set[str] = set()
    for entry in raw.split(_ENTRY):
        parts = entry.split(_FIELD)
        if len(parts) < 5:
            continue
        market, symbol, name, _pinyin, kind = (p.strip() for p in parts[:5])
        if not market or not symbol:
            continue
        code, reason = _classify(market, symbol, kind)
        if not code:
            if reason and reason not in seen_reasons:
                seen_reasons.add(reason)
                out.skipped.append(reason)
            continue
        # 落库键必须是本系统认得的写法，否则页面选中后取数会 400。
        try:
            code = sources.normalize_code(code)
        except DataSourceError:
            continue
        if code in seen:
            continue
        seen.add(code)
        # `market` 用**数据商给的市场字段**，不是从落库键切出来的：
        # `sh.513600` 在 `markets.store_key` 里会被缩短成 `513600`（A 股通行的
        # 不带前缀写法），对没有点的键再 `split(".")[0]` 就会把市场报成代码本身。
        out.hits.append(Hit(code=code, name=name, market=market, kind=kind,
                            source="smartbox"))
        if len(out.hits) >= limit:
            break
    return out


def local(rows: list[dict], query: str, limit: int = 20) -> list[Hit]:
    """本地品种表里按代码或名称筛。`rows` 是 `meta.get_universe` 的字典化结果。

    先比代码前缀再比名称包含：搜「600」时用户要的是 `600000`/`600519` 这一串，
    而不是名字里恰好有「600」的票（实测没有，但顺序必须定下来）。
    """
    q = str(query or "").strip().lower()
    if not q:
        return []
    prefix: list[Hit] = []
    contains: list[Hit] = []
    for r in rows:
        code = str(r.get("code", ""))
        name = str(r.get("name", "") or "")
        market = str(r.get("market", "") or "") or code.split(".", 1)[0]
        if code.lower().startswith(q):
            prefix.append(Hit(code=code, name=name, market=market, kind="GP", source="universe"))
        elif q in name.lower():
            contains.append(Hit(code=code, name=name, market=market, kind="GP", source="universe"))
        if len(prefix) + len(contains) >= limit:
            break
    return (prefix + contains)[:limit]


def search(rows: list[dict], query: str, limit: int = 20) -> Result:
    """本地优先、联想词补位；按落库键去重。"""
    q = str(query or "").strip()
    if not q:
        return Result()
    out = Result()
    seen: set[str] = set()
    for hit in local(rows, q, limit):
        if hit.code in seen:
            continue
        seen.add(hit.code)
        out.hits.append(hit)
    remote = smartbox(q, limit)
    out.error = remote.error
    for hit in remote.hits:
        if hit.code in seen:
            continue
        seen.add(hit.code)
        out.hits.append(hit)
    for reason in remote.skipped:
        if reason not in out.skipped:
            out.skipped.append(reason)
    return out
