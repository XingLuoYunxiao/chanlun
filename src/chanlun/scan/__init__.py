"""全市场扫描与自选池跟踪（Task 15）。

对外只需要记住两个入口：

- `scan_report(...)` / `scan(...)`：全市场或指定代码的买卖点扫描（多进程），
  返回按强度排序的 `ScanHit`；
- `track_watchlist(...)`：自选池相对**上一次快照**的变化（新中枢/突破/买卖点/背驰）。

命令行入口见 `chanlun.scan.cli`（`add_parser(sub)` / `register(sub)`），
通知出口见 `chanlun.notify`（控制台 + 文件，不联网）。
"""

from __future__ import annotations

from .scanner import (
    KIND_WEIGHT,
    LEVEL_WEIGHT,
    LEVEL_RANK,
    REQUIREMENTS,
    SIGNAL_LABEL,
    STATUS_WEIGHT,
    Outcome,
    Requirement,
    ScanHit,
    ScanReport,
    ScanTask,
    Sufficiency,
    TaskResult,
    analyze_snapshot,
    check_sufficiency,
    default_snapshot_source,
    divergence_of,
    hit_strength,
    save_report,
    scan,
    scan_report,
    segment_divergence,
    signal_label,
)
from .watchlist import (
    SNAPSHOT_VERSION,
    TrackChange,
    TrackChangeKind,
    TrackRow,
    build_payload,
    diff_payloads,
    track_watchlist,
)

__all__ = [
    "KIND_WEIGHT", "LEVEL_WEIGHT", "LEVEL_RANK", "REQUIREMENTS", "SIGNAL_LABEL",
    "STATUS_WEIGHT", "SNAPSHOT_VERSION",
    "Outcome", "Requirement", "ScanHit", "ScanReport", "ScanTask", "Sufficiency",
    "TaskResult", "TrackChange", "TrackChangeKind", "TrackRow",
    "analyze_snapshot", "build_payload", "check_sufficiency", "default_snapshot_source",
    "diff_payloads", "divergence_of", "hit_strength", "save_report", "scan",
    "scan_report", "segment_divergence", "signal_label", "track_watchlist",
]
