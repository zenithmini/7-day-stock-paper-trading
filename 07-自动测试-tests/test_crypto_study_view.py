import copy
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crypto_research.__main__ import synthetic_dataset
from crypto_research.contracts import DEFAULT_PARAMETERS
from crypto_research.reports import report_fingerprint
from crypto_research.study_view import render_study_html
from crypto_research.validation import prepare_study


class ParsedPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external_loads = []
        self.script_starts = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.script_starts += 1
        for name, value in attrs:
            if name in ("src", "href", "action") and value:
                self.external_loads.append((tag, name, value))


class StudyViewerTests(unittest.TestCase):
    def setUp(self):
        self.study = prepare_study(synthetic_dataset(), DEFAULT_PARAMETERS, "viewer01")

    def test_training_metrics_source_and_partition_counts_are_visible(self):
        html = render_study_html(self.study)
        metrics = self.study["train_report"]["metrics"]
        self.assertIn("人工行情 · 僅供流程測試", html)
        self.assertIn(f"{metrics['net_return_pct']:+.3f}%", html)
        self.assertIn("360 根", html)
        self.assertIn("120 根", html)
        self.assertIn("UTC", html)
        self.assertIn("本地 Ollama", html)
        self.assertIn("尚未扣電力", html)

    def test_imported_label_does_not_claim_authentication(self):
        dataset = synthetic_dataset()
        dataset["source"] = "imported"
        html = render_study_html(prepare_study(dataset, DEFAULT_PARAMETERS, "imported01"))
        self.assertIn("匯入資料 · 來源真實性需另行核對", html)
        self.assertNotIn("已驗證 Binance", html)

    def test_out_of_datetime_range_metadata_does_not_break_export(self):
        dataset = synthetic_dataset()
        for bar in dataset["bars"]:
            bar["open_time"] += 300 * 10**13
        study = prepare_study(dataset, DEFAULT_PARAMETERS, "large_epoch")
        html = render_study_html(study)
        self.assertIn("時間超出可顯示範圍", html)
        self.assertIn("歷史量化研究報告", html)

    def test_no_external_loads_or_network_requests(self):
        html = render_study_html(self.study)
        parsed = ParsedPage()
        parsed.feed(html)
        self.assertEqual(parsed.external_loads, [])
        self.assertEqual(parsed.script_starts, 1)
        self.assertIn("connect-src 'none'", html)
        self.assertNotRegex(html, r"\b(?:fetch|XMLHttpRequest|WebSocket)\s*\(")
        self.assertNotRegex(html, r"url\s*\(")

    def test_prompt_and_embedded_text_are_escaped(self):
        study = copy.deepcopy(self.study)
        attack = '</textarea><script id="injected">alert(1)</script>'
        study["train_report"]["limitations"][0] = attack
        study["train_report"]["report_sha256"] = report_fingerprint(study["train_report"])
        html = render_study_html(study)
        self.assertNotIn(attack, html)
        self.assertIn("&lt;/textarea&gt;&lt;script", html)
        parsed = ParsedPage()
        parsed.feed(html)
        self.assertEqual(parsed.script_starts, 1)

    def test_future_metrics_are_never_serialized(self):
        study = copy.deepcopy(self.study)
        # The viewer deliberately selects only training data and boundary fields.
        study["partitions"]["holdout"]["candidate_metrics"] = {"net_return_pct": 987654321.234}
        study["partitions"]["validation"]["baseline_metrics"] = {"net_return_pct": 123456789.987}
        html = render_study_html(study)
        self.assertNotIn("987654321", html)
        self.assertNotIn("123456789", html)
        self.assertNotIn("candidate_metrics", html)
        self.assertNotIn("baseline_metrics", html)

    def test_invalid_report_and_broken_boundaries_are_rejected(self):
        study = copy.deepcopy(self.study)
        study["train_report"]["metrics"]["net_return_pct"] += 1
        with self.assertRaises(ValueError):
            render_study_html(study)
        study = copy.deepcopy(self.study)
        study["partitions"]["holdout"]["start_index"] += 1
        with self.assertRaises(ValueError):
            render_study_html(study)


if __name__ == "__main__":
    unittest.main()
