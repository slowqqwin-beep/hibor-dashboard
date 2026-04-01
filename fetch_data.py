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
    批量抓取美元流动性底层指标，全部来自 FRED（SRF 来自 Fed H.4.1）。
    返回字典，失败的字段不包含（调用方需做 .get() 防御）。
    字段说明：
      onrrp        B    ON RRP 隔夜逆回购
      reserves     B    银行准备金 (WRESBAL, M÷1000=B)
      tga          B    TGA (WTREGEN, M→B)
      tga_wow      B    TGA 周变化
      sofr_on      %    Overnight SOFR
      sofr_1m      %    Term SOFR 30D avg
      sofr_6m      %    Term SOFR 180D avg
      iorb         %    IORB
      effr         %    联邦基金有效利率
      dw           B    贴现窗口 DPCREDIT
      srf          B    SRF 常备回购 (H.4.1)
      sofr_iorb_bp bp   (sofr_on - iorb)×100
      sofr_effr_bp bp   (sofr_on - effr)×100
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

    # ON RRP（十亿美元）
    v = _get("RRPONTSYD"); result["onrrp"] = v if v is not None else None
    # 银行准备金（WRESBAL 单位 M → ÷1000 = B）
    v = _get("WRESBAL", 1000); result["reserves"] = v if v is not None else None
    # TGA（WTREGEN 单位 M → ÷1000 = B）
    try:
        params = {"series_id": "WTREGEN", "api_key": api_key, "file_type": "json",
                  "sort_order": "desc", "limit": "5"}
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params=params, timeout=15)
        r.raise_for_status()
        obs = [o for o in r.json().get("observations", []) if o["value"] != "."]
        if obs:
            result["tga"] = round(float(obs[0]["value"]) / 1000, 3)
            result["tga_wow"] = (round(result["tga"] - float(obs[1]["value"]) / 1000, 3)
                                 if len(obs) >= 2 else 0.0)
    except Exception as e:
        print(f"   TGA ERROR: {e}")
    # overnight SOFR（区别于 Term SOFR 3M → sofr 字段）
    v = _get("SOFR"); result["sofr_on"] = v if v is not None else None
    # IORB
    v = _get("IORB"); result["iorb"] = v if v is not None else None
    # EFFR
    v = _get("FEDFUNDS"); result["effr"] = v if v is not None else None
    # DW 贴现窗口（B）
    v = _get("DPCREDIT"); result["dw"] = v if v is not None else None
    # SRF（H.4.1 HTML parse）
    result["srf"] = _fetch_srf()
    # 衍生：SOFR-IORB 利差
    if result.get("sofr_on") is not None and result.get("iorb") is not None:
        result["sofr_iorb_bp"] = round((result["sofr_on"] - result["iorb"]) * 100, 1)
    # Term SOFR 1M / 6M（曲线形态计算用）
    v = _get("SOFR30DAYAVG"); result["sofr_1m"] = v if v is not None else None
    v = _get("SOFR180DAYAVG"); result["sofr_6m"] = v if v is not None else None
    # 衍生：SOFR-EFFR 利差
    if result.get("sofr_on") is not None and result.get("effr") is not None:
        result["sofr_effr_bp"] = round((result["sofr_on"] - result["effr"]) * 100, 1)

    return {k: v for k, v in result.items() if v is not None}


def _fetch_srf() -> float:
    """解析 Fed H.4.1，返回 SRF 使用量（十亿美元）；失败或未使用则返回 0.0。"""
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
        "reserves": ("WRESBAL",  1000),   # M → B
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
# 外汇信号历史回填（首次运行或 dxy 不足 60 条时自动执行）
# ────────────────────────────────────────────────────────────────────
def backfill_fx_history(history: list) -> list:
    """
    回填外汇信号 90 天历史：
      yfinance 日频：DXY / USDJPY / AUDJPY
      FRED 月频（forward-fill）：JP 2Y / DE 10Y / IT 10Y
    衍生写入：us_jp_2y_spread / de_us_spread / it_de_spread
              usdjpy_1d_chg / audjpy_5d_chg
    仅在 dxy 字段不足 60 条时触发（幂等）。
    """
    has_dxy = sum(1 for r in history if r.get("dxy") is not None)
    if has_dxy >= 60:
        return history

    print("\n── 外汇信号历史回填 (90d) ──────────────────────────────────")
    api_key = os.environ.get("FRED_API_KEY", "")
    all_obs: dict = {}   # {date_str: {field: value}}

    # ── yfinance 日频 ──────────────────────────────────────────────
    for ticker, field in [("DX-Y.NYB", "dxy"),
                          ("USDJPY=X", "usdjpy"),
                          ("AUDJPY=X", "audjpy")]:
        try:
            h = yf.Ticker(ticker).history(period="3mo")
            if h.empty:
                print(f"   {ticker}: 无数据，跳过")
                continue
            for dt, row in h.iterrows():
                d = str(dt.date())
                all_obs.setdefault(d, {})[field] = round(float(row["Close"]), 4)
            print(f"   {ticker}: {len(h)} 条  ✓")
        except Exception as e:
            print(f"   {ticker} ERROR: {e}")

    # 计算 usdjpy_1d_chg / audjpy_5d_chg（基于已拉取的序列）
    sorted_dates = sorted(all_obs.keys())
    usdjpy_pts = [(d, all_obs[d]["usdjpy"]) for d in sorted_dates if all_obs[d].get("usdjpy") is not None]
    audjpy_pts = [(d, all_obs[d]["audjpy"]) for d in sorted_dates if all_obs[d].get("audjpy") is not None]
    for idx, (d, v) in enumerate(usdjpy_pts):
        if idx >= 1:
            prev = usdjpy_pts[idx - 1][1]
            all_obs[d]["usdjpy_1d_chg"] = round((v / prev - 1) * 100, 3)
    for idx, (d, v) in enumerate(audjpy_pts):
        if idx >= 5:
            prev5 = audjpy_pts[idx - 5][1]
            all_obs[d]["audjpy_5d_chg"] = round((v / prev5 - 1) * 100, 3)

    # ── FRED 月频（forward-fill） ──────────────────────────────────
    monthly_raw: dict = {}   # {field: {date: value}}
    if api_key:
        for field, sid in [("jp_2y",  "IRLTLT01JPM156N"),
                           ("de_10y", "IRLTLT01DEM156N"),
                           ("it_10y", "IRLTLT01ITM156N")]:
            try:
                obs = _fred_series_90d(sid, api_key, days=180)
                monthly_raw[field] = obs
                print(f"   {sid}: {len(obs)} 条（月频）")
            except Exception as e:
                print(f"   {sid} ERROR: {e}")

    # 合并所有日期轴（history + 抓取日期）
    all_dates = sorted(set(list(all_obs.keys()) + [r["date"] for r in history]))

    def _ffill_dict(raw: dict, dates: list) -> dict:
        out, last = {}, None
        for d in dates:
            if d in raw:
                last = raw[d]
            out[d] = last
        return out

    monthly_ff = {f: _ffill_dict(raw, all_dates) for f, raw in monthly_raw.items()}
    hist_by_date = {r["date"]: r for r in history}

    for d in all_dates:
        if d not in all_obs:
            all_obs[d] = {}
        # 月频 forward-fill
        for field, ff in monthly_ff.items():
            if ff.get(d) is not None and all_obs[d].get(field) is None:
                all_obs[d][field] = ff[d]
        # 衍生利差（需要 us_2y / us_10y，从同日 history 取）
        hr = hist_by_date.get(d, {})
        us2y  = hr.get("us_2y")
        us10y = hr.get("us_10y")
        jp2y  = all_obs[d].get("jp_2y")
        de10y = all_obs[d].get("de_10y")
        it10y = all_obs[d].get("it_10y")
        if us2y is not None and jp2y is not None and all_obs[d].get("us_jp_2y_spread") is None:
            all_obs[d]["us_jp_2y_spread"] = round(us2y - jp2y, 4)
        if de10y is not None and us10y is not None and all_obs[d].get("de_us_spread") is None:
            all_obs[d]["de_us_spread"] = round(de10y - us10y, 4)
        if it10y is not None and de10y is not None and all_obs[d].get("it_de_spread") is None:
            all_obs[d]["it_de_spread"] = round(it10y - de10y, 4)

    # 合并进 history（只补缺失字段）
    existing = {r["date"]: i for i, r in enumerate(history)}
    updated = 0
    for d, fields in sorted(all_obs.items()):
        if d in existing:
            rec = history[existing[d]]
            for k, v in fields.items():
                if rec.get(k) is None and v is not None:
                    rec[k] = v
                    updated += 1

    history.sort(key=lambda r: r["date"])
    history = history[-MAX_HIST_DAYS:]
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  外汇信号回填完成：补字段 {updated} 处，共 {len(history)} 条")
    return history


# ────────────────────────────────────────────────────────────────────
# ④ 南向资金
# ────────────────────────────────────────────────────────────────────
# ⑤ 中美利差 10Y  (CN via akshare, US via FRED DGS10)
# ────────────────────────────────────────────────────────────────────
def fetch_cn_us_spread() -> dict:
    from datetime import timedelta

    # US 10Y from FRED DGS10
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        raise EnvironmentError("FRED_API_KEY not set")
    params = {
        "series_id":  "DGS10",
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
    us10y = None
    for obs in r.json().get("observations", []):
        if obs["value"] != ".":
            us10y = float(obs["value"])
            break
    if us10y is None:
        raise ValueError("FRED DGS10 无有效数据")

    # CN 10Y from akshare bond_zh_us_rate
    start = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    df = ak.bond_zh_us_rate(start_date=start)
    cn_col = next((c for c in df.columns if "中国" in c and "10年" in c), None)
    if cn_col is None:
        raise ValueError("akshare bond_zh_us_rate 无中国10年列")
    df = df.dropna(subset=[cn_col])
    if df.empty:
        raise ValueError("akshare CN 10Y 无有效数据")
    cn10y = float(df.iloc[-1][cn_col])

    return {
        "cn10y":     cn10y,
        "us10y":     us10y,
        "spread_bp": round((cn10y - us10y) * 100, 2),
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
# ⑪ 外汇信号（DXY / USDJPY / AUDJPY + 月频利差）
# ────────────────────────────────────────────────────────────────────
def fetch_fx_signals(us_2y_val=None, us_10y_val=None) -> dict:
    """
    拉取外汇信号指标（最新值）：
      DXY (DX-Y.NYB)、USDJPY (USDJPY=X)、AUDJPY (AUDJPY=X) — yfinance
      JP 2Y  (IRLTLT01JPM156N)、DE 10Y (IRLTLT01DEM156N)、
      IT 10Y (IRLTLT01ITM156N)                              — FRED 月频
    衍生：
      us_jp_2y_spread = us_2y − jp_2y
      de_us_spread    = de_10y − us_10y
      it_de_spread    = it_10y − de_10y
      usdjpy_1d_chg   单日涨跌幅 %
      audjpy_5d_chg   5日涨跌幅 %
    """
    api_key = os.environ.get("FRED_API_KEY", "")
    result = {}

    # yfinance 日频（period="10d" 保证有足够行计算 1d/5d 变化）
    for ticker, field in [("DX-Y.NYB", "dxy"),
                          ("USDJPY=X", "usdjpy"),
                          ("AUDJPY=X", "audjpy")]:
        try:
            h = yf.Ticker(ticker).history(period="10d")
            if h.empty:
                raise ValueError("无数据")
            result[field] = round(float(h["Close"].iloc[-1]), 4)
            if field == "usdjpy" and len(h) >= 2:
                prev = float(h["Close"].iloc[-2])
                result["usdjpy_1d_chg"] = round((result["usdjpy"] / prev - 1) * 100, 3)
            if field == "audjpy":
                if len(h) >= 6:
                    prev5 = float(h["Close"].iloc[-6])
                else:
                    prev5 = float(h["Close"].iloc[0])
                result["audjpy_5d_chg"] = round((result["audjpy"] / prev5 - 1) * 100, 3)
        except Exception as e:
            print(f"   {ticker} ERROR: {e}")

    # FRED 月频
    if api_key:
        for field, sid in [("jp_2y",  "IRLTLT01JPM156N"),
                           ("de_10y", "IRLTLT01DEM156N"),
                           ("it_10y", "IRLTLT01ITM156N")]:
            try:
                result[field] = _fred_latest(sid, api_key)
            except Exception as e:
                print(f"   {sid} ERROR: {e}")

    # 衍生计算
    us2y  = us_2y_val  if us_2y_val  is not None else result.get("us_2y")
    us10y = us_10y_val if us_10y_val is not None else result.get("us_10y")
    jp2y  = result.get("jp_2y")
    de10y = result.get("de_10y")
    it10y = result.get("it_10y")
    if us2y is not None and jp2y is not None:
        result["us_jp_2y_spread"] = round(us2y - jp2y, 4)
    if de10y is not None and us10y is not None:
        result["de_us_spread"] = round(de10y - us10y, 4)
    if it10y is not None and de10y is not None:
        result["it_de_spread"] = round(it10y - de10y, 4)

    return result


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
        ("cn10y_fred",   "IRLTLT01CNM156N"),   # 中国10Y（月频）
    ]
    result = {}
    for field, sid in series_map:
        try:
            result[field] = _fred_latest(sid, api_key)
        except Exception as e:
            print(f"   {sid} 跳过: {e}")
    # 计算 US-CN 利差（%）
    if result.get("us_10y") is not None and result.get("cn10y_fred") is not None:
        result["cnus_fred"] = round(result["us_10y"] - result["cn10y_fred"], 3)
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

    # SOFR 缺失时向前填充
    last_sofr = 0.0
    sofrs = []
    for r in rows:
        v = r.get("sofr")
        if v is not None:
            last_sofr = v
        sofrs.append(last_sofr)

    # 中美利差缺失时向前填充
    last_cnus = None
    cnus_list = []
    for r in rows:
        v = r.get("cnus_bp")
        if v is not None:
            last_cnus = v
        cnus_list.append(last_cnus)

    def _ffill(key):
        last = None
        out = []
        for r in rows:
            v = r.get(key)
            if v is not None:
                last = v
            out.append(last)
        return out

    dates   = [r["date"]                         for r in rows]
    hibors  = _ffill("hibor")
    spreads = [round((hibors[i] - sofrs[i]) * 100, 2) if hibors[i] is not None and sofrs[i] is not None else None for i in range(n)]
    souths  = [r.get("south", 0.0)               for r in rows]
    etf3033 = _ffill("etf3033")
    etf3110 = _ffill("etf3110")
    ratios  = [round(etf3033[i] / etf3110[i], 4) if etf3033[i] and etf3110[i] else None for i in range(n)]

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
    cnus_fred_arr   = _ffill("cnus_fred")
    sofr_iorb_arr        = _ffill("sofr_iorb_bp")
    sofr_effr_arr        = _ffill("sofr_effr_bp")
    sofr_fwd1x3_arr      = _ffill("sofr_fwd1x3")
    sofr_fwd3x6_arr      = _ffill("sofr_fwd3x6")
    sofr_curve_shape_arr = _ffill("sofr_curve_shape")
    sofr_6m_arr          = _ffill("sofr_6m")
    wresbal_arr     = _ffill("reserves")
    dw_arr          = _ffill("dw")
    onrrp_arr       = _ffill("onrrp")
    tga_arr         = _ffill("tga")
    # ── ⑥ 外汇信号 ──────────────────────────────────────────────────
    us_jp_2y_arr    = _ffill("us_jp_2y_spread")
    dxy_arr         = _ffill("dxy")
    usdjpy_arr      = _ffill("usdjpy")
    audjpy_arr      = _ffill("audjpy")
    de_us_arr       = _ffill("de_us_spread")
    it_de_arr       = _ffill("it_de_spread")

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
        f"const CNUS  ={json.dumps(cnus_list)};\n"
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
        f"const CNUS_FRED={json.dumps(cnus_fred_arr)};\n"
        f"const SOFR_IORB={json.dumps(sofr_iorb_arr)};\n"
        f"const SOFR_EFFR={json.dumps(sofr_effr_arr)};\n"
        f"const FWD1X3={json.dumps(sofr_fwd1x3_arr)};\n"
        f"const FWD3X6={json.dumps(sofr_fwd3x6_arr)};\n"
        f"const SOFR_CURVE_SHAPE={json.dumps(sofr_curve_shape_arr)};\n"
        f"const SOFR_6M={json.dumps(sofr_6m_arr)};\n"
        f"const WRESBAL={json.dumps(wresbal_arr)};\n"
        f"const DW_ARR={json.dumps(dw_arr)};\n"
        f"const ONRRP={json.dumps(onrrp_arr)};\n"
        f"const TGA_ARR={json.dumps(tga_arr)};\n"
        f"const US_JP_2Y_SPREAD={json.dumps(us_jp_2y_arr)};\n"
        f"const DXY_ARR={json.dumps(dxy_arr)};\n"
        f"const USDJPY_ARR={json.dumps(usdjpy_arr)};\n"
        f"const AUDJPY_ARR={json.dumps(audjpy_arr)};\n"
        f"const DE_US_SPREAD={json.dumps(de_us_arr)};\n"
        f"const IT_DE_SPREAD={json.dumps(it_de_arr)};\n\n"
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
        f"onrrp:[...ONRRP],tga:[...TGA_ARR],"
        f"usJp2ySpread:[...US_JP_2Y_SPREAD],dxy:[...DXY_ARR],"
        f"usdjpy:[...USDJPY_ARR],audjpy:[...AUDJPY_ARR],"
        f"deUsSpread:[...DE_US_SPREAD],itDeSpread:[...IT_DE_SPREAD]}};"
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
# 飞书推送 · 付鹏框架每日监控（统一卡片）
# ────────────────────────────────────────────────────────────────────
def _compute_macro_state(today: dict) -> dict:
    """
    基于付鹏框架，计算当日宏观状态（A/B/C/D/E）。
    输入：history.json 最新一条记录。
    输出：{state, score, label, portfolio, style}
    """
    score = 0.0

    # 维度1：利率曲线（权重0.25）
    sp3m = today.get("spread_3m10y")
    sp2y = today.get("spread_2y10y")
    if sp3m is not None:
        if   sp3m > 1.5:  cs = 2.0
        elif sp3m > 0.5:  cs = 1.0
        elif sp3m > 0.0:  cs = 0.0
        elif sp3m > -0.5: cs = -1.0
        else:             cs = -2.0
        if sp2y is not None and sp2y * sp3m < 0:
            cs *= 0.7
        score += cs * 0.25

    # 维度2：流动性压力（权重0.20）
    si = today.get("sofr_iorb_bp")
    reserves_b = today.get("reserves_b")
    if reserves_b is None:
        reserves_b = today.get("reserves")   # reserves 已是 B 单位（M÷1000）
    ls = 0.0
    if si is not None:
        ls = -2.0 if si > 10 else (-1.0 if si > 5 else (0.5 if si < -5 else 0.0))
    if reserves_b is not None:
        if reserves_b < 2900:   ls -= 1.0
        elif reserves_b > 3100: ls += 0.5
    score += max(-2.0, min(2.0, ls)) * 0.20

    # 维度3：实际利率（权重0.20）
    tips = today.get("tips_10y")
    if tips is not None:
        lvl = 1.5 if tips < 0 else (0.5 if tips < 1 else (-0.5 if tips < 2 else -1.5))
        score += lvl * 0.4 * 0.20

    # 维度4：风险偏好 VIX（权重0.20）
    vix = today.get("vix")
    if vix is not None:
        rs = -1.5 if vix > 35 else (-0.5 if vix > 25 else (0.5 if vix < 15 else 0.0))
        score += rs * 0.20

    # 维度5：HIBOR-SOFR 利差（港元流动性，权重0.15）
    spread_bp = today.get("spread_bp")
    if spread_bp is not None:
        hs = (1.0 if spread_bp < -50 else
              0.5 if spread_bp < -10 else
              -1.0 if spread_bp > 30 else
              -0.5 if spread_bp > 10 else 0.0)
        score += hs * 0.15

    if   score >= 2.0:  state, label, port, style = "A", "扩张期",      "钻头型",     "成长"
    elif score >= 0.5:  state, label, port, style = "B", "滞胀压力期",  "哑铃型",     "价值"
    elif score >= -0.5: state, label, port, style = "C", "衰退前期",    "哑铃型(防御)", "偏价值"
    elif score >= -2.5: state, label, port, style = "E", "去杠杆期",    "锤子型",     "防御"
    else:               state, label, port, style = "D", "流动性危机",  "锤子型",     "全防御"

    return {"state": state, "score": round(score, 2),
            "label": label, "portfolio": port, "style": style}


def push_feishu_unified(history: list, webhook_url: str) -> None:
    import traceback as _tb
    try:
        import urllib3; urllib3.disable_warnings()
    except Exception:
        pass
    if not webhook_url or not history:
        print("  飞书推送：WEBHOOK_URL 未设置或无数据，跳过")
        return
    try:
        _push_feishu_unified_inner(history, webhook_url)
    except Exception as _e:
        print(f"  飞书推送崩溃（未捕获异常）: {_e}")
        _tb.print_exc()


def _push_feishu_unified_inner(history: list, webhook_url: str) -> None:

    rec = history[-1]

    def fv(key, fmt=".2f", fallback="--"):
        v = rec.get(key)
        try:
            return format(v, fmt) if v is not None else fallback
        except Exception:
            return fallback

    # ── §1 港元流动性 ─────────────────────────────────────────────────
    spread = rec.get("spread_bp")
    if spread is not None:
        if   spread < -50: hk_icon, hk_sig, hk_st = "🟢", "港元极度宽松", "green"
        elif spread < -10: hk_icon, hk_sig, hk_st = "🟢", "港元宽松",     "green"
        elif spread <=  0: hk_icon, hk_sig, hk_st = "🟡", "利差中性",     "yellow"
        elif spread <= 20: hk_icon, hk_sig, hk_st = "🟠", "港元偏紧",     "orange"
        else:              hk_icon, hk_sig, hk_st = "🔴", "港元显著偏紧", "red"
    else:
        hk_icon, hk_sig, hk_st = "⚪", "数据待接入", "na"

    sec_hk = (
        f"**【港元流动性】**\n"
        f"HIBOR: {fv('hibor','.3f')}% ｜ SOFR: {fv('sofr','.3f')}% ｜ 利差: {fv('spread_bp','+.1f')}bp\n"
        f"状态：{hk_icon} {hk_sig}"
    )

    # ── §2 美元流动性 ─────────────────────────────────────────────────
    siorb    = rec.get("sofr_iorb_bp")
    reserves = rec.get("reserves")   # B（WRESBAL M÷1000，约2994）
    dw       = rec.get("dw")         # B
    f1x3     = rec.get("sofr_fwd1x3")  # bp
    f3x6     = rec.get("sofr_fwd3x6")  # bp

    res_str  = f"{reserves:.0f}B" if reserves is not None else "--"
    if f1x3 is not None and f3x6 is not None:
        curve_label = "Contango" if f3x6 > f1x3 else "Backwardation"
        curve_diff  = round(f3x6 - f1x3, 1)
        curve_line  = f"曲线结构：1x3 {f1x3:+.1f}bp / 3x6 {f3x6:+.1f}bp / 差值 {curve_diff:+.1f}bp [{curve_label}]"
    else:
        curve_line  = "曲线结构：数据待接入"

    usd_red    = (siorb is not None and siorb > 15) or (dw is not None and dw > 5)
    usd_orange = (siorb is not None and siorb > 10) or (reserves is not None and reserves < 2900) or (dw is not None and dw > 2)
    usd_yellow = (siorb is not None and siorb > 5)  or (reserves is not None and reserves < 3100)
    if   usd_red:    usd_icon, usd_sig, usd_st = "🔴", "三重警戒·流动性紧张",   "red"
    elif usd_orange: usd_icon, usd_sig, usd_st = "🟠", "指标偏紧·关注共振",     "orange"
    elif usd_yellow: usd_icon, usd_sig, usd_st = "🟡", "轻微偏紧·持续跟踪",     "yellow"
    else:            usd_icon, usd_sig, usd_st = "🟢", "美元流动性充裕",         "green"

    sec_usd = (
        f"**【美元流动性】**\n"
        f"SOFR-IORB: {fv('sofr_iorb_bp','+.1f')}bp ｜ WRESBAL: {res_str} ｜ 贴现窗口: {fv('dw','.2f')}B\n"
        f"{curve_line}\n"
        f"状态：{usd_icon} {usd_sig}"
    )

    # ── §3 外汇信号 ───────────────────────────────────────────────────
    dxy      = rec.get("dxy")
    uj_chg   = rec.get("usdjpy_1d_chg")   # %
    aj_chg5  = rec.get("audjpy_5d_chg")   # %

    uj_str = f"（日涨跌{uj_chg:+.2f}%）" if uj_chg is not None else ""
    aj_str = f"（5日涨跌{aj_chg5:+.2f}%）" if aj_chg5 is not None else ""

    fx_red    = (uj_chg is not None and uj_chg < -2.0) or (aj_chg5 is not None and aj_chg5 < -4.0)
    fx_orange = (uj_chg is not None and uj_chg < -1.0) or (aj_chg5 is not None and aj_chg5 < -2.0) or (dxy is not None and dxy > 113)
    fx_yellow = (uj_chg is not None and uj_chg < -0.5) or (aj_chg5 is not None and aj_chg5 < -1.0) or (dxy is not None and dxy > 108)
    if   fx_red:    fx_icon, fx_sig, fx_st = "🔴", "Carry Trade崩溃·紧急避险",       "red"
    elif fx_orange: fx_icon, fx_sig, fx_st = "🟠", "Carry Trade承压·降低风险敞口",   "orange"
    elif fx_yellow: fx_icon, fx_sig, fx_st = "🟡", "Carry Trade松动·保持警惕",       "yellow"
    else:           fx_icon, fx_sig, fx_st = "🟢", "Carry Trade稳定·情绪正常",       "green"

    sec_fx = (
        f"**【外汇信号】**\n"
        f"DXY: {fv('dxy','.2f')} ｜ USDJPY: {fv('usdjpy','.2f')}{uj_str} ｜ AUDJPY: {fv('audjpy','.2f')}{aj_str}\n"
        f"状态：{fx_icon} {fx_sig}"
    )

    # ── §4 恒生科技 ───────────────────────────────────────────────────
    e3033 = rec.get("etf3033")
    e3110 = rec.get("etf3110")
    south = rec.get("south")
    ratio = round(e3033 / e3110, 4) if (e3033 and e3110) else None

    south_flow = "净流入" if (south is not None and south > 0) else ("净流出" if (south is not None and south < 0) else "--")
    south_str  = f"{south:+.1f}亿港元（{south_flow}）" if south is not None else "--"
    ratio_str  = f"{ratio:.4f}" if ratio else "--"

    # 恒科仓位建议：宏观状态（A/B/C/D/E）× HIBOR 利差双重判断
    macro = _compute_macro_state(rec)
    macro_state = macro["state"]
    macro_label = macro["label"]
    hk_spread_ok = spread is not None and spread < -10  # HIBOR 宽松是必要条件

    if macro_state in ("D", "E"):
        hk_pos, hk_pos_icon = "锤子型：清仓风险资产，持现金/黄金", "🔴"
    elif macro_state == "C":
        hk_pos, hk_pos_icon = "衰退前期，减持3033，增配3110防御", "🟠"
    elif macro_state == "B" and hk_spread_ok:
        hk_pos, hk_pos_icon = "哑铃配置：3033(成长端)+3110(防御端)各半", "🟡"
    elif macro_state == "B" and not hk_spread_ok:
        hk_pos, hk_pos_icon = "滞胀期+港元偏紧，偏守3110", "🟠"
    elif macro_state == "A" and hk_spread_ok:
        hk_pos, hk_pos_icon = "积极做多3033，宏观+流动性双重确认", "🟢"
    elif macro_state == "A" and not hk_spread_ok:
        hk_pos, hk_pos_icon = "宏观扩张但港元偏紧，半仓观望", "🟡"
    else:
        hk_pos, hk_pos_icon = "数据不足，维持上期判断", "⚪"

    sec_hst = (
        f"**【恒生科技】**\n"
        f"3033: HK${fv('etf3033','.3f')} ｜ 3110: HK${fv('etf3110','.3f')} ｜ 比值: {ratio_str}\n"
        f"南向资金: {south_str}\n"
        f"宏观状态：{macro_state}（{macro_label}）评分 {macro['score']:+.2f}\n"
        f"方向：{hk_pos_icon} {hk_pos}"
    )

    # ── §5 利率曲线 ───────────────────────────────────────────────────
    spr2y10 = rec.get("spread_2y10y")   # % (10Y - 2Y)
    spr_bp  = round(spr2y10 * 100, 1) if spr2y10 is not None else None

    # 中美利差：cnus_bp = US−CN in bp（正值=美债溢价）；cnus_fred = US−CN in %
    cnus = rec.get("cnus_bp")
    if cnus is None and rec.get("cnus_fred") is not None:
        cnus = round(rec["cnus_fred"] * 100, 1)   # US−CN，正值
    cnus_str = f"{cnus:+.0f}bp" if cnus is not None else "--"

    if spr_bp is not None:
        if   spr_bp < -50: yc_icon, yc_sig, yc_st = "🔴", "深度倒挂·衰退风险高",   "red"
        elif spr_bp <   0: yc_icon, yc_sig, yc_st = "🟠", "曲线倒挂·经济承压",     "orange"
        elif spr_bp <  20: yc_icon, yc_sig, yc_st = "🟡", "曲线趋平·关注放缓",     "yellow"
        else:              yc_icon, yc_sig, yc_st = "🟢", "曲线正常·周期健康",     "green"
    else:
        yc_icon, yc_sig, yc_st = "⚪", "数据待接入", "na"

    sec_yc = (
        f"**【利率曲线】**\n"
        f"2Y: {fv('us_2y','.3f')}% ｜ 10Y: {fv('us_10y','.3f')}% ｜ "
        f"2Y-10Y: {format(spr_bp,'+.1f') if spr_bp is not None else '--'}bp ｜ "
        f"TIPS: {fv('tips_10y','.3f')}%\n"
        f"中美利差: {cnus_str}\n"
        f"状态：{yc_icon} {yc_sig}"
    )

    # ── 综合信号 ──────────────────────────────────────────────────────
    scored = [s for s in [hk_st, usd_st, fx_st, yc_st] if s != "na"]
    red_n, orange_n, yellow_n = scored.count("red"), scored.count("orange"), scored.count("yellow")

    if   red_n > 0:                       ov_icon, ov_label, ov_tpl = "🔴", "全面避险·检查风险敞口",     "red"
    elif orange_n > 0 or yellow_n >= 3:   ov_icon, ov_label, ov_tpl = "🟠", "降低仓位·控制回撤",         "orange"
    elif yellow_n >= 1:                   ov_icon, ov_label, ov_tpl = "🟡", "保持关注·暂缓加仓",         "yellow"
    else:                                 ov_icon, ov_label, ov_tpl = "🟢", "市场正常·维持仓位",         "green"

    sec_overall = (
        f"**【综合信号】**\n"
        f"{ov_icon} **{ov_label}**\n"
        f"（{hk_icon}港元 {usd_icon}美元流动性 {fx_icon}外汇 {yc_icon}利率曲线）"
    )

    # ── 构建卡片 ──────────────────────────────────────────────────────
    date_str = rec.get("date", date.today().isoformat())
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📊 付鹏框架每日监控 · {date_str}"},
            "template": ov_tpl,
        },
        "elements": [
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
                               "content": f"数据来源：HKAB·FRED·akshare·yfinance · 自动更新 {date_str}"}]
            },
        ],
    }

    try:
        resp = requests.post(
            webhook_url,
            json={"msg_type": "interactive", "card": card},
            timeout=15, verify=False,
        )
        if resp.status_code == 200 and resp.json().get("StatusCode") == 0:
            print("  飞书推送 OK（付鹏框架每日监控）")
        else:
            print(f"  飞书推送失败: {resp.status_code}  {resp.text[:200]}")
    except Exception as e:
        import traceback
        print(f"  飞书推送 HTTP 异常: {e}")
        traceback.print_exc()


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
        # 向前填充：用 history.json 最近一条有效 hibor
        hist_tmp = load_history()
        last = next((r["hibor"] for r in reversed(hist_tmp) if r.get("hibor")), None)
        if last:
            record["hibor"] = last
            print(f"   使用上次已知值 {last:.4f}%（填充）")
        else:
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

    # ── ③ 美元流动性底层指标 ─────────────────────────────────────────────
    print("\n③ 美元流动性底层指标")
    try:
        liq = fetch_liq_bundle()
        for k, v in liq.items():
            record[k] = v
        parts = []
        if "sofr_iorb_bp" in liq: parts.append(f"SOFR-IORB={liq['sofr_iorb_bp']:+.1f}bp")
        if "reserves"     in liq: parts.append(f"WRESBAL={liq['reserves']:.0f}B")
        if "dw"           in liq: parts.append(f"DW={liq['dw']:.2f}B")
        if "onrrp"        in liq: parts.append(f"ONRRP={liq['onrrp']:.1f}B")
        if "tga"          in liq: parts.append(f"TGA={liq['tga']:.3f}T")
        if "srf"          in liq: parts.append(f"SRF={liq['srf']:.3f}B")
        print(f"   {'  '.join(parts)}  ✓" if parts else "   (部分字段获取失败)")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("LIQ_BUNDLE")

    # SOFR 曲线形态（依赖 sofr=Term 3M + liq_bundle sofr_1m/sofr_6m）
    s3m = record.get("sofr")
    s1m = record.get("sofr_1m")
    s6m = record.get("sofr_6m")
    if s1m is not None and s3m is not None:
        record["sofr_fwd1x3"] = round((s3m - s1m) * 100, 1)
    if s6m is not None and s3m is not None:
        record["sofr_fwd3x6"] = round((s6m - s3m) * 100, 1)
    if "sofr_fwd1x3" in record and "sofr_fwd3x6" in record:
        record["sofr_curve_shape"] = round(record["sofr_fwd1x3"] - record["sofr_fwd3x6"], 1)
    if "sofr_curve_shape" in record:
        print(f"   SOFR曲线 1x3={record.get('sofr_fwd1x3',0):+.1f}bp  "
              f"3x6={record.get('sofr_fwd3x6',0):+.1f}bp  "
              f"形态={record['sofr_curve_shape']:+.1f}bp")

    # ── ④ ETF ────────────────────────────────────────────────────────
    print("\n④ ETF 收盘价")
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

    # ── ⑤ 中美利差 10Y CN−US ────────────────────────────────────────
    print("\n⑤ 中美利差 10Y CN−US")
    try:
        cnus = fetch_cn_us_spread()
        record["cnus_bp"] = cnus["spread_bp"]
        print(f"   CN {cnus['cn10y']:.3f}%  US {cnus['us10y']:.3f}%"
              f"  利差 {cnus['spread_bp']:+.1f}bp  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        hist_tmp = load_history()
        last = next((r["cnus_bp"] for r in reversed(hist_tmp) if r.get("cnus_bp") is not None), None)
        if last is not None:
            record["cnus_bp"] = last
            print(f"   使用上次已知值 {last:+.1f}bp（填充）")
        else:
            print("   无历史数据，跳过（非必要字段）")

    # ── ⑥ CFTC 原油投机净多仓 ──────────────────────────────────────────
    print("\n⑥ CFTC 原油投机净多仓（Legacy COT）")
    try:
        cftc = fetch_cftc_crude()
        record["cftc_net"] = cftc["net_long"]
        record["cftc_chg"] = cftc["chg"]
        sign = "+" if cftc["chg"] >= 0 else ""
        print(f"   净多仓 {cftc['net_long']:,}  WoW {sign}{cftc['chg']:,}"
              f"  ({cftc['date']})  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        hist_tmp = load_history()
        for fld in ("cftc_net", "cftc_chg"):
            last = next((r[fld] for r in reversed(hist_tmp)
                         if r.get(fld) is not None), None)
            if last is not None:
                record[fld] = last
                print(f"   {fld} 使用上次已知值 {last}（填充）")

    # ── ⑦ WTI 1-5月差 ─────────────────────────────────────────────────
    print("\n⑦ WTI 1-5月差（CME期货）")
    try:
        ws = fetch_wti_spread()
        record["wti_m15"] = ws["spread"]
        structure = "Backwardation" if ws["spread"] > 0 else "Contango"
        print(f"   M1 ${ws['front']:.2f}  M5 ${ws['m5']:.2f}"
              f"  差 {ws['spread']:+.2f}  {structure}  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        hist_tmp = load_history()
        last = next((r["wti_m15"] for r in reversed(hist_tmp)
                     if r.get("wti_m15") is not None), None)
        if last is not None:
            record["wti_m15"] = last
            print(f"   使用上次已知值 {last:+.2f}（填充）")

    # ── ⑧ Brent−WTI 价差 ──────────────────────────────────────────────
    print("\n⑧ Brent−WTI 价差")
    try:
        bwti = fetch_brent_wti()
        record["brent_wti"] = bwti["spread"]
        record["wti_price"] = bwti["wti"]
        print(f"   Brent ${bwti['brent']:.2f}  WTI ${bwti['wti']:.2f}"
              f"  差 {bwti['spread']:+.2f}  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        hist_tmp = load_history()
        for fld in ("brent_wti", "wti_price"):
            last = next((r[fld] for r in reversed(hist_tmp)
                         if r.get(fld) is not None), None)
            if last is not None:
                record[fld] = last

    # ── ⑨ VIX 指数 ────────────────────────────────────────────────────
    print("\n⑨ VIX 指数")
    try:
        vix_d = fetch_vix()
        record["vix"] = vix_d["vix"]
        print(f"   VIX = {vix_d['vix']:.2f}  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        hist_tmp = load_history()
        last = next((r["vix"] for r in reversed(hist_tmp)
                     if r.get("vix") is not None), None)
        if last is not None:
            record["vix"] = last
            print(f"   使用上次已知值 {last:.2f}（填充）")

    # ── ⑩ 美债收益率曲线 ──────────────────────────────────────────────
    print("\n⑩ 美债收益率曲线（FRED）")
    _yield_fields = ("us_2y", "us_10y", "spread_2y10y", "spread_3m10y",
                     "bei_10y", "tips_10y", "cn10y_fred", "cnus_fred")
    try:
        uy = fetch_us_yields()
        for fld in _yield_fields:
            record[fld] = uy[fld]
        print(f"   2Y {uy['us_2y']:.3f}%  10Y {uy['us_10y']:.3f}%"
              f"  2Y10Y {uy['spread_2y10y']:+.3f}%"
              f"  3M10Y {uy['spread_3m10y']:+.3f}%"
              f"  BEI {uy['bei_10y']:.3f}%  TIPS {uy['tips_10y']:.3f}%  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        hist_tmp = load_history()
        for fld in _yield_fields:
            last = next((r[fld] for r in reversed(hist_tmp)
                         if r.get(fld) is not None), None)
            if last is not None:
                record[fld] = last
                print(f"   {fld} 使用上次已知值 {last:.3f}（填充）")

    # ── ⑪ 外汇信号 ────────────────────────────────────────────────────
    print("\n⑪ 外汇信号（DXY / USDJPY / AUDJPY + 月频利差）")
    try:
        fx = fetch_fx_signals(
            us_2y_val  = record.get("us_2y"),
            us_10y_val = record.get("us_10y"),
        )
        _fx_fields = ("dxy", "usdjpy", "usdjpy_1d_chg", "audjpy", "audjpy_5d_chg",
                      "jp_2y", "de_10y", "it_10y",
                      "us_jp_2y_spread", "de_us_spread", "it_de_spread")
        for fld in _fx_fields:
            if fld in fx:
                record[fld] = fx[fld]
        parts = []
        if fx.get("dxy"):          parts.append(f"DXY={fx['dxy']:.2f}")
        if fx.get("usdjpy"):       parts.append(f"USDJPY={fx['usdjpy']:.2f}")
        if fx.get("audjpy"):       parts.append(f"AUDJPY={fx['audjpy']:.2f}")
        if fx.get("us_jp_2y_spread") is not None:
            parts.append(f"美日2Y利差={fx['us_jp_2y_spread']:.3f}%")
        if fx.get("de_us_spread") is not None:
            parts.append(f"德美10Y={fx['de_us_spread']:.3f}%")
        if fx.get("it_de_spread") is not None:
            parts.append(f"德意10Y={fx['it_de_spread']:.3f}%")
        print(f"   {'  '.join(parts)}  ✓" if parts else "   (部分字段获取失败)")
    except Exception as e:
        print(f"   ERROR: {e}")
        hist_tmp = load_history()
        for fld in ("dxy", "usdjpy", "audjpy", "us_jp_2y_spread",
                    "de_us_spread", "it_de_spread"):
            last = next((r[fld] for r in reversed(hist_tmp)
                         if r.get(fld) is not None), None)
            if last is not None:
                record[fld] = last

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
    history = backfill_etf_history(history)      # 不足 60 条时自动回填 ETF 历史
    history = backfill_yields_history(history)   # us_2y/cnus_fred 不足 60 条时回填
    history = backfill_fx_history(history)       # dxy 不足 60 条时回填外汇信号历史
    history = save_history(history, record)
    update_index_html(history)

    if errors:
        print(f"\n  注意：{', '.join(errors)} 数据获取失败，其余已写入")
    else:
        print("\n  全部完成 ✓")

    # ── 飞书推送 ──────────────────────────────────────────────────────
    print("\n── 飞书推送 ────────────────────────────────────────────────")
    push_feishu_unified(history, os.environ.get("WEBHOOK_URL", ""))


if __name__ == "__main__":
    main()
