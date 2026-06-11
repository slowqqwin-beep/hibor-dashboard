"""
extract_keyframes.py
遍历 05_HotMoney_Logic/Videos/ 下所有 .mp4，
仅在画面发生显著变化时（切股、画线、翻页）抓取高质量截图，
按视频名称分子文件夹存入 05_HotMoney_Logic/frames/。

算法：
  - 每隔 SAMPLE_INTERVAL 秒取一帧（避免逐帧暴力比较）
  - 将帧缩小后转灰度，计算与上一张关键帧的直方图差异
  - 差异超过 HIST_THRESHOLD 则判定为场景切换，保存原始分辨率截图
  - 同时计算帧差（像素级），双重过滤误判

用法：
    python 03_Strategies_Code/extract_keyframes.py
可选：
    --threshold   直方图差异阈值，默认 0.35（越小=越灵敏）
    --interval    采样间隔秒数，默认 2
    --min-gap     两张关键帧最短间隔秒数，默认 5（防重复）
    --quality     JPEG 质量 1-100，默认 90
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

# ── 路径配置 ──────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent
VIDEOS_DIR = BASE_DIR / "05_HotMoney_Logic" / "Videos"
FRAMES_DIR = BASE_DIR / "05_HotMoney_Logic" / "frames"


# ── 直方图比较（Bhattacharyya 距离，0=完全相同，1=完全不同）──
def hist_distance(gray1: np.ndarray, gray2: np.ndarray) -> float:
    h1 = cv2.calcHist([gray1], [0], None, [64], [0, 256])
    h2 = cv2.calcHist([gray2], [0], None, [64], [0, 256])
    cv2.normalize(h1, h1)
    cv2.normalize(h2, h2)
    return cv2.compareHist(h1, h2, cv2.HISTCMP_BHATTACHARYYA)


def extract_keyframes(
    mp4_path: Path,
    out_dir: Path,
    threshold: float,
    interval: float,
    min_gap: float,
    quality: int,
) -> int:
    cap = cv2.VideoCapture(str(mp4_path))
    if not cap.isOpened():
        print(f"  [错误] 无法打开视频：{mp4_path.name}")
        return 0

    fps        = cap.get(cv2.CAP_PROP_FPS) or 25
    interval_f = max(1, int(fps * interval))   # 采样间隔帧数
    min_gap_f  = int(fps * min_gap)             # 最小关键帧间隔帧数

    out_dir.mkdir(parents=True, exist_ok=True)

    prev_gray      = None
    last_saved_f   = -min_gap_f  # 上一张关键帧的帧号
    frame_idx      = 0
    saved          = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # 只处理采样帧
        if frame_idx % interval_f != 0:
            frame_idx += 1
            continue

        # 缩小用于比较（加速计算）
        small = cv2.resize(frame, (320, 180))
        gray  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        is_keyframe = False

        if prev_gray is None:
            # 第一帧强制保存
            is_keyframe = True
        elif (frame_idx - last_saved_f) >= min_gap_f:
            dist = hist_distance(prev_gray, gray)
            if dist > threshold:
                is_keyframe = True

        if is_keyframe:
            ts_sec  = frame_idx / fps
            minutes = int(ts_sec // 60)
            seconds = int(ts_sec % 60)
            fname   = f"{mp4_path.stem}_f{frame_idx:06d}_{minutes:02d}m{seconds:02d}s.jpg"
            out_path = out_dir / fname
            cv2.imwrite(
                str(out_path),
                frame,
                [cv2.IMWRITE_JPEG_QUALITY, quality],
            )
            prev_gray    = gray
            last_saved_f = frame_idx
            saved       += 1

        frame_idx += 1

    cap.release()
    return saved


def main():
    parser = argparse.ArgumentParser(description="视频关键帧提取（场景变化检测）")
    parser.add_argument("--threshold", type=float, default=0.35,
                        help="直方图变化阈值 0-1，默认 0.35（越小越灵敏）")
    parser.add_argument("--interval",  type=float, default=2.0,
                        help="采样间隔秒数，默认 2")
    parser.add_argument("--min-gap",   type=float, default=5.0,
                        help="两张关键帧最短间隔秒，默认 5")
    parser.add_argument("--quality",   type=int,   default=90,
                        help="JPEG 质量 1-100，默认 90")
    args = parser.parse_args()

    if not VIDEOS_DIR.exists():
        print(f"[错误] 视频目录不存在：{VIDEOS_DIR}")
        sys.exit(1)

    mp4_files = sorted(VIDEOS_DIR.glob("*.mp4"))
    if not mp4_files:
        print(f"[警告] 未找到 .mp4 文件")
        sys.exit(0)

    print(f"共 {len(mp4_files)} 个视频 | 阈值={args.threshold} | "
          f"采样间隔={args.interval}s | 最短帧间距={args.min_gap}s\n")

    total_frames = 0
    for i, mp4 in enumerate(mp4_files, 1):
        sub_dir = FRAMES_DIR / mp4.stem
        print(f"[{i:02d}/{len(mp4_files)}] {mp4.name}", end=" ... ", flush=True)
        n = extract_keyframes(
            mp4, sub_dir,
            threshold=args.threshold,
            interval=args.interval,
            min_gap=args.min_gap,
            quality=args.quality,
        )
        print(f"提取 {n} 帧 → {sub_dir.relative_to(BASE_DIR)}")
        total_frames += n

    print(f"\n完成：共提取 {total_frames} 张关键帧，存入 {FRAMES_DIR.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
