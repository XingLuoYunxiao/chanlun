"""`scan` 子命令：把扫描/跟踪接到统一命令行（Task 15）。

为什么单独提供 `add_parser(sub)` 与 `register(sub)` 两个入口
----------------------------------------------------------
Task 16 要把本任务并进 `__main__.py` 的分发器，而这里必须能在**不改**
`__main__.py` 的前提下被挂上：

- `add_parser(sub)`：只负责「参数长什么样」，返回 `ArgumentParser`；
- `register(sub)`：`add_parser` + `set_defaults(func=run)`，让父分发器直接
  `args.func(args)` 就能跑。

`main()` 则让本模块可以独立运行（`python -m chanlun.scan.cli`），
这也是本次交付真实扫描耗时的入口。
"""

from __future__ import annotations

import argparse
import contextlib
import sqlite3
from typing import Any, Iterator, Sequence

from ..data import meta
from ..notify import build_notifier, notify_scan, notify_track
from .scanner import ScanHit, scan_report
from .watchlist import TrackRow, track_watchlist


@contextlib.contextmanager
def _conn_scope() -> Iterator[sqlite3.Connection]:
    """打开元数据库连接并按需归还（测试会替换成临时库）。"""
    conn = meta.init()
    try:
        yield conn
    finally:
        conn.close()


def _split(text: str | None) -> list[str] | None:
    if text is None:
        return None
    items = [part.strip() for part in str(text).replace("，", ",").split(",")]
    return [i for i in items if i] or None


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """把 `scan` 子命令挂到父级 subparsers 上。"""
    parser = sub.add_parser(
        "scan", help="全市场扫描 / 自选池跟踪（缠论买卖点）",
        description="按缠论结构扫描全市场或指定代码，输出按强度排序的买卖点清单。",
    )
    parser.add_argument("--periods", default="day,30,5",
                        help="周期列表，逗号分隔（day,60,30,5），默认 day,30,5")
    parser.add_argument("--codes", default=None,
                        help="代码列表，逗号分隔；不填则扫描 meta.universe 全市场")
    parser.add_argument("--workers", type=int, default=8,
                        help="并行进程数，默认 8（1 = 串行）")
    parser.add_argument("--top", type=int, default=20, help="终端最多打印多少条命中，默认 20")
    parser.add_argument("--watch", action="store_true",
                        help="改为跟踪自选池（meta.watchlist）并给出相对上一次快照的变化")
    parser.add_argument("--notify-file", default=None,
                        help="同时把结果追加写入该文件（UTF-8，不联网）")
    parser.add_argument("--no-save", dest="save", action="store_false", default=True,
                        help="不写入 scan_result 表（默认写入）")
    parser.add_argument("--run-date", default=None, help="写入 scan_result 的日期，默认今天")
    return parser


def register(sub: Any) -> argparse.ArgumentParser:
    """`add_parser` + 绑定 `func`，供父分发器直接调用。"""
    parser = add_parser(sub)
    parser.set_defaults(func=run)
    return parser


def format_hits_table(hits: Sequence[ScanHit]) -> str:
    """中文定宽表格：终端里能直接看清「哪只票、什么级别、什么信号、多强」。"""
    if not hits:
        return "（无命中）"
    header = ("代码", "名称", "周期", "信号", "状态", "强度", "中枢区间", "背驰", "时间")
    rows = [header]
    for h in hits:
        rng = f"{h.pivot_range[0]:.2f}-{h.pivot_range[1]:.2f}" if len(h.pivot_range) == 2 else "-"
        name = h.name or "-"
        rows.append((
            h.code, name, str(h.period), h.signal_label, str(h.status),
            f"{h.strength:.2f}", rng, f"{h.divergence_strength:.2f}", str(h.ts),
        ))
    widths = [max(_width(r[i]) for r in rows) for i in range(len(header))]
    lines = []
    for idx, row in enumerate(rows):
        cells = [_pad(cell, widths[i]) for i, cell in enumerate(row)]
        lines.append("  ".join(cells).rstrip())
        if idx == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _width(text: str) -> int:
    """中文按 2 个字符宽计算，表格才不会错位。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(text))


def _pad(text: str, width: int) -> str:
    return str(text) + " " * max(0, width - _width(text))


def format_track_table(rows: Sequence[TrackRow]) -> str:
    if not rows:
        return "（自选池为空）"
    lines = []
    for row in rows:
        if row.status != "ok":
            lines.append(f"{row.code} {row.name or '-'} [{row.period}] {row.status}：{row.error}")
            continue
        for change in row.changes:
            lines.append(f"{row.code} {row.name or '-'} [{row.period}] {change.detail}")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    """执行子命令。返回 0=正常；1=所有任务都失败（相对上一次快照/扫描无任何结论）。"""
    periods = _split(getattr(args, "periods", None)) or ["day"]
    codes = _split(getattr(args, "codes", None))
    workers = max(1, int(getattr(args, "workers", 8) or 1))
    top = max(0, int(getattr(args, "top", 20) or 0))
    run_date = getattr(args, "run_date", None)
    save = bool(getattr(args, "save", True))
    notify_file = getattr(args, "notify_file", None)
    notifier = build_notifier("file", path=notify_file) if notify_file else build_notifier("null")

    with _conn_scope() as conn:
        if getattr(args, "watch", False):
            rows = track_watchlist(codes, periods=periods, conn=conn, save=save)
            print(format_track_table(rows[:top] if top else rows))
            failed = [r for r in rows if r.status == "error"]
            print(f"跟踪完成：{len(rows)} 只，失败 {len(failed)} 只")
            notify_track(notifier, rows)
            return 1 if rows and len(failed) == len(rows) else 0

        report = scan_report(periods=periods, codes=codes, max_workers=workers,
                             conn=conn, save=save, run_date=run_date)
        print(format_hits_table(report.hits[:top] if top else report.hits))
        print(report.summary())
        if report.structure:
            st = report.structure
            print(f"结构统计：bar {st.get('bars', 0)} 根、笔 {st.get('strokes', 0)}、"
                  f"线段 {st.get('segments', 0)}、中枢 {st.get('pivots', 0)}、"
                  f"买卖点 {st.get('signals', 0)}")
        if report.notes:
            print("数据质量提示（元数据与本地文件不一致等，前 3 条）：")
            for note in report.notes[:3]:
                print(f"  - {note}")
        notify_scan(notifier, report.hits, run_date=report.run_date)
        if report.tasks and len(report.errors) == report.tasks:
            print("本次扫描全部失败：请检查本地数据（parquet）与 meta.sync_state 是否一致。")
            return 1
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    """独立入口（`python -m chanlun.scan.cli scan ...`）。"""
    parser = argparse.ArgumentParser(prog="chanlun-scan", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    register(sub)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - 手动运行入口
    raise SystemExit(main())
