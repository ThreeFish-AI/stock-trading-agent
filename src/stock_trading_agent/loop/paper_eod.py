"""paper 日终驱动（EOD）：monitor/ticks 末轮 bar → evaluate_day/T1 判定 → 请求/GTC/权益接线。

bar 源口径（13-paper §⑧，ESTIMATED）：`monitor/ticks/<date>.jsonl` 末轮快照自带全日累计
OHLC（open/high/low/prev_close 为行情源自累计、非轮询窗极值），取每 code 末行即当日 bar；
微差 = A 股末轮 14:57 vs 15:00 收盘竞价（HK 15:57 vs 16:00 同理），登 ESTIMATED。
零 LLM 零网络（消费既有 monitor 台账，并存收编不替代）；sim.lock 内读-改-写
（与面板确认流同锁同纪律）；幂等 = 决策台账 paper_eod_done/skipped 当日一条
（容错扫描：坏行跳过；断链降级语义由 serve/DecisionLog 层承载，本层不重复判链）。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from stock_trading_agent.loop.events import MARKET_TZ
from stock_trading_agent.loop.monitor_events import market_today
from stock_trading_agent.paper import ledger as paper_ledger
from stock_trading_agent.paper import pending as paper_pending
from stock_trading_agent.paper.batches import expire_tranche, sync_position
from stock_trading_agent.paper.engine import (
    DayInput,
    evaluate_day,
    judge_t1_open,
    judge_t3,
    mark_equity,
)
from stock_trading_agent.paper.fills import Bar
from stock_trading_agent.state.store import FileLock

EOD_KINDS = ("paper_eod_done", "paper_eod_skipped")
EOD_WINDOW_START = dt.time(16, 5)  # 双市收盘后（CN 15:00 / HK 16:00 + 缓冲）


@dataclass(frozen=True)
class EodOutcome:
    """一次 EOD 驱动的结构化结果（计数面；决策 payload 同口径零价格零数量，NR-5）。"""

    date: str
    bars: int
    requests: int
    expired: int
    converted: int
    judged: int
    marked: bool
    skipped_codes: list[str]


def ticks_path(layout, date: str) -> Path:
    """monitor/ticks/<date>.jsonl（watch.py 写者；本层只读）。"""
    return layout.monitor_dir / "ticks" / f"{date}.jsonl"


def derive_day_bars(ticks_file: Path, codes: list[str]) -> dict[str, Bar]:
    """monitor/ticks/<date>.jsonl → {code: 当日 bar}（每 code 末行为准；坏行跳过）。

    ticks 行自带 open/high/low/prev_close（全日累计）+ price（末轮现价=收盘近似）；
    缺席 code 不入返回（调用方按 skip 留痕——缺席=停牌或未入盯盘宇宙，宁缺毋猜）。
    """
    last: dict[str, dict] = {}
    if not ticks_file.exists():
        return {}
    with open(ticks_file, encoding="utf-8") as f:
        for raw in f:
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue
            code = str(line.get("code") or "")
            if code:
                last[code] = line  # 台账即时间序：后行覆盖
    wanted = set(codes)
    bars: dict[str, Bar] = {}
    for code, line in last.items():
        if code not in wanted:
            continue
        try:
            bars[code] = Bar(
                date=str(line.get("ts_utc8") or "")[:10],
                open=float(line["open"]),
                high=float(line["high"]),
                low=float(line["low"]),
                close=float(line["price"]),
                prev_close=float(line["prev_close"]) if line.get("prev_close") else None,
            )
        except (KeyError, TypeError, ValueError):
            continue  # 坏行容错（半写态/字段漂移）：宁缺毋猜
    return bars


def plan_paper_eod(
    now_local: dt.datetime,
    *,
    has_portfolio: bool,
    ticks_exists: bool,
    already_ran: bool,
    backfill: bool = False,
) -> str | None:
    """EOD 触发判定（纯函数；返回 None=应跑，否则返回 skip 原因码供留痕/调试）。

    backfill=True（CLI --date 显式补跑过去日）：跳过窗前检查——该日的窗口早已过去，
    以 ticks 在场性为唯一闸（缺席即留痕 no_ticks）。
    """
    if not has_portfolio:
        return "no_portfolio"  # 无账本不留痕（幂等探针只对有账本日生效）
    if already_ran:
        return "already_ran"
    if not backfill and now_local.time() < EOD_WINDOW_START:
        return "before_window"
    if not ticks_exists:
        return "no_ticks"
    return None


def eod_ran_on(decisions_path: Path, date: str) -> bool:
    """幂等探针：决策台账含当日 paper_eod_done/skipped（容错扫描坏行）。"""
    if not decisions_path.exists():
        return False
    with open(decisions_path, encoding="utf-8") as f:
        for raw in f:
            if '"paper_eod_' not in raw:
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if line.get("kind") in EOD_KINDS and (line.get("payload") or {}).get("date") == date:
                return True
    return False


def _t1_due(position, date: str) -> bool:
    """T1/T3 待判定（导入后首个驱动日=「次日交易日」；日历无关判据）。"""
    if position.opened_date is None or date <= position.opened_date:
        return False
    return any(
        t.leg in ("T1_open", "T3_trigger") and t.status == "pending" and t.judged_date is None
        for t in position.tranches
    )


def _t3_window_end(position) -> str | None:
    """T3 触发窗终点（opened_date + 28 日历日 ≈ 20 交易日，ESTIMATED；缺锚 None=不补足）。"""
    if position.opened_date is None:
        return None
    return (dt.date.fromisoformat(position.opened_date) + dt.timedelta(days=28)).isoformat()


def run_paper_eod(layout, *, date: str | None = None, now=None, decide=None) -> EodOutcome | None:
    """一次日终驱动（sim.lock 内：load→bars→evaluate_day+T1 判定→请求/GTC/权益→persist）。

    decide(kind, payload)：决策回调（worker=DecisionLog.append；CLI/测试注入）。
    无账本 → None（无事可做不留痕）；skip（窗前/无 ticks）→ 记 paper_eod_skipped 后返回 None
    （留痕即幂等锚：同日不重试；次日窗口自然重开）。
    """
    local = (now or dt.datetime.now(tz=dt.UTC)).astimezone(MARKET_TZ)
    date = date or market_today(local)
    portfolio = paper_ledger.load(layout.sim_portfolio_path)
    if portfolio is None:
        return None
    if decide is None:

        def decide(kind, payload):  # 测试/无决策面调用（留痕缺省 no-op）
            return None

    reason = plan_paper_eod(
        local,
        has_portfolio=True,
        ticks_exists=ticks_path(layout, date).exists(),
        already_ran=eod_ran_on(layout.decisions_path, date),
        backfill=date != market_today(local),  # 显式过去日补跑：窗前检查不适用
    )
    if reason in ("already_ran", "before_window", "no_portfolio"):  # backfill 下无 before_window
        return None  # 窗前静默（窗口内自然触发）；已跑幂等返回
    if reason is not None:
        decide("paper_eod_skipped", {"date": date, "reason_code": reason})
        return None  # 窗内无 ticks（节假日/盯盘未跑）→ 留痕即当日幂等锚

    codes = [p.code for p in portfolio.positions if p.status != "closed"]
    with FileLock(layout.sim_lock):
        # 锁内重载（确认闸同纪律：并发确认后以文件态为准）
        portfolio = paper_ledger.load(layout.sim_portfolio_path) or portfolio
        bars = derive_day_bars(ticks_path(layout, date), codes)
        day_input = DayInput(market_date=date, bars=dict(bars), is_final=True)
        outcome = evaluate_day(portfolio, day_input)
        queue = paper_pending.load(layout.sim_pending_fills_path)
        converted = 0
        judged = 0
        # T1 开盘批一次性判定 + T3 触发批逐日判定（evaluate_day 不驱动两批，06 复合口径）
        for position in portfolio.positions:
            if position.status == "closed" or position.code not in bars:
                continue
            bar = bars[position.code]
            for tranche in position.tranches:
                if tranche.status != "pending" or tranche.judged_date is not None:
                    continue
                if not _t1_due(position, date):
                    continue  # 导入日当日不判（T1 次日、T3 窗同锚）
                if tranche.leg == "T1_open":
                    verdict = judge_t1_open(position, tranche, bar)
                    tranche.judged_date = date
                    judged += 1
                    if verdict.request is not None:
                        outcome.requests.append(verdict.request)
                    elif verdict.convert_limit is not None:
                        tranche.limit_price = verdict.convert_limit
                        tranche.expires_at = verdict.convert_expires
                        converted += 1
                elif tranche.leg == "T3_trigger":
                    verdict = judge_t3(position, tranche, bar, window_end=_t3_window_end(position))
                    if verdict.request is not None:
                        tranche.judged_date = date  # 触发/补足即盖戳；未触发不盖、逐日续判
                        judged += 1
                        outcome.requests.append(verdict.request)
        # GTC 到期执行（evaluate_day 只收集，执行在驱动层）
        expired = 0
        for code, leg in outcome.expired_legs:
            position = portfolio.position(code)
            tranche = next(
                (t for t in position.tranches if t.leg == leg and t.status == "pending"), None
            )
            if tranche is None:
                continue
            position.tranches = [
                expire_tranche(t) if t is tranche else t for t in position.tranches
            ]
            idx = portfolio.positions.index(position)
            portfolio.positions[idx] = sync_position(position)  # 状态机械重导（返回新对象）
            expired += 1
            decide("paper_tranche_expired", {"date": date, "code": code, "leg": leg})
        # 触价请求入队（触价只写待确认，用户确认才落账——07 逐笔确认环）
        for req in outcome.requests:
            queue.enqueue(req)
            decide(
                "paper_pending_fill",
                {
                    "date": date,
                    "code": req.code,
                    "leg": req.tranche_leg,
                    "request_id": req.request_id,
                },
            )
        # T1 转限价留痕（零价格：leg 与日期即可追溯，limit 见台账呈现态）
        if converted:
            decide("paper_t1_converted", {"date": date, "n": converted})
        paper_pending.save(queue, layout.sim_pending_fills_path)
        # 权益标记与落盘（marks 含停牌沿用旧价的等价回填）
        marked = bool(outcome.marks)
        if marked:
            portfolio = mark_equity(portfolio, date, dict(outcome.marks))
        paper_ledger.persist(portfolio, layout.sim_portfolio_path)
    skipped = [c for c in codes if c not in bars]
    result = EodOutcome(
        date=date,
        bars=len(bars),
        requests=len(outcome.requests),
        expired=expired,
        converted=converted,
        judged=judged,
        marked=marked,
        skipped_codes=skipped,
    )
    decide(
        "paper_eod_done",
        {
            "date": date,
            "bars": result.bars,
            "requests": result.requests,
            "expired": result.expired,
            "converted": result.converted,
            "judged": result.judged,
            "marked": result.marked,
            "skipped_codes": skipped,
        },
    )
    return result
