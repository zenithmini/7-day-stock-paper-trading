"""Strict, versioned inputs for reproducible offline research.

Missing known strategy parameters receive experimental defaults. Dataset fields
are all required. Booleans are never accepted as numeric values.
"""

import hashlib
import json
import math
import re

DEFAULT_PARAMETERS = {
    "strategy_type": "sma_crossover",
    "fast_window": 5,
    "slow_window": 20,
    "position_fraction": 0.10,
    "initial_cash": 10000.0,
    "fee_bps": 10.0,
    "slippage_bps": 5.0,
}

_DATASET_FIELDS = {
    "schema_version", "dataset_id", "symbol", "interval_seconds", "source", "bars",
}
_BAR_FIELDS = {"open_time", "open", "high", "low", "close", "volume"}
_RESERVED_IDS = {"CON", "PRN", "AUX", "NUL"} | {
    "%s%d" % (prefix, number) for prefix in ("COM", "LPT") for number in range(1, 10)
}


def _fields(value, allowed, name, required=True):
    if not isinstance(value, dict):
        raise ValueError(name + " must be an object")
    if set(value) - allowed:
        raise ValueError(name + " contains unknown fields")
    if required and set(value) != allowed:
        raise ValueError(name + " is missing required fields")


def _integer(value, name):
    if type(value) is not int:
        raise ValueError(name + " must be an integer, not a boolean")
    return value


def _number(value, name, minimum=None, maximum=None, positive=False):
    if type(value) not in (int, float):
        raise ValueError(name + " must be a finite number, not a boolean")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(name + " must be a finite number") from None
    if not math.isfinite(result):
        raise ValueError(name + " must be a finite number")
    if positive and result <= 0:
        raise ValueError(name + " must be positive")
    if minimum is not None and result < minimum:
        raise ValueError(name + " is below its minimum")
    if maximum is not None and result > maximum:
        raise ValueError(name + " exceeds its maximum")
    return result


def validate_identifier(value):
    """Validate the same portable run_id rules used by the paper runtime."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", value):
        raise ValueError("Identifier must be a valid 1-120 character ASCII run_id")
    if value.split("_")[0].upper() in _RESERVED_IDS:
        raise ValueError("Reserved identifier")
    return value


def canonical_sha256(value):
    """Hash finite JSON data using the research project's canonical encoding."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_dataset(value):
    """Return a normalized copy; reject gaps, duplicates and malformed bars."""
    _fields(value, _DATASET_FIELDS, "dataset")
    if _integer(value["schema_version"], "schema_version") != 1:
        raise ValueError("Unsupported dataset schema_version")
    identifier = validate_identifier(value["dataset_id"])
    symbol = value["symbol"]
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{4,20}", symbol):
        raise ValueError("symbol must contain 4-20 uppercase ASCII letters or digits")
    interval = _integer(value["interval_seconds"], "interval_seconds")
    if interval not in (60, 300, 900, 3600):
        raise ValueError("Unsupported interval_seconds")
    if type(value["source"]) is not str or value["source"] not in ("synthetic", "imported"):
        raise ValueError("source must be synthetic or imported")
    bars = value["bars"]
    if not isinstance(bars, list) or not 1 <= len(bars) <= 100000:
        raise ValueError("bars must contain 1-100000 records")
    normalized = []
    previous_time = None
    for item in bars:
        _fields(item, _BAR_FIELDS, "bar")
        timestamp = _integer(item["open_time"], "bar.open_time")
        if timestamp < 0:
            raise ValueError("Bar times must be nonnegative epoch seconds")
        if timestamp % interval:
            raise ValueError("Bar times must align with interval_seconds")
        if previous_time is not None and timestamp != previous_time + interval:
            raise ValueError("Bars must be ordered, unique and have no interval gaps")
        bar = {"open_time": timestamp}
        for key in ("open", "high", "low", "close"):
            bar[key] = _number(item[key], "bar." + key, positive=True)
        bar["volume"] = _number(item["volume"], "bar.volume", minimum=0)
        if bar["low"] > min(bar["open"], bar["close"]) or bar["high"] < max(bar["open"], bar["close"]) or bar["low"] > bar["high"]:
            raise ValueError("Inconsistent OHLC prices")
        normalized.append(bar)
        previous_time = timestamp
    return {
        "schema_version": 1, "dataset_id": identifier, "symbol": symbol,
        "interval_seconds": interval, "source": value["source"], "bars": normalized,
    }


def validate_parameters(value):
    """Fill known defaults and reject unknown or unsafe parameters."""
    _fields(value, set(DEFAULT_PARAMETERS), "parameters", required=False)
    parameters = dict(DEFAULT_PARAMETERS)
    parameters.update(value)
    if type(parameters["strategy_type"]) is not str or parameters["strategy_type"] != "sma_crossover":
        raise ValueError("Unsupported strategy_type")
    fast = _integer(parameters["fast_window"], "fast_window")
    slow = _integer(parameters["slow_window"], "slow_window")
    if not 2 <= fast <= 200 or not fast < slow <= 500:
        raise ValueError("Require 2 <= fast_window <= 200 and fast_window < slow_window <= 500")
    parameters["position_fraction"] = _number(parameters["position_fraction"], "position_fraction", positive=True, maximum=0.10)
    parameters["initial_cash"] = _number(parameters["initial_cash"], "initial_cash", minimum=1, maximum=1e9)
    for key in ("fee_bps", "slippage_bps"):
        parameters[key] = _number(parameters[key], key, minimum=0, maximum=100)
    return parameters
