# Fed Reaction Dashboard

**Fed 反应函数雷达** — 每日市场状态自动网页仪表盘。

## 架构：方案二（本地 push 源文件 + GitHub 云端构建）

```
本地编辑 Markdown / JSON
        ↓
本地 watcher 自动 git push
        ↓
GitHub Actions 接收 push
        ↓
云端运行 build_site.py 生成网页
        ↓
GitHub Pages 展示
```

- 本地不构建网页，只负责写/推源文件
- 网页由 GitHub Actions 云端生成
- 无 API key、无 cookie、无 Futu 依赖

## 文件结构

```
fed-reaction-dashboard/
├── .github/workflows/build-pages.yml  # CI/CD：push 触发 → 构建网页
├── scripts/
│   ├── local_auto_push.py             # 本地 watcher：监听源文件 → 自动 push
│   └── build_site.py                  # 云端构建：读取源文件 → 生成 HTML
├── data/
│   ├── latest.json                    # 机器可读数据 + 评分（本地生成）
│   └── history.csv                    # 历史评分时间序列（本地生成）
├── docs/                              # GitHub Pages 根目录（云端生成，不手动编辑）
│   ├── index.html
│   └── assets/
│       ├── style.css
│       └── app.js
├── fed_reaction_dashboard.md          # Markdown 报告（本地编辑）
├── requirements.txt                   # 云端依赖
├── requirements-local.txt             # 本地依赖（仅 watchdog）
├── start_auto_push.bat                # Windows 一键启动监听器
└── README.md
```

## 快速开始

### 1. GitHub Pages 设置

在 GitHub 仓库中：
- **Settings → Pages → Source: Deploy from a branch**
- Branch: `main`, Folder: `/docs`
- Save

### 2. 本地安装

```powershell
cd fed-reaction-dashboard
pip install -r requirements-local.txt
```

### 3. 启动本地自动推送

双击 `start_auto_push.bat`，或在终端运行：

```powershell
python scripts\local_auto_push.py
```

它会持续监听 `fed_reaction_dashboard.md`、`data/latest.json`、`data/history.csv` 三个文件，检测到变化后自动 `git add → commit → push`。

### 4. 更新仪表盘内容

编辑以下文件之一：
- `fed_reaction_dashboard.md` — 写分析报告
- `data/latest.json` — 更新数据和评分
- `data/history.csv` — 追加历史记录

保存后 watcher 会自动推送，GitHub Actions 随后自动生成网页。

### 5. 查看仪表盘

```
https://你的用户名.github.io/仓库名/
```

### 6. 手动触发构建

在 GitHub 仓库中：**Actions → Build Dashboard Pages → Run workflow**

## 触发条件

GitHub Actions 仅在以下文件被 push 到 main 分支时触发：

- `fed_reaction_dashboard.md`
- `data/**`
- `scripts/build_site.py`
- `.github/workflows/build-pages.yml`

**不会**因为 `docs/` 目录变化而触发，避免循环。

## 网页功能

- 🟢🟡🔴 红黄绿交通灯总状态
- 六模块评分卡（Fed 鹰派/鸽派/估值挤压/通胀/流动性/增长恐慌）
- 资产反应链条（Fed 预期 → 名义利率 → 实际利率 → 美元 → 黄金/成长股 → 信用 → 波动率）
- 关键数据表
- 自动市场解读
- 抄底评估与等待信号
- Chart.js 历史趋势图

## 安全注意事项

- 不要把 API key、密码、cookie 写进仓库
- `docs/` 目录由 GitHub Actions 自动生成，不要手动编辑
- 本地只 push 三个源文件，不 push 网页文件
