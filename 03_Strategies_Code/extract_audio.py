"""
extract_audio.py
将 05_HotMoney_Logic/Videos/ 下的所有 .mp4 批量转换为 .mp3
输出到 05_HotMoney_Logic/audio_source/

依赖：系统已安装 ffmpeg（无需额外 Python 库）

用法：
    python 03_Strategies_Code/extract_audio.py
可选参数：
    --bitrate  音频码率，默认 128k（可选 64k / 192k / 320k）
    --workers  并发转换数，默认 4
"""

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent.parent
VIDEOS_DIR  = BASE_DIR / "05_HotMoney_Logic" / "Videos"
OUTPUT_DIR  = BASE_DIR / "05_HotMoney_Logic" / "audio_source"


def convert(mp4_path: Path, bitrate: str) -> tuple[Path, bool, str]:
    """调用 ffmpeg 将单个 mp4 转为 mp3，返回 (路径, 是否成功, 错误信息)。"""
    out_path = OUTPUT_DIR / (mp4_path.stem + ".mp3")

    if out_path.exists():
        return out_path, True, "已存在，跳过"

    cmd = [
        "ffmpeg",
        "-i", str(mp4_path),       # 输入
        "-vn",                      # 不处理视频流
        "-acodec", "libmp3lame",    # MP3 编码器
        "-ab", bitrate,             # 音频码率
        "-ar", "44100",             # 采样率
        "-y",                       # 覆盖输出（幂等）
        str(out_path),
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        # 提取 ffmpeg 错误的最后一行
        err = result.stderr.strip().splitlines()[-1] if result.stderr else "未知错误"
        return out_path, False, err

    return out_path, True, "转换成功"


def main():
    parser = argparse.ArgumentParser(description="批量 MP4 → MP3 转换")
    parser.add_argument("--bitrate", default="128k",
                        choices=["64k", "128k", "192k", "320k"],
                        help="音频码率（默认 128k）")
    parser.add_argument("--workers", type=int, default=4,
                        help="并发线程数（默认 4）")
    args = parser.parse_args()

    # 检查输入目录
    if not VIDEOS_DIR.exists():
        print(f"[错误] 视频目录不存在：{VIDEOS_DIR}")
        sys.exit(1)

    mp4_files = sorted(VIDEOS_DIR.glob("*.mp4"))
    if not mp4_files:
        print(f"[警告] 未找到 .mp4 文件：{VIDEOS_DIR}")
        sys.exit(0)

    # 创建输出目录
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total    = len(mp4_files)
    done     = 0
    skipped  = 0
    failed   = 0

    print(f"共找到 {total} 个视频，码率={args.bitrate}，并发={args.workers}")
    print(f"输出目录：{OUTPUT_DIR}\n")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(convert, f, args.bitrate): f for f in mp4_files}

        for future in as_completed(futures):
            out_path, ok, msg = future.result()
            done += 1

            if not ok:
                failed += 1
                status = f"[失败] {out_path.name} — {msg}"
            elif msg == "已存在，跳过":
                skipped += 1
                status = f"[跳过] {out_path.name}"
            else:
                status = f"[{done:02d}/{total}] {out_path.name}"

            print(status)

    print(f"\n完成：成功 {done - failed - skipped} 个 | 跳过 {skipped} 个 | 失败 {failed} 个")
    if failed:
        print("[提示] 失败文件请检查 ffmpeg 是否支持该视频编码格式。")


if __name__ == "__main__":
    main()
