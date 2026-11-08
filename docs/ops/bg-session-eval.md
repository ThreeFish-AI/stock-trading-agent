# `--bg` 后台会话族 · 评估记录（方案 D-63，2026-11-08）

> 评估性质：文档性判定（无代码变更）。对象：Claude Code CLI 原生 `--bg` 会话族（agents/attach/logs/stop，`--resume` 可同 ID 后台续跑）vs 本产品现行自管 spawn-once 一次性进程。

## 结论

**不引入 `--bg` 会话族，维持自管 spawn-once 一次性进程 + `--resume` 重建形态。**

## 依据

1. **稳定性反证（D-06，#101641/#99194）**：一次性进程 + resume 比长驻双向 stream-json 管道更稳——两个 open issue（回合结束约 2s 即 exit 0 丢未写完 stdout、长驻 stdout 静默而转写仍追加）均指向长驻会话面；`--bg` 属同一会话族状态面（agents/attach/logs/stop 四个新管理原语），引入即接入该风险面。
2. **现形态已覆盖保活诉求**：M1 已落的超时状态机（timeout_s → SIGINT 收轮 → SIGKILL）+ launchd AbandonProcessGroup=true（D3）+ 崩溃重放（0.0.10 R5：monitor 源自动重放、同 dedup_key ≤2 实体防自旋）共同覆盖「会话中断后恢复」诉求；`--resume` 旗标全量重传（D-32）已在通道层可用。
3. **成本收益未证**：`--bg` 的收益场景（同 ID 后台续跑省冷启动）依赖高频 spawn 的固定成本成为瓶颈（F-59 冷 spawn 约 $0.20/次）——当前 L2 事件频率（每日 T2 ≤5 票 + 5 票闸）远未触达；引入新状态面与版本耦合的成本先于收益发生。

## 触发再评条件

L2 开启后观测到高频 spawn 冷启动成本成为实锤瓶颈（如：日 spawn ≥20 次且 cache 复用率低）时重开本评审，与 F-59 cache 复用优化（同日 resume 续连摊薄）一并评估。
