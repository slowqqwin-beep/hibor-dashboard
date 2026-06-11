"""
Day2 批量转录脚本 - 20231105 系列 (14 个文件)
转录稿 -> transcripts/day2/
截图   -> frames/day2/<文件名>/
"""

import os
import subprocess
import whisper
import re

BASE = "D:/Quan_Strategy/05_HotMoney_Logic"
AUDIO_DIR = f"{BASE}/audio_source"
VIDEO_DIR = f"{BASE}/Videos"
TRANS_DIR = f"{BASE}/transcripts/day2"
FRAMES_DIR = f"{BASE}/frames/day2"

# 14 个目标文件（按课程顺序）
FILES = [
    "20231105上午_part000",
    "20231105上午_part001",
    "20231105上午_part002",
    "20231105上午_part003",
    "20231105上午_part004",
    "20231105下午_part000",
    "20231105下午_part001",
    "20231105下午_part002",
    "20231105下午_part003",
    "20231105下午_part004",
    "20231105下午_part005",
    "20231105下午_part006",
    "20231105下午_part007",
    "20231105下午_part008",
]

DATE_DENY_PATTERNS = [
    r"今天不是.{0,6}(11月5日|1105|11\.5)",
    r"(今天|现在).{0,10}(11月4日|1104|11\.4)",
    r"(今天|现在).{0,10}(不是|并非).{0,10}(今天|11月)",
]

def check_date_disclaimer(text):
    """检查主讲人是否说今天不是 11月5日"""
    for pat in DATE_DENY_PATTERNS:
        if re.search(pat, text):
            return True
    return False

def extract_frames(name):
    """每隔 60 秒截一张图"""
    video_path = f"{VIDEO_DIR}/{name}.mp4"
    out_dir = f"{FRAMES_DIR}/{name}"
    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", "fps=1/60",
        "-q:v", "2",
        f"{out_dir}/frame_%04d.jpg",
        "-y", "-loglevel", "error"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [截图错误] {name}: {result.stderr[:200]}")
        return False
    count = len([f for f in os.listdir(out_dir) if f.endswith(".jpg")])
    print(f"  [截图] {name}: {count} 张")
    return True

def transcribe_and_save(model, name):
    audio_path = f"{AUDIO_DIR}/{name}.mp3"
    out_path = f"{TRANS_DIR}/{name}.md"

    if not os.path.exists(audio_path):
        print(f"  [跳过] 音频不存在: {audio_path}")
        return

    print(f"\n[转录中] {name} ...")
    result = model.transcribe(audio_path, language="zh", verbose=False)

    segments = result.get("segments", [])
    full_text = result.get("text", "")

    # 日期校验
    date_warn = ""
    if check_date_disclaimer(full_text):
        date_warn = (
            "> ⚠️ **日期警告**：主讲人在音频中提及今天可能不是 11月5日，"
            "请人工核实内容归属日期。\n\n"
        )

    # 格式化为 Markdown
    lines = [f"# Transcript: {name}\n"]
    if date_warn:
        lines.append(date_warn)
    lines.append(f"**课程日期（文件名）**：20231105\n\n")

    for seg in segments:
        start = seg["start"]
        minutes = int(start // 60)
        seconds = int(start % 60)
        ts = f"{minutes:02d}:{seconds:02d}"
        text = seg["text"].strip()
        if text:
            lines.append(f"**[{ts}]** {text}\n\n")

    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"  [完成] 转录稿已保存: {out_path}")

def main():
    print("=" * 60)
    print("Day2 转录批处理启动")
    print(f"共 {len(FILES)} 个文件")
    print("=" * 60)

    print("\n加载 Whisper 模型 (medium)...")
    model = whisper.load_model("medium")
    print("模型加载完成。\n")

    for i, name in enumerate(FILES, 1):
        print(f"\n[{i:02d}/{len(FILES)}] 处理: {name}")
        # 并行逻辑：先启动截图（非阻塞），再转录
        extract_frames(name)
        transcribe_and_save(model, name)

    print("\n" + "=" * 60)
    print("全部完成！")
    print(f"转录稿: {TRANS_DIR}")
    print(f"截图:   {FRAMES_DIR}")
    print("=" * 60)

if __name__ == "__main__":
    main()
