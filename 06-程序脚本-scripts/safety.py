"""Local-only safety boundaries shared by all entry points."""
import os
import re
from contextlib import contextmanager
from pathlib import Path

WRITE_PERMISSIONS = (
    "enableWithdrawals", "enableSpotAndMarginTrading", "enableFutures",
    "enableVanillaOptions", "enableInternalTransfer", "permitsUniversalTransfer",
    "enableMargin", "enableFixApiTrade", "enablePortfolioMarginTrading",
)


def read_only_permissions(value):
    if not isinstance(value, dict) or value.get("enableReading") is not True:
        return False
    if any(value.get(key) is not False for key in WRITE_PERMISSIONS):
        return False
    # Unknown newly introduced capability flags must be explicitly disabled too.
    return all(v is False for k, v in value.items()
               if k not in {"enableReading", "enableFixReadOnly"} and k.startswith(("enable", "permits")))


def validate_run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", value):
        raise ValueError("run_id must contain 1-120 ASCII letters, digits, underscores or hyphens")
    if value.split("_")[0].upper() in {"CON", "PRN", "AUX", "NUL", *("COM%d" % n for n in range(1, 10)), *("LPT%d" % n for n in range(1, 10))}:
        raise ValueError("Reserved run_id")
    return value


def record_path(directory, run_id, suffix):
    validate_run_id(run_id)
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("Record directory cannot be a symbolic link")
    path = directory / (run_id + suffix)
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError("Record path must stay inside its directory")
    return path


@contextmanager
def process_lock(path):
    """OS-released lock; a crash cannot leave a permanent stale lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Lock path cannot be a symbolic link")
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise BlockingIOError("Another process is using local data") from None
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
