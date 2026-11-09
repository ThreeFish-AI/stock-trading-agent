# Issue 记录

> 处理过的 Issue 摘要：头部索引表 + 主体详情锚点；ID 形如 `I-001` 全文唯一递增。

| ID | 标题 | 状态 | 完成时间 | 详情 |
| :--- | :--- | :--- | :--- | :--- |
| I-001 | 建仓迁移：主 Agent 文档族自 vibe-stock 0.0.28 迁入，原型 A 股代码假码化 | ✅ 已完成 | 2026-11-07 | [详情](#I-001) |
| I-002 | T1 轻量日 K 链机制升级候选（vibe-stock CLI 暴露），现采全量 fetch+prepare 过渡口径 | ⏸ 暂不处理 | — | [详情](#I-002) |
| I-003 | 跨午夜时敏测试钉死 2026-11-08 钟面（advice refused 扫描与 briefing ack 当日卡闸），隔日必红 | ✅ 已完成 | 2026-11-09 | [详情](#I-003) |
| I-003 | decisions.jsonl 增量游标 bug 生产破链：daemon 重启重灌全史、hash 链校验静默失效 | ✅ 已完成 | 2026-11-08 | [详情](#I-003) |
| I-004 | 界面梳理第五轮（issue #36）：PyYAML 前导零八进制误析 + CSS 断点级联双坑，44 项逐模块修复 | ✅ 已完成 | 2026-11-10 | [详情](#I-004) |

<a id="I-001"></a>

## I-001：建仓迁移：主 Agent 文档族自 vibe-stock 0.0.28 迁入，原型 A 股代码假码化

- **问题描述**：vibe-stock 0.0.28 引入的主 Agent 调研/方案/图/原型与产品实施同仓混放，违反机制与产品 SSOT 分离；原型 HTML 使用真实格式 A 股交易所代码而方案自称「合成代码」。
- **表因根因**：产品实施裁定（2026-11-07）前无独立产品仓，文档暂存机制仓；原型取码时未做「不在真实代码全集」断言。
- **处理方式**：建本仓（产品实施 SSOT）；四组文档按原相对布局迁入（同仓链接零断链），跨仓链接改绝对 URL；5 个真实格式码替换为显式假码（309866/609234/609789/309517/009567，经 akshare 代码全集断言不在场）并按原口径重截 4 张 PNG。
- **后续防范**：跨仓引用一律绝对 URL、禁止转述机制规约句；原型/测试数据取码先过「真实代码全集不在场」断言（红线扫描测试同纪律）。
- **同类问题影响**：vibe-stock 侧 0.0.29 迁移 PR 同步指针族（README/knowledge-map/CHANGELOG/references/07）；后续产品文档一律落本仓。

<a id="I-002"></a>

## I-002：T1 轻量日 K 链机制升级候选（vibe-stock CLI 暴露）

- **问题描述**：07 升级链预钉的 T1 轻量刷新（[vibe-stock references/07-monitor.md L89](https://github.com/ThreeFish-AI/vibe-stock/blob/main/references/07-monitor.md)：触发股重拉日 K → derive_technical 重算 → snapshot 通道拼新 evidence → `prepare --evidence` 直入出新 context-pack）停留「预留」（07 L103 事件梯 T1 行状态列）；vibe-stock 无对应 CLI 暴露，产品仓 M1（0.0.3）T1 接线只能采全量 fetch+prepare 过渡口径（每触发股一次全量取数，消耗诊股熔断额度大于轻量链）。
- **表因根因**：机制 SSOT 在 vibe-stock，其实施属其仓 0.0.26+ 升级链 PR（07 L126 分期表）；产品仓不可复制机制实现（跨仓纪律），只能登记候选待机制侧排期。
- **处理方式（草案，待实施）**：vibe-stock 侧暴露 T1 轻量链 CLI（重拉日 K → technical 重算 → snapshot 通道拼 evidence → `prepare --evidence` 直入），产品仓 `loop/t1.py` 的 `T1Runner` 换调新 CLI（换 argv 构造，状态机零改动）；实施时 vibe-stock 三处联动——07 L103 T1 行状态翻转「预留→已实施（版本号）」、07 L126 分期表同步、其仓 CHANGELOG 登记。
- **后续防范（两条坑位，实施时必带）**：①07 L90：snapshot 通道会让 prepare 跳过走势参考出卡——轻量链必须保 T1 context-pack 必含走势参考节（0.0.24 必含句）；②拉日 K 须走全局锁 `fetch_state.lock` 计账（verify-hk/technical 预热同款纪律，07 L28 熔断隔离），不烧穿诊股熔断额度。
- **同类问题影响**：D8 定谳若放开港股盘中口径，T1 轻量链的时戳新鲜度门禁同享 D8 结论（本条不单独评估）。
- **注意事项**：本条为机制升级候选登记（产品仓 issue 体系），非缺陷；vibe-stock 的 issue.md 为已处理缺陷档案，未实施候选不登其仓（实施时走其仓 PR 流程）。

<a id="I-003"></a>

## I-003：跨午夜时敏测试钉死 2026-11-08 钟面（advice refused 扫描与 briefing ack 当日卡闸），隔日必红

- **问题描述**：`test_panel_pages_e2e.py::TestAdviceRendered::test_refused_reasons_fallback` 种子决策 ts 钉死 `2026-11-08T02:50:29+00:00`，而断言查询 `market_today()`；`test_decision_chain_e2e.py::test_l2_toggle_and_briefing_ack_keep_chain_valid` 落卡与 ack 均钉死 `date="2026-11-08"`。两用例在 2026-11-09 起必然失败（`_advice_refused` 按市场钟面日过滤、ack 端点「仅当日卡可确认」422 闸）。
- **表因根因**：用例编写当日（11-08）恰与钉死值一致，动态口径未被察觉；两处被测判据本身都是「当日」语义（有意为之），错在种子用静态日期。
- **处理方式**：种子改动态——refused 用例 ts 取 `f"{market_today()}T02:50:29+00:00"`；ack 用例落卡/ack date 均取 `market_today()`（判据零改动，只收严为任意日可跑）。
- **后续防范**：新用例凡断言「当日」语义端点，种子日期一律 `market_today()` 动态取值，禁写字面日期；评审时对 `2026-` 字面日期 grep 一眼。
- **同类问题影响**：同模式扫描过其余 e2e（`_seed_events` 等已走 `market_today()`），无同类残留。
- **注意事项**：属测试时敏缺陷修复（判据只收严），随 0.0.24 UI/UX 升级 PR 合入。
## I-003：decisions.jsonl 增量游标 bug 生产破链：daemon 重启重灌全史、hash 链校验静默失效

- **问题描述**：生产 `~/.vibe-stock/agent/decisions.jsonl`（2026-11-08 实测）57 行中仅 11 个唯一 seq、46 行重复（max seq 29），`verify_chain()=False`——hash 链防篡改语义失效且静默：载入侧忽略校验返回值，面板 aumeta 照常显示链 head。由存储选型盘点（[storage-db-decision.md](../research/storage-db-decision.md)）四路取证 workflow 实测发现。
- **表因根因**：双写路径共享可变游标——`_persisted_lines` 仅 `AgentLoop.__init__` 置 0（`loop.py:95`）；`acquire_daemon` 换入盘上链不重置游标（`worker.py:169`）；`worker._decide` 直写盘不推游标（`worker.py:204-206`）；`_persist_all` 从旧游标起把全部历史行重追加（`loop.py:422`）。旁路直写点共 4 处（worker.py:204-206、routes.py:584-589、routes.py:688-690、routes.py:806-811）。放大器：`DecisionLog.load` 忽略 `verify_chain()` 返回值（`decisions.py:74` 注释称「载入即校验」实为 no-op）；e2e 测试每次全新空链（`test_loop_dryrun_e2e.py:26`）未覆盖跨重启场景。
- **处理方式（P0，待实施）**：W1 最小修复——`worker.py:169` 载入盘上链后同步置 `loop._persisted_lines = len(decisions.lines)`（彻底形态=决策落盘唯一入口化：`DecisionLog.append(path=...)` 自持落盘，收编 4 处旁路，单独成 PR）；W3 `verify_chain` fail-loud（先裁定拒启 vs 只读降级：生产文件已破链，直接拒启会让 daemon 起不来，须与 W2 同 PR 或先 W2）；W2 一次性链修复子命令 repair-decisions——按 (seq,hash) 保每个 seq 首个自洽行、重算后续 prev_hash、dry-run diff 预览后写回，修复动作写一条 decision 留痕；W4 回归测试（「_decide 与 assert_and_persist 交错执行后盘上无重复 seq 且 verify_chain()=True」）。
- **后续防范**：决策落盘唯一入口纪律——新写点必须经 DecisionLog 自持落盘，禁旁路 `atomic_append_jsonl` 直写 decisions 路径；「检查必须接住返回值」纪律（校验函数返回值被忽略=no-op 防线）；跨重启游标类内存状态必须与盘上事实同步（载入即对齐）。
- **同类问题影响**：同属「绕开单一入口」族——audit 归档裸 `open("a")` 无 flock 无 fsync（`cc_client/cli.py:301-304`，随 W7 收编 store.py）；watcher.json 无条件重写（W6 条件写）；跨文件写序窗口（W5）。修复拆序与全量路线见 [storage-db-decision.md §6](../research/storage-db-decision.md)。
- **注意事项**：已核实外仓 [agent_postmortem.py](https://github.com/ThreeFish-AI/vibe-stock/blob/main/scripts/agent_postmortem.py) 为键控 last-wins 消费、重复行 payload 逐字相同，W2 去重不改变外仓视图（无需跨仓对账，留痕即可）。
- **生产现状（2026-11-08 复核）**：盘上链已被先行修复（59 行全唯一 seq、verify_chain 通过、留有 chain_repair_started/done 决策行）——但生产 0.0.13 根因仍在，daemon 重启后任一会话任务完成即复发重灌；0.0.15 W1 唯一入口根治。
- **升级手册（合并 0.0.15 后执行一次）**：①`uv tool upgrade`（或按原安装源重装，取 main 最新）；②`launchctl kickstart -k gui/$(id -u)/com.threefish.stocktrading.agent` 重启 daemon；③（仅当链再破时）daemon 空闲期跑 `stock-trading-agent repair-decisions --apply` 再 kickstart 一次。链健康时 ② 即完成切换。
\n
<a id="I-004"></a>

## I-004：界面梳理第五轮（issue #36）：PyYAML 前导零八进制误析 + CSS 断点级联双坑，44 项逐模块修复

- **问题描述**：用户反馈七页面板依旧丑（拥挤/未对齐/遮挡/不居中）；审计 workflow（18 只读代理：8 审计 → 8 对抗核验 → 完备性批评家）确认 44 条 + 编排者实测补录 2 条功能缺陷。
- **表因根因（两条非显性坑）**：①PyYAML（YAML 1.1）把 front-matter 未加引号的 `code: 002513` 按**八进制**解析为 1355——0 开头 A 股代码（000/002/300 段）建议卡详情链接 404、复核状态错配，`parse_advice_card` 既有 int→str 转换只防了十进制；②作用于两栏基座的高特异性 CSS 覆写未裹 `min-width` 门——id 特异性 (1,1,0) 恒压过 ≤1180 塌缩媒体块 (0,1,0)（#page-monitor 中招），且源序晚于塌缩块的同特异性规则级联必胜（#page-diagnosis 中招），≤1180 恒双栏。
- **处理方式**：octal 双保险（写侧加引号 + 读侧 stem 权威对账）；级联坑以 `@media (min-width:1181px)` 包裹修复并钉入 14-design-system §⑬「断点覆写须裹 min-width 门」；44 项逐模块修复详见 CHANGELOG 0.0.32（头体 num 配对/列宽档族扩档/双钟面/空态等高/触点/排版口径八主线）。
- **后续防范**：YAML 数值型身份字段一律引号落盘，读侧以文件名/键位对账兜底；断点行为必须实测（matchMedia + computed grid-template-columns 双探针），勿信「媒体块写了就生效」；审计证据链用 headless 专属浏览器（共享 Playwright 实例的 viewport 会被并发会话反复改写，测量全废）。
- **同类问题影响**：audit 归档/ledger 等 yaml/数值身份字段面暂无同型消费点（grep 复核）；CSS 侧其余 id 特异性覆写仅剩 #page-diagnosis/#page-monitor 两处（已修），新增覆写走 §⑬ 门。
- **注意事项**：本条与 0.0.31 一屏化（PR #38）并发撞车——处置沿既定法：WIP 提交 → 从 origin/main 重建分支 → cherry-pick 语义重放（2 文件 3 处冲突手工按「限高壳 + 新列宽」合并）。
