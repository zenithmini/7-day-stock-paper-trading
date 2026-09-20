import importlib.util
import json
import sys
import os
import tempfile
import unittest
from io import BytesIO
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "06-程序脚本-scripts" / "binance_readiness_check.py"
sys.path.insert(0, str(SOURCE.parent))
from safety import WRITE_PERMISSIONS
SAFE = {"enableReading": True, **{key: False for key in WRITE_PERMISSIONS}}
SPEC = importlib.util.spec_from_file_location("checker", SOURCE)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class ReadOnlyChecks(unittest.TestCase):
    def setUp(self):
        self.config = {"BINANCE_ENV": "production", "BINANCE_API_KEY": "fake-key", "BINANCE_API_SECRET": "fake-secret"}

    def test_missing_keys_are_not_success(self):
        config = {"BINANCE_ENV": "production"}
        with patch.object(checker, "get_json", side_effect=[({}, None), ({"serverTime": 1000}, None)]) as request:
            result = checker.check(config)
        self.assertFalse(result["signed_account_read_verified"])
        self.assertTrue(result["errors"])
        self.assertEqual(request.call_count, 2)

    def test_success_does_not_grant_stock_or_key_permissions(self):
        replies = [({}, None), ({"serverTime": 1000}, None), ({"canTrade": True, "balances": []}, None)]
        with patch.object(checker, "signed_get", return_value=(SAFE, None)), patch.object(checker, "get_json", side_effect=replies) as request:
            result = checker.check(self.config)
        self.assertTrue(result["signed_account_read_verified"])
        for field in ("stock_etf_access_verified", "orders_allowed"):
            self.assertFalse(result[field])
        output = json.dumps(result)
        self.assertNotIn("fake-key", output)
        self.assertNotIn("fake-secret", output)
        url = request.call_args.args[0]
        self.assertTrue(url.startswith("https://api.binance.com/api/v3/account?timestamp=1000&"))
        self.assertIn("signature=", url)

    def test_failed_account_shape_is_not_verified(self):
        replies = [({}, None), ({"serverTime": 1000}, None), ({"code": -2015}, None)]
        with patch.object(checker, "signed_get", return_value=(SAFE, None)), patch.object(checker, "get_json", side_effect=replies):
            result = checker.check(self.config)
        self.assertFalse(result["signed_account_read_verified"])

    def test_public_only_never_sends_credentials(self):
        with patch.object(checker, "get_json", side_effect=[({}, None), ({"serverTime": 1000}, None)]) as request:
            checker.check(self.config, public_only=True)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(len(call.args) == 1 for call in request.call_args_list))

    def test_configuration_rejects_custom_host_and_duplicate(self):
        for content in ("BINANCE_ENV=production\nBINANCE_BASE_URL=https://example.com", "BINANCE_ENV=production\nBINANCE_ENV=testnet"):
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "config.env"
                path.write_text(content)
                path.chmod(0o600)
                with self.assertRaises(ValueError):
                    checker.load_config(path)

    def test_readiness_preserves_unrelated_checks_and_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "04-运行状态-state").mkdir()
            path = root / "04-运行状态-state" / "readiness.json"
            before = {"last_readiness_check": None, "broker_connected": False, "notes": ["Keep this"], "orders_allowed": False}
            path.write_text(json.dumps(before))
            with patch.object(checker, "ROOT", root):
                checker.update_readiness({"signed_account_read_verified": True})
            after = json.loads(path.read_text())
            for key, value in before.items():
                self.assertEqual(after[key], value)
            self.assertTrue(after["binance_spot_api"]["signed_account_read_verified"])

    def test_redirects_are_not_followed(self):
        self.assertIsNone(checker.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com"))

    def test_error_body_only_exports_numeric_code(self):
        error = HTTPError("https://api.binance.com", 401, "", {}, BytesIO(b'{"code":-2015,"msg":"fake-secret"}'))
        with patch.object(checker, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            _, message = checker.get_json("https://api.binance.com")
        self.assertEqual(message, "HTTP 401 / Binance -2015")
        self.assertNotIn("fake-secret", message)

    def test_stocks_reads_use_equity_tickers_and_do_not_enable_trading(self):
        baseline = {"credentials_present": True, "public_api_reachable": True, "errors": [], "orders_allowed": False}
        replies = [({"symbols": [{"symbol": "AAPL", "minNotional": "5"}]}, None),
                   ({"symbol": "AAPL", "bidPrice": "100", "askPrice": "101"}, None)]
        with patch.object(checker, "check", return_value=baseline), patch.object(checker, "get_json", side_effect=replies) as get, patch.object(checker, "signed_get", side_effect=[(SAFE, None), ([], None)]):
            result = checker.check_stocks(self.config)
        self.assertTrue(result["stock_etf_access_verified"])
        self.assertFalse(result["orders_allowed"])
        self.assertFalse(result["quote_freshness_verified"])
        self.assertTrue(result["quote_response_received"])
        self.assertIn("/sapi/v1/equity/market/exchangeInfo?symbol=AAPL", get.call_args_list[0].args[0])

    def test_empty_stock_symbols_do_not_pass(self):
        baseline = {"credentials_present": True, "public_api_reachable": True, "errors": [], "orders_allowed": False}
        with patch.object(checker, "check", return_value=baseline), patch.object(checker, "get_json", side_effect=[({"symbols": []}, None), (None, "No data available")]), patch.object(checker, "signed_get", side_effect=[(SAFE, None), ([], None)]):
            result = checker.check_stocks(self.config)
        self.assertFalse(result["stock_etf_access_verified"])

    def test_quote_rejects_nan_infinity_and_crossed_market(self):
        for bid, ask in (("NaN", "10"), ("1", "Infinity"), ("11", "10"), ("0", "1")):
            self.assertFalse(checker.valid_quote({"symbol": "AAPL", "bidPrice": bid, "askPrice": ask}, "AAPL"))


if __name__ == "__main__":
    unittest.main()

