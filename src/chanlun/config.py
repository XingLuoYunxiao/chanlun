"""配置加载。

设计约定：
- Web 端口默认且推荐 8888；配置只允许改值，不允许在运行期静默切换。
- 所有路径相对于项目根目录（`chanlun/`）解析。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8888


@dataclass(frozen=True)
class DataConfig:
    root: Path = PROJECT_ROOT / "data"

    @property
    def meta_db(self) -> Path:
        return self.root / "meta.db"

    @property
    def calendar_csv(self) -> Path:
        return self.root / "calendar.csv"


@dataclass(frozen=True)
class Config:
    web: WebConfig = field(default_factory=WebConfig)
    data: DataConfig = field(default_factory=DataConfig)
    periods: list[str] = field(default_factory=lambda: ["day", "30", "5"])
    bs_adjust: str = "2"
    log_path: Path = PROJECT_ROOT / "logs" / "chanlun.log"


def load_config(path: str | Path | None = None) -> Config:
    """加载配置；文件不存在时返回全默认值（端口仍为 8888）。"""
    p = Path(path) if path else PROJECT_ROOT / "config.toml"
    raw: dict = {}
    if p.exists():
        raw = tomllib.loads(p.read_text(encoding="utf-8"))

    web = WebConfig(**raw.get("web", {}))

    data_raw = dict(raw.get("data", {}))
    if "root" in data_raw:
        root = Path(data_raw["root"])
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        data_raw["root"] = root
    data = DataConfig(**data_raw)

    log_raw = raw.get("log_path")
    log_path = Path(log_raw) if log_raw else PROJECT_ROOT / "logs" / "chanlun.log"

    return Config(
        web=web,
        data=data,
        periods=list(raw.get("periods", ["day", "30", "5"])),
        bs_adjust=str(raw.get("bs_adjust", "2")),
        log_path=log_path,
    )
