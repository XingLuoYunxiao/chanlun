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
4. **桶的性质**：硬判据 = 文件找不到 / 范围越界（**只有它们影响退出码**）。
   路径歧义、符号漂移、首末行空行、缺符号无法校验都是**建议桶**。
   非 ``.py`` 引用**只枚举、不判硬绿**（符号名写在散文里，自动规则无从解析）。

用法::

    python optimizer/tools/check_doc_line_refs.py            # 汇总
    python optimizer/tools/check_doc_line_refs.py --verbose   # 每条引用明细
    python optimizer/tools/check_doc_line_refs.py --strict-symbols  # 漂移升级为失败
"""
from __future__ import annotations

import argparse
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
            report.passed += 1
            s, e = ref.ranges[0]
            first, last = res.lines[s - 1], res.lines[e - 1]
            if not first.strip() or not last.strip():
                report.blank_edges.append((ref, first.strip(), last.strip()))

    _scan_drift(report, lines, root)
    _scan_weak_coverage(report, lines)
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
    print(f"  out-of-range: {sum(1 for r in report.failed if report.results[(r.doc_line, r.span[0])].status == 'ok')}")
    for r in report.failed:
        res = report.results[(r.doc_line, r.span[0])]
        if res.status == "ok":
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

    if verbose:
        print()
        print("=== 逐条明细 ===")
        for r in report.refs:
            res = report.results[(r.doc_line, r.span[0])]
            mark = {"ok": "OK", "ambiguous": "歧义", "missing": "找不到", "non-repo": "非仓库"}[res.status]
            if res.status == "ok" and any(e > res.total_lines for _, e in r.ranges):
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
    if report.failed:
        return 1
    if strict and len(report.scans) > 1 and report.scans[1].warnings:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ARCHITECTURE.md 行号引用校验器")
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC, help="被校验的文档（默认 ARCHITECTURE.md）")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="仓库根（默认本文件上溯两级）")
    parser.add_argument("--section", default="3.4", help="符号漂移扫描的小节号（默认 3.4）")
    parser.add_argument("--verbose", action="store_true", help="打印每条引用明细")
    parser.add_argument("--strict-symbols", action="store_true", help="把符号漂移告警升级为失败")
    args = parser.parse_args(argv)

    lines = _read_lines(args.doc)
    report = scan(args.doc, args.root, args.section)
    loose_zero = loose_no_symbol_at_2(report, lines)
    return print_report(report, args.doc, args.root, args.verbose, args.strict_symbols, loose_zero)


if __name__ == "__main__":
    sys.exit(main())
