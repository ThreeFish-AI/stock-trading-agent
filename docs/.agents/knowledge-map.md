# Knowledge Map（知识索引）

> stock-trading-agent 文档统一入口；文档目录变更时同步更新本文件。目录职责速查见 [AGENTS.md](../../AGENTS.md)。机制文档（诊股 Skill 规约等）事实源在 [vibe-stock](https://github.com/ThreeFish-AI/vibe-stock)，本表只留指针不转述。

| 文档 | 主旨 | 受众 |
| :--- | :--- | :--- |
| [README.md](../../README.md) | 产品首页：定位、SSOT 边界、文档索引、安装运行 | 访客 |
| [AGENTS.md](../../AGENTS.md) | 协作规约、红线（NR-1~5）、测试门禁、目录速查 | 贡献者 / Agent |
| [CHANGELOG.md](../../CHANGELOG.md) | 版本登记（Keep a Changelog，0.0.x patch 序列） | 贡献者 |
| [docs/research/main-agent.md](../research/main-agent.md) | 前期技术与产品调研报告：159-agent 流水线、646 findings、14 章 + 拒绝引入清单 | 贡献者 / Agent |
| [docs/research/main-agent-proposal.md](../research/main-agent-proposal.md) | 产品与技术方案：上/下篇 19 节、M1/M2/M3、红线继承 13+NR-5、D1–D8、R-01~R-13 | 贡献者 / Agent |
| [docs/research/storage-db-decision.md](../research/storage-db-decision.md) | 存储选型决策记录：不引入 SQLite/PostgreSQL/seekdb（重申 §13）、触发判据 T1-T5 与 queryindex 只读索引投影蓝图、四痛点修法映射 | 贡献者 / Agent |
| [docs/diagrams/main-agent/](../diagrams/main-agent/) | 四张架构图 archify A 档四件套（总览/Loop 时序/状态数据流/模拟盘生命周期） | 贡献者 / Agent |
| [docs/prototypes/main-agent/](../prototypes/main-agent/) | 四页产品原型（自包含 HTML + 整页 PNG，全合成数据，方案上篇 §2 借图展开） | 贡献者 |
| [docs/specs/](../specs/) | 实施规约：[12-orchestration.md](../specs/12-orchestration.md)（编排，随实施 PR 增节，现 §①–⑮）、[13-paper.md](../specs/13-paper.md)（paper，0.0.6 建）、[14-design-system.md](../specs/14-design-system.md)（设计系统，0.0.19 建） | 贡献者 / Agent |
| [docs/ops/launchd.md](../ops/launchd.md) | launchd 第四槽安装与运维（面板守护；三槽并存收编不替代） | 贡献者 |
| [docs/ops/trading-day-verify.md](../ops/trading-day-verify.md) | 交易日验证运维：D8 定谳流程与 T2 出卡真跑手册 | 贡献者 / Agent |
| [docs/ops/tauri-gate-review.md](../ops/tauri-gate-review.md) | Tauri 桌面壳门口评审记录（结论：不做） | 贡献者 |
| [docs/ops/bg-session-eval.md](../ops/bg-session-eval.md) | --bg 会话族评估（结论：不引入） | 贡献者 |
| [docs/ops/d6-sdk-comparison.md](../ops/d6-sdk-comparison.md) | Agent SDK 对照实验记录（脚本待触发） | 贡献者 |
| [vibe-stock（外仓）](https://github.com/ThreeFish-AI/vibe-stock) | 机制 SSOT：诊股 Skill（SKILL.md + references/01–11 + strategies/ + scripts/ + batch/），本产品经 `~/.claude/skills/vibe-stock` 软链使用 | 贡献者 / Agent |
| [issue.md](issue.md) | Issue 处理档案（头部索引 + 详情锚点） | 贡献者 / Agent |
