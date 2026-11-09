# 13 · paper 引擎规约（paper）

> 沿方案 [§12](../research/main-agent-proposal.md)（母体拆分，红线句随拆分原样携带）与 [§17](../research/main-agent-proposal.md) 落位：本文件随 paper 全量 PR（0.0.6）建立并回吸现行为；此后演进只发生在本目录。批状态机/两把尺/三批窗口的机制口径 SSOT 在 [vibe-stock references/06/07](https://github.com/ThreeFish-AI/vibe-stock/tree/main/references)，本文只留指针与本仓实施裁定。

## ① 定位与边界

- **NR-2（无条件全局规则）**：模拟盘永不接真实下单接口——含券商 API 直连与委托报文任何形态；模拟与实盘物理隔离。paper/ 全包零 LLM、零网络（红线扫描机器门：禁网络库 import、禁动态 import 机制、全仓禁下单符号族）。
- **逐笔确认唯一入口**：触价只写待确认记录（`sim-pending-fills.json`），用户确认后经 `ledger.apply_fill` 落账；无人工确认不落账；驳回只作废请求、批保持 pending 零状态迁移（07 口径）。
- **台账纪律**：operations append-only 只增不改（方案 §12）；sim-portfolio.json 留 `~/.vibe-stock/` 根（写者=paper 引擎）。

## ② 数据模型与扩展键登记

07 P3 schema 的 pydantic 镜像（model.py）为加载/落盘唯一形态；扩展键三件登记义务（docstring 列举 + 本节登记 + 默认值保旧文件可载入，version 不动、版本闸拒未知版本）：
Tranche.limit_price / expires_at / judged_date（批判定戳：T1 开盘批一次性判定日、T3 触发批触发/补足日）、Operation.fees / trade_date（T+1 推导锚，旧行回落 ts 钟面日）、Position.board_lot / last_price / last_price_date（权益标记）/ opened_date（allocation 导入日，T1「次日交易日」与 T3 触发窗的锚）/ t3_ma20（T3 回踩锚）、SimPortfolio.equity_history / corporate_actions；确认流 dataclass 另有 PendingFillRequest.fill_price（撮合价，§⑧）。

## ③ 撮合与 A 股市场规则

- **两把尺**（06/07 指针）：成交判据严格穿价 `low < limit`、成交价 `min(open, limit)`（卖出镜像）；提示口径 `low ≤ entry_ref` 只推演不写回；同 bar 止盈止损悲观序（止损先）。
- **T+1 冻结面裁定**：可卖 = 持仓 − 当日买入（operations trade_date 钟面日）− 在途卖出冻结（仅展示层）；**确认闸 = sim.lock 锁内串行 + 台账重载**（先到先得：并发同额后到者被持仓不足拒）——冻结不进确认闸（避免同额双拒死锁）；日切清零由 market_date 参数化天然实现（零持久化冻结态）。
- **涨跌停三态**（E-09，estimated）：板价 `limit_band` 按最小变动单位向内取整；一字板不利向不成交（一字涨停禁买/一字跌停禁卖，有利向可成交）；触及打开=价格 clamp 进板成交（保守裁定：不模拟按比例排队份额，登记升级位）；幅度常量 A 主板 10%（创业板/科创板 20%、ST 5% 留常量位不启用）。
- **停牌三规则**（E-26）：停牌禁买卖（gate_fill 全拒）；不计收益（mark 沿用旧 last_price）；复牌按实际开盘价（下一根 bar.open 天然实现）。
- **整手闸**（E-41）：买卖 lots 须 ≥1 整数手；零股卖出不模拟。
- **HK 收盘复核闸**（E-25/E-76，D8 定谳前保守处置）：HK 撮合唯一入口 `hk_close_review_match`，只吃日终收盘 bar（`is_final` 门：盘中零撮合请求）；HK 无涨跌停三态（VCM 不模拟）；建仓批同口径。

## ④ 公司行动（E-43，estimated）

先送转后分红（同日除权除息调用方保序）：送转=Decimal 重算 lots×ratio 与 avg_cost÷ratio（tranches 价位字段与三价同步除权；不足 1 股按 avg_cost 现金找零，estimated）；分红=FIFO 持股期分档红利税（≤30 天 20%、≤365 天 10%、>365 天免；基不齐的余量按最重档保守）；**不产 Operation 行**（operations 纯交易流，公司行动单列 corporate_actions 登记）。~~公司行动 M2 为纯函数+面板手动馈送（自动化属 M3）~~ → **0.0.20 馈送入口落地**：POST `/api/paper/corporate-action`（+htmx 变体）——sim.lock 内 ledger load→幂等查重（同 code+kind+ex_date+口径 409）→apply→persist，写者恒为 paper 引擎进程内直调；决策 kind `paper_corporate_action`（payload 只带 code/action_kind/ex_date/ratio，每股额/税/找零禁入，NR-5）；④页馈送表单+登记表；同日除权除息先送转后分红由调用方保序（表单文案提示）；M3 自动化（公告源对接）不立项——外部公告数据源不在本仓面。

## ⑤ 确认流与台账写拓扑

- `sim-pending-fills.json` 落 `~/.vibe-stock/` 根（写者=paper 引擎；瞬态可重建、非 append-only；不进 sim-portfolio.json 也不进 agent/）。
- `sim.lock`（flock）串行 pending+portfolio 双文件读-改-写周期（面板线程池并发 POST 防双写）。
- t2_pending_fill 台账事件 → `engine.request_from_t2_event` 容错适配（facts 缺字段不产半截请求）→ 入队 + `paper_pending_fill` 决策（payload 零数量，NR-5）。
- 决策 kind 族：paper_pending_fill / paper_fill_confirmed / paper_fill_rejected / paper_corporate_action（0.0.20 增；payload 零敏感量）。
- 日终驱动 `engine.evaluate_day`：触价产请求不写回、GTC 到期收集（expired_legs 由调用方走 batches.expire_tranche）、marks 收集 → `mark_equity`（同日重跑覆盖同日点）。

## ⑥ 面板④契约与 NR-5 边界

- 端点：GET /api/paper/view（持仓+可卖+市值+待确认队列+权益历史+公司行动）；GET /api/panel/paper fragment（NR-2 常驻条+持仓表 T1/T2/T3 徽章+确认/驳回按钮+权益 sparkline 内联 SVG+「假设性·含费用与滑点」标注）；POST /api/paper/confirm|reject（+htmx 变体；进程内直调引擎，写者=paper 引擎）。
- **NR-5 呈现态豁免裁定**：面板/会话呈现态可显份额原值（方案 §11「T+1 可卖份额原值仅在面板与会话呈现态给值」）；一切落盘卡产物（advice/briefing）只落百分比与档位词；sim-portfolio.json 与 sim-pending-fills.json 为引擎自产模拟台账（合成持仓，非真实账户），不属 R2 敏感面；decisions payload 恒零数量。
- 权益口径：equity = cash + Σ(lots×board_lot×last_price)；NAV = equity/budget；last_price 只由引擎写、停牌沿用（③）。0.0.16 落地（勘误：原登记 0.0.11 系虚报，实际 0.0.11–0.0.15 未交付该批渲染件）：面板④表尾汇总条（paper_totals）、坐标轴版权益曲线 SVG（_axis_equity_svg）、绩效三格（Sharpe/maxDD/胜率，<30 insufficient）、持仓名称列、盈亏红涨绿跌、建仓中高亮与状态 pill（st-open/st-hold/st-close）——均呈现态（§⑥ 豁免面），数据源 equity_history/operations 现算。

## ⑦ 口径纪律与门禁

- ESTIMATED 标注族：fees / slippage / rules（幅度常量与排队份额建模）/ corporate_actions（税档与现金找零）/ **bar 源与 GTC 窗换算（§⑧，0.0.31）**——自家成交样本校准后固化，判据只许收严。
- 费用数值锚测试（test_paper_fees_slippage）不放宽；T+1 破旧测试改写已登记（同日买卖 → 次日 bar + 新增当日卖拒用例）。
- selftest 义务：T+1/板价/一字板/税档/整手/权益基线/队列往返/**EOD bar 推导与 T1/T3 判定/allocation 导入** 与 pytest 同源（判据只收严）。

## ⑧ 日终驱动与 allocation 导入裁定（0.0.31 落地；issue #37 补齐驱动面缺口）

- **bar 源口径（ESTIMATED）**：`monitor/ticks/<date>.jsonl` 每 code **末轮快照**为当日 bar——ticks 行自带 open/high/low/prev_close（行情源**全日累计**值，非轮询窗极值），price=末轮现价（收盘近似）；微差 = A 股末轮 14:57 vs 15:00 收盘竞价（HK 15:57 vs 16:00），登 ESTIMATED 待自家样本校准。缺席 code（停牌/未入盯盘宇宙）宁缺毋猜，`skipped_codes` 留痕于 paper_eod_done 决策。
- **驱动形态**：`loop/paper_eod.py` `run_paper_eod`（sim.lock 内 load→bars→evaluate_day+T1/T3 判定→请求入队/GTC 执行/权益点→persist；调用序 sim→decisions §④ 纪律）双入口——serve worker 60s 节流自动（`_maybe_paper_eod`，零 LLM 无 L2 闸、与 T1 同源）+ CLI `paper-eod [--date]`（手动/补跑）。窗口=市场钟面 16:05–23:59（双市收盘后缓冲）；幂等锚=决策台账 `paper_eod_done`/`paper_eod_skipped` 当日一条（容错扫描坏行；窗前静默不留痕、窗内无 ticks 留痕即当日锚）。
- **T1 开盘批一次性判定**（`engine.judge_t1_open`，evaluate_day 不驱动 T1）：导入后首个驱动日（`opened_date` 锚 + `judged_date` 未盖戳；「次日交易日」日历无关判据——ticks 在场即交易日）；开盘 ≤ 信号日收盘×1.02 → 开盘价成交产请求（fill_price=open）；跳空 >+2% → 转限价 ×1.02、GTC +7 日历日≈5 交易日（ESTIMATED）；闸拒防御位同转限价保守处置。判定即盖戳 `judged_date`。
- **T3 触发批复合判定**（`engine.judge_t3`，evaluate_day 不驱动 T3；06「站上目标1 或回踩 MA20±1%，20 日内未触发按收盘补足」）：①close > target1 → 收盘价成交；②low ≤ t3_ma20×1.01 ∧ close ≥ t3_ma20×0.99 → 成交价 = ma20 夹入当日 [low, high]（band 触及近似，ESTIMATED——日 K 内触达次序不可知）；③窗口终点（opened+28 日历日≈20 交易日，ESTIMATED）未触发 → 按收盘补足。触发/补足即盖戳；未触发不盖、逐日续判。t3_ma20/target1 缺席（旧仓位）恒不触发（宁缺毋猜）。
- **T2 限价批 GTC**：allocation 导入即设 `expires_at`=导入日+14 日历日≈10 交易日（ESTIMATED）；到期由 evaluate_day 收集、驱动层 `expire_tranche` 执行 + `paper_tranche_expired` 决策留痕。
- **allocation 导入**（`paper/allocation.py`，06 Stage D 产物零格式发明）：CLI `paper-import --file <allocation.json>` 与面板④「allocation 导入」表单（`POST /api/paper/allocation`（+htmx 变体）、load-only `alloc-form`，沿 ca-form 0.0.24 先例）；逐行建三批（T1 ref=信号日收盘、T2 ref=limit=entry_ref、T3 ref=ma20 回踩锚；市场判定 6 位 A/5 位 HK 宁拒不猜；三价单调与手数守恒校验；单手股单段建满）；账本缺席以 allocation budget 起始新建、已有账本追加；dup code **整批拒**（先全量预检防半批导入）；确认前不动现金。决策 `paper_allocation_imported` payload 恰 `{date, codes}`（零价格零数量，NR-5）。
- **撮合价透传落账**（方案 §12 成交价口径收口）：`PendingFillRequest.fill_price`（引擎撮合请求必带=min(open,limit)/止损 max|min(open,stop)±滑点；t2_pending_fill 事件适配请求不带——提示口径无撮合，确认按参考价语义人工核对）；`ledger.apply_fill` 落账优先 fill_price（notional/filled_price/avg_cost/Operation.price 四面），旧行缺省 None 回落 ref_price。
- **决策 kind 族新增**：`paper_eod_done`（计数与 skipped_codes，零价格零数量）/ `paper_eod_skipped`（reason_code）/ `paper_tranche_expired` {date, code, leg} / `paper_t1_converted` {date, n} / `paper_allocation_imported` {date, codes}；既有 `paper_pending_fill` 增 date 键。
- **呈现态**：④持仓表/确认队列自然消费新台账（SSE paper 事件驱动重拉）；确认队列呈现撮合价与参考价两口径（呈现态豁免面 §⑥）。
