"""The Claude Code goal-mode entry scripts must pin their own stdio to UTF-8.

Claude Code pipes the PreToolUse event, the statusline session JSON and the
`/loopx` command's arguments as UTF-8, and reads every answer as UTF-8.
`sys.stdin` / `sys.stdout` inside these scripts default to the host locale codec
instead - `cp936` on a zh-CN Windows host. Under that codec the PreToolUse gate
decodes a non-ASCII event into mojibake, misses the project goal and prints `{}`,
so the should_run / write_scope gate silently fails OPEN for every tool, and the
statusline and the `/loopx` state line cannot encode their own `▶` / `⏸` / `⚠`
glyphs, so they render nothing or die with `UnicodeEncodeError`.

`test_cli_stdio_utf8.py` guards the shipped CLI's own streams, and
`test_loopx_text_io_utf8.py` / `test_runtime_subprocess_utf8.py` guard the files
and subprocess pipes LoopX opens. The entry scripts this plugin launches itself -
the gate, the statusline and the `/loopx` command - are the remaining
locale-dependent surface, so they are covered here.

The fixtures keep the project path and the goal id non-ASCII, so a missing pin
fails on the decode side (the event's `cwd` becomes mojibake, so the armed goal
is no longer found) *and* on the encode side. A non-ASCII goal id exercises that
encode path whichever branch the `should_run` probe takes, since that probe is
not reachable on every host.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "loopx/claude_goal_mode/hooks/goal_policy.py"
STATUSLINE = REPO_ROOT / "loopx/claude_goal_mode/statusline/goal_status.py"
GOALMODE = REPO_ROOT / "loopx/claude_goal_mode/scripts/goalmode_cmd.py"

# A codec the scripts must not fall back to. `gbk` cannot encode the statusline
# glyphs and decodes the UTF-8 project path below into mojibake, so a missing pin
# is a hard, observable failure rather than a silent byte difference.
LOCALE_CODEC = "gbk"
PROJECT_NAME = "项目"
GOAL_ID = "目标-goal"
GLYPHS = ("▶", "⏸", "⚠")


def _arm(project: Path) -> Path:
    """An armed goal-mode project: one registry goal plus the FORCE test switch."""

    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "coordination": {"registered_agents": ["cc"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project


def _run(
    script: Path, project: Path, payload: bytes, codec: str, *args: str
) -> subprocess.CompletedProcess[bytes]:
    environment = {
        **os.environ,
        "LOOPX_GOAL_FORCE": "1",
        "PYTHONIOENCODING": codec,
        "PYTHONUTF8": "0",
    }
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=project,
        env=environment,
        input=payload,
        capture_output=True,
        timeout=120,
    )


def test_pretooluse_gate_resolves_the_armed_goal_under_a_locale_codec(tmp_path: Path) -> None:
    """The gate must read a non-ASCII event identically on any locale codec."""

    project = _arm(tmp_path / PROJECT_NAME)
    event = {"tool_name": "Read", "cwd": str(project)}
    payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
    # The fixture must carry raw non-ASCII bytes: ASCII-escaped input would decode
    # identically under `gbk` and UTF-8 and hide a missing stdin pin.
    assert PROJECT_NAME.encode("utf-8") in payload

    locale_result = _run(HOOK, project, payload, LOCALE_CODEC)
    utf8_result = _run(HOOK, project, payload, "utf-8")

    assert utf8_result.returncode == 0, utf8_result.stderr.decode("utf-8", "replace")
    assert locale_result.returncode == 0, locale_result.stderr.decode("utf-8", "replace")
    assert locale_result.stdout == utf8_result.stdout

    decision = json.loads(utf8_result.stdout.decode("utf-8"))
    assert decision["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_pretooluse_write_is_not_waved_through_under_a_locale_codec(tmp_path: Path) -> None:
    """A write tool must not come back as the silent `{}` fail-open no-op.

    `{}` is Claude Code's "defer to the normal permission flow", i.e. no gate at
    all. Before the stdio pin the hook lost the armed goal on a non-ASCII event
    and returned exactly that for every tool, so Edit/Write/Bash ran ungated.
    """

    project = _arm(tmp_path / PROJECT_NAME)
    event = {
        "tool_name": "Write",
        "cwd": str(project),
        "tool_input": {"file_path": str(project / "notes.txt")},
    }
    payload = json.dumps(event, ensure_ascii=False).encode("utf-8")

    result = _run(HOOK, project, payload, LOCALE_CODEC)

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout != b"{}", result.stdout
    decision = json.loads(result.stdout.decode("utf-8"))
    assert decision["hookSpecificOutput"]["permissionDecision"] in {"allow", "deny"}


def test_slash_command_status_survives_a_locale_codec(tmp_path: Path) -> None:
    """`/loopx status` is shown to the user verbatim, glyphs included."""

    project = _arm(tmp_path / PROJECT_NAME)

    locale_result = _run(GOALMODE, project, b"", LOCALE_CODEC, "status")
    utf8_result = _run(GOALMODE, project, b"", "utf-8", "status")

    for result in (locale_result, utf8_result):
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        rendered = result.stdout.decode("utf-8")
        assert GOAL_ID in rendered, rendered
        # Every state line carries one of these, whichever way the probe went.
        assert any(g.encode("utf-8") in result.stdout for g in GLYPHS), rendered


def _unencodable(text: str, codec: str) -> list[str]:
    """The characters of `text` the codec cannot represent."""

    hazard = []
    for char in text:
        try:
            char.encode(codec)
        except UnicodeEncodeError:
            hazard.append(char)
    return hazard


def test_rendered_segment_needs_a_codec_the_locale_cannot_fall_back_to() -> None:
    """Why the stdout pin matters: the segment's own glyphs are not `gbk`.

    The subprocess test above cannot assert a glyph directly, because the
    `should_run` probe is unreachable on some hosts and the statusline then
    degrades to its bare `[loopx <goal>]` fallback. This pins the encode side of
    the contract deterministically instead.
    """

    spec = importlib.util.spec_from_file_location("goal_status_under_test", STATUSLINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    segment = module._render(GOAL_ID, {"should_run": True, "recommended_action": "step"})
    # The segment carries a state glyph the locale codec cannot encode, which is
    # why stdout needs the pin and not only stdin. Order-independent: any glyph
    # in the segment counts, not just the first unencodable character.
    assert set(_unencodable(segment, LOCALE_CODEC)) & set(GLYPHS), segment


def test_statusline_keeps_its_segment_under_a_locale_codec(tmp_path: Path) -> None:
    """The statusline must still render this session's goal on a gbk stdout."""

    project = _arm(tmp_path / PROJECT_NAME)
    session = json.dumps(
        {"cwd": str(project), "session_id": "s1"}, ensure_ascii=False
    ).encode("utf-8")
    assert PROJECT_NAME.encode("utf-8") in session

    locale_result = _run(STATUSLINE, project, session, LOCALE_CODEC)
    utf8_result = _run(STATUSLINE, project, session, "utf-8")

    assert utf8_result.returncode == 0, utf8_result.stderr.decode("utf-8", "replace")
    assert locale_result.returncode == 0, locale_result.stderr.decode("utf-8", "replace")
    for result in (locale_result, utf8_result):
        rendered = result.stdout.decode("utf-8")
        assert rendered.strip(), rendered
        assert GOAL_ID in rendered, rendered
    # The segment reaches Claude Code as UTF-8 bytes on either host codec.
    assert GOAL_ID.encode("utf-8") in locale_result.stdout
