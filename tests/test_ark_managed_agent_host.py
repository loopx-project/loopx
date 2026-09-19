from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

from loopx.agent_onboarding import (
    REQUIRED_HOST_SKILL_IDS,
    _skill_delivery_contract,
    build_agent_onboarding_packet,
)
from loopx.control_plane.testing.canary_harness import (
    run_json_cli,
    write_fixture_registry,
)
from loopx.doctor import collect_doctor
from loopx.heartbeat_prompt import build_heartbeat_prompt
from loopx.host_loop_activation import (
    agent_type_uses_host_managed_skills,
    build_host_loop_activation_packet,
)
from loopx.skill_install_readback import (
    PACKAGED_HOST_SKILL_IDS,
    SKILL_INSTALL_READBACK_FILENAME,
    inspect_skill_install_readback,
    retire_duplicate_managed_skills,
    write_skill_install_readback,
)
from loopx.slash_command_install import materialize_loopx_entry_skill

REPO_ROOT = Path(__file__).resolve().parents[1]


def _materialize_workflow_skills(skills_dir: Path) -> None:
    for skill_id in PACKAGED_HOST_SKILL_IDS:
        shutil.copytree(REPO_ROOT / "skills" / skill_id, skills_dir / skill_id)
    entry = materialize_loopx_entry_skill(
        skills_dir=skills_dir,
        execute=True,
        host_surface="ark-managed-agent",
    )
    assert entry["status"] == "created"
    write_skill_install_readback(
        skills_dir=skills_dir,
        skill_ids=REQUIRED_HOST_SKILL_IDS,
        source_root=REPO_ROOT,
    )


CONTINUITY_GOAL_ID = "managed-agent-continuity-fixture"
CONTINUITY_AGENT_ID = "managed-agent"
CONTINUITY_TODO_ID = "todo_aaaaaaaaaaaa"


def _goal_prompt() -> dict:
    return build_heartbeat_prompt(
        goal_id="managed-agent-fixture",
        active_state=Path("/workspace/ACTIVE_GOAL_STATE.md"),
        thin=True,
        runtime_profile="ark_managed_agent_goal",
    )


def test_goal_prompt_is_one_transport_independent_activation() -> None:
    local_development = _goal_prompt()
    cloud = _goal_prompt()
    normalized = " ".join(local_development["task_body"].split())

    assert local_development["task_body"] == cloud["task_body"]
    assert len(local_development["task_body"]) <= 4_000
    assert "in one Goal activation" in normalized
    assert "Goal runtime owns continuation and inner iterations" in normalized
    assert "goal loop, not automation" in normalized
    assert "invoke LoopX Turn" in normalized
    assert "Progress is not a new Goal boundary" in normalized
    assert "do not create a new host Goal merely to continue" in normalized
    assert "current `interaction_contract`" in normalized
    assert "selection_command" in normalized
    assert "settlement_plan.ordered_steps" in normalized
    assert "terminal no-follow-up" in normalized
    assert local_development["progress_refresh_state_command"] not in local_development["task_body"]
    assert local_development["quota_spend_command"] not in local_development["task_body"]


def test_goal_prompt_projects_goal_only_host_contract() -> None:
    payload = _goal_prompt()

    assert payload["runtime_profile"] == "ark_managed_agent_goal"
    assert payload["host_contract"] == {
        "schema_version": "loopx_ark_managed_agent_goal_host_v0",
        "host_kind": "ark-managed-agent",
        "activation_mode": "goal_once",
        "prompt_family": "loopx_goal_prompt_v0",
        "policy_source": "quota_should_run.interaction_contract",
        "transport_contract": "goal_prompt_v0",
        "goal_runtime_owns_continuation": True,
        "goal_lifecycle_scope": "registered_goal_until_terminal",
        "phase_handoff_allowed": False,
        "loopx_turn_driver_required": False,
        "session_state_authoritative": False,
        "goal_runtime_continuation": {
            "source_ref": (
                "quota_should_run.scheduler_hint.goal_runtime_continuation"
            ),
            "packet_schema_version": "goal_runtime_continuation_v0",
            "dispositions": ["continue_now", "defer", "complete"],
            "defer_wake_policy": "state_change_or_deadline",
            "reason_source_ref": "quota_should_run.scheduler_hint.reason_code",
            "state_identity_source_ref": (
                "quota_should_run.scheduler_hint.reset_policy"
            ),
            "goal_prompt_mutated": False,
        },
        "runtime_capability_reentry": {
            "source_ref": (
                "quota_should_run.interaction_contract.cli_channel."
                "runtime_capability_reentry"
            ),
            "cli_projection_ref": "quota_should_run.runtime_capability_reentry",
            "packet_schema_version": "runtime_capability_reentry_v0",
            "delivery_channel": "quota_tool_result",
            "goal_prompt_mutated": False,
            "session_scoped": True,
            "durable_grant_written": False,
        },
    }
    assert "--runtime-profile ark_managed_agent_goal" in payload["task_body"]
    assert "runtime_capability_reentry_v0" not in payload["task_body"]
    assert "Before the first quota guard" not in payload["task_body"]
    assert "--available-capability <name>" not in payload["task_body"]


def test_host_activation_submits_one_goal_without_turn_or_automation() -> None:
    packet = build_host_loop_activation_packet(
        agent_type="ark-managed-agent",
        goal_id="managed-agent-fixture",
        agent_id="managed-agent",
        registered_agents=["managed-agent"],
    )

    assert packet["activation_method"] == "submit_goal_once"
    assert packet["host_surface"] == "ark_managed_agent_goal_mode"
    assert packet["host_mutation"]["transport_contract"] == "goal_prompt_v0"
    assert packet["host_mutation"]["prompt_field"] == "task_body"
    assert any(
        "runtime_capability_reentry_v0" in step and "do not rewrite task_body" in step
        for step in packet["activation_steps"]
    )
    prompt_args = shlex.split(packet["commands"]["heartbeat_prompt"])
    assert prompt_args[prompt_args.index("--runtime-profile") + 1] == "ark_managed_agent_goal"
    assert prompt_args.count("--bootstrap") == 1
    assert "automation_update" not in str(packet)
    assert "loopx turn run-once" not in str(packet).lower()


def test_host_requires_host_managed_loopx_skill_delivery(monkeypatch) -> None:
    monkeypatch.delenv("LOOPX_SKILLS_DIR", raising=False)
    contract = _skill_delivery_contract("ark-managed-agent")

    assert contract["mode"] == "host_managed"
    assert contract["owner"] == "loopx_install_script"
    assert contract["required_skill_ids"] == REQUIRED_HOST_SKILL_IDS
    assert contract["host_readback_required"] is True
    assert contract["codex_skills_root_required"] is False
    assert contract["preferred_delivery"] == "fixed_install_script"
    assert contract["onboarding_role"] == "read_only_verifier"
    assert contract["onboarding_required_for_install"] is False
    assert contract["install_script"] == "scripts/install-local.sh"
    assert (
        contract["no_clone_install_command"] == "curl -fsSL "
        "https://loopx-project.github.io/loopx/install.sh"
        " | env LOOPX_SKILLS_DIR=./.agents/skills "
        "LOOPX_ENTRY_HOST_SURFACE=ark-managed-agent "
        "LOOPX_INSTALL_SLASH_COMMANDS=0 bash"
    )
    assert contract["skills_dir_env"] == "LOOPX_SKILLS_DIR"
    assert contract["entry_host_surface_env"] == "LOOPX_ENTRY_HOST_SURFACE"
    assert contract["entry_host_surface"] == "ark-managed-agent"
    assert contract["target_layout"] == "./.agents/skills"
    assert contract["generated_skill_ids"] == ["loopx"]
    assert "skills/loopx" not in contract["source_directories"]
    assert contract["fixed_installer_skill_ids"] == REQUIRED_HOST_SKILL_IDS
    assert contract["filesystem_readback"]["status"] == "skills_dir_not_configured"
    assert agent_type_uses_host_managed_skills("ark-managed-agent") is True


def test_host_onboarding_starts_from_loopx_skill_before_goal_submission() -> None:
    from loopx.agent_onboarding import _start_instruction

    instruction = _start_instruction("ark-managed-agent")

    assert instruction.startswith("Use `$loopx <task>` as the ordinary task entry")
    assert "todo writeback" in instruction
    assert "submit the generated Goal task body exactly once" in instruction


def test_doctor_checks_host_installation_readback_instead_of_codex_skill_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skills_dir = tmp_path / "workspace" / ".agents" / "skills"
    _materialize_workflow_skills(skills_dir)
    monkeypatch.setenv("LOOPX_SKILLS_DIR", str(skills_dir))

    payload = collect_doctor(agent_type="ark-managed-agent")

    assert payload["skill_delivery"]["mode"] == "host_managed"
    assert payload["skill_delivery"]["owner"] == "loopx_install_script"
    assert payload["skill_delivery"]["codex_skills_root_applicable"] is False
    readback = payload["skill_delivery"]["filesystem_readback"]
    assert readback["status"] == "ready_for_host_load"
    assert readback["ready"] is True
    assert readback["source_revision"]
    skill_checks = {
        item["id"]: item for item in payload["checks"] if item["id"].startswith("installed_")
    }
    assert skill_checks
    assert all(item["applicable"] is False for item in skill_checks.values())
    host_check = next(
        item
        for item in payload["checks"]
        if item["id"] == "host_skill_installation_readback"
    )
    assert host_check["ok"] is True
    assert host_check["required"] is False


def test_onboarding_projects_verified_filesystem_readback(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / ".agents" / "skills"
    _materialize_workflow_skills(skills_dir)

    contract = _skill_delivery_contract(
        "ark-managed-agent",
        host_skills_dir=skills_dir,
    )

    assert contract["status"] == "ready_for_host_load"
    assert contract["filesystem_readback"]["ready"] is True
    assert contract["host_readback_required"] is True


def test_skill_readback_rejects_content_changed_after_install(tmp_path: Path) -> None:
    skills_dir = tmp_path / ".agents" / "skills"
    _materialize_workflow_skills(skills_dir)
    skill_path = skills_dir / REQUIRED_HOST_SKILL_IDS[0] / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8") + "\npost-install mutation\n",
        encoding="utf-8",
    )

    readback = inspect_skill_install_readback(
        skills_dir=skills_dir,
        required_skill_ids=REQUIRED_HOST_SKILL_IDS,
    )

    assert readback["ready"] is False
    assert readback["status"] == "skill_digest_mismatch"
    assert readback["digest_mismatches"] == [REQUIRED_HOST_SKILL_IDS[0]]


def test_skill_readback_rejects_a_different_cli_source_revision(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / ".agents" / "skills"
    _materialize_workflow_skills(skills_dir)
    manifest_path = skills_dir / SKILL_INSTALL_READBACK_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["revision"] = "0" * 40
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    readback = inspect_skill_install_readback(
        skills_dir=skills_dir,
        required_skill_ids=REQUIRED_HOST_SKILL_IDS,
        source_root=REPO_ROOT,
    )

    assert readback["ready"] is False
    assert readback["status"] == "source_revision_mismatch"
    assert readback["source_revision_matches"] is False


def test_skill_readback_records_the_resolved_archive_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skills_dir = tmp_path / ".agents" / "skills"
    _materialize_workflow_skills(skills_dir)
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    resolved_commit = "a" * 40
    monkeypatch.setenv("LOOPX_RESOLVED_SOURCE_GIT_COMMIT", resolved_commit)
    write_skill_install_readback(
        skills_dir=skills_dir,
        skill_ids=REQUIRED_HOST_SKILL_IDS,
        source_root=archive_root,
    )
    (archive_root / "release.json").write_text(
        json.dumps({"source": {"git_commit": resolved_commit}}) + "\n",
        encoding="utf-8",
    )

    readback = inspect_skill_install_readback(
        skills_dir=skills_dir,
        required_skill_ids=REQUIRED_HOST_SKILL_IDS,
        source_root=archive_root,
    )

    assert readback["ready"] is True
    assert readback["source_revision"] == resolved_commit
    assert readback["source_revision_matches"] is True


def test_retire_duplicate_managed_skills_removes_only_loopx_managed_copies(
    tmp_path: Path,
) -> None:
    codex_skills = tmp_path / ".codex" / "skills"
    agents_skills = tmp_path / ".agents" / "skills"
    _materialize_workflow_skills(codex_skills)
    _materialize_workflow_skills(agents_skills)
    canonical_facade = codex_skills / "loopx-global-gates" / "SKILL.md"
    canonical_facade.parent.mkdir(parents=True)
    canonical_facade.write_text(
        "<!-- loopx-managed-slash-command:v1 command=/loopx-global-gates -->\n",
        encoding="utf-8",
    )
    facade = agents_skills / "loopx-global-gates" / "SKILL.md"
    facade.parent.mkdir(parents=True)
    facade.write_text(
        "<!-- loopx-managed-slash-command:v1 command=/loopx-global-gates -->\n",
        encoding="utf-8",
    )
    user_file = agents_skills / "loopx-project" / "SKILL.md"
    user_file.write_text(
        user_file.read_text(encoding="utf-8") + "\nuser-edit\n",
        encoding="utf-8",
    )

    dry = retire_duplicate_managed_skills(
        codex_skills,
        alternate_root=agents_skills,
        execute=False,
    )
    assert dry["ok"] is True
    assert "loopx-global-gates" in dry["would_retire"]
    assert "loopx-project" in dry["skipped"]
    assert agents_skills.is_dir()

    applied = retire_duplicate_managed_skills(
        codex_skills,
        alternate_root=agents_skills,
        execute=True,
    )
    assert applied["ok"] is True
    assert "loopx-global-gates" in applied["retired"]
    assert not (agents_skills / "loopx-global-gates").exists()
    assert (agents_skills / "loopx-project").exists()
    assert (codex_skills / "loopx-project").exists()


def _write_continuity_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state = (
        project
        / ".codex"
        / "goals"
        / CONTINUITY_GOAL_ID
        / "ACTIVE_GOAL_STATE.md"
    )
    state.parent.mkdir(parents=True)
    state.write_text(
        "\n".join(
            [
                "---",
                "status: active",
                "updated_at: 2026-07-29T00:00:00+00:00",
                "---",
                "",
                "# Managed Agent continuity fixture",
                "",
                "## Agent Todo",
                "",
                "- [ ] [P0] Continue the public continuity qualification.",
                (
                    "  <!-- loopx:todo "
                    f"todo_id={CONTINUITY_TODO_ID} "
                    "status=open task_class=advancement_task "
                    "action_kind=qualification "
                    f"claimed_by={CONTINUITY_AGENT_ID} priority=P0 -->"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    registry = project / ".loopx" / "registry.json"
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=CONTINUITY_GOAL_ID,
        domain="managed-agent-continuity",
        adapter_kind="harness_self_improvement",
        registered_agents=[CONTINUITY_AGENT_ID],
        quota_allowed_slots=None,
    )
    return project, runtime, registry


def test_host_onboarding_replays_the_managed_agent_surface(tmp_path: Path) -> None:
    project, _runtime, _registry = _write_continuity_fixture(tmp_path)

    payload = build_agent_onboarding_packet(
        project=project,
        agent_type="ark-managed-agent",
        goal_id=CONTINUITY_GOAL_ID,
        agent_id=CONTINUITY_AGENT_ID,
        task_text="Continue the public qualification.",
    )

    command = payload["commands"]["bootstrap_command_pack"]
    assert "--host-surface ark-managed-agent" in command
    assert "worker-bridge" not in command


def _fresh_host_quota_read(
    *,
    project: Path,
    runtime: Path,
    registry: Path,
) -> dict:
    return run_json_cli(
        "quota",
        "should-run",
        "--goal-id",
        CONTINUITY_GOAL_ID,
        "--agent-id",
        CONTINUITY_AGENT_ID,
        "--runtime-profile",
        "ark_managed_agent_goal",
        "--scan-root",
        str(project),
        registry_path=registry,
        runtime_root=runtime,
    )


def test_fresh_host_reconstructs_frontier_from_durable_loopx_state(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_continuity_fixture(tmp_path)

    first_host = _fresh_host_quota_read(
        project=project,
        runtime=runtime,
        registry=registry,
    )
    assert first_host["should_run"] is True
    assert first_host["selected_todo"]["todo_id"] == CONTINUITY_TODO_ID

    run_json_cli(
        "todo",
        "update",
        "--goal-id",
        CONTINUITY_GOAL_ID,
        "--todo-id",
        CONTINUITY_TODO_ID,
        "--agent-id",
        CONTINUITY_AGENT_ID,
        "--note",
        "phase A validated; resume from the durable frontier",
        registry_path=registry,
        runtime_root=runtime,
    )

    # run_json_cli launches a fresh Python process for each read. This models a
    # replacement host session: only the shared registry/runtime/state paths
    # survive, while no in-memory host object or opaque session handle does.
    replacement_host = _fresh_host_quota_read(
        project=project,
        runtime=runtime,
        registry=registry,
    )
    todo_readback = run_json_cli(
        "todo",
        "list",
        "--goal-id",
        CONTINUITY_GOAL_ID,
        "--agent-id",
        CONTINUITY_AGENT_ID,
        registry_path=registry,
        runtime_root=runtime,
    )

    assert replacement_host["should_run"] is True
    assert replacement_host["selected_todo"]["todo_id"] == CONTINUITY_TODO_ID
    selected = next(
        item
        for item in todo_readback["agent_todos"]["items"]
        if item["todo_id"] == CONTINUITY_TODO_ID
    )
    assert selected["note"] == "phase A validated; resume from the durable frontier"
