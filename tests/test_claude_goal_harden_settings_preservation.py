r"""`install.py --harden` must never destroy a settings.json it cannot read.

`add_hardening` deep-merges the optional PreToolUse gate and statusline into the
user's Claude Code `settings.json` and then rewrites that file whole. It used to
read the file inside `try` and fall back to `cur = {}` on any exception, so a
settings file that exists but does not parse was silently replaced by the
hardening block alone: `permissions` (including `deny` rules), `env`, `model`,
other hooks and MCP approvals were dropped, at exit status 0, with no warning
and no backup. Ordinary ways to reach that state on a supported host include a
UTF-8 BOM (Notepad's "UTF-8 with BOM"), a hand-added `//` comment or trailing
comma, and a torn tail left by an interrupted write - the installer's own
`write_text` truncated the file in place, so the next run destroyed the rest.

The file is user-owned: LoopX did not create it. `loopx/slash_command_install.py`
already fails closed on the same class of input (it returns `invalid_status` and
writes nothing) and replaces the file with one `os.replace`. These tests pin the
same boundary here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.claude_goal_mode.scripts import install as claude_install

# Each entry is an ordinary on-disk state of a user-owned settings file that
# `json.loads` rejects. The last one is what an interrupted write leaves behind.
UNREADABLE_SETTINGS = (
    ("utf8-bom", "\ufeff" + json.dumps({"permissions": {"deny": ["Read(~/.ssh/**)"]}})),
    ("jsonc-comment", '{\n  // mine\n  "model": "opus"\n}'),
    ("trailing-comma", '{"model": "opus",}'),
    ("torn-tail", '{"model": "opus", "permissions": {"deny": ['),
    ("empty", ""),
)


@pytest.mark.parametrize("label,raw", UNREADABLE_SETTINGS)
def test_an_unreadable_settings_file_is_refused_and_left_byte_identical(
    label: str, raw: str, tmp_path: Path
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(raw, encoding="utf-8")
    before = settings.read_bytes()

    with pytest.raises(claude_install.HardenSettingsUnreadable) as raised:
        claude_install.add_hardening(dry=False, settings_path=settings)

    assert str(settings) in str(raised.value)
    assert settings.read_bytes() == before, f"{label}: settings.json was rewritten"


def test_a_json_file_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('["not", "an", "object"]', encoding="utf-8")

    with pytest.raises(claude_install.HardenSettingsUnreadable):
        claude_install.add_hardening(dry=False, settings_path=settings)

    assert settings.read_text(encoding="utf-8") == '["not", "an", "object"]'


def test_a_readable_settings_file_keeps_every_key_it_had(tmp_path: Path) -> None:
    """The refusal above must not turn into refusing to install at all."""

    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Read(~/.ssh/**)"]},
                "env": {"ANTHROPIC_LOG": "1"},
                "model": "opus",
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [
                            {"type": "command", "command": "my-own-guard.sh"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    claude_install.add_hardening(dry=False, settings_path=settings)

    written = json.loads(settings.read_text(encoding="utf-8"))
    assert written["permissions"] == {
        "allow": ["Bash(ls:*)"],
        "deny": ["Read(~/.ssh/**)"],
    }
    assert written["env"] == {"ANTHROPIC_LOG": "1"}
    assert written["model"] == "opus"
    commands = [
        hook.get("command")
        for group in written["hooks"]["PreToolUse"]
        for hook in group["hooks"]
    ]
    assert "my-own-guard.sh" in commands
    assert any("goal_policy.py" in str(command) for command in commands)
    assert written["statusLine"]["type"] == "command"


def test_a_missing_settings_file_is_created_with_the_hardening_block(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "claude" / "settings.json"

    claude_install.add_hardening(dry=False, settings_path=settings)

    written = json.loads(settings.read_text(encoding="utf-8"))
    assert sorted(written) == ["hooks", "statusLine"]


def test_a_failed_write_leaves_the_previous_settings_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The replacement is one `os.replace`, not a truncate-in-place.

    `write_text` opens the destination with `O_TRUNC`, so anything that stops
    the process between that open and the last byte leaves the user's settings
    as a partial document - and the next run used to finish the job by
    replacing it. Refusing to read it is only half the boundary; the write must
    also be able to fail without taking the old bytes with it.
    """

    settings = tmp_path / "settings.json"
    original = json.dumps({"model": "opus", "permissions": {"deny": ["Read(~/.ssh/**)"]}})
    settings.write_text(original, encoding="utf-8")

    def refuse_replace(*_args, **_kwargs):
        raise OSError("simulated interruption")

    monkeypatch.setattr(claude_install.os, "replace", refuse_replace)

    with pytest.raises(OSError):
        claude_install.add_hardening(dry=False, settings_path=settings)

    assert settings.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.tmp")) == []


def _symlinks_available(directory: Path) -> bool:
    target = directory / "symlink-probe-target"
    link = directory / "symlink-probe-link"
    target.write_text("{}", encoding="utf-8")
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    finally:
        link.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
    return True


def test_a_symlinked_settings_file_keeps_its_target(tmp_path: Path) -> None:
    """A dotfiles-managed `settings.json` is the user's link, not ours to drop.

    The atomic write ends in `os.replace`, which overwrites the destination
    directory entry; a plain write used to follow the link and land in the
    target file. The write therefore resolves the path first, so the merged
    document reaches the file the user actually keeps in version control and
    the link still names it afterwards.
    """

    if not _symlinks_available(tmp_path):
        pytest.skip("this host cannot create symlinks")

    target = tmp_path / "dotfiles" / "claude-settings.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    settings = tmp_path / "settings.json"
    settings.symlink_to(target)

    claude_install.add_hardening(dry=False, settings_path=settings)

    assert settings.is_symlink(), "the write must not replace the user's link"
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["model"] == "opus"
    assert written["statusLine"]["type"] == "command"
