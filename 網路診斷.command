#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 2
printf '%s\n' '量化研究網路診斷：使用同一套 Python 檢查兩個幣安官方行情主機。'
printf '%s\n' '只查公開時間及一根已收盤 K 線；不讀取帳戶、API 密鑰或交易紀錄。'
python3 -B -m crypto_research diagnose-network
research_exit_code=$?
printf '\n%s\n' '請將上方診斷結果貼回對話；網址測試不會更改行情來源。'
printf '%s' '按 Enter 關閉這個視窗。'
read -r research_close
exit "$research_exit_code"
