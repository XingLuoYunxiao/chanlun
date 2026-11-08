"""判据：`optimizer/theory/*.md` 里的缠论原文引用必须**逐字**来自 `chanlun108/原文/`。

为什么要这个
------------
AGENTS.md §3 定的是硬规矩：

> 引用原文时，必须写清课号 + 原句。**不许转述成"禅师说过中枢不能延伸太久"** ——
> 判据要靠原句的字面量。

但在此之前这条规矩**只靠自觉**。理论条目是判据变更的唯一依据（§5 第 1 步），
里面的摘录一旦被顺手"润色"成通顺的转述，判据就失去了锚 —— 而且**没有任何东西会报警**：
文档照样渲染，测试照样绿，只是那条判据从此不再有原文支撑。

这个脚本把「逐字」变成可执行的检查。**它会失败**：把任意一条摘录改一个字，
`--self-test` 之外的正常跑法立刻报错。

怎么判定「逐字」
----------------
- 理论条目里的省略号（`……` / `...`）是**作者有意删节**，所以按省略号**切段**，
  要求**每一段**都能在那一课的存档里逐字找到（而不是整句连着找）。
- 空白一律折叠：存档是按 HTML 转成的 markdown，换行位置与原文排版无关。
- `**加粗**` 是文档的强调标记，不属于原文，先剥掉。
- 课号 → 文件：`第 84 课` → `chanlun108/原文/084-*.md`。

跑法：
    cd <项目根>
    ../.venv-chanlun/bin/python optimizer/tools/check_theory_quotes.py

这是判据，**接入退出码**：0 = 全部逐字一致，1 = 有条目对不上。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "chanlun108" / "原文"
THEORY = ROOT / "optimizer" / "theory"

#: `第 84 课：` 这种归属行 —— 后面紧跟的 `>` 引用块就归它。
COURSE_LINE = re.compile(r"^第\s*(\d{1,3})\s*课")
#: markdown 引用行。
QUOTE_LINE = re.compile(r"^\s*>\s?(.*)$")
#: 有意删节的标记。
ELLIPSIS = re.compile(r"…+|\.\.\.+")
MIN_FRAGMENT = 8


def normalize(text: str) -> str:
    """折叠空白并剥掉加粗标记 —— 两者都不是原文的一部分。"""
    return re.sub(r"\s+", "", text.replace("**", ""))


def fragments(quote: str) -> list[str]:
    """按省略号切段；太短的片段（标点、单字）不参与核对，它们到处都能命中。"""
    return [f for f in (normalize(p) for p in ELLIPSIS.split(quote)) if len(f) >= MIN_FRAGMENT]


def archive_text(course: int) -> str | None:
    hits = sorted(ARCHIVE.glob(f"{course:03d}-*.md"))
    if not hits:
        return None
    return normalize(hits[0].read_text(encoding="utf-8"))


def collect() -> list[tuple[Path, int, int, str]]:
    """抽出 `(文件, 行号, 课号, 引文)`。

    **归属规则：课号只「武装」紧随其后的那个引用块。** 任何非空、非引用的行都会
    解除武装 —— 这条不是洁癖，是踩出来的：初版用「最近出现过的课号」归属，
    于是

    - 文档自己的行文（`> **原判据三（保留原样…）**：…`）被当成第 33 课的原文；
    - `第 060 课 … 。第 027 课（…）的措辞更直接：` 后面那段**确属第 027 课**的
      引用被算到第 060 课头上。

    两次都是解析器的错、文档的归属是对的。**判据写错方向比没判据更坏**，
    所以这里宁可少收（漏几块），也不误报。
    """
    out: list[tuple[Path, int, int, str]] = []
    for path in sorted(THEORY.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        armed: int | None = None      # 刚出现、还没被别的内容打断的课号
        buf: list[str] = []
        start = 0

        def flush() -> None:
            if armed is not None and buf:
                out.append((path, start, armed, " ".join(buf)))

        for i, line in enumerate(lines, 1):
            m = COURSE_LINE.match(line)
            if m:
                flush()
                armed, buf = int(m.group(1)), []
                continue
            q = QUOTE_LINE.match(line)
            if q is not None:
                if armed is not None:
                    if not buf:
                        start = i
                    buf.append(q.group(1))
                continue
            if line.strip():          # 任何别的正文都解除归属
                flush()
                armed, buf = None, []
            else:                     # 空行：块内空行留着，块已开始则收尾
                if buf:
                    flush()
                    armed, buf = None, []
        flush()
    return out


#: 节选边界的标点替换：引用到此为止时，原文那里的标点可能不是句号。
_TRAILING = "。，、；：！？,;:."


def match_quality(frag: str, text: str) -> str:
    """`exact` = 逐字命中；`boundary` = 只差末尾标点；`none` = 对不上。

    允许末尾标点不同，是因为**节选必然在某处断开**，而断开处原文的标点是什么
    由不得摘录者挑（第 35 课那句原文是「…逐级上推，然后就可以严格定义…」，
    截到「逐级上推」就只能补个句号）。这是编辑惯例，不是改动原文。
    **但只放宽末尾一个字符**：改一个实词照样报错。
    """
    if frag in text:
        return "exact"
    if len(frag) > 1 and frag[-1] in _TRAILING and frag[:-1] in text:
        return "boundary"
    return "none"


def check() -> tuple[int, list[str], list[str]]:
    cache: dict[int, str | None] = {}
    problems: list[str] = []
    notes: list[str] = []
    n = 0
    for path, line, course, quote in collect():
        frags = fragments(quote)
        if not frags:
            continue
        n += 1
        text = cache.setdefault(course, archive_text(course))
        # `THEORY` 可能被指到仓库外（测试就这么做）—— 那时用绝对路径报位置，
        # 不要 `relative_to` 抛 ValueError 把整轮检查打断。
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            rel = path
        if text is None:
            problems.append(f"{rel}:{line}  第 {course} 课**存档里没有这个文件**")
            continue
        for f in frags:
            q = match_quality(f, text)
            if q == "none":
                problems.append(
                    f"{rel}:{line}  第 {course} 课**对不上**：「{f[:60]}…」"
                )
            elif q == "boundary":
                notes.append(
                    f"{rel}:{line}  第 {course} 课 末尾标点与原句不同（节选边界）：「…{f[-18:]}」"
                )

    # 补一遍：**归属之外的引用块**也要在存档里逐字存在（不判它属于哪一课）。
    # 不这么查的话，同一课号后面用空行隔开的第二、三块就是监管盲区。
    # **不查「没有课号归属」的引用块。** 试过一版（把每个 `>` 块拿去全存档里找），
    # 结果 22 条里明显混着**真原文** —— 那些块是跨行引的，朴素拼接会把原文的行内
    # 停顿拼掉，于是「找不到」。**一个 22 条误报的检查会被忽略，比没有检查更糟。**
    # 所以规则是明确的：**想让一段引用进检查，就在它前面写上 `第 N 课：`**；
    # 没有课号归属的块一律不查（它可能是作者自己的强调，也可能是跨行引用的原文）。
    # `notes` 里只留「末尾标点」那一种。
    return n, problems, notes


def main() -> int:
    n, problems, notes = check()
    for note in notes:
        print(f"  · {note}")
    if notes:
        print()
    if not problems:
        print(f"✓ {n} 条**有课号归属**的原文引用全部逐字来自 chanlun108/原文/"
              f"（{len(notes)} 条仅末尾标点为节选边界）")
        print("  注：只查**有课号归属**的块（`第 N 课：` 之后紧跟的那一块）。"
              "想让一段引用进检查，就在它前面写上 `第 N 课：`。")
        return 0
    print(f"✗ {len(problems)}/{n} 条对不上（转述或抄错课号）：\n")
    for p in problems:
        print(f"  {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
