"""FastAPI 应用装配与端口策略。

默认端口**写死 8888**：这个页面是给个人看盘用的固定地址，被占用时应该报错让人去看
是谁占着，而不是悄悄换到 8889 让书签、自选池、脚本全部失效。所以 `serve()` 起服务前
必须 `ensure_port_free()`，占用即抛 `PortInUseError`（显式传 `port=` 是例外，
但那要写出来，不算"悄悄换"）。

`create_app()` 会把 `store.DATA_ROOT` 钉到配置里的数据根：Web 进程只有一个数据根，
在装配时定一次比每次请求都去读配置更清楚（也让测试能指向临时目录）。
"""

from __future__ import annotations

import socket
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config, load_config
from ..data import store
from . import api

STATIC_DIR = Path(__file__).resolve().parent / "static"


class PortInUseError(RuntimeError):
    """目标端口已被占用（按约定不自动换端口）。"""


def ensure_port_free(host: str, port: int) -> None:
    """端口被占用则抛 `PortInUseError`；否则原样返回。

    用「真的 bind 一下」判断，而不是 `lsof` 或 connect：bind 失败才是操作系统
    层面的权威结论（connect 在有防火墙/仅绑定特定网卡时会骗人）。
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, int(port)))
    except OSError as exc:
        raise PortInUseError(
            f"端口 {port} 已被占用（{exc}）。本系统固定使用 {port}，不会自动换端口；"
            f"请先停掉占用进程（lsof -nP -iTCP:{port} -sTCP:LISTEN）再启动。"
        ) from exc
    finally:
        probe.close()


def create_app(cfg: Config | None = None, port: int | None = None) -> FastAPI:
    """装配应用。`cfg` 缺省读 `config.toml`；`port` 只影响 `/api/health` 的自报。"""
    conf = cfg or load_config()
    store.DATA_ROOT = Path(conf.data.root)

    app = FastAPI(title="缠论看盘", version="0.1.0", docs_url="/api/docs", redoc_url=None)
    app.state.cfg = conf
    app.state.port = int(port or conf.web.port)
    app.include_router(api.router)

    if not (STATIC_DIR / "index.html").exists():  # pragma: no cover - 打包缺失时才发生
        raise RuntimeError(f"静态目录缺少 index.html: {STATIC_DIR}")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


def serve(cfg: Config | None = None, host: str | None = None, port: int | None = None) -> None:
    """起服务（前台阻塞）。端口占用直接抛错退出。"""
    conf = cfg or load_config()
    h = host or conf.web.host
    p = int(port or conf.web.port)
    ensure_port_free(h, p)
    uvicorn.run(create_app(conf, port=p), host=h, port=p, log_level="info")
