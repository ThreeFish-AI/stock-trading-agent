"""D8 采样器纯函数：qt 行解析与 lag、新浪/东财解析、标的集回落、会话判定、JSONL 追加。"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "ops"))
import d8_probe

CST = dt.timezone(dt.timedelta(hours=8))

# 31 字段测试行（位序对齐解析面消费位：1=开 3=现价 4=昨收 5=高 6=低 30=时戳）
QT_LINE = "~".join(
    ['v_r_hk00700="100', "318.000", "腾讯控股", "319.000", "318.000", "319.400", "315.600"]
    + [""] * 23
    + ["2026/11/06 16:00:03"]
)


class TestQtParse:
    def test_fields_and_ts(self):
        # 固定本地钟：2026-11-06 16:08:03 CST → lag = 8.0 分钟
        now = dt.datetime(2026, 11, 6, 16, 8, 3, tzinfo=CST)
        parsed = d8_probe.parse_qt_r_hk(QT_LINE, now=now)
        assert parsed is not None
        assert parsed["price"] == 319.0 and parsed["prev_close"] == 318.0
        assert parsed["open"] == 318.0 and parsed["high"] == 319.4 and parsed["low"] == 315.6
        assert parsed["ts_raw"] == "2026/11/06 16:00:03"
        assert parsed["ts_utc8"] == "2026-11-06T16:00:03+08:00"
        assert parsed["lag_min"] == 8.0

    def test_malformed_ts_keeps_raw(self):
        bad = QT_LINE.replace("2026/11/06 16:00:03", "N/A")
        parsed = d8_probe.parse_qt_r_hk(bad)
        assert parsed is not None
        assert parsed["ts_raw"] == "N/A" and parsed["lag_min"] is None

    def test_short_line_none(self):
        assert d8_probe.parse_qt_r_hk('v_r_hk00700="1~2~3"') is None


class TestSinaAndEastmoney:
    def test_sina_price(self):
        body = 'var hq_str_rt_hk00700="腾讯控股,318.000,319.000,319.000,321.000,316.000,...";'
        assert d8_probe.parse_sina_rt_hk(body) == 319.0
        assert d8_probe.parse_sina_rt_hk("garbage") is None

    def test_eastmoney_fields(self):
        parsed = d8_probe.parse_eastmoney(
            {"f43": 319000, "f124": 1790000000, "f19": 318500, "f20": 319200}
        )
        assert parsed is not None
        assert parsed["price"] == 319.0
        assert parsed["ts"] is not None
        assert parsed["bid1"] == 318.5 and parsed["ask1"] == 319.2

    def test_eastmoney_empty(self):
        assert d8_probe.parse_eastmoney({}) is None


class TestUniverseFallback:
    def test_fallback_when_absent(self, tmp_path: Path):
        assert d8_probe.market_codes(tmp_path / "none.json") == ["00700", "00005", "00388"]

    def test_universe_hk_rows(self, tmp_path: Path):
        p = tmp_path / "universe.json"
        p.write_text(
            json.dumps(
                {"rows": [{"code": "700", "market": "hk"}, {"code": "600519", "market": "cn"}]}
            ),
            encoding="utf-8",
        )
        assert d8_probe.market_codes(p) == ["00700"]  # 五位零填充 + 非 hk 剔除


class TestSession:
    def test_states(self):
        sat = dt.datetime(2026, 11, 7, 10, 0, tzinfo=CST)  # 周六
        assert d8_probe.session_state(sat) == "closed"
        assert d8_probe.session_state(dt.datetime(2026, 11, 9, 10, 0, tzinfo=CST)) == "open"
        assert d8_probe.session_state(dt.datetime(2026, 11, 9, 12, 30, tzinfo=CST)) == "lunch"
        assert d8_probe.session_state(dt.datetime(2026, 11, 9, 17, 0, tzinfo=CST)) == "closed"


class TestRunOnceAppend:
    def test_jsonl_appended(self, tmp_path: Path, monkeypatch):
        out = tmp_path / "logs"
        monkeypatch.setattr(d8_probe, "LOG_DIR", str(out))
        monkeypatch.setattr(d8_probe, "UNIVERSE", str(tmp_path / "none.json"))
        monkeypatch.setattr(
            d8_probe,
            "sample_code",
            lambda code: {"code": code, "qt_r_hk": {"err": "offline-test"}},
        )
        now = dt.datetime(2026, 11, 8, 20, 0, tzinfo=CST)
        count, path = d8_probe.run_once(now=now, out_dir=out, universe_path=tmp_path / "none.json")
        assert count == 3
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3 and "d8-samples-20261108" in path
        row = json.loads(lines[0])
        assert row["session"] == "closed" and row["local_ts"].startswith("2026-11-08T20:00")
        d8_probe.run_once(now=now, out_dir=out, universe_path=tmp_path / "none.json")
        assert len(Path(path).read_text(encoding="utf-8").splitlines()) == 6  # append-only
