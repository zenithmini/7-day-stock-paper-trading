"""Offline checks for the optional, nonexecuting Ollama adapter."""

import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crypto_research import ollama_local as adapter
from crypto_research import DEFAULT_PARAMETERS, run_backtest
from crypto_research.__main__ import synthetic_dataset
from crypto_research.reports import build_report, proposal_schema, proposal_template


REPORT = {"report_id": "offline-research-fixture"}
PROPOSAL = {"hypothesis": "Test a slower signal after costs."}
SCHEMA = {"type": "object", "properties": {"hypothesis": {"type": "string"}}}


class FakeResponse(io.BytesIO):
    def __init__(self, data, *, url=adapter.OLLAMA_ENDPOINT, status=200):
        super().__init__(data)
        self.url = url
        self.status = status
        self.requested_size = None

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status

    def read(self, size=-1):
        self.requested_size = size
        return super().read(size)


def envelope(**changes):
    payload = {"done": True, "done_reason": "stop", "response": json.dumps(PROPOSAL)}
    payload.update(changes)
    return json.dumps(payload).encode("utf-8")


class OllamaAdapterTests(unittest.TestCase):
    def test_actual_research_report_and_validator_restrict_local_model_candidates(self):
        report = build_report(run_backtest(synthetic_dataset(), DEFAULT_PARAMETERS), "ollama-review01")
        before = copy.deepcopy(report)
        no_change = proposal_template(report, "ollama-nochange01")
        candidate = proposal_template(report, "ollama-candidate01")
        candidate.update(action="propose", rationale="Test a shorter fast SMA after the fixed costs.",
                         candidate_parameters={"fast_window": 3, "slow_window": 20, "position_fraction": 0.05})
        invalid = copy.deepcopy(candidate)
        invalid["candidate_parameters"]["fee_bps"] = 0
        opener = Mock()
        opener.open.side_effect = [FakeResponse(envelope(response=json.dumps(value)))
                                   for value in (no_change, candidate, invalid)]
        with patch.object(adapter, "build_opener", return_value=opener):
            self.assertEqual(adapter.request_proposal(report, "qwen3:8b"), no_change)
            self.assertEqual(adapter.request_proposal(report, "qwen3:8b"), candidate)
            with self.assertRaises(ValueError):
                adapter.request_proposal(report, "qwen3:8b")
        self.assertEqual(report, before)
        for call in opener.open.call_args_list:
            payload = json.loads(call.args[0].data)
            self.assertEqual(payload["format"], proposal_schema())
            self.assertIn(report["report_sha256"], payload["prompt"])
            self.assertIn(report["report_id"], payload["prompt"])
            self.assertIn('"fee_bps":', payload["prompt"])

    def request(self, data=None, *, response=None, model="qwen3:8b", timeout=60):
        response = response or FakeResponse(envelope() if data is None else data)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(adapter, "build_ai_prompt", return_value="canonical report") as prompt, \
                patch.object(adapter, "proposal_schema", return_value=SCHEMA), \
                patch.object(adapter, "validate_proposal", return_value=PROPOSAL) as validate, \
                patch.object(adapter, "build_opener", return_value=opener) as build:
            result = adapter.request_proposal(REPORT, model, timeout=timeout)
        return result, response, opener, build, prompt, validate

    def test_fixed_endpoint_and_structured_request_use_no_environment_proxy(self):
        result, response, opener, build, prompt, validate = self.request(timeout=12)
        self.assertEqual(result, PROPOSAL)
        handlers = build.call_args.args
        self.assertIsInstance(handlers[0], ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], adapter._NoRedirect)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, adapter.OLLAMA_ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 12)
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "qwen3:8b")
        self.assertEqual(payload["prompt"], "canonical report")
        self.assertEqual(payload["format"], SCHEMA)
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["options"], {"temperature": 0, "num_predict": 4096})
        self.assertEqual(response.requested_size, adapter.MAX_RESPONSE_BYTES + 1)
        prompt.assert_called_once_with(REPORT)
        validate.assert_called_once_with(PROPOSAL, REPORT)

    def test_invalid_models_are_rejected_before_network_or_report_access(self):
        bad_names = ("", "../model", "http://server", "x/y", "x\\y", " qwen3", "模型",
                     "qwen3:8b-cloud", "CLOUD", "x" * 121, None, 8)
        for model in bad_names:
            with self.subTest(model=model), patch.object(adapter, "build_opener") as build, \
                    patch.object(adapter, "build_ai_prompt") as prompt, self.assertRaises(ValueError):
                adapter.request_proposal(REPORT, model)
            build.assert_not_called()
            prompt.assert_not_called()

    def test_invalid_timeouts_are_rejected_before_network(self):
        for timeout in (0, -1, 60.001, float("nan"), float("inf"), True, "60", None):
            with self.subTest(timeout=timeout), patch.object(adapter, "build_opener") as build, \
                    self.assertRaises(ValueError):
                adapter.request_proposal(REPORT, "qwen3:8b", timeout=timeout)
            build.assert_not_called()

    def test_redirect_handler_refuses_even_loopback_redirects(self):
        handler = adapter._NoRedirect()
        for target in ("http://127.0.0.1:11434/other", "https://example.com/"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                handler.redirect_request(None, None, 302, "Found", {}, target)

    def test_response_url_and_status_are_checked_before_reading(self):
        for args in ({"url": "https://example.com/"}, {"status": 302}, {"status": 500}):
            response = FakeResponse(envelope(), **args)
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.request(response=response)
            self.assertIsNone(response.requested_size)

    def test_incomplete_and_missing_results_are_rejected(self):
        for changes in ({"done": False}, {"done": 1}, {"done": None}, {"done_reason": "length"},
                        {"done_reason": None}, {"response": ""}, {"response": "   "},
                        {"response": None}, {"response": {}}, {"response": "[]"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.request(envelope(**changes))
        for raw in (b'{}', b'[]', b'{"response":"{}"}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.request(raw)

    def test_absent_done_reason_is_allowed_when_done_is_true(self):
        raw = json.dumps({"done": True, "response": json.dumps(PROPOSAL)}).encode("utf-8")
        result, *_ = self.request(raw)
        self.assertEqual(result, PROPOSAL)

    def test_duplicate_keys_and_nonfinite_values_are_rejected(self):
        bad = [b'{"done":false,"done":true,"response":"{}"}',
               b'{"done":true,"response":"{}","duration":NaN}',
               b'{"done":true,"response":"{}","duration":Infinity}',
               b'{"done":true,"response":"{}","duration":1e999}']
        for text in ('{"hypothesis":"first","hypothesis":"second"}', '{"value":NaN}',
                     '{"value":-Infinity}', '{"value":1e999}'):
            bad.append(envelope(response=text))
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.request(raw)

    def test_invalid_encoding_json_and_executable_text_are_rejected(self):
        for raw in (b'\xff', b'not json', envelope(response="```json\n{}\n```"),
                    envelope(response="__import__('os').system('echo unsafe')")):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.request(raw)

    def test_response_size_is_bounded(self):
        response = FakeResponse(b" " * (adapter.MAX_RESPONSE_BYTES + 2))
        with self.assertRaises(ValueError):
            self.request(response=response)
        self.assertEqual(response.requested_size, adapter.MAX_RESPONSE_BYTES + 1)

    def test_validator_rejection_does_not_expose_model_output(self):
        secret = "do not expose raw model output"
        opener = Mock()
        opener.open.return_value = FakeResponse(envelope())
        with patch.object(adapter, "build_ai_prompt", return_value="prompt"), \
                patch.object(adapter, "proposal_schema", return_value=SCHEMA), \
                patch.object(adapter, "build_opener", return_value=opener), \
                patch.object(adapter, "validate_proposal", side_effect=ValueError(secret)), \
                self.assertRaises(ValueError) as failure:
            adapter.request_proposal(REPORT, "qwen3:8b")
        self.assertNotIn(secret, str(failure.exception))
        self.assertIsNone(failure.exception.__cause__)

    def test_network_errors_do_not_expose_server_body(self):
        opener = Mock()
        opener.open.side_effect = RuntimeError("private prompt and server response")
        with patch.object(adapter, "build_ai_prompt", return_value="prompt"), \
                patch.object(adapter, "proposal_schema", return_value=SCHEMA), \
                patch.object(adapter, "build_opener", return_value=opener), \
                self.assertRaises(ValueError) as failure:
            adapter.request_proposal(REPORT, "qwen3:8b")
        self.assertNotIn("private", str(failure.exception))
        self.assertIsNone(failure.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
