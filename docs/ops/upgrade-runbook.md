# CLI 升级 Runbook（升级门禁三件套 + versions/ 回滚）

> 0.0.26 落地；规约裁定见 [12-orchestration.md §②「升级门禁与 versions/ 回滚裁定」](../specs/12-orchestration.md)。触发纪律：**首次主动升级 CLI 版本前必做**（§⑮ 原登记项，用户 2026-11-08 裁定提前清偿）。

## 何时跑

钉版本变更（`claude install <ver>` 换版本）前必跑；建议同时跳过已知回归窗版本（2.1.310→2.1.318 先例，避开 2.1.314–2.1.317）。

## versions/ 回滚机制

- 机器侧：`~/.local/share/claude/versions/<ver>` 多版本共存（claude 原生安装器），`~/.local/bin/claude` 软链指当前版。
- 回滚主路径：`claude install <旧版>`（重指软链）。
- 离线兜底：`ln -sfn ~/.local/share/claude/versions/<旧版> ~/.local/bin/claude`（install 理论可能触网校验，兜底不依赖网络）。
- 仓侧对应物：`ops/upgrade-gate/pinned.json`（钉版本锚）+ `claude-<ver>.snapshot.json`/`.help.txt` 基线快照族——回滚后复跑 gate 即验证回滚成功。

## 升级流程

```bash
# —— 升级前（旧版在跑）——
git pull && uv sync
python3 ops/upgrade_changelog.py > .temp/claude-changelog.md   # 网络在 ops 面；失败则人工下载喂入
ls ~/.local/share/claude/versions/ && readlink ~/.local/bin/claude   # 盘点回滚位（旧版二进制不在场先补装）
STA_ALLOW_REAL_CC=1 uv run stock-trading-agent upgrade-gate    # 现版 vs 基线自检（应 PASS；漂移=环境异常先查）

# —— 安装 ——
claude install <目标版>

# —— 升级后三件套 ——
STA_ALLOW_REAL_CC=1 uv run stock-trading-agent upgrade-gate --deep \
    --changelog .temp/claude-changelog.md --baseline <旧版>
# exit 0 → PASS；exit 2 → 逐条核 WARN 后 --ack-warn 复跑登记；exit 1 → 回滚（见下）

# —— PASS 收尾 ——
STA_ALLOW_REAL_CC=1 uv run stock-trading-agent upgrade-gate --capture --deep   # 落新基线
# 更新 ops/upgrade-gate/pinned.json → 新版本号；快照入库 → PR（含 spec/CHANGELOG）
launchctl kickstart -k gui/$(id -u)/com.threefish.stocktrading.agent          # 重启面板（serve 重载通道）
```

## 判定不过（exit 1）→ 回滚

```bash
claude install <旧版>                    # 或离线兜底 ln -sfn（见上）
STA_ALLOW_REAL_CC=1 uv run stock-trading-agent upgrade-gate --baseline <旧版>  # 应 PASS
launchctl kickstart -k gui/$(id -u)/com.threefish.stocktrading.agent
```

FAIL 项登记 `docs/.agents/issue.md`，升级押后再议（判据只收严：不得为过 gate 降级判据）。

## exit 契约与决策留痕

- `0=PASS · 1=FAIL/错误 · 2=WARN（--ack-warn 复跑登记人工已核）`。
- 每次运行追加 `upgrade_gate_run` 决策（payload：baseline_version/current_version/verdict/codes/ack/deep，零敏感量 NR-5；hash 链留痕）。决策链断裂时结果照常输出但不落决策（打 repair-decisions 指引）。

## 已知坑位

- **隐藏旗标**：`--max-turns` 在用但 `--help` 不可见（diff 只比基线可见面，隐藏态登记 INFO；真实通道由集成测试与非法枚举探针兜底）。
- **F-30 枚举探针**：`--permission-mode __sta_invalid__ --version` 应 exit 1 秒拒零成本；exit 0=隐藏别名吃进 → FAIL。
- **终端宽度**：capture_help 钉 `COLUMNS=100`——描述换行随宽度漂移，diff 不比描述原文（sha256 仅审计指纹）。
- **deep 探针花钱**：默认关；升级场景一次性（NR-4 预算字段照带）。
- **CHANGELOG 源不可得**：拉取失败不阻断升级（缺项 WARN 人工确认；`--changelog` 恒支持人工喂文件）。
