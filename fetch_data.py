#!/usr/bin/env python3
"""
fetch_data.py v2
────────────────────────────────────────────────────────────────
抓取：
  ① HIBOR 3M       → 东方财富 API
  ② Term SOFR 3M   → FRED API  (env: FRED_API_KEY)
  ③ 3033.HK / 3110.HK → yfinance
  ④ 南向资金净买入  → akshare (东方财富)

写入：
  · data/history.json  —— 每日一条，最多保留 365 条
  · index.html         —— 替换 JS 数据块（最近 60 条）

依赖：pip install requests akshare yfinance
"""

import os
import sys
import json
import re
import requests
import yfinance as yf
import akshare as ak
from datetime import date
from pathlib import Path

# Windows 终端 UTF-8 兼容
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT      = Path(__file__).parent
HISTORY_FILE   = REPO_ROOT / "data" / "history.json"
INDEX_HTML     = REPO_ROOT / "index.html"
MAX_CHART_DAYS = 60
MAX_HIST_DAYS  = 365

# ────────────────────────────────────────────────────────────────────
# ① HIBOR 3M
# ────────────────────────────────────────────────────────────────────
def fetch_hibor_3m() -> dict:
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_IMP_INTRESTRATEN",
        "columns": "ALL",
        "filter": '(MARKET_CODE="005")(CURRENCY_CODE="HKD")(INDICATOR_ID="203")(LATEST_RECORD="1")',
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "pageSize": "1",
        "pageNumber": "1",
        "source": "WEB",
        "client": "WEB",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    row = r.json()["result"]["data"][0]
    return {
        "date":       row["REPORT_DATE"][:10],
        "rate":       float(row["IR_RATE"]),
        "change_pct": float(row["CHANGE_RATE"]),
    }


# ────────────────────────────────────────────────────────────────────
# ② Term SOFR 3M  (FRED: SOFR90DAYAVG)
#    GitHub Secrets 变量名: FRED_API_KEY
# ────────────────────────────────────────────────────────────────────
def fetch_sofr_3m() -> dict:
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        raise EnvironmentError("FRED_API_KEY not set")
    params = {
        "series_id":  "SOFR90DAYAVG",
        "api_key":    api_key,
        "file_type":  "json",
        "sort_order": "desc",
        "limit":      "5",
    }
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params=params, timeout=15,
    )
    r.raise_for_status()
    for obs in r.json().get("observations", []):
        if obs["value"] != ".":
            return {"date": obs["date"], "rate": float(obs["value"])}
    raise ValueError("FRED 无有效数据")


# ────────────────────────────────────────────────────────────────────
# ③ ETF 收盘价
# ────────────────────────────────────────────────────────────────────
def fetch_etf_prices() -> dict:
    result = {}
    for ticker in ["3033.HK", "3110.HK"]:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            raise ValueError(f"yfinance 空数据: {ticker}")
        result[ticker] = {
            "date":  str(hist.index[-1].date()),
            "close": round(float(hist["Close"].iloc[-1]), 4),
        }
    return result


# ────────────────────────────────────────────────────────────────────
# ④ 南向资金
# ────────────────────────────────────────────────────────────────────
def fetch_southbound() -> dict:
    df = ak.stock_hsgt_hist_em(symbol="南向资金")
    if df.empty:
        raise ValueError("akshare 返回空数据")
    last = df.iloc[-1]
    return {
        "date":        str(last.iloc[0])[:10],
        "net_flow_bn": round(float(last.iloc[1]), 2),   # 亿港元
        "buy_bn":      round(float(last.iloc[2]), 2),
        "sell_bn":     round(float(last.iloc[3]), 2),
    }


# ────────────────────────────────────────────────────────────────────
# history.json
# ────────────────────────────────────────────────────────────────────
def load_history() -> list:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return []


def save_history(history: list, record: dict) -> list:
    today = record["date"]
    idx = next((i for i, r in enumerate(history) if r["date"] == today), None)
    if idx is not None:
        history[idx] = record
        action = "更新"
    else:
        history.append(record)
        action = "追加"

    history.sort(key=lambda r: r["date"])
    history = history[-MAX_HIST_DAYS:]

    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  history.json: {action} {today}（共 {len(history)} 条）")
    return history


# ────────────────────────────────────────────────────────────────────
# index.html —— 替换 JS 数据块
# ────────────────────────────────────────────────────────────────────
def update_index_html(history: list) -> bool:
    if not INDEX_HTML.exists():
        print(f"  WARNING: {INDEX_HTML} 不存在，跳过")
        return False

    rows = history[-MAX_CHART_DAYS:]
    n    = len(rows)

    # SOFR 缺失时向前填充
    last_sofr = 0.0
    sofrs = []
    for r in rows:
        v = r.get("sofr")
        if v is not None:
            last_sofr = v
        sofrs.append(last_sofr)

    dates   = [r["date"]                         for r in rows]
    hibors  = [r["hibor"]                         for r in rows]
    spreads = [round((hibors[i] - sofrs[i]) * 100, 2) for i in range(n)]
    souths  = [r.get("south", 0.0)               for r in rows]
    etf3033 = [r["etf3033"]                       for r in rows]
    etf3110 = [r["etf3110"]                       for r in rows]
    ratios  = [round(etf3033[i] / etf3110[i], 4) if etf3110[i] else 0.0 for i in range(n)]

    today_str = date.today().isoformat()

    new_block = (
        f"// ── 实时数据（fetch_data.py 写入 {today_str}）──\n"
        f"const DATES={json.dumps(dates)};\n"
        f"const HIBOR={json.dumps(hibors)};\n"
        f"const SOFR ={json.dumps(sofrs)};\n"
        f"const SPREAD={json.dumps(spreads)};\n"
        f"const SOUTH ={json.dumps(souths)};\n"
        f"const ETF3033={json.dumps(etf3033)};\n"
        f"const ETF3110={json.dumps(etf3110)};\n"
        f"const RATIO={json.dumps(ratios)};\n\n"
        f"let data={{dates:[...DATES],hibor:[...HIBOR],sofr:[...SOFR],"
        f"spread:[...SPREAD],south:[...SOUTH],etf3033:[...ETF3033],"
        f"etf3110:[...ETF3110],ratio:[...RATIO]}};"
    )

    html = INDEX_HTML.read_text(encoding="utf-8")
    pattern = re.compile(r"const DATES=.*?let data=\{[^;]*\};", re.DOTALL)
    new_html, count = pattern.subn(new_block, html)

    if count == 0:
        print("  WARNING: index.html 数据块未匹配，跳过")
        return False

    INDEX_HTML.write_text(new_html, encoding="utf-8")
    print(f"  index.html: 已写入 {n} 条（最新 {today_str}）")
    return True


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'═' * 58}")
    print(f"  HIBOR · SOFR · ETF · 南向资金   {date.today()}")
    print(f"{'═' * 58}\n")

    today_str = date.today().isoformat()
    record = {"date": today_str}
    errors = []

    # ── ① HIBOR ──────────────────────────────────────────────────────
    print("① HIBOR 3M")
    try:
        h = fetch_hibor_3m()
        record["hibor"] = h["rate"]
        print(f"   {h['rate']:.4f}%  ({h['date']})  变动 {h['change_pct']:+.3f}%  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("HIBOR")

    # ── ② SOFR ───────────────────────────────────────────────────────
    print("\n② Term SOFR 3M (FRED: SOFR90DAYAVG)")
    try:
        s = fetch_sofr_3m()
        record["sofr"] = s["rate"]
        print(f"   {s['rate']:.4f}%  ({s['date']})  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        # 向前填充：用 history.json 最近一条有效 sofr
        hist_tmp = load_history()
        last = next((r["sofr"] for r in reversed(hist_tmp) if r.get("sofr")), None)
        if last:
            record["sofr"] = last
            print(f"   使用上次已知值 {last:.4f}%（填充）")
        else:
            errors.append("SOFR")

    # ── ③ ETF ────────────────────────────────────────────────────────
    print("\n③ ETF 收盘价")
    try:
        etfs = fetch_etf_prices()
        record["etf3033"] = etfs["3033.HK"]["close"]
        record["etf3110"] = etfs["3110.HK"]["close"]
        for tk, v in etfs.items():
            print(f"   {tk}: HK${v['close']:.3f}  ({v['date']})  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("ETF")

    # ── ④ 南向 ───────────────────────────────────────────────────────
    print("\n④ 南向资金")
    try:
        south = fetch_southbound()
        record["south"] = south["net_flow_bn"]
        label = "流入" if south["net_flow_bn"] > 0 else "流出"
        print(f"   净买入 {south['net_flow_bn']:+.2f} 亿港元（{label}）"
              f"  买 {south['buy_bn']:.1f}  卖 {south['sell_bn']:.1f}  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("南向资金")

    # ── 利差 ─────────────────────────────────────────────────────────
    if "hibor" in record and "sofr" in record:
        spread = (record["hibor"] - record["sofr"]) * 100
        record["spread_bp"] = round(spread, 2)
        signal = "港元宽松" if spread < -10 else ("港元偏紧" if spread > 10 else "利差中性")
        print(f"\n  利差 HIBOR−SOFR : {spread:+.1f} bp  →  {signal}")

    if "etf3033" in record and "etf3110" in record:
        ratio = round(record["etf3033"] / record["etf3110"], 4)
        print(f"  3033÷3110 比值  : {ratio:.4f}")

    # ── 必要字段检查 ─────────────────────────────────────────────────
    required = {"hibor", "etf3033", "etf3110"}
    if not required.issubset(record.keys()):
        missing = required - record.keys()
        print(f"\n  ✗ 关键数据缺失 {missing}，不写入，退出")
        sys.exit(1)

    # ── 写入 ─────────────────────────────────────────────────────────
    print("\n── 写入 ──────────────────────────────────────────────────")
    history = load_history()
    history = save_history(history, record)
    update_index_html(history)

    if errors:
        print(f"\n  注意：{', '.join(errors)} 数据获取失败，其余已写入")
    else:
        print("\n  全部完成 ✓")


if __name__ == "__main__":
    main()
