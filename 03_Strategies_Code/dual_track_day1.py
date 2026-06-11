"""
dual_track_day1.py - Day1 visual + audio dual-track (first 13 videos)
Fixes:
  - cv2.imwrite Chinese path bug -> use imencode + write_bytes
  - Use ThreadPoolExecutor (no pickling issues) for frames
  - Whisper 'small' model for better speed/accuracy on CPU
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np
import whisper

# -- paths --
BASE_DIR   = Path(__file__).resolve().parent.parent
VIDEOS_DIR = BASE_DIR / "05_HotMoney_Logic" / "Videos"
AUDIO_DIR  = BASE_DIR / "05_HotMoney_Logic" / "audio_source"
FRAMES_OUT = BASE_DIR / "05_HotMoney_Logic" / "frames" / "day1"
TRANS_OUT  = BASE_DIR / "05_HotMoney_Logic" / "transcripts" / "day1"

# -- params --
THRESHOLD     = 0.25
SAMPLE_SEC    = 2.0
MIN_GAP_SEC   = 4.0
JPEG_QUALITY  = 85
FRAME_WORKERS = 4
WHISPER_THREADS = 8

_lock = threading.Lock()

def log(msg: str):
    with _lock:
        print(msg, flush=True)


# ===== VISUAL TRACK =====

def hist_dist(g1, g2) -> float:
    h1 = cv2.calcHist([g1], [0], None, [64], [0, 256])
    h2 = cv2.calcHist([g2], [0], None, [64], [0, 256])
    cv2.normalize(h1, h1)
    cv2.normalize(h2, h2)
    return cv2.compareHist(h1, h2, cv2.HISTCMP_BHATTACHARYYA)

def imwrite_safe(path: Path, frame: np.ndarray, quality: int):
    """cv2.imwrite workaround for Windows Chinese paths."""
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if ok:
        path.write_bytes(buf.tobytes())

def extract_frames(mp4_path: Path, idx: int, total: int) -> int:
    out_dir = FRAMES_OUT / mp4_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(mp4_path))
    if not cap.isOpened():
        log(f"[FRAME ] {idx:02d}/{total} ERROR: cannot open {mp4_path.name}")
        return 0

    fps      = cap.get(cv2.CAP_PROP_FPS) or 25
    step_f   = max(1, int(fps * SAMPLE_SEC))
    gap_f    = int(fps * MIN_GAP_SEC)

    prev_gray  = None
    last_saved = -gap_f
    fidx       = 0
    saved      = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if fidx % step_f != 0:
            fidx += 1
            continue

        small = cv2.resize(frame, (320, 180))
        gray  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        do_save = False
        if prev_gray is None:
            do_save = True
        elif (fidx - last_saved) >= gap_f and hist_dist(prev_gray, gray) > THRESHOLD:
            do_save = True

        if do_save:
            ts = fidx / fps
            m, s = int(ts // 60), int(ts % 60)
            fname = f"f{fidx:06d}_{m:02d}m{s:02d}s.jpg"
            imwrite_safe(out_dir / fname, frame, JPEG_QUALITY)
            prev_gray  = gray
            last_saved = fidx
            saved += 1

        fidx += 1

    cap.release()
    log(f"[FRAME ] {idx:02d}/{total} {mp4_path.name} -> {saved} frames")
    return saved

def visual_track(mp4_files: list):
    log(f"[VISUAL] Starting {len(mp4_files)} videos | workers={FRAME_WORKERS} | threshold={THRESHOLD}")
    total_frames = 0
    with ThreadPoolExecutor(max_workers=FRAME_WORKERS) as pool:
        futures = {
            pool.submit(extract_frames, Path(f), i, len(mp4_files)): f
            for i, f in enumerate(mp4_files, 1)
        }
        for fut in as_completed(futures):
            total_frames += fut.result()
    log(f"[VISUAL] Done. Total={total_frames} frames -> {FRAMES_OUT.relative_to(BASE_DIR)}")


# ===== AUDIO TRACK =====

def transcribe_one(mp3_path: Path, model, idx: int, total: int):
    out_md = TRANS_OUT / (mp3_path.stem + ".md")
    if out_md.exists():
        log(f"[AUDIO ] {idx:02d}/{total} SKIP (exists): {mp3_path.name}")
        return

    log(f"[AUDIO ] {idx:02d}/{total} START: {mp3_path.name}")
    result = model.transcribe(
        str(mp3_path),
        language="zh",
        fp16=False,
        verbose=False,
    )
    lines = [f"# Transcript: {mp3_path.stem}\n\n"]
    for seg in result["segments"]:
        m, s = divmod(int(seg["start"]), 60)
        lines.append(f"**[{m:02d}:{s:02d}]** {seg['text'].strip()}\n\n")

    out_md.write_text("".join(lines), encoding="utf-8")
    log(f"[AUDIO ] {idx:02d}/{total} DONE: {out_md.name} | {len(result['segments'])} segments")

def audio_track(mp3_files: list):
    TRANS_OUT.mkdir(parents=True, exist_ok=True)
    log(f"[AUDIO ] Loading Whisper 'base' model (OMP_THREADS={WHISPER_THREADS})...")
    os.environ["OMP_NUM_THREADS"] = str(WHISPER_THREADS)
    model = whisper.load_model("base")
    log(f"[AUDIO ] Model ready. Processing {len(mp3_files)} files sequentially...")
    for i, mp3 in enumerate(mp3_files, 1):
        transcribe_one(Path(mp3), model, i, len(mp3_files))
    log(f"[AUDIO ] All transcripts done -> {TRANS_OUT.relative_to(BASE_DIR)}")


# ===== MAIN =====

def main():
    mp4_files = sorted(str(p) for p in VIDEOS_DIR.glob("*.mp4"))[:13]
    if not mp4_files:
        print("ERROR: no mp4 files found in", VIDEOS_DIR)
        sys.exit(1)

    mp3_files = []
    for f in mp4_files:
        mp3 = AUDIO_DIR / (Path(f).stem + ".mp3")
        if mp3.exists():
            mp3_files.append(str(mp3))
        else:
            log(f"[WARN  ] audio missing, skip: {mp3.name}")

    print("=" * 60)
    print(f"  Day1 Dual-Track | Videos: {len(mp4_files)} | Audio: {len(mp3_files)}")
    print(f"  Frames -> {FRAMES_OUT.relative_to(BASE_DIR)}")
    print(f"  Transcripts -> {TRANS_OUT.relative_to(BASE_DIR)}")
    print("=" * 60)

    t0 = time.time()

    vt = threading.Thread(target=visual_track, args=(mp4_files,), name="visual", daemon=True)
    at = threading.Thread(target=audio_track,  args=(mp3_files,), name="audio",  daemon=True)

    vt.start()
    at.start()
    vt.join()
    at.join()

    print("=" * 60)
    print(f"  ALL DONE - {(time.time()-t0)/60:.1f} min elapsed")
    print("=" * 60)

if __name__ == "__main__":
    main()
