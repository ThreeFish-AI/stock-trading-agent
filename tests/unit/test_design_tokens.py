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
        for v in re.findall(r"border-radius:\s*([^;}]+)", RULES):
            assert v.strip() == "50%" or re.fullmatch(r"var\(--r-[\w-]+\)", v.strip()), v


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
        assert ".tbl-wrap{max-height:460px;overflow:auto;overscroll-behavior:contain}" in RULES
        assert ".tbl-wrap .tbl thead th{position:sticky;top:0" in RULES  # thead 吸顶

    def test_sys_row_four_tracks(self):
        # 4 子元素（sdot/sys-l/sys-sched/sys-st）配 4 轨；修复前 3 轨致第 4 元素掉隐式行
        assert "grid-template-columns:14px minmax(0,1fr) 56px 56px" in RULES

    def test_action_cell_touch_spacing(self):
        assert ".tbl-act{display:flex;align-items:center;gap:var(--sp-4);flex-wrap:wrap}" in RULES
