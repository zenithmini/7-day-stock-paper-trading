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

這個版本只有單一資料集研究，**没有樣本外策略驗證、24 小時行情服務、看板或下單功能**。
候選結果一律 `promotion_allowed: false`，不會自動啟用或改動原股票帳本。

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

`report --data <path>` 只讀本機檔案。目前沒有交易所自動下載器。
公開行情與 API 整合會在下一階段加入；不要把既有 Stocks 查詢當作 crypto 資料來源。

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

1. **資料層**：加密貨幣公開歷史／即時行情、事件时间與收盤確認、限流、斷線復原及缺漏品質報告。
2. **獨立驗證**：按時間分開訓練、驗證及封存測試期；滾動測試；紀錄試過的候選數，防止反覆選到偶然好成績。
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
- [Binance Spot 市場資料](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints)：目前僅規劃，尚未實作下載。
