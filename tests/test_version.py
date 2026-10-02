"""版本号的单一来源契约。

版本号分叉是**静默**的故障：`pyproject.toml` 写着 0.2.0、代码里硬编码着 0.1.0，
两边都不报错，但「线上跑的是哪一版」这个问题从此没有答案，CHANGELOG 也就失去锚点。
所以这里把「只有一个真相来源」钉成测试，而不是靠约定。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

import chanlun
from chanlun.version import PACKAGE, UNKNOWN, version_info

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
SRC = ROOT / "src" / "chanlun"

# SemVer 2.0.0 的官方正则（https://semver.org/lang/zh-CN/），故意照抄而不是简化：
# 简化版会放过 `01.2.3`（前导零）这类非法版本号，而前导零正是「手写版本号」的典型症状。
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


def _pyproject_version() -> str:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_package_name_matches_pyproject():
    """`version.py` 里的 PACKAGE 必须和 pyproject 的分发包名一致。

    不一致时 `_from_pyproject()` 会跳过自己家那份 pyproject，静默退到
    `importlib.metadata`，于是源码态的版本号变成「上次 pip install 时的版本」。
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["project"]["name"] == PACKAGE


def test_version_equals_pyproject():
    assert chanlun.__version__ == _pyproject_version()


def test_version_is_valid_semver():
    assert SEMVER.match(chanlun.__version__), f"{chanlun.__version__!r} 不是合法 SemVer"


def test_source_tree_reads_pyproject():
    """源码态必须走 pyproject 分支。

    这条同时守住另一件事：`_from_pyproject()` 认不出自己家 pyproject 时会回退到
    安装元数据，测试环境里那可能是任意旧版本 —— 版本号看起来正常，其实来自别处。
    """
    info = version_info()
    assert info["source"] == "pyproject.toml", f"版本号来源意外：{info}"
    assert info["version"] == _pyproject_version()


def test_unknown_fallback_is_not_valid_semver():
    """兜底值必须一眼看得出是异常。

    如果兜底写成 `0.0.0`，页面上会和真实版本号混在一起；带 `+unknown` 构建元数据
    后它仍是合法 SemVer（能被解析），但不可能是发布版本，肉眼也能认出来。
    """
    assert UNKNOWN != _pyproject_version()
    assert "unknown" in UNKNOWN


def test_no_hardcoded_version_assignment_in_source():
    """src 下不许再出现 `__version__ = "..."` 这种硬编码。

    `version.py` 自己是通过 `version_info()` 计算的，不匹配本正则；一旦有人图省事
    在别处写死版本号，这条会失败。
    """
    offenders = []
    pattern = re.compile(r"""__version__\s*=\s*["']""")
    for path in SRC.rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"发现硬编码版本号：{offenders}"


def test_cli_version_flag(capsys):
    """`python -m chanlun --version` 不必先选子命令就能答出来。"""
    from chanlun.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() == f"chanlun {chanlun.__version__}"


def test_subcommand_still_required():
    """加了 `--version` 不能顺手把「必须给子命令」这条约束弄丢。"""
    from chanlun.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0
