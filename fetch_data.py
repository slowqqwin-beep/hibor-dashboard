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
import io
import csv
import zipfile
import requests
import yfinance as yf
import akshare as ak
from datetime import date, datetime, timedelta
from pathlib import Path

# WTI期货月份代码 (CME/NYMEX)
_FUT_MONTHS = {1:'F',2:'G',3:'H',4:'J',5:'K',6:'M',
               7:'N',8:'Q',9:'U',10:'V',11:'X',12:'Z'}

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
# ③ 美元流动性底层指标（原 fetch_liquidity.py）
#    ON RRP / WRESBAL / TGA / overnight SOFR / IORB / EFFR / DW / SRF
# ────────────────────────────────────────────────────────────────────
def fetch_liq_bundle() -> dict:
    """
    字段说明（修复后）：
      reserves_b   B    银行准备金 WRESBAL（十亿美元，原始单位）
      reserves     T    同上 ÷1000（兼容旧字段，保留向后兼容）
      dw           B    贴现窗口 DPCREDIT
      sofr_iorb_bp bp   (sofr_on - iorb)×100
    """
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        raise EnvironmentError("FRED_API_KEY not set")
    result = {}
 
    def _get(sid, divisor=1):
        params = {"series_id": sid, "api_key": api_key, "file_type": "json",
                  "sort_order": "desc", "limit": "5"}
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params=params, timeout=15)
        r.raise_for_status()
        for o in r.json().get("observations", []):
            if o["value"] != ".":
                return round(float(o["value"]) / divisor, 3)
        return None
 
    # ON RRP（B）
    v = _get("RRPONTSYD");      result["onrrp"]      = v
    # ── BUG 1 FIX ──────────────────────────────────────────────────
    # WRESBAL 原始单位是 B（十亿美元），不需要除以1000
    # 旧代码: _get("WRESBAL", 1000) 得到的是 T，显示 "2.994T" 正确
    #         但 HTML JS 里 kpi-sub 写的是"万亿美元"，数值也带 T 后缀
    #         导致视觉上出现 "2993.955T"（其实是拼接 .toFixed(3)+'T'）
    # 新做法: 存两个字段
    #   reserves_b = 原始 B 值（约 2994）→ HTML 显示 "2,994 B"
    #   reserves   = 换算 T 值（约 2.994）→ 图表用（保持向后兼容）
    wres_b = _get("WRESBAL");          result["reserves_b"] = wres_b     # B
    if wres_b is not None:
        result["reserves"] = round(wres_b / 1000, 3)                    # T（兼容）
    # ───────────────────────────────────────────────────────────────
 
    # TGA（WTREGEN 单位 M → B）
    try:
        params = {"series_id": "WTREGEN", "api_key": api_key, "file_type": "json",
                  "sort_order": "desc", "limit": "5"}
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params=params, timeout=15)
        r.raise_for_status()
        obs = [o for o in r.json().get("observations", []) if o["value"] != "."]
        if obs:
            result["tga"] = round(float(obs[0]["value"]) / 1000, 3)  # M→B
            result["tga_wow"] = (round(result["tga"] - float(obs[1]["value"]) / 1000, 3)
                                 if len(obs) >= 2 else 0.0)
    except Exception as e:
        print(f"   TGA ERROR: {e}")
 
    v = _get("SOFR");            result["sofr_on"]    = v
    v = _get("IORB");            result["iorb"]       = v
    v = _get("FEDFUNDS");        result["effr"]       = v
    v = _get("DPCREDIT");        result["dw"]         = v   # 已是 B
    result["srf"] = _fetch_srf()
    v = _get("SOFR30DAYAVG");    result["sofr_1m"]    = v
    v = _get("SOFR180DAYAVG");   result["sofr_6m"]    = v
 
    if result.get("sofr_on") is not None and result.get("iorb") is not None:
        result["sofr_iorb_bp"] = round((result["sofr_on"] - result["iorb"]) * 100, 1)
    if result.get("sofr_on") is not None and result.get("effr") is not None:
        result["sofr_effr_bp"] = round((result["sofr_on"] - result["effr"]) * 100, 1)
 
    return {k: v for k, v in result.items() if v is not None}
 
 
def _fetch_srf() -> float:
    try:
        from bs4 import BeautifulSoup
        r = requests.get(
            "https://www.federalreserve.gov/releases/h41/current/h41.htm",
            timeout=25, headers={"User-Agent": "Mozilla/5.0 (compatible)"}
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            label = cells[0].get_text(" ", strip=True).lower()
            if "standing" in label and "repo" in label:
                for c in cells[1:]:
                    txt = c.get_text(strip=True).replace(",", "").replace("\xa0", "")
                    if txt and txt not in ("-", "n.a.", "ND"):
                        try:
                            return round(float(txt) / 1000, 3)
                        except ValueError:
                            continue
        return 0.0
    except Exception:
        return 0.0


# ────────────────────────────────────────────────────────────────────
# ④ ETF 收盘价
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
# ETF 历史回填（history 条数不足 60 时自动执行）
# ────────────────────────────────────────────────────────────────────
def backfill_etf_history(history: list) -> list:
    """
    用 yfinance 拉取 3033.HK / 3110.HK 近 3 个月日收盘价，
    回填到 history 中已存在的日期（补充缺失字段），
    以及创建尚未存在的日期记录。
    仅在 history < 60 条时执行（避免每日重复抓取）。
    """
    if len(history) >= 60:
        return history

    print("\n── ETF 历史回填 (3mo) ─────────────────────────────────")
    etf_rows: dict = {}   # {date_str: {"etf3033": float, "etf3110": float}}
    for ticker in ["3033.HK", "3110.HK"]:
        field = "etf3033" if "3033" in ticker else "etf3110"
        try:
            h = yf.Ticker(ticker).history(period="3mo")
            if h.empty:
                h = yf.download(ticker, start="2026-01-01", progress=False,
                                auto_adjust=True)
                # yf.download 可能返回 MultiIndex 列
                if not h.empty and hasattr(h.columns, "levels"):
                    h.columns = h.columns.droplevel(1)
            if h.empty:
                print(f"   {ticker}: 无数据，跳过")
                continue
            for dt, row in h.iterrows():
                d = str(dt.date())
                etf_rows.setdefault(d, {})[field] = round(float(row["Close"]), 4)
            print(f"   {ticker}: 取得 {len(h)} 条  ✓")
        except Exception as e:
            print(f"   {ticker}: ERROR {e}")

    existing = {r["date"]: i for i, r in enumerate(history)}
    added, updated = 0, 0
    for d, fields in sorted(etf_rows.items()):
        if d in existing:
            rec = history[existing[d]]
            for k, v in fields.items():
                if k not in rec:          # 只补缺失字段，不覆盖已有数据
                    rec[k] = v
                    updated += 1
        elif len(fields) == 2:            # 两个ETF都有数据才建新记录
            history.append({"date": d, **fields})
            added += 1

    history.sort(key=lambda r: r["date"])
    history = history[-MAX_HIST_DAYS:]
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  ETF 回填：新增 {added} 条 / 补字段 {updated} 处，共 {len(history)} 条")
    return history


# ────────────────────────────────────────────────────────────────────
# 收益率曲线历史回填（首次运行或缺数据时自动执行）
# ────────────────────────────────────────────────────────────────────
def backfill_yields_history(history: list) -> list:
    """
    用 FRED API 拉取近 90 天的 DGS2/DGS10/T10Y2Y/T10Y3M/T10YIE
    以及月频 IRLTLT01CNM156N（中国10Y），填充到 history 中。
    仅在 us_2y 字段不足 60 条时触发（幂等）。
    """
    has_us2y    = sum(1 for r in history if r.get("us_2y") is not None)
    has_cnus    = sum(1 for r in history if r.get("cnus_fred") is not None)
    has_iorb    = sum(1 for r in history if r.get("iorb") is not None)
    has_reserves = sum(1 for r in history if r.get("reserves") is not None)
    if has_us2y >= 60 and has_cnus >= 60 and has_iorb >= 60 and has_reserves >= 60:
        return history

    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        print("  backfill_yields: FRED_API_KEY 未设置，跳过")
        return history

    print("\n── 收益率曲线历史回填 (90d) ─────────────────────────────")

    # 日频序列（收益率曲线 + 流动性）
    daily_map = {
        "us_2y":        "DGS2",
        "us_10y":       "DGS10",
        "spread_2y10y": "T10Y2Y",
        "spread_3m10y": "T10Y3M",
        "bei_10y":      "T10YIE",
        "sofr_on":      "SOFR",
        "sofr":         "SOFR90DAYAVG",   # Term SOFR 3M
        "sofr_1m":      "SOFR30DAYAVG",   # Term SOFR 1M
        "sofr_6m":      "SOFR180DAYAVG",  # Term SOFR 6M
        "iorb":         "IORB",
        "effr":         "FEDFUNDS",
        "onrrp":        "RRPONTSYD",
    }
    all_obs: dict = {}   # {date: {field: value}}
    for field, sid in daily_map.items():
        try:
            obs = _fred_series_90d(sid, api_key)
            for d, v in obs.items():
                all_obs.setdefault(d, {})[field] = v
            print(f"   {sid}: {len(obs)} 条")
        except Exception as e:
            print(f"   {sid} ERROR: {e}")

    # 周频序列（需 forward-fill）
    weekly_map = {
        "reserves": ("WRESBAL",  1000),   # M → T
        "dw":       ("DPCREDIT", 1),      # 已是 B
    }
    weekly_raw: dict = {}   # {field: {date: value}}
    for field, (sid, div) in weekly_map.items():
        try:
            obs = _fred_series_90d(sid, api_key, days=120)
            weekly_raw[field] = {d: round(v / div, 3) for d, v in obs.items()}
            print(f"   {sid}: {len(obs)} 条（周频）")
        except Exception as e:
            print(f"   {sid} ERROR: {e}")

    # 月频：IRLTLT01CNM156N（中国10Y）
    cn10y_dict: dict = {}
    try:
        cn10y_dict = _fred_series_90d("IRLTLT01CNM156N", api_key, days=180)
        print(f"   IRLTLT01CNM156N: {len(cn10y_dict)} 条（月频）")
    except Exception as e:
        print(f"   IRLTLT01CNM156N ERROR: {e}")

    # 所有日期轴（合并 history 日期 + 抓取日期）
    all_dates = sorted(set(list(all_obs.keys()) + [r["date"] for r in history]))

    def _ffill_dict(raw: dict, dates: list) -> dict:
        out, last = {}, None
        for d in dates:
            if d in raw:
                last = raw[d]
            out[d] = last
        return out

    # Forward-fill 月频 CN10Y
    cn10y_ff = _ffill_dict(cn10y_dict, all_dates)
    # Forward-fill 周频序列
    weekly_ff = {field: _ffill_dict(raw, all_dates) for field, raw in weekly_raw.items()}

    # 把衍生字段写入 all_obs
    for d in all_dates:
        if d not in all_obs:
            all_obs[d] = {}
        # 周频 forward-fill
        for field, ff in weekly_ff.items():
            if ff.get(d) is not None and all_obs[d].get(field) is None:
                all_obs[d][field] = ff[d]
        # CN10Y + CNUS_FRED
        us10y = all_obs[d].get("us_10y")
        cn = cn10y_ff.get(d)
        if us10y is not None and cn is not None:
            all_obs[d]["cn10y_fred"] = round(cn, 3)
            all_obs[d]["cnus_fred"]  = round(us10y - cn, 3)
        # SOFR-IORB bp
        sofr_on = all_obs[d].get("sofr_on")
        iorb    = all_obs[d].get("iorb")
        effr    = all_obs[d].get("effr")
        if sofr_on is not None and iorb is not None:
            all_obs[d]["sofr_iorb_bp"] = round((sofr_on - iorb) * 100, 1)
        # SOFR-EFFR bp
        if sofr_on is not None and effr is not None:
            all_obs[d]["sofr_effr_bp"] = round((sofr_on - effr) * 100, 1)
        # SOFR 曲线形态（1x3 − 3x6）
        s1m = all_obs[d].get("sofr_1m")
        s3m = all_obs[d].get("sofr")
        s6m = all_obs[d].get("sofr_6m")
        if s1m is not None and s3m is not None:
            all_obs[d]["sofr_fwd1x3"] = round((s3m - s1m) * 100, 1)
        if s6m is not None and s3m is not None:
            all_obs[d]["sofr_fwd3x6"] = round((s6m - s3m) * 100, 1)
        f1x3 = all_obs[d].get("sofr_fwd1x3")
        f3x6 = all_obs[d].get("sofr_fwd3x6")
        if f1x3 is not None and f3x6 is not None:
            all_obs[d]["sofr_curve_shape"] = round(f1x3 - f3x6, 1)

    # 合并进 history（只补缺失字段，不覆盖已有值）
    existing = {r["date"]: i for i, r in enumerate(history)}
    updated = 0
    for d, fields in sorted(all_obs.items()):
        if d in existing:
            rec = history[existing[d]]
            for k, v in fields.items():
                if rec.get(k) is None:
                    rec[k] = v
                    updated += 1

    history.sort(key=lambda r: r["date"])
    history = history[-MAX_HIST_DAYS:]
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  收益率回填完成：补字段 {updated} 处，共 {len(history)} 条")
    return history


# ────────────────────────────────────────────────────────────────────
# ④ 南向资金
# ────────────────────────────────────────────────────────────────────
# ⑤ 中美利差 10Y  (CN via akshare, US via FRED DGS10)
# ────────────────────────────────────────────────────────────────────
def fetch_cn_us_spread() -> dict:
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        raise EnvironmentError("FRED_API_KEY not set")
 
    params = {"series_id": "DGS10", "api_key": api_key, "file_type": "json",
              "sort_order": "desc", "limit": "5"}
    r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                     params=params, timeout=15)
    r.raise_for_status()
    us10y = None
    for obs in r.json().get("observations", []):
        if obs["value"] != ".":
            us10y = float(obs["value"])
            break
    if us10y is None:
        raise ValueError("FRED DGS10 无有效数据")
 
    start = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    df = ak.bond_zh_us_rate(start_date=start)
    cn_col = next((c for c in df.columns if "中国" in c and "10年" in c), None)
    if cn_col is None:
        raise ValueError("akshare bond_zh_us_rate 无中国10年列")
    df = df.dropna(subset=[cn_col])
    if df.empty:
        raise ValueError("akshare CN 10Y 无有效数据")
    cn10y = float(df.iloc[-1][cn_col])
 
    # ── BUG 2 FIX：统一为 US - CN（正值表示美债溢价）──
    return {
        "cn10y":              cn10y,
        "us10y":              us10y,
        "cnus_bp":            round((us10y - cn10y) * 100, 2),   # 正值=美债高于中债
        "spread_us_minus_cn": round(us10y - cn10y, 3),           # % 格式
    }


# ────────────────────────────────────────────────────────────────────
# ⑥ CFTC 原油投机净多仓（Managed Money，Legacy COT）
# ────────────────────────────────────────────────────────────────────
def fetch_cftc_crude() -> dict:
    year = date.today().year
    url = f"https://www.cftc.gov/files/dea/history/deacot{year}.zip"
    r = requests.get(url, timeout=60,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        content = z.read(z.namelist()[0]).decode("latin-1")

    reader = csv.DictReader(io.StringIO(content))
    crude_rows = []
    for row in reader:
        name = row.get("Market and Exchange Names",
                       row.get("Market_and_Exchange_Names", ""))
        if "CRUDE OIL" in name.upper() and "LIGHT SWEET" in name.upper():
            crude_rows.append(row)

    if len(crude_rows) < 2:
        raise ValueError(f"CFTC原油数据不足: {len(crude_rows)}行")

    # 按日期排序
    date_key = next((k for k in crude_rows[0] if "date" in k.lower()), None)
    if date_key:
        crude_rows.sort(key=lambda x: x.get(date_key, ""))

    def _net(row):
        lk = next((k for k in ("Noncommercial Positions-Long (All)",
                                "NonComm_Positions_Long_All") if k in row), None)
        sk = next((k for k in ("Noncommercial Positions-Short (All)",
                                "NonComm_Positions_Short_All") if k in row), None)
        lng = int((row.get(lk) or "0").replace(",", "")) if lk else 0
        sht = int((row.get(sk) or "0").replace(",", "")) if sk else 0
        return lng - sht

    latest, prev = crude_rows[-1], crude_rows[-2]
    net_now, net_prev = _net(latest), _net(prev)
    return {
        "date":     latest.get(date_key, "") if date_key else "",
        "net_long": net_now,
        "chg":      net_now - net_prev,
    }


# ────────────────────────────────────────────────────────────────────
# ⑦ WTI 1-5月差（yfinance CME合约）
# ────────────────────────────────────────────────────────────────────
def fetch_wti_spread() -> dict:
    m, y = date.today().month, date.today().year
    syms = []
    for _ in range(9):
        syms.append(f"CL{_FUT_MONTHS[m]}{str(y)[2:]}.NYM")
        m = m % 12 + 1
        if m == 1:
            y += 1

    prices = {}
    for sym in syms:
        try:
            h = yf.Ticker(sym).history(period="3d")
            if not h.empty:
                prices[sym] = round(float(h["Close"].iloc[-1]), 2)
        except Exception:
            pass

    valid = list(prices.values())
    if len(valid) < 5:
        raise ValueError(f"WTI期货合约不足5个: {len(valid)}")
    front, m5 = valid[0], valid[4]
    return {"front": front, "m5": m5, "spread": round(front - m5, 2)}


# ────────────────────────────────────────────────────────────────────
# ⑧ Brent−WTI 价差（yfinance）
# ────────────────────────────────────────────────────────────────────
def fetch_brent_wti() -> dict:
    brent = yf.Ticker("BZ=F").history(period="5d")
    wti   = yf.Ticker("CL=F").history(period="5d")
    if brent.empty or wti.empty:
        raise ValueError("yfinance Brent/WTI 返回空数据")
    b = round(float(brent["Close"].iloc[-1]), 2)
    w = round(float(wti["Close"].iloc[-1]), 2)
    return {"brent": b, "wti": w, "spread": round(b - w, 2)}


# ────────────────────────────────────────────────────────────────────
# ⑨ VIX 指数（yfinance）
# ────────────────────────────────────────────────────────────────────
def fetch_vix() -> dict:
    h = yf.Ticker("^VIX").history(period="5d")
    if h.empty:
        raise ValueError("yfinance ^VIX 返回空数据")
    return {"vix": round(float(h["Close"].iloc[-1]), 2)}


# ────────────────────────────────────────────────────────────────────
# ─ FRED 单序列辅助：取最新非空值
# ────────────────────────────────────────────────────────────────────
def _fred_latest(series_id: str, api_key: str) -> float:
    params = {
        "series_id": series_id, "api_key": api_key,
        "file_type": "json", "sort_order": "desc", "limit": "10",
    }
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params=params, timeout=15,
    )
    r.raise_for_status()
    for obs in r.json().get("observations", []):
        if obs["value"] != ".":
            return float(obs["value"])
    raise ValueError(f"FRED {series_id} 无有效数据")


def _fred_series_90d(series_id: str, api_key: str, days: int = 90) -> dict:
    """返回近 days 天的 FRED 序列，{date_str: float}，按日期升序。"""
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {
        "series_id": series_id, "api_key": api_key,
        "file_type": "json", "sort_order": "asc",
        "observation_start": start, "limit": "300",
    }
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params=params, timeout=15,
    )
    r.raise_for_status()
    return {
        o["date"]: float(o["value"])
        for o in r.json().get("observations", [])
        if o["value"] != "."
    }


# ────────────────────────────────────────────────────────────────────
# ⑩ 美债收益率曲线（FRED 批量）
#    DGS2    → us_2y
#    DGS10   → us_10y
#    T10Y2Y  → spread_2y10y  (%)
#    T10Y3M  → spread_3m10y  (%)
#    T10YIE  → bei_10y       (%)
#    DFII10  → tips_10y      (%)
# ────────────────────────────────────────────────────────────────────
def fetch_us_yields() -> dict:
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        raise EnvironmentError("FRED_API_KEY not set")
 
    series_map = [
        ("us_2y",        "DGS2"),
        ("us_10y",       "DGS10"),
        ("spread_2y10y", "T10Y2Y"),
        ("spread_3m10y", "T10Y3M"),
        ("bei_10y",      "T10YIE"),
        ("tips_10y",     "DFII10"),
        ("cn10y_fred",   "IRLTLT01CNM156N"),   # 中国10Y（月频，滞后2-3个月）
    ]
    result = {}
    for field, sid in series_map:
        try:
            result[field] = _fred_latest(sid, api_key)
        except Exception as e:
            print(f"   {sid} 跳过: {e}")
 
    # ── BUG 2 FIX ──────────────────────────────────────────────────
    # 统一语义：cnus_fred = US10Y - CN10Y（正值 = 美债收益率高于中债）
    # 当前美债 ≈ 4.4%，中债 ≈ 1.7%，cnus_fred ≈ +2.7%（约 +270bp）
    # 旧代码已是 us_10y - cn10y，但 fetch_cn_us_spread() 是 cn - us（负值）
    # 飞书里用的是 cnus_bp（来自 fetch_cn_us_spread，是负值）所以显示 -263bp
    # 修复：废弃 fetch_cn_us_spread() 的 cnus_bp 字段，统一用 cnus_fred
    if result.get("us_10y") is not None and result.get("cn10y_fred") is not None:
        result["cnus_fred"] = round(result["us_10y"] - result["cn10y_fred"], 3)
        result["cnus_bp"]   = round(result["cnus_fred"] * 100, 1)  # bp，正值=美债溢价
    # ───────────────────────────────────────────────────────────────
 
    return result


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
 
    last_sofr = 0.0
    sofrs = []
    for r in rows:
        v = r.get("sofr")
        if v is not None:
            last_sofr = v
        sofrs.append(last_sofr)
 
    def _ffill(key):
        last = None
        out = []
        for r in rows:
            v = r.get(key)
            if v is not None:
                last = v
            out.append(last)
        return out
 
    dates   = [r["date"]                           for r in rows]
    hibors  = _ffill("hibor")
    spreads = [round((hibors[i] - sofrs[i]) * 100, 2)
               if hibors[i] is not None and sofrs[i] is not None else None
               for i in range(n)]
    souths  = [r.get("south", 0.0)                 for r in rows]
    etf3033 = _ffill("etf3033")
    etf3110 = _ffill("etf3110")
    ratios  = [round(etf3033[i] / etf3110[i], 4)
               if etf3033[i] and etf3110[i] else None for i in range(n)]
 
    cftc_net     = _ffill("cftc_net")
    cftc_chg     = _ffill("cftc_chg")
    wti_m15      = _ffill("wti_m15")
    brent_wti    = _ffill("brent_wti")
    vix_arr      = _ffill("vix")
    wti_px       = _ffill("wti_price")
    us_2y_arr    = _ffill("us_2y")
    us_10y_arr   = _ffill("us_10y")
    sp_2y10y_arr = _ffill("spread_2y10y")
    sp_3m10y_arr = _ffill("spread_3m10y")
    bei_arr      = _ffill("bei_10y")
    tips_arr     = _ffill("tips_10y")
    cn10y_fred_arr  = _ffill("cn10y_fred")
    cnus_fred_arr   = _ffill("cnus_fred")      # US - CN，正值
    cnus_bp_arr     = _ffill("cnus_bp")        # bp，正值=美债溢价
 
    sofr_iorb_arr        = _ffill("sofr_iorb_bp")
    sofr_effr_arr        = _ffill("sofr_effr_bp")
    sofr_fwd1x3_arr      = _ffill("sofr_fwd1x3")
    sofr_fwd3x6_arr      = _ffill("sofr_fwd3x6")
    sofr_curve_shape_arr = _ffill("sofr_curve_shape")
    sofr_6m_arr          = _ffill("sofr_6m")
 
    # ── BUG 1 FIX: WRESBAL 用 reserves_b（B单位），不用 reserves（T单位）──
    # 旧: wresbal_arr = _ffill("reserves")  → 值约 2.994（T），JS 里加 T 后缀 → "2.994T" ✓
    # 问题: 历史数据里没有 reserves_b 字段（旧记录只有 reserves）
    # 方案: 优先用 reserves_b（B），没有则用 reserves×1000（从T换算回B）
    wresbal_b_arr = []
    for r in rows:
        v_b = r.get("reserves_b")           # 新字段，B
        v_t = r.get("reserves")             # 旧字段，T
        if v_b is not None:
            wresbal_b_arr.append(v_b)
        elif v_t is not None:
            wresbal_b_arr.append(round(v_t * 1000, 1))   # T → B
        else:
            wresbal_b_arr.append(None)
    # forward-fill
    last_w = None
    wresbal_arr_ff = []
    for v in wresbal_b_arr:
        if v is not None:
            last_w = v
        wresbal_arr_ff.append(last_w)
    # ───────────────────────────────────────────────────────────────
 
    dw_arr          = _ffill("dw")
    onrrp_arr       = _ffill("onrrp")
    tga_arr         = _ffill("tga")
 
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
        f"const RATIO={json.dumps(ratios)};\n"
        f"const CNUS  ={json.dumps(cnus_bp_arr)};\n"        # bp，正值=美债溢价
        f"const CFTC_NET={json.dumps(cftc_net)};\n"
        f"const CFTC_CHG={json.dumps(cftc_chg)};\n"
        f"const WTI_M15={json.dumps(wti_m15)};\n"
        f"const BWTI  ={json.dumps(brent_wti)};\n"
        f"const VIX_ARR={json.dumps(vix_arr)};\n"
        f"const WTI_PX={json.dumps(wti_px)};\n"
        f"const US2Y  ={json.dumps(us_2y_arr)};\n"
        f"const US10Y ={json.dumps(us_10y_arr)};\n"
        f"const SP2Y10Y={json.dumps(sp_2y10y_arr)};\n"
        f"const SP3M10Y={json.dumps(sp_3m10y_arr)};\n"
        f"const BEI10Y={json.dumps(bei_arr)};\n"
        f"const TIPS10Y={json.dumps(tips_arr)};\n"
        f"const CN10Y_FRED={json.dumps(cn10y_fred_arr)};\n"
        f"const CNUS_FRED={json.dumps(cnus_fred_arr)};\n"   # % 格式，正值
        f"const SOFR_IORB={json.dumps(sofr_iorb_arr)};\n"
        f"const SOFR_EFFR={json.dumps(sofr_effr_arr)};\n"
        f"const FWD1X3={json.dumps(sofr_fwd1x3_arr)};\n"
        f"const FWD3X6={json.dumps(sofr_fwd3x6_arr)};\n"
        f"const SOFR_CURVE_SHAPE={json.dumps(sofr_curve_shape_arr)};\n"
        f"const SOFR_6M={json.dumps(sofr_6m_arr)};\n"
        f"const WRESBAL={json.dumps(wresbal_arr_ff)};\n"    # B单位，约2994
        f"const DW_ARR={json.dumps(dw_arr)};\n"
        f"const ONRRP={json.dumps(onrrp_arr)};\n"
        f"const TGA_ARR={json.dumps(tga_arr)};\n\n"
        f"let data={{dates:[...DATES],hibor:[...HIBOR],sofr:[...SOFR],"
        f"spread:[...SPREAD],south:[...SOUTH],etf3033:[...ETF3033],"
        f"etf3110:[...ETF3110],ratio:[...RATIO],cnusSpread:[...CNUS],"
        f"cftcNet:[...CFTC_NET],cftcChg:[...CFTC_CHG],wtiM15:[...WTI_M15],"
        f"brentWti:[...BWTI],vix:[...VIX_ARR],wtiPx:[...WTI_PX],"
        f"us2y:[...US2Y],us10y:[...US10Y],sp2y10y:[...SP2Y10Y],"
        f"sp3m10y:[...SP3M10Y],bei10y:[...BEI10Y],tips10y:[...TIPS10Y],"
        f"tips:[...TIPS10Y],"
        f"cn10yFred:[...CN10Y_FRED],cnusFred:[...CNUS_FRED],"
        f"sofrIorb:[...SOFR_IORB],sofrEffrBp:[...SOFR_EFFR],"
        f"fwd1x3:[...FWD1X3],fwd3x6:[...FWD3X6],"
        f"sofrCurveShape:[...SOFR_CURVE_SHAPE],sofr6m:[...SOFR_6M],"
        f"wresbal:[...WRESBAL],dw:[...DW_ARR],"
        f"onrrp:[...ONRRP],tga:[...TGA_ARR]}};"
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
# 飞书推送 · 卡片1：港元流动性日报
# ────────────────────────────────────────────────────────────────────
def _compute_macro_state(today: dict) -> dict:
    """
    基于付鹏框架，计算当日宏观状态（A/B/C/D/E）。
    输入：history.json 最新一条记录。
    输出：{state, score, label, portfolio_type, style}
    """
    score = 0.0
 
    # 维度1：利率曲线（权重0.25）
    sp3m = today.get("spread_3m10y")   # % 格式
    sp2y = today.get("spread_2y10y")
    if sp3m is not None:
        if sp3m > 1.5:   cs = 2.0
        elif sp3m > 0.5: cs = 1.0
        elif sp3m > 0.0: cs = 0.0
        elif sp3m > -0.5:cs = -1.0
        else:            cs = -2.0
        # 2Y-10Y 辅助调整
        if sp2y is not None and sp2y * sp3m < 0:
            cs *= 0.7
        score += cs * 0.25
 
    # 维度2：流动性压力（权重0.20）
    si = today.get("sofr_iorb_bp", 0)
    reserves_b = today.get("reserves_b")
    if reserves_b is None:
        reserves_t = today.get("reserves")
        reserves_b = reserves_t * 1000 if reserves_t else None
    ls = 0.0
    if si is not None:
        ls = -2.0 if si > 10 else (-1.0 if si > 5 else (0.5 if si < -5 else 0))
    if reserves_b is not None:
        if reserves_b < 2900:  ls -= 1.0
        elif reserves_b > 3100: ls += 0.5
    score += max(-2, min(2, ls)) * 0.20
 
    # 维度3：实际利率（权重0.20）
    tips = today.get("tips_10y")
    if tips is not None:
        lvl = 1.5 if tips < 0 else (0.5 if tips < 1 else (-0.5 if tips < 2 else -1.5))
        score += lvl * 0.4 * 0.20
        # 趋势部分需要历史数据，此处用绝对水平代替
 
    # 维度4：信用/风险偏好（权重0.20）
    # 用 VIX 代理
    vix = today.get("vix")
    if vix is not None:
        rs = -1.5 if vix > 35 else (-0.5 if vix > 25 else (0.5 if vix < 15 else 0))
        score += rs * 0.20
 
    # 维度5：HIBOR利差（港元流动性，权重0.15）
    spread_bp = today.get("spread_bp")  # HIBOR-SOFR, 负=宽松
    if spread_bp is not None:
        hs = 1.0 if spread_bp < -50 else (0.5 if spread_bp < -10 else
             (-1.0 if spread_bp > 30 else (-0.5 if spread_bp > 10 else 0)))
        score += hs * 0.15
 
    # 状态判断
    if score >= 2.0:
        state, label, port, style = "A", "扩张期",      "钻头型", "成长"
    elif score >= 0.5:
        state, label, port, style = "B", "滞胀压力期",  "哑铃型", "价值"
    elif score >= -0.5:
        state, label, port, style = "C", "衰退前期",    "哑铃型(防御)", "偏价值"
    elif score >= -2.5:
        state, label, port, style = "E", "去杠杆期",    "锤子型", "防御"
    else:
        state, label, port, style = "D", "流动性危机",  "锤子型", "全防御"
 
    return {"state": state, "score": round(score, 2),
            "label": label, "portfolio": port, "style": style}
 
 
def _fetch_fx_latest() -> dict:
    """用 yfinance 拉取 DXY / AUDJPY / USDJPY 最新收盘价。"""
    result = {}
    pairs = {"dxy": "DX-Y.NYB", "audjpy": "AUDJPY=X", "usdjpy": "USDJPY=X"}
    for name, ticker in pairs.items():
        try:
            h = yf.Ticker(ticker).history(period="5d")
            if not h.empty:
                result[name] = round(float(h["Close"].iloc[-1]), 4)
        except Exception as e:
            print(f"   FX {ticker} ERROR: {e}")
    return result
 
 
def push_feishu_hibor(history: list, webhook_url: str) -> None:
    if not webhook_url or len(history) < 1:
        print("  飞书推送：WEBHOOK_URL 未设置或无数据，跳过")
        return
 
    today = history[-1]
    prev  = history[-2] if len(history) >= 2 else {}
    d5    = history[-6] if len(history) >= 6 else {}
 
    # ── 宏观状态（BUG 3 FIX）────────────────────────────────────────
    macro = _compute_macro_state(today)
    state_icon = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴", "E": "🟤"}.get(macro["state"], "⚪")
 
    # ── 外汇数据（新增）────────────────────────────────────────────
    fx = _fetch_fx_latest()
    dxy_str    = f"{fx['dxy']:.2f}"    if "dxy"    in fx else "--"
    usdjpy_str = f"{fx['usdjpy']:.2f}" if "usdjpy" in fx else "--"
    audjpy_str = f"{fx['audjpy']:.2f}" if "audjpy" in fx else "--"
    # AUDJPY 信号（风险偏好代理，付鹏框架洞一）
    audjpy_val = fx.get("audjpy")
    if audjpy_val is not None:
        fx_icon = "🟢" if audjpy_val > 96 else ("🔴" if audjpy_val < 88 else "🟡")
        fx_sig  = "风险偏好正常" if audjpy_val > 96 else ("风险厌恶" if audjpy_val < 88 else "中性")
    else:
        fx_icon, fx_sig = "⚪", "数据待接入"
 
    # ── 核心数值 ────────────────────────────────────────────────────
    spread      = today.get("spread_bp")
    spread_prev = prev.get("spread_bp")
    spread_chg  = round(spread - spread_prev, 2) if (spread is not None and spread_prev is not None) else None
    hibor  = today.get("hibor")
    sofr   = today.get("sofr")
    e3033  = today.get("etf3033")
    e3110  = today.get("etf3110")
    south  = today.get("south")
    ratio  = round(e3033 / e3110, 4) if (e3033 and e3110) else None
 
    # ── WRESBAL（BUG 1 FIX：统一显示 B）────────────────────────────
    reserves_b = today.get("reserves_b")
    if reserves_b is None:
        reserves_t = today.get("reserves")
        reserves_b = round(reserves_t * 1000, 0) if reserves_t else None
    res_str = f"{int(reserves_b):,}B" if reserves_b is not None else "--"
 
    # ── 利差信号 ────────────────────────────────────────────────────
    if spread is not None:
        if   spread < -50: spread_signal, spread_icon = "港元极度宽松", "🟢"
        elif spread < -10: spread_signal, spread_icon = "港元宽松",     "🟢"
        elif spread >  30: spread_signal, spread_icon = "港元显著偏紧", "🔴"
        elif spread >  10: spread_signal, spread_icon = "港元偏紧",     "🟡"
        else:              spread_signal, spread_icon = "利差中性",     "⚪"
    else:
        spread_signal, spread_icon = "--", "⚪"
 
    # ── BUG 3 FIX: 港股信号 = 宏观状态 × HIBOR利差双重判断 ─────────
    # 旧逻辑：只看 spread_bp → 全力做多（错误）
    # 新逻辑：宏观状态 A/B 才允许做多，C/D/E 降仓
    state = macro["state"]
    hk_spread_ok = spread is not None and spread < -10   # HIBOR 宽松（必要条件）
 
    if state == "A" and hk_spread_ok:
        hk_pos, hk_emoji = "积极做多3033，宏观+流动性双重确认", "🟢"
    elif state == "A" and not hk_spread_ok:
        hk_pos, hk_emoji = "宏观扩张但港元偏紧，半仓观望", "🟡"
    elif state == "B" and hk_spread_ok:
        hk_pos, hk_emoji = "哑铃配置：3033(成长端)+3110(防御端)各半", "🟡"
    elif state == "B" and not hk_spread_ok:
        hk_pos, hk_emoji = "滞胀期+港元偏紧，偏守3110", "🟠"
    elif state == "C":
        hk_pos, hk_emoji = "衰退前期，减持3033，增配3110防御", "🟠"
    elif state in ("D", "E"):
        hk_pos, hk_emoji = "锤子型：清仓风险资产，持现金/黄金", "🔴"
    else:
        hk_pos, hk_emoji = "数据不足，维持上期判断", "⚪"
    # ───────────────────────────────────────────────────────────────
 
    # ── 南向 ────────────────────────────────────────────────────────
    south_str  = f"{south:+.1f} 亿港元" if south is not None else "--"
    south_icon = "🟢" if (south is not None and south > 0) else "🔴"
 
    # ── 美元流动性 ──────────────────────────────────────────────────
    siorb    = today.get("sofr_iorb_bp")
    dw       = today.get("dw")
    dw_str   = f"{dw:.2f}B"      if dw      is not None else "--"
    siorb_str = f"{siorb:+.1f}bp" if siorb  is not None else "--"
 
    usd_red    = (siorb is not None and siorb > 15) or (dw is not None and dw > 5)
    usd_orange = (siorb is not None and siorb > 10) or (reserves_b is not None and reserves_b < 2900)
    usd_yellow = (siorb is not None and siorb >  5) or (reserves_b is not None and reserves_b < 3100)
    if   usd_red:    usd_icon, usd_sig = "🔴", "三重警戒·流动性紧张"
    elif usd_orange: usd_icon, usd_sig = "🟠", "指标偏紧·关注共振"
    elif usd_yellow: usd_icon, usd_sig = "🟡", "轻微偏紧·持续跟踪"
    else:            usd_icon, usd_sig = "🟢", "美元流动性充裕"
 
    # ── 利率曲线 ────────────────────────────────────────────────────
    spr2y10 = today.get("spread_2y10y")
    spr_bp  = round(spr2y10 * 100, 1) if spr2y10 is not None else None
    tips    = today.get("tips_10y")
    # BUG 2 FIX: cnus_bp 现在是正值（US-CN），显示时标注方向
    cnus_bp = today.get("cnus_bp")
    if cnus_bp is not None:
        cnus_str = f"US高于CN {cnus_bp:+.0f}bp" if cnus_bp > 0 else f"CN高于US {abs(cnus_bp):.0f}bp"
    else:
        cnus_str = "--"
 
    spr_str = f"{spr_bp:+.1f}bp" if spr_bp is not None else "--"
    if spr_bp is not None:
        if   spr_bp < -50: yc_icon, yc_sig = "🔴", "深度倒挂·衰退风险高"
        elif spr_bp <   0: yc_icon, yc_sig = "🟠", "曲线倒挂·经济承压"
        elif spr_bp <  20: yc_icon, yc_sig = "🟡", "曲线趋平·关注放缓"
        else:              yc_icon, yc_sig = "🟢", "曲线正常·周期健康"
    else:
        yc_icon, yc_sig = "⚪", "数据待接入"
 
    # ── 综合信号 ────────────────────────────────────────────────────
    # 用宏观状态直接映射综合建议
    ov_map = {
        "A": ("🟢", "宏观扩张·积极配置",     "green"),
        "B": ("🟡", "滞胀压力·哑铃配置",     "yellow"),
        "C": ("🟠", "衰退前期·降低仓位",     "orange"),
        "D": ("🔴", "流动性危机·全面避险",   "red"),
        "E": ("🟤", "去杠杆期·锤子型防御",   "orange"),
    }
    ov_icon, ov_label, ov_tpl = ov_map.get(state, ("⚪", "状态未知", "blue"))
 
    def _fv(key, fmt=".3f"):
        v = today.get(key)
        try: return format(v, fmt) if v is not None else "--"
        except: return "--"
 
    # ── 各节内容 ────────────────────────────────────────────────────
    sec_macro = (
        f"**【第一层宏观状态】**\n"
        f"{state_icon} 状态 **{state}（{macro['label']}）**  评分 {macro['score']:+.2f}\n"
        f"配置型态: {macro['portfolio']}  |  风格偏向: {macro['style']}"
    )
 
    sec_hk = (
        f"**【港元流动性】**\n"
        f"HIBOR: {_fv('hibor')}% | SOFR: {_fv('sofr')}% | 利差: {spread_icon} {spread:+.1f}bp\n"
        f"状态：{spread_icon} {spread_signal}"
    ) if spread is not None else (
        f"**【港元流动性】**\n数据待接入"
    )
 
    sec_usd = (
        f"**【美元流动性】**\n"
        f"SOFR-IORB: {siorb_str} | WRESBAL: {res_str} | 贴现窗口: {dw_str}\n"
        f"状态：{usd_icon} {usd_sig}"
    )
 
    sec_fx = (
        f"**【外汇信号】**\n"
        f"DXY: {dxy_str} | USDJPY: {usdjpy_str} | AUDJPY: {audjpy_str}\n"
        f"状态：{fx_icon} {fx_sig}（AUDJPY风险偏好代理）"
    )
 
    sec_hst = (
        f"**【恒生科技 · 港股方向】**\n"
        f"3033: HK${_fv('etf3033')} | 3110: HK${_fv('etf3110')} | 比值: {f'{ratio:.4f}' if ratio else '--'}\n"
        f"南向资金: {south_str}（{south_icon}）\n"
        f"方向：{hk_emoji} {hk_pos}"
    )
 
    sec_yc = (
        f"**【利率曲线】**\n"
        f"2Y: {_fv('us_2y')}% | 10Y: {_fv('us_10y')}% | 2Y-10Y: {spr_str} | TIPS: {_fv('tips_10y')}%\n"
        f"中美利差: {cnus_str}\n"
        f"状态：{yc_icon} {yc_sig}"
    )
 
    sec_overall = (
        f"**【综合信号】**\n"
        f"{ov_icon} **{ov_label}**\n"
        f"宏观:{state_icon}{state} | 港元:{spread_icon} | 美元:{usd_icon} | 外汇:{fx_icon} | 曲线:{yc_icon}"
    )
 
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"📊 付鹏框架每日监控 · {today['date']}"},
            "template": ov_tpl,
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": sec_macro}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": sec_hk}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": sec_usd}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": sec_fx}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": sec_hst}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": sec_yc}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": sec_overall}},
            {
                "tag": "note",
                "elements": [{"tag": "plain_text",
                               "content": f"数据来源：HKAB·FRED·东方财富·yfinance · 自动更新 {today['date']}"}],
            },
        ],
    }
 
    try:
        r = requests.post(
            webhook_url,
            json={"msg_type": "interactive", "card": card},
            timeout=15, verify=False
        )
        if r.status_code == 200 and r.json().get("StatusCode") == 0:
            print("  飞书推送 OK（付鹏框架每日监控）")
        else:
            print(f"  飞书推送失败: {r.status_code}  {r.text[:120]}")
    except Exception as e:
        print(f"  飞书推送异常: {e}")
