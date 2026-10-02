"""Contract cases for the book's pure provider; no live LoopX state is used."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROVIDER = PACKAGE_ROOT / "src" / "loopx_text_stats" / "cli.py"
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

from loopx_text_stats.cli import MAX_INPUT_BYTES, response  # noqa: E402


def request(text: object = "hello world\n", **extra: object) -> bytes:
    return json.dumps({
        "schema_version": "loopx_text_stats_request_v0", "text": text, **extra,
    }).encode("utf-8")


class ProviderContractTests(unittest.TestCase):
    def assert_rejected(self, data: bytes, error: str) -> None:
        code, payload = response(data)
        self.assertEqual(code, 1)
        self.assertEqual(payload, {
            "schema_version": "loopx_text_stats_response_v0",
            "extension_id": "loopx-text-stats",
            "ok": False,
            "error": error,
        })

    def test_documented_example(self) -> None:
        code, payload = response((PACKAGE_ROOT / "examples/request.json").read_bytes())
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["request_schema_version"], "loopx_text_stats_request_v0")
        self.assertEqual(payload["result"], {
            "characters": 51, "non_whitespace_characters": 44,
            "words": 8, "lines": 2,
        })

    def test_unknown_field_does_not_echo_private_value(self) -> None:
        self.assert_rejected(request(path="synthetic-private-value"), "invalid_fields")

    def test_wrong_schema(self) -> None:
        self.assert_rejected(request(schema_version="unknown"), "schema_mismatch")

    def test_non_object(self) -> None:
        for data in (b"[]", b"null", b'"hello"', b"123"):
            with self.subTest(data=data):
                self.assert_rejected(data, "object_required")

    def test_missing_and_non_text_values(self) -> None:
        self.assert_rejected(b'{}', "invalid_fields")
        for text in (None, False, 123, [], {}):
            with self.subTest(text=text):
                self.assert_rejected(request(text), "nonempty_text_required")

    def test_empty_and_whitespace(self) -> None:
        for text in ("", " \n\t", "\u2003"):
            with self.subTest(text=text):
                self.assert_rejected(request(text), "nonempty_text_required")

    def test_duplicate_key_keeps_specific_error(self) -> None:
        self.assert_rejected(
            b'{"schema_version":"loopx_text_stats_request_v0","text":"a","text":"b"}',
            "duplicate_field",
        )

    def test_bad_json_and_utf8(self) -> None:
        for data in (b"{", b"\xff", b'{"text":}'):
            with self.subTest(data=data):
                self.assert_rejected(data, "invalid_json")

    def test_encoded_size_boundary(self) -> None:
        data = request("a")
        self.assertEqual(response(data + b" " * (MAX_INPUT_BYTES - len(data)))[0], 0)
        self.assert_rejected(data + b" " * (MAX_INPUT_BYTES + 1 - len(data)), "input_too_large")

    def test_text_length_boundary(self) -> None:
        self.assertEqual(response(request("a" * 32000))[0], 0)
        self.assert_rejected(request("a" * 32001), "text_too_long")

    def test_repeat_and_unicode_measurement(self) -> None:
        data = request("你好 e\u0301\n")
        first = response(data)
        self.assertEqual(first, response(data))
        self.assertEqual(first[1]["result"], {
            "characters": 6, "non_whitespace_characters": 4,
            "words": 2, "lines": 1,
        })

    def test_doctor_does_not_consume_request(self) -> None:
        result = subprocess.run(
            [sys.executable, str(PROVIDER), "--doctor"], input=b"not json",
            capture_output=True, check=False,
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))

    def test_cli_writes_one_object(self) -> None:
        result = subprocess.run(
            [sys.executable, str(PROVIDER)], input=request(), capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(json.loads(result.stdout)["result"]["words"], 2)

    def test_large_integer_is_a_json_error_not_a_traceback(self) -> None:
        # Reproduce Python 3.11+'s conversion limit below our input byte limit.
        data = b'{"schema_version":' + b"1" * 5000 + b',"text":"x"}'
        result = subprocess.run(
            [sys.executable, str(PROVIDER)], input=data, capture_output=True, check=False,
            env={**os.environ, "PYTHONINTMAXSTRDIGITS": "4300"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout), {
            "schema_version": "loopx_text_stats_response_v0",
            "extension_id": "loopx-text-stats", "ok": False, "error": "invalid_json",
        })


if __name__ == "__main__":
    unittest.main()
