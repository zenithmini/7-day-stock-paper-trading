#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 2
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    printf '%s\n' '找不到 Python 3.11 或更新版本；請先確認 Python 安裝與路徑。'
    printf '%s' '按 Enter 關閉這個視窗。'
    read -r research_close
    exit 2
fi
printf '%s\n' '開始加密貨幣量化研究：下載 BTCUSDT 歷史行情並產生訓練報告。'
printf '%s\n' '預設使用 180 天、15 分鐘 K 線；完成後會開啟本機報告。'
python3 -B -m crypto_research start-study --symbol BTCUSDT --interval 15m --days 180 --open-report
research_exit_code=$?
if [ "$research_exit_code" -ne 0 ]; then
    printf '\n%s\n' '研究未完成，請依上方錯誤訊息處理。'
    printf '%s\n' '若為連線問題，請開啟同資料夾的「網路診斷.command」，把診斷結果貼回對話。'
fi
printf '\n%s' '按 Enter 關閉這個視窗。'
read -r research_close
exit "$research_exit_code"
