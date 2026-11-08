"""create_app 工厂：TrustedHost 白名单 + 127.0.0.1 绑定常量 + StaticFiles 同进程挂载（§8/§13/§16）。

安全（§13）：绑 127.0.0.1 之上加 TrustedHostMiddleware 白名单防 DNS rebinding（I-46/I-47）；
数据面单文件 JSON 原子写、不引数据库。
运行时（M1 接线）：enable_loop_runtime=True 时 lifespan 内起 LoopRuntime（daemon 锁失败
降级只读——面板照常、/api/monitor/status.loop.active=false 可见；硬 exit 3 只属 CLI run-once）；
on_event 已 deprecated，统一 lifespan 上下文（I-33）。
"""

from __future__ import annotations

import contextlib
import http.client
import json
import socket
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from stock_trading_agent import __version__
from stock_trading_agent.budget.gate import BudgetConfig
from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.dirwatch import DirWatcher
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.server import routes
from stock_trading_agent.server.origin_guard import OriginGuardMiddleware
from stock_trading_agent.server.sse import router as sse_router
from stock_trading_agent.server.stream_hub import StreamHub, to_event_name
from stock_trading_agent.state.paths import StateLayout

HOST = "127.0.0.1"
PORT = 8730
# testserver = starlette TestClient 默认 Host（测试兼容位；非本机可路由域名）
TRUSTED_HOSTS = ("localhost", "127.0.0.1", "testserver")

_STATIC_DIR = Path(__file__).parent / "static"


def port_is_listening(host: str, port: int, timeout_s: float = 0.5) -> bool:
    """connect_ex 探测端口是否有活 LISTEN（0=被占）；异常视同空闲，交 uvicorn bind 兜底。

    不采用试 bind：探测结果依赖探测者自身 socket 选项栈（SO_REUSEADDR×TIME_WAIT 交互），
    与 uvicorn 选项栈对不齐即漂移，且探测 bind-close 自造残留态。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_s)
            return sock.connect_ex((host, port)) == 0
    except OSError:  # 构造期（EMFILE 等 fd 耗尽）与带超时 connect 阶段一律视同空闲
        return False


def _is_panel_health(payload: object) -> bool:
    """占口者身份判据（component 名与 health 端点同源 routes.PANEL_COMPONENT）。"""
    return isinstance(payload, dict) and payload.get("component") == routes.PANEL_COMPONENT


def probe_panel(host: str, port: int, timeout_s: float = 1.5) -> dict | None:
    """GET /api/health 探占口者身份：本面板返回 health dict，其余一律 None（宁拒不误认）。

    HTTPError/URLError/Timeout ⊂ OSError、JSONDecode ⊂ ValueError、getresponse 阶段
    BadStatusLine 等 ⊂ HTTPException（非 HTTP 占口者触发，stdlib 不包装原样穿透）
    ——一族 except 兜底。
    """
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=timeout_s) as resp:
            payload = json.loads(resp.read(65536))
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return payload if _is_panel_health(payload) else None


def listener_pid(port: int, timeout_s: float = 3.0) -> str | None:
    """lsof 查占口 PID（best-effort：缺二进制/超时/无输出一律 None，不阻塞主流程）。

    坑位：-Fp 输出混 f<fd> 行（本机实测），必须按 p 前缀过滤；多 PID 双栈取首个。
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("p"):
            return line[1:]
    return None


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
    app.add_middleware(OriginGuardMiddleware)  # R2 CSRF 收严：非 GET 同源校验（§⑩）
    _register_panel_error_handler(app)
    app.state.state_root = str(layout.root) if state_root is not None else None
    app.state.loop_runtime = runtime or LoopRuntime(
        layout=layout,
        cc=DryRunCCClient(audit_dir=layout.audit_dir),
        budget=BudgetConfig(),
    )
    app.include_router(routes.router)
    app.include_router(routes.root_router)
    app.include_router(sse_router)
    # SSE 装配（只读桥，不依赖 daemon 锁）：hub 绑事件循环 + DirWatcher 盯状态根
    # R5'：roots 收敛为单根（原 4 根互相包含——白起 3 条 FSEvents 流白扫 3 遍）；
    # monitor/ticks 高频写子树经 skip 谓词出扫描面（外仓增长与 SSE 桥成本解耦）。
    hub = StreamHub()
    app.state.stream_hub = hub
    app.state.sse_layout = layout
    ticks_dir = layout.monitor_dir / "ticks"
    watcher = DirWatcher(
        roots=(layout.root,),
        on_change=hub.publish_threadsafe,
        to_event=lambda path: to_event_name(path, layout),
        skip=lambda path: path.is_relative_to(ticks_dir),
    )
    app.state.dir_watcher = watcher
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
    return app


def _register_panel_error_handler(app: FastAPI) -> None:
    """htmx 面板错误卡内容协商（0.0.24）：/api/panel/* + HX-Request 的 4xx/5xx 统一渲
    HTML 错误卡 fragment（保留原状态码），配合 index.html htmx-config responseHandling
    swap:true 使错误原因落入操作容器原位；其余（JSON 孪生端点/curl）回落 JSON 原样。
    """

    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    def _is_panel_htmx(request) -> bool:
        return request.url.path.startswith("/api/panel/") and (
            request.headers.get("hx-request") == "true"
        )

    def _detail_text(exc: Exception) -> str:
        if isinstance(exc, RequestValidationError):
            try:
                return str(exc.errors(include_url=False))
            except TypeError:  # 新版 ValidationException 无 include_url 形参
                return str(exc)
        return str(getattr(exc, "detail", ""))

    @app.exception_handler(StarletteHTTPException)
    async def panel_http_exception(request, exc):
        from stock_trading_agent.server.panel import render_error_card

        if _is_panel_htmx(request):
            retry = str(request.url) if request.method == "GET" else ""
            return HTMLResponse(
                render_error_card(exc.status_code, _detail_text(exc), retry_path=retry),
                status_code=exc.status_code,
                headers=getattr(exc, "headers", None),
            )
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"detail": getattr(exc, "detail", None)},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def panel_validation_exception(request, exc):
        from fastapi.responses import JSONResponse

        if _is_panel_htmx(request):
            from stock_trading_agent.server.panel import render_error_card

            retry = str(request.url) if request.method == "GET" else ""
            return HTMLResponse(
                render_error_card(422, _detail_text(exc), retry_path=retry), status_code=422
            )
        try:
            detail = exc.errors(include_url=False)
        except TypeError:
            detail = str(exc)
        return JSONResponse({"detail": detail}, status_code=422)


def _make_lifespan(enable: bool, runtime: LoopRuntime | None, layout: StateLayout) -> object:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = app.state.loop_runtime
        if enable and not rt.start():  # daemon 锁失败/决策链断链：降级只读（面板照常服务）
            reason = getattr(rt, "chain_error", None) or "daemon.lock 被持有"
            print(f"[sta] 降级只读：{reason}（root={layout.root}）")
        import asyncio

        try:
            rt.apply_l2_config(probe=True)  # L2 开启时探测认证态联动预算闸（决策 l2_probe）
        except Exception as exc:  # 断链文件上 l2_probe 决策落盘会再抛：不炸面板（0.0.15）
            print(f"[sta] apply_l2_config 降级跳过：{exc}")
        app.state.stream_hub.bind_loop(asyncio.get_running_loop())
        app.state.dir_watcher.start()
        yield
        app.state.dir_watcher.stop()
        if enable:
            rt.stop()

    return lifespan
