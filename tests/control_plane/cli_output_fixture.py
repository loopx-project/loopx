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


def read_cli_context_for_oracle(text: str) -> tuple[str, dict[str, int]]:
    """Read a fixture's pinned recovery without another decision or admission.

    Full traversal is a test oracle, not a model read requirement or retention
    claim. Keep its cost separate from the original, untouched CLI stdout.
    """
    payload = json.loads(text)
    if not isinstance(payload, dict) or payload.get("schema_version") != "context_capture_navigation_v1":
        return text, {}
    command = payload["recovery"]["command"]
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
        assert scope == identity, "context continuation changed its source or reader route"
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
            return body, {
                "navigation_chars": len(text),
                "navigation_lines": len(text.splitlines()),
                "navigation_utf8_bytes": len(text.encode("utf-8")),
                "reader_calls": calls,
                "reader_stdout_utf8_bytes": reader_bytes,
                "body_utf8_bytes": len(observed),
                "total_stdout_utf8_bytes": len(text.encode("utf-8")) + reader_bytes,
            }
        assert page["next_offset"] == len(observed) < total
        command = page["next_command"]


def measure_cli_context_for_oracle(text, *, output_format, measure):
    """Keep body semantics and both observable output costs in one receipt."""
    measurement = measure(text, output_format=output_format)
    if output_format != "json":
        return measurement
    body, cost = read_cli_context_for_oracle(text)
    if cost:
        measurement["resolved_payload"] = json.loads(body)
        measurement["context_body"] = measure(body, output_format=output_format)
        measurement["context_delivery"] = cost
    return measurement
