"""Consistent local SQLite backup. Does not copy credentials or upload anything."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from local_runtime import ROOT
from safety import process_lock


def backup_portfolio(root=ROOT):
    root = Path(root)
    data = root / "05-交易记录-data"
    source = data / "paper-ledger.sqlite3"
    if not source.is_file() or source.is_symlink():
        raise ValueError("No existing portfolio database to back up")
    folder = data / "backups"
    if folder.is_symlink():
        raise ValueError("Backup directory cannot be a symbolic link")
    folder.mkdir(exist_ok=True)
    name = datetime.now(timezone.utc).strftime("portfolio-%Y%m%dT%H%M%S%f.sqlite3")
    target = folder / name
    # Both locks use the same acquisition order as the engine.
    with process_lock(root / "runtime.lock"), process_lock(data / "portfolio.lock"):
        with target.open("xb"):
            pass
        try:
            with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup integrity check failed")
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    return target


if __name__ == "__main__":
    try:
        print("本機帳本備份完成：" + backup_portfolio().name)
    except (OSError, ValueError, sqlite3.Error):
        print("備份未完成；請確認帳本已建立，且沒有其他程序正在寫入。")
        raise SystemExit(2)
