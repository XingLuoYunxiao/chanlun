"""通达信 vipdoc 本地数据的解析与整包解压。

格式（在 2026-09-30 官方 hsjday.zip 上逐字段核对过）：

日线 `.day`，32 字节定长小端：
``u32 日期YYYYMMDD | u32 开 | u32 高 | u32 低 | u32 收 | f32 成交额(元) | u32 成交量(股) | u32 保留``
价格单位是**分**，÷100 得元。

分钟线 `.lc5` / `.lc1`：
``u16 日期=(年-2004)*2048+月*100+日 | u16 当日第几分钟 | f32 开/高/低/收(元) | f32 成交额(元) |
u32 量(股) | u32 保留``
价格单位是**元**，与日线不同（两套单位混用会差 100 倍）。

官方只公开日线整包（``hsjday.zip``），没有公开的分钟整包，因此分钟解析**故意不实现**：
等拿到真实 ``lc5`` 文件能核对日期编码后再补——宁可不做，也不写没核对过的二进制解码。
"""

from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import meta, store
from .types import normalize

RECORD = 32
DAY_RECORD = struct.Struct("<IIIIIfII")
DAY_URL = "http://data.tdx.com.cn/vipdoc/hsjday.zip"
PERIOD_SUFFIXES = {"day": ".day", "5": ".lc5", "1": ".lc1"}
_SUFFIX_PERIODS = {v: k for k, v in PERIOD_SUFFIXES.items()}
FOLDERS = {"day": "lday", "5": "fzline", "1": "minline"}
DATE_LO, DATE_HI = 19900101, 29991231
COLUMNS = ["ts", "open", "high", "low", "close", "volume", "amount"]


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


def parse_file(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() != ".day":
        raise TdxFormatError(f"只支持 .day（分钟线缺可核对的真实样本）: {p.name}")
    return parse_day_bytes(p.read_bytes())


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
    """个股用裸 6 位（与一期一致）；指数带市场前缀。

    ``sh000001``（上证指数）与 ``sz000001``（平安银行）裸码都是 ``000001``，不区分就会互相覆盖。
    """
    return f"{market}.{code}" if is_index(market, code) else code


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
) -> ImportStats:
    """把解开的 vipdoc 目录写进本地库；口径是**不复权 raw**（传入即写，不覆盖已有一期数据）。"""
    syms = scan_dir(src, period, markets, kinds, codes)
    aliases = detect_bj_aliases(src, period) if "bj" in markets and not codes else {}
    live = {s.code for s in syms}
    ok = failed = rows = skipped_empty = skipped_alias = 0
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
            df = parse_file(sym.path)
        except Exception as exc:  # 单只坏文件不拖垮整包
            failed += 1
            errors.append((sym.key, f"{type(exc).__name__}: {exc}"))
            if conn is not None and not dry_run:
                meta.set_sync(conn, sym.key, period, None, None, 0, "raw", error=str(exc))
            continue
        if len(df) == 0:  # 空文件既不是成功也不是失败：不写库、不记账
            skipped_empty += 1
            continue
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
