"""逐个提案补丁：apply → 跑全量测试 → 反 apply → 校验主干逐字复原。

这是「提案不改主干」的证据生成器，也是在补丁上真跑测试的唯一方式。
跑完把结果写成 JSON，供写 journal 时引用**真实**的 tests 字段。

三处口径必须说明（否则数字会被误读）：

- 早期排除过 ``tests/data/test_tdx_*``（当时是并行 agent 在建的 TDX 导入/解析测试）。
  那两个文件现已提交且全绿（29 passed），排除的理由消失了 —— 现在**不排除任何文件**，
  基线就等于全量套件，``--deselect`` 只在量补丁时摘掉那个自己会 apply 补丁的用例。
- 「碰没碰主干」只认**补丁触及的那些文件**：apply 前记下它们的 sha256，
  反 apply 后再记一次，必须逐个相同。不用整棵树比 —— 并行 agent 随时在改
  ``data/meta.py`` 这类文件，整棵树比会得到「别人改了 ⇒ 我碰了主干」的假结论。
- ``tests`` 字段填的就是这里跑出来的结论，不手写。
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))  # 判据（parse_header/ADOPTED）与主干同一份实现
PYTEST = str(Path(sys.executable).parent / "pytest")
#: 曾经要排除的在建工程；现已提交且全绿，留空 = 基线跑全量。
FOREIGN_IGNORES: tuple[str, ...] = ()
PATCHES = sorted((ROOT / "optimizer" / "patches").glob("*.patch"))
#: 补丁里 ``+++ b/<路径>`` 的目标文件，也就是 diff 真正会改的那些。
TOUCHED_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def _repo_prefix() -> list[str]:
    """``ROOT`` 相对 git 仓库根的前缀，喂给 ``git apply --directory``。

    **这是一个会静默出错的坑**：``git apply`` 即使在子目录里执行，也按**仓库根**
    解析补丁路径；路径落在当前目录之外的补丁会被打印一行「跳过补丁」然后
    **返回 0、一个字节都不改**。本机 checkout 里 ``chanlun/`` 是外层工作区
    的子目录，补丁里写的是 ``src/...`` —— 于是这个工具长期在「什么都没打」
    的状态下报全绿。补上 ``--directory=chanlun`` 之后路径才落到
    ``chanlun/src/...``；不是 git 仓库时（补丁相对 cwd 解析）不加前缀。
    """
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True
    )
    if top.returncode != 0:
        return []
    try:
        rel = ROOT.relative_to(Path(top.stdout.strip()))
    except ValueError:
        return []
    return ["--directory", rel.as_posix()] if rel.parts else []


REPO_PREFIX = _repo_prefix()


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _is_adopted(path: Path) -> bool:
    """补丁头 `# status: adopted` = 提案已被主干采纳（与常驻测试同一判据）。"""
    from chanlun.optimizer.journal import ADOPTED
    from chanlun.optimizer.theory import parse_header

    return parse_header(path.read_text(encoding="utf-8")).get("status", "").strip().lower() == ADOPTED


def _is_retired(path: Path) -> bool:
    """补丁头 `# status: retired` = 提案的**前提**已被实测证伪（与常驻测试同一判据）。"""
    from chanlun.optimizer.journal import RETIRED
    from chanlun.optimizer.theory import parse_header

    return parse_header(path.read_text(encoding="utf-8")).get("status", "").strip().lower() == RETIRED


#: 这个用例会把 ``optimizer/patches/*.patch`` 逐份 apply 到自己的临时副本上。
#: 本工具**是在主干里**打补丁再跑测试的，于是它会拿到一份已经被打过的文件、
#: 再打一次必然失败 —— 那是测量方法自己撞自己，不是补丁的毛病。量的时候摘掉它，
#: 它在干净主干上照常跑（基线里包含它）。
SELF_APPLYING_TEST = "tests/optimizer/test_optimizer.py::test_real_patches_apply_and_revert_cleanly"


def pytest_summary(patched: bool = False) -> dict:
    ignores = [f"--ignore={p}" for p in FOREIGN_IGNORES]
    if patched:
        ignores.append(f"--deselect={SELF_APPLYING_TEST}")
    cmd = [PYTEST, "-q", *ignores]
    for attempt in (1, 2):
        done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        # rc=2 是收集期错误：并行 agent 正在写文件时会瞬时出现，重跑一次再看。
        if done.returncode != 2 or attempt == 2:
            break
    tail = [ln for ln in done.stdout.strip().splitlines() if ln.strip()]
    # 挂了哪些用例必须落进记录：只说「4 failed」等于没说，评审的人得知道
    # 是补丁本身错了，还是它跟主干新加的回归用例撞了。
    failed = [
        ln.split(" - ")[0].removeprefix("FAILED ").strip()
        for ln in done.stdout.splitlines()
        if ln.startswith("FAILED ")
    ]
    return {
        "returncode": done.returncode,
        "summary": tail[-1].strip() if tail else done.stderr.strip()[-200:],
        "failed_tests": failed,
        "cmd": " ".join(["pytest", "-q", *ignores]),
        "ignored": list(FOREIGN_IGNORES),
    }


def digests(paths: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in paths:
        p = ROOT / rel
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "<缺失>"
    return out


def main() -> int:
    baseline = pytest_summary()
    print(f"主干基线：{baseline['summary']}")
    results: dict = {
        "baseline": baseline, "patches": {}, "restored": True, "all_effective": True,
        "deselected_while_patched": [SELF_APPLYING_TEST],
    }
    for path in PATCHES:
        touched = sorted(set(TOUCHED_RE.findall(path.read_text(encoding="utf-8"))))
        # 已采纳的补丁不在这里复核：它已经是主干的一部分，`git apply` 会打印
        # 「跳过补丁」并返回 0 —— 那是一个**假绿**（什么都没改，测试当然全过）。
        # 它的判据归常驻回归管，见 tests/optimizer/test_optimizer.py。
        # `# status: retired`（前提已被实测证伪）同样跳过，但**理由不同**：
        # 证伪的补丁只作证据留档，主干往前走之后它自然对不上（round-004-G2a 的
        # 9 段上限被 round-012-G11b 的 8 段取代，同一个延伸循环已经不是原来那几
        # 行了）。这里曾经写着「retired 不跳过，因为它仍然能 apply、没有假绿问题」
        # —— 那个前提在 G11b 采纳当天就失效了，而失效的后果是整个工具 `return 1`
        # 停摆。要求一份**前提已被证伪**的补丁「仍能 apply」，等于要求主干停在
        # 证伪当时；那不是证据，是枷锁。它的证据价值在补丁头与 journal 里。
        if _is_adopted(path):
            results["patches"][path.name] = {"skipped": "adopted"}
            print(f"{path.name}: 已采纳 → 跳过（判据在常驻回归里，"
                  f"apply 会被 git 静默跳过，不算通过）")
            continue
        if _is_retired(path):
            results["patches"][path.name] = {"skipped": "retired"}
            print(f"{path.name}: 已证伪 → 跳过（只作证据留档，"
                  f"前提不成立，主干已往前走）")
            continue
        before_digests = digests(touched)
        applied = git("apply", *REPO_PREFIX, "-p1", str(path))
        if applied.returncode != 0:
            print(f"{path.name}: 无法 apply：{applied.stderr.strip()}")
            return 1
        try:
            after = pytest_summary(patched=True)
            changed = [t for t in touched if digests([t])[t] != before_digests[t]]
        finally:
            back = git("apply", *REPO_PREFIX, "-p1", "-R", str(path))
        if back.returncode != 0:
            print(f"{path.name}: 无法反 apply：{back.stderr.strip()}")
            return 1
        restored = digests(touched) == before_digests
        effective = bool(changed)
        results["patches"][path.name] = {
            **after,
            "touched": touched,
            "changed_while_applied": changed,
            "restored_after_revert": restored,
            "touched_sha256": before_digests,
        }
        results["restored"] = results["restored"] and restored
        results["all_effective"] = results["all_effective"] and effective
        print(f"{path.name}: {after['summary']} (rc={after['returncode']})  "
              f"触及 {len(touched)} 个文件，反 apply 后逐字复原={restored}")
        if not effective:
            # 「apply 成功但一个字节都没动」= 主干已含这些改动，测试全过没有意义。
            # 这种假绿必须报成失败，否则采纳当天起这把尺子就永久失灵。
            print(f"{path.name}: 假绿！apply 返回 0 但触及的文件一个都没变"
                  f"（要么主干已含这些改动，补丁头应标 `# status: adopted`；"
                  f"要么路径被 git 静默跳过 —— 见 _repo_prefix 的说明）")
            return 1

    print(f"全部补丁：反 apply 后触及文件逐字复原={results['restored']}；"
          f"每个待评审补丁都真的改了代码={results['all_effective']}")
    out = ROOT / "optimizer" / "tools" / "patch_test_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写出 {out}")
    return 0 if results["restored"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
