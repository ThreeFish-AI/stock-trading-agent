# Changelog

本文件只登记版本级变化；条目遵循 Keep a Changelog、[SemVer 2.0.0](https://semver.org/lang/zh-CN/)。

> 版本序列说明：项目未到稳定发布，版本号统一走 **0.0.x** patch 序列；minor（0.1.0）预留给语义级变更。

## [0.0.1] - 2026-11-07

### Added

- **建档与文档迁移（来源 [ThreeFish-AI/vibe-stock](https://github.com/ThreeFish-AI/vibe-stock) 0.0.28，迁出登记见其 [0.0.29 条目](https://github.com/ThreeFish-AI/vibe-stock/blob/main/CHANGELOG.md)）**：自 vibe-stock 迁入 [docs/research/main-agent.md](docs/research/main-agent.md)（前期技术与产品调研报告，159-agent 流水线、646 findings、14 章 + 18 项拒绝引入清单）与 [docs/research/main-agent-proposal.md](docs/research/main-agent-proposal.md)（产品与技术方案，上/下篇 19 节）——两文保持原相对布局（同仓链接零断链），头部各加迁注（原载 vibe-stock 0.0.28；文内「本仓」指迁移时点的 vibe-stock；机制引用以 vibe-stock 现行为准；裁定④修正为「产品实施成果沉淀本仓、机制成果沉淀 vibe-stock」），proposal §2/§6 与调研报告第 14 章加节首快照指针注，§12 内两条 references 相对链接改 vibe-stock 绝对 URL，§17 规约落位改判本仓 `docs/specs/`（12-orchestration / 13-paper 沿编号、随实施 PR 建；vibe-stock 不建、其 SKILL 判表不同步）。四张架构图 archify A 档四件套迁入 [docs/diagrams/main-agent/](docs/diagrams/main-agent/)（零改动）；四页产品原型迁入 [docs/prototypes/main-agent/](docs/prototypes/main-agent/)——A 股行代码由真实格式交易所代码统一替换为显式假码（309866/609234/609789/309517/009567，经 akshare A 股代码全集断言不在场；「示例·」名称与「HK 示例」标识不动），按原口径（1440px、fullPage）重截 4 张整页 PNG。
- **SSOT 边界建档**：本仓 = Stock Trading Agent 产品实施 SSOT（调研/方案/图/原型/specs/代码/NR-1~NR-5 产品红线全文）；vibe-stock = 机制 SSOT（SKILL.md 路由壳 + references/01–11 + strategies/ + scripts/ + batch/）；跨仓引用一律 GitHub 绝对 URL。见 [AGENTS.md](AGENTS.md) 与 [README.md](README.md)。
- **M1 代码骨架（可安装可运行最小骨架，非完整 M1 功能）**：uv 项目形态（pyproject + uv.lock + src 布局 + console script `stock-trading-agent`，serve/probe/selftest 三子命令）。六模块：`cc_client/`（CCRequest/CCResult/ProbeReport 契约与 CCClient Protocol、UUIDv5 会话派生、build_argv/sanitize_env 纯函数——真实 spawn 默认拒、`STA_ALLOW_REAL_CC=1` 才放行、DryRunCCClient 合成通道）、`loop/`（事件信封+dedup_key+禁跑窗、YAML 确定性路由零 LLM、NR-3 断言器判据族、六段编排骨架 dry-run 端到端可跑）、`state/`（StateLayout 状态布局与写者归属、原子写+flock+schema 版本闸、queue WAL 状态机+幂等键、sessions 登记、decisions sha256 hash 链）、`paper/`（07 P3 schema pydantic 镜像、批状态机与仓位推导、严格穿价撮合 low<limit 与 min(open,limit)、逐笔确认流、A/HK 静态费用栈与滑点表全标 ESTIMATED、确认成交落账唯一入口）、`budget/`（预算闸默认关、订阅/计费双形态）、`server/`（FastAPI 面板骨架：127.0.0.1+TrustedHost、只读态路由、七页禁用态导航+NR-2 横幅静态页、POST 占位 501）。测试 14 文件（unit 纯函数 + integration tmp 状态目录端到端 + 红线源码扫描机器门），全绿 <60s；CI 首版即建（uv sync → ruff → pytest，无 secrets 无本机依赖）。口径纪律：paper 模块注释只放 vibe-stock references/06/07 指针、不复制口径文字。
