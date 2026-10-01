#!/usr/bin/env python
"""记录一个 24x7 周期：跑 10 轮，把每轮的发现与**当轮实测**数字写进 journal。

用法::

    cd chanlun
    PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/record_rounds.py

它做的事和 ``python optimizer/agent.py --rounds 10 --measure`` 一样，
差别只有一个：它会把每轮的 ``evidence`` 补成「**能重跑的命令 + 那串原始输出**」，
让任何人拿到 journal 都能自己复核这一轮的数字是不是真的。

设计上的三条自我约束：

1. 每轮都重新量 before/after，不抄上一轮的结论；
2. 量出来数字没动 → 由 ``Optimizer.run_round`` 自动降级为 ``inconclusive``，
   不许写成 ``proposed``；
3. 熔断照常生效：连续无有效产出就停，并把 ``circuit_break`` 留在 journal 里。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # chanlun/
sys.path.insert(0, str(ROOT / "src"))

from chanlun.optimizer.agent import (  # noqa: E402
    PROBE_SCRIPTS,
    Optimizer,
    default_probes,
)
from chanlun.optimizer.journal import ADOPTED, read_all, write_entry  # noqa: E402

CLI = "python -m chanlun.optimizer.cli --root . --audit"


#: 每条记录都要带的口径说明。``finding`` 那句话里的计数写自取证刚开始那一版
#: （24/24 只票、bars 39240），本轮的 before/after 量自本轮冻结快照（重建中，
#: 22/24 只票、bars 35970）。两个数都真，差的只是行情仓在两个时刻的完整度；
#: 缺陷的判据（exit_dir_matches_position=0 之类）不依赖缺掉的那两只票，
#: 移动的只是绝对计数。不说清楚就会变成「同一件事有两套数字」。
BASIS_DRIFT_NOTE = (
    "[基准漂移] finding 里的流水线计数写自 24/24 只票那一版口径（bars 39240）；"
    "本条的 before/after 量自本轮冻结快照（见上一行 [口径]）。取证期间全市场同步"
    "正在重写 data/day，600000 与 000001 暂时不在库里，所以每轮绝对计数少 4 个信号；"
    "同一快照内的 before/after 差值不受影响，判据也不依赖缺掉的那两只票。"
)


#: 补丁在仓库里的相对位置：evidence 里给全路径，人才可以直接复制粘贴重跑。
PATCH_REL = "optimizer/patches"


def audit_cmd(rid: str, patch: str | None = None) -> str:
    """这一轮用的那把尺子，以及怎么把它重跑一遍。"""
    parts = [CLI]
    if rid in PROBE_SCRIPTS:
        parts.append(f"--probe {rid}")
    if patch:
        parts.append(f"--patch {PATCH_REL}/{patch}")
    return " ".join(parts)


def patch_key(entry, results: dict) -> str | None:
    """这一轮该看哪份补丁的测试结论。

    ``entry.patch_file`` 只在「量出来数字确实动了」时才落盘（no-op 会被降级成
    ``inconclusive``，patch_file 留空）—— 但那份补丁**确实量过**，它的测试结论
    不能因为没被采纳就丢掉。G2b 就是这种：候选补丁叫
    ``round-005-G2b-candidate.patch``（故意不被 ``patches/*.patch`` 当提案），
    entry.patch_file 为空，于是这里按轮次+观测点把文件名找回来。
    """
    keys = results["patches"]
    for cand in (
        entry.patch_file,
        f"round-{entry.round:03d}-{entry.probe}.patch",
        f"round-{entry.round:03d}-{entry.probe}-candidate.patch",
    ):
        if cand and cand in keys:
            return cand
    return None


def tests_for(entry, results: dict) -> dict:
    """这一轮的测试结论。取自 ``run_patch_tests.py`` 的真实输出，不许手写。

    **这里曾经写死过一句「结果与主干基线逐项一致，无新增失败」**：那个工具当时
    在子目录里跑 ``git apply``，路径落在当前目录之外被 git 静默跳过（rc=0、
    一个字节都没改），于是它永远报全绿，这句话也永远「成立」。工具修好之后
    真相是：待评审的补丁打进主干，每一份都会撞掉几个常驻用例 —— 那些用例钉的
    正是提案要改的行为。测试结论必须照实写，包括撞了哪几个用例。
    """
    base = results["baseline"]
    key = patch_key(entry, results)
    got = results["patches"].get(key) if key else None
    if got and "skipped" in got:
        return {
            "before": base["summary"],
            "after": base["summary"],
            "cmd": base["cmd"],
            "summary": ("已采纳：补丁已是主干的一部分，本工具不再重复 apply"
                        "（git 会静默跳过），判据在常驻回归里"),
        }
    if got:
        failed = list(got.get("failed_tests", []))
        if failed:
            summary = (
                f"{key} 单独 apply 进主干后跑同一套测试："
                f"主干基线「{base['summary']}」→ 打补丁后「{got['summary']}」，"
                f"新增失败 {len(failed)} 个：{'、'.join(failed)}。"
                "这些用例钉的正是本提案要改的行为，所以采纳时必须连同判据一起改"
                "（改判据要引 theory 原文），不能只看 before/after 变好就落地。"
            )
        else:
            summary = (
                f"{key} 单独 apply 进主干后跑同一套测试："
                "结果与主干基线逐项一致，无新增失败"
            )
        return {
            "before": base["summary"],
            "after": got["summary"],
            "cmd": got["cmd"],
            "failed_tests": failed,
            "summary": summary,
        }
    return {
        "before": base["summary"],
        "after": base["summary"],
        "cmd": base["cmd"],
        "summary": "本轮无提案补丁，未打任何补丁；主干测试基线见 before",
    }


def evidence_text(rid: str, patch_file: str | None, before: dict, after: dict) -> str:
    """命令 + 原始输出：补丁头 ``# evidence:`` 里要的东西，逐字可复核。"""
    ruler = (
        f"# 尺子：{PROBE_SCRIPTS[rid]}（在影子目录里跑，主干一个字节都不动）"
        if rid in PROBE_SCRIPTS
        else "# 尺子：缠论流水线审计脚本（24 只票 / 日线 / 2020-01-01..2026-09-29）"
    )
    lines = [ruler, f"$ cd chanlun && {audit_cmd(rid)}"]
    lines.append(f"metrics = {json.dumps(before, ensure_ascii=False, sort_keys=True)}")
    if patch_file:
        lines.append(f"$ cd chanlun && {audit_cmd(rid, patch_file)}")
        lines.append(f"metrics = {json.dumps(after, ensure_ascii=False, sort_keys=True)}")
    else:
        lines.append("# 本轮无提案补丁：只量主干一次，after 与 before 相同")
    return "\n".join(lines)


def main() -> int:
    probes = list(default_probes(ROOT))

    # G2b 的候选补丁文件名带 -candidate，故意不被 glob 命中（它不是提案）。
    # 但它**量过**，所以这里手动挂上，让这一轮的 before/after 是当轮实测出来的。
    candidate = ROOT / "optimizer" / "patches" / "round-005-G2b-candidate.patch"
    for i, probe in enumerate(probes):
        if probe.rid == "G2b" and candidate.is_file():
            probes[i] = dataclasses.replace(
                probe, patch_text=candidate.read_text(encoding="utf-8")
            )

    results_path = ROOT / "optimizer" / "tools" / "patch_test_results.json"
    if not results_path.is_file():
        print(f"缺少测试结果：{results_path}\n先跑 optimizer/tools/run_patch_tests.py",
              file=sys.stderr)
        return 1
    test_results = json.loads(results_path.read_text(encoding="utf-8"))

    opt = Optimizer(ROOT, propose_only=True, measure=True)
    print(f"待回访观测点 {len(probes)} 个：{', '.join(p.rid for p in probes)}\n")

    recorded = []
    for round_no, probe in enumerate(probes, start=1):
        # 已采纳的观测点跳过，但**不改变轮次号**：轮次号是 journal 的历史主键，
        # 从列表里删掉一个会让后面每一轮都往前挪一格，把旧记录覆盖成别的观测点。
        if probe.adopted:
            # 不重量，但要把 journal 里的状态**对齐成 adopted**：补丁头写着
            # `# status: adopted`、判据在常驻回归里，日志却还留着 proposed，
            # 那就成了两套说法。这里幂等修正，重复跑不会追加。
            old = next(
                (e for e in read_all(opt.journal_dir) if e.probe == probe.rid), None
            )
            if old is not None:
                note = (
                    "[采纳] 补丁已是主干的一部分（补丁头 `# status: adopted`），"
                    "判据搬进常驻回归：tests/optimizer/test_optimizer.py::"
                    "test_adopted_patches_are_regression_tested_in_trunk 与"
                    " test_summary_breaks_the_check_down_by_kind。"
                    "本观测点不再作为提案回访。"
                )
                fresh_tests = tests_for(old, test_results)
                # 幂等：状态、判据、说明都对齐成「已采纳」，重复跑不会追加。
                stale = (
                    old.status != ADOPTED
                    or old.tests != fresh_tests
                    or "[采纳]" not in old.notes
                )
                old.status = ADOPTED
                old.tests = fresh_tests
                if "[采纳]" not in old.notes:
                    old.notes = f"{old.notes}\n{note}".strip()
                if stale:
                    write_entry(opt.journal_dir, old)
                    print(f"round {round_no:03d}  {probe.rid:4} 已采纳 → 状态/判据对齐为"
                          f" adopted（判据在常驻回归里，补丁已是主干的一部分）")
                else:
                    print(f"round {round_no:03d}  {probe.rid:4} 已采纳 → 跳过回访"
                          f"（判据在常驻回归里，补丁已是主干的一部分）")
            else:
                print(f"round {round_no:03d}  {probe.rid:4} 已采纳 → journal 里没有这一轮，"
                      f"跳过（判据在常驻回归里）")
            continue
        entry = opt.run_round(round_no, probe)
        # 把「能重跑的命令 + 原始输出」写进 evidence。注意 before/after 是
        # run_round 刚刚量出来的，所以这段文字与 journal 的数字必然一致。
        entry.evidence = evidence_text(
            probe.rid, entry.patch_file, entry.before, entry.after
        )
        # 口径必须跟着读数一起存：这一轮量的是哪份冻结行情、缺了哪几只票。
        # 少了它，过两天行情仓一变，谁也没法判断两个数能不能比。
        basis_note = [ln for ln in entry.notes.splitlines() if ln.startswith("[口径] ")]
        if basis_note:
            entry.evidence += (
                "\n"
                + "\n".join(basis_note)
                + "\n# 注：命令每次重跑都会重新冻结当时的行情仓，basis_sha256 会随数据变；"
                  "同一轮里的 before/after 用的是同一份快照。"
            )
        entry.tests = tests_for(entry, test_results)
        entry.notes = f"{entry.notes}\n{BASIS_DRIFT_NOTE}".strip()
        write_entry(opt.journal_dir, entry)
        recorded.append(entry)
        flag = {"proposed": "提案", "rejected": "拒绝", "inconclusive": "无结论",
                "circuit_break": "熔断", "adopted": "已采纳"}[entry.status]
        print(f"round {round_no:03d}  {probe.rid:4} {entry.kind:11} {flag}  "
              f"signals {entry.before.get('signals', '-')} → {entry.after.get('signals', '-')}")
        if opt.state.should_break:
            print(f"\n熔断：{opt.state.reason}")
            break

    final = read_all(opt.journal_dir)
    effective = sum(1 for e in final if e.effective)
    print(f"\n共 {len(final)} 轮，有效产出 {effective} 轮，"
          f"熔断 {'已触发' if opt.state.should_break else '未触发'}"
          f"（trailing barren streak = {opt.state.barren_streak}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
