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


def test_no_hardcoded_version_literal_in_source():
    """src 下不许把版本号当字面量写进 `version=`。

    `test_no_hardcoded_version_assignment_in_source` 只拦 `__version__ = "..."`，
    拦不住**换了个地方的**硬编码。真犯过：`web/app.py` 的
    `FastAPI(title="缠论看盘", version="0.1.0", ...)` —— `pyproject.toml` 涨到
    `0.2.0` 之后它仍写着 `0.1.0`，于是 `/api/health` 报 `0.2.0`、`/api/docs`
    的 OpenAPI 却报 `0.1.0`，两边都不报错。

    只匹配**带引号且以数字开头**的 `version=`，所以 `engine.py` 的
    `version=1`（快照序号，不是包版本）不受影响。
    """
    offenders = []
    pattern = re.compile(r"""\bversion\s*=\s*["']\d""")
    for path in SRC.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, f"发现硬编码版本号字面量：{offenders}"


def test_web_app_declared_version_matches_single_source(tmp_path, monkeypatch):
    """FastAPI 应用自报的版本号必须等于单一真相来源。

    这是上面那条静态规则的**行为版**：静态规则只能拦住「字面量」这一种写法，
    拦不住 `version=SOME_OTHER_CONSTANT`；而 `app.version` 是用户真能看到的东西
    （`/api/docs` 的 OpenAPI 文档头部），必须与 `/api/health`、CLI 一致。
    """
    import dataclasses

    from chanlun.config import load_config
    from chanlun.data import store
    from chanlun.web.app import create_app

    base = load_config()
    conf = dataclasses.replace(base, data=dataclasses.replace(base.data, root=tmp_path))
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path)

    app = create_app(conf)
    assert app.version == chanlun.__version__, (
        f"FastAPI 自报 {app.version!r}，单一来源是 {chanlun.__version__!r}"
    )


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
