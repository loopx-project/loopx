"""Codex session evidence must survive a host default encoding that is not UTF-8.

The shipped Turn driver reads the Codex CLI's UTF-8 JSONL event stream and
persists its checkpoint-refresh attempt record as UTF-8 JSON. Both go through
`Path.open()` / `os.fdopen()`, which the shipped file gate
(`tests/test_loopx_text_io_utf8.py`) does not inspect, so nothing stopped the
host locale codec from rewriting them.

Each case drives one real owner in a child interpreter whose default encoding is
not UTF-8 (the host ANSI code page on Windows, `ascii` under `LC_ALL=C` on
POSIX). The child's workspace stays ASCII: the forced filesystem encoding must be
able to decode the `str` paths the owner hands to file I/O, so a non-ASCII
workspace path would fail on the surrogate escape first and hide the codec
hazard this suite exists to reproduce. The non-ASCII payload is the CLI's own
UTF-8 event bytes and the attempt record, which is what the codecs under test
decode.

The program travels on stdin rather than as a `-c` argument: `-c` text is
decoded with the filesystem encoding, so under `LC_ALL=C` CPython rejects the
program before it imports LoopX ("Unable to decode the command from the command
line"), while source read from stdin is decoded as UTF-8. `-` keeps the working
directory on `sys.path` the way `-c` does.

The child also proves the case is not vacuous: it decodes the exact bytes the
CLI writes with its resolved default codec and reports whether they are
rejected. A single-byte codec such as `latin-1` or `cp1251` accepts those bytes,
so on such a host the read succeeds with mojibake instead of raising and this
regression cannot be shown; the case skips there rather than claim coverage.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_CHILD = r'''
import json, locale, sys
from pathlib import Path
from types import SimpleNamespace


def events_bytes(identity):
    # The Codex CLI writes its event stream as UTF-8 whatever the host codec is.
    return b"".join([
        json.dumps({"type": "item.completed", "text": "任务已完成"},
                   ensure_ascii=False).encode("utf-8") + b"\n",
        json.dumps({"type": "thread.started", "thread_id": identity}).encode("utf-8") + b"\n",
    ])


locale.setlocale(locale.LC_CTYPE, "C")
_codec = locale.getpreferredencoding(False)
assert sys.flags.utf8_mode == 0
assert _codec.lower().replace("-", "") != "utf8"
try:
    events_bytes("locale-probe").decode(_codec)
except UnicodeDecodeError:
    locale_hazard = True
else:
    # A single-byte codec decodes the sample without raising, so the defective
    # default-codec read would return mojibake instead of failing. Say so
    # instead of letting the case pass for the wrong reason.
    locale_hazard = False

from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
from loopx.control_plane.turn_driver.codex_sessions import (
    _store_codex_cli_session, load_codex_cli_session,
)

base, mode = Path(sys.argv[1]), sys.argv[2]
review = {"identity": {"effect_id": "current-review"}, "decision_sha256": "c" * 64}


def session(root):
    subject = CodexAgentSession.__new__(CodexAgentSession)
    subject.root = root
    subject.lineage = {"goal_id": "destination", "agent_id": "worker"}
    subject.goal_ref = {"goal_id": "destination", "instance_id": "current"}
    subject.digest = "b" * 64
    _store_codex_cli_session(root, lineage=subject.lineage, session_scope="agent",
        session_id="original", goal_ref=subject.goal_ref, session_profile_digest=subject.digest,
        checkpoint_artifact_sha256="a" * 64, checkpoint_artifact_root=str(root))
    subject.admission = SimpleNamespace(
        require_current=lambda: None, accept_result=lambda callback: callback())
    subject.binding = load_codex_cli_session(root, lineage=subject.lineage, session_scope="agent")
    subject.session_id = "original"
    subject.receipt = {"session": {"session_id": "original"}}
    subject.checkpoint_refresh = None
    return subject


def events(path, identity):
    path.write_bytes(events_bytes(identity))


root = base / mode / "checkout"
root.mkdir(parents=True)
subject = session(root)

if mode == "observe":
    # The refresh is prepared first, then the CLI's own UTF-8 events are read.
    subject.prepare_checkpoint_refresh(review, root / "attempt.json", root / "stdout.jsonl")
    events(root / "stdout.jsonl", "fresh")
    subject.observe(root / "stdout.jsonl")
    payload = {"session_id": subject.session_id}
else:
    # The durable record carries the workspace and stdout paths; the shipped
    # recovery read is `json.loads(attempt.read_bytes())`.
    attempt, retry = root / "attempt.json", root / "retry.jsonl"
    subject.prepare_checkpoint_refresh(review, attempt, root / "stdout.jsonl")
    events(root / "stdout.jsonl", "resumed")
    subject.prepare_checkpoint_refresh(review, attempt, retry)
    payload = {"session_id": subject.session_id,
               "attempt_stdout": json.loads(attempt.read_bytes())["stdout"],
               "retry_launched": retry.exists()}

print(json.dumps({"encoding": _codec, "locale_hazard": locale_hazard, **payload},
                 ensure_ascii=True))
'''


def _drive(base: Path, mode: str) -> subprocess.CompletedProcess[bytes]:
    environment = {
        **os.environ,
        "LC_ALL": "C",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "0",
    }
    return subprocess.run(
        [sys.executable, "-X", "utf8=0", "-", str(base), mode],
        cwd=REPO_ROOT,
        env=environment,
        # `-c` arguments are decoded with the ASCII filesystem encoding this
        # environment forces, which rejects a non-ASCII program before LoopX is
        # imported; stdin source is decoded as UTF-8 and keeps the working
        # directory on `sys.path`, as `-c` would.
        input=_CHILD.encode("utf-8"),
        capture_output=True,
        timeout=180,
    )


def _payload(base: Path, mode: str) -> dict:
    result = _drive(base, mode)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    payload = json.loads(result.stdout.decode("utf-8"))
    # A vacuous pass would hide the codec this test exists to exercise. The host
    # default must not be UTF-8, and it must reject the exact bytes the CLI
    # writes; a permissive single-byte codec decodes them without raising, so the
    # defective read would return mojibake and the assertions below would hold
    # with the fix reverted.
    assert payload["encoding"].lower().replace("-", "") != "utf8"
    if not payload["locale_hazard"]:
        pytest.skip(
            f"host default codec {payload['encoding']} decodes the UTF-8 event bytes; "
            "the default-codec read hazard cannot be reproduced on this host"
        )
    return payload


def test_codex_events_are_read_back_with_a_locale_codec(tmp_path: Path) -> None:
    """The CLI's UTF-8 event stream survives a non-UTF-8 default codec.

    Covers the `CodexAgentSession.observe` read only: reverting its codec pin
    makes this case fail with the reproduced `UnicodeDecodeError`.
    """

    base = tmp_path
    payload = _payload(base, "observe")

    assert payload["session_id"] == "fresh"


def test_checkpoint_refresh_attempt_record_is_readable_again(tmp_path: Path) -> None:
    """The recovery wake re-reads its original output and its attempt record.

    Covers the recovery path's reads — `prepare_checkpoint_refresh` re-runs the
    `observe` read on the recorded stdout path, and the record itself is re-read
    as UTF-8 bytes. It does not cover the two `os.fdopen` write pins: their
    payload is ASCII today because `json.dump` escapes non-ASCII by default, so
    reverting those pins leaves this case green. The gate rule in
    `tests/test_loopx_text_io_utf8.py` is what protects them.
    """

    base = tmp_path
    payload = _payload(base, "recover")

    assert payload["session_id"] == "resumed"
    assert payload["attempt_stdout"] == str(base / "recover" / "checkout" / "stdout.jsonl")
    assert payload["retry_launched"] is False
