"""
每日全自动脚本：拉数据 → 复制 → 构建 HTML → 推送到 GitHub
用法：python scripts/run_daily.py
前提：FutuOpenD 在 11111 端口运行
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]  # fed-reaction-dashboard/
DASHBOARD_PY = ROOT.parents[1] / "fed_dashboard.py"  # 上级 美元流动性/fed_dashboard.py
MD_SRC = DASHBOARD_PY.parent / "fed_reaction_dashboard.md"  # 上级 .md 输出
MD_DST = ROOT / "fed_reaction_dashboard.md"  # 本目录 .md
DATA_DIR = ROOT / "data"
JSON_PATH = DATA_DIR / "latest.json"
DOCS_DIR = ROOT / "docs"
BUILD_PY = ROOT / "scripts" / "build_site.py"


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def run_cmd(cmd: list[str], cwd=None, check=True) -> int:
    """运行命令，打印输出。"""
    log(f"  >>> {' '.join(cmd)}")
    result = subprocess.run(
        cmd, cwd=cwd or str(ROOT), text=True,
        capture_output=False,  # 实时输出
    )
    if check and result.returncode != 0:
        log(f"  ❌ 命令失败, exit code={result.returncode}")
    return result.returncode


def main():
    log("=" * 60)
    log("Fed Dashboard 每日全自动更新")
    log("=" * 60)

    # ── Step 1: 拉数据 ──
    log("Step 1/4: 拉取实时数据 (yfinance + FutuOpenD) ...")
    if not DASHBOARD_PY.exists():
        log(f"❌ 找不到 fed_dashboard.py: {DASHBOARD_PY}")
        return 1
    rc = run_cmd(["uv", "run", "python", str(DASHBOARD_PY)],
                 cwd=str(DASHBOARD_PY.parent))
    if rc != 0:
        log("❌ 数据拉取失败，中止")
        return 1

    # ── Step 2: 复制产物 ──
    log("Step 2/4: 复制数据产物到 dashboard 目录 ...")
    if not MD_SRC.exists():
        log(f"⚠️ 未找到生成的 .md: {MD_SRC}")
        return 1
    shutil.copy2(str(MD_SRC), str(MD_DST))
    log(f"  ✓ {MD_SRC.name} → {MD_DST}")

    # 验证 latest.json
    if not JSON_PATH.exists():
        log(f"⚠️ 未找到 latest.json: {JSON_PATH}，尝试从 .md 生成...")
        _fallback_json_from_md()
    else:
        log(f"  ✓ latest.json 已存在: {JSON_PATH.stat().st_size} bytes")

    # ── Step 3: 构建 HTML ──
    log("Step 3/4: 构建 HTML (build_site.py) ...")
    # 需要先装 markdown 依赖
    run_cmd(["pip", "install", "-q", "markdown"], check=False)
    run_cmd(["python", str(BUILD_PY)], cwd=str(ROOT))

    # ── Step 4: Git 推送 ──
    log("Step 4/4: 提交并推送到 GitHub ...")
    repo_root = ROOT.parents[1]  # 回到仓库根目录 (Fed 反应函数雷达/)
    
    run_cmd(["git", "add",
             "fed-reaction-dashboard/fed_reaction_dashboard.md",
             "fed-reaction-dashboard/data/latest.json",
             "fed-reaction-dashboard/docs/",
             ".github/workflows/build-pages.yml"],
            cwd=str(repo_root))
    
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    run_cmd(["git", "commit", "-m", f"auto: daily dashboard update {ts}"],
            cwd=str(repo_root), check=False)
    # 先拉远程再推，--autostash 自动暂存未跟踪改动防止冲突
    run_cmd(["git", "pull", "--rebase", "--autostash", "origin", "main"],
            cwd=str(repo_root), check=False)
    run_cmd(["git", "push", "origin", "master:main"], cwd=str(repo_root))

    log("=" * 60)
    log("✅ 全部完成！等 GitHub Actions 部署后刷新页面即可。")
    log(f"   https://slowqqwin-beep.github.io/hibor-dashboard/")
    log("=" * 60)
    return 0


def _fallback_json_from_md():
    """从 .md 中提取 vix/hyg_lqd/10Y 生成 latest.json 兜底。"""
    import re
    if not MD_DST.exists():
        log("  ❌ .md 也不存在，无法兜底")
        return
    md = MD_DST.read_text(encoding="utf-8")
    latest = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "YELLOW",
        "headline": "自动更新",
        "recommendation": "自动更新",
        "vix": None,
        "hyg_lqd": None,
        "ten_year": None,
    }
    # VIX
    m = re.search(r'VIX.*?(\d{2}\.\d+)', md)
    if m: latest["vix"] = float(m.group(1))
    # HYG/LQD
    m = re.search(r'HYG/LQD.*?(\d\.\d+)', md)
    if m: latest["hyg_lqd"] = float(m.group(1))
    # 10Y
    m = re.search(r'10Y.*?(\d\.\d+)%', md)
    if m: latest["ten_year"] = float(m.group(1))
    # 抄底建议
    m = re.search(r'\*\*(RED|GREEN|YELLOW|ORANGE)\*\*', md)
    if m: latest["status"] = m.group(1)
    m = re.search(r'\*\*(RED|GREEN|YELLOW|ORANGE)\*\*:\s*(.+)', md)
    if m:
        latest["headline"] = m.group(2).replace("**", "")
        latest["recommendation"] = latest["headline"]
    
    os.makedirs(str(DATA_DIR), exist_ok=True)
    JSON_PATH.write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  ✓ 从 .md 兜底生成 latest.json")


if __name__ == "__main__":
    sys.exit(main())
