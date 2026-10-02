#!/usr/bin/env python3
"""控制者交叉校验：ARCHITECTURE.md 行号引用扫描。

**★ 本文件不是 Task 10a 的交付物。** Task 10a 要新建的是
`chanlun/optimizer/tools/check_doc_line_refs.py`（带 CLI、退出码、建议桶）。
本文件是**控制者自己的最小交叉校验实现**，用来独立复算简报里引用的数字，
并给复审者一个「第二实现」对照。**Task 10a 不许改本文件。**
两个实现必须给出同一组数字；对不上就是其中一边的规则理解错了。

**抽取规则（与 task-10a-brief.md §4 第 1 条逐字一致，不许换）**
  在全文**逐行**扫，取每一个 `文件名.py:NN` 或 `文件名.py:NN-MM`，
  **不要求被反引号包住**（裸写的也算）：
      头部  ([A-Za-z0-9_./\\-]+)\\.py:\\s*(\\d+)(?:\\s*[-–]\\s*(\\d+))?
      续段  (?:^|[,\\s])(\\d+)(?:\\s*[-–]\\s*(\\d+))?(?=\\s*(?:[,)]|$))
  续段挂在头部匹配的**尾部**，沿用同一个文件名（文档大量用
  `` `pivot.py:31-33, 165-167` `` 这种简写；漏掉续段会把「符号其实在范围里」误报成漂移）。

**符号漂移扫描范围 = §3.4（ARCHITECTURE.md:377-1007）**，窗口 ±N 行。
窗口内取**反引号跨度**里的标识符 token（`` `Snapshot.clipped_to` `` → `Snapshot` 与
`clipped_to` **两个都算**，任一定义行落在范围内即通过）。
符号的**定义行**必须落在「**该文档行**所引的、**同一个文件**的所有行号范围之并集」内。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 路径由本文件位置推导（本文件在 chanlun/optimizer/tools/ 下），便于在 worktree 里跑。
TOOLS = Path(__file__).resolve().parent
PROJ = TOOLS.parent.parent          # chanlun/
ROOT = PROJ.parent                  # 仓库根
SRC = PROJ / "src" / "chanlun"
DOC = PROJ / "ARCHITECTURE.md"

BT = re.compile(r"`([^`]+)`")
HEAD = re.compile(r"([A-Za-z0-9_./\-]+)\.py:\s*(\d+)(?:\s*[-–]\s*(\d+))?")
CONT = re.compile(r"(?:^|[,\s])(\d+)(?:\s*[-–]\s*(\d+))?(?=\s*(?:[,)]|$))")
TOK = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

SEC34 = (377, 1007)          # §3.4 的文档行范围（控制者从标题行读出）
OUTSIDE = ("/tmp/",)         # 非仓库路径例外


def resolve(rp: str):
    """① src/chanlun/<rp> ② 项目根 <rp> ③ 后缀唯一 ④ basename 唯一 ⑤ 歧义"""
    if rp.startswith(OUTSIDE):
        return None, "non-repo"
    for base, tag in ((SRC, "src"), (PROJ, "proj")):
        p = base / rp
        if p.is_file():
            return p, tag
    name = Path(rp).name
    if not name.endswith(".py"):
        name += ".py"
    cands = sorted(SRC.rglob(name))
    if len(cands) == 1:
        return cands[0], "base-uniq"
    if len(cands) > 1:
        return None, f"ambiguous({len(cands)})"
    return None, "missing"


_DEF_CACHE: dict[Path, dict[str, int]] = {}


def def_lines(path: Path) -> dict[str, int]:
    if path in _DEF_CACHE:
        return _DEF_CACHE[path]
    out: dict[str, int] = {}
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for pat in (r"\s*def\s+([A-Za-z_]\w*)", r"\s*class\s+([A-Za-z_]\w*)",
                    r"\s*([A-Z][A-Z0-9_]*)\s*="):
            m = re.match(pat, line)
            if m:
                out.setdefault(m.group(1), i)
                break
    _DEF_CACHE[path] = out
    return out


def extract(lines: list[str]):
    """返回 [(doc_line, filename, [(a,b),...]), ...]，一行可有多条。

    ★ 续段必须在**反引号跨度内部**取：跨度边界就是 `$`，否则
    `` `segment.py:373-381, 447-466` `` 的续段会因为在行内后面跟着反引号而漏掉
    （控制者实测踩过：漏掉后 `_monotone_stamps`@449 被误报成漂移）。
    """
    refs = []
    for ln, text in enumerate(lines, 1):
        spans = [(m.start(1), m.end(1), m.group(1)) for m in BT.finditer(text)]
        for h in HEAD.finditer(text):
            fname = h.group(1) + ".py"
            ranges = [(int(h.group(2)), int(h.group(3) or h.group(2)))]
            tail = text[h.end():]
            for s0, s1, body in spans:          # 头部落在哪个跨度里，就只取到跨度末尾
                if s0 <= h.start() and h.end() <= s1:
                    tail = text[h.end():s1]
                    break
            for c in CONT.finditer(tail):
                ranges.append((int(c.group(1)), int(c.group(2) or c.group(1))))
            refs.append((ln, fname, ranges))
    return refs


def main() -> int:
    lines = DOC.read_text(encoding="utf-8").splitlines()
    refs = extract(lines)
    n34 = [r for r in refs if SEC34[0] <= r[0] <= SEC34[1]]
    print(f"引用总数：全文 {len(refs)} 处 / {len({r[0] for r in refs})} 行；"
          f"§3.4 {len(n34)} 处 / {len({r[0] for r in n34})} 行")

    # ---------------- 硬判据 ----------------
    buckets: dict[str, list] = {}
    ok = 0
    for ln, fname, ranges in refs:
        path, how = resolve(fname)
        if path is None:
            buckets.setdefault(how, []).append((ln, fname, ranges))
            continue
        total = len(path.read_text(encoding="utf-8").splitlines())
        bad = [r for r in ranges if r[1] > total or r[0] < 1]
        if bad:
            buckets.setdefault("out-of-range", []).append((ln, fname, bad, total))
            continue
        ok += 1
    print(f"硬判据通过 {ok} / {len(refs)}")
    for k, v in sorted(buckets.items()):
        print(f"  {k}: {len(v)}")
        for item in v:
            print(f"    {item}")

    # ---------------- 符号漂移（仅 §3.4） ----------------
    print("\n=== 符号漂移扫描（§3.4，ARCHITECTURE.md:%d-%d）===" % SEC34)
    for win in (0, 1, 2):
        # 每行 → {file: [(a,b)...]} 并集
        per_line: dict[int, dict[str, list]] = {}
        for ln, fname, ranges in n34:
            d = per_line.setdefault(ln, {})
            d.setdefault(fname, []).extend(ranges)

        flagged, nosym, pairs = [], 0, 0
        for ln in sorted(per_line):
            lo, hi = max(1, ln - win), min(len(lines), ln + win)
            syms: set[str] = set()
            for i in range(lo, hi + 1):
                for span in BT.finditer(lines[i - 1]):
                    s = span.group(1)
                    if ".py:" in s or re.search(r"[\s=()]", s):
                        continue
                    syms.update(t for t in TOK.findall(s) if len(t) > 1)
            if not syms:
                nosym += 1
                continue
            # 只在该行引用的文件里查这些符号
            for fname, union in per_line[ln].items():
                path, how = resolve(fname)
                if path is None:
                    continue
                defs = def_lines(path)
                for sym in sorted(syms):
                    d = defs.get(sym)
                    if d is None:
                        continue          # 不在被引文件里 ⇒ 无法归因，跳过
                    pairs += 1
                    if not any(a <= d <= b for a, b in union):
                        flagged.append((ln, sym, fname, d, sorted(union)))
        byline: dict[int, list] = {}
        for f in flagged:
            byline.setdefault(f[0], []).append(f)
        print(f"±{win}: 检查 {pairs} 对 / 告警 {len(byline)} 行 / {len(flagged)} 对 / 缺符号 {nosym}")
        for ln in sorted(byline):
            for f in byline[ln]:
                print(f"    L{ln} {f[1]} -> {f[2]} def@{f[3]} 范围{f[4]}")
    # ---------------- 汇总行其余字段 ----------------
    print("\n=== 汇总行字段（钉死规则：不要求反引号 + 续段合并计）===")
    blank = []
    for ln, fname, ranges in refs:
        path, how = resolve(fname)
        if path is None:
            continue
        src = path.read_text(encoding="utf-8").splitlines()
        if not src:
            continue
        if ranges[0][0] > len(src) or ranges[0][1] > len(src):
            continue
        first, last = src[ranges[0][0] - 1].strip(), src[min(ranges[0][1], len(src)) - 1].strip()
        if not first or not last:
            blank.append((ln, fname, ranges[0], repr(first), repr(last)))
    print(f"首末行空行 B = {len(blank)}")
    for b in blank[:30]:
        print(f"    L{b[0]} {b[1]}:{b[2][0]}-{b[2][1]} 首={b[3]} 末={b[4]}")

    # 缺符号 J（±1）与 J2（±2 行内有符号）
    per_line: dict[int, dict[str, list]] = {}
    for ln, fname, ranges in n34:
        per_line.setdefault(ln, {}).setdefault(fname, []).extend(ranges)

    def syms_at(ln: int, win: int) -> set[str]:
        out: set[str] = set()
        for i in range(max(1, ln - win), min(len(lines), ln + win) + 1):
            for span in BT.finditer(lines[i - 1]):
                s = span.group(1)
                if ".py:" in s or re.search(r"[\s=()]", s):
                    continue
                out.update(t for t in TOK.findall(s) if len(t) > 1)
        return out

    j1 = [ln for ln in per_line if not syms_at(ln, 1)]
    j2 = [ln for ln in j1 if syms_at(ln, 2)]
    print(f"缺符号 J(±1) = {len(j1)}；其中 ±2 行内有符号 J2 = {len(j2)}")
    j0 = [ln for ln in per_line if not syms_at(ln, 0)]
    print(f"缺符号(±0) = {len(j0)}；±2 完全无符号 = {len([ln for ln in j2 if not syms_at(ln, 2)])}")
    print(f"§3.4 引用行数 = {len(per_line)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
