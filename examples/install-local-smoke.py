#!/usr/bin/env python3
"""Smoke-test local installer wrapper and skill installation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-local.sh"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx import __version__  # noqa: E402
from loopx.doctor import add_promotion_readiness_freshness, build_install_freshness  # noqa: E402
from loopx.release_manifest import release_version_tag  # noqa: E402
from loopx.release_manifest import build_release_manifest, load_release_manifest  # noqa: E402
from loopx.skill_install_readback import PACKAGED_HOST_SKILL_IDS  # noqa: E402


def git_output(args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def normal_turns_use_cli_interaction_contract(task_body: str) -> bool:
    clauses = " ".join(task_body.split()).replace(";", ".").split(".")
    for clause in clauses:
        normalized = clause.lower()
        if (
            "`interaction_contract`" in normalized
            and "cli" in normalized
            and ("normal turn" in normalized or "normal-turn" in normalized)
            and any(marker in normalized for marker in (" use ", " follow ", " authority"))
        ):
            return True
    return False


def source_git_commit(root: Path = REPO_ROOT) -> str:
    if root == REPO_ROOT:
        commit = git_output(["rev-parse", "HEAD"])
        if commit:
            return commit
    release_manifest = load_release_manifest(root)
    manifest = release_manifest.get("manifest") if isinstance(release_manifest, dict) else None
    source = manifest.get("source") if isinstance(manifest, dict) else None
    commit = source.get("git_commit") if isinstance(source, dict) else None
    assert isinstance(commit, str) and commit, release_manifest
    return commit


def run_install(
    env: dict[str, str],
    release_id: str,
    *,
    cwd: Path = REPO_ROOT,
    revalidate_extensions: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSTALL_SCRIPT)],
        cwd=cwd,
        env={
            **env,
            "LOOPX_RELEASE_ID": release_id,
            "LOOPX_INSTALL_REVALIDATE_EXTENSIONS": (
                "1" if revalidate_extensions else "0"
            ),
        },
        check=True,
        capture_output=True,
        text=True,
    )


def write_promotion_readiness(
    runtime_run_dir: Path,
    *,
    generated_at: str,
    label: str,
) -> dict[str, str]:
    runtime_run_dir.mkdir(parents=True, exist_ok=True)
    readiness_json = runtime_run_dir / f"2026-01-01T00-00-00-{label}-canary-promotion-readiness.json"
    readiness_markdown = runtime_run_dir / f"2026-01-01T00-00-00-{label}-canary-promotion-readiness.md"
    readiness_record = {
        "generated_at": generated_at,
        "goal_id": "loopx-meta",
        "classification": "canary_promotion_readiness_smoke_group",
        "delivery_batch_scale": "multi_surface",
        "delivery_outcome": "primary_goal_outcome",
        "recommended_action": f"fixture {label} promotion readiness evidence",
        "json_path": str(readiness_json),
        "markdown_path": str(readiness_markdown),
    }
    readiness_json.write_text(json.dumps(readiness_record, indent=2, sort_keys=True), encoding="utf-8")
    readiness_markdown.write_text("# Canary promotion readiness\n", encoding="utf-8")
    (runtime_run_dir / "index.jsonl").write_text(json.dumps(readiness_record, sort_keys=True) + "\n", encoding="utf-8")
    return readiness_record


def assert_release_snapshot_source_fallback(root: Path) -> None:
    source_root = root / "source-snapshot"
    release_root = root / "nested-release"
    source_root.mkdir()
    release_root.mkdir()
    source_manifest = {
        "schema_version": "loopx_release_manifest_v0",
        "release_id": "fixture-source",
        "source": {
            "kind": "github_archive",
            "repo": "loopx-project/loopx",
            "ref": "main",
            "git_commit": "abc123def4567890abc123def4567890abc123de",
            "git_ref": "main",
            "git_dirty": False,
            "archive_url": "https://example.com/loopx.tar.gz",
            "archive_sha256": "f" * 64,
        },
        "package": {
            "name": "loopx",
            "version": __version__,
            "version_tag": release_version_tag(),
            "version_source": "loopx.__version__",
        },
        "skills": {"digest": "fixture", "items": {}},
    }
    (source_root / "release.json").write_text(
        json.dumps(source_manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    nested_manifest = build_release_manifest(
        release_root=release_root,
        release_id="fixture-nested",
        source_root=source_root,
        installed_at="2026-01-01T00:00:00Z",
    )
    assert nested_manifest["source"]["git_commit"] == source_manifest["source"]["git_commit"], nested_manifest
    assert nested_manifest["source"]["git_ref"] == source_manifest["source"]["git_ref"], nested_manifest
    assert nested_manifest["source"]["git_dirty"] is False, nested_manifest
    assert nested_manifest["source"]["kind"] == "github_archive", nested_manifest
    assert nested_manifest["source"]["repo"] == source_manifest["source"]["repo"], nested_manifest
    assert nested_manifest["source"]["ref"] == source_manifest["source"]["ref"], nested_manifest
    assert nested_manifest["source"]["archive_url"] == source_manifest["source"]["archive_url"], nested_manifest
    assert nested_manifest["source"]["archive_sha256"] == source_manifest["source"]["archive_sha256"], nested_manifest


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-install-smoke-") as tmp:
        root = Path(tmp)
        home = root / "home"
        home.mkdir()
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        legacy_scripts = (
            home
            / ".local"
            / "share"
            / "goal-harness"
            / "releases"
            / "legacy"
            / "scripts"
        )
        legacy_scripts.mkdir(parents=True)
        legacy_goal_harness = legacy_scripts / "goal-harness"
        legacy_goal_harness.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
        legacy_goal_harness.chmod(0o755)
        legacy_canary = legacy_scripts / "goal-harness-canary"
        legacy_canary.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
        legacy_canary.chmod(0o755)
        (bin_dir / "goal-harness").symlink_to(legacy_goal_harness)
        (bin_dir / "goal-harness-canary").symlink_to(legacy_canary)
        stale_loopx = bin_dir / "loopx"
        stale_loopx.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
        stale_loopx.chmod(0o755)
        stale_fallback = bin_dir / "loopx-apply-rrule"
        stale_fallback.write_text("#!/usr/bin/env bash\nexit 98\n", encoding="utf-8")
        stale_fallback.chmod(0o755)
        stale_canary_target = root / "stale-canary-target"
        stale_canary_target.mkdir()
        (bin_dir / "loopx-canary").symlink_to(stale_canary_target)
        codex_home = home / ".codex"
        stale_command_skill = codex_home / "skills" / "loopx" / "SKILL.md"
        stale_command_skill.parent.mkdir(parents=True)
        stale_command_skill.write_text(
            "<!-- loopx-managed-slash-command:v1 command=/loopx surface=codex-skills -->\n",
            encoding="utf-8",
        )
        stale_command_metadata = stale_command_skill.parent / "agents" / "openai.yaml"
        stale_command_metadata.parent.mkdir(parents=True)
        stale_command_metadata.write_text(
            "# <!-- loopx-managed-slash-command:v1 command=/loopx surface=codex-skill-metadata -->\n",
            encoding="utf-8",
        )
        profile = home / ".zshrc"
        assert_release_snapshot_source_fallback(root)
        env = {
            **os.environ,
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "OPENCODE_CONFIG_DIR": str(home / ".config" / "opencode"),
            "LOOPX_BIN_DIR": str(bin_dir),
            "LOOPX_SHELL_PROFILE": str(profile),
            "LOOPX_INSTALL_SKILL": "1",
            "LOOPX_PROMOTE_DEFAULT": "1",
            "LOOPX_PYTHON": sys.executable,
            "PATH": os.environ.get("PATH", ""),
            "SHELL": "/bin/zsh",
        }
        source_commit = source_git_commit()

        shadow_cwd = root / "shadow-checkout"
        shadow_package = shadow_cwd / "loopx"
        shadow_package.mkdir(parents=True)
        (shadow_package / "__init__.py").write_text(
            '"""Deliberately incomplete package that must not shadow the release."""\n',
            encoding="utf-8",
        )

        install = run_install(env, "install-smoke-initial", cwd=shadow_cwd)
        assert "loopx installed locally" in install.stdout, install.stdout
        assert "promotion-readiness evidence is missing" in install.stderr, install.stderr
        assert "non-blocking" in install.stderr, install.stderr
        assert "examples/canary/canary-promotion-readiness-smoke.py" in install.stderr, install.stderr
        assert f"- executable: {bin_dir / 'loopx'}" in install.stdout, install.stdout
        assert (
            f"- Codex App fallback executable: {bin_dir / 'loopx-apply-rrule'}"
            in install.stdout
        ), install.stdout
        assert "- release: " in install.stdout, install.stdout
        assert f"- canary executable: {bin_dir / 'loopx-canary'}" in install.stdout, install.stdout
        assert "- executable compatibility: none" in install.stdout, install.stdout
        assert (
            f"- legacy command disabled: {bin_dir / 'goal-harness.legacy-disabled'}"
            in install.stdout
        ), install.stdout
        assert (
            f"- legacy command disabled: {bin_dir / 'goal-harness-canary.legacy-disabled'}"
            in install.stdout
        ), install.stdout
        for skill_id in PACKAGED_HOST_SKILL_IDS:
            assert (
                f"- skill: {codex_home / 'skills' / skill_id}" in install.stdout
            ), install.stdout
        assert "project skill source:" in install.stdout, install.stdout
        assert "install explicitly per project" in install.stdout, install.stdout
        assert f"codex skills: {codex_home / 'skills'}" in install.stdout, install.stdout
        assert f"claude skills: {home / '.claude' / 'skills'}" in install.stdout, install.stdout
        assert "loopx OpenCode bridge: skipped (opt-in" in install.stdout, install.stdout
        opencode_root = home / ".config" / "opencode"
        assert (opencode_root / "commands" / "loopx.md").is_file()
        assert not (opencode_root / "plugins" / "loopx-goal.js").exists()
        assert not (opencode_root / "loopx" / "goal-bridge-runtime.mjs").exists()
        assert not (opencode_root / "package.json").exists()

        wrapper = bin_dir / "loopx"
        assert wrapper.is_symlink(), wrapper
        fallback_wrapper = bin_dir / "loopx-apply-rrule"
        assert fallback_wrapper.is_symlink(), fallback_wrapper
        assert not (bin_dir / "goal-harness").exists()
        assert (bin_dir / "goal-harness.legacy-disabled").is_symlink()
        assert wrapper.resolve() != REPO_ROOT / "scripts" / "loopx", wrapper.resolve()
        assert wrapper.resolve().name == "loopx", wrapper.resolve()
        release_root = wrapper.resolve().parents[1]
        assert fallback_wrapper.resolve() == (
            release_root / "scripts" / "loopx-apply-rrule"
        ), fallback_wrapper.resolve()
        fallback_help = subprocess.run(
            [str(fallback_wrapper), "--help"],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "outer host turn identity" in fallback_help.stdout, fallback_help.stdout
        release_python = release_root / ".loopx-python"
        assert release_python.read_text(encoding="utf-8").strip() == sys.executable
        assert (release_root / "loopx" / "cli.py").is_file(), release_root
        runtime_package = release_root / "loopx" / "control_plane" / "runtime"
        assert (runtime_package / "run_compaction.py").is_file(), release_root
        assert (runtime_package / "session_runtime.py").is_file(), release_root
        dashboard_page = release_root / "apps" / "presentation" / "dashboard" / "src" / "views" / "dashboard-page.tsx"
        action_packet = release_root / "apps" / "presentation" / "dashboard" / "src" / "data" / "action-packet.ts"
        dashboard_node_modules = release_root / "apps" / "presentation" / "dashboard" / "node_modules"
        assert dashboard_page.is_file(), dashboard_page
        assert action_packet.is_file(), action_packet
        assert not dashboard_node_modules.exists(), dashboard_node_modules
        assert (release_root / ".github" / "workflows" / "update-notes.yml").is_file(), release_root
        assert (release_root / "docs/development/contributor-tasks.md").is_file(), release_root
        assert (release_root / "LICENSE").is_file(), release_root
        release_manifest_path = release_root / "release.json"
        assert release_manifest_path.is_file(), release_manifest_path
        release_manifest = json.loads(release_manifest_path.read_text(encoding="utf-8"))
        assert release_manifest["schema_version"] == "loopx_release_manifest_v0", release_manifest
        assert release_manifest["release_id"] == "install-smoke-initial", release_manifest
        assert release_manifest["package"]["name"] == "loopx", release_manifest
        assert release_manifest["package"]["version"] == __version__, release_manifest
        assert release_manifest["package"]["version_tag"] == release_version_tag(), release_manifest
        assert release_manifest["package"]["version_source"] == "loopx.__version__", release_manifest
        assert release_manifest["source"]["kind"] == "local_checkout", release_manifest
        assert release_manifest["source"]["promotion_mode"] == "explicit_override", release_manifest
        assert release_manifest["source"]["git_commit"] == source_commit, release_manifest
        assert isinstance(release_manifest["source"]["git_dirty"], bool), release_manifest
        assert release_manifest["skills"]["digest"], release_manifest
        assert release_manifest["skills"]["items"]["loopx-project"]["sha256"], release_manifest
        canary_wrapper = bin_dir / "loopx-canary"
        assert canary_wrapper.is_symlink(), canary_wrapper
        assert not (stale_canary_target / "loopx-canary").exists(), stale_canary_target
        assert not (bin_dir / "goal-harness-canary").exists()
        assert (bin_dir / "goal-harness-canary.legacy-disabled").is_symlink()
        assert canary_wrapper.resolve() == REPO_ROOT / "scripts" / "loopx", canary_wrapper.resolve()
        assert profile.read_text(encoding="utf-8").count("LoopX local CLI") == 1, profile.read_text()

        unsupported_host_bin = root / "unsupported-host-bin"
        unsupported_host_bin.mkdir()
        unsupported_python = unsupported_host_bin / "python3"
        unsupported_python.write_text("#!/usr/bin/env bash\nexit 86\n", encoding="utf-8")
        unsupported_python.chmod(0o755)
        promoted_env = {
            key: value
            for key, value in env.items()
            if key != "LOOPX_PYTHON"
        }
        promoted_env["PATH"] = f"{unsupported_host_bin}:{bin_dir}:{promoted_env['PATH']}"
        promoted_doctor = subprocess.run(
            ["loopx", "--format", "json", "doctor"],
            cwd=root,
            env=promoted_env,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(promoted_doctor.stdout)["ok"] is True, promoted_doctor.stdout
        subprocess.run(
            [str(wrapper), "canary", "premerge", "--help"],
            cwd=REPO_ROOT,
            env=promoted_env,
            check=True,
            capture_output=True,
            text=True,
        )

        skill = codex_home / "skills" / "loopx-project" / "SKILL.md"
        assert not skill.parent.is_symlink(), skill.parent
        skill_readback = json.loads(
            (codex_home / "skills" / ".loopx-skill-install.json").read_text(
                encoding="utf-8"
            )
        )
        assert skill_readback["integration_mode"] == "fixed_install_script"
        assert skill_readback["source"]["revision"] == source_commit
        materialized_skill_ids = set(skill_readback["materialized_skill_ids"])
        assert materialized_skill_ids == {
            "loopx",
            *PACKAGED_HOST_SKILL_IDS,
        }
        # Only declared global scopes are delivered: a repo-kept workflow carries
        # no scope marker, so the fixed install keeps it in the checkout instead
        # of copying a merge-decision workflow onto a host that never merges.
        repo_only_skill_ids = {
            path.name
            for path in (REPO_ROOT / "skills").iterdir()
            if path.is_dir() and not (path / ".loopx-skill-scope").exists()
        }
        assert repo_only_skill_ids, "the checkout no longer carries a repo-only skill source"
        assert not (materialized_skill_ids & repo_only_skill_ids), sorted(
            materialized_skill_ids & repo_only_skill_ids
        )
        skill_text = skill.read_text(encoding="utf-8")
        compact_skill_text = " ".join(skill_text.split())
        for phrase in (
            "Set Up Recurring Heartbeats",
            "loopx heartbeat-prompt",
            "run a short steering audit before choosing work",
            "at least three plausible next-action candidates",
            "continuation check",
            "compute quota separate from focus quota",
            "Register Project Authority And Material Sources",
            "doc-registry skill trigger",
            "Diagnose For The User",
            "loopx diagnose",
            "Use those signals as evidence",
            "Identify the target project and goal first",
            "loopx register-authority-source",
            "loopx import-doc-registry-authority",
            "LoopX managed heartbeat bootstrap v2",
            "Direct SQLite/TOML migration requires the App to be closed",
            "Generate A Review Packet",
            "loopx review-packet --goal-id",
            "loopx review-packet --goal-id <STABLE_GOAL_ID> --handoff-only",
            "loopx --format json review-packet --goal-id",
            "target project agent must not run this draft",
            "This command is read-only",
            "JSON output returns a minimized handoff payload with `handoff_text` instead of the full operator packet",
            "--classification <PUBLIC_SAFE_PROGRESS_CLASSIFICATION>",
            "--delivery-batch-scale <ACTUAL_DELIVERY_BATCH_SCALE>",
            "--delivery-outcome <ACTUAL_DELIVERY_OUTCOME>",
            "Never default or upgrade a smaller/preparatory turn",
            "do not infer scale/outcome from the classification name",
        ):
            assert phrase in compact_skill_text, phrase
        assert "JSON output still keeps the full payload" not in compact_skill_text, compact_skill_text
        pr_review_skill = codex_home / "skills" / "loopx-pr-review" / "SKILL.md"
        pr_review_text = " ".join(pr_review_skill.read_text(encoding="utf-8").split())
        for phrase in (
            "loopx --format json pr-review --state all",
            "thin host adapter",
            "agent_response_contract.review_execution_contract",
            "review_groups",
            "pull_requests[review_action_kind!=null].review_plan",
            "pull_requests[review_action_kind!=null].review_template",
            "pull_requests[review_action_kind!=null].evidence_commands",
            "Do not pipe the only copy through `jq`",
            "completion_gate",
            "Never infer `verified` from metadata or CI",
            "formal `REQUEST_CHANGES`",
            "Read the published review back",
            "Merge still routes through `loopx-pr-merge`",
        ):
            assert phrase in pr_review_text, phrase
        assert "Do not use this skill to approve" not in pr_review_text, pr_review_text
        pr_program_skill = codex_home / "skills" / "loopx-pr-program" / "SKILL.md"
        pr_program_text = " ".join(pr_program_skill.read_text(encoding="utf-8").split())
        for phrase in (
            "one `continuous_monitor` todo",
            "result_completeness.complete=true",
            "diff_snapshot.py",
            "Product requirements set priority",
            "Quiet monitor polls",
        ):
            assert phrase in pr_program_text, phrase
        assert (
            pr_program_skill.parent / "scripts" / "diff_snapshot.py"
        ).is_file()
        pr_review_metadata = pr_review_skill.parent / "agents" / "openai.yaml"
        pr_review_metadata_text = pr_review_metadata.read_text(encoding="utf-8")
        assert (
            'short_description: "Review LoopX PRs with deep key-code explanations"'
            in pr_review_metadata_text
        ), pr_review_metadata_text
        assert "Guide agentloop" not in pr_review_metadata_text, pr_review_metadata_text
        auto_research_skill = codex_home / "skills" / "loopx-auto-research" / "SKILL.md"
        assert not auto_research_skill.exists(), auto_research_skill
        material_skill = codex_home / "skills" / "loopx-material" / "SKILL.md"
        assert not material_skill.exists(), material_skill
        quality_skill = codex_home / "skills" / "loopx-change-quality" / "SKILL.md"
        assert not quality_skill.exists(), quality_skill
        loopx_prompt = codex_home / "prompts" / "loopx.md"
        assert not loopx_prompt.exists(), loopx_prompt
        loopx_command_skill = codex_home / "skills" / "loopx" / "SKILL.md"
        loopx_command_skill_text = loopx_command_skill.read_text(encoding="utf-8")
        assert "surface=codex-skills" in loopx_command_skill_text, loopx_command_skill_text
        assert "Identify the exact current host surface" in loopx_command_skill_text
        assert "`goal_start_contract` as authoritative" in loopx_command_skill_text
        loopx_openai_metadata = loopx_command_skill.parent / "agents" / "openai.yaml"
        loopx_openai_metadata_text = loopx_openai_metadata.read_text(encoding="utf-8")
        assert 'display_name: "LoopX"' in loopx_openai_metadata_text, loopx_openai_metadata_text
        assert 'display_name: "LoopX /loopx"' not in loopx_openai_metadata_text, loopx_openai_metadata_text
        assert "allow_implicit_invocation: false" in loopx_openai_metadata_text, loopx_openai_metadata_text
        loopx_project_metadata = codex_home / "skills" / "loopx-project" / "agents" / "openai.yaml"
        loopx_project_metadata_text = loopx_project_metadata.read_text(encoding="utf-8")
        assert 'display_name: "LoopX Project"' in loopx_project_metadata_text, loopx_project_metadata_text
        assert 'display_name: "LoopX"' not in loopx_project_metadata_text, loopx_project_metadata_text
        claude_loopx_skill = home / ".claude" / "skills" / "loopx" / "SKILL.md"
        claude_loopx_skill_text = claude_loopx_skill.read_text(encoding="utf-8")
        assert "surface=claude-skills" in claude_loopx_skill_text, claude_loopx_skill_text
        assert not (home / ".claude" / "commands" / "loopx.md").exists(), (
            "default installer must not install the opt-in Claude adapter command"
        )
        assert not (home / ".claude" / "settings.json").exists(), (
            "default installer must not install Claude adapter hooks/settings"
        )
        doc_registry_skill = codex_home / "skills" / "loopx-doc-registry" / "SKILL.md"
        doc_registry_text = " ".join(doc_registry_skill.read_text(encoding="utf-8").split())
        doc_registry_metadata = doc_registry_skill.parent / "agents" / "openai.yaml"
        doc_registry_metadata_text = doc_registry_metadata.read_text(encoding="utf-8")
        assert 'display_name: "LoopX Doc Registry"' in doc_registry_metadata_text
        assert 'display_name: "Loopx' not in doc_registry_metadata_text
        for phrase in (
            "Use even when the user does not mention LoopX or doc registry",
            "use `.loopx/registry.json` as the project-local doc registry",
            "not a substitute for project-local authority registration",
            "loopx --registry .loopx/registry.json register-authority-source",
        ):
            assert phrase in doc_registry_text, phrase
        benchmark_skill = codex_home / "skills" / "loopx-benchmark" / "SKILL.md"
        benchmark_text = " ".join(
            benchmark_skill.read_text(encoding="utf-8").split()
        )
        assert "builtin `benchmark-toolkit` capability" in benchmark_text
        assert "without a per-Goal enable switch" in benchmark_text
        benchmark_metadata = benchmark_skill.parent / "agents" / "openai.yaml"
        assert 'display_name: "LoopX Benchmark"' in benchmark_metadata.read_text(
            encoding="utf-8"
        )
        self_repair_skill = codex_home / "skills" / "loopx-self-repair" / "SKILL.md"
        self_repair_text = " ".join(self_repair_skill.read_text(encoding="utf-8").split())
        for phrase in (
            "Build a compact evidence packet",
            "loopx --format json diagnose --goal-id <goal-id>",
            "loopx --format json status --goal-id <goal-id> --limit 20",
            "status` defaults to the registry/dashboard view, but accepts `--goal-id`",
            "registry-declared active state file",
            "references/repair-patterns.md",
            "Repair at the lowest durable layer",
            "Do not solve contradictory payloads by guessing",
        ):
            assert phrase in self_repair_text, phrase
        self_repair_patterns = (
            codex_home
            / "skills"
            / "loopx-self-repair"
            / "references"
            / "repair-patterns.md"
        )
        self_repair_patterns_text = self_repair_patterns.read_text(encoding="utf-8")
        assert "`boundary_projection_gap`" in self_repair_patterns_text, self_repair_patterns_text
        assert "`skill_cli_contract_drift`" in self_repair_patterns_text, self_repair_patterns_text
        assert "`tiny_turn_under_delivery`" in self_repair_patterns_text, self_repair_patterns_text
        self_repair_issue_escalation = (
            codex_home
            / "skills"
            / "loopx-self-repair"
            / "references"
            / "upstream-issue-escalation.md"
        )
        self_repair_issue_escalation_text = self_repair_issue_escalation.read_text(
            encoding="utf-8"
        )
        assert "LOOPX_SELF_REPAIR_AUTO_ISSUE=1" in self_repair_issue_escalation_text
        assert "gh issue list" in self_repair_issue_escalation_text
        assert "gh issue create" in self_repair_issue_escalation_text
        assert (
            codex_home / "skills" / "loopx-self-repair" / "agents" / "openai.yaml"
        ).is_file()
        for implicit_skill_name in PACKAGED_HOST_SKILL_IDS:
            implicit_metadata = codex_home / "skills" / implicit_skill_name / "agents" / "openai.yaml"
            if implicit_metadata.exists():
                implicit_metadata_text = implicit_metadata.read_text(encoding="utf-8")
                assert "allow_implicit_invocation: false" not in implicit_metadata_text, (
                    implicit_skill_name,
                    implicit_metadata_text,
                )

        cli_env = {**env, "PATH": f"{bin_dir}:{env['PATH']}"}
        runtime_run_dir = home / ".codex" / "loopx" / "goals" / "loopx-meta" / "runs"
        generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        write_promotion_readiness(runtime_run_dir, generated_at=generated_at, label="fresh")

        doctor = subprocess.run(
            ["loopx", "--format", "json", "doctor"],
            cwd=root,
            env=cli_env,
            check=True,
            capture_output=True,
            text=True,
        )
        doctor_payload = json.loads(doctor.stdout)
        assert doctor_payload["ok"] is True, doctor_payload
        freshness = doctor_payload["install_freshness"]
        assert freshness["schema_version"] == "loopx_install_freshness_v0", freshness
        assert freshness["status"] == "unknown", freshness
        assert freshness["requires_upgrade"] is False, freshness
        assert freshness["current_version"], freshness
        assert freshness["current_version_tag"] == release_version_tag(), freshness
        assert freshness["manifest_package_version"] == __version__, freshness
        assert freshness["manifest_package_version_tag"] == release_version_tag(), freshness
        assert freshness["manifest_package_version_matches_runtime"] is True, freshness
        assert freshness["release_manifest_available"] is True, freshness
        assert freshness["release_manifest_path"] == str(release_manifest_path), freshness
        assert freshness["manifest_source_kind"] == "local_checkout", freshness
        assert freshness["manifest_source_git_commit"] == source_commit, freshness
        assert freshness["manifest_source_git_commit_short"] == source_commit[:12], freshness
        assert freshness["manifest_source_revision"] == source_commit, freshness
        assert freshness["manifest_skills_digest"] == release_manifest["skills"]["digest"], freshness
        assert "loopx-project.github.io/loopx/install.sh" in freshness["upgrade_command"], freshness
        assert "loopx doctor" in freshness["upgrade_command"], freshness
        assert doctor_payload["upgrade_hint"] == freshness, doctor_payload
        assert doctor_payload["path"]["loopx"] == str(wrapper), doctor_payload
        assert doctor_payload["path"]["loopx_realpath"] == str(wrapper.resolve()), doctor_payload
        assert doctor_payload["path"]["loopx_canary"] == str(canary_wrapper), doctor_payload
        assert doctor_payload["path"]["loopx_canary_realpath"] == str(canary_wrapper.resolve()), doctor_payload
        assert doctor_payload["package"]["release_root"] == str(release_root), doctor_payload
        assert doctor_payload["package"]["release_manifest_path"] == str(release_manifest_path), doctor_payload
        assert doctor_payload["release_manifest"]["available"] is True, doctor_payload
        assert doctor_payload["release_manifest"]["path"] == str(release_manifest_path), doctor_payload
        assert doctor_payload["release_manifest"]["manifest"]["source"]["kind"] == "local_checkout", doctor_payload
        assert (
            doctor_payload["release_manifest"]["manifest"]["source"]["git_commit"] == source_commit
        ), doctor_payload
        assert doctor_payload["release_manifest"]["manifest"]["skills"]["digest"] == release_manifest["skills"]["digest"], doctor_payload
        assert doctor_payload["skill"]["path"] == str(skill), doctor_payload
        assert doctor_payload["skill"]["exists"] is True, doctor_payload
        assert doctor_payload["skill"]["delivery_hints"] is True, doctor_payload
        assert "loopx-auto-research" not in doctor_payload["skills"], doctor_payload
        assert "loopx-material" not in doctor_payload["skills"], doctor_payload
        assert "loopx-change-quality" not in doctor_payload["skills"], doctor_payload
        assert {
            "loopx-material",
            "loopx-change-quality",
        }.issubset(set(doctor_payload["project_scoped_skill_ids"])), doctor_payload
        assert doctor_payload["globally_visible_project_skills"] == [], doctor_payload
        for skill_id in PACKAGED_HOST_SKILL_IDS:
            assert doctor_payload["skills"][skill_id]["exists"] is True, doctor_payload
            assert (
                doctor_payload["skills"][skill_id]["required_phrases"] is True
            ), doctor_payload
        provenance = doctor_payload["release_provenance"]
        assert provenance["default_release"]["root"] == str(release_root), provenance
        assert provenance["default_release"]["release_id"] == release_root.name, provenance
        assert provenance["default_release"]["is_release_snapshot"] is True, provenance
        assert provenance["default_release"]["release_manifest_available"] is True, provenance
        assert provenance["default_release"]["release_manifest_path"] == str(release_manifest_path), provenance
        assert provenance["default_release"]["promotion_mode"] == "explicit_override", provenance
        assert provenance["live_canary"]["root"] == str(REPO_ROOT), provenance
        assert provenance["live_canary"]["separate_from_default"] is True, provenance
        assert provenance["current_invocation"]["source"] == "release_snapshot", provenance
        assert provenance["promotion_readiness"]["available"] is True, provenance
        assert provenance["promotion_readiness"]["goal_id"] == "loopx-meta", provenance
        assert provenance["promotion_readiness"]["classification"] == "canary_promotion_readiness_smoke_group", provenance
        assert provenance["promotion_readiness"]["delivery_outcome"] == "primary_goal_outcome", provenance
        assert provenance["promotion_readiness"]["freshness_status"] == "fresh", provenance
        assert provenance["promotion_readiness"]["is_fresh"] is True, provenance
        assert provenance["promotion_readiness"]["requires_readiness_run"] is False, provenance
        assert provenance["promotion_readiness"]["freshness_window_hours"] == 24, provenance
        assert provenance["promotion_readiness"]["json_exists"] is True, provenance
        assert provenance["promotion_readiness"]["markdown_exists"] is True, provenance
        doctor_checks = {check["id"]: check for check in doctor_payload["checks"]}
        for check_id in (
            "default_command_is_release_snapshot",
            "canary_command_on_path",
            "canary_separate_from_default",
            "installed_skill_exists",
            "installed_skill_delivery_hints",
            "installed_required_skills",
            "installed_required_skill_routes",
            "project_scoped_skills_absent_globally",
        ):
            assert doctor_checks[check_id]["ok"] is True, doctor_payload

        doctor_markdown = subprocess.run(
            ["loopx", "doctor"],
            cwd=root,
            env=cli_env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "installed_skill_delivery_hints: `True`" in doctor_markdown, doctor_markdown
        assert (
            "installed_required_skills: "
            f"`{','.join(sorted(PACKAGED_HOST_SKILL_IDS))}`"
            in doctor_markdown
        ), doctor_markdown
        assert "loopx_canary_realpath:" in doctor_markdown, doctor_markdown
        assert "release_root:" in doctor_markdown, doctor_markdown
        assert "## Release Provenance" in doctor_markdown, doctor_markdown
        assert "## Install Freshness" in doctor_markdown, doctor_markdown
        assert "schema_version: `loopx_install_freshness_v0`" in doctor_markdown, doctor_markdown
        assert "status: `unknown`" in doctor_markdown, doctor_markdown
        assert f"current_version_tag: `{release_version_tag()}`" in doctor_markdown, doctor_markdown
        assert f"manifest_package_version: `{__version__}`" in doctor_markdown, doctor_markdown
        assert f"manifest_package_version_tag: `{release_version_tag()}`" in doctor_markdown, doctor_markdown
        assert "manifest_package_version_matches_runtime: `True`" in doctor_markdown, doctor_markdown
        assert "release_manifest_available: `True`" in doctor_markdown, doctor_markdown
        assert "default_promotion_mode: `explicit_override`" in doctor_markdown, doctor_markdown
        assert f"manifest_source_git_commit: `{source_commit[:12]}`" in doctor_markdown, doctor_markdown
        assert "manifest_source: `local_checkout` @ `n/a`" not in doctor_markdown, doctor_markdown
        assert "manifest_skills_digest:" in doctor_markdown, doctor_markdown
        assert "loopx-project.github.io/loopx/install.sh" in doctor_markdown, doctor_markdown
        assert "latest_promotion_readiness: available=`True`" in doctor_markdown, doctor_markdown
        assert "freshness=`fresh`" in doctor_markdown, doctor_markdown
        assert "requires_readiness_run=`False`" in doctor_markdown, doctor_markdown

        stale = add_promotion_readiness_freshness(
            {
                "available": True,
                "generated_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
            }
        )
        assert stale["freshness_status"] == "stale", stale
        assert stale["is_fresh"] is False, stale
        assert stale["requires_readiness_run"] is True, stale
        missing = add_promotion_readiness_freshness({"available": False})
        assert missing["freshness_status"] == "missing", missing
        assert missing["requires_readiness_run"] is True, missing

        stale_install = build_install_freshness(
            command_path=wrapper,
            release_root=root / "releases" / "20260101T000000Z",
            repo_root=REPO_ROOT,
            skills={
                skill_id: {"exists": True, "required_phrases": True}
                for skill_id in PACKAGED_HOST_SKILL_IDS
            },
            now=datetime(2026, 1, 9, tzinfo=timezone.utc),
        )
        assert stale_install["status"] == "stale", stale_install
        assert stale_install["requires_upgrade"] is True, stale_install
        assert stale_install["release_age_hours"] == 192.0, stale_install
        assert "loopx-project.github.io/loopx/install.sh" in stale_install["no_clone_upgrade_command"], stale_install

        fresh_install = build_install_freshness(
            command_path=wrapper,
            release_root=root / "releases" / "20260108T000000Z",
            repo_root=REPO_ROOT,
            skills={
                skill_id: {"exists": True, "required_phrases": True}
                for skill_id in PACKAGED_HOST_SKILL_IDS
            },
            now=datetime(2026, 1, 9, tzinfo=timezone.utc),
        )
        assert fresh_install["status"] == "fresh", fresh_install
        assert fresh_install["requires_upgrade"] is False, fresh_install

        canary_mismatch_install = build_install_freshness(
            command_path=wrapper,
            release_root=root / "releases" / "20260108T000000Z",
            repo_root=REPO_ROOT,
            skills={
                skill_id: {"exists": True, "required_phrases": True}
                for skill_id in PACKAGED_HOST_SKILL_IDS
            },
            release_manifest={
                "available": True,
                "path": str(root / "release.json"),
                "reason": None,
                "manifest": {
                    "package": {"version": __version__},
                    "source": {
                        "kind": "local_checkout",
                        "git_commit": "a" * 40,
                        "git_ref": "main",
                        "git_dirty": False,
                    },
                    "skills": {"digest": "fixture-skills-digest"},
                },
            },
            comparison_source={
                "label": "loopx-canary",
                "root": str(REPO_ROOT),
                "git_commit": "b" * 40,
                "git_ref": "main",
                "git_dirty": False,
                "revision_relation": "unknown",
            },
            now=datetime(2026, 1, 8, 1, tzinfo=timezone.utc),
        )
        assert canary_mismatch_install["status"] == "fresh", canary_mismatch_install
        assert canary_mismatch_install["requires_upgrade"] is False, canary_mismatch_install
        assert canary_mismatch_install["release_age_hours"] == 1.0, canary_mismatch_install
        assert canary_mismatch_install["manifest_source_matches_comparison"] is False, canary_mismatch_install
        assert canary_mismatch_install["comparison_source_git_commit_short"] == "b" * 12, canary_mismatch_install
        assert canary_mismatch_install["manifest_source_comparison_relation"] == "unknown", canary_mismatch_install

        cli = subprocess.run(
            [
                "loopx",
                "--format",
                "json",
                "heartbeat-prompt",
                "--goal-id",
                "installer-smoke-goal",
                "--active-state",
                "/tmp/public-installer-smoke/ACTIVE_GOAL_STATE.md",
            ],
            cwd=REPO_ROOT,
            env=cli_env,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(cli.stdout)
        assert payload["ok"] is True, payload
        assert payload["schema_version"] == "heartbeat_agent_input_v1", payload
        expected_quota_guard = (
            'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" '
            'quota should-run --goal-id installer-smoke-goal '
            '--turn-instance-id "${LOOPX_TURN:?}"'
        )
        assert expected_quota_guard in payload["task_body"], payload
        assert payload["interface_budget"]["mode"] == "thin", payload
        assert payload["interface_budget"]["within_budget"] is True, payload
        for generator_only_field in (
            "quota_guard_command",
            "quota_spend_command",
            "progress_refresh_state_command",
            "cli_bin",
        ):
            assert generator_only_field not in payload, payload
        assert normal_turns_use_cli_interaction_contract(payload["task_body"]), payload
        assert not normal_turns_use_cli_interaction_contract(
            "Normal turns use the runtime skill; recovery may inspect CLI `interaction_contract`."
        )
        assert not normal_turns_use_cli_interaction_contract(
            "Normal turns use the runtime skill and repair contract."
        )
        assert "```sh\nLOOPX_TURN=<current_time_iso>\n" in payload["task_body"], payload
        assert "not a command-prefix assignment" in payload["task_body"], payload
        assert "guard; 2 stalls->replan" in payload["task_body"], payload
        assert "no-change=surface_only/no spend" in payload["task_body"], payload

        canary_cli = subprocess.run(
            [
                "loopx-canary",
                "--format",
                "json",
                "heartbeat-prompt",
                "--goal-id",
                "installer-canary-smoke-goal",
                "--active-state",
                "/tmp/public-installer-canary-smoke/ACTIVE_GOAL_STATE.md",
                "--brief",
                "--cli-bin",
                "loopx-canary",
            ],
            cwd=REPO_ROOT,
            env=cli_env,
            check=True,
            capture_output=True,
            text=True,
        )
        canary_payload = json.loads(canary_cli.stdout)
        assert canary_payload["cli_bin"] == "loopx-canary", canary_payload
        assert "loopx-canary doctor" in canary_payload["cli_preflight"], canary_payload
        assert "loopx-canary --format json" in canary_payload["quota_guard_command"], canary_payload
        assert "loopx-canary heartbeat-prompt --compact" in canary_payload["task_body"], canary_payload
        canary_task_body = canary_payload["task_body"]
        # Brief mode renders one bounded guard block: it deliberately omits the
        # accountable refresh/spend pair, which belongs to the full and compact
        # modes. Assert the brief contract instead of the retired sequence.
        assert canary_payload["quota_guard_command"] in canary_task_body, canary_payload
        assert canary_payload["progress_refresh_state_command"] not in canary_task_body, canary_payload
        assert canary_payload["quota_spend_command"] not in canary_task_body, canary_payload
        assert "```bash\n" in canary_task_body and "LOOPX_TURN=<current_time_iso>" in canary_task_body, canary_payload
        assert "not a command-prefix assignment" in canary_task_body, canary_payload

        # The initial install exercises the default post-install extension
        # revalidation. Repeated fixture installs do not add coverage for that
        # same provider scan, so skip the optional pass to keep this smoke
        # inside the public-suite timeout budget.
        fresh_install = run_install(
            env, "install-smoke-fresh", revalidate_extensions=False
        )
        assert "loopx installed locally" in fresh_install.stdout, fresh_install.stdout
        assert "loopx install warning" not in fresh_install.stderr, fresh_install.stderr

        stale_generated_at = (datetime.now(timezone.utc) - timedelta(hours=25)).replace(microsecond=0).isoformat()
        write_promotion_readiness(runtime_run_dir, generated_at=stale_generated_at, label="stale")
        stale_install = run_install(
            env, "install-smoke-stale", revalidate_extensions=False
        )
        assert "loopx installed locally" in stale_install.stdout, stale_install.stdout
        assert "promotion-readiness evidence is stale" in stale_install.stderr, stale_install.stderr
        assert "age_hours=" in stale_install.stderr, stale_install.stderr
        assert "non-blocking" in stale_install.stderr, stale_install.stderr

        blocked_opencode_root = home / ".config" / "opencode-blocked"
        blocked_opencode_root.mkdir(parents=True)
        (blocked_opencode_root / "opencode.jsonc").write_text(
            "{ invalid\n",
            encoding="utf-8",
        )
        blocked_opencode_install = run_install(
            {
                **env,
                "LOOPX_INSTALL_OPENCODE": "1",
                "OPENCODE_CONFIG_DIR": str(blocked_opencode_root),
            },
            "install-smoke-opencode-blocked",
            revalidate_extensions=False,
        )
        assert (
            "loopx OpenCode bridge: install attempted; run manually:"
            in blocked_opencode_install.stdout
        ), blocked_opencode_install.stdout
        assert (blocked_opencode_root / "commands" / "loopx.md").is_file()
        assert not (blocked_opencode_root / "plugins" / "loopx-goal.js").exists()
        assert not (blocked_opencode_root / "loopx" / "goal-bridge-runtime.mjs").exists()
        assert not (blocked_opencode_root / "package.json").exists()

        opencode_install = run_install(
            {**env, "LOOPX_INSTALL_OPENCODE": "1"},
            "install-smoke-opencode",
            revalidate_extensions=False,
        )
        assert "loopx OpenCode bridge:" in opencode_install.stdout, opencode_install.stdout
        assert (opencode_root / "commands" / "loopx.md").is_file()
        assert (opencode_root / "plugins" / "loopx-goal.js").is_file()
        assert (opencode_root / "loopx" / "goal-bridge-runtime.mjs").is_file()
        assert (opencode_root / "package.json").is_file()

    print("install-local-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
