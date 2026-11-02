@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo 開始加密貨幣量化研究：下載 BTCUSDT 歷史行情並產生訓練報告。
echo 預設使用 180 天、15 分鐘 K 線；完成後會開啟本機報告。
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
    python -B -m crypto_research start-study --symbol BTCUSDT --interval 15m --days 180 --open-report
) else (
    py -3 -B -m crypto_research start-study --symbol BTCUSDT --interval 15m --days 180 --open-report
)
set "research_exit_code=%errorlevel%"
if not "%research_exit_code%"=="0" (
    echo.
    echo 研究未完成。請確認 Python 3.11 或更新版本已安裝，並查看上方訊息。
)
echo.
pause
exit /b %research_exit_code%
