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

### 决策链写拓扑裁定（0.0.15 落地）

- **生产事故**：`decisions.jsonl` 出现 57 行仅 11 个唯一 seq、链断 7 处——三个叠加 bug：W1 serve 降级只读态（daemon 锁竞争失败不 load 文件）下面板写端点仍以空内存链从 seq=1/GENESIS 落盘（KeepAlive=true 常驻重启反复触发）；W2 面板侧追加立即落盘但不推进 `_persisted_lines` 游标，下一次 `_persist_all` 把已落盘行再追加一遍；W3 `acquire_daemon` 重载链对象但游标不重置，重启后首次 persist 重放全部历史；W4 `load()` 调 `verify_chain()` 丢弃返回值，断链静默续写。
- **写者侧单一入口（无条件全局规则：文件是 SSOT、内存是缓存）**：一切落盘追加必经 `DecisionLog.append(path=...)`——`agent/decisions.lock`（最内层锁；调用序恒 queue→decisions / sim→decisions）内尾部对账：内存空/落后即采纳文件态续链；内存超前于文件即 `DecisionChainError`（fail-closed，疑似回滚/篡改）；等长但尾行不一致即拒。`_persist_all` 的 decisions 段与 `_persisted_lines` 游标删除（W2/W3 随之消失）；全部 10 处落盘调用点（loop 7 + worker 1 + routes 3）收敛到该入口。
- **读侧 strict（判据只收严）**：`load()` 断链/坏行 raise `DecisionChainError`（含首个断点行号与修复指引）；坑位：只收严不上修复工具 = 生产断链文件让 serve 永久降级——修复 CLI 与 strict load 必须同 PR。
- **降级配套三件**：`acquire_daemon` 捕获断链→释放 daemon 锁、记 `chain_error`、返回 False（worker/watcher 不启动）；app lifespan 把 `apply_l2_config` 包 try/except（断链上 l2_probe 决策落盘会再抛，不炸面板）；全部写端点（submit/l2/briefing_ack/paper confirm/reject）`DecisionChainError`→503 附修复命令文案；`/api/monitor/status.loop.chain_error` 与①页 syscard「决策链·待修复」行呈现。
- **一次性修复 CLI `repair-decisions`**：纯函数 `repair_chain_lines` 保行重链（按原序保 ts/prefix/kind/payload，重算 seq/prev/hash），链尾追加 `chain_repair_started`（rows_before/old_sha256/breaks/tool）+`chain_repair_done`（rows_after）两条决策（payload 零敏感量，NR-5）；IO 壳先备份 `decisions.jsonl.bak-<ts>` 再原子写；坏 JSON 行即中止人工检查（不删行纪律下无降级路径）；完好链 noop。runbook：修复后 `launchctl kickstart -k gui/$(id -u)/com.threefish.stocktrading.agent` 重启 serve。

## ⑤ 预算闸与降级

- 预算闸默认关（人在环期仅记账，§13）；外闸在 `run_session` 前置预检（日/月 USD 信封+硬次数闸 50/日）；订阅轨 token+等效美元双口径记账写 `agent/budget.json`。
- 降级形态（设计内非故障）：daemon 锁被持有→面板降级只读（提交仍可入队，消费由锁持有者完成；`/api/monitor/status.loop.active` 可见）；锁在场/禁跑窗→提交 423 / 消费 defer；断言违例→拒落盘+`assert_refuse`；T1 失败→终态 failed 当日不重试；决策链断裂→serve 降级只读、写端点 503 附 repair-decisions 指引（0.0.15，§④ 裁定）。
- CLI `run-once` 退出码契约：0=done · 1=failed/被拒 · 2=队空或全部 defer · 3=daemon 锁被持有。

## ⑥ 门禁预案

- `agent/` 编排件 selftest 与 pytest 同源判据（判据只收严）：现有 16 case 含 T1 触发判据、解析容错、RejectReason→HTTP 映射、消费期资格矩阵、`queue.find_any` 每股每日；新增行为必须先扩 selftest 再合入。0.0.15 增 `state.decisions.tail_sync_repair`（空链直写续链+断链重链）；pytest 防回退族：尾部对账五象限（空链续链/双实例交替/落后重载/超前拒/等长分叉拒）、strict load 断链行号、repair 保行与备份、W1/W2/W3 端到端（未 start 提交续链、全生命周期 seq 连续、重启无历史重放）、断链降级 503+chain_error+syscard 行、CLI repair 黑盒 noop。
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
- **晨报/复盘卡**：`agent/briefing/<date>/<code>.<am|pm>.md` 每日每股每槽一卡（0.0.18 早晚分卡；legacy `briefing_review` 仍落 `<code>.md` 兼容旧卡/旧队列任务零迁移）；行动项措辞限定「复核」禁指令词（R7 同源）；脱敏三层（prompt 禁读模拟台账+断言器键扫+卡面数量词负断言）；ack 走 decision log `briefing_ack`（payload 带 session 槽位——ack 无 session 只命中 legacy 卡、带槽只命中本槽卡；hash 链防篡改；降级只读态 503、仅当日卡可确认、session 仅允许空/am/pm）；worker 钩子=断言放行后落盘+`briefing_card` 决策（payload 带 kind+session；失败即 failed 杜绝 done-无卡）。

### 早晚分卡与自动触发裁定（0.0.18 落地，§⑮ 两行翻绿）

- **双 kind 加槽（六层）**：新 kind `briefing_morning`/`briefing_evening`（routing.yaml 双模板带槽位化 prompt 与 NR-4 字段、schema_ref=briefing 收编可选 action_items 数组；dedup_key 与 UUIDv5 会话身份天然按 kind 分槽；REPORT_BLOCKING_KINDS/提交白名单两处同步收编；②页 dgform 下拉换双 kind、legacy kind 仍可 API 提交）。
- **自动触发**：判据纯函数 `plan_auto_briefings`（工作日 ∧ L2 总闸开 ∧ universe 可读 ∧ 槽窗内 ∧ 每股每槽当日未入队）——盘前窗 09:00–09:29、盘后窗 18:15–23:59（禁跑窗 18:10 含端点之后留余量）；worker 线程 60s 节流检查、source=monitor、universe 全码（不分 real/sim）、universe 不可读记 `briefing_auto_skipped` 决策后跳过（宁拒不漏留痕）。坑位：节假日未建模（多触发一次=一次小会话成本，宁多勿漏口径）；L2 关闭零入队（无人值守语义与 §⑩ 同源，不产决策噪音）。
- **面板⑥**：stats 聚合（kind 分布/任务三态成功率（样本 <30 标 insufficient，MIN_SAMPLES=30 与 postmortem 同值）/budget 双口径日桶曲线）；「全样本登记含亏损、模拟绩效假设性」常驻标注。

## ⑩ L2 无人值守裁定（0.0.9 落地）

- **触发路线=events 台账驱动**：watcher 偏移断点消费 monitor/events + `t2_worthy` 判据（12 规则枚举 ∪ mech_risk 非 None，与 t1_trigger 同构独立）→ auto-T2 入队。**不依赖 vibe-stock exit 2**（其 0.0.30 为零实现预留且坑位④补拉协调评审未发生；「会话不在场监控照常」是 07 现行合法态）——方案 D5 的 exit 2 时点裁定顺延至机制侧重启评审时再议。代价：重诊窗内 watch 继续产事件（dedup+5 票闸已兜）。
- **R3 入队纪律**：auto-T2 直建 TaskRecord（不走 loop.ingest——lock_precheck 在 watch.lock 持有中恒拒；不嵌 self.submit——record_monitor_event 已持 queue.lock 会 flock 自死锁）；幂等=`find_any("monitor_review:{code}:{market_today}")` 当日一次含终态（R4；与 panel 路径 UTC 日界差远离市场时段）。
- **L2 总闸**：`agent/config.json`（AgentConfig{version=1, l2_enabled 默认 False, billing_mode, updated_at}；加载纪律=缺席/坏 JSON/版本不符一律默认关宁拒不放）；面板⑦设置区 GET/POST `/api/l2`（开启方向 hx-confirm 二次确认；POST 要求 daemon 持有者否则 503）；开关变更记 `l2_toggled` 决策。**R1 联动**：l2_enabled→budget.enabled 同步、billing_mode 由 probe 判定持久化（判不准/失败一律 api 收紧，D2）；关闭仅封 monitor 源，panel/cli 人在环照旧。
- **消费期 L2 闸（NR-4 收口）**：`source=="monitor" ∧ kind==monitor_review ∧ !l2_enabled` → failed + `advice_refused(reason_code=l2_disabled)`；叠加序=defer（锁/窗可恢复）→ L2 闸 → 5 票闸 → 预算闸（终态递进，防终态吞 defer 补跑）；每次消费现读 config（面板关闸即刻生效于在队任务）。
- **CSRF 中间件（R2 收严）**：OriginGuardMiddleware 覆盖全部非 GET（/api/stream 豁免）——Origin（缺失回落 Referer）皆缺或跨源 403；白名单 host {127.0.0.1/localhost/[::1]/testserver} 任意端口。坑位：curl 脚本须 `-H 'Origin: http://127.0.0.1:8730'`；htmx 同源零影响。
- **预算分桶（修两实 bug）**：`record_dual_axis` 增 `daily{day:{usd,tasks}}`/`monthly{month:{usd}}`（旧全期键保留兼容面板；旧 ledger 零迁移）；`_budget_precheck` 改读当日/当月桶（market_today 钟面）——旧月闸空转（读从不写的 month_cost_usd_equiv）、tasks 全期计数两 bug 修复。D2 语义不动（subscription 仅次数闸+记账）。
- **防御件实用集**：①泄漏巡检（D-08）：启动+每小时扫 `pgrep -fl 'claude.*-p'` 差集 live_pids，孤儿记 `cc_orphan_detected`（log-only，kill 留 opt-in）；②`--setting-sources project` 无条件加（D-52：143s→2s；cwd 空目录=空集；不适用依赖 user settings 凭证注入的宿主——登记）；③permission_denials 解析入 CCResult + task_done 决策 payload（F-28 审计消费）。
- **升级位（只登记不实施）**：D-72 waitingFor 轮询（触发：L2 开启后观测挂起会话实锤）；F-56 PreToolUse hook deny（触发：需宿主侧零例外强制时）。

## ⑪ 崩溃重放与实验裁定（0.0.10 落地）

- **R5 重放策略**：启动期 running 残留全部先 abandoned（WAL 语义不变+task_abandoned 决策照记）；monitor 源残留按计划重放——判据纯函数 `plan_replays`（判据与 IO 分离）：monitor_review 另要求当下 l2_enabled（关则 `task_replay_skipped(l2_disabled)`，与消费期闸双保险）；**防自旋=队列派生判据**（同 dedup_key 实体数 ≥2 即 replay_cap——重放任务若再崩成 abandoned，count=3 仍 >2 拦截，同键至多两个实体任务，零新状态位）；t1_refresh（零 LLM）无条件重放；panel/cli 源维持 abandoned（M1 语义）。决策 kind 族：task_requeued / task_replay_skipped{reason_code}。重放≠保证执行——新任务消费期仍过全闸。
- **--bg 评估（D-63，文档性判定）**：结论不引入——依据 D-06 稳定性反证（一次性+resume 比长驻管道稳，两个 open issue 属同一会话族状态面）、现形态已覆盖保活（超时状态机+AbandonProcessGroup+R5 重放）、成本收益未证（L2 事件频率远未触达冷启动瓶颈）；触发再评条件见 [docs/ops/bg-session-eval.md](../ops/bg-session-eval.md)。
- **SDK 对照（D6，R7 只交脚本+模板不真跑）**：`ops/d6_sdk_compare.py`（--real 旗标+STA_ALLOW_REAL_CC 环境门；cli_path 钉同二进制+preset 对齐+setting_sources=[]，D-16/D-17）；记录模板 [docs/ops/d6-sdk-comparison.md](../ops/d6-sdk-comparison.md) 初始行「未跑，待用户触发」。

## ⑫ 面板七页重构裁定（0.0.11 落地）

- **信息架构**：七页 hash 路由（`#/overview|diagnosis|monitor|paper|advice|stats|audit`，纯 CSS/JS ~20 行无构建链；七 section 常驻 DOM——隐藏页 fragment 照常 180s+SSE compound trigger 轮询，本机单用户成本可忽略）；每页 hero 元信息带（①刷新+盘态/②会话+预算/③心跳+零 LLM/④台账注/⑤脱敏口径/⑥样本纪律/⑦hash 链头）。
- **视觉 token SSOT = index.html**：`:root` 18 组变量（bg/line/t/四语义色三态/字体栈）自原型提炼；**A 股红涨绿跌唯一语义色**（--up 红=涨/命中、--dn 绿=跌）；`.num` 等宽 tabular-nums；组件类（tbl/lv/riskchip/metachip/ev 时间轴/sig 五词点/leg/phase/todo/kchip/stat）自原型直取——panel.py 类名同 PR 原子切换。
- **呈现态原值三处**（用户裁定；落盘产物脱敏零改动重申 NR-5/R2）：events `facts` 透出（现价/机械事实）、`/api/advice/{date}/{code}` 响应 `rendered` 字段（运行时 sim-portfolio+tplus1+frozen 现算 t1_sell_shares；无持仓→null；`scan_advice_text` 负断言照过）、④表尾汇总条。诊断报告正文 md 直读（自带免责段，缺失时警示）。
- **新端点契约**（全 GET 零 CSRF 新面）：`/api/signals`（signals.json 只读+last_price→facts.price 现价序）、`/api/todos`（三源：pending fills→mech_risk→briefing 未 ack）、`/api/panel/{phase,topbar,signals,todos,navcard,syscard,overview(事件流),mometa,dgmeta,aumeta,tasks,reports,report/{dir}/{file},advice-detail/{date}/{code},dgform}`；report fragment 三保险路径安全（目录正则+日期文件名正则+resolve.is_relative_to）。
- **纯函数落点**：`server/panel_data.py`（market_phase 连续窗简化午休并入盘中——升级位/build_name_map 无名称源硬缺省 mono code——升级位/signals_snapshot 非法词呈现降级/derive_todos/briefing_action_items/薄读取器）；`stats.perf_summary`（Sharpe 日频年化/maxDD/胜率 FIFO，<30 insufficient）与 `t1_review_hitrate`（vs ledger.agent.json）；`loop/advice.parse_trend_table`（σ√h 表 round-trip 反解，与 render 同模块）；`stream_hub` 增 signals/reports 事件名。
- **轻量版过渡清单**（升级位）：②五段流水线/双轨意见板/裁决卡（P2 会话中间产物架构，另轮）、③通知预览（P2 通知台账）、①事件日历预告（c 类数据源）、股票名称源、现价源、午休分态。
- **0.0.12 翻绿三项**：①名称源回退链落地（`panel_data.load_name_map`：names.json 主源→signals stock_name→universe name 三源合并；产品仓只读，signals 表接名称列）；②事件日历（`aggregate_calendar`：T1 workdir+会话 workdir 的 evidence forecast.next_disclosure/unlock.upcoming/notices.items 聚合，`/api/calendar` 端点，①页中栏 kchip 渲染）；③通知预览历史重放（`rebuild_notifications`：watch.out.log notify 行（终态原文优先）+events notify=true 行双源，`/api/notifications` 端点，③页 macOS 通知样式卡+红线 7 说明——产品仓永不发通知）。港股 forecast/unlock 块 not_supported 如实空。
- **勘误（0.0.16）**：0.0.13 CHANGELOG「§⑫ 升级位全部翻绿收官」不实——**午休分态与现价源两项未翻绿、仍为升级位**（§⑫ L112-113 原文为准）；0.0.13 实际翻绿的仅 §⑫ 清单中的②五段流水线/双轨意见板/裁决卡一项。0.0.16 另补落地 0.0.11 虚报件：③事件表 facts 消费（现价/距线/mech_risk/名称列+breach/near 判据）、③⑤建议卡详情联动（advice-detail 渲染器接 hx-get 入口）、④表尾汇总条（`stats.paper_totals`）+坐标轴版权益曲线（`_axis_equity_svg`）+持仓名称列+盈亏红涨绿跌+建仓中高亮+状态 pill（st-open/st-hold/st-close 中文标签）、①hero 计数带 oob 接线（ov-sub/ov-ev-count/ov-todo-count）+晨报关键价位 kchip（signals key_levels 透传：止损恒显、买点/MA20 有键才显）+CC 行探针时刻、⑥T+1 复核命中率小卡（数据链 0.0.11 已通）、⑦会话登记表（`/api/panel/sessions`+render_sessions_table，三件套互查 sessions 腿）、③通知卡「锁屏不保证送达」边界句（方案 §1）；删除死码 render_overview（零调用）。

## ⑬ 会话观测裁定（0.0.13 落地，P2 §⑫ 升级位翻绿收官）

- **案 a 会后解析+轮询补**（用户裁定）：零通道/零跨仓改动——`server/session_products.py` 读 `agent/runs/<uuid>/.vs/` 中间产物（evidence/opinions×2/verdict×2/context-pack/报告存在性），defensive 缺席降级；流式 stdout 改造登记为**超远期不立项**（案 a 已满足观测诉求，执行核改造风险不复引入）。
- **五段状态机**：evidence 落地→①②done；opinions **非空数组**→③（铺底 `[]` 是骨架态不算完成——LLM 覆写后才推进）；verdict→④；reports/<code>.<market>/<date>.md→⑤；首个未完成=current。
- **观测反查键**：worker `build_request` 后把 `request.session_uuid` 回写 TaskRecord 并落盘（queue.json 终态保留）——`GET /api/session/{task_id}|/api/panel/session/{task_id}` 反查 `.vs/` 产物；②页「会话观测」区由任务卡同源 5s 轮询双写驱动（running 态进度条随 workdir 文件逐步落地自然点亮，非严格流式）。
- **渲染件**：五段进度条（done/cur/pend）+ 双轨意见板（default 逐卡 source/signal/confidence/weight、position 折叠计数）+ 裁决卡（initial→RiskGuard log→final、置信、分歧三桶计数、R3/R5 尾注）——组件自原型 diagnosis.html 语义对齐；dry-run 通道无 workdir 时如实空态（「待真实会话产物」）。

## ⑭ 面板 UI/UX 审计修复裁定（0.0.14 起分四 PR 落地）

- **审计取证（0.0.14 前置）**：七页逐页实走 + 三路实现/文档/测试侦察 + 9 路对抗核验（80 findings / 58 声明核验）——发现集中在 htmx 生命周期断链、两代渲染器并存（0.0.11 前旧 fragment 类名与壳 CSS 失配）、表格规范缺口三类。
- **htmx swap 契约（0.0.14 落地）**：**交换策略统一 innerHTML、挂点常驻壳层为 SSOT**——fragment 根不自带挂点 id；唯一安全例外是自轮询任务卡（outerHTML 自替换且根带 id，0.0.13）。提交类按钮 target 必须指向壳层既有容器（③复核→`#advice-detail`、briefing ack→`#ov-brief`、L2/paper→自身容器 innerHTML）。
- **报告文件名白名单（0.0.14 落地）**：`(?:\d{4}-\d{2}-\d{2}|index)\.md`——index 为固定字面量不扩大攻击面，目录正则与 resolve 边界两道保险不动。
- **后续三 PR 升级位**：0.0.15（列头全中文裁定：术语/机器值保留英文原词 + colgroup 固定列宽/跨表同宽/单行省略+title Tooltip/空值 — 占位 + ④绩效三格与⑥T+1 命中率已算未渲补齐 + ⑦链校验呈现 + ⑤卡详情链接接通）；0.0.16（首屏 load 触发——**裁定不动 sse.py multi init**（SSE 单流+180s 兜底契约钉死，load 触发零契约变更同效）+ 全局错误条 + hx-disabled-elt 防重 + ②表单行式（label 同行 1/12 + Tooltip）+ a11y 基线（tablist/aria-selected/:focus-visible）+ `--t4` 提亮过 AA）；0.0.17（CSS 去重与死码清理——**必须终态后重盘发射面**，坑位：`.op/.opins/.op-src/.verd/.sc/.sl/.ss/.stats/.stat/.ghostbtn` 为活码勿按原型残留误删 + 响应式两档 + md-lite 结构化渲染 + `_sparkline_svg` 泛化双曲线 + SSE retry 真化）。
- **0.0.16 并轨裁定（与 0.0.15 P1 内容补全同日合流）**：③事件表在其列头/表格三规范之上并入 facts 消费（现价/距线 breach|near/mech_risk riskchip/名称列）；④绩效三格复用其 `_perf_grid` 单一渲染件（本 PR 另补表尾汇总条/坐标轴曲线/名称列/状态 pill/tr.open）；⑤卡详情取其 code 链接形态；⑥t1_review 取其文案。
- **0.0.15 落地（P1 内容补全）**：列头全中文（机器值保留英文原词）；表格三规范（colgroup 固定列宽 + 列宽 SSOT 类跨表同宽 + scope/title Tooltip/空值 —）；④绩效三格与盈亏红涨绿跌、⑥T+1 命中率两处「已算未渲」补齐；⑦链校验呈现（DecisionLog.load 断链不抛仍渲染 + verify_chain 状态行）；⑤卡详情链接接通 + 拒卡 reasons 回退/五源图例/钟面短格式；oob 计数兑现三处死位；fragment 语义类（.muted/.ok/.warn/.task-card/h3）与 .mini 按钮类归位。
- **0.0.17 落地（P1 交互与壳层）**：全部轮询容器 load 首绘（裁定不动 sse.py multi init——SSE 契约钉死，load 同效零变更）；全局错误条（#global-err + responseError/sendError/afterSwap 三监听）；六按钮+表单 hx-disabled-elt 防重；②表单行式（label 同行 1/12 + Tooltip + for/id 关联，全局表单规范）；a11y 基线（tablist/tab/aria-selected/:focus-visible/scrollTo）；--t4 提亮 #7E8AA5 过 AA。


## ⑮ 悬空项登记表（0.0.16 建；消灭「既未落地也未登记」态）

> 规约句三属性：适用条件 / 做法 / 坑位。三态分类：**顺延**（本仓排期）/ **待裁定**（登记触发条件）/ **机制侧阻塞**（vibe-stock 仓为机制 SSOT，本仓不可独立推进）。本表为唯一悬空项索引，后续 PR 落地或裁撤即更新本表。

| 项 | 态 | 说明与触发条件 |
| :--- | :--- | :--- |
| ~~早晚分卡（盘前晨报 vs 盘后复盘行动项卡）~~ | ✅ 0.0.18 落地 | §⑨ 新增「早晚分卡与自动触发裁定」节 |
| ~~briefing 盘前/盘后自动触发~~ | ✅ 0.0.18 落地 | 同上（plan_auto_briefings 纯函数 + worker 60s 节流钩子；未引第二调度器） |
| §⑫ 现价源升级位 | 待裁定 | 触发：signals/facts 双源现价口径冲突实锤；方案=读 monitor/state.json watch 快照价/T1 evidence 价合并进 price_map 带 asof 陈旧标注；公开行情直取需新裁定（产品仓 src/ 现零网络面） |
| §⑫ 午休分态升级位 | 待裁定 | panel_data.market_phase 连续窗简化（CN 11:30–13:00/HK 12:00–13:00 并入盘中）；对闸与调度零影响，纯呈现粒度；与现价源同轮裁定 |
| 买点/MA20 key_levels 扩键 | 机制侧阻塞 | ①晨报 kchip 已按「有键才显」实现；阻塞方：vibe-stock signals.json key_levels 现仅 stop——扩键后本仓自动点亮 |
| F-57 paper_confirm defer 链（-p 第四决策值挂起等人工） | 机制侧阻塞+待裁定 | §13 承诺；现状 paper_confirm kind 用户可达但 CC 链 defer/resume 通道零存在（build_argv 无 --permission-prompt-tool）；触发：paper_confirm 真实使用面出现再裁（先裁定该 kind 是否裁撤或改语义） |
| 升级门禁三件套+versions/ 回滚（§14、R-03 缓解主体） | 顺延低优 | --version 探针+--help 旗标快照 diff+init.capabilities 探测+CHANGELOG 扫描；已落半边（DISABLE_AUTOUPDATER/probe/--system-prompt-snapshot off）；触发：首次主动升级 CLI 版本前必做 |
| caffeinate -i 交易时段防睡死（I-50） | 待裁定 | launchd 日历槽不唤醒睡死 Mac；触发：出现「槽错过+机器睡眠」实锤再上（面板守护 KeepAlive 已覆盖自愈面） |
| agent/budget.json 面板写半边（§11 读写矩阵） | 待裁定 | 现仅 GET 只读；enabled 联动经 POST /api/l2 达成——「读写」宣称要么补写端点要么勘误矩阵（倾向后者：写面收敛 L2 端点更安全） |
| sim 台账逐笔 hash 链（方案 §12 纪律） | 待裁定 | 13-paper §① 已降格为 append-only（无排除裁定）；decisions 链 0.0.15 修复后 sim 链是否补齐待裁；触发：sim 台账作为审计证据被实际消费时 |
| §15「预测 vs 实际」校准分桶表 | 机制侧阻塞 | 披露形态复用 outlook calibration——阻塞方：outlook 侧形态定义（vibe-stock）；本仓 stats.py 待其对齐 |
| ③页「导出脱敏卡片/标记已复核」按钮（原型对账项） | 待裁定 | 「标记已复核」需新决策 kind（现仅 briefing_ack）；原型承诺件非 CHANGELOG 明文——裁：立项或登记原型差异 |
| D5 watch.py exit 2 接管时点 | 机制侧阻塞 | 07 坑位④协调评审未发生（§⑩ 已采 events 台账驱动替代路线）；触发：vibe-stock 侧 exit 2 实施重启评审 |
| D6 SDK 对照真跑 / D8 定谳 / T2 出卡真跑 | 运维待办 | docs/ops/trading-day-verify.md 承载（D8 周一 2026-11-10、T2 盘后唤起、D6 用户触发）；不属代码悬空项 |
| 流式 stdout 改造 | 不立项 | §⑬ 超远期不立项（案 a 已满足观测诉求） |
| D-72 waitingFor 轮询 / F-56 PreToolUse hook deny | 条件触发 | §⑩ 升级位原文（触发条件已登记，不重复） |
