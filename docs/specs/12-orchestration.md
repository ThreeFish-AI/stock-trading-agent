# 12 · 主 Agent 编排规约（orchestration）

> 沿方案 [§17](../research/main-agent-proposal.md) 落位（编号沿原约续排）：本文件随首个实施功能 PR 建立（0.0.3），回吸骨架现行为并登记实施裁定；此后演进只发生在本目录，方案原文保持审批时点快照。vibe-stock 仓不建同名文件、其 SKILL 判表不同步（产品编排不入诊股 Skill 判表）。机制引用（T0/T1/T2 事件梯、建议卡契约、锁纪律）事实源在 [vibe-stock](https://github.com/ThreeFish-AI/vibe-stock)，本文只留指针与产品侧裁定。

## ① 定位与边界

- **LLM 唯一通道**：所有 LLM 调用单通道经本机 Claude Code headless CLI 子进程（`cc_client/`；mcp serve 拒入大脑链路、Agent SDK 仅 M2 对照通道）。真实 spawn 默认拒，`STA_ALLOW_REAL_CC=1` 才放行（通道完整版属下一 PR）。
- **LLM 零直写裁决面**：signals/ledger 的写者永远是 vibe-stock 既有确定性脚本；本仓不提供任何 signals/ledger 写 API（红线扫描机器门钉死：`state/` 目录代码禁出现两文件名字面量；postmortem 槽 mtime 探测放 server 层）。
- **并存收编不替代**：launchd 三槽（monitor 09:15 / outlook 17:35 / postmortem 18:05）职责原样，主 Agent 只消费其台账产物（`monitor/`、`outlook/`、`reports/` 只读），不引第二套调度器（I-36/I-11）。
- **确定性层可独活**：CC 不可达 / daemon 锁被持有时，盯盘、台账、面板只读态全部照常（降级是设计内形态非故障，§14）。
- **写拓扑**：`~/.vibe-stock/agent/` 是唯一新写区（queue/sessions/decisions/audit/budget/runs/watcher.json），写者=主 Agent（面板经进程内 `LoopRuntime.submit` 提交，不出现第二写者）；`monitor/advice/` 建议卡是 `agent/` 外唯一写者例外（断言器放行后原子写，M2）；sim-portfolio.json 写者=paper 引擎。

## ② CCClient 通道契约

- 契约签名与旗标面以 `src/stock_trading_agent/cc_client/contract.py` 与 `cli.py` 现行为准（方案 §9 签名级细化已代码化）：`CCRequest(task_id, session_uuid=UUIDv5(ns, f"{date}:{code}:{kind}"), prompt="/vibe-stock <code>" 形态, schema, budget_usd, max_turns, allowed_tools, permission_mode="dontAsk", mcp="strict-empty", timeout_s=900, autocompact)`；`CCResult(exit_code, envelope, structured_output, result_line_present, transcript_path, usage_tokens)`。
- NR-4 双闸位：`validate_request`（budget_usd/max_turns 必填>0）+ 路由表解析闸（模板缺 NR-4 字段即 `RoutingError`）。
- 禁跑纪律（无条件全局规则：编排器永不主动向 CC 子进程发 SIGTERM）：SIGTERM=143 语义为外部弃轮、SIGINT 才是收轮正道（D-01）；退出码归一化 `-sig → 128+sig`（130 收轮 / 137 SIGKILL 兜底 / 143 弃轮）。坑位：完整状态机随真实通道 PR 落地，届时回写本节。
- `--json-schema` 只喂 draft-07 扁平子集（`to_draft07` 遇 2020-12 方言直接 raise，F-12）；喂 CC 与本地复验同一份 schema（NR-3）。

## ③ 主 Loop 六段

- **①触发源**三类：panel（面板手动）/ launchd（monitor 台账经 watchfiles 监听，watch.py 为写者）/ monitor（M3 预留）。统一事件信封 + dedup_key=`f"{kind}:{code}:{date}"`；入队前过禁跑窗（17:30–18:10 Asia/Shanghai 钟面，覆盖 diagnose_full/diagnose_quick/batch_wave/briefing_review 与 `report` 前缀）与双锁前置（fetch_state.lock/watch.lock 存在即 defer，弱约定）。
- **②意图路由**：YAML 确定性查表零 LLM（六 kind：diagnose_full/diagnose_quick/monitor_review/batch_wave/paper_confirm/briefing_review）；用户路径覆盖只许收严（预算/轮次只降、工具只删子集，`load_table` 机器闸）。零 LLM 分派（条件：`zero_llm` 表；做法：T0→决策台账登记 only、T1→diagnose prepare 链；坑位：T0 通知唯一通道是 vibe-stock watch.py 的 osascript，面板与主 Agent 永不重复通知，R7）。
- **③CC 会话**：每任务一次性 `claude -p` 短会话 fresh context；`--session-id` 按日期+标的+kind 派生 UUIDv5。订阅形态 budget 取 `max(模板, sentinel=1000)`（D2①高位哨兵满足 NR-4 在场性，数值闸在 budget.gate）。
- **④断言器**（NR-3 落盘前置门）：三段完成判据=退出码+result 信封+result 行存在；success 而 structured_output 缺失视为失败；意见类 signal 五词枚举；全部 kind 免责段必带；schema 在场则 draft-07 本地复验；敏感字段词族子串扫描。违例→task=failed+`assert_refuse` 决策+不归档转录。
- **⑤落盘**：原子写 agent/（os.replace+fsync）；decision log sha256 hash 链追加（`agent:` 前缀，18:05 postmortem 槽消费契约）；转录归档在断言器放行后（NR-3 次序）。
- **⑥后验反馈环**：M1 只写不消费（decision log 供外仓 postmortem 拾取；独立画像记分属 M2）。

### 运行时裁定（0.0.3 登记）

1. **WAL 不变式**（无条件全局规则：崩溃断点重放的前提）：任何执行器在 spawn/子进程启动**之前**完成 `queued→running` 迁移并落盘 queue.json（`AgentLoop.mark_running`；`run_session(task, *, queue)` 的 queue 必填使漏传即 TypeError）；`assert_and_persist` 只守卫不补迁（非 running 即拒，判据收严）。坑位：消费期 build_request 被拒（预算闸等）→ running→failed 终态，防 worker 自旋。
2. **单消费者不变式**：`agent/daemon.lock`（flock 长持有）= 唯一有权消费 queue 的运行时实例，serve worker 线程与 CLI `run-once` 二选一；`agent/queue.lock` 串行化 queue.json 一切读-改-写周期（锁内重载→变更→落盘，锁外绝不回写）；worker 执行 spawn/T1 子进程期间零锁持有；单 worker 线程串行 CC spawn（D-71 OAuth 竞争防护）。
3. **崩溃恢复**：启动期 running 残留 → abandoned + `task_abandoned` 决策（M1 人在环不自动重放；M3 预留 `replayables()` 消费方）。
4. **T1 自动刷新（M1 口径，用户裁定）**：触发判据四条件 AND——`level=="T0"` ∧ `code≠"*"` ∧ 未压制 ∧（review 复核短语非空 ∨ `mech_risk.severity∈{high,medium}`）；幂等=`t1_refresh:<code>:<date>` 每股每日一次含失败不自动重试（`queue.find_any` 终态也命中）；执行链=调 vibe-stock 已暴露 CLI（`fetch.py` 全量取数 + `diagnose.py prepare --evidence`），成功判据=context-pack.md 在场；workdir=`agent/runs/<date>/t1/<code>/`（恒不传 `--cost`，NR-5）。坑位：轻量日 K 链（重拉日 K+technical 重算+快照拼 evidence）vibe-stock 侧无 CLI 暴露，登记为机制升级项；锁在场（盯盘/取数中）→ 消费期 defer 留 queued 不阻塞队头，收盘锁释放自动补跑（I-005 盯盘期间不跑全诊取数）。
5. **monitor 监听**：watchfiles 线程盯 `monitor/events/`（事件只当触发器，真实增量以文件偏移为准防丢防重）；偏移断点持久化 `agent/watcher.json`（崩溃不重放已登记事件；日切换清键）；T0 事件逐条记 `monitor_event` 决策（登记 only）。坑位：launchd WatchPaths 不用（race-prone，I-52）；events 目录不存在时退化为步长轮询等待目录出现。
6. **消费期资格复检**：锁与禁跑窗在消费期复检（与 ingest 闸同源，判据只收严）；defer 的任务留 queued，worker 按 FIFO 跳过 defer 项继续找可执行任务。

## ④ 状态扩展写拓扑

方案 §11 表为快照；现行权威布局=`state/paths.py`（代码化读写矩阵）。0.0.3 增补：`agent/queue.lock`（串行闸）、`agent/runs/<date>/t1/<code>/`（T1 工作目录）、`agent/runs/<session_uuid>/`（CC 会话 cwd，随真实通道 PR 启用）、`agent/watcher.json`（监听偏移断点）、`agent/daemon.lock`（单消费者）。vibe-stock 只读消费位：`monitor/events/<date>.jsonl`、`monitor/state.json`、`outlook/<date>.md`、`reports/`、根级 `ledger.json`（仅 mtime 探测，只读）。

## ⑤ 预算闸与降级

- 预算闸默认关（人在环期仅记账，§13）；外闸在 `run_session` 前置预检（日/月 USD 信封+硬次数闸 50/日）；订阅轨 token+等效美元双口径记账写 `agent/budget.json`。
- 降级形态（设计内非故障）：daemon 锁被持有→面板降级只读（提交仍可入队，消费由锁持有者完成；`/api/monitor/status.loop.active` 可见）；锁在场/禁跑窗→提交 423 / 消费 defer；断言违例→拒落盘+`assert_refuse`；T1 失败→终态 failed 当日不重试。
- CLI `run-once` 退出码契约：0=done · 1=failed/被拒 · 2=队空或全部 defer · 3=daemon 锁被持有。

## ⑥ 门禁预案

- `agent/` 编排件 selftest 与 pytest 同源判据（判据只收严）：现有 16 case 含 T1 触发判据、解析容错、RejectReason→HTTP 映射、消费期资格矩阵、`queue.find_any` 每股每日；新增行为必须先扩 selftest 再合入。
- 红线扫描机器门（`tests/integration/test_redline_scan.py`）：NR-2 下单符号族、paper 禁网络库、R2 密钥变量名只许 `cc_client/cli.py`、`state/` 禁 signals/ledger 字面量。
- HTTP 提交契约（面板半边）：kind 白名单=路由表六 kind、code=5/6 位数字（pydantic extra=forbid）；`RejectReason→HTTP`：DUPLICATE→409、LOCK/WINDOW→423、UNKNOWN_KIND→422（`server/schemas.py` 机器闸）。
