"""PR-I 纯函数族：盘态/名称映射/信号快照/待办聚合/晨报行动项/绩效/命中率/σ√h 解析。"""

from __future__ import annotations

import datetime as dt
import json

from stock_trading_agent.loop.advice import parse_trend_table, render_advice_card
from stock_trading_agent.server.panel_data import (
    MARKET_TZ,
    PriceFact,
    aggregate_calendar,
    asof_stale,
    briefing_action_items,
    build_name_map,
    derive_todos,
    load_name_map,
    market_phase,
    merge_price_facts,
    parse_ticks_prices,
    rebuild_notifications,
    signals_snapshot,
    t1_price,
)
from stock_trading_agent.server.stats import perf_summary, t1_review_hitrate
from stock_trading_agent.state.paths import StateLayout

CST = MARKET_TZ


def _at(h: int, m: int, weekday_sunday: bool = False) -> dt.datetime:
    day = 8 if weekday_sunday else 9  # 2026-11-09 周一 / 11-08 周日
    return dt.datetime(2026, 11, day, h, m, tzinfo=CST)


class TestMarketPhase:
    def test_matrix(self):
        assert market_phase(_at(10, 0))["overall"] == "live"
        assert market_phase(_at(10, 0))["label"] == "盘中"
        assert market_phase(_at(15, 30))["overall"] == "live"  # HK 仍盘中（16:00 前）
        assert market_phase(_at(16, 30))["overall"] == "post"
        assert market_phase(_at(8, 0))["overall"] == "pre"
        assert market_phase(_at(10, 0, weekday_sunday=True))["overall"] == "closed"

    def test_lunch_matrix(self):
        # 午休分态（0.0.25）：12:00 双市同休显午休；11:45 CN 午休 HK 仍盘中 → 盘中；
        # 11:29 边界前盘中；13:00 HK 复盘（端点不含）→ 盘中
        p = market_phase(_at(12, 0))
        assert p["overall"] == "lunch" and p["label"] == "午休"
        assert p["cn"] == "lunch" and p["hk"] == "lunch"
        p2 = market_phase(_at(11, 45))
        assert p2["overall"] == "live" and p2["cn"] == "lunch" and p2["hk"] == "live"
        assert market_phase(_at(11, 29))["overall"] == "live"
        assert market_phase(_at(13, 0))["overall"] == "live"

    def test_date_fields(self):
        p = market_phase(_at(10, 0))
        assert p["date"] == "2026-11-09" and p["weekday"] == "周一"


class TestNameMap:
    def test_from_reports(self):
        m = build_name_map([{"dir": "600519.cn"}, {"dir": "00700.hk"}])
        assert m["600519"]["market"] == "cn" and m["00700"]["market"] == "hk"


class TestSignalsSnapshot:
    def test_latest_per_code_and_to_stop(self):
        entries = [
            {"code": "600519", "signal": "buy", "confidence": 0.6, "key_levels": {"stop": 9.5}},
            {"code": "600519", "signal": "sell", "confidence": 0.7, "key_levels": {"stop": 9.5}},
        ]
        snap = signals_snapshot(entries, {"600519": 9.0})
        row = snap["rows"][0]
        assert row["signal"] == "sell" and row["to_stop_pct"] == round(9.0 / 9.5 * 100 - 100, 2)

    def test_invalid_signal_degrades(self):
        snap = signals_snapshot([{"code": "X", "signal": "梭哈"}], {})
        assert snap["rows"][0]["invalid"] is True and snap["rows"][0]["signal"] is None

    def test_bad_input_read_not_ok(self):
        snap = signals_snapshot(None, {})
        assert snap == {"rows": [], "read_ok": False}

    def test_price_missing_dash(self):
        snap = signals_snapshot([{"code": "X", "signal": "hold", "key_levels": {"stop": 1}}], {})
        assert snap["rows"][0]["price"] is None and snap["rows"][0]["to_stop_pct"] is None

    def test_price_fact_fields_and_stale(self):
        snap = signals_snapshot(
            [{"code": "600519", "signal": "hold", "key_levels": {"stop": 9.0}}],
            {"600519": PriceFact(price=9.5, asof="2026-11-09T10:30:00+08:00", source="ticks")},
            market_date="2026-11-09",
        )
        row = snap["rows"][0]
        assert row["price"] == 9.5 and row["price_source"] == "ticks"
        assert row["price_stale"] is False and row["to_stop_pct"] == round(9.5 / 9.0 * 100 - 100, 2)
        # 昨日 asof → 陈旧；无 market_date → 不判
        stale_row = signals_snapshot(
            [{"code": "600519", "signal": "hold"}],
            {"600519": PriceFact(price=9.5, asof="2026-11-08T10:30:00+08:00", source="ticks")},
            market_date="2026-11-09",
        )["rows"][0]
        assert stale_row["price_stale"] is True

    def test_price_bare_float_compat(self):
        snap = signals_snapshot([{"code": "X", "signal": "hold"}], {"X": 9.0}, market_date="d")
        row = snap["rows"][0]
        assert row["price"] == 9.0 and row["price_asof"] == "" and row["price_stale"] is False


class TestPriceFacts:
    def test_parse_ticks_tail_wins_and_bad_lines(self):
        lines = [
            "{bad json",
            json.dumps({"code": "600519", "price": 9.4, "ts_utc8": "2026-11-09 10:00:01"}),
            json.dumps({"code": "600519", "price": 9.6, "ts_utc8": "2026-11-09 10:05:00"}),
            json.dumps({"code": "00700", "price": "not-num", "ts_utc8": "x"}),
            json.dumps({"price": 1.0}),  # 无 code 跳过
        ]
        out = parse_ticks_prices(lines)
        assert out["600519"].price == 9.6 and out["600519"].source == "ticks"
        assert "00700" not in out and len(out) == 1

    def test_t1_price_available_only(self):
        ev = {"blocks": {"quote": {"price": 9.7, "state": "available"}}, "generated_at": "t"}
        assert t1_price(ev) == PriceFact(price=9.7, asof="t", source="t1")
        assert t1_price({"blocks": {"quote": {"price": 9.7, "state": "stale"}}}) is None
        assert t1_price({"blocks": {}}) is None
        assert t1_price(None) is None

    def test_merge_asof_latest_wins(self):
        merged = merge_price_facts(
            {"A": PriceFact(9.0, "2026-11-09T09:00:00+08:00", "sim")},
            {"A": PriceFact(9.6, "2026-11-09T10:05:00+08:00", "ticks")},
        )
        assert merged["A"].price == 9.6 and merged["A"].source == "ticks"

    def test_merge_empty_asof_later_arg_wins(self):
        merged = merge_price_facts(
            {"A": PriceFact(9.0, "", "sim")},
            {"A": PriceFact(9.1, "", "sim2")},
        )
        assert merged["A"].price == 9.1
        # 有 asof 者恒胜无 asof 者（即使实参在前）
        merged2 = merge_price_facts(
            {"A": PriceFact(9.0, "", "sim")},
            {"A": PriceFact(8.0, "2026-11-09T10:00:00+08:00", "t1")},
        )
        assert merged2["A"].price == 8.0 and merged2["A"].source == "t1"

    def test_asof_stale(self):
        assert asof_stale("2026-11-09T10:00:00+08:00", "2026-11-09") is False
        assert asof_stale("2026-11-08T15:00:00+08:00", "2026-11-09") is True
        assert asof_stale("", "2026-11-09") is True  # 缺席保守视陈旧
        assert asof_stale("garbage", "2026-11-09") is True  # 坏串保守视陈旧
        # UTC 晚间换算：01:00 UTC = 09:00 当日市场钟面 → 不陈旧
        assert asof_stale("2026-11-09T01:00:00+00:00", "2026-11-09") is False


class TestTodos:
    def test_three_sources_pending_first(self):
        todos = derive_todos(
            [{"code": "A", "mech_risk": {"severity": "high"}, "rule": "stop_break"}],
            [{"code": "B", "state": "pending", "tranche_leg": "T2_limit", "ref_price": 10}],
            [{"code": "C", "ack": False}],
        )
        assert [t["source"] for t in todos] == ["pending_fill", "mech_risk", "briefing"]

    def test_suppressed_and_acked_excluded(self):
        todos = derive_todos(
            [{"code": "A", "mech_risk": {"severity": "high"}, "suppress_reason": "cooldown"}],
            [],
            [{"code": "C", "ack": True}],
        )
        assert todos == []


class TestBriefingItems:
    def test_parse(self):
        md = "---\ncode: 600519\n---\n## 行动项\n- [ ] 复核：止损线距离\n- [ ] 复核：均线\n"
        assert briefing_action_items([md]) == [
            {"code": "600519", "item": "止损线距离"},
            {"code": "600519", "item": "均线"},
        ]


class TestPerf:
    def test_sharpe_dd_winrate(self):
        h = [{"nav": 1.0}, {"nav": 1.02}, {"nav": 0.99}, {"nav": 1.05}]
        ops = [
            {"code": "c", "side": "buy", "price": 10.0, "lots": 1},
            {"code": "c", "side": "sell", "price": 11.0, "lots": 1},
        ]
        r = perf_summary(h, ops)
        assert r["sharpe"] is not None and r["max_drawdown"] < 0
        assert r["closed_pairs"] == 1 and r["win_rate"] is None  # 样本 <30 只观察

    def test_insufficient_note(self):
        r = perf_summary([], [])
        assert r["note"] == "样本 0 < 30：只观察不记分"

    def test_partial_sell_fifo(self):
        ops = [
            {"code": "c", "side": "buy", "price": 10.0, "lots": 2},
            {"code": "c", "side": "sell", "price": 9.0, "lots": 1},
            {"code": "c", "side": "sell", "price": 12.0, "lots": 1},
        ]
        r = perf_summary([{"nav": 1.0}, {"nav": 1.01}], ops)
        assert r["closed_pairs"] == 2


class TestHitrate:
    def test_from_ledger(self):
        ledger = {"attribution": {"agent:buy": {"wins": 3, "losses": 1}}}
        r = t1_review_hitrate([], ledger)
        assert r["sampled"] == 4 and r["hits"] == 3 and r["rate"] is None  # <30

    def test_absent(self):
        assert t1_review_hitrate([], {})["sampled"] == 0


class TestTrendTableRoundtrip:
    def test_roundtrip(self):
        payload = {
            "outcome": "card",
            "candidate_action": "持有观察",
            "final_signal": "hold",
            "prev_diff": {"prev_final_signal": "none", "note": "n"},
            "t1_sell_pct": 85.0,
            "t1_sell_tier": "大部分",
            "verdict_date": "2026-11-08",
            "opinion_data_date": "2026-11-08",
            "replayed_opinions": 2,
            "trend_ref": {
                "band_rows": [
                    {"horizon": "1d", "low": 9.5, "high": 10.5, "prob_touch_low_pct": 31.0},
                    {"horizon": "5d", "low": 9.0, "high": 11.0, "prob_touch_low_pct": 52.0},
                ]
            },
            "rationale": "r",
        }
        md = render_advice_card(payload, code="600519", date="2026-11-08", task_id="t")
        rows = parse_trend_table(md)
        assert rows[0] == {"horizon": "1d", "low": 9.5, "high": 10.5, "prob_touch_low_pct": 31.0}
        assert len(rows) == 2

    def test_no_section_empty(self):
        assert parse_trend_table("# 无关") == []


class TestLoadNameMap:
    def test_chain_merge(self, tmp_path):
        layout = StateLayout(root=tmp_path)
        layout.root.mkdir(parents=True, exist_ok=True)
        (layout.root / "names.json").write_text(
            json.dumps({"cn:600519": "贵州茅台", "hk:00700": "腾讯控股"}), encoding="utf-8"
        )
        (layout.root / "signals.json").write_text(
            json.dumps([{"code": "601919", "stock_name": "中远海控"}]), encoding="utf-8"
        )
        layout.monitor_dir.mkdir(parents=True)
        (layout.monitor_dir / "universe.json").write_text(
            json.dumps({"rows": [{"code": "00268", "name": "金蝶国际"}]}), encoding="utf-8"
        )
        names = load_name_map(layout)
        assert names["600519"] == "贵州茅台"  # 主源
        assert names["601919"] == "中远海控"  # signals 回退
        assert names["00268"] == "金蝶国际"  # universe 回退
        assert names["00700"] == "腾讯控股"

    def test_absent_empty(self, tmp_path):
        assert load_name_map(StateLayout(root=tmp_path)) == {}


class TestAggregateCalendar:
    def _payload(self, code="600519", **blocks):
        return {"meta": {"code": code}, "blocks": blocks}

    def test_three_types_sorted(self):
        # 日期相对 today 构造（时区无关断言）：原硬编码 2026-11-10 公告日跨午夜即被
        # 「过去日过滤」吃掉致断言失败（I-003 跨午夜时敏同族）
        d = lambda n: (dt.datetime.now(tz=None) + dt.timedelta(days=n)).strftime("%Y-%m-%d")  # noqa: E731
        payload = self._payload(
            forecast={"next_disclosure": {"appoint_date": d(20)}},
            unlock={"upcoming": [{"date": d(9), "ratio_pct": 5.2, "type": "定向增发"}]},
            notices={"items": [{"date": d(2), "type": "公告", "title": "董事会决议"}]},
        )
        rows = aggregate_calendar([payload])
        assert [r["type"] for r in rows] == ["公告", "解禁", "披露"]
        assert rows[1]["detail"].startswith("解禁占流通 5.2%")

    def test_dedupe_and_past_filtered(self):
        payload = self._payload(
            unlock={"upcoming": [{"date": "2020-01-01", "ratio_pct": 1, "type": "x"}]},
            notices={"items": []},
        )
        assert aggregate_calendar([payload, payload]) == []


class TestRebuildNotifications:
    def test_log_line_parsed(self):
        rows = rebuild_notifications(
            [], "notify: [vibe-stock 盯盘] 紫金矿业 x ·止损复核 ·待收盘复核·非投资建议", {}
        )
        assert rows[0]["title"] == "vibe-stock 盯盘"
        assert rows[0]["body"].endswith("·待收盘复核·非投资建议")

    def test_event_row_with_name(self):
        rows = rebuild_notifications(
            [
                {
                    "ts": "09:31",
                    "code": "600519",
                    "notify": True,
                    "rule": "stop_break",
                    "review": "止损复核",
                }
            ],
            "",
            {"600519": "贵州茅台"},
        )
        assert "贵州茅台" in rows[0]["body"] and "止损复核" in rows[0]["body"]

    def test_suppressed_excluded(self):
        rows = rebuild_notifications(
            [{"ts": "t", "code": "c", "notify": True, "rule": "r", "suppress_reason": "cap"}],
            "",
            {},
        )
        assert rows == []
