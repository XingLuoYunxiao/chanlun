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
from pathlib import Path

import pandas as pd

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
