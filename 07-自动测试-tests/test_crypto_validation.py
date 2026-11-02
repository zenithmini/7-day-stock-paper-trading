import copy
import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crypto_research import validation
from crypto_research.backtest import run_backtest
from crypto_research.contracts import DEFAULT_PARAMETERS, canonical_sha256, validate_dataset
from crypto_research.reports import proposal_template


def sample_dataset(count=600):
    bars = []
    previous = 100.0
    for index in range(count):
        closing = round(100 + 8 * math.sin(index / 11) + index * 0.01, 6)
        bars.append({
            "open_time": 1704067200 + index * 300, "open": previous,
            "high": max(previous, closing) + 1, "low": min(previous, closing) - 1,
            "close": closing, "volume": 1000,
        })
        previous = closing
    return {"schema_version": 1, "dataset_id": "history01", "symbol": "BTCUSDT",
            "interval_seconds": 300, "source": "synthetic", "bars": bars}


def candidate(study, **overrides):
    value = proposal_template(study["train_report"], "candidate01")
    parameters = {"fast_window": 3, "slow_window": 12, "position_fraction": 0.05}
    parameters.update(overrides)
    value.update(action="propose", rationale="A bounded hypothesis using only training results.",
                 candidate_parameters=parameters)
    return value


class ChronologicalStudyTests(unittest.TestCase):
    def setUp(self):
        self.dataset = sample_dataset()
        self.study = validation.prepare_study(self.dataset, DEFAULT_PARAMETERS, "study01")

    def test_partitions_are_ordered_disjoint_and_exhaustive(self):
        parts = self.study["partitions"]
        self.assertEqual([(parts[name]["start_index"], parts[name]["end_index_exclusive"])
                          for name in ("train", "validation", "holdout")],
                         [(0, 360), (360, 480), (480, 600)])
        self.assertEqual(sum(part["bars_count"] for part in parts.values()), 600)
        self.assertEqual(parts["train"]["last_open_time"] + 300, parts["validation"]["first_open_time"])
        self.assertEqual(parts["validation"]["last_open_time"] + 300, parts["holdout"]["first_open_time"])
        report = self.study["train_report"]
        self.assertEqual(report["dataset"]["bars_count"], 360)
        self.assertEqual(report["dataset"]["last_open_time"], self.dataset["bars"][359]["open_time"])
        self.assertNotIn("bars", report)
        self.assertNotIn("validation_metrics", self.study)
        self.assertNotIn("holdout_metrics", self.study)
        self.assertTrue(all(set(part) == {"start_index", "end_index_exclusive", "bars_count",
                                         "first_open_time", "last_open_time", "sha256"}
                            for part in parts.values()))

    def test_rounding_keeps_every_bar(self):
        study = validation.prepare_study(sample_dataset(601), DEFAULT_PARAMETERS, "odd01")
        self.assertEqual(study["partitions"]["train"]["end_index_exclusive"], 360)
        self.assertEqual(study["partitions"]["validation"]["end_index_exclusive"], 480)
        self.assertEqual(study["partitions"]["holdout"]["bars_count"], 121)

    def test_prepare_runs_only_training_backtest(self):
        with patch.object(validation, "run_backtest", wraps=run_backtest) as runner:
            validation.prepare_study(self.dataset, DEFAULT_PARAMETERS, "pure_train")
        self.assertEqual(runner.call_count, 1)
        used = runner.call_args.args[0]
        self.assertEqual(len(used["bars"]), 360)
        self.assertEqual(used["bars"][-1]["open_time"], self.dataset["bars"][359]["open_time"])

    def test_future_changes_cannot_change_training_report(self):
        changed = copy.deepcopy(self.dataset)
        for bar in changed["bars"][360:]:
            for key in ("open", "high", "low", "close"):
                bar[key] *= 0.25
            bar["volume"] *= 17
        alternate = validation.prepare_study(changed, DEFAULT_PARAMETERS, "study01")
        self.assertEqual(alternate["train_report"], self.study["train_report"])
        self.assertNotEqual(alternate["dataset_sha256"], self.study["dataset_sha256"])
        self.assertNotEqual(alternate["holdout_key"], self.study["holdout_key"])

    def test_hash_tamper_unknown_fields_and_bool_version_are_rejected(self):
        for mutate in (
            lambda s: s["partitions"]["train"].update(end_index_exclusive=361),
            lambda s: s.update(api_key="fake-secret"),
            lambda s: s.update(schema_version=True),
            lambda s: s["parameters"].update(fee_bps=0),
        ):
            changed = copy.deepcopy(self.study)
            mutate(changed)
            changed["study_sha256"] = canonical_sha256({key: value for key, value in changed.items()
                                                        if key != "study_sha256"})
            with self.assertRaises(ValueError):
                validation.validate_study(changed, self.dataset)

    def test_dataset_replacement_is_rejected_even_if_train_is_unchanged(self):
        changed = copy.deepcopy(self.dataset)
        changed["bars"][-1]["volume"] += 1
        with self.assertRaises(ValueError):
            validation.validate_study(self.study, changed)
        with self.assertRaises(ValueError):
            validation.validation_comparison(changed, self.study, candidate(self.study))

    def test_candidate_bound_to_training_report_and_costs(self):
        proposal = candidate(self.study)
        proposal["report_id"] = "different_train"
        with self.assertRaises(ValueError):
            validation.validation_comparison(self.dataset, self.study, proposal)
        proposal = candidate(self.study)
        proposal["candidate_parameters"]["fee_bps"] = 0
        with self.assertRaises(ValueError):
            validation.validation_comparison(self.dataset, self.study, proposal)
        proposal = candidate(self.study, position_fraction=0.11)
        with self.assertRaises(ValueError):
            validation.holdout_comparison(self.dataset, self.study, proposal)

    def test_candidate_must_fit_both_unseen_periods_before_evaluation(self):
        proposal = candidate(self.study, slow_window=200)
        # 200 fits the training report but not either 120-bar later period.
        with patch.object(validation, "run_backtest", wraps=run_backtest) as runner:
            with self.assertRaises(ValueError):
                validation.validation_comparison(self.dataset, self.study, proposal)
        self.assertTrue(all(call.args[0]["dataset_id"].endswith("_train")
                            for call in runner.call_args_list))

    def test_validation_never_evaluates_holdout_and_preserves_costs(self):
        with patch.object(validation, "run_backtest", wraps=run_backtest) as runner:
            comparison = validation.validation_comparison(self.dataset, self.study, candidate(self.study))
        self.assertEqual([call.args[0]["dataset_id"] for call in runner.call_args_list],
                         ["study01_train", "study01_validation", "study01_validation"])
        self.assertEqual(comparison["stage"], "validation")
        self.assertFalse(comparison["promotion_allowed"])
        self.assertEqual(comparison["partition"], self.study["partitions"]["validation"])
        self.assertEqual(comparison["source"], "synthetic")
        for field in ("fee_bps", "slippage_bps", "initial_cash", "strategy_type"):
            self.assertEqual(comparison["candidate_parameters"][field], DEFAULT_PARAMETERS[field])
        self.assertNotEqual(comparison["baseline_benchmarks"], comparison["candidate_benchmarks"])
        self.assertNotIn("holdout_metrics", comparison)
        json.dumps(comparison, allow_nan=False)

    def test_holdout_is_isolated_and_restarts_cash_and_warmup(self):
        proposal = candidate(self.study)
        with patch.object(validation, "run_backtest", wraps=run_backtest) as runner:
            comparison = validation.holdout_comparison(self.dataset, self.study, proposal)
        self.assertEqual([call.args[0]["dataset_id"] for call in runner.call_args_list],
                         ["study01_train", "study01_holdout", "study01_holdout"])
        normalized = validate_dataset(self.dataset)
        period = {**normalized, "dataset_id": "study01_holdout", "bars": normalized["bars"][480:]}
        baseline = run_backtest(period, DEFAULT_PARAMETERS)
        self.assertEqual(comparison["baseline_metrics"], baseline["metrics"])
        self.assertEqual(comparison["partition"]["first_open_time"], normalized["bars"][480]["open_time"])
        self.assertFalse(comparison["promotion_allowed"])
        self.assertEqual(baseline["equity_curve"][0]["cash"], DEFAULT_PARAMETERS["initial_cash"])
        self.assertEqual(baseline["equity_curve"][0]["quantity"], 0)
        self.assertEqual(baseline["equity_curve"][-1]["quantity"], 0)

    def test_holdout_identity_survives_renaming_source_and_parameter_changes(self):
        changed = copy.deepcopy(self.dataset)
        changed["dataset_id"] = "renamed_data"
        changed["source"] = "imported"
        alternate = validation.prepare_study(changed, {**DEFAULT_PARAMETERS, "fee_bps": 20}, "renamed_study")
        self.assertEqual(alternate["holdout_key"], self.study["holdout_key"])
        self.assertNotEqual(alternate["study_sha256"], self.study["study_sha256"])

    def test_short_period_long_study_id_and_no_change_are_rejected(self):
        with self.assertRaises(ValueError):
            validation.prepare_study(sample_dataset(100), DEFAULT_PARAMETERS, "short")
        with self.assertRaises(ValueError):
            validation.prepare_study(self.dataset, DEFAULT_PARAMETERS, "x" * 101)
        with self.assertRaises(ValueError):
            validation.validation_comparison(self.dataset, self.study, proposal_template(self.study["train_report"]))

    def test_reproduction_is_deterministic_and_inputs_are_unchanged(self):
        before_data, before_study = copy.deepcopy(self.dataset), copy.deepcopy(self.study)
        self.assertEqual(validation.validate_study(self.study, self.dataset), self.study)
        left = validation.validation_comparison(self.dataset, self.study, candidate(self.study))
        right = validation.validation_comparison(self.dataset, self.study, candidate(self.study))
        self.assertEqual(left, right)
        self.assertEqual(self.dataset, before_data)
        self.assertEqual(self.study, before_study)


if __name__ == "__main__":
    unittest.main()
