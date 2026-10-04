import copy
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crypto_research import DEFAULT_PARAMETERS, run_backtest
from crypto_research import __main__ as cli
from crypto_research.reports import (
    build_ai_prompt, build_report, candidate_parameters, proposal_template,
    report_fingerprint, validate_proposal, validate_report,
)


class CryptoReportTests(unittest.TestCase):
    def setUp(self):
        self.data = cli.synthetic_dataset()
        self.result = run_backtest(self.data, DEFAULT_PARAMETERS)
        self.report = build_report(self.result, "research01")

    def candidate(self):
        proposal = proposal_template(self.report)
        proposal.update(action="propose", rationale="Compare a shorter fast SMA on the same data.",
                        candidate_parameters={"fast_window": 3, "slow_window": 20, "position_fraction": 0.05})
        return proposal

    def test_ai_cannot_change_costs_initial_cash_risk_or_execute_code(self):
        for key, value in (("fee_bps", 0), ("slippage_bps", 0), ("initial_cash", 1e9),
                           ("live_trading_enabled", True), ("python", "print('do not execute')")):
            proposal = self.candidate()
            proposal["candidate_parameters"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_proposal(proposal, self.report)
        proposal = self.candidate()
        proposal["candidate_parameters"]["position_fraction"] = 0.10001
        with self.assertRaises(ValueError):
            validate_proposal(proposal, self.report)
        proposal = self.candidate()
        proposal["candidate_parameters"]["fast_window"] = True
        with self.assertRaises(ValueError):
            validate_proposal(proposal, self.report)

    def test_report_link_prevents_candidate_from_another_context(self):
        for key, value in (("report_id", "another01"), ("report_sha256", "0" * 64),
                           ("proposal_id", "../outside")):
            proposal = self.candidate()
            proposal[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_proposal(proposal, self.report)

    def test_no_change_is_supported_and_not_a_candidate(self):
        proposal = proposal_template(self.report)
        self.assertEqual(validate_proposal(proposal, self.report), proposal)
        with self.assertRaises(ValueError):
            candidate_parameters(proposal, self.report)
        proposal["candidate_parameters"] = self.candidate()["candidate_parameters"]
        with self.assertRaises(ValueError):
            validate_proposal(proposal, self.report)

    def test_reports_are_bounded_and_honest_about_validation(self):
        self.assertLessEqual(len(self.report["recent_trades"]), 20)
        self.assertEqual(set(self.report["validation"].values()), {False})
        self.assertNotIn("equity_curve", self.report)
        self.assertIn("synthetic", build_ai_prompt(self.report))
        for bad in (True, 0, "false"):
            report = copy.deepcopy(self.report)
            report["validation"]["out_of_sample_tested"] = bad
            report["report_sha256"] = report_fingerprint(report)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_report(report)

    def test_tampered_report_and_unknown_fields_are_rejected(self):
        report = copy.deepcopy(self.report)
        report["metrics"]["net_return_pct"] += 5
        with self.assertRaises(ValueError):
            validate_report(report)
        report["report_sha256"] = report_fingerprint(report)
        with self.assertRaises(ValueError):
            cli.compare_candidate(self.data, report, self.candidate())
        report = copy.deepcopy(self.report)
        report["api_key"] = "fake-secret-not-for-AI"
        with self.assertRaises(ValueError):
            validate_report(report)
        report = copy.deepcopy(self.report)
        del report["parameters"]["fee_bps"]
        report["report_sha256"] = report_fingerprint(report)
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_comparison_reproduces_baseline_and_preserves_costs(self):
        comparison = cli.compare_candidate(self.data, self.report, self.candidate())
        self.assertEqual(comparison["baseline_metrics"], self.result["metrics"])
        self.assertEqual(comparison["baseline_benchmarks"], self.result["benchmarks"])
        self.assertNotEqual(comparison["candidate_benchmarks"], comparison["baseline_benchmarks"])
        self.assertFalse(comparison["promotion_allowed"])
        for key in ("fee_bps", "slippage_bps", "initial_cash", "strategy_type"):
            self.assertEqual(comparison["candidate_parameters"][key], self.report["parameters"][key])
        changed_data = copy.deepcopy(self.data)
        changed_data["bars"][0]["volume"] += 1
        with self.assertRaises(ValueError):
            cli.compare_candidate(changed_data, self.report, self.candidate())

    def test_input_json_rejects_duplicate_keys_nan_and_infinity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.json"
            for value in ('{"action":"propose","action":"no_change"}', '{"x":NaN}', '{"x":1e999}'):
                path.write_text(value, encoding="utf-8")
                with self.subTest(value=value), self.assertRaises(ValueError):
                    cli.load_json(path)

    def test_exports_refuse_overwrite_and_bad_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / "research"
            paths = cli.write_bundle("reports", "report01", {".json": "safe"}, root)
            with self.assertRaises(ValueError):
                cli.write_bundle("reports", "report01", {".json": "changed"}, root)
            self.assertEqual(paths[".json"].read_text(encoding="utf-8"), "safe")
            if os.name != "nt":
                self.assertEqual(paths[".json"].stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                cli.write_bundle("reports", "../outside", {".json": "bad"}, root)
            with self.assertRaises(ValueError):
                cli.write_bundle("reports", "x", {"/outside": "bad"}, root)

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require Windows developer mode")
    def test_export_rejects_symlinked_ancestor(self):
        with tempfile.TemporaryDirectory() as temp:
            # Resolve macOS's system /var alias before creating our intentional symlink.
            directory = Path(temp).resolve()
            (directory / "outside").mkdir()
            (directory / "alias").symlink_to(directory / "outside", target_is_directory=True)
            with self.assertRaises(ValueError):
                cli.write_bundle("reports", "x", {".json": "bad"}, directory / "alias" / "research")
            self.assertEqual(list((directory / "outside").iterdir()), [])

    def test_cli_demo_manual_import_and_comparison_are_offline(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "LOCAL_ROOT", Path(temp).resolve() / "research"), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["demo", "--run-id", "demo01"]), 0)
            report = cli.LOCAL_ROOT / "reports/demo01.json"
            data = cli.LOCAL_ROOT / "datasets/demo01.json"
            proposal = proposal_template(cli.load_json(report), "chatgpt01")
            proposal.update(action="propose", rationale="A test hypothesis only.",
                            candidate_parameters={"fast_window": 3, "slow_window": 20, "position_fraction": 0.1})
            source = Path(temp) / "manual-proposal.json"
            source.write_text(json.dumps(proposal), encoding="utf-8")
            self.assertEqual(cli.main(["import-proposal", "--report", str(report), "--proposal", str(source)]), 0)
            saved = cli.LOCAL_ROOT / "candidates/chatgpt01.json"
            self.assertEqual(cli.main(["compare", "--data", str(data), "--report", str(report),
                                       "--proposal", str(saved), "--run-id", "comparison01"]), 0)
            compared = cli.load_json(cli.LOCAL_ROOT / "comparisons/comparison01.json")
            self.assertFalse(compared["promotion_allowed"])
            self.assertEqual(cli.main(["demo", "--run-id", "demo01"]), 2)
            self.assertFalse((cli.LOCAL_ROOT / "paper-ledger.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
