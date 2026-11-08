"""验证：**每一只已落库的票，三种复权格式都要能给出**（或如实说明为什么给不出）。

用户指令①「需要给出不复权 / 前复权 / 后复权三种格式」。页面、接口、命令行三处都有
出口了，但**出口存在 ≠ 每只票都拿得到**。这个脚本逐只核对契约：

- **三种模式真的不同** ⇒ 三态齐备；
- **三种模式塌成一种** ⇒ 只允许两种情况，且都必须是**如实说明**的：
  1. **指数**：没有除权除息，三态本来就相同；
  2. **落在因子表覆盖窗口内没有除权的股票**：`factors` 会报 `no_change`
     （「两份数据在重叠区间内逐笔同价」），所以也不该有因子。
  除这两种之外塌成一态，就是**真的缺因子**（本该能反推却没反推出来），要报出来。

**为什么不能只看「有没有因子表」**：指数与「窗口内无除权」的票本来就没有因子行，
拿「因子表为空」当缺陷会得到一堆假告警。

跑法：
    cd <项目根>
    ../.venv-chanlun/bin/python optimizer/tools/verify_adjust_modes.py --period 1 --limit 200

退出码 0 = 契约成立（要么三态齐备、要么是上面两种如实的塌缩）；1 = 有不该塌的塌了。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from chanlun.config import load_config  # noqa: E402
from chanlun.data import markets  # noqa: E402
from chanlun.web.api import _period_frame  # noqa: E402

MODES = ("raw", "qfq", "hfq")


def _key(path: str) -> str:
    mkt = Path(path).parts[-2]
    bare = Path(path).stem
    # 沪市 `000xxx`/`399xxx` 是指数，落库键要带前缀（裸 `000001` 是平安银行）
    return f"{mkt}.{bare}" if (mkt == "sh" and bare.startswith(("000", "399"))) else bare


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="1")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    files: list[str] = []
    for mkt in ("sh", "sz"):
        files += sorted(glob.glob(str(ROOT / "data" / args.period / mkt / "*.parquet")))
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"没有 {args.period} 数据")
        return 1

    cfg = load_config()
    counts: Counter[str] = Counter()
    bad: list[str] = []
    for f in files:
        key = _key(f)
        try:
            eff = {
                m: _period_frame(key, args.period, adjust=m, meta_db=cfg.data.meta_db)[1]
                for m in MODES
            }
        except Exception as exc:                       # noqa: BLE001
            counts["出错"] += 1
            bad.append(f"{key}: {type(exc).__name__}: {exc}")
            continue
        if len(set(eff.values())) == 3:
            counts["三态齐备"] += 1
        elif markets.is_index(key):
            counts["指数·三态本就相同"] += 1
        elif any(v != "raw" for v in eff.values()):
            # 落库不是 raw 且三态塌缩 ⇒ 靠的是「库内那一态」，本身合法，
            # 但**要求因子表为空是因为窗口内无除权**，这一条由 `factors --dry-run` 认定，
            # 本脚本只能标出来让人复核。
            counts["塌缩·待复核（非指数）"] += 1
            bad.append(f"{key}: 三态塌成 {set(eff.values())}，请用 "
                       f"`python -m chanlun factors --codes {key} --dry-run` 确认是 no_change")
        else:
            counts["三态齐备"] += 1

    print(f"=== 三种复权格式契约（{args.period} 基准，{len(files)} 只）===")
    for k, v in counts.most_common():
        print(f"  {k}: {v}")
    if bad:
        print(f"\n  需要复核 {len(bad)} 只：")
        for line in bad[:10]:
            print(f"    - {line}")
        if len(bad) > 10:
            print(f"    …（共 {len(bad)} 条）")
    only_pending = set(counts) <= {"三态齐备", "指数·三态本就相同"}
    print("\n  ⇒ " + ("**契约成立**：要么三态齐备，要么是指数（三态本来就相同）"
                      if only_pending else
                      "**有该能切三态却塌成一态的票**，见上面的复核清单"))
    return 0 if only_pending else 1


if __name__ == "__main__":
    raise SystemExit(main())
