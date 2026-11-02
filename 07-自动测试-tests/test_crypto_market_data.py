"""Offline checks for the fixed Binance public market-data reader."""
import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crypto_research import market_data as client
from crypto_research.contracts import canonical_sha256


START = 1704067200
SECONDS = 300


def row(index=0):
    timestamp = (START + index * SECONDS) * 1000
    return [timestamp, "100.0", "102.0", "99.0", "101.0", "10.0",
            timestamp + SECONDS * 1000 - 1, "1010.0", 12, "5.0", "505.0", "0"]


class FakeResponse(io.BytesIO):
    def __init__(self, raw, url, status=200):
        super().__init__(raw)
        self.url, self.status, self.read_size = url, status, None

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status

    def read(self, size=-1):
        self.read_size = size
        return super().read(size)


class MarketDataTests(unittest.TestCase):
    def download(self, rows=None, *, count=2, server_time=None, raw_pages=None, response_url=None, status=200):
        rows = [row(i) for i in range(count)] if rows is None else rows
        server_time = (START + (count + 1) * SECONDS) * 1000 if server_time is None else server_time
        pages = [rows[index:index + 1000] for index in range(0, len(rows), 1000)] or [[]]
        payloads = [json.dumps({"serverTime": server_time}).encode()]
        payloads += raw_pages if raw_pages is not None else [json.dumps(page).encode() for page in pages]
        opener, responses = Mock(), []

        def opening(request, *, timeout):
            response = FakeResponse(payloads.pop(0), response_url or request.full_url, status)
            responses.append(response)
            return response

        opener.open.side_effect = opening
        with patch.object(client, "build_opener", return_value=opener) as build, \
                patch.object(client, "sleep") as sleep:
            result = client.download_history("BTCUSDT", "5m", START, START + count * SECONDS, "history01")
        return result, opener, build, sleep, responses

    def test_fixed_public_gets_and_millisecond_boundaries(self):
        (dataset, provenance), opener, build, sleep, responses = self.download()
        self.assertEqual(dataset["source"], "imported")
        self.assertEqual(dataset["bars"][0]["open_time"], START)
        self.assertEqual(dataset["bars"][0]["open"], 100.0)
        self.assertEqual(provenance["dataset_sha256"], canonical_sha256(dataset))
        self.assertTrue(provenance["closed_bars_only"])
        self.assertEqual(provenance["gaps_detected"], 0)
        self.assertEqual(provenance["request_count"], 2)
        handlers = build.call_args.args
        self.assertIsInstance(handlers[0], ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], client._NoRedirect)
        urls = []
        for call in opener.open.call_args_list:
            request = call.args[0]
            parsed = urlparse(request.full_url)
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, "data-api.binance.vision")
            self.assertEqual(request.get_method(), "GET")
            self.assertIsNone(request.data)
            self.assertEqual(call.kwargs["timeout"], 15)
            self.assertNotIn("X-mbx-apikey", dict(request.header_items()))
            urls.append(parsed)
        self.assertEqual(urls[0].path, "/api/v3/time")
        self.assertEqual(urls[0].query, "")
        query = parse_qs(urls[1].query)
        self.assertEqual(query, {"symbol": ["BTCUSDT"], "interval": ["5m"],
                                 "startTime": [str(START * 1000)],
                                 "endTime": [str((START + 600) * 1000 - 1)],
                                 "timeZone": ["0"], "limit": ["2"]})
        sleep.assert_called_once_with(0.25)
        self.assertTrue(all(response.read_size == client.MAX_RESPONSE_BYTES + 1 for response in responses))

    def test_pagination_is_consecutive_without_overlap(self):
        (dataset, provenance), opener, _, sleep, _ = self.download(count=1001)
        self.assertEqual(len(dataset["bars"]), 1001)
        self.assertEqual(provenance["request_count"], 3)
        queries = [parse_qs(urlparse(call.args[0].full_url).query)
                   for call in opener.open.call_args_list[1:]]
        self.assertEqual(queries[0]["limit"], ["1000"])
        self.assertEqual(queries[1]["limit"], ["1"])
        self.assertEqual(queries[1]["startTime"], [str((START + 1000 * SECONDS) * 1000)])
        self.assertEqual(sleep.call_count, 2)

    def test_all_invalid_inputs_are_rejected_before_network(self):
        valid = ["BTCUSDT", "5m", START, START + 600, "history01"]
        invalid = [(0, "btcUSDT"), (0, "BTC/USDT"), (0, "ABＣD"), (0, None),
                   (1, "1M"), (1, {}), (2, True), (2, START + 1), (2, -300),
                   (3, float("inf")), (3, START), (3, START - 300),
                   (3, START + 100001 * 300), (4, "../escape"), (4, "CON")]
        for index, value in invalid:
            args = list(valid)
            args[index] = value
            with self.subTest(index=index, value=value), patch.object(client, "build_opener") as build, \
                    self.assertRaises(client.MarketDataError):
                client.download_history(*args)
            build.assert_not_called()

    def test_server_time_rejects_future_or_open_bars_before_paging(self):
        opener = Mock()
        server = {"serverTime": (START + 600) * 1000 - 1}
        opener.open.side_effect = lambda request, **kwargs: FakeResponse(json.dumps(server).encode(), request.full_url)
        with patch.object(client, "build_opener", return_value=opener), patch.object(client, "sleep") as sleep, \
                self.assertRaisesRegex(client.MarketDataError, "not yet closed"):
            client.download_history("BTCUSDT", "5m", START, START + 600, "history01")
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()
        # At the exact next-bar boundary, the preceding bar is fully closed.
        self.download(server_time=(START + 600) * 1000)

    def test_empty_short_and_excess_pages_never_return_partial_history(self):
        for rows in ([], [row(0)], [row(0), row(1), row(2)]):
            with self.subTest(length=len(rows)), self.assertRaises(client.MarketDataError):
                self.download(rows)

    def test_gaps_duplicates_order_and_millisecond_errors_are_rejected(self):
        cases = ([row(0), row(0)], [row(0), row(2)], [row(1), row(0)])
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(client.MarketDataError):
                self.download(rows)
        for field in (0, 6):
            rows = [row(0), row(1)]
            rows[0][field] += 1
            with self.subTest(field=field), self.assertRaises(client.MarketDataError):
                self.download(rows)

    def test_cross_page_duplicate_or_gap_is_rejected(self):
        for final in (row(999), row(1001)):
            rows = [row(i) for i in range(1000)] + [final]
            with self.subTest(time=final[0]), self.assertRaises(client.MarketDataError):
                self.download(rows, count=1001)

    def test_malformed_rows_and_nonfinite_numeric_strings_are_rejected(self):
        bad = []
        for field, value in ((1, "NaN"), (1, "1e999"), (1, "1e-999"), (2, "90"),
                             (3, "110"), (4, True), (5, "-1"), (6, True), (7, "bad"),
                             (8, True), (9, "Infinity"), (10, "-5"), (11, {})):
            changed = row(0)
            changed[field] = value
            bad.append(changed)
        bad += [row(0)[:-1], row(0) + ["extra"], {}]
        for changed in bad:
            with self.subTest(row=changed), self.assertRaises(client.MarketDataError):
                self.download([changed, row(1)])

    def test_invalid_json_duplicate_keys_and_nonfinite_json_are_rejected(self):
        for raw in (b"not JSON", b"\xff", b'{"x":1,"x":2}', b'[NaN]', b'[1e999]'):
            with self.subTest(raw=raw), self.assertRaises(client.MarketDataError):
                self.download(raw_pages=[raw])

    def test_response_size_redirect_and_status_are_checked(self):
        for kwargs in ({"raw_pages": [b" " * (client.MAX_RESPONSE_BYTES + 1)]},
                       {"response_url": "https://example.com/"}, {"status": 503}):
            with self.subTest(kwargs=list(kwargs)), self.assertRaises(client.MarketDataError):
                self.download(**kwargs)
        with self.assertRaises(client.MarketDataError):
            client._NoRedirect().redirect_request(None, None, 302, "", {}, client.BASE_URL + "/api/v3/time")

    def test_rate_limit_stops_without_retry_or_raw_error_body(self):
        for code in (418, 429):
            opener = Mock()
            error = HTTPError(client.BASE_URL, code, "secret server message", {"Retry-After": "120"},
                              io.BytesIO(b"DO-NOT-PRINT-RESPONSE"))
            opener.open.side_effect = error
            with patch.object(client, "build_opener", return_value=opener), patch.object(client, "sleep") as sleep:
                with self.assertRaises(client.MarketDataError) as caught:
                    client.download_history("BTCUSDT", "5m", START, START + 600, "history01")
            self.assertIn(str(code), str(caught.exception))
            self.assertIn("120 seconds", str(caught.exception))
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("DO-NOT", str(caught.exception))
            self.assertEqual(opener.open.call_count, 1)
            sleep.assert_not_called()

    def test_transport_error_is_sanitized(self):
        opener = Mock()
        opener.open.side_effect = URLError("PRIVATE-ERROR-BODY")
        with patch.object(client, "build_opener", return_value=opener), self.assertRaises(client.MarketDataError) as caught:
            client.download_history("BTCUSDT", "5m", START, START + 600, "history01")
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_invalid_server_time_is_not_used(self):
        for server in ({"serverTime": True}, {"serverTime": "1700000000000"}, {}, [],
                       {"serverTime": 1000, "extra": "unknown"}):
            opener = Mock()
            opener.open.side_effect = lambda request, **kwargs: FakeResponse(json.dumps(server).encode(), request.full_url)
            with self.subTest(server=server), patch.object(client, "build_opener", return_value=opener), \
                    self.assertRaises(client.MarketDataError):
                client.download_history("BTCUSDT", "5m", START, START + 600, "history01")
            self.assertEqual(opener.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
