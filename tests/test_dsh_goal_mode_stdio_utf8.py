"""The DSH stdio host adapter must pin its own stdio to UTF-8.

LoopX starts this adapter as a subprocess (`python -m loopx.dsh_goal_mode`, or
the compatible `scripts/dsh_turn_host_adapter.py`) and speaks one UTF-8
`loopx_turn_host_request_v0` JSON object on its stdin and one UTF-8
`loopx_turn_result_v0` object on its stdout. `sys.stdin` / `sys.stdout` default
to the host locale codec instead - `cp936` on a zh-CN Windows host. There, a
signed Turn whose envelope carries non-ASCII text (a Chinese `primary_action`,
a required-read reason, a non-ASCII path inside the workspace guard) is decoded
with `gbk` and rejected (`invalid request JSON on stdin`, rc=2), and a typed
result whose bounded text contains a character outside GBK raises
`UnicodeEncodeError` on the final `print(...)`, so the adapter dies with an
empty stdout.

`tests/test_cli_stdio_utf8.py` guards the shipped CLI's own streams and
`tests/test_claude_goal_mode_stdio_utf8.py` the Claude Code entry scripts; this
file covers the remaining stdio transport of that family. The child is given
`PYTHONIOENCODING=gbk` (plus `PYTHONUTF8=0`) so the locale-codec behavior is
deterministic on any host, including UTF-8 CI, instead of depending on the
runner's ANSI code page.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

from loopx.control_plane.quota.turn_envelope import (
    turn_envelope_action_signature_document,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_ENTRY = [sys.executable, "-m", "loopx.dsh_goal_mode"]
LEGACY_ENTRY = [
    sys.executable,
    str(REPO_ROOT / "scripts" / "dsh_turn_host_adapter.py"),
]
FAKE_RUNNER = REPO_ROOT / "tests" / "dsh_goal_mode_fake_runner.py"

# A signed payload must carry raw non-ASCII bytes: an ASCII-escaped request
# would decode identically under `gbk` and UTF-8 and hide a missing stdin pin.
CHINESE_ACTION = "\u6574\u7406\u5bfc\u51fa\u7ed3\u679c\uff0c\u5b8c\u6210\u4e2d\u6587\u76ee\u6807"
# Not encodable in `gbk`/`cp936`, so a locale-codec stdout is a hard failure.
NON_GBK_GLYPH = "\u2705"
LOCALE_CODEC = "gbk"


def _signed_request(primary_action: str) -> dict:
    """One loopx_turn_host_request_v0 with a matching action signature.

    Kept in the shape `tests/test_dsh_goal_mode.py` builds and the executor's
    `build_loopx_turn_host_request` produces, so the adapter sees a request a
    real Turn could send.
    """

    request = {
        "schema_version": "loopx_turn_host_request_v0",
        "turn_key": "sha256:" + "0" * 64,
        "route": "primary_delivery",
        "session": {"goal_id": "g", "agent_id": "a"},
        "turn_envelope": {
            "schema_version": "loopx_turn_envelope_v0",
            "goal_id": "g",
            "agent_id": "a",
            "action": {
                "recommended_action": "legacy action must not win",
                "primary_action": primary_action,
                "must_attempt": True,
            },
            "required_reads": [
                {
                    "kind": "command",
                    "command": "git status --short",
                    "reason": "establish the workspace baseline",
                }
            ],
            "boundary": {
                "write_scope": ["docs/**", "tests/**"],
                "workspace_guard": {
                    "schema_version": "workspace_guard_v0",
                    "status": "ready",
                    "action": "continue",
                },
            },
            "action_signature": {
                "schema_version": "loopx_action_signature_v0",
                "matches": True,
            },
        },
        "result_contract": {
            "schema_version": "loopx_turn_result_v0",
            "completed_phases": ["host_execute", "typed_result"],
        },
    }
    signature = turn_envelope_action_signature_document(request["turn_envelope"])
    digest = "sha256:" + sha256(
        json.dumps(
            signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    request["turn_envelope"]["action_signature"].update(
        {"source_hash": digest, "envelope_hash": digest}
    )
    return request


def _run(entry: list[str], runner: Path, request: dict, codec: str) -> subprocess.CompletedProcess:
    """Start one adapter subprocess with UTF-8 bytes and the selected codec."""

    environment = {
        **os.environ,
        "PYTHONIOENCODING": codec,
        "PYTHONUTF8": "0",
    }
    payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
    return subprocess.run(
        [*entry, "--dsh-runner", str(runner)],
        cwd=str(REPO_ROOT),
        env=environment,
        input=payload,
        capture_output=True,
        timeout=180,
    )


def _write_non_ascii_runner(tmp_path: Path) -> Path:
    """A hermetic runner whose typed result carries a non-GBK glyph."""

    runner = tmp_path / "dsh_non_ascii_runner.py"
    runner.write_text(
        "import json\n"
        "\n"
        "\n"
        "def run_dsh_turn(**_: object) -> str:\n"
        "    return json.dumps(\n"
        "        {\n"
        '            "result_kind": "validated_progress",\n'
        '            "classification": "segment '
        + NON_GBK_GLYPH
        + ' completed",\n'
        '            "summary": "one bounded segment finished",\n'
        '            "recommended_action": "validate the typed result",\n'
        '            "next_action": "run the LoopX validator",\n'
        '            "vision_unchanged_reason": "the fake runner never replans",\n'
        "        }\n"
        "    )\n",
        encoding="utf-8",
    )
    return runner


def test_module_entry_decodes_a_non_ascii_signed_request_under_a_locale_codec() -> None:
    """The documented module entry must read the UTF-8 request identically."""

    request = _signed_request(CHINESE_ACTION)
    payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
    assert CHINESE_ACTION.encode("utf-8") in payload

    locale_result = _run(MODULE_ENTRY, FAKE_RUNNER, request, LOCALE_CODEC)
    utf8_result = _run(MODULE_ENTRY, FAKE_RUNNER, request, "utf-8")

    assert utf8_result.returncode == 0, utf8_result.stderr
    assert locale_result.returncode == 0, locale_result.stderr.decode("utf-8", "replace")
    assert locale_result.stdout == utf8_result.stdout

    result = json.loads(utf8_result.stdout.decode("utf-8"))
    assert result["result_kind"] == "validated_progress"
    assert result["classification"] == "fake dsh typed result"


def test_legacy_script_launcher_decodes_the_same_request_under_a_locale_codec() -> None:
    """The compatible launcher resolves to the same pinned implementation."""

    request = _signed_request(CHINESE_ACTION)
    legacy_result = _run(LEGACY_ENTRY, FAKE_RUNNER, request, LOCALE_CODEC)
    module_result = _run(MODULE_ENTRY, FAKE_RUNNER, request, LOCALE_CODEC)

    assert legacy_result.returncode == 0, legacy_result.stderr.decode("utf-8", "replace")
    assert legacy_result.stdout == module_result.stdout


def test_typed_result_with_a_non_gbk_character_round_trips_under_a_locale_codec(
    tmp_path: Path,
) -> None:
    """The result JSON must reach the host intact, not die in the last print()."""

    runner = _write_non_ascii_runner(tmp_path)
    request = _signed_request("Do the signed thing.")

    locale_result = _run(MODULE_ENTRY, runner, request, LOCALE_CODEC)
    utf8_result = _run(MODULE_ENTRY, runner, request, "utf-8")

    assert utf8_result.returncode == 0, utf8_result.stderr
    assert locale_result.returncode == 0, locale_result.stderr.decode("utf-8", "replace")
    assert locale_result.stdout == utf8_result.stdout

    result = json.loads(locale_result.stdout.decode("utf-8"))
    assert NON_GBK_GLYPH in result["classification"]
