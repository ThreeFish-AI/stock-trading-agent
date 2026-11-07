"""红线源码扫描（机器门，CI 可跑零本机依赖）：NR-2 / R2 / R3/R8 代码层可检。

适用条件：无条件全局规则——每次提交跑；判据只收严（新增禁止符号即扩表）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

# NR-2：真实下单接口符号族（券商 SDK / 下单动词）——src/ 全域禁现（注释中的红线陈述除外）
ORDER_INTERFACE_TOKENS = (
    "easytrader",
    "xtquant",
    "miniqmt",
    "vnpy",
    "alpaca",
    "ib_insync",
    "tigeropen",
    "futu",
    "longport",
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "broker_api",
)

# paper/ 禁网络库（NR-2 物理隔离：撮合零网络）
NETWORK_MODULES = (
    "requests",
    "httpx",
    "aiohttp",
    "urllib.request",
    "apprise",
    "websocket",
    "smtplib",
)

# 敏感环境变量名：仅 cc_client/cli.py 的 sanitize_env 剥离位允许出现
SECRET_ENV_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# R3/R8：state/ 不得出现 signals/ledger 写路径符号
FORBIDDEN_STATE_PATHS = ("signals.json", "ledger.json")


def _py_files(*parts: str) -> list[Path]:
    base = SRC.joinpath(*parts)
    return sorted(base.rglob("*.py")) if base.exists() else []


def _code_only(source: str) -> str:
    """剥全部 docstring（AST 纯字符串表达式语句）与注释（tokenize）后的纯代码。

    坑位：注释剥离走 tokenize 而非按 # 切串（字符串字面量内的 # 不受影响）；
    已知限界：变量拼接/非常量 f-string 组装的 token 属静态扫描不可见，由 review 补位。
    """
    import io
    import tokenize

    tree = ast.parse(source)
    lines = source.splitlines()
    # 剥全部纯字符串表达式语句（含首节点 docstring 与任何位置的裸字符串语句）
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for ln in range(node.lineno, node.end_lineno + 1):
                lines[ln - 1] = ""
    # 全量 tokenize 剥注释（多行字符串安全；字符串字面量保留——动态符号常在字符串里）
    text = "\n".join(lines)
    kept = []
    skip = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type not in skip:
            kept.append(tok.string)
    return " ".join(kept)


def _imports_of(path: Path) -> set[str]:
    """静态 import 面 + 动态 import 常量调用（importlib.import_module / __import__）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            if node.module == "urllib":  # from urllib import request → 视同 urllib.request
                mods.update(f"urllib.{alias.name}" for alias in node.names)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute | ast.Name)
            and (
                (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")
                or (isinstance(node.func, ast.Name) and node.func.id == "__import__")
            )
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            mods.add(node.args[0].value)
    return mods


class TestNR2NoOrderInterface:
    def test_no_order_interface_tokens_in_src(self):
        import re

        offenders: list[str] = []
        for path in _py_files():
            code = _code_only(path.read_text(encoding="utf-8"))
            for token in ORDER_INTERFACE_TOKENS:
                if re.search(rf"\b{re.escape(token)}\b", code):
                    offenders.append(f"{path.relative_to(SRC)} 含 {token}")
        assert not offenders, "NR-2 违例（src/ 出现下单接口符号）：\n" + "\n".join(offenders)

    def test_paper_imports_no_network(self):
        offenders: list[str] = []
        for path in _py_files("stock_trading_agent", "paper"):
            mods = _imports_of(path)
            for mod in mods:
                if any(mod == net or mod.startswith(net + ".") for net in NETWORK_MODULES):
                    offenders.append(f"{path.relative_to(SRC)} import {mod}")
        assert not offenders, "NR-2 违例（paper/ 引网络库）：\n" + "\n".join(offenders)

    def test_paper_has_no_dynamic_import_machinery(self):
        # 动态取模块的面（importlib / __import__）在 paper/ 直接禁——静态网络禁令不可被绕过
        offenders: list[str] = []
        for path in _py_files("stock_trading_agent", "paper"):
            code = _code_only(path.read_text(encoding="utf-8"))
            for token in ("importlib", "__import__"):
                if token in code:
                    offenders.append(f"{path.relative_to(SRC)} 出现 {token}")
        assert not offenders, "NR-2 违例（paper/ 出现动态 import 机制）：\n" + "\n".join(offenders)


class TestR2SecretHygiene:
    def test_secret_env_names_only_in_sanitize(self):
        allowed = {
            "stock_trading_agent/cc_client/cli.py"
        }  # 完整相对路径白名单（basename 全域豁免有越界面）
        offenders: list[str] = []
        for path in _py_files("stock_trading_agent"):
            code = _code_only(path.read_text(encoding="utf-8"))
            for name in SECRET_ENV_NAMES:
                if name in code and str(path.relative_to(SRC)) not in allowed:
                    offenders.append(f"{path.relative_to(SRC)} 出现 {name}")
        assert not offenders, "R2 违例（密钥环境变量名越界出现）：\n" + "\n".join(offenders)


class TestR3R8NoSignalsLedgerWrite:
    def test_state_module_has_no_signals_ledger_paths(self):
        offenders: list[str] = []
        for path in _py_files("stock_trading_agent", "state"):
            code = _code_only(path.read_text(encoding="utf-8"))
            for token in FORBIDDEN_STATE_PATHS:
                if token in code:
                    offenders.append(f"{path.relative_to(SRC)} 出现 {token}")
        assert not offenders, "R3/R8 违例（state/ 出现 signals/ledger 写路径符号）：\n" + "\n".join(
            offenders
        )


class TestNoRealHoldingsLeak:
    """公开仓敏感面：敏感标的代码不进 src/tests（测试数据一律合成码/600519 示例码）。

    适用条件：敏感码清单经环境变量 STA_SENSITIVE_CODES 本机注入（逗号分隔，绝不入库——
    在公开仓硬编码敏感清单本身即泄露）；未注入时跳过（CI 天然跳过，本机手跑生效）。
    """

    def test_no_sensitive_codes_in_sources(self):
        import os

        raw = os.environ.get("STA_SENSITIVE_CODES", "")
        if not raw.strip():
            pytest.skip("STA_SENSITIVE_CODES 未注入（本机手跑门禁；清单不入公开仓）")
        tokens = tuple(t.strip() for t in raw.split(",") if t.strip())
        offenders: list[str] = []
        for path in sorted((SRC.parent / "tests").rglob("*.py")) + _py_files():
            code = path.read_text(encoding="utf-8")
            for code_token in tokens:
                if code_token in code:
                    offenders.append(f"{path.name} 出现敏感码")
        assert not offenders, "敏感面违例（敏感标的码入库）：\n" + "\n".join(offenders)
