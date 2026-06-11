@echo off
cd /d "d:\Quan_Strategy\Macro_Research\03_Strategies_Code\chanlun_gui\美元流动性\Fed 反应函数雷达\fed-reaction-dashboard"

echo ============================================================
echo   Fed Dashboard 本地自动推送监听器
echo ============================================================
echo.
echo 监听文件：
echo   - fed_reaction_dashboard.md
echo   - data\latest.json
echo   - data\history.csv
echo.
echo 检测到变化后自动 git add/commit/push。
echo 按 Ctrl+C 停止监听。
echo ============================================================
echo.

python scripts\local_auto_push.py

pause
