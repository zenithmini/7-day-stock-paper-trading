"""Offline experiments and manual ChatGPT exchange, isolated from paper accounts."""
import argparse
import json
import math
import os
import sys
from pathlib import Path

from .backtest import run_backtest
from .contracts import DEFAULT_PARAMETERS, canonical_sha256, validate_dataset, validate_identifier
from .reports import (
    build_report, candidate_parameters, proposal_template, render_markdown,
    validate_proposal, validate_report,
)

PROJECT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = PROJECT / ".local" / "crypto-research"
sys.path.insert(0, str(PROJECT / "06-程序脚本-scripts"))
from safety import process_lock

MAX_INPUT_BYTES = 32 * 1024 * 1024


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
    if kind not in {"datasets", "reports", "candidates", "comparisons"}:
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
    validate_identifier(identifier)
    if not isinstance(contents, dict) or not contents or any(
        suffix not in {".json", ".md", ".proposal-template.json", ".source.json", ".result.json"}
        or not isinstance(value, str) for suffix, value in contents.items()
    ):
        raise ValueError("Unsupported research file bundle")
    directory = ensure_directory(kind, root)
    paths = {suffix: directory / (identifier + suffix) for suffix in contents}
    created = []
    with process_lock(directory.parent / "research.lock"):
        if any(path.exists() or path.is_symlink() for path in paths.values()):
            raise ValueError("Research identifier already exists; use a new identifier")
        try:
            for suffix, path in paths.items():
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created.append(path)
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(contents[suffix])
                    handle.flush()
                    os.fsync(handle.fileno())
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise
    return paths


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


def _paths(paths):
    # JSON-escaped output also works with restricted Windows console encodings.
    print(json.dumps({suffix: str(path) for suffix, path in paths.items()}, ensure_ascii=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Crypto research only; no broker or cloud API")
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
        return 0
    except (ValueError, OSError, TypeError, KeyError, OverflowError, RecursionError):
        print("Research command stopped: invalid input, unavailable local service or output conflict.", file=sys.stderr)
        print("No strategy was activated. Check the input schema and use a new output identifier.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
