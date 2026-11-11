"""0.0.19 设计 token 机器门禁：三层架构合规（纯文件解析，无服务器）。

规范 SSOT = index.html :root（docs/specs/14-design-system.md 为投影）。
四断言组：(a) 裸色零容忍 (b) 孤儿 var() 绝根 (c) 字号/圆角仅 token
(d) panel.py inline/SVG 仅 var()。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

HTML = Path("src/stock_trading_agent/server/static/index.html").read_text(encoding="utf-8")
PANEL = Path("src/stock_trading_agent/server/panel.py").read_text(encoding="utf-8")
STYLE = HTML.split("<style>", 1)[1].split("</style>", 1)[0]
ROOT_MATCH = re.search(r":root\s*\{(.*?)\n\}", STYLE, re.S)
ROOT_BLOCK = ROOT_MATCH.group(1) if ROOT_MATCH else ""
RULES = (
    re.sub(r"/\*.*?\*/", "", STYLE.replace(ROOT_MATCH.group(0), ""), flags=re.S)
    if ROOT_MATCH
    else ""
)
BODY = HTML.split("</style>", 1)[1]

COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(")

LEGACY_30 = [
    "--bg0",
    "--bg1",
    "--bg2",
    "--bg3",
    "--line",
    "--line2",
    "--t1",
    "--t2",
    "--t3",
    "--t4",
    "--up",
    "--up2",
    "--upbg",
    "--upln",
    "--dn",
    "--dn2",
    "--dnbg",
    "--dnln",
    "--hold",
    "--holdbg",
    "--holdln",
    "--acc",
    "--acc2",
    "--accbg",
    "--accln",
    "--warn",
    "--warnbg",
    "--warnln",
    "--mono",
    "--sans",
]


class TestLayeredRoot:
    def test_three_layer_sections_present(self):
        assert "1/3 Primitive" in ROOT_BLOCK
        assert "2/3 Semantic" in ROOT_BLOCK
        assert "3/3 Component" in ROOT_BLOCK

    def test_legacy_30_names_defined(self):
        for name in LEGACY_30:
            assert re.search(rf"(?:^|;)\s*{re.escape(name)}:", ROOT_BLOCK, re.M), name

    def test_semantic_refers_to_primitive(self):
        assert "--up:var(--px-red);" in ROOT_BLOCK
        assert "--t4:var(--px-txt-4);" in ROOT_BLOCK
        assert "--line:color-mix(in srgb,var(--px-white) 8%,transparent);" in ROOT_BLOCK

    def test_palette_pinned(self):
        """防手滑改色板：核心 primitive 逐字钉死（A股红涨绿跌基因）。"""
        for pin in [
            "--px-red:#FF6B6B",
            "--px-green:#3DCE96",
            "--px-blue:#6C9EFF",
            "--px-amber:#F0B35C",
            "--px-txt-4:#7E8AA5",
            "--px-bg-0:#0B0E14",
        ]:
            assert pin in ROOT_BLOCK, pin


class TestNoBareColors:
    def test_style_block_outside_root_zero_bare_colors(self):
        assert not COLOR_RE.search(RULES), "style 块 :root 外存在裸色"

    def test_body_svg_fill_stroke_via_tokens(self):
        for m in re.finditer(r'(?:fill|stroke)="([^"]*)"', BODY):
            assert m.group(1) in ("none",) or m.group(1).startswith("var("), m.group(0)


class TestNoOrphanVars:
    def test_all_var_refs_defined(self):
        defined = set(re.findall(r"(--[\w-]+)\s*:", ROOT_BLOCK))
        refs = set(re.findall(r"var\((--[\w-]+)", HTML)) | set(
            re.findall(r"var\((--[\w-]+)", PANEL)
        )
        assert refs <= defined, sorted(refs - defined)

    def test_nr2ln_orphan_gone(self):
        assert "--nr2ln" not in HTML


class TestScalesTokensOnly:
    def test_font_size_tokens_only(self):
        for v in re.findall(r"font-size:\s*([^;}]+)", RULES):
            assert re.fullmatch(r"var\(--fs-\d+\)", v.strip()), v

    def test_font_shorthand_size_token(self):
        for sh in re.findall(r"font:\s*[^;}]+", RULES):
            assert "var(--fs-" in sh and "px" not in sh, sh

    def test_radius_tokens_only(self):
        # 0.0.31：允许多角 token 简写（如 subtabs 页签 var(--r-sm) var(--r-sm) 0 0），逐角校验
        for v in re.findall(r"border-radius:\s*([^;}]+)", RULES):
            corners = v.strip().split()
            assert all(
                c == "50%" or c == "0" or re.fullmatch(r"var\(--r-[\w-]+\)", c) for c in corners
            ), v


class TestPanelInlineCompliance:
    def test_inline_style_colors_fonts_via_tokens(self):
        offenders = []
        for s in re.findall(r'style="([^"]*)"', PANEL):
            for decl in s.split(";"):
                if re.match(r"\s*(font-size|color|background|border|stroke|fill)\s*:", decl):
                    if "var(--" not in decl:
                        offenders.append(decl.strip())
        assert not offenders, offenders

    def test_svg_attrs_via_style_or_none(self):
        for attr in ("font-size", "fill", "stroke", "font-family"):
            for v in re.findall(rf'{attr}="([^"]*)"', PANEL):
                assert v == "none" or v.startswith("var("), f'{attr}="{v}"'


class TestStatesAndMotion:
    def test_four_states_present(self):
        assert ".tab:not(.on):hover" in STYLE
        assert ".tab:not(.on):active" in STYLE
        assert ".btn-pri:hover" in STYLE and ".btn-pri:active" in STYLE
        assert ".mini:active" in STYLE
        assert "a:hover" in STYLE
        assert ".inp:disabled" in STYLE

    def test_motion_tokens_and_reduced_motion(self):
        assert "--dur-fast:120ms" in ROOT_BLOCK and "--dur-slow:180ms" in ROOT_BLOCK
        assert "prefers-reduced-motion" in STYLE

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("--fs-10", "10px"),
            ("--fs-17", "17px"),
            ("--fs-26", "26px"),
            ("--r-xs", "4px"),
            ("--r-full", "999px"),
            ("--sp-4", "8px"),
        ],
    )
    def test_scale_values(self, name: str, expected: str):
        assert f"{name}:{expected}" in ROOT_BLOCK


class TestHtmxUntouched:
    HX_BASELINE = 24

    def test_hx_attrs_not_removed(self):
        assert HTML.count("hx-") >= self.HX_BASELINE


class TestDesignUpgradeNorms:
    """0.0.24 整体设计升级只收严断言：空态三分 / .sel:focus / cursor 冲突 / token 债清偿。"""

    def test_empty_component_present(self):
        assert ".empty{" in RULES and ".empty-tip" in RULES

    def test_sel_focus_ring(self):
        assert ".inp:focus,.sel:focus" in RULES

    def test_btn_cursor_conflict_gone(self):
        assert "cursor:default" not in RULES

    def test_dur_slow_has_consumer(self):
        assert "var(--dur-slow)" in RULES  # 0.0.24 .htmx-indicator 接活

    def test_component_hex_lands_primitive(self):
        # Component 层 6 枚裸 hex 落 Primitive；Component 引用经 var()（计算值逐位不变）
        for name in (
            "--px-btn-1",
            "--px-btn-2",
            "--px-step-2",
            "--px-nr2-1",
            "--px-nr2-2",
            "--px-evrisk",
        ):
            assert f"{name}:" in ROOT_BLOCK, name
            assert f"var({name})" in ROOT_BLOCK, name

    def test_panel_no_loadmsg_no_inline_flex(self):
        # 空态三分收口：panel.py 全面退场 loadmsg；inline display:flex 收编 .chip-row
        assert 'class="loadmsg"' not in PANEL
        assert 'style="display:flex' not in PANEL

    def test_dead_cbx_rule_gone(self):
        assert ".cbx{" not in RULES  # ①待办假复选框 → .sdot（防回流）

    def test_htmx_config_response_handling(self):
        assert 'name="htmx-config"' in HTML and '"[45]..","swap":true,"error":true' in HTML


class TestLayoutNorms028:
    """0.0.28 布局与对齐规约只收严断言：两栏上限 / gap 全量 token 化 / 数值右对齐 / 空态居中。

    规范 SSOT = docs/specs/14-design-system.md §⑪（index.html :root/CSS 为实现层）。
    """

    def test_three_column_grid_retired(self):
        assert "grid3" not in HTML, "三栏布局退役（每行分栏上限两栏）"
        assert ".grid-main{" in RULES and ".grid-rail{" in RULES
        assert "grid-template-columns:1fr 340px" in RULES  # grid-main 基座

    def test_all_gap_values_tokenized(self):
        offenders = [
            v
            for v in re.findall(r"gap:\s*([^;}]+)", RULES)
            for v in [v.strip()]
            if not all(tok == "0" or tok.startswith("var(") for tok in v.split())
        ]
        assert not offenders, offenders

    def test_numeric_cells_right_aligned(self):
        assert ".tbl td.num,.tbl th.num{text-align:right}" in RULES

    def test_empty_state_centered(self):
        assert (
            ".empty{color:var(--t3);font-size:var(--fs-12);line-height:1.7;"
            "text-align:center;padding:18px 12px}" in RULES
        )

    def test_font_shorthand_none_placeholder_gone(self):
        assert ")None " not in STYLE


class TestLayoutNorms029:
    """0.0.29 布局只收严断言：长表滚动容器 / sys-row 列数与子元素对齐 / 操作列触点间距。

    规范 SSOT = docs/specs/14-design-system.md §⑪（index.html :root/CSS 为实现层）。
    """

    def test_long_table_scroll_container(self):
        # 0.0.38：tbl-wrap 定帽退役（滚动统一归页滚位），thead 吸顶保留（相对页滚位吸附）
        assert ".tbl-wrap{max-height" not in RULES
        assert ".tbl-wrap.sm{max-height" not in RULES
        assert ".tbl-wrap .tbl thead th{position:sticky;top:0" in RULES  # thead 吸顶

    def test_sys_row_four_tracks(self):
        # 4 子元素（sdot/sys-l/sys-sched/sys-st）配 4 轨；修复前 3 轨致第 4 元素掉隐式行
        assert "grid-template-columns:14px minmax(0,1fr) 56px 56px" in RULES

    def test_action_cell_touch_spacing(self):
        assert ".tbl-act{display:flex;align-items:center;gap:var(--sp-4);flex-wrap:wrap}" in RULES


class TestOneScreen031:
    """0.0.31 一屏化只收严断言；0.0.36 视口锁 calc(100vh-K) 定高改弹性充满（Issue #36）。

    规范 SSOT = docs/specs/14-design-system.md §⑫（index.html :root/CSS 为实现层）。
    一屏判据：≥1280 桌面档 scrollHeight ≤ 视口高（实机探针验证；本门钉机制在场）。
    0.0.36 机制：main(flex:1)→.page.on(flex:1)→.screen(flex:1 1 auto) 恒恰满视口余高，
    K 值定高退役（定高小于内容时 grow 卡被挤没、大于可用时页脚上方恒留死带，双病灶）。
    """

    def test_screen_lock_present(self):
        assert ".screen{display:flex;flex-direction:column;flex:1 1 auto" in RULES
        assert ".flex-fill{flex:1 1 0;min-height:0}" in RULES

    def test_page_stretch_chain(self):
        # 弹性充满传导链：.page.on 漏加 flex 则 .screen 拿不到分配高度整链失效
        assert ".page.on{display:flex;flex-direction:column;gap:var(--sp-6);flex:1 1 auto" in RULES
        assert "#page-stats #stats{display:flex;flex-direction:column;flex:1 1 auto" in RULES

    def test_page_k_offsets_retired(self):
        # 0.0.36：页级 K 值定高全退役——逐页精确负钉 + 族级负钉（calc 定高形态整体禁回；
        # 若未来确需 max-height:calc 再按需收窄至 .screen 作用域）
        for page, k in (("overview", 318), ("diagnosis", 273), ("paper", 288)):
            assert f"#page-{page} .screen{{height:calc(100vh - {k}px)}}" not in RULES, page
        assert "#page-stats #stats{display:flex;flex-direction:column;height:calc" not in RULES
        assert "height:calc(100vh" not in RULES  # 族级：基座 K=236 与任意新 K 回流皆拒

    def test_grow_floor_present(self):
        # 0.0.38：grow 族整体退役（卡体滚动归页滚位）——机制零回流负钉
        assert ".grow{flex" not in RULES
        assert ".grow>.card-b,.grow>.card>.card-b" not in RULES
        assert ".card.grow,.grow>.card{min-height:96px}" not in RULES

    def test_last_card_stretch_grow_exclusion(self):
        # 0.0.29 末卡拉伸（0.0.38 grow 退役后选择器简化）+ 滚位列例外（内容流不拉伸）
        assert ".grid-main .col>.card:last-child{flex:1 1 auto}" in RULES
        assert ".grid-main .col.scrollhost>.card:last-child{flex:0 1 auto}" in RULES

    def test_main_flex_chain_root(self):
        # 0.0.36 弹性充满链首环 main{flex:1}；0.0.38 底 padding 归零 + min-height:0
        # （页脚上方间距统一交 .foot margin-top:12 承载：模块间/页脚上方全站 12px 节奏；
        #   min-content 撑破可用高致 body 滚的漏环由 min-height:0 收口，滚位接管前提）
        main_pin = (
            "main{width:100%;max-width:1400px;margin:0 auto;padding:18px 22px 0;flex:1;min-height:0"
        )
        assert main_pin in RULES

    def test_subtabs_component(self):
        assert ".subtabs:has(.st-labels input:nth-of-type(1):checked)" in RULES
        assert ".subpanels .subpanel:nth-of-type(1)" in RULES
        assert "radio" in PANEL  # ②④ fragment 页签 radio 在场

    def test_narrow_unlock(self):
        assert "@media (max-width:1180px){" in RULES
        assert ".screen,#page-stats #stats{flex:none;min-height:0}" in RULES


class TestSingleScroller038:
    """0.0.38 单滚动条治理（issue #36 第八轮）：每页至多一个滚动容器（页滚位）。

    判据只收严：页滚位外区域零 overflow、旧三套滚动机制（grow 卡体滚/定帽滚/①泄压阀）
    零回流；间距全站 12px 节奏（footer 上方 token 化）；矮视口 ≤760 退位自然流。
    页滚位清单：①左列 .scrollhost｜② .subpanel｜③ .screen｜④ 无（零滚）｜
    ⑤ #page-advice.on｜⑥ #stats｜⑦主列 .scrollhost。
    """

    def test_scrollhost_rule(self):
        assert (
            ".scrollhost{overflow:auto;overscroll-behavior:contain;scrollbar-width:thin}" in RULES
        )

    def test_scrollhost_mounted(self):
        # ①⑦ 主列挂载（BODY 为 </style> 后正文）
        assert BODY.count('class="col scrollhost"') == 2
        # ③ 页 screen 滚 + grid 退拉伸顶对齐
        assert "#page-monitor .screen{overflow:auto;overscroll-behavior:contain" in RULES
        assert "#page-monitor .screen>.grid-main{flex:0 1 auto;align-items:start}" in RULES
        # ⑤ 页容器滚
        assert "#page-advice.on{overflow:auto;overscroll-behavior:contain}" in RULES
        # ⑥ #stats 容器滚
        assert "#page-stats #stats{display:flex;flex-direction:column;flex:1 1 auto" in RULES
        assert "min-height:460px;gap:var(--sp-6);overflow:auto" in RULES

    def test_old_scrollers_retired(self):
        # ① 泄压阀 / .feed-cap 帽 / 转录 md-pre 帽 / ③⑤ 详情卡体滚 零回流
        assert "#page-overview .grid-main>.col:last-child{overflow:auto" not in RULES
        assert ".feed-cap{max-height" not in RULES
        assert "#au-transcript .md-pre{max-height" not in RULES
        assert "#advice-detail-2>.card>.card-b{flex:1 1 auto;overflow:auto" not in RULES
        assert "#advice-detail{flex:1 1 0;min-height:0;overflow:auto" not in RULES
        assert "#page-stats .grid-2>.card>.card-b{flex:1 1 auto;overflow:auto" not in RULES

    def test_grow_class_zero_in_body(self):
        # HTML 壳零 grow 类残留（CSS 退役 + DOM 同步清理）
        assert "grow" not in BODY

    def test_spacing_tokens_uniform(self):
        # 页脚上方 30px（14 margin + 16 padding）死区收口：foot margin 单源 12px
        # （main 底 padding 归零，模块间/页脚上方全站统一 12px 节奏）
        assert ".foot{border-top:1px solid var(--line);margin-top:var(--sp-6);" in RULES
        assert "padding:18px 22px 0;flex:1;min-height:0" in RULES

    def test_short_viewport_unlock(self):
        # 0.0.38 极矮视口（≤760 高）退位自然流：body 滚成为唯一滚动条（与窄档同构）
        assert "@media (max-height:760px){" in RULES

    def test_transcript_slot_moved_to_main_col(self):
        # ⑦ 转录槽挪主列（边列恒收敛；注入后自然高随主列页滚位滚）
        assert '<div id="au-transcript" class="transcript-slot"></div>' in BODY
