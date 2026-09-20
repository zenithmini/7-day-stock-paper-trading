# 本機模擬交易研究版 — 安全修補版

以 [yomislight/7-day-stock-paper-trading](https://github.com/yomislight/7-day-stock-paper-trading) 的
`332d3c481855209d4a28bdeab92965b3bbeb4e29` 為基礎。保留原作者來源與歷史；本分支不是作者的官方版本。

**目前提供股票／ETF 模擬帳本與唯讀檢查。沒有實盤下單、24 小時監控、內建 AI 推論或自我訓練功能。**
原作者的已驗證帳戶、排程及交易紀錄只是歷史參考，不能代表你的帳戶已通過驗證。

## 這次修正

- API 密鑰必須有讀取權限，且所有已知交易、提領、劃轉權限明確關閉；缺少欄位或未知啟用權限即拒絕。
- 所有網路讀取只允許固定 Binance HTTPS 主機，不接受重新導向；不印出原始帳戶回應或密鑰。
- `run_id` 僅允許有限長度的英數字、底線及連字號，拒絕路徑穿越和符號連結輸出。
- 模擬資金及事件在同一個 SQLite 交易中保存；跨程序鎖、重複執行保護、JSON 匯出恢復及本機備份。
- 使用 `.local/` 保存個人運行資料；Git 忽略整個資料夾。初始化不繼承原作者密鑰、日期或帳戶核准狀態。
- GitHub Actions **只跑離線測試**，不讀取 Secrets、不操作帳戶、不上傳交易紀錄、不傳 Telegram 通知。
- 行情時間改為回應接收時間，拒絕超過 5 秒的報價請求；未取得交易所事件時間時，不宣稱已驗證行情新鮮度。

詳見 [修補紀錄與限制](HARDENING.md)。

## 先做離線驗證

需要 Python 3.11 或更新版本。Windows 若缺少 IANA 時區資料，先安裝固定版本：

```powershell
python -m pip install tzdata==2025.2
```

在專案根目錄執行（macOS 可將 `python` 改為 `python3`）：

```sh
python -B run_tests.py
python -B "06-程序脚本-scripts/local_runtime.py"
python -B "06-程序脚本-scripts/run_observation.py" --dry-run
```

離線測試會阻擋真實網路連線，全部使用虛構帳戶資料與暫存帳本。
初始化預設 **不啟用模擬交易及排程**，重複初始化不會覆蓋既有資料。

## 選擇性唯讀帳戶檢查

此版本的股票行情來源需要使用者自己的 Binance Stocks 存取資格與唯讀密鑰。
資格及權限無法核實時，保持停用；不要為了通過檢查而開啟交易、提領或劃轉權限。
純加密貨幣公開行情版本尚未加入，沒有密鑰也能先執行上述離線測試。

密鑰只能在本機填入 `.local/09-API密钥-仅本地/binance-api.env`，不需填到 GitHub Secrets 或聊天。
macOS/Linux 此檔案必須保持 `600` 權限；Windows 請放在自己的使用者目錄，避免共享給其他帳戶。

```sh
python -B "06-程序脚本-scripts/binance_readiness_check.py" --check-config
python -B "06-程序脚本-scripts/binance_readiness_check.py" --update-readiness
python -B "06-程序脚本-scripts/run_observation.py" --manual
```

`--check-config` 只檢查本地格式；密鑰空白時回傳碼 2。其餘兩個命令會呼叫官方唯讀 API。
這些命令不提交真實訂單，也不會自動啟用交易。API 讀取成功不等於地區資格或策略獲利已獲驗證。

## 帳本及恢復

`.local/05-交易记录-data/paper-ledger.sqlite3` 是模擬帳本的唯一權威來源；同目錄的 JSON 是可重建匯出。
不要直接修改 JSON 來調整已初始化的資金、持倉或事件，也不要刪除資料庫來處理一般錯誤。
`last-observation.json` 與 `last-paper-run.json` 是獨立執行資訊，不會覆寫帳本。

```sh
python -B "06-程序脚本-scripts/paper_trade.py" reconcile
python -B "06-程序脚本-scripts/backup_local.py"
```

備份僅包含一致的模擬帳本資料庫，不包含密鑰。備份也屬於個人資料，請留在本機。
遇到寫檔中斷時先 reconcile，再以**同一個 run_id** 重試原決策，避免重複扣款。
同一個 run_id 不可用於不同決策。程序衝突時稍後重试，不要手動刪除鎖來強行執行。
目前各種輸入／API 失敗會停止該次工作；沒有背景自動重連或連續止損服務。

## 運行範圍

- GitHub 保存程式、測試及文件；個人行情、帳本與密鑰放在本機。
- 股市排程範本仍保留供研究，但新工作區日期為空、狀態為停用。
- 目前仍需外部提供決策 JSON，沒有自動產生 AI 決策的程式。
- `manage/evaluate` 被執行時才檢查止損、停利及不隔夜；電腦關機、休眠或斷線期間不會執行。
- Windows、macOS、Linux 已配置 CI 測試矩陣；實際雲端 CI 結果請查看 Actions，不以本機測試代替。
- `.gitignore` 不會過濾手動上傳、壓縮包或強制 `git add -f`；分享時不得包含 `.local/`。

## 上游資料與後续方向

`01-开始使用` 至 `09-API密钥-仅本地` 中原有文件及紀錄保留作為來源歷史。
舊文件若提到自動任務已啟用、舊資料路徑或帳戶已驗證，**不適用於本分支的新工作區**，以本頁為準。
來源版本未附明確 LICENSE；本分支保留作者署名，不另行宣稱取得上游商用或重新授權權利。

下一階段才是加密貨幣公開行情、24 小時資料收集、持續模擬監控、看板與 AI 分析；這些不是本次修補已完成的功能。
