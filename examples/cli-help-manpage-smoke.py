#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx import __version__  # noqa: E402
from loopx.cli import build_parser  # noqa: E402
from loopx.help_surface import (  # noqa: E402
    COMMAND_GROUPS,
    MANPAGE_COMMAND_HELP_ONLY,
    manpage_top_level_commands,
    render_manpage,
)


LONG_TAIL_COMMAND = "codex-cli-visible-first-response-capture-plan"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )


def assert_concise_default_help(output: str) -> None:
    assert "LoopX keeps long-running agent work moving" in output, output
    assert "Start here:" in output, output
    assert "/loopx <goal text>" in output, output
    assert "slash-commands --install" in output, output
    assert "ready-score --goal-id ID" in output, output
    assert "start-goal --guided" in output, output
    assert "Run the loop:" in output, output
    assert "Codex App" in output, output
    assert "Claude Code" in output, output
    assert "loopx commands" in output, output
    assert "evidence-log" not in output, output
    assert "Read this agent's thin ledger" not in output, output
    assert "man loopx" in output, output
    assert LONG_TAIL_COMMAND not in output, output
    assert len(output.splitlines()) <= 39, output


def assert_default_help_surface() -> None:
    bare = run_cli()
    assert bare.returncode == 0, (bare.returncode, bare.stdout, bare.stderr)
    assert_concise_default_help(bare.stdout)
    assert bare.stderr == "", bare.stderr

    top_help = run_cli("--help")
    assert top_help.returncode == 0, (top_help.returncode, top_help.stdout, top_help.stderr)
    assert_concise_default_help(top_help.stdout)
    assert top_help.stderr == "", top_help.stderr


def assert_command_reference_surface() -> None:
    result = run_cli("commands")
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "LoopX command reference" in result.stdout, result.stdout
    assert "Daily operator commands" in result.stdout, result.stdout
    assert "host-projected replan context" in result.stdout, result.stdout
    assert "evidence-log" not in result.stdout, result.stdout
    assert "Loop driver hints" in result.stdout, result.stdout
    assert "Claude Code /loop" in result.stdout, result.stdout
    assert "Maintainer and adapter commands" in result.stdout, result.stdout
    assert "loopx <command> --help" in result.stdout, result.stdout
    assert "codex-cli-bootstrap-message" in result.stdout, result.stdout
    assert "loopx ready-score --goal-id <goal-id>" in result.stdout, result.stdout
    assert "loopx chat --goal-id <goal-id>" in result.stdout, result.stdout
    assert result.stderr == "", result.stderr


def assert_top_level_commands_are_explicitly_classified() -> None:
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    parser_commands = frozenset(subparsers.choices)
    manual_commands = manpage_top_level_commands()
    assert manual_commands.isdisjoint(MANPAGE_COMMAND_HELP_ONLY)
    assert parser_commands == manual_commands | MANPAGE_COMMAND_HELP_ONLY, {
        "unclassified": sorted(
            parser_commands - manual_commands - MANPAGE_COMMAND_HELP_ONLY
        ),
        "stale_manual": sorted(manual_commands - parser_commands),
        "stale_help_only": sorted(MANPAGE_COMMAND_HELP_ONLY - parser_commands),
    }


def assert_extension_capabilities_stay_out_of_core_manual() -> None:
    manifest_paths = sorted(REPO_ROOT.glob("loopx/extensions/**/extension.toml"))
    manifest_paths.extend(sorted(REPO_ROOT.glob("packages/**/extension.toml")))
    extension_owned_ids: set[str] = set()
    for path in manifest_paths:
        manifest = tomllib.loads(path.read_text(encoding="utf-8"))
        extension_id = manifest.get("id")
        if isinstance(extension_id, str):
            extension_owned_ids.add(extension_id)
        for capability in manifest.get("provides", []):
            capability_id = capability.get("id")
            if isinstance(capability_id, str):
                extension_owned_ids.add(capability_id)

    # `[[implements]]` ids are intentionally excluded: they name core-owned
    # capability contracts whose stable core command may belong in the manual.
    manual_commands = manpage_top_level_commands()
    assert extension_owned_ids.isdisjoint(manual_commands), {
        "extension_owned_manual_commands": sorted(
            extension_owned_ids & manual_commands
        )
    }
    man_text = render_manpage()
    for extension_owned_id in extension_owned_ids:
        escaped_id = extension_owned_id.replace("-", r"\-")
        assert rf"\fBloopx {escaped_id}\fR" not in man_text, extension_owned_id


def assert_checked_in_manpage_surface() -> None:
    manpage = REPO_ROOT / "man" / "loopx.1"
    assert manpage.read_text(encoding="utf-8") == render_manpage()


def assert_catalog_is_present(man_text: str) -> None:
    assert f'"LoopX {__version__}"' in man_text, man_text
    for group in COMMAND_GROUPS:
        for entry in group["commands"]:
            command = str(entry["command"])
            purpose = str(entry["purpose"])
            assert command.replace("-", r"\-") in man_text, command
            assert purpose.replace("-", r"\-") in man_text, purpose


def assert_installer_manpage_surface() -> None:
    script = REPO_ROOT / "scripts" / "install-local.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)

    with tempfile.TemporaryDirectory(prefix="loopx-help-manpage-smoke-") as raw_tmp:
        tmp = Path(raw_tmp)
        home = tmp / "home"
        bin_dir = home / ".local" / "bin"
        man_root = home / ".local" / "share" / "man"
        profile = home / ".zshrc"
        home.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "LOOPX_BIN_DIR": str(bin_dir),
            "LOOPX_RELEASES_DIR": str(home / ".local" / "share" / "loopx" / "releases"),
            "LOOPX_SHELL_PROFILE": str(profile),
            "LOOPX_INSTALL_SKILL": "0",
            "LOOPX_INSTALL_CANARY": "0",
            "LOOPX_PYTHON": sys.executable,
            "LOOPX_PROMOTE_DEFAULT": "1",
            "LOOPX_RELEASE_ID": "help-manpage-smoke-release",
            "PATH": os.environ.get("PATH", ""),
            "SHELL": "/bin/zsh",
        }

        help_result = subprocess.run(
            [str(script), "--help"],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        assert help_result.returncode == 0, help_result
        assert "Usage: install-local.sh [--help]" in help_result.stdout, help_result.stdout
        assert "positional arguments are not supported" in help_result.stdout, help_result.stdout
        assert help_result.stderr == "", help_result.stderr
        assert not bin_dir.exists(), bin_dir
        assert not man_root.exists(), man_root
        assert not profile.exists(), profile
        assert not Path(env["CODEX_HOME"]).exists(), env["CODEX_HOME"]
        assert not Path(env["LOOPX_RELEASES_DIR"]).exists(), env["LOOPX_RELEASES_DIR"]

        unknown_result = subprocess.run(
            [str(script), "--unknown"],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        assert unknown_result.returncode == 2, unknown_result
        assert unknown_result.stdout == "", unknown_result.stdout
        assert "loopx installer error: unknown argument: --unknown" in unknown_result.stderr
        assert "Usage: install-local.sh [--help]" in unknown_result.stderr
        assert not bin_dir.exists(), bin_dir
        assert not man_root.exists(), man_root
        assert not profile.exists(), profile
        assert not Path(env["CODEX_HOME"]).exists(), env["CODEX_HOME"]
        assert not Path(env["LOOPX_RELEASES_DIR"]).exists(), env["LOOPX_RELEASES_DIR"]

        install = subprocess.run(
            [str(script)],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        assert install.returncode == 0, (
            install.returncode,
            install.stdout,
            install.stderr,
        )
        assert f"- manpage: {man_root / 'man1' / 'loopx.1.gz'}" in install.stdout, install.stdout

        installed_help = subprocess.run(
            [str(bin_dir / "loopx")],
            cwd=tmp,
            env=env,
            text=True,
            capture_output=True,
        )
        assert installed_help.returncode == 0, (
            installed_help.returncode,
            installed_help.stdout,
            installed_help.stderr,
        )
        assert_concise_default_help(installed_help.stdout)
        assert installed_help.stderr == "", installed_help.stderr

        manpage = man_root / "man1" / "loopx.1.gz"
        assert manpage.is_file(), manpage
        with gzip.open(manpage, "rt", encoding="utf-8") as handle:
            man_text = handle.read()
        assert man_text == render_manpage(), man_text
        assert_catalog_is_present(man_text)
        compact_man_text = " ".join(man_text.split())
        assert ".TH LOOPX 1" in man_text, man_text
        assert ".SH LOOP DRIVER HINTS" in man_text, man_text
        assert "Codex App automation" in man_text, man_text
        assert "loopx commands" in man_text, man_text
        assert "loopx extension" in man_text, man_text
        assert r"loopx evidence\-log \-\-goal\-id" in man_text, man_text
        assert "before replan or handoff" in compact_man_text, man_text
        assert r"loopx COMMAND \-\-help" in man_text, man_text

        profile_text = profile.read_text(encoding="utf-8")
        assert 'export MANPATH="$HOME/.local/share/man:${MANPATH:-}"' in profile_text, profile_text

        man = subprocess.run(
            ["man", "-M", str(man_root), "loopx"],
            cwd=tmp,
            env={**env, "MANPAGER": "cat", "PAGER": "cat"},
            text=True,
            capture_output=True,
        )
        rendered_man = "\n".join(part for part in (man.stdout, man.stderr) if part)
        # man implementations insert presentation-only Unicode hyphens when
        # wrapping words. Remove those line-break artifacts before checking
        # the rendered semantics.
        dehyphenated_man = re.sub(r"[-\u2010-\u2015]\s+", "", rendered_man)
        compact_rendered_man = " ".join(dehyphenated_man.split())
        compact_rendered_man = re.sub(r"[-\u2010-\u2015]", "", compact_rendered_man)
        assert "LOOPX(1)" in rendered_man, (man.returncode, man.stdout, man.stderr)
        assert "LoopX keeps longrunning agent work moving" in compact_rendered_man, (
            rendered_man
        )
        assert "heartbeat automation" in compact_rendered_man, rendered_man


def main() -> int:
    assert_default_help_surface()
    assert_command_reference_surface()
    assert_top_level_commands_are_explicitly_classified()
    assert_extension_capabilities_stay_out_of_core_manual()
    assert_checked_in_manpage_surface()
    assert_installer_manpage_surface()
    print("cli-help-manpage-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
