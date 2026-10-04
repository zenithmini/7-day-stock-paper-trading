"""Offline crypto backtests with an optional local research adapter; no order APIs."""

from .backtest import run_backtest
from .contracts import DEFAULT_PARAMETERS, canonical_sha256, validate_dataset, validate_identifier, validate_parameters

__all__ = ["DEFAULT_PARAMETERS", "canonical_sha256", "run_backtest", "validate_dataset", "validate_identifier", "validate_parameters"]
