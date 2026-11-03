# 加密貨幣量化與 AI 研究

本專案的開發目標是：以可重現的量化策略產生訊號，本地 AI 與 ChatGPT 提出改良假設，
用獨立測試及持續模擬評估扣除成本後的報酬與風險。獲利是研究目標，不是目前已證明的能力。

第一版雲端合作方式依使用者選擇：**匯出報告給 ChatGPT，再匯回 JSON 候選策略**。
目前不串接 OpenAI API，不需要雲端金鑰，不會自動产生 API 使用費。
本地推論可選擇呼叫已安裝的 Ollama 模型。

## 已完成的研究基礎

- `crypto_research` 是獨立的現貨多頭／空手研究模組，不使用股票時段或 Stocks 帳戶權限。
- 讀取本機 OHLC JSON，檢查重複、缺漏、不連續時段、OHLC 不一致及非有限數字。
- 固定的 SMA 交叉策略：完整 K 線收盤產生訊號，下一根開盤模擬成交；期末明確平倉。
- 雙邊模擬手續費與不利滑價、收盤取樣回撤、淨報酬、交易數、勝率及 profit factor。
- 進場包含手續費的資金預算最多為當時資金的 10%，不借款、不放空、不加碼。
- 同初始投入比例的持幣基準與空手基準；兩者仍不代表相同持倉時間。
- 報告與資料 SHA-256 指紋；候選回測前會重新產生原報告，確認可重現。
- 匯出 ChatGPT 提示與候選範本；可接收 `no_change`，資料不足時不用勉強修改策略。
- Ollama 只連接 `127.0.0.1:11434`，停用環境代理、拒絕重新導向與明顯 cloud 模型名稱。
- AI 只能提出快／慢 SMA 和投入比例，不能改手續費、滑價、本金或執行程式。
- 原策略與候選採同一份資料及費用假設比較；每個策略各列自己的持幣基準。
- 公開歷史行情下載器會先讀交易所時間，只接受完整、連續且已收盤的 K 線；分頁不完整時不保存部分資料。
- 固定按時間切成 60% 訓練、20% 驗證、20% 保留；AI 只取得訓練段報告。
- 最終保留測試在計算績效前建立使用紀錄；研究名稱或資料名稱變更不會重設相同保留資料的識別。
- Windows／Mac 啟動入口與可離線開啟的 HTML 歷史研究結果頁。

這個版本提供時間切分研究流程，**尚未建立真實市場的獲利證據、24 小時行情服務或下單功能**。
候選結果一律 `promotion_allowed: false`，不會自動啟用或改動原股票帳本。

## 先開啟真實行情研究流程

解壓縮完整專案後，Windows 11 雙擊 `開始量化研究.bat`，Mac 執行 `開始量化研究.command`。
若 Mac 壓縮包沒有保留執行權限，可在專案目錄使用 `bash 開始量化研究.command`。
需要 Python 3.11+ 與網路；不需幣安帳戶或 API 密鑰。也可在專案根目錄執行：

```sh
python -B -m crypto_research start-study --symbol BTCUSDT --interval 15m --days 180 --open-report
```

它會下載最近 180 個完整 UTC 日、共 17,280 根 15 分鐘 K 線，切成訓練 10,368 根、驗證 3,456 根、
保留 3,456 根，再開啟本機 HTML 頁面。BTCUSDT 只是工程示例，不是買入建議。
研究編號自動產生，所有結果放在 `.local/crypto-research/`，不會上傳雲端。
頁面只顯示訓練段績效、各段時間及可複製的 AI 提示；不是即時行情看板。

`--days` 可指定 7 至 365 個完整 UTC 日；下載仍受最多 100,000 根限制，例如 1 分鐘資料不能一次取 180 天。
其他初始成本與策略假設可透過 `--parameters <local-json>` 設定，格式見下方。
資料不足以涵蓋指定區間、缺 K 線、重複、未收盤、HTTP 418／429 或連線失敗會停止；不重試、不補造資料。
下載預設使用 `https://data-api.binance.vision`；可用 `--market-host api.binance.com` 明確選擇另一個官方主機。
只有這兩個固定主機，以及公開時間／K 線 GET 端點在允許清單內，保留 HTTPS 憑證驗證，不自動切換或重試。
遇到網路問題時請檢查本機 DNS、Python 憑證及服務在所在地的可用性。

### 網路診斷

Mac 可執行 `網路診斷.command`，或使用：

```sh
python -B -m crypto_research diagnose-network
```

這會使用啟動研究的同一套 Python，顯示 Python 版本／路徑、系統預設 CA 路徑是否存在，
並測試兩個固定官方主機的公開時間與一根已收盤 BTCUSDT K 線。
它不更改 DNS、代理、憑證或目前行情來源，也不讀取帳戶、密鑰與交易紀錄。
`--json` 可產生結構化診斷輸出；一般模式可將整段結果貼回對話協助排查。

- `[dns]`：Python 無法解析該主機名稱。
- `[tls_certificate]`：Python 無法驗證 HTTPS 憑證，需核對該 Python 的 CA 憑證設定。
- `[tls]`、`[timeout]`、`[connection_refused]`、`[connection_reset]`、`[unreachable]`：分別標示其他 TLS 或連線故障。
- HTTP 418／429：立即停止，遵守數字格式的 Retry-After，沒有自動重試。
- HTTP 403／451：顯示存取限制並停止後續診斷主機測試。

如果只有 `api.binance.com` 顯示 `ok`，確認服務在所在地可用後，可明確執行：

```sh
python -B -m crypto_research start-study --market-host api.binance.com --open-report
```

來源紀錄會保存實際選擇的官方主機。單次時間／K 線探測成功仍不保證整段歷史下載一定成功。
憑證錯誤不要改成不驗證憑證；這個程式沒有關閉 TLS 驗證的選項。

**開發驗證限制**：此開發環境無法解析幣安資料主機，實際網路下載未跑通；分頁、資料品質及完整工作流程
使用離線假回應測試。沒有用人工行情冒充真實行情，也沒有實際執行使用者的本地模型。
啟動檔需在使用者的 Windows／Mac 上完成實際操作驗證。

## 指定歷史期間與分段研究

若要使用固定期間，可分開執行。所有日期都使用 **UTC**；`--end` 是不包含在內的結束日期：

```sh
python -B -m crypto_research history --symbol BTCUSDT --interval 15m --start 2026-05-01 --end 2026-11-01 --run-id btc_history01
python -B -m crypto_research prepare-study --data .local/crypto-research/datasets/btc_history01.json --run-id btc_study01
```

第一個命令保存資料及 `.source.json` 來源紀錄，包含交易所時間、要求區間、根數、請求數與資料指紋。
第二個命令建立 `.local/crypto-research/studies/btc_study01.json`，並輸出：

- `reports/btc_study01_train.html`：可離線開啟的本機研究結果頁。
- `reports/btc_study01_train.json`：只使用訓練段的 AI 報告。
- `reports/btc_study01_train.md`：人工交給 ChatGPT 的提示。
- `reports/btc_study01_train.proposal-template.json`：候選範本。

將訓練提示交給本地模型或 ChatGPT，候選必須綁定 `btc_study01_train` 這份報告的 ID 與指紋。
不要先用整份資料做 `report` 再交給 AI，也不要把後面的驗證／保留行情提供給 AI，否則會破壞這次切分的目的。

```sh
python -B -m crypto_research local-review --report .local/crypto-research/reports/btc_study01_train.json --model qwen3:8b
```

或把 ChatGPT 回覆的純 JSON 存成 `.local/crypto-research/manual-proposal.json`，再匯入：

```sh
python -B -m crypto_research import-proposal --report .local/crypto-research/reports/btc_study01_train.json --proposal .local/crypto-research/manual-proposal.json
```

以下假設候選 ID 是 `candidate01`，先比較中間 20%：

```sh
python -B -m crypto_research validate --data .local/crypto-research/datasets/btc_history01.json --study .local/crypto-research/studies/btc_study01.json --proposal .local/crypto-research/candidates/candidate01.json --run-id validation01
```

評估淨報酬、回撤、交易數、成本與各自的持幣基準後，固定一個候選，才執行最後 20%：

```sh
python -B -m crypto_research holdout --data .local/crypto-research/datasets/btc_history01.json --study .local/crypto-research/studies/btc_study01.json --proposal .local/crypto-research/candidates/candidate01.json --validation .local/crypto-research/validations/validation01.json
```

保留測試會重新計算對應的驗證結果，確認資料、研究、候選和已存紀錄一致，再先寫入
`holdouts/<holdout_key>.claim.json`。同一保留段不能再透過 CLI 重測；更換研究名稱、資料名稱、來源標籤或本金
也不會重設這個識別。若計算或匯出途中失敗，該使用紀錄仍保留，不能拿同一段重新調參。
成功的完整結果存為 `holdouts/<holdout_key>.json`。

這是本機流程的防誤用措施；持有原始檔案的人仍可自行讀取或編寫程式分析，不能保證資料從未被看過。
各段各自重設本金與 SMA 暖機，期末平倉，不把上一段持倉帶入下一段。它目前不是連續持倉的滾動驗證。
最低 `slow_window + 2` 根只是計算需求，不代表統計樣本足夠；重複選擇驗證段最佳候選仍可能過度配適。

## Windows 11／macOS 先試跑

需要 Python 3.11 或更新版本；從專案根目錄執行。macOS 可將 `python` 改為 `python3`。
研究模組本身只使用 Python 標準函式庫。整套舊帳本測試在 Windows 仍需要 README 的時區資料。

```sh
python -B run_tests.py
python -B -m crypto_research demo --run-id demo01
```

`demo` 使用 **600 根人工產生的 5 分鐘 K 線**，不是 Binance 真實行情。
它只確認程式流程能跑通，任何收益數字都不能當作策略獲利證據。
不會自動下載模型、讀取帳戶或呼叫網路。

產出位置：

- `.local/crypto-research/datasets/demo01.json`：人工資料。
- `.local/crypto-research/reports/demo01.json`：結構化研究報告。
- `.local/crypto-research/reports/demo01.md`：可交給 ChatGPT 的完整提示。
- `.local/crypto-research/reports/demo01.result.json`：交易及完整資金曲線。
- `.local/crypto-research/reports/demo01.proposal-template.json`：預設不修改的候選範本。

研究檔案只留在本機；不寫入 GitHub，也不放入 Actions 上傳物。
重複識別碼會拒絕覆寫。重新研究請使用新的 `--run-id` 和 `proposal_id`。
研究模組可自行建立 `.local/crypto-research`；若要使用原股票帳本初始化，請先依 README
執行 `local_runtime.py`，因為該初始化會拒絕既有 `.local` 資料夾。

## ChatGPT 人工合作流程

1. 打開 `demo01.md`，將「交給 ChatGPT 或本地模型」中的提示交給 ChatGPT。
2. ChatGPT 依報告審查成本、回撤及資料限制，回傳 JSON 候選或 `no_change`。
3. 只將 JSON 內容存入 `.local/crypto-research/manual-proposal.json`，不要保留 Markdown 程式區塊標記。
4. 驗證候選；只有 `action: propose` 才有可比較的參數。

```sh
python -B -m crypto_research import-proposal --report .local/crypto-research/reports/demo01.json --proposal .local/crypto-research/manual-proposal.json
```

若回覆的 `proposal_id` 是 `chatgpt01`，比較命令為：

```sh
python -B -m crypto_research compare --data .local/crypto-research/datasets/demo01.json --report .local/crypto-research/reports/demo01.json --proposal .local/crypto-research/candidates/chatgpt01.json --run-id comparison01
```

结果放在 `.local/crypto-research/comparisons/comparison01.json`。改善同一資料集上的報酬
不等於策略已通過驗證。投入比例降低所帶來的回撤下降，也不等於訊號本身進步。
候選來源人工匯入，程式無法核實是哪個雲端模型產生；來源資訊明確標為未驗證。

## 本地 AI

先在自己的電腦啟動 Ollama，確認所選模型已經安裝。以下 `qwen3:8b` 只是你先前提到的
模型名稱範例，不代表已在 M4 16 GB 或你的 Windows 電腦驗證速度與記憶體需求。

```sh
python -B -m crypto_research local-review --report .local/crypto-research/reports/demo01.json --model qwen3:8b
```

有效候選會存入 `candidates/<proposal_id>.json`，來源資訊另存；後續同樣用 `compare`。
格式錯誤、無效候選、模型未安裝、服務未開、輸出不完整或逾時都會停止該次請求。
逾時最多 60 秒；慢速模型可能需要改用人工交換方式。這裡的測試使用假回應，沒有實際跑過你的模型。
本機端點只能約束這個客戶端連線目的地；Ollama 服務本身的行為與已安裝模型來源仍需由你掌握。

## 匯入自己的歷史資料

`report --data <path>` 仍可分析單一完整本機資料集；它不會替你保留獨立測試期。
新的研究流程請先用 `history`／`start-study` 和 `prepare-study`，不要把既有 Stocks 查詢當作 crypto 資料來源。

資料 JSON 的所有欄位都必填；時間使用 **Unix epoch 秒，不是毫秒**。
`interval_seconds` 支援 60、300、900、3600；各根時間必須對齊間隔且連續，最多 100,000 根。
`source` 只能是 `synthetic` 或 `imported`。`imported` 只是標示來源，並不代表程式已核實交易所資料。
至少需要 `slow_window + 2` 根 K 線才能運算；這只是計算最低需求，不是統計證據足夠的門檻。

```json
{
  "schema_version": 1,
  "dataset_id": "history01",
  "symbol": "BTCUSDT",
  "interval_seconds": 300,
  "source": "imported",
  "bars": [
    {"open_time": 1704067200, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000}
  ]
}
```

上面的單根範例只解釋結構，不能拿來完成回測。請放入連續且已收盤的歷史 K 線。
程式只驗證結構，不知道使用者檔案是否含未完成 K 線、是否真實、是否經過挑選。

```sh
python -B -m crypto_research report --data .local/crypto-research/history01.json --run-id history01
```

預設本金 10,000，投入比例 0.10，快／慢 SMA 5／20，單邊費用 10 bps、滑價 5 bps。
1 bp 是 0.01%。**這些是實驗假設，不是你的 Binance 實際費率。**
若要改初始研究假設，用 `--parameters <local-json>`；支援下列欄位，省略欄位會補預設值：

```json
{
  "strategy_type": "sma_crossover", "fast_window": 5, "slow_window": 20,
  "position_fraction": 0.10, "initial_cash": 10000,
  "fee_bps": 10, "slippage_bps": 5
}
```

AI 候選不能修改費用或本金。模型回覆也不會被當成 Python、shell 指令或帳戶操作執行。

## 測試結果的限制與下一階段

當前回測假設分數數量、足夠流動性與固定滑價；沒有模擬最小交易單位、未成交、逐筆買賣價差、
API 延遲或市場衝擊。回撤只在 K 線收盤取樣。沒有即時止損、日損失限制或背景服務。
所有淨報酬目前只扣設定的交易費用與滑價，尚未扣電力、硬體及可能的雲端 API 費用。
SHA-256 用於關聯／重現資料，不是數位簽章，也不證明資料真實或策略獲利。

後續實作按以下順序：

1. **資料層**：歷史下載、收盤及完整性檢查已加入；下一步是在使用者電腦驗證真實下載，增加即時行情與斷線復原。
2. **獨立驗證**：固定時間切分與一次性保留測試已加入；下一步是滾動測試與試驗登錄，紀錄試過的候選數。
3. **穩健性比較**：增加成本、小幅改參數、不同市場階段與不同資料期；一起檢查報酬、回撤、樣本及基準表現。
4. **24 小時模擬**：版本可追溯、斷線後不補造交易、即時風控與看板；AI 延遲或不可用時由固定程式維持管理。
5. **策略晉級**：事先訂定風險與樣本規則，完成獨立驗證及持續模擬才准備後續實盤設計。實盤不在本版範圍。

「自我改良」在這裡指 **產生候選 → 驗證 → 比較 → 保留／拒絕**，不是自動更新模型權重或任意改寫程式。
AI 能加速研究，但候選是否有效由可重現的測試證據判定。

## 官方介面參考

- [Ollama generate API](https://docs.ollama.com/api/generate)
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [OpenAI Responses API 使用方式](https://developers.openai.com/api/docs/guides/text)：供未來選用自動 API 串接時參考。
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)：格式約束仍須配合應用程式驗證。
- [Binance Spot 市場資料](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints)。
- [Binance 公開行情專用端點](https://developers.binance.com/en/docs/products/spot/faqs/market_data_only)：不需要 API 密鑰。
- [Binance REST API 官方主機清單](https://developers.binance.com/en/docs/products/spot/rest-api)。
