"""把「改过的文件」变成一份带原文引用的提案补丁。

补丁不是手写的：手写的补丁头很容易把原文摘录抄错一个标点，而
``validate_patch`` 会逐字比对。这个工具从 ``optimizer/theory/`` 里直接取
原文，所以补丁头里的 ``quote:`` 与被引用的证据天然一致。

用法::

    python optimizer/tools/make_patch.py \\
        --root . \\
        --rel src/chanlun/chan/signal.py \\
        --edited /tmp/c1/signal.py \\
        --out optimizer/patches/round-002-G1.patch \\
        --kind theory \\
        --theory L20-THIRD-POINT-POSITION \\
        --evidence "PYTHONPATH=src pytest tests/chan -q ..." \\
        --finding "..."

一个补丁可以同时改多个文件（工程类常见：``quality.py`` 记数据 + ``__main__.py``
显示数据），用可重复的 ``--edit 相对路径=改好的文件`` 给出::

    --edit src/chanlun/data/quality.py=/tmp/g5/quality.py \\
    --edit src/chanlun/__main__.py=/tmp/g5/main.py

补丁**永远以当前主干为基准生成**（基准是 ``--root`` 下的原文件），
所以每个补丁都能单独 apply 到干净主干上；互为替代方案的补丁不会互相依赖。
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun.optimizer.theory import load_theory  # noqa: E402


def build_header(fields: list[tuple[str, str]]) -> str:
    lines: list[str] = []
    for key, value in fields:
        parts = str(value).splitlines() or [""]
        lines.append(f"# {key}: {parts[0]}")
        for part in parts[1:]:
            lines.append(f"#    {part}")
    return "\n".join(lines) + "\n"


def unified(rel: str, before: str, after: str) -> str:
    diff = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            n=4,
        )
    )
    if not diff:
        raise SystemExit(f"{rel}: 编辑后的内容与原文件完全相同，没有可提案的改动")
    return f"diff --git a/{rel} b/{rel}\n" + "".join(diff)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成带理论引用的提案补丁")
    parser.add_argument("--root", default=".", help="chanlun/ 项目根")
    parser.add_argument("--rel", default="", help="主干内相对路径（单文件补丁用）")
    parser.add_argument("--edited", default="", help="改好之后的文件（单文件补丁用）")
    parser.add_argument(
        "--edit",
        action="append",
        default=[],
        metavar="相对路径=改好的文件",
        help="多文件补丁：可重复给出",
    )
    parser.add_argument("--out", required=True, help="写出的 .patch")
    parser.add_argument("--kind", required=True, choices=("theory", "engineering"))
    parser.add_argument("--theory", default="", help="引用的理论条目 id，逗号分隔")
    parser.add_argument("--evidence", required=True, help="实测证据：命令 + 原始输出")
    parser.add_argument("--finding", default="", help="这一轮发现了什么")
    parser.add_argument("--note", default="", help="补充说明")
    parser.add_argument("--quote-from", default="", help="从哪个条目取原文摘录（默认取 --theory 第一个）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()

    edits: list[tuple[str, Path]] = []
    if args.rel or args.edited:
        if not (args.rel and args.edited):
            print("--rel 与 --edited 必须同时给出", file=sys.stderr)
            return 1
        edits.append((args.rel, Path(args.edited)))
    for spec in args.edit:
        rel_part, sep, edited_part = spec.partition("=")
        if not sep or not rel_part or not edited_part:
            print(f"--edit 需要写成 相对路径=文件，收到：{spec}", file=sys.stderr)
            return 1
        edits.append((rel_part, Path(edited_part)))
    if not edits:
        print("至少给一个 --rel/--edited 或 --edit", file=sys.stderr)
        return 1

    bodies: list[str] = []
    rels: list[str] = []
    for rel, edited_path in edits:
        original_path = root / rel
        if not original_path.is_file():
            print(f"主干里没有这个文件：{original_path}", file=sys.stderr)
            return 1
        before = original_path.read_text(encoding="utf-8")
        after = edited_path.read_text(encoding="utf-8")
        bodies.append(unified(rel, before, after))
        rels.append(rel)

    fields: list[tuple[str, str]] = [("kind", args.kind)]
    if args.kind == "theory":
        ids = [p.strip() for p in args.theory.split(",") if p.strip()]
        if not ids:
            print("理论类补丁必须给 --theory", file=sys.stderr)
            return 1
        theory = load_theory(root / "optimizer" / "theory")
        missing = [i for i in ids if i not in theory]
        if missing:
            print(f"理论库里没有这些条目：{missing}", file=sys.stderr)
            return 1
        quote_from = args.quote_from or ids[0]
        quote = theory[quote_from].quote.splitlines()[0]
        fields.append(("theory", ",".join(ids)))
        fields.append(("quote", quote))
        fields.append(("source", theory[quote_from].source))
    if args.finding:
        fields.append(("finding", args.finding))
    fields.append(("evidence", args.evidence))
    if args.note:
        fields.append(("note", args.note))

    patch = build_header(fields) + "".join(bodies)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(patch, encoding="utf-8")
    print(f"写出 {out}（{len(patch.splitlines())} 行，触及 {len(rels)} 个文件：{', '.join(rels)}）")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
