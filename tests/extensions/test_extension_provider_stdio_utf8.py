"""Bundled extension entrypoints must pin their own stdio to UTF-8.

LoopX's extension transport hands a provider one JSON object on UTF-8 bytes over
stdin and decodes one UTF-8 JSON object from its stdout
(`loopx.extensions.runtime`). The provider runs as an ordinary interpreter, so
without an explicit pin `sys.stdin` and `sys.stdout` use the host locale codec -
`cp936` on a zh-CN Windows host. A non-ASCII request then fails to decode and a
non-ASCII result fails to encode, so the provider cannot honor the transport
contract at all.

`tests/test_cli_stdio_utf8.py` guards this for the shipped CLI process; these
cases cover the bundled extension entrypoints the transport launches. The
payload carries an emoji, which no `gbk` stream can encode, so a missing pin is
a hard failure rather than a silent byte difference.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# A codec the entrypoints must not fall back to. `gbk` cannot represent the
# emoji below, so an unpinned stream fails hard instead of differing in bytes.
LOCALE_CODEC = "gbk"
NON_ASCII_TEXT = "🚀 中文偏好"

_REQUEST_SCHEMA = "semantic_preference_provider_request_v0"
_RESPONSE_SCHEMA = "semantic_preference_provider_response_v0"

_SEMANTIC_PREFERENCE_ENTRYPOINT = """\
from loopx.extensions.openviking_semantic_preference import provider


def _find(**kwargs):
    return [
        {
            "preference_ref": kwargs["target_uri"].rstrip("/") + "/preferences/demo",
            "summary": kwargs["query"],
        }
    ]


provider._find = _find
raise SystemExit(provider.main())
"""

_PERIODIC_REPORT_ENTRYPOINT = """\
from loopx.extensions.openviking_periodic_report import provider


class _Client:
    def close(self):
        pass


def _archive(request, *, client, extension_revision):
    return {"schema_version": provider.COMMIT_SCHEMA, "echo": request["note"]}


provider._client = lambda args: _Client()
provider.archive_request = _archive
raise SystemExit(provider.main())
"""


def _history_export_entrypoint(note: str) -> str:
    # `ascii` keeps the inline program ASCII-only so the interpreter never has
    # to decode the payload from an argument string.
    return (
        "from loopx.extensions.openviking_semantic_preference import history_export\n"
        f"NOTE = {ascii(note)}\n"
        "\n"
        "\n"
        "def _export(**kwargs):\n"
        "    return {\n"
        '        "public_projection": {"note": NOTE},\n'
        '        "local_receipt": {"out_dir": str(kwargs["out_dir"])},\n'
        "    }\n"
        "\n"
        "\n"
        "history_export.export_goal_conclusions = _export\n"
        "raise SystemExit(history_export.main())\n"
    )


def _environment() -> dict[str, str]:
    return {**os.environ, "PYTHONIOENCODING": LOCALE_CODEC, "PYTHONUTF8": "0"}


def _run_entrypoint(
    program: str, *argv: str, stdin: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-c", program, *argv],
        cwd=REPO_ROOT,
        env=_environment(),
        input=stdin,
        capture_output=True,
        timeout=180,
    )


def _request_bytes(payload: dict[str, object]) -> bytes:
    """Raw UTF-8 request bytes that keep non-ASCII literal, not ASCII-escaped."""

    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assert NON_ASCII_TEXT.encode("utf-8") in raw
    return raw


def test_semantic_preference_provider_decodes_a_non_ascii_request(
    tmp_path: Path,
) -> None:
    """The provider must read its UTF-8 request instead of the locale codec."""

    result = _run_entrypoint(
        _SEMANTIC_PREFERENCE_ENTRYPOINT,
        "--project",
        str(tmp_path),
        "--loopx-project-id",
        "demo",
        stdin=_request_bytes(
            {"schema_version": _REQUEST_SCHEMA, "query": NON_ASCII_TEXT, "limit": 3}
        ),
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["schema_version"] == _RESPONSE_SCHEMA
    assert payload["items"][0]["summary"] == NON_ASCII_TEXT


def test_periodic_report_provider_round_trips_non_ascii_stream() -> None:
    """Both request decode and result encode must use UTF-8, not the locale."""

    result = _run_entrypoint(
        _PERIODIC_REPORT_ENTRYPOINT,
        stdin=_request_bytes({"note": NON_ASCII_TEXT}),
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["echo"] == NON_ASCII_TEXT


def test_history_export_writes_non_ascii_stdout(tmp_path: Path) -> None:
    """The exporter's result must encode as UTF-8 rather than the locale codec."""

    out_dir = tmp_path / NON_ASCII_TEXT
    result = _run_entrypoint(
        _history_export_entrypoint(NON_ASCII_TEXT),
        "--goal-id",
        "demo",
        "--registry-path",
        str(tmp_path / "registry.json"),
        "--runtime-root",
        str(tmp_path / "runtime"),
        "--out-dir",
        str(out_dir),
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["public_projection"]["note"] == NON_ASCII_TEXT
    assert payload["local_receipt"]["out_dir"] == str(out_dir)
