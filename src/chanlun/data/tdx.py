"""通达信 vipdoc 本地数据的解析与整包解压。

格式（在 2026-09-30 官方 hsjday.zip 上逐字段核对过）：

日线 `.day`，32 字节定长小端：
``u32 日期YYYYMMDD | u32 开 | u32 高 | u32 低 | u32 收 | f32 成交额(元) | u32 成交量(股) | u32 保留``
价格单位是**分**，÷100 得元。

分钟线 `.lc5` / `.lc1`：
``u16 日期=(年-2004)*2048+月*100+日 | u16 当日第几分钟 | f32 开/高/低/收(元) | f32 成交额(元) |
u32 量(股) | u32 保留``
价格单位是**元**，与日线不同（两套单位混用会差 100 倍）。

官方只公开日线整包（``hsjday.zip``），**没有公开的分钟整包**。但分钟解析在
2026-10-13 补上了，因为多了一条**不依赖整包**的取数路径：通达信官方有
**iMac 版**（V3.61，支持 Intel/M1–M4），客户端自带的「盘后数据下载」会把分钟线
落成本地 ``vipdoc/{market}/fzline/*.lc5`` 与 ``minline/*.lc1`` —— 那是**目录**而不是 zip，
`import_dir` 直接就能吃。**vipdoc 里指数与股票同目录**，所以这条路径同时解决了
「指数没有分钟源」那个空白（baostock 对指数分钟线返回 0 行）。

**日期编码仍是待真实样本核对的假设**：`parse_minute_bytes` 按上面的公式解码，
并对**每一条**做范围校验（年/月/日/时/分/涨跌合理性/时间单调），任何一条不合就
`TdxFormatError` —— 宁可当场炸，也不静默产出一份日期错位的行情。落地后的第一件事
应当是跑 `verify_minute_against_day()`（用同代码的 `.day` 交叉验证日聚合 OHLC），
过了才算核对完成。
"""

from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import markets as _markets
from . import meta, store
from .types import normalize

RECORD = 32
DAY_RECORD = struct.Struct("<IIIIIfII")
#: 分钟线记录：日期(u16) 分钟数(u16) 开 高 低 收 额(5×f32) 量(u32) 保留(u32) = 32 字节。
#: 字段**顺序与日线一致**（OHLC、成交额、成交量），只有日期/时间的编码不同。
MINUTE_RECORD = struct.Struct("<HHfffffII")
MINUTE_PERIODS = ("5", "1")
DAY_URL = "http://data.tdx.com.cn/vipdoc/hsjday.zip"
#: 通达信 iMac 版（「盘后数据下载」的分钟线就落在这个客户端的 vipdoc 里）。
MAC_CLIENT_URL = "https://data.tdx.com.cn/level2/tdx_macosx.dmg"
PERIOD_SUFFIXES = {"day": ".day", "5": ".lc5", "1": ".lc1"}
_SUFFIX_PERIODS = {v: k for k, v in PERIOD_SUFFIXES.items()}
FOLDERS = {"day": "lday", "5": "fzline", "1": "minline"}
DATE_LO, DATE_HI = 19900101, 29991231
COLUMNS = ["ts", "open", "high", "low", "close", "volume", "amount"]
#: 解码出的年份边界。低于 `DATE_LO//10000` 或高得离谱，说明位宽假设错了。
YEAR_LO, YEAR_HI = 1990, 2100


class TdxFormatError(ValueError):
    """文件长度、记录内容或路径不合规。"""


def parse_day_bytes(data: bytes) -> pd.DataFrame:
    """把 ``.day`` 原始字节解成 BarFrame 契约的 DataFrame。"""
    if len(data) % RECORD:
        raise TdxFormatError(f"文件长度 {len(data)} 不是 {RECORD} 的整数倍（多半被截断）")
    rows = []
    for i in range(0, len(data), RECORD):
        date, op, hi, lo, cl, amount, volume, _res = DAY_RECORD.unpack_from(data, i)
        if not DATE_LO <= date <= DATE_HI:
            raise TdxFormatError(f"第 {i // RECORD} 条日期非法: {date}")
        rows.append(
            {
                "ts": f"{date // 10000:04d}-{date // 100 % 100:02d}-{date % 100:02d}",
                "open": op / 100,
                "high": hi / 100,
                "low": lo / 100,
                "close": cl / 100,
                "volume": float(volume),
                "amount": float(amount),
            }
        )
    return normalize(pd.DataFrame(rows, columns=COLUMNS))


def minute_ts(raw_date: int, raw_time: int) -> str:
    """``u16 日期`` + ``u16 当日第几分钟`` → ``YYYY-MM-DD HH:MM``。

    日期编码：``raw = (年 - 2004) * 2048 + 月 * 100 + 日``。高 5 位是「年 − 2004」，
    低 11 位是 ``月*100 + 日``（月 ≤ 12、日 ≤ 31 ⇒ 最大 1231 < 2048，不会进位到高位）。

    时间字段是**当日第几分钟**（9:35 → 575），不是 ``HHMM``。两者混淆会让
    所有时间错位，所以这里只认分钟数，并对结果做范围校验。
    """
    year = (raw_date >> 11) + 2004
    month, day = divmod(raw_date & 0x7FF, 100)
    hour, minute = divmod(raw_time, 60)
    if not YEAR_LO <= year <= YEAR_HI:
        raise TdxFormatError(f"日期解码出界：raw={raw_date} → 年 {year}")
    if not 1 <= month <= 12:
        raise TdxFormatError(f"日期解码出界：raw={raw_date} → 月 {month}")
    if not 1 <= day <= 31:
        raise TdxFormatError(f"日期解码出界：raw={raw_date} → 日 {day}")
    if not 0 <= raw_time < 24 * 60:
        raise TdxFormatError(f"时间解码出界：raw={raw_time}（应为当日第几分钟）")
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


def parse_minute_bytes(data: bytes, period: str = "5") -> pd.DataFrame:
    """把 ``.lc5`` / ``.lc1`` 原始字节解成 BarFrame 契约的 DataFrame。

    **每条都校验**，任何一条不合就抛 ``TdxFormatError``：日期/时间能解出合法值、
    时间严格递增、OHLC 为正且满足 ``low <= min(open, close) <= max(open, close) <= high``。
    这样「日期编码假设错了」会当场炸，而不是静默产出一份日期错位的行情 ——
    后者一旦落库，结构会跨着错位的 bar 算，比直接失败危险得多。

    解码假设是否成立，由 ``verify_minute_against_day()`` 用同代码的 ``.day`` 交叉验证。
    """
    if len(data) % RECORD:
        raise TdxFormatError(f"文件长度 {len(data)} 不是 {RECORD} 的整数倍（多半被截断）")
    rows = []
    prev_ts: str | None = None
    for i in range(0, len(data), RECORD):
        raw_date, raw_time, op, hi, lo, cl, amount, volume, _res = MINUTE_RECORD.unpack_from(data, i)
        n = i // RECORD
        ts = minute_ts(raw_date, raw_time)
        if prev_ts is not None and ts <= prev_ts:
            raise TdxFormatError(f"第 {n} 条时间没有前进: {prev_ts} → {ts}")
        prev_ts = ts
        if not (op > 0 and hi > 0 and lo > 0 and cl > 0):
            raise TdxFormatError(f"第 {n} 条（{ts}）有非正价格: {op}/{hi}/{lo}/{cl}")
        if not (lo <= min(op, cl) and max(op, cl) <= hi):
            raise TdxFormatError(
                f"第 {n} 条（{ts}）OHLC 不成立: 开{op} 高{hi} 低{lo} 收{cl}"
            )
        rows.append(
            {
                "ts": ts,
                "open": float(op),
                "high": float(hi),
                "low": float(lo),
                "close": float(cl),
                "volume": float(volume),
                "amount": float(amount),
            }
        )
    return normalize(pd.DataFrame(rows, columns=COLUMNS))


def parse_file(path: str | Path, period: str | None = None) -> pd.DataFrame:
    """按扩展名分派；``period`` 只用于交叉核对扩展名（给了就必须一致）。"""
    p = Path(path)
    suffix = p.suffix.lower()
    got = _SUFFIX_PERIODS.get(suffix)
    if got is None:
        raise TdxFormatError(f"未知扩展名 {suffix}（只认 .day/.lc5/.lc1）: {p.name}")
    if period is not None and str(period) != got:
        raise TdxFormatError(f"周期与扩展名不符：--period {period} 但文件是 {p.name}")
    data = p.read_bytes()
    return parse_day_bytes(data) if got == "day" else parse_minute_bytes(data, got)


def verify_minute_against_day(minute: pd.DataFrame, day: pd.DataFrame) -> list[str]:
    """用同代码的日线交叉验证分钟线解码：按日聚合后 OHLC 必须对得上。

    这是**唯一能证明日期/时间编码假设成立**的检查（单看分钟文件内部自洽是不够的：
    一个整体错位的日期同样能自洽）。返回问题列表，空 = 通过。
    """
    problems: list[str] = []
    if len(minute) == 0 or len(day) == 0:
        return ["分钟线或日线为空，无法交叉验证"]
    m = minute.copy()
    m["d"] = m["ts"].str[:10]
    agg = m.groupby("d").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
    )
    ref = day.copy()
    ref["d"] = ref["ts"].str[:10]
    ref = ref.set_index("d")
    common = [d for d in agg.index if d in ref.index]
    if not common:
        return ["分钟线与日线没有任何共同交易日 —— 日期编码几乎肯定错了"]
    for d in common:
        a, r = agg.loc[d], ref.loc[d]
        for col in ("open", "high", "low", "close"):
            if abs(float(a[col]) - float(r[col])) > 1e-6:
                problems.append(
                    f"{d} {col} 不一致：分钟聚合 {float(a[col])} vs 日线 {float(r[col])}"
                )
                if len(problems) >= 10:
                    return problems
    return problems


def split_vipdoc_path(name: str) -> tuple[str, str, str]:
    """``sh\\lday\\sh600000.day`` → ``('sh', '600000', 'day')``。

    zip 内路径用的是反斜杠，必须归一化，否则 ``unzip`` 会解出一堆带 ``\\`` 的扁平文件名。
    """
    parts = [p for p in name.replace("\\", "/").split("/") if p]
    if len(parts) != 3:
        raise TdxFormatError(f"无法识别的 vipdoc 路径: {name}")
    market, _folder, filename = parts
    suffix = Path(filename).suffix.lower()
    period = _SUFFIX_PERIODS.get(suffix)
    if period is None:
        raise TdxFormatError(f"未知扩展名: {filename}")
    stem = Path(filename).stem
    if not stem.startswith(market):
        raise TdxFormatError(f"文件名与市场目录不符: {name}")
    code = stem[len(market) :]
    if len(code) != 6 or not code.isdigit():
        raise TdxFormatError(f"代码不是 6 位数字: {filename}")
    return market, code, period


def is_index(market: str, code: str) -> bool:
    return (market == "sh" and code.startswith("00")) or (market == "sz" and code.startswith("39"))


def store_key(market: str, code: str) -> str:
    """落库键：交给 :mod:`chanlun.data.markets` 的统一规则。

    只有「裸码会指向另一个市场」时才保留前缀：``sh000001``（上证指数）必须留
    ``sh.``，否则与 ``sz000001``（平安银行）抢同一个文件；``sz399001``（深证成指）
    的 ``39`` 号段深市独有，裸码不撞车，前缀是冗余的。

    注意 ``is_index`` 是**品种**分类（谁是指数），与「键要不要带前缀」是两件事，
    所以这里不再复用它 —— 之前两者混用，导致 ``sz.399001`` 多背了一个没用的前缀。
    """
    return _markets.store_key(f"{market}.{code}")


def list_package(zip_path: str | Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as z:
        return [n for n in z.namelist() if not n.endswith("/")]


def extract_package(
    zip_path: str | Path, dest: str | Path, periods: tuple[str, ...] = ("day",)
) -> int:
    """解到 ``dest/{market}/{folder}/{file}``，返回写出的文件数。"""
    dest = Path(dest)
    written = 0
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            try:
                market, _code, period = split_vipdoc_path(name)
            except TdxFormatError:
                continue
            if period not in periods:
                continue
            out = dest / market / FOLDERS[period] / Path(name.replace("\\", "/")).name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(name))
            written += 1
    return written


# ---------------- 整包导入 ----------------
STOCK_RULES = {"sh": ("60", "68"), "sz": ("00", "30"), "bj": ("43", "83", "87", "89", "92")}
INDEX_RULES = {"sh": ("00",), "sz": ("39",)}
FUND_RULES = {"sh": ("11", "50", "51", "56", "58"), "sz": ("12", "15", "16", "18", "20")}
BOARD_RULES = {"sh": ("88",), "bj": ("81", "82")}  # 通达信自定义板块/指数，语义未核实
MIN_OVERLAP = 20
ALIAS_TOL = 0.005


@dataclass(frozen=True)
class Symbol:
    market: str
    code: str
    key: str
    period: str
    path: Path
    kind: str


@dataclass(frozen=True)
class ImportStats:
    ok: int = 0
    failed: int = 0
    rows: int = 0
    skipped_empty: int = 0
    skipped_alias: int = 0
    aliased: tuple[tuple[str, str], ...] = ()
    newest_by_market: tuple[tuple[str, str], ...] = ()
    errors: tuple[tuple[str, str], ...] = ()
    #: 分钟线**过了日线交叉验证**、因此允许落库的只数。
    verified: int = 0
    #: 分钟线**没通过**（或没有参照可验）因此被拒绝落库的只数。它们进 `errors`，
    #: 但单独计数 —— 「解不出来」与「解出来但对不上」是两回事，运维要能一眼分开。
    unverified: int = 0


def _day_reference(
    src: str | Path, market: str, code: str, key: str, conn
) -> tuple[pd.DataFrame | None, str]:
    """分钟线交叉验证要用的**日线参照**，返回 `(DataFrame 或 None, 来源说明)`。

    优先 vipdoc 自己的 ``{market}/lday/{market}{code}.day`` —— 它与分钟线
    **同口径（都是不复权 raw）**，逐格可比，是唯一干净的参照。

    退而求其次用本地库的日线，**但只在它也是 `raw` 口径时才用**：库里多数日线是
    baostock **前复权**，拿它跟不复权的分钟线比，会在每个除权日整片对不上 ——
    那是**口径差**，不是解码错，用它当参照会把好数据判成坏的。

    两个都拿不到就返回 `None`：**没有参照就不能证明解码是对的**，此时调用方拒绝落库。
    """
    p = Path(src) / market / FOLDERS["day"] / f"{market}{code}.day"
    if p.exists():
        try:
            return parse_day_bytes(p.read_bytes()), "vipdoc .day（raw）"
        except Exception:  # 坏参照不该把分钟线一起判死，往下找库里那份
            pass
    if conn is not None:
        row = meta.get_sync(conn, key, "day")
        if row is not None and str(row["adjust"] or "") == "raw":
            df = store.read(key, "day")
            if len(df):
                return df, "本地库 day（raw）"
    return None, "无 raw 口径的日线参照"


def classify(market: str, code: str) -> str:
    if is_index(market, code):
        return "index"
    head = code[:2]
    if head in BOARD_RULES.get(market, ()):
        return "board"
    if head in STOCK_RULES.get(market, ()):
        return "stock"
    if head in FUND_RULES.get(market, ()):
        return "fund"
    return "other"


def scan_dir(
    src: str | Path,
    period: str = "day",
    markets: tuple[str, ...] = ("sh", "sz", "bj"),
    kinds: tuple[str, ...] = ("stock", "index"),
    codes: tuple[str, ...] | None = None,
) -> list[Symbol]:
    """列出待导入的品种；默认只要个股与指数。"""
    src = Path(src)
    want = set(codes) if codes else None
    found: list[Symbol] = []
    for market in markets:
        folder = src / market / FOLDERS[period]
        for path in sorted(folder.glob(f"*{PERIOD_SUFFIXES[period]}")):
            try:
                mkt, code, _ = split_vipdoc_path(f"{market}/{folder.name}/{path.name}")
            except TdxFormatError:
                continue
            kind = classify(mkt, code)
            key = store_key(mkt, code)
            if kind not in kinds or (want and code not in want and key not in want):
                continue
            found.append(Symbol(mkt, code, key, period, path, kind))
    return found


def detect_bj_aliases(src: str | Path, period: str = "day") -> dict[str, str]:
    """北交所改号：老码 → ``920`` + 老码后三位，只在价格证据成立时认。

    判据：重叠交易日 ≥ ``MIN_OVERLAP`` 天，且重叠日收盘价最大差 < ``ALIAS_TOL`` 元。
    证据不足的（``81/82`` 段等）不认，宁可留两份，也不把两只不同标的并成一只。
    """
    folder = Path(src) / "bj" / FOLDERS[period]
    if not folder.exists():
        return {}
    old: dict[str, pd.DataFrame] = {}
    new: dict[str, pd.DataFrame] = {}
    for path in folder.glob(f"*{PERIOD_SUFFIXES[period]}"):
        code = path.stem[len("bj") :]
        (new if code.startswith("920") else old)[code] = parse_file(path)
    aliases: dict[str, str] = {}
    for code, df in old.items():
        other = new.get(f"920{code[-3:]}")
        if other is None or len(df) == 0 or len(other) == 0:
            continue
        merged = df[["ts", "close"]].merge(
            other[["ts", "close"]], on="ts", suffixes=("_o", "_n")
        )
        if len(merged) < MIN_OVERLAP:
            continue
        if float((merged["close_o"] - merged["close_n"]).abs().max()) < ALIAS_TOL:
            aliases[code] = f"920{code[-3:]}"
    return aliases


def import_dir(
    src: str | Path,
    conn,
    period: str = "day",
    markets: tuple[str, ...] = ("sh", "sz", "bj"),
    kinds: tuple[str, ...] = ("stock", "index"),
    codes: tuple[str, ...] | None = None,
    dry_run: bool = False,
    allow_unverified: bool = False,
) -> ImportStats:
    """把解开的 vipdoc 目录写进本地库；口径是**不复权 raw**。

    **会整份覆盖**同名 `(code, period)` 文件（`store.write` 是原子替换，不是追加）。
    因此这是「换源」而不是「补充数据源」：`day` 周期原来那份 baostock 前复权会被换成
    不复权，`sync_state.adjust` 从 `"2"` 变 `raw`。缠论结构本身对两种口径都能算，
    但**跨口径拼接**（旧前复权 + 新不复权）会在除权日长出假跳空，所以同一 `(code, period)`
    只能有一个写入方 —— 见 spec §3.2。

    **分钟周期还多一道门（`period in MINUTE_PERIODS`）**：每条都要过
    `verify_minute_against_day()` 才允许落库，参照是 vipdoc 自己的 `.day`
    （同口径 raw）或本地库里 `adjust == "raw"` 的日线。过不了就**不写**，
    计进 `unverified` 并留一条 error。理由是分钟线的日期编码至今仍是假设，
    而"整体错位"的日期内部完全自洽，只有日线交叉验证能证伪。
    真要放行用 `allow_unverified=True`（CLI 的 `--allow-unverified`），
    放行的也会如实计进 `unverified`。
    """
    syms = scan_dir(src, period, markets, kinds, codes)
    aliases = detect_bj_aliases(src, period) if "bj" in markets and not codes else {}
    live = {s.code for s in syms}
    ok = failed = rows = skipped_empty = skipped_alias = verified = unverified = 0
    newest: dict[str, str] = {}
    errors: list[tuple[str, str]] = []
    merged_pairs: list[tuple[str, str]] = []
    for sym in syms:
        target = aliases.get(sym.code)
        if target in live:  # 老码并到 920 新码（新码是当前代码）
            skipped_alias += 1
            merged_pairs.append((sym.code, target))
            if conn is not None and not dry_run:
                meta.save_alias(
                    conn, sym.code, target, sym.market, f"重叠收盘价一致（{MIN_OVERLAP}+ 日）"
                )
            continue
        try:
            df = parse_file(sym.path, period)
        except Exception as exc:  # 单只坏文件不拖垮整包
            failed += 1
            errors.append((sym.key, f"{type(exc).__name__}: {exc}"))
            if conn is not None and not dry_run:
                meta.set_sync(conn, sym.key, period, None, None, 0, "raw", error=str(exc))
            continue
        if len(df) == 0:  # 空文件既不是成功也不是失败：不写库、不记账
            skipped_empty += 1
            continue
        if period in MINUTE_PERIODS:
            # **不验证就不落库。** 这是 D-09 那条自我约束（"宁可不做，也不写没核对过的
            # 二进制解码"）在代码里的落点：日期编码至今仍是**假设**，而一个整体错位的
            # 日期是"内部自洽"的，靠肉眼与内部校验都发现不了，只有拿同代码的日线
            # 交叉验证才能证伪。所以门开在这里，而不是等落库之后再发现。
            day_df, ref = _day_reference(src, sym.market, sym.code, sym.key, conn)
            problems = (
                ["找不到可用于交叉验证的日线参照（需要 raw 口径的 .day）"]
                if day_df is None
                else verify_minute_against_day(df, day_df)
            )
            if problems and not allow_unverified:
                unverified += 1
                reason = f"分钟线未通过日线交叉验证（参照：{ref}）：{problems[0]}"
                errors.append((sym.key, reason))
                if conn is not None and not dry_run:
                    meta.set_sync(conn, sym.key, period, None, None, 0, "raw", error=reason)
                continue
            if problems:      # 显式放行：照写，但如实记一条
                unverified += 1
                errors.append((
                    sym.key,
                    f"未验证但被 --allow-unverified 放行（参照：{ref}）：{problems[0]}",
                ))
            else:
                verified += 1
        if not dry_run:
            store.write(sym.key, period, df)
            meta.set_sync(
                conn, sym.key, period, df.iloc[0]["ts"], df.iloc[-1]["ts"], len(df), "raw"
            )
        ok += 1
        rows += len(df)
        end = df.iloc[-1]["ts"]
        if end > newest.get(sym.market, ""):
            newest[sym.market] = end
    return ImportStats(
        ok,
        failed,
        rows,
        skipped_empty,
        skipped_alias,
        tuple(sorted(merged_pairs)),
        tuple(sorted(newest.items())),
        tuple(errors),
        verified,
        unverified,
    )


def fetch_package(dest_dir: str | Path, url: str = DAY_URL) -> Path:
    """下载官方整包；本地已有且大小与服务器一致就跳过。

    551 MB 的包每次重下是纯浪费，用 ``Content-Length`` 做「大小一致即认为同一版本」的判断；
    下载先落 ``.part`` 再改名，中途断线不会留下一个看起来完整的坏包。
    """
    import urllib.request

    dest = Path(dest_dir) / url.rsplit("/", 1)[-1]
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as resp:
        remote = int(resp.headers.get("Content-Length") or 0)
    if dest.exists() and remote and dest.stat().st_size == remote:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    return dest
