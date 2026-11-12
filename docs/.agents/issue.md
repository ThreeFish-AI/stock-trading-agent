# Issue 记录

> 处理过的 Issue 摘要：头部索引表 + 主体详情锚点；ID 形如 `I-001` 全文唯一递增。

| ID | 标题 | 状态 | 完成时间 | 详情 |
| :--- | :--- | :--- | :--- | :--- |
| I-001 | 建仓迁移：主 Agent 文档族自 vibe-stock 0.0.28 迁入，原型 A 股代码假码化 | ✅ 已完成 | 2026-11-07 | [详情](#I-001) |
| I-002 | T1 轻量日 K 链机制升级候选（vibe-stock CLI 暴露），现采全量 fetch+prepare 过渡口径 | ⏸ 暂不处理 | — | [详情](#I-002) |
| I-003 | 跨午夜时敏测试钉死 2026-11-08 钟面（advice refused 扫描与 briefing ack 当日卡闸），隔日必红 | ✅ 已完成 | 2026-11-09 | [详情](#I-003) |
| I-004 | decisions.jsonl 增量游标 bug 生产破链：daemon 重启重灌全史、hash 链校验静默失效 | ✅ 已完成 | 2026-11-08 | [详情](#I-004) |
| I-005 | 特性覆盖审计（#37）：paper 驱动面缺口补齐（EOD 驱动/allocation 导入/撮合价透传）+ 15 项文档对齐 + postmortem 槽补装 | ✅ 已完成 | 2026-11-10 | [详情](#I-005) |
| I-006 | selftest 安装态 4 例误红（ops/ 类 case 缺仓 FAIL）→ SelftestSkip 守卫 | ✅ 已完成 | 2026-11-10 | [详情](#I-006) |
| I-007 | 界面梳理第五轮（issue #36）：PyYAML 前导零八进制误析 + CSS 断点级联双坑，44 项逐模块修复 | ✅ 已完成 | 2026-11-10 | [详情](#I-007) |
| I-008 | ①⑦ 页 grid-main.screen 级联：两栏纵向堆叠、⑦ 台账 0px 不可见（0.0.31 引入）→ .grid-main.screen{display:grid} 一行收口 | ✅ 已完成 | 2026-11-10 | [详情](#I-008) |
| I-009 | PR #39 review 遗留清理：overview 双发射收口/坏 front-matter 免疫/research 术语档案收尾 | ✅ 已完成 | 2026-11-10 | [详情](#I-009) |
| I-010 | 界面梳理第六轮（issue #36）：视口锁 calc(100vh-K) 定高双病灶（模块压没+页脚死带）→ 弹性充满机制，七页文案瘦身 | ✅ 已完成 | 2026-11-10 | [详情](#I-010) |
| I-011 | 禁跑窗时钟 flake：CI 恰落在 17:30–18:10 Asia/Shanghai（UTC 09:30–10:10）窗内时 loop 全家 24 例必红（非分支引入） | ✅ 已完成 | 2026-11-12 | [详情](#I-011) |

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
<a id="I-004"></a>

## I-004：decisions.jsonl 增量游标 bug 生产破链：daemon 重启重灌全史、hash 链校验静默失效（原条目误用重复 ID I-003，0.0.32 勘误重编号）

- **问题描述**：生产 `~/.vibe-stock/agent/decisions.jsonl`（2026-11-08 实测）57 行中仅 11 个唯一 seq、46 行重复（max seq 29），`verify_chain()=False`——hash 链防篡改语义失效且静默：载入侧忽略校验返回值，面板 aumeta 照常显示链 head。由存储选型盘点（[storage-db-decision.md](../research/storage-db-decision.md)）四路取证 workflow 实测发现。
- **表因根因**：双写路径共享可变游标——`_persisted_lines` 仅 `AgentLoop.__init__` 置 0（`loop.py:95`）；`acquire_daemon` 换入盘上链不重置游标（`worker.py:169`）；`worker._decide` 直写盘不推游标（`worker.py:204-206`）；`_persist_all` 从旧游标起把全部历史行重追加（`loop.py:422`）。旁路直写点共 4 处（worker.py:204-206、routes.py:584-589、routes.py:688-690、routes.py:806-811）。放大器：`DecisionLog.load` 忽略 `verify_chain()` 返回值（`decisions.py:74` 注释称「载入即校验」实为 no-op）；e2e 测试每次全新空链（`test_loop_dryrun_e2e.py:26`）未覆盖跨重启场景。
- **处理方式（P0，待实施）**：W1 最小修复——`worker.py:169` 载入盘上链后同步置 `loop._persisted_lines = len(decisions.lines)`（彻底形态=决策落盘唯一入口化：`DecisionLog.append(path=...)` 自持落盘，收编 4 处旁路，单独成 PR）；W3 `verify_chain` fail-loud（先裁定拒启 vs 只读降级：生产文件已破链，直接拒启会让 daemon 起不来，须与 W2 同 PR 或先 W2）；W2 一次性链修复子命令 repair-decisions——按 (seq,hash) 保每个 seq 首个自洽行、重算后续 prev_hash、dry-run diff 预览后写回，修复动作写一条 decision 留痕；W4 回归测试（「_decide 与 assert_and_persist 交错执行后盘上无重复 seq 且 verify_chain()=True」）。
- **后续防范**：决策落盘唯一入口纪律——新写点必须经 DecisionLog 自持落盘，禁旁路 `atomic_append_jsonl` 直写 decisions 路径；「检查必须接住返回值」纪律（校验函数返回值被忽略=no-op 防线）；跨重启游标类内存状态必须与盘上事实同步（载入即对齐）。
- **同类问题影响**：同属「绕开单一入口」族——audit 归档裸 `open("a")` 无 flock 无 fsync（`cc_client/cli.py:301-304`，随 W7 收编 store.py）；watcher.json 无条件重写（W6 条件写）；跨文件写序窗口（W5）。修复拆序与全量路线见 [storage-db-decision.md §6](../research/storage-db-decision.md)。
- **注意事项**：已核实外仓 [agent_postmortem.py](https://github.com/ThreeFish-AI/vibe-stock/blob/main/scripts/agent_postmortem.py) 为键控 last-wins 消费、重复行 payload 逐字相同，W2 去重不改变外仓视图（无需跨仓对账，留痕即可）。
- **生产现状（2026-11-08 复核）**：盘上链已被先行修复（59 行全唯一 seq、verify_chain 通过、留有 chain_repair_started/done 决策行）——但生产 0.0.13 根因仍在，daemon 重启后任一会话任务完成即复发重灌；0.0.15 W1 唯一入口根治。
- **升级手册（合并 0.0.15 后执行一次）**：①`uv tool upgrade`（或按原安装源重装，取 main 最新）；②`launchctl kickstart -k gui/$(id -u)/com.threefish.stocktrading.agent` 重启 daemon；③（仅当链再破时）daemon 空闲期跑 `stock-trading-agent repair-decisions --apply` 再 kickstart 一次。链健康时 ② 即完成切换。

<a id="I-005"></a>

## I-005：特性覆盖审计（issue #37）——paper 驱动面系统性缺口补齐 + 15 项文档对齐

- **问题描述**：对照《产品方案+技术方案》（main-agent-proposal.md）逐项特性实机验证（47 代理审计 workflow：16 特性簇 × 实机核对 + 缺口双镜头对抗核验 + 完备性批评），确认 19 条缺口。功能级唯一系统性缺口=paper 引擎驱动面：撮合/费用/批状态机纯函数层全齐（87 passed）但生产零接线——allocation 导入入口缺失（三批建仓对用户不可达）、日终 evaluate_day 无驱动（T2/T3 触价、止盈止损、GTC 到期全不跑）、mark_equity 无接线（权益曲线/绩效恒空）、撮合价 fill.price 未透传落账（违反 §12 成交价口径）；另 T3 触发批被引擎按通用限价回落匹配（06 复合口径「站上目标1/回踩 MA20±1%/20 日补足」未实现）。
- **表因根因**：0.0.6 paper v1 与 0.0.20 M2 全量均以纯函数+面板确认流交付，驱动面（数据源组装+调度+入口）属最后一块拼图，无对应 issue 挂载故悬空；文档侧为索引类滞后（README 结构图把 ops/ 误画 docs/ 下、AGENTS 速查缺 14-design-system、specs/README 缺 §⑯、12-orchestration §③①/§⑦/§⑫ 各一句陈旧、14-design-system Primitive 计数与页高数字失真、knowledge-map D6 行陈旧、issue.md I-003 重复编号）。
- **处理方式（0.0.32）**：①**paper 驱动面接线**——`loop/paper_eod.py`（ticks 末轮 bar 源 ESTIMATED + 16:05 窗口 + 决策幂等锚 + sim.lock 纪律）双入口（worker 60s 自动 + CLI paper-eod）；`paper/allocation.py` 导入（06 产物零格式发明、dup 整批拒）+ CLI paper-import + ④页表单；`engine.judge_t1_open`（跳空 ×1.02 转限价 5 日 GTC）与 `engine.judge_t3`（复合三臂判定）落地、evaluate_day 通用匹配收窄为仅 T2；`PendingFillRequest.fill_price` 撮合价透传 `ledger.apply_fill` 四面落账；T2 批 10 日 GTC 导入即设。②**装机**——postmortem 18:05 槽本机补装（vibe-stock 有模板未 bootstrap，M2「agent: 记分」生产未运转；本地安装件串联 agent_postmortem.py，vibe-stock 侧串联模板未物化为跨仓缺口登记于此）。③**文档对齐 15 项**（含 §③① 触发源 taxonomy/defer 语义、§⑦ 占位符与锁冲突表述、§⑫ 三源→四源勘误与两条呈现口径裁定、README 树重排+sed 修正+archive-state 运行说明、14-design Primitive 55 枚/页高 1219px、specs/README §⑯、knowledge-map D6 行、launchd.md 两处、issue.md I-003→I-004）。④**登记**——§⑮ 新行 --no-session-persistence（待裁定，触发=敏感 run 实锤）；①页事件流 T 级分色 lv 映射落地；trading-day-verify 补 T1 真跑待办项。
- **后续防范**：「纯函数层交付 ≠ 链路交付」——含调度/入口的完整链路须同一 issue 挂载到底（本条即 issue #37 逐项对账的意义）；索引类文档（目录速查/结构树/节枚举）随建档 PR 同步增改，防计数与枚举漂移。
- **同类问题影响**：vibe-stock 侧 agent_postmortem.py 的 plist 串联 README 未物化（其 docstring 承诺「见 batch/postmortem/ README」），属跨仓缺口——待其仓补模板后本机安装件可换回官方形态。
- **注意事项**：bar 源（ticks 末轮 vs 收盘竞价）、GTC/T3 窗日历日换算均为 ESTIMATED（13-paper §⑦/§⑧），自家成交样本校准后固化，判据只收严。

<a id="I-006"></a>

## I-006：selftest 安装态 4 例误红——ops/ 类 case 在 uv tool 环境缺仓即 FAIL

- **问题描述**：生产升级 0.0.32 后实机跑 `~/.local/bin/stock-trading-agent selftest` 报 4 项失败（ops.launchd.plist / ops.d8_probe.parsers / ops.state_probe.report / ops.upgrade-gate.baseline），均为仓内 `ops/` 件路径解析到 uv tool venv（`Path(__file__).parents[2]` 在安装态指向 `lib/python3.14/`，无 ops/）。
- **表因根因**：selftest 的 ops 类 case 以仓库检出态为隐含前提（`_repo_root()` 假设 src 布局仓根），安装态无此结构；0.0.26 起 baseline case 入册即带此盲点，既往自检均在仓内 `uv run` 跑故未暴露。
- **处理方式（0.0.33）**：`SelftestSkip` 语义——`_require_ops()` 守卫四例，安装态 SKIP（`ok=None`、exit 0、打印器带计数注记「仓库检出态全量跑」）；仓库态（CI 与本地 `uv run`）零变化（判据只收严不适用——降级只影响安装态呈现，不放宽任何仓态判据）。
- **后续防范**：依赖仓内文件（ops/、docs/）的 selftest/测试 case 必须带环境守卫并区分 SKIP/FAIL；安装态自检纳入生产升级 runbook 步骤（upgrade-runbook 已含 selftest 自检步，此后安装态跑法为可正常收敛态）。
- **同类问题影响**：无——其余 48 例为纯函数（包内自足），安装态与仓态同判。

<a id="I-007"></a>

## I-007：界面梳理第五轮（issue #36）：PyYAML 前导零八进制误析 + CSS 断点级联双坑，44 项逐模块修复

- **问题描述**：用户反馈七页面板依旧丑（拥挤/未对齐/遮挡/不居中）；审计 workflow（18 只读代理：8 审计 → 8 对抗核验 → 完备性批评家）确认 44 条 + 编排者实测补录 2 条功能缺陷。
- **表因根因（两条非显性坑）**：①PyYAML（YAML 1.1）把 front-matter 未加引号的 `code: 002513` 按**八进制**解析为 1355——0 开头 A 股代码（000/002/300 段）建议卡详情链接 404、复核状态错配，`parse_advice_card` 既有 int→str 转换只防了十进制；②作用于两栏基座的高特异性 CSS 覆写未裹 `min-width` 门——id 特异性 (1,1,0) 恒压过 ≤1180 塌缩媒体块 (0,1,0)（#page-monitor 中招），且源序晚于塌缩块的同特异性规则级联必胜（#page-diagnosis 中招），≤1180 恒双栏。
- **处理方式**：octal 双保险（写侧加引号 + 读侧 stem 权威对账）；级联坑以 `@media (min-width:1181px)` 包裹修复并钉入 14-design-system §⑬「断点覆写须裹 min-width 门」；44 项逐模块修复详见 CHANGELOG 0.0.34（头体 num 配对/列宽档族扩档/双钟面/空态等高/触点/排版口径八主线）。
- **后续防范**：YAML 数值型身份字段一律引号落盘，读侧以文件名/键位对账兜底；断点行为必须实测（matchMedia + computed grid-template-columns 双探针），勿信「媒体块写了就生效」；审计证据链用 headless 专属浏览器（共享 Playwright 实例的 viewport 会被并发会话反复改写，测量全废）。
- **同类问题影响**：audit 归档/ledger 等 yaml/数值身份字段面暂无同型消费点（grep 复核）；CSS 侧其余 id 特异性覆写仅剩 #page-diagnosis/#page-monitor 两处（已修），新增覆写走 §⑬ 门。
- **注意事项**：本条与 0.0.31 一屏化（PR #38）并发撞车——处置沿既定法：WIP 提交 → 从 origin/main 重建分支 → cherry-pick 语义重放（2 文件 3 处冲突手工按「限高壳 + 新列宽」合并）。

<a id="I-008"></a>

## I-008：①⑦ 页 grid-main.screen 级联冲突——两栏纵向堆叠、⑦ 台账区 0px 不可见（0.0.31 引入）

- **问题描述**：①⑦ 页两 `.col` 自 0.0.31 起纵向堆叠（非左右两栏），⑦ 页 `#audit`（decisions 台账 grow 区）塌 0px 完全不可见，≤1180/880 塌缩块亦全数失效；生产 0.0.33 与工作树双实测复现（PR #39 review 轮浏览器探针发现）。
- **表因根因**：`grid-main` 与 `screen` 叠挂同元素时两基座类各设 `display`（grid vs flex）且同特异性 (0,1,0)，`.screen`（行 186）源序晚于 `.grid-main`（行 103）级联必胜——flex 竖排下 grid-template-columns/塌缩媒体块全部空转；`#audit` 的 `.grow{flex:1 1 0;min-height:0}` 在内容高 flex 列中基准 0 塌缩。与 I-007 级联双坑同族（源序压胜支），但受害面是整页布局。
- **处理方式（0.0.34）**：`.grid-main.screen{display:grid}`（(0,2,0) 特异性压胜，一行收口）；1440 两栏恢复（⑦ 台账区实测 >600px 可见）、1180/880 单栏塌缩恢复；规约 14-design-system §⑬ 新钉「组合基座 display 归属显式化」无条件规则（替代形态＝外层 .screen 包内层 .grid-main，③④页先例）。
- **后续防范**：同一元素叠加两个基座类时 grep 两类是否都设 display，必须显式组合选择器声明归属；布局类验收必含 computed display + grid-template-columns 双探针，勿只看媒体块文本。
- **同类问题影响**：全仓 grep 组合基座仅此一处（③④页为包裹形态无冲突）；⑤ `#dg-form` 等注入槽无基座叠加。
- **注意事项**：0.0.31–0.0.33 生产面板 ①⑦ 一直处于堆叠态（用户感知为「页面依旧丑」的部分来源）；修复后 ①⑦ K 值（318/236）与两栏布局经三档实测无回归。

<a id="I-009"></a>

## I-009：PR #39 review 遗留清理——overview 双发射/坏 front-matter 免疫/research 术语

- **问题描述**：PR #39 八轮 review 留痕四项范围外存量：①页 overview 同响应双发射 ov-ev-count（外层+feed 片内各一，次序耦合）；`parse_advice_card` 对坏 front-matter 抛 YAMLError 致列表 500；`docs/research/` 四处「等效美元」与 0.0.34 双称统一漂移；（ov-ev-count 双发射与 docstring 计数随本条一并收口）。
- **表因根因**：overview 轮询目标即 `/api/panel/overview`，`render_event_feed` 亦仅被其消费——外层发射自始冗余；safe_load 无异常守卫；research 档案在 0.0.34 术语统一时未纳入清扫面。
- **处理方式（0.0.35）**：删外层发射留片内单源（docstring 同步口径）；safe_load 包 try/except 视同无元数据（单测钉死）；research 四处术语同步。
- **后续防范**：新增 oob 发射点前先查同 id 既有发射面（htmx oob 无去重语义）；外部输入（落盘 md）反解一律带异常守卫降级。
- **同类问题影响**：`parse_advice_card` 两生产调用点（列表/单卡）同享守卫收益；无其他同型。
- **注意事项**：本条属 review 遗留收尾，无行为收紧面；CI 于禁跑窗（17:30–18:10 市场钟面）外跑。

<a id="I-010"></a>

## I-010：界面梳理第六轮（issue #36）——视口锁 calc(100vh-K) 定高双病灶，弹性充满机制 + ①页三区重构 + 七页文案瘦身

- **问题描述**：用户截图实证 ①今日总览模块被压到极小区块（事件流/信号卡/日历不可读）、页面下方空白未用、页脚不居中且文案冗长；实机探针补录更重：①今日事件流 37px/今日待办 9px、②会话观测 2px 完全不可见，页脚上方恒留 30–98px 死带。
- **表因根因**：0.0.31 视口锁 `height:calc(100vh-K)` 定高的两个失败模式同源——锁高与真实可用高恒不相等：K 偏大（①318 vs 实际头部 ~132）时 `.screen` 不吃 `main(flex:1)` 拉伸余量→页脚上死带；锁高小于内容需求（①主栏 4 卡 2grow+2固定、②任务表裸表 20 行 ~800px）时 grow 卡（flex 基数 0）被挤至下限以下直至不可见。另 ② `subpanel>grid-2` 不吃余量、③⑤② 三处定帽与充满冲突、`.grid-main .col>.card:last-child` 末卡拉伸把 grow 卡 basis 打回 auto（同列非对称分配）。
- **处理方式（0.0.36）**：机制换血——`.page.on`/`.screen`/`#stats` 弹性充满链（flex:1 1 auto）替代五枚 K 魔数，grow 卡 96px 下限 + ①边栏泄压阀；①页三区重构（主栏双 grow 均分 + 边栏 净值/系统/待办 + 底部信息带日历|晨报，对齐原型）；②任务表入 tbl-wrap sm + subpanel grid-2 充满；三定帽退役；末卡拉伸 `:not(.grow)`；系统状态卡紧凑化；页脚单行居中（免责钉面词保留）；七页文案瘦身 ~30 处（3 审计代理 75 提案，机制名与测试钉面词零触碰）；test_design_tokens 钉面同步（K 钉改逐页精确负钉）；规约 §⑫ 改写。**r2（review 第二轮）**：≤1180 窄档整链退位修复五页塌缩（P0）；④两表单 details 折叠化 + `#paper` 96px 下限（P1，有持仓态生产同病）；⑦末卡降 flex:0 1 auto 平衡主栏（P2）。
- **后续防范**：一屏类布局禁用「定高 calc 近似可用高」——一律 flex 链充满（差距恒存在，只是被探针阈值掩盖）；一屏探针除溢出外须测页脚贴底与最小卡高（本Probe 三项判据）；改 CSS 后探针必带 `?cb=N` 新帧 + 整页重载（hash 切页不重载文档）。
- **同类问题影响**：K 值机制族（五页锁、#dg-report-body calc 帽、#mo-notif/#advice-detail-2 定帽）全数退役；`height:calc(100vh` 形态零残留（逐页精确负钉 + 族级负钉双门防回流，宽泛负钉刻意不设以免误伤未来合法 vh 用法）。
- **注意事项**：判据只收严——钉面替换经 CHANGELOG 0.0.36 登记；②④ 页签与 ⑥ 在超短窗口（<600px 高）由 96px 下限触发页面轻微滚动，属设计内行为（一屏判据仅面向 ≥1280 桌面档）。


<a id="I-011"></a>

## I-011：禁跑窗时钟 flake：CI 恰落在 17:30–18:10 Asia/Shanghai 窗内时 loop 全家 24 例必红（非分支引入）

- **问题描述**：PR #45（纯面板 UI 改动）CI test job 24 例红，全部死于同一 `LoopReject: 17:30–18:10 禁跑窗内拒跑 report`；check 运行于 09:57:34 UTC = 北京 17:57:34，恰在禁跑窗内。
- **表因根因**：`AgentLoop.ingest`（loop.py）取真实时钟 `datetime.now(tz=UTC)` 经 `gate_report_event` 拒跑 report 类 kind；受影响测试（panel_submit/loop_dryrun/worker/sse 等族）未冻结时间，任何恰落在窗内的运行（本地或 CI）必红。属 main 既有 flake：gate 自 0.0.4（PR #4）在场、失败测试文件与 loop/ 在分支与 main 字节级一致；确定性复现：origin/main 干净 worktree 强制 `in_no_report_window=True` 跑同集合，失败与 CI 一致。
- **处理方式**：定谳非本分支造成 → 仅重跑 CI（出窗后 `test: success`），未动代码；flake 根治（受影响测试时间冻结/时钟注入）留待后续单独 PR，未混入 UI 分支。
- **后续防范**：CI 在 UTC 09:30–10:10 失败先查钟面再查 diff；新增走 `ingest` 的测试一律冻结/注入时钟，勿依赖运行时刻在窗外。
- **同类问题影响**：窗口占全天约 2.8% 运行时段，随机复发；修法建议 conftest 级 autouse 时钟 fixture 或 gate 函数参数化注入，二选一在根治 PR 定夺。
- **注意事项**：重跑即愈是时钟 flake 的特征签名（同 commit 出窗绿）；勿把此类失败当回归回滚代码。
