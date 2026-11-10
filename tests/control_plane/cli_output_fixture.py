"""Owned physical roots for current and historical CLI output probes."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


def budget_fixture_path(root: Path) -> Path:
    suffix = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    if os.name == "nt":
        # Keep the native name short without depending on a drive-root /tmp.
        return Path(tempfile.gettempdir()) / f"lxb-{suffix}"
    return Path("/tmp") / f"loopx-cli-budget-{suffix}"


@contextlib.contextmanager
def stable_budget_fixture_root(root: Path):
    fixture = budget_fixture_path(root)
    if fixture.exists() or fixture.is_symlink():
        raise RuntimeError(f"refusing to replace existing fixture root: {fixture}")
    fixture.mkdir()
    try:
        yield fixture
    finally:
        shutil.rmtree(fixture)


def read_cli_context_for_oracle(text: str, *, output_format: str = "json") -> tuple[str, dict[str, int]]:
    """Read a fixture's pinned recovery without another decision or admission.

    Full traversal is a test oracle, not a model read requirement or retention
    claim. Keep its cost separate from the original, untouched CLI stdout.
    """
    body = text
    if output_format == "markdown":
        if not text.startswith("# LoopX Turn Envelope\n") or "```json\n" not in text:
            return text, {}
        body = text.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    payload = json.loads(body)
    if output_format == "markdown" and (not isinstance(payload, dict) or payload.get("schema_version")
            not in ("loopx_turn_envelope_v1", "context_capture_navigation_v1")):
        return text, {}  # A historical narrative's work-context block is not its carrier.
    if not isinstance(payload, dict) or payload.get("schema_version") != "context_capture_navigation_v1":
        return body, {}
    body, calls, reader_bytes = read_pinned_cli_body(payload["recovery"]["command"])
    navigation_bytes = len(text.encode("utf-8"))
    return body, {
        "navigation_chars": len(text),
        "navigation_lines": len(text.splitlines()),
        "navigation_utf8_bytes": navigation_bytes,
        "reader_calls": calls,
        "reader_stdout_utf8_bytes": reader_bytes,
        "body_utf8_bytes": len(body.encode("utf-8")),
        "total_stdout_utf8_bytes": navigation_bytes + reader_bytes,
    }


def read_pinned_cli_body(command: str) -> tuple[str, int, int]:
    """Read one digest-bound fixture body and account for every reader response."""
    observed = bytearray()
    reader_bytes = calls = 0
    identity = total = digest = None
    while True:
        words = shlex.split(command)
        quota = words.index("quota")
        if words[quota + 1] != "context-read":
            raise AssertionError("context recovery must be a read, never another guard")
        # Pagination may change only its cursor and page budgets. Preserve the
        # producing program, globals and original capture/reference identity.
        scope = list(words)
        for flag in ("--offset", "--page-bytes", "--max-page-output-bytes"):
            if flag in scope:
                index = scope.index(flag)
                del scope[index:index + 2]
        if identity is None:
            identity = scope
        assert scope == identity, ("context continuation changed its source or reader route", scope, identity)
        result = subprocess.run(words, capture_output=True, text=True, encoding="utf-8",
            errors="strict", check=False, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        page = json.loads(result.stdout)
        assert page["ok"] is True and page["offset"] == len(observed)
        if total is None:
            total, digest = page["total_bytes"], page.get("sha256")
        assert page["total_bytes"] == total and page.get("sha256") == digest
        chunk = page["content"].encode("utf-8")
        assert chunk, "context reader made no progress"
        observed.extend(chunk)
        reader_bytes += len(result.stdout.encode("utf-8"))
        calls += 1
        if page["eof"] is True:
            assert len(observed) == total, "context reader ended before the complete body"
            body = observed.decode("utf-8")
            assert isinstance(json.loads(body), dict)
            return body, calls, reader_bytes
        assert page["next_offset"] == len(observed) < total
        command = page["next_command"]


def measure_cli_context_for_oracle(text, *, output_format, measure):
    """Keep body semantics and both observable output costs in one receipt."""
    measurement = measure(text, output_format=output_format)
    body, cost = read_cli_context_for_oracle(text, output_format=output_format)
    if cost or body != text:
        measurement["resolved_payload"] = json.loads(body)
    if cost:
        measurement["context_body"] = measure(body, output_format="json")
        measurement["context_delivery"] = cost
    return measurement
