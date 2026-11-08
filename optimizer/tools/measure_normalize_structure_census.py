#!/usr/bin/env python
"""全库「结构不可能」普查：`normalize` 的两条新判据在**存量数据**上各命中多少。

## 判据（对应 F-2 / D-46）

`src/chanlun/data/types.py` 的 `normalize` 在原有「价格必须为正」之外追加两档：

- **倒挂**：`high < low`。这类行的价格区间是空的，`chan/fractal.py` 的分型四条件
  与 `chan/include.py` 的包含关系判定都建立在区间比较上，倒挂会进到合并K线里，
  所以**剔除**（与非正价格同一处置）。
- **越界**：`open` 或 `close` 出了 `[low, high]`。`open` 完全不参与任何判据；
  `close` 不参与分型/笔/线段/中枢的极值比较，但会进 MACD（`chan/signal.py:202`、
  `chan/divergence.py:357`）⇒ 影响背驰。剔除会**改变结构**（少一根K线就重排包含
  处理与分型），代价比「一根 close 偏一点」大，所以**只警告、保留**。

## 口径（必须写清，否则数字不可比）

- 读盘用 `pandas.read_parquet` **直读**，**不过** `store.read` —— 后者会调
  `normalize`，那就等于拿修复后的判据去看修复后的数据，什么都测不到。
- 判据用**严格小于**（`high < low`），所以一字板 `high == low` 不计入倒挂。
- 行数按**磁盘上的原始行**数，不做任何去重/排序。
- `open`/`close` 越界与倒挂**互斥**统计：倒挂行同时也会「越界」，先剔倒挂再数越界。

## 跑法

```bash
cd <本项目根目录>
../.venv-chanlun/bin/python optimizer/tools/measure_normalize_structure_census.py
# 只看某一批周期：
../.venv-chanlun/bin/python optimizer/tools/measure_normalize_structure_census.py --periods day
```

脚本**只读**：不写任何 parquet、不动 `sync_state`、不改配置。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

try:
    ROOT = Path(__file__).resolve().parents[2]
except NameError:  # 交互式粘贴
    ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from chanlun.data.store import data_root  # noqa: E402

PRICE_COLS = ["open", "high", "low", "close"]
MAX_EXAMPLES = 12


def main() -> int:
    ap = argparse.ArgumentParser(description="全库结构不可能行普查（只读）")
    ap.add_argument("--periods", nargs="*", default=None,
                    help="只扫这些周期目录（默认全扫）")
    args = ap.parse_args()

    root = data_root()
    files = sorted(root.rglob("*.parquet"))
    if args.periods:
        want = {str(p) for p in args.periods}
        files = [f for f in files if f.relative_to(root).parts[0] in want]
    if not files:
        print(f"❌ {root} 下没有 parquet —— 0 结果必须先验证：目录对不对？")
        return 2
    print(f"扫描目录: {root}")
    print(f"parquet 文件: {len(files)}")

    total_rows = 0
    nonpos_rows = 0
    upside_down_rows = 0
    oob_open_rows = 0
    oob_close_rows = 0
    bad_files: list[str] = []
    hit_files: Counter[str] = Counter()
    upside_files: list[tuple[str, str, Path]] = []
    examples: dict[str, list[str]] = {"nonpos": [], "upside_down": [], "oob_open": [],
                                     "oob_close": []}
    idem_bad: list[str] = []

    for path in files:
        rel = path.relative_to(root)
        period, market, name = rel.parts[0], rel.parts[1], path.stem
        code = f"{market}.{name}"
        try:
            df = pd.read_parquet(path, engine="pyarrow")
        except Exception as exc:  # noqa: BLE001
            bad_files.append(f"{rel}: {exc}")
            continue
        if len(df) == 0:
            continue
        total_rows += len(df)
        for col in PRICE_COLS:
            if col not in df.columns:
                df[col] = 0.0

        nonpos = (df[PRICE_COLS] <= 0).any(axis=1)
        rest = df[~nonpos]
        upside = rest["high"] < rest["low"]
        ok = rest[~upside]
        oob_open = (ok["open"] > ok["high"]) | (ok["open"] < ok["low"])
        oob_close = (ok["close"] > ok["high"]) | (ok["close"] < ok["low"])

        nonpos_rows += int(nonpos.sum())
        upside_down_rows += int(upside.sum())
        oob_open_rows += int(oob_open.sum())
        oob_close_rows += int(oob_close.sum())
        # ★ 掩码必须配它自己的那一层帧：`nonpos` 是 df 上的、`upside` 是 rest 上的、
        #   越界是 ok 上的 —— 拿 df 的掩码去索引 rest 会被 pandas 判为不可对齐。
        for kind, frame, mask in (("nonpos", df, nonpos),
                                  ("upside_down", rest, upside),
                                  ("oob_open", ok, oob_open),
                                  ("oob_close", ok, oob_close)):
            n = int(mask.sum())
            if n:
                hit_files[f"{kind}:{period}"] += 1
                if kind == "upside_down":
                    upside_files.append((code, period, path))
                for _, row in frame[mask].head(2).iterrows():
                    if len(examples[kind]) < MAX_EXAMPLES:
                        examples[kind].append(
                            f"{code} {period} {row['ts']} "
                            f"o={row['open']:.4g} h={row['high']:.4g} "
                            f"l={row['low']:.4g} c={row['close']:.4g}")

        # 幂等：再 normalize 一次行数不该再变（对新判据而言，第一次就已剔除干净）
        from chanlun.data.types import normalize
        once = normalize(df)
        twice = normalize(once)
        if len(once) != len(twice):
            idem_bad.append(f"{rel}: {len(once)} → {len(twice)}")

    print(f"读取失败: {len(bad_files)}")
    for b in bad_files[:5]:
        print(f"    {b}")
    print()
    print(f"磁盘原始行数合计: {total_rows}")
    print(f"  非正价格行（现有判据；写库时已剔除，磁盘上应为 0）: {nonpos_rows}")
    print(f"  high < low 倒挂行（新判据 ⇒ 剔除）: {upside_down_rows}")
    print(f"  open 越出 [low,high]（新判据 ⇒ 保留仅警告）: {oob_open_rows}")
    print(f"  close 越出 [low,high]（新判据 ⇒ 保留仅警告）: {oob_close_rows}")
    for kind, label in (("nonpos", "非正价格"), ("upside_down", "倒挂"),
                        ("oob_open", "open 越界"), ("oob_close", "close 越界")):
        if examples[kind]:
            print(f"\n{label} 样例（最多 {MAX_EXAMPLES} 条）:")
            for e in examples[kind]:
                print(f"    {e}")
    if hit_files:
        print("\n命中文件数（按 桶:周期）:")
        for k, v in sorted(hit_files.items()):
            print(f"    {k}: {v}")
    print()
    print("=== 幂等检查（normalize 两次，行数不该再变）===")
    print(f"不一致文件: {len(idem_bad)}")
    for b in idem_bad[:5]:
        print(f"    {b}")
    print()
    print("=== 结论口径 ===")
    if upside_down_rows == 0 and oob_open_rows == 0 and oob_close_rows == 0:
        print("存量数据一条都没命中 ⇒ 本次修复对现有库是 **no-op**（预防性护栏，")
        print("不是修掉了一个正在发生的错）。按 §5「证据质量规则」如实记为 inconclusive，")
        print("不许记成「验证通过」。")
    else:
        print(f"存量数据命中：倒挂 {upside_down_rows} 行（会被剔除）、"
              f"越界 {oob_open_rows + oob_close_rows} 行（保留）。")

    if upside_files:
        structure_impact(upside_files)
    return 0


def structure_impact(upside_files: list[tuple[str, str, Path]]) -> None:
    """倒挂行到底 inert 不 inert：同一份帧，剔除前 / 剔除后各算一次结构。

    修复前的帧怎么重建（**这是本探针唯一一处需要论证的地方**）：
    修复前的 `normalize` 只有「≤0 剔除 + `drop_duplicates` + 排序」三步，
    而倒挂行既不是 ≤0、`ts` 也不重复 ⇒ 它必然活过那三步。
    所以

        修复前输出 ≡ 修复后输出 + 把倒挂行按 `ts` 插回去

    两边行数必须正好差「倒挂行数」，差得不对就报错退出，绝不猜。
    """
    from chanlun.chan.engine import ChanEngine
    from chanlun.chan.include import merge_bars
    from chanlun.data.types import normalize

    print()
    print("=== 倒挂行对结构的影响（证明这 %d 行不是 inert）===" % len(upside_files))
    print()
    print("口径 A（机制层）：`chan/include.py::merge_bars` 直读 high/low，**不经过**")
    print("  normalize**，所以「倒挂行会变成一根 high<low 的合并K线」在修复前后都能实测。")
    print("口径 B（引擎层）：`ChanEngine.full()` 入口就是 `normalize`，修复之后那条路")
    print("  已经被关掉了。修复前的帧按「修复后帧 + 倒挂行按 ts 插回」重建（见函数 docstring），")
    print("  再直接调 `ChanEngine._compute()` 绕过 normalize 重算，两边逐项对比。")
    bad_total = 0
    for code, period, path in upside_files:
        raw = pd.read_parquet(path, engine="pyarrow")   # 磁盘原样，倒挂还在
        upside = raw["high"] < raw["low"]
        after = normalize(raw)                          # 修复后：倒挂已被剔除
        pre = (
            pd.concat([after, raw.loc[upside]])
            .sort_values("ts", kind="stable")
            .reset_index(drop=True)
        )
        if len(pre) - len(after) != int(upside.sum()):
            raise SystemExit(
                f"中止：{code} {period} 重建的修复前帧与修复后帧相差 "
                f"{len(pre) - len(after)} 行，期望 {int(upside.sum())} 行"
            )
        engine = ChanEngine(code, period)
        snap_pre = engine._compute(pre, version=1)      # 绕过 normalize = 修复前行为
        snap_after = engine.full(raw)                   # 生产路径 = 修复后行为
        merged_raw = merge_bars(raw)
        merged_after = merge_bars(after)
        bad_raw = [m for m in merged_raw if m.high < m.low]
        bad_after = [m for m in merged_after if m.high < m.low]
        bad_total += len(bad_raw)
        print(f"\n{code} {period}（{path.relative_to(path.parents[2])}）")
        print(f"  磁盘行数 {len(raw)} → 剔除倒挂后 {len(after)}"
              f"（剔除 {len(raw) - len(after)} 行）")
        print(f"  口径 A  merge_bars(磁盘原样)   合并K线 {len(merged_raw):>5}"
              f" / 其中 high<low {len(bad_raw)} 根")
        for m in bad_raw[:3]:
            print(f"      idx={m.idx} ts={m.ts} high={m.high:.6g} low={m.low:.6g}")
        print(f"  口径 A  merge_bars(修复后)     合并K线 {len(merged_after):>5}"
              f" / 其中 high<low {len(bad_after)} 根")
        print(f"  口径 B  引擎(修复前)  合并K线 {len(snap_pre.merged):>5}"
              f" / 分型 {len(snap_pre.fractals):>5} / 笔 {len(snap_pre.strokes):>4}"
              f" / 线段 {len(snap_pre.segments):>3} / 中枢 {len(snap_pre.pivots):>3}")
        print(f"  口径 B  引擎(修复后)  合并K线 {len(snap_after.merged):>5}"
              f" / 分型 {len(snap_after.fractals):>5} / 笔 {len(snap_after.strokes):>4}"
              f" / 线段 {len(snap_after.segments):>3} / 中枢 {len(snap_after.pivots):>3}")
        changed = [
            name
            for name, a, b in (
                ("合并K线", snap_pre.merged, snap_after.merged),
                ("分型", snap_pre.fractals, snap_after.fractals),
                ("笔", snap_pre.strokes, snap_after.strokes),
                ("线段", snap_pre.segments, snap_after.segments),
                ("中枢", snap_pre.pivots, snap_after.pivots),
            )
            if len(a) != len(b)
        ]
        print(f"  口径 B  有变化的层：{'、'.join(changed) if changed else '（无）'}")
    print(f"\n倒挂行在 merge_bars 下产出 high<low 合并K线的总数：{bad_total} 根")



if __name__ == "__main__":
    raise SystemExit(main())
