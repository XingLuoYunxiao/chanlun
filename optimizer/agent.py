#!/usr/bin/env python
"""缠论 24x7 优化器：命令行入口（薄壳）。

它只干一件事——把 ``src/`` 塞进 ``sys.path``，然后把控制权交给
:mod:`chanlun.optimizer.cli`。真正的逻辑（理论库、日志、探针、影子测量、
熔断）全在 ``src/chanlun/optimizer/`` 里，因为那些代码需要被 pytest 直接
import、需要一个能写单测的位置；放在这里的壳子只有「让 ``python agent.py``
在没装包的情况下也能跑」这一个职责。

用法::

    python optimizer/agent.py --list-probes          # 看这一轮要回访哪些问题
    python optimizer/agent.py --rounds 10 --measure  # 跑 10 轮（提案模式，默认）
    python optimizer/agent.py --audit                # 只读审计：量一次主干口径
    python optimizer/agent.py --audit --patch optimizer/patches/round-001-G1a.patch
    python optimizer/agent.py --reset-breaker        # 人工 review 之后解除熔断

``--rounds N`` 是 24x7 循环的步长：跑完 N 轮就正常退出（退出码 0），
由 systemd ``Restart=always`` / launchd ``KeepAlive`` 接着起下一轮。
熔断时以退出码 3 退出，那个码是**不许自动重启**的信号。

提案模式是默认也是唯一模式：这个进程永远不会改主干算法代码，
只会写 ``optimizer/journal/`` 和 ``optimizer/patches/``。
"""

from __future__ import annotations

import sys
from pathlib import Path

#: chanlun/ —— 本文件在 chanlun/optimizer/agent.py，所以 parents[1] 就是包根。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from chanlun.optimizer.cli import main  # noqa: E402  （必须在上面的 sys.path 之后）


if __name__ == "__main__":
    raise SystemExit(main())
