#!/usr/bin/env python3
"""
backfill.py — 补录 2026-01-01 至今的历史数据
────────────────────────────────────────────────
数据源：
  HIBOR 3M   → hkab.org.hk 历史查询（逐日）
  SOFR 3M    → FRED API  series=SOFR90DAYAVG
  3033/3110  → yfinance  period 覆盖全段
  南向资金   → akshare stock_hsgt_hist_em

写入：
  data/history.json  （与 fetch_data.py 格式一致）
  index.html         （替换 JS 数据块）

依赖：pip install requests akshare yfinance
"""

import os, sys, json, re, requests
import yfinance as yf
import akshare as ak
from datetime import date, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT    = Path(__file__).parent
HISTORY_FILE = REPO_ROOT / "data" / "history.json"
INDEX_HTML   = REPO_ROOT / "index.html"
START_DATE   = date(2026, 1, 1)
TODAY        = date.today()
FRED_KEY     = os.environ.get("FRED_API_KEY", "")

# ────────────────────────────────────────────────────────────────────
# 辅助
# ────────────────────────────────────────────────────────────────────
def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ────────────────────────────────────────────────────────────────────
# ① HIBOR 历史  (东方财富 API，一次拉全段)
#   注：hkab.org.hk 旧 listRates.do 接口已废弃（301 自身循环）
# ────────────────────────────────────────────────────────────────────
def fetch_hibor_all() -> dict:
    """返回 {date_str: rate} dict，交易日有值，非交易日无键。"""
    print("① 抓取 HIBOR 历史（东方财富 API）...")
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_IMP_INTRESTRATEN",
        "columns":    "REPORT_DATE,IR_RATE",
        "filter":     '(MARKET_CODE="005")(CURRENCY_CODE="HKD")(INDICATOR_ID="203")',
        "sortColumns": "REPORT_DATE",
        "sortTypes":  "-1",
        "pageSize":   "500",        # 远超所需，确保一次拿全
        "pageNumber": "1",
        "source":     "WEB",
        "client":     "WEB",
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    rows = r.json()["result"]["data"]
    start_str = START_DATE.isoformat()
    result = {}
    for row in rows:
        ds = row["REPORT_DATE"][:10]
        if ds >= start_str:
            result[ds] = float(row["IR_RATE"])
    print(f"   获取 {len(result)} 条 HIBOR 记录（{start_str} 至今）")
    return result


# ────────────────────────────────────────────────────────────────────
# ② SOFR 历史  (FRED: SOFR90DAYAVG, 一次性拉全段)
# ────────────────────────────────────────────────────────────────────
def fetch_sofr_all() -> dict:
    """返回 {date_str: rate}"""
    print("\n② 抓取 SOFR 历史 (FRED)...")
    if not FRED_KEY:
        print("   FRED_API_KEY 未设置，跳过（后续用填充值）")
        return {}
    params = {
        "series_id":         "SOFR90DAYAVG",
        "api_key":           FRED_KEY,
        "file_type":         "json",
        "observation_start": START_DATE.isoformat(),
        "observation_end":   TODAY.isoformat(),
        "sort_order":        "asc",
    }
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params=params, timeout=20
    )
    r.raise_for_status()
    result = {}
    for obs in r.json().get("observations", []):
        if obs["value"] != ".":
            result[obs["date"]] = float(obs["value"])
    print(f"   获取 {len(result)} 条 SOFR 记录")
    return result


# ────────────────────────────────────────────────────────────────────
# ③ ETF 历史  (yfinance, 一次性)
# ────────────────────────────────────────────────────────────────────
def fetch_etf_all() -> dict:
    """返回 {date_str: {"3033": price, "3110": price}}"""
    print("\n③ 抓取 ETF 历史 (yfinance)...")
    result = {}
    for ticker, key in [("3033.HK", "3033"), ("3110.HK", "3110")]:
        last_err = None
        for attempt in range(3):
            try:
                hist = yf.Ticker(ticker).history(period="6mo")
                break
            except Exception as e:
                last_err = e
                import time; time.sleep(2)
        else:
            raise RuntimeError(f"{ticker} 抓取失败（3次重试）: {last_err}")
        start_str = START_DATE.isoformat()
        for dt, row in hist.iterrows():
            ds = str(dt.date())
            if ds < start_str:
                continue
            if ds not in result:
                result[ds] = {}
            result[ds][key] = round(float(row["Close"]), 4)
        count = sum(1 for v in result.values() if key in v)
        print(f"   {ticker}: {count} 条")
    return result


# ────────────────────────────────────────────────────────────────────
# ④ 南向资金历史  (akshare, 一次性)
# ────────────────────────────────────────────────────────────────────
def fetch_south_all() -> dict:
    """返回 {date_str: net_flow_bn}"""
    print("\n④ 抓取南向资金历史 (akshare)...")
    df = ak.stock_hsgt_hist_em(symbol="南向资金")
    result = {}
    for _, row in df.iterrows():
        ds = str(row.iloc[0])[:10]
        if ds >= START_DATE.isoformat():
            result[ds] = round(float(row.iloc[1]), 2)
    print(f"   获取 {len(result)} 条南向记录")
    return result


# ────────────────────────────────────────────────────────────────────
# 合并 & 写入 history.json
# ────────────────────────────────────────────────────────────────────
def build_and_save(hibor_map, sofr_map, etf_map, south_map) -> list:
    print("\n── 合并数据 ─────────────────────────────────────")

    # SOFR 向前填充
    last_sofr = None
    sofr_filled = {}
    for d in daterange(START_DATE, TODAY):
        ds = str(d)
        if ds in sofr_map:
            last_sofr = sofr_map[ds]
        sofr_filled[ds] = last_sofr

    records = []
    skipped = 0

    for d in daterange(START_DATE, TODAY):
        ds = str(d)
        hibor = hibor_map.get(ds)
        etf   = etf_map.get(ds, {})
        e3033 = etf.get("3033")
        e3110 = etf.get("3110")

        # 必须有 HIBOR 和 ETF 才算交易日
        if hibor is None or e3033 is None or e3110 is None:
            skipped += 1
            continue

        sofr  = sofr_filled.get(ds)
        south = south_map.get(ds, None)
        spread_bp = round((hibor - sofr) * 100, 2) if sofr else None

        rec = {
            "date":    ds,
            "hibor":   hibor,
            "sofr":    sofr,
            "etf3033": e3033,
            "etf3110": e3110,
            "south":   south,
        }
        if spread_bp is not None:
            rec["spread_bp"] = spread_bp

        records.append(rec)

        spread_str = f"{spread_bp:+.1f}bp" if spread_bp is not None else "N/A"
        south_str  = f"{south:+.1f}" if south is not None else "N/A"
        sofr_disp = f"{sofr:.3f}%" if sofr is not None else "N/A"
        print(
            f"  {ds}  HIBOR={hibor:.3f}%  SOFR={sofr_disp}"
            f"  利差={spread_str}  3033={e3033:.3f}  3110={e3110:.3f}"
            f"  南向={south_str}亿"
        )

    print(f"\n  合计 {len(records)} 个交易日，跳过 {skipped} 天（节假日/无数据）")

    # 写入 history.json（追加/合并已有数据）
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if HISTORY_FILE.exists():
        existing = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))

    existing_map = {r["date"]: r for r in existing}
    for rec in records:
        existing_map[rec["date"]] = rec   # 新数据覆盖旧数据

    merged = sorted(existing_map.values(), key=lambda r: r["date"])
    HISTORY_FILE.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"  history.json 写入完成（共 {len(merged)} 条）")
    return merged


# ────────────────────────────────────────────────────────────────────
# 更新 index.html（复用 fetch_data.py 逻辑）
# ────────────────────────────────────────────────────────────────────
def update_index_html(history: list):
    if not INDEX_HTML.exists():
        print(f"  index.html 不存在于 {INDEX_HTML}，跳过")
        return

    rows = history[-60:]
    n    = len(rows)

    last_sofr = 0.0
    sofrs = []
    for r in rows:
        v = r.get("sofr")
        if v:
            last_sofr = v
        sofrs.append(last_sofr)

    dates   = [r["date"]                              for r in rows]
    hibors  = [r["hibor"]                              for r in rows]
    spreads = [round((hibors[i]-sofrs[i])*100, 2)      for i in range(n)]
    souths  = [r.get("south") or 0.0                   for r in rows]
    etf3033 = [r["etf3033"]                            for r in rows]
    etf3110 = [r["etf3110"]                            for r in rows]
    ratios  = [round(etf3033[i]/etf3110[i], 4) if etf3110[i] else 0.0 for i in range(n)]

    today_str = TODAY.isoformat()
    new_block = (
        f"// ── 实时数据（backfill.py 写入 {today_str}）──\n"
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
        print("  index.html 数据块未匹配，跳过")
        return
    INDEX_HTML.write_text(new_html, encoding="utf-8")
    print(f"  index.html 更新完成（最近 {n} 条，最新 {today_str}）")


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'═'*60}")
    print(f"  backfill.py  {START_DATE} → {TODAY}")
    print(f"{'═'*60}\n")

    hibor_map = fetch_hibor_all()
    sofr_map  = fetch_sofr_all()
    etf_map   = fetch_etf_all()
    south_map = fetch_south_all()

    history = build_and_save(hibor_map, sofr_map, etf_map, south_map)
    update_index_html(history)

    print("\n全部完成。请检查上方数据，确认无误后提交到仓库。")
