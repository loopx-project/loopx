"""The managed slash-command marker is stated once and read everywhere else.

`loopx/slash_command_files.py` writes the marker into a generated facade;
`loopx/skill_install_readback.py` reads it back to decide whether a file is
LoopX-managed. Both stated the same literal under their own names, so a version
bump had two places to land and a drift between them would silently make the
readback treat a managed file as user-owned.

Shipped host assets carry the marker as their first line in their own runtime.
They cannot import the Python owner, so they are listed here and checked against
it instead: renaming the prefix has to move them in the same change.
"""

from __future__ import annotations

from pathlib import Path

from loopx import skill_install_readback
from loopx.slash_command_files import MANAGED_MARKER_PREFIX, managed_marker

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OWNER_MODULE = "loopx/slash_command_files.py"

ASSET_HEADERS = (
    "loopx/opencode_goal_mode/goal-bridge-runtime.mjs",
    "loopx/opencode_goal_mode/loopx-goal.js",
    "loopx/pi_goal_mode/loopx-goal.ts",
    "loopx/pi_goal_mode/pi-goal-loop-runtime.mjs",
)

# Surfaces a generated facade may be written for; each one has to survive the
# round trip from the writer to the readback through the single owner.
FACADE_SURFACES = ("claude-skills", "codex-skills", "pi-extension")


def test_the_marker_body_is_stated_only_by_the_owner_in_python() -> None:
    # A census, not a roster: a module that starts stating the literal again has
    # to appear here, and a site that already imports the owner fails too.
    found: dict[str, int] = {}
    for path in sorted(REPOSITORY_ROOT.glob("loopx/**/*.py")):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        count = path.read_text(encoding="utf-8").count(MANAGED_MARKER_PREFIX)
        if count:
            found[relative] = count
    assert found == {OWNER_MODULE: 1}


def test_the_readback_imports_the_owner_object() -> None:
    assert skill_install_readback.LOOPX_MANAGED_SLASH_COMMAND_MARKER is MANAGED_MARKER_PREFIX


def test_the_writer_and_the_reader_agree_on_every_facade_surface() -> None:
    for surface in FACADE_SURFACES:
        marker = managed_marker(command="/loopx", surface=surface)
        assert marker.startswith(MANAGED_MARKER_PREFIX)
        assert MANAGED_MARKER_PREFIX in marker


def test_each_shipped_asset_header_states_the_owner_marker() -> None:
    for relative in ASSET_HEADERS:
        header = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8").splitlines()[0]
        assert MANAGED_MARKER_PREFIX in header, relative
