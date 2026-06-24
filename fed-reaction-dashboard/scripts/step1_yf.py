"""
Step 1: 纯 yfinance 数据拉取（不 import futu，避免网络冲突）
输出: _yf_result.json
v2: +VIX3M(^VXV) + HYG/LQD 历史z-score(252日)
"""
import json, sys, os
import numpy as np

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yfinance as yf

results, vix, vix3m, hyg_lqd_z = {}, {}, {}, {}

# ── 美债收益率 ──
for name, sym in [("10Y","^TNX"),("5Y","^FVX"),("30Y","^TYX"),("13W","^IRX")]:
    try:
        h = yf.Ticker(sym).history(period="25d")  # 扩展至25d供vol计算用
        if len(h) >= 2:
            closes = h["Close"].values.astype(float)
            c, p = closes[-1], closes[-2]
            d5 = closes[-min(5,len(closes))]
            # 20日波动率 (bp)
            if len(closes) >= 10:
                daily_chg = np.diff(closes[-21:]) * 100  # bp
                vol_20d = round(float(np.std(daily_chg)), 2) if len(daily_chg) > 0 else None
            else:
                vol_20d = None
            results[name] = {
                "value": round(c, 3), "prev": round(p, 3), "d5": round(d5, 3),
                "chg_d": round((c-p)*100, 1), "chg_5d": round((c-d5)*100, 1),
                "vol_20d_bp": vol_20d,
            }
            print(f"  {name}: {c:.3f}% (日{results[name]['chg_d']:+.1f}bp / 5日{results[name]['chg_5d']:+.1f}bp / vol20d={vol_20d}bp)")
        else:
            results[name] = {"error": f"data_short (len={len(h)})"}
    except Exception as e:
        results[name] = {"error": str(e)}
        print(f"  {name}: ERROR {e}")

# ── VIX ──
try:
    h = yf.Ticker("^VIX").history(period="5d")
    if len(h) >= 2:
        c, p = float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2])
        vix = {"value": round(c, 2), "chg": round(c-p, 2)}
        print(f"  VIX: {c} (日{vix['chg']:+.2f})")
    else:
        vix = {"error": f"data_short"}
except Exception as e:
    vix = {"error": str(e)}

# ── VIX3M (^VIX3M, 原 ^VXV 已弃用) ──
try:
    h = yf.Ticker("^VIX3M").history(period="1mo")
    if len(h) >= 2:
        c, p = float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2])
        vix3m = {"value": round(c, 2), "chg": round(c-p, 2)}
        print(f"  VIX3M: {c} (日{vix3m['chg']:+.2f})")
    else:
        vix3m = {"error": "data_short"}
except Exception as e:
    vix3m = {"error": str(e)}

# ── HYG / LQD 历史 z-score (252日) ──
for ticker in ["HYG", "LQD"]:
    try:
        h = yf.Ticker(ticker).history(period="1y")
        if len(h) >= 60:
            closes = h["Close"].values.astype(float)
            latest = closes[-1]
            # 252日z-score
            ma = np.mean(closes[-252:])
            sd = np.std(closes[-252:])
            z_252 = round((latest - ma) / sd, 3) if sd > 0 else 0
            # 20日趋势 (线性回归斜率 / mean)
            if len(closes) >= 20:
                recent = closes[-20:]
                x = np.arange(len(recent))
                slope = np.polyfit(x, recent, 1)[0]
                trend_20d = round(slope / np.mean(recent) * 100, 3)  # % per day
            else:
                trend_20d = 0
            hyg_lqd_z[ticker] = {
                "price": round(latest, 2),
                "z_252": z_252,
                "trend_20d_pct": trend_20d,
                "chg_d": round(float((closes[-1] - closes[-2]) / closes[-2] * 100), 2) if len(closes) >= 2 else None,
            }
            print(f"  {ticker}: ${latest:.2f} z_252={z_252:+.2f} 20d_trend={trend_20d:+.3f}%/d")
        else:
            hyg_lqd_z[ticker] = {"error": f"data_short (len={len(h)})"}
    except Exception as e:
        hyg_lqd_z[ticker] = {"error": str(e)}

# ── 输出 ──
hyg_z = hyg_lqd_z.get("HYG", {}).get("z_252")
lqd_z = hyg_lqd_z.get("LQD", {}).get("z_252")
hyg_lqd_spread_z = round(hyg_z - lqd_z, 3) if hyg_z is not None and lqd_z is not None else None

out = {
    "yields": results,
    "vix": vix,
    "vix3m": vix3m,
    "hyg_lqd_z": hyg_lqd_z,
    "hyg_lqd_spread_z": hyg_lqd_spread_z,
}
with open("_yf_result.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\n[yf] 已保存 _yf_result.json (v2 expanded)")
