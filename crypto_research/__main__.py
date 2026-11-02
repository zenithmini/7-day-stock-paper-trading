"""Offline experiments and manual ChatGPT exchange, isolated from paper accounts."""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pathlib import Path

from .backtest import run_backtest
from .contracts import DEFAULT_PARAMETERS, canonical_sha256, validate_dataset, validate_identifier
from .market_data import MarketDataError
from .reports import (
    build_report, candidate_parameters, proposal_template, render_markdown,
    validate_proposal, validate_report,
)

PROJECT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = PROJECT / ".local" / "crypto-research"
sys.path.insert(0, str(PROJECT / "06-程序脚本-scripts"))
from safety import process_lock

MAX_INPUT_BYTES = 32 * 1024 * 1024
OUTPUT_KINDS = {"datasets", "reports", "candidates", "comparisons", "studies", "validations", "holdouts"}
OUTPUT_SUFFIXES = {".json", ".md", ".html", ".proposal-template.json", ".source.json", ".result.json", ".claim.json"}


class ResearchWorkflowError(ValueError):
    """An actionable workflow message which contains no server or model output."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are not accepted")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-finite JSON numbers are not accepted")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON numbers are not accepted")
    return result


def load_json(path):
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Input file exceeds the research size limit")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
                      parse_constant=_invalid_constant, parse_float=_float)


def ensure_directory(kind, root=None):
    if kind not in OUTPUT_KINDS:
        raise ValueError("Unsupported research output directory")
    root = Path(root) if root is not None else LOCAL_ROOT
    # Check existing ancestors before mkdir, including a symlinked .local folder.
    for item in [*reversed(root.parents), root, root / kind]:
        if item.is_symlink():
            raise ValueError("Research output cannot traverse symbolic links")
    (root / kind).mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        root.chmod(0o700)
        (root / kind).chmod(0o700)
    return root / kind


def write_bundle(kind, identifier, contents, root=None):
    """Exclusive, locked exports. Refuse overwrites and remove partial packets."""
    return write_packet_groups([(kind, identifier, contents)], root)[0]


def write_packet_groups(groups, root=None):
    """Export related study/report files together, under the existing writer lock."""
    if not isinstance(groups, list) or not groups:
        raise ValueError("A research export requires at least one packet")
    # Validate all identifiers and suffixes before creating any output directories.
    for kind, identifier, contents in groups:
        validate_identifier(identifier)
        if kind not in OUTPUT_KINDS or not isinstance(contents, dict) or not contents or any(
            suffix not in OUTPUT_SUFFIXES or not isinstance(value, str) for suffix, value in contents.items()
        ):
            raise ValueError("Unsupported research file bundle")
    packet_paths, writes = [], {}
    for kind, identifier, contents in groups:
        directory = ensure_directory(kind, root)
        paths = {suffix: directory / (identifier + suffix) for suffix in contents}
        packet_paths.append(paths)
        for suffix, path in paths.items():
            if path in writes:
                raise ValueError("Research packets cannot write to the same path")
            writes[path] = contents[suffix]
    created = []
    with process_lock(directory.parent / "research.lock"):
        if any(path.exists() or path.is_symlink() for path in writes):
            raise ValueError("Research identifier already exists; use a new identifier")
        try:
            for path, content in writes.items():
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created.append(path)
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise
    return packet_paths


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def save_report(dataset, parameters, identifier):
    result = run_backtest(dataset, parameters)
    report = build_report(result, identifier)
    paths = write_bundle("reports", identifier, {
        ".json": _json(report), ".md": render_markdown(report),
        ".proposal-template.json": _json(proposal_template(report)), ".result.json": _json(result),
    })
    return report, paths


def synthetic_dataset():
    """Artificial OHLC test fixture. This is not exchange history or profit evidence."""
    bars, previous = [], 100.0
    for index in range(600):
        close = round(100 + 6 * math.sin(index / 23) + 1.5 * math.sin(index / 4) + index * 0.005, 6)
        bars.append({
            "open_time": 1704067200 + index * 300,
            "open": previous, "high": round(max(previous, close) + 0.3, 6),
            "low": round(min(previous, close) - 0.3, 6), "close": close,
            "volume": 1000.0,
        })
        previous = close
    return {"schema_version": 1, "dataset_id": "synthetic01", "symbol": "BTCUSDT",
            "interval_seconds": 300, "source": "synthetic", "bars": bars}


def compare_candidate(dataset, report, proposal):
    report = validate_report(report)
    dataset = validate_dataset(dataset)
    if canonical_sha256(dataset) != report["dataset"]["sha256"]:
        raise ValueError("Candidate must use the exact dataset from the report")
    baseline = run_backtest(dataset, report["parameters"])
    # A manually edited report with a newly computed hash is still untrusted.
    reproduced = build_report(baseline, report["report_id"])
    if reproduced != report:
        raise ValueError("Report does not reproduce from these data and parameters")
    parameters = candidate_parameters(proposal, report)
    candidate = run_backtest(dataset, parameters)
    return {
        "schema_version": 1, "status": "single_dataset_research_only", "promotion_allowed": False,
        "report_id": report["report_id"], "report_sha256": report["report_sha256"],
        "proposal": validate_proposal(proposal, report),
        "baseline_metrics": baseline["metrics"], "candidate_metrics": candidate["metrics"],
        "baseline_benchmarks": baseline["benchmarks"],
        "candidate_benchmarks": candidate["benchmarks"], "candidate_parameters": parameters,
        "delta_net_return_pct": candidate["metrics"]["net_return_pct"] - baseline["metrics"]["net_return_pct"],
        "delta_max_drawdown_pct": candidate["metrics"]["max_drawdown_pct"] - baseline["metrics"]["max_drawdown_pct"],
        "limitations": [
            "A better result on the same dataset is not out-of-sample evidence.",
            "Changing allocation changes risk and return; each benchmark uses that strategy's own initial allocation.",
            "Candidates are never activated by this command; no paper or live orders are available.",
            "Trading fees and slippage are experimental assumptions. Electricity and API costs are excluded.",
        ],
    }


def parse_utc_date(value):
    """The CLI deliberately accepts unambiguous UTC date boundaries only."""
    if not isinstance(value, str) or len(value) != 10:
        raise ResearchWorkflowError("Use a date in YYYY-MM-DD format; boundaries are UTC.")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if parsed.strftime("%Y-%m-%d") != value:
            raise ValueError("Noncanonical date")
        return int(parsed.timestamp())
    except (ValueError, OverflowError, OSError):
        raise ResearchWorkflowError("Use a valid UTC date in YYYY-MM-DD format.") from None


def save_study(dataset, parameters, identifier, provenance=None):
    from .validation import prepare_study
    from .study_view import render_study_html
    study = prepare_study(dataset, parameters, identifier)
    report = study["train_report"]
    packets = [
        ("studies", identifier, {".json": _json(study)}),
        ("reports", report["report_id"], {
            ".json": _json(report), ".md": render_markdown(report), ".html": render_study_html(study),
            ".proposal-template.json": _json(proposal_template(report)),
        }),
    ]
    if provenance is not None:
        packets.append(("datasets", dataset["dataset_id"], {".json": _json(dataset), ".source.json": _json(provenance)}))
    paths = write_packet_groups(packets)
    output = {"study": paths[0][".json"], "train_report": paths[1][".json"],
              "chatgpt_prompt": paths[1][".md"], "viewer": paths[1][".html"],
              "proposal_template": paths[1][".proposal-template.json"]}
    if provenance is not None:
        output.update(dataset=paths[2][".json"], provenance=paths[2][".source.json"])
    return study, output


def recent_utc_window(days, now=None):
    if type(days) is not int or not 7 <= days <= 365:
        raise ResearchWorkflowError("Research history must contain 7-365 complete UTC days.")
    now = now if now is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ResearchWorkflowError("Research date requires a timezone.")
    end = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((end - timedelta(days=days)).timestamp()), int(end.timestamp())


def open_local_report(path):
    import webbrowser
    try:
        if webbrowser.open(path.resolve().as_uri()):
            return
    except (OSError, ValueError):
        pass
    print("The report is saved. Open the generated .html file manually if no browser appeared.", file=sys.stderr)


def holdout_once(dataset, study, proposal, validation_record):
    """Consume a local holdout claim before evaluating any holdout prices."""
    from .validation import holdout_comparison, validate_study, validation_comparison
    study = validate_study(study, dataset)
    # This recomputation only examines the training and validation stages.
    expected_validation = validation_comparison(dataset, study, proposal)
    if canonical_sha256(validation_record) != canonical_sha256(expected_validation):
        raise ResearchWorkflowError("Validation record does not match this study and fixed candidate.")
    key = study["holdout_key"]
    directory = ensure_directory("holdouts")
    claim_path = directory / (key + ".claim.json")
    result_path = directory / (key + ".json")
    if any(path.exists() or path.is_symlink() for path in (claim_path, result_path)):
        raise ResearchWorkflowError("This holdout has already been claimed. It cannot be tested again through this workflow.")
    claim = {
        "schema_version": 1, "holdout_key": key, "study_id": study["study_id"],
        "study_sha256": study["study_sha256"], "proposal_sha256": canonical_sha256(proposal),
        "validation_sha256": canonical_sha256(validation_record),
        "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "consumed_before_evaluation", "promotion_allowed": False,
    }
    # Exclusive creation under the shared OS lock prevents two workers from peeking.
    write_bundle("holdouts", key, {".claim.json": _json(claim)})
    try:
        result = holdout_comparison(dataset, study, proposal)
        output = {"schema_version": 1, "promotion_allowed": False, "claim": claim, "evaluation": result}
        paths = write_bundle("holdouts", key, {".json": _json(output)})
    except (ValueError, OSError, TypeError, KeyError, OverflowError, RecursionError):
        raise ResearchWorkflowError(
            "Holdout claim was saved, but evaluation/export failed. The claim remains consumed; do not retune on this holdout."
        ) from None
    return output, paths


def _paths(paths):
    # JSON-escaped output also works with restricted Windows console encodings.
    print(json.dumps({suffix: str(path) for suffix, path in paths.items()}, ensure_ascii=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Crypto history and research; no broker or cloud AI API")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Run an artificial, offline demonstration")
    demo.add_argument("--run-id", default="demo01")
    report = commands.add_parser("report", help="Backtest local OHLC JSON and export AI report")
    report.add_argument("--data", required=True)
    report.add_argument("--parameters")
    report.add_argument("--run-id", required=True)
    local = commands.add_parser("local-review", help="Ask an already installed local Ollama model")
    local.add_argument("--report", required=True)
    local.add_argument("--model", required=True)
    imported = commands.add_parser("import-proposal", help="Validate manually saved AI JSON")
    imported.add_argument("--report", required=True)
    imported.add_argument("--proposal", required=True)
    compare = commands.add_parser("compare", help="Compare a candidate; never activate it")
    compare.add_argument("--data", required=True)
    compare.add_argument("--report", required=True)
    compare.add_argument("--proposal", required=True)
    compare.add_argument("--run-id", required=True)
    history = commands.add_parser("history", help="Download closed historical bars from Binance's public data service")
    history.add_argument("--symbol", required=True)
    history.add_argument("--interval", choices=("1m", "5m", "15m", "1h"), default="15m")
    history.add_argument("--start", required=True, help="Inclusive UTC date YYYY-MM-DD")
    history.add_argument("--end", required=True, help="Exclusive UTC date YYYY-MM-DD")
    history.add_argument("--run-id", required=True)
    start = commands.add_parser("start-study", help="Download recent public history, prepare a study and show a local training report")
    start.add_argument("--symbol", default="BTCUSDT")
    start.add_argument("--interval", choices=("1m", "5m", "15m", "1h"), default="15m")
    start.add_argument("--days", type=int, default=180)
    start.add_argument("--parameters")
    start.add_argument("--run-id")
    start.add_argument("--open-report", action="store_true")
    study = commands.add_parser("prepare-study", help="Reserve 60/20/20 chronological partitions and export only training analysis")
    study.add_argument("--data", required=True)
    study.add_argument("--parameters")
    study.add_argument("--run-id", required=True)
    for name, help_text in (("validate", "Compare a training-bound candidate on the middle 20%%"),
                            ("holdout", "Evaluate a fixed validated candidate once on the final 20%%")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--data", required=True)
        command.add_argument("--study", required=True)
        command.add_argument("--proposal", required=True)
        if name == "validate":
            command.add_argument("--run-id", required=True)
        else:
            command.add_argument("--validation", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            validate_identifier(args.run_id)
            dataset = synthetic_dataset()
            write_bundle("datasets", args.run_id, {".json": _json(dataset)})
            _, paths = save_report(dataset, dict(DEFAULT_PARAMETERS), args.run_id)
            _paths(paths)
        elif args.command == "report":
            validate_identifier(args.run_id)
            parameters = load_json(args.parameters) if args.parameters else dict(DEFAULT_PARAMETERS)
            _, paths = save_report(load_json(args.data), parameters, args.run_id)
            _paths(paths)
        elif args.command in ("local-review", "import-proposal"):
            context = validate_report(load_json(args.report))
            if args.command == "local-review":
                from .ollama_local import request_proposal
                proposal = request_proposal(context, args.model)
                source = {"source": "local_ollama_service", "model": args.model,
                          "human_reviewed": False, "execution_enabled": False}
            else:
                proposal = validate_proposal(load_json(args.proposal), context)
                source = {"source": "manual_import", "model_origin_verified": False,
                          "execution_enabled": False}
            paths = write_bundle("candidates", proposal["proposal_id"], {
                ".json": _json(proposal), ".source.json": _json(source),
            })
            _paths(paths)
        elif args.command == "compare":
            validate_identifier(args.run_id)
            value = compare_candidate(load_json(args.data), load_json(args.report), load_json(args.proposal))
            _paths(write_bundle("comparisons", args.run_id, {".json": _json(value)}))
        elif args.command == "history":
            from .market_data import download_history
            validate_identifier(args.run_id)
            start, end = parse_utc_date(args.start), parse_utc_date(args.end)
            # Avoid consuming network requests for an output identifier already in use.
            output_dir = ensure_directory("datasets")
            if any((output_dir / (args.run_id + suffix)).exists() or
                   (output_dir / (args.run_id + suffix)).is_symlink() for suffix in (".json", ".source.json")):
                raise ResearchWorkflowError("Dataset identifier already exists; use a new --run-id.")
            dataset, source = download_history(args.symbol, args.interval, start, end, args.run_id)
            _paths(write_bundle("datasets", args.run_id, {".json": _json(dataset), ".source.json": _json(source)}))
        elif args.command == "start-study":
            from .market_data import download_history
            from .contracts import validate_parameters
            identifier = args.run_id or ("research_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8])
            validate_identifier(identifier)
            if len(identifier) > 100:
                raise ResearchWorkflowError("Study identifiers must contain at most 100 characters.")
            parameters = validate_parameters(load_json(args.parameters) if args.parameters else dict(DEFAULT_PARAMETERS))
            start_time, end_time = recent_utc_window(args.days)
            for kind, stem, suffixes in (
                ("datasets", identifier, (".json", ".source.json")),
                ("studies", identifier, (".json",)),
                ("reports", identifier + "_train", (".json", ".md", ".html", ".proposal-template.json")),
            ):
                directory = ensure_directory(kind)
                if any((directory / (stem + suffix)).exists() or (directory / (stem + suffix)).is_symlink()
                       for suffix in suffixes):
                    raise ResearchWorkflowError("Study identifier already exists; use a new --run-id.")
            dataset, provenance = download_history(args.symbol, args.interval, start_time, end_time, identifier)
            _, paths = save_study(dataset, parameters, identifier, provenance)
            _paths(paths)
            if args.open_report:
                open_local_report(paths["viewer"])
        elif args.command == "prepare-study":
            validate_identifier(args.run_id)
            parameters = load_json(args.parameters) if args.parameters else dict(DEFAULT_PARAMETERS)
            _, paths = save_study(load_json(args.data), parameters, args.run_id)
            _paths(paths)
        elif args.command == "validate":
            from .validation import validation_comparison
            validate_identifier(args.run_id)
            value = validation_comparison(load_json(args.data), load_json(args.study), load_json(args.proposal))
            _paths(write_bundle("validations", args.run_id, {".json": _json(value)}))
        elif args.command == "holdout":
            _, paths = holdout_once(load_json(args.data), load_json(args.study), load_json(args.proposal),
                                    load_json(args.validation))
            _paths(paths)
        return 0
    except (ResearchWorkflowError, MarketDataError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (ValueError, OSError, TypeError, KeyError, OverflowError, RecursionError):
        print("Research command stopped: invalid input, unavailable local service or output conflict.", file=sys.stderr)
        print("No strategy was activated. Check the input schema and use a new output identifier.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
