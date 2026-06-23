"""
云端构建脚本 — 在 GitHub Actions 上运行。

读取：
  - fed_reaction_dashboard.md  （Markdown 报告，本地生成）
  - data/latest.json           （关键指标摘要）

生成：
  - docs/index.html            （精美交易仪表盘）
"""

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / "fed_reaction_dashboard.md"
JSON_PATH = ROOT / "data" / "latest.json"
DOCS_DIR = ROOT / "docs"
INDEX_PATH = DOCS_DIR / "index.html"

# ──────────────────────────────────────────────
# 内嵌 HTML 模板（不读 docs/index.html，避免被上次输出覆盖）
# ──────────────────────────────────────────────
HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Fed Reaction Dashboard</title>
  <link rel="stylesheet" href="assets/style.css" />
</head>
<body>
  <div class="app-shell">
    <header class="hero">
      <div class="hero-glow"></div>
      <nav class="topbar">
        <div class="brand">
          <span class="brand-dot"></span>
          <span>Fed Reaction Dashboard</span>
        </div>
        <div class="timestamp">__TIMESTAMP__</div>
      </nav>

      <section class="hero-grid">
        <div class="hero-main card glass">
          <div class="eyebrow">Market Regime</div>
          <div class="status-row">
            <span class="status-pill __STATUS_CLASS__">__STATUS__</span>
            <h1>__HEADLINE__</h1>
          </div>
          <p class="hero-copy">__HERO_COPY__</p>
          <div class="hero-tags">
            __HERO_TAGS__
          </div>
        </div>

        <aside class="decision card">
          <div class="card-title">今日操作框架</div>
          __DECISION_LINES__
          <p class="small-note">__WAIT_NOTE__</p>
        </aside>
      </section>
    </header>

    <main>
      <section class="score-section">
        <div class="section-head">
          <div>
            <div class="eyebrow">Signal Modules</div>
            <h2>模块评分</h2>
          </div>
          <p>红色代表压力源，绿色代表尚未确认系统性风险。</p>
        </div>

        <div class="score-grid">
          __SCORE_CARDS__
        </div>
      </section>

      <section class="chain-section card">
        <div class="section-head compact">
          <div>
            <div class="eyebrow">Asset Reaction Chain</div>
            <h2>资产反应链条</h2>
          </div>
        </div>
        <div class="chain-scroll">
          <div class="asset-chain">
          __CHAIN_NODES__
          </div>
        </div>
      </section>

      <section class="data-grid">
        __TREASURY_TABLE__
        __ETF_TABLE__
      </section>

      <section class="explain-grid">
        <article class="card explain-card">
          <div class="card-title">自动解读</div>
          __EXPLANATION__
        </article>

        <article class="card watch-card">
          <div class="card-title">重新考虑抄底的条件</div>
          <ul class="watch-list">
            __WATCH_LIST__
          </ul>
        </article>
      </section>

      <section class="cross card">
        <div class="card-title">ABCD 交叉验证</div>
        <div class="cross-grid">
          __CROSS_VALIDATION__
        </div>
      </section>
    </main>
  </div>

  <script src="assets/app.js"></script>
</body>
</html>"""


# ──────────────────────────────────────────────
# 1.  Markdown 解析器
# ──────────────────────────────────────────────

def parse_md_sections(md: str) -> dict:
    """将 Markdown 按 ## 标题拆分为多个段落。"""
    sections = {}
    current_key = None
    current_lines = []

    for line in md.splitlines():
        m = re.match(r'^##\s+(.+)', line)
        if m:
            if current_key is not None:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = m.group(1).strip()
            current_lines = []
        else:
            if current_key is not None:
                current_lines.append(line)
            # 跳过 ## 之前的头部

    if current_key is not None:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


def parse_table(text: str) -> list[dict]:
    """解析 Markdown 表格，返回 [{col: val, ...}, ...]。"""
    rows = []
    lines = [l.strip() for l in text.splitlines() if l.strip().startswith("|")]
    if not lines:
        return rows

    # 跳过表头分隔线（|---|---|）
    header_line = lines[0] if lines else ""
    headers = [h.strip() for h in header_line.split("|") if h.strip()]

    for line in lines[1:]:
        # 跳过分隔行
        if re.match(r'^[\|\s\-:]+$', line):
            continue
        cells = [c.strip() for c in line.split("|")]
        # 去掉首尾空（markdown 表格首尾是 |）
        cells = [c for c in cells if c]
        if len(cells) >= len(headers):
            row = {}
            for i, h in enumerate(headers):
                row[h] = cells[i] if i < len(cells) else ""
            rows.append(row)
        elif len(cells) >= 1 and headers:
            # 可能 header 数量少于数据列（表格第一列是空）
            adjusted_headers = headers[:]
            if len(headers) < len(cells):
                adjusted_headers = [""] + headers
            row = {}
            for i, h in enumerate(adjusted_headers):
                row[h] = cells[i] if i < len(cells) else ""
            rows.append(row)

    return rows


def extract_md_meta(md: str) -> dict:
    """提取 Markdown 顶部的元数据行（时间、数据源）。"""
    meta = {}
    first_lines = md.strip().split("\n")[:3]
    for line in first_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'\*\*(.+?)\*\*\s*\|\s*(.+)', line)
        if m:
            meta[m.group(1).strip()] = m.group(2).strip()
        # 纯时间行
        elif re.match(r'\d{4}-\d{2}-\d{2}', line):
            meta["timestamp"] = line.replace("**", "").strip()
    return meta


# ──────────────────────────────────────────────
# 2.  模板填充函数（每个区块独立）
# ──────────────────────────────────────────────

def build_status_pill(status: str) -> str:
    """根据 status 返回 CSS 类名和显示文本。"""
    s = (status or "").upper().strip()
    cls_map = {
        "RED": "red",
        "GREEN": "green",
        "YELLOW": "yellow",
        "DEEP RED": "deepred",
        "DEEP_RED": "deepred",
    }
    return cls_map.get(s, "yellow"), s


def build_hero_tags(vix, hyg_lqd, ten_year, scores: dict) -> str:
    tags = []
    # 主导模块
    if scores:
        top = max(scores.items(), key=lambda kv: int(kv[1].get("score", 0)), default=None)
        if top:
            mod_names = {"A": "Fed 鹰派", "B": "Fed 鸽派", "C": "流动性压力",
                         "D": "通胀/期限溢价", "E": "增长恐慌", "F": "实际利率/估值"}
            name = mod_names.get(top[0], top[0])
            tags.append(f"<span>{name} {top[1].get('score', '?')}/{top[1].get('max', '?')}</span>")
    if vix is not None:
        tags.append(f"<span>VIX {vix}</span>")
    if hyg_lqd is not None:
        tags.append(f"<span>HYG/LQD {hyg_lqd}</span>")
    if ten_year is not None:
        tags.append(f"<span>10Y {ten_year}%</span>")
    return "\n            ".join(tags)


def build_decision_lines(scores, vix) -> str:
    lines = []

    # 主导矛盾
    hawk = scores.get("A", {}).get("score", 0)
    growth = scores.get("E", {}).get("score", 0)
    liquid = scores.get("C", {}).get("score", 0)
    infl = scores.get("D", {}).get("score", 0)
    real = scores.get("F", {}).get("score", 0)

    dominant = "鹰派重定价" if int(hawk or 0) >= 2 else \
               "增长恐慌" if int(growth or 0) >= 2 else \
               "通胀/期限溢价" if int(infl or 0) >= 2 else \
               "实际利率挤压" if int(real or 0) >= 2 else \
               "震荡观察"

    lines.append(f"""          <div class="decision-line danger">
            <span>主导矛盾</span>
            <strong>{dominant}</strong>
          </div>""")

    # 系统性压力
    sys_verdict = "已触发 ⚠️" if int(liquid or 0) >= 2 else "暂未触发"
    sys_cls = "danger" if int(liquid or 0) >= 2 else "ok"
    lines.append(f"""          <div class="decision-line {sys_cls}">
            <span>系统性压力</span>
            <strong>{sys_verdict}</strong>
          </div>""")

    # VIX 状态
    vix_val = vix or 0
    vix_verdict = "高温 >25" if float(vix_val) > 25 else "可控" if float(vix_val) > 20 else "平静"
    vix_cls = "danger" if float(vix_val) > 25 else "warn" if float(vix_val) > 20 else "ok"
    lines.append(f"""          <div class="decision-line {vix_cls}">
            <span>VIX</span>
            <strong>{vix_verdict} ({vix_val})</strong>
          </div>""")

    return "\n".join(lines)


def build_score_cards(scores: dict) -> str:
    """根据 scores 生成六张模块卡片。"""
    mods = [
        ("A", "A. Fed鹰派", "hot"),
        ("B", "B. Fed鸽派", "mute"),
        ("C", "C. 流动性压力", "ok"),
        ("D", "D. 通胀/期限溢价", "warn"),
        ("E", "E. 增长恐慌", "ok"),
        ("F", "F. 实际利率/估值挤压", "calm"),
    ]

    cards = []
    for key, label, default_cls in mods:
        s = scores.get(key, {})
        score = int(s.get("score", 0))
        max_s = int(s.get("max", 4))
        desc = s.get("description", s.get("signal", ""))
        pct = min(100, int(score / max_s * 100)) if max_s > 0 else 0

        # 动态 class
        cls = default_cls
        if score >= 3:
            cls = "hot"
        elif score >= 2:
            cls = "warn"
        elif score >= 1:
            cls = "calm"

        cards.append(f"""          <article class="score-card {cls}">
            <div class="score-top"><span>{label}</span><b>{score}/{max_s}</b></div>
            <div class="meter"><span style="width:{pct}%"></span></div>
            <p>{desc}</p>
          </article>""")

    return "\n".join(cards)


def build_chain_nodes(treasury_rows, etf_rows, scores) -> str:
    """构建资产反应链条 — 使用 chain-card + chain-scroll 横向滚动布局。"""
    # 旧 cls → 新 cls 映射
    cls_map = {"red": "danger", "amber": "warning", "green": "neutral"}

    nodes = []

    # Fed 预期
    hawk_score = int(scores.get("A", {}).get("score", 0))
    fed_v = "偏鹰" if hawk_score >= 2 else "偏鸽" if int(scores.get("B", {}).get("score", 0)) >= 2 else "中性"
    fed_cls = "danger" if hawk_score >= 2 else "neutral" if int(scores.get("B", {}).get("score", 0)) >= 2 else "warning"
    nodes.append((fed_cls, "Fed预期", fed_v, "短中端重定价" if hawk_score >= 2 else "等待信号"))

    # 名义利率 — 看 10Y
    row_10y = _find_treasury_row(treasury_rows, "10Y") or {}
    d1_10y = _row_get(row_10y, "日变(bp)", "Daily (bp)", "Daily")
    d1_10y_val = _parse_bp(d1_10y)
    nom_v = "上行" if d1_10y_val > 0 else "下行" if d1_10y_val < 0 else "持平"
    nom_cls = "danger" if d1_10y_val > 0 else "neutral" if d1_10y_val < 0 else "warning"
    nodes.append((nom_cls, "名义利率", nom_v, f"10Y {_row_get(row_10y, '最新', 'Latest', 'latest')}"))

    # 实际利率
    real_score = int(scores.get("F", {}).get("score", 0))
    real_v = "上行" if real_score >= 2 else "次要项"
    real_cls = "danger" if real_score >= 2 else "warning" if real_score >= 1 else "neutral"
    nodes.append((real_cls, "实际利率", real_v, "DFII10 驱动" if real_score >= 2 else "非主驱动"))

    # 黄金/成长
    gld_row = _find_etf_row(etf_rows, "GLD") or {}
    qqq_row = _find_etf_row(etf_rows, "QQQ") or {}
    gld_chg = _row_get(gld_row, "日涨跌", "Daily")
    qqq_chg = _row_get(qqq_row, "日涨跌", "Daily")
    gld_down = _pct_negative(gld_chg)
    qqq_down = _pct_negative(qqq_chg)
    gold_v = "承压" if (gld_down or qqq_down) else "偏强"
    gold_cls = "danger" if gld_down else "warning" if qqq_down else "neutral"
    nodes.append((gold_cls, "黄金 / 成长", gold_v, f"GLD {gld_chg} · QQQ {qqq_chg}"))

    # 信用
    hyg_row = _find_etf_row(etf_rows, "HYG") or {}
    lqd_row = _find_etf_row(etf_rows, "LQD") or {}
    hyg_chg = _row_get(hyg_row, "日涨跌", "Daily")
    hyg_down = _pct_negative(hyg_chg)
    credit_v = "轻微恶化" if hyg_down else "稳定"
    credit_cls = "danger" if hyg_down else "neutral"
    nodes.append((credit_cls, "信用", credit_v, f"HYG {hyg_chg}"))

    # 波动率
    vix_val = _find_vix_from_md()
    vix_v = "高温" if vix_val > 25 else "升温" if vix_val > 20 else "平静"
    vix_cls = "danger" if vix_val > 25 else "warning" if vix_val > 20 else "neutral"
    nodes.append((vix_cls, "波动率", vix_v, f"VIX {vix_val}"))

    # 组装新结构
    html_parts = []
    for i, (cls, label, val, detail) in enumerate(nodes):
        html_parts.append(f"""            <div class="chain-card {cls}">
              <div class="chain-label">{label}</div>
              <div class="chain-title">{val}</div>
              <div class="chain-value">{detail}</div>
            </div>""")
        if i < len(nodes) - 1:
            html_parts.append(f"""            <div class="chain-arrow">\u2192</div>""")

    return "\n".join(html_parts)


def _find_vix_from_md():
    """从 latest.json 读取 VIX。"""
    if JSON_PATH.exists():
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            j = json.load(f)
            return float(j.get("vix", 0))
    return 0


def build_treasury_table(rows: list[dict], md_text: str = "") -> str:
    """生成美债收益率表格 + 曲线 tilt 解读（兼容 v1 中文 & v2 英文表头）。"""
    table_rows = ""
    for r in rows:
        term = _row_get(r, "期限", "Tenor", "tenor")
        latest = _row_get(r, "最新", "Latest", "latest")
        d1 = _row_get(r, "日变(bp)", "Daily (bp)", "Daily")
        d5 = _row_get(r, "5日变(bp)", "5D (bp)", "5D")

        d1_cls = _bp_class(d1)
        d5_cls = _bp_class(d5, strong_threshold=5)

        table_rows += f"""              <tr><td>{term}</td><td>{latest}</td><td class="{d1_cls}">{d1}bp</td><td class="{d5_cls}">{d5}bp</td></tr>
"""

    # 提取曲线 tilt 说明
    tilt_text = ""
    for line in md_text.splitlines():
        if "曲线tilt" in line or "Bear Flattening" in line or "Bear Steepening" in line:
            tilt_text = line.replace("**", "").strip()
            break

    tilt_html = ""
    if tilt_text:
        tilt_html = f"""          <div class="insight danger-soft">曲线 tilt：<strong>{tilt_text}</strong></div>"""

    return f"""        <article class="card table-card">
          <div class="card-title">美债收益率</div>
          <table>
            <thead><tr><th>期限</th><th>最新</th><th>日变</th><th>5日变</th></tr></thead>
            <tbody>
{table_rows}            </tbody>
          </table>
{tilt_html}        </article>"""


def build_etf_table(rows: list[dict]) -> str:
    """生成 ETF 快照表（兼容 v1 中文 & v2 英文表头）。"""
    table_rows = ""
    for r in rows:
        name = _row_get(r, "标的", "Ticker", "ticker")
        price = _row_get(r, "价格", "Price", "price")
        chg = _row_get(r, "日涨跌", "Daily")
        signal = _row_get(r, "信号", "Signal", "signal")

        chg_cls = _pct_class(chg)
        strong = " strong" if abs(_parse_pct(chg)) > 1.5 else ""

        table_rows += f"""              <tr><td>{name}</td><td>{price}</td><td class="{chg_cls}{strong}">{chg}</td><td>{signal}</td></tr>
"""

    return f"""        <article class="card table-card">
          <div class="card-title">ETF 快照</div>
          <table>
            <thead><tr><th>标的</th><th>价格</th><th>日涨跌</th><th>信号</th></tr></thead>
            <tbody>
{table_rows}            </tbody>
          </table>
        </article>"""


def build_explanation(md_text: str) -> str:
    """从 Markdown 中提取关键解读段落。"""
    lines_out = []

    # 提取曲线解读
    for line in md_text.splitlines():
        stripped = line.strip()
        # 跳过表格、标题
        if stripped.startswith("#") or stripped.startswith("|") or stripped.startswith(">"):
            continue
        if "曲线tilt" in stripped or "Bear" in stripped:
            lines_out.append(f"<p>{stripped.replace('**', '<strong>').replace('**', '</strong>')}</p>")
        if "注:" in stripped or "代理" in stripped:
            lines_out.append(f"<p>{stripped.replace('**', '<strong>').replace('**', '</strong>')}</p>")

    # 核心三段
    core = [
        "<p><strong>1. 为什么是鹰派重定价？</strong> 从模块评分和曲线结构判断，利率上行集中在短中端，属于 Fed 路径重定价而非期限溢价拉升。</p>",
        "<p><strong>2. 为什么不是系统性危机？</strong> 流动性压力评分低的背景下，信用 ETF 未现明显恶化，VIX 虽上行但仍处于可控区间，系统性风险链未闭合。</p>",
        "<p><strong>3. 黄金为什么跌这么多？</strong> 当真实利率变化幅度不足以解释黄金跌幅时，缺口更可能来自仓位踩踏/动量去杠杆，而非实际利率主导。</p>",
    ]
    return "\n".join(core)


def build_watch_list(md_text: str) -> str:
    """生成抄底等待条件列表。"""
    items = [
        "10Y 收益率停止上行，最好重新压回 4.50% 附近或下方。",
        "VIX 不再扩张，且信用 HYG/LQD 稳住。",
        "QQQ 跌幅收敛，不再被实际利率/贴现率压制。",
        "GLD 跌幅开始与实际利率变化重新匹配，而不是继续仓位踩踏。",
    ]
    li = "\n".join(
        f"            <li><span></span>{item}</li>" for item in items
    )
    return li


def build_cross_validation(md_text: str) -> str:
    """从 Markdown 第 8 节提取 ABCD 交叉验证表格。"""
    # 匹配 ## 8 节中的表格
    sections = parse_md_sections(md_text)
    sec8 = ""
    for k, v in sections.items():
        if "ABCD" in k or "交叉验证" in k:
            sec8 = v
            break

    if not sec8:
        return ""

    rows = parse_table(sec8)
    if not rows:
        return ""

    divs = []
    for r in rows:
        tool = r.get("本工具", "")
        abcd = r.get("ABCD 对应读数", r.get("ABCD 对应", ""))
        match = r.get("一致?", r.get("是否一致", ""))
        is_wide = len(tool) > 30 or len(abcd) > 40

        cls = ' class="wide"' if is_wide else ""
        icon = {"✅": "✅", "⚠️": "⚠️", "❌": "❌"}.get(match.strip(), match.strip())
        abcd_text = abcd if abcd else match

        divs.append(f"""          <div{cls}><b>{tool}</b><span>{abcd_text} {icon}</span></div>""")

    # 提取下方注释
    notes = []
    for line in sec8.splitlines():
        if line.startswith(">"):
            notes.append(line.lstrip("> ").strip())

    if notes:
        note_html = "<br>".join(notes)
        divs.append(f"""          <div class="wide"><b>综合结论</b><span>{note_html}</span></div>""")

    return "\n".join(divs)


# ──────────────────────────────────────────────
# 3.  主流程
# ──────────────────────────────────────────────

def _row_get(row, *keys):
    """Try multiple column names, return first non-empty value. Supports v1(CN) & v2(EN) tables."""
    for k in keys:
        v = row.get(k, "")
        if v:
            return v
    return ""


def _find_treasury_row(rows, term):
    for r in rows:
        if term in str(_row_get(r, "期限", "Tenor", "tenor")):
            return r
    return None


def _find_etf_row(rows, ticker):
    for r in rows:
        if ticker.upper() in str(_row_get(r, "标的", "Ticker", "ticker")).upper():
            return r
    return None


def _parse_bp(val: str) -> float:
    try:
        return float(str(val).replace("+", "").replace("bp", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _bp_class(val: str, strong_threshold: float = 3.0) -> str:
    v = _parse_bp(val)
    if v > strong_threshold:
        return "up strong"
    elif v > 0:
        return "up"
    elif v < -strong_threshold:
        return "down strong"
    elif v < 0:
        return "down"
    return "flat"


def _parse_pct(val: str) -> float:
    try:
        return float(str(val).replace("+", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _pct_class(val: str) -> str:
    v = _parse_pct(val)
    if v > 0:
        return "up"
    elif v < 0:
        return "down"
    return "flat"


def _pct_negative(val: str) -> bool:
    return _parse_pct(val) < -0.01


def _clean_md_html(text: str) -> str:
    """将 Markdown 内联格式转为 HTML。"""
    # 粗体
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # 行内代码
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    return text


def main():
    print("[build_site] 开始构建 Fed Reaction Dashboard ...")

    # 1. 读取源文件
    if not MD_PATH.exists():
        print(f"[build_site] [ERROR] Cannot find {MD_PATH}")
        return 1

    md_text = MD_PATH.read_text(encoding="utf-8")
    print(f"[build_site] 读取 Markdown: {len(md_text)} 字符")

    latest = {}
    if JSON_PATH.exists():
        latest = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        print(f"[build_site] 读取 latest.json")

    # 2. 解析 Markdown
    sections = parse_md_sections(md_text)

    # 找各节
    treasury_sec = etf_sec = vix_sec = scores_sec = recommend_sec = ""
    curve_sec = y2y10_sec = cross_sec = ""

    for k, v in sections.items():
        kl = k.lower()
        if any(x in kl for x in ("美债", "treasury", "ust", "yield", "收益率")):
            treasury_sec = v
        elif "etf" in kl and ("快照" in kl or "snapshot" in kl):
            etf_sec = v
        elif "vix" in kl:
            vix_sec = v
        elif any(x in kl for x in ("评分", "模块", "score module", "signal")):
            scores_sec = v
        elif "抄底" in kl or "建议" in kl:
            recommend_sec = v
        elif "曲线" in kl:
            curve_sec = v
        elif "信号" in kl or "2y" in kl.lower() or "解读" in kl:
            y2y10_sec = v
        elif "abcd" in kl.lower() or "交叉" in kl:
            cross_sec = v

    # 解析各表
    treasury_rows = parse_table(treasury_sec) if treasury_sec else []
    etf_rows = parse_table(etf_sec) if etf_sec else []
    score_rows = parse_table(scores_sec) if scores_sec else []

    # 3. 构建 scores 字典（兼容 v1 中文 & v2 英文表头）
    scores = {}
    for r in score_rows:
        key_raw = _row_get(r, "模块", "Module", "module")
        # 提取 A/B/C/D/E/F
        m = re.match(r'([A-F])', key_raw)
        if m:
            key = m.group(1)
            scores[key] = {
                "score": int(_row_get(r, "分数", "Score", "score") or 0),
                "max": int(_row_get(r, "满分", "Max", "max") or 4),
                "description": _row_get(r, "信号含义", "信号", "description", "Detail"),
            }

    # 4. 从 latest.json 提取关键值（兼容 v1/v2 键名）
    status = latest.get("status", latest.get("state", "YELLOW"))
    headline = latest.get("headline", latest.get("recommendation", "--"))
    vix = latest.get("vix", None)
    hyg_lqd = latest.get("hyg_lqd", None)
    ten_year = latest.get("ten_year", None)
    recommendation = latest.get("recommendation", "--")

    # 5. 构建 HTML
    status_cls, status_text = build_status_pill(status)

    # 模板内嵌（不读 docs/index.html，避免被上次输出覆盖）
    html = HTML_TEMPLATE

    # 填充占位符
    ts = datetime.now(timezone(timedelta(hours=8))).strftime(
        "%Y-%m-%d %H:%M:%S · GitHub Actions"
    )

    hero_copy = f'当前读数收敛为：<strong>{headline}</strong>。这不是单纯"跌了就买"的环境，先看利率、实际利率、信用与波动是否继续恶化。'

    replacements = {
        "__TIMESTAMP__": ts,
        "__STATUS_CLASS__": status_cls,
        "__STATUS__": status_text,
        "__HEADLINE__": recommendation,
        "__HERO_COPY__": hero_copy,
        "__HERO_TAGS__": build_hero_tags(vix, hyg_lqd, ten_year, scores),
        "__DECISION_LINES__": build_decision_lines(scores, vix),
        "__WAIT_NOTE__": "如果要重新考虑抄底，优先等：10Y 停止上行、VIX 不再扩张、HYG/LQD 稳住、QQQ 跌幅收敛。",
        "__SCORE_CARDS__": build_score_cards(scores),
        "__CHAIN_NODES__": build_chain_nodes(treasury_rows, etf_rows, scores),
        "__TREASURY_TABLE__": build_treasury_table(treasury_rows, md_text),
        "__ETF_TABLE__": build_etf_table(etf_rows),
        "__EXPLANATION__": build_explanation(md_text),
        "__WATCH_LIST__": build_watch_list(md_text),
        "__CROSS_VALIDATION__": build_cross_validation(md_text),
    }

    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)

    # 6. 写入
    INDEX_PATH.write_text(html, encoding="utf-8")
    print(f"[build_site] [OK] Generated index.html ({INDEX_PATH.stat().st_size} bytes)")

    # 更新时间戳
    ts_path = DOCS_DIR / "build_timestamp.txt"
    ts_path.write_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ\n"),
                       encoding="utf-8")

    # 验证没有残留占位符
    remaining = [p for p in replacements if p in html]
    if remaining:
        print(f"[build_site] [WARN] Placeholders not replaced: {remaining}")
    else:
        print("[build_site] [OK] All placeholders replaced")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
