"""The Claude Code goal-mode gate must refuse an event it cannot read.

``hooks/goal_policy.py`` documents itself as fail-closed around the
deterministic ``should_run`` probe: when the probe cannot be reached, a
non-read-only tool is denied so the gate never silently disappears. The event
read was the one boundary outside that rule. With the strict stdin pin
(``test_claude_goal_mode_stdio_utf8.py``) an undecodable byte raises
``UnicodeDecodeError`` at the read, and a decodable-but-malformed payload raised
inside ``active_context``: the hook exited ``rc=1`` with a traceback and printed
no decision. Claude Code treats a hook that fails that way as a non-blocking
error and continues the tool call, so the gate vanished exactly when the hook
could not verify anything. #6166 named this as its deliberately deferred
successor.

The same read boundary also covers values that decode but cannot be used: a
session ``cwd`` string that is not a resolvable path (an embedded NUL raises
``ValueError`` inside ``active_context``), a missing/non-string ``tool_name``,
a falsy non-object ``tool_input`` (``[]`` / ``false`` / ``0`` / ``""``, which
the old ``or {}`` coerced before the type check), and a non-string
``command`` / ``file_path`` / ``notebook_path`` on a gated tool. Those are
refused the same way; the read-only exception and the absent-``tool_input``
default are pinned so the refusal does not widen onto valid events.

These cases run the shipped script the way Claude Code invokes it - own process,
event on stdin, answer read back as JSON - and assert the rc/decision contract,
so the regression fails on the real entry point rather than on an in-process
approximation. The decision-level cases drive ``decide`` directly against an
armed context with the gate open (``should_run`` stubbed true), which is what
made the falsy-coercion failure visible. The unarmed case pins the other
direction: the refusal must not widen the gate onto projects where goal-mode is
off.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "loopx/claude_goal_mode/hooks/goal_policy.py"

GOAL_ID = "hook-fail-closed-goal"


def _project(root: Path, *, armed: bool) -> Path:
    """A project with one registry goal; armed also carries `.claude/loop.md`."""

    registry = root / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(root),
                        "coordination": {"registered_agents": ["cc"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    if armed:
        (root / ".claude").mkdir(parents=True, exist_ok=True)
        (root / ".claude" / "loop.md").write_text("# loop\n", encoding="utf-8")
    return root


def _run(
    project: Path, payload: bytes, *, force_armed: bool
) -> subprocess.CompletedProcess[bytes]:
    environment = {**os.environ}
    if force_armed:
        environment["LOOPX_GOAL_FORCE"] = "1"
    else:
        environment.pop("LOOPX_GOAL_FORCE", None)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=project,
        env=environment,
        input=payload,
        capture_output=True,
        timeout=120,
    )


UNREADABLE_EVENT_PAYLOADS = [
    pytest.param(b"\xff\xfe{", id="undecodable-stdin"),
    pytest.param(b"{not json", id="malformed-json"),
    pytest.param(b"[]", id="json-not-an-object"),
    pytest.param(b"", id="empty-stdin"),
    pytest.param(
        json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}).encode(
            "utf-8"
        ),
        id="object-without-cwd",
    ),
    pytest.param(
        json.dumps({"tool_name": "Bash", "workspace": "not-an-object"}).encode("utf-8"),
        id="non-object-workspace",
    ),
]

UNUSABLE_CWD_EVENTS = [
    pytest.param(
        {
            "tool_name": "Bash",
            "cwd": "/bad\u0000cwd",
            "tool_input": {"command": "ls"},
        },
        id="cwd-nul-byte",
    ),
    pytest.param(
        {
            "tool_name": "Bash",
            "workspace": {"current_dir": "/bad\u0000cwd"},
            "tool_input": {"command": "ls"},
        },
        id="workspace-current-dir-nul-byte",
    ),
]

FALSY_NON_OBJECT_TOOL_INPUTS = [
    pytest.param([], id="empty-list"),
    pytest.param(False, id="false"),
    pytest.param(0, id="zero"),
    pytest.param("", id="empty-string"),
]

UNREADABLE_EVENT_VALUES = [
    pytest.param(
        {"tool_name": ["Bash"], "tool_input": {"command": "ls"}},
        "tool_name",
        id="non-string-tool-name",
    ),
    pytest.param(
        {"tool_name": "Bash", "tool_input": {"command": ["rm", "/"]}},
        "command",
        id="non-string-command",
    ),
    pytest.param(
        {"tool_name": "Write", "tool_input": {"file_path": ["/proj/notes.md"]}},
        "file_path",
        id="non-string-file-path",
    ),
    pytest.param(
        {
            "tool_name": "NotebookEdit",
            "tool_input": {"notebook_path": {"p": "/proj/n.ipynb"}},
        },
        "notebook_path",
        id="non-string-notebook-path",
    ),
]

ARMED_CTX = {
    "goal_id": GOAL_ID,
    "registry": "/tech/registry.json",
    "agent_id": "cc",
    "write_scope": ["/proj"],
    "project_root": "/proj",
}


@pytest.mark.parametrize("payload", UNREADABLE_EVENT_PAYLOADS)
def test_unreadable_event_is_refused_with_an_explicit_deny(
    tmp_path: Path, payload: bytes
) -> None:
    project = _project(tmp_path / "armed", armed=True)

    result = _run(project, payload, force_armed=True)

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert b"Traceback" not in result.stderr, result.stderr.decode("utf-8", "replace")
    decision = json.loads(result.stdout.decode("utf-8"))
    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    assert output["permissionDecisionReason"]


@pytest.mark.parametrize("event", UNUSABLE_CWD_EVENTS)
def test_unusable_cwd_is_refused_with_an_explicit_deny(
    tmp_path: Path, event: dict
) -> None:
    """A decoded cwd string can still be unusable as a path.

    ``Path.resolve`` on a string with an embedded NUL raises ``ValueError``
    inside ``active_context``, so the pre-fix hook exited rc=1 with no decision
    and a traceback - the same fail-open signature the unreadable-event refusal
    removes. The reason substring pins the refusal to the usable-path rule, so
    a deny from another branch (for example the closed gate) cannot satisfy the
    regression.
    """

    project = _project(tmp_path / "armed", armed=True)

    result = _run(project, json.dumps(event).encode("utf-8"), force_armed=True)

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert b"Traceback" not in result.stderr, result.stderr.decode("utf-8", "replace")
    output = json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "usable path" in output["permissionDecisionReason"]


def test_non_object_tool_input_for_a_gated_tool_is_refused(tmp_path: Path) -> None:
    """A truthy non-object `tool_input` is an unreadable event, not a crash.

    ``decide`` reads the tool input with ``ti.get`` for the gated tools, so on
    the pre-fix hook a Bash event carrying a non-object input died with a
    traceback (rc=1, no decision) as soon as ``should_run`` returned true. The
    reason substring pins the refusal to the unreadable-input rule: a deny from
    the probe path carries a different reason, so the regression fails against
    the old hook whichever branch the probe takes.
    """

    project = _project(tmp_path / "armed", armed=True)
    event = json.dumps(
        {
            "tool_name": "Bash",
            "cwd": str(project),
            "tool_input": "not-an-object",
        }
    ).encode("utf-8")

    result = _run(project, event, force_armed=True)

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert b"Traceback" not in result.stderr, result.stderr.decode("utf-8", "replace")
    output = json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "tool_input" in output["permissionDecisionReason"]

    # Read-only tools keep their unconditional allow even then: the refusal
    # must not widen the gate past the tools it was meant to gate.
    read_event = json.dumps(
        {
            "tool_name": "Read",
            "cwd": str(project),
            "tool_input": "not-an-object",
        }
    ).encode("utf-8")
    read_result = _run(project, read_event, force_armed=True)
    assert read_result.returncode == 0, read_result.stderr.decode("utf-8", "replace")
    read_output = json.loads(read_result.stdout.decode("utf-8"))["hookSpecificOutput"]
    assert read_output["permissionDecision"] == "allow"


@pytest.mark.parametrize("tool_input", FALSY_NON_OBJECT_TOOL_INPUTS)
def test_falsy_non_object_tool_input_is_refused_not_coerced(
    tmp_path: Path, tool_input
) -> None:
    """``tool_input`` must be type-checked before any default is applied.

    The pre-review hook read ``ev.get("tool_input", {}) or {}``, so a present
    falsy non-object (``[]`` / ``false`` / ``0`` / ``""``) became ``{}`` before
    the non-object check; with an open gate the pre-review ``decide`` answered
    "allow / bash permitted under goal policy" for it. The reason substring
    proves the deny comes from the unreadable-input rule rather than the
    closed-gate branch.
    """

    project = _project(tmp_path / "armed", armed=True)
    event = json.dumps(
        {"tool_name": "Bash", "cwd": str(project), "tool_input": tool_input}
    ).encode("utf-8")

    result = _run(project, event, force_armed=True)

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert b"Traceback" not in result.stderr, result.stderr.decode("utf-8", "replace")
    output = json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "tool_input" in output["permissionDecisionReason"]


@pytest.mark.parametrize("partial,field", UNREADABLE_EVENT_VALUES)
def test_unreadable_event_value_is_refused_with_an_explicit_deny(
    tmp_path: Path, partial: dict, field: str
) -> None:
    """A decoded event value the gate cannot use is refused, never a traceback.

    A non-string ``tool_name`` crashed the set membership tests, a non-string
    ``command`` crashed ``.lower()``, and a non-string file path silently
    resolved to the project root - all variations of the same fail-open event
    that the gate must refuse instead.
    """

    project = _project(tmp_path / "armed", armed=True)
    event = json.dumps({"cwd": str(project), **partial}).encode("utf-8")

    result = _run(project, event, force_armed=True)

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert b"Traceback" not in result.stderr, result.stderr.decode("utf-8", "replace")
    output = json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert field in output["permissionDecisionReason"]


@pytest.fixture()
def hook_policy():
    """The shipped hook module, imported in-process for ``decide`` cases."""

    hooks_dir = REPO_ROOT / "loopx" / "claude_goal_mode" / "hooks"
    if str(hooks_dir) not in sys.path:
        sys.path.insert(0, str(hooks_dir))
    import goal_policy

    return goal_policy


@pytest.fixture()
def armed_decide(hook_policy, monkeypatch):
    """``decide`` against an armed project with the gate OPEN (should_run=true)."""

    monkeypatch.setattr(hook_policy, "active_context", lambda cwd: ARMED_CTX)
    monkeypatch.setattr(hook_policy, "should_run", lambda *a, **k: True)
    return hook_policy.decide


def test_decide_refuses_an_unusable_cwd_before_resolving_the_project(
    hook_policy, monkeypatch
) -> None:
    def never_active(cwd):
        pytest.fail(f"active_context received an unusable cwd: {cwd!r}")

    monkeypatch.setattr(hook_policy, "active_context", never_active)

    output = hook_policy.decide(
        {"cwd": "/bad\u0000cwd", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    )["hookSpecificOutput"]

    assert output["permissionDecision"] == "deny"
    assert "usable path" in output["permissionDecisionReason"]


def test_decide_keeps_the_empty_input_default_for_an_absent_tool_input(
    armed_decide,
) -> None:
    """Only an absent/null field takes the permitted ``{}`` default."""

    output = armed_decide({"cwd": "/proj", "tool_name": "Bash"})["hookSpecificOutput"]

    assert output["permissionDecision"] == "allow"
    assert output["permissionDecisionReason"] == "bash permitted under goal policy"


@pytest.mark.parametrize("tool_input", FALSY_NON_OBJECT_TOOL_INPUTS)
def test_decide_refuses_falsy_non_object_tool_input_when_the_gate_is_open(
    armed_decide, tool_input
) -> None:
    """With should_run=true the pre-review ``decide`` allowed these (P2)."""

    output = armed_decide(
        {"cwd": "/proj", "tool_name": "Bash", "tool_input": tool_input}
    )["hookSpecificOutput"]

    assert output["permissionDecision"] == "deny"
    assert "tool_input" in output["permissionDecisionReason"]


@pytest.mark.parametrize("partial,field", UNREADABLE_EVENT_VALUES)
def test_decide_refuses_unreadable_event_values_when_the_gate_is_open(
    armed_decide, partial: dict, field: str
) -> None:
    output = armed_decide({"cwd": "/proj", **partial})["hookSpecificOutput"]

    assert output["permissionDecision"] == "deny"
    assert field in output["permissionDecisionReason"]


def test_decide_keeps_the_read_only_exception_for_unreadable_input(
    armed_decide,
) -> None:
    """The gated-tool refusal must not capture read-only tools."""

    output = armed_decide(
        {"cwd": "/proj", "tool_name": "Read", "tool_input": []}
    )["hookSpecificOutput"]

    assert output["permissionDecision"] == "allow"
    assert output["permissionDecisionReason"] == "read-only/safe tool under goal-mode"


def test_unarmed_project_still_defers_to_the_normal_flow(tmp_path: Path) -> None:
    project = _project(tmp_path / "unarmed", armed=False)

    # A valid event in a project with a registry goal but no `.claude/loop.md`
    # (goal-mode off): its write tool must still defer to Claude Code's normal
    # permission flow, not be captured by the new refusal.
    event = json.dumps(
        {
            "tool_name": "Edit",
            "cwd": str(project),
            "tool_input": {"file_path": str(project / "notes.txt")},
        }
    ).encode("utf-8")
    result = _run(project, event, force_armed=False)

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    # `{}` is Claude Code's "defer to the normal permission flow".
    assert result.stdout == b"{}"
