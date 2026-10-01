"""逐个提案补丁：apply → 跑全量测试 → 反 apply → 校验主干逐字复原。

这是「提案不改主干」的证据生成器，也是在补丁上真跑测试的唯一方式。
跑完把结果写成 JSON，供写 journal 时引用**真实**的 tests 字段。

三处口径必须说明（否则数字会被误读）：

- ``tests/data/test_tdx_*`` 是并行 agent 正在写的 TDX 导入/解析测试（未跟踪或
  正在改动的文件），本优化器不碰它，所以 ``--ignore`` 排除；它自己的失败不算在
  本轮头上。排除后的基线是 474 passed / 0 failed。
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
PYTEST = str(Path(sys.executable).parent / "pytest")
#: 并行 agent 的在建工程，与本优化器无关，排除在判据之外。
FOREIGN_IGNORES = (
    "tests/data/test_tdx_parse.py",
    "tests/data/test_tdx_import.py",
)
PATCHES = sorted((ROOT / "optimizer" / "patches").glob("*.patch"))
#: 补丁里 ``+++ b/<路径>`` 的目标文件，也就是 diff 真正会改的那些。
TOUCHED_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def pytest_summary() -> dict:
    ignores = [f"--ignore={p}" for p in FOREIGN_IGNORES]
    cmd = [PYTEST, "-q", *ignores]
    for attempt in (1, 2):
        done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        # rc=2 是收集期错误：并行 agent 正在写文件时会瞬时出现，重跑一次再看。
        if done.returncode != 2 or attempt == 2:
            break
    tail = [ln for ln in done.stdout.strip().splitlines() if ln.strip()]
    return {
        "returncode": done.returncode,
        "summary": tail[-1].strip() if tail else done.stderr.strip()[-200:],
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
    results: dict = {"baseline": baseline, "patches": {}, "restored": True}
    for path in PATCHES:
        touched = sorted(set(TOUCHED_RE.findall(path.read_text(encoding="utf-8"))))
        before_digests = digests(touched)
        applied = git("apply", "-p1", str(path))
        if applied.returncode != 0:
            print(f"{path.name}: 无法 apply：{applied.stderr.strip()}")
            return 1
        try:
            after = pytest_summary()
            changed = [t for t in touched if digests([t])[t] != before_digests[t]]
        finally:
            back = git("apply", "-p1", "-R", str(path))
        if back.returncode != 0:
            print(f"{path.name}: 无法反 apply：{back.stderr.strip()}")
            return 1
        restored = digests(touched) == before_digests
        results["patches"][path.name] = {
            **after,
            "touched": touched,
            "changed_while_applied": changed,
            "restored_after_revert": restored,
            "touched_sha256": before_digests,
        }
        results["restored"] = results["restored"] and restored
        print(f"{path.name}: {after['summary']} (rc={after['returncode']})  "
              f"触及 {len(touched)} 个文件，反 apply 后逐字复原={restored}")

    print(f"全部补丁：反 apply 后触及文件逐字复原={results['restored']}")
    out = ROOT / "optimizer" / "tools" / "patch_test_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写出 {out}")
    return 0 if results["restored"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
