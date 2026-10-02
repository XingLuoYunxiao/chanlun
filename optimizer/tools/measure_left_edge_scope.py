"""量一量「左端起点」这条口径在全样本上的影响（round-020 的探针）。

第 67 课只说「一切同一级别图上的走势都可以唯一地划分为线段的连接」，没说**被截断的
数据窗口左端**该怎么起段 —— 这是原文空白项，只能取工程判据。两种口径：

  旧：从第 0 笔起，取**第一个可行起点**（`best(s)[1] is not None`）。
      代价是把「数据商从哪一天开始给数据」当成线段边界。`600180` 日线从 2021-01-04
      开始（该股 1998 年上市），于是全史 114 笔只划出 5 段、首段 **73 笔**；
      `sz.399001` 从 2020-01-01 截断 → 首段 **81 笔**（全史只有 47）；
      `603777` 同样截断 → 首段 **87 笔**（全史只有 25）。即同一个走势换个取数窗口
      就塌成一条线段，与用户报告的深证成指同一机制。
  新：取**还能确认最多线段**的起点（平局取更早），前缀交给 TENTATIVE 前导段。

判据：新口径必须消掉全部「怪物段」，且前导段不得反过来吞掉大段笔。

用法：`python optimizer/tools/measure_left_edge_scope.py [--sample N]`
"""
import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun.chan import segment as S  # noqa: E402
from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.data import store  # noqa: E402

#: 怪物段阈值：一条线段超过这么多笔就该怀疑左端把数据边界当成了线段边界。
MONSTER = 80

SHORT = (
    "sz.300760", "sh.600000", "sh.601088", "sz.300059", "sz.300750", "sh.600519",
    "sz.002415", "sh.600887", "sh.600276", "sh.600900", "sz.000651", "sz.000725",
    "sh.600030", "sh.600036", "sh.601012", "sh.601166", "sh.601318", "sh.601899",
    "sz.000001", "sz.000333", "sz.000858", "sz.002594", "sz.002714", "sz.300124",
)
FULL = ("sz.399001", "sz.399006", "sh.000001", "sh.000905", "sh.000300")
INTRADAY = (("sh.600759", "5"), ("sh.600759", "30"), ("sh.600519", "30"))
#: 数据窗口起点明显晚于上市日的票 —— 左端口径在这里最容易出怪物段。
LATE_START = ("600180", "603777", "603799", "600654")


def _cases(sample: int) -> list[tuple[str, str, object]]:
    cases: list[tuple[str, str, object]] = [
        (c, "day", store.read(c, "day", start="2020-01-01")) for c in SHORT
    ]
    cases += [(c, "day", store.read(c, "day")) for c in FULL]
    cases += [(c, p, store.read(c, p)) for c, p in INTRADAY]
    cases += [(c, "day", store.read(c, "day")) for c in LATE_START]
    # 截断窗口：同一走势只换取数起点，看结构会不会塌。
    cases += [(c, "day", store.read(c, "day", start="2020-01-01"))
              for c in ("sz.399001", "603777", "600180")]
    if sample:
        root = store.data_root() / "day" / "sh"
        codes = sorted(p.stem for p in root.glob("*.parquet"))
        codes = random.Random(20261001).sample(codes, min(sample, len(codes)))
        cases += [(c, "day", store.read(c, "day", start="2020-01-01")) for c in codes]
    return [c for c in cases if len(c[2]) > 0]


def _dp(pol, strokes):
    """`classify` 的递推本体，两种口径共用。"""
    n = len(strokes)
    memo: dict[int, tuple[int, object]] = {}

    def best(s: int):
        if s + pol.min_strokes > n:
            return 0, None
        if s in memo:
            return memo[s]
        res: tuple[int, object] = (0, None)
        for br in pol.candidates(strokes, s):
            count, _ = best(br.stroke_idx + 1)
            if count + 1 > res[0]:
                res = (count + 1, br)
        memo[s] = res
        return res

    return n, best


def _walk(n: int, best, start: int) -> list:
    breaks: list = []
    while start < n:
        _, br = best(start)
        if br is None:
            break
        breaks.append(br)
        start = br.stroke_idx + 1
    return breaks


def _classify_first(self, strokes):
    """旧口径：第一个可行起点。"""
    n = len(strokes)
    if n < self.min_strokes:
        return []
    n, best = _dp(self, strokes)
    start = 0
    while start < n and best(start)[1] is None:
        start += 1
    return _walk(n, best, start)


def _classify_argmax(self, strokes):
    """新口径：还能确认最多线段的起点（平局取更早），带单调上界剪枝。"""
    n = len(strokes)
    if n < self.min_strokes:
        return []
    n, best = _dp(self, strokes)
    start, best_count = 0, 0
    for s in range(n):
        if best_count >= (n - s) // self.min_strokes:
            break
        count = best(s)[0]
        if count > best_count:
            start, best_count = s, count
    return _walk(n, best, start)


def stats(code: str, period: str, bars) -> dict:
    snap = ChanEngine(code, period).full(bars)
    segs = snap.segments
    lead = segs[0] if segs else None
    return {
        "bars": len(bars),
        "strokes": len(snap.strokes),
        "segs": len(segs),
        "confirmed": sum(1 for s in segs if s.status.name == "CONFIRMED"),
        "pivots": len(snap.pivots),
        "maxseg": max((s.stroke_count for s in segs), default=0),
        "lead": lead.stroke_count if lead is not None and lead.status.name == "TENTATIVE" else 0,
        "first": segs[0].start_stroke_idx if segs else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="额外随机抽取 N 只沪市日线")
    args = ap.parse_args()

    orig = S.Lesson6768Policy.classify
    print(f"{'code':11s} {'周期':>4s} {'bars':>6s} {'笔':>5s} | "
          f"{'段 旧→新':>12s} {'最长段 旧→新':>13s} {'中枢':>7s} | 新前导")
    tot = {"segs_o": 0, "segs_n": 0, "mon_o": 0, "mon_n": 0, "lead_n": 0, "lead_o": 0}
    worst: list[tuple[int, str, str, int, int]] = []
    for code, period, bars in _cases(args.sample):
        S.Lesson6768Policy.classify = _classify_first
        old = stats(code, period, bars)
        S.Lesson6768Policy.classify = _classify_argmax
        new = stats(code, period, bars)
        S.Lesson6768Policy.classify = orig
        tot["segs_o"] += old["segs"]
        tot["segs_n"] += new["segs"]
        tot["mon_o"] += old["maxseg"] > MONSTER
        tot["mon_n"] += new["maxseg"] > MONSTER
        tot["lead_o"] += old["lead"]
        tot["lead_n"] += new["lead"]
        worst.append((old["maxseg"] - new["maxseg"], code, period, old["maxseg"], new["maxseg"]))
        print(f"{code:11s} {period:>4s} {old['bars']:6d} {old['strokes']:5d} | "
              f"{old['segs']:5d}→{new['segs']:<6d} {old['maxseg']:5d}→{new['maxseg']:<7d} "
              f"{old['pivots']:2d}→{new['pivots']:<4d} | {old['lead']}→{new['lead']}")

    print(f"\n线段合计：{tot['segs_o']} → {tot['segs_n']}")
    print(f"怪物段（最长段 > {MONSTER} 笔）样本数：{tot['mon_o']} → {tot['mon_n']}")
    print(f"前导 TENTATIVE 段藏笔合计：{tot['lead_o']} → {tot['lead_n']}")
    print("最长段收缩最多的 8 个样本（旧→新）：")
    for d, code, period, a, b in sorted(worst, reverse=True)[:8]:
        print(f"  {code} {period}: {a} → {b} 笔（-{d}）")
    if tot["mon_n"]:
        print(f"\n[失败] 新口径下仍有 {tot['mon_n']} 个样本存在怪物段。")
        return 1
    if tot["lead_n"] > tot["segs_n"]:
        print(f"\n[失败] 前导段藏笔（{tot['lead_n']}）超过线段总数（{tot['segs_n']}），"
              "等于把问题从前段搬到前导段。")
        return 1
    print("\n[通过] 新口径消掉全部怪物段，且前导段没有反过来吞掉大段笔。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
