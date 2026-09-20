import copy
import json
import io
from contextlib import redirect_stdout
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06-程序脚本-scripts"))
import binance_readiness_check as checker
import paper_engine
import run_observation as observation
import test_paper_ledger as fixtures
from backup_local import backup_portfolio
from local_runtime import initialize
from paper_ledger import PaperLedgerError
from safety import WRITE_PERMISSIONS, process_lock, read_only_permissions, record_path, validate_run_id

SAFE = {"enableReading": True, **{key: False for key in WRITE_PERMISSIONS}}


class BoundaryTests(unittest.TestCase):
    def test_read_only_permission_policy_fails_closed(self):
        self.assertTrue(read_only_permissions(SAFE))
        self.assertTrue(read_only_permissions({**SAFE, "ipRestrict": True, "enableFixReadOnly": True}))
        for key in WRITE_PERMISSIONS:
            for value in (True, None, "false", 0):
                with self.subTest(key=key, value=value):
                    self.assertFalse(read_only_permissions({**SAFE, key: value}))
            missing = dict(SAFE)
            del missing[key]
            self.assertFalse(read_only_permissions(missing))
        self.assertFalse(read_only_permissions({**SAFE, "enableReading": False}))
        self.assertFalse(read_only_permissions({**SAFE, "enableFutureNewTrading": True}))

    def test_bad_key_never_reaches_stocks_or_account_reads(self):
        config = {"BINANCE_ENV": "production", "BINANCE_API_KEY": "fake", "BINANCE_API_SECRET": "fake"}
        for fn in (checker.check, checker.check_stocks):
            with patch.object(checker, "get_json", side_effect=[({}, None), ({"serverTime": 1000}, None)]) as network, \
                    patch.object(checker, "signed_get", return_value=({**SAFE, "enableWithdrawals": True}, None)) as signed:
                result = fn(config)
            self.assertFalse(result["stock_etf_access_verified"])
            self.assertFalse(result["signed_account_read_verified"])
            self.assertEqual(network.call_count, 2)
            signed.assert_called_once_with(config, "/sapi/v1/account/apiRestrictions")

    def test_run_ids_cannot_escape_or_inject_journal_lines(self):
        for value in ("../escape", "/tmp/escape", "C:\\escape", "a/b", "a\\b", "..", "a\nb", "", "x" * 121, "CON", "COM1", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_run_id(value)
        self.assertEqual(validate_run_id("2026-09-20_slot-1"), "2026-09-20_slot-1")

    def test_invalid_run_id_is_rejected_before_account_or_disk_access(self):
        with patch.object(paper_engine, "build_ledger") as ledger, patch.object(paper_engine, "load_config") as config:
            with self.assertRaises(ValueError):
                paper_engine.execute({"action": "no_trade"}, "../outside")
            with self.assertRaises(ValueError):
                paper_engine.write_records("../outside", {}, {}, None)
        ledger.assert_not_called()
        config.assert_not_called()

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require Windows developer mode")
    def test_symlink_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "outside.json").write_text("keep")
            (root / "records").mkdir()
            (root / "records/id.json").symlink_to(root / "outside.json")
            with self.assertRaises(ValueError):
                record_path(root / "records", "id", ".json")
            self.assertEqual((root / "outside.json").read_text(encoding="utf-8"), "keep")

    def test_official_host_allowlist_precedes_network(self):
        for url in ("http://api.binance.com/", "https://api.binance.com.evil.example/", "https://user@api.binance.com/", "https://api.binance.com:443/", "https://example.com/"):
            with self.subTest(url=url), patch.object(checker, "build_opener") as opener:
                with self.assertRaises(ValueError):
                    checker.get_json(url, {"X-MBX-APIKEY": "fake"})
                opener.assert_not_called()

    def test_slow_quote_is_not_accepted_as_fresh(self):
        responses = [({"symbols": [{"symbol": "AAPL"}]}, None),
                     ({"symbol": "AAPL", "bidPrice": "100", "askPrice": "100.01", "bidSize": "10", "askSize": "10"}, None)]
        with patch.object(paper_engine, "get_json", side_effect=responses), patch.object(paper_engine, "monotonic", side_effect=[0, 6]):
            with self.assertRaisesRegex(paper_engine.PaperEngineError, "latency"):
                paper_engine.fetch_snapshot({"BINANCE_API_KEY": "fake"}, "AAPL", None)

    def test_initialization_never_inherits_account_or_overwrites_existing_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "local"
            initialize(root)
            before = (root / "05-交易记录-data/current-state.json").read_bytes()
            ready = json.loads((root / "04-运行状态-state/readiness.json").read_text(encoding="utf-8"))
            self.assertFalse(ready["broker_connected"])
            self.assertFalse(ready["autonomous_paper_execution_enabled"])
            self.assertFalse(ready["live_trading_enabled"])
            schedule = json.loads((root / "03-定时任务-routines/schedule.json").read_text(encoding="utf-8"))
            self.assertEqual(schedule["planned_trading_dates"], [])
            self.assertEqual(schedule["activation_status"], "disabled")
            with self.assertRaises(ValueError):
                initialize(root)
            self.assertEqual(before, (root / "05-交易记录-data/current-state.json").read_bytes())

    def test_observation_reads_chinese_portfolio_as_utf8(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "local"
            initialize(root)
            path = root / "05-交易记录-data/current-state.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            state["positions"] = [{"symbol": "AAPL", "thesis": "中文模擬策略"}]
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            check = {"checked_at": "2026-09-20T00:00:00+00:00", "stock_etf_access_verified": False, "errors": []}
            with patch.object(observation, "ROOT", root), patch.object(observation, "load_config", return_value={}), \
                    patch.object(observation, "check_stocks", return_value=check), \
                    patch.object(observation, "update_readiness"), patch.object(sys, "argv", ["observe", "--manual"]), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(observation.main(), 2)
            journals = list((root / "05-交易记录-data/journal").glob("*.md"))
            self.assertEqual(len(journals), 1)
            self.assertIn("中文模擬策略", journals[0].read_text(encoding="utf-8"))

    def test_cross_process_lock_rejects_second_writer_then_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "test.lock"
            code = "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from safety import process_lock\ntry:\n with process_lock(Path(sys.argv[2])): print('acquired')\nexcept BlockingIOError: print('blocked')"
            args = [sys.executable, "-B", "-c", code, str(ROOT / "06-程序脚本-scripts"), str(lock)]
            with process_lock(lock):
                result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True)
                self.assertEqual(result.stdout.strip(), "blocked")
            result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True)
            self.assertEqual(result.stdout.strip(), "acquired")

    def test_cloud_workflow_has_no_runtime_or_secrets(self):
        workflow = (ROOT / ".github/workflows/paper-trading-observation.yml").read_text(encoding="utf-8")
        for text in ("secrets.", "upload-artifact", "api.telegram.org", "run_observation.py", "schedule:", "pull_request_target"):
            self.assertNotIn(text, workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("run_tests.py", workflow)


class LedgerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PaperLedgerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_stale_second_instance_cannot_overwrite_first_trade(self):
        f = self.fixture
        first, second = f.make_ledger(), f.make_ledger()
        first.open_long({**f.request(), "run_id": "first"}, f.now)
        with self.assertRaisesRegex(PaperLedgerError, "one active"):
            second.open_long({**f.request(), "run_id": "second"}, f.now)
        latest = f.make_ledger()
        self.assertEqual(len(latest.ledger["events"]), 1)
        self.assertEqual(latest.ledger["events"][0]["run_id"], "first")

    def test_paper_records_do_not_overwrite_observation_records(self):
        f = self.fixture
        data = f.root / "05-交易记录-data"
        for folder, suffix in (("evidence", ".json"), ("journal", ".md")):
            (data / folder).mkdir()
            (data / folder / ("shared_id" + suffix)).write_text("original observation")
        with patch.object(paper_engine, "ROOT", f.root), patch.object(paper_engine, "build_ledger", side_effect=f.make_ledger):
            paper_engine.write_records("shared_id", {"action": "no_trade"}, {"status": "no_trade", "event": None}, f.now)
        self.assertEqual((data / "evidence/shared_id.json").read_text(encoding="utf-8"), "original observation")
        self.assertEqual((data / "journal/shared_id.md").read_text(encoding="utf-8"), "original observation")
        self.assertTrue((data / "evidence/paper/shared_id.json").is_file())
        self.assertTrue((data / "journal/paper/shared_id.md").is_file())

    def test_duplicate_execution_does_not_double_debit(self):
        f = self.fixture
        request = {**f.request(), "run_id": "repeat"}
        first = f.make_ledger().open_long(request, f.now)
        state = copy.deepcopy(f.make_ledger().state)
        second = f.make_ledger().open_long(request, f.now)
        self.assertEqual(first, second)
        self.assertEqual(f.make_ledger().state, state)
        self.assertEqual(len(f.make_ledger().ledger["events"]), 1)

    def test_export_crash_preserves_committed_trade_and_replay_is_safe(self):
        f = self.fixture
        request = {**f.request(), "run_id": "crash"}
        with patch("paper_ledger.atomic_json", side_effect=OSError("simulated disk fault")):
            with self.assertRaises(OSError):
                f.make_ledger().open_long(request, f.now)
        restored = f.make_ledger()
        self.assertEqual(len(restored.state["positions"]), 1)
        restored.reconcile()
        restored.open_long(request, f.now)
        self.assertEqual(len(f.make_ledger().ledger["events"]), 1)
        exported = json.loads((f.root / "05-交易记录-data/current-state.json").read_text(encoding="utf-8"))
        self.assertEqual(exported, f.make_ledger().state)

    def test_rejected_exit_does_not_commit_partial_cash_or_position_changes(self):
        f = self.fixture
        ledger = f.make_ledger()
        ledger.open_long(f.request(), f.now)
        before = copy.deepcopy(ledger.state)
        with self.assertRaises(PaperLedgerError):
            ledger.close({"quote": f.quote(), "reason": "invalid"}, f.now)
        self.assertEqual(f.make_ledger().state, before)
        self.assertEqual(len(f.make_ledger().ledger["events"]), 1)

    def test_no_trade_is_idempotent_and_never_changes_cash(self):
        f = self.fixture
        for _ in range(2):
            f.make_ledger().record_no_trade({"run_id": "wait"}, f.now)
        ledger = f.make_ledger()
        self.assertEqual(len(ledger.ledger["events"]), 1)
        self.assertEqual(ledger.state["cash_usdt"], 10000)

    def test_backup_contains_consistent_portfolio_without_credentials(self):
        f = self.fixture
        f.make_ledger().open_long(f.request(), f.now)
        target = backup_portfolio(f.root)
        with sqlite3.connect(target) as db:
            state, ledger = map(json.loads, db.execute("SELECT state,ledger FROM portfolio").fetchone())
            self.assertEqual(len(state["positions"]), 1)
            self.assertEqual(len(ledger["events"]), 1)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_enlarged_risk_limits_are_rejected(self):
        f = self.fixture
        f.readiness["max_position_size_percent"] = 100
        f._write_all()
        with self.assertRaisesRegex(PaperLedgerError, "ceiling"):
            f.make_ledger().open_long(f.request(), f.now)

    def test_readiness_permission_flag_alone_cannot_bypass_checks(self):
        f = self.fixture
        f.readiness["binance_stocks_api"]["key_permissions"]["enableWithdrawals"] = True
        f._write_all()
        with self.assertRaisesRegex(PaperLedgerError, "read-only"):
            f.make_ledger().open_long(f.request(), f.now)


if __name__ == "__main__":
    unittest.main()
