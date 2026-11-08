# 存储选型决策记录：不引入 SQLite / PostgreSQL / seekdb（重申方案 §13）

> 决策日期 2026-11-08；状态：已裁定（本轮全局盘点重申既有裁定，零代码变更）。
> 盘点动机：用户四项痛点全中（面板体验变慢 / 状态无限增长 / 一致性可靠性疑虑 / 历史检索困难）+ 三选项选型确认；硬约束：不破坏「开箱即用」。
> 取证方式：四路只读盘点（写入侧 / 读取侧 / 文档约束 / 实测规模）+ 主线亲读承重文件 + 三视角裁决 Panel（最小干预派 / SQLite 索引投影派 / 最强反方）+ 对抗核验官逐条实证复核，全部结论收敛。

## 1 裁决

**本期不引入任何一个数据库：零新依赖、零迁移，重申 [main-agent-proposal.md](main-agent-proposal.md) §13「数据面单文件/JSON 原子写、不引数据库」（代码化于 [store.py](../../src/stock_trading_agent/state/store.py) 与 [app.py](../../src/stock_trading_agent/server/app.py) 模块 docstring）。**

| 选项 | 裁决 | 理由 |
| :--- | :--- | :--- |
| SQLite | 现在不引入；**唯一合法下一档候选**（触发判据达成后以「只读索引投影」形态进入，见 §5/§6） | Python stdlib（零新依赖、CI 零改动），是三者中唯一不与开箱即用冲突的形态；但实测规模距触发线差 2-3 个数量级（decisions 57 行 vs 10 万行触发线），且本轮全部实证缺陷均为实现 bug 非范式缺陷——换库一条都不自动解决 |
| PostgreSQL | **否决** | server 进程与「launchd 单进程常驻 / uv sync 一命令装 8 件依赖 / CI 无 services 无 secrets」三面正面冲突；其优势区（多写者并发 / 服务级 ACID）在本仓「daemon.lock 单写者 + tmp+fsync+os.replace 原子写」范式里不存在——真问题（跨文件多步写窗口）要靠文件层写序收敛修，不是换引擎 |
| seekdb | **彻底否决** | OceanBase 2025-11 开源的 [AI-Native 搜索数据库](https://github.com/oceanbase/seekdb)（[dbdb.io 条目](https://dbdb.io/db/seekdb)）：服务端形态与零外部服务承诺正面冲突、开源约一年成熟度未过观察期；其差异化能力（向量 + 全文 + JSON 混合检索）与本系统「按 kind/task_id/code 精确过滤 + tail」的查询形状（[routes.py](../../src/stock_trading_agent/server/routes.py) 全读侧）零交集——为不存在的需求付架构税 |

## 2 证据

### 2.1 既有决策是「评估后排除」，不是盲区

调研 [main-agent.md](main-agent.md) ch12 显式评估过 SQLite WAL（I-44，多读者 + 单写者、仅同机文件）与 7 个 DB-backed 竞品先例（Ghostfolio/Jesse/rotki/Wealthfolio/Portfolio Performance/Fava/InStock）；ch12 启示曾允许「单文件/SQLite(WAL) 最小落盘」，获批方案 §13 收紧为「不引数据库」。推翻已批裁定须人工评审 + SemVer 0.1.0 语义级版本 + CHANGELOG 登记；本轮证据没有一条达到推翻门槛（全部缺陷属实现级，见 §2.2）。

### 2.2 规模实测（2026-11-08，状态根 ~/.vibe-stock）

- 状态根 11 天共 **12MB / 451 文件**；本仓 agent/ 写区仅 **392KB / 14 文件**（decisions 57 行/21KB、sessions 3 条、queue 5 任务、audit 4 个 dryrun 桩、panel.out.log 250K）。
- 增长构成（按源分列，防外推盯错对象）：**reports/ 9.1M 为第一大**（其中 hs300 批扫原始 CSV 一次性 8.1M，属「诊断扫描突发型」；常规报告 .md ~90K/日）；monitor/ 2.0M 第二（ticks 1.8M，~0.45MB/交易日线性型）；outlook ~70K/日。三源均在外仓 vibe-stock 写区，本仓零接触无权迁移。
- 一年外推 150-250MB（最坏 300-500MB），MB 量级纯文件原子写轻松承载；台账侧一年仅数千至数万行。
- 唯一未知增量：L2 放开后真实 CC 会话 audit stream-json 归档（零实测样本）——由触发判据 T4 盯住（§5）。

### 2.3 实证缺陷分类：全部实现级，零条「结构性且范式内不可修」

| 缺陷 | 证据 | 定性 |
| :--- | :--- | :--- |
| decisions.jsonl 增量游标 bug 生产破链 | 57 行仅 11 个唯一 seq、46 行重复、`verify_chain()=False` 且返回值被忽略（[decisions.py](../../src/stock_trading_agent/state/decisions.py):74）——根因三处：游标只在 `AgentLoop.__init__` 置 0（loop.py:95）、acquire_daemon 换入盘上链不重置（worker.py:169）、`_decide` 直写盘不推游标（worker.py:204-206）→ `_persist_all` 从 0 重灌全史（loop.py:422）；旁路写点共 4 处 | 实现 bug（详见 [issue.md I-003](../.agents/issue.md)） |
| audit 归档绕开落盘单一入口 | 裸 `open("a")` 无 flock 无 fsync（cc_client/cli.py:301-304），全仓唯一不走 store.py 的台账写点 | 实现 bug（W7 收编） |
| watcher.json 无效高频重写 | 每 2s 无条件全量重写 ≈4.3 万次/日（watcher.py:52-58） | 实现 bug（W6 条件写） |
| DirWatcher 丢弃变更集改全树扫描 | 4 个重叠 roots、每 ~2s `sorted(rglob)+逐文件 stat`，O(总文件数) 无界（dirwatch.py:35-52、app.py:73-79） | 实现 bug（R5' 收窄） |
| 跨文件多步写无事务窗口 | 任务终态跨 queue/sessions/budget/decisions 四文件分步落（loop.py:259-285,414-425）、paper 确认双文件两步（routes.py:783-813）；崩溃窗毫秒级，留脏态（预算多记/缺 session 行/confirmed 残留 409） | **半结构性**：范式内有解——写序调整（queue 终态最后落）+ 启动对账自愈，或 decisions.jsonl 升格 journal 化投影重建；不需要数据库事务 |

「面板体验变慢」的真实成因同样非 IO：htmx 2.0.9 `every` 为 setTimeout 首延触发，安静系统首屏可空转 180s（SSE 无初始快照推送，时段性出现于非交易时段）；一次 SSE 扇出 40+ 次文件打开但合计 <1MB，当前毫无压力。修法见 §4。

### 2.4 三个文本态契约锁死 SSOT 不能动

支柱③「全状态在 ~/.vibe-stock 可审计（cat/jq 级）」；decision log 以 `agent:` 前缀被外仓 18:05 postmortem 槽按行拾取——[vibe-stock agent_postmortem.py](https://github.com/ThreeFish-AI/vibe-stock/blob/main/scripts/agent_postmortem.py) 硬编码单文件 `~/.vibe-stock/agent/decisions.jsonl`、仅取 `advice_card_written`、按 `f"{date}:{code}"` 键控 last-wins、T+1/3/5 回看（最长 5 个交易日）且幂等索引在其仓 ledger.agent.json；advice 卡按文件路径被 vibe-stock 读走。已核实推论：decisions 月度切分契约安全（当月窗口 ≫ 5 日回看）；生产 46 个重复行已被外仓消费但 payload 逐字相同，键控 last-wins 下无副作用，去重修复不改变外仓视图。任何「真迁移」方案都要三处重签跨仓契约；投影方案（§6）一处都不碰。

### 2.5 红线机器门与文本形态

NR-3/NR-5 产物断言器是落盘前内存态扫描（敏感键正则 + 渲染 md 份额负断言），理论可平移 DB 写路径，但 md 落盘负断言自检与「人可直接 cat 审计」依赖产物是文本；[test_redline_scan.py](../../tests/integration/test_redline_scan.py) 扫源码不受影响。外仓 vibe-stock 自身零 sqlite 使用（全仓 grep 无匹配）——DB 对本状态根是全新面，不存在跟随外仓的先例选项。

## 3 四痛点 → 成因与修法映射（全部文件范式内）

| 痛点 | 真实成因 | 修法 |
| :--- | :--- | :--- |
| 面板体验变慢 | 协议与扫描实现：htmx 首延 180s + DirWatcher 全树扫 + 零缓存全量读（时段性，非 IO 瓶颈） | **R1'** 20 处 fragment 的 `hx-trigger` 前缀加 `load,`（一行属性，立即消除首屏空转，零服务端代码）；**R1''** SSE 建连初始快照（第二段，覆盖断线重连）；**R5'** DirWatcher roots 去重 + 快照循环跳过 monitor/ticks 谓词（~5 行）；**R2** 进程内 mtime 短路缓存；**R3** mtime+size 缓存读（T1 触达后升级字节偏移续读）；**R4** 请求内 memo + task_id→uuid 索引（消三跳反查被 5s 轮询放大） |
| 状态无限增长 | 策略缺失（全仓零 retention）；大头在外仓（reports/_scans + ticks），本仓写区 ~11MB/年 | **G1** 六桶 retention（归档非删除，月度 JSONL append 复用 store.py 纪律，恢复=cat+jq）：queue 终态 30 天/500 条、sessions 90 天、audit 90 天 gzip、runs/ 14 天、budget daily 桶 180 天、decisions 月度切分（切分契约已核实安全，实施时登记 CHANGELOG 并知会外仓）；**G2** ops 巡检脚本（d8_probe 同形 stdlib 直跑）按三源分列自动采集——判据数据源，不能靠人肉盯 |
| 一致性/可靠性疑虑 | 游标 bug（已破链）+ 跨文件写序 + 绕入口写点；历史上三起状态层缺陷均在文件范式内修好（WAL 不落盘 0.0.3、锁存在性误判 0.0.4、预算分桶 0.0.9，CHANGELOG 可查） | **W1** 游标修复（最小形态 3 行：worker.py:169 载入后置 `_persisted_lines = len(decisions.lines)`；彻底形态=决策落盘唯一入口化收编 4 处旁路）；**W2** repair-decisions 链修复子命令；**W3** verify_chain fail-loud；**W4** 双写路径回归测试；**W5** 写序调整（queue 终态最后落）+ paper confirmed 孤儿启动清理；**W6** watcher 条件写；**W7** audit 收编 store.py |
| 历史检索困难 | 唯一真正的数据结构缺口；但当前 2k 决策/年量级 jq/grep 亚秒级，且这正是支柱③承诺的审计形态 | 近期：G1 归档 + jq 查询配方；**触发判据 T1-T5 任一达成后**：queryindex 只读索引投影（§6），SSOT 不迁、跨仓契约不动 |

## 4 触发判据（T1-T5）

适用条件：SQLite 索引投影进入人工评审的唯一门禁；做法：全部由 G2 巡检自动采集（可观测判据），任一满足即启动评审，「用户提出诉求」类主观判据一律换成可计数代理（如历史端点查询计数器）；坑位：判据不自动采集会退化成「永远下不了决心」或反向拍脑袋引入——G2 是本决策的配套义务，不是可选项。

- **T1 台账行数/解析成本**：decisions 单月热文件 >10 万行（约 30MB）或全量解析冷读 P95 >300ms（当前 57 行，余量约 3 个数量级）。
- **T2 SSE 扇出延迟**：一次 multi 事件 20 fragment 重 GET 端到端 P95 >1s（localhost），且 R2 缓存落地后复测仍不达标。
- **T3 DirWatcher 扫描成本**：单次 poll_once >250ms 或状态根文件数 >5000（当前 451），且 R5' 收窄后复测仍超（说明外仓增长逼到本仓桥上）。
- **T4 audit 实测增量**（唯一未知量）：L2 放开后 30 天内出实测报告，agent/audit/ 单月 >1GB 或单会话 stream-json 均值 >20MB 即触发。
- **T5 跨月检索日常化**：历史检索端点（若上线）跨归档月查询占比 >30%（须同步埋查询计数器）。
- **负触发**：R 族读侧修法落地后面板 P95 达标且无跨月检索诉求 → 索引无限期推迟，蓝图归档备查。

## 5 触发后形态：queryindex 只读索引投影蓝图（本期不实施，全文以本节为准）

### 5.1 目标与边界

只解决历史检索/聚合查询：decisions 按 kind/date/code/关键词跨月检索、queue 终态任务按日按 kind 统计分页、sessions 月度 token/cost 聚合、monitor events 跨日按 code 取尾。**文件台账仍是唯一 SSOT 与 NR-3/NR-5 断言器唯一扫描对象；索引是可随时删除全量重建的查询加速副本，绝不回写。** 明确不解决：跨文件写原子性（W5 写序收敛）、外仓 SSOT 迁移（零接触铁律）、retention（投影不裁事实）、180s 首屏空转（R1' 客户端修）、热路径（当日 events/queue/portfolio 均 <1MB，全量读即最优，不进索引）、audit stream-json 检索（无实测样本，只留壳表占位不灌数据）。

### 5.2 模块落位与依赖

新包 `src/stock_trading_agent/queryindex/`（index.py 建表/连接 + sync.py 对账 + api.py 查询函数），与 state/ 平级；依赖单向只读（queryindex → state.paths/store 只读函数与 loop.monitor_events 读取器），state/ 与 loop/ 一律不得 import queryindex（写侧零改动）；仅 server/routes.py 新增的 `/api/history/*` 只读端点消费。索引落 `agent/index/query.db`（paths.py 增 index_dir property；agent/ 子树对 SSE 是 default-deny，`agent/index/*` 天然不产生变更事件——补一条「agent/index/ → 无事件」回归钉即可，无需过滤代码）。stdlib sqlite3（WAL、`synchronous=NORMAL`——派生物不需要 FULL）：零新依赖、CI 零改动；FTS5 在本项目 uv 管理的 Python 实测可用（SQLite 3.53.1 编译启用），audit 全文检索届时保留 FTS5 选项。

### 5.3 同步机制与崩溃语义

mtime+size 校验和对账式惰性增量：sources 表记每源 (mtime,size,lines)，查询前 stat 源文件不符才重读（JSONL 用 size 偏移增量读，与 EventsWatcher 偏移断点同语义；JSON 整文件变更即整表 upsert）；事件驱动只当 hint 不当事实（watchfiles 退化路径会静默丢事件），启动全量重建仅兜底。崩溃/损坏语义：PRAGMA quick_check 失败、schema_version 不符或任何 sqlite3.DatabaseError → 删 db(+wal/shm) 全量重建，期间查询降级回文件直读旧路径。前置强依赖：**索引绝不能建在破链文件上——W1/W2/W3 修复完成是 queryindex 的硬前置**。

### 5.4 schema（要点）

meta(key,value) 存 schema_version；sources(path,mtime,size,lines) 对账账本；decisions(line_no 主键=重建幂等键, seq, ts, kind, code, payload_json 原文整行, ts_day 生成列) + (ts_day,kind)/(code)/(kind) 索引；tasks(终态 only) + (ts_day,status)/(kind,finished_at) 索引；sessions(uuid 主键, task_id, usage, cost, ts_month) + (ts_month)/(task_id) 索引；monitor_events(date,line_no 主键, code, kind, payload_json) + (code,date) 索引。不建表：advice 卡（跨仓文件契约）、audit（无样本）、ticks/批扫 CSV（外仓）。

### 5.5 红线适配

NR-5 不对索引二次扫描的论证：索引所有列逐字节源自断言器已放行的落盘文本，投影不产生新敏感面；但必须加不变式测试钉死「索引每行 payload_json 与台账对应行逐字节相等、无合成字段无 join 产物落库」，且模块 docstring 立规「禁止 ingest 任何不经过 store.py 落盘的数据」——防索引成为绕过扫描的侧门。R3/R8：queryindex 不出现 signals.json/ledger.json 字面量、不写 monitor/ 与根级任何文件（test_redline_scan 的 FORBIDDEN_STATE_PATHS 只扫 state/ 子树，新包不引用两文件名即天然通过，另加镜像自测断言双保险）。

### 5.6 §13 修订程序与已知坑位

适用条件：触发判据任一达成且决定实施 queryindex 时；做法：**修订三件套严格先于任何 main 合码**——人工评审记录（本文件为依据）+ SemVer 0.1.0（语义级：新增派生数据面）+ CHANGELOG 登记，§13 修订为「台账维持单文件 JSON 原子写且为唯一 SSOT；允许一个可随时删除重建的只读查询投影（非事实源、stdlib sqlite3、agent/index/ 下）」；坑位：`STA_QUERYINDEX=1` 默认关的软开关在获批前合入 main 即构成变相绕行，只许存在于特性分支；可加轻量 CI 门（src/ 出现 `sqlite3.connect` 时要求本文件存在该条款）。

实施期坑位清单：sqlite3 连接默认 `check_same_thread=True`，FastAPI 同步端点走线程池——必须每线程连接或单连接加锁（写进 design，不留白）；WAL 三件套（db/-wal/-shm）非原子，Time Machine/cp 单拷主库得旧态——文档明示「备份只须备份台账文件，agent/index/ 整目录可丢」；双源漂移——索引只承诺最终一致，查询响应带 as-of 元数据、UI 标注「索引投影非实时」；防范围蔓延——模块不暴露任何写台账函数，评审门禁盯「读侧先富起来变双 SSOT」。

## 6 修复路线（0.0.15 已全量落地；拆序即优先级）

> 2026-11-08 状态：W1-W7 / R1'-R5' / R1''-R4 / G1 / G2 全部实施（规约 [12-orchestration.md §⑭](../specs/12-orchestration.md)）；R3 以 mtime+size 短路缓存形态落地（当前量级整读即最优，字节偏移续读登记为 T1 触达后升级位）；生产 decisions.jsonl 破链已被先行修复（59 行全唯一），升级 0.0.15 后根因不再复发（升级手册见 [issue.md I-003](../.agents/issue.md)）。

1. **P0（先于一切读侧工作）**：W1 最小形态 + W3（含「拒启 vs 只读降级」裁定——生产文件已破链，直接拒启会让 daemon 起不来，须与 W2 同 PR 或先 W2）+ W2 生产链修复 + W4 回归测试。缺陷拖延会让范式背锅，反方向加固「不引数据库」的裁定。
2. **读侧速效（~25 行）**：R1'（htmx `load,` × 20 处）+ R5'（roots 去重 + 跳过 ticks 谓词），消除首屏空转与扫描无界增长。
3. **其余**：W5-W7 / R1''-R4 / G1-G2 按本文件拆序推进；W1 彻底形态（唯一入口化收编 4 处旁路）单独成 PR。

## 7 参考

- [main-agent-proposal.md](main-agent-proposal.md) §10⑤/§11/§13（状态布局、落盘裁定原文）
- [main-agent.md](main-agent.md) ch12（I-44 SQLite WAL 评估与 DB-backed 竞品先例）
- [12-orchestration.md](../specs/12-orchestration.md)（状态层实施规约与读写矩阵代码化说明）
- [issue.md](../.agents/issue.md) I-003（decisions.jsonl 游标 bug 生产破链，P0）
- [vibe-stock agent_postmortem.py](https://github.com/ThreeFish-AI/vibe-stock/blob/main/scripts/agent_postmortem.py)（外仓 decision log 消费契约：硬编码单文件、键控 last-wins、T+1/3/5 回看）
- [oceanbase/seekdb](https://github.com/oceanbase/seekdb)（seekdb 形态核实）
