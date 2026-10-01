"""优化器代理：一轮 = 发现 → 取证 → 出补丁 → 同一口径量前后 → 记录。

这里实现的是**提案模式**的唯一执行路径：

- 永远不在主干工作区里 apply 补丁。要量「打了补丁之后」，就把 ``src/``
  整份复制到一个临时影子目录，在影子里 apply，再用 ``PYTHONPATH`` 指向
  影子的 ``src`` 跑同一个测量脚本。主干一个字节都不动，连临时状态都不留。
- 前后两次测量用**同一个脚本、同一段行情、同一批股票**，只换代码来源。
  没有可比数字的一轮不允许记成 ``proposed``。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .journal import (
    CIRCUIT_BREAK,
    INCONCLUSIVE,
    PROPOSED,
    REJECTED,
    CircuitBreaker,
    CircuitState,
    JournalEntry,
    read_all,
    write_entry,
)
from .theory import TheoryEntry, load_theory, validate_patch

#: 前后对比的固定样本：24 只主板/创业板龙头，固定窗口。
#: 换股票或换窗口就等于换了口径，前后数字不可比——所以写死在代码里。
AUDIT_CODES: tuple[str, ...] = (
    "600000", "600030", "600036", "600276", "600519", "600887",
    "600900", "601012", "601088", "601166", "601318", "601899",
    "000001", "000333", "000651", "000725", "000858", "002415",
    "002594", "002714", "300059", "300124", "300750", "300760",
)
AUDIT_START = "2020-01-01"
AUDIT_END = "2026-09-29"

#: 测量脚本：只用公开 API，所以「打补丁前」和「打补丁后」跑的是同一段逻辑。
#: 输出一行 ``__METRICS__ {json}`` 供代理解析。
AUDIT_SCRIPT = r'''
import json
import statistics

from chanlun.chan.engine import ChanEngine
from chanlun.chan.signal import find_signals, validate_signals
from chanlun.chan.trend import TrendType, classify_trends
from chanlun.chan.types import Status
from chanlun.data import store

CODES = %(codes)r
START, END = %(start)r, %(end)r


def positional_side(seg, zd, zg):
    """离开方向由**位置**决定：整段在 ZG 之上=向上离开，在 ZD 之下=向下离开。"""
    if seg.low >= zg:
        return 1
    if seg.high <= zd:
        return -1
    return 0


def main():
    metrics = {
        "bars": 0, "segments": 0, "confirmed_segments": 0, "pivots": 0,
        "trends_up": 0, "trends_down": 0, "trends_consolidation": 0,
        "signals": 0, "b1": 0, "b2": 0, "b3": 0, "s1": 0, "s2": 0, "s3": 0,
        "zero_signal_codes": 0, "signal_problems": 0,
        "stroke_max": 0, "stroke_median": 0,
    }
    mech = {
        "pivots_with_next": 0, "exit_entirely_outside": 0,
        "exit_dir_matches_position": 0, "exit_dir_opposite_position": 0,
        "trend_groups": 0, "trend_group_leave_dir_ok": 0,
        "trend_group_prev_ok": 0, "trend_group_both_ok": 0,
        "b3_trunk": 0, "b3_positional": 0,
        "pivot_max_segments": 0, "pivot_over_9": 0,
        "pivot_segment_total": 0,
    }
    detail = {"codes": {}, "missing": []}
    stock_medians = []

    for code in CODES:
        bars = store.read(code, "day", start=START, end=END)
        if bars is None or len(bars) == 0:
            detail["missing"].append(code)
            continue
        snap = ChanEngine(code, "day", signal_fn=find_signals).full(bars)
        segs = list(snap.segments)
        pivots = list(snap.pivots)
        signals = list(snap.signals)
        confirmed = [s for s in segs if s.status is Status.CONFIRMED]
        trends = classify_trends(pivots, "day")

        counts = {"b1": 0, "b2": 0, "b3": 0, "s1": 0, "s2": 0, "s3": 0}
        for sig in signals:
            key = str(getattr(sig.kind, "value", sig.kind)).lower()
            if key in counts:
                counts[key] += 1

        problems = validate_signals(signals, bars, segs)
        metrics["bars"] += len(bars)
        metrics["segments"] += len(segs)
        metrics["confirmed_segments"] += len(confirmed)
        metrics["pivots"] += len(pivots)
        metrics["signals"] += len(signals)
        metrics["signal_problems"] += len(problems)
        metrics["zero_signal_codes"] += 1 if not signals else 0
        metrics["stroke_max"] = max(metrics["stroke_max"],
                                    max((s.stroke_count for s in segs), default=0))
        if segs:
            stock_medians.append(statistics.median([s.stroke_count for s in segs]))
        for key, value in counts.items():
            metrics[key] += value
        for t in trends:
            if t.kind is TrendType.UP:
                metrics["trends_up"] += 1
            elif t.kind is TrendType.DOWN:
                metrics["trends_down"] += 1
            else:
                metrics["trends_consolidation"] += 1

        # ---- 机制计数：不改主干也能算，用来定位「为什么是 0」 ----
        spans = sorted(p.segment_count for p in pivots)
        if spans:
            mech["pivot_max_segments"] = max(mech["pivot_max_segments"], spans[-1])
            mech["pivot_over_9"] += sum(1 for s in spans if s > 9)
            mech["pivot_segment_total"] += sum(spans)
        for p in pivots:
            if p.end_idx + 1 >= len(segs):
                continue
            mech["pivots_with_next"] += 1
            cand = segs[p.end_idx + 1]
            side = positional_side(cand, p.zd, p.zg)
            if side:
                mech["exit_entirely_outside"] += 1
            if side and cand.direction == side:
                mech["exit_dir_matches_position"] += 1
            if side and cand.direction != side:
                mech["exit_dir_opposite_position"] += 1
            # 主干口径：离开段=end_idx+1，回试=end_idx+2
            if p.end_idx + 2 < len(segs):
                leave, back = cand, segs[p.end_idx + 2]
                if leave.direction == 1 and leave.low > p.zg and \
                        back.direction == -1 and back.low > p.zg:
                    mech["b3_trunk"] += 1
                elif leave.direction == -1 and leave.high < p.zd and \
                        back.direction == 1 and back.high < p.zd:
                    mech["b3_trunk"] += 1
            # 候选口径：离开段=end_idx（结束在区间外的那一段），回试=end_idx+1
            ex = segs[p.end_idx]
            if ex.high > p.zg and cand.low > p.zg and cand.direction == -1:
                mech["b3_positional"] += 1
            elif ex.low < p.zd and cand.high < p.zd and cand.direction == 1:
                mech["b3_positional"] += 1

        down = [p for t in trends if t.kind is TrendType.DOWN
                for p in pivots[t.start_idx:t.end_idx + 1]]
        up = [p for t in trends if t.kind is TrendType.UP
              for p in pivots[t.start_idx:t.end_idx + 1]]
        for want, group in ((-1, down), (1, up)):
            if len(group) < 2:
                continue
            mech["trend_groups"] += 1
            for k, p in enumerate(group):
                if p.end_idx + 1 >= len(segs):
                    continue
                leave = segs[p.end_idx + 1]
                leave_ok = leave.direction == want
                mech["trend_group_leave_dir_ok"] += 1 if leave_ok else 0
                prev_ok = False
                if k > 0 and group[k - 1].end_idx + 1 < len(segs):
                    prev = segs[group[k - 1].end_idx + 1]
                    prev_ok = prev.direction == want
                mech["trend_group_prev_ok"] += 1 if prev_ok else 0
                mech["trend_group_both_ok"] += 1 if (leave_ok and prev_ok) else 0

        detail["codes"][code] = {
            "bars": len(bars), "segments": len(segs), "pivots": len(pivots),
            "signals": len(signals), "problems": problems[:3],
        }

    if stock_medians:
        metrics["stroke_median"] = statistics.median(stock_medians)
    print("__METRICS__" + json.dumps(
        {"metrics": metrics, "mech": mech, "detail": detail}, ensure_ascii=False))


main()
''' % {"codes": list(AUDIT_CODES), "start": AUDIT_START, "end": AUDIT_END}


@dataclass(frozen=True)
class Probe:
    """一轮要做的事：发现 + 证据 + （可选）补丁 + 测量脚本。"""

    rid: str
    kind: str
    finding: str
    evidence: str
    theory_id: str | None = None
    patch_text: str | None = None
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    measure_script: str | None = None


class Optimizer:
    """提案模式的优化循环。``root`` 是 ``chanlun/`` 项目根。"""

    def __init__(
        self,
        root: str | Path,
        *,
        propose_only: bool = True,
        breaker: CircuitBreaker | None = None,
        measure: bool = False,
        timeout: int = 900,
    ) -> None:
        if not propose_only:
            raise ValueError(
                "优化器只支持提案模式：它不会、也不允许修改主干算法代码。"
                "要落地补丁请人工 review 后自己 git apply。"
            )
        self.root = Path(root).resolve()
        self.theory_dir = self.root / "optimizer" / "theory"
        self.journal_dir = self.root / "optimizer" / "journal"
        self.patches_dir = self.root / "optimizer" / "patches"
        self.breaker = breaker or CircuitBreaker()
        self.measure_shadow = measure
        self.timeout = timeout
        self.theory: dict[str, TheoryEntry] = load_theory(self.theory_dir)
        self.state: CircuitState = self.breaker.evaluate(read_all(self.journal_dir))

    # ------------------------------------------------------------- 测量

    def freeze_data(self) -> tuple[Path, dict[str, Any]]:
        """把这一轮要读的行情复制成一份**冻结快照**。

        为什么非冻不可：``data/`` 是个活仓库，全市场同步随时在删了重建
        （实测同一把尺子在两条命令之间就从 24/24 只票掉到 22/24，bars
        39240 → 35970）。如果 before 和 after 各读一次活仓库，量出来的差
        可能根本不是补丁的差，而是仓库换了一半。冻结之后，一轮里的两次
        测量读的是同一份字节，「同口径」才是真的而不只是嘴上说的。

        返回 ``(快照目录, 口径描述)``；``口径`` 里带 ``sha256``，写进 journal
        后任何人都能验证这一轮到底量的是哪份数据。
        """
        from ..data import store as _store

        frozen = Path(tempfile.mkdtemp(prefix="chanlun-frozen-"))
        data_src = self.root / "data"
        data_dst = frozen / "data"
        copied: list[str] = []
        missing: list[str] = []
        for code in AUDIT_CODES:
            rel = Path("day") / _store.market_of(code) / f"{code}.parquet"
            src = data_src / rel
            if src.is_file():
                (data_dst / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, data_dst / rel)
                copied.append(code)
            else:
                missing.append(code)
        # 校验报告是 G5a 的读数对象；meta.db 是代码清单的来源，一起冻。
        for extra in ("quality_report.json", "meta.db"):
            if (data_src / extra).is_file():
                shutil.copy2(data_src / extra, data_dst / extra)
        digest = hashlib.sha256()
        for rel in sorted(p.relative_to(data_dst) for p in data_dst.rglob("*") if p.is_file()):
            digest.update(str(rel).encode("utf-8"))
            digest.update(hashlib.sha256((data_dst / rel).read_bytes()).digest())
        basis = {
            "copied": len(copied),
            "expected": len(AUDIT_CODES),
            "missing": missing,
            "sha256": digest.hexdigest()[:16],
            "frozen_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        (frozen / "_basis.json").write_text(
            json.dumps(basis, ensure_ascii=False), encoding="utf-8"
        )
        return frozen, basis

    def _basis_of(self, frozen: Path) -> dict[str, Any]:
        """读回冻结快照的口径描述（``freeze_data`` 写在里的那份）。"""
        try:
            return json.loads((frozen / "_basis.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"copied": 0, "expected": len(AUDIT_CODES), "missing": [], "sha256": "?"}

    def _shadow(self, patch_text: str | None, frozen: Path | None = None) -> Path:
        """把 ``src/`` 复制成一个独立可跑的项目影子。

        ``config.PROJECT_ROOT`` 是从 ``__file__`` 推出来的，所以影子里必须
        有 ``config.toml`` 和 ``data/``。给了 ``frozen`` 就链到那份冻结快照，
        没给才链活仓库。
        """
        tmp = Path(tempfile.mkdtemp(prefix="chanlun-shadow-"))
        shutil.copytree(self.root / "src", tmp / "src")
        cfg = self.root / "config.toml"
        if cfg.exists():
            shutil.copy2(cfg, tmp / "config.toml")
        (tmp / "logs").mkdir(exist_ok=True)
        data = (frozen / "data") if frozen is not None else (self.root / "data")
        if data.exists():
            os.symlink(data, tmp / "data")
        if patch_text:
            # 必须用 cwd + 相对路径：macOS 的 /var 是指向 /private/var 的符号
            # 链接，把绝对路径交给 `git apply --directory` 会被判成「越出符号
            # 链接」而拒绝。
            (tmp / "_proposal.patch").write_text(patch_text, encoding="utf-8")
            done = subprocess.run(
                ["git", "apply", "-p1", "_proposal.patch"],
                cwd=tmp, capture_output=True, text=True,
            )
            if done.returncode != 0:
                shutil.rmtree(tmp, ignore_errors=True)
                raise RuntimeError(f"影子目录 apply 失败：{done.stderr.strip()}")
        return tmp

    def measure(
        self,
        script: str,
        patch_text: str | None = None,
        frozen: Path | None = None,
    ) -> dict[str, Any]:
        """在影子代码上跑测量脚本，返回 ``{metrics, mech, detail}``。

        ``frozen`` 给了就用那份冻结行情；没给就现冻一份（单跑 ``--audit``
        时也一样 —— 读数必须有个明确的口径，而不是「我跑的那一刻」）。
        """
        if frozen is None:
            frozen, basis = self.freeze_data()
        else:
            basis = self._basis_of(frozen)
        tmp = self._shadow(patch_text, frozen)
        try:
            env = dict(os.environ)
            env["PYTHONPATH"] = str(tmp / "src")
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            done = subprocess.run(
                [sys.executable, "-c", script],
                cwd=tmp, env=env, capture_output=True, text=True,
                timeout=self.timeout,
            )
            if done.returncode != 0:
                raise RuntimeError(
                    f"测量脚本失败（{done.returncode}）：{done.stderr.strip()[-2000:]}"
                )
            for line in reversed(done.stdout.splitlines()):
                if line.startswith("__METRICS__"):
                    result = json.loads(line[len("__METRICS__"):])
                    result["basis"] = basis
                    return result
            raise RuntimeError(f"测量脚本没有输出 __METRICS__：{done.stdout[-1000:]}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def diff_scope(self, patch_text: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """同一脚本、**同一份冻结行情**，量「打补丁前 / 打补丁后」。"""
        frozen, _ = self.freeze_data()
        try:
            before = self.measure(AUDIT_SCRIPT, None, frozen)
            after = self.measure(AUDIT_SCRIPT, patch_text, frozen)
        finally:
            shutil.rmtree(frozen, ignore_errors=True)
        return before, after

    # ------------------------------------------------------------- 单轮

    def _same_patch(self, patch_text: str) -> str | None:
        """``patches/`` 里有没有逐字相同的补丁；有就返回它的文件名。

        24x7 循环会反复回访同一批观测点。只要行情和代码没动，同一轮的 diff
        就是一模一样的字节 —— 那种重复文件不含任何新信息，只该被认出来。
        """
        if not self.patches_dir.is_dir():
            return None
        for path in sorted(self.patches_dir.glob("*.patch")):
            try:
                if path.read_text(encoding="utf-8") == patch_text:
                    return path.name
            except OSError:
                continue
        return None

    def run_round(self, round_no: int, probe: Probe) -> JournalEntry:
        verdict = (
            validate_patch(probe.patch_text, self.theory) if probe.patch_text else None
        )
        before = dict(probe.before)
        after = dict(probe.after)
        status = INCONCLUSIVE
        theory_id = probe.theory_id
        notes = probe.notes
        evidence = probe.evidence

        if probe.patch_text is not None and verdict is not None:
            if verdict.ok:
                status = PROPOSED
                theory_id = theory_id or verdict.theory_id
            else:
                status = REJECTED
                verdict_text = "；".join(verdict.reasons)
                notes = f"{notes}\n[校验拒绝] {verdict_text}".strip()
                evidence = f"{evidence}\n[校验拒绝] {verdict_text}".strip()

        # ---- 当轮实测：before/after 必须是这一轮量出来的 ----
        measured = False
        if self.measure_shadow and probe.measure_script:
            measured = True
            # 一轮一冻结：before 和 after 读同一份字节，差才是补丁的差。
            frozen, basis = self.freeze_data()
            try:
                measured_before = self.measure(probe.measure_script, None, frozen)
                measured_after = (
                    self.measure(probe.measure_script, probe.patch_text, frozen)
                    if status == PROPOSED
                    else measured_before
                )
            finally:
                shutil.rmtree(frozen, ignore_errors=True)
            before = measured_before.get("metrics", measured_before)
            after = measured_after.get("metrics", measured_after)
            note_basis = (
                f"[口径] 冻结快照 basis_sha256={basis['sha256']}"
                f"，{basis['copied']}/{basis['expected']} 只票"
            )
            if basis["missing"]:
                note_basis += (
                    f"（缺 {len(basis['missing'])} 只：{'、'.join(basis['missing'])}）"
                    "。缺票不是代码造成的，是行情仓那一刻就没有它们；"
                    "before/after 读的是同一份快照，所以差值仍然有效，"
                    "但这一轮的绝对数不能和别轮直接比。"
                )
            notes = (
                f"{notes}\n{note_basis}"
                f"\n[机制] before={json.dumps(measured_before.get('mech', {}), ensure_ascii=False)}"
                f"\n[机制] after={json.dumps(measured_after.get('mech', {}), ensure_ascii=False)}"
            ).strip()

        # ---- 不许自欺：这一轮量出来数字没动，就不许叫「提案」 ----
        # 校验通过只能说明补丁的形式合规（引了原文、给了证据），不能说明它有用。
        # 「有用」的判据是同一把尺子上的数字确实动了；没动就是 no-op，
        # 老老实实记成 inconclusive —— 否则 propossed 这个词会变成一种自我表扬。
        if measured and status == PROPOSED and dict(before) == dict(after):
            status = INCONCLUSIVE
            notes = (
                f"{notes}\n[判定] 本轮实测 before 与 after 逐项相同（no-op）："
                "数字没动就不算提案，改记 inconclusive。"
            ).strip()

        patch_file: str | None = None
        patch_sha = ""
        if status == PROPOSED and probe.patch_text is not None:
            patch_file = f"round-{round_no:03d}-{probe.rid}.patch"
            # 24x7 去重：同一份补丁（逐字相同）只落盘一次。第二圈回访同一个
            # 观测点时，行情没变、代码没变，diff 就会一模一样 —— 再写一份只是
            # 让 patches/ 每天长一截、把真正的新提案淹掉。内容**不同**才写新文件
            # （数据窗口移动导致上下文变了，那时确实是一份新提案）。
            duplicate = self._same_patch(probe.patch_text)
            if duplicate is not None:
                patch_file = duplicate
                # 只有「复用了别的轮次的补丁」才值得记一笔；如果命中的就是
                # 本轮该写的那份（内容没变，重复落盘被挡下），那是常态，不必噪声。
                if duplicate != f"round-{round_no:03d}-{probe.rid}.patch":
                    notes = (
                        f"{notes}\n[去重] 与已有补丁 {duplicate} 逐字相同，不再重复落盘。"
                    ).strip()
            else:
                self.patches_dir.mkdir(parents=True, exist_ok=True)
                (self.patches_dir / patch_file).write_text(
                    probe.patch_text, encoding="utf-8"
                )
            patch_sha = hashlib.sha256(probe.patch_text.encode("utf-8")).hexdigest()[:16]

        entry = JournalEntry(
            round=round_no,
            started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            kind=probe.kind,
            finding=probe.finding,
            evidence=evidence,
            theory_id=theory_id,
            patch_file=patch_file,
            before=before,
            after=after,
            tests=dict(probe.tests) or {"before": "skip", "after": "skip", "summary": ""},
            status=status,
            probe=probe.rid,
            notes=notes,
            patch_sha256=patch_sha,
        )

        # 熔断只看「历史 + 本轮」。同一轮次号如果磁盘上已有旧记录（人手动重跑同一轮、
        # 或进程在第 N 轮写完前崩过），必须用本轮覆盖它，而不是把它也算一遍 ——
        # 否则一个陈旧的无产出轮次会跟新一轮拼成假的「连续 3 轮」，
        # 让熔断在数据没变的情况下凭空跳闸。按轮次号去重，新记录优先。
        history = [e for e in read_all(self.journal_dir) if e.round != round_no] + [entry]
        state = self.breaker.evaluate(history)
        if state.should_break and status != CIRCUIT_BREAK:
            entry.status = CIRCUIT_BREAK
            entry.notes = f"{entry.notes}\n[熔断] {state.reason}".strip()
        self.state = state
        write_entry(self.journal_dir, entry)
        return entry

    def run(
        self, rounds: int, probes: Sequence[Probe] | None = None
    ) -> list[JournalEntry]:
        probes = list(probes if probes is not None else default_probes(self.root))
        if not probes:
            return []
        existing = read_all(self.journal_dir)
        start = (max((e.round for e in existing), default=0)) + 1
        recorded: list[JournalEntry] = []
        for offset in range(rounds):
            probe = probes[(start - 1 + offset) % len(probes)]
            entry = self.run_round(start + offset, probe)
            recorded.append(entry)
            if self.state.should_break:
                break
        return recorded


# --------------------------------------------------------------- 探针登记表

#: 十个观测点（G1–G5）对应的补丁文件名。第三项是这一轮记在日志里的**发现**，
#: 第四项是它依据的 ``theory/`` 条目（工程类为 ``None``）。补丁本体在
#: ``optimizer/patches/`` 里，是**人可读的提案**，不是自动落地。
PROBE_SPECS: tuple[tuple[str, str, str, str | None], ...] = (
    (
        "G1a",
        "theory",
        "第三类买卖点整个失效：_third_kind 拿 end_idx+1 / end_idx+2 当「离开段 / 回试段」，"
        "实测 44/57 个中枢的 end_idx+1 方向与离开方向相反（exit_dir_matches_position=0），"
        "24 只票 5 年一个买卖点都不出（zero_signal_codes=24 → 2）",
        "L20-THIRD-POINT-POSITION",
    ),
    (
        "G1b",
        "theory",
        "第一类买卖点整个失效：_entering_and_leaving 取 end_idx+1（离开之后的反向回试段）当离开段，"
        "背驰被拿去做同向比较的两段其实反向，18 个趋势组 trend_group_prev_ok=0（signals 0 → 1）",
        "L27-DIVERGENCE-UNIT,L24-MACD-AREA-ABC",
    ),
    (
        "G1c",
        "theory",
        "B3 与 S3 写成嵌套 if，一个既穿过 ZG 又穿过 ZD 的离开段会让 B3 的回试条件失败、"
        "顺带吞掉本该成立的 S3（实测 4 例：600651/002415/300750/601318，signals 40 → 44，s3 13 → 17）",
        "L20-THIRD-POINT-POSITION",
    ),
    (
        "G2a",
        "theory",
        "find_pivots 的延伸循环没有段数上限，一个中枢可以吸收到 14 段（pivot_over_9=7/57）。"
        "超过九段已是级别扩张而非延伸，继续当同一个中枢会把两个中枢吸成一个，"
        "盘整走势 6 → 9（pivot_max_segments 14 → 9）",
        "L29-PIVOT-LEVEL-EXPANSION",
    ),
    (
        "G2b",
        "theory",
        "trend.py::_direction 只看相邻中枢 zd/zg 的高低，不检查两中枢是否重叠，"
        "区间套在一起也会被判成趋势；本窗口实测为纯 no-op（10 涨/8 跌/6 盘整前后不变），"
        "拿不出「改完更好」的数字 → inconclusive",
        "L20-PIVOT-EXTENSION",
    ),
    (
        "G3a",
        "theory",
        "回试段可能是窗口右端的 TENTATIVE 线段（pivot.py 文档已注明），买点会在「回试还没走完」时发出；"
        "加一道「离开段与回试段都必须 CONFIRMED」的门后 44 → 43（b3 27 → 25）",
        "L20-THIRD-POINT-POSITION",
    ),
    (
        "G4a",
        "theory",
        "线段划分的不变量：328 根确认线段里 72 根跨 9 笔以上（最长 63 笔）、"
        "123 根走缺口确认路径（validate_problems=0）。划分标准本身没坏，但线段偏粗，"
        "缺少「背驰段在图上的位置」这一独立证据前不该动它 → inconclusive",
        "L67-SEGMENT-CHAR-SEQ",
    ),
    (
        "G4b",
        "theory",
        "第67课说走势可以「唯一地划分」，而实测 396 个起点里 299 个存在多个可行分界，"
        "当前由 DP 启发式取「确认线段最多」者平局取更早；多个分界同样合法时合成唯一解"
        "是启发式而非唯一性 → inconclusive",
        "L67-SEGMENT-CHAR-SEQ",
    ),
    (
        "G5a",
        "engineering",
        "校验口径被合并：quality_report.json 的 summary 只有 {total, ok, failed}，"
        "daily 只印「抽样 40 条，失败 0 条」，跨源校验（要联网取外部源）与"
        "前复权自洽校验（本地就能算）共用一个数字；外部源整年不可达也照样显示满分通过。"
        "__main__.py 的「数据源：baostock 前复权」还是一行硬编码字符串",
        None,
    ),
    (
        "G5b",
        "engineering",
        "带市场前缀的代码让 store.path_for 拼出不存在的文件名、read 静默返回空表："
        "store.read('sh.600000') 得 0 行而 store.read('600000') 得 38 行，"
        "把「代码写法不对」伪装成「这只票没有数据」（0 → 38 行）",
        None,
    ),
)


#: 每个观测点用哪把尺子量：默认是 :data:`AUDIT_SCRIPT`（缠论流水线口径），
#: 工程类的那几个要看的是别的数字（报告字段、读盘行数、线段统计），
#: 尺子放在 ``optimizer/tools/`` 里，好让 24x7 回访量的是**同一把尺子**。
PROBE_SCRIPTS: dict[str, str] = {
    "G4a": "tools/measure_segments.py",
    "G4b": "tools/measure_segments.py",
    "G5a": "tools/measure_quality_source.py",
    "G5b": "tools/measure_store_codes.py",
}


def probe_script(root: str | Path, rid: str) -> str:
    """取观测点 ``rid`` 的测量脚本正文（缺文件时退回审计脚本）。"""
    rel = PROBE_SCRIPTS.get(rid)
    if rel:
        path = Path(root) / "optimizer" / rel
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return AUDIT_SCRIPT


def default_probes(root: str | Path) -> list[Probe]:
    """回访已登记的问题：有补丁就重跑实测，没有就只记一次观测。

    这保证 24x7 跑起来时每一轮都有真实数字，而不是每轮换一个新话题。
    ``before``/``after`` 只在没有历史时留空：一旦挂上测量脚本，两者都会被
    **当轮实测**覆盖，所以「有没有变好」永远来自当轮的 ``__METRICS__``，
    不来自上一轮的结论。

    ``optimizer/patches/`` 里的命名有约定：``round-NNN-RID.patch`` 是已提议的改动，
    ``round-NNN-RID-candidate.patch`` 是量过但不足以提议的候选（例如 G2b 实测为
    no-op），因此它不会被 glob 命中，也就永远不会被当成提案重新量一遍。
    """
    root = Path(root)
    patches = root / "optimizer" / "patches"
    journal = root / "optimizer" / "journal"
    history = {e.probe: e for e in read_all(journal)}
    probes: list[Probe] = []
    for rid, kind, finding, theory_id in PROBE_SPECS:
        candidates = sorted(patches.glob(f"round-*-{rid}.patch"))
        patch_text = candidates[-1].read_text(encoding="utf-8") if candidates else None
        last = history.get(rid)
        probe = Probe(
            rid=rid,
            kind=kind,
            finding=last.finding if last else finding,
            evidence=last.evidence if last else "尚未取证",
            theory_id=theory_id,
            patch_text=patch_text,
            before=last.after if last else {},
            after=last.after if last else {},
            tests={"before": "skip", "after": "skip", "summary": "re-measure"},
            notes="24x7 回访：补丁内容不变，重新量一次当前数据。",
            measure_script=probe_script(root, rid),
        )
        probes.append(probe)
    return probes
