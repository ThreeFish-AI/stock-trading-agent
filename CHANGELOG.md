# Changelog

本文件只登记版本级变化；条目遵循 Keep a Changelog、[SemVer 2.0.0](https://semver.org/lang/zh-CN/)。

> 版本序列说明：项目未到稳定发布，版本号统一走 **0.0.x** patch 序列；minor（0.1.0）预留给语义级变更。

## [0.0.2] - 2026-11-08

### Fixed

- **三 lens 对抗评审修复（20 条确认 findings：4 P1 + 16 P2；评审 = 方案符合性 / 红线与安全 / 测试与迁移完整性，25-agent 流水线，2 条误报被对抗复核推翻）**。P1 四条：①禁跑窗双失效——窗口改按市场本地钟面（Asia/Shanghai）换算（旧实现 UTC 钟面错位 8 小时、真实窗内保护为零），禁跑 kind 显式枚举 diagnose_full/diagnose_quick/batch_wave/briefing_review（旧 report* 前缀过滤在路由表六 kind 上全部落空）；②Position 补 07 P3 schema 钉死的 trailing_armed 字段（旧镜像 load→persist 往返静默丢该字段），docstring 改「SSOT 字段逐一对应 + 本仓扩展字段显式列举」；③NR-3 两要素落地——run_session 按 routing.yaml schema_ref 注入 draft-07 schema（意见类 kind 缺 schema 即拒，喂 CC 与本地复验同份）、断言器新增 jsonschema Draft7Validator 本地复验（旧全仓零 import）；④e2e 幂等/锁用例恒真断言改 pytest.raises(LoopReject, match=…)。P2 十六条含：免责段检查扩全部落盘 kind（§7 免责四件套）、预算外闸接入 AgentLoop（spawn 前 check + 成功后双口径记账写 budget.json + 订阅形态哨兵值）、load_table 用户覆盖只许收严（预算/轮次/工具扩集即拒）、卖出落账不再占用建仓批（tranche_ref=None、清零即 closed）、A 股佣金费率位（max(2.5bp×名义, 5 元)）、港股价位表对齐 E-55 现行口径（0.25–10 档 0.005、高价档续表）、敏感字段词族子串匹配（Quantity/avgCost/pnl 等变体可拦截）、红线扫描机器门加固（tokenize 剥注释、AST 剥全部 docstring、白名单改完整相对路径、urllib.from-import 与 importlib/__import__ 动态导入面可见、paper/ 禁动态 import 机制）、NR-3 归档次序（转录归档移至断言器放行后，违例不归档）、/api/decisions tail 夹紧 ge=1 le=200、tick 旋钮浮点容差与镜像/止损等值边界用例、卖出分支三用例（全额/部分/超持拒）、sessions 落盘回放断言、store 失败清理 tmp 与 append 直接用例、proposal §9 两处超出登记类的排版漂移复原（回到与 vibe-stock 0.0.28 原文逐字节一致，仅保留五类登记编辑）与状态行改「已获批实施」、docs/specs/README 补 0.0.1 骨架先行的预规约定性。版本 0.0.1→0.0.2（patch）。

## [0.0.1] - 2026-11-07

### Added

- **建档与文档迁移（来源 [ThreeFish-AI/vibe-stock](https://github.com/ThreeFish-AI/vibe-stock) 0.0.28，迁出登记见其 [0.0.29 条目](https://github.com/ThreeFish-AI/vibe-stock/blob/main/CHANGELOG.md)）**：自 vibe-stock 迁入 [docs/research/main-agent.md](docs/research/main-agent.md)（前期技术与产品调研报告，159-agent 流水线、646 findings、14 章 + 18 项拒绝引入清单）与 [docs/research/main-agent-proposal.md](docs/research/main-agent-proposal.md)（产品与技术方案，上/下篇 19 节）——两文保持原相对布局（同仓链接零断链），头部各加迁注（原载 vibe-stock 0.0.28；文内「本仓」指迁移时点的 vibe-stock；机制引用以 vibe-stock 现行为准；裁定④修正为「产品实施成果沉淀本仓、机制成果沉淀 vibe-stock」），proposal §2/§6 与调研报告第 14 章加节首快照指针注，§12 内两条 references 相对链接改 vibe-stock 绝对 URL，§17 规约落位改判本仓 `docs/specs/`（12-orchestration / 13-paper 沿编号、随实施 PR 建；vibe-stock 不建、其 SKILL 判表不同步）。四张架构图 archify A 档四件套迁入 [docs/diagrams/main-agent/](docs/diagrams/main-agent/)（零改动）；四页产品原型迁入 [docs/prototypes/main-agent/](docs/prototypes/main-agent/)——A 股行代码由真实格式交易所代码统一替换为显式假码（309866/609234/609789/309517/009567，经 akshare A 股代码全集断言不在场；「示例·」名称与「HK 示例」标识不动），按原口径（1440px、fullPage）重截 4 张整页 PNG。
- **SSOT 边界建档**：本仓 = Stock Trading Agent 产品实施 SSOT（调研/方案/图/原型/specs/代码/NR-1~NR-5 产品红线全文）；vibe-stock = 机制 SSOT（SKILL.md 路由壳 + references/01–11 + strategies/ + scripts/ + batch/）；跨仓引用一律 GitHub 绝对 URL。见 [AGENTS.md](AGENTS.md) 与 [README.md](README.md)。
- **M1 代码骨架（可安装可运行最小骨架，非完整 M1 功能）**：uv 项目形态（pyproject + uv.lock + src 布局 + console script `stock-trading-agent`，serve/probe/selftest 三子命令）。六模块：`cc_client/`（CCRequest/CCResult/ProbeReport 契约与 CCClient Protocol、UUIDv5 会话派生、build_argv/sanitize_env 纯函数——真实 spawn 默认拒、`STA_ALLOW_REAL_CC=1` 才放行、DryRunCCClient 合成通道）、`loop/`（事件信封+dedup_key+禁跑窗、YAML 确定性路由零 LLM、NR-3 断言器判据族、六段编排骨架 dry-run 端到端可跑）、`state/`（StateLayout 状态布局与写者归属、原子写+flock+schema 版本闸、queue WAL 状态机+幂等键、sessions 登记、decisions sha256 hash 链）、`paper/`（07 P3 schema pydantic 镜像、批状态机与仓位推导、严格穿价撮合 low<limit 与 min(open,limit)、逐笔确认流、A/HK 静态费用栈与滑点表全标 ESTIMATED、确认成交落账唯一入口）、`budget/`（预算闸默认关、订阅/计费双形态）、`server/`（FastAPI 面板骨架：127.0.0.1+TrustedHost、只读态路由、七页禁用态导航+NR-2 横幅静态页、POST 占位 501）。测试 14 文件（unit 纯函数 + integration tmp 状态目录端到端 + 红线源码扫描机器门），全绿 <60s；CI 首版即建（uv sync → ruff → pytest，无 secrets 无本机依赖）。口径纪律：paper 模块注释只放 vibe-stock references/06/07 指针、不复制口径文字。
