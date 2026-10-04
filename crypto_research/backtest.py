"""Deterministic, causal spot long/cash SMA crossover research.

A crossing of fast/slow SMAs at a completed bar queues an action for the next
bar's open. The first calculable SMA pair establishes the comparison; it does
not create a crossing. No action generated at the final close is executed.
Any remaining position is explicitly liquidated at the final close. Fractional
units, unlimited modeled liquidity and fixed adverse slippage are assumptions,
not an exchange fill guarantee. No leverage, shorts, pyramiding or rebalancing.
"""

import math
from decimal import Decimal, localcontext

from .contracts import canonical_sha256, validate_dataset, validate_parameters


def _decimal(value):
    return Decimal(str(value))


def _json_number(value):
    result = float(value)
    if not math.isfinite(result) or (value != 0 and result == 0):
        raise ValueError("Computed result cannot be represented as a finite JSON number")
    return result


def run_backtest(dataset, parameters):
    """Validate inputs and return a JSON-safe result without mutating either."""
    dataset = validate_dataset(dataset)
    parameters = validate_parameters(parameters)
    if len(dataset["bars"]) < parameters["slow_window"] + 2:
        raise ValueError("A backtest needs at least slow_window + 2 bars")
    digest = canonical_sha256(dataset)
    with localcontext() as context:
        context.prec = 50
        return _simulate(dataset, parameters, digest)


def _simulate(dataset, parameters, digest):
    bars = dataset["bars"]
    interval = dataset["interval_seconds"]
    fast_window, slow_window = parameters["fast_window"], parameters["slow_window"]
    fee = _decimal(parameters["fee_bps"]) / 10000
    slip = _decimal(parameters["slippage_bps"]) / 10000
    fraction = _decimal(parameters["position_fraction"])
    initial = _decimal(parameters["initial_cash"])
    cash, peak, max_drawdown, total_fees = initial, initial, Decimal(0), Decimal(0)
    position = None
    pending = None
    previous_difference = None
    fast_sum, slow_sum = Decimal(0), Decimal(0)
    closes = []
    trades, equity_curve = [], []

    def close_position(reference_price, fill_time, signal_time, reason):
        nonlocal cash, position, total_fees
        exit_price = reference_price * (1 - slip)
        proceeds = position["quantity"] * exit_price
        exit_fee = proceeds * fee
        cash += proceeds - exit_fee
        total_fees += exit_fee
        pnl = proceeds - exit_fee - position["entry_cost"]
        trade = {
            "entry_signal_time": position["entry_signal_time"],
            "entry_time": position["entry_time"],
            "exit_signal_time": signal_time, "exit_time": fill_time,
            "entry_price": _json_number(position["entry_price"]),
            "exit_price": _json_number(exit_price),
            "quantity": _json_number(position["quantity"]),
            "entry_notional": _json_number(position["entry_notional"]),
            "exit_notional": _json_number(proceeds),
            "entry_fee": _json_number(position["entry_fee"]),
            "exit_fee": _json_number(exit_fee),
            "net_pnl": _json_number(pnl),
            "return_pct": _json_number(pnl / position["entry_cost"] * 100),
            "exit_reason": reason,
        }
        trades.append(trade)
        position = None

    for index, bar in enumerate(bars):
        opening = _decimal(bar["open"])
        closing = _decimal(bar["close"])
        if pending is not None:
            action, signal_time = pending
            if action == "enter" and position is None:
                # Include entry fees within the fraction budget; never borrow.
                budget = cash * fraction
                notional = budget / (1 + fee)
                entry_fee = notional * fee
                entry_price = opening * (1 + slip)
                position = {
                    "entry_signal_time": signal_time, "entry_time": bar["open_time"],
                    "entry_price": entry_price, "quantity": notional / entry_price,
                    "entry_notional": notional, "entry_fee": entry_fee,
                    "entry_cost": notional + entry_fee,
                }
                cash -= notional + entry_fee
                total_fees += entry_fee
            elif action == "exit" and position is not None:
                close_position(opening, bar["open_time"], signal_time, "sma_cross_down")
            pending = None

        closes.append(closing)
        fast_sum += closing
        slow_sum += closing
        if index >= fast_window:
            fast_sum -= closes[index - fast_window]
        if index >= slow_window:
            slow_sum -= closes[index - slow_window]
        close_time = bar["open_time"] + interval
        if index >= slow_window - 1:
            difference = fast_sum / fast_window - slow_sum / slow_window
            if previous_difference is not None:
                if previous_difference <= 0 < difference and position is None:
                    pending = ("enter", close_time)
                elif previous_difference >= 0 > difference and position is not None:
                    pending = ("exit", close_time)
            previous_difference = difference

        if index == len(bars) - 1 and position is not None:
            close_position(closing, close_time, None, "end_of_dataset")
        # Liquidation value includes estimated exit friction while holding.
        quantity = position["quantity"] if position else Decimal(0)
        equity = cash + quantity * closing * (1 - slip) * (1 - fee)
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak * 100
        max_drawdown = max(max_drawdown, drawdown)
        equity_curve.append({
            "open_time": bar["open_time"], "close_time": close_time,
            "cash": _json_number(cash), "quantity": _json_number(quantity),
            "mark_price": _json_number(closing), "equity": _json_number(equity),
            "drawdown_pct": _json_number(drawdown),
        })

    wins = sum(1 for trade in trades if trade["net_pnl"] > 0)
    gains = sum((_decimal(trade["net_pnl"]) for trade in trades if trade["net_pnl"] > 0), Decimal(0))
    losses = sum((-_decimal(trade["net_pnl"]) for trade in trades if trade["net_pnl"] < 0), Decimal(0))
    benchmark_budget = initial * fraction
    benchmark_quantity = benchmark_budget / (1 + fee) / (_decimal(bars[0]["open"]) * (1 + slip))
    benchmark_exit = benchmark_quantity * _decimal(bars[-1]["close"]) * (1 - slip) * (1 - fee)
    benchmark_equity = initial - benchmark_budget + benchmark_exit
    return {
        "schema_version": 1, "engine_version": "crypto-research-v1",
        "dataset": {
            "dataset_id": dataset["dataset_id"], "symbol": dataset["symbol"],
            "interval_seconds": interval, "source": dataset["source"],
            "bars_count": len(bars), "first_open_time": bars[0]["open_time"],
            "last_open_time": bars[-1]["open_time"], "sha256": digest,
        },
        "parameters": parameters,
        "metrics": {
            "net_return_pct": _json_number((cash / initial - 1) * 100),
            "max_drawdown_pct": _json_number(max_drawdown), "trade_count": len(trades),
            "win_rate_pct": 100.0 * wins / len(trades) if trades else 0.0,
            "profit_factor": _json_number(gains / losses) if losses else None,
            "final_equity": _json_number(cash), "total_fees": _json_number(total_fees),
        },
        "benchmarks": {
            "cash_return_pct": 0.0,
            "same_initial_fraction_buy_hold_return_pct": _json_number((benchmark_equity / initial - 1) * 100),
        },
        "trades": trades, "equity_curve": equity_curve,
        "limitations": [
            "Single-dataset research is not out-of-sample evidence or proof of future profitability.",
            "OHLC fills assume fractional units, unlimited liquidity, fixed adverse slippage and no spread or latency beyond that assumption.",
            "Signals use completed closes and execute at the next open; terminal liquidation executes at the final close.",
            "Drawdown is measured at bar closes using liquidation value; intrabar drawdown is not measured.",
            "Fees and slippage are experimental assumptions, not verified account or exchange fees.",
            "The buy-and-hold benchmark invests the same initial fraction at the first open and liquidates at the last close; it does not match exposure duration.",
        ],
    }
