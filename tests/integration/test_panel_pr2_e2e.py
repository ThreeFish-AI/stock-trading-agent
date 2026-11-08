"""0.0.16 面板呈现层欠账清偿端到端：③facts 列与卡联动、④原型六件、①oob 计数带、
⑥t1_review 小卡、⑦会话登记表、briefing 关键价位 kchip、通知锁屏句。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import ORIGIN_HEADERS
from stock_trading_agent.loop.monitor_events import market_today
from stock_trading_agent.loop.t1 import T1Runner
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.server.app import create_app
from stock_trading_agent.state.paths import StateLayout


def _app(tmp_path: Path):
    layout = StateLayout(root=tmp_path / "state")
    rt = LoopRuntime(layout=layout, t1=T1Runner(), watcher_enabled=False)
    return create_app(state_root=layout.root, runtime=rt), rt, layout


def _seed_events(layout: StateLayout, day: str) -> None:
    layout.monitor_events_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {  # 距线 breach（price<level）+ mech_risk
            "ts": "09:31:00",
            "code": "600519",
            "market": "cn",
            "kind": "real",
            "rule": "stop_break",
            "level": "T0",
            "notify": True,
            "review": "止损复核",
            "mech_risk": {"severity": "high"},
            "facts": {"price": 9.5, "level": 10.0},
        },
        {  # 距线 near（0<pct≤3）+ 名称链命中 + 无 mech_risk
            "ts": "09:35:00",
            "code": "000001",
            "market": "cn",
            "kind": "real",
            "rule": "ma20_near",
            "level": "T0",
            "notify": False,
            "review": "",
            "facts": {"price": 10.2, "level": 10.0},
        },
    ]
    layout.monitor_events_file(day).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    names = layout.root / "names.json"
    names.parent.mkdir(parents=True, exist_ok=True)
    names.write_text(json.dumps({"600519": "示例·酱香"}), encoding="utf-8")


class TestMonitorPageFacts:
    def test_events_table_facts_columns_and_detail_link(self, tmp_path: Path):
        app, _, layout = _app(tmp_path)
        day = market_today()
        _seed_events(layout, day)
        (layout.advice_dir / day).mkdir(parents=True, exist_ok=True)
        (layout.advice_dir / day / "600519.md").write_text(
            "---\ncode: '600519'\n---\n卡", encoding="utf-8"
        )
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/events").text
        assert "示例·酱香" in frag  # 名称链注入
        assert "9.5" in frag and "-5.00%" in frag and "dist-breach" in frag  # 现价/距线 breach
        assert "+2.00%" in frag and "dist-near" in frag  # near 判据
        assert "mech_risk high" in frag  # riskchip
        assert f'hx-get="/api/panel/advice-detail/{day}/600519"' in frag
        assert 'hx-target="#advice-detail"' in frag and ">详情</button>" in frag
        # 无复核短语行（000001）无盯盘复核按钮，但有详情（无卡 → 也不给详情）
        assert "盯盘复核 000001" not in frag

    def test_advice_library_detail_button_targets_second_slot(self, tmp_path: Path):
        app, _, layout = _app(tmp_path)
        day = market_today()
        (layout.advice_dir / day).mkdir(parents=True, exist_ok=True)
        (layout.advice_dir / day / "600519.md").write_text(
            "---\ncode: '600519'\ncandidate_action: 持有\nfinal_signal: hold\n"
            "prev_final_signal: hold\nt1_sell_pct: 50\nt1_sell_tier: half\n"
            f"verdict_date: {day}\nopinion_data_date: {day}\n---\n内容\n",
            encoding="utf-8",
        )
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/advice").text
        # 0.0.15 P1 并轨后：code 单元格即链接（无独立详情按钮列）
        assert 'hx-target="#advice-detail-2"' in frag
        assert f'hx-get="/api/panel/advice-detail/{day}/600519"' in frag

    def test_notifications_lockscreen_note(self, tmp_path: Path):
        app, _, _layout = _app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/notifications").text
        assert "锁屏不保证送达" in frag


class TestPaperPageSix:
    def _seed_portfolio(self, layout: StateLayout):
        from stock_trading_agent.paper import ledger as paper_ledger
        from stock_trading_agent.paper.model import SimPortfolio, Tranche

        pf = SimPortfolio(budget=100_000, cash=60_000)
        pf = paper_ledger.open_position(
            pf,
            "600519",
            "A",
            [
                Tranche(leg="T1_open", lots=1, ref_price=10.0),
                Tranche(leg="T2_limit", lots=1, ref_price=9.5, status="filled"),
            ],
            entry_ref=10.0,
        )
        pos = pf.position("600519")
        pos.lots = 1
        pos.avg_cost = 10.0
        pos.last_price = 11.0
        from stock_trading_agent.paper.model import EquityPoint

        pf.equity_history = [
            EquityPoint(
                date="2026-11-06", equity=100_000, cash=60_000, market_value=40_000, nav=1.0
            ),
            EquityPoint(
                date="2026-11-07", equity=100_500, cash=60_000, market_value=40_500, nav=1.005
            ),
            EquityPoint(
                date="2026-11-08", equity=101_000, cash=60_000, market_value=41_000, nav=1.01
            ),
        ]
        paper_ledger.persist(pf, layout.sim_portfolio_path)
        names = layout.root / "names.json"
        names.parent.mkdir(parents=True, exist_ok=True)
        names.write_text(json.dumps({"600519": "示例·酱香"}), encoding="utf-8")

    def test_six_pieces_and_totals_anchor(self, tmp_path: Path):
        app, _, layout = _app(tmp_path)
        self._seed_portfolio(layout)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            view = client.get("/api/paper/view").json()
            frag = client.get("/api/panel/paper").text
        totals = view["totals"]
        assert totals["market_value"] == 1100.0  # 1 手 × board_lot 100 × 11.0
        assert totals["cash"] == 60000.0
        assert totals["total"] == 61100.0
        assert totals["nav"] == round(61100.0 / 100_000, 4)
        assert "示例·酱香" in frag  # 名称列
        assert "建仓中" in frag and "st-open" in frag  # 状态 pill（pending_open）
        assert '<tr class="open">' in frag  # 建仓高亮
        assert 'class="leg filled"' in frag  # 批徽章 .leg 形态
        assert "pf" in frag and "+10.00%" in frag  # 盈亏红涨绿跌
        assert "Sharpe" in frag and "最大回撤" in frag and "胜率" in frag  # 绩效三格
        assert frag.count("现金") == 1  # 旧 muted 行已删，汇总条唯一
        assert "<text" in frag and "1.010" in frag  # 带轴 SVG 刻度 + 末点标签

    def test_totals_pure_function_anchor(self):
        from stock_trading_agent.server.stats import paper_totals

        t = paper_totals([{"market_value": 11000.0}, {"market_value": 4600.0}], 60_000.0, 100_000.0)
        assert t == {"market_value": 15600.0, "cash": 60000.0, "total": 75600.0, "nav": 0.756}
        assert paper_totals([], 0.0, 0.0)["nav"] is None


class TestOverviewOob:
    def test_overview_carries_oob_counters(self, tmp_path: Path):
        app, _, layout = _app(tmp_path)
        _seed_events(layout, market_today())
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/overview").text
        assert 'id="ov-sub" hx-swap-oob="true"' in frag
        assert 'id="ov-ev-count" hx-swap-oob="true" class="cm"' in frag
        assert 'id="ov-todo-count" hx-swap-oob="true" class="cm"' in frag
        assert "事件 2 条（1 条降档高亮）" in frag  # mech_risk 计数

    def test_briefing_key_level_kchips_conditional(self, tmp_path: Path):
        from stock_trading_agent.loop.briefing import persist_card

        app, _, layout = _app(tmp_path)
        day = market_today()
        persist_card(layout, day, "600519", "# 盘前晨报 600519\n复核项：示例\n")
        layout.root.mkdir(parents=True, exist_ok=True)
        (layout.root / "signals.json").write_text(
            json.dumps(
                [
                    {
                        "code": "600519",
                        "signal": "hold",
                        "date": day,
                        "key_levels": {"stop": 9.4, "buy": 9.8, "ma20": 10.1},
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/briefing").text
        assert ">止损</span>" in frag and "9.4" in frag
        assert ">买点</span>" in frag and "9.8" in frag
        assert ">MA20</span>" in frag and "10.1" in frag

    def test_syscard_probe_time(self, tmp_path: Path):
        from stock_trading_agent.state.agent_config import load_agent_config, save_agent_config

        app, _, layout = _app(tmp_path)
        save_agent_config(layout.agent_config_path, load_agent_config(layout.agent_config_path))
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/syscard").text
        # save 即盖 updated_at 时间戳：断言结构与时戳在场（save_agent_config 现行为）
        assert 'title="probe 认证态判定时刻">probe ' in frag


class TestStatsAndAudit:
    def test_stats_fragment_renders_t1_review(self, tmp_path: Path):
        app, _, layout = _app(tmp_path)
        layout.root.mkdir(parents=True, exist_ok=True)
        (layout.root / "ledger.agent.json").write_text(
            json.dumps({"attribution": {"agent:monitor_review": {"wins": 2, "losses": 1}}}),
            encoding="utf-8",
        )
        # 种子决策行：advice_card_written 命中 ledger（t1_review_hitrate 判据）
        from stock_trading_agent.state.decisions import DecisionLog

        log = DecisionLog()
        log.append(
            "2026-11-08T02:00:00+00:00",
            "advice_card_written",
            {
                "task_id": "t",
                "code": "600519",
                "date": market_today(),
                "path": "x",
                "candidate_action": "持有",
                "final_signal": "hold",
            },
            path=layout.decisions_path,
        )
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/stats").text
        assert "T+1 复核命中率" in frag

    def test_sessions_table_and_index_card(self, tmp_path: Path):
        app, _, layout = _app(tmp_path)
        from stock_trading_agent.state.sessions import SessionRecord, SessionsState
        from stock_trading_agent.state.store import atomic_write_json

        state = SessionsState()
        state.upsert(
            SessionRecord(
                session_uuid="aabbccdd-0000-0000-0000-000000000001",
                task_id="monitor_review:600519:2026-11-08:100000",
                transcript_path="/tmp/x/aabb.jsonl",
                usage_tokens={"total": 1234},
                cost_usd_equiv=0.05,
                ts="2026-11-08T02:00:00+00:00",
            )
        )
        atomic_write_json(layout.sessions_path, state.to_dict())
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/sessions").text
        assert "aabbccdd-0000" in frag and "monitor_review:600519"[:12] in frag
        assert 'title="' in frag and "1,234" in frag and "0.05" in frag
        assert "三件套互查" in frag
        html_txt = Path("src/stock_trading_agent/server/static/index.html").read_text(
            encoding="utf-8"
        )
        assert 'id="au-sessions"' in html_txt and "/api/panel/sessions" in html_txt
