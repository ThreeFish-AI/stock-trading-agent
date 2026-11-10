"""paper EOD 驱动端到端（tmp 状态根）：allocation 导入 → ticks bar → T1 判定/T2 触价/
T3 复合触发/GTC 到期/权益点 → 请求入队与决策留痕 → 幂等重跑。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from stock_trading_agent.loop.paper_eod import run_paper_eod, ticks_path
from stock_trading_agent.paper import ledger as paper_ledger
from stock_trading_agent.paper import pending as paper_pending
from stock_trading_agent.paper.allocation import new_portfolio_from_allocation
from stock_trading_agent.state.decisions import DecisionLog

TZ8 = dt.timezone(dt.timedelta(hours=8))
# allocation 行取真机 allocation.json 实形（06 portfolio.py 产物）：ma20=16.279 为 T3 回踩锚
ALLOC = {
    "budget": 100_000.0,
    "rows": [
        {
            "code": "601919",
            "price": 16.67,
            "stop": 15.84,
            "entry_ref": 16.17,
            "target1": 16.87,
            "ma20": 16.279,
            "lots": 6,
            "tranches": {"T1_open": 2, "T2_limit_entryref": 2, "T3_trigger": 2},
        }
    ],
}


def _write_ticks(layout, date: str, rows: list[dict]) -> None:
    f = ticks_path(layout, date)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _tick(code: str, date: str, **over) -> dict:
    row = {
        "code": code,
        "market": "cn",
        "price": 16.5,
        "open": 16.4,
        "high": 16.9,
        "low": 16.2,
        "prev_close": 16.67,
        "ts_utc8": f"{date}T14:57:00+08:00",
    }
    row.update(over)
    return row


def _decisions(layout) -> list[dict]:
    out = []
    if layout.decisions_path.exists():
        for raw in layout.decisions_path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                out.append(json.loads(raw))
    return out


class TestEodChain:
    def test_full_day_t1_fill_t2_touch_t3_pullback_marks(self, tmp_path: Path):
        """导入次日首个驱动日：T1 无跳空开盘成交 + T2 严格穿价 + T3 回踩触发 + 权益点。"""
        from stock_trading_agent.state.paths import StateLayout

        layout = StateLayout(root=tmp_path / "state")
        date = "2026-11-11"
        paper_ledger.persist(
            new_portfolio_from_allocation(ALLOC, opened_date="2026-11-10"),
            layout.sim_portfolio_path,
        )
        # open=16.4 ≤ 16.67×1.02≈17.00 → T1 开盘成交 @16.4；low=16.05 < 16.17 → T2 穿价
        # @min(16.4,16.17)=16.17；low ≤ ma20×1.01=16.44 ∧ close ≥ ma20×0.99 → T3 回踩 @16.279
        _write_ticks(
            layout, date, [_tick("601919", date, open=16.4, high=16.9, low=16.05, price=16.5)]
        )
        ran: list[tuple] = []
        out = run_paper_eod(
            layout,
            date=date,
            now=dt.datetime(2026, 11, 11, 16, 6, tzinfo=TZ8),
            decide=lambda k, p: ran.append((k, p)),
        )
        assert out is not None and out.bars == 1
        assert out.requests == 3  # T1 开盘批 + T2 限价批 + T3 回踩批
        assert out.judged == 2  # T1 与 T3 盖戳（T2 无戳语义——通用撮合）
        assert out.marked is True and out.converted == 0 and out.expired == 0
        kinds = [k for k, _ in ran]
        assert "paper_eod_done" in kinds and kinds.count("paper_pending_fill") == 3
        queue = paper_pending.load(layout.sim_pending_fills_path)
        by_leg = {r.tranche_leg: r for r in queue.requests}
        assert by_leg["T1_open"].fill_price == 16.4  # T1 无跳空 → 开盘价成交
        assert by_leg["T2_limit"].fill_price == 16.17  # 严格穿价 = min(open, limit)
        assert by_leg["T3_trigger"].fill_price == 16.279  # 回踩 = ma20 夹入当日区间
        pos = paper_ledger.load(layout.sim_portfolio_path).position("601919")
        stamps = {t.leg: t.judged_date for t in pos.tranches}
        assert stamps["T1_open"] == date and stamps["T3_trigger"] == date
        assert stamps["T2_limit"] is None  # T2 未盖戳（逐日撮合语义）
        hist = paper_ledger.load(layout.sim_portfolio_path).equity_history
        assert [p.date for p in hist] == [date]
        assert hist[0].equity == 100_000.0 and pos.last_price == 16.5

    def test_t1_gap_converts_to_limit_and_t3_above_target1(self, tmp_path: Path):
        from stock_trading_agent.state.paths import StateLayout

        layout = StateLayout(root=tmp_path / "state")
        date = "2026-11-11"
        paper_ledger.persist(
            new_portfolio_from_allocation(ALLOC, opened_date="2026-11-10"),
            layout.sim_portfolio_path,
        )
        # open=17.2 > 17.00 → T1 转限价；close=17.1 > target1=16.87 → T3 站上目标1 @收盘
        _write_ticks(
            layout, date, [_tick("601919", date, open=17.2, high=17.4, low=17.0, price=17.1)]
        )
        out = run_paper_eod(
            layout,
            date=date,
            now=dt.datetime(2026, 11, 11, 16, 6, tzinfo=TZ8),
            decide=lambda *a: None,
        )
        assert out is not None and out.converted == 1 and out.judged == 2
        pf = paper_ledger.load(layout.sim_portfolio_path)
        pos = pf.position("601919")
        t1 = pos.tranches[0]
        assert t1.limit_price == round(16.67 * 1.02, 2)  # 17.00（06 Stage D ×1.02）
        assert t1.expires_at == "2026-11-18"  # +7 日历日 ≈ 5 交易日（ESTIMATED）
        assert t1.status == "pending"  # 转限价不迁移状态
        queue = paper_pending.load(layout.sim_pending_fills_path)
        t3_req = next(r for r in queue.requests if r.tranche_leg == "T3_trigger")
        assert t3_req.fill_price == 17.1  # 站上目标1 → 收盘价成交

    def test_t3_window_fallback_fills_at_close(self, tmp_path: Path):
        from stock_trading_agent.state.paths import StateLayout

        layout = StateLayout(root=tmp_path / "state")
        date = "2026-11-11"
        pf = new_portfolio_from_allocation(ALLOC, opened_date="2026-10-01")  # 窗口早过
        paper_ledger.persist(pf, layout.sim_portfolio_path)
        # 无触发形态：close < target1 且 low > ma20×1.01 → 只剩窗口补足臂
        _write_ticks(
            layout, date, [_tick("601919", date, open=16.6, high=16.7, low=16.55, price=16.62)]
        )
        run_paper_eod(
            layout,
            date=date,
            now=dt.datetime(2026, 11, 11, 16, 6, tzinfo=TZ8),
            decide=lambda *a: None,
        )
        # T1 已过窗（opened 远昔）仍会判定：open=16.6 ≤ 17.00 → 开盘成交请求
        queue = paper_pending.load(layout.sim_pending_fills_path)
        t3_req = next(r for r in queue.requests if r.tranche_leg == "T3_trigger")
        assert t3_req.fill_price == 16.62  # 窗口补足 = 按收盘
        pos = paper_ledger.load(layout.sim_portfolio_path).position("601919")
        assert next(t for t in pos.tranches if t.leg == "T3_trigger").judged_date == date

    def test_gtc_expiry_executes_and_syncs_status(self, tmp_path: Path):
        from stock_trading_agent.state.paths import StateLayout

        layout = StateLayout(root=tmp_path / "state")
        date = "2026-11-11"
        pf = new_portfolio_from_allocation(ALLOC, opened_date="2026-11-10")
        # T1 已判定成交；T2/T3 GTC 已过期 → 到期执行（T3 无触发形态防判定臂抢先）
        pf = pf.model_copy(
            update={
                "positions": [
                    pf.positions[0].model_copy(
                        update={
                            "tranches": [
                                t.model_copy(
                                    update={"status": "filled", "judged_date": "2026-11-10"}
                                )
                                if t.leg == "T1_open"
                                else t.model_copy(update={"expires_at": "2026-11-10"})
                                for t in pf.positions[0].tranches
                            ]
                        }
                    )
                ]
            }
        )
        paper_ledger.persist(pf, layout.sim_portfolio_path)
        _write_ticks(
            layout, date, [_tick("601919", date, open=16.6, high=16.7, low=16.55, price=16.62)]
        )
        ran = []
        out = run_paper_eod(
            layout,
            date=date,
            now=dt.datetime(2026, 11, 11, 16, 6, tzinfo=TZ8),
            decide=lambda k, p: ran.append((k, p)),
        )
        assert out.expired == 2  # T2+T3 到期执行
        assert any(k == "paper_tranche_expired" for k, _ in ran)
        pos = paper_ledger.load(layout.sim_portfolio_path).position("601919")
        legs = {t.leg: t.status for t in pos.tranches}
        assert legs == {"T1_open": "filled", "T2_limit": "expired", "T3_trigger": "expired"}

    def test_before_window_and_no_ticks_skip_semantics(self, tmp_path: Path):
        from stock_trading_agent.state.paths import StateLayout

        layout = StateLayout(root=tmp_path / "state")
        paper_ledger.persist(
            new_portfolio_from_allocation(ALLOC, opened_date="2026-11-10"),
            layout.sim_portfolio_path,
        )
        # 窗前：静默（不留痕，窗口内自然触发）
        ran: list[tuple] = []
        assert (
            run_paper_eod(
                layout,
                date="2026-11-11",
                now=dt.datetime(2026, 11, 11, 10, 0, tzinfo=TZ8),
                decide=lambda k, p: ran.append((k, p)),
            )
            is None
        )
        assert ran == []
        # 窗内无 ticks：留痕（当日幂等锚）
        assert (
            run_paper_eod(
                layout,
                date="2026-11-11",
                now=dt.datetime(2026, 11, 11, 16, 6, tzinfo=TZ8),
                decide=lambda k, p: ran.append((k, p)),
            )
            is None
        )
        assert ran and ran[0][0] == "paper_eod_skipped" and ran[0][1]["reason_code"] == "no_ticks"

    def test_no_portfolio_returns_none(self, tmp_path: Path):
        from stock_trading_agent.state.paths import StateLayout

        layout = StateLayout(root=tmp_path / "state")
        assert run_paper_eod(layout, now=dt.datetime(2026, 11, 11, 16, 6, tzinfo=TZ8)) is None

    def test_decisions_written_through_decision_log(self, tmp_path: Path):
        """worker 钩子同源：decide 经 DecisionLog.append 落盘且行可解析。"""
        from stock_trading_agent.loop.monitor_events import market_today
        from stock_trading_agent.state.paths import StateLayout

        layout = StateLayout(root=tmp_path / "state")
        date = market_today(dt.datetime(2026, 11, 11, 16, 6, tzinfo=TZ8))
        paper_ledger.persist(
            new_portfolio_from_allocation(ALLOC, opened_date="2026-11-10"),
            layout.sim_portfolio_path,
        )
        _write_ticks(layout, date, [_tick("601919", date)])
        log = DecisionLog()

        def decide(kind: str, payload: dict) -> None:
            log.append("2026-11-11T16:06:00+08:00", kind, payload, path=layout.decisions_path)

        out = run_paper_eod(layout, now=dt.datetime(2026, 11, 11, 16, 6, tzinfo=TZ8), decide=decide)
        assert out is not None
        lines = _decisions(layout)
        assert any(x["kind"] == "paper_eod_done" for x in lines)
        assert any(x["kind"] == "paper_pending_fill" for x in lines)
        # 幂等：决策落盘后同日重跑直接 None（paper_eod_done 当日锚）
        assert (
            run_paper_eod(layout, now=dt.datetime(2026, 11, 11, 16, 7, tzinfo=TZ8), decide=decide)
            is None
        )

    def test_worker_hook_delegates_to_run_paper_eod(self, make_runtime):
        """LoopRuntime._maybe_paper_eod（worker 60s 节流钩子）：委托 run_paper_eod 全链。"""
        rt = make_runtime()
        date = dt.datetime(2026, 11, 11, 16, 6, tzinfo=TZ8)
        from stock_trading_agent.loop.monitor_events import market_today

        today = market_today(date)
        paper_ledger.persist(
            new_portfolio_from_allocation(ALLOC, opened_date="2026-11-10"),
            rt.layout.sim_portfolio_path,
        )
        _write_ticks(rt.layout, today, [_tick("601919", today)])
        rt._maybe_paper_eod(now=date)
        assert any(x["kind"] == "paper_eod_done" for x in _decisions(rt.layout))
        # 幂等：同刻再触发零新增
        n_before = len(_decisions(rt.layout))
        rt._maybe_paper_eod(now=date)
        assert len(_decisions(rt.layout)) == n_before
