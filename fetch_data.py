#!/usr/bin/env python3
"""
fetch_data.py — HIBOR · SOFR · ETF · 南向资金 数据抓取
────────────────────────────────────────────────────────
数据源：
  ① HIBOR 3M       → 东方财富 API (MARKET_CODE=005, HKD, 3M)
  ② Term SOFR 3M   → FRED API  series=SOFR90DAYAVG
                      key 来自环境变量 FRED_API_KEY (GitHub Secrets)
  ③ 3033.HK / 3110.HK → yfinance
  ④ 南向资金净买入  → akshare stock_hsgt_hist_em(symbol='南向资金')

依赖：pip install requests akshare yfinance
"""

import os
import sys
import json
import requests

# Windows 终端 UTF-8 兼容
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import yfinance as yf
import akshare as ak
from datetime import date

# ────────────────────────────────────────────────────────────────────
# ① HIBOR 3M  (东方财富，MARKET_CODE=005, INDICATOR_ID=203)
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
    d = r.json()
    row = d["result"]["data"][0]
    return {
        "date": row["REPORT_DATE"][:10],
        "rate": float(row["IR_RATE"]),      # 百分比，e.g. 2.30113
        "change_pct": float(row["CHANGE_RATE"]),
    }


# ────────────────────────────────────────────────────────────────────
# ② Term SOFR 3M  (FRED, series=SOFR90DAYAVG)
#    GitHub Secrets 变量名: FRED_API_KEY
# ────────────────────────────────────────────────────────────────────
def fetch_sofr_3m() -> dict:
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        raise EnvironmentError("FRED_API_KEY not set — add it to GitHub Secrets")
    params = {
        "series_id": "SOFR90DAYAVG",   # 90-day compounded avg，最接近 Term SOFR 3M
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": "5",
    }
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params=params, timeout=15
    )
    r.raise_for_status()
    obs = r.json().get("observations", [])
    for o in obs:
        if o["value"] != ".":
            return {
                "date": o["date"],
                "rate": float(o["value"]),  # 百分比
            }
    raise ValueError("FRED: no valid SOFR90DAYAVG observation found")


# ────────────────────────────────────────────────────────────────────
# ③ ETF 收盘价  (yfinance)
# ────────────────────────────────────────────────────────────────────
def fetch_etf_prices() -> dict:
    result = {}
    for ticker in ["3033.HK", "3110.HK"]:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            raise ValueError(f"yfinance 返回空数据: {ticker}")
        result[ticker] = {
            "date": str(hist.index[-1].date()),
            "close": round(float(hist["Close"].iloc[-1]), 4),
        }
    return result


# ────────────────────────────────────────────────────────────────────
# ④ 南向资金净买入  (akshare → 东方财富)
# ────────────────────────────────────────────────────────────────────
def fetch_southbound() -> dict:
    df = ak.stock_hsgt_hist_em(symbol="南向资金")
    if df.empty:
        raise ValueError("akshare 南向资金返回空数据")
    last = df.iloc[-1]
    trade_date = str(last.iloc[0])[:10]
    net_flow = round(float(last.iloc[1]), 2)   # 亿港元，负=净流出
    buy_total = round(float(last.iloc[2]), 2)   # 买入成交额
    sell_total = round(float(last.iloc[3]), 2)  # 卖出成交额
    return {
        "date": trade_date,
        "net_flow_bn": net_flow,
        "buy_bn": buy_total,
        "sell_bn": sell_total,
    }


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'═'*58}")
    print(f"  HIBOR · SOFR · ETF · 南向资金   {date.today()}")
    print(f"{'═'*58}\n")

    results = {}

    # ── ① HIBOR ──────────────────────────────────────────────────────
    print("① HIBOR 3M (东方财富)")
    try:
        hibor = fetch_hibor_3m()
        results["hibor"] = hibor
        print(f"   率值 : {hibor['rate']:.4f}%")
        print(f"   日期 : {hibor['date']}")
        print(f"   变动 : {hibor['change_pct']:+.3f}%")
        print("   状态 : ✓\n")
    except Exception as e:
        print(f"   ERROR: {e}\n")
        results["hibor"] = None

    # ── ② SOFR ───────────────────────────────────────────────────────
    print("② Term SOFR 3M (FRED: SOFR90DAYAVG)")
    try:
        sofr = fetch_sofr_3m()
        results["sofr"] = sofr
        print(f"   率值 : {sofr['rate']:.4f}%")
        print(f"   日期 : {sofr['date']}")
        print("   状态 : ✓\n")
    except Exception as e:
        print(f"   ERROR: {e}\n")
        results["sofr"] = None

    # ── ③ ETF ────────────────────────────────────────────────────────
    print("③ ETF 收盘价 (yfinance)")
    try:
        etfs = fetch_etf_prices()
        results["etf"] = etfs
        for tk, v in etfs.items():
            print(f"   {tk} : HK${v['close']:.3f}  ({v['date']})")
        print("   状态 : ✓\n")
    except Exception as e:
        print(f"   ERROR: {e}\n")
        results["etf"] = None

    # ── ④ 南向 ───────────────────────────────────────────────────────
    print("④ 南向资金 (akshare → 东方财富)")
    try:
        south = fetch_southbound()
        results["southbound"] = south
        flow_label = f"{south['net_flow_bn']:+.2f} 亿港元"
        print(f"   净买入 : {flow_label}  ({'流入' if south['net_flow_bn'] > 0 else '流出'})")
        print(f"   买入额 : {south['buy_bn']:.2f} 亿")
        print(f"   卖出额 : {south['sell_bn']:.2f} 亿")
        print(f"   日期   : {south['date']}")
        print("   状态   : ✓\n")
    except Exception as e:
        print(f"   ERROR: {e}\n")
        results["southbound"] = None

    # ── 衍生计算 ─────────────────────────────────────────────────────
    h = results.get("hibor")
    s = results.get("sofr")
    if h and s:
        spread_bp = (h["rate"] - s["rate"]) * 100
        print(f"{'─'*40}")
        print(f"  利差 HIBOR−SOFR : {spread_bp:+.1f} bp")
        if spread_bp < -10:
            print("  信号            : ↓ 港元明显宽松")
        elif spread_bp > 10:
            print("  信号            : ↑ 港元偏紧")
        else:
            print("  信号            : → 利差中性")
        print(f"{'─'*40}")

    etf = results.get("etf")
    if etf and "3033.HK" in etf and "3110.HK" in etf:
        ratio = etf["3033.HK"]["close"] / etf["3110.HK"]["close"]
        print(f"  3033÷3110 比值  : {ratio:.4f}")

    print()
    return results


if __name__ == "__main__":
    main()
