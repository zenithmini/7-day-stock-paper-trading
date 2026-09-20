#!/usr/bin/env python3
"""Execute one autonomous local paper-trading decision; never sends a broker order."""

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import urlencode
import re
import sqlite3
from local_runtime import ROOT
from safety import process_lock, record_path, validate_run_id
from zoneinfo import ZoneInfo

from binance_readiness_check import HOSTS, check_stocks, get_json, load_config, update_readiness
from paper_ledger import PaperLedger, PaperLedgerError, atomic_json, decimal_value

CONFIG = ROOT / "09-API密钥-仅本地" / "binance-api.env"


class PaperEngineError(ValueError):
    pass


def build_ledger():
    return PaperLedger(
        ROOT / "05-交易记录-data" / "current-state.json",
        ROOT / "04-运行状态-state" / "readiness.json",
        ROOT / "04-运行状态-state" / "paper-config.json",
        ROOT / "05-交易记录-data" / "paper-ledger.json",
    )


def load_decision(path):
    decision = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(decision, dict):
        raise PaperEngineError("Decision must be a JSON object")
    if decision.get("paper_trading_only") is not True:
        raise PaperEngineError("Decision must explicitly confirm paper_trading_only")
    action = decision.get("action")
    if action not in {"open_long", "manage", "close", "no_trade"}:
        raise PaperEngineError("Unsupported paper decision action")
    return decision


def fetch_snapshot(config, symbol, now):
    """Read current ordinary-equity rules and quote using GET-only endpoints."""
    symbol = str(symbol).upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,19}", symbol):
        raise PaperEngineError("Invalid equity symbol")
    headers = {"X-MBX-APIKEY": config["BINANCE_API_KEY"]}
    base = HOSTS["production"]
    rules_data, error = get_json(base + "/sapi/v1/equity/market/exchangeInfo?" + urlencode({"symbol": symbol}), headers)
    symbols = rules_data.get("symbols") if isinstance(rules_data, dict) else None
    matches = [item for item in symbols if isinstance(item, dict) and item.get("symbol") == symbol] if isinstance(symbols, list) else []
    if not matches:
        raise PaperEngineError("No tradable paper rules for the selected symbol")
    quote_started = monotonic()
    quote_data, error = get_json(base + "/sapi/v1/equity/market/quote?" + urlencode({"symbol": symbol}), headers)
    latency = monotonic() - quote_started
    received_at = datetime.now(timezone.utc)
    if latency > 5:
        raise PaperEngineError("Quote request exceeded latency limit")
    if not isinstance(quote_data, dict) or quote_data.get("symbol") != symbol:
        raise PaperEngineError("No current paper quote for the selected symbol")
    try:
        bid = decimal_value(quote_data["bidPrice"], "bidPrice")
        ask = decimal_value(quote_data["askPrice"], "askPrice")
        bid_size = decimal_value(quote_data["bidSize"], "bidSize")
        ask_size = decimal_value(quote_data["askSize"], "askSize")
    except (KeyError, PaperLedgerError) as exc:
        raise PaperEngineError("Invalid paper quote shape") from exc
    if not (0 < bid <= ask and bid_size > 0 and ask_size > 0):
        raise PaperEngineError("Paper quote is not executable")
    rule = matches[0]
    return {
        "rules": {key: rule.get(key) for key in (
            "symbol", "tradability", "fractionable", "stepSize", "minQty", "maxQty", "minNotional", "maxNotional")},
        "quote": {
            "symbol": symbol,
            "bid": str(bid),
            "ask": str(ask),
            "bid_size": str(bid_size),
            "ask_size": str(ask_size),
            "received_at": received_at.isoformat(),
            "request_seconds": latency,
            "source_timestamp_verified": False,
        },
    }


def prior_event(ledger, run_id):
    return next((event for event in ledger.ledger.get("events", []) if event.get("run_id") == run_id), None)


def decision_symbol(decision, ledger):
    if decision["action"] == "open_long":
        return str(decision.get("symbol", "")).upper()
    positions = ledger.state.get("positions", [])
    if positions:
        return positions[0]["symbol"]
    return str(decision.get("symbol", "AAPL")).upper()


def execute(decision, run_id, now=None):
    validate_run_id(run_id)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ledger = build_ledger()
    duplicate = prior_event(ledger, run_id)
    if duplicate:
        return {"status": "duplicate_skipped", "event": duplicate, "snapshot": None}

    symbol = decision_symbol(decision, ledger)
    if not symbol:
        raise PaperEngineError("A symbol is required")
    config = load_config(CONFIG)
    readiness = check_stocks(config, symbol=symbol)
    update_readiness(readiness)
    if readiness.get("stock_etf_access_verified") is not True:
        raise PaperEngineError("Fresh Stocks read verification failed")

    # The API check itself takes time; use a post-refresh timestamp for freshness validation.
    now = datetime.now(timezone.utc)
    ledger = build_ledger()
    ledger.validate_readiness(now)
    snapshot = fetch_snapshot(config, symbol, now)
    now = datetime.now(timezone.utc)
    action = decision["action"]
    if action == "no_trade":
        ledger.record_no_trade({"run_id": run_id}, now)
        return {"status": "no_trade", "event": None, "snapshot": snapshot}
    if action == "open_long":
        request = {
            "run_id": run_id,
            "symbol": symbol,
            "quote": snapshot["quote"],
            "rules": snapshot["rules"],
            "stop_price": decision.get("stop_price"),
            "target_price": decision.get("target_price"),
            "thesis": decision.get("thesis", ""),
            "evidence": decision.get("evidence"),
        }
        event = ledger.open_long(request, now)
    elif action == "manage":
        event = ledger.mark({"run_id": run_id, "quote": snapshot["quote"]}, now, evaluate=True)
    else:
        event = ledger.close({"run_id": run_id, "quote": snapshot["quote"],
                              "reason": decision.get("reason", "end_of_day")}, now)
    return {"status": "executed", "event": event, "snapshot": snapshot}


def write_records(run_id, decision, result, now):
    evidence_path = record_path(ROOT / "05-交易记录-data" / "evidence" / "paper", run_id, ".json")
    journal_path = record_path(ROOT / "05-交易记录-data" / "journal" / "paper", run_id, ".md")
    ledger = build_ledger()
    local = now.astimezone(ZoneInfo("America/Chicago"))
    state = ledger.state
    state["last_run"] = {
        "run_id": run_id,
        "timestamp": now.isoformat(),
        "mode": "autonomous_local_paper",
        "stock_api_verified": True,
        "orders_placed": False,
        "paper_action": decision["action"],
        "paper_result": result["status"],
    }
    state["next_task_focus"] = (
        "Refresh market evidence and reassess the local paper position at the next scheduled Central-time check. "
        "Live trading remains disabled."
    )
    # Observation metadata is separate from the transactional portfolio.
    atomic_json(ROOT / "05-交易记录-data" / "last-paper-run.json", state["last_run"])
    evidence = {
        "run_id": run_id,
        "timestamp": now.isoformat(),
        "mode": "local_paper_only",
        "live_order_sent": False,
        "decision": decision,
        "result": result,
        "state": {key: state.get(key) for key in ("cash_usdt", "equity_usdt", "positions", "daily_open_risk_usdt")},
    }
    atomic_json(evidence_path, evidence)
    action = decision["action"]
    event = result.get("event") or {}
    lines = [
        "", "## autonomous_paper_" + run_id + " - " + now.isoformat(), "",
        "- What was done: Autonomous local paper decision processed: " + action + ".",
        "- Why it was done: The user authorized autonomous paper-trading decisions within the documented risk limits.",
        "- Order proposed: " + ("Yes" if action == "open_long" else "No") + ".",
        "- Order placed: No real order; local paper ledger only.",
        "- Order filled: " + ("Yes, simulated locally." if event else "No."),
        "- Current holdings: " + json.dumps(state.get("positions", []), ensure_ascii=False) + ".",
        "- Current cash: " + str(state.get("cash_usdt")) + " USDT paper cash.",
        "- Current risk: " + str(state.get("daily_open_risk_usdt")) + " USDT open risk.",
        "- Evidence captured: `05-交易记录-data/evidence/paper/" + run_id + ".json`.",
        "- Next task focus: Refresh read-only quote and reassess the paper position or no-trade state.",
        "- Human confirmations needed: None for local paper trading; live trading remains disabled.",
    ]
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision", required=True, type=Path, help="Non-secret JSON paper decision")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    try:
        validate_run_id(args.run_id)
        with process_lock(ROOT / "runtime.lock"):
            decision = load_decision(args.decision)
            result = execute(decision, args.run_id, now)
            if result["status"] != "duplicate_skipped":
                write_records(args.run_id, decision, result, now)
        print(json.dumps({"paper_trading": True, "live_order_sent": False, **result}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error, PaperLedgerError, PaperEngineError):
        print(json.dumps({"paper_trading": True, "live_order_sent": False,
                          "error": "Paper decision incomplete; reconcile local state before retrying with the same run_id. No broker order was sent."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

