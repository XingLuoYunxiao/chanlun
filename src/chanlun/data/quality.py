"""复权哨兵校验。

两条哨兵：
1. `check_latest_equals_actual`：同一交易日，前复权收盘价必须等于不复权收盘价
   （前复权锚定当前价，最新一天的复权因子为 1）。
2. `check_cross_source`：baostock 前复权日线 vs 外部源（东财 `fqt=1`，
   不可达时回退腾讯 `qfq`）日线，逐日相对误差。

原则：任何网络失败都转成 `CheckResult(ok=False, detail=...)`，
哨兵不得中断流水线，但失败原因必须留痕。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
from tqdm import tqdm

from ..config import load_config
from . import meta
from .baostock_source import fetch_bars, strip_bs_code, to_bs_code

log = logging.getLogger(__name__)


class ExternalDataError(RuntimeError):
    """外部（非 baostock）行情源不可用：网络、代理或返回空数据。"""

# 相对误差阈值：0.5%
TOLERANCE = 0.005

EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
# 备用源：腾讯前复权日线。实测本地 HTTP 代理会突发失效（curl/requests 同时
# `Empty reply from server`），而腾讯支持直连，故作为跨源校验的回退。
TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_MAX_BARS = 1000
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HTTP_TIMEOUT = 20
HTTP_RETRIES = 3
# 指数退避。外部源有多重回退，单源重试保持轻量即可
HTTP_RETRY_BASE_SLEEP = 1.0
HTTP_RETRY_MAX_SLEEP = 4.0

# 跨源比对窗口：取最近一个月。
# 实测各家「前复权」口径不同（baostock 用除权除息因子，东财/腾讯为另一套因子），
# 价格水平会随距今时间累积漂移：6 只票抽样最坏相对误差约 30 天 0.07%、
# 60 天 0.21%、180 天 0.67%，越久越不可比（2024 年上半年窗口可达 8%）。
# 因此跨源哨兵只做近期窗口，保证 0.5% 阈值下有足够余量。
CROSS_SOURCE_WINDOW_DAYS = 30
# 前复权=不复权只在最新交易日成立，取最近一个多月的窗口即可
LATEST_WINDOW_DAYS = 45

REPORT_NAME = "quality_report.json"


@dataclass
class CheckResult:
    code: str
    kind: str
    ok: bool
    detail: str
    max_rel_err: float = 0.0


# ---------------- 哨兵 1：前复权最新收盘 == 不复权最新收盘 ----------------
def check_latest_equals_actual(code: str, period: str = "day") -> CheckResult:
    kind = "latest_equals_actual"
    end = dt.date.today()
    start = end - dt.timedelta(days=LATEST_WINDOW_DAYS)
    try:
        adj = fetch_bars(code, period, start.isoformat(), end.isoformat(), adjust="2")
        raw = fetch_bars(code, period, start.isoformat(), end.isoformat(), adjust="3")
    except Exception as exc:  # noqa: BLE001 - 哨兵不抛异常
        return CheckResult(code, kind, False,
                           f"baostock 拉取失败: {type(exc).__name__}: {exc}")

    if len(adj) == 0 or len(raw) == 0:
        return CheckResult(code, kind, False, "baostock 前复权或不复权数据为空")

    raw_map = {
        str(ts): float(c)
        for ts, c in zip(raw["ts"], raw["close"])
        if float(c) > 0
    }
    common = [
        (str(ts), float(c))
        for ts, c in zip(adj["ts"], adj["close"])
        if float(c) > 0 and str(ts) in raw_map
    ]
    if not common:
        return CheckResult(code, kind, False, "前复权与不复权无重叠可交易日")

    ts, latest_adj = common[-1]
    latest_raw = raw_map[ts]
    err = abs(latest_adj - latest_raw) / latest_raw
    ok = err <= TOLERANCE
    detail = (
        f"最新共同交易日 {ts}: 前复权={latest_adj:.4f} 不复权={latest_raw:.4f} "
        f"相对误差={err:.4%}（阈值 {TOLERANCE:.1%}）"
    )
    return CheckResult(code, kind, ok, detail, err)


# ---------------- 哨兵 2：baostock 前复权 vs 外部同口径日线（东财/腾讯） ----------------
def check_cross_source(code: str, start: str | dt.date, end: str | dt.date) -> CheckResult:
    kind = "cross_source"
    start_s, end_s = str(start)[:10], str(end)[:10]

    try:
        bs_df = fetch_bars(code, "day", start_s, end_s, adjust="2")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(code, kind, False,
                           f"baostock 前复权拉取失败: {type(exc).__name__}: {exc}")
    if len(bs_df) == 0:
        return CheckResult(code, kind, False, f"baostock 前复权无数据（{start_s}~{end_s}）")

    try:
        source, ext_rows = _cross_source_rows(code, start_s, end_s)
    except Exception as exc:  # noqa: BLE001 - 网络失败降级为 FAIL
        return CheckResult(code, kind, False, f"外部数据源均不可用: {exc}")

    ext_map = {d: c for d, c in ext_rows if c > 0}
    pairs = [
        (str(ts)[:10], float(c))
        for ts, c in zip(bs_df["ts"], bs_df["close"])
        if float(c) > 0 and str(ts)[:10] in ext_map
    ]
    if not pairs:
        return CheckResult(code, kind, False, f"baostock 与{source}无重叠交易日")

    errs = [(abs(b - ext_map[d]) / ext_map[d], d, b) for d, b in pairs]
    max_err, worst_day, worst_bs = max(errs, key=lambda x: x[0])
    ok = max_err < TOLERANCE
    detail = (
        f"{source}前复权对齐 {len(pairs)} 个交易日，最大相对误差 {max_err:.4%}"
        f"（阈值 {TOLERANCE:.1%}）"
    )
    if not ok:
        detail += (
            f"；最大偏差日 {worst_day} baostock={worst_bs:.4f} "
            f"{source}={ext_map[worst_day]:.4f}"
        )
    return CheckResult(code, kind, ok, detail, max_err)


# ---------------- 批量 ----------------
def run_all(
    codes: list[str] | None = None,
    n_sample: int = 20,
    seed: int = 42,
) -> list[CheckResult]:
    """默认从品种表随机抽 `n_sample` 只（seed 固定，可复现），跑两条哨兵并落盘。"""
    cfg = load_config()
    conn = meta.init(cfg.data.meta_db)
    try:
        if codes is None:
            pool = [r["bs_code"] for r in meta.get_universe(conn, include_delisted=False)]
            if not pool:
                from .universe import build_universe

                pool = [s.bs_code for s in build_universe(conn=conn)]
            rng = random.Random(seed)
            sample = rng.sample(pool, min(n_sample, len(pool)))
        else:
            sample = [to_bs_code(c) for c in codes]

        end = dt.date.today()
        start = end - dt.timedelta(days=CROSS_SOURCE_WINDOW_DAYS)

        results: list[CheckResult] = []
        for bs_code in tqdm(sample, desc="quality", unit="只"):
            for result in (
                check_latest_equals_actual(bs_code),
                check_cross_source(bs_code, start.isoformat(), end.isoformat()),
            ):
                results.append(result)
                meta.set_quality(
                    conn,
                    strip_bs_code(result.code),
                    "day",
                    result.kind,
                    result.ok,
                    result.detail,
                )

        _write_report(cfg.data.root / REPORT_NAME, results)
        failed = sum(1 for r in results if not r.ok)
        log.info("质量校验完成：%d 条，失败 %d 条", len(results), failed)
        return results
    finally:
        conn.close()


# ---------------- 外部行情源 ----------------
def _http_get_json(url: str, *, params: dict | None = None,
                   direct: bool = False, what: str = "外网") -> dict:
    """带重试的 JSON GET；`direct=True` 时绕开环境代理（含 socks ALL_PROXY）。"""
    last = "unknown"
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            if direct:
                with requests.Session() as sess:
                    sess.trust_env = False  # 否则 ALL_PROXY=socks5 会盖掉直连
                    resp = sess.get(url, params=params,
                                    headers={"User-Agent": USER_AGENT},
                                    timeout=HTTP_TIMEOUT,
                                    proxies={"http": None, "https": None})
            else:
                resp = requests.get(url, params=params,
                                    headers={"User-Agent": USER_AGENT},
                                    timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            if attempt < HTTP_RETRIES:
                log.warning("%s 第 %d 次失败: %s", what, attempt, last)
                time.sleep(min(HTTP_RETRY_BASE_SLEEP * 2 ** (attempt - 1),
                               HTTP_RETRY_MAX_SLEEP))
    raise ExternalDataError(f"{what} 失败: {last}")


def _reason(exc: Exception) -> str:
    """把异常压成可读中文原因（保留其他异常的类型与消息）。"""
    if isinstance(exc, (requests.Timeout, TimeoutError)):
        return "超时"
    if isinstance(exc, (requests.exceptions.ProxyError,
                        requests.exceptions.ConnectionError,
                        requests.exceptions.SSLError)):
        return "连接失败"
    return f"{type(exc).__name__}: {exc}"


def _eastmoney_secid(code: str) -> str:
    """沪市 `1.`，深市/北交所 `0.`。"""
    bs_code = to_bs_code(code)
    market, _, num = bs_code.partition(".")
    return f"{'1' if market == 'sh' else '0'}.{num}"


def _eastmoney_kline(code: str, start: str, end: str) -> list[tuple[str, float]]:
    """东财前复权日线 `[(date, close)]`；网络失败重试后仍失败则抛异常。"""
    params = {
        "secid": _eastmoney_secid(code),
        "klt": 101,
        "fqt": 1,
        "beg": str(start)[:10].replace("-", ""),
        "end": str(end)[:10].replace("-", ""),
        "lmt": 100000,
        "fields1": "f1,f2",
        "fields2": "f51,f53",
    }
    payload = _http_get_json(EASTMONEY_URL, params=params, what=f"东财 {code}")
    data = (payload or {}).get("data") or {}
    rows: list[tuple[str, float]] = []
    for line in data.get("klines") or []:
        parts = str(line).split(",")
        if len(parts) >= 2 and parts[0]:
            try:
                rows.append((parts[0], float(parts[1])))
            except ValueError:
                continue
    if not rows:
        log.warning("东财返回空数据 code=%s", code)
    return rows


def _tencent_kline(code: str, start: str, end: str, *,
                   direct: bool = False) -> list[tuple[str, float]]:
    """腾讯前复权日线 `[(date, close)]`：`qfqday` 行序为 日期,开,收,高,低,量。"""
    sym = to_bs_code(code).replace(".", "")
    params = {
        "param": f"{sym},day,{str(start)[:10]},{str(end)[:10]},{TENCENT_MAX_BARS},qfq",
    }
    label = "腾讯(直连)" if direct else "腾讯"
    payload = _http_get_json(TENCENT_URL, params=params, direct=direct,
                             what=f"{label} {code}")
    node = ((payload or {}).get("data") or {}).get(sym) or {}
    rows: list[tuple[str, float]] = []
    for item in node.get("qfqday") or node.get("day") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3 or not item[0]:
            continue
        try:
            close = float(item[2])
        except (TypeError, ValueError):
            continue
        if close > 0:
            rows.append((str(item[0])[:10], close))
    return rows


# 回退顺序：东财（需代理）→ 腾讯（代理）→ 腾讯（直连）
_CROSS_SOURCES = (
    ("东财", lambda code, s, e: _eastmoney_kline(code, s, e)),
    ("腾讯", lambda code, s, e: _tencent_kline(code, s, e)),
    ("腾讯(直连)", lambda code, s, e: _tencent_kline(code, s, e, direct=True)),
)


def _cross_source_rows(code: str, start: str, end: str) -> tuple[str, list[tuple[str, float]]]:
    """按回退顺序取外部前复权日线，返回 `(源名, 行)`；全部失败则抛异常。"""
    problems: list[str] = []
    for name, fetch in _CROSS_SOURCES:
        try:
            rows = fetch(code, start, end)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{name}{_reason(exc)}")
            continue
        if rows:
            return name, rows
        problems.append(f"{name}返回空数据")
    raise ExternalDataError("；".join(problems))


def _kind_breakdown(results: list[CheckResult]) -> dict[str, dict[str, int]]:
    """按哨兵种类分组统计。

    `summary` 只给总数时，「抽样 40 条，失败 0 条」这句话分不清
    `cross_source`（要去外部源取数）和 `latest_equals_actual`（本地前复权
    自洽）各查了多少条。外部源整年不可达、跨源校验实际一条没跑成，
    报表依然显示满分通过 —— 数量本身没有说谎，是口径被合并掉了。
    """
    out: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = out.setdefault(r.kind, {"total": 0, "failed": 0})
        bucket["total"] += 1
        if not r.ok:
            bucket["failed"] += 1
    return dict(sorted(out.items()))


def _write_report(path: Path, results: list[CheckResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "tolerance": TOLERANCE,
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r.ok),
            "failed": sum(1 for r in results if not r.ok),
            "kinds": _kind_breakdown(results),
        },
        "results": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("质量报告写入 %s", path)
