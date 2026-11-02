"""Bounded, unauthenticated Binance Spot history reads for local research.

Only the public market-data host is contacted. A download is returned only when
the complete requested interval consists of consecutive bars already closed at
the exchange server time captured before pagination. No account API, key,
configuration file, redirect, order or automatic retry is involved.
"""
import json
import math
import re
from datetime import datetime, timezone
from time import sleep
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .contracts import canonical_sha256, validate_dataset, validate_identifier


BASE_URL = "https://data-api.binance.vision"
INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 15
PAGE_SIZE = 1000
MAX_BARS = 100000
MAX_EPOCH_SECONDS = 253402300799
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)


class MarketDataError(ValueError):
    """A sanitized, actionable public-data download failure."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MarketDataError("Public market-data redirects are not accepted.")


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise MarketDataError("Public market-data JSON contains duplicate fields.")
        value[key] = item
    return value


def _constant(value):
    raise MarketDataError("Public market-data JSON contains non-finite numbers.")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise MarketDataError("Public market-data JSON contains non-finite numbers.")
    return result


def _parse_json(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=_constant, parse_float=_float)
    except MarketDataError:
        raise
    except (ValueError, UnicodeError, OverflowError, RecursionError):
        raise MarketDataError("Public market-data response is not valid JSON.") from None


def _number_string(value, label, *, positive=False):
    if not isinstance(value, str) or len(value) > 100 or not _DECIMAL.fullmatch(value):
        raise MarketDataError("Invalid numeric string in market-data " + label + ".")
    result = float(value)
    if not math.isfinite(result) or result < 0 or positive and result <= 0:
        raise MarketDataError("Invalid numeric value in market-data " + label + ".")
    return result


def _integer(value, label, maximum=None):
    if type(value) is not int or value < 0 or maximum is not None and value > maximum:
        raise MarketDataError("Invalid integer in market-data " + label + ".")
    return value


def _request_json(opener, path, query):
    if path not in ("/api/v3/time", "/api/v3/klines"):
        raise MarketDataError("Unsupported public market-data endpoint.")
    url = BASE_URL + path + ("?" + urlencode(query) if query else "")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "CryptoResearch/1"},
                      method="GET")
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            if response.geturl() != url:
                raise MarketDataError("Public market-data redirects are not accepted.")
            if response.getcode() != 200:
                raise MarketDataError("Public market-data server returned an unsuccessful response.")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
            raise MarketDataError("Public market-data response exceeds the size limit.")
        return _parse_json(raw)
    except HTTPError as error:
        code = error.code if type(error.code) is int else 0
        retry_after = error.headers.get("Retry-After", "") if error.headers is not None else ""
        suffix = ""
        if isinstance(retry_after, str) and re.fullmatch(r"[0-9]{1,7}", retry_after):
            suffix = " Retry after at least " + str(int(retry_after)) + " seconds."
        error.close()
        if code in (418, 429):
            raise MarketDataError("Public market-data HTTP " + str(code) + "; download stopped without retry." + suffix) from None
        raise MarketDataError("Public market-data HTTP " + str(code) + "; download stopped.") from None
    except MarketDataError:
        raise
    except Exception:
        raise MarketDataError("Public market-data connection failed; check connectivity and regional availability.") from None


def _bar(row, expected_open_time, seconds, server_time_ms):
    if not isinstance(row, list) or len(row) != 12:
        raise MarketDataError("Kline response must contain 12 fields per bar.")
    opening_ms = _integer(row[0], "open time", MAX_EPOCH_SECONDS * 1000)
    closing_ms = _integer(row[6], "close time", MAX_EPOCH_SECONDS * 1000)
    if opening_ms != expected_open_time * 1000:
        raise MarketDataError("History has missing, duplicate, unordered or unexpected bars.")
    if closing_ms != (expected_open_time + seconds) * 1000 - 1 or closing_ms >= server_time_ms:
        raise MarketDataError("History contains an incomplete bar or inconsistent close time.")
    output = {"open_time": expected_open_time}
    for offset, key in ((1, "open"), (2, "high"), (3, "low"), (4, "close"), (5, "volume")):
        output[key] = _number_string(row[offset], key, positive=key != "volume")
    # Metadata is not used as a signal, but reject malformed API rows as a whole.
    for offset in (7, 9, 10):
        _number_string(row[offset], "volume metadata")
    _integer(row[8], "trade count")
    _number_string(row[11], "unused field")
    if output["low"] > min(output["open"], output["close"]) or output["high"] < max(output["open"], output["close"]) or output["low"] > output["high"]:
        raise MarketDataError("History contains inconsistent OHLC prices.")
    return output


def download_history(symbol, interval, start_time, end_time, dataset_id):
    """Return ``(normalized_dataset, provenance)`` for UTC ``[start, end)``.

    Start/end use integer epoch *seconds*. Binance wire timestamps use
    milliseconds. The end is exclusive and must be a closed-bar boundary, not
    later than the server's current open-bar boundary. All caller inputs are
    checked before the first connection. A partial dataset is never returned.
    """
    try:
        validate_identifier(dataset_id)
    except ValueError:
        raise MarketDataError("Invalid portable dataset identifier.") from None
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{4,20}", symbol):
        raise MarketDataError("Symbol must contain 4-20 uppercase ASCII letters or digits.")
    if not isinstance(interval, str) or interval not in INTERVALS:
        raise MarketDataError("Supported intervals are 1m, 5m, 15m and 1h.")
    seconds = INTERVALS[interval]
    for value, label in ((start_time, "requested start time"), (end_time, "requested end time")):
        _integer(value, label, MAX_EPOCH_SECONDS)
        if value % seconds:
            raise MarketDataError("Requested times must align with the UTC bar interval.")
    count = (end_time - start_time) // seconds
    if not 1 <= count <= MAX_BARS:
        raise MarketDataError("Request must contain 1-100000 bars and have end after start.")

    opener = build_opener(ProxyHandler({}), _NoRedirect())
    server = _request_json(opener, "/api/v3/time", {})
    if not isinstance(server, dict) or set(server) != {"serverTime"}:
        raise MarketDataError("Exchange server time could not be verified.")
    server_time_ms = _integer(server["serverTime"], "server time", MAX_EPOCH_SECONDS * 1000)
    last_closed_boundary = server_time_ms // (seconds * 1000) * seconds
    if end_time > last_closed_boundary:
        raise MarketDataError("Requested end includes a bar not yet closed at exchange server time.")

    bars, cursor, request_count = [], start_time, 1
    while cursor < end_time:
        requested = min(PAGE_SIZE, (end_time - cursor) // seconds)
        sleep(0.25)
        rows = _request_json(opener, "/api/v3/klines", {
            "symbol": symbol, "interval": interval, "startTime": cursor * 1000,
            "endTime": end_time * 1000 - 1, "timeZone": "0", "limit": requested,
        })
        request_count += 1
        if not isinstance(rows, list) or len(rows) != requested:
            raise MarketDataError("Exchange returned incomplete history; no partial dataset was saved.")
        for row in rows:
            bars.append(_bar(row, cursor, seconds, server_time_ms))
            cursor += seconds

    try:
        dataset = validate_dataset({
            "schema_version": 1, "dataset_id": dataset_id, "symbol": symbol,
            "interval_seconds": seconds, "source": "imported", "bars": bars,
        })
    except ValueError:
        raise MarketDataError("Downloaded history failed dataset quality checks.") from None
    provenance = {
        "schema_version": 1, "source": "binance_public_rest", "endpoint": BASE_URL,
        "symbol": symbol, "interval": interval, "dataset_sha256": canonical_sha256(dataset),
        "exchange_server_time_ms": server_time_ms, "requested_start_time": start_time,
        "requested_end_time_exclusive": end_time, "bar_count": len(bars),
        "request_count": request_count, "closed_bars_only": True, "gaps_detected": 0,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    return dataset, provenance
