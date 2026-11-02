import copy
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crypto_research import __main__ as cli
from crypto_research.contracts import DEFAULT_PARAMETERS, canonical_sha256
from crypto_research.market_data import MarketDataError
from crypto_research.reports import proposal_template
from crypto_research.validation import (
    holdout_comparison, prepare_study, validation_comparison,
)


class ChronologicalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.dataset = cli.synthetic_dataset()
        self.study = prepare_study(self.dataset, DEFAULT_PARAMETERS, "study01")
        self.proposal = proposal_template(self.study["train_report"], "candidate01")
        self.proposal.update(action="propose", rationale="Test shorter SMA on later data.",
                             candidate_parameters={"fast_window": 3, "slow_window": 20, "position_fraction": 0.1})
        self.validation = validation_comparison(self.dataset, self.study, self.proposal)

    def test_first_holdout_claim_precedes_evaluation_and_prevents_repeat(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "LOCAL_ROOT", Path(temp).resolve() / "research"):
            key = self.study["holdout_key"]

            def evaluate(dataset, study, proposal):
                self.assertTrue((cli.LOCAL_ROOT / "holdouts" / (key + ".claim.json")).exists())
                return holdout_comparison(dataset, study, proposal)

            with patch("crypto_research.validation.holdout_comparison", side_effect=evaluate) as computation:
                output, paths = cli.holdout_once(self.dataset, self.study, self.proposal, self.validation)
                self.assertEqual(output["evaluation"]["stage"], "holdout")
                self.assertFalse(output["promotion_allowed"])
                self.assertTrue(paths[".json"].exists())
                with self.assertRaises(cli.ResearchWorkflowError):
                    cli.holdout_once(self.dataset, self.study, self.proposal, self.validation)
                self.assertEqual(computation.call_count, 1)

    def test_renaming_study_and_dataset_cannot_reset_holdout(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "LOCAL_ROOT", Path(temp).resolve() / "research"):
            cli.holdout_once(self.dataset, self.study, self.proposal, self.validation)
            dataset = copy.deepcopy(self.dataset)
            dataset["dataset_id"] = "renamed_data"
            study = prepare_study(dataset, DEFAULT_PARAMETERS, "renamed_study")
            proposal = proposal_template(study["train_report"], "another_candidate")
            proposal.update(action="propose", rationale="Another name does not reserve new data.",
                            candidate_parameters={"fast_window": 4, "slow_window": 20, "position_fraction": 0.1})
            validation = validation_comparison(dataset, study, proposal)
            self.assertEqual(study["holdout_key"], self.study["holdout_key"])
            with self.assertRaises(cli.ResearchWorkflowError):
                cli.holdout_once(dataset, study, proposal, validation)

    def test_invalid_validation_never_claims_or_evaluates_holdout(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "LOCAL_ROOT", Path(temp).resolve() / "research"), \
                patch("crypto_research.validation.holdout_comparison") as computation:
            for key, value in (("promotion_allowed", 0), ("delta_net_return_pct", 999), ("proposal_id", "wrong")):
                changed = copy.deepcopy(self.validation)
                changed[key] = value
                with self.subTest(key=key), self.assertRaises(cli.ResearchWorkflowError):
                    cli.holdout_once(self.dataset, self.study, self.proposal, changed)
            computation.assert_not_called()
            self.assertFalse((cli.LOCAL_ROOT / "holdouts").exists())

    def test_evaluation_failure_keeps_consumed_claim(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "LOCAL_ROOT", Path(temp).resolve() / "research"), \
                patch("crypto_research.validation.holdout_comparison", side_effect=ValueError("fake failure")) as computation:
            with self.assertRaisesRegex(cli.ResearchWorkflowError, "claim remains consumed"):
                cli.holdout_once(self.dataset, self.study, self.proposal, self.validation)
            key = self.study["holdout_key"]
            self.assertTrue((cli.LOCAL_ROOT / "holdouts" / (key + ".claim.json")).exists())
            self.assertFalse((cli.LOCAL_ROOT / "holdouts" / (key + ".json")).exists())
            with self.assertRaises(cli.ResearchWorkflowError):
                cli.holdout_once(self.dataset, self.study, self.proposal, self.validation)
            self.assertEqual(computation.call_count, 1)

    def test_related_packet_export_rolls_back_partial_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / "research"
            real_open, count = os.open, 0

            def fail_second_file(*args, **kwargs):
                nonlocal count
                count += 1
                if count == 2:
                    raise PermissionError("fake export failure")
                return real_open(*args, **kwargs)

            with patch.object(cli.os, "open", side_effect=fail_second_file), self.assertRaises(PermissionError):
                cli.write_packet_groups([
                    ("studies", "study01", {".json": "{}"}),
                    ("reports", "study01_train", {".json": "{}", ".md": "prompt"}),
                ], root)
            self.assertEqual(list(root.rglob("*.json")), [])
            self.assertEqual(list(root.rglob("*.md")), [])

    def test_utc_dates_are_unambiguous(self):
        self.assertEqual(cli.parse_utc_date("2026-11-01"), 1793491200)
        for value in ("2026-1-01", "2026-02-30", "2026-11-01T08:00+08:00", "../outside", True):
            with self.subTest(value=value), self.assertRaises(cli.ResearchWorkflowError):
                cli.parse_utc_date(value)
        start, end = cli.recent_utc_window(180, datetime(2026, 11, 2, 11, 30, tzinfo=timezone.utc))
        self.assertEqual(end, cli.parse_utc_date("2026-11-02"))
        self.assertEqual(end - start, 180 * 86400)
        for invalid in (True, 0, 366, 180.0):
            with self.subTest(days=invalid), self.assertRaises(cli.ResearchWorkflowError):
                cli.recent_utc_window(invalid)

    def test_start_study_exports_packets_and_opens_only_local_report(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "LOCAL_ROOT", Path(temp).resolve() / "research"), \
                patch("crypto_research.market_data.download_history") as download, \
                patch("webbrowser.open", return_value=True) as browser, redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()):
            dataset = copy.deepcopy(self.dataset)
            dataset["dataset_id"], dataset["source"] = "start01", "imported"
            provenance = {"source": "binance_public_rest", "dataset_sha256": canonical_sha256(dataset)}
            download.return_value = dataset, provenance
            self.assertEqual(cli.main(["start-study", "--symbol", "BTCUSDT", "--interval", "5m",
                                       "--days", "180", "--run-id", "start01", "--open-report"]), 0)
            self.assertEqual(len(list((cli.LOCAL_ROOT / "datasets").glob("*.json"))), 2)
            viewer = cli.LOCAL_ROOT / "reports/start01_train.html"
            self.assertTrue(viewer.exists())
            self.assertTrue((cli.LOCAL_ROOT / "studies/start01.json").exists())
            browser.assert_called_once_with(viewer.as_uri())
            self.assertFalse((cli.LOCAL_ROOT / "validations").exists())
            self.assertFalse((cli.LOCAL_ROOT / "holdouts").exists())
            self.assertEqual(cli.main(["start-study", "--run-id", "start01"]), 2)
            self.assertEqual(download.call_count, 1)

    def test_history_prepare_validate_holdout_cli_without_real_network(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "LOCAL_ROOT", Path(temp).resolve() / "research"), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            dataset = copy.deepcopy(self.dataset)
            dataset["dataset_id"], dataset["source"] = "history01", "imported"
            provenance = {"source": "binance_public_rest", "dataset_sha256": canonical_sha256(dataset)}
            args = ["history", "--symbol", "BTCUSDT", "--interval", "15m", "--start", "2026-05-01",
                    "--end", "2026-11-01", "--run-id", "history01"]
            with patch("crypto_research.market_data.download_history", return_value=(dataset, provenance)) as download:
                self.assertEqual(cli.main(args), 0)
                self.assertEqual(cli.main(args), 2)
                self.assertEqual(download.call_count, 1)
            data = cli.LOCAL_ROOT / "datasets/history01.json"
            self.assertEqual(cli.load_json(data)["source"], "imported")
            self.assertTrue((cli.LOCAL_ROOT / "datasets/history01.source.json").exists())
            self.assertEqual(cli.main(["prepare-study", "--data", str(data), "--run-id", "study01"]), 0)
            study = cli.LOCAL_ROOT / "studies/study01.json"
            train = cli.LOCAL_ROOT / "reports/study01_train.json"
            proposal = proposal_template(cli.load_json(train), "candidate01")
            proposal.update(action="propose", rationale="A research hypothesis only.",
                            candidate_parameters={"fast_window": 3, "slow_window": 20, "position_fraction": 0.1})
            candidate = Path(temp) / "proposal.json"
            candidate.write_text(json.dumps(proposal), encoding="utf-8")
            self.assertEqual(cli.main(["validate", "--data", str(data), "--study", str(study),
                                       "--proposal", str(candidate), "--run-id", "validation01"]), 0)
            validation = cli.LOCAL_ROOT / "validations/validation01.json"
            command = ["holdout", "--data", str(data), "--study", str(study), "--proposal", str(candidate),
                       "--validation", str(validation)]
            self.assertEqual(cli.main(command), 0)
            self.assertEqual(cli.main(command), 2)
            self.assertEqual(len(list((cli.LOCAL_ROOT / "holdouts").glob("*.claim.json"))), 1)
            self.assertFalse((cli.LOCAL_ROOT / "paper-ledger.sqlite3").exists())

    def test_failed_download_does_not_export_partial_data(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "LOCAL_ROOT", Path(temp).resolve() / "research"), \
                patch("crypto_research.market_data.download_history", side_effect=MarketDataError("HTTP 429; stopped without retry.")), \
                redirect_stderr(io.StringIO()) as errors:
            code = cli.main(["history", "--symbol", "BTCUSDT", "--start", "2026-05-01", "--end", "2026-11-01",
                             "--run-id", "history01"])
            self.assertEqual(code, 2)
            self.assertIn("429", errors.getvalue())
            self.assertEqual(list(cli.LOCAL_ROOT.rglob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
