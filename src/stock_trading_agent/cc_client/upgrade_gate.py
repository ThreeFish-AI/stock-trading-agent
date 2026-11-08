"""升级门禁纯函数层（0.0.26，§② 升级门禁裁定；R-03 缓解主体）。

零网络零子进程：--help 旗标快照解析/diff、CHANGELOG 窗口扫描、verdict 聚合。
通道层（capture_help/probe_enum_reject）在 cli.py；CLI 子命令在 __main__.py。

设计依据（proposal §14 / 调研 F-44/F-10/F-37/D-09/F-30）：
- --help 描述换行随终端宽度漂移——解析按「2 空格缩进 + `--` 开头」锚定旗标行，
  只比旗标名/占位/choices，不比描述原文（help_sha256 仅审计指纹不作判据）；
- F-30：--permission-mode 隐藏别名 --help 不可见——旗标面回归须配非法枚举探针
  （通道层 probe_enum_reject，FAIL 级）；
- 判据只收严：diff 分级只能升不能降，改动走新裁定。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Severity = Literal["fail", "warn", "info"]

# CHANGELOG 窗口扫描关键词族（F-37；大小写不敏感）
CHANGELOG_KEYWORDS: tuple[str, ...] = (
    "breaking",
    "removed",
    "deprecat",
    "renamed",
    "flag",
    "headless",
    "stream-json",
    "permission",
    "budget",
    "schema",
    "session",
    "--bare",
)

# 描述文本中的 R-03 头号观察项（--bare 化语义关键词）
_BARE_HINT = "--bare"

_CHOICES_RE = re.compile(r"\(choices:\s*([^)]*)\)")
_VERSION_HEADING_RE = re.compile(r"^#{1,3}\s*\[?v?(\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class FlagSpec:
    """单旗标形态：名/别名/值占位/choices（从描述 (choices: ...) 提取）。"""

    name: str
    aliases: tuple[str, ...] = ()
    value: str | None = None
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    """一次 --help + 版本探测的基线快照（serialize_snapshot 稳定序列化入库）。"""

    cli_version: str
    captured_at: str
    flags: dict[str, FlagSpec] = field(default_factory=dict)
    commands: tuple[str, ...] = ()
    capabilities: dict[str, object] | None = None  # None=未采（deep 花钱，首基线可缺）
    help_sha256: str = ""


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    detail: str


@dataclass(frozen=True)
class GateVerdict:
    verdict: Literal["pass", "warn", "fail"]
    findings: tuple[Finding, ...]


def parse_help_flags(help_text: str) -> dict[str, FlagSpec]:
    """commander 风格 --help → 旗标表。

    锚定：旗标行 = 行首恰 2 空格 + `--` 开头；值占位与别名同行（空格分隔、别名带尾逗号）；
    描述文本 = 旗标行 2+ 空格后的余文 + 后续一切缩进 ≥4 的续行（正常续行对齐描述列 ~40、
    旗标面过长换行后续行缩进 6——统一吃，只用于提取 choices，不作 diff 判据）；
    choices 形如 `(choices: "a", "b")`（可跨行，先拼整段再匹配）。
    坑位：隐藏旗标（如 --max-turns）--help 不可见属预期，diff 只比基线可见面（见 diff_snapshots）。
    """
    flags: dict[str, FlagSpec] = {}
    lines = help_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not (line.startswith("  --") and not line.startswith("   ")):
            i += 1
            continue
        m = re.match(r"^  (\S.*?)(?:\s{2,}(\S.*))?$", line.rstrip())
        if not m:
            i += 1
            continue
        spec_text = m.group(1)
        desc_parts = [m.group(2) or ""]
        j = i + 1
        while j < len(lines):
            cont = lines[j]
            indent = len(cont) - len(cont.lstrip())
            if not cont.strip() or indent < 4:
                break  # 空行/新旗标行（恰 2 缩进）/出 Options 区
            desc_parts.append(cont.strip())
            j += 1
        tokens = spec_text.split()
        names = [t.rstrip(",") for t in tokens if t.startswith("--")]
        values = [t for t in tokens if not t.startswith("--")]
        if not names:
            i = j
            continue
        desc = " ".join(p for p in desc_parts if p)
        choices: tuple[str, ...] = ()
        cm = _CHOICES_RE.search(desc)
        if cm:
            choices = tuple(
                re.findall(r'"([^"]+)"', cm.group(1))
                or re.findall(r"\b[a-zA-Z][\w.-]*\b", cm.group(1))
            )
        flags[names[0]] = FlagSpec(
            name=names[0],
            aliases=tuple(names[1:]),
            value=values[0] if values else None,
            choices=choices,
        )
        i = j
    return flags


def parse_help_commands(help_text: str) -> tuple[str, ...]:
    """Commands: 区子命令名（恰 2 空格缩进行首 token；≥4 缩进描述续行跳过不续读）。"""
    out: list[str] = []
    in_commands = False
    for line in help_text.splitlines():
        if re.match(r"^Commands:\s*$", line):
            in_commands = True
            continue
        if not in_commands:
            continue
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break  # 出 Commands 区
        if indent == 2:
            token = line.strip().split()[0]
            if not token.startswith("--") and not token.startswith("<"):
                out.append(token.rstrip(","))
    return tuple(dict.fromkeys(out))


def watch_flags() -> frozenset[str]:
    """在用旗标面：从 build_argv（本仓真实调用面）派生，非手抄清单

    与 argv_snapshot selftest 同源。
    """
    import sys

    sys_path0 = str(Path(__file__).resolve().parents[2])
    if sys_path0 not in sys.path:
        sys.path.insert(0, sys_path0)
    from stock_trading_agent.cc_client.cli import build_argv
    from stock_trading_agent.cc_client.contract import CCRequest

    req = CCRequest(
        task_id="upgrade-gate",
        session_uuid="00000000-0000-4000-8000-0000000000fe",
        prompt="/vibe-stock 600519",
        schema=None,
        budget_usd=1.0,
        max_turns=10,
        allowed_tools=("Read",),
    )
    argv = build_argv(req)
    return frozenset(t for t in argv if t.startswith("--"))


def diff_snapshots(base: Snapshot, cur: Snapshot) -> list[Finding]:
    """基线 vs 现行旗标面 diff（分级判据只收严，见 §② 裁定表）。

    坑位（F-30 同类）：隐藏旗标 --help 不可见（如 --max-turns 在用但缺席）——diff 面
    只比「基线可见旗标」；基线也缺席的在用旗标属隐藏态，登记 info（由真实通道测试与
    非法枚举探针兜底），基线可见而现行消失才升 fail。
    """
    findings: list[Finding] = []
    watched = watch_flags()
    base_flags, cur_flags = base.flags, cur.flags
    base_names = set(base_flags)
    cur_names = set(cur_flags)
    for name in sorted(watched - base_names - cur_names):
        findings.append(Finding("watch_flag_hidden", "info", f"在用旗标对 --help 不可见：{name}"))
    for name in sorted(base_names - cur_names):
        if name in watched:
            findings.append(Finding("watch_flag_removed", "fail", f"在用旗标消失：{name}"))
        else:
            findings.append(Finding("flag_removed", "warn", f"非在用旗标消失：{name}"))
    for name in sorted(cur_names - base_names):
        findings.append(Finding("flag_added", "info", f"新增旗标：{name}"))

    # 改名启发：消失+新增且去尾段相似（--foo-bar vs --foo-baz）
    def _stem(name: str) -> str:
        return re.sub(r"\d+$", "", name.lstrip("-").rsplit("-", 1)[0])

    removed = sorted(base_names - cur_names)
    added = sorted(cur_names - base_names)
    for gone in removed:
        for new in added:
            if new != gone and _stem(gone) == _stem(new) and len(_stem(gone)) > 3:
                findings.append(Finding("flag_renamed", "warn", f"疑似改名：{gone} → {new}"))
    # 在用旗标 choices 收窄（枚举值被删）
    for name in sorted(base_names & cur_names):
        if name not in watched:
            continue
        b, c = base_flags[name], cur_flags[name]
        lost = set(b.choices) - set(c.choices)
        if b.choices and lost:
            findings.append(
                Finding("watch_choice_removed", "fail", f"{name} 枚举收窄，丢失：{sorted(lost)}")
            )
    # R-03 头号观察项承载：--help 全文指纹变化提示人工核对（含 --bare 化语义变化）
    if base.help_sha256 and base.help_sha256 != cur.help_sha256:
        findings.append(
            Finding(
                "help_text_changed",
                "info",
                "--help 全文指纹变化（人工核对新增/语义变化，重点 --bare）",
            )
        )
    # capabilities（deep 采到才有可比性）
    if base.capabilities is not None and cur.capabilities is not None:
        lost_caps = set(base.capabilities) - set(cur.capabilities)
        if lost_caps:
            findings.append(
                Finding("capability_removed", "warn", f"capabilities 键消失：{sorted(lost_caps)}")
            )
    elif base.capabilities is not None and cur.capabilities is None:
        findings.append(
            Finding("capability_not_probed", "info", "现行未采 capabilities（--deep 可补）")
        )
    return findings


def scan_changelog(text: str, from_ver: str, to_ver: str) -> list[Finding]:
    """CHANGELOG 窗口 [to_ver .. from_ver) 行抽取 + 关键词扫描（WARN 级；纯文本进）。"""
    findings: list[Finding] = []
    window: list[str] = []
    in_window = False
    for line in text.splitlines():
        m = _VERSION_HEADING_RE.match(line.strip())
        if m:
            ver = m.group(1)
            in_window = _ver_lte(to_ver, ver) and not _ver_lte(from_ver, ver)
            continue
        if in_window:
            window.append(line)
    blob = "\n".join(window)
    for kw in CHANGELOG_KEYWORDS:
        if kw.lower() in blob.lower():
            findings.append(
                Finding(
                    "changelog_keyword", "warn", f"窗口 ({from_ver}..{to_ver}] 命中关键词：{kw}"
                )
            )
    if not window:
        findings.append(
            Finding(
                "changelog_window_empty", "warn", f"窗口 ({from_ver}..{to_ver}] 未取到行——人工确认"
            )
        )
    return findings


def _ver_lte(a: str, b: str) -> bool:
    """语义版本比较辅助：b <= a 按三元组。坏串恒 False（宁空窗人工确认）。"""
    try:
        av = tuple(int(x) for x in a.split("."))
        bv = tuple(int(x) for x in b.split("."))
    except ValueError:
        return False
    return bv <= av


def serialize_snapshot(snap: Snapshot) -> str:
    """稳定序列化（sort_keys+缩进，入库可 diff）。"""

    def _flags() -> dict:
        return {
            name: {
                "aliases": list(f.aliases),
                "value": f.value,
                "choices": list(f.choices),
            }
            for name, f in sorted(snap.flags.items())
        }

    return json.dumps(
        {
            "cli_version": snap.cli_version,
            "captured_at": snap.captured_at,
            "flags": _flags(),
            "commands": list(snap.commands),
            "capabilities": snap.capabilities,
            "help_sha256": snap.help_sha256,
        },
        ensure_ascii=False,
        indent=1,
        sort_keys=True,
    )


def load_snapshot(path: Path) -> Snapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    flags = {
        name: FlagSpec(
            name=name,
            aliases=tuple(f.get("aliases", [])),
            value=f.get("value"),
            choices=tuple(f.get("choices", [])),
        )
        for name, f in (raw.get("flags") or {}).items()
    }
    return Snapshot(
        cli_version=raw["cli_version"],
        captured_at=raw.get("captured_at", ""),
        flags=flags,
        commands=tuple(raw.get("commands", [])),
        capabilities=raw.get("capabilities"),
        help_sha256=raw.get("help_sha256", ""),
    )


def snapshot_from_capture(
    cli_version: str, captured_at: str, help_text: str, *, capabilities: dict | None = None
) -> Snapshot:
    return Snapshot(
        cli_version=cli_version,
        captured_at=captured_at,
        flags=parse_help_flags(help_text),
        commands=parse_help_commands(help_text),
        capabilities=capabilities,
        help_sha256=hashlib.sha256(help_text.encode("utf-8")).hexdigest(),
    )


def aggregate(findings: list[Finding]) -> GateVerdict:
    """fail > warn > pass；--ack-warn 登记语义由 CLI 层承载。"""
    if any(f.severity == "fail" for f in findings):
        return GateVerdict("fail", tuple(findings))
    if any(f.severity == "warn" for f in findings):
        return GateVerdict("warn", tuple(findings))
    return GateVerdict("pass", tuple(findings))
