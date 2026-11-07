"""create_app 工厂：TrustedHost 白名单 + 127.0.0.1 绑定常量 + StaticFiles 同进程挂载（§8/§13/§16）。

安全（§13）：绑 127.0.0.1 之上加 TrustedHostMiddleware 白名单防 DNS rebinding（I-46/I-47）；
数据面单文件 JSON 原子写、不引数据库。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from stock_trading_agent.server import routes

HOST = "127.0.0.1"
PORT = 8730
# testserver = starlette TestClient 默认 Host（测试兼容位；非本机可路由域名）
TRUSTED_HOSTS = ("localhost", "127.0.0.1", "testserver")

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(*, state_root: Path | None = None) -> FastAPI:
    """依赖注入工厂（测试可换 tmp state_root；缺省 StateLayout.from_env()）。"""
    app = FastAPI(title="Stock Trading Agent", version="0.0.1", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(TRUSTED_HOSTS))
    app.state.state_root = state_root  # 注入 tmp root（测试）；None → StateLayout.from_env()
    app.include_router(routes.router)
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
    return app
