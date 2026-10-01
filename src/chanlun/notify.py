"""通知出口：把扫描/跟踪结果送出去（Task 15）。

**为什么先只做控制台与文件两种出口**：一是可测（不依赖外部服务），二是
「不联网」是硬要求——真实交易环境里扫描结果属于敏感信息，默认不应该离开本机。
需要推送到手机/IM 时，实现 `Notifier` 协议（只有 `send(title, body)` 一个方法）
在外层接一层即可，本模块不做网络调用。

`MultiNotifier` 让「同时打印到终端 + 追加到日志文件」成为一行代码，
这也是收盘后无人值守跑扫描时的常用组合。
"""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol, Sequence, TextIO, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    """通知出口协议：只要能发一条「标题 + 正文」就算。"""

    def send(self, title: str, body: str) -> None:  # pragma: no cover - 协议
        ...


@dataclass
class ConsoleNotifier:
    """打印到流（默认 stdout）。流在**调用时**解析，便于测试捕获。"""

    stream: TextIO | None = None
    prefix: str = ""

    def send(self, title: str, body: str) -> None:
        out = self.stream if self.stream is not None else sys.stdout
        print(f"{self.prefix}{title}", file=out)
        if body:
            print(body, file=out)
        out.flush()


@dataclass
class FileNotifier:
    """追加写入 UTF-8 文本文件（带时间戳，方便回看历史）。"""

    path: Path | str
    encoding: str = "utf-8"

    def send(self, title: str, body: str) -> None:
        p = Path(self.path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with p.open("a", encoding=self.encoding) as fh:
            fh.write(f"[{stamp}] {title}\n")
            if body:
                fh.write(f"{body}\n")


@dataclass
class NullNotifier:
    """什么都不做的出口（测试/静默模式）。"""

    def send(self, title: str, body: str) -> None:
        return None


@dataclass
class MultiNotifier:
    """扇出到多个出口；单个出口抛错不影响其它出口。"""

    notifiers: Sequence[Notifier] = field(default_factory=tuple)

    def send(self, title: str, body: str) -> None:
        for n in self.notifiers:
            try:
                n.send(title, body)
            except Exception:  # 一个出口坏了不能连累其它出口
                continue


def build_notifier(kind: str = "console", path: Path | str | None = None,
                   stream: TextIO | None = None) -> Notifier:
    """按名字造出口。未知名字**报错**而不是静默退回控制台（避免漏通知）。"""
    key = str(kind).strip().lower()
    if key == "console":
        return ConsoleNotifier(stream=stream)
    if key in ("null", "none", "silent"):
        return NullNotifier()
    if key == "file":
        if path is None:
            raise ValueError("file 通知必须提供 path")
        return FileNotifier(path)
    raise ValueError(f"未知通知类型：{kind}（可选 console/file/null）")


# ------------------------------------------------------------------ 渲染

def format_scan_hits(hits: Iterable[object], *, run_date: str = "") -> str:
    """把命中列表渲染成中文正文。空命中也要说清楚「无命中」。"""
    hits = list(hits)
    if not hits:
        return "本次扫描无命中（数据不足或结构未达买卖点条件）。"
    lines = [f"共 {len(hits)} 条命中（按强度降序）："]
    for h in hits:
        rng = getattr(h, "pivot_range", ())
        rng_text = f"中枢 {rng[0]:.2f}-{rng[1]:.2f}" if len(rng) == 2 else "无对应中枢"
        lines.append(
            f"- {h.code} {h.name} [{h.period}] {h.signal_label}"
            f" 强度 {h.strength:.2f}（{rng_text}，背驰 {h.divergence_strength:.2f}）"
            f" @ {h.ts}"
        )
    return "\n".join(lines)


def format_track_rows(rows: Iterable[object]) -> str:
    """把自选池跟踪结果渲染成中文正文：每只票一行，逐条列出变化原因。"""
    rows = list(rows)
    if not rows:
        return "自选池为空，无跟踪结果。"
    lines = [f"自选池 {len(rows)} 只："]
    for r in rows:
        if getattr(r, "status", "ok") != "ok":
            lines.append(f"- {r.code} {r.name} [{r.period}] {r.status}：{r.error}")
            continue
        details = "；".join(c.detail for c in r.changes) or "无变化"
        lines.append(f"- {r.code} {r.name} [{r.period}] {details}")
    return "\n".join(lines)


def notify_scan(notifier: Notifier, hits: Iterable[object], *,
                title: str = "缠论扫描命中", run_date: str = "") -> None:
    head = f"日期：{run_date}\n" if run_date else ""
    notifier.send(title, head + format_scan_hits(hits, run_date=run_date))


def notify_track(notifier: Notifier, rows: Iterable[object], *,
                 title: str = "缠论自选池跟踪") -> None:
    notifier.send(title, format_track_rows(rows))
