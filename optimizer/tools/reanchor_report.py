"""诊断：把 `ARCHITECTURE.md` 里因代码改动而漂移的行号引用**按内容**定位到新位置。

`check_doc_line_refs.py` 能告诉你「这一条引用的内容对不上基线了」，但不会告诉你
那段代码搬到哪去了 —— 重锚只能靠人。这个脚本补那一步：对每条漂移引用，把
**旧行号在 HEAD 上的内容**取出来，再在当前工作区的同一个文件里按**逐行相等**
搜索它的新位置。

**只读**，不改任何东西。输出的新行号要人工确认后再写回 ARCHITECTURE.md
（不要拿 +N 硬加：中间可能还夹着内容改动）。

跑法：
    cd <项目根>
    ../.venv-chanlun/bin/python optimizer/tools/reanchor_report.py

注意：本脚本自己**不是**判据，不接退出码。判据是 `check_doc_line_refs.py`。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent                       # 外层工作区才是 git 仓库根
TOOL = ROOT / "optimizer" / "tools" / "check_doc_line_refs.py"
#: 校验器的指纹漂移行格式：`L<文档行> <引用>#<序号> 期望 <哈希> 实际 <哈希>`
REF_RE = re.compile(r"L(\d+) (.+?)#\d+ 期望 \S+ 实际 \S+")


def resolve(rel: str) -> Path | None:
    """把引用里的路径写法归一到工作区真实路径。

    引用里出现过三种写法：`chan/foo.py`、`src/chanlun/chan/foo.py`、以及**裸文件名**
    （`sources.py` 在 `data/`、`scanner.py` 在 `scan/`、`runner.py` 在 `backtest/`…）。

    ★ 裸文件名**必须递归找**，不能只列几个已知子目录。踩过：初版只找了
    `chan/` / `data/` / 根目录，于是 `scanner.py:313-315` 这类引用**解析不到 →
    被静默跳过**，工具报「0 行要改」，看起来像「没有漂移」——而实际上那两条引用
    已经指错了行。**工具漏掉一件事时必须说不知道，不能报平安。**
    """
    if rel.startswith("src/"):
        p = ROOT / rel
        return p if p.is_file() else None
    if "/" in rel and not rel.startswith("chan/"):
        p = ROOT / rel          # 文档里有 `scan/scanner.py`、`web/static/index.html` 这类写法
        if p.is_file():
            return p
    # 文档**自查引用**（`ARCHITECTURE.md:NNNN`）也要能重锚：本文档自己加了几行，
    # 后面所有 `ARCHITECTURE.md:NNNN` 就全指错了 —— 这类漂移最容易漏，
    # 因为它不涉及任何代码改动。
    if "/" not in rel and (ROOT / rel).is_file():
        return ROOT / rel
    if "/" in rel:
        p = ROOT / "src" / "chanlun" / rel
        return p if p.is_file() else None
    hits = sorted((ROOT / "src" / "chanlun").rglob(rel))
    files = [h for h in hits if h.is_file()]
    return files[0] if len(files) == 1 else None


def head_lines(rel: str) -> list[str]:
    out = subprocess.run(
        ["git", "show", f"HEAD:chanlun/{rel}"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    return out.splitlines()


def find_in_new(rel: str, snippet: list[str], new_lines: list[str]) -> list[int]:
    """在**新**文件里找这段内容（允许行号整体平移）。返回候选起始行（1 起）。"""
    hits: list[int] = []
    for i in range(len(new_lines) - len(snippet) + 1):
        if new_lines[i : i + len(snippet)] == snippet:
            hits.append(i + 1)
    return hits


def solve(rel: str, a: int, b: int, old: list[str], new: list[str]) -> tuple[int, int] | None:
    """把旧区间 `a-b` 解到新位置；**解不出或解出不唯一就返回 None**。

    以整段逐行相等为主；失配时退化成「用区间内第一个非空行当锚」，但那种情况下
    锚可能出现多次（同一行在文件里出现两遍），此时**必须判为歧义**而不是随手取第一个 ——
    这个工具写错行号的成本，比它省下的时间高得多。
    """
    snippet = old[a - 1 : b]
    if not snippet:
        return None
    hits = find_in_new(rel, snippet, new)
    if len(hits) == 1:
        return hits[0], hits[0] + (b - a)
    off = next((i for i, ln in enumerate(snippet) if ln.strip()), None)
    if off is None:
        return None
    anchors = find_in_new(rel, [snippet[off]], new)
    starts = sorted({h - off for h in anchors})
    if len(starts) == 1 and starts[0] >= 1:
        return starts[0], starts[0] + (b - a)
    return None


def main() -> int:
    apply = "--apply" in sys.argv
    res = subprocess.run(
        [sys.executable, str(TOOL)], cwd=ROOT, capture_output=True, text=True
    )
    text = res.stdout + res.stderr
    drift = [(int(m.group(1)), m.group(2)) for m in REF_RE.finditer(text)]
    if not drift:
        print("没有漂移引用")
        return 0

    cache_old: dict[str, list[str]] = {}
    cache_new: dict[str, list[str]] = {}
    #: 文档行号 → [(旧区间字符串, 新区间字符串)]，全部解出来之后一次性写回。
    planned: dict[int, list[tuple[str, str]]] = {}
    unresolved: list[str] = []

    for doc_line, ref in drift:
        if ":" not in ref:
            continue
        path, ranges = ref.split(":", 1)
        if not path.endswith((".py", ".md", ".js", ".html", ".css")):
            continue
        target = resolve(path)
        if target is None:
            # **不要静默跳过。** 解析不到就说不知道 —— 否则「工具没报」会被读成
            # 「没有漂移」，而实际是那些引用根本没被检查（见 `resolve` 的 docstring）。
            unresolved.append(f"doc L{doc_line}  {ref}  **解析不到这个文件**")
            continue
        rel = str(target.relative_to(ROOT))
        old = cache_old.setdefault(rel, head_lines(rel))
        new = cache_new.setdefault(rel, target.read_text(encoding="utf-8").splitlines())
        for part in ranges.split(","):
            part = part.strip()
            m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
            if not m:
                continue
            a = int(m.group(1))
            b = int(m.group(2) or a)
            got = solve(rel, a, b, old, new)
            if got is None:
                why = "找不到" if not find_in_new(rel, old[a - 1 : b], new) else "歧义"
                unresolved.append(f"doc L{doc_line}  {ref}  区间 {a}-{b} → **{why}**")
                continue
            na, nb = got
            newpart = str(na) if na == nb else f"{na}-{nb}"
            if newpart != part:
                planned.setdefault(doc_line, []).append((part, newpart))

    for msg in unresolved:
        print(msg)
    if not planned:
        print("没有可自动应用的（以上若有歧义项，请人工处理）")
        return 0 if not unresolved else 1

    doc = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8").splitlines(keepends=True)
    changed = 0
    for doc_line, pairs in sorted(planned.items()):
        line = doc[doc_line - 1]
        before = line
        for oldpart, newpart in pairs:
            # 按**数字 token 边界**替换：引用可能被反引号包着（`` `pivot.py:73` ``）、
            # 也可能在多引用里跟着逗号（`pivot.py:12-13, 109-111`），
            # 所以不能要求后面一定是 `,`/`#`/行尾 —— 只要求它两侧都不是数字或连字符。
            #
            # ★ 还要试**两种旧形态**：`check_doc_line_refs` 把单行引用规范化成
            # `114-114` 再输出，而文档里写的是 `114`。只按它给的那一种去替换，
            # 会一条都改不动（而且不报错，只是"计划了但没写回"）。
            forms = [oldpart]
            m = re.fullmatch(r"(\d+)-(\d+)", oldpart)
            if m and m.group(1) == m.group(2):
                forms.append(m.group(1))
            done = False
            for form in forms:
                pat = re.compile(r"(?<![\d-])" + re.escape(form) + r"(?![\d-])")
                line, n = pat.subn(newpart, line, count=1)
                if n:
                    done = True
                    break
            if not done:
                unresolved.append(f"doc L{doc_line}  写回失败：{oldpart} 在行内找不到唯一位置")
        if line != before:
            doc[doc_line - 1] = line
            changed += 1
    if apply:
        (ROOT / "ARCHITECTURE.md").write_text("".join(doc), encoding="utf-8")
        print(f"已写回 {changed} 行")
    else:
        print(f"**试运行**：将改动 {changed} 行（加 --apply 才写回）")
        for doc_line in sorted(planned):
            print(f"  L{doc_line}: {planned[doc_line]}")
    return 0 if not unresolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
