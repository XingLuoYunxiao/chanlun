"""每轮记录 + 熔断判定。

优化器是 24x7 跑的，所以「它自己坏了」必须能被机器判定，而不是等人
去看日志。这里定义两件事：

- :class:`JournalEntry` —— 一轮的完整证据（含前后对比数字）；
- :class:`CircuitBreaker` —— 什么时候必须停下来，并且**停下来就不再自己
  重启**（`Restart=always` 的 systemd 会一直拉起进程，所以熔断必须体现为
  非零退出码 + 一条 ``circuit_break`` 记录，而不是靠进程退出）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

PROPOSED = "proposed"
REJECTED = "rejected"
INCONCLUSIVE = "inconclusive"
CIRCUIT_BREAK = "circuit_break"

VALID_STATUS: tuple[str, ...] = (PROPOSED, REJECTED, INCONCLUSIVE, CIRCUIT_BREAK)
VALID_KIND: tuple[str, ...] = ("theory", "engineering")

#: 一轮记录必须齐备的字段。缺一个就是不完整的一轮。
REQUIRED_FIELDS: tuple[str, ...] = (
    "round",
    "started_at",
    "kind",
    "finding",
    "evidence",
    "theory_id",
    "patch_file",
    "before",
    "after",
    "tests",
    "status",
)


@dataclass
class JournalEntry:
    """一轮优化记录。

    ``before`` / ``after`` 用**同一套口径**的指标字典，例如
    ``{"signals": 0, "confirmed_segments": 328}``；没有可比的数字就不算
    有效产出。
    """

    round: int
    started_at: str
    kind: str
    finding: str
    evidence: str
    theory_id: str | None = None
    patch_file: str | None = None
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, Any] = field(default_factory=dict)
    status: str = INCONCLUSIVE
    probe: str = ""
    notes: str = ""
    patch_sha256: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.round, int) or self.round < 1:
            raise ValueError(f"round 必须是 >=1 的整数，实际 {self.round!r}")
        if not str(self.started_at).strip():
            raise ValueError("started_at 不能为空")
        if self.kind not in VALID_KIND:
            raise ValueError(f"kind 必须是 {VALID_KIND} 之一，实际 {self.kind!r}")
        if self.status not in VALID_STATUS:
            raise ValueError(f"status 必须是 {VALID_STATUS} 之一，实际 {self.status!r}")
        if not str(self.evidence).strip():
            raise ValueError("evidence 不能为空：没有实测证据的一轮没有意义")
        if not str(self.finding).strip():
            raise ValueError("finding 不能为空")
        if self.status == PROPOSED and self.kind == "theory" and not self.theory_id:
            raise ValueError("理论类提案必须给出 theory_id")

    # ------------------------------------------------------------- 序列化

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "JournalEntry":
        known = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in payload.items() if k in known}
        return cls(**data)

    # ------------------------------------------------------------- 判定

    @property
    def effective(self) -> bool:
        return is_effective(self)

    @property
    def green_to_red(self) -> int:
        """本轮把测试从全绿搞红的次数（0 或 1，外加显式累加字段）。"""
        count = int(self.tests.get("green_to_red", 0) or 0)
        before = str(self.tests.get("before", "")).lower()
        after = str(self.tests.get("after", "")).lower()
        if before == "green" and after.startswith("red"):
            count += 1
        return count


def is_effective(entry: JournalEntry) -> bool:
    """是否算一次**有效产出**：提案 + 前后数字确实动了。

    注意这里**不**看有没有补丁文件：一个 ``proposed`` 却数字没动的提案，
    同样是「没有有效产出」，会照常累计到熔断计数里。这样「原地打转」
    跑不掉。
    """
    if entry.status != PROPOSED:
        return False
    return dict(entry.before) != dict(entry.after)


def write_entry(journal_dir: str | Path, entry: JournalEntry) -> Path:
    journal_dir = Path(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = journal_dir / f"round-{entry.round:03d}.json"
    path.write_text(
        json.dumps(entry.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def read_all(journal_dir: str | Path) -> list[JournalEntry]:
    journal_dir = Path(journal_dir)
    if not journal_dir.is_dir():
        return []
    entries: list[JournalEntry] = []
    for path in sorted(journal_dir.glob("round-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            entries.append(JournalEntry.from_dict(payload))
        except (TypeError, ValueError):
            continue
    entries.sort(key=lambda e: e.round)
    return entries


@dataclass(frozen=True)
class CircuitState:
    should_break: bool
    reason: str
    barren_streak: int
    red_transitions: int


@dataclass
class CircuitBreaker:
    """连续无有效产出 / 反复把测试搞红 → 停。

    两条独立的线，任一触发就熔断：

    - ``max_barren`` 轮连续没有有效产出（提案 + 补丁 + 数字动了）；
    - 累计 ``max_red`` 次把测试从全绿搞红。

    另外，只要历史上出现过 ``circuit_break`` 记录，就**保持熔断**：
    自动重启不能把熔断状态冲掉，必须有人来看一眼。
    """

    max_barren: int = 3
    max_red: int = 2

    def evaluate(self, entries: Iterable[JournalEntry]) -> CircuitState:
        ordered = sorted(entries, key=lambda e: e.round)
        barren = 0
        red = 0
        for entry in ordered:
            if entry.status == CIRCUIT_BREAK:
                # 熔断轮本身不只是「一次翻转」：它意味着循环已经停下，
                # 之后的记录不再累计，且状态保持为熔断。
                red += entry.green_to_red
                return CircuitState(
                    should_break=True,
                    reason=f"第 {entry.round} 轮已记录 circuit_break，保持熔断直到人工处理",
                    barren_streak=0,
                    red_transitions=red,
                )
            red += entry.green_to_red
            barren = 0 if entry.effective else barren + 1
            if barren >= self.max_barren:
                return CircuitState(
                    should_break=True,
                    reason=f"连续 {barren} 轮没有有效产出（阈值 {self.max_barren}）",
                    barren_streak=barren,
                    red_transitions=red,
                )
            if red >= self.max_red:
                return CircuitState(
                    should_break=True,
                    reason=f"累计 {red} 次把测试从全绿改成红（阈值 {self.max_red}）",
                    barren_streak=barren,
                    red_transitions=red,
                )
        return CircuitState(
            should_break=False,
            reason="",
            barren_streak=barren,
            red_transitions=red,
        )
