"""版本号的单一来源解析。

版本号**只写一份**，在 `pyproject.toml` 的 `[project].version`。本模块负责把它读出来：

- 源码态（`PYTHONPATH=src`，日常开发与测试）：向上找到 `pyproject.toml` 读 `[project].version`；
- 安装态（装进 site-packages，树里没有 pyproject）：回退到 `importlib.metadata`。

两边都不硬编码版本号 —— 硬编码就是第二个真相来源，早晚和 `pyproject.toml` 分叉，
而版本号一旦分叉，所有「线上跑的是哪个版本」的判断都失去依据。
`tests/test_version.py` 钉住这一点。

版本的**变更规则**见 `AGENTS.md`，变更**历史**见 `CHANGELOG.md`。
"""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path

#: 分发包名，与 `pyproject.toml` 的 `[project].name` 必须一致。
PACKAGE = "chanlun"

#: 两种来源都拿不到时的占位值。故意做成不合法的 SemVer（`+` 之后是构建元数据），
#: 这样它一旦出现在页面上就一眼看得出来是异常，而不是某个真实的版本号。
UNKNOWN = "0.0.0+unknown"


def _from_pyproject() -> str | None:
    """从源码树里的 `pyproject.toml` 读版本号。

    逐级向上找，但只认 `[project].name == "chanlun"` 的那一份：工作区里还有别的
    pyproject.toml，随便捡一个就会把别人的版本号当成自己的。
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "pyproject.toml"
        if not candidate.is_file():
            continue
        try:
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue
        project = data.get("project")
        if not isinstance(project, dict) or project.get("name") != PACKAGE:
            continue
        found = project.get("version")
        if isinstance(found, str) and found.strip():
            return found.strip()
    return None


def _from_metadata() -> str | None:
    """从已安装分发的元数据读版本号。"""
    try:
        return _dist_version(PACKAGE)
    except PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - 元数据损坏不该让整个系统起不来
        return None


def version_info() -> dict[str, str]:
    """版本号及其来源，供 `/api/version` 与排查用。"""
    found = _from_pyproject()
    if found:
        return {"version": found, "source": "pyproject.toml"}
    found = _from_metadata()
    if found:
        return {"version": found, "source": "importlib.metadata"}
    return {"version": UNKNOWN, "source": "unknown"}


#: 当前版本号。导入本模块即完成解析（读一个本地文件，开销可忽略）。
__version__ = version_info()["version"]
