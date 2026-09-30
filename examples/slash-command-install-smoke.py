#!/usr/bin/env python3
"""Smoke-test LoopX slash-command prompt/skill installation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def statuses_for(payload: dict, path: Path) -> list[str]:
    return [
        str(item["status"])
        for item in payload["installed"]
        if item.get("path") == str(path)
    ]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-slash-install-smoke-") as tmp:
        root = Path(tmp)
        codex_home = root / ".codex"
        claude_home = root / ".claude"
        opencode_home = root / ".opencode"
        os.environ["OPENCODE_CONFIG_DIR"] = str(opencode_home)

        dry = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--dry-run",
                "--codex-home",
                str(codex_home),
                "--claude-home",
                str(claude_home),
            ).stdout
        )
        assert dry["schema_version"] == "loopx_slash_command_install_v0", dry
        assert dry["operation"] == "install", dry
        assert dry["execute"] is False, dry
        assert dry["summary"]["status_counts"]["would_create"] >= 20, dry
        assert dry["summary"]["status_counts"]["unsupported_host_surface"] >= 1, dry
        assert not (codex_home / "prompts").exists(), dry
        assert not (claude_home / "skills").exists(), dry
        assert not opencode_home.exists(), dry
        assert dry["summary"]["codex_prompt_dir"] is None, dry

        payload = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--install",
                "--codex-home",
                str(codex_home),
                "--claude-home",
                str(claude_home),
            ).stdout
        )
        assert payload["execute"] is True, payload
        assert payload["operation"] == "install", payload
        assert payload["summary"]["codex_prompt_dir"] is None, payload
        assert payload["summary"]["codex_skill_dir"] == str(codex_home / "skills"), payload
        assert payload["summary"]["claude_skill_dir"] == str(claude_home / "skills"), payload
        assert payload["summary"]["opencode_command_dir"] == str(opencode_home / "commands"), payload
        assert payload["summary"]["opencode_plugin_path"] is None, payload
        assert payload["summary"]["status_counts"]["created"] >= 20, payload
        assert payload["summary"]["status_counts"]["unsupported_host_surface"] >= 1, payload
        assert "skipped_user_file" not in payload["summary"]["status_counts"], payload
        codex_skill_rows = [
            item
            for item in payload["installed"]
            if item.get("mechanism") == "codex_explicit_skills" and item.get("command") == "/loopx"
        ]
        assert codex_skill_rows, payload
        assert codex_skill_rows[0]["invoke_as"] == ["$loopx", "/skills"], codex_skill_rows
        assert "/loopx" not in codex_skill_rows[0]["invoke_as"], codex_skill_rows
        codex_metadata_rows = [
            item
            for item in payload["installed"]
            if item.get("mechanism") == "codex_skill_openai_metadata" and item.get("command") == "/loopx"
        ]
        assert codex_metadata_rows, payload
        codex_cli_rows = [
            item
            for item in payload["installed"]
            if item.get("mechanism") == "unsupported_native_slash_registry" and item.get("command") == "/loopx"
        ]
        assert codex_cli_rows, payload
        assert codex_cli_rows[0]["status"] == "unsupported_host_surface", codex_cli_rows
        assert codex_cli_rows[0]["native_registry_supported"] is False, codex_cli_rows
        assert codex_cli_rows[0]["failure_policy"] == "fail_closed_to_explicit_skill", codex_cli_rows
        assert "$loopx" in codex_cli_rows[0]["fallback"], codex_cli_rows

        codex_skill = codex_home / "skills" / "loopx" / "SKILL.md"
        codex_skill_text = codex_skill.read_text(encoding="utf-8")
        assert "name: loopx\n" in codex_skill_text
        assert 'name: "loopx"' not in codex_skill_text
        assert "surface=codex-skills" in codex_skill_text
        assert "LoopX `/loopx`" in codex_skill_text
        assert "start-goal --guided --project . --slash-command-arguments=" in codex_skill_text
        codex_metadata = codex_home / "skills" / "loopx" / "agents" / "openai.yaml"
        codex_metadata_text = codex_metadata.read_text(encoding="utf-8")
        assert 'display_name: "LoopX"' in codex_metadata_text
        assert 'display_name: "LoopX /loopx"' not in codex_metadata_text
        assert "allow_implicit_invocation: false" in codex_metadata_text
        assert "loopx-managed-slash-command:v1 command=/loopx surface=codex-skill-metadata" in codex_metadata_text

        assert not (codex_home / "prompts" / "loopx-pr-review.md").exists()

        claude_skill = claude_home / "skills" / "loopx-global-summary" / "SKILL.md"
        claude_skill_text = claude_skill.read_text(encoding="utf-8")
        assert "name: loopx-global-summary\n" in claude_skill_text
        assert "surface=claude-skills" in claude_skill_text
        assert "global-summary" in claude_skill_text

        claude_gates_skill = claude_home / "skills" / "loopx-global-gates" / "SKILL.md"
        claude_gates_skill_text = claude_gates_skill.read_text(encoding="utf-8")
        assert "loopx global-gates" in claude_gates_skill_text
        assert "This command is read-only" in claude_gates_skill_text
        assert "global-summary" not in claude_gates_skill_text

        claude_todos_skill = claude_home / "skills" / "loopx-global-todos" / "SKILL.md"
        claude_todos_text = claude_todos_skill.read_text(encoding="utf-8")
        assert "loopx global-todos" in claude_todos_text
        assert "This command is read-only" in claude_todos_text
        assert "global-summary" not in claude_todos_text

        claude_risks_skill = claude_home / "skills" / "loopx-global-risks" / "SKILL.md"
        claude_risks_text = claude_risks_skill.read_text(encoding="utf-8")
        assert "loopx global-risks" in claude_risks_text
        assert "structured stale runs" in claude_risks_text
        assert "formally evidenced rollback candidate source" in claude_risks_text
        assert "without mutating state" in claude_risks_text
        assert "This command is read-only" in claude_risks_text
        assert "global-summary" not in claude_risks_text
        assert not (claude_home / "skills" / "loop-global-risks").exists()

        # A deprecated alias skill republished by an older install (or copied
        # back in by a cross-host import) is retired on the next install, while
        # a user-owned same-name skill survives untouched.
        managed_alias = claude_home / "skills" / "loop-global-summary" / "SKILL.md"
        managed_alias.parent.mkdir(parents=True)
        managed_alias.write_text(
            "<!-- loopx-managed-slash-command:v1 command=/loop-global-summary surface=Codex-skills -->\n"
            "# LoopX /loop-global-summary\n",
            encoding="utf-8",
        )
        user_alias = claude_home / "skills" / "loop-global-risks" / "SKILL.md"
        user_alias.parent.mkdir(parents=True)
        user_alias.write_text("# user-owned alias skill\n", encoding="utf-8")
        alias_retire = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--install",
                "--codex-home",
                str(codex_home),
                "--claude-home",
                str(claude_home),
            ).stdout
        )
        assert statuses_for(alias_retire, managed_alias) == ["retired_managed_file"], alias_retire
        assert not managed_alias.exists()
        assert statuses_for(alias_retire, user_alias) == ["skipped_user_file"], alias_retire
        assert user_alias.read_text(encoding="utf-8") == "# user-owned alias skill\n"

        rerun = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--install",
                "--codex-home",
                str(codex_home),
                "--claude-home",
                str(claude_home),
            ).stdout
        )
        assert rerun["summary"]["status_counts"]["unchanged"] >= 20, rerun

        prompt_dir = codex_home / "prompts"
        prompt_dir.mkdir(parents=True)
        user_owned = prompt_dir / "loopx-global-risks.md"
        user_owned.write_text("# user-owned command\n", encoding="utf-8")
        managed_prompt = prompt_dir / "loopx-global-todos.md"
        managed_prompt.write_text(
            "<!-- loopx-managed-slash-command:v1 command=/loopx-global-todos surface=codex-prompts -->\n",
            encoding="utf-8",
        )
        capability_skill = codex_home / "skills" / "loopx-pr-review" / "SKILL.md"
        capability_skill.write_text(
            "# LoopX PR Review\n\nRun `loopx pr-review` first.\n",
            encoding="utf-8",
        )
        capability_metadata = codex_home / "skills" / "loopx-pr-review" / "agents" / "openai.yaml"
        assert capability_metadata.exists(), capability_metadata
        mixed = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--install",
                "--codex-home",
                str(codex_home),
                "--claude-home",
                str(claude_home),
            ).stdout
        )
        assert statuses_for(mixed, user_owned) == ["skipped_user_file"], mixed
        assert user_owned.read_text(encoding="utf-8") == "# user-owned command\n"
        assert statuses_for(mixed, managed_prompt) == ["retired_managed_file"], mixed
        assert not managed_prompt.exists()
        assert statuses_for(mixed, capability_skill) == ["preserved_existing_loopx_skill"], mixed
        assert capability_skill.read_text(encoding="utf-8") == (
            "# LoopX PR Review\n\nRun `loopx pr-review` first.\n"
        )
        assert statuses_for(mixed, capability_metadata) == ["retired_managed_file"], mixed
        assert not capability_metadata.exists(), capability_metadata

        markdown = run_cli(
            "slash-commands",
            "--install",
            "--codex-home",
            str(codex_home),
            "--claude-home",
            str(claude_home),
        ).stdout
        assert "# LoopX Slash Command Install" in markdown, markdown
        assert "codex skills:" in markdown, markdown
        assert "claude skills:" in markdown, markdown
        assert "Skipped user-owned files:" in markdown, markdown
        assert "$loopx" in markdown and "command-facade skills" in markdown, markdown

        codex_only = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--install",
                "--surface",
                "codex-cli",
                "--codex-home",
                str(root / "codex-only"),
                "--claude-home",
                str(root / "claude-unused"),
            ).stdout
        )
        assert codex_only["effective_surfaces"] == ["codex"], codex_only
        assert codex_only["summary"]["codex_prompt_dir"] is None, codex_only
        assert codex_only["summary"]["codex_skill_dir"] == str(root / "codex-only" / "skills"), codex_only
        assert codex_only["summary"]["claude_skill_dir"] is None, codex_only
        codex_skill_commands = {
            item["command"]
            for item in codex_only["installed"]
            if item["mechanism"] == "codex_explicit_skills"
        }
        unsupported_native_commands = {
            item["command"]
            for item in codex_only["installed"]
            if item["status"] == "unsupported_host_surface"
        }
        assert unsupported_native_commands == codex_skill_commands, codex_only

        legacy_codex_home = root / "legacy-codex"
        legacy_claude_home = root / "legacy-claude"
        legacy_skill = legacy_codex_home / "skills" / "loopx" / "SKILL.md"
        legacy_skill.parent.mkdir(parents=True)
        legacy_skill.write_text(
            "<!-- loopx-managed-slash-command:v1 command=/loopx surface=codex-skills -->\n",
            encoding="utf-8",
        )
        legacy_install = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--install",
                "--surface",
                "codex",
                "--codex-home",
                str(legacy_codex_home),
                "--claude-home",
                str(legacy_claude_home),
            ).stdout
        )
        assert statuses_for(legacy_install, legacy_skill) == ["updated"], legacy_install
        assert "loopx-managed-slash-command:v1 command=/loopx surface=codex-skills" in legacy_skill.read_text(encoding="utf-8")

        user_codex_home = root / "user-codex"
        user_skill = user_codex_home / "skills" / "loopx" / "SKILL.md"
        user_skill.parent.mkdir(parents=True)
        user_skill.write_text("# user-owned loopx skill\n", encoding="utf-8")
        user_install = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--install",
                "--surface",
                "codex",
                "--codex-home",
                str(user_codex_home),
                "--claude-home",
                str(root / "user-claude"),
            ).stdout
        )
        assert statuses_for(user_install, user_skill) == ["skipped_user_file"], user_install
        assert user_skill.read_text(encoding="utf-8") == "# user-owned loopx skill\n"

        uninstall_codex_home = root / "uninstall-codex"
        uninstall_claude_home = root / "uninstall-claude"
        uninstall_seed = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--install",
                "--codex-home",
                str(uninstall_codex_home),
                "--claude-home",
                str(uninstall_claude_home),
            ).stdout
        )
        assert uninstall_seed["summary"]["status_counts"]["created"] >= 20, uninstall_seed
        uninstall_user_skill = uninstall_codex_home / "skills" / "loopx-global-risks" / "SKILL.md"
        uninstall_user_skill.write_text("# user-owned LoopX helper\n", encoding="utf-8")
        managed_legacy_prompt = uninstall_codex_home / "prompts" / "loopx.md"
        managed_legacy_prompt.parent.mkdir(parents=True)
        managed_legacy_prompt.write_text(
            "<!-- loopx-managed-slash-command:v1 command=/loopx surface=codex-prompts -->\n",
            encoding="utf-8",
        )
        dry_uninstall = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--uninstall",
                "--dry-run",
                "--codex-home",
                str(uninstall_codex_home),
                "--claude-home",
                str(uninstall_claude_home),
            ).stdout
        )
        assert dry_uninstall["operation"] == "uninstall", dry_uninstall
        assert dry_uninstall["execute"] is False, dry_uninstall
        assert dry_uninstall["summary"]["status_counts"]["would_retire_managed_file"] >= 20, dry_uninstall
        assert statuses_for(dry_uninstall, uninstall_user_skill) == ["skipped_user_file"], dry_uninstall
        assert uninstall_user_skill.exists(), dry_uninstall

        uninstall = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--uninstall",
                "--codex-home",
                str(uninstall_codex_home),
                "--claude-home",
                str(uninstall_claude_home),
            ).stdout
        )
        assert uninstall["operation"] == "uninstall", uninstall
        assert uninstall["execute"] is True, uninstall
        assert uninstall["summary"]["status_counts"]["retired_managed_file"] >= 20, uninstall
        assert statuses_for(uninstall, uninstall_user_skill) == ["skipped_user_file"], uninstall
        assert not (uninstall_codex_home / "skills" / "loopx" / "SKILL.md").exists()
        assert not (uninstall_codex_home / "skills" / "loopx" / "agents" / "openai.yaml").exists()
        assert not (uninstall_claude_home / "skills" / "loopx" / "SKILL.md").exists()
        assert not managed_legacy_prompt.exists()
        assert uninstall_user_skill.read_text(encoding="utf-8") == "# user-owned LoopX helper\n"
        assert not (uninstall_user_skill.parent / "agents" / "openai.yaml").exists()

        uninstall_again = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--uninstall",
                "--codex-home",
                str(uninstall_codex_home),
                "--claude-home",
                str(uninstall_claude_home),
            ).stdout
        )
        assert uninstall_again["summary"]["status_counts"]["absent"] >= 20, uninstall_again

        bridge_home = root / "opencode-bridge"
        bridge = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--install",
                "--surface",
                "opencode",
                "--with-goal-bridge",
                "--opencode-home",
                str(bridge_home),
            ).stdout
        )
        assert bridge["with_goal_bridge"] is True, bridge
        assert (bridge_home / "commands" / "loopx.md").is_file(), bridge
        assert (bridge_home / "plugins" / "loopx-goal.js").is_file(), bridge
        assert (bridge_home / "loopx" / "goal-bridge-runtime.mjs").is_file(), bridge
        assert (bridge_home / "package.json").is_file(), bridge

        blocked_bridge_home = root / "opencode-bridge-blocked"
        blocked_bridge_home.mkdir()
        (blocked_bridge_home / "opencode.jsonc").write_text(
            "{ invalid\n",
            encoding="utf-8",
        )
        blocked_bridge_result = run_cli(
            "--format",
            "json",
            "slash-commands",
            "--install",
            "--surface",
            "opencode",
            "--with-goal-bridge",
            "--opencode-home",
            str(blocked_bridge_home),
            check=False,
        )
        assert blocked_bridge_result.returncode == 1, blocked_bridge_result
        blocked_bridge = json.loads(blocked_bridge_result.stdout)
        assert blocked_bridge["ok"] is False, blocked_bridge
        assert not (blocked_bridge_home / "commands" / "loopx.md").exists(), blocked_bridge
        assert not (blocked_bridge_home / "plugins" / "loopx-goal.js").exists(), blocked_bridge
        assert not (blocked_bridge_home / "loopx" / "goal-bridge-runtime.mjs").exists(), blocked_bridge
        assert not (blocked_bridge_home / "package.json").exists(), blocked_bridge

        pi_project = root / "pi-project"
        pi_project.mkdir()
        pi_install = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--install",
                "--surface",
                "pi",
                "--pi-project",
                str(pi_project),
            ).stdout
        )
        assert pi_install["ok"] is True, pi_install
        pi_extension = pi_project / ".pi" / "extensions" / "loopx-goal.ts"
        pi_runtime = pi_project / ".pi" / "extensions" / "pi-goal-loop-runtime.mjs"
        assert pi_install["summary"]["pi_extension_path"] == str(pi_extension.resolve()), pi_install
        assert pi_install["summary"]["pi_runtime_path"] == str(pi_runtime.resolve()), pi_install
        assert pi_extension.is_file(), pi_extension
        assert pi_runtime.is_file(), pi_runtime
        pi_extension_text = pi_extension.read_text(encoding="utf-8")
        assert "surface=pi-extension" in pi_extension_text, pi_install
        assert "loopx_goal_activate" in pi_extension_text, pi_install
        assert "loop.dispose()" in pi_extension_text, pi_install
        # The quota/wait/store loop core lives in the sibling runtime module;
        # session shutdown disposes the whole extension instance.
        pi_runtime_text = pi_runtime.read_text(encoding="utf-8")
        assert "surface=pi-extension-runtime" in pi_runtime_text, pi_install
        assert "terminal_no_followup" in pi_runtime_text, pi_install

        pi_uninstall = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--uninstall",
                "--surface",
                "pi",
                "--pi-project",
                str(pi_project),
            ).stdout
        )
        assert pi_uninstall["ok"] is True, pi_uninstall
        assert not pi_extension.exists(), pi_uninstall
        assert not pi_runtime.exists(), pi_uninstall

        # The default all surface never installs the Pi extension.
        pi_default = json.loads(
            run_cli(
                "--format",
                "json",
                "slash-commands",
                "--install",
                "--codex-home",
                str(codex_home),
                "--claude-home",
                str(claude_home),
                "--pi-project",
                str(pi_project),
            ).stdout
        )
        assert pi_default["summary"]["pi_extension_path"] is None, pi_default
        assert pi_default["summary"]["pi_runtime_path"] is None, pi_default
        assert not pi_extension.exists(), pi_default
        assert not pi_runtime.exists(), pi_default

    print("slash-command-install-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
