# 14 · 面板设计系统规约（0.0.19 建）

**SSOT = `src/stock_trading_agent/server/static/index.html` `:root`**（三层 token 唯一权威定义源；本文档为投影与规范，不双写值）。方法论：三层 token 架构（Primitive→Semantic→Component，ui-ux-pro-max design-system）。

## ① 三层架构

| 层 | 职责 | 规模 | 改动时机 |
| :--- | :--- | :--- | :--- |
| Primitive（`--px-*` 等） | 原始值：全库**唯一** hex/px 字面落点 | 48 枚（17 色 + 9 字号 + 5 圆角 + 7 间距 + 3 阴影 + 4 glow + 3 动效） | 极少——色板基因级变更 |
| Semantic（30 个既有名） | 语义别名：`--bg0..3/--line(2)/--t1..4/--up|--dn|--acc|--warn|--hold 各 fg/bg/ln(2)/--mono/--sans` | 30 枚 | 主题切换位（light 主题不入版图） |
| Component（`--inp-*/--btn-*/--step-cur-grad/--nr2-fg*/--ev-risk-fg`） | 组件级 token：变体经作用域覆写入口（如 `.x{--inp-bg:…}`） | 8 枚 | 组件定制 |

**兼容裁定（用户批准）**：30 个 Semantic 名**名与计算值双不动**——组件类与 panel.py 的 `var()` 引用零改动即吃到 Primitive 层；`rgba(a,b,c,α)` 与 `color-mix(in srgb,#abc α%,transparent)` 在 srgb 预乘插值下逐位相等（getComputedStyle 使用点探针验证法见 ⑥）。

## ② 标尺

- **字号 9 档**：`--fs-10/11/12/13/14/16/17/19/26`（自 14 档收敛：10.5→11、11.5→12、12.5→13、16.5→17、9.5→10、25→26）。
- **圆角 5 档**：`--r-xs(4)/sm(6)/md(8)/lg(10)/full(999)`；`50%` 圆点为形状非档位（门禁白名单）；1px 流水线连接线→`--r-xs`（2px 高线渲染等价）。
- **间距 7 档**：`--sp-1..7`（2/4/6/8/10/12/16px）。**裁定：既有 padding/margin/gap 不回溯迁移**（37+19+13 个取值防布局像素漂移）；`13px` 布局主 gap 为量化终端密度基因不入档、字面保留；`--sp-*` 仅供新增规则（升级位：全量迁移待后续轮）。
- **阴影三档 + glow 四枚**：`--sh-1`（卡片）/`--sh-2`（主按钮）/`--sh-3`（focus 环 3px@25%）；`--glow-up/dn/warn`（8px@70% 归一）+ `--glow-acc`（18px@35%）。glow 族自 8 种 alpha 异值归一（.4–.9→70%/35% 两档）。
- **动效**：`--dur-fast(120ms)/--dur-slow(180ms)/--ease-std(ease-in-out)`。

## ③ 裸值映射规则 R1–R6（0.0.19 清剿 60 处的规则沉淀）

- **R1 精确匹配**：与 primitive 值相等 → 直接 `var()`。
- **R2 同族 alpha**：同基色不同透明度 → `color-mix(in srgb,var(--px-*) N%,transparent)`；**吸附条款**：与既有语义态 token alpha 差 ≤0.05 时吸附语义 token（消灭近似重复，如 `.nr2` 边框 .38→`var(--upln)` .34）。
- **R3 无彩 alpha**：白/黑系 rgba → `--px-white/--px-black` mix（分隔线、卡片阴影、毛玻璃底）。
- **R4 近似异色归族**：先判语义族（红 up/绿 dn/蓝 acc/黄 warn），基色归族后走 R2（接受 ±个位数色阶漂移；`rgba(255,93,93,*)`/`rgba(255,80,80,*)`→红系；体系外蓝 `#58a6ff`→`--acc`）。
- **R5 文本色禁 color-mix**：文本色只允许语义/组件 token——不支持 color-mix 的环境**文本永不失效**（降级只丢装饰层）。
- **R6 残余异色单点收编**：无法用基色+无彩干净表达者（`--nr2-fg/#FFB4B4`、`--nr2-fg-em/#FFD0D0`、`--ev-risk-fg/#D9A0A0` 与渐变端点 `--btn-grad/--step-cur-grad`）→ Component token，hex 落 `:root`（hex 白名单=`:root` 本身）。

## ④ 组件四态规范

状态优先级（高→低）：**disabled > loading（经 `hx-disabled-elt`→`button:disabled`）> active > focus（全局 `:focus-visible` 2px token 环 + `.inp:focus` `--sh-3`）> hover > default**。

| 组件 | hover | active | focus | disabled |
| :--- | :--- | :--- | :--- | :--- |
| `.tab` | `:not(.on)` 显 `--t2/--bg2` | 显 `--t1/--bg3` | 全局环 | 不适用（永不 disabled） |
| `.btn-pri` | `brightness(1.07)` | `brightness(.93)` | 全局环 | `opacity:.5`+`not-allowed` |
| `.mini` | 边框/文字 `--accln/--acc2` | `--bg2/--t1` 底 | 全局环 | 同上（6 处 hx-disabled-elt） |
| `a` | `--acc2` | — | 全局环 | — |
| `.inp/.sel` | 边框 `--accln` | — | `--sh-3` 环+边框 | `opacity:.5`+`--bg2` |
| 静态语义态族 | `.st` 11 态 / `.sig` 5 / `.lv/.chip/.phase/.step/.leg` 3 / `.sdot/.ev` 2 / `tr.open` 1 / `dist-*` 2 | | | |

统一 `transition var(--dur-fast) var(--ease-std)`；`@media (prefers-reduced-motion:reduce)` 关闭 `.pd-live/.sdot-ok/.spin` 脉冲与全部过渡。

## ⑤ color-mix 基线与降级语义

基线：Chrome 111+/Safari 16.2+（2023 baseline；本机面板 127.0.0.1 访问）。**裁定：接受裸降级，不做 @supports 兜底**（兜底需在 :root 外保留整套 rgba 传统值——双事实源+违背零裸色门禁）。降级逐属性语义：卡片阴影平、glow 无光晕、半透明分隔线/底色缺失、脉冲无光环；布局与文本零受损（文本 R5 兜底、focus 环走 `--acc` 不经 mix、结构 flex/grid 承载）。

## ⑥ 机器门禁（`tests/unit/test_design_tokens.py`，22 例）

(a) style 块 `:root` 外零裸 hex/rgba/hsl + body SVG `fill/stroke` 仅 `var()`；(b) 全部 `var(--x)` 引用必须在 `:root` 定义（孤儿绝根，`--nr2ln` 类问题不可能复发）；(c) font-size/圆角仅 token 或白名单（`50%`；`font:` 简写 size 位单独校验）；(d) panel.py inline style 与 SVG 属性中颜色/字号仅 `var()`；(钉面) 30 兼容名在场、色板核心逐字钉死（红涨绿跌基因）、四态/reduced-motion/动效 token 存在性、`hx-` 属性计数 ≥ 基线（htmx 契约不回退）。

**token 链路验证法**：custom property 不解析 color-mix，语义层计算值验证须经**使用点探针**（`d.style.color='var(--x)'` → `getComputedStyle(d).color`）。

## ⑦ 扩展位（登记未做）

`--hold2/--warn2`（hi 态对称，无消费者暂不加）；间距全量迁移至 `--sp-*`；light 主题（Semantic 层已具备切换位，产品定位 dark-only 不入版图）。
