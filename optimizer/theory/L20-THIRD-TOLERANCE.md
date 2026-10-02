---
id: L20-THIRD-TOLERANCE
lesson: 20
source: 缠中说禅《教你炒股票108课》第20课《缠中说禅走势中枢级别扩张及第三类买卖点》
url: https://chanlun108.cn/chanzhongshuochan108ke/20.html
applies_to: src/chanlun/chan/signal.py
tags: 第三类买卖点,第一次,ZG,ZD,回试,回抽,容忍度,工程口径,非严格模式
---

# 第三类买卖点：位置判据、必须是第一次、容忍度（第 20、24、32、33 课）

## 原文摘录

第 020 课《缠中说禅走势中枢级别扩张及第三类买卖点》：

> 由定理一，可以得到第三类买卖点定理：一个次级别走势类型向上离开缠中说禅走势中枢，
> 然后以一个次级别走势类型回试，其低点不跌破ZG，则构成第三类买点；
> 一个次级别走势类型向下离开缠中说禅走势中枢，然后以一个次级别走势类型回抽，
> 其高点不升破ZD，则构成第三类卖点。

第 020 课：

> 但一定要注意，并不是任何回调回抽都是第三类买卖点，必须是第一次。

第 020 课中心定理一：

> 走势中枢的延伸等价于任意区间[dn，gn]与[ZD，ZG]有重叠。

第 024 课：

> 其后的次级别回跌并不重新回到前面的中枢里

第 032 课：

> 如果一个5分钟级别的回拉不回到中枢里，就意味着有第三类买点

第 033 课：

> 次级别回抽一旦不重新回到中枢里，就意味着第三类买点出现了

## 判据一：「必须是第一次」现行代码**已经满足**，不需要补过滤

`src/chanlun/chan/pivot.py` 的 `find_pivots` 延伸循环
`while j < n and j - i < MAX_SEGMENTS and _overlaps(confirmed[j], zd, zg)`
让中枢**在第一个完全不碰 `[zd, zg]` 的段处封闭**，`end_idx = positions[j-1]`。
因此 `src/chanlun/chan/signal.py` 的 `_third_kind` 唯一取用的回试段
`segs[p.end_idx + 1]` **就是**离开中枢后的第一次回抽（转述），结构上不可能取到第二次。
`find_pivots` 里紧跟的 `i = j` 又保证相邻中枢的 `end_idx` 严格递增，
同一次离开不会重复产出。

**已作废的度量**：曾统计中枢组内部在最后一段之前是否已有段越过 ZG（转述），
40 只票 / 64 个第三类信号命中 50 个（78%），一度判为偏离。
但这是**中枢延伸的正常形态** —— 由中心定理一，组内同向段只要 `dn <= ZG`
就仍在延伸，`gn` 完全可以远高于 ZG（实测 `600537` 的 ZG=5.4、组内某段最高 8.79）。
该指标测量不到「第一次」这件事。

## 判据二：容忍度 = 原文空白项，属工程口径

四课原文一致使用「不跌破」「不重新回到」「不回到」，
**没有任何允许小幅回到中枢的措辞**。
⇒ 容忍度 `tol` 无原文依据，只能在非严格模式生效，
且必须在代码注释、本条目、UI 文案三处标注为**工程口径**。

## 判据三：容忍度只可能在**被段数上限截断**的中枢上翻结论

`find_pivots` 的延伸循环会把**任何**仍与 `[zd, zg]` 重叠的后续段并进中枢组
（`_overlaps` 是闭区间 `seg.low <= zg and seg.high >= zd`），
只有 `MAX_SEGMENTS = 8` 先到，回试段才会「重叠却没被并进去」：

- 中枢组没满 8 段：回试段一旦回到 `[zd, zg]` 里就被并进中枢组，`end_idx` 后移，
  `_third_kind` 取到的「回试段」随之变成更后面那一段 —— 穿透型回试**根本不会**
  作为第三类候选出现，`tol` 取多大都翻不了结论；
- 中枢组已满 8 段：截断把回试段留在组外，`back.low` 落在 `(zg - tol, zg]` 时
  严格模式不出、非严格模式出。

⇒ `THIRD_TOL` 是一个**只在少数被截断的中枢上**才起作用的参数，
这一点必须写进 `_third_kind` 的注释（已写）。

**实测命中率**（2026-10-02，`data/day` 全市场，可复现）：

```python
import random
from pathlib import Path
import pandas as pd
from chanlun.chan.engine import ChanEngine
from chanlun.chan.signal import SignalMode, _third_kind, find_signals

pool = []
for pref in ("sh", "sz"):                        # bj 目录 0 个文件
    for f in sorted(Path("data/day", pref).glob("*.parquet")):
        df = pd.read_parquet(f)
        if len(df) >= 250:                       # 池子共 5327 只
            pool.append((f"{pref}.{f.stem}", df.drop(columns=["code"], errors="ignore")))
pool.sort(key=lambda x: x[0])
random.seed(7)
n_s = n_l = diff = 0
for code, bars in random.sample(pool, 60):       # 抽 60 只
    snap = ChanEngine(code, "day", signal_fn=find_signals, level="day").full(bars)
    segs, pivs = list(snap.segments), list(snap.pivots)
    s = _third_kind(segs, pivs, "day", SignalMode.STRICT)
    l = _third_kind(segs, pivs, "day", SignalMode.LOOSE)
    n_s, n_l = n_s + len(s), n_l + len(l)
    if len(s) != len(l):
        diff += 1
        for x in l:
            p = next(q for q in pivs if q.idx == x.pivot_idx)
            back = segs[p.end_idx + 1]
            over = (p.zg - back.low) / (p.zg - p.zd)
            print(f"{code} {x.kind.value} {x.ts} 越界比例={over:.4f} "
                  f"中枢段数={p.segment_count} 中枢高度={p.zg - p.zd:.3f}")
print(f"第三类：严格 {n_s} / 非严格 {n_l}；计数不同的票 {diff}/60")
```

跑法：`cd /Users/zzz/workspace/chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python <上面这段>`。

实测输出：

```
sh.600850 b3 2023-08-25 越界比例=0.0861 中枢段数=8 中枢高度=2.637
第三类：严格 63 / 非严格 64；计数不同的票 1/60
```

即：第三类买卖点计数不同的票只有 **1/60 ≈ 1.7%**；
唯一被容忍度救回来的那个信号，回试段进入中枢的深度是中枢高度的 **0.0861**
（< `THIRD_TOL = 0.1`，刚好落在容忍区间内），且它的中枢 `segment_count == 8`
**正好被段数上限截断** —— 与上面的机制一致。
（同一批 60 只票上，全部买卖点是严格 68 / 非严格 642：容忍度不是
「非严格模式多出来的信号」的主要来源，主要来源是 `pb`/`ps` 与第 27 课入口。）

## 被否决的替代方案

- **把容忍度写进严格模式**：违反第 20/24/32/33 课，否决。
- **用中枢延伸后允许后续回抽来放宽「必须是第一次」**：实测为空操作
  （见判据一）。唯一例外是被 `MAX_SEGMENTS = 8` 截断的中枢 —— 那里的回试段
  本该被并进中枢组，容忍度确实会翻结论；但那是「容忍度」这一条（判据三），
  不是「第二次回抽」的放宽，不能拿它给「必须是第一次」开后门。
- **把严格判据改成 `>=`（贴合「不跌破」的字面）**：字面上「其低点不跌破ZG」
  含等号，现行代码是 `back.low > p.zg`，即比原文更严一点点。但严格模式有
  「与 Task 5 提交逐字节一致」的硬约束（R1 裁决 G），改代码会动到已有信号，
  故本轮只把文档从 `>=` 更正为代码实际的 `>`，等号归属记为**已知偏差**，
  留待将来单独论证（D-35 变更历史）。
