#!/usr/bin/env python3
"""
fetch_liquidity.py
────────────────────────────────────────────────────────────────
抓取美元流动性指标：
  ① ON RRP        FRED: RRPONTSYD       (daily, billions)
  ② 联储储备金    FRED: WRESBAL         (weekly, billions)
  ③ TGA 财政账户  FRED: WTREGEN         (weekly, billions)
  ④ SOFR          FRED: SOFR            (daily)
  ⑤ IORB          FRED: IORB            (daily)
  ⑥ EFFR          FRED: FEDFUNDS        (daily)
  ⑦ 3M SOFR avg   FRED: SOFR90DAYAVG   (daily)
  ⑧ 贴现窗口      FRED: DPCREDIT        (weekly, billions)
  ⑨ SRF           Federal Reserve H.4.1 HTML parse

写入：
  data/liquidity_history.json
  liquidity.html  (替换 JS 数据块)

依赖：pip install requests beautifulsoup4
"""

import os
import sys
import json
import re
import requests
from datetime import date, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT        = Path(__file__).parent
HISTORY_FILE     = REPO_ROOT / "data" / "liquidity_history.json"
LIQUIDITY_HTML   = REPO_ROOT / "liquidity.html"
MAX_HIST_DAYS    = 365
FRED_KEY         = os.environ.get("FRED_API_KEY", "")


# ────────────────────────────────────────────────────────────────────
# FRED 通用拉取
# ────────────────────────────────────────────────────────────────────
def fred_fetch(series_id: str, limit: int = 10) -> list[dict]:
    """返回最近 limit 条观测值 [{"date": ..., "value": ...}, ...]"""
    if not FRED_KEY:
        raise EnvironmentError("FRED_API_KEY not set")
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id":  series_id,
            "api_key":    FRED_KEY,
            "file_type":  "json",
            "sort_order": "desc",
            "limit":      str(limit),
        },
        timeout=20,
    )
    r.raise_for_status()
    obs = r.json().get("observations", [])
    result = []
    for o in obs:
        if o["value"] != ".":
            result.append({"date": o["date"], "value": float(o["value"])})
    return result


def fred_latest(series_id: str) -> tuple[str, float] | tuple[None, None]:
    """返回 (date_str, value) 或 (None, None)"""
    try:
        obs = fred_fetch(series_id, limit=5)
        if obs:
            return obs[0]["date"], obs[0]["value"]
    except Exception as e:
        print(f"   FRED {series_id} 失败: {e}")
    return None, None


# ────────────────────────────────────────────────────────────────────
# H.4.1 SRF 解析
# ────────────────────────────────────────────────────────────────────
def fetch_srf_from_h41() -> float | None:
    """从 Fed H.4.1 Release 解析 SRF 使用量（十亿美元）"""
    try:
        from bs4 import BeautifulSoup
        url = "https://www.federalreserve.gov/releases/h41/current/h41.htm"
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # 查找 "Repurchase agreements" 行（SRF 属于 repos）
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            label = cells[0].get_text(strip=True).lower()
            if "repurchase" in label and "standing" in label:
                # 取最新一列数值（单位为百万，需÷1000转十亿）
                for c in cells[1:]:
                    txt = c.get_text(strip=True).replace(",", "")
                    if txt and txt.lstrip("-").replace(".", "").isdigit():
                        return round(float(txt) / 1000, 3)
        # 如果没有"standing repo"行，SRF 当前使用量为零
        return 0.0
    except ImportError:
        print("   beautifulsoup4 未安装，SRF 返回 0.0")
        return 0.0
    except Exception as e:
        print(f"   H.4.1 解析失败: {e}")
        return 0.0


# ────────────────────────────────────────────────────────────────────
# 历史数据加载/保存
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
    print(f"  liquidity_history.json: {action} {today}（共 {len(history)} 条）")
    return history


# ────────────────────────────────────────────────────────────────────
# 更新 liquidity.html
# ────────────────────────────────────────────────────────────────────
def update_liquidity_html(history: list) -> bool:
    if not LIQUIDITY_HTML.exists():
        print(f"  WARNING: {LIQUIDITY_HTML} 不存在，跳过")
        return False

    rows  = history[-60:]
    today_str = date.today().isoformat()

    def col(key, default=None):
        return [r.get(key, default) for r in rows]

    dates      = col("date", "")
    onrrp      = col("onrrp")
    reserves   = col("reserves")
    tga        = col("tga")
    tga_wow    = col("tga_wow")
    sofr       = col("sofr")
    iorb       = col("iorb")
    effr       = col("effr")
    sofr90     = col("sofr90")
    srf        = col("srf")
    dw         = col("dw")

    latest     = rows[-1] if rows else {}

    new_block = (
        f"// ── 实时数据（fetch_liquidity.py 写入 {today_str}）──\n"
        f"const LIQ_DATES={json.dumps(dates)};\n"
        f"const LIQ_ONRRP={json.dumps(onrrp)};\n"
        f"const LIQ_RESERVES={json.dumps(reserves)};\n"
        f"const LIQ_TGA={json.dumps(tga)};\n"
        f"const LIQ_TGA_WOW={json.dumps(tga_wow)};\n"
        f"const LIQ_SOFR={json.dumps(sofr)};\n"
        f"const LIQ_IORB={json.dumps(iorb)};\n"
        f"const LIQ_EFFR={json.dumps(effr)};\n"
        f"const LIQ_SOFR90={json.dumps(sofr90)};\n"
        f"const LIQ_SRF={json.dumps(srf)};\n"
        f"const LIQ_DW={json.dumps(dw)};\n"
        f"\nconst LIQ_LATEST={json.dumps(latest, ensure_ascii=False)};"
    )

    html = LIQUIDITY_HTML.read_text(encoding="utf-8")
    pattern = re.compile(r"// ── 实时数据.*?const LIQ_LATEST=\{[^;]*\};", re.DOTALL)
    new_html, count = pattern.subn(new_block, html)
    if count == 0:
        print("  WARNING: liquidity.html 数据块未匹配，跳过")
        return False
    LIQUIDITY_HTML.write_text(new_html, encoding="utf-8")
    print(f"  liquidity.html: 已写入 {len(rows)} 条（最新 {today_str}）")
    return True


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'═' * 58}")
    print(f"  美元流动性监控   {date.today()}")
    print(f"{'═' * 58}\n")

    today_str = date.today().isoformat()
    record    = {"date": today_str}
    errors    = []

    # ── ① ON RRP ─────────────────────────────────────────────────────
    print("① ON RRP (RRPONTSYD)")
    try:
        d, v = fred_latest("RRPONTSYD")
        record["onrrp"] = v
        print(f"   {v:.3f} B  ({d})  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("ONRRP")

    # ── ② 储备金 ──────────────────────────────────────────────────────
    print("\n② Bank Reserves (WRESBAL)")
    try:
        hist_prev = load_history()
        d, v = fred_latest("WRESBAL")
        record["reserves"] = v
        print(f"   {v:.0f} B  ({d})  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("WRESBAL")

    # ── ③ TGA ─────────────────────────────────────────────────────────
    print("\n③ TGA (WTREGEN)")
    try:
        obs = fred_fetch("WTREGEN", limit=3)
        if len(obs) >= 1:
            tga_now = obs[0]["value"]
            tga_prev = obs[1]["value"] if len(obs) >= 2 else None
            tga_wow = round(tga_now - tga_prev, 2) if tga_prev is not None else 0.0
            record["tga"]     = tga_now
            record["tga_wow"] = tga_wow
            print(f"   TGA={tga_now:.1f}B  WoW={tga_wow:+.1f}B  ({obs[0]['date']})  ✓")
        else:
            errors.append("TGA")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("TGA")

    # ── ④ SOFR ────────────────────────────────────────────────────────
    print("\n④ SOFR (daily)")
    try:
        d, v = fred_latest("SOFR")
        record["sofr"] = v
        print(f"   {v:.4f}%  ({d})  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("SOFR")

    # ── ⑤ IORB ────────────────────────────────────────────────────────
    print("\n⑤ IORB")
    try:
        d, v = fred_latest("IORB")
        record["iorb"] = v
        print(f"   {v:.4f}%  ({d})  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("IORB")

    # ── ⑥ EFFR ────────────────────────────────────────────────────────
    print("\n⑥ EFFR (FEDFUNDS)")
    try:
        d, v = fred_latest("FEDFUNDS")
        record["effr"] = v
        print(f"   {v:.4f}%  ({d})  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("EFFR")

    # ── ⑦ 3M SOFR avg ─────────────────────────────────────────────────
    print("\n⑦ SOFR90DAYAVG")
    try:
        d, v = fred_latest("SOFR90DAYAVG")
        record["sofr90"] = v
        futures_price = round(100 - v, 4)
        print(f"   {v:.4f}%  期货价={futures_price:.4f}  ({d})  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("SOFR90")

    # ── ⑧ 贴现窗口 ────────────────────────────────────────────────────
    print("\n⑧ DW (DPCREDIT)")
    try:
        d, v = fred_latest("DPCREDIT")
        record["dw"] = v
        print(f"   {v:.3f} B  ({d})  ✓")
    except Exception as e:
        print(f"   ERROR: {e}")
        errors.append("DW")

    # ── ⑨ SRF H.4.1 ──────────────────────────────────────────────────
    print("\n⑨ SRF (H.4.1)")
    srf_val = fetch_srf_from_h41()
    record["srf"] = srf_val if srf_val is not None else 0.0
    print(f"   {record['srf']:.3f} B  ✓")

    # ── 利差计算 ──────────────────────────────────────────────────────
    if "sofr" in record and "iorb" in record:
        bp = round((record["sofr"] - record["iorb"]) * 100, 1)
        record["sofr_iorb_bp"] = bp
        signal = "偏紧" if bp > 15 else ("预警" if bp > 10 else "正常")
        print(f"\n  SOFR-IORB 利差: {bp:+.1f}bp  → {signal}")

    if "sofr" in record and "effr" in record:
        bp2 = round((record["sofr"] - record["effr"]) * 100, 1)
        record["sofr_effr_bp"] = bp2
        print(f"  SOFR-EFFR 利差: {bp2:+.1f}bp")

    # ── 写入 ──────────────────────────────────────────────────────────
    print("\n── 写入 ──────────────────────────────────────────────────")
    history = load_history()
    history = save_history(history, record)
    update_liquidity_html(history)

    if errors:
        print(f"\n  注意：{', '.join(errors)} 数据获取失败，其余已写入")
    else:
        print("\n  全部完成 ✓")


if __name__ == "__main__":
    main()
