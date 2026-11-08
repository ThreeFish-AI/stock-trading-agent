"""serve 端口冲突预检（0.0.11）：connect 探测 / probe 身份判别 / _cmd_serve 早退三分支。

真实 socket + 线程 http.server，全临时端口（bind ("127.0.0.1", 0)）零端口碰撞。
"""

from __future__ import annotations

import argparse
import http.server
import socket
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from stock_trading_agent.__main__ import _cmd_serve
from stock_trading_agent.server import app as server_app

PANEL_JSON = b'{"ok": true, "component": "stock-trading-agent-panel", "version": "0.0.11"}'


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def _http_server(payload: bytes) -> Iterator[int]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # http.server 约定名
            if self.path != "/api/health":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:  # 静默防输出噪音
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class TestPortProbe:
    def test_listening_socket_detected(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            assert server_app.port_is_listening("127.0.0.1", port) is True

    def test_free_port_not_listening(self) -> None:
        assert server_app.port_is_listening("127.0.0.1", _free_port()) is False


class TestProbePanel:
    def test_identifies_panel_health(self) -> None:
        with _http_server(PANEL_JSON) as port:
            info = server_app.probe_panel("127.0.0.1", port)
            assert info is not None
            assert info["component"] == "stock-trading-agent-panel"
            assert info["version"] == "0.0.11"

    def test_foreign_component_rejected(self) -> None:
        with _http_server(b'{"ok": true, "component": "other-service"}') as port:
            assert server_app.probe_panel("127.0.0.1", port) is None

    def test_garbage_body_rejected(self) -> None:
        with _http_server(b"not-json") as port:
            assert server_app.probe_panel("127.0.0.1", port) is None

    def test_refused_port_returns_none(self) -> None:
        assert server_app.probe_panel("127.0.0.1", _free_port(), timeout_s=0.5) is None


class TestServePreflight:
    """_cmd_serve 早退三分支（monkeypatch app 模块属性：函数内 import 每次调用时解析）。"""

    def _run(
        self,
        monkeypatch,
        capsys,
        *,
        listening: bool,
        panel: bool,
    ) -> tuple[int, str, dict]:
        calls: dict = {}
        monkeypatch.setattr(server_app, "port_is_listening", lambda h, p: listening)
        panel_health = {"component": "stock-trading-agent-panel", "version": "9"}
        monkeypatch.setattr(
            server_app,
            "probe_panel",
            lambda h, p: panel_health if panel else None,
        )
        monkeypatch.setattr(server_app, "listener_pid", lambda p: "1041")
        monkeypatch.setattr(server_app, "create_app", lambda **kw: "app-sentinel")

        import uvicorn

        monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.setdefault("run", (app, kw)))
        rc = _cmd_serve(argparse.Namespace(port=None, graceful_shutdown=10))
        return rc, capsys.readouterr().out, calls

    def test_exit_0_when_panel_running(self, monkeypatch, capsys) -> None:
        rc, out, calls = self._run(monkeypatch, capsys, listening=True, panel=True)
        assert rc == 0
        assert "已在运行" in out and "kickstart" in out and "1041" in out
        assert "run" not in calls  # uvicorn 未启动

    def test_exit_1_when_foreign_occupant(self, monkeypatch, capsys) -> None:
        rc, out, calls = self._run(monkeypatch, capsys, listening=True, panel=False)
        assert rc == 1
        assert "未知进程" in out and "1041" in out
        assert "run" not in calls

    def test_proceeds_when_port_free(self, monkeypatch, capsys) -> None:
        rc, _out, calls = self._run(monkeypatch, capsys, listening=False, panel=False)
        assert rc == 0
        app_obj, kwargs = calls["run"]
        assert app_obj == "app-sentinel"
        assert kwargs["host"] == server_app.HOST
        assert kwargs["port"] == server_app.PORT


def test_listener_pid_free_port_none() -> None:
    """弱断言：空闲端口无 LISTEN → None（lsof 缺失/CI 环境亦恒绿）。"""
    assert server_app.listener_pid(_free_port()) is None


def test_listener_pid_filters_fd_lines_and_takes_first_pid(monkeypatch) -> None:
    """正测解析契约：-Fp 输出混 f<fd> 行须按 p 前缀过滤，多 PID 双栈取首个。"""
    seen: dict = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="p1041\nf12\np2048\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert server_app.listener_pid(8730) == "1041"
    assert any("8730" in part for part in seen["cmd"])
