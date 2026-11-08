#!/usr/bin/env python
"""baostock 静默挂死复现：黑洞 TCP 服务端下，取数到底会不会自己回来。

## 复现的故障

baostock 的 socket 是**裸创建**的（`.venv-chanlun/.../baostock/util/socketutil.py`
里直接 `socket.socket(...)` + `.connect(...)`，**从不 `settimeout`**），
`send_msg()` 又在 `while True` 里 `recv(8192)` 直到收到结束标记。
于是服务端一旦接受连接却不回包，`recv` 就**永久**阻塞。

`chanlun/data/baostock_source.py` 原有的 `RETRIES` 只在**拿到响应之后**才生效
（空结果 / 非零 `error_code`），对静默挂死毫无办法。

实测事故：2026-10-08 17:35:41 起的定时任务卡在 `sz.000033`，
到被手动 kill 时已经过了 **6 小时 14 分**；主线程栈停在
`sock_recv → sock_recv_guts → sock_call_ex → sock_recv_impl → __recvfrom`，
累计 CPU 只有 2.8 秒，落库停在 17:36:32。

## 这个脚本怎么测

起一个**本地黑洞服务端**（accept 之后永不回包，也不关闭连接），把 baostock 的
`BAOSTOCK_SERVER_IP/PORT` 指过去，然后量一次 `fetch_bars` 的行为：

| 这一路 | `SOCKET_TIMEOUT` | 期望 |
|---|---|---|
| 修复后 | `0.3s` | `DataSourceError`（超时 → 重试 → 报错），秒级返回 |
| 修复前 | `None` | **不返回**，由父进程在 20s 处杀掉 |

两路都跑在**子进程**里，因为「修复前」那一路的唯一正确结局就是永不返回 ——
父进程用 `subprocess.run(timeout=...)` 把它杀掉，不能让它拖住测量本身。

`SOCKET_TIMEOUT=None` 就是修复前的行为：`socket.setdefaulttimeout(None)` = 阻塞。

## 跑法

```bash
cd <本项目根目录>
../.venv-chanlun/bin/python optimizer/tools/measure_baostock_socket_hang.py
```

退出码 0 = 两路都符合上表（修复确实改变了行为）；1 = 不符合。

脚本**只连本机黑洞端口**，不碰真实 baostock，也不写任何数据。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    ROOT = Path(__file__).resolve().parents[2]
except NameError:  # 交互式粘贴
    ROOT = Path.cwd()

#: 「修复前」那一路最多等这么久，超了就当挂死判定。
KILL_AFTER = 20.0
#: 「修复后」那一路的 socket 超时，压到 0.3s 让测量几秒内跑完。
PROBE_TIMEOUT = 0.3


def _run_child(probe_timeout: float | None) -> int:
    """子进程：对着黑洞服务端真跑一次 `fetch_bars`，把结果打成一行 JSON。"""
    import baostock.common.contants as cons

    from chanlun.data import baostock_source as bst
    from chanlun.data.types import DataSourceError

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    host, port = srv.getsockname()

    held: list[socket.socket] = []
    stop = threading.Event()
    srv.settimeout(0.2)

    def accept_loop() -> None:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except (TimeoutError, OSError):
                continue
            held.append(conn)  # 收下就晾着：永不回包、也不关闭

    threading.Thread(target=accept_loop, daemon=True).start()

    cons.BAOSTOCK_SERVER_IP = host
    cons.BAOSTOCK_SERVER_PORT = port
    bst.SOCKET_TIMEOUT = probe_timeout
    bst.RETRY_SLEEP = 0
    bst._LOGGED_IN = False  # noqa: SLF001 - 测量要一次干净的登录

    started = time.monotonic()
    try:
        bst.fetch_bars("sh.600000", "day", "2024-01-01", "2024-01-05")
        outcome = "returned（异常：黑洞服务端不可能给出数据）"
    except DataSourceError as exc:
        outcome = f"DataSourceError: {exc}"
    except Exception as exc:  # noqa: BLE001 - 测量要看到所有形状
        outcome = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.monotonic() - started, 2)

    stop.set()
    srv.close()
    for conn in held:
        conn.close()
    print(json.dumps(
        {"outcome": outcome, "elapsed": elapsed, "socket_timeout": probe_timeout},
        ensure_ascii=False,
    ))
    return 0


def _run_parent() -> int:
    env = dict(os.environ)
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    me = str(Path(__file__).resolve())

    cases = (
        ("修复后 SOCKET_TIMEOUT=0.3s", PROBE_TIMEOUT),
        ("修复前 SOCKET_TIMEOUT=None", None),
    )
    results: list[tuple[str, dict]] = []
    for label, timeout in cases:
        cmd = [sys.executable, me, "--child", "--probe-timeout", "none" if timeout is None else str(timeout)]
        started = time.monotonic()
        try:
            cp = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=KILL_AFTER)
        except subprocess.TimeoutExpired:
            results.append((label, {
                "outcome": f"永不返回（{KILL_AFTER:.0f}s 后由父进程杀掉）",
                "elapsed": round(time.monotonic() - started, 2),
                "socket_timeout": timeout,
            }))
            continue
        line = (cp.stdout or "").strip().splitlines()
        try:
            payload = json.loads(line[-1]) if line else {"outcome": f"无输出 rc={cp.returncode}"}
        except json.JSONDecodeError:
            payload = {"outcome": f"输出不可解析 rc={cp.returncode}: {cp.stdout!r}{cp.stderr!r}"}
        results.append((label, payload))

    print("=== 黑洞服务端下的 fetch_bars 行为 ===")
    for label, payload in results:
        print(f"  {label}：{payload['outcome']}（{payload['elapsed']}s）")

    fixed = results[0][1]
    broken = results[1][1]
    ok_fixed = str(fixed.get("outcome", "")).startswith("DataSourceError") and fixed.get("elapsed", 9e9) < KILL_AFTER
    ok_broken = "永不返回" in str(broken.get("outcome", ""))
    print()
    print(f"  修复后必须秒级报错：{'通过' if ok_fixed else '不通过'}")
    print(f"  修复前必须永不返回：{'通过' if ok_broken else '不通过'}")
    print("  结论：" + ("两路行为确实不同 —— 这次测量在测量东西" if ok_fixed and ok_broken
                      else "两路没有差别 —— 测量失效，不许当作验证通过"))
    return 0 if (ok_fixed and ok_broken) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="baostock 静默挂死复现（黑洞 TCP 服务端）")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--probe-timeout", default=str(PROBE_TIMEOUT), help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child:
        raw = str(args.probe_timeout)
        return _run_child(None if raw.lower() == "none" else float(raw))
    return _run_parent()


if __name__ == "__main__":
    raise SystemExit(main())
