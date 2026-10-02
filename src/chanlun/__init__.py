"""缠论分析系统。

版本号不在这里写死：唯一来源是 `pyproject.toml`，由 `version.py` 解析后导出。
"""

from .version import __version__, version_info

__all__ = ["__version__", "version_info"]
