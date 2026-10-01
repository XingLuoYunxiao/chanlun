"""缠论优化器：只提案、不改主干的 7x24 自我优化循环。

对外只暴露三件事：

- :mod:`chanlun.optimizer.theory` —— 缠论原文证据库 + 补丁引用校验（安全带）；
- :mod:`chanlun.optimizer.journal` —— 每轮结构化记录 + 熔断判定；
- :mod:`chanlun.optimizer.agent` —— 单轮循环（发现 → 取证 → 出补丁 → 前后对比）。

设计底线（写进代码而不是写进文档）：优化器在任何情况下都不修改
``src/chanlun/{chan,scan,web,backtest}/`` 下的主干算法代码。它只把
``git diff`` 形式的补丁写进 ``optimizer/patches/``，前后对比数字写进
``optimizer/journal/``，等人来确认。
"""

from .journal import (
    CIRCUIT_BREAK,
    INCONCLUSIVE,
    PROPOSED,
    REJECTED,
    CircuitBreaker,
    CircuitState,
    JournalEntry,
    is_effective,
    read_all,
    write_entry,
)
from .theory import (
    ALGO_PREFIXES,
    PatchVerdict,
    TheoryEntry,
    load_theory,
    search,
    validate_patch,
)

__all__ = [
    "ALGO_PREFIXES",
    "CIRCUIT_BREAK",
    "INCONCLUSIVE",
    "PROPOSED",
    "REJECTED",
    "CircuitBreaker",
    "CircuitState",
    "JournalEntry",
    "PatchVerdict",
    "TheoryEntry",
    "is_effective",
    "load_theory",
    "read_all",
    "search",
    "validate_patch",
    "write_entry",
]
