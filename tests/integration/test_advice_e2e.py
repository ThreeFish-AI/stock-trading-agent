"""T2 建议卡端到端：提交→会话→断言器→写者例外落卡→面板⑤读到；降级/锚③/闸/locks 回归。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import ORIGIN_HEADERS
from stock_trading_agent.cc_client.contract import CCResult
from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.monitor_events import market_today
from stock_trading_agent.server.app import create_app


class CardCC(DryRunCCClient):
    """合成指定 structured_output 的通道（默认走 DryRun 全量卡载荷）。"""

    def __init__(self, structured: dict | None = None) -> None:
        super().__init__()
        self._structured = structured

    def spawn_once(self, req):
        if self._structured is None:
            return super().spawn_once(req)
        return CCResult(
            exit_code=0,
            envelope={"subtype": "success", "session_id": req.session_uuid, "total_cost_usd": 0.0},
            structured_output=dict(self._structured),
            result_line_present=True,
            transcript_path=Path("/dev/null"),
            usage_tokens={"input_tokens": 0, "output_tokens": 0},
        )


def make_app(tmp_path: Path, cc=None):
    from stock_trading_agent.loop.t1 import T1Runner
    from stock_trading_agent.loop.worker import LoopRuntime
    from stock_trading_agent.state.paths import StateLayout

    layout = StateLayout(root=tmp_path / "state")
    runtime = LoopRuntime(
        layout=layout,
        cc=cc if cc is not None else DryRunCCClient(audit_dir=layout.audit_dir),
        t1=T1Runner(),
        watcher_enabled=False,
    )
    return create_app(state_root=layout.root, runtime=runtime)


class TestFullChain:
    def test_card_written_and_served(self, tmp_path: Path):
        app = make_app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            resp = client.post(
                "/api/queue/submit", json={"kind": "monitor_review", "code": "600519"}
            )
            assert resp.status_code == 201
            task = client.app.state.loop_runtime.consume_one()
            assert task is not None and task.status == "done"

            day = market_today()
            layout_root = Path(client.app.state.state_root)
            card_file = layout_root / "monitor" / "advice" / day / "600519.md"
            assert card_file.exists(), "建议卡应落盘（写者例外）"
            md = card_file.read_text(encoding="utf-8")
            assert "monitor-advice/1" in md and "持有观察" in md
            assert "不构成投资建议" in md  # 免责尾注

            body = client.get("/api/advice").json()
            assert body["cards"] and body["cards"][0]["code"] == "600519"
            assert body["cards"][0]["candidate_action"] == "持有观察"

            detail = client.get(f"/api/advice/{day}/600519").json()
            assert detail["md"] == md

            frag = client.get("/api/panel/advice").text
            assert "600519" in frag and "持有观察" in frag

            decisions = client.get("/api/decisions").json()
            kinds = [d["kind"] for d in decisions]
            assert "advice_card_written" in kinds and "task_done" in kinds

    def test_schema_violation_refused_no_card(self, tmp_path: Path):
        bad = CardCC(
            structured={  # 非法候选动作 → 断言拒 → 无卡
                "kind": "monitor_review",
                "outcome": "card",
                "disclaimer": "x",
                "candidate_action": "梭哈",
                "final_signal": "hold",
                "verdict_date": market_today(),
                "opinion_data_date": market_today(),
                "replayed_opinions": 2,
                "t1_sell_pct": 50,
                "t1_sell_tier": "半",
                "prev_diff": {"prev_final_signal": "none", "note": "n"},
                "trend_ref": {
                    "band_rows": [{"horizon": "1d", "low": 1, "high": 2, "prob_touch_low_pct": 30}]
                },
                "rationale": "r",
            }
        )
        app = make_app(tmp_path, cc=bad)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            client.post("/api/queue/submit", json={"kind": "monitor_review", "code": "600519"})
            task = client.app.state.loop_runtime.consume_one()
            assert task is not None and task.status == "failed"
            day = market_today()
            assert not (Path(client.app.state.state_root) / "monitor" / "advice" / day).exists()
            decisions = client.get("/api/decisions").json()
            assert any(d["kind"] == "advice_refused" for d in decisions)

    def test_facts_only_degrades_done_without_card(self, tmp_path: Path):
        degraded = CardCC(
            structured={
                "kind": "monitor_review",
                "outcome": "facts_only",
                "facts_note": "触发事实：跌破止损线，待收盘复核。",
                "disclaimer": "不构成投资建议；AI 生成，用户复核前置",
            }
        )
        app = make_app(tmp_path, cc=degraded)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            client.post("/api/queue/submit", json={"kind": "monitor_review", "code": "600519"})
            task = client.app.state.loop_runtime.consume_one()
            assert task is not None and task.status == "done"  # 会话自报降级=正常 done
            day = market_today()
            advice_dir = Path(client.app.state.state_root) / "monitor" / "advice"
            assert not (advice_dir / day / "600519.md").exists()
            refused = client.get("/api/advice").json()["refused"]
            assert refused and refused[0]["origin"] == "session_degraded"
            assert "待收盘复核" in refused[0]["facts_note"]

    def test_anchor3_end_to_end(self, tmp_path: Path):
        layout_root = tmp_path / "state"
        layout_root.mkdir()
        (layout_root / "signals.json").write_text(
            json.dumps([{"code": "600519", "signal": "hold"}]), encoding="utf-8"
        )
        aggressive = CardCC(
            structured={
                "kind": "monitor_review",
                "outcome": "card",
                "disclaimer": "不构成投资建议；AI 生成，用户复核前置",
                "candidate_action": "可加仓",
                "final_signal": "buy",
                "verdict_date": market_today(),
                "opinion_data_date": market_today(),
                "replayed_opinions": 2,
                "t1_sell_pct": 100,
                "t1_sell_tier": "全部",
                "prev_diff": {"prev_final_signal": "hold", "note": "n"},
                "trend_ref": {
                    "band_rows": [{"horizon": "1d", "low": 1, "high": 2, "prob_touch_low_pct": 30}]
                },
                "rationale": "r",
            }
        )
        app = make_app(tmp_path, cc=aggressive)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            client.post("/api/queue/submit", json={"kind": "monitor_review", "code": "600519"})
            task = client.app.state.loop_runtime.consume_one()
            assert task is not None and task.status == "failed"
            assert "锚③" in (task.error or "")
            day = market_today()
            assert not (Path(client.app.state.state_root) / "monitor" / "advice" / day).exists()

    def test_quota_gate_end_to_end(self, tmp_path: Path):
        app = make_app(tmp_path)
        runtime = app.state.loop_runtime
        market_today()
        # 预置 5 条当日 done monitor_review（不同 code，enqueued_at=now 即市场今日）
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            for i in range(5):
                client.post(
                    "/api/queue/submit", json={"kind": "monitor_review", "code": f"60051{i}"}
                )
                runtime.consume_one()
            client.post("/api/queue/submit", json={"kind": "monitor_review", "code": "601919"})
            task = runtime.consume_one()
            assert task is not None and task.status == "failed"
            assert "5 票" in (task.error or "")
            # 无 universe（不可读）且超额 → 宁拒不漏；universe 在场且 code=real → 放行
            refused = client.get("/api/advice").json()["refused"]
            assert refused and refused[-1]["origin"] == "gate"


class TestLocksRegression:
    def test_status_locks_held_vs_stale(self, tmp_path: Path):
        from conftest import hold_lock

        app = make_app(tmp_path)
        layout_root = Path(app.state.state_root)
        (layout_root / "monitor").mkdir(parents=True, exist_ok=True)
        (layout_root / "monitor" / "watch.lock").touch()  # 陈旧锁文件
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            body = client.get("/api/monitor/status").json()
            assert body["locks"]["watch"] is False  # 存在≠持有（0.0.5 收敛回归钉）
            with hold_lock(layout_root / "monitor" / "watch.lock"):
                body = client.get("/api/monitor/status").json()
                assert body["locks"]["watch"] is True


class TestReviewExport:
    """0.0.25「标记已复核 + 导出脱敏卡片」：advice_reviewed 决策往返、降级序、导出三保险。"""

    def _card_via_full_chain(self, client) -> tuple[str, str]:
        client.post("/api/queue/submit", json={"kind": "monitor_review", "code": "600519"})
        task = client.app.state.loop_runtime.consume_one()
        assert task is not None and task.status == "done"
        return market_today(), "600519"

    def test_review_roundtrip_and_idempotent(self, tmp_path: Path):
        app = make_app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            day, code = self._card_via_full_chain(client)
            frag = client.get(f"/api/panel/advice-detail/{day}/{code}?mount=mo").text
            assert "标记已复核" in frag and 'hx-target="#advice-detail"' in frag
            assert "导出脱敏卡片" in frag

            assert app.state.loop_runtime.start()
            resp = client.post(
                "/api/panel/advice/review", data={"date": day, "code": code, "mount": "mo"}
            )
            assert resp.status_code == 200
            assert "已复核" in resp.text and "标记已复核" not in resp.text  # 按钮位换徽章

            decisions = client.get("/api/decisions").json()
            reviewed = [d for d in decisions if d["kind"] == "advice_reviewed"]
            # NR-5：payload 恰两字段
            assert reviewed and reviewed[-1]["payload"] == {"date": day, "code": code}

            # 幂等：重复 POST 再记一条、呈现不回退（沿 briefing_ack 语义不 409）
            resp2 = client.post("/api/panel/advice/review", data={"date": day, "code": code})
            assert resp2.status_code == 200 and "已复核" in resp2.text
            reviewed2 = [
                d for d in client.get("/api/decisions").json() if d["kind"] == "advice_reviewed"
            ]
            assert len(reviewed2) == len(reviewed) + 1

            # ⑤卡库复核列 + advice 列表 reviewed 字段
            lib = client.get("/api/panel/advice").text
            assert "复核</th>" in lib and "已复核" in lib
            assert client.get("/api/advice").json()["cards"][0]["reviewed"] is True
        app.state.loop_runtime.stop()

    def test_review_degradations(self, tmp_path: Path):
        app = make_app(tmp_path)  # runtime 未 start → 503
        day = market_today()
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            resp = client.post("/api/panel/advice/review", data={"date": day, "code": "600519"})
            assert resp.status_code == 503
        rt = app.state.loop_runtime
        assert rt.start()
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            day, code = self._card_via_full_chain(client)
            assert (
                client.post(
                    "/api/panel/advice/review", data={"date": "2020-01-01", "code": code}
                ).status_code
                == 422
            )  # 非当日
            assert (
                client.post(
                    "/api/panel/advice/review", data={"date": day, "code": "abc"}
                ).status_code
                == 422
            )  # code 非法
            assert (
                client.post(
                    "/api/panel/advice/review", data={"date": day, "code": "601919"}
                ).status_code
                == 404
            )  # 卡不在场
        rt.stop()

    def test_export_serves_disk_card_verbatim(self, tmp_path: Path):
        app = make_app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            day, code = self._card_via_full_chain(client)
            md = (
                Path(client.app.state.state_root) / "monitor" / "advice" / day / f"{code}.md"
            ).read_text(encoding="utf-8")
            resp = client.get(f"/api/advice/export/{day}/{code}")
            assert resp.status_code == 200
            assert resp.text == md  # 零改写下发
            assert resp.headers["content-disposition"] == (
                f'attachment; filename="advice-{day}-{code}.md"'
            )

    def test_export_path_guards(self, tmp_path: Path):
        app = make_app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            for bad in (
                "/api/advice/export/2026-11-09/600519/..",  # 多段
                "/api/advice/export/bad-date/600519",  # 日期正则
                "/api/advice/export/2026-11-09/61a19",  # code 正则
                "/api/advice/export/2026-11-09/601919",  # 边界内但卡缺席
            ):
                assert client.get(bad).status_code == 404, bad


class TestPriceSources:
    """§⑫ 现价源三源合并 e2e（ticks 轮询价 / T1 evidence 价 / sim·events 既有源）。"""

    @staticmethod
    def _seed(tmp_path: Path, *, ticks_asof: str, t1: bool = True) -> None:
        layout_root = tmp_path / "state"
        layout_root.mkdir(exist_ok=True)
        (layout_root / "signals.json").write_text(
            json.dumps([{"code": "600519", "signal": "hold", "key_levels": {"stop": 9.0}}]),
            encoding="utf-8",
        )
        ticks_dir = layout_root / "monitor" / "ticks"
        ticks_dir.mkdir(parents=True, exist_ok=True)
        (ticks_dir / f"{market_today()}.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {"code": "600519", "price": 9.4, "ts_utc8": f"{market_today()} 09:31:00"}
                    ),
                    json.dumps({"code": "600519", "price": 9.6, "ts_utc8": ticks_asof}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        if t1:
            t1_dir = layout_root / "agent" / "runs" / market_today() / "t1" / "600519"
            t1_dir.mkdir(parents=True, exist_ok=True)
            (t1_dir / "evidence.json").write_text(
                json.dumps(
                    {
                        "blocks": {"quote": {"price": 9.5, "state": "available"}},
                        "generated_at": f"{market_today()}T08:00:00+08:00",  # 比 ticks 旧
                    }
                ),
                encoding="utf-8",
            )

    def test_ticks_latest_wins_and_stale_flag(self, tmp_path: Path):
        today = market_today()
        self._seed(tmp_path, ticks_asof=f"{today} 10:05:00")  # 市场今日 → 不陈旧
        app = make_app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            row = next(
                r for r in client.get("/api/signals").json()["rows"] if r["code"] == "600519"
            )
            assert row["price"] == 9.6 and row["price_source"] == "ticks"  # 后行覆盖+asof 胜 t1
            assert row["price_stale"] is False

    def test_stale_asof_marks_row(self, tmp_path: Path):
        import datetime as _dt

        yesterday = (_dt.datetime.fromisoformat(market_today()) - _dt.timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        self._seed(tmp_path, ticks_asof=f"{yesterday} 15:00:00", t1=False)  # 昨日 ticks → 陈旧
        app = make_app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            row = next(
                r for r in client.get("/api/signals").json()["rows"] if r["code"] == "600519"
            )
            assert row["price"] == 9.6 and row["price_stale"] is True

    def test_detail_fragment_price_block_and_mount(self, tmp_path: Path):
        today = market_today()
        self._seed(tmp_path, ticks_asof=f"{today} 10:05:00")
        app = make_app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            client.post("/api/queue/submit", json={"kind": "monitor_review", "code": "600519"})
            task = client.app.state.loop_runtime.consume_one()
            assert task is not None and task.status == "done"
            frag = client.get(f"/api/panel/advice-detail/{today}/600519?mount=mo").text
            assert "现价（多源 asof）" in frag and "ticks" in frag and "10:05" in frag
            assert 'hx-target="#advice-detail"' in frag  # mount=mo → ③页挂点
            body = client.get(f"/api/advice/{today}/600519").json()
            assert body["price"]["price"] == 9.6 and body["price"]["source"] == "ticks"
