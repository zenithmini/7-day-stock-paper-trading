import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crypto_research import canonical_sha256, run_backtest, validate_dataset, validate_identifier, validate_parameters


def dataset(closes, opens=None):
    opens = opens if opens is not None else closes
    return {
        "schema_version": 1, "dataset_id": "test-series", "symbol": "BTCUSDT",
        "interval_seconds": 60, "source": "synthetic",
        "bars": [
            {"open_time": index * 60, "open": opening, "high": max(opening, closing),
             "low": min(opening, closing), "close": closing, "volume": 100}
            for index, (opening, closing) in enumerate(zip(opens, closes))
        ],
    }


def parameters(**overrides):
    result = {"fast_window": 2, "slow_window": 3, "fee_bps": 0, "slippage_bps": 0}
    result.update(overrides)
    return result


class CryptoBacktestTests(unittest.TestCase):
    def test_close_signal_executes_only_at_next_open(self):
        prices = [100, 100, 100, 110, 120, 130, 140]
        opens = [100, 100, 100, 110, 200, 130, 140]
        result = run_backtest(dataset(prices, opens), parameters())
        trade = result["trades"][0]
        self.assertEqual(trade["entry_signal_time"], 4 * 60)
        self.assertEqual(trade["entry_time"], 4 * 60)
        self.assertEqual(trade["entry_price"], 200)
        self.assertEqual(result["equity_curve"][3]["quantity"], 0)
        self.assertEqual(result["equity_curve"][4]["quantity"], 5)
        self.assertEqual(trade["exit_time"], 7 * 60)
        self.assertEqual(trade["exit_reason"], "end_of_dataset")
        self.assertIsNone(trade["exit_signal_time"])
        self.assertEqual(result["equity_curve"][-1]["quantity"], 0)

    def test_future_data_cannot_change_prior_signals_or_equity(self):
        common = [100, 100, 100, 110, 120, 130, 140]
        left = run_backtest(dataset(common + [150, 160, 170]), parameters())
        right = run_backtest(dataset(common + [20, 10, 5]), parameters())
        self.assertEqual(left["equity_curve"][:7], right["equity_curve"][:7])
        self.assertEqual(left["trades"][0]["entry_time"], right["trades"][0]["entry_time"])
        self.assertEqual(left["trades"][0]["quantity"], right["trades"][0]["quantity"])

    def test_final_close_crossing_is_not_filled(self):
        result = run_backtest(dataset([100, 100, 100, 100, 110]), parameters())
        self.assertEqual(result["metrics"]["trade_count"], 0)
        self.assertEqual(result["metrics"]["final_equity"], 10000)
        self.assertEqual(result["metrics"]["total_fees"], 0)

    def test_initial_above_sma_is_not_a_crossover(self):
        result = run_backtest(dataset([100, 110, 120, 130, 140, 150]), parameters())
        self.assertEqual(result["metrics"]["trade_count"], 0)

    def test_both_sided_costs_reduce_a_profitable_result(self):
        data = dataset([100, 100, 100, 110, 120, 130, 140, 150, 160])
        free = run_backtest(data, parameters())
        costly = run_backtest(data, parameters(fee_bps=10, slippage_bps=5))
        trade = costly["trades"][0]
        self.assertGreater(trade["entry_fee"], 0)
        self.assertGreater(trade["exit_fee"], 0)
        self.assertGreater(trade["entry_price"], 120)
        self.assertLess(trade["exit_price"], 160)
        self.assertAlmostEqual(costly["metrics"]["total_fees"], trade["entry_fee"] + trade["exit_fee"])
        self.assertLess(costly["metrics"]["final_equity"], free["metrics"]["final_equity"])
        self.assertLess(costly["benchmarks"]["same_initial_fraction_buy_hold_return_pct"], free["benchmarks"]["same_initial_fraction_buy_hold_return_pct"])

    def test_position_budget_includes_fees_and_does_not_repeat_entries(self):
        prices = [100, 100, 100, 110, 120, 130, 120, 110, 100, 90, 100, 110, 120, 130, 120, 110, 100, 90]
        result = run_backtest(dataset(prices), parameters(fee_bps=100, slippage_bps=100))
        self.assertEqual(result["metrics"]["trade_count"], 2)
        available = 10000
        for trade in result["trades"]:
            cost = trade["entry_notional"] + trade["entry_fee"]
            self.assertLessEqual(cost, available * 0.1 + 1e-9)
            self.assertLessEqual(cost, available)
            available += trade["net_pnl"]
        self.assertTrue(all(item["cash"] >= 0 for item in result["equity_curve"]))
        self.assertAlmostEqual(result["metrics"]["final_equity"], available)

    def test_buy_hold_benchmark_applies_identical_fraction_and_costs(self):
        result = run_backtest(dataset([100, 100, 100, 100, 100]), parameters(fee_bps=100, slippage_bps=100))
        expected = 10 * (((1 - 0.01) ** 2 / (1 + 0.01) ** 2) - 1)
        self.assertAlmostEqual(result["benchmarks"]["same_initial_fraction_buy_hold_return_pct"], expected)
        self.assertEqual(result["benchmarks"]["cash_return_pct"], 0)
        self.assertEqual(result["metrics"]["final_equity"], 10000)

    def test_flat_and_losing_trade_metrics_are_honest(self):
        flat = run_backtest(dataset([100] * 8), parameters())
        self.assertEqual(flat["metrics"]["win_rate_pct"], 0)
        self.assertIsNone(flat["metrics"]["profit_factor"])
        losing = run_backtest(dataset([100, 100, 100, 110, 120, 110, 100, 90]), parameters())
        self.assertEqual(losing["metrics"]["trade_count"], 1)
        self.assertEqual(losing["metrics"]["win_rate_pct"], 0)
        self.assertEqual(losing["metrics"]["profit_factor"], 0)
        self.assertGreater(losing["metrics"]["max_drawdown_pct"], 0)

    def test_hashes_determinism_and_no_input_mutation(self):
        original = dataset([100, 100, 100, 110, 120, 130])
        untouched = copy.deepcopy(original)
        configured = parameters()
        result = run_backtest(original, configured)
        self.assertEqual(result, run_backtest(original, configured))
        self.assertEqual(original, untouched)
        self.assertNotIn("initial_cash", configured)
        self.assertEqual(result["dataset"]["sha256"], canonical_sha256(validate_dataset(original)))
        reordered = {key: original[key] for key in reversed(original)}
        self.assertEqual(result["dataset"]["sha256"], run_backtest(reordered, configured)["dataset"]["sha256"])
        json.dumps(result, allow_nan=False)

    def test_rejects_unknown_fields_bool_nan_and_invalid_ohlc(self):
        mutations = [
            ("schema_version", True), ("interval_seconds", True),
            ("symbol", "btcusdt"), ("source", "binance_verified"),
            ("extra", 1),
        ]
        for key, value in mutations:
            with self.subTest(key=key):
                data = dataset([100] * 6)
                data[key] = value
                with self.assertRaises(ValueError):
                    validate_dataset(data)
        for key, value in (("open", True), ("close", float("nan")), ("high", float("inf")), ("low", 101), ("volume", -1), ("open_time", False), ("extra", 0)):
            with self.subTest(bar_key=key):
                data = dataset([100] * 6)
                data["bars"][0][key] = value
                with self.assertRaises(ValueError):
                    validate_dataset(data)

    def test_rejects_duplicate_unordered_gap_and_misaligned_bars(self):
        for timestamp in (0, -60, 180, 61):
            with self.subTest(timestamp=timestamp):
                data = dataset([100] * 6)
                data["bars"][1]["open_time"] = timestamp
                with self.assertRaises(ValueError):
                    validate_dataset(data)

    def test_rejects_aligned_consecutive_negative_epoch_times(self):
        data = dataset([100] * 6)
        for bar in data["bars"]:
            bar["open_time"] -= 6 * 60
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            validate_dataset(data)

    def test_parameter_limits_and_minimum_sample(self):
        invalid = ({"fast_window": True}, {"slow_window": 2}, {"position_fraction": 0.10001},
                   {"position_fraction": 0}, {"fee_bps": float("nan")}, {"slippage_bps": 101},
                   {"initial_cash": False}, {"initial_cash": 1e10}, {"leverage": 2}, {"strategy_type": "ai"})
        for settings in invalid:
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):
                    validate_parameters(settings)
        with self.assertRaises(ValueError):
            run_backtest(dataset([100] * 4), parameters())
        for value in ("../data", "CON", "LPT1_test", "漢字", "x" * 121):
            with self.subTest(identifier=value):
                with self.assertRaises(ValueError):
                    validate_identifier(value)


if __name__ == "__main__":
    unittest.main()
