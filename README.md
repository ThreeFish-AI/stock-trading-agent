# Stock Trading Agent — 单用户本机主 Agent

![License](https://img.shields.io/badge/License-MIT-blue) ![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)

在 [vibe-stock](https://github.com/ThreeFish-AI/vibe-stock) 确定性诊股底座之上，以本机 Claude Code（headless CLI 子进程通道）为唯一 LLM 大脑的单用户主 Agent：**诊股 · 盯盘 · 操作策略 · 模拟盘** 四能力域编排。它是编排者与会话大脑，不是新的信号源（五段流水线 LLM 唯一必经段零改动）。

![主 Agent 双层架构总览](docs/diagrams/main-agent/main-architecture/main-architecture.svg)

> **红线**：本产品输出仅供学习与研究参考，不构成投资建议；系统不下单、不改单、不撤单、不保证收益；**模拟盘永不接真实下单接口，与实盘物理隔离**（NR-2）。

## 与 vibe-stock 的关系（SSOT 边界）

| 事实源 | 仓库 | 内容 |
| :--- | :--- | :--- |
| 机制 SSOT | [ThreeFish-AI/vibe-stock](https://github.com/ThreeFish-AI/vibe-stock) | 诊股 Skill 路由壳（SKILL.md）、references/01–11 规约、策略库、scripts 六件、批量与盯盘参考实现 |
| 产品实施 SSOT | 本仓 | 调研报告、产品与技术方案、架构图、原型、CCClient / Loop / 断言器 / paper 引擎 / 面板 / 预算闸的实施规约与代码、NR-1~NR-5 产品红线 |

本产品通过安装 vibe-stock Skill（`~/.claude/skills/vibe-stock` 软链指向其 clone）以 `/vibe-stock <code>` 形态拉起诊股流水线；对 vibe-stock 机制的引用一律走其仓绝对链接，不在本仓转述其规约句（不造第二事实源）。

## 核心文档

- [docs/research/main-agent.md](docs/research/main-agent.md)——前期技术与产品调研报告（159-agent 流水线，646 findings，14 章 + 拒绝引入清单）
- [docs/research/main-agent-proposal.md](docs/research/main-agent-proposal.md)——产品与技术方案（上/下篇 19 节；M1/M2/M3 分期、红线继承 13 条 + NR-1~5、决策点 D1–D8、风险地图 R-01~R-13）
- 四张架构图（archify A 档四件套）：[总览](docs/diagrams/main-agent/main-architecture/main-architecture.svg) · [Loop 时序](docs/diagrams/main-agent/agent-loop-sequence/agent-loop-sequence.svg) · [状态数据流](docs/diagrams/main-agent/state-extension-dataflow/state-extension-dataflow.svg) · [模拟盘生命周期](docs/diagrams/main-agent/paper-trading-lifecycle/paper-trading-lifecycle.svg)
- 四页产品原型（合成数据）：[今日总览](docs/prototypes/main-agent/overview.png) · [诊股工作台](docs/prototypes/main-agent/diagnosis.png) · [盯盘与 T2 复核](docs/prototypes/main-agent/monitor-advice.png) · [模拟盘](docs/prototypes/main-agent/paper.png)

## 安装与运行（M1，0.0.3）

前置：[uv](https://docs.astral.sh/uv/)、macOS、可选本机 `claude` CLI（无则跑 dry-run 通道）。

```bash
git clone https://github.com/ThreeFish-AI/stock-trading-agent.git
cd stock-trading-agent
uv sync                       # 建 .venv 并按 uv.lock 锁定依赖
uv run pytest -q              # 测试门禁（全绿才可提交）
uv run stock-trading-agent selftest   # 仓级 selftest（与 pytest 同源判据）
uv run stock-trading-agent serve      # 本地面板（127.0.0.1:8730：Loop 运行时 + htmx 轮询，dry-run 通道）
STA_ALLOW_REAL_CC=1 uv run stock-trading-agent probe   # 真实 CC 通道探针（不带 env 即 dry-run 报告）
uv run stock-trading-agent run-once --kind diagnose_quick --code 600519  # CLI 消费一单（与 serve 抢 daemon 锁）
```

面板①今日总览/②诊股工作台（提交入队）/③盯盘事件与 T2 复核（手动入口）/④模拟盘（确认队列+权益曲线）/⑤建议卡库/⑦审计与预算已点亮（180s htmx 轮询，本地 vendor 零 CDN）；④⑤⑥随 M2 点亮（方案 §16）。T1 自动刷新（零 LLM 消费 monitor 台账 → 收盘后补跑出新 context-pack）随面板进程常驻运行。真实 CC 通道（headless CLI 子进程：超时 SIGINT→SIGKILL 状态机、stream-json 解析、audit 归档）经 `STA_ALLOW_REAL_CC=1` 放行，默认 dry-run。

### launchd 第四槽（面板守护，可选）

```bash
mkdir -p ~/.vibe-stock/agent/logs
sed -e "s|__UV_PATH__|$(command -v uv)|g" -e "s|__REPO_PATH__|$(pwd)|g" -e "s|__HOME__|$HOME|g" \
    ops/launchd/com.threefish.stocktrading.agent.plist > ~/Library/LaunchAgents/com.threefish.stocktrading.agent.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.threefish.stocktrading.agent.plist
```

与 vibe-stock 三槽并存收编不替代；逐键取值与运维见 [docs/ops/launchd.md](docs/ops/launchd.md)。

## 仓库结构

```text
├── AGENTS.md               # 协作规约：SSOT 边界、红线（NR-1~5）、测试门禁、目录速查
├── CHANGELOG.md            # 版本登记（Keep a Changelog，0.0.x patch 序列）
├── docs/
│   ├── research/           # 调研报告与产品技术方案（自 vibe-stock 0.0.28 迁入）
│   ├── diagrams/main-agent/  # 四张架构图 archify A 档四件套
│   ├── prototypes/main-agent/  # 四页产品原型（自包含 HTML + 整页 PNG，全合成数据）
│   ├── specs/              # 实施规约：12-orchestration.md（已建）；13-paper 随 M2 建
│   ├── ops/launchd/        # 第四槽 LaunchAgent 模板（面板守护）
│   └── .agents/            # knowledge-map（文档统一入口）+ issue.md
├── src/stock_trading_agent/
│   ├── cc_client/          # CCRequest/CCClient 契约、CLI 旗标构建、dry-run 通道
│   ├── loop/               # 意图路由（YAML 零 LLM）、NR-3 断言器、Loop 六段、T1 刷新链、
│   │                       # monitor 监听（watchfiles）、LoopRuntime 消费者（daemon/queue 双锁）
│   ├── state/              # ~/.vibe-stock/agent/ 状态布局、原子写、hash 链 decision log
│   ├── paper/              # 模拟盘引擎：批状态机、严格穿价撮合、费用栈、逐笔确认
│   ├── budget/             # 预算闸（默认关）
│   └── server/             # FastAPI 本地面板：只读态 + 提交半边 + monitor 投影 + htmx fragment
├── tests/                  # unit（纯函数）+ integration（tmp 状态目录端到端）
└── .github/workflows/ci.yml  # uv sync → ruff → pytest
```

## 免责

本项目输出仅供学习与研究参考，不构成投资建议；不下单、不保证收益；模拟绩效均为假设性推演（含费用与滑点），全部产物带 AI 生成标注、用户复核前置。

## License

MIT © 2026 ThreeFish-AI
