"""The Codex App automation helpers must read the CLI's UTF-8 output, not the host codec.

`scripts/codex_app_apply_rrule.py` shells out to the shipped `loopx` CLI and parses
its JSON. The CLI pins its own stdio to UTF-8 (`loopx/entrypoint.py` ->
`pin_utf8_stdio`), so its stdout bytes are UTF-8 whatever the host locale is. These
helpers read that stdout with `subprocess.run(text=True)` and no `encoding`, so the
stream is decoded with the host code page - `cp936` on a zh-CN Windows host.

On such a host the outcome is not a mojibake warning but a dead automation path:

* the JSON payload carries non-ASCII prompt text, the host codec raises
  `UnicodeDecodeError` inside CPython's reader thread, and CPython swallows a
  reader-thread exception - `subprocess.run` returns with `stdout=None` instead of
  raising. The `except json.JSONDecodeError` guards therefore never run, and the
  failure surfaces as a bare `TypeError: ... NoneType` from `json.loads`.
* a failing CLI prints its reason to stdout; the same swallowed exception turns
  that reason into `stdout=None`, which `_subprocess_failure_detail` renders as
  "no diagnostic output" - the operator is told the CLI was silent when it was not.

Both cases hide a real failure behind a false one.

The rows below drive the real helper functions against a real child process, since a
faked `CompletedProcess` cannot reproduce a decoder failure in CPython's reader
thread. The child is the `loopx` stand-in and pins its own stdout to UTF-8 under
`PYTHONIOENCODING=gbk`, which is the same contract the shipped CLI has; the parent
*is* the interpreter running pytest, so its codec is the host one. That keeps the
row meaningful on every platform rather than only on a zh-CN Windows host.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from scripts.codex_app_apply_rrule import (
    _heartbeat_task_body,
    _load_scheduler_hint,
    _run_ack,
    _subprocess_failure_detail,
)

# A codec that cannot decode the prompt text below, so a missing parent-side
# `encoding=` raises instead of quietly producing readable-looking output.
# `gbk` rejects the three-byte UTF-8 sequence that names the target, whereas
# `cp936` would decode it - asserting on `cp936` would make the row pass on
# Windows for the wrong reason.
LOCALE_CODEC = "gbk"

# Non-ASCII text as it appears in a real `loopx heartbeat-prompt` task body. Kept
# literal in the payload: a `\uXXXX` escape decodes identically under every codec
# and would hide a missing pin.
PROMPT_TEXT = "Advance `loopx-meta` from Agent: `codex-fixture` 目标已锁定"

TASK_BODY_PAYLOAD = {"ok": True, "task_body": PROMPT_TEXT}
HINT_PAYLOAD = {
    "ok": True,
    "scheduler_hint": {
        "codex_app": {
            "recommended_rrule": "FREQ=MINUTELY;INTERVAL=5",
            # Carried through real `should-run` output for a Chinese-labeled Goal;
            # without it the payload decodes identically under every codec.
            "state_summary": "目标已锁定",
            "stateful_backoff": {
                "apply_needed": False,
                "ack_needed": False,
                "current_rrule": "FREQ=MINUTELY;INTERVAL=3",
            },
            "ack_hint": None,
        }
    },
}
ACK_REFUSAL = "quota denied 目标已锁定"

# The encoding the fake `loopx` prints with - the same one the shipped CLI pins
# itself to in `loopx/entrypoint.py`.
CLI_STDIO_ENCODING = "utf-8"

_FAKE_CLI = """\
import json, sys
PAYLOADS = {payloads!r}
argv = sys.argv[1:]
reconfigure = getattr(sys.stdout, "reconfigure", None)
if reconfigure is not None:
    # Same child-side contract as loopx/entrypoint.py: pin_utf8_stdio().
    reconfigure(encoding="utf-8")
if "should-run" in argv:
    print(json.dumps(PAYLOADS["hint"], ensure_ascii=False))
elif "heartbeat-prompt" in argv:
    print(json.dumps(PAYLOADS["task_body"], ensure_ascii=False))
elif "--stdout-error" in argv:
    sys.stdout.write(PAYLOADS["ack_refusal"] + "\\n")
    sys.exit(3)
else:
    print("ack ok")
""".format(
    payloads={
        "task_body": TASK_BODY_PAYLOAD,
        "hint": HINT_PAYLOAD,
        "ack_refusal": ACK_REFUSAL,
    }
)


@pytest.fixture
def fake_loopx(tmp_path: Path) -> str:
    """A real executable standing in for the `loopx` console entry.

    Written as a `.cmd` on Windows and a shebang script elsewhere so the helper's
    single-string `loopx` argument can be spawned directly, the way it spawns the
    installed CLI.
    """

    cli = tmp_path / "loopx-cli.py"
    cli.write_text(_FAKE_CLI, encoding="utf-8")
    # The child starts on the locale codec and pins UTF-8 itself, which is the
    # shipped CLI contract under test.
    child_env = {"PYTHONIOENCODING": LOCALE_CODEC, "PYTHONUTF8": "0"}
    if os.name == "nt":
        launcher = tmp_path / "loopx.cmd"
        launcher.write_text(
            "@echo off\r\n"
            + "".join(f"set {key}={value}\r\n" for key, value in child_env.items())
            + f'"{sys.executable}" -I "{cli}" %*\r\n',
            encoding="utf-8",
        )
    else:
        launcher = tmp_path / "loopx"
        assignments = " ".join(f"{key}={value}" for key, value in child_env.items())
        launcher.write_text(
            f"#!/bin/sh\n{assignments} exec \"{sys.executable}\" -I \"{cli}\" \"$@\"\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)
    return str(launcher)


@pytest.fixture(autouse=True)
def _host_codec_is_the_locale_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the parent's decode codec the host locale one.

    `subprocess` decodes the child's bytes with `locale.getpreferredencoding(False)`
    unless `encoding` is passed. That honours `PYTHONIOENCODING`, so a CI default of
    UTF-8 must be cleared for the row to mean anything; clearing it does not make a
    gbk host gbk, it only stops UTF-8 from masking a missing pin.
    """

    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)


def _registry(tmp_path: Path) -> Path:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": []}), encoding="utf-8")
    return registry


def test_heartbeat_prompt_reads_utf8_task_body_under_a_locale_codec(
    tmp_path: Path, fake_loopx: str
) -> None:
    """The prompt body must reach the automation instead of collapsing to `None`."""

    body = _heartbeat_task_body(
        loopx=fake_loopx,
        registry=_registry(tmp_path),
        goal_id="loopx-meta",
        agent_id="codex-fixture",
    )

    assert body == PROMPT_TEXT


def test_scheduler_hint_reads_utf8_json_under_a_locale_codec(
    tmp_path: Path, fake_loopx: str
) -> None:
    """`should-run` must yield a usable hint rather than a `TypeError` on None."""

    codex_app, stateful = _load_scheduler_hint(
        loopx=fake_loopx,
        registry=_registry(tmp_path),
        goal_id="loopx-meta",
        agent_id="codex-fixture",
        parent_turn_instance_id="heartbeat-turn-3231",
        capabilities=[],
    )

    assert codex_app["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=5"
    assert codex_app["state_summary"] == HINT_PAYLOAD["scheduler_hint"]["codex_app"]["state_summary"]
    assert stateful["current_rrule"] == "FREQ=MINUTELY;INTERVAL=3"


def test_failing_cli_keeps_its_stdout_reason_under_a_locale_codec(
    tmp_path: Path, fake_loopx: str
) -> None:
    """A refused ACK must report the reason, not claim the CLI printed nothing."""

    with pytest.raises(SystemExit) as raised:
        _run_ack(fake_loopx, ["ack", "--stdout-error"])

    detail = str(raised.value)
    assert ACK_REFUSAL in detail
    assert "no diagnostic output" not in detail


def test_failure_detail_separates_undecoded_from_silent() -> None:
    """An undecoded stream must not be reported as "nothing was printed".

    `None` is what CPython leaves behind when its reader thread's decode raises;
    `""` is what a genuinely silent process produces.
    """

    class _Completed:
        returncode = 1

        def __init__(self, stdout: str | None) -> None:
            self.stdout = stdout
            self.stderr = ""

    silent = _Completed("")
    undecoded = _Completed(None)

    assert _subprocess_failure_detail(undecoded) != _subprocess_failure_detail(silent)
    assert "no diagnostic output" not in _subprocess_failure_detail(undecoded)
    assert _subprocess_failure_detail(silent) == "no diagnostic output"


def test_locale_codec_actually_rejects_the_fixture_text() -> None:
    """The fixture must be able to fail.

    Without this row a host whose codec accepts the prompt text would turn the
    rows above green whether or not the pin exists.
    """

    try:
        codec = __import__("codecs").lookup(LOCALE_CODEC)
    except LookupError:  # pragma: no cover - gbk ships with CPython
        pytest.skip(f"{LOCALE_CODEC} unavailable")
    for payload in (TASK_BODY_PAYLOAD, HINT_PAYLOAD):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with pytest.raises(UnicodeDecodeError):
            codec.decode(raw)
    assert CLI_STDIO_ENCODING == "utf-8"
