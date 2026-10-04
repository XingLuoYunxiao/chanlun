#!/usr/bin/env python
"""``ARCHITECTURE.md`` 行号引用校验器 —— 让硬编码行号不能再悄悄漂移。

**为什么需要它**：``ARCHITECTURE.md`` 里写死了大量 ``文件.py:行号`` 引用。
它们是文档的一部分，却没人保证它们跟着代码一起动。此前靠人肉复审，
结果是「转抄的行号是错的」这类事故反复发生（本项目已发生 14 次）。
本工具把这些引用变成**可机检**的东西：文件是否存在、范围是否越界、
符号是否漂移、符号是否无从校验。

**钉死的规则**（改动这些规则等于换了一把尺子，历史数字全部作废）：

1. **头部正则不要求反引号、不许硬编码 ``\\.py``** —— 否则 ``app.js:NN``
   这类引用会被静默跳过，探针会把「一个字都没检查」报成全绿。
   续段（``foo.py:12-16, 20``）**合并进同一条引用**，不新起一条；
   续段的搜索区间**截止在该引用所在的反引号跨度末尾**，否则会把散文里的数字吃进来。
2. **符号提取的跳过规则不许用 ``".py:" in s`` 这种子串判断** ——
   它会误伤本项目自己的 ``文件.py::符号`` 记法（双冒号，不是行号引用）。
   要用「冒号后必须跟数字」的正则。
3. **符号漂移判据**：窗口 = 该引用所在文档行 ±1 行；窗口内**反引号跨度**里的
   标识符 token（≥2 字符）算候选符号；跨度本身是引用、或含空格/``=``/``(``/``)``
   的表达式不算符号名。符号的**定义行**（``def`` / ``class`` / 顶层 ``NAME =``）
   必须落在该文档行所引的**同一个文件**的行号范围并集内，否则记「符号漂移告警」。
4. **桶的性质**：硬判据 = 文件找不到 / 范围越界 / **区间倒置**（起 > 止；
   两端都可能落在文件范围内，只查上界看不见它）/ **引用行内容指纹不符**
   （见第 5 条）—— 硬判据**恒影响退出码**，符号漂移告警只在 ``--strict-symbols``
   下影响退出码。其余都是**建议桶**：
   路径歧义、首末行空行、缺符号无法校验、覆盖度偏弱点、引用行无可校验符号。
   非 ``.py`` 引用**只枚举、不判硬绿**（符号名写在散文里，自动规则无从解析）。
5. **引用行内容指纹（整文件行号平移检测）** —— 前四类判据**看不见整文件平移**：
   在 ``segment.py`` 头部插入 15 行后，本工具输出逐字节不变、退出码仍是 0。
   原因很直接：它们只问「行号在不在范围内」「符号定义行在不在范围内」，
   整体平移后两个问题的答案都还是「在」。要让平移可见，必须记住**被引行当时
   的内容**。口径：只覆盖 ``.py`` 桶里通过硬判据的那些引用；指纹 = 被引全部行
   （含续段）逐行喂 sha256 取前 16 位；键 = ``文件名:范围文本#文档内出现序号``，
   **键里不含文档行号** —— 否则在文档里插一段话就会让下面所有引用假失配。
   基线文件 ``optimizer/tools/doc_refs_baseline.json`` **随仓库提交**；
   改动任何被引代码行后必须重跑 ``--update-baseline``。

用法::

    python optimizer/tools/check_doc_line_refs.py            # 汇总
    python optimizer/tools/check_doc_line_refs.py --verbose   # 每条引用明细
    python optimizer/tools/check_doc_line_refs.py --strict-symbols  # 漂移升级为失败
    python optimizer/tools/check_doc_line_refs.py --update-baseline  # 改了被引代码后重记指纹
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOC = REPO_ROOT / "ARCHITECTURE.md"

# 头部引用：`路径.扩展名:行号[-行号]`。不要求反引号，也不假定扩展名是 py。
REF_RE = re.compile(r"([A-Za-z0-9_./\-]+)\.([A-Za-z0-9]+):\s*(\d+)(?:\s*[-\u2013]\s*(\d+))?")
# 续段：`foo.py:12-16, 20` / `foo.py:12, 20-21`。要求后面是 `,` / `)` / 行尾，
# 否则散文里的数字（「共 34 处」）会被吃进来。
CONT_RE = re.compile(r"[,\s]+(\d+)(?:\s*[-\u2013]\s*(\d+))?(?=\s*(?:[,)]|$))")
# 符号名：≥2 个字符的标识符（`X[:t+1]` 里的 `X`、`t` 不算符号名）。
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")
# 表达式跨度（含这些字符）不算符号名：`MAX_SEGMENTS = 8`、`ema()`、`segs[p.end_idx]`…
EXPR_CHARS = " =()"
SECTION_RE = re.compile(r"^(#{1,6})\s*(\d+(?:\.\d+)*)")

PY_EXT = "py"


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Ref:
    """一条引用（一个头部匹配 + 它吃进来的续段）。"""

    doc_line: int
    path: str
    ext: str
    ranges: tuple[tuple[int, int], ...]
    span: tuple[int, int]
    raw: str = ""

    @property
    def filename(self) -> str:
        """文档里写的那一串（含扩展名）——解析和打印都用它，别只用 path。"""
        return f"{self.path}.{self.ext}"

    def ranges_text(self) -> str:
        return ", ".join(f"{s}-{e}" for s, e in self.ranges)

    def inverted_ranges(self) -> tuple[tuple[int, int], ...]:
        """起 > 止 的区间（写反了）。

        这是**独立于「越界」的硬判据**：`mod.py:10-3` 两端都在文件范围内，
        只查上界会静默放过它；`mod.py:99-3` 更糟 —— 起点越界、终点不越界，
        于是溜到 `res.lines[s - 1]` 处抛 `IndexError`（崩溃不是判定）。
        """
        return tuple((s, e) for s, e in self.ranges if e < s)

    def below_lower_bound_ranges(self) -> tuple[tuple[int, int], ...]:
        """行号 < 1 的区间。文档行号是 1-based，`0` 会让 `res.lines[s - 1]`
        退化成 Python 负索引，静默指到文件尾部。"""
        return tuple((s, e) for s, e in self.ranges if s < 1)


@dataclass
class Resolved:
    status: str  # ok | ambiguous | missing | non-repo
    path: Path | None = None
    candidates: tuple[Path, ...] = ()
    lines: tuple[str, ...] = ()

    @property
    def total_lines(self) -> int:
        return len(self.lines)


@dataclass
class DriftPair:
    doc_line: int
    symbol: str
    ref: Ref
    def_line: int


@dataclass
class DriftScan:
    radius: int
    pairs: int = 0
    warnings: list[DriftPair] = field(default_factory=list)
    missing_lines: list[int] = field(default_factory=list)

    @property
    def warn_lines(self) -> int:
        return len({w.doc_line for w in self.warnings})


@dataclass(frozen=True)
class Unverifiable:
    """一条「引用行自身没有可校验符号」的披露记录（人工复核清单的一行）。"""

    doc_line: int
    refs_text: str
    own_symbols: int
    verdict: str


@dataclass
class Report:
    refs: list[Ref] = field(default_factory=list)
    results: dict[tuple[int, int], Resolved] = field(default_factory=dict)
    passed: int = 0
    failed: list[Ref] = field(default_factory=list)
    ambiguous: list[Ref] = field(default_factory=list)
    non_repo: list[Ref] = field(default_factory=list)
    blank_edges: list[tuple[Ref, str, str]] = field(default_factory=list)
    section: tuple[int, int] | None = None
    section_number: str = "3.4"
    scans: list[DriftScan] = field(default_factory=list)
    weak_coverage: list[tuple[int, int, int]] = field(default_factory=list)
    unverifiable: list[Unverifiable] = field(default_factory=list)

    def py_refs(self, lo: int | None = None, hi: int | None = None) -> list[Ref]:
        out = []
        for r in self.refs:
            if r.ext != PY_EXT:
                continue
            if lo is not None and not (lo <= r.doc_line <= hi):
                continue
            out.append(r)
        return out

    def non_py_refs(self) -> list[Ref]:
        return [r for r in self.refs if r.ext != PY_EXT]


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def backtick_spans(line: str) -> list[tuple[int, int]]:
    """把反引号两两配对；**未闭合的尾部跨度一直延伸到行尾**。

    真实文档里有反引号个数为奇数的行（正文里少打一个反引号），
    不这样兜底，那些行上的引用就会被整段漏掉。
    """
    pos = [m.start() for m in re.finditer(r"`", line)]
    spans = [(pos[i], pos[i + 1]) for i in range(0, len(pos) - 1, 2)]
    if len(pos) % 2:
        spans.append((pos[-1], len(line)))
    return spans


def parse_refs(doc_line: int, text: str) -> list[Ref]:
    """从一行文档里解析出全部引用（续段合并进头部那条）。"""
    spans = backtick_spans(text)
    refs: list[Ref] = []
    for m in REF_RE.finditer(text):
        start = int(m.group(3))
        end = int(m.group(4)) if m.group(4) else start
        ranges = [(start, end)]
        # 续段只在**该引用所在跨度内**继续吃数字。
        limit = next((e for (s, e) in spans if s <= m.start() < e), len(text))
        region = text[:limit]
        cursor = m.end()
        while cursor < limit:
            cm = CONT_RE.match(region, cursor)
            if cm is None:
                break
            s2 = int(cm.group(1))
            e2 = int(cm.group(2)) if cm.group(2) else s2
            ranges.append((s2, e2))
            cursor = cm.end()
        refs.append(
            Ref(doc_line, m.group(1), m.group(2), tuple(ranges), (m.start(), m.end()), text[m.start() : cursor])
        )
    return refs


def symbols_in_line(text: str) -> list[str]:
    """严格符号集：非引用跨度里的 ≥2 字符标识符，表达式跨度不算。"""
    out: list[str] = []
    for s, e in backtick_spans(text):
        content = text[s + 1 : e]
        if REF_RE.search(content):
            continue
        if any(ch in content for ch in EXPR_CHARS):
            continue
        out.extend(TOKEN_RE.findall(content))
    return out


def any_tokens_in_line(text: str) -> list[str]:
    """宽松 token 集：**任何**反引号跨度里的 ≥2 字符标识符（含引用跨度）。

    用来回答「±2 行内是不是真的一穷二白」——「缺符号」用严格集，
    「有没有兜底死角」用宽松集，两个数在探针里是分开报的。
    """
    out: list[str] = []
    for s, e in backtick_spans(text):
        out.extend(TOKEN_RE.findall(text[s + 1 : e]))
    return out


def find_def_line(lines: tuple[str, ...], name: str) -> int | None:
    """符号定义行：``def`` / ``class`` / 顶层 ``NAME =``。找不到返回 None。"""
    esc = re.escape(name)
    pats = (
        re.compile(rf"^\s*def\s+{esc}\b"),
        re.compile(rf"^\s*class\s+{esc}\b"),
        re.compile(rf"^{esc}\s*="),
    )
    for i, line in enumerate(lines, 1):
        if any(p.match(line) for p in pats):
            return i
    return None


# --------------------------------------------------------------------------- #
# 路径解析
# --------------------------------------------------------------------------- #
def resolve_ref(ref_path: str, root: Path) -> Resolved:
    """解析顺序钉死：① ``src/chanlun/<路径>`` ② 仓库根 ``<路径>``
    ③ ``src/chanlun/`` 下路径后缀唯一匹配 ④ basename 唯一匹配 ⑤ 歧义/找不到。

    ``src/chanlun/`` 排在仓库根前面是实测出来的：``optimizer/agent.py`` 在仓库根
    是个 41 行的壳，真文件在 ``src/chanlun/`` 下 781 行；顺序反了会误报越界。
    """
    if ref_path.startswith("/"):
        return Resolved("non-repo")

    src = root / "src" / "chanlun"
    for candidate in (src / ref_path, root / ref_path):
        if candidate.is_file():
            return Resolved("ok", candidate, (), _read_lines(candidate))

    if src.is_dir():
        suffix_hits = [
            p for p in sorted(src.rglob("*")) if p.is_file() and p.as_posix().endswith("/" + ref_path)
        ]
        if len(suffix_hits) == 1:
            return Resolved("ok", suffix_hits[0], (), _read_lines(suffix_hits[0]))
        base = Path(ref_path).name
        base_hits = [p for p in sorted(src.rglob(base)) if p.is_file()]
        if len(base_hits) == 1:
            return Resolved("ok", base_hits[0], (), _read_lines(base_hits[0]))
        if len(base_hits) > 1:
            return Resolved("ambiguous", None, tuple(base_hits))
    return Resolved("missing")


def _read_lines(path: Path) -> tuple[str, ...]:
    return tuple(path.read_text(encoding="utf-8").splitlines())


def find_section(lines: tuple[str, ...], number: str) -> tuple[int, int] | None:
    """按标题找小节行号区间（1-based，闭区间）；找不到返回 None。"""
    start = None
    for i, line in enumerate(lines, 1):
        m = SECTION_RE.match(line)
        if not m:
            continue
        if start is None:
            if m.group(2) == number:
                start = i
        else:
            return (start, i - 1)
    if start is not None:
        return (start, len(lines))
    return None


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #
def scan(doc_path: Path, root: Path, section_number: str = "3.4") -> Report:
    lines = _read_lines(doc_path)
    report = Report(section_number=section_number)
    report.section = find_section(lines, section_number)

    for i, line in enumerate(lines, 1):
        for ref in parse_refs(i, line):
            report.refs.append(ref)
            res = resolve_ref(ref.filename, root)
            report.results[(ref.doc_line, ref.span[0])] = res
            if ref.ext != PY_EXT:
                continue
            if res.status == "ambiguous":
                report.ambiguous.append(ref)
                continue
            if res.status == "non-repo":
                report.non_repo.append(ref)
                continue
            if res.status == "missing":
                report.failed.append(ref)
                continue
            if any(e > res.total_lines for _, e in ref.ranges):
                report.failed.append(ref)
                continue
            # 区间倒置（起 > 止）：两端都可能落在文件范围内，只查上界看不见它。
            # 必须在下面 `res.lines[s - 1]` 之前拦掉 —— `99-3` 的起点越界、终点
            # 不越界，旧实现正是在那里抛 IndexError（退出码 1 来自崩溃，不是判据）。
            if ref.inverted_ranges():
                report.failed.append(ref)
                continue
            # 行号 < 1：`res.lines[s - 1]` 会退化成负索引，静默指到文件尾部。
            if ref.below_lower_bound_ranges():
                report.failed.append(ref)
                continue
            report.passed += 1
            s, e = ref.ranges[0]
            first, last = res.lines[s - 1], res.lines[e - 1]
            if not first.strip() or not last.strip():
                report.blank_edges.append((ref, first.strip(), last.strip()))

    _scan_drift(report, lines, root)
    _scan_unverifiable(report, lines)
    return report


def _scan_drift(report: Report, lines: tuple[str, ...], root: Path) -> None:
    if report.section is None:
        return
    lo, hi = report.section
    section_refs = report.py_refs(lo, hi)
    ref_lines = sorted({r.doc_line for r in section_refs})
    by_line: dict[int, list[Ref]] = {}
    for r in section_refs:
        by_line.setdefault(r.doc_line, []).append(r)

    for radius in (0, 1, 2):
        scan_result = DriftScan(radius=radius)
        for doc_line in ref_lines:
            window = range(max(1, doc_line - radius), min(len(lines), doc_line + radius) + 1)
            # 窗口内的符号**按名字去重**（同一个符号在两行里各出现一次只算一对）。
            symbols: list[str] = []
            seen: set[str] = set()
            for j in window:
                for sym in symbols_in_line(lines[j - 1]):
                    if sym not in seen:
                        seen.add(sym)
                        symbols.append(sym)
            strict_any = bool(symbols)
            for ref in by_line[doc_line]:
                res = report.results.get((ref.doc_line, ref.span[0]))
                if res is None or res.status != "ok":
                    continue
                for sym in symbols:
                    def_line = find_def_line(res.lines, sym)
                    if def_line is None:
                        continue
                    scan_result.pairs += 1
                    if not any(s <= def_line <= e for s, e in ref.ranges):
                        scan_result.warnings.append(DriftPair(doc_line, sym, ref, def_line))
            if not strict_any:
                scan_result.missing_lines.append(doc_line)
        scan_result.warnings.sort(key=lambda w: (w.doc_line, w.symbol))
        report.scans.append(scan_result)

    _scan_weak_coverage(report, lines)


def _scan_weak_coverage(report: Report, lines: tuple[str, ...]) -> None:
    """覆盖度偏弱点：同一文档行里「引用条数 > ±1 窗口内的符号个数」。

    尺子与漂移扫描一致（严格符号集、±1 窗口）。「符号个数」只数该行自身的
    反引号内容会把每一行光秃秃的表格行都算进来（200+ 行，清单就没用了），
    所以这里数窗口 —— 它回答的是「这一行的行号有没有别的东西可以互相印证」。
    """
    by_line: dict[int, int] = {}
    for r in report.refs:
        if r.ext == PY_EXT:
            by_line[r.doc_line] = by_line.get(r.doc_line, 0) + 1
    weak: list[tuple[int, int, int]] = []
    for doc_line, n_refs in sorted(by_line.items()):
        window = range(max(1, doc_line - 1), min(len(lines), doc_line + 1) + 1)
        n_sym = len({sym for j in window for sym in symbols_in_line(lines[j - 1])})
        if n_refs > n_sym:
            weak.append((doc_line, n_refs, n_sym))
    report.weak_coverage = weak


def _scan_unverifiable(report: Report, lines: tuple[str, ...]) -> None:
    """引用行无可校验符号：**该文档行自身**的反引号里没有任何可校验符号。

    尺子与漂移扫描、覆盖度偏弱点一致（严格符号集），只把窗口从「±1 行」
    收到「本行」。本行没有符号，就没有任何东西能交叉印证它的行号 ——
    符号漂移扫描对它是瞎的（它只按窗口里的符号查定义行，一个符号都没有时
    连一对都凑不出）。邻行有符号时判定列记「可旁证」，但那只说明**行号有
    参照物**，不代表引用正确，仍要人打开代码看一眼。

    本桶只披露、不接入退出码（退出码归属见 ``print_report``）。
    """
    by_line: dict[int, list[Ref]] = {}
    for r in report.refs:
        if r.ext == PY_EXT:
            by_line.setdefault(r.doc_line, []).append(r)
    out: list[Unverifiable] = []
    for doc_line in sorted(by_line):
        own = symbols_in_line(lines[doc_line - 1])
        if own:
            continue
        window = range(max(1, doc_line - 1), min(len(lines), doc_line + 1) + 1)
        near = sorted({sym for j in window for sym in symbols_in_line(lines[j - 1])})
        verdict = "±1 窗口亦无符号" if not near else f"±1 窗口有符号可旁证：{', '.join(near)}"
        refs_text = "；".join(f"{r.filename}:{r.ranges_text()}" for r in by_line[doc_line])
        out.append(Unverifiable(doc_line, refs_text, len(own), verdict))
    report.unverifiable = out


def loose_no_symbol_at_2(report: Report, lines: tuple[str, ...]) -> int:
    """§3.4 里 ±2 行内**连一个反引号 token 都没有**的引用行数（兜底死角）。"""
    if report.section is None:
        return 0
    lo, hi = report.section
    count = 0
    for doc_line in sorted({r.doc_line for r in report.py_refs(lo, hi)}):
        window = range(max(1, doc_line - 2), min(len(lines), doc_line + 2) + 1)
        if not any(any_tokens_in_line(lines[j - 1]) for j in window):
            count += 1
    return count


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def failure_kind(ref: Ref, res: Resolved) -> str:
    """把 `status == "ok"` 的失败再分类，供打印用。

    口径只有一份（就写在 `scan()` 的判定顺序里），打印处不许重推 —— 否则
    「判定进了 failed、消息却说越界」这类分叉会再长出来。**判定顺序与 `scan()`
    逐字一致**：先上界越界、再区间倒置、最后下界越界；并列时以 `scan()` 先判的
    那条为准（退出码由它决定），所以这里也把越界放在倒置前面。
    返回值：`inverted` | `out-of-range` | `ok`。
    """
    if any(e > res.total_lines for _, e in ref.ranges):
        return "out-of-range"
    if ref.inverted_ranges():
        return "inverted"
    if ref.below_lower_bound_ranges():
        return "out-of-range"
    return "ok"


def print_report(
    report: Report,
    doc_path: Path,
    root: Path,
    verbose: bool,
    strict: bool,
    loose_zero: int,
) -> int:
    py = report.py_refs()
    py_lines = len({r.doc_line for r in py})
    if report.section is not None:
        lo, hi = report.section
        sec = report.py_refs(lo, hi)
        sec_lines = len({r.doc_line for r in sec})
        sec_text = f"§{report.section_number} {len(sec)} 处 / {sec_lines} 行"
        sec_range = f"{doc_path.name}:{lo}-{hi}"
    else:
        sec_text, sec_range = "（未找到该小节）", "-"
    print(f"引用总数（.py 桶）：全文 {len(py)} 处 / {py_lines} 行；{sec_text}")
    print(f"硬判据通过 {report.passed} / {len(py)}")
    print(f"  ambiguous: {len(report.ambiguous)}")
    for r in report.ambiguous:
        res = report.results[(r.doc_line, r.span[0])]
        cands = ", ".join(_rel(c, root) for c in res.candidates)
        print(f"    ({r.doc_line}, '{r.filename}', {list(r.ranges)})  候选: {cands}")
    print(f"  non-repo: {len(report.non_repo)}")
    for r in report.non_repo:
        print(f"    ({r.doc_line}, '{r.filename}', {list(r.ranges)})")
    print(f"  missing: {sum(1 for r in report.failed if report.results[(r.doc_line, r.span[0])].status == 'missing')}")
    for r in report.failed:
        res = report.results[(r.doc_line, r.span[0])]
        if res.status == "missing":
            print(f"    ({r.doc_line}, '{r.filename}:{r.ranges_text()}' 找不到)")
    ok_failed = [r for r in report.failed if report.results[(r.doc_line, r.span[0])].status == "ok"]
    print(f"  out-of-range: {sum(1 for r in ok_failed if failure_kind(r, report.results[(r.doc_line, r.span[0])]) == 'out-of-range')}")
    print(f"  inverted: {sum(1 for r in ok_failed if failure_kind(r, report.results[(r.doc_line, r.span[0])]) == 'inverted')}")
    for r in report.failed:
        res = report.results[(r.doc_line, r.span[0])]
        if res.status != "ok":
            continue
        kind = failure_kind(r, res)
        if kind == "inverted":
            bad = ", ".join(f"{s}-{e}" for s, e in r.inverted_ranges())
            print(f"    ({r.doc_line}, '{r.filename}:{r.ranges_text()}' 区间倒置：{bad}（起 > 止，写反了）)")
        elif kind == "out-of-range":
            if r.below_lower_bound_ranges():
                bad = ", ".join(f"{s}-{e}" for s, e in r.below_lower_bound_ranges())
                print(f"    ({r.doc_line}, '{r.filename}:{r.ranges_text()}' 越界：{bad} 低于下界（行号从 1 起）)")
            else:
                print(
                    f"    ({r.doc_line}, '{r.filename}:{r.ranges_text()}' 越界：{_rel(res.path, root)} 共 {res.total_lines} 行)"
                )

    non_py = report.non_py_refs()
    print()
    print(f"=== 非 .py 引用（{len(non_py)} 处 / {len({r.doc_line for r in non_py})} 行）—— 不判硬绿，必须人工逐条核对 ===")
    for r in non_py:
        res = report.results[(r.doc_line, r.span[0])]
        where = _rel(res.path, root) if res.path else f"{r.filename}（{res.status}）"
        total = f"（{res.total_lines} 行）" if res.path else ""
        print(f"  L{r.doc_line}: {r.raw or r.filename}  → {where}{total}")
        for s, e in r.ranges:
            for n in range(s, e + 1):
                if res.path and 1 <= n <= res.total_lines:
                    print(f"         {n:>4}| {res.lines[n - 1]}")

    print()
    print(f"=== 符号漂移扫描（§{report.section_number}，{sec_range}）===")
    for scan_result in report.scans:
        print(
            f"±{scan_result.radius}: 检查 {scan_result.pairs} 对 / 告警 {scan_result.warn_lines} 行 / "
            f"{len(scan_result.warnings)} 对 / 缺符号 {len(scan_result.missing_lines)}"
        )
        for w in scan_result.warnings:
            print(
                f"    L{w.doc_line} {w.symbol} -> {w.ref.filename} def@{w.def_line} "
                f"范围{list(w.ref.ranges)}"
            )

    # 「缺符号无法校验」里还要再分一桶：±1 看不到符号、但 ±2 看得到的那些行。
    # 它们是**唯一可能被窗口宽度误判**的一批（要么引用漂了，要么窗口太紧），
    # 必须逐行打印出来给人看，不许只给个计数。
    if len(report.scans) > 2:
        near = set(report.scans[1].missing_lines) - set(report.scans[2].missing_lines)
        print(f"    ── 其中 ±2 行内有符号 J2 = {len(near)}（逐行列出，供人判「引用漂了」还是「窗口太紧」）")
        doc_lines = _read_lines(doc_path)
        for doc_line in sorted(near):
            window = range(max(1, doc_line - 2), min(len(doc_lines), doc_line + 2) + 1)
            syms = sorted({sym for j in window for sym in symbols_in_line(doc_lines[j - 1])})
            refs_here = "；".join(
                f"{r.filename}:{r.ranges_text()}"
                for r in report.refs
                if r.doc_line == doc_line and r.ext == PY_EXT
            )
            print(f"      L{doc_line} 引 {refs_here} / ±2 窗口符号: {', '.join(syms) or '（无）'}")

    print()
    print("=== 覆盖度偏弱点（同一文档行：引用条数 > ±1 窗口内符号个数）===")
    if report.weak_coverage:
        for doc_line, n_refs, n_sym in report.weak_coverage:
            print(f"  L{doc_line}: 引用 {n_refs} 条 / 窗口符号 {n_sym} 个")
    else:
        print("  无")

    print()
    print("=== 引用行无可校验符号（本行反引号里没有可校验符号 ⇒ 行号无从交叉印证）===")
    if report.unverifiable:
        print("  文档行号 | 引用文本 | 该行符号数 | 判定")
        for u in report.unverifiable:
            print(f"  L{u.doc_line} | {u.refs_text} | {u.own_symbols} | {u.verdict}")
        n_blind = sum(1 for u in report.unverifiable if u.verdict == "±1 窗口亦无符号")
        print(
            f"  共 {len(report.unverifiable)} 行：±1 窗口亦无符号 {n_blind} 行"
            f"（窗口 0 个符号 ⇒ 必然也在「覆盖度偏弱点」桶里）、"
            f"邻行有符号可旁证 {len(report.unverifiable) - n_blind} 行。"
        )
        print(
            "  判定列的「可旁证」只说邻行有符号能当参照物，**不代表引用正确** ——"
            " 本桶是人工复核清单，不接入退出码。"
        )
    else:
        print("  无（每条引用所在的行都至少有一个可校验符号）")

    if verbose:
        print()
        print("=== 逐条明细 ===")
        for r in report.refs:
            res = report.results[(r.doc_line, r.span[0])]
            mark = {"ok": "OK", "ambiguous": "歧义", "missing": "找不到", "non-repo": "非仓库"}[res.status]
            if res.status == "ok":
                kind = failure_kind(r, res)
                if kind == "inverted":
                    mark = "区间倒置"
                elif kind == "out-of-range":
                    mark = "越界"
            print(f"  L{r.doc_line} {r.filename}:{r.ranges_text()} → {mark}")

    print()
    print("=== 汇总行字段（钉死规则：不要求反引号 + 续段合并计）===")
    print(f"首末行空行 B = {len(report.blank_edges)}")
    for ref, first, last in report.blank_edges:
        print(f"    L{ref.doc_line} {ref.filename}:{ref.ranges_text()} 首='{first}' 末='{last}'")
    j1 = len(report.scans[1].missing_lines) if len(report.scans) > 1 else 0
    j2 = len(report.scans[2].missing_lines) if len(report.scans) > 2 else 0
    print(f"缺符号 J(±1) = {j1}；其中 ±2 行内有符号 J2 = {j1 - j2}")
    print(
        f"缺符号(±0) = {len(report.scans[0].missing_lines) if report.scans else 0}；"
        f"±2 完全无符号 = {loose_zero}"
    )
    print(f"§3.4 引用行数 = {len({r.doc_line for r in report.py_refs(*(report.section or (0, 0)))})}")

    warn_rows = len({w.doc_line for w in report.scans[1].warnings}) if len(report.scans) > 1 else 0
    print()
    print(
        f"共 {len(py)} 条，通过 {report.passed} 条，失败 {len(report.failed)} 条，"
        f"路径歧义 {len(report.ambiguous)} 条，符号漂移告警 {warn_rows} 条，"
        f"缺符号无法校验 {j1} 条（其中 ±2 行内有符号的 J2 {j1 - j2} 条），"
        f"非仓库路径 {len(report.non_repo)} 条，首末行空行 {len(report.blank_edges)} 条"
    )
    # 退出码归属：这一行**只陈述既有判定**（判定逻辑就在下面三行，不许改），
    # 目的是让读输出的人知道「哪些桶能改退出码、哪些桶永远不能」——
    # 尤其是「缺符号无法校验」（含 J2）**没有**退出码通路，它只是披露。
    print(
        "  ── 退出码归属：失败（文件找不到 / 行号越界 / 区间倒置）⇒ 恒 exit 1；"
        f"符号漂移告警 {warn_rows} 行 ⇒ 仅 --strict-symbols 时 exit 1。"
    )
    print(
        "     其余各桶**不接入退出码**（只披露、不因它失败）："
        f"路径歧义 {len(report.ambiguous)}、非仓库路径 {len(report.non_repo)}、"
        f"首末行空行 {len(report.blank_edges)}、"
        f"缺符号无法校验 {j1}（含 J2 {j1 - j2}）、覆盖度偏弱点 {len(report.weak_coverage)}、"
        f"引用行无可校验符号 {len(report.unverifiable)}、"
        f"非 .py 引用 {len(report.non_py_refs())}。"
    )
    if report.failed:
        return 1
    if strict and len(report.scans) > 1 and report.scans[1].warnings:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ARCHITECTURE.md 行号引用校验器",
        epilog=(
            "退出码归属：失败（文件找不到 / 行号越界 / 区间倒置）与"
            "引用行内容指纹不符恒为 1；"
            "符号漂移告警只在 --strict-symbols 下为 1。"
            "路径歧义、非仓库路径、首末行空行、缺符号无法校验（含 J2）、"
            "覆盖度偏弱点、引用行无可校验符号、非 .py 引用桶都只披露，不影响退出码。"
        ),
    )
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC, help="被校验的文档（默认 ARCHITECTURE.md）")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="仓库根（默认本文件上溯两级）")
    parser.add_argument("--section", default="3.4", help="符号漂移扫描的小节号（默认 3.4）")
    parser.add_argument("--verbose", action="store_true", help="打印每条引用明细")
    parser.add_argument(
        "--strict-symbols",
        action="store_true",
        help="把符号漂移告警升级为失败（只影响符号漂移这一桶；缺符号无法校验不在其内）",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=f"引用行指纹基线（默认 <root>/{DEFAULT_BASELINE_REL}）",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="把当前引用行指纹写进基线文件（改了被引代码行后必须跑一次）",
    )
    parser.add_argument(
        "--no-fingerprints",
        action="store_true",
        help="跳过引用行内容指纹校验（只跑原有四类判据）",
    )
    args = parser.parse_args(argv)

    lines = _read_lines(args.doc)
    report = scan(args.doc, args.root, args.section)
    loose_zero = loose_no_symbol_at_2(report, lines)
    code = print_report(report, args.doc, args.root, args.verbose, args.strict_symbols, loose_zero)

    if args.no_fingerprints:
        return code
    baseline_path = args.baseline or default_baseline(args.root)
    current, index = collect_fingerprints(report, args.root)
    if args.update_baseline:
        save_baseline(baseline_path, current)
        print()
        print(
            f"=== 引用行内容指纹基线已写入 {_rel(baseline_path, args.root)}"
            f"（{len(current)} 条）==="
        )
        return code
    return code | fingerprint_section(
        load_baseline(baseline_path), current, index, baseline_path, args.root
    )


# --------------------------------------------------------------------------- #
# 引用行内容指纹：整文件行号平移检测
# --------------------------------------------------------------------------- #
# 为什么需要它：本文件原有的四类判据（文件存在 / 不越界 / 区间不倒置 / 符号漂移）
# **看不见整文件行号平移**。实测（2026-10-04）：在 `src/chanlun/chan/segment.py`
# 头部插入 15 行，本工具输出**逐字节不变**、退出码仍是 0。原因是它们只问
# 「行号在不在范围内」「符号的定义行在不在范围内」—— 整体平移后这两个问题
# 的答案都还是「在」。要让平移可见，必须记住**被引行当时的内容**。
#
# 口径（钉死，改口径等于换尺子，历史数字全部作废）：
#   * 覆盖面：只收 `.py` 桶里 `status == "ok"` 且通过硬判据的引用（与 `passed` 同批）。
#   * 指纹 = 被引**全部行**（各 range 按顺序，含续段）逐行 UTF-8 + "\n" 喂 sha256，
#     取前 16 位十六进制。行内容**不做任何规范化** —— 缩进变化也是变化。
#   * 键 = `<文档里写的文件名>:<ranges_text>#<该键在文档里出现的序号>`。
#     **键里不含文档行号**：在文档里插一段话会让下面所有引用的文档行号变化，
#     但被引代码行没变 —— 那种情况必须是绿，否则每次改文档都假警报。
#   * 「指纹不符」是**硬判据**（影响退出码）。「未入基线」「基线残留」只披露：
#     前者是新增引用、后者是引用被删或范围被改，都不是「行号指到了别的内容上」。
FINGERPRINT_BASELINE_VERSION = 1
DEFAULT_BASELINE_REL = "optimizer/tools/doc_refs_baseline.json"


def _fingerprint(cited: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    for line in cited:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:16]


def cited_lines(ref: Ref, res: Resolved) -> tuple[str, ...]:
    """被引全部行的内容（含续段，按 range 顺序）。越界行取空串兜底。"""
    out: list[str] = []
    for s, e in ref.ranges:
        for n in range(s, e + 1):
            out.append(res.lines[n - 1] if 1 <= n <= res.total_lines else "")
    return tuple(out)


def collect_fingerprints(
    report: Report, root: Path
) -> tuple[dict[str, str], dict[str, tuple[int, Ref]]]:
    """返回 (键 → 指纹, 键 → (文档行号, 引用))。遍历顺序 = 文档顺序，故序号稳定。"""
    refs: dict[str, str] = {}
    index: dict[str, tuple[int, Ref]] = {}
    seen: dict[tuple[str, str], int] = {}
    for ref in report.py_refs():
        res = report.results[(ref.doc_line, ref.span[0])]
        if res.status != "ok" or failure_kind(ref, res) != "ok":
            continue
        pair = (ref.filename, ref.ranges_text())
        occ = seen.get(pair, 0)
        seen[pair] = occ + 1
        key = f"{pair[0]}:{pair[1]}#{occ}"
        refs[key] = _fingerprint(cited_lines(ref, res))
        index[key] = (ref.doc_line, ref)
    return refs, index


def default_baseline(root: Path) -> Path:
    return root / DEFAULT_BASELINE_REL


def load_baseline(path: Path) -> dict[str, str] | None:
    """读基线；不存在 / 坏 JSON / 版本不符都返回 None（调用方按「没在跑」披露）。"""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") != FINGERPRINT_BASELINE_VERSION:
        return None
    refs = data.get("refs")
    if not isinstance(refs, dict):
        return None
    return {str(k): str(v) for k, v in refs.items()}


def save_baseline(path: Path, refs: dict[str, str]) -> None:
    """写基线。键排序 + 固定缩进 ⇒ 同一份代码重跑得到逐字节相同的文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"version": FINGERPRINT_BASELINE_VERSION, "refs": dict(sorted(refs.items()))}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fingerprint_section(
    baseline: dict[str, str] | None,
    current: dict[str, str],
    index: dict[str, tuple[int, Ref]],
    baseline_path: Path,
    root: Path,
) -> int:
    """打印指纹小节并返回本桶的退出码增量（不符 ⇒ 1，其余 ⇒ 0）。"""
    where = _rel(baseline_path, root)
    print()
    print("=== 引用行内容指纹（整文件行号平移检测）===")
    if baseline is None:
        print(f"  基线不存在或不可读：{where}")
        print("  **这一桶没有在跑** —— 跑 --update-baseline 生成并提交它，否则整文件行号平移仍不可见。")
        return 0
    changed = sorted(k for k in set(baseline) & set(current) if baseline[k] != current[k])
    added = sorted(set(current) - set(baseline))
    gone = sorted(set(baseline) - set(current))
    print(f"  基线 {where}（version {FINGERPRINT_BASELINE_VERSION}，{len(baseline)} 条）；本次 {len(current)} 条")
    print(f"  不符 {len(changed)} 条 / 未入基线 {len(added)} 条 / 基线残留 {len(gone)} 条")
    for key in changed:
        doc_line, _ref = index.get(key, (0, None))
        print(
            f"    L{doc_line} {key} 期望 {baseline[key]} 实际 {current[key]}"
            f"（被引行内容已不是基线记的那几行 ⇒ 行号漂了，或代码被改过）"
        )
    for key in added:
        doc_line, _ref = index.get(key, (0, None))
        print(f"    L{doc_line} {key} 未入基线（新增引用，跑 --update-baseline 收编）")
    for key in gone:
        print(f"    {key} 基线残留（文档里已经没有这条引用了）")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
