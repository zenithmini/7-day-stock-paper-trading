#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 2
printf '%s\n' '開始加密貨幣量化研究：下載 BTCUSDT 歷史行情並產生訓練報告。'
printf '%s\n' '預設使用 180 天、15 分鐘 K 線；完成後會開啟本機報告。'
python3 -B -m crypto_research start-study --symbol BTCUSDT --interval 15m --days 180 --open-report
research_exit_code=$?
if [ "$research_exit_code" -ne 0 ]; then
    printf '\n%s\n' '研究未完成。請確認 Python 3.11 或更新版本已安裝，並查看上方訊息。'
fi
printf '\n%s' '按 Enter 關閉這個視窗。'
read -r research_close
exit "$research_exit_code"
