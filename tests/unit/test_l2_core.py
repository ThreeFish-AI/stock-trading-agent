"""PR-G 新增测试族：agent_config、t2_worthy、auto-T2/L2 闸、CSRF 守卫、L2 端点 e2e。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import ORIGIN_HEADERS
from stock_trading_agent.loop.monitor_events import (
    T2_WORTHY_RULES,
    MonitorEvent,
    parse_line,
    t2_worthy,
)
from stock_trading_agent.loop.t1 import T1Runner
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.server.app import create_app
from stock_trading_agent.server.origin_guard import origin_allowed
from stock_trading_agent.state.agent_config import AgentConfig, load_agent_config, save_agent_config
from stock_trading_agent.state.paths import StateLayout


class TestAgentConfig:
    def test_roundtrip_and_default_off(self, tmp_path: Path):
        path = tmp_path / "config.json"
        save_agent_config(path, AgentConfig(l2_enabled=True, billing_mode="subscription"))
        cfg = load_agent_config(path)
        assert cfg.l2_enabled and cfg.billing_mode == "subscription" and cfg.updated_at

    def test_absent_corrupt_unknown_version_default_off(self, tmp_path: Path):
        assert load_agent_config(tmp_path / "none.json").l2_enabled is False
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        assert load_agent_config(bad).l2_enabled is False
        future = tmp_path / "v2.json"
        future.write_text(json.dumps({"version": 99, "l2_enabled": True}), encoding="utf-8")
        assert load_agent_config(future).l2_enabled is False  # 宁拒不放


class TestT2Worthy:
    def _ev(self, **kw) -> MonitorEvent:
        base = dict(
            ts="t",
            code="600519",
            market="cn",
            kind="real",
            rule="source_dropped",
            level="T0",
            notify=True,
        )
        base.update(kw)
        return MonitorEvent(**base)

    def test_rule_set_pinned_verbatim(self):
        assert T2_WORTHY_RULES == frozenset(
            {
                "stop_break",
                "stop_break_confirmed",
                "whipsaw",
                "target_hit",
                "abnormal_move",
                "t2_pending_fill",
                "ma20_break",
                "trailing_armed",
                "trailing_exit",
                "band_lo_touch",
                "band_hi_touch",
                "band_trail_break",
            }
        )

    def test_matrix(self):
        assert t2_worthy(self._ev(rule="stop_break"))
        assert t2_worthy(self._ev(mech_risk={"severity": "high"}))  # mech_risk 非 None
        assert not t2_worthy(self._ev(rule="storm_digest"))  # 运维类零短语
        assert not t2_worthy(self._ev(rule="stop_break", suppress_reason="cooldown"))
        assert not t2_worthy(self._ev(rule="stop_break", code="*"))
        assert not t2_worthy(self._ev(rule="stop_break", level="T1"))


class _OkT1:
    """CI 无 vibe-stock skill 目录：注入恒成功 T1 runner（零网络零子进程）。"""

    def run(self, code: str, *, workdir):
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "context-pack.md").write_text("# pack", encoding="utf-8")
        from stock_trading_agent.loop.t1 import T1Outcome

        return T1Outcome(ok=True, workdir=workdir, reasons=())


def _make_runtime(tmp_path: Path) -> LoopRuntime:
    layout = StateLayout(root=tmp_path / "state")
    return LoopRuntime(layout=layout, t1=_OkT1(), watcher_enabled=False)


def _decisions(rt) -> list[dict]:
    raw = rt.layout.decisions_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in raw]


def _t2_event(code: str = "600519", rule: str = "stop_break") -> MonitorEvent:
    return parse_line(
        json.dumps(
            {
                "ts": "09:31:00",
                "code": code,
                "market": "cn",
                "kind": "real",
                "rule": rule,
                "level": "T0",
                "notify": True,
                "review": "止损复核",
            },
            ensure_ascii=False,
        )
    )


class TestAutoT2AndGate:
    def test_l2_off_no_auto_t2_but_logged(self, tmp_path: Path):
        rt = _make_runtime(tmp_path)
        rt.record_monitor_event(_t2_event())
        q = json.loads(rt.layout.queue_path.read_text(encoding="utf-8"))
        assert not [t for t in q["tasks"] if t["kind"] == "monitor_review"]  # 零自动入队
        decisions = _decisions(rt)
        assert any(d["kind"] == "monitor_event" for d in decisions)  # 事件照记

    def test_l2_on_auto_t2_once_per_day(self, tmp_path: Path):
        rt = _make_runtime(tmp_path)
        save_agent_config(rt.layout.agent_config_path, AgentConfig(l2_enabled=True))
        rt.record_monitor_event(_t2_event())
        rt.record_monitor_event(_t2_event())  # 二次事件：当日一次幂等
        q = json.loads(rt.layout.queue_path.read_text(encoding="utf-8"))
        auto = [t for t in q["tasks"] if t["kind"] == "monitor_review"]
        assert len(auto) == 1 and auto[0]["source"] == "monitor"
        decisions = _decisions(rt)
        submit = [d for d in decisions if d["kind"] == "task_submitted"]
        assert submit and submit[-1]["payload"].get("trigger", {}).get("rule") == "stop_break"

    def test_l2_consume_gate_refuses_monitor_source(self, tmp_path: Path):
        rt = _make_runtime(tmp_path)
        save_agent_config(rt.layout.agent_config_path, AgentConfig(l2_enabled=True))
        rt.record_monitor_event(_t2_event())
        # 关闸后消费：failed + advice_refused(l2_disabled)——面板关闸即刻生效于在队任务
        save_agent_config(rt.layout.agent_config_path, AgentConfig(l2_enabled=False))
        task = rt.consume_one()  # 先消费同事件的 T1（零 LLM 不受 L2 闸）
        assert task is not None and task.kind == "t1_refresh" and task.status == "done"
        task = rt.consume_one()  # 再消费 auto-T2：L2 已关 → failed
        assert task is not None and task.kind == "monitor_review"
        assert task.status == "failed"
        assert "L2" in (task.error or "")
        decisions = _decisions(rt)
        refused = [d for d in decisions if d["kind"] == "advice_refused"]
        assert refused and refused[-1]["payload"]["reason_code"] == "l2_disabled"

    def test_panel_source_unaffected_by_l2_gate(self, tmp_path: Path):
        rt = _make_runtime(tmp_path)
        rt.submit(kind="diagnose_quick", code="600519")  # panel 源
        task = rt.consume_one()  # L2 关闭：panel 源不受闸
        assert task is not None and task.status == "done"

    def test_apply_l2_config_links_budget(self, tmp_path: Path):
        rt = _make_runtime(tmp_path)
        save_agent_config(rt.layout.agent_config_path, AgentConfig(l2_enabled=True))
        rt.apply_l2_config(probe=False)  # DryRun probe=False：读存值
        assert rt.loop.budget.enabled is True
        assert rt.loop.budget.billing_mode == "api"  # billing_mode None 回落 api 收紧


class TestOriginGuard:
    def test_pure_function(self):
        assert origin_allowed({"origin": "http://127.0.0.1:8730"})
        assert origin_allowed({"origin": "http://testserver"})
        assert origin_allowed({"referer": "http://localhost:9999/some"})
        assert not origin_allowed({})
        assert not origin_allowed({"origin": "https://evil.example"})

    def test_middleware_end_to_end(self, tmp_path: Path):
        from stock_trading_agent.server.app import create_app as mk

        with TestClient(mk(state_root=tmp_path)) as client:
            assert (
                client.post(
                    "/api/queue/submit", json={"kind": "diagnose_quick", "code": "600519"}
                ).status_code
                == 403
            )  # 无 Origin → 拒
            with_headers = client.post(
                "/api/queue/submit",
                json={"kind": "diagnose_quick", "code": "600519"},
                headers={"Origin": "https://evil.example"},
            )
            assert with_headers.status_code == 403  # 跨源 → 拒（客户端默认头被覆盖）
            assert client.get("/api/health").status_code == 200  # GET 不校验


class TestL2EndpointE2E:
    def _app(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        rt = LoopRuntime(layout=layout, t1=T1Runner(), watcher_enabled=False)
        return create_app(state_root=layout.root, runtime=rt), rt

    def test_get_default_off_and_toggle_flow(self, tmp_path: Path):
        app, rt = self._app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            assert rt.start()
            assert client.get("/api/l2").json()["l2_enabled"] is False
            resp = client.post("/api/l2", json={"enabled": True})
            assert resp.status_code == 200
            body = resp.json()
            assert body["l2_enabled"] is True and body["budget_enabled"] is True
            cfg = load_agent_config(rt.layout.agent_config_path)
            assert cfg.l2_enabled and cfg.billing_mode  # probe 持久化（dry-run → api）
            decisions = client.get("/api/decisions").json()
            kinds = [d["kind"] for d in decisions]
            assert "l2_toggled" in kinds and "l2_probe" in kinds
            # 关闭回落
            resp = client.post("/api/l2", json={"enabled": False})
            assert resp.json()["budget_enabled"] is False
            rt.stop()

    def test_toggle_requires_active_runtime_503(self, tmp_path: Path):
        app, _ = self._app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            assert client.post("/api/l2", json={"enabled": True}).status_code == 503

    def test_fragment_served(self, tmp_path: Path):
        app, _ = self._app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/l2").text
            assert "L2" in frag and "关闭" in frag  # 默认关态
