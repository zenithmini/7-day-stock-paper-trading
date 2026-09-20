"""SQLite execution journal for read-only experiment runs."""

import json
import sqlite3
from safety import validate_run_id
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('running','complete','interrupted')),
            finished_at TEXT, payload TEXT)""")

    def close(self):
        self.db.close()

    def begin(self, run_id):
        validate_run_id(run_id)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT 1 FROM runs WHERE status='running'").fetchone():
                raise RuntimeError("Unfinished run requires reconciliation")
            if self.db.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
                self.db.execute("COMMIT")
                return False
            self.db.execute("INSERT INTO runs VALUES (?,?,'running',NULL,NULL)", (run_id, utc_now()))
            self.db.execute("COMMIT")
            return True
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def finish(self, run_id, payload):
        encoded = json.dumps(payload, ensure_ascii=False)
        changed = self.db.execute("UPDATE runs SET status='complete',finished_at=?,payload=? WHERE run_id=? AND status='running'",
                                  (utc_now(), encoded, run_id)).rowcount
        if changed != 1:
            raise RuntimeError("Run is not active")

    def recover_read_only(self):
        # Caller must hold the process lock; this runner never places orders.
        count = self.db.execute("UPDATE runs SET status='interrupted',finished_at=? WHERE status='running'", (utc_now(),)).rowcount
        return count

    def completed(self):
        return [(run_id, json.loads(payload)) for run_id, payload in self.db.execute(
            "SELECT run_id,payload FROM runs WHERE status='complete' ORDER BY started_at,run_id")]

