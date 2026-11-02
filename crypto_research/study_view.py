"""Self-contained local training report; no future-period performance is rendered."""
from datetime import datetime, timezone
from html import escape

from .contracts import validate_identifier
from .reports import build_ai_prompt, validate_report


def _text(value):
    return escape(str(value), quote=True)


def _time(value):
    if type(value) is not int or value < 0:
        raise ValueError("Study times must be nonnegative epoch seconds")
    try:
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):
        return "時間超出可顯示範圍（epoch 秒：" + str(value) + "）"


def render_study_html(study):
    """Render only training metrics and partition boundaries, with escaped text."""
    if not isinstance(study, dict):
        raise ValueError("Study must be an object")
    identifier = validate_identifier(study["study_id"])
    report = validate_report(study["train_report"])
    if report["report_id"] != identifier + "_train":
        raise ValueError("Training report does not belong to this study")
    partitions = study["partitions"]
    if not isinstance(partitions, dict) or set(partitions) != {"train", "validation", "holdout"}:
        raise ValueError("A study must contain three chronological partitions")
    data, metrics, parameters = report["dataset"], report["metrics"], report["parameters"]
    rows = []
    previous_end = 0
    for period, label, usage in (
        ("train", "訓練段 · 60%", "提供本地 AI 與 ChatGPT 分析"),
        ("validation", "驗證段 · 20%", "比較候選策略；此頁不顯示績效"),
        ("holdout", "保留段 · 20%", "選定候選後作最終檢查；此頁不顯示績效"),
    ):
        metadata = partitions[period]
        count, start, end = metadata["bars_count"], metadata["start_index"], metadata["end_index_exclusive"]
        if any(type(value) is not int for value in (count, start, end)) or count <= 0 or start != previous_end or end - start != count:
            raise ValueError("Partition counts and boundaries must be consecutive")
        previous_end = end
        first, last = _time(metadata["first_open_time"]), _time(metadata["last_open_time"])
        rows.append("<tr><th>" + _text(label) + "</th><td>" + _text(f"{count:,}") +
                    " 根</td><td>" + _text(first) + "<br><span class='muted'>至 " + _text(last) +
                    " UTC</span></td><td>" + _text(usage) + "</td></tr>")
    if partitions["train"]["bars_count"] != data["bars_count"]:
        raise ValueError("Training report and partition counts differ")
    source = "人工行情 · 僅供流程測試" if data["source"] == "synthetic" else "匯入資料 · 來源真實性需另行核對"
    interval = f"{data['interval_seconds'] // 60} 分鐘 K 線"
    return """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; base-uri 'none'; form-action 'none'">
<title>歷史量化研究報告</title><style>
:root{color-scheme:light;--ink:#172b3a;--muted:#5d707d;--line:#dbe5e8;--accent:#136e6b;--paper:#fff;--bg:#f0f5f6}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,-apple-system,"Microsoft JhengHei",sans-serif}main{max-width:1120px;margin:auto;padding:32px 24px 60px}header{display:flex;justify-content:space-between;gap:24px;align-items:center;margin-bottom:26px}h1{font-size:29px;letter-spacing:.02em;margin:0}h2{font-size:20px;margin:0 0 12px}p{margin:8px 0}.eyebrow{font-size:13px;color:var(--accent);font-weight:700;letter-spacing:.08em}.muted{color:var(--muted);font-size:14px}.badge{white-space:nowrap;border:1px solid #b4d7d2;border-radius:20px;padding:6px 14px;color:#145b58;background:#e5f4ef;font-size:13px}.panel{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:24px;margin-top:20px}.context{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 0}.context span{background:#f1f5f6;border-radius:6px;padding:4px 9px;font-size:13px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:18px}.metric{padding:20px;background:var(--paper);border:1px solid var(--line);border-radius:12px}.metric strong{font-size:30px;line-height:1.3;display:block;margin:6px 0}.metric label{font-size:14px;color:var(--muted)}.metric small{font-size:12px;color:var(--muted)}.table-scroll{overflow:auto}table{width:100%;border-collapse:collapse;text-align:left;min-width:700px}th,td{padding:14px 10px;border-bottom:1px solid var(--line);font-size:14px;vertical-align:top}thead th{color:var(--muted);font-size:12px;font-weight:600}tbody th{min-width:150px}tbody tr:last-child td,tbody tr:last-child th{border-bottom:0}.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.step{background:#f5f8f8;border-radius:9px;padding:16px}.step b{display:block;margin-bottom:5px}.step p{font-size:14px;color:var(--muted)}.toolbar{display:flex;justify-content:space-between;gap:12px;align-items:center;margin:18px 0 10px}button{border:0;border-radius:8px;padding:10px 18px;background:var(--accent);color:white;cursor:pointer;font:inherit;font-size:14px}button:focus-visible,textarea:focus-visible{outline:3px solid #79bcb5;outline-offset:3px}textarea{display:block;width:100%;min-height:260px;resize:vertical;border:1px solid #c7d5d9;border-radius:8px;padding:15px;color:#25404f;background:#f8fafb;font:13px/1.6 ui-monospace,Consolas,monospace}.note{border-left:3px solid #c2974b;padding-left:14px;font-size:14px;color:#68532e;margin-top:18px}.foot{margin-top:20px;font-size:13px;color:var(--muted)}code{font:13px ui-monospace,Consolas,monospace;background:#edf2f4;padding:2px 5px;border-radius:4px}@media(max-width:720px){main{padding:22px 16px 40px}header{align-items:flex-start;gap:12px}h1{font-size:23px}.metrics,.steps{grid-template-columns:1fr}.metric{padding:16px}.metric strong{font-size:27px}.panel{padding:18px}.toolbar{align-items:flex-start;flex-direction:column}}
</style></head><body><main>
<header><div><div class="eyebrow">CRYPTO RESEARCH</div><h1>歷史量化研究報告</h1><p class="muted">先分析訓練資料，再用後續時段檢查候選策略。</p></div><span class="badge">歷史研究</span></header>
<section class="panel"><h2>""" + _text(data["symbol"]) + " · " + _text(interval) + """</h2>
<p>此頁只顯示訓練段結果。驗證段與保留段的績效會在後續步驟分別計算。</p>
<div class="context"><span>研究編號：""" + _text(identifier) + "</span><span>" + _text(source) + "</span><span>訓練資料 " + _text(f"{data['bars_count']:,}") + """ 根</span></div></section>
<div class="metrics"><div class="metric"><label>訓練段淨報酬</label><strong>""" + _text(f"{metrics['net_return_pct']:+.3f}%") + """</strong><small>已扣設定的交易費用與滑價</small></div>
<div class="metric"><label>訓練段最大回撤</label><strong>""" + _text(f"{metrics['max_drawdown_pct']:.3f}%") + """</strong><small>以每根 K 線收盤估算</small></div>
<div class="metric"><label>訓練段完成交易</label><strong>""" + _text(f"{metrics['trade_count']:,}") + """</strong><small>交易數量不等於證據已足夠</small></div></div>
<section class="panel"><h2>按時間分開研究與檢查</h2><div class="table-scroll"><table><thead><tr><th>資料區段</th><th>數量</th><th>首根至末根開盤時間</th><th>用途</th></tr></thead><tbody>""" + "".join(rows) + """</tbody></table></div>
<p class="muted">各段獨立重設本金、重新暖機 SMA，且在各段結束時模擬平倉。</p></section>
<section class="panel"><h2>讓本地 AI 與 ChatGPT 一起研究</h2><div class="steps">
<div class="step"><b>1 · 分析同一份報告</b><p>本地 Ollama 模型或 ChatGPT 只看下方訓練報告，提出 SMA 參數候選；資料不足可以回覆不修改。</p></div>
<div class="step"><b>2 · 保存 JSON 建議</b><p>將模型回覆的 JSON 單獨存成候選檔案，依操作說明執行 <code>import-proposal</code> 與 <code>validate</code>。</p></div>
<div class="step"><b>3 · 固定候選再檢查</b><p>選定候選後執行 <code>holdout</code>。保留段會記錄使用狀態，避免看過結果後反覆調參。</p></div></div>
<div class="toolbar"><div><b>給 AI 的完整訓練提示</b><div class="muted">複製後貼到 ChatGPT；本地模型也使用相同內容。</div></div><button type="button" id="copy-prompt">複製提示</button></div>
<textarea id="ai-prompt" readonly aria-label="給 AI 的完整訓練提示">""" + _text(build_ai_prompt(report)) + """</textarea><p id="copy-status" class="muted" role="status" aria-live="polite"></p>
<p class="muted">完整命令與候選格式請看專案中的 <code>CRYPTO_RESEARCH.md</code>。此頁可離線開啟，不會自動上傳報告。</p></section>
<section class="panel"><h2>這次使用的研究假設</h2><p>SMA 快／慢週期：""" + _text(parameters["fast_window"]) + "／" + _text(parameters["slow_window"]) + "；單次投入比例：" + _text(f"{parameters['position_fraction'] * 100:.2f}%") + "。</p><p>單邊費用：" + _text(f"{parameters['fee_bps']:g}") + " bps；單邊不利滑價：" + _text(f"{parameters['slippage_bps']:g}") + """ bps。這些是實驗假設，尚未扣電力、硬體與可能的雲端 API 費用。</p>
<p class="note">研究結果尚不能證明實際獲利。AI 候選需經過獨立檢查；這個流程不會自動啟用策略或提交真實訂單。</p></section>
<p class="foot">本機歷史研究報告 · 此頁不提供即時行情或 24 小時監控 · 無外部素材、追蹤或網路請求 · 日期使用 UTC</p>
</main><script>
document.getElementById('copy-prompt').addEventListener('click', async function () {
  const text = document.getElementById('ai-prompt');
  const status = document.getElementById('copy-status');
  try {
    if (!navigator.clipboard || !window.isSecureContext) throw new Error('select');
    await navigator.clipboard.writeText(text.value);
    status.textContent = '已複製，可貼到 ChatGPT。';
  } catch (_) {
    text.focus(); text.select();
    status.textContent = '提示已全選，請按 Ctrl+C；Mac 請按 Command+C。';
  }
});
</script></body></html>
"""
