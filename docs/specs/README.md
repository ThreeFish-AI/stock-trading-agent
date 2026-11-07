# docs/specs/ · 实施规约落位

沿方案 [§17 实施规约落位预案](../research/main-agent-proposal.md)（2026-11-07 迁仓裁定后的落位形态）：

- **12-orchestration.md**：主 Agent 编排规约（①定位与边界 ②CCClient 通道契约 ③主 Loop 六段规约 ④状态扩展写拓扑 ⑤预算闸与降级 ⑥门禁预案）——首个实施 PR 建，编号沿方案原约；vibe-stock 不建同名文件、其 SKILL 判表不同步（产品编排不入诊股 Skill 判表）。
- **13-paper.md**：paper 引擎规约（方案 §12 母体拆分，红线句随拆分原样携带）——规约体量成型时（M2）拆入。

在文件建成前，方案原文（`../research/main-agent-proposal.md` §9–§17）即现行规约；建成后的演进只发生在本目录，方案原文保持审批时点快照。注：0.0.1 M1 骨架先于 12-orchestration.md 落地（预规约骨架，方案 §17 ①–⑤ 内容与 ⑥ selftest 判据已由代码与测试承载）；12-orchestration.md 随首个实施功能 PR 建立并回吸骨架现行为。
