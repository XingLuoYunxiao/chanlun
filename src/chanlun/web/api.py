"""Web API：行情、缠论结构、扫描与自选池。

设计约定
--------
1. **品种代码在接口边界就归一化并校验**。Store 落盘用的是**裸数字**代码
   （`data/day/sh/600000.parquet`），而 baostock 用 `sh.600000`；两边混用会把
   文件找错地方。更要紧的是交易所前缀：`sh.300059` 在 baostock 那里不报错、
   只返回零行，若放过去，页面会安静地显示「这只票没有结构」。所以这里用
   `to_bs_code()` 校验，前缀不符直接 400。
2. **没有数据就是 404，不返回空数组**。同一类错误（代码写错、周期没同步、
   真的没上市）在页面上必须给出不同的话，否则用户没法自救。
3. **结构对象一律带 `status` 与 `confirmed_at`**。页面要把未确认的笔画虚线、
   回测要按 `confirmed_at` 判可见性，这两个字段不许在序列化时丢掉。
4. `Snapshot` 的字段全是 frozen dataclass / tuple，直接 `to_jsonable()` 即可，
   不需要手写映射——手写映射最容易在加字段时漏掉。
"""

from __future__ import annotations

import datetime as dt
import re
import threading
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

from ..chan.engine import ChanEngine, Snapshot
from ..chan.macd import macd as compute_macd
from ..chan.signal import find_signals
from ..chan.types import Status, to_jsonable
from ..data import adjust as adjust_mod
from ..data import markets, meta, store
from ..data.baostock_source import to_bs_code
from ..data.types import DataSourceError

router = APIRouter()

#: 页面固定免责声明（原文也用于接口返回值，前端与推送共用一句话）。
DISCLAIMER = "仅结构信号提示，不构成投资建议；结构为收盘后确认，非盘中实时。"

PERIODS = ("day", "60", "30", "15", "5")
_CODE_RE = re.compile(r"^(?:(sh|sz|bj)\.?)?(\d{6})$")

#: 默认窗口。窗口不是自相似的：**结构随窗口起点变化**，500 根时实测 4 只样本
#: 一段中枢都凑不出（中枢至少要 3 段确认线段重叠），1200 根才各 2 个。
#: 所有"给结构用"的接口共用这一个值，避免页面（1200）和自选池卡片（曾用 300）
#: 描述两个不同窗口、看起来像自相矛盾。
DEFAULT_LIMIT = 1200

#: 结构快照缓存：同一只票同一段行情只算一次。键为 (code, period, limit, 口径, last_ts, 因子指纹)。
_CACHE: dict[tuple, Snapshot] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 64

#: 默认均线组：MA5/10/20/60/120/250。页面开关直接对应这一组，服务端只算一次。
DEFAULT_MA = (5, 10, 20, 60, 120, 250)
#: 单次请求最多几条均线：画在图上再多也没有信息量，只会让响应变胖。
MAX_MA = 12
#: 均线窗口上限，防 `ma=100000` 这类请求把 pandas 拖住。
MAX_MA_PERIOD = 1000


# ---------------- 工具 ----------------
def normalize_code(raw: str) -> str:
    """把各种写法归一成 **store 的键**；前缀与号段不符时抛 400。

    注意这里**不是**一律去前缀：`sh.000300`（沪深300）与 `sz.000300` 是同号段不同品种，
    去掉前缀会退化成裸码 `000300`，按约定指向深市，于是页面安静地读到别人的文件
    （实测表现为 404 或画出另一只票的曲线）。要不要保留前缀只由
    `markets.store_key` 一处决定。
    """
    text = str(raw or "").strip().lower().replace(" ", "")
    m = _CODE_RE.match(text)
    if not m:
        raise HTTPException(status_code=400, detail=f"无法识别的代码: {raw!r}（应为 6 位数字，可带 sh./sz./bj. 前缀）")
    market, digits = m.group(1), m.group(2)
    try:
        bs = to_bs_code(f"{market}.{digits}" if market else digits)
    except ValueError as exc:  # 号段与前缀矛盾（例如 sh.300059）
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return markets.store_key(bs)


def normalize_adjust_or_400(value: str | None) -> str:
    """复权口径归一；不认识的写法给 400 而不是静默当不复权。

    静默兜底的后果是：用户点了「后复权」，页面画出来的却是不复权，
    而没有任何地方提示口径没生效 —— 复权图与不复权图在前复权下几乎一样，
    只有除权日附近才分叉，很难肉眼发现。
    """
    try:
        return adjust_mod.normalize_adjust(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def parse_ma(raw: str | None) -> tuple[int, ...]:
    """解析 `ma=5,10,20`：`None` = 默认六条，空串/`none`/`off`/`0` = 关掉均线。"""
    if raw is None:
        return DEFAULT_MA
    text = str(raw).strip().lower().replace("，", ",")
    if text in ("", "none", "off", "0"):
        return ()
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            period = int(part)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"均线周期必须是整数: {part!r}") from None
        if not 1 <= period <= MAX_MA_PERIOD:
            raise HTTPException(status_code=400, detail=f"均线周期必须在 1..{MAX_MA_PERIOD} 之间: {period}")
        if period not in out:
            out.append(period)
    if len(out) > MAX_MA:
        raise HTTPException(status_code=400, detail=f"均线最多 {MAX_MA} 条，收到 {len(out)} 条")
    return tuple(out)


def ma_payload(df: pd.DataFrame, periods: tuple[int, ...] = DEFAULT_MA) -> dict[str, list[float | None]]:
    """按**当前口径**的收盘价算均线；不足窗口的前几根给 `None`（不是 0）。

    给 0 会让前端把均线画到坐标原点，图上多出一条垂直假线；`None` 才会断掉。
    """
    close = df["close"].astype("float64")
    out: dict[str, list[float | None]] = {}
    for period in periods:
        series = close.rolling(int(period)).mean()
        out[str(period)] = [None if pd.isna(v) else round(float(v), 4) for v in series]
    return out


def factors_for(meta_db, code: str) -> pd.DataFrame:
    """读除权因子阶梯；没有记录时返回空表（不是 None）。"""
    conn = meta.init(meta_db)
    try:
        return meta.get_adjust_factors(conn, code)
    finally:
        conn.close()


def _stored_adjust(meta_db, code: str, period: str) -> str:
    """这批数据**落库时**的口径：`sync_state.adjust`（`"2"`→前复权、`"3"`→不复权）。

    为什么非要知道这个：库里一期 baostock 落的是前复权、二期通达信整包落的是不复权，
    同一个因子表要用两条相反的公式。查不到记录时按不复权处理 —— 生产数据的每一行都由
    同步流水线写进 `sync_state`，查不到只可能是测试夹具或手工放进库的文件。
    """
    if meta_db is None:
        return "raw"
    conn = meta.init(meta_db)
    try:
        row = meta.get_sync(conn, code, period)
    finally:
        conn.close()
    if row is None or not row["adjust"]:
        return "raw"
    try:
        return adjust_mod.normalize_adjust(row["adjust"])
    except ValueError:
        return "raw"


def _extrapolation_note(df: pd.DataFrame, factors: pd.DataFrame) -> str:
    """窗口起点早于因子表首日时，如实说明前面那段是外推。

    `adjust._k_series` 对首个因子段之前的 K 线沿用 `k[0]`（Task 20 的既定设计：没有观测时
    保持最近一次已知值）。这在窗口落在因子区间内时是对的，但 `day` 一旦切到通达信整包，
    原始价会回溯到 2003 年，而反推出来的因子只覆盖有前复权参照的那几年 —— 前面那段前复权价
    是拿今天的因子外推的，比真值偏高。与其安静地画出来，不如把区间写清楚。
    """
    if len(factors) == 0 or len(df) == 0:
        return ""
    first = str(factors["ts"].min())[:10]
    start = str(df["ts"].iloc[0])[:10]
    if start >= first:
        return ""
    return f"（因子表自 {first} 起，{start} ~ {first} 为外推值）"


def _apply_factors(df: pd.DataFrame, factors: pd.DataFrame, mode: str,
                   stored: str = "raw") -> tuple[pd.DataFrame, str, str]:
    """按因子表把行情切成三态之一，并如实报告**实际生效**的口径。

    `stored` 是落库口径（见 `_stored_adjust`）。它决定用哪条公式：落库是不复权
    → `qfq = raw × k_t`；落库是前复权 → `raw = q_t / k_t`。两条公式用反了不会报错，
    只会把价格二次复权，除权日反而长出一根假缺口。
    """
    stored = adjust_mod.normalize_adjust(stored)
    segments = len(factors)
    extrapolated = _extrapolation_note(df, factors)
    if segments == 0:
        # 没有因子表时能给的只有落库口径本身，别的口径要靠因子反算，不能假装切得动
        if stored == "qfq":
            if mode == "qfq":
                return df, "qfq", "本票为前复权落库（baostock），无独立除权因子：前复权即当前价格"
            name = {"raw": "不复权", "hfq": "后复权"}[mode]
            return df, "qfq", f"本票为前复权落库（baostock），且无除权因子，无法还原{name}：当前显示前复权"
        return df, "raw", "无除权记录（三态相同，价格即不复权原始价）"
    if stored == "qfq":
        name = {"raw": "不复权", "qfq": "前复权", "hfq": "后复权"}[mode]
        adjusted = adjust_mod.unapply_adjust(df, factors, mode)
        return adjusted, mode, (
            f"{name}：由库内前复权价按 {segments} 段除权因子反算（成交量/成交额不复权）{extrapolated}"
        )
    if mode == "raw":
        return df, "raw", f"不复权：原始价（本票有 {segments} 段除权因子，可切前/后复权）"
    name = {"qfq": "前复权", "hfq": "后复权"}[mode]
    adjusted = adjust_mod.apply_adjust(df, factors, mode)
    return adjusted, mode, (
        f"{name}：按 {segments} 段除权因子缩放开高低收（成交量/成交额不复权）{extrapolated}"
    )


def _factor_fingerprint(factors: pd.DataFrame) -> tuple:
    """因子指纹：段数 + 末段（起始日, 系数）。

    只按 `last_ts` 做缓存键会漏掉「行情没动、因子表被修正」这一种更新
    （除权数据后补、系数纠正都属此类），于是页面继续拿旧复权价算结构。
    """
    if len(factors) == 0:
        return ()
    last = factors.iloc[-1]
    return (len(factors), str(last["ts"]), float(last["k"]))


def _cfg(request: Request):
    return request.app.state.cfg


def _read_bars(code: str, period: str, limit: int) -> pd.DataFrame:
    if period not in PERIODS:
        raise HTTPException(status_code=400, detail=f"不支持的周期: {period!r}（可选 {'/'.join(PERIODS)}）")
    if not store.exists(code, period):
        raise HTTPException(
            status_code=404,
            detail=(
                f"{code} 的 {period} 周期没有本地数据。"
                f"请先同步：python -m chanlun sync --period {period} --codes {code}"
            ),
        )
    df = store.read(code, period, limit=limit)
    if len(df) == 0:
        raise HTTPException(status_code=404, detail=f"{code} 的 {period} 周期数据为空文件")
    return df


def _bars_payload(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "ts": str(r.ts),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume),
            "amount": float(r.amount),
        }
        for r in df.itertuples()
    ]


def _macd_payload(bars: pd.DataFrame) -> dict[str, list[float]]:
    """MACD 由**服务端**算：页面必须和引擎看到同一条 DIF/DEA/柱子。

    前端自己再算一遍 EMA 看着更省事，但两边一旦口径分叉（种子、hist 是否乘 2），
    页面上就会拿一条和买卖点无关的 MACD 去解释背驰 —— 那是自欺。
    """
    df = compute_macd(bars["close"].to_numpy(dtype=float))
    return {c: [round(float(v), 4) for v in df[c]] for c in ("dif", "dea", "hist")}


def snapshot_of(code: str, period: str, limit: int, *, adjust: str = "qfq",
                meta_db=None) -> tuple[Snapshot, pd.DataFrame, str, str]:
    """算（或取缓存）某只票的结构快照，返回 `(快照, 复权后行情, 生效口径, 口径说明)`。

    **结构必须与画出来的 K 线同一个口径**：请求 `hfq` 却拿 `raw` 的笔/段/中枢，
    中枢的 ZG/ZD 会与 K 线对不上。所以复权在这里做，缓存键也带口径与因子指纹。

    取数一律走 `_read_bars`：缺数据的 404 与周期校验必须只有一处实现，
    否则某条路径会绕过检查、拿着空 DataFrame 往下跑到 500。
    """
    df = _read_bars(code, period, limit)
    factors = pd.DataFrame() if meta_db is None else factors_for(meta_db, code)
    df, effective, note = _apply_factors(df, factors, adjust, _stored_adjust(meta_db, code, period))
    last_ts = str(df["ts"].iloc[-1])
    key = (code, period, limit, effective, last_ts, _factor_fingerprint(factors))
    with _CACHE_LOCK:
        snap = _CACHE.get(key)
    if snap is None:
        snap = ChanEngine(code, period, signal_fn=find_signals, level=period).full(df)
        with _CACHE_LOCK:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.clear()
            _CACHE[key] = snap
    return snap, df, effective, note


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _rail_payload(code: str, period: str, limit: int, *, meta_db=None) -> dict[str, Any]:
    """自选栏那一格：**不复权真实成交价** + **交易所口径涨跌幅**。

    这一格回答的是「我的票现在多少钱」，所以价格必须是不复权成交价 —— 后复权价是拿因子
    重算出来的分析序列（中信证券 25.86 会显示成 31.82），券商与同花顺在这一栏给的都不是它。
    涨跌幅则要跟着交易所走：交易所用的是**除权参考价**做分母，等价于前复权帧的相邻两根；
    拿不复权前收盘去除，每只票除权日都会凭空多出一根跌停。

    代价是每行多读一次 parquet（不算缠论、不进 `_CACHE`），换来自选栏与券商对得上账。
    """
    stored_df = _read_bars(code, period, limit)
    factors = pd.DataFrame() if meta_db is None else factors_for(meta_db, code)
    stored = _stored_adjust(meta_db, code, period)
    # 不复权落库时库里就是原始价，前复权落库时要除回去 —— 拿反了会二次复权。
    # 注意反算用的是**该根 K 线当时**的因子：库里断更、因子表却已经走到下一段时，
    # 库内价仍是旧的复权价，不能直接当成交价用。
    if stored == "qfq":
        raw = adjust_mod.unapply_adjust(stored_df, factors, "raw")
    else:
        raw = adjust_mod.apply_adjust(stored_df, factors, "raw")
    qfq, _, _ = _apply_factors(stored_df, factors, "qfq", stored)
    payload = _change_payload(qfq)
    payload["close"] = round(float(raw["close"].iloc[-1]), 4)
    return payload


def _change_payload(df: pd.DataFrame) -> dict[str, Any]:
    """最新价与涨跌幅：**必须用同一个复权帧的相邻两根**算。

    自选栏是「扫一眼」的地方，最容易出的错就是口径混用：不复权帧在除权日有一根
    几十个点的缺口，直接拿来算涨跌幅就会报出一根不存在的跌停。
    """
    close = df["close"].astype("float64")
    if len(close) < 2:
        return {"prev_close": None, "change": None, "change_pct": None}
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    if prev == 0:
        return {"prev_close": None, "change": None, "change_pct": None}
    return {
        "prev_close": round(prev, 4),
        "change": round(last - prev, 4),
        "change_pct": round((last - prev) / prev * 100, 2),
    }


def _counts(snap: Snapshot) -> dict[str, int]:
    return {
        "strokes": len(snap.strokes),
        "segments": len(snap.segments),
        "pivots": len(snap.pivots),
        "signals": len(snap.signals),
        "confirmed_segments": sum(1 for s in snap.segments if s.status is Status.CONFIRMED),
        "tentative_segments": sum(1 for s in snap.segments if s.status is Status.TENTATIVE),
    }


def structure_payload(snap: Snapshot, bars: pd.DataFrame, *,
                      include_merged: bool = False) -> dict[str, Any]:
    """结构 + 行情一起返回：一次请求就能画图，且两边必然对齐。

    `merged`（包含处理后的 K 线）默认不传 —— 它是内部中间量，条数与 bars 同量级，
    传了只会让页面变慢；需要核对包含处理时用 `?merged=true` 单独取。
    """
    body = to_jsonable(snap)
    if not include_merged:
        body.pop("merged", None)
    body["bars"] = _bars_payload(bars)
    body["macd"] = _macd_payload(bars)
    body["counts"] = _counts(snap)
    body["disclaimer"] = DISCLAIMER
    return body


# ---------------- 接口 ----------------
@router.get("/api/health")
def health(request: Request) -> dict[str, Any]:
    cfg = _cfg(request)
    return {
        "ok": True,
        "port": getattr(request.app.state, "port", cfg.web.port),
        "data_root": str(cfg.data.root),
        "periods": list(cfg.periods),
        "disclaimer": DISCLAIMER,
    }


@router.get("/api/universe")
def universe(request: Request, limit: int = Query(200, ge=1, le=10000), q: str = "") -> dict[str, Any]:
    cfg = _cfg(request)
    conn = meta.init(cfg.data.meta_db)
    try:
        rows = meta.get_universe(conn)
    finally:
        conn.close()
    items = [dict(r) for r in rows]
    if q:
        needle = q.strip().lower()
        items = [i for i in items if needle in str(i["code"]) or needle in str(i["name"]).lower()]
    return {"count": len(items), "items": items[:limit]}


@router.get("/api/bars")
def bars(request: Request, code: str, period: str = "day",
         limit: int = Query(DEFAULT_LIMIT, ge=10, le=20000),
         adjust: str = "qfq", ma: str | None = None) -> dict[str, Any]:
    cfg = _cfg(request)
    key = normalize_code(code)
    mode = normalize_adjust_or_400(adjust)
    periods = parse_ma(ma)
    df = _read_bars(key, period, limit)
    df, effective, note = _apply_factors(df, factors_for(cfg.data.meta_db, key), mode,
                                       _stored_adjust(cfg.data.meta_db, key, period))
    return {
        "code": key, "period": period, "count": len(df), "bars": _bars_payload(df),
        "adjust": mode, "adjust_effective": effective, "adjust_note": note,
        "ma": ma_payload(df, periods), "ma_periods": list(periods),
    }


@router.get("/api/structure")
def structure(request: Request, code: str, period: str = "day",
              limit: int = Query(DEFAULT_LIMIT, ge=10, le=20000), merged: bool = False,
              adjust: str = "qfq", ma: str | None = None) -> dict[str, Any]:
    cfg = _cfg(request)
    key = normalize_code(code)
    mode = normalize_adjust_or_400(adjust)
    periods = parse_ma(ma)
    snap, df, effective, note = snapshot_of(key, period, limit, adjust=mode,
                                            meta_db=cfg.data.meta_db)
    body = structure_payload(snap, df, include_merged=merged)
    body.update({
        "code": key, "period": period,
        "adjust": mode, "adjust_effective": effective, "adjust_note": note,
        "ma": ma_payload(df, periods), "ma_periods": list(periods),
    })
    return body


@router.get("/api/scan")
def scan(request: Request, period: str = "day", date: str | None = None) -> dict[str, Any]:
    cfg = _cfg(request)
    run_date = date or dt.date.today().isoformat()
    conn = meta.init(cfg.data.meta_db)
    try:
        hits = meta.get_scan_results(conn, run_date)
    finally:
        conn.close()
    return {"run_date": run_date, "period": period, "count": len(hits), "hits": [dict(h) for h in hits]}


# ---------------- 自选池 ----------------
@router.get("/api/watchlist")
def watchlist(request: Request) -> dict[str, Any]:
    cfg = _cfg(request)
    conn = meta.init(cfg.data.meta_db)
    try:
        rows = meta.get_watchlist(conn)
    finally:
        conn.close()
    return {"count": len(rows), "items": [dict(r) for r in rows]}


@router.post("/api/watchlist")
async def watchlist_add(request: Request) -> dict[str, Any]:
    cfg = _cfg(request)
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001 - 空 body / 坏 JSON 都按参数错误处理
        raise HTTPException(status_code=400, detail=f"请求体不是合法 JSON: {exc}") from exc
    code = normalize_code(str(body.get("code", "")))  # 自选池可先记代码后同步，但格式必须合法
    conn = meta.init(cfg.data.meta_db)
    try:
        meta.add_watch(conn, code, str(body.get("name", "")), str(body.get("note", "")))
    finally:
        conn.close()
    return {"ok": True, "code": code}


@router.delete("/api/watchlist")
def watchlist_remove(request: Request, code: str) -> dict[str, Any]:
    cfg = _cfg(request)
    bare = normalize_code(code)
    conn = meta.init(cfg.data.meta_db)
    try:
        meta.remove_watch(conn, bare)
    finally:
        conn.close()
    return {"ok": True, "code": bare}


@router.get("/api/watchlist/structure")
def watchlist_structure(request: Request, period: str = "day", limit: int = DEFAULT_LIMIT,
                        adjust: str = "qfq") -> dict[str, Any]:
    """自选池结构摘要：一次请求给全，字段足以做「结构跟踪表」。

    这里也接受 `adjust`：摘要里的中枢 ZG/ZD 是**价位**，口径与看盘页不一致的话，
    同一只票在自选栏卡片和图表上会显示两个不同的中枢区间。
    """
    cfg = _cfg(request)
    mode = normalize_adjust_or_400(adjust)
    conn = meta.init(cfg.data.meta_db)
    try:
        rows = [dict(r) for r in meta.get_watchlist(conn)]
        # 自选池只记代码（加自选时懒得输名字），名称从 universe 表兜底：
        # 左侧栏里一排光秃秃的数字，看盘时根本认不出是哪只票。
        names = {str(r["code"]): str(r["name"] or "") for r in meta.get_universe(conn)}
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    for row in rows:
        code = str(row["code"])
        base = {"code": code, "name": row.get("name") or names.get(code, ""), "period": period,
                "note": row.get("note") or "", "adjust": mode}
        if not store.exists(code, period):
            items.append({**base, "missing": True, "error": f"{period} 周期未同步"})
            continue
        try:
            snap, df, effective, note = snapshot_of(code, period, limit, adjust=mode,
                                                    meta_db=cfg.data.meta_db)
        except (DataSourceError, HTTPException, ValueError) as exc:
            items.append({**base, "missing": True, "error": str(exc)})
            continue
        last = snap.segments[-1] if snap.segments else None
        try:
            rail = _rail_payload(code, period, limit, meta_db=cfg.data.meta_db)
        except (DataSourceError, HTTPException, ValueError) as exc:
            items.append({**base, "missing": True, "error": str(exc)})
            continue
        items.append({
            **base,
            "missing": False,
            "adjust_effective": effective,
            "adjust_note": note,
            "as_of": snap.as_of,
            "ts": str(df["ts"].iloc[-1]),
            # `close`/`change_pct` 是**图表口径**的价（和图上最后一根 K 线对得上）；
            # `rail_*` 是自选栏那一格要显示的真实成交价与交易所口径涨跌幅。
            "close": round(float(df["close"].iloc[-1]), 4),
            **_change_payload(df),
            "rail_close": rail["close"],
            "rail_change": rail["change"],
            "rail_change_pct": rail["change_pct"],
            "counts": _counts(snap),
            "pivots": [
                {"zg": p.zg, "zd": p.zd, "level": p.level, "status": p.status.value,
                 "start_ts": p.start_ts, "end_ts": p.end_ts, "confirmed_at": p.confirmed_at}
                for p in snap.pivots
            ],
            "last_segment": None if last is None else {
                "direction": last.direction, "status": last.status.value,
                "low": last.low, "high": last.high, "confirmed_at": last.confirmed_at,
                "stroke_count": last.stroke_count,
            },
            "signals": [
                {"kind": s.kind.value, "kind_cn": s.kind.name_cn, "ts": s.ts, "price": s.price,
                 "status": s.status.value, "confirmed_at": s.confirmed_at}
                for s in snap.signals
            ],
        })
    return {"period": period, "count": len(items), "items": items}
