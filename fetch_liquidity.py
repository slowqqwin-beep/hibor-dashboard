#!/usr/bin/env python3
"""
fetch_liquidity.py  v2
────────────────────────────────────────────────────────────────
自动抓取美元流动性指标（无任何手动输入）：

FRED API (env: FRED_API_KEY):
  ①  ON RRP          RRPONTSYD        daily   B
  ②  Bank Reserves   WRESBAL          weekly  M→B (/1000)
  ③  TGA             WTREGEN          weekly  M→B (/1000)
  ④  SOFR            SOFR             daily   %
  ⑤  IORB            IORB             daily   %
  ⑥  EFFR            FEDFUNDS         daily   %
  ⑦  3M SOFR Rate    SR3M → SOFR90DAYAVG (fallback)  daily  %
  ⑧  DW 贴现窗口     DPCREDIT         weekly  B

H.4.1 Fed Release:
  ⑨  SRF 常备回购便利  https://www.federalreserve.gov/releases/h41/current/h41.htm
                        HTML parse → M→B (/1000)

yfinance（试抓，失败 → None，UI 显示「数据待接入」）:
  ⑩  JPY/USD 3M 货币基差  CME 6J 期货隐含 + 利率平价近似

衍生计算:
  sofr_iorb_bp   = (SOFR - IORB) × 100
  sofr_effr_bp   = (SOFR - EFFR) × 100

写入:
  data/liquidity_history.json
  liquidity.html  (替换 JS 数据块)

依赖: pip install requests beautifulsoup4 yfinance
"""

import os, sys, json, re, requests
from datetime import date
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT      = Path(__file__).parent
HISTORY_FILE   = REPO_ROOT / "data" / "liquidity_history.json"
LIQUIDITY_HTML = REPO_ROOT / "liquidity.html"
MAX_HIST_DAYS  = 365
FRED_KEY       = os.environ.get("FRED_API_KEY", "")


# ════════════════════════════════════════════════════════════════════
# FRED 通用
# ════════════════════════════════════════════════════════════════════
def fred_obs(series_id: str, limit: int = 5) -> list[dict]:
    """返回最近 limit 条有效观测 [{"date":…, "value":…}]，降序排列。"""
    if not FRED_KEY:
        raise EnvironmentError("FRED_API_KEY not set")
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params=dict(series_id=series_id, api_key=FRED_KEY,
                    file_type="json", sort_order="desc", limit=str(limit)),
        timeout=20,
    )
    r.raise_for_status()
    return [
        {"date": o["date"], "value": float(o["value"])}
        for o in r.json().get("observations", [])
        if o["value"] != "."
    ]


def fred_latest(series_id: str) -> tuple:
    """返回 (date_str, float) 或 (None, None)。"""
    try:
        obs = fred_obs(series_id, limit=5)
        if obs:
            return obs[0]["date"], obs[0]["value"]
    except Exception as e:
        print(f"   FRED {series_id} 失败: {e}")
    return None, None


def fred_latest_billion(series_id: str) -> tuple:
    """同上，但把原始值 ÷1000（百万→十亿）。"""
    d, v = fred_latest(series_id)
    if v is not None:
        return d, round(v / 1000, 3)
    return None, None


# ════════════════════════════════════════════════════════════════════
# ① ON RRP
# ════════════════════════════════════════════════════════════════════
def fetch_onrrp():
    print("① ON RRP (RRPONTSYD)")
    d, v = fred_latest("RRPONTSYD")
    if v is not None:
        print(f"   {v:.3f} B  ({d})  ✓")
    return v


# ════════════════════════════════════════════════════════════════════
# ② Bank Reserves  (WRESBAL 单位：百万 → ÷1000 = 十亿)
# ════════════════════════════════════════════════════════════════════
def fetch_reserves():
    print("\n② Bank Reserves (WRESBAL  M→B)")
    d, v = fred_latest_billion("WRESBAL")
    if v is not None:
        print(f"   {v:.1f} B  ({d})  ✓")
    return v


# ════════════════════════════════════════════════════════════════════
# ③ TGA (WTREGEN 单位：百万 → ÷1000 = 十亿)；返回 (tga_now, tga_wow)
# ════════════════════════════════════════════════════════════════════
def fetch_tga():
    print("\n③ TGA (WTREGEN  M→B)")
    try:
        obs = fred_obs("WTREGEN", limit=4)
        if not obs:
            raise ValueError("无数据")
        tga_now  = round(obs[0]["value"] / 1000, 3)
        tga_prev = round(obs[1]["value"] / 1000, 3) if len(obs) >= 2 else None
        tga_wow  = round(tga_now - tga_prev, 3) if tga_prev is not None else 0.0
        print(f"   TGA={tga_now:.1f}B  WoW={tga_wow:+.1f}B  ({obs[0]['date']})  ✓")
        return tga_now, tga_wow
    except Exception as e:
        print(f"   ERROR: {e}")
        return None, None


# ════════════════════════════════════════════════════════════════════
# ④⑤⑥ SOFR / IORB / EFFR
# ════════════════════════════════════════════════════════════════════
def fetch_rates():
    results = {}
    for label, sid, key in [
        ("④ SOFR",       "SOFR",     "sofr"),
        ("⑤ IORB",       "IORB",     "iorb"),
        ("⑥ EFFR",       "FEDFUNDS", "effr"),
    ]:
        print(f"\n{label} ({sid})")
        d, v = fred_latest(sid)
        if v is not None:
            results[key] = v
            print(f"   {v:.4f}%  ({d})  ✓")
        else:
            print(f"   ERROR: 无数据")
    return results


# ════════════════════════════════════════════════════════════════════
# ⑦ 3M SOFR Rate  SR3M → fallback SOFR90DAYAVG
# ════════════════════════════════════════════════════════════════════
def fetch_sofr3m():
    print("\n⑦ 3M SOFR Rate (SR3M → fallback SOFR90DAYAVG)")
    # 优先尝试 FRED 的 SR3M（CME Term SOFR 3M）
    for sid in ("SR3M", "SOFR90DAYAVG"):
        d, v = fred_latest(sid)
        if v is not None:
            print(f"   {v:.4f}%  ({d})  [{sid}]  ✓")
            return v, sid
    print("   ERROR: SR3M / SOFR90DAYAVG 均无数据")
    return None, None


# ════════════════════════════════════════════════════════════════════
# ⑧ DW 贴现窗口 (DPCREDIT 单位已是十亿)
# ════════════════════════════════════════════════════════════════════
def fetch_dw():
    print("\n⑧ DW 贴现窗口 (DPCREDIT)")
    d, v = fred_latest("DPCREDIT")
    if v is not None:
        print(f"   {v:.3f} B  ({d})  ✓")
    return v


# ════════════════════════════════════════════════════════════════════
# ⑨ SRF — H.4.1 HTML parse
#    URL: https://www.federalreserve.gov/releases/h41/current/h41.htm
#    值在 <TABLE class="statistics"> 中，单位百万 → ÷1000 = 十亿
# ════════════════════════════════════════════════════════════════════
def fetch_srf() -> float:
    """解析 Fed H.4.1，返回 SRF 使用量（十亿美元）；失败或未使用则返回 0.0。"""
    print("\n⑨ SRF 常备回购便利 (H.4.1)")
    url = "https://www.federalreserve.gov/releases/h41/current/h41.htm"
    try:
        from bs4 import BeautifulSoup
        r = requests.get(url, timeout=25,
                         headers={"User-Agent": "Mozilla/5.0 (compatible)"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # ── 策略 1：找含 "standing" 的行（最精确）──
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            label = cells[0].get_text(" ", strip=True).lower()
            if "standing" in label and "repo" in label:
                val = _extract_first_number(cells[1:])
                if val is not None:
                    result = round(val / 1000, 3)
                    print(f"   SRF={result:.3f} B  (standing repo row)  ✓")
                    return result

        # ── 策略 2：在 "供给储备金因素" 区块找 "repurchase agreements" ──
        supply_section = False
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            text = cells[0].get_text(" ", strip=True).lower()
            # 识别区块标题（含 "supplying" 或 "supply" 的行）
            if "supplying" in text or "supply" in text:
                supply_section = True
            elif supply_section and ("absorbing" in text or "absorb" in text):
                supply_section = False          # 离开供给区块
            if supply_section and "repurchase agreement" in text and "reverse" not in text:
                val = _extract_first_number(cells[1:])
                if val is not None:
                    result = round(val / 1000, 3)
                    print(f"   SRF={result:.3f} B  (repurchase agreements row)  ✓")
                    return result

        # ── SRF 当前未动用 → 0.0 ──
        print("   SRF=0.000 B  (H.4.1 未见使用记录，视为零)  ✓")
        return 0.0

    except ImportError:
        print("   WARNING: beautifulsoup4 未安装，SRF=0.0")
        return 0.0
    except Exception as e:
        print(f"   WARNING: H.4.1 解析失败({e})，SRF=0.0")
        return 0.0


def _extract_first_number(cells) -> float | None:
    """从 td 列表中提取第一个有效数字。"""
    for c in cells:
        txt = c.get_text(strip=True).replace(",", "").replace("\xa0", "")
        if not txt or txt in ("-", "n.a.", "ND"):
            continue
        try:
            return float(txt)
        except ValueError:
            continue
    return None


# ════════════════════════════════════════════════════════════════════
# ⑩ JPY/USD 3M 货币基差  (yfinance 近似；失败→None)
#    跨货币基差 = JPY_rate - SOFR - (forward_prem_ann)
#    严格计算需要远期报价，此处用 CME 6J 期货隐含远期 + FRED JPY 利率近似：
#      basis ≈ (6J_forward_implied_rate) - (SOFR) + (spot/forward conversion)
#    若 yfinance 无法取数则返回 None，UI 显示「数据待接入」。
# ════════════════════════════════════════════════════════════════════
def fetch_jpy_basis(sofr_val: float | None = None) -> float | None:
    """
    尝试从 yfinance + FRED 估算 JPY/USD 3M 跨货币基差（bp）。
    无法可靠计算则返回 None。
    """
    print("\n⑩ JPY/USD 3M 货币基差 (yfinance + FRED 近似)")
    try:
        import yfinance as yf

        # 获取 JPY/USD 即期汇率
        spot_df = yf.Ticker("JPY=X").history(period="5d")
        if spot_df.empty:
            raise ValueError("yfinance JPY=X 无数据")
        spot_usdjpy = float(spot_df["Close"].iloc[-1])   # JPY per USD

        # 获取最近一个交割的 CME 6J 期货（近月，e.g. 6JM26）
        # 合约代码：6J + 月份字母 + 年份后2位
        # 尝试当季及下季
        today = date.today()
        # 季度月份: 3=H, 6=M, 9=U, 12=Z
        quarter_months = [(3,'H'),(6,'M'),(9,'U'),(12,'Z')]
        fwd_usdjpy = None
        for month_num, letter in quarter_months:
            yr = today.year if month_num > today.month else today.year + 1
            ticker_sym = f"6J{letter}{str(yr)[-2:]}.CME"
            try:
                df = yf.Ticker(ticker_sym).history(period="5d")
                if not df.empty:
                    fwd_raw = float(df["Close"].iloc[-1])
                    # 6J 期货报价：JPY per USD 的倒数 × 10000
                    # 实际单位：1 contract = 12,500,000 JPY，报价 = USD per JPY × 10^4
                    fwd_usdjpy = 1.0 / (fwd_raw * 1e-4) if fwd_raw > 0 else None
                    print(f"   6J ticker={ticker_sym}  raw={fwd_raw:.4f}  fwd_USDJPY={fwd_usdjpy:.2f}")
                    break
            except Exception:
                continue

        if fwd_usdjpy is None:
            raise ValueError("CME 6J 期货数据不可用")

        # 覆盖利率平价：(Fwd/Spot - 1) × (360/90) × 100  = forward premium (%, ann.)
        days = 91   # 近似 3M
        fwd_prem_pct = ((spot_usdjpy / fwd_usdjpy) - 1) * (360 / days) * 100

        # 近似日本 3M 无风险利率：从 FRED 取 Japan 3M 国债 IR3TIB01JPM156N（月频）
        jpy_rate = None
        try:
            _, jpy_rate = fred_latest("IR3TIB01JPM156N")
        except Exception:
            pass

        if jpy_rate is None:
            raise ValueError("FRED JPY 3M 利率不可用")

        sofr = sofr_val if sofr_val is not None else 4.30  # fallback
        # 近似基差 = JPY_rate - sofr + forward_premium
        # 在 CIP 无套利下 basis=0；实际 basis = 实际forward_prem - CIP_implied_prem
        basis_pct = jpy_rate - sofr + fwd_prem_pct
        basis_bp  = round(basis_pct * 100, 1)
        print(f"   spot={spot_usdjpy:.2f}  fwd_prem={fwd_prem_pct:+.3f}%"
              f"  JPY_rate={jpy_rate:.4f}%  SOFR={sofr:.4f}%"
              f"  basis≈{basis_bp:+.1f}bp  ✓")
        return basis_bp

    except ImportError:
        print("   yfinance 未安装，JPY basis=None（数据待接入）")
        return None
    except Exception as e:
        print(f"   yfinance/FRED 无法计算 JPY basis: {e}")
        print("   → JPY basis=None（数据待接入）")
        return None


# ════════════════════════════════════════════════════════════════════
# 历史文件 I/O
# ════════════════════════════════════════════════════════════════════
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
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  liquidity_history.json: {action} {today}（共 {len(history)} 条）")
    return history


# ════════════════════════════════════════════════════════════════════
# 注入 liquidity.html
# ════════════════════════════════════════════════════════════════════
def _load_hibor_spread(dates: list) -> list:
    """按 dates 对齐从 history.json 读取 HIBOR-SOFR spread_bp。"""
    hibor_file = REPO_ROOT / "data" / "history.json"
    if not hibor_file.exists():
        return [None] * len(dates)
    try:
        hist     = json.loads(hibor_file.read_text(encoding="utf-8"))
        hib_map  = {r["date"]: r.get("spread_bp") for r in hist}
        return [hib_map.get(d) for d in dates]
    except Exception:
        return [None] * len(dates)


def update_liquidity_html(history: list) -> bool:
    if not LIQUIDITY_HTML.exists():
        print(f"  WARNING: {LIQUIDITY_HTML} 不存在，跳过")
        return False

    rows      = history[-60:]
    today_str = date.today().isoformat()

    def col(key):
        return [r.get(key) for r in rows]

    dates   = col("date")
    latest  = dict(rows[-1]) if rows else {}

    # HIBOR-SOFR 利差（按日期对齐）
    hibor_spread = _load_hibor_spread(dates)
    latest["hibor_spread_bp"] = hibor_spread[-1] if hibor_spread else None

    new_block = (
        f"// ── 实时数据（fetch_liquidity.py 写入 {today_str}）──\n"
        f"const LIQ_DATES={json.dumps(dates)};\n"
        f"const LIQ_ONRRP={json.dumps(col('onrrp'))};\n"
        f"const LIQ_RESERVES={json.dumps(col('reserves'))};\n"
        f"const LIQ_TGA={json.dumps(col('tga'))};\n"
        f"const LIQ_TGA_WOW={json.dumps(col('tga_wow'))};\n"
        f"const LIQ_SOFR={json.dumps(col('sofr'))};\n"
        f"const LIQ_IORB={json.dumps(col('iorb'))};\n"
        f"const LIQ_EFFR={json.dumps(col('effr'))};\n"
        f"const LIQ_SOFR90={json.dumps(col('sofr90'))};\n"
        f"const LIQ_SRF={json.dumps(col('srf'))};\n"
        f"const LIQ_DW={json.dumps(col('dw'))};\n"
        f"const LIQ_JPY={json.dumps(col('jpy'))};\n"
        f"const LIQ_HIBOR_SPREAD={json.dumps(hibor_spread)};\n"
        f"\nconst LIQ_LATEST={json.dumps(latest, ensure_ascii=False)};"
    )

    html = LIQUIDITY_HTML.read_text(encoding="utf-8")
    pattern = re.compile(
        r"// ── 实时数据.*?const LIQ_LATEST=\{[^;]*\};",
        re.DOTALL
    )
    new_html, count = pattern.subn(new_block, html)
    if count == 0:
        print("  WARNING: liquidity.html 数据块未匹配，跳过")
        return False
    LIQUIDITY_HTML.write_text(new_html, encoding="utf-8")
    print(f"  liquidity.html: 已写入 {len(rows)} 条（最新 {today_str}）")
    return True


# ════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════
def main():
    print(f"\n{'═' * 60}")
    print(f"  美元流动性全量抓取   {date.today()}")
    print(f"{'═' * 60}\n")

    today_str = date.today().isoformat()
    record    = {"date": today_str}
    errors    = []

    # ── ① ON RRP ──────────────────────────────────────────────────────
    v = fetch_onrrp()
    if v is not None: record["onrrp"] = v
    else: errors.append("ONRRP")

    # ── ② 储备金 ────────────────────────────────────────────────────────
    v = fetch_reserves()
    if v is not None: record["reserves"] = v
    else: errors.append("WRESBAL")

    # ── ③ TGA ───────────────────────────────────────────────────────────
    tga_now, tga_wow = fetch_tga()
    if tga_now is not None:
        record["tga"]     = tga_now
        record["tga_wow"] = tga_wow
    else:
        errors.append("TGA")

    # ── ④⑤⑥ SOFR / IORB / EFFR ────────────────────────────────────────
    rates = fetch_rates()
    record.update(rates)
    for key in ("sofr", "iorb", "effr"):
        if key not in record:
            errors.append(key.upper())

    # ── ⑦ 3M SOFR Rate (SR3M→SOFR90DAYAVG) ──────────────────────────
    sofr3m_val, sofr3m_src = fetch_sofr3m()
    if sofr3m_val is not None:
        record["sofr90"] = sofr3m_val
        record["sofr90_src"] = sofr3m_src
    else:
        errors.append("SR3M")

    # ── ⑧ DW ────────────────────────────────────────────────────────────
    v = fetch_dw()
    if v is not None: record["dw"] = v
    else: errors.append("DW")

    # ── ⑨ SRF ───────────────────────────────────────────────────────────
    record["srf"] = fetch_srf()

    # ── ⑩ JPY basis ─────────────────────────────────────────────────────
    jpy_val = fetch_jpy_basis(sofr_val=record.get("sofr"))
    record["jpy"] = jpy_val   # None = 数据待接入

    # ── 衍生利差 ─────────────────────────────────────────────────────────
    print("\n── 衍生计算 ──────────────────────────────────────────────")
    if "sofr" in record and "iorb" in record:
        bp = round((record["sofr"] - record["iorb"]) * 100, 1)
        record["sofr_iorb_bp"] = bp
        level = "警戒" if bp > 15 else ("预警" if bp > 10 else "正常")
        print(f"  SOFR−IORB : {bp:+.1f} bp  → {level}")
    if "sofr" in record and "effr" in record:
        bp2 = round((record["sofr"] - record["effr"]) * 100, 1)
        record["sofr_effr_bp"] = bp2
        print(f"  SOFR−EFFR : {bp2:+.1f} bp")
    if "sofr90" in record:
        fp = round(100 - record["sofr90"], 4)
        record["futures_price"] = fp
        print(f"  3M期货隐含: {fp:.4f}  ({record.get('sofr90_src','?')})")
    if record.get("jpy") is not None:
        jbp = record["jpy"]
        level = "警戒" if jbp < -50 else ("预警" if jbp < -30 else "正常")
        print(f"  JPY basis : {jbp:+.1f} bp  → {level}")
    else:
        print("  JPY basis : 数据待接入")

    # ── 写入 ─────────────────────────────────────────────────────────────
    print("\n── 写入 ──────────────────────────────────────────────────")
    history = load_history()
    history = save_history(history, record)
    update_liquidity_html(history)

    if errors:
        print(f"\n  注意：以下指标获取失败 → {', '.join(errors)}")
    else:
        print("\n  全部完成 ✓")


if __name__ == "__main__":
    main()
