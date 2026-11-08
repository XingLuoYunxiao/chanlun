"""从**文本导出**导入 K 线（CSV / TSV / 空格分隔的 txt）。

为什么需要它
------------
通达信 macOS 版（`com.tdx.mac2022` v3.61）**没有 vipdoc**：把官方 `.dmg` 只读挂载
后，包内不存在 `vipdoc` / `fzline` / `minline` / `lday` / `.lc5` / `.lc1` /
`盘后数据` 任何字面量（4 种编码 × 全部 >200KB 文件，见 D-09 变更历史）。
而它的原生二进制里**有** `导出` / `CSV` / `Excel` / `txt`。

所以 macOS 上要拿本地 K 线，唯一现实做法是**从客户端导出文本再导入**，
这个模块就是那个「再导入」。

设计：容错，但**拒绝猜**
------------------------
导出格式没有样本，所以这里对**布局**宽容、对**语义**严格：

- **编码**：依次试 `utf-8-sig` / `utf-8` / `gb18030`（Windows 版导出是 GBK，
  macOS 版未知）。哪个能解出合法表头就用哪个。
- **分隔符**：`,` / `\\t` / `;` / 连续空白，按表头行自动判定。
- **前导行**：导出文件常有标题行、空行、`代码 名称 周期` 之类的抬头 —— 在前 30 行里
  找**表头行**（同时含日期类与收盘类列名），之前的都当抬头丢掉。
- **列名**：中英文都认，**按名字取列，不按位置**。导出的列顺序各家不同，
  按位置取会**静默串列**（把最高当收盘用，谁也不报错）。找不到必需列就报错。
- **时间戳**：有独立「时间」列就拼成 ``YYYY-MM-DD HH:MM``；否则用日期列，
  日线就是 ``YYYY-MM-DD``。
- **校验**：沿用 `types.normalize`（剔除非正价格、字段越界），并**额外要求 OHLC 自洽**
  （`low <= min(开,收) <= max(开,收) <= high`）—— 不满足就报错，不静默写库。

不做什么
--------
**不做复权**。导出的是客户端当时显示的口径（前复权/不复权取决于用户设置），
本模块照原样落库并把口径交给调用方声明 —— 猜口径比不导入危险得多。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .types import empty_frame, normalize

#: 依次尝试的编码。`gb18030` 是 GBK 的超集，Windows 版通达信导出就是它。
ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030")
#: 能当分隔符的候选（空白单独处理，因为它会顺带吃掉连续空格）。
_DELIMS: tuple[str, ...] = (",", "\t", ";", "|")
#: 表头往前最多看多少行（导出文件的抬头一般没几行）。
_HEADER_SCAN = 30
#: 列名 → 逻辑字段。**长名放前面**，这样 `开盘价` 先于 `开盘` 命中。
_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("日期", "date", "trade_date", "datetime", "时间"),
    "time": ("时间", "time"),
    "open": ("开盘价", "开盘", "open"),
    "high": ("最高价", "最高", "high"),
    "low": ("最低价", "最低", "low"),
    "close": ("收盘价", "收盘", "close"),
    "volume": ("成交量", "volume", "vol"),
    "amount": ("成交额", "成交金额", "amount", "turnover"),
}
#: 没有它就构成不了一条 K 线。
PRICE_REQUIRED = ("open", "high", "low", "close")
#: 在数据行里找日期列时最多看几行（够判断「这列是不是日期」就行）。
_PROBE_ROWS = 5


class TextImportError(ValueError):
    """导出文件读不成 K 线 —— 消息面向用户，要说清缺什么、在哪一行。"""


def _norm_head(cell: str) -> str:
    """表头单元格归一：去掉空白、单位括号、BOM、末尾冒号，转小写。"""
    s = str(cell).strip().strip("\ufeff").lower()
    s = re.sub(r"[（(【\[].*?[)）】\]]", "", s)      # 去掉 `(元)` `[股]` 这类单位
    s = re.sub(r"[\s:：*]+", "", s)
    return s


def _match(cols: list[str]) -> dict[str, int]:
    """表头 → 逻辑字段到下标的映射。**按名字取列，不按位置。**"""
    got: dict[str, int] = {}
    for i, raw in enumerate(cols):
        cell = _norm_head(raw)
        if not cell:
            continue
        for field, names in _ALIASES.items():
            if field in got:
                continue
            if any(cell == _norm_head(n) or cell.startswith(_norm_head(n)) for n in names):
                got[field] = i
                break
    return got


def _split(line: str) -> list[str]:
    for d in _DELIMS:
        if d in line:
            return [c.strip() for c in line.split(d)]
    return line.split()


def _to_ts(date_cell: str, time_cell: str | None) -> str:
    """日期 +（可选）时间 → 契约里的 `ts`。"""
    raw = str(date_cell).strip().replace("/", "-")
    m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", raw)
    if not m:
        raise TextImportError(f"认不出日期 {date_cell!r}（支持 2026-10-13 / 20261013 / 2026/10/13）")
    day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if time_cell is None or not str(time_cell).strip():
        return day
    t = str(time_cell).strip().replace(":", "")
    if not re.fullmatch(r"\d{3,6}", t):
        raise TextImportError(f"认不出时间 {time_cell!r}（支持 0935 / 09:35 / 09:35:00）")
    t = t.zfill(4)[:4]
    return f"{day} {t[:2]}:{t[2:]}"


def _num(cell: str, field: str, line_no: int) -> float:
    s = str(cell).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "--", "None", "null"):
        return 0.0
    try:
        return float(s)
    except ValueError as exc:
        raise TextImportError(f"第 {line_no} 行的{field} {cell!r} 不是数字") from exc


def _parses_as_date(cell: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-?\d{2}-?\d{2}", str(cell).strip().replace("/", "-")))


def _detect_leading_extra(lines: list[str], header_at: int) -> int:
    """数据行比表头行多一列时，判断多的是不是**开头那个没列名的日期列**。

    来由：空白分隔的导出里，日期列的表头是**空的**，而 `split()` 会把前导空格吃掉 ——
    于是表头行比数据行少一格，后面所有列名**整体左移一位**（`开盘` 被当成日期列）。
    逗号分隔时不会有这个问题（空字段会被保留成 `""`），所以只在宽度真的差 1 时才动。

    返回要整体右移的列数（0 或 1）。判据是**数据第一列的值真的像日期** —— 不靠猜。
    """
    head_w = len(_split(lines[header_at]))
    widths = [len(_split(lines[i])) for i in range(header_at + 1, min(len(lines), header_at + 1 + _PROBE_ROWS))
              if lines[i].strip()]
    if not widths:
        return 0
    data_w = max(set(widths), key=widths.count)
    if data_w != head_w + 1:
        return 0
    probes = [_split(lines[i])[0] for i in range(header_at + 1, min(len(lines), header_at + 1 + _PROBE_ROWS))
              if lines[i].strip()]
    return 1 if len(probes) >= 2 and all(_parses_as_date(p) for p in probes) else 0


def _find_date_column(lines: list[str], header_at: int, cols: dict[str, int],
                      *, shifted: bool = False) -> int:
    """日期列表头为**空**时，在数据里找出那一列。

    **不靠猜**：候选列必须（a）没被别的列占用，(b）值真的能解析成日期，
    而且要有**结构性依据**才算数 —— 二者满足其一即可：

    - 表头那一格是**空的**（`",开盘,最高,…"` 这种，空字段本身就是占位）；
    - 或者刚刚因为数据比表头宽一列而**整体右移过**（见 `_detect_leading_extra`）。

    没有结构性依据时（列宽相同、表头也没空位），要求**至少两行**都像日期 ——
    一行巧合不算证据。找不到就抛错，绝不拿开盘点当日期。

    这条是有实际来由的：通达信导出就把日期列的表头留空、且是空白分隔，
    所以「表头里找日期列」会直接失败，而文件本身完全正常。
    """
    head_cells = _split(lines[header_at])
    taken = set(cols.values())
    width = max(len(_split(lines[i])) for i in range(header_at, min(len(lines), header_at + _PROBE_ROWS)))
    for col in range(width):
        if col in taken:
            continue
        probes = []
        for i in range(header_at + 1, min(len(lines), header_at + 1 + _PROBE_ROWS)):
            cells = _split(lines[i])
            if len(cells) > col and cells[col].strip():
                probes.append(cells[col])
        if not probes or not all(_parses_as_date(p) for p in probes):
            continue
        empty_head = col < len(head_cells) and not head_cells[col].strip()
        if shifted or empty_head or len(probes) >= 2:
            return col
    raise TextImportError(
        "找不到日期列：表头里没有日期/时间列名，数据里也没有哪一列的值能解析成日期"
        "（支持 2026-10-13 / 20261013 / 2026/10/13）。"
        "如果日期列的表头是空的，那是支持的 —— 但那一列要么在表头留下空位，"
        "要么至少有**两行**真的像日期。"
    )


def parse_text(text: str) -> pd.DataFrame:
    """文本 → BarFrame 契约的 DataFrame。**认不出就抛 `TextImportError`。**"""
    lines = text.splitlines()
    header_at: int | None = None
    cols: dict[str, int] = {}
    for i in range(min(_HEADER_SCAN, len(lines))):
        cells = _split(lines[i])
        if len(cells) < 4:
            continue
        got = _match(cells)
        # 表头判据只要**价格四列**齐全：日期列可能没有列名（通达信导出就是这样），
        # 那就交给 `_find_date_column` 到数据里按内容确认。
        if all(f in got for f in PRICE_REQUIRED):
            header_at, cols = i, got
            break
    if header_at is None:
        raise TextImportError(
            f"前 {_HEADER_SCAN} 行里没找到表头：需要同时含"
            f"「开盘/最高/最低/收盘」（中英文均可）。"
            f"首行是：{lines[0][:80]!r}" if lines else "文件是空的"
        )
    if "date" not in cols:
        shift = _detect_leading_extra(lines, header_at)
        if shift:
            cols = {k: v + shift for k, v in cols.items()}
        cols["date"] = _find_date_column(lines, header_at, cols, shifted=bool(shift))

    rows: list[dict[str, object]] = []
    for n, line in enumerate(lines[header_at + 1 :], start=header_at + 2):
        if not line.strip():
            continue
        cells = _split(line)
        if len(cells) <= max(cols.values()):
            continue
        try:
            ts = _to_ts(cells[cols["date"]], cells[cols["time"]] if "time" in cols else None)
        except TextImportError:
            # 抬头/合计行形状不对是正常的；但只要它看起来像数据行就必须报错，
            # 否则会**静默少几根 K 线**。判据：这一行的开盘点得出数字吗。
            if _looks_numeric(cells[cols["open"]]):
                raise
            continue
        rows.append({
            "ts": ts,
            "open": _num(cells[cols["open"]], "开盘", n),
            "high": _num(cells[cols["high"]], "最高", n),
            "low": _num(cells[cols["low"]], "最低", n),
            "close": _num(cells[cols["close"]], "收盘", n),
            "volume": _num(cells[cols["volume"]], "成交量", n) if "volume" in cols else 0.0,
            "amount": _num(cells[cols["amount"]], "成交额", n) if "amount" in cols else 0.0,
        })
    if not rows:
        raise TextImportError(f"表头在第 {header_at + 1} 行，但后面没有一行能解成 K 线")

    df = normalize(pd.DataFrame(rows))
    if df.empty:
        raise TextImportError("所有行都被剔除了（非正价格 / 字段越界），没有可导入的数据")
    bad = (df["low"] > df[["open", "close"]].min(axis=1)) | (
        df[["open", "close"]].max(axis=1) > df["high"]
    )
    if bool(bad.any()):
        first = df[bad].iloc[0]
        raise TextImportError(
            f"OHLC 不自洽：{int(bad.sum())}/{len(df)} 行违反 "
            f"low<=min(开,收)<=max(开,收)<=high，例如 {first['ts']} "
            f"开{first['open']} 高{first['high']} 低{first['low']} 收{first['close']} "
            f"—— 多半是**列名对上了但列序/含义不对**，请核对导出文件的表头"
        )
    return df.reset_index(drop=True)


def _looks_numeric(cell: str) -> bool:
    try:
        float(str(cell).strip().replace(",", ""))
        return True
    except ValueError:
        return False


def load(path: str | Path, encoding: str | None = None) -> pd.DataFrame:
    """读文件并解析。`encoding` 不给就按 `ENCODINGS` 依次试。"""
    p = Path(path)
    blob = p.read_bytes()
    if encoding:
        tries: tuple[str, ...] = (encoding,)
    else:
        tries = ENCODINGS
    last: Exception | None = None
    for enc in tries:
        try:
            return parse_text(blob.decode(enc))
        except (UnicodeDecodeError, TextImportError) as exc:
            last = exc
    raise TextImportError(f"{p.name} 解析失败（试过 {'/'.join(tries)}）：{last}") from last


def empty() -> pd.DataFrame:
    """给调用方一个空帧，免得自己 import `types`。"""
    return empty_frame()
