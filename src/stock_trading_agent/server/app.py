"""create_app 工厂：TrustedHost 白名单 + 127.0.0.1 绑定常量 + StaticFiles 同进程挂载（§8/§13/§16）。

安全（§13）：绑 127.0.0.1 之上加 TrustedHostMiddleware 白名单防 DNS rebinding（I-46/I-47）；
数据面单文件 JSON 原子写、不引数据库。
运行时（M1 接线）：enable_loop_runtime=True 时 lifespan 内起 LoopRuntime（daemon 锁失败
降级只读——面板照常、/api/monitor/status.loop.active=false 可见；硬 exit 3 只属 CLI run-once）；
on_event 已 deprecated，统一 lifespan 上下文（I-33）。
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from stock_trading_agent import __version__
from stock_trading_agent.budget.gate import BudgetConfig
from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.dirwatch import DirWatcher
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.server import routes
from stock_trading_agent.server.sse import router as sse_router
from stock_trading_agent.server.stream_hub import StreamHub, to_event_name
from stock_trading_agent.state.paths import StateLayout

HOST = "127.0.0.1"
PORT = 8730
# testserver = starlette TestClient 默认 Host（测试兼容位；非本机可路由域名）
TRUSTED_HOSTS = ("localhost", "127.0.0.1", "testserver")

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    *,
    state_root: Path | None = None,
    enable_loop_runtime: bool = False,
    runtime: LoopRuntime | None = None,
) -> FastAPI:
    """依赖注入工厂：测试可换 tmp state_root / 注入 runtime；缺省 StateLayout.from_env()。

    enable_loop_runtime：serve 入口置 True（lifespan 起 worker/watcher 线程）；
    测试直调端点（TestClient 不进 lifespan 上下文）零线程。
    """
    layout = StateLayout(root=state_root) if state_root is not None else StateLayout.from_env()
    app = FastAPI(
        title="Stock Trading Agent",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=_make_lifespan(enable_loop_runtime, runtime, layout),
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(TRUSTED_HOSTS))
    app.state.state_root = str(layout.root) if state_root is not None else None
    app.state.loop_runtime = runtime or LoopRuntime(
        layout=layout,
        cc=DryRunCCClient(audit_dir=layout.audit_dir),
        budget=BudgetConfig(),
    )
    app.include_router(routes.router)
    app.include_router(routes.root_router)
    app.include_router(sse_router)
    # SSE 装配（只读桥，不依赖 daemon 锁）：hub 绑事件循环 + DirWatcher 盯台账目录
    hub = StreamHub()
    app.state.stream_hub = hub
    app.state.sse_layout = layout
    watcher = DirWatcher(
        roots=(
            layout.agent_dir,
            layout.monitor_dir,
            layout.outlook_dir,
            layout.root,
        ),
        on_change=hub.publish_threadsafe,
        to_event=lambda path: to_event_name(path, layout),
    )
    app.state.dir_watcher = watcher
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
    return app


def _make_lifespan(enable: bool, runtime: LoopRuntime | None, layout: StateLayout) -> object:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = app.state.loop_runtime
        if enable and not rt.start():  # daemon 锁失败：降级只读（面板照常服务）
            print(f"[sta] daemon.lock 被持有，降级只读（root={layout.root}）")
        import asyncio

        app.state.stream_hub.bind_loop(asyncio.get_running_loop())
        app.state.dir_watcher.start()
        yield
        app.state.dir_watcher.stop()
        if enable:
            rt.stop()

    return lifespan
