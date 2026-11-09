# 14 · 面板设计系统规约（0.0.23 建；文件头原标 0.0.19 属虚标——git 历史首库于 0.0.23 eba8ddf）

**SSOT = `src/stock_trading_agent/server/static/index.html` `:root`**（三层 token 唯一权威定义源；本文档为投影与规范，不双写值）。方法论：三层 token 架构（Primitive→Semantic→Component，ui-ux-pro-max design-system）。

## ① 三层架构

| 层 | 职责 | 规模 | 改动时机 |
| :--- | :--- | :--- | :--- |
| Primitive（`--px-*` 等） | 原始值：全库**唯一** hex/px 字面落点 | 55 枚（24 色 + 9 字号 + 5 圆角 + 7 间距 + 3 阴影 + 4 glow + 3 动效；0.0.24 增 `--px-btn-1/2/--px-step-2/--px-nr2-1/2/--px-evrisk` 6 枚收编 Component 裸 hex；0.0.31 勘误：建档期计数笔误 54→55、23→24 色） | 极少——色板基因级变更 |
| Semantic（30 个既有名） | 语义别名：`--bg0..3/--line(2)/--t1..4/--up|--dn|--acc|--warn|--hold 各 fg/bg/ln(2)/--mono/--sans` | 30 枚 | 主题切换位（light 主题不入版图） |
| Component（`--inp-*/--btn-*/--step-cur-grad/--nr2-fg*/--ev-risk-fg`） | 组件级 token：变体经作用域覆写入口（如 `.x{--inp-bg:…}`） | 8 枚 | 组件定制 |

**兼容裁定（用户批准）**：30 个 Semantic 名**名与计算值双不动**——组件类与 panel.py 的 `var()` 引用零改动即吃到 Primitive 层；`rgba(a,b,c,α)` 与 `color-mix(in srgb,#abc α%,transparent)` 在 srgb 预乘插值下逐位相等（getComputedStyle 使用点探针验证法见 ⑥）。

## ② 标尺

- **字号 9 档**：`--fs-10/11/12/13/14/16/17/19/26`（自 14 档收敛：10.5→11、11.5→12、12.5→13、16.5→17、9.5→10、25→26）。
- **圆角 5 档**：`--r-xs(4)/sm(6)/md(8)/lg(10)/full(999)`；`50%` 圆点为形状非档位（门禁白名单）；1px 流水线连接线→`--r-xs`（2px 高线渲染等价）。
- **间距 7 档**：`--sp-1..7`（2/4/6/8/10/12/16px）。**0.0.28：`gap` 全量回溯迁移完成**——全部 `gap` 声明仅 `var(--sp-*)` 或 `0`（机器门）；`13px` 密度基因退役吸附 `--sp-6`(12px)，模块间隙统一 `--sp-6`、卡内密度档 `--sp-4`(8px)；**padding/margin 仍不回溯**（防布局像素漂移，升级位待后续轮）。
- **阴影三档 + glow 四枚**：`--sh-1`（卡片）/`--sh-2`（主按钮）/`--sh-3`（focus 环 3px@25%）；`--glow-up/dn/warn`（8px@70% 归一）+ `--glow-acc`（18px@35%）。glow 族自 8 种 alpha 异值归一（.4–.9→70%/35% 两档）。
- **动效**：`--dur-fast(120ms)/--dur-slow(180ms)/--ease-std(ease-in-out)`。

## ③ 裸值映射规则 R1–R6（0.0.19 清剿 60 处的规则沉淀）

- **R1 精确匹配**：与 primitive 值相等 → 直接 `var()`。
- **R2 同族 alpha**：同基色不同透明度 → `color-mix(in srgb,var(--px-*) N%,transparent)`；**吸附条款**：与既有语义态 token alpha 差 ≤0.05 时吸附语义 token（消灭近似重复，如 `.nr2` 边框 .38→`var(--upln)` .34）。
- **R3 无彩 alpha**：白/黑系 rgba → `--px-white/--px-black` mix（分隔线、卡片阴影、毛玻璃底）。
- **R4 近似异色归族**：先判语义族（红 up/绿 dn/蓝 acc/黄 warn），基色归族后走 R2（接受 ±个位数色阶漂移；`rgba(255,93,93,*)`/`rgba(255,80,80,*)`→红系；体系外蓝 `#58a6ff`→`--acc`）。
- **R5 文本色禁 color-mix**：文本色只允许语义/组件 token——不支持 color-mix 的环境**文本永不失效**（降级只丢装饰层）。
- **R6 残余异色单点收编**：无法用基色+无彩干净表达者（`--nr2-fg/#FFB4B4`、`--nr2-fg-em/#FFD0D0`、`--ev-risk-fg/#D9A0A0` 与渐变端点 `--btn-grad/--step-cur-grad`）→ Component token，hex 落 **Primitive 层**（0.0.24 起 Component 零裸 hex；:root 外零 hex 门禁不变）。

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

## ⑥ 机器门禁（`tests/unit/test_design_tokens.py`，0.0.19 建 22 例 + 0.0.24 增 8 例 + 0.0.28 增 5 例）

(a) style 块 `:root` 外零裸 hex/rgba/hsl + body SVG `fill/stroke` 仅 `var()`；(b) 全部 `var(--x)` 引用必须在 `:root` 定义（孤儿绝根，`--nr2ln` 类问题不可能复发）；(c) font-size/圆角仅 token 或白名单（`50%`；`font:` 简写 size 位单独校验）；(d) panel.py inline style 与 SVG 属性中颜色/字号仅 `var()`；(e) 0.0.28 布局与对齐五断言（`TestLayoutNorms028`：grid3 绝根、gap 仅 `var()`/`0`、数值右对齐规则在场、空态居中钉面、`font:` 简写 `None` 占位清零）；(钉面) 30 兼容名在场、色板核心逐字钉死（红涨绿跌基因）、四态/reduced-motion/动效 token 存在性、`hx-` 属性计数 ≥ 基线（htmx 契约不回退）。

**token 链路验证法**：custom property 不解析 color-mix，语义层计算值验证须经**使用点探针**（`d.style.color='var(--x)'` → `getComputedStyle(d).color`）。

## ⑦ 扩展位（登记未做）

`--hold2/--warn2`（hi 态对称，无消费者暂不加）；padding/margin 全量迁移至 `--sp-*`；light 主题（Semantic 层已具备切换位，产品定位 dark-only 不入版图）。

## ⑧ 空态三分（0.0.24 建）

| 类 | 语义 | 使用面 |
| :-- | :-- | :-- |
| `.loadmsg` | 初始加载占位 | **仅 index.html 静态壳**（panel.py 全面退场，负断言收口） |
| `.empty` + `.empty-tip` | 服务端数据空态：主句人话 + 下一步引导（内部术语/路径入 `title`） | panel.py 各 render_* 空分支 |
| `.muted` | 有数据时的说明性小字 | 预算行/stats 汇总/卡面口径注记等 |

## ⑨ 时间与数字排版（0.0.24 建）

- **时间两档**：当日视图（①流/③表/②队列/⑤落盘）= `HH:MM`（`_ts_hhmm`）；跨日台账（⑦audit/⑦sessions/④逐笔）= `MM-DD HH:MM`（`_ts_short`，`astimezone()` 转本地——台账多为 +00:00 串）；一律 `title` 挂 ISO 全值。
- **数字**：None/缺数 = `—`；真实零 = `0`/`0.0`（不许互串）；比率 2 位小数；百分比带符号；千分位 `,`。
- **条件列**：全表无数据的列整列省去（colgroup/thead/td 三处联动，①距止损列先例）。
- **md 内嵌表**（`_md_lite`）：首行升 `thead`（`th scope`）；动态列数豁免——colgroup 不带 `.w-*`。

## ⑩ 交互反馈规范（0.0.24 建）

- **错误呈现**：`meta[name=htmx-config] responseHandling` 令 4xx/5xx 也 swap；`app.py` 异常处理器把 `/api/panel/*` + `HX-Request` 的错误统一渲 HTML 错误卡（`render_error_card`，**状态码保留**，GET 附重试/POST 不附）；JSON 孪生端点零变化。`showErr` 以 content-type 守卫避免与局部错误卡叠加。
- **滚动定位**：用户点击驱动的详情注入统一 `hx-swap="innerHTML show:top"`（③详情/复核、⑤code 链接、②报告、⑦转录/sessions 互查）；轮询容器一律不带 show（防阅读打断）。
- **阅读态保护**：含输入的表单外置 load-only 容器（④ca-form 先例）；阅读容器不嵌轮询 fragment（⑦转录外置先例）。
- **破坏性防护**：改账本/不可逆操作带 `hx-confirm`（④确认/驳回/馈送；L2 开启既有）；briefing ack 例行操作不确认。
- **加载反馈**：卡级 `.htmx-indicator spin`（13 卡）+ 顶栏全局忙灯 `#busy`（beforeRequest/afterRequest 计数）。
- **折叠**：长文正文用原生 `<details class="brief-fold">`（briefing/通知聚合/无持仓 ca-form）；可点击元素不进 `<summary>`。
- **内部编号收纳**：文档编号（R7/R8/R9/NR-3/NR-5/F-34/F-38/§15/04 规约等）出正文入同元素 `title`；产品机制名（CC/T2/L2/adjudicate/断言器/hash 链/NR-2 横幅/NR-1 标记）留正文（测试钉死）。

## ⑪ 布局与对齐规约（0.0.28 建）

- **两栏上限（无条件全局规则：每行分栏最多左右两栏）**：页面级布局仅两基座——`.grid-main`（`1fr 340px` 主+边栏，①⑦ 页；③ 页作用域覆写 `1fr 380px`）与 `.grid-rail`（`1fr 500px`，④ 页 fragment 内）+ 既有 `.grid-2`（等宽两栏，②⑥ 页）与全宽单栏；三栏及以上布局禁止（机器门 `grid3` 绝根）。做法：内容按「主信息流 1fr + 辅助面定宽栏」分派；两栏 `align-items:stretch` 等高（grid 行高取两栏最大者）。坑位：卡内内容格（`.stats`/`.opins` KPI 与意见格）是内容网格非分栏，不受两栏上限约束；1180/880 断点一律单栏塌缩。
- **模块间隙等距**：见 §② 0.0.28 gap 迁移句（模块间 `--sp-6`、卡内 `--sp-4` 两档节奏，机器门 gap 仅 `var()`/`0`）。
- **对齐学说（无条件全局规则：三类对齐各归其位）**：标题/正文/表格文本列左对齐（默认）；数值列右对齐（`td.num`/`th.num` 配对标注，panel.py 十表已标）；居中仅三类——空态（`.empty` 居中 + 18px 呼吸）、流水线步骤（`.pipe`）、徽章圆点。坑位：日期/时间列按数值列右对齐（等宽数字同位对齐），代码列保持左对齐（与名称同格时防推挤错位）。
- **卡片化统一（0.0.28）**：④⑥ 页裸 `h3` 段收编 `.card` 模块——④：账户与持仓 / 逐笔确认队列|权益曲线+模拟绩效（grid-rail）/ 公司行动登记；⑥：任务画像·预算双口径 | 管线质量·T+1 复核（grid-2 双卡等宽）；`h3` 仅存卡内节标题。坑位：`.stats-4` 修饰类供四格 KPI 一行四列（≤880 塌缩 2×2），三格绩效仍用 `.stats`。
- **长表滚动容器（0.0.29）**：行数无上界或常态 ≥20 行的表（③事件/⑦decisions 台账/⑦sessions）一律 `<div class="tbl-wrap">` 外裹——`max-height:460px;overflow:auto;overscroll-behavior:contain` + `thead th` sticky 吸顶（底色 `var(--bg1)` 防透视）；数据层 `tail` 截断不动（API 契约稳定），`.tbl-note` 计数条留在容器外恒可见。坑位：包裹层不得成为 hx-target（swap 目标仍指 card-b 级容器）；③页实机页高 5370→1219px 即此范式收益（0.0.31 勘误：原句 1466 为中间态数字，PR #35 终态 1219）。
- **网格子元素数=轨道数（0.0.29，无条件全局规则：display:grid 容器的直接子元素数必须等于显式轨道数）**：`.sys-row` 4 子元素（sdot/sys-l/sys-sched/sys-st）配 `14px minmax(0,1fr) 56px 56px` 四轨（0.0.28 三轨致第 4 元素掉隐式行）；`sys-desc` 恒带 `title` 全值兜底截断。坑位：改子元素数时同步改轨道数并跑 `TestLayoutNorms029`。
- **操作列触点间距（0.0.29）**：表内多按钮操作列用 `.tbl-act`（flex + `gap:var(--sp-4)`=8px 触点下限 + flex-wrap）；首个消费者 ③事件表「详情/盯盘复核」。坑位：单钮列无需包裹。
- **空态同构（0.0.29）**：可注入容器（③`#advice-detail`）的初始空态与注入态同构（`.card` 包裹同款 chrome），选中前后布局零跳变；空文案仍走 `.empty` 三分规范。

## ⑫ 一屏化规约（0.0.31 建）

- **一屏判据（无条件全局规则：≥1280 桌面档整页零滚动）**：每页 `document.scrollHeight ≤ 视口高`（实机探针验证：1440×800/1280×800/1440×900 七页全过）；≤1180 窄档解除视口锁回单栏自然流（可读性优先，零横向溢出）；页脚免责恒在一屏内。坑位：改页内固定元素（hero/横幅/折叠条）高度后须复测该页 K 值。
- **视口锁三件套**：`.screen`（内容区 `height:calc(100vh - K)`，页级 K 实测钉面：①318/②273/③⑦236/④288/⑥243）+ `.grow`（卡体 flex 滚动，数据态再涨恒一屏）+ `.flex-fill`（填满余高）。做法：固定卡自然高在前，可长内容卡挂 grow 收尾；两栏页锁挂在 grid-main 上。坑位：锁容器内固定卡总高超过锁高时会溢出页面——新增固定卡须同步上调该页 K 值。
- **subtabs 页签（radio 零 JS）**：`.st-labels`（input+label 邻接高亮）+ `.subpanels/.subpanel`（:has 选择器按选中序显隐，至多 4 签）；键盘可达（radio 组方向键切换）；htmx 轮询容器 id 与属性零变化。消费者：②页两签（工作台|报告档案）、④页三签（账户持仓|逐笔与曲线|公司行动）。坑位：签内长内容须自带限高（grow/tbl-wrap），激活签 overflow:auto 兜底。
- **限高档族**：`.tbl-wrap` 默认 300px + `.sm` 200px；feed-cap 220px；注入区按位定值（#mo-notif 240 / #au-transcript 260 / ⑤详情 420 / ②报告正文 calc）。坑位：改默认值须同步 `TestLayoutNorms029` 钉面。
