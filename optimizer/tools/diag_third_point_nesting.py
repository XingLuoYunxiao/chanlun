"""数一数：`_third_kind` 的嵌套 if 到底吞掉了几个三类买卖点。

背景（G1c 提案）：主干原来把两类判定写成嵌套 if

    if leave.direction == 1 and leave.high > zg:
        if back.direction == -1 and back.low > zg:   -> B3
    elif leave.direction == -1 and leave.low < zd:
        if back.direction == 1 and back.high < zd:   -> S3

G1c 主张改成平铺 and 链，理由是「一段既穿过 ZG 又穿过 ZD 的离开段会让 B3 分支
进得去、出不来，把本该成立的 S3 一起吞掉」。

这个脚本不改主干，只在真实数据上数「吞掉了几个」：

    b3_entered_inner_fail : 进了 B3 外层分支、内层不成立（嵌套真的挡住了东西）
    g1c_adds_s3           : 上述里 G1c 的平铺 S3 条件成立 —— 即被吞掉的 S3
    s3_entered_inner_fail : 进了 S3 外层分支、内层不成立
    g1c_adds_b3           : 上述里 G1c 的平铺 B3 条件成立 —— 即被吞掉的 B3
    pierce_both           : 离开段同时穿过 ZG 与 ZD 的中枢数（G1c 担心的形状）

用法（在 chanlun/ 下）：

    python optimizer/tools/diag_third_point_nesting.py [--codes 150] [--start 2020-01-02]

2026-10-01 实测：148 只票 / 197793 根日线 / 214 个中枢 →
pierce_both=65、b3_entered_inner_fail=41、s3_entered_inner_fail=20，
但 g1c_adds_s3=0、g1c_adds_b3=0。原因是**线段方向必然交替**（线段定义的一部分），
leave.direction==1 ⇒ back.direction==-1，平铺版 S3 要求的 back.direction==1
恒不成立；反过来同理。嵌套 if 结构上不可能吞掉另一侧，G1c 的前提随 G1a
（位置口径）一起消失。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from chanlun.chan.engine import ChanEngine      # noqa: E402
from chanlun.chan.signal import find_signals    # noqa: E402
from chanlun.data import store                  # noqa: E402


def sample_codes(limit: int) -> list[str]:
    conn = sqlite3.connect(ROOT / "data" / "meta.db")
    try:
        codes = [c for (c,) in conn.execute(
            "select code from sync_state where period='day' and rows>0 order by code")]
    finally:
        conn.close()
    if limit <= 0 or len(codes) <= limit:
        return codes
    return codes[:: max(1, len(codes) // limit)][:limit]


def scan(codes: list[str], start: str) -> dict[str, int]:
    tot = {"codes": 0, "bars": 0, "pivots": 0, "pierce_both": 0,
           "b3_entered_inner_fail": 0, "g1c_adds_s3": 0,
           "s3_entered_inner_fail": 0, "g1c_adds_b3": 0}
    for code in codes:
        try:
            bars = store.read(code, "day", start=start)
        except Exception:
            continue
        if bars is None or len(bars) < 200:
            continue
        snap = ChanEngine(code, "day", signal_fn=find_signals).full(bars)
        segs = list(snap.segments)
        tot["codes"] += 1
        tot["bars"] += len(bars)
        for p in snap.pivots:
            tot["pivots"] += 1
            if p.end_idx + 1 >= len(segs):
                continue
            leave, back = segs[p.end_idx], segs[p.end_idx + 1]
            if leave.high > p.zg and leave.low < p.zd:
                tot["pierce_both"] += 1
            if leave.direction == 1 and leave.high > p.zg:
                if not (back.direction == -1 and back.low > p.zg):
                    tot["b3_entered_inner_fail"] += 1
                    if leave.low < p.zd and back.direction == 1 and back.high < p.zd:
                        tot["g1c_adds_s3"] += 1
            elif leave.direction == -1 and leave.low < p.zd:
                if not (back.direction == 1 and back.high < p.zd):
                    tot["s3_entered_inner_fail"] += 1
                    if leave.high > p.zg and back.direction == -1 and back.low > p.zg:
                        tot["g1c_adds_b3"] += 1
    return tot


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--codes", type=int, default=150, help="抽样只数（<=0 表示全量）")
    ap.add_argument("--start", default="2020-01-02", help="起始日期")
    args = ap.parse_args()
    print(json.dumps(scan(sample_codes(args.codes), args.start), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
