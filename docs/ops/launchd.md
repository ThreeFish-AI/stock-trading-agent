# launchd 第四槽 · 面板进程守护（安装与运维）

第四个 LaunchAgent 守护面板进程：`KeepAlive=true` 常驻自愈（任何退出后按 `ThrottleInterval=30` 节流重启；彻底停机走 `bootout`）。坑位（本机实测）：`KeepAlive={Crashed}` 的 after-crash 信号量在 macOS 上挂起不触发——kill -9 后 90s 无重启；常驻形态下 daemon 锁冲突时 serve **进程内降级只读**（`loop.active=false` 可见、提交仍可入队），锁释放后须 `launchctl kickstart -k` 重启回归——硬 exit 3 只属 CLI run-once/archive-state（0.0.32 勘误：原句「自动回归」与实现不符）。与 vibe-stock 三槽（monitor 09:15 / outlook 17:35 / postmortem 18:05）**并存收编不替代**——三份 `com.threefish.vibestock.*` plist 零接触，本槽只守护 `stock-trading-agent serve`，不引第二套调度器（I-36/I-11）。逐键取值论证见 [12-orchestration.md §②](../specs/12-orchestration.md)。

## 安装

```bash
# 仓根执行；先 uv sync 建 .venv（模板直接调 .venv/bin/stock-trading-agent，不经 uv run 包装）
uv sync
mkdir -p ~/.vibe-stock/agent/logs   # launchd 不建日志父目录
sed -e "s|__REPO_PATH__|$(pwd)|g" -e "s|__HOME__|$HOME|g" \
    ops/launchd/com.threefish.stocktrading.agent.plist \
    > ~/Library/LaunchAgents/com.threefish.stocktrading.agent.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.threefish.stocktrading.agent.plist
curl -s http://127.0.0.1:8730/api/health   # RunAtLoad 已拉起；验证 ok=true
```

坑位：入口必须是 console script 直调——若用 `uv run` 包装，launchd 只盯 uv 父进程，python 服务进程会在父进程死亡时成孤儿继续占端口、守护与实际服务脱钩（本机实测）。依赖更新后须重跑 `uv sync`（.venv 形态）或 `uv tool install --force <repo>`（tool 形态）。

备选安装形态（本机实测坑位）：仓库若位于 `~/Documents` 下（iCloud「桌面与文稿」同步域），launchd 拉起的 Python 会在解释器初始化 getpath 阶段对 `open()` 无限挂起（TCC/FileProvider 交互；同路径 shell 直跑正常、非 Documents 路径 launchd 正常）。此时改用 `uv tool install --force <repo>`，plist 入口指向 `~/.local/bin/stock-trading-agent`（venv 落 `~/.local/share/uv/tools/`，脱离 Documents 域）。

坑位：sed 用 `|` 分隔符（路径含 `/`）；模板**不带** `STA_ALLOW_REAL_CC`（出厂 dry-run 安全）——启用真实 CC 通道须自行在 plist 注入 `<key>EnvironmentVariables</key>` + `STA_ALLOW_REAL_CC=1` 并 `bootout`+`bootstrap` 重载，同时建议开启预算闸（NR-4）。

## 验证与运维

```bash
launchctl print gui/$(id -u)/com.threefish.stocktrading.agent | head -20  # 状态与最近退出码
tail -f ~/.vibe-stock/agent/logs/panel.err.log                            # 运行日志
pgrep -f "stock-trading-agent serve" && kill -9 $(pgrep -f "stock-trading-agent serve")  # 守护自愈验证：kill 后 ≤30s 内重启（ThrottleInterval）
launchctl bootout gui/$(id -u)/com.threefish.stocktrading.agent          # 卸载（彻底停止须 bootout——kill 后 KeepAlive=true 无条件重拉）
```

## 语义要点

- **AbandonProcessGroup=true（D3 已裁定）**：launchd 重启面板时不连杀同 process group 的在跑 CC 子进程（R-12）；产品侧另有 `start_new_session=True` 使 CC 子进程独立进程组（双保险）。
- **ExitTimeOut=20 > uvicorn 优雅关停 10s**（`serve --graceful-shutdown`，默认 10）：保证「关停在杀之前完成」，在途任务留给断点恢复（启动期 running 残留 → abandoned）。
- **单实例**：`agent/daemon.lock`（flock）；被持有时面板降级只读（`/api/monitor/status.loop.active=false`），CLI `run-once` 则 exit 3。
- **端口冲突预检（0.0.11）**：适用条件 = serve 启动期端口（127.0.0.1:8730 或 `--port` 指定值）已被占用；做法 = connect 探测 + `GET /api/health` component 判占口者身份——本面板已在跑则打印 URL/version/PID 与 kickstart 指引后 exit 0（幂等成功：面板在线即目标达成；`KeepAlive=true` 为无条件 30s 节流重启，退出码不影响 launchd 行为），异构进程则打印 PID 与排查指引 exit 1；坑位 = 不做自动换端口回落（8730 是面板 SSOT 地址）、lsof 缺失时 PID 位留空不阻塞、预检通过到 uvicorn bind 间仍有理论竞态窗（罕见情形仍裸 errno 48 兜底，属常见路径改善非绝对保证）。
- 模板关键属性由 selftest/单测以 plistlib 钉死（KeepAlive/AbandonProcessGroup/ExitTimeOut/ThrottleInterval/RunAtLoad），模板漂移即测试红。
