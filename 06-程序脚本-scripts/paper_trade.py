#!/usr/bin/env python3
"""Apply a local paper-trade JSON request. Never sends a broker order."""

import argparse
import json
from pathlib import Path
import sqlite3
from local_runtime import ROOT
from safety import process_lock

from paper_ledger import PaperLedger, PaperLedgerError



def build_ledger():
    return PaperLedger(
        ROOT / "05-交易记录-data/current-state.json",
        ROOT / "04-运行状态-state/readiness.json",
        ROOT / "04-运行状态-state/paper-config.json",
        ROOT / "05-交易记录-data/paper-ledger.json",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("open", "mark", "evaluate", "close", "status", "reconcile"))
    parser.add_argument("--request", type=Path, help="Path to a non-secret paper request JSON file")
    args = parser.parse_args()
    try:
        ledger = build_ledger()
        request = {}
        if args.request:
            request = json.loads(args.request.read_text(encoding="utf-8"))
        if args.action == "open":
            result = ledger.open_long(request)
        elif args.action == "mark":
            result = ledger.mark(request)
        elif args.action == "evaluate":
            result = ledger.mark(request, evaluate=True)
        elif args.action == "close":
            result = ledger.close(request)
        elif args.action == "reconcile":
            result = ledger.reconcile()
        else:
            ledger.validate_readiness()
            result = ledger.state
        print(json.dumps({"paper_trading": True, "live_order_sent": False, "result": result}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error, PaperLedgerError):
        print(json.dumps({
            "paper_trading": True,
            "live_order_sent": False,
            "error": "Paper action rejected; inspect the non-secret request, readiness and local state",
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

