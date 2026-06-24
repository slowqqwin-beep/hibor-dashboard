"""
Fed Reaction Dashboard v2 - 决策流水线
OBSERVE->CANDIDATE->ACTIONABLE 状态机 + 归因判别 + sigma计分 + 闸门 + 行动块
数据源: FutuOpenD + step1_yf.py 缓存
用法: uv run python -X utf8 fed_dashboard.py
输出: fed_reaction_dashboard.md + latest.json + pipeline_result.json
"""
import json, os, sys
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.platform == "win32": sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# === 配置 ===
FUTU_HOST, FUTU_PORT = "127.0.0.1", 11111
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = BASE_DIR / "_yf_result.json"
OUT_DIR = BASE_DIR / "Fed 反应函数雷达" / "fed-reaction-dashboard" / "data"
STATE_PATH = OUT_DIR / "state.json"

def _last_trading_day(ref=None):
    """Return last US trading day (skip Sat/Sun). ref defaults to now."""
    if ref is None:
        ref = datetime.now()
    dt = ref - timedelta(days=1)
    # Monday(0) → Friday, Sunday(6) → Friday
    while dt.weekday() >= 5:  # 5=Sat, 6=Sun
        dt -= timedelta(days=1)
    return dt
POSITION_PATH = OUT_DIR / "position.json"
ARCHIVE_DIR = OUT_DIR / "archive"
SCHEMA_VERSION = "1.0.0"
ABCD_CASC_PATH = BASE_DIR / "casc_state.json"
VIX_HIGH, VIX_LOW = 25, 18
Y10_WARN, Y10_DANGER = 4.60, 4.70
OIL_JUMP = 2.0
FRONT_LEAD_RATIO = 0.60
LONG_LEAD_10Y_MIN, LONG_LEAD_13W_MAX = 5.0, 2.0

FUTU_CODES = {"QQQ":"US.QQQ","SPY":"US.SPY","IWM":"US.IWM","UUP":"US.UUP",
    "GLD":"US.GLD","CL":"US.CL","VXX":"US.VXX","HYG":"US.HYG",
    "LQD":"US.LQD","TLT":"US.TLT","IEF":"US.IEF","SHY":"US.SHY","DX":"US.DX"}

NOW = datetime.now()
TZ_CN = timezone(timedelta(hours=8))
TS_CN = NOW.astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M:%S CST")
TS_UTC = NOW.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

# === 1. 数据获取 ===

def load_cache():
    if not CACHE_PATH.exists():
        print("  [WARN] _yf_result.json missing, run step1_yf.py first")
        return {}, {}, {}, {}, None
    with open(CACHE_PATH, "r", encoding="utf-8") as f: data = json.load(f)
    print("  read _yf_result.json (v2)")
    return (data.get("yields",{}), data.get("vix",{}), data.get("vix3m",{}),
            data.get("hyg_lqd_z",{}), data.get("hyg_lqd_spread_z"))

def fetch_futu_history():
    from futu import OpenQuoteContext, KLType, RET_OK
    q = OpenQuoteContext(FUTU_HOST, FUTU_PORT)
    labels, codes = list(FUTU_CODES.keys()), list(FUTU_CODES.values())
    snap, klines = {}, {}
    ret, data = q.get_market_snapshot(codes)
    if ret == RET_OK:
        for i, lb in enumerate(labels):
            try:
                r = data.iloc[i]
                lp = float(r.get("last_price",0) or 0)
                pc = float(r.get("prev_close_price",0) or 0)
                op = float(r.get("open_price",0) or 0)
                snap[lb] = {"price":round(lp,2),"prev_close":round(pc,2),"open":round(op,2),
                    "chg_d":round((lp-pc)/pc*100,2) if pc else None,
                    "chg_intra":round((lp-op)/op*100,2) if op else None}
            except Exception as e: snap[lb] = {"error":str(e)}
    else:
        for lb in labels: snap[lb] = {"error":f"snap_{ret}"}
    end = NOW.strftime("%Y-%m-%d")
    start = (NOW - timedelta(days=40)).strftime("%Y-%m-%d")
    for lb, code in FUTU_CODES.items():
        try:
            ret, kdata, _ = q.request_history_kline(code, start=start, end=end, ktype=KLType.K_DAY)
            if ret == RET_OK and len(kdata) >= 5:
                closes = kdata["close"].values.astype(float)
                c5 = closes[-min(5,len(closes))]
                klines[lb] = {"chg_5d":round((closes[-1]-c5)/c5*100,2)}
                if len(closes) >= 10:
                    dr = np.diff(closes[-21:])/closes[-21:-1]*100
                    klines[lb]["vol_20d_pct"] = round(float(np.std(dr)),3) if len(dr)>0 else None
                else: klines[lb]["vol_20d_pct"] = None
            else: klines[lb] = {"chg_5d":None,"vol_20d_pct":None}
        except: klines[lb] = {"chg_5d":None,"vol_20d_pct":None}
    q.close()
    return snap, klines

# === 2. 驱动归因判别器 ===

def driver_attribution(yields):
    """归因分类 (v3 σ归一化版, 修复绝对bp阈值对低波动前端系统的偏见)
    - 使用 daily_bp / vol_20d_bp (σ) 替代绝对 bp
    - 加入 5Y 作为前端辅助确认
    - LONG_LEAD: 13W σ < 0.5 AND (10Y σ > 1.0 OR 5Y σ > 1.0) → 前端几乎不动, 长端引领 → risk-off
    - FRONT_LEAD: 13W σ > 1.0 AND (5Y σ > 0.8 OR 10Y σ > 0.8) AND 同向 → 前端引领降息/加息
    """
    y13 = yields.get("13W",{}); y5 = yields.get("5Y",{})
    y10 = yields.get("10Y",{}); y30 = yields.get("30Y",{})

    d13 = y13.get("chg_d",0) if "error" not in y13 else 0
    d5  = y5.get("chg_d",0)  if "error" not in y5  else 0
    d10 = y10.get("chg_d",0) if "error" not in y10 else 0
    d30 = y30.get("chg_d",0) if "error" not in y30 else 0

    v13 = y13.get("vol_20d_bp") or 1.0
    v5  = y5.get("vol_20d_bp")  or 1.0
    v10 = y10.get("vol_20d_bp") or 1.0

    s13 = abs(d13) / v13  # σ
    s5  = abs(d5)  / v5
    s10 = abs(d10) / v10

    # LONG_LEAD: 13W barely moved in its own vol (<0.5σ), but long-end moved >1σ
    if s13 < 0.5 and (s10 > 1.0 or s5 > 1.0):
        rt = "避险久期买盘" if d10 < 0 else "期限溢价/供给冲击"
        return {"type":"LONG_LEAD","label":f"{rt} (长端引领)",
            "detail":f"13W{d13:+.1f}bp({s13:.1f}σ)不动 / 5Y{d5:+.1f}bp({s5:.1f}σ) / 10Y{d10:+.1f}bp({s10:.1f}σ) / 30Y{d30:+.1f}bp -> 鸽派信号无效,改判risk-off",
            "veto":True}

    # FRONT_LEAD: 13W σ > 1.0 AND (5Y or 10Y) σ > 0.8 AND same direction
    if s13 > 1.0 and max(s5, s10) > 0.8 and d13 * d10 > 0:
        label = "降息重定价 (前端引领,BucketA顺风)" if d13 < 0 else "鹰派重定价 (前端引领)"
        return {"type":"FRONT_LEAD","label":label,
            "detail":f"13W{d13:+.1f}bp({s13:.1f}σ) / 5Y{d5:+.1f}bp({s5:.1f}σ) / 10Y{d10:+.1f}bp({s10:.1f}σ) / 30Y{d30:+.1f}bp",
            "veto":False}

    # Legacy FRONT_LEAD ratio check kept as fallback
    if d13*d10 > 0 and abs(d13) >= abs(d10)*FRONT_LEAD_RATIO:
        label = "降息重定价 (前端引领,BucketA顺风)" if d13 < 0 else "鹰派重定价 (前端引领)"
        return {"type":"FRONT_LEAD","label":label,
            "detail":f"13W{d13:+.1f}bp / 5Y{d5:+.1f}bp / 10Y{d10:+.1f}bp / 30Y{d30:+.1f}bp",
            "veto":False}

    return {"type":"NEUTRAL","label":"混合信号",
        "detail":f"13W{d13:+.1f}bp({s13:.1f}σ) / 5Y{d5:+.1f}bp({s5:.1f}σ) / 10Y{d10:+.1f}bp({s10:.1f}σ) / 30Y{d30:+.1f}bp",
        "veto":False}


def validate_attribution(attr, scores, vix, hyg_lqd_z, snap):
    """跨资产一致性验证: 若 attribution 判 risk-off 但权益/VIX/信用一致指向 risk-on, 降级 veto。
    返回: (attr, conflicts) — attr 可能被覆盖, conflicts 写入报告
    """
    if not attr.get("veto"):
        return attr, []

    evidence = []
    vv = vix.get("value", 0)
    vix_chg = vix.get("chg", 0)
    hyg_z = hyg_lqd_z.get("HYG", {}).get("z_252", 0)
    lqd_z = hyg_lqd_z.get("LQD", {}).get("z_252", 0)
    dovish = scores.get("Dovish", 0)
    liq = scores.get("Liquidity", 0)
    growth = scores.get("Growth", 0)

    # Equity: check snap for QQQ/IWM/SHY if available, else use scores
    # VIX decline → risk-on
    if vv > 0 and vix_chg < -1.0:
        evidence.append(f"VIX {vv:.1f} (chg {vix_chg:+.1f}, risk-on)")
    # Credit healthy → risk-on
    if hyg_z >= 0 and lqd_z >= 0:
        evidence.append(f"HYG z={hyg_z:+.2f} LQD z={lqd_z:+.2f} (credit stable)")
    # Strong dovish score without liquidity stress
    if dovish >= 3 and liq == 0 and growth <= 1:
        evidence.append(f"B={dovish}/4 C=0 E={growth}/3 (sharp dovish, no stress)")

    if len(evidence) >= 2:
        conflicts = [
            f"⚠️ Attribution veto ({attr['label']}) contradicted by: {'; '.join(evidence)}",
            f"  → veto overridden: attribution set to NEUTRAL, no veto power"
        ]
        overridden = {
            "type": "LONG_LEAD_OVERRIDE",
            "label": f"{attr['label']} — VETO OVERRIDDEN (risk appetite contradicts)",
            "detail": attr["detail"],
            "veto": False,
            "original_veto": True,
        }
        return overridden, conflicts

    return attr, []

# === 3. sigma归一化计分 ===

def sigma_grade(chg_pct, vol_20d):
    if chg_pct is None or vol_20d is None or vol_20d <= 0: return 0, "?"
    z = abs(chg_pct)/vol_20d
    if z < 0.5: return 0, ""
    elif z < 1.5: return 1, ""
    else: return 1, "强"

def get_vol(lb, klines):
    v = klines.get(lb,{}).get("vol_20d_pct")
    if v and v > 0: return v
    typical = {"SHY":0.15,"IEF":0.35,"TLT":0.90,"GLD":0.80,"QQQ":1.10,
               "SPY":0.70,"IWM":1.20,"HYG":0.30,"LQD":0.40,"UUP":0.30,"CL":2.50,"VXX":4.00}
    return typical.get(lb, 0.50)

def _score_ab(side, snap, klines):
    """A=Hawkish, B=Dovish — v3.5.1: details split scored vs near-miss, mutual exclusivity enforced"""
    items = {"A":[("SHY",-1,"SHY_down=2Y_up"),("UUP",+1,"DXY_up"),("GLD",-1,"Gold_down"),("QQQ",-1,"Nasdaq_weak")],
             "B":[("SHY",+1,"SHY_up=2Y_down"),("UUP",-1,"DXY_down"),("GLD",+1,"Gold_up"),("QQQ",+1,"Nasdaq_strong")]}
    th = {"A":[-0.05,0.05,-0.30,-0.50],"B":[0.05,-0.05,0.30,0.50]}
    s, d_scored, d_miss, strong = 0, [], [], 0
    for i,(lb,direction,desc) in enumerate(items[side]):
        chg = snap.get(lb,{}).get("chg_d")
        if chg is None: continue
        vol = get_vol(lb, klines)
        sc, st = sigma_grade(chg, vol)
        t = th[side][i]
        z = abs(chg)/vol if vol and vol > 0 else 0
        dir_ok = (direction > 0 and chg > t) or (direction < 0 and chg < t)
        if sc > 0 and dir_ok:
            s += 1
            tag = f" ({st})" if st else ""
            d_scored.append(f"{desc} {chg:+.2f}% z={z:.1f}{tag}")
            if st == "强": strong += 1
        else:
            # near-miss: show current value / threshold / sigma — clearly marked
            d_miss.append(f"{desc} {chg:+.2f}% (thresh {t:+.2f}%, z={z:.1f})")
    # v3.5.1: separate scored items from below-threshold to prevent reader confusion
    d = d_scored + (["— 以下未达阈值 —"] + d_miss if d_miss else [])
    return s, d, strong

def score_a(s,k): return _score_ab("A",s,k)
def score_b(s,k): return _score_ab("B",s,k)

def score_c(snap, vix, klines, hyg_lqd_z):
    s, d, strong = 0, [], 0
    vv = vix.get("value")
    if vv is not None:
        if vv > VIX_HIGH: s += 1; d.append(f"VIX={vv}>{VIX_HIGH} panic")
        elif vv > VIX_LOW: d.append(f"VIX={vv}>{VIX_LOW} elevated")
        else: d.append(f"VIX={vv} (thresh >{VIX_LOW})")
    hyg = hyg_lqd_z.get("HYG",{}); lqd = hyg_lqd_z.get("LQD",{})
    if hyg and lqd and "error" not in hyg and "error" not in lqd:
        hz, lz = hyg.get("z_252",0), lqd.get("z_252",0)
        spz = hz - lz
        if spz < -0.5: s += 1; d.append(f"HYG-LQD z-spread={spz:+.2f} (credit stress)")
        else: d.append(f"HYG z={hz:+.2f} LQD z={lz:+.2f} spread={spz:+.2f} (credit neutral)")
    iwm_c = snap.get("IWM",{}).get("chg_d")
    spy_c = snap.get("SPY",{}).get("chg_d")
    if iwm_c is not None and spy_c is not None:
        diff = iwm_c - spy_c
        if diff < -0.3:
            s += 1; d.append(f"IWM-SPY={diff:+.2f}% (small-cap weak)")
        else:
            d.append(f"IWM-SPY={diff:+.2f}% (thresh <-0.30%)")
    return s, d, strong

def score_d(snap, yields, klines):
    s, d, strong = 0, [], 0
    y5_cd = yields.get("5Y",{}).get("chg_5d"); y10_cd = yields.get("10Y",{}).get("chg_5d"); y30_cd = yields.get("30Y",{}).get("chg_5d")
    is_bs = False
    if y5_cd and y10_cd and y30_cd:
        if y30_cd > y10_cd > y5_cd and y30_cd > 5:
            is_bs = True; s += 1; d.append(f"bear-steepen: 30Y_5d={y30_cd:+.1f} > 10Y_5d={y10_cd:+.1f} > 5Y_5d={y5_cd:+.1f}")
        elif y5_cd > y10_cd > y30_cd and y5_cd > 5:
            d.append(f"bear-flatten: 5Y_5d={y5_cd:+.1f} > 10Y_5d={y10_cd:+.1f} > 30Y_5d={y30_cd:+.1f} (Fed repricing)")
        else:
            d.append(f"curve: 5Y_5d={y5_cd:+.1f} 10Y_5d={y10_cd:+.1f} 30Y_5d={y30_cd:+.1f} (no bear-steepen/bear-flatten)")
    tlt_c = snap.get("TLT",{}).get("chg_d")
    if tlt_c is not None and tlt_c < -0.5:
        s += 1; d.append(f"30Y_up TLT{tlt_c:+.2f}%")
    elif tlt_c is not None:
        d.append(f"TLT{tlt_c:+.2f}% (thresh <-0.50%)")
    cl_c = snap.get("CL",{}).get("chg_d")
    if cl_c is not None and cl_c > OIL_JUMP:
        s += 1; d.append(f"WTI+{cl_c:+.2f}% (need BEI confirm)")
    elif cl_c is not None:
        d.append(f"WTI{cl_c:+.2f}% (thresh >+{OIL_JUMP}%)")
    if tlt_c is not None and tlt_c < -0.5 and is_bs:
        s += 1; d.append(f"long-end pressure TLT{tlt_c:+.2f}%")
    if not is_bs: s = min(s, 2)
    return s, d, strong

def score_e(snap, klines):
    s, d, strong = 0, [], 0
    shy_c = snap.get("SHY",{}).get("chg_d"); ief_c = snap.get("IEF",{}).get("chg_d")
    if shy_c and ief_c:
        if shy_c > 0.05 and ief_c > 0.05:
            s += 1; d.append(f"SHY&IEF both up: SHY{shy_c:+.2f}% IEF{ief_c:+.2f}% (2Y&10Y down)")
        else:
            d.append(f"SHY{shy_c:+.2f}% IEF{ief_c:+.2f}% (need both >+0.05%)")
    iwm_c = snap.get("IWM",{}).get("chg_d"); spy_c = snap.get("SPY",{}).get("chg_d")
    if iwm_c and spy_c:
        diff = iwm_c - spy_c
        if diff < -0.3:
            s += 1; d.append(f"small-cap lag IWM-SPY={diff:+.2f}%")
        else:
            d.append(f"IWM-SPY={diff:+.2f}% (thresh <-0.30%)")
    qqq_c = snap.get("QQQ",{}).get("chg_d")
    if qqq_c and iwm_c:
        div = abs(qqq_c-iwm_c)
        if div > 1.0:
            s += 1; d.append(f"QQQ-IWM divergence {qqq_c-iwm_c:+.2f}%")
        else:
            d.append(f"QQQ-IWM={qqq_c-iwm_c:+.2f}% (thresh >1.0%)")
    return s, d, strong

# === 4. 闸门检查 ===

def check_gates(vix, vix3m, casc_state, hyg_lqd_z):
    gates = {}
    vv = vix.get("value"); v3 = vix3m.get("value")
    if vv and v3 and v3 > 0:
        ratio = vv / v3
        gates["vix_contango"] = {"pass": ratio < 1.0, "state": "PASS" if ratio < 1.0 else "FAIL",
            "ratio": round(ratio,3),
            "detail": f"VIX/VIX3M={ratio:.3f}{' ok' if ratio<1.0 else ' backwardation!'}"}
    else:
        gates["vix_contango"] = {"pass": False, "state": "NO_DATA",
            "detail": "VIX3M data missing (^VIX3M ticker)"}
    abstain = casc_state.get("abstain", False)
    gates["casc"] = {"pass": not abstain, "state": "FAIL" if abstain else "PASS",
        "abstain": abstain,
        "detail": "CASC ABSTAIN -> lock CANDIDATE" if abstain else "CASC ok"}
    hyg = hyg_lqd_z.get("HYG",{}); lqd = hyg_lqd_z.get("LQD",{})
    if hyg and lqd and "error" not in hyg and "error" not in lqd:
        hz = hyg.get("z_252",0); lz = lqd.get("z_252",0)
        ht = hyg.get("trend_20d_pct",0)
        ok = hz > -1 and lz > -1 and ht > -0.01
        gates["credit"] = {"pass": ok, "state": "PASS" if ok else "FAIL",
            "hyg_z": round(hz,2), "lqd_z": round(lz,2),
            "detail": f"HYG z={hz:+.2f}(20d:{'up' if ht>0 else 'down'}) LQD z={lz:+.2f}: {'ok' if ok else 'deteriorate'}"}
    else:
        gates["credit"] = {"pass": False, "state": "NO_DATA",
            "detail": "HYG/LQD z missing"}
    gates["all_pass"] = all(g.get("pass",False) for g in gates.values() if isinstance(g,dict))
    return gates

def load_casc_state():
    if not ABCD_CASC_PATH.exists(): return {}
    try:
        with open(ABCD_CASC_PATH,"r",encoding="utf-8") as f: return json.load(f)
    except: return {}

def load_abcd_md():
    # Primary path: D:\liquidity-dashboard\v3.5\report\daily_*.md
    abcd_search = [
        BASE_DIR.parent / "v3.5" / "report",
    ]
    for d in abcd_search:
        if not d.exists(): continue
        try:
            cand = sorted(
                [p for p in d.glob("daily_*.md")],
                key=lambda p: p.stat().st_mtime, reverse=True
            )
            if cand: return cand[0].read_text(encoding="utf-8")
        except: pass
    return ""

# === 5. 状态机 ===
# 连续性定义: 交易日计数 (Trading Days)
#   周五 CANDIDATE day 1 → 周一数据续好 → 升级为 day 2 → 三门全绿触发 ACTIONABLE
#   跨周末不中断。节假日无数据时自然不更新,Day 保持不变。
#   降级: immediate (当日生效)。升级: 需要连续 2 个交易日的确认。

def load_state():
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH,"r",encoding="utf-8") as f: return json.load(f)
        except: pass
    return {"state":"OBSERVE","state_days":0,"last_update":"","history":[]}

def save_state(st):
    st["last_update"] = TS_CN
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH,"w",encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)

# === 5a. 持仓生命周期 (独立于信号状态机, 每日率先运行) ===

def load_position():
    """加载活跃持仓, 若不存在或已平仓则返回 None"""
    if not POSITION_PATH.exists():
        return None
    try:
        with open(POSITION_PATH, "r", encoding="utf-8") as f:
            pos = json.load(f)
        if not pos.get("active", False):
            return None
        return pos
    except Exception:
        return None

def save_position(pos):
    """写入持仓文件"""
    POSITION_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(POSITION_PATH, "w", encoding="utf-8") as f:
        json.dump(pos, f, ensure_ascii=False, indent=2)

def archive_position(pos, exit_triggers, exit_date):
    """平仓后归档 position.json 到 archive/"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_name = f"{exit_date}_position_closed.json"
    pos["active"] = False
    pos["exit_date"] = exit_date
    pos["exit_triggers"] = exit_triggers
    archive_path = ARCHIVE_DIR / archive_name
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(pos, f, ensure_ascii=False, indent=2)
    return archive_path

def check_position_exit(position, yields, vix, vix3m, attribution, hyg_lqd_z):
    """对照 entry_snapshot 锚定值逐条检查失效条件。
    返回: (triggers, checks_detail, gaps) 
      triggers 非空 → 需清仓
      gaps 非空 → 数据缺失导致部分条件今日无法检查, 必须 fail-loud
    """
    snap = position.get("entry_snapshot", {})
    triggers = []
    checks = []
    gaps = []  # 数据缺失导致未检查的条件

    y10 = yields.get("10Y", {}).get("value")
    anchor_10y = snap.get("10Y")
    if y10 is not None and anchor_10y is not None:
        hit = y10 > anchor_10y
        checks.append({
            "condition": "10Y > entry anchor",
            "anchor": f"{anchor_10y:.2f}%",
            "current": f"{y10:.2f}%",
            "triggered": hit
        })
        if hit:
            triggers.append(f"10Y {y10:.2f}% > entry anchor {anchor_10y:.2f}%")
    else:
        checks.append({
            "condition": "10Y > entry anchor",
            "anchor": f"{anchor_10y}%" if anchor_10y is not None else "N/A",
            "current": f"{y10}%" if y10 is not None else "N/A",
            "triggered": False
        })
        if y10 is None:
            gaps.append("10Y data missing — '10Y > entry anchor' unchecked today")

    vv = vix.get("value")
    if vv is not None and vv > 0:
        hit_vix = vv > 25
        checks.append({
            "condition": "VIX > 25",
            "anchor": "25",
            "current": str(vv),
            "triggered": hit_vix
        })
        if hit_vix:
            triggers.append(f"VIX {vv} > 25")
    else:
        checks.append({
            "condition": "VIX > 25",
            "anchor": "25",
            "current": str(vv) if vv is not None else "N/A",
            "triggered": False
        })
        gaps.append("VIX data missing — 'VIX > 25' unchecked today")

    v3 = vix3m.get("value")
    if vv is not None and vv > 0 and v3 is not None and v3 > 0:
        ratio = vv / v3
        hit_ratio = ratio > 1.05
        checks.append({
            "condition": "VIX/VIX3M > 1.05",
            "anchor": "1.05",
            "current": f"{ratio:.3f}",
            "triggered": hit_ratio
        })
        if hit_ratio:
            triggers.append(f"VIX/VIX3M {ratio:.3f} > 1.05")
    else:
        checks.append({
            "condition": "VIX/VIX3M > 1.05",
            "anchor": "1.05",
            "current": "N/A",
            "triggered": False
        })
        if v3 is None or v3 <= 0:
            gaps.append("VIX3M data missing — 'VIX/VIX3M > 1.05' unchecked today")
        elif vv is None or vv <= 0:
            gaps.append("VIX data missing — 'VIX/VIX3M > 1.05' unchecked today")

    # Attribution flip: 任意归因 veto → 触发
    attr_flip = attribution.get("veto", False)
    checks.append({
        "condition": "Attribution flip to risk-off",
        "anchor": snap.get("attribution", "N/A"),
        "current": attribution.get("type", "N/A"),
        "triggered": attr_flip
    })
    if attr_flip:
        triggers.append(f"Attribution flipped to risk-off: {attribution['label']}")

    # Credit stress: HYG z < -1
    hyg = hyg_lqd_z.get("HYG", {})
    if hyg and "error" not in hyg:
        hz = hyg.get("z_252", 0)
        hit_credit = hz < -1
        checks.append({
            "condition": "HYG z-score < -1",
            "anchor": "-1",
            "current": f"{hz:.2f}",
            "triggered": hit_credit
        })
        if hit_credit:
            triggers.append(f"HYG z-score {hz:.2f} < -1 (credit stress)")
    else:
        checks.append({
            "condition": "HYG z-score < -1",
            "anchor": "-1",
            "current": "N/A",
            "triggered": False
        })
        gaps.append("HYG z-score data missing — 'HYG z < -1' unchecked today")

    # Time stop: 10 个交易日
    deadline = position.get("time_stop_date")
    if deadline:
        try:
            dl = datetime.strptime(deadline, "%Y-%m-%d")
            hit_time = NOW >= dl
            checks.append({
                "condition": f"Time stop ({deadline})",
                "anchor": deadline,
                "current": NOW.strftime("%Y-%m-%d"),
                "triggered": hit_time
            })
            if hit_time:
                triggers.append(f"Time stop: {deadline} reached (10 trading days)")
        except Exception:
            pass

    return triggers, checks, gaps

def generate_exit_md(position, triggers, checks, exit_date_str):
    """生成持仓退出块 MD"""
    entry_date = position.get("entry_date", "?")
    L = []
    L.append("## ⛔ POSITION EXIT TRIGGERED\n")
    L.append(f"> **Action: Close all tactical positions today.**")
    L.append(f"> {len(triggers)} of {len(checks)} exit conditions triggered.\n")
    L.append(f"**Exit Date**: {exit_date_str} | **Entry Date**: {entry_date}\n")
    L.append("| Condition | Anchor | Current | Status |")
    L.append("|-----------|--------|---------|--------|")
    for c in checks:
        icon = "❌ TRIGGERED" if c["triggered"] else "✅ ok"
        L.append(f"| {c['condition']} | {c['anchor']} | {c['current']} | {icon} |")
    L.append("")
    for t in triggers:
        L.append(f"> ⛔ {t}")
    L.append("")
    # 归档信息
    L.append(f"> Position archived to `data/archive/{exit_date_str}_position_closed.json`\n")
    return "\n".join(L)

def generate_position_gaps_md(gaps, position):
    """持仓监控数据缺失 → fail-loud 醒目警告块 (与 EXIT 同级)"""
    entry_date = position.get("entry_date", "?")
    L = []
    L.append("## 🔔 POSITION MONITORING INCOMPLETE\n")
    L.append(f"> **{len(gaps)} exit condition(s) could NOT be verified today due to missing data.**")
    L.append(f"> Entry Date: {entry_date} | Today: {NOW.strftime('%Y-%m-%d')}\n")
    L.append(f"> ⚠️ 持仓风控存在盲区 — 以下失效条件今日未被检查, 默认视为未触发但实际状态未知:\n")
    for i, g in enumerate(gaps, 1):
        L.append(f"> {i}. {g}")
    L.append(f"\n> Action: 手动确认缺失数据, 或根据已知条件自行判断是否继续持有。\n")
    return "\n".join(L)

def generate_transition_note(note_type, position, **kwargs):
    """生成持仓相关过渡提示块 (re-ACTIONABLE / tranche triggered / tranche expired)"""
    entry_date = position.get("entry_date", "?")
    L = []
    if note_type == "re_actionable":
        L.append("## ⚠️ ACTIONABLE Re-Signaled (Position Active)\n")
        L.append("> **信号再次触发, 但 position.json 已存在 — 不重复入场。**")
        L.append(f"> Entry Date: {entry_date} | New Signal: {NOW.strftime('%Y-%m-%d')}")
        L.append(f"> 现有锚定值不变: 10Y anchor 保持入场时快照, 失效条件不重置。")
        y10_cur = kwargs.get("y10_current")
        if y10_cur:
            L.append(f"> 当前 10Y: {y10_cur:.2f}% | 锚定 10Y: {position.get('entry_snapshot',{}).get('10Y','?'):.2f}%")
        L.append("")
    elif note_type == "tranche_triggered":
        ratio_now = kwargs.get("ratio_now", "?")
        ratio_entry = position.get("entry_snapshot", {}).get("vix_vix3m_ratio", "?")
        L.append("## 🟢 Second Tranche Triggered\n")
        L.append(f"> **分批第二腿条件满足 — 执行剩余 50% 入场。**")
        L.append(f"> VIX/VIX3M 当前 {ratio_now} vs 入场 {ratio_entry} (条件: 持续下降)")
        L.append(f"> Entry Date: {entry_date} | Tranche 2 Date: {NOW.strftime('%Y-%m-%d')}\n")
    elif note_type == "tranche_expired":
        L.append("## ⏸ Second Tranche Expired\n")
        L.append(f"> 分批第二腿条件在 {kwargs.get('expire_date','?')} 前未满足, 已过期清除。")
        L.append(f"> 剩余 50% 不再执行, 现有仓位继续受原始失效条件监控。\n")
    return "\n".join(L)

def compute_state(scores, attribution, gates, prev_state, data_date=""):
    """状态机幂等: 同一 data_date 重复运行不递增天数, 仅新交易日才累计确认窗口"""
    B = scores.get("Dovish",0); C = scores.get("Liquidity",0); E = scores.get("Growth",0)
    vix_val = scores.get("_vix_value", 99)
    curr = prev_state.get("state","OBSERVE")
    last_dd = prev_state.get("last_data_date","")
    is_new_day = data_date and data_date > last_dd  # 只有新交易日才能递增天数

    days = prev_state.get("state_days",0)
    veto_days = prev_state.get("veto_days",0)
    # 递增修饰: 同 state + 新日 → +1, 同 state + 同日 → +0 (幂等)
    inc = 1 if is_new_day else 0

    # Veto: attribution = long-end risk-off -> ABSTAIN
    # veto_days 与升级确认计数独立 — ABSTAIN 天数不计入 CANDIDATE 确认窗口
    if attribution.get("veto"):
        vd = (veto_days + inc) if curr == "ABSTAIN" else 1
        return {"state":"ABSTAIN","state_days":vd,"veto_days":vd,
            "last_data_date": data_date,
            "reason": f"Attribution veto: {attribution['label']}",
            "upgrade_needs":["Wait for 13W to follow 10Y move"],"prev_state":curr}

    # Downgrade: immediate. 若已处于 OBSERVE 则增量, 否则重置
    if C >= 1 or vix_val > VIX_HIGH:
        reason = f"C={C}/3" if C >= 1 else f"VIX={vix_val}>{VIX_HIGH}"
        ddays = (days + inc) if curr == "OBSERVE" else 1
        return {"state":"OBSERVE","state_days":ddays,
            "last_data_date": data_date,
            "reason":f"{reason} -> downgrade effective today","upgrade_needs":["Wait for risk to fade"],"prev_state":curr}

    # Attempt upgrade: OBSERVE/ABSTAIN -> CANDIDATE
    if curr in ("OBSERVE","ABSTAIN"):
        if B >= 3 and C == 0 and E <= 1:
            return {"state":"CANDIDATE","state_days":1,
                "last_data_date": data_date,
                "reason":f"B={B}/4 C={C}/3 E={E}/3 -> candidate triggered",
                "upgrade_needs":["Need 2nd day confirmation + all gates pass"],"prev_state":curr}
        needs = []
        if B < 3: needs.append(f"B>={3} (now {B}/4)")
        if C > 0: needs.append(f"C==0 (now {C}/3)")
        if E > 1: needs.append(f"E<={1} (now {E}/3)")
        ndays = (days + inc) if curr == "OBSERVE" else 1
        return {"state":"OBSERVE","state_days":ndays,
            "last_data_date": data_date,
            "reason":f"No trigger B={B} C={C} E={E}","upgrade_needs":needs,"prev_state":curr}

    # CANDIDATE -> ACTIONABLE or hold
    if curr == "CANDIDATE":
        still = B >= 3 and C == 0 and E <= 1
        if not still:
            return {"state":"OBSERVE","state_days":1,
                "last_data_date": data_date,
                "reason":"Candidate conditions lost -> back to OBSERVE","upgrade_needs":[],"prev_state":curr}
        next_days = days + inc  # 幂等: 同日 → days 不变, 新日 → days+1
        if next_days >= 2 and gates.get("all_pass",False):
            return {"state":"ACTIONABLE","state_days":next_days,
                "last_data_date": data_date,
                "reason":f"CANDIDATE day {next_days} confirmed + all gates pass -> UPGRADE",
                "upgrade_needs":[],"prev_state":curr}
        missing = []
        if next_days < 2: missing.append(f"need {2-next_days} more day(s)")
        if not gates.get("all_pass"):
            failed = [k for k,v in gates.items() if isinstance(v,dict) and not v.get("pass")]
            missing.append(f"gates closed: {','.join(failed)}")
        return {"state":"CANDIDATE","state_days":next_days,
            "last_data_date": data_date,
            "reason":f"Hold CANDIDATE day {next_days}","upgrade_needs":missing,"prev_state":curr}

    return {"state":curr,"state_days":(days + inc),"last_data_date": data_date,
            "reason":"hold","upgrade_needs":[],"prev_state":curr}

# === 6. 行动块 (仅 ACTIONABLE) ===

def generate_action_block(state_info, scores, yields, vix, vix3m, gates, snap):
    if state_info["state"] != "ACTIONABLE": return ""
    d = state_info.get("state_days", 0)
    vv = vix.get("value", 0); v3 = vix3m.get("value", 0)
    y10 = yields.get("10Y",{}).get("value", 0)
    vix_ratio = round(vv/v3, 3) if v3 and v3 > 0 else None
    B = scores.get("Dovish",0); B_strong = scores.get("_b_strong", 0)
    cash_pct = min(30, 10 + B * 5 + B_strong * 5)

    L = []
    L.append("## 9. Action Block (ACTIONABLE)\n")
    L.append(f"- **[State]** ACTIONABLE (CANDIDATE confirmed day {d})")
    L.append("- **[Direction]** Tactical add")
    L.append("- **[Target Pool]** Bucket A high-beta / QQQ")
    L.append(f"- **[Size]** {cash_pct}% of cash, 2 tranches (50% today, 50% tomorrow if VIX/VIX3M keeps dropping)")
    L.append("- **[Invalidation]** Any of below triggers -> close tactical position same day:")
    L.append(f"  - 10Y > {y10:.2f}% (entry anchor)")
    L.append(f"  - VIX > 25 or VIX/VIX3M > 1.05 (current {vix_ratio})")
    L.append("  - HYG/LQD z-score breaks below -1 intraday")
    L.append("  - 13W/10Y ratio flips to risk-off attribution")
    L.append("- **[Time Stop]** +3% not reached within 10 trading days -> unconditional exit")
    L.append(f"\n### Entry Snapshot (for backtest)\n")
    L.append(f"| Item | Value |")
    L.append(f"|------|-------|")
    L.append(f"| Entry Date | {NOW.strftime('%Y-%m-%d')} |")
    L.append(f"| B Score | {B}/4 (strong signals: {B_strong}) |")
    L.append(f"| VIX | {vv} |")
    L.append(f"| VIX/VIX3M | {vix_ratio} |")
    L.append(f"| 10Y | {y10}% |")
    L.append(f"| Attribution | {scores.get('_attr_label','?')} |")
    L.append(f"| Anchor: 10Y | {y10:.2f}% |")
    L.append(f"| Anchor: VIX/VXV | 1.05 |")
    L.append("")
    return "\n".join(L)

# === 7. 曲线信号 ===

def curve_signals(yields, snap):
    y10 = yields.get("10Y",{}).get("value")
    sig = []
    shy_up = (snap.get("SHY",{}).get("chg_d") or 0) > 0.05
    shy_dn = (snap.get("SHY",{}).get("chg_d") or 0) < -0.05
    ief_up = (snap.get("IEF",{}).get("chg_d") or 0) > 0.05
    ief_dn = (snap.get("IEF",{}).get("chg_d") or 0) < -0.05
    spx_up = (snap.get("SPY",{}).get("chg_d") or 0) > 0.3
    if shy_up and ief_dn: sig.append(("WARN","Cut expectation + long-end term premium"))
    if shy_up and ief_up and spx_up: sig.append(("GOOD","Dovish healthy"))
    if shy_up and ief_up and not spx_up: sig.append(("WARN","Growth-scare type cut"))
    if shy_dn and ief_dn: sig.append(("BAD","Inflation/hawkish pressure"))
    if y10:
        if y10 >= Y10_DANGER: sig.append(("BAD",f"10Y={y10}% valuation killzone"))
        elif y10 >= Y10_WARN: sig.append(("WARN",f"10Y={y10}% valuation pressure"))
    return sig

# === 8. ABCD交叉验证 (从文件自动读) ===

def build_cross_validation(scores, attribution, abcd_md, state_info, attr_conflicts=None, curve=None):
    L = []
    L.append("## 8. ABCD Cross-Validation\n")
    L.append("| This Tool | ABCD Reading | Match? |")
    L.append("|-----------|-------------|--------|")

    # Parse ABCD 四端框架格式 (daily_*.md from liquidity-dashboard/report/)
    # Mapping:
    #   Fed A Hawkish  ← ABCD C 长端利率定价 (real rates pressure)
    #   Fed C Liquidity ← ABCD B 信用融资条件 (HY OAS / HYG)
    #   Fed E Growth    ← v3.5 信号检查 (VIX, meltdown signals)
    #   Fed D Inflation  ← ABCD D 外汇风险扩散 (FX)
    #   CASC Gate       ← CASC 确认 count
    abcd_signals = {}
    casc_line = ""
    v35_signals = {}
    regime = ""
    judgement = ""
    if abcd_md:
        # Phase: 0=preface, 1=diagnosis, 2=v3.5, 3=casc
        phase = 0
        for line in abcd_md.splitlines():
            ls = line.strip()
            # Detect sections
            if "ABCD 四端框架" in ls and "固定前言" in ls:
                phase = 0; continue
            if "核心诊断" in ls and ls.startswith("##"):
                phase = 1; continue
            if "v3.5 信号检查" in ls and ls.startswith("##"):
                phase = 2; continue
            if "CASC" in ls and ("跨资产" in ls or ls.startswith("##")):
                phase = 3; continue
            if ls.startswith("## ") and phase <= 3:
                phase = -1; continue
            # Phase 0: ABCD preface table
            if phase == 0 and ls.startswith("|"):
                parts = [p.strip() for p in ls.split("|") if p.strip()]
                if len(parts) >= 2:
                    if parts[0].startswith("A "):
                        abcd_signals["A"] = parts[1][:30]
                        if len(parts) >= 3: abcd_signals["A_desc"] = parts[2][:60]
                    elif parts[0].startswith("B "):
                        abcd_signals["B"] = parts[1][:30]
                        if len(parts) >= 3: abcd_signals["B_desc"] = parts[2][:60]
                    elif parts[0].startswith("C "):
                        abcd_signals["C"] = parts[1][:30]
                        if len(parts) >= 3: abcd_signals["C_desc"] = parts[2][:60]
                    elif parts[0].startswith("D "):
                        abcd_signals["D"] = parts[1][:30]
                        if len(parts) >= 3: abcd_signals["D_desc"] = parts[2][:60]
            # Phase 0: 综合判定
            if phase == 0 and ls.startswith("> **综合判定**"):
                judgement = ls.replace("> **综合判定**：", "").replace("> **综合判定**:", "").strip()[:80]
            # Phase 1: Regime
            if phase == 1 and "Regime" in ls:
                regime = ls.replace("- **Regime**：", "").replace("**Regime**:", "").strip()[:30]
            # Phase 2: v3.5 signals
            if phase == 2 and ls.startswith("|") and not ls.startswith("| 信号"):
                parts = [p.strip() for p in ls.split("|") if p.strip()]
                if len(parts) >= 2:
                    v35_signals[parts[0]] = parts[1:3]
            # Phase 3: CASC
            if phase == 3 and "CASC 确认" in ls:
                casc_line = ls[:80]

    def _safe(val):
        val = val.replace("|", "·") if val else "N/A"
        return val[:55]

    h = scores.get("Hawkish",0); l_sc = scores.get("Liquidity",0)
    g = scores.get("Growth",0); d_sc = scores.get("Inflation",0)

    # A Hawkish → ABCD C 长端利率定价
    c_light = abcd_signals.get("C", "?")
    c_desc = abcd_signals.get("C_desc", "")
    rate_line = f"{c_light} {c_desc}" if c_desc else c_light
    rate_match = "⚠️ conflict" if h>=2 else ("—" if rate_line=="N/A" else "✅")
    L.append(f"| A Hawkish {h}/4 | {_safe(rate_line)} | {rate_match} |")

    # C Liquidity → ABCD B 信用融资条件
    b_light = abcd_signals.get("B", "?")
    b_desc = abcd_signals.get("B_desc", "")
    # Also try to get HY OAS from v3.5
    hy_oas = ""
    for sig_key, sig_vals in v35_signals.items():
        if "Drawdown" in sig_key and len(sig_vals) >= 2:
            hy_oas = f" {_safe(sig_vals[1])}"[:35]
            break
    credit_line = f"{b_light} {b_desc}{hy_oas}"
    credit_match = "⚠️ conflict" if l_sc>=2 else ("—" if credit_line=="N/A" else "✅")
    L.append(f"| C Liquidity {l_sc}/3 | {_safe(credit_line)} | {credit_match} |")

    # E Growth Scare → v3.5 signals
    meltdown = v35_signals.get("Extreme Meltdown", ["N/A"])
    meltdown_status = meltdown[0] if meltdown else "N/A"
    risk_line = f"VIX sig: {meltdown_status}"
    L.append(f"| E Growth Scare {g}/3 | {_safe(risk_line)} | {'⚠️' if g>=2 else '✅'} |")

    # D Inflation → ABCD D 外汇风险扩散
    d_light = abcd_signals.get("D", "?")
    d_desc = abcd_signals.get("D_desc", "")
    infl_line = f"{d_light} {d_desc}" if d_desc else d_light
    infl_match = "⚠️" if d_sc>=2 else ("—" if infl_line=="N/A" else "✅")
    L.append(f"| D Inflation {d_sc}/4 | {_safe(infl_line)} | {infl_match} |")

    # CASC Gate
    if casc_line:
        casc_text = casc_line.lstrip("> ")[:55]
    else:
        casc_text = "OK"
    L.append(f"| CASC Gate | {casc_text} | ✅ |")

    # Auto conflict detection -> state penalty
    conflicts = []

    # External: ABCD vs this tool
    if state_info.get("state") in ("CANDIDATE","ACTIONABLE") and casc_status == "ABSTAIN":
        conflicts.append("CASC ABSTAIN -> max CANDIDATE (cannot upgrade)")
    if state_info.get("state") == "ACTIONABLE" and h >= 2:
        conflicts.append("ABCD shows rate pressure while this tool says actionable -> review")

    # Internal: attribution vs B/C modules (v3)
    B = scores.get("Dovish",0); C = scores.get("Liquidity",0)
    if attribution.get("veto") and B >= 3 and C == 0:
        conflicts.append(f"⚠️ Internal: attribution veto ({attribution['label']}) but B={B}/4 C=0/3 — risk-off label contradicted by strong dovish score")
    if attribution.get("veto") and curve:
        curve_types = [t for t, m in curve]
        if "GOOD" in curve_types:
            conflicts.append(f"⚠️ Internal: attribution says risk-off but curves says GOOD ({[m for t,m in curve if t=='GOOD'][0]}) — module inconsistency")

    # Attr validation conflicts from validate_attribution
    if attr_conflicts:
        conflicts.extend(attr_conflicts)

    if conflicts:
        L.append("")
        for c in conflicts: L.append(f"> ⚠️ **Cross-check conflict**: {c}")
    else:
        L.append("")
        L.append("> Both systems converge: no structural conflict detected.")
    L.append("")
    return "\n".join(L)

# === 8b. Narrative Engine (v0.1.2) ===
from pathlib import Path as _Path
import importlib.util as _importlib_util
_NARRATIVE_PATH = _Path(__file__).resolve().parent / "Fed 反应函数雷达" / "fed-reaction-dashboard" / "scripts" / "narrative_engine.py"
_spec = _importlib_util.spec_from_file_location("narrative_engine", _NARRATIVE_PATH)
_narrative_mod = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_narrative_mod)
build_narrative = _narrative_mod.build_narrative
build_asset_chain_struct = _narrative_mod.build_asset_chain_struct

# === 8c. Pipeline Result JSON (机器可读) ===

def _build_pipeline_result(ts_cn, ts_utc, state_info, attr, gates, scores, curve,
                           action_block, cross_sec, yields, snap, klines, vix, vix3m,
                           hyg_lqd_z, now, abcd_md, narrative=None, position=None,
                           exit_block="", exit_triggers=None, position_note=""):
    """生成 pipeline_result.json — build_site.py 的唯一逻辑源"""
    v3_val = vix3m.get("value")
    vv = vix.get("value", 0)

    # Treasury table data
    treasury_rows = []
    for k in ["13W", "5Y", "10Y", "30Y"]:
        y = yields.get(k, {})
        if "error" not in y:
            treasury_rows.append({
                "tenor": k,
                "latest": f"{y['value']:.3f}%",
                "daily_bp": f"{y.get('chg_d',0):+.1f}",
                "5d_bp": f"{y.get('chg_5d',0):+.1f}",
                "vol_20d_bp": str(y.get("vol_20d_bp", "?"))
            })

    # ETF snapshot table data
    etf_rows = []
    info_map = {
        "SHY": "2Y proxy", "IEF": "10Y proxy", "TLT": "long-end",
        "UUP": "USD", "GLD": "Gold", "QQQ": "Nasdaq",
        "SPY": "S&P500", "IWM": "Russell", "HYG": "HY credit",
        "LQD": "IG credit", "VXX": "Volatility", "CL": "WTI"
    }
    for lb, meaning in info_map.items():
        d = snap.get(lb, {})
        c5 = klines.get(lb, {}).get("chg_5d")
        etf_rows.append({
            "ticker": lb,
            "price": f"${d['price']}" if "price" in d else "--",
            "daily_pct": f"{d['chg_d']:+.2f}%" if "chg_d" in d else "--",
            "5d_pct": f"{c5:+.2f}%" if c5 is not None else "?",
            "signal": meaning
        })

    # Scores structured
    score_mods = {
        "hawkish":  {"label": "A. Hawkish",   "max": 4, "score_key": "Hawkish"},
        "dovish":   {"label": "B. Dovish",     "max": 4, "score_key": "Dovish"},
        "liquidity": {"label": "C. Liquidity",  "max": 3, "score_key": "Liquidity"},
        "inflation": {"label": "D. Inflation",  "max": 4, "score_key": "Inflation"},
        "growth":   {"label": "E. Growth Scare","max": 3, "score_key": "Growth"}
    }
    detail_keys = {
        "hawkish": "_a_details", "dovish": "_b_details",
        "liquidity": "_c_details", "inflation": "_d_details",
        "growth": "_e_details"
    }
    strong_keys = {
        "hawkish": "_a_strong", "dovish": "_b_strong",
        "liquidity": "_c_strong", "inflation": "_d_strong",
        "growth": "_e_strong"
    }
    scores_structured = {}
    for k, v in score_mods.items():
        sc = scores.get(v["score_key"], 0)
        details = scores.get(detail_keys[k], [])
        strong = scores.get(strong_keys[k], 0)
        scores_structured[k] = {
            "label": v["label"],
            "score": sc,
            "max": v["max"],
            "strong": strong,
            "details": "; ".join(details) if details else "-"
        }

    # Curves
    curve_list = [{"type": t, "message": m} for t, m in curve]

    # Cross validation: pre-parse from MD section (skip header row)
    cross_rows = []
    cross_notes = []
    cross_conflicts = []
    saw_header = False  # track first | line = header
    if cross_sec:
        for line in cross_sec.splitlines():
            ls = line.strip()
            if ls.startswith("|") and not ls.startswith("|---"):
                cells = [c.strip() for c in ls.split("|") if c.strip()]
                if not saw_header:
                    saw_header = True  # skip header row: | This Tool | ABCD Reading | Match? |
                    continue
                if len(cells) >= 3:
                    cross_rows.append({
                        "tool": cells[0],
                        "abcd": cells[1],
                        "match": cells[2]
                    })
            elif ls.startswith("> ⚠️"):
                cross_conflicts.append(ls.lstrip("> "))
            elif ls.startswith("> "):
                cross_notes.append(ls.lstrip("> "))

    # Action block: null or structured
    ab_structured = None
    if action_block:
        ab_structured = _parse_action_block(action_block, now)

    # VIX ratio
    vix_ratio = round(vv / v3_val, 3) if vv and v3_val else None

    # data_date: US market close date (skip weekends)
    data_dt = _last_trading_day(now)
    data_date_str = data_dt.strftime("%Y-%m-%d")

    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "ts_cn": ts_cn,
            "ts_utc": ts_utc,
            "version": "3.0.0",
            "data_date": data_date_str,
            "attribution_method": "sigma_normalized",
            "attribution_thresholds": {
                "long_lead_13w_sigma_max": 0.5,
                "long_lead_long_sigma_min": 1.0,
                "front_lead_13w_sigma_min": 1.0,
                "front_lead_long_sigma_min": 0.8,
                "legacy_front_lead_ratio": FRONT_LEAD_RATIO
            }
        },
        "state": {
            "state": state_info["state"],
            "state_days": state_info["state_days"],
            "veto_days": state_info.get("veto_days"),
            "prev_state": state_info.get("prev_state", ""),
            "reason": state_info["reason"],
            "upgrade_needs": state_info.get("upgrade_needs", [])
        },
        "attribution": {
            "type": attr["type"],
            "label": attr["label"],
            "detail": attr["detail"],
            "veto": attr["veto"]
        },
        "gates": {
            **{k: v for k, v in gates.items() if isinstance(v, dict)},
            "all_pass": gates.get("all_pass", False)
        },
        "scores": scores_structured,
        "curves": curve_list,
        "cross_validation": {
            "rows": cross_rows,
            "notes": cross_notes,
            "conflicts": cross_conflicts
        },
        "action_block": ab_structured,
        "treasury": treasury_rows,
        "etf_snapshot": etf_rows,
        "vix": {"value": vv, "chg": vix.get("chg")} if vv is not None else None,
        "vix3m": v3_val,
        "vix_vix3m_ratio": vix_ratio,
        "ten_year": yields.get("10Y", {}).get("value"),
        "position": {
            "active": (position is not None and position.get("active", False)) if position else False,
            "has_exit": bool(exit_block),
            "exit_triggers": exit_triggers if exit_triggers else [],
            "has_monitor_gaps": "MONITORING INCOMPLETE" in position_note if position_note else False,
            "has_re_actionable": "Re-Signaled" in position_note if position_note else False,
            "has_tranche": "Tranche" in position_note if position_note else False,
            "entry_date": position.get("entry_date") if position and position.get("active") else None,
            "time_stop_date": position.get("time_stop_date") if position and position.get("active") else None,
            "pending_tranche_active": position.get("pending_tranche", {}).get("active", False) if position and position.get("active") else False,
            "position_note_md": position_note
        },
        "narrative": narrative if narrative else {}
    }


def _parse_action_block(md_text, now):
    """解析 MD action block 为结构化字段"""
    lines = md_text.splitlines()
    result = {
        "state": "",
        "direction": "",
        "target_pool": "",
        "size": "",
        "invalidations": [],
        "time_stop": "",
        "entry_snapshot": {}
    }
    for l in lines:
        ls = l.strip()
        if ls.startswith("- **[State]**"):
            result["state"] = ls.replace("- **[State]**", "").strip()
        elif "- **[Direction]**" in ls:
            result["direction"] = ls.split("**")[-1].strip()
        elif "- **[Target Pool]**" in ls:
            result["target_pool"] = ls.split("**")[-1].strip()
        elif "- **[Size]**" in ls:
            result["size"] = ls.split("**")[-1].strip()
        elif ls.startswith("- **[Invalidation]**"):
            continue  # header line
        elif "- **[Time Stop]**" in ls:
            result["time_stop"] = ls.split("**")[-1].strip()
        elif ls.startswith("- ") and not ls.startswith("- **[") and not ls.startswith("- **"):
            # sub-bullet: invalidation conditions
            result["invalidations"].append(ls[2:])  # strip "- "
        elif ls.startswith("| Entry Date"):
            continue
        elif ls.startswith("|---"):
            continue
        elif ls.startswith("|"):
            cells = [c.strip() for c in ls.split("|") if c.strip()]
            if len(cells) >= 2:
                result["entry_snapshot"][cells[0]] = cells[1]
    return result


# === 9. Markdown/Console 输出 ===

EMOJI = {"GOOD":"✅","WARN":"⚠️","BAD":"🔴","RED":"🔴","GREEN":"🟢","YELLOW":"🟡","ORANGE":"🟠"}

def generate_md(yields, snap, k5, vix, scores, curve, attribution, gates,
                state_info, action_block, cross_sec, filepath, exit_block="", position_note=""):
    now = NOW.strftime("%Y-%m-%d %H:%M:%S")
    L = []
    L.append(f"# Fed Reaction Dashboard v2\n\n**{TS_CN}** | **{TS_UTC}** | Futu + yfinance\n")

    # EXIT block — 持仓退出触发 (最高优先级)
    if exit_block:
        L.append(exit_block)

    # Position notes — 持仓监控警告 / re-ACTIONABLE / tranche (第二优先级)
    if position_note:
        L.append(position_note)

    # State header
    st = state_info["state"]
    days = state_info["state_days"]
    emoji_st = {"OBSERVE":"⏸","CANDIDATE":"🟡","ACTIONABLE":"🟢","ABSTAIN":"🔴"}.get(st,"❓")
    L.append(f"## 0. State Machine: **{emoji_st} {st}** (Day {days})\n")
    L.append(f"> {state_info['reason']}\n")
    needs = state_info.get("upgrade_needs", [])
    if needs:
        L.append(f"> **To upgrade**: " + " | ".join(needs))
    L.append("")

    # Driver attribution
    L.append(f"## Driver Attribution\n\n**{attribution['label']}**\n")
    L.append(f"> {attribution['detail']}\n")

    # Gate status
    L.append("## Gates\n\n| Gate | Status | Detail |\n|------|--------|--------|")
    for k, v in gates.items():
        if isinstance(v, dict) and "detail" in v:
            icon = "✅" if v.get("pass",False) else "❌"
            L.append(f"| {k} | {icon} | {v['detail']} |")
    L.append("")

    # Treasury yields
    L.append("## 1. UST Yields\n\n| Tenor | Latest | Daily (bp) | 5D (bp) | 20D vol (bp) |\n|-------|--------|-----------|---------|-------------|")
    for k in ["13W","5Y","10Y","30Y"]:
        y = yields.get(k,{})
        if "error" not in y:
            vol = y.get("vol_20d_bp","?")
            L.append(f"| {k} | {y['value']:.3f}% | {y['chg_d']:+.1f} | {y['chg_5d']:+.1f} | {vol} |")
        else: L.append(f"| {k} | - | - | - | - |")
    # 2Y proxy from SHY ETF (CBOE ^TWO delisted, FRED DGS2 not via yfinance)
    shy_d = snap.get("SHY",{})
    shy_val = shy_d.get("price")
    shy_chg = shy_d.get("chg_d")
    shy_chg5 = k5.get("SHY",{}).get("chg_5d")
    if shy_val is not None:
        L.append(f"| **2Y (SHY proxy)** | ${shy_val:.2f} | {shy_chg:+.2f}% | {f'{shy_chg5:+.2f}%' if shy_chg5 is not None else '?'} | ETF price |")
    L.append(f"> 2Y yield proxy: SHY (1-3Y Treasury ETF). CBOE ^TWO delisted, FRED DGS2 unavailable.\n")

    # ETF snapshot
    L.append("## 2. ETF Snapshot (Futu)\n\n| Ticker | Price | Daily | 5D | Signal |\n|--------|-------|-------|-----|--------|")
    info = {"SHY":"2Y proxy","IEF":"10Y proxy","TLT":"long-end","UUP":"USD","GLD":"Gold",
            "QQQ":"Nasdaq","SPY":"S&P500","IWM":"Russell","HYG":"HY credit","LQD":"IG credit",
            "VXX":"Volatility","CL":"WTI"}
    for lb, meaning in info.items():
        d = snap.get(lb,{})
        if "error" in d: L.append(f"| {lb} | - | - | - | {meaning} |")
        else:
            c5 = k5.get(lb,{}).get("chg_5d")
            L.append(f"| {lb} | ${d['price']} | {d['chg_d']:+.2f}% | {f'{c5:+.2f}%' if c5 else '?'} | {meaning} |")
    L.append("")

    # VIX
    if "error" not in vix: L.append(f"## 3. VIX\n\n**{vix['value']}** (daily {vix['chg']:+.2f})\n")

    # Scores
    L.append("## 4. Score Modules\n\n| Module | Score | Max | Strength | Details |\n|--------|-------|-----|----------|--------|")
    mods = [("A. Hawkish","Hawkish",4,"_a_details","_a_strong"),
            ("B. Dovish","Dovish",4,"_b_details","_b_strong"),
            ("C. Liquidity","Liquidity",3,"_c_details","_c_strong"),
            ("D. Inflation","Inflation",4,"_d_details","_d_strong"),
            ("E. Growth","Growth",3,"_e_details","_e_strong")]
    for label, key, mx, det_key, strong_key in mods:
        sc = scores.get(key,0); st = scores.get(strong_key,0)
        details = scores.get(det_key,[])
        detail_str = "; ".join(details) if details else "-"
        strong_tag = f"{st}strong" if st > 0 else ""
        L.append(f"| {label} | {sc} | {mx} | {strong_tag} | {detail_str} |")
    L.append("")

    # Curve
    L.append("## 6. Curve Signals\n")
    if curve:
        for t, m in curve: L.append(f"- **{t}**: {m}")
    else: L.append("- No structural curve signal")
    L.append("")

    # 2Y/10Y interpretation
    L.append("## 7. 2Y/10Y Interpretation\n")
    shy_c = snap.get("SHY",{}).get("chg_d"); ief_c = snap.get("IEF",{}).get("chg_d")
    tlt_c = snap.get("TLT",{}).get("chg_d"); y10_v = yields.get("10Y",{}).get("value")
    L.append("> Note: 2Y proxy=SHY, 10Y proxy=IEF; ETF up = yield down")
    if shy_c is not None:
        dl = "yield_down(dovish)" if shy_c>0.05 else ("yield_up(hawkish)" if shy_c<-0.05 else "flat")
        L.append(f"- **2Y proxy(SHY)**: {dl} ({shy_c:+.2f}%)")
    if ief_c is not None:
        dl = "yield_down" if ief_c>0.05 else ("yield_up" if ief_c<-0.05 else "flat")
        L.append(f"- **10Y proxy(IEF)**: {dl} ({ief_c:+.2f}%)")
    if tlt_c is not None:
        dl = "yield_down" if tlt_c>0.05 else ("yield_up" if tlt_c<-0.05 else "flat")
        L.append(f"- **30Y(TLT)**: {dl} ({tlt_c:+.2f}%)")
    if y10_v is not None:
        if y10_v >= Y10_DANGER: L.append(f"- **10Y={y10_v}% > {Y10_DANGER}%**: valuation killzone")
        elif y10_v >= Y10_WARN: L.append(f"- **10Y={y10_v}% > {Y10_WARN}%**: valuation pressure")
        else: L.append(f"- **10Y={y10_v}% < {Y10_WARN}%**: manageable")
    L.append("")

    # Cross validation
    L.append(cross_sec)

    # Action block (only when ACTIONABLE)
    if action_block:
        L.append(action_block)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"\n[MD] Saved: {filepath}")

# === 10. Main ===

def main():
    import sys
    if sys.platform == "win32": sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("="*60)
    print(f"  Fed Reaction Dashboard v2 - {TS_CN}")
    print("="*60)

    print("[1/5] Load yfinance cache...")
    yields, vix, vix3m, hyg_lqd_z, hyg_lqd_spread_z = load_cache()

    print("[2/5] Fetch Futu ETF snapshots + 25d kline...")
    snap, klines = fetch_futu_history()

    # Driver attribution — 先算, 因为持仓风控需要归因判断
    attr = driver_attribution(yields)
    print(f"  Attribution: {attr['label']}")

    # === 持仓风控检查 (在信号状态机之前, 优先级最高) ===
    position = load_position()
    exit_block = ""
    exit_triggers = []
    position_note = ""  # 非退出类持仓提示 (gaps / re-ACTIONABLE / tranche)
    if position:
        exit_triggers, exit_checks, exit_gaps = check_position_exit(
            position, yields, vix, vix3m, attr, hyg_lqd_z)
        
        # 数据缺失 fail-loud (在退出检查之外独立报警)
        if exit_gaps:
            print(f"\n  🔔 POSITION MONITORING INCOMPLETE: {len(exit_gaps)} conditions unchecked!")
            for g in exit_gaps:
                print(f"    -> {g}")
            position_note = generate_position_gaps_md(exit_gaps, position)

        # Pending tranche: 分批入场第二腿检查
        pt = position.get("pending_tranche")
        if pt and pt.get("active"):
            pt_expire = pt.get("expire_date", "")
            try:
                pt_dl = datetime.strptime(pt_expire, "%Y-%m-%d")
                pt_expired = NOW >= pt_dl
            except Exception:
                pt_expired = True
            
            if pt_expired:
                # 第二腿已过期 → 清除 pending_tranche
                position["pending_tranche"]["active"] = False
                save_position(position)
                tn = generate_transition_note("tranche_expired", position, expire_date=pt_expire)
                position_note = position_note + "\n" + tn if position_note else tn
                print(f"  [TRANCHE] Second tranche expired ({pt_expire}), cleared")
            else:
                # 检查条件: VIX/VIX3M < entry ratio
                vv_cur = vix.get("value", 0)
                v3_cur = vix3m.get("value", 0)
                if vv_cur and v3_cur and v3_cur > 0:
                    ratio_now = vv_cur / v3_cur
                    ratio_entry = pt.get("entry_vix_ratio", 99)
                    if ratio_now < ratio_entry:
                        # 触发第二腿
                        position["pending_tranche"]["active"] = False
                        position["pending_tranche"]["triggered_date"] = NOW.strftime("%Y-%m-%d")
                        save_position(position)
                        tn = generate_transition_note("tranche_triggered", position, ratio_now=round(ratio_now, 3))
                        position_note = position_note + "\n" + tn if position_note else tn
                        print(f"  [TRANCHE] Second tranche triggered! VIX/VIX3M {ratio_now:.3f} < entry {ratio_entry:.3f}")
                # else: data missing → 静默跳过, 第二腿条件日后再判

        if exit_triggers:
            exit_date_str = NOW.strftime("%Y-%m-%d")
            print(f"\n  ⛔ POSITION EXIT: {len(exit_triggers)} conditions triggered!")
            for t in exit_triggers:
                print(f"    -> {t}")
            exit_block = generate_exit_md(position, exit_triggers, exit_checks, exit_date_str)
            # 归档已平仓 position
            archive_path = archive_position(position, exit_triggers, exit_date_str)
            print(f"    Archived: {archive_path}")
            # 删除活跃 position.json (下一日不再检查)
            try:
                POSITION_PATH.unlink()
                print(f"    Removed active position.json")
            except Exception:
                pass
            # 退出触发时, position_note 不再有意义 (exit_block 已置顶)
            position_note = ""
        else:
            print(f"  Position active (entry {position.get('entry_date','?')}): no exit triggers")

    print("[3/5] Compute scores & state machine...")
    # 5-module scoring
    a_s, a_d, a_st = score_a(snap, klines)
    b_s, b_d, b_st = score_b(snap, klines)
    c_s, c_d, c_st = score_c(snap, vix, klines, hyg_lqd_z)
    d_s, d_d, d_st = score_d(snap, yields, klines)
    e_s, e_d, e_st = score_e(snap, klines)

    # Gates
    casc = load_casc_state()
    gates = check_gates(vix, vix3m, casc, hyg_lqd_z)
    print(f"  Gates: {'ALL PASS' if gates['all_pass'] else 'SOME CLOSED'}")

    # Scores dict
    scores = {
        "Hawkish": a_s, "Dovish": b_s, "Liquidity": c_s,
        "Inflation": d_s, "Growth": e_s,
        "_a_details": a_d, "_b_details": b_d, "_c_details": c_d,
        "_d_details": d_d, "_e_details": e_d,
        "_a_strong": a_st, "_b_strong": b_st, "_c_strong": c_st,
        "_d_strong": d_st, "_e_strong": e_st,
        "_vix_value": vix.get("value"), "_attr_label": attr["label"],
    }

    # Cross-asset consistency: 若 attribution 判 risk-off 但权益/VIX/信用指向 risk-on, 降级 veto
    attr, attr_conflicts = validate_attribution(attr, scores, vix, hyg_lqd_z, snap)
    if attr_conflicts:
        for ac in attr_conflicts:
            print(f"  {ac}")

    # State machine (幂等: data_date 从 US 收盘日派生, 跳周末, 同日重跑不递增天数)
    data_dt = _last_trading_day()
    data_date_str = data_dt.strftime("%Y-%m-%d")
    prev = load_state()
    # 旧日守卫: data_date < last_data_date → 回放/回测, 只输出判定不落盘
    stale = bool(data_date_str and prev.get('last_data_date','') and data_date_str < prev['last_data_date'])
    if stale:
        print(f"  ⚠ STALE REPLAY: data_date {data_date_str} < last_data_date {prev['last_data_date']} — state NOT persisted")
    state_info = compute_state(scores, attr, gates, prev, data_date=data_date_str)
    print(f"  State: {prev['state']} -> {state_info['state']} (day {state_info['state_days']})"
          f"  | data_date: {data_date_str}{' NEW' if data_date_str > prev.get('last_data_date','') else ' (same)'}")
    print(f"  Reason: {state_info['reason']}")

    # Save state (stale 回放不落盘; 同日同状态不重复写历史)
    if not stale:
        history = prev.get("history", [])
        today_str = NOW.strftime("%Y-%m-%d")
        should_append = True
        if history:
            last = history[-1]
            # 同日且同方向转换 → 跳过 (幂等去重)
            if last.get("date") == today_str and last.get("from") == prev["state"] and last.get("to") == state_info["state"]:
                should_append = False
        if should_append:
            history = history + [{
                "date": today_str, "from": prev["state"],
                "to": state_info["state"], "reason": state_info["reason"],
                "B": b_s, "C": c_s, "E": e_s, "attr": attr["type"]
            }]
        new_state = {**state_info, "history": history}
        save_state(new_state)
    else:
        print(f"  Stale replay: state.json preserved at {prev['state']} day {prev['state_days']}")

    # === 持仓生命周期: 入场写入 ===
    # position 变量在持仓风控段已加载, 此处复用 (注意: 若 exit 触发则 POSITION_PATH 已删除)
    position = load_position()
    if state_info["state"] == "ACTIONABLE":
        if position:
            # 已有活跃持仓 — 跳过入场, 仅报告标注
            y10_cur = yields.get("10Y", {}).get("value", 0)
            re_note = generate_transition_note("re_actionable", position, y10_current=y10_cur)
            position_note = position_note + "\n" + re_note if position_note else re_note
            print("  [POSITION] ACTIONABLE re-signaled but position active — skipping entry, anchors unchanged")
        else:
            # 无活跃持仓 — 正式入场, 写入 position.json
            vv = vix.get("value", 0)
            v3 = vix3m.get("value")
            vix_ratio_entry = round(vv / v3, 3) if vv and v3 else None
            y10 = yields.get("10Y", {}).get("value", 0)
            time_stop_date = (NOW + timedelta(days=14)).strftime("%Y-%m-%d")  # 10 trading days ≈ 14 calendar
            tranche_expire = (NOW + timedelta(days=3)).strftime("%Y-%m-%d")  # 2 trading days ≈ 3 calendar
            entry_pos = {
                "active": True,
                "entry_date": NOW.strftime("%Y-%m-%d"),
                "entry_snapshot": {
                    "10Y": y10,
                    "vix": vv,
                    "vix_vix3m_ratio": vix_ratio_entry,
                    "b_score": b_s,
                    "b_strong": b_st,
                    "attribution": attr["type"]
                },
                "time_stop_date": time_stop_date,
                "entry_reason": state_info["reason"],
                "pending_tranche": {
                    "active": True,
                    "condition": "VIX/VIX3M keeps dropping from entry",
                    "entry_vix_ratio": vix_ratio_entry,
                    "expire_date": tranche_expire
                }
            }
            save_position(entry_pos)
            print(f"  [POSITION] Entry written: position.json (time stop: {time_stop_date}, tranche expire: {tranche_expire})")

    # Curve (needed before cross-validation for internal consistency check)
    curve = curve_signals(yields, snap)

    # ABCD cross-validation
    abcd_md = load_abcd_md()
    cross_sec = build_cross_validation(scores, attr, abcd_md, state_info, 
                                       attr_conflicts=attr_conflicts, curve=curve)

    # Action block
    action_block = generate_action_block(state_info, scores, yields, vix, vix3m, gates, snap)

    print("[4/5] Generate outputs...")

    # MD output
    out_md = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fed_reaction_dashboard.md")
    generate_md(yields, snap, klines, vix, scores, curve, attr, gates,
                state_info, action_block, cross_sec, out_md, exit_block=exit_block, position_note=position_note)

    # latest.json
    hyg_p = snap.get("HYG",{}).get("price")
    lqd_p = snap.get("LQD",{}).get("price")
    v3_val = vix3m.get("value")
    vix_ratio = round(vix.get("value",0)/v3_val,3) if vix.get("value") and v3_val else None
    latest = {
        "updated_at": TS_CN,
        "updated_at_utc": TS_UTC,
        "state": state_info["state"],
        "state_days": state_info["state_days"],
        "headline": state_info["reason"],
        "recommendation": state_info["state"],
        "attribution": attr["label"],
        "vix": vix.get("value"),
        "vix3m": v3_val,
        "vix_vix3m_ratio": vix_ratio,
        "hyg_lqd_z": {
            "hyg_z": hyg_lqd_z.get("HYG",{}).get("z_252"),
            "lqd_z": hyg_lqd_z.get("LQD",{}).get("z_252"),
            "spread_z": hyg_lqd_spread_z,
        },
        "hyg_lqd": round(hyg_p/lqd_p,4) if hyg_p and lqd_p else None,
        "ten_year": yields.get("10Y",{}).get("value"),
        "scores": {
            "hawkish": a_s, "dovish": b_s, "liquidity": c_s,
            "inflation": d_s, "growth": e_s,
        },
        "gates": {k: v.get("pass",False) for k,v in gates.items() if isinstance(v,dict)},
        "upgrade_needs": state_info.get("upgrade_needs", []),
    }
    json_path = OUT_DIR / "latest.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(json_path,"w",encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    print(f"[JSON] Saved: {json_path}")

    # Narrative engine
    narrative = build_narrative(state_info, attr, scores, yields, snap, vix, vix3m, hyg_lqd_z)

    # pipeline_result.json — 机器可读全量产物 (供 build_site.py 直接取逻辑)
    pipeline = _build_pipeline_result(
        TS_CN, TS_UTC, state_info, attr, gates, scores, curve,
        action_block, cross_sec, yields, snap, klines, vix, vix3m, hyg_lqd_z,
        NOW, abcd_md, narrative=narrative, position=position, exit_block=exit_block,
        exit_triggers=exit_triggers, position_note=position_note
    )
    ppath = OUT_DIR / "pipeline_result.json"
    with open(ppath, "w", encoding="utf-8") as f:
        json.dump(pipeline, f, ensure_ascii=False, indent=2)
    print(f"[JSON] Pipeline result saved: {ppath}")

    # === Archive daily copies (信号质量样本库原料) ===
    date_tag = NOW.strftime("%Y-%m-%d")
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_pp = ARCHIVE_DIR / f"{date_tag}_pipeline_result.json"
    archive_lj = ARCHIVE_DIR / f"{date_tag}_latest.json"
    with open(archive_pp, "w", encoding="utf-8") as f:
        json.dump(pipeline, f, ensure_ascii=False, indent=2)
    with open(archive_lj, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    print(f"[ARCHIVE] Saved: {archive_pp}")
    print(f"[ARCHIVE] Saved: {archive_lj}")

    # Console summary
    print("\n" + "="*60)
    print(f"  Decision Pipeline Output")
    print("="*60)
    emoji = {"OBSERVE":"⏸","CANDIDATE":"🟡","ACTIONABLE":"🟢","ABSTAIN":"🔴"}
    print(f"  State: {emoji.get(state_info['state'],'?')} {state_info['state']} (Day {state_info['state_days']})")
    print(f"  Attribution: {attr['label']}")
    print(f"  Scores: A={a_s}/4 B={b_s}/4 C={c_s}/3 D={d_s}/4 E={e_s}/3")
    print(f"  Gates: {'ALL PASS' if gates['all_pass'] else 'SOME CLOSED'}")
    print(f"  VIX: {vix.get('value')}  VIX3M: {v3_val}  Ratio: {vix_ratio}")
    print(f"  Upgrade needs: {state_info.get('upgrade_needs',[])}")
    if action_block:
        print("\n  >>> ACTION BLOCK GENERATED <<<")
    if exit_triggers:
        print(f"\n  ⛔ EXIT TRIGGERED: {len(exit_triggers)} conditions")
    print("="*60)

if __name__ == "__main__":
    main()

