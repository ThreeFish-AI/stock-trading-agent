# 交易日验证运维 · D8 定谳与 T2 出卡真跑

两项「需交易日」的遗留验证的操作手册。D8 采样由 launchd 自动执行（`ops/launchd/com.threefish.stocktrading.d8-probe.plist`，周一至五 09:36/13:06 两对照时点）；T2 出卡真跑按约定由用户在交易日盘后唤起 Agent 执行（花订阅额度约 $0.3–0.8，不自动化）。

## D8 港股时戳重探（E-25 vs E-76 定谳）

### 采样机制（自动）

- 采样器：`ops/d8_probe.py`（stdlib-only `/usr/bin/python3` 直跑）。每轮对标的集（`monitor/universe.json` hk 行，缺席回落 00700/00005/00388）逐股采：qt r_hk（f[30] 时戳→lag_min，E-25 主张面）、新浪 rt_hk（交叉价，无时戳）、东财 push2（价格+自带时戳+五档 bid1/ask1，E-76「与东财同步」锚）。
- 输出：`~/.vibe-stock/agent/logs/d8-samples-YYYYMMDD.jsonl`（append-only；每行含 local_ts 与 session 态——非盘中如实标 `session_closed`）。
- 时点纪律：09:36（隔夜陈旧延续面）与 13:06（午休时戳解冻基线）；12:00–13:00 时戳冻结窗样本保留但定谳不采信（07:26）；多日采样（建议 ≥2 个交易日）后定谳。
- 收盘锚：任一交易日 16:10 后 `cd ~/Documents/projects/aurelius/vibe-stock && uv run --no-project --with 'akshare==1.20.*' --with efinance --with yfinance python batch/monitor/watch.py --verify-hk`（三方价格对照写 `monitor/state.json hk_source`；注意其 16:10 前交易日守卫与 exit 3 锁语义）。

### 定谳判据（读样本时逐项核对）

1. **E-25 检验**：09:36 批次 r_hk `lag_min` 分布——若普遍 >15 分钟且 ts_raw 停留前日/早市早段，E-25（qt 快照滞后）在该机成立；
2. **E-76 检验**：同刻 r_hk vs 东财 push2 时戳差与价格偏离——若 r_hk lag 大而东财时戳新鲜且价格一致，E-76（r_hk 数据陈旧但可被东财校正）成立；五档 bid1/ask1 全 0 情况如实记录（vibe-stock 全仓零消费五档，不构成阻断）；
3. **午休后基线**：13:06 批次 lag_min 应显著回落（若仍高，说明通道整体滞后而非早市特例）；
4. **mode 对照**：收盘锚 `hk_source.mode`（r_hk/bars_fallback，0.3% 口径）与盘中样本一致性。

### 定谳落位（下轮会话执行）

- 产品仓 `docs/.agents/issue.md` 新增定谳条目（ID 递增），`docs/specs/12-orchestration.md` D8 相关句回写定谳结论；
- **判据只收严**：若定谳未通过（时戳滞后坐实），HK 撮合维持收盘复核闸（`paper/rules.hk_close_review_match`）不动；若定谳通过，港股盘中口径是否放开**另行评审**（本记录不构成放开依据）；
- 采样完成后 `launchctl bootout gui/$(id -u)/com.threefish.stocktrading.d8-probe`（多采无益，样本足够即停）。

## T2 出卡路径真跑（交易日盘后，用户唤起 Agent）

前置（Agent 核对）：

```bash
date +%F                          # 确认交易日
ls ~/.vibe-stock/agent/runs/$(date +%F)/t1/ 2>/dev/null   # T1 当日 context-pack 已出（面板常驻 worker 收盘后自动补跑）
tail -3 ~/.vibe-stock/agent/decisions.jsonl               # 无在途 monitor_review（dedup 幂等键当日已耗则次日）
```

执行与验证点：

```bash
# 临时停常驻面板释放 daemon 锁（launchd 会自动拉起，跑完手动恢复）
launchctl bootout gui/$(id -u)/com.threefish.stocktrading.agent
STA_ALLOW_REAL_CC=1 ~/Documents/projects/aurelius/stock-trading-agent/.venv/bin/stock-trading-agent \
  run-once --kind monitor_review --code <在册港股或A股code>
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.threefish.stocktrading.agent.plist
```

- 成功判据：exit 0 + `~/.vibe-stock/monitor/advice/$(date +%F)/<code>.md` 出卡 + decisions 尾部 `advice_card_written` + 面板⑤（127.0.0.1:8730）可见该卡；
- 降级判据（同样算验证通过）：会话自报 `facts_only`（done + `advice_refused[origin=session_degraded]`）或断言拒（failed + reasons 含保鲜门/锚③）——如实留证；
- 预算：monitor_review 路由 budget 1.0 / max_turns 12；真实消耗约 $0.3–0.8（订阅轨记账）。
