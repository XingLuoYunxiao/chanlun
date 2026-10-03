"""两个行号校验工具的 §3.4 窗口必须一致，且必须覆盖 §3.4 里的全部引用。

**为什么单开一个文件**：`check_doc_line_refs.py` 与 `scan_doc_line_refs.py`
是**两个独立实现**（后者是控制者当年写的交叉校验）。两个实现的符号漂移扫描
都只看 §3.4，但一个**现读**标题行、另一个曾经**写死** `SEC34 = (377, 1007)`。

写死的那一侧不是「容量不足」而是**真的在漏**：实测同一份 `ARCHITECTURE.md`，
写死窗口得 `§3.4 引用行数 = 55`，现读窗口得 **58** —— 漏掉的正是
`L1216`（D-36 `confirmed_at` 的生产链路）、`L1218`（`scan/scanner.py`）、
`L1260`（D-37 离开段口径）三行。**漏掉的表现是「更绿」**，所以没人会发现。

这里的判据是「窗口 == 标题现读区间」+「窗口内的引用行数 == 独立扫出来的行数」。
两者都能失败：任何一侧改回写死常量，或者文档长到越过旧上界，都会红。

`optimizer/tools/` 不是包（无 `__init__.py`、不在 `src/` 下），所以按路径加载。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

CHANLUN = Path(__file__).resolve().parents[2]
TOOLS = CHANLUN / "optimizer" / "tools"
DOC = CHANLUN / "ARCHITECTURE.md"
SECTION = "3.4"

# 独立的实现：只认标题行，不碰任何一个被测工具。
HEADING_RE = re.compile(r"^(#{1,6})\s*(\d+(?:\.\d+)*)")
# 独立的实现：文档里的 `文件.py:NN` / `文件.py:NN-MM`。
REF_RE = re.compile(r"[A-Za-z0-9_./\-]+\.py:\s*\d+")


def load(name: str):
    """按路径加载 `optimizer/tools/<name>`（它不是包内模块）。"""
    path = TOOLS / f"{name}.py"
    assert path.is_file(), f"工具不存在：{path}"
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def section_range(lines: list[str], number: str) -> tuple[int, int]:
    """独立算出小节的 1-based 闭区间：从标题行到下一个标题的前一行。"""
    start = None
    for i, line in enumerate(lines, 1):
        m = HEADING_RE.match(line)
        if not m:
            continue
        if start is None:
            if m.group(2) == number:
                start = i
        else:
            return (start, i - 1)
    assert start is not None, f"ARCHITECTURE.md 里找不到小节 {number!r}"
    return (start, len(lines))


@pytest.fixture(scope="module")
def lines() -> list[str]:
    return DOC.read_text(encoding="utf-8").splitlines()


@pytest.fixture(scope="module")
def expected(lines) -> tuple[int, int]:
    return section_range(lines, SECTION)


def test_scan_tool_reads_section_from_heading(expected):
    """`scan_doc_line_refs.SEC34` 必须是现读值，不是写死的常量。"""
    mod = load("scan_doc_line_refs")
    assert mod.SEC34 == expected, (
        f"scan_doc_line_refs.SEC34 = {mod.SEC34}，标题现读值是 {expected}；"
        "写死一个会漂的区间会静默漏掉引用（漏掉的表现是更绿）"
    )


def test_check_tool_reads_section_from_heading(lines, expected):
    """`check_doc_line_refs.find_section()` 必须给出同一个区间。"""
    mod = load("check_doc_line_refs")
    assert mod.find_section(tuple(lines), SECTION) == expected


def test_two_tools_agree_on_window():
    """两个独立实现的 §3.4 窗口必须逐项相同（这是「交叉校验」的全部意义）。"""
    scan = load("scan_doc_line_refs")
    check = load("check_doc_line_refs")
    lines = DOC.read_text(encoding="utf-8").splitlines()
    assert scan.SEC34 == check.find_section(tuple(lines), SECTION), (
        f"scan={scan.SEC34} vs check={check.find_section(tuple(lines), SECTION)}"
    )


def test_window_covers_every_reference_in_section(lines, expected):
    """窗口内带 `.py:NN` 引用的行数，必须等于 §3.4 里独立扫出来的行数。

    这一条是**内容**判据，不是区间判据：它直接回答「有没有引用落在窗口外」。
    """
    start, end = expected
    in_section = {i for i in range(start, end + 1) if REF_RE.search(lines[i - 1])}
    assert in_section, "§3.4 里一条 `.py` 引用都没有，判据本身失效了"

    scan = load("scan_doc_line_refs")
    covered = {i for i in in_section if scan.SEC34[0] <= i <= scan.SEC34[1]}
    missing = sorted(in_section - covered)
    assert not missing, (
        f"§3.4 里有 {len(missing)} 行引用落在扫描窗口 {scan.SEC34} 之外：{missing}；"
        "这些引用的符号漂移不会被任何东西检查"
    )


def test_find_section_raises_on_missing_heading():
    """找不到标题必须抛错，不许静默退化成空区间（空区间同样是「全绿」）。"""
    mod = load("scan_doc_line_refs")
    with pytest.raises(RuntimeError, match="找不到小节"):
        mod.find_section("99.99")
