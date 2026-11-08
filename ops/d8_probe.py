#!/usr/bin/env python3
"""D8 港股时戳重探采样器（E-25 vs E-76 定谳证据采集；12-orchestration D8 节）。

stdlib-only（/usr/bin/python3 直跑，零依赖零 vibe-stock import——launchd 无 uv/PATH 依赖）。
每轮对标的集逐股采三通道 + 五档，输出 append-only JSONL：
  ~/.vibe-stock/agent/logs/d8-samples-YYYYMMDD.jsonl
通道面：
  - qt.gtimg r_hk 快照：f[30] 时戳原文（YYYY/MM/DD HH:MM:SS）→ lag_min（E-25 主张面）；
  - 新浪 rt_hk：交叉价（响应体无时戳，如实标注）；
  - 东财 push2：价格 + 自带时戳（E-76「与东财同步」锚）+ 五档原始值（bid1/ask1）。
标的集：~/.vibe-stock/monitor/universe.json 的 hk 行（缺席回落固定集 00700/00005/00388）。
时点纪律：09:36（隔夜陈旧延续面）与 13:06（午休解冻基线）两对照；避开 12:00–13:00
时戳冻结窗；非盘中时段跑则每行如实标 session_closed（通道验证用，不误判定谳）。
判据解读与定谳流程见 docs/ops/trading-day-verify.md。
"""

import datetime as dt
import json
import os
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~/.vibe-stock")
UNIVERSE = os.path.join(HOME, "monitor", "universe.json")
LOG_DIR = os.path.join(HOME, "agent", "logs")
FALLBACK_CODES = ("00700", "00005", "00388")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) d8-probe/0.1"
TIMEOUT_S = 10


def market_codes(universe_path=UNIVERSE):
    """标的集：universe hk 行（code 五位零填充）；缺席/损坏回落固定集。"""
    try:
        with open(universe_path, encoding="utf-8") as f:
            rows = json.load(f).get("rows") or []
        codes = [str(r.get("code", "")).zfill(5) for r in rows if r.get("market") == "hk"]
        return sorted(set(codes)) or list(FALLBACK_CODES)
    except (OSError, ValueError, AttributeError):
        return list(FALLBACK_CODES)


def session_state(now=None):
    """港股时段粗判（无节假日日历，休市由快照日期事后判）：open/lunch/closed。"""
    if now is None:
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    minute = now.hour * 60 + now.minute
    weekday = now.weekday()
    if weekday >= 5 or minute < 9 * 60 + 30 or minute >= 16 * 60:
        return "closed"
    if 12 * 60 <= minute < 13 * 60:
        return "lunch"  # 时戳冻结窗（07:26）：样本保留但定谳不采信本窗
    return "open"


def parse_qt_r_hk(raw_line, now=None):
    """qt r_hk 行 → {ts_raw, price, prev_close, open, high, low, lag_min, ts_utc8}。

    字段位序对齐 vibe-stock watch.py 解析面（f[1]开/f[3]现价/f[4]昨收/f[5]高/f[6]低/
    f[30] 时戳）；lag_min =（本地墙钟 − 行情时戳）/60，一位小数。
    """
    parts = raw_line.split("~")
    if len(parts) < 31:
        return None
    try:
        price = float(parts[3])
        prev_close = float(parts[4])
    except (ValueError, IndexError):
        return None
    ts_raw = parts[30]
    ts_utc8 = None
    lag_min = None
    try:
        stamp = dt.datetime.strptime(ts_raw, "%Y/%m/%d %H:%M:%S").replace(
            tzinfo=dt.timezone(dt.timedelta(hours=8))
        )
        ts_utc8 = stamp.isoformat(timespec="seconds")
        now = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        lag_min = round((now - stamp).total_seconds() / 60, 1)
    except ValueError:
        pass  # 时戳字段形态漂移：保留 ts_raw 原文，lag 如实缺省
    return {
        "ts_raw": ts_raw,
        "ts_utc8": ts_utc8,
        "lag_min": lag_min,
        "price": price,
        "prev_close": prev_close,
        "open": _f(parts, 1),
        "high": _f(parts, 5),
        "low": _f(parts, 6),
    }


def _f(parts, i):
    try:
        return float(parts[i])
    except (ValueError, IndexError):
        return None


def parse_sina_rt_hk(body):
    """新浪 rt_hk 体 → 价格（响应无时戳字段，调用方标注）。"""
    fields = body.split(",")
    if len(fields) < 4:
        return None
    try:
        return float(fields[3])
    except ValueError:
        return None


def parse_eastmoney(fields):
    """东财 push2 字典 → {price, ts, bid1, ask1}（f43 价×1000、f124/f86 时戳秒）。"""
    try:
        price = float(fields["f43"]) / 1000.0
    except (KeyError, TypeError, ValueError):
        return None
    ts = None
    for key in ("f124", "f86"):
        try:
            ts = dt.datetime.fromtimestamp(
                int(fields[key]), dt.timezone(dt.timedelta(hours=8))
            ).isoformat(timespec="seconds")
            break
        except (KeyError, TypeError, ValueError, OSError):
            continue
    return {
        "price": price,
        "ts": ts,
        "bid1": _em_depth(fields, "f19"),
        "ask1": _em_depth(fields, "f20"),
    }


def _em_depth(fields, key):
    try:
        value = float(fields[key])
        return value / 1000.0 if value > 0 else value
    except (KeyError, TypeError, ValueError):
        return None


def fetch_text(url, referer=None):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, **({"Referer": referer} if referer else {})}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode("utf-8", errors="replace")


def sample_code(code):
    """单股一轮三通道采样（通道失败逐项如实记 err）。"""
    row = {"code": code}
    try:
        qt = fetch_text(f"https://qt.gtimg.cn/q=r_hk{code}")
        parsed = parse_qt_r_hk(qt)
        row["qt_r_hk"] = parsed or {"err": "parse", "raw_head": qt[:80]}
    except OSError as exc:
        row["qt_r_hk"] = {"err": str(exc)}
    try:
        body = fetch_text(
            f"https://hq.sinajs.cn/list=rt_hk{code}",
            referer="https://finance.sina.com.cn",
        )
        price = parse_sina_rt_hk(body)
        row["sina_rt_hk"] = (
            {"price": price, "note": "响应无时戳字段"} if price else {"err": "parse"}
        )
    except OSError as exc:
        row["sina_rt_hk"] = {"err": str(exc)}
    try:
        secid = f"116.{code}"
        url = "https://push2.eastmoney.com/api/qt/stock/get?" + urllib.parse.urlencode(
            {"secid": secid, "fields": "f43,f86,f124,f19,f20", "fltt": "2", "invt": "2"}
        )
        payload = json.loads(fetch_text(url))
        fields = payload.get("data") or {}
        row["eastmoney"] = parse_eastmoney(fields) or {"err": "parse"}
    except (OSError, ValueError) as exc:
        row["eastmoney"] = {"err": str(exc)}
    return row


def run_once(now=None, out_dir=LOG_DIR, universe_path=UNIVERSE):
    """一轮采样：全部标的 → JSONL 追加。返回本轮行数。"""
    os.makedirs(out_dir, exist_ok=True)
    now = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    envelope = {"local_ts": now.isoformat(timespec="seconds"), "session": session_state(now)}
    rows = [{**envelope, **sample_code(code)} for code in market_codes(universe_path)]
    path = os.path.join(out_dir, f"d8-samples-{now:%Y%m%d}.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows), path


def main():
    count, path = run_once()
    session = session_state()
    print(f"d8-probe: {count} 只标的已采样（session={session}）→ {path}")


if __name__ == "__main__":
    main()
