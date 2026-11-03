"""Transport diagnosis must be actionable without disclosing raw failures."""
import errno
import io
import json
import socket
import ssl
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crypto_research import __main__ as cli
from crypto_research import market_data as client
from crypto_research import network_diagnostics as diagnostics
from contextlib import redirect_stderr, redirect_stdout
from test_crypto_market_data import START, SECONDS, FakeResponse, row


class NetworkDiagnosticsTests(unittest.TestCase):
    def test_transport_failure_categories_keep_raw_messages_private(self):
        failures = [
            (socket.gaierror(-3, "PRIVATE-DNS"), "dns"),
            (ssl.SSLCertVerificationError(1, "PRIVATE-CERT"), "tls_certificate"),
            (ssl.SSLError(1, "PRIVATE-TLS"), "tls"),
            (TimeoutError("PRIVATE-TIMEOUT"), "timeout"),
            (ConnectionRefusedError("PRIVATE-REFUSED"), "connection_refused"),
            (ConnectionResetError("PRIVATE-RESET"), "connection_reset"),
            (OSError(errno.ENETUNREACH, "PRIVATE-UNREACHABLE"), "unreachable"),
            ("PRIVATE-UNKNOWN", "connection"),
        ]
        for reason, expected in failures:
            opener = Mock()
            opener.open.side_effect = URLError(reason)
            with self.subTest(expected=expected), patch.object(client, "build_opener", return_value=opener):
                with self.assertRaises(client.MarketDataError) as caught:
                    client.download_history("BTCUSDT", "5m", START, START + SECONDS, "network01")
            self.assertEqual(caught.exception.kind, expected)
            self.assertIn(client.DEFAULT_MARKET_HOST, str(caught.exception))
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertEqual(opener.open.call_count, 1)

    def test_unapproved_hosts_fail_before_any_connection(self):
        for host in ("https://api.binance.com", "api.binance.com:443", "api.binance.com.evil.example", "example.com", None):
            with self.subTest(host=host), patch.object(client, "build_opener") as build:
                with self.assertRaises(client.MarketDataError):
                    client.download_history("BTCUSDT", "5m", START, START + SECONDS, "network01", market_host=host)
                build.assert_not_called()

    def test_explicit_official_host_is_used_for_all_pages_and_provenance(self):
        payloads = [json.dumps({"serverTime": (START + 900) * 1000}).encode(), json.dumps([row(0)]).encode()]
        opener = Mock()
        opener.open.side_effect = lambda request, **kwargs: FakeResponse(payloads.pop(0), request.full_url)
        with patch.object(client, "build_opener", return_value=opener), patch.object(client, "sleep"):
            data, source = client.download_history("BTCUSDT", "5m", START, START + SECONDS, "network01",
                                                   market_host="api.binance.com")
        self.assertEqual(source["endpoint"], "https://api.binance.com")
        self.assertEqual(len(data["bars"]), 1)
        self.assertTrue(all(urlparse(call.args[0].full_url).netloc == "api.binance.com"
                            for call in opener.open.call_args_list))

    def test_network_failure_never_automatically_switches_host(self):
        opener = Mock()
        opener.open.side_effect = URLError(socket.gaierror(-3, "PRIVATE"))
        with patch.object(client, "build_opener", return_value=opener), self.assertRaises(client.MarketDataError):
            client.download_history("BTCUSDT", "5m", START, START + SECONDS, "network01")
        self.assertEqual(opener.open.call_count, 1)
        self.assertIn(client.DEFAULT_MARKET_HOST, opener.open.call_args.args[0].full_url)

    def test_probe_checks_exchange_time_and_exactly_one_closed_bar(self):
        selected = "api.binance.com"
        probe_row = row(3)
        probe_row[6] = (START + 1800) * 1000 - 1
        payloads = [json.dumps({"serverTime": (START + 1800) * 1000 + 1000}).encode(), json.dumps([probe_row]).encode()]
        opener = Mock()
        opener.open.side_effect = lambda request, **kwargs: FakeResponse(payloads.pop(0), request.full_url)
        with patch.object(client, "build_opener", return_value=opener), patch.object(client, "sleep"):
            result = client.probe_market_host(selected)
        self.assertTrue(result["closed_kline_verified"])
        query = parse_qs(urlparse(opener.open.call_args_list[1].args[0].full_url).query)
        self.assertEqual(query["limit"], ["1"])
        self.assertEqual(query["startTime"], [str((START + 900) * 1000)])
        self.assertEqual(query["endTime"], [str((START + 1800) * 1000 - 1)])
        self.assertNotIn("bars", result)

    def test_diagnostic_compares_hosts_but_does_not_expose_environment_secrets(self):
        def probe(host):
            if host == client.DEFAULT_MARKET_HOST:
                raise client.MarketDataError("[dns] hostname not resolved", kind="dns")
            return {"host": host, "status": "ok", "server_time_verified": True, "closed_kline_verified": True}
        with patch.object(diagnostics, "probe_market_host", side_effect=probe), \
                patch.object(diagnostics.ssl, "get_default_verify_paths", return_value=SimpleNamespace(cafile=None, capath=None)), \
                patch.dict(diagnostics.os.environ, {"BINANCE_API_KEY": "PRIVATE-KEY", "HTTPS_PROXY": "PRIVATE-PROXY"}):
            value = diagnostics.diagnose_network()
        self.assertTrue(value["any_host_verified"])
        self.assertEqual([row["status"] for row in value["hosts"]], ["failed", "ok"])
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertNotIn("BINANCE_API_KEY", json.dumps(value))
        self.assertIn("--market-host api.binance.com", diagnostics.render_diagnostics(value))
        self.assertEqual(value["tls_verification"], "enabled")

    def test_rate_limit_or_access_block_stops_remaining_diagnostic_probes(self):
        for status in (418, 429, 403, 451):
            error = client.MarketDataError("HTTP " + str(status), kind="rate_limit" if status in (418, 429) else "http", http_status=status)
            with self.subTest(status=status), patch.object(diagnostics, "probe_market_host", side_effect=error) as probe:
                result = diagnostics.diagnose_network()
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(result["hosts"][1]["status"], "skipped")
            self.assertFalse(result["any_host_verified"])

    def test_cli_diagnostic_returns_readable_json_and_clear_status(self):
        value = {"any_host_verified": False, "hosts": []}
        with patch.object(diagnostics, "diagnose_network", return_value=value), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["diagnose-network", "--json"]), 2)
        self.assertEqual(json.loads(output.getvalue()), value)

    def test_cli_passes_explicit_host_and_reports_connection_diagnosis(self):
        with patch("crypto_research.market_data.download_history", side_effect=client.MarketDataError("[tls_certificate] test", kind="tls_certificate")) as download, \
                patch.object(cli, "ensure_directory", return_value=Path("/unused-test-directory")), \
                redirect_stderr(io.StringIO()) as output:
            self.assertEqual(cli.main(["history", "--symbol", "BTCUSDT", "--start", "2026-10-01", "--end", "2026-10-02",
                                       "--run-id", "network01", "--market-host", "api.binance.com"]), 2)
        self.assertEqual(download.call_args.kwargs, {"market_host": "api.binance.com"})
        self.assertIn("tls_certificate", output.getvalue())
        self.assertIn("diagnose-network", output.getvalue())

    def test_http_error_category_does_not_disclose_response_or_retry(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(client.BASE_URL, 451, "PRIVATE", {}, io.BytesIO(b"PRIVATE"))
        with patch.object(client, "build_opener", return_value=opener), self.assertRaises(client.MarketDataError) as caught:
            client.download_history("BTCUSDT", "5m", START, START + SECONDS, "network01")
        self.assertEqual(caught.exception.http_status, 451)
        self.assertEqual(caught.exception.kind, "http")
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(opener.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
