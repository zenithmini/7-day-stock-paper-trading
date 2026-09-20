#!/usr/bin/env python3
"""Six-slot, read-only runner with locking, duplicate prevention and export recovery."""

import argparse
import sqlite3
from safety import process_lock, record_path, validate_run_id
from local_runtime import PROJECT
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from binance_readiness_check import CONFIG, ROOT, check_stocks, load_config, update_readiness
from run_store import RunStore, utc_now

STARTUP = ("AGENTS.md", "AGENTS.zh-CN.md", "02-项目文档-docs/TRADING-STRATEGY.md", "02-项目文档-docs/TRADING-STRATEGY.zh-CN.md",
           "03-定时任务-routines/schedule.json", "03-定时任务-routines/schedule.zh-CN.json", "03-定时任务-routines/CONTINUITY.md",
           "03-定时任务-routines/CONTINUITY.zh-CN.md", "04-运行状态-state/readiness.json", "05-交易记录-data/current-state.json")


def due_slot(schedule, now):
    local = now.astimezone(ZoneInfo(schedule["timezone"]))
    if local.date().isoformat() not in schedule["planned_trading_dates"]:
        return None
    candidates = []
    for task in schedule["tasks"]:
        hour, minute = map(int, task["time"].split(":"))
        planned = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delay = local - planned
        if timedelta(0) <= delay <= timedelta(minutes=schedule["late_start_grace_minutes"]):
            candidates.append((delay, task["id"]))
    if candidates:
        _, task_id = min(candidates, key=lambda candidate: candidate[0])
        return local.date().isoformat() + "_" + task_id
    return None


def atomic_write(path, text):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def export_records(store):
    # Export deterministic per-run files; replay after a crash cannot duplicate entries.
    records = store.completed()
    for run_id, payload in records:
        atomic_write(record_path(ROOT / "05-交易记录-data" / "journal", run_id, ".md"), payload["journal"])
        atomic_write(record_path(ROOT / "05-交易记录-data" / "evidence", run_id, ".json"), json.dumps(payload["check"], indent=2) + "\n")
    if records:
        run_id, payload = records[-1]
        atomic_write(ROOT / "05-交易记录-data" / "last-observation.json", json.dumps({
            "run_id": run_id, "timestamp": payload["check"]["checked_at"],
            "stock_api_verified": payload["check"].get("stock_etf_access_verified", False),
            "orders_placed": False,
        }, indent=2) + "\n")


def make_payload(run_id, check, state):
    journal = "\n".join([
        "# Read-only observation / 只读观察", "", "- Run: " + run_id,
        "- Timestamp: " + utc_now(),
        "- Action / 本次操作: Read Binance Stocks API and record actual verification results.",
        "- Reason / 原因: Verify data and account access before experiment execution.",
        "- Order proposed / 提出订单: no", "- Order placed / 提交订单: no", "- Order filled / 成交: no",
        "- Local paper holdings / 本地模拟持仓: " + json.dumps(state.get("positions", []), ensure_ascii=False),
        "- Local paper cash / 本地模拟现金 USDT: " + str(state.get("cash_usdt")),
        "- Local paper risk / 本地模拟未平仓风险 USDT: " + str(state.get("daily_open_risk_usdt")),
        "- Real account holdings and cash / 真实账户资产: not reconciled by this checker.",
        "- Errors / 检查问题: " + json.dumps(check.get("errors", [])),
        "- Next / 下次重点: Resolve authentication, verify Stocks data and review the stop/fill draft.",
        "- Human input / 人工事项: Local API settings if authentication fails; review stop/fill draft.",
        "- Evidence: ../evidence/" + run_id + ".json", "",
    ])
    return {"journal": journal, "check": check}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--manual", action="store_true")
    group.add_argument("--recover", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    schedule = json.loads((ROOT / "03-定时任务-routines/schedule.json").read_text(encoding="utf-8"))
    slot = due_slot(schedule, datetime.now(ZoneInfo("UTC")))
    if args.dry_run:
        print(json.dumps({"due_slot": slot, "planned_slots": len(schedule["planned_trading_dates"]) * len(schedule["tasks"])}))
        return 0
    if not args.manual and not args.recover and (schedule.get("activation_status") != "enabled" or slot is None):
        print(json.dumps({"status": "outside_scheduled_window"}))
        return 0
    with process_lock(ROOT / "runtime.lock"):
        store = RunStore(ROOT / "05-交易记录-data" / "runs.sqlite3")
        try:
            export_records(store)
            if args.recover:
                count = store.recover_read_only()
                print(json.dumps({"interrupted_runs_recorded": count, "exports_restored": True}))
                return 0
            for name in STARTUP:
                (PROJECT / name).read_text(encoding="utf-8")
            journals = sorted((ROOT / "05-交易记录-data" / "journal").glob("*.md"), key=lambda p: p.stat().st_mtime)
            if journals:
                journals[-1].read_text(encoding="utf-8")
            run_id = "manual_" + datetime.now().strftime("%Y%m%dT%H%M%S%f") if args.manual else slot
            if not store.begin(run_id):
                print(json.dumps({"status": "duplicate_skipped", "run_id": run_id}))
                return 0
            state = json.loads((ROOT / "05-交易记录-data" / "current-state.json").read_text(encoding="utf-8"))
            result = check_stocks(load_config(CONFIG))
            update_readiness(result)
            store.finish(run_id, make_payload(run_id, result, state))
            export_records(store)
            print(json.dumps({"run_id": run_id, "stock_api_verified": result["stock_etf_access_verified"], "errors": result["errors"]}, indent=2))
            return 0 if result["stock_etf_access_verified"] else 2
        finally:
            store.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        print(json.dumps({"error": "Observation incomplete; inspect local state and use --recover before next run"}))
        raise SystemExit(4)

