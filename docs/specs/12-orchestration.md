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
- 禁跑纪律（无条件全局规则：编排器永不主动向 CC 子进程发 SIGTERM）：SIGTERM=143 语义为外部弃轮、SIGINT 才是收轮正道（D-01）；退出码归一化 `-sig → 128+sig`（130 收轮 / 137 SIGKILL 兜底 / 143 弃轮）。
- **真实通道完整版（0.0.4 落地）**：per-call `asyncio.run`（线程内无 loop 主路径、async 误用回退单线程池）；`create_subprocess_exec` + `start_new_session=True` 独立进程组 + `_live_procs` 持引用防 GC（I-37）；超时状态机 = `timeout_s`（首次消费）→ `killpg(SIGINT)` 收轮（宽限 `SIGINT_GRACE_S=10`）→ `killpg(SIGKILL)`，最坏总时长 = timeout_s+10s；坏行/未知行型不炸、原样进 raw 缓存（审计保真，R-03 版本漂移容错）；无 result 行 → result_line_present=False（D-05）。**实测坑位三则（2.1.319 真跑）**：①子进程 stdin 必须显式 DEVNULL——继承父进程 stdin 时 claude -p 等待 3s 且在部分宿主环境（pytest）下直接 exit 1 无输出；②`--mcp-config` 空配置必须是带 `mcpServers` 键的合法 JSON 文件（`/dev/null` 与裸 `{}` 均报 Invalid MCP configuration，D-53 优化不回退）；③`--session-id` 复用已存在会话——重复值会接续旧上下文（探针/调试须每次新鲜 uuid，生产路径由 UUIDv5 日期键天然防重）。
- **probe 认证态启发式**（F-31/D2）：环境密钥名在场→api；`~/.claude/.credentials.json` 在场→subscription；其余→api（判不准收紧）；`billing_mode_from_probe` 联动 BudgetConfig（subscription 透传、其余一律 api）。坑位：只判密钥名在场永不取值（R2）；macOS Keychain 形态未覆盖属启发式，deep 探针（mini spawn 读 capabilities）真实花钱默认关。
- **断言器收严两条（0.0.4）**：result subtype 非 success（运行内失败打印为 result，D-01）；init.mcp_server_errors 非空（F-23）。
- **归档命名**：真实通道 `agent/audit/<date>/<session_uuid>.jsonl`、dry-run `dryrun-<task_id>.jsonl`（两通道产物可辨）；spawn 期 raw 行只驻内存、断言器放行后 archive() 落盘（NR-3 次序在通道层同样成立）。
- **routing.yaml monitor_review 修错裁定（0.0.4）**：allowed_tools 由 [Read,Glob,Grep] 修正为 [Read,Write,Glob,Grep,Bash]。论证：NR-1 无条件要求 T2 会话跑 adjudicate 原命令（需 Bash）与 risk.json 机械覆写（需 Write），旧值使该会话物理不可能履约（每次运行必违例或永久降级）——属修错非放宽；修正集是 diagnose_full 工具面的严格子集且为满足 NR-1 的最小集；budget_usd/max_turns 不动；机器钉 `test_monitor_review_has_nr1_tools`（⊇{Bash,Write} 且 ⊂diagnose_full）防回退。用户覆盖表的只收严闸管「覆盖 vs 默认」，默认表自身修正走新裁定+CHANGELOG 登记程序。
- **CCRequest.cwd**：会话工作目录注入（`agent/runs/<session_uuid>/`，spawn 前建）；DryRun 通道忽略该字段，契约两通道同构。
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

## ⑦ launchd 第四槽守护（0.0.4 落地）

- 模板 `ops/launchd/com.threefish.stocktrading.agent.plist`（占位符 `__UV_PATH__/__REPO_PATH__/__HOME__`，安装见 [docs/ops/launchd.md](../ops/launchd.md)）：Label `com.threefish.stocktrading.agent`；ProgramArguments=`<repo>/.venv/bin/stock-trading-agent serve`（直接调 console script；坑位：uv run 包装会使 launchd 盯错进程、python 成孤儿占端口——本机实测；前置 `uv sync` 建 .venv）；RunAtLoad=true（随登录自起）；KeepAlive=true 常驻自愈（坑位实测：KeepAlive={Crashed} 的 after-crash 信号量在本 macOS 挂起不触发，kill -9 后 90s 无重启——故取常驻形态，ThrottleInterval=30 节流，停机走 bootout，daemon 锁冲突重试至锁释放后自动回归）；AbandonProcessGroup=true（D3：守护重启不连杀在跑 CC 子进程，与 start_new_session 双保险）；ExitTimeOut=20 > `serve --graceful-shutdown` 默认 10（关停在杀之前完成，R-12）；ThrottleInterval=30；日志 `~/.vibe-stock/agent/logs/panel.{out,err}.log`。
- 模板**不带** STA_ALLOW_REAL_CC（出厂 dry-run 安全）；启用真实通道由用户安装时注入 EnvironmentVariables（文档明示后果与预算闸建议）。
- WatchPaths/StartCalendarInterval 不用（I-52 race-prone；常驻服务非日历槽）；三份 vibe-stock plist 零接触。
- 模板关键属性由 selftest/单测 plistlib 钉死（KeepAlive/AbandonProcessGroup/ExitTimeOut/ThrottleInterval/RunAtLoad/占位符），漂移即测试红。

## ⑧ T2 建议卡链裁定（0.0.5 落地）

- **ADVICE_SCHEMA 全量契约**（`loop/schemas.py`）：`outcome enum [card, facts_only]`（降级一等公民）+卡面字段（candidate_action=vibe-stock/04 五档逐字枚举、final_signal 五词、prev_diff、t1_sell_pct 0..100、t1_sell_tier、verdict_date、opinion_data_date、replayed_opinions≥2、trend_ref.band_rows、rationale、facts_note）；扁平子集无 if/then，分支必填由断言器程序化（防 CC 端 schema 方言风险）；喂 CC 与本地复验同一份（NR-3 延伸）。
- **脱敏字段命名与双保险**：字段名避开断言器敏感词族（禁 sellable 等前缀，扫描零豁免零放宽）；份额原值负断言=渲染层单位正则（股|份|手）+断言层键扫双保险；卡面常量（三声明/免责尾注）置空视为缺失（空串恒子串坑位）。
- **三声明句裁定**：产品侧等义短句+vibe-stock 绝对 URL 指针落卡面，禁转述外仓规约句原文（AGENTS SSOT 纪律）；断言只钉存在性与关键词。
- **锚③落位**（只降不升三重锚的编排侧唯一可独立复算项）：signals.json 只读读取在 loop/advice.py（layout.root 直拼；红线扫描字面量禁令只覆盖 state/ 层，state/ 零字面量纪律不变）；锚①②归 vibe-stock adjudicate 机制。signals 在场不可解析→拒卡（宁拒不漏）。
- **保鲜门编排侧投影**：verdict_date 与 opinion_data_date 均==市场今日（断言）；「有效意见<2 回退重产」由 replayed_opinions≥2 投影；真实重产在会话内（07 机制）。
- **写者例外执行序**：断言放行→render→scan_advice_text→atomic write（`state.store.atomic_write_text` 单一入口）→`advice_card_written` 决策（payload 全脱敏：task_id/code/date/path/candidate_action/final_signal）→queue done；任何一步失败即 failed+`advice_refused`，杜绝 done-无卡。facts_only（会话自报降级）=task done+`advice_refused(origin=session_degraded)` 携 facts_note，不触 advice/ 目录。
- **5 票闸与实盘优先**（07 硬前置）：消费期判定（mark_running 后、build_request 前；配额随日变化消费时刻权威）；计数口径=当日 done 的 monitor_review（含 facts_only，enqueued_at 市场钟面匹配——规避 ingest dedup UTC 日期的既有时区日界差）；超额仅放行 monitor/universe.json kind==real；universe 不可读+超额→宁拒不漏；拒绝=failed 终态防自旋。
- **advice_refused 决策 kind**：五源统一（gate/assert/render_scan/write_failure/session_degraded），monitor_review 断言违例由 assert_refuse 改判此 kind（其余 kind 维持）；面板⑤拒卡/降级通报区消费。
- **locks 语义收敛**：/api/monitor/status locks 由存在性改 lock_is_held()（0.0.4 修复的闸语义延伸到展示面）。
- **monitor_review prompt 扩展**（默认表修正程序沿 0.0.4 先例）：T1 pack 指针+NR-1 链指令+facts_only 出口+写者纪律（禁写 advice/ 与 signals.json）；模板禁 {code} 外花括号（router 测试钉 .format 不炸）。

## ⑨ SSE 与晨报/复盘卡裁定（0.0.7 落地）

- **单流单端点** `GET /api/stream`（sse-starlette EventSourceResponse，15s ping）：多目录变更以命名事件区分（queue/decisions/budget/monitor/advice/status/outlook/paper/briefing；同拍多事件并包为 multi）；localhost 单页一条连接（I-21 上限约束）。
- **事件桥独立**：泛型 `loop/dirwatch.py`（mtime 快照 diff，与 EventsWatcher 分立——后者承载 T1 偏移断点领域态）；不依赖 daemon 锁（只读桥）；watchfiles 事件只当触发器。
- **轮询兜底**：面板 compound trigger `every 180s, sse:X`（逗号=OR）——SSE 挂了轮询仍在（I-23）；htmx-ext-sse 本地 vendor（2.2.4，sha256 锚入 vendor/README）。
- **锁态快照坑位**：vibe-stock 两锁 flock 获取/释放不改 mtime（锁文件不删除）——锁态不可文件监听，SSE 侧 30s 节拍快照 diff 推 status 事件。
- **晨报/复盘卡**：`agent/briefing/<date>/<code>.md` 每日每股一卡（同日重跑覆盖；早晚分卡留 M3）；行动项措辞限定「复核」禁指令词（R7 同源）；脱敏三层（prompt 禁读模拟台账+断言器键扫+卡面数量词负断言）；ack 走 decision log `briefing_ack`（hash 链防篡改；降级只读态 503、仅当日卡可确认）；worker 钩子=断言放行后落盘+`briefing_card` 决策（失败即 failed 杜绝 done-无卡）。
- **面板⑥**：stats 聚合（kind 分布/任务三态成功率（样本 <30 标 insufficient，MIN_SAMPLES=30 与 postmortem 同值）/budget 双口径日桶曲线）；「全样本登记含亏损、模拟绩效假设性」常驻标注。
