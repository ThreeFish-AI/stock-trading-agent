# Issue 记录

> 处理过的 Issue 摘要：头部索引表 + 主体详情锚点；ID 形如 `I-001` 全文唯一递增。

| ID | 标题 | 状态 | 完成时间 | 详情 |
| :--- | :--- | :--- | :--- | :--- |
| I-001 | 建仓迁移：主 Agent 文档族自 vibe-stock 0.0.28 迁入，原型 A 股代码假码化 | ✅ 已完成 | 2026-11-07 | [详情](#I-001) |
| I-002 | T1 轻量日 K 链机制升级候选（vibe-stock CLI 暴露），现采全量 fetch+prepare 过渡口径 | ⏸ 暂不处理 | — | [详情](#I-002) |
| I-003 | 跨午夜时敏测试钉死 2026-11-08 钟面（advice refused 扫描与 briefing ack 当日卡闸），隔日必红 | ✅ 已完成 | 2026-11-09 | [详情](#I-003) |

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
