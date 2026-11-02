"""Chronological research partitions and bounded, nonactivating comparisons.

Only the earliest 60% produces a report for AI. The following 20% is available
for candidate validation; the final 20% is evaluated separately by the caller.
Each period restarts cash and its SMA warmup and liquidates at its own end.
This module is pure computation: the CLI must claim/persist one-use holdouts.
"""

import copy
import re

from .backtest import run_backtest
from .contracts import (
    DEFAULT_PARAMETERS, canonical_sha256, validate_dataset, validate_identifier,
    validate_parameters,
)
from .reports import build_report, candidate_parameters, validate_proposal

_STUDY_FIELDS = {
    "schema_version", "study_id", "study_sha256", "engine_version",
    "dataset_sha256", "parameters", "partitions", "train_report", "holdout_key",
    "limitations",
}
_PERIODS = ("train", "validation", "holdout")
_LIMITATIONS = (
    "Chronological partitions are fixed at 60/20/20 using floor(0.6*N) and floor(0.8*N) boundaries.",
    "Only training-period results belong in AI prompts; viewing later data before selecting a candidate compromises the experiment.",
    "Each period independently restarts the same initial cash and SMA warmup, carries no position across boundaries, and liquidates at its final close.",
    "The minimum bar count is only an operational requirement, not sufficient statistical evidence.",
    "Repeated validation can overfit that period; a holdout must be frozen for one selected candidate before its results are viewed.",
    "A chronological comparison is research, not proof of future profitability or authorization to activate a strategy.",
    "Synthetic data cannot establish real-market performance; imported data is not authenticated by its source label or hash.",
    "These fingerprints bind reproducible inputs; they are not signatures or a defense against deliberate manual file edits.",
)


def _study_identifier(value):
    identifier = validate_identifier(value)
    if len(identifier) > 100:
        raise ValueError("Study identifiers must contain at most 100 characters")
    return identifier


def _fingerprint(study):
    return canonical_sha256({key: value for key, value in study.items() if key != "study_sha256"})


def _period_dataset(dataset, study_id, period, bounds):
    start, end = bounds
    return {
        "schema_version": 1, "dataset_id": study_id + "_" + period,
        "symbol": dataset["symbol"], "interval_seconds": dataset["interval_seconds"],
        "source": dataset["source"], "bars": copy.deepcopy(dataset["bars"][start:end]),
    }


def _boundaries(count):
    train_end, validation_end = count * 3 // 5, count * 4 // 5
    return {"train": (0, train_end), "validation": (train_end, validation_end),
            "holdout": (validation_end, count)}


def prepare_study(dataset, parameters, study_id):
    """Create a self-contained plan and a training-only AI report.

    No validation/holdout backtest runs occur here. Their metadata contains time
    boundaries and fingerprints, never prices, trades, metrics or equity curves.
    """
    study_id = _study_identifier(study_id)
    dataset = validate_dataset(dataset)
    parameters = validate_parameters(parameters)
    bounds = _boundaries(len(dataset["bars"]))
    minimum = parameters["slow_window"] + 2
    if any(end - start < minimum for start, end in bounds.values()):
        raise ValueError("Every chronological period needs at least slow_window + 2 bars")
    periods = {name: _period_dataset(dataset, study_id, name, bounds[name]) for name in _PERIODS}
    partitions = {}
    for name in _PERIODS:
        start, end = bounds[name]
        period = periods[name]
        partitions[name] = {
            "start_index": start, "end_index_exclusive": end,
            "bars_count": end - start,
            "first_open_time": period["bars"][0]["open_time"],
            "last_open_time": period["bars"][-1]["open_time"],
            "sha256": canonical_sha256(period),
        }
    study = {
        "schema_version": 1, "study_id": study_id,
        "engine_version": "crypto-research-v1",
        "dataset_sha256": canonical_sha256(dataset), "parameters": parameters,
        "partitions": partitions,
        "train_report": build_report(run_backtest(periods["train"], parameters), study_id + "_train"),
        # Renaming a study/file, changing its source label or altering baseline
        # parameters must not reset the identity of the same held-out prices.
        "holdout_key": canonical_sha256({
            "symbol": dataset["symbol"], "interval_seconds": dataset["interval_seconds"],
            "bars": periods["holdout"]["bars"],
        }),
        "limitations": list(_LIMITATIONS),
    }
    study["study_sha256"] = _fingerprint(study)
    return study


def validate_study(study, dataset):
    """Reproduce the plan and training report from the exact original inputs."""
    if not isinstance(study, dict) or set(study) != _STUDY_FIELDS:
        raise ValueError("Study has missing or unsupported fields")
    if type(study["schema_version"]) is not int or study["schema_version"] != 1:
        raise ValueError("Unsupported study schema version")
    _study_identifier(study["study_id"])
    if study["engine_version"] != "crypto-research-v1":
        raise ValueError("Unsupported study engine version")
    for key in ("dataset_sha256", "study_sha256", "holdout_key"):
        if not isinstance(study[key], str) or not re.fullmatch(r"[0-9a-f]{64}", study[key]):
            raise ValueError("Study fingerprints must be SHA-256 strings")
    if not isinstance(study["parameters"], dict) or set(study["parameters"]) != set(DEFAULT_PARAMETERS):
        raise ValueError("A study must preserve all original parameter assumptions")
    parameters = validate_parameters(study["parameters"])
    dataset = validate_dataset(dataset)
    if canonical_sha256(dataset) != study["dataset_sha256"]:
        raise ValueError("Study belongs to a different dataset")
    if _fingerprint(study) != study["study_sha256"]:
        raise ValueError("Study contents do not match its fingerprint")
    reproduced = prepare_study(dataset, parameters, study["study_id"])
    # Canonical hashes also distinguish JSON booleans from numerically equal
    # integers, unlike Python's plain nested-dict equality.
    if canonical_sha256(reproduced) != canonical_sha256(study):
        raise ValueError("Study does not reproduce from these data and parameters")
    return reproduced


def _comparison(dataset, study, proposal, stage):
    dataset = validate_dataset(dataset)
    study = validate_study(study, dataset)
    report = study["train_report"]
    proposal = validate_proposal(proposal, report)
    parameters = candidate_parameters(proposal, report)
    if any(study["partitions"][name]["bars_count"] < parameters["slow_window"] + 2
           for name in _PERIODS):
        raise ValueError("Candidate window is too long for a chronological period")
    metadata = study["partitions"][stage]
    period = _period_dataset(dataset, study["study_id"], stage,
                             (metadata["start_index"], metadata["end_index_exclusive"]))
    baseline = run_backtest(period, study["parameters"])
    candidate = run_backtest(period, parameters)
    return {
        "schema_version": 1, "stage": stage, "status": "chronological_research_only",
        "promotion_allowed": False, "source": dataset["source"],
        "study_id": study["study_id"], "study_sha256": study["study_sha256"],
        "dataset_sha256": study["dataset_sha256"], "holdout_key": study["holdout_key"],
        "train_report_id": report["report_id"], "train_report_sha256": report["report_sha256"],
        "proposal_id": proposal["proposal_id"], "proposal_sha256": canonical_sha256(proposal),
        "proposal": proposal, "partition": copy.deepcopy(metadata),
        "baseline_parameters": dict(study["parameters"]), "candidate_parameters": parameters,
        "baseline_metrics": baseline["metrics"], "candidate_metrics": candidate["metrics"],
        "baseline_benchmarks": baseline["benchmarks"], "candidate_benchmarks": candidate["benchmarks"],
        "delta_net_return_pct": candidate["metrics"]["net_return_pct"] - baseline["metrics"]["net_return_pct"],
        "delta_max_drawdown_pct": candidate["metrics"]["max_drawdown_pct"] - baseline["metrics"]["max_drawdown_pct"],
        "limitations": list(_LIMITATIONS) + [
            "Each buy-and-hold benchmark uses its strategy's initial allocation, not matched exposure duration.",
            "OHLC fills, costs and bar-close-only drawdown retain the backtest engine's simplified assumptions.",
        ],
    }


def validation_comparison(dataset, study, proposal):
    """Evaluate a training-linked candidate on the middle chronological period."""
    return _comparison(dataset, study, proposal, "validation")


def holdout_comparison(dataset, study, proposal):
    """Evaluate a frozen candidate on the final period; caller enforces one use."""
    return _comparison(dataset, study, proposal, "holdout")
