"""Run regression tests with real network connections disabled."""
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Network disabled during tests")), \
            patch.object(socket, "create_connection", side_effect=AssertionError("Network disabled during tests")):
        suite = unittest.defaultTestLoader.discover(str(root / "07-自动测试-tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
