# D6 · Agent SDK 对照实验记录（CLI vs SDK，D-16/D-17）

> 实验脚本：`ops/d6_sdk_compare.py`（`uv run --with claude-agent-sdk ops/d6_sdk_compare.py --real`；环境门 `STA_ALLOW_REAL_CC=1`；默认无 --real 安全退出）。对照口径：同 prompt、cli_path 钉同一 claude 二进制、SDK preset=claude_code + setting_sources=[] 对齐、CLI 走 build_argv 全旗标面。

| 日期 | CLI 版本 | SDK 版本 | prompt | 次数 | CLI 耗时 p50/max | SDK 耗时 p50/max | CLI cost | SDK cost | 备注 |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| — | — | — | — | — | — | — | — | — | 未跑，待用户触发（R7：本轮只交脚本+模板） |

填写说明：跑完把脚本 stdout 的 JSON 摘要填入上表（一行一次实验）；结论性判断（如 SDK 版本捆绑漂移是否复现 D-14）记在表后追加小节。
