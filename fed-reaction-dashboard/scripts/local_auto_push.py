"""
本地自动推送脚本 — 监听源文件变化，自动 git add/commit/push。

只监听：
  - fed_reaction_dashboard.md
  - data/latest.json
  - data/history.csv

不在本地构建网页，网页交给 GitHub Actions 云端生成。
"""

import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("❌ 缺少 watchdog，请先安装：")
    print("   pip install -r requirements-local.txt")
    sys.exit(1)


ROOT = Path(__file__).resolve().parents[1]

WATCH_FILES = [
    ROOT / "fed_reaction_dashboard.md",
    ROOT / "data" / "latest.json",
    ROOT / "data" / "history.csv",
]

DEBOUNCE_SECONDS = 20


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def run(cmd: list[str]) -> int:
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )
    if result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            log(f"  {line}")
    if result.stderr.strip():
        for line in result.stderr.strip().splitlines():
            log(f"  [stderr] {line}")
    return result.returncode


def has_changes() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )
    # 只关心我们监听的三个文件
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        path_part = line[3:].strip()
        if any(
            path_part.endswith(f.name) or path_part.endswith(str(f.relative_to(ROOT)))
            for f in WATCH_FILES
        ):
            return True
    return False


def auto_push():
    log("准备自动提交并推送...")

    if not has_changes():
        log("没有检测到目标文件变化，跳过。")
        return

    # 只 add 监听的三个文件
    code = run(["git", "add", "fed_reaction_dashboard.md", "data/latest.json", "data/history.csv"])
    if code != 0:
        log("⚠ git add 异常，但继续尝试 commit。")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_msg = f"Update dashboard source {timestamp}"

    commit_code = run(["git", "commit", "-m", commit_msg])

    if commit_code != 0:
        log("没有可提交内容，跳过 push。")
        return

    push_code = run(["git", "push"])

    if push_code == 0:
        log("✅ 已推送到 GitHub，GitHub Actions 会自动生成网页。")
    else:
        log("❌ push 失败，请检查网络、GitHub 权限或是否有远程冲突。")


class ChangeHandler(FileSystemEventHandler):
    def __init__(self):
        self.timer: threading.Timer | None = None
        self.lock = threading.Lock()

    def on_modified(self, event):
        if event.is_directory:
            return

        changed_path = Path(event.src_path).resolve()
        if changed_path not in {f.resolve() for f in WATCH_FILES}:
            return

        log(f"检测到文件变化: {changed_path.name}")

        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(DEBOUNCE_SECONDS, auto_push)
            self.timer.start()
            log(f"将在 {DEBOUNCE_SECONDS} 秒防抖后自动提交...")


def main():
    log("============================================================")
    log("Fed Dashboard 本地自动推送监听器")
    log("============================================================")
    log(f"项目目录: {ROOT}")
    log("监听文件:")
    for f in WATCH_FILES:
        exists = "✓" if f.exists() else "✗ (不存在)"
        log(f"  {exists}  {f.name}")
    log("")
    log("等待文件变化... 按 Ctrl+C 停止")
    log("============================================================")

    handler = ChangeHandler()
    observer = Observer()

    watch_dirs = sorted({f.parent for f in WATCH_FILES})
    for directory in watch_dirs:
        if directory.exists():
            observer.schedule(handler, str(directory), recursive=False)
            log(f"监听目录: {directory}")
        else:
            log(f"⚠ 目录不存在，跳过监听: {directory}")

    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("")
        log("收到停止信号，正在退出...")
        observer.stop()

    observer.join()
    log("监听器已停止。")


if __name__ == "__main__":
    main()
