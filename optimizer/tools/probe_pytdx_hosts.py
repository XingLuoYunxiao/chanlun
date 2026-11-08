"""实测 `pytdx` 的行情服务器哪些还活着。

**为什么需要它**：`pytdx.config.hosts.hq_hosts` 里 104 台服务器，2026-10-13 实测
**只有 8 台活着**（国泰君安 `117.34.114.13–.27:7709` 段）。`data/pytdx_source.py`
的 `HOSTS` 就是那次实测的结果，但**IP 会变** —— 全线取数失败时先跑这个，
把还活着的填回 `HOSTS`。

**本项目在此栽过一次**：第 3 轮试到几台死服务器，就把结论写成「pytdx 协议不兼容」，
于是 1 分钟数据被判为「无源」**长达十轮**，直到 2026-10-13 把 104 台全试一遍才发现。
教训写在工具输出里：**把「试了几个都失败」写成「协议不兼容」之前，先确认样本量够。**

跑法：
    cd <项目根>
    ../.venv-chanlun/bin/python optimizer/tools/probe_pytdx_hosts.py [--limit N]

**联网**，所以不进测试。退出码 0 = 至少有一台可用（否则 1）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pytdx.config.hosts import hq_hosts  # noqa: E402
from pytdx.hq import TdxHq_API  # noqa: E402


def probe(timeout: float = 4.0, limit: int | None = None) -> list[tuple[str, str, int]]:
    """逐台试 `get_security_bars(9, 0, '000001', 0, 5)`；返回活着的那些。"""
    hosts = hq_hosts[:limit] if limit else hq_hosts
    alive: list[tuple[str, str, int]] = []
    for i, (name, ip, port) in enumerate(hosts):
        api = TdxHq_API(heartbeat=False)
        try:
            if not api.connect(ip, port, time_out=timeout):
                continue
            bars = api.get_security_bars(9, 0, "000001", 0, 5)   # 9 = 日线
            if bars:
                alive.append((name, ip, port))
                print(f"  ✓ [{i:3d}] {name:12s} {ip}:{port}  日线 {len(bars)} 根")
        except Exception as exc:
            print(f"    [{i:3d}] {ip}:{port}  {type(exc).__name__}")
        finally:
            try:
                api.disconnect()
            except Exception:
                pass
    return alive


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=4.0)
    ap.add_argument("--limit", type=int, default=None, help="只试前 N 台（调试用）")
    args = ap.parse_args()

    print(f"逐台试 pytdx 内置行情服务器（共 {len(hq_hosts)} 台）…")
    alive = probe(args.timeout, args.limit)
    print(f"\n⇒ 可用 {len(alive)}/{args.limit or len(hq_hosts)} 台")
    if alive:
        print("\n把这一段填回 src/chanlun/data/pytdx_source.py 的 HOSTS：")
        print("HOSTS: tuple[tuple[str, int], ...] = (")
        for _name, ip, port in alive:
            print(f'    ("{ip}", {port}),')
        print(")")
        return 0
    print(
        "\n一台都没有。**别急着写「协议不兼容」** —— 先确认你试的台数够"
        "（本项目就因为只试了几台死的，把 1 分钟数据误判为「无源」十轮）。"
        "也检查本机网络/防火墙是否放行 7709。"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
