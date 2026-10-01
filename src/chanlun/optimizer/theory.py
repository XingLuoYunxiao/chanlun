"""缠论理论证据库：原文摘录 + 补丁强制引用校验。

优化器允许改缠论算法的唯一通行证是：**引用 theory/ 里的条目 id，
并把该条目的原文摘录贴进补丁头**。这个模块负责把这条规矩变成
机器可判定的规则，而不是靠人自觉。

证据库本身用 Markdown 存（front-matter + `## 原文摘录` 小节），
不引入任何第三方解析依赖，因为 `pyproject.toml` 里没有声明 YAML。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

#: 主干算法目录。改了这里面的任何东西，都必须走「理论类」通道。
ALGO_PREFIXES: tuple[str, ...] = (
    "src/chanlun/chan/",
    "src/chanlun/scan/",
    "src/chanlun/web/",
    "src/chanlun/backtest/",
)

KIND_THEORY = "theory"
KIND_ENGINEERING = "engineering"

_FRONT_MATTER_FENCE = "---"
_DIFF_START = re.compile(r"^(diff --git |--- |\+\+\+ |@@ )")
_TOUCHED = re.compile(r"^(?:\+\+\+|---) [ab]/(.+?)(?:\t.*)?$")
_HEADER_LINE = re.compile(r"^# ?([A-Za-z_][A-Za-z0-9_]*)\s*:\s?(.*)$")
_HEADER_CONT = re.compile(r"^#\s{3,}(\S.*)$")
_QUOTE_SECTION = re.compile(r"^##\s*原文摘录\s*$")
_ANY_SECTION = re.compile(r"^##\s")
_QUOTE_MARK = re.compile(r"^\s*>\s?")


@dataclass(frozen=True)
class TheoryEntry:
    """一条缠论原文证据。``quote`` 是逐字摘录，不允许改写。"""

    id: str
    source: str
    quote: str
    applies_to: tuple[str, ...]
    lesson: str
    url: str = ""
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "lesson": self.lesson,
            "quote": self.quote,
            "applies_to": list(self.applies_to),
            "url": self.url,
            "tags": list(self.tags),
        }

    def haystack(self) -> str:
        return " ".join(
            [self.id, self.lesson, self.source, self.quote, *self.applies_to, *self.tags]
        )


@dataclass(frozen=True)
class PatchVerdict:
    """补丁审核结论。``kind`` 由**补丁碰了哪些文件**决定，不由作者自称决定。"""

    ok: bool
    kind: str
    theory_id: str | None
    reasons: tuple[str, ...]
    touched: tuple[str, ...]

    def __bool__(self) -> bool:  # 方便 `if not validate_patch(...)`
        return self.ok


# ----------------------------------------------------------------- 解析


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def load_theory(root: str | Path) -> dict[str, TheoryEntry]:
    """读取证据库目录下所有 ``*.md``，返回 ``{id: entry}``。

    格式错误一律抛 :class:`ValueError` —— 宁可起不来，也不要让一条
    没有原文的「证据」混进库里。
    """
    root = Path(root)
    entries: dict[str, TheoryEntry] = {}
    if not root.is_dir():
        return entries
    for path in sorted(root.glob("*.md")):
        entry = _parse_entry(path)
        if entry.id in entries:
            raise ValueError(f"理论库存在重复 id：{entry.id}（{path.name}）")
        entries[entry.id] = entry
    return entries


def _parse_entry(path: Path) -> TheoryEntry:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_FENCE:
        raise ValueError(f"{path.name}: 缺少 front-matter 起始 ---")
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == _FRONT_MATTER_FENCE)
    except StopIteration as exc:
        raise ValueError(f"{path.name}: front-matter 没有闭合 ---") from exc

    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"{path.name}: front-matter 行无法解析：{line!r}")
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()

    body = lines[end + 1 :]
    quote = _extract_quote(path, body)

    for required in ("id", "source", "applies_to"):
        if not meta.get(required):
            raise ValueError(f"{path.name}: 缺少必填字段 {required}")

    return TheoryEntry(
        id=meta["id"],
        source=meta["source"],
        quote=quote,
        applies_to=_split_list(meta["applies_to"]),
        lesson=meta.get("lesson", ""),
        url=meta.get("url", ""),
        tags=_split_list(meta.get("tags", "")),
    )


def _extract_quote(path: Path, body: Iterable[str]) -> str:
    body = list(body)
    try:
        start = next(i for i, line in enumerate(body) if _QUOTE_SECTION.match(line))
    except StopIteration as exc:
        raise ValueError(f"{path.name}: 缺少 `## 原文摘录` 小节") from exc
    chunk: list[str] = []
    for line in body[start + 1 :]:
        if _ANY_SECTION.match(line):
            break
        chunk.append(_QUOTE_MARK.sub("", line).rstrip())
    quote = "\n".join(chunk).strip()
    if not _norm(quote):
        raise ValueError(f"{path.name}: `## 原文摘录` 是空的")
    return quote


def _split_list(value: str) -> tuple[str, ...]:
    parts = [p.strip().strip("`") for p in re.split(r"[,，]", value)]
    return tuple(p for p in parts if p)


# ----------------------------------------------------------------- 检索


def search(
    entries: Mapping[str, TheoryEntry], query: str, limit: int = 5
) -> list[TheoryEntry]:
    """极简关键词检索。查询串里的词按空白切分，中文也照切。

    只要求「能找回该找的条目」，故意不引入向量库——证据库很小，
    可解释性比召回率重要：每条命中都能说清是哪个词命中的。
    """
    tokens = [t for t in re.split(r"[\s,，、/]+", query) if t]
    if not tokens or not entries:
        return []
    scored: list[tuple[int, str, TheoryEntry]] = []
    for entry in entries.values():
        hay = entry.haystack()
        score = 0
        for token in tokens:
            if token in entry.id:
                score += 3
            elif token in hay:
                score += 1
        if score:
            scored.append((score, entry.id, entry))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [entry for _, _, entry in scored[:limit]]


# ----------------------------------------------------------------- 补丁审核


def parse_header(patch_text: str) -> dict[str, str]:
    """取出补丁最前面那段 ``# key: value`` 元数据。

    没有元数据块就返回空 dict —— 「没写」和「写了但写错」都要能区分。
    """
    header: dict[str, str] = {}
    current: str | None = None
    for line in patch_text.splitlines():
        if _DIFF_START.match(line):
            break
        if not line.strip():
            current = None
            continue
        match = _HEADER_LINE.match(line)
        if match:
            current = match.group(1).lower()
            header[current] = match.group(2).strip()
            continue
        cont = _HEADER_CONT.match(line)
        if cont and current:
            header[current] = f"{header[current]}\n{cont.group(1).strip()}".strip()
            continue
        current = None
    return header


def touched_paths(patch_text: str) -> tuple[str, ...]:
    """补丁碰到的文件（已归一化掉 a/ b/ 与 chanlun/ 前缀）。"""
    seen: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            for token in parts[2:4]:
                norm = _normalize_path(token)
                if norm and norm not in seen:
                    seen.append(norm)
            continue
        match = _TOUCHED.match(line)
        if match:
            norm = _normalize_path(match.group(1))
            if norm and norm not in seen:
                seen.append(norm)
    return tuple(seen)


def _normalize_path(path: str) -> str:
    path = path.strip().strip('"')
    if path in ("/dev/null", "dev/null"):
        return ""
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
    if path.startswith("chanlun/"):
        path = path[len("chanlun/") :]
    return path


def is_algo_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in ALGO_PREFIXES)


def _resolve_ids(raw: str, entries: Mapping[str, TheoryEntry]) -> tuple[list[str], list[str]]:
    declared = [p.strip() for p in re.split(r"[,，\s]+", raw or "") if p.strip()]
    known = [i for i in declared if i in entries]
    unknown = [i for i in declared if i not in entries]
    return known, unknown


def _quote_matches(quote: str, entries: Iterable[TheoryEntry]) -> bool:
    target = _norm(quote)
    if not target:
        return False
    for entry in entries:
        source = _norm(entry.quote)
        if target in source or source in target:
            return True
    return False


def validate_patch(
    patch_text: str, entries: Mapping[str, TheoryEntry]
) -> PatchVerdict:
    """判定一个补丁能否被接受为「提案」。

    规则（按优先级）：

    1. 补丁必须至少改动一个文件；
    2. 必须带 ``# key: value`` 元数据块；
    3. 碰到 :data:`ALGO_PREFIXES` → ``kind: theory``，必须
       （a）声明 ``kind: theory``，（b）``theory:`` 列出**存在**的条目 id，
       （c）``quote:`` 是所引用条目原文的逐字片段；
    4. 没碰主干 → ``kind: engineering``，必须附 ``evidence:`` 实测证据
       （命令 + 原始输出），但不得自称理论类。
    """
    reasons: list[str] = []
    touched = touched_paths(patch_text)
    if not touched:
        return PatchVerdict(
            ok=False,
            kind=KIND_ENGINEERING,
            theory_id=None,
            reasons=("补丁没有改动任何文件（找不到 diff 行），拒绝。",),
            touched=(),
        )

    algo = [p for p in touched if is_algo_path(p)]
    kind = KIND_THEORY if algo else KIND_ENGINEERING
    header = parse_header(patch_text)
    if not header:
        reasons.append(
            "缺少补丁头：请在最前面加 `# kind: ...` 等元数据；"
            "改动缠论算法必须注明 theory: 条目 id 与 quote: 原文摘录。"
        )

    declared_kind = header.get("kind", "")
    theory_id: str | None = None

    if algo:
        if declared_kind != KIND_THEORY:
            reasons.append(
                "改动缠论算法（{}）必须以 `# kind: theory` 提出——"
                "工程类补丁不得触碰主干算法。".format("、".join(algo))
            )
        known, unknown = _resolve_ids(header.get("theory", ""), entries)
        if not known and not unknown:
            reasons.append(
                "改动缠论算法必须引用 theory/ 条目 id"
                "（补丁头缺 `# theory: <条目 id>`）：没有原文依据的算法改动一律拒绝。"
            )
        for bad in unknown:
            reasons.append(f"引用了不存在的理论条目 id：{bad}")
        if known:
            theory_id = ",".join(known)
        quote = header.get("quote", "")
        if not quote.strip():
            reasons.append(
                "改动缠论算法必须附 `# quote: <原文摘录>`（theory/ 条目的逐字原文）。"
            )
        elif known and not _quote_matches(quote, [entries[i] for i in known]):
            reasons.append(
                "`quote:` 与所引用理论条目的原文不符——不允许改写或编造原文摘录。"
            )
    else:
        if declared_kind and declared_kind != KIND_ENGINEERING:
            reasons.append(
                f"补丁未改缠论算法，`kind` 应为 `{KIND_ENGINEERING}`，实际为 `{declared_kind}`。"
            )
        if not header.get("evidence", "").strip():
            reasons.append(
                "工程类改动必须附实测证据（补丁头缺 `# evidence:`：命令 + 原始输出）。"
            )

    return PatchVerdict(
        ok=not reasons,
        kind=kind,
        theory_id=theory_id,
        reasons=tuple(reasons),
        touched=touched,
    )
