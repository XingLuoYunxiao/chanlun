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
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

from ..chan.engine import ChanEngine, Snapshot
from ..chan.macd import macd as compute_macd
from ..chan.signal import find_signals
from ..chan.types import Status, to_jsonable
from ..data import adjust as adjust_mod
from ..data import markets, meta, store
from ..data import sync as sync_mod
from ..data import periods as periods_mod
from ..data.baostock_source import to_bs_code
from ..data.types import DataSourceError

log = logging.getLogger("chanlun.web")

router = APIRouter()

#: 页面固定免责声明（原文也用于接口返回值，前端与推送共用一句话）。
DISCLAIMER = "仅结构信号提示，不构成投资建议；结构为收盘后确认，非盘中实时。"

#: 接口认得的周期。`week`/`month` **不落库**：本地日线聚合出来（见 `periods_mod`），
#: 所以它们没有自己的 parquet，也不需要 `sync --period week`。
PERIODS = ("day", "week", "month", "60", "30", "15", "5")
_CODE_RE = re.compile(r"^(?:(sh|sz|bj)\.?)?(\d{6})$")

#: 默认显示窗口（根数）。**窗口只决定看得见多少，不决定怎么划分**：
#: 结构一律在完整历史上算完再裁到窗口（见 `snapshot_of`），所以同一只票
#: 换个根数不会换一套笔/段/中枢 —— 价格早就定了这条原则（spec §3.3），结构同理。
#: 所有"给结构用"的接口共用这一个值，避免页面和自选池卡片描述两个不同窗口。
DEFAULT_LIMIT = 1200

#: 结构快照缓存：同一只票同一段行情只算一次。键为 (code, period, 口径, last_ts, 因子指纹)
#: —— **不含 limit**：快照是全量的，窗口只在出口处裁，所以多切几次根数不必重算。
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
        if mode == "qfq":
            # 前复权是**原样返回**（`unapply_adjust` 对 qfq 直接 return）：这条路上
            # 一次换算都没发生。写成"按因子反算"会让人以为图上的价是算出来的，
            # 而它其实就是落库价 —— 说明本身不能先误导一次。
            return df, "qfq", (
                f"前复权：库内即前复权（{segments} 段除权因子可切不复权/后复权，"
                f"成交量/成交额不复权）{extrapolated}"
            )
        name = {"raw": "不复权", "hfq": "后复权"}[mode]
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


def _read_bars(code: str, period: str, limit: int | None = None, *,
               requested: str | None = None) -> pd.DataFrame:
    """读某只票的行情（**落库口径，未复权**）。`limit=None` = 全量。

    结构必须在完整历史上算完再裁窗口，所以 `snapshot_of` 走 `limit=None`；
    只有显示用的那一段才按根数截断。

    派生周期（周/月）在这里**先聚合再截断**：反过来先截日线再聚合，第一根周K
    就只剩窗口内的那几天，开盘价会变成一个不存在的价格。聚合永远基于全量日线。

    `requested` 只影响**报错时说的是哪个周期**：用户请求的是周线，报错却说
    「day 周期没有数据」，他会去 sync 日线；说清「周线由日线聚合」才能自救。
    """
    if period not in PERIODS:
        raise HTTPException(status_code=400, detail=f"不支持的周期: {period!r}（可选 {'/'.join(PERIODS)}）")
    shown = requested or period
    base = periods_mod.base_period(period)
    if not store.exists(code, base):
        if base != shown:
            detail = (
                f"{code} 的 {periods_mod.label(shown)}由{periods_mod.label(base)}聚合，"
                f"但本地没有{periods_mod.label(base)}数据。"
                f"请先同步：python -m chanlun sync --period {base} --codes {code}"
            )
        else:
            detail = (
                f"{code} 的 {shown} 周期没有本地数据。"
                f"请先同步：python -m chanlun sync --period {shown} --codes {code}"
            )
        raise HTTPException(status_code=404, detail=detail)
    df = store.read(code, base, limit=None if periods_mod.is_derived(period) else limit)
    if len(df) == 0:
        raise HTTPException(status_code=404, detail=f"{code} 的 {shown} 周期数据为空文件")
    if periods_mod.is_derived(period):
        df = periods_mod.aggregate(df, period)
        if limit is not None and len(df) > limit:
            df = df.tail(limit).reset_index(drop=True)
    return df


def _period_frame(code: str, period: str, *, adjust: str, meta_db) -> tuple[pd.DataFrame, str, str, tuple]:
    """读 + 复权 + （派生周期）聚合，返回 `(全量帧, 生效口径, 口径说明, 因子指纹)`。

    **复权必须排在聚合前面**：后复权是把每一天的价格各乘一个因子，周K的开盘价取的是
    那一周**第一天**的价格 —— 先把日线聚合成周线、再整根乘最后一个交易日的因子，
    除权发生在周中间时开盘价就错了（最高/最低同理，它们可能来自不同的日子）。
    所以顺序固定为：读日线 → 按日复权 → 再按周/月聚合。
    """
    base = periods_mod.base_period(period)
    full = _read_bars(code, base, None, requested=period)
    factors = pd.DataFrame() if meta_db is None else factors_for(meta_db, code)
    full, effective, note = _apply_factors(full, factors, adjust, _stored_adjust(meta_db, code, base))
    if periods_mod.is_derived(period):
        full = periods_mod.aggregate(full, period)
    return full, effective, note, _factor_fingerprint(factors)


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


def _macd_payload(macd_df: pd.DataFrame, n: int) -> dict[str, list[float]]:
    """MACD 由**服务端**算，且由**全量**收盘序列算完再裁尾巴。

    前端自己再算一遍 EMA 看着更省事，但两边一旦口径分叉（种子、hist 是否乘 2），
    页面上就会拿一条和买卖点无关的 MACD 去解释背驰 —— 那是自欺。在窗口里重算
    同样不行：EMA 是递推量，窗口一变种子就变，实测同一只票 `limit=500` 的柱子
    与全量尾部最大差 0.0958 —— 那是两条不同的 MACD。
    """
    return {c: [round(float(v), 4) for v in macd_df[c].to_numpy()[-n:]]
            for c in ("dif", "dea", "hist")}


@dataclass(frozen=True)
class StructureView:
    """一次结构请求的全部结果：**全量算出来的结构** + 按窗口裁出来的显示数据。

    两者分开存，是为了让「怎么划分」与「看得见多少」互不污染：`snap` 与全量快照
    是**同一套划分**，只是窗口外的对象没送出；`totals`/`bars_total` 是全史规模，
    页面据此说明「还有多少没画」——不报，用户会以为全史就这么点结构。
    """

    snap: Snapshot
    bars: pd.DataFrame
    macd: pd.DataFrame
    totals: dict[str, int]
    bars_total: int
    effective: str
    note: str
    derived: bool = False
    base_period: str = "day"
    partial: bool | None = None


def snapshot_of(code: str, period: str, limit: int, *, adjust: str = "qfq",
                meta_db=None) -> StructureView:
    """全量算结构、按 `limit` 裁显示，返回 `StructureView`。

    **结构必须与画出来的 K 线同一个口径**：请求 `hfq` 却拿 `raw` 的笔/段/中枢，
    中枢的 ZG/ZD 会与 K 线对不上。所以复权在这里做，缓存键也带口径与因子指纹。

    **结构也必须与可见窗口无关**：先在**全量**历史上算完划分，再裁出要画的对象
    （`Snapshot.clipped_to`）。反过来「先按 limit 截断再算」等于让窗口参与划分 ——
    实测同一只票 `limit=300` 的首段在 1212 根全量里根本不存在，默认 1200 根只
    显示全史买卖点的三分之一。价格早就定过同一条原则（spec §3.3：某一天的后复权价
    是该日期的属性，与可见窗口无关），结构同理。

    取数一律走 `_read_bars`：缺数据的 404 与周期校验必须只有一处实现，
    否则某条路径会绕过检查、拿着空 DataFrame 往下跑到 500。
    """
    full, effective, note, fingerprint = _period_frame(code, period, adjust=adjust, meta_db=meta_db)
    last_ts = str(full["ts"].iloc[-1])
    key = (code, period, effective, last_ts, fingerprint)
    with _CACHE_LOCK:
        snap = _CACHE.get(key)
    if snap is None:
        snap = ChanEngine(code, period, signal_fn=find_signals, level=period).full(full)
        with _CACHE_LOCK:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.clear()
            _CACHE[key] = snap
    if limit is None or len(full) <= limit:
        bars = full
    else:
        bars = full.tail(limit).reset_index(drop=True)
    return StructureView(
        snap=snap.clipped_to(str(bars["ts"].iloc[0]), last_ts),
        bars=bars,
        macd=compute_macd(full["close"].to_numpy(dtype=float)),
        totals=_counts(snap),
        bars_total=len(full),
        effective=effective,
        note=note,
        derived=periods_mod.is_derived(period),
        base_period=periods_mod.base_period(period),
        partial=_partial_last_bar(last_ts, period) if periods_mod.is_derived(period) else None,
    )


def _partial_last_bar(last_ts: str, period: str) -> bool | None:
    """派生的最后一根 K 线走完了没有。**不知道就返回 None**（不假装走完了）。

    周末/月末的最后一根要等本周/本月最后一个交易日收盘才算定下来。页面据此标
    「未走完」—— 不标的话，读图的人会把一根还在变的周K 当成定论去数中枢。
    """
    try:
        from ..calendar import get_calendar

        return not periods_mod.is_complete(last_ts, period, get_calendar())
    except Exception:  # noqa: BLE001 - 日历取不到时宁可不说，也不谎报
        return None


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

    取数一律走**落库周期**（`base_period`）：这一格问的是「现在多少钱」，
    与图表选的是日线还是周线无关；拿一根还在变的周K 的收盘价当"现价"是另一回事。
    """
    base = periods_mod.base_period(period)
    stored_df = _read_bars(code, base, limit)
    factors = pd.DataFrame() if meta_db is None else factors_for(meta_db, code)
    stored = _stored_adjust(meta_db, code, base)
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


def structure_payload(view: StructureView, *, include_merged: bool = False) -> dict[str, Any]:
    """结构 + 行情一起返回：一次请求就能画图，且两边必然对齐。

    `counts` 数的是**本窗口看得见**的对象（要和图上的条数对得上），
    `counts_total`/`bars_total` 数的是**全史**（要说清还有多少没画）。

    `merged`（包含处理后的 K 线）默认不传 —— 它是内部中间量，条数与 bars 同量级，
    传了只会让页面变慢；需要核对包含处理时用 `?merged=true` 单独取。
    """
    body = to_jsonable(view.snap)
    if not include_merged:
        body.pop("merged", None)
    body["bars"] = _bars_payload(view.bars)
    body["macd"] = _macd_payload(view.macd, len(view.bars))
    body["counts"] = _counts(view.snap)
    body["counts_total"] = view.totals
    body["bars_total"] = view.bars_total
    body["derived"] = view.derived
    body["base_period"] = view.base_period
    body["partial"] = view.partial
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
    df, effective, note, _ = _period_frame(key, period, adjust=mode, meta_db=cfg.data.meta_db)
    if len(df) > limit:
        df = df.tail(limit).reset_index(drop=True)
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
    view = snapshot_of(key, period, limit, adjust=mode, meta_db=cfg.data.meta_db)
    body = structure_payload(view, include_merged=merged)
    conn = meta.init(cfg.data.meta_db)
    try:
        name = meta.name_of(conn, key)
    finally:
        conn.close()
    body.update({
        "code": key, "name": name, "period": period,
        "adjust": mode, "adjust_effective": view.effective, "adjust_note": view.note,
        "ma": ma_payload(view.bars, periods), "ma_periods": list(periods),
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


@router.patch("/api/watchlist")
async def watchlist_move(request: Request) -> dict[str, Any]:
    """自选池的「改」= **上下移动**（用户 2026-10-01 选定）。

    不做改名/改代码：代码是这只票的身份，改名只是显示；两者都会让「自选池里的顺序」
    这件事被搅进一堆无关的写操作里。顺序是用户唯一的排序诉求，就只做顺序。
    """
    cfg = _cfg(request)
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"请求体不是合法 JSON: {exc}") from exc
    code = normalize_code(str(body.get("code", "")))
    try:
        delta = int(body.get("delta"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="delta 只能是 -1（上移）或 +1（下移）") from exc
    if delta not in (-1, 1):
        raise HTTPException(status_code=400, detail=f"delta 只能是 -1（上移）或 +1（下移），收到 {delta}")
    conn = meta.init(cfg.data.meta_db)
    try:
        known = {str(r["code"]) for r in meta.get_watchlist(conn)}
        if code not in known:
            raise HTTPException(status_code=404, detail=f"{code} 不在自选池里，无法移动")
        order = meta.move_watch(conn, code, delta)
    finally:
        conn.close()
    return {"ok": True, "code": code, "delta": delta, "order": order}


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
        # 指数**不在品种表里**（那张表只有股票），名称来自默认池或加自选时存的那一份。
        names = {str(r["code"]): str(r["name"] or "") for r in meta.get_universe(conn)}
        names.update({str(c): str(n) for c, n in meta.DEFAULT_WATCHLIST})
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    for row in rows:
        code = str(row["code"])
        base = {"code": code, "name": row.get("name") or names.get(code, ""), "period": period,
                "note": row.get("note") or "", "adjust": mode}
        # 存在性判据用**落库周期**：周/月没有自己的 parquet，`store.exists(code, "week")`
        # 永远为假，整栏会被标成「未同步」—— 明明日线就在本地。
        if not store.exists(code, periods_mod.base_period(period)):
            items.append({**base, "missing": True, "error": f"{period} 周期未同步"})
            continue
        try:
            view = snapshot_of(code, period, limit, adjust=mode, meta_db=cfg.data.meta_db)
        except (DataSourceError, HTTPException, ValueError) as exc:
            items.append({**base, "missing": True, "error": str(exc)})
            continue
        snap, df = view.snap, view.bars
        last = snap.segments[-1] if snap.segments else None
        try:
            rail = _rail_payload(code, period, limit, meta_db=cfg.data.meta_db)
        except (DataSourceError, HTTPException, ValueError) as exc:
            items.append({**base, "missing": True, "error": str(exc)})
            continue
        items.append({
            **base,
            "missing": False,
            "adjust_effective": view.effective,
            "adjust_note": view.note,
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


# ---------------- 「同步这个周期」（Task 31） ----------------
#: baostock 是单会话 socket 协议：两个线程同时拉会互相踩，所以**全局串行**。
#: 首次拉一只票的 5 分钟线实测要 149 秒（78432 根），串行是唯一不把自己搞崩的做法。
_SYNC_RUN_LOCK = threading.Lock()
#: 任务登记表：`(code, period) -> 状态`。**跑完不删**：按钮要靠它显示结果与失败原因，
#: 也要靠「跑完的任务不算在跑」来判断重试。
_SYNC_TASKS: dict[tuple[str, str], dict[str, Any]] = {}
_SYNC_TASKS_LOCK = threading.Lock()

#: `sync_one` 的三态 → 页面四态。`skipped` 单列：源返回零行**不是**失败，
#: 页面不能把它画成红字（真空区间是合法的）。
_SYNC_STATE = {"ok": "done", "skipped": "skipped", "failed": "error"}


def clear_sync_tasks() -> None:
    """清空任务登记表（测试用；服务运行期没有清理的理由——条目很小且要留着看结果）。"""
    with _SYNC_TASKS_LOCK:
        _SYNC_TASKS.clear()


def _sync_target_period(raw: Any) -> str:
    """校验周期并映射到**落库周期**：周线/月线是本地聚合的，同步 `week` 落不了库。"""
    # 与 `/api/bars` 一致：**大小写严格**。同一个参数在两个接口上放宽程度不同，
    # 页面调 A 能过、调 B 报 400，是最难查的那种不一致。
    period = str(raw or "")
    if period not in PERIODS:
        raise HTTPException(
            status_code=400, detail=f"不认得的周期: {raw!r}（可选 {'/'.join(PERIODS)}）"
        )
    return periods_mod.base_period(period)


def _task_view(key: tuple[str, str], rec: dict[str, Any]) -> dict[str, Any]:
    elapsed = rec.get("elapsed")
    if elapsed is None:
        elapsed = time.monotonic() - float(rec["started_mono"])
    return {
        "code": key[0],
        "period": key[1],
        "requested": rec.get("requested", key[1]),
        "state": rec["state"],
        "rows": int(rec.get("rows") or 0),
        "start_ts": rec.get("start_ts"),
        "end_ts": rec.get("end_ts"),
        "error": rec.get("error") or "",
        "elapsed": round(float(elapsed), 1),
        "started_at": rec.get("started_at"),
        "finished_at": rec.get("finished_at"),
    }


def _run_sync(cfg: Any, code: str, period: str, requested: str, key: tuple[str, str]) -> None:
    """后台线程：串行取数并落库，把结果写回登记表。**不抛异常给线程**。"""
    error = ""
    rows = 0
    start_ts: str | None = None
    end_ts: str | None = None
    status = "failed"
    with _SYNC_RUN_LOCK:
        conn = meta.init(cfg.data.meta_db)  # SQLite 连接必须在用它自己的线程里开
        try:
            out = sync_mod.sync_one(conn, cfg, code, period)
            status, rows = out.status, out.rows
            start_ts, end_ts, error = out.start_ts, out.end_ts, out.error
        except Exception as exc:  # noqa: BLE001 - 后台线程抛异常会让按钮永远停在「正在同步」
            error = f"{type(exc).__name__}: {exc}"
            log.warning("同步任务异常 code=%s period=%s: %s", code, period, exc)
        finally:
            conn.close()
    with _SYNC_TASKS_LOCK:
        rec = _SYNC_TASKS.get(key)
        if rec is None:  # 被 clear_sync_tasks 清掉了（只可能发生在测试里）
            return
        rec.update(
            state=_SYNC_STATE.get(status, "error"),
            rows=rows,
            start_ts=start_ts,
            end_ts=end_ts,
            error=error,
            elapsed=time.monotonic() - float(rec["started_mono"]),
            finished_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        )


@router.post("/api/sync", status_code=202)
async def sync_start(request: Request) -> dict[str, Any]:
    """立刻返回 202，取数在后台线程里跑。

    **不能同步等待**：首次拉 5 分钟线实测 149 秒，浏览器会先超时。
    """
    cfg = _cfg(request)
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001 - 空 body / 坏 JSON 都按参数错误处理
        raise HTTPException(status_code=400, detail=f"请求体不是合法 JSON: {exc}") from exc
    code = normalize_code(str(body.get("code", "")))
    requested = str(body.get("period", "day"))
    period = _sync_target_period(requested)

    if period != "day" and markets.is_index(to_bs_code(code)):
        # 拒绝要发生在取数之前：实测 baostock 对 sh.000001/sh.000300/sh.000688 的
        # 分钟线一律返回 0 行（重登重试后仍是 0），等 30 秒再看「没有数据」是浪费。
        raise HTTPException(
            status_code=400,
            detail=(
                f"{code} 是指数，baostock 不提供指数的分钟线，同步这个周期只会空跑。"
                "指数看日线/周线/月线即可（周月线由本地日线聚合）。"
            ),
        )

    key = (code, period)
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    with _SYNC_TASKS_LOCK:
        rec = _SYNC_TASKS.get(key)
        if rec is not None and rec["state"] == "running":
            return _task_view(key, rec)  # 幂等：连点不叠加第二个任务
        rec = {
            "state": "running",
            "requested": requested.strip().lower(),
            "started_mono": time.monotonic(),
            "started_at": now,
            "finished_at": None,
        }
        _SYNC_TASKS[key] = rec
    threading.Thread(
        target=_run_sync, args=(cfg, code, period, requested, key),
        name=f"sync-{code}-{period}", daemon=True,
    ).start()
    return _task_view(key, rec)


@router.get("/api/sync/status")
def sync_status(request: Request, code: str, period: str = "day") -> dict[str, Any]:
    """轮询用：如实汇报 `idle/running/done/skipped/error` 与失败原因。"""
    key = (normalize_code(code), _sync_target_period(period))
    with _SYNC_TASKS_LOCK:
        rec = _SYNC_TASKS.get(key)
        if rec is None:
            return {
                "code": key[0], "period": key[1], "requested": str(period), "state": "idle",
                "rows": 0, "start_ts": None, "end_ts": None, "error": "", "elapsed": 0.0,
                "started_at": None, "finished_at": None,
            }
        return _task_view(key, rec)
