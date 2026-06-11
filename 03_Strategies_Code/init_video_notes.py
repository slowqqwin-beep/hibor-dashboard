"""
init_video_notes.py
遍历 05_HotMoney_Logic/Videos 下的所有 .mp4 文件，
为每个视频在 05_HotMoney_Logic 目录下生成同名复盘笔记。
"""

import os
import re
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent          # D:\Quan_Strategy
VIDEOS_DIR = BASE_DIR / "05_HotMoney_Logic" / "Videos"
NOTES_DIR  = BASE_DIR / "05_HotMoney_Logic"

# ── 从文件名提取日期（支持 YYYY-MM-DD 或 YYYYMMDD） ───────
_DATE_PATTERNS = [
    re.compile(r"(\d{4}-\d{2}-\d{2})"),   # 2026-03-15
    re.compile(r"(\d{4})(\d{2})(\d{2})"),  # 20260315
]

def extract_date(stem: str) -> str:
    """从文件名 stem 中提取日期字符串，无法识别则返回空字符串。"""
    m = _DATE_PATTERNS[0].search(stem)
    if m:
        return m.group(1)
    m = _DATE_PATTERNS[1].search(stem)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def build_note(video_filename: str, date_str: str) -> str:
    """根据视频文件名和日期生成 Markdown 笔记内容。"""
    title = Path(video_filename).stem
    date_field = date_str or "未知日期"

    return f"""\
---
date: {date_field}
video: "[[Videos/{video_filename}]]"
tags: [复盘, 游资, 视频笔记]
type: video_review
---

# {title}

![[Videos/{video_filename}]]

---

## 一、宏观流动性（盾）

| 指标 | 数值 | 变动 | 备注 |
|------|------|------|------|
| 美债 10Y 收益率 |  |  |  |
| 美债 2Y 收益率 |  |  |  |
| 10Y/2Y 利差 |  |  |  |
| DXY 美元指数 |  |  |  |
| VIX 恐慌指数 |  |  |  |

**宏观研判：**
>

---

## 二、缠论结构（骨）

### 重点标的

| 标的 | 级别 | 当前结构 | 笔/线段位置 | 中枢状态 |
|------|------|----------|-------------|----------|
|  |  |  |  |  |
|  |  |  |  |  |

**结构小结：**
>

---

## 三、游资情绪（剑）

### 连板梯队

| 板块/题材 | 领头羊 | 连板数 | 情绪级别 |
|-----------|--------|--------|----------|
|  |  |  |  |

### 题材溢价观察

- 强势题材：
- 衰减题材：
- 次日关注：

**情绪研判：**
>

---

## 四、视频要点摘要

-
-
-

---

## 五、操作记录

| 标的 | 方向 | 价格 | 逻辑 | 结果 |
|------|------|------|------|------|
|  |  |  |  |  |

---

## 六、复盘总结

**得分（1-10）：**
- 宏观判断：
- 结构识别：
- 情绪把握：
- 执行纪律：

**一句话总结：**
>
"""


def main():
    if not VIDEOS_DIR.exists():
        print(f"[错误] 视频目录不存在：{VIDEOS_DIR}")
        return

    mp4_files = sorted(VIDEOS_DIR.glob("*.mp4"))
    if not mp4_files:
        print(f"[警告] 未找到任何 .mp4 文件：{VIDEOS_DIR}")
        return

    created = skipped = 0

    for mp4_path in mp4_files:
        note_path = NOTES_DIR / (mp4_path.stem + ".md")

        if note_path.exists():
            print(f"[跳过] 已存在：{note_path.name}")
            skipped += 1
            continue

        date_str   = extract_date(mp4_path.stem)
        note_content = build_note(mp4_path.name, date_str)
        note_path.write_text(note_content, encoding="utf-8")

        print(f"[生成] {note_path.name}  (日期={date_str or '未识别'})")
        created += 1

    print(f"\n完成：新建 {created} 个笔记，跳过 {skipped} 个已存在笔记。")


if __name__ == "__main__":
    main()
