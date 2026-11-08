# D6 · Agent SDK 对照实验记录（CLI vs SDK，D-16/D-17）

> 实验脚本：`ops/d6_sdk_compare.py`（`uv run --with claude-agent-sdk ops/d6_sdk_compare.py --real`；环境门 `STA_ALLOW_REAL_CC=1`；默认无 --real 安全退出）。对照口径：同 prompt、cli_path 钉同一 claude 二进制、CLI 走 build_argv 全旗标面；SDK 对齐项随其 API 演进调整（见 2026-11-08 记录①）。

| 日期 | CLI 版本 | SDK 版本 | prompt | 次数 | CLI 耗时 p50/max | SDK 耗时 p50/max | CLI cost | SDK cost | 备注 |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| 2026-11-08 | 2.1.319 | claude-agent-sdk 0.2.179 | 只回复两个字：收到 | 3+3 | 5.94s / 7.14s | 21.18s / 21.86s | $0.1335（合计） | 订阅态 cost 字段缺席 | 两通道 subtype 全 success |

## 2026-11-08 首跑结论（用户授权真跑）

1. **SDK API 漂移实锤（D-14 同族）**：claude-agent-sdk 0.2.179 的 `ClaudeAgentOptions` 已无 `preset`/`setting_sources` 形参（脚本原按调研时点 API 写就即抛 TypeError）；现签名族为 `tools/allowed_tools/system_prompt/mcp_servers/strict_mcp_config/permission_mode/session_id/max_turns/max_budget_usd/cli_path/settings/extra_args/env` 等。对齐口径改为 cli_path 钉同二进制 + 默认 system prompt + allowed_tools/max_turns/max_budget_usd 同值。
2. **耗时对照**：CLI（headless 全旗标面）p50 5.94s vs SDK p50 21.18s——SDK 包装层每次 spawn 多约 15s（客户端引导/握手），与「headless CLI 主通道、SDK 仅对照」的技术主线裁定（方案 §9）实证一致。
3. **成本口径**：CLI 侧单次 $0.014–0.060（input 2.7k–14.7k tokens、output 3——「收到」二字；input 量差来自系统提示与 settings 注入面波动）；订阅登录态下 SDK result 消息 usage/cost 字段缺席（total_cost_usd null），成本监控以 CLI 通道 envelope 为准（支柱③审计线同源）。
4. **坑位两则**：①`--session-id` 用 uuid5 按日期+序号派生会在同日重跑时撞「Session ID already in use」——对照脚本改 uuid4 每跑真新鲜（§② 坑位③延伸）；②SDK 侧 query() 流式消息的 result 载荷在本版不带 usage 字段，token 对照须走 CLI 侧或后续 SDK 版本复核。

