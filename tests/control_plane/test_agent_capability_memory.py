"""Separate CLI processes must share only the registered agent's local observations."""
import json

import pytest

from loopx.cli import main

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, _run_cli, _write_fixture,
)


def test_next_turn_reuses_declared_runtime_capability(tmp_path):
    project, runtime, registry = _write_fixture(tmp_path, required_capability="network", provider="sqlite")
    before = registry.read_bytes()
    args = ("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    rc, first = _run_cli(registry, runtime, *args, "--available-capability", "network")
    assert rc == 0, first
    dispatch = first["turn_start_capability_hook_dispatch"]
    memory_result = next(
        item
        for item in dispatch["results"]
        if item["hook_id"] == "agent.capability_memory"
    )
    assert memory_result["local_private_state_mutated"] is True
    assert memory_result["external_writes_performed"] is False
    rc, next_turn = _run_cli(registry, runtime, *args)
    assert rc == 0, next_turn
    assert next_turn["capability_gate"]["action"] == "run"
    assert registry.read_bytes() == before


def test_goal_inheritance_local_failure_recovery_and_forget(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network", claimed=False, provider="sqlite")
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["available_capabilities"] = ["network"]
    data["goals"][0]["coordination"]["registered_agents"].append("agent-b")
    registry.write_text(json.dumps(data))
    before = registry.read_bytes()
    inspect = ("agent-capabilities", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    quota = ("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    rc, preview = _run_cli(registry, runtime, *inspect, "--unavailable-capability", "network")
    assert rc == 0, preview
    assert preview["availability"]["effective"] == ["network"]
    assert preview["agent_capabilities"]["proposed"]["unavailable"] == ["network"]
    assert not (runtime / "agent-runtime-capabilities").exists()
    rc, changed = _run_cli(registry, runtime, *inspect, "--unavailable-capability", "network", "--execute")
    assert rc == 0 and changed["agent_capabilities"]["written"], changed
    rc, blocked = _run_cli(registry, runtime, *quota)
    assert rc == 0 and blocked["capability_gate"]["action"] == "repair_bridge", blocked
    rc, peer = _run_cli(registry, runtime, "quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", "agent-b")
    assert rc == 0 and peer["capability_gate"]["action"] == "run", peer
    rc, recovered = _run_cli(registry, runtime, *quota, "--available-capability", "network")
    assert rc == 0 and recovered["capability_gate"]["action"] == "run", recovered
    rc, forgotten = _run_cli(registry, runtime, *inspect, "--forget-capability", "network", "--execute")
    assert rc == 0, forgotten
    assert forgotten["agent_capabilities"]["available"] == []
    assert forgotten["availability"]["effective"] == ["network"]
    assert registry.read_bytes() == before


def test_owner_authority_and_optional_enablement_are_not_remembered(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="credentials", provider="sqlite")
    args = ("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    rc, first = _run_cli(registry, runtime, *args, "--available-capability", "credentials",
                         "--available-capability", "material_lifecycle")
    assert rc == 0, first
    assert not (runtime / "agent-runtime-capabilities").exists()
    rc, next_turn = _run_cli(registry, runtime, *args)
    assert rc == 0 and next_turn["capability_gate"]["action"] == "ask_owner", next_turn


def test_observation_does_not_cross_registry_or_agent(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network", claimed=False, provider="sqlite")
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("agent-b")
    registry.write_text(json.dumps(data))
    args = ("quota", "should-run", "--goal-id", GOAL_ID)
    rc, first = _run_cli(registry, runtime, *args, "--agent-id", AGENT_ID, "--available-capability", "network")
    assert rc == 0, first
    rc, peer = _run_cli(registry, runtime, *args, "--agent-id", "agent-b")
    assert rc == 0 and peer["capability_gate"]["action"] == "repair_bridge", peer
    second_registry = registry.with_name("second.json")
    second_registry.write_bytes(registry.read_bytes())
    rc, other = _run_cli(second_registry, runtime, *args, "--agent-id", AGENT_ID)
    assert rc == 0 and other["capability_gate"]["action"] == "repair_bridge", other


def test_turn_plan_stays_read_only_and_settlement_rechecks_memory(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network", provider="sqlite")
    scope = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    rc, plan = _run_cli(registry, runtime, "turn", "plan", *scope, "--available-capability", "network")
    assert rc == 0, plan
    assert not (runtime / "agent-runtime-capabilities").exists()
    rc, first = _run_cli(registry, runtime, "quota", "should-run", *scope, "--available-capability", "network")
    assert rc == 0, first
    _, spend = _run_cli(registry, runtime, "quota", "spend-slot", *scope)
    # Accounting still needs its ordinary delivery evidence; capability availability survives.
    assert spend["before"]["capability_gate"]["action"] == "run", spend
    assert spend["appended"] is False
    rc, cached = _run_cli(registry, runtime, "quota", "should-run", *scope, "--write-projection-cache")
    assert rc == 0, cached
    rc, unavailable = _run_cli(registry, runtime, "agent-capabilities", *scope,
                              "--unavailable-capability", "network", "--execute")
    assert rc == 0, unavailable
    rc, fresh = _run_cli(registry, runtime, "quota", "should-run", *scope, "--use-projection-cache")
    assert rc == 0 and fresh["capability_gate"]["action"] == "repair_bridge", fresh


def test_executing_managed_turn_remembers_through_turn_start_hook(
    tmp_path, capsys, monkeypatch
):
    project, runtime, registry = _write_fixture(
        tmp_path, required_capability="network", provider="sqlite",
    )

    def completed_turn(*args, **kwargs):
        return {
            "ok": True,
            "schema_version": "loopx_turn_execution_v0",
            "effects": {
                "host_invoked": False,
                "state_written": False,
                "scheduler_acknowledged": False,
                "quota_spent": False,
            },
        }

    monkeypatch.setattr(
        "loopx.cli_commands.turn_run_once.run_loopx_turn_once",
        completed_turn,
    )
    code = main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "turn",
            "run-once",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--project",
            str(project),
            "--host",
            "generic-cli",
            "--execution-mode",
            "isolated-headless",
            "--host-adapter-command-json",
            '["/usr/bin/true"]',
            "--available-capability",
            "network",
            "--execute",
        ]
    )
    assert code == 0, capsys.readouterr().out
    capsys.readouterr()

    rc, next_turn = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
    )
    assert rc == 0, next_turn
    assert next_turn["capability_gate"]["action"] == "run"


def test_agent_alias_uses_existing_identity_normalization(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network", provider="sqlite")
    rc, first = _run_cli(registry, runtime, "quota", "should-run", "--goal-id", GOAL_ID,
                         "--agent-id", AGENT_ID.upper(), "--available-capability", "network")
    assert rc == 0, first
    rc, state = _run_cli(registry, runtime, "agent-capabilities", "--goal-id", GOAL_ID,
                         "--agent-id", AGENT_ID)
    assert rc == 0, state
    assert state["agent_capabilities"]["available"] == ["network"]


def test_global_registry_sync_shares_canonical_source_registry_capability_memory(tmp_path):
    _, runtime, registry = _write_fixture(
        tmp_path, required_capability="external_evidence_poll", provider="sqlite"
    )
    global_registry = runtime / "registry.global.json"
    source_payload = json.loads(registry.read_text())
    synced_goal = dict(source_payload["goals"][0])
    synced_goal["source_registry"] = str(registry.resolve())
    global_registry.parent.mkdir(parents=True, exist_ok=True)
    global_registry.write_text(
        json.dumps({**source_payload, "goals": [synced_goal]})
    )

    rc, recorded = _run_cli(
        registry,
        runtime,
        "agent-capabilities",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--available-capability",
        "external_evidence_poll",
        "--execute",
    )
    assert rc == 0 and recorded["agent_capabilities"]["written"] is True, recorded

    rc, via_global = _run_cli(
        global_registry,
        runtime,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
    )
    assert rc == 0, via_global
    assert via_global["capability_gate"]["action"] == "run"

    rc, prompt = _run_cli(
        global_registry,
        runtime,
        "heartbeat-prompt",
        "--thin",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
    )
    assert rc == 0, prompt
    assert "heartbeat-prequota" in prompt["task_body"]
    assert "--available-capability external_evidence_poll" not in prompt["task_body"]

    rc, revoked = _run_cli(
        global_registry,
        runtime,
        "agent-capabilities",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--unavailable-capability",
        "external_evidence_poll",
        "--execute",
    )
    assert rc == 0, revoked
    rc, revoked_prompt = _run_cli(
        registry,
        runtime,
        "heartbeat-prompt",
        "--thin",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
    )
    assert rc == 0, revoked_prompt
    assert "heartbeat-prequota" not in revoked_prompt["task_body"]


@pytest.mark.parametrize("original", ["available", "unavailable"])
@pytest.mark.parametrize("via_mirror", [True, False])
def test_forget_legacy_observation_survives_fresh_registry_reads(tmp_path, original, via_mirror):
    _, runtime, project = _write_fixture(tmp_path, provider="sqlite")
    mirror = runtime / "registry.global.json"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_bytes(project.read_bytes())
    args = ("agent-capabilities", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    capability = "external_evidence_poll"

    def observe(registry, *flags):
        rc, result = _run_cli(registry, runtime, *args, *flags)
        assert rc == 0, result
        return result["agent_capabilities"]

    observe(mirror, f"--{original}-capability", capability, "--available-capability", "cli_bridge", "--execute")
    synced = json.loads(mirror.read_text())
    synced["goals"][0]["source_registry"] = str(project.resolve())
    mirror.write_text(json.dumps(synced))
    clear = ("--forget-capability", capability, "--execute")
    assert observe(mirror if via_mirror else project, *clear)["written"]
    assert not observe(mirror if via_mirror else project, *clear)["written"]
    for registry in (project, mirror):
        current = observe(registry)
        assert capability not in current["available"] + current["unavailable"]
    assert "cli_bridge" in observe(mirror)["available"]
    observe(mirror, "--available-capability", capability, "--execute")
    assert set(observe(project)["available"]) == {"cli_bridge", capability}


def test_live_task_facing_capability_commands_auto_record_observations(
    tmp_path, monkeypatch, capsys
):
    from loopx.capabilities.issue_fix import pr_lifecycle
    from loopx.cli_commands import pr_review as pr_review_cmd

    def _run_inproc(*cli_args: str) -> tuple[int, dict]:
        rc = main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                *cli_args,
            ]
        )
        return rc, json.loads(capsys.readouterr().out)

    project, runtime, registry = _write_fixture(
        tmp_path, required_capability="external_evidence_poll", provider="sqlite"
    )
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("peer-b")
    data["goals"][0]["coordination"]["thread_agent_bindings"] = [
        {
            "schema_version": "loopx_thread_agent_binding_v0",
            "host_surface": "codex-app",
            "thread_id": "thread-agent-primary",
            "agent_id": AGENT_ID,
            "bound_at": "2026-11-23T00:00:00Z",
        }
    ]
    registry.write_text(json.dumps(data))

    fixture_path = tmp_path / "pr-fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "repository": "owner/repo",
                "reviewer_login": "reviewer",
                "pull_requests": [],
            }
        )
    )
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-agent-primary")
    rc, fixture_review = _run_inproc(
        "pr-review",
        "--goal-id",
        GOAL_ID,
        "--fixture",
        str(fixture_path),
    )
    assert rc == 0, fixture_review
    assert not (runtime / "agent-runtime-capabilities").exists()

    monkeypatch.setattr(
        pr_review_cmd, "resolve_current_github_repository", lambda: "owner/repo"
    )
    monkeypatch.setattr(
        pr_review_cmd, "resolve_current_github_login", lambda: "reviewer"
    )
    monkeypatch.setattr(
        pr_review_cmd,
        "scan_github_pull_requests",
        lambda **kwargs: {"pull_requests": [], "truncated": False},
    )
    rc, live_review = _run_inproc(
        "pr-review",
        "--goal-id",
        GOAL_ID,
    )
    assert rc == 0, live_review

    rc, state = _run_cli(
        registry,
        runtime,
        "agent-capabilities",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
    )
    assert rc == 0, state
    assert state["agent_capabilities"]["available"] == [
        "external_evidence_poll",
        "network",
    ]
    rc, peer_state = _run_cli(
        registry,
        runtime,
        "agent-capabilities",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        "peer-b",
    )
    assert rc == 0 and peer_state["agent_capabilities"]["available"] == []

    # Explicit negative observations are preserved across passive task-facing polls.
    rc, _ = _run_cli(
        registry,
        runtime,
        "agent-capabilities",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        "peer-b",
        "--unavailable-capability",
        "external_evidence_poll",
        "--execute",
    )
    assert rc == 0
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(
        pr_lifecycle,
        "fetch_github_pr_lifecycle_payload",
        lambda reference, **kwargs: {
            "url": f"https://github.com/{reference['repo']}/pull/{reference['number']}",
            "number": reference["number"],
            "state": "OPEN",
        },
    )
    rc, lifecycle = _run_inproc(
        "issue-fix",
        "pr-lifecycle",
        "--project",
        str(project),
        "--goal-id",
        GOAL_ID,
        "--claimed-by",
        "peer-b",
        "--repo",
        "owner/repo",
        "--pr-ref",
        "#42",
        "--fetch-metadata",
    )
    assert rc == 0, lifecycle
    rc, peer_after = _run_cli(
        registry,
        runtime,
        "agent-capabilities",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        "peer-b",
    )
    assert rc == 0, peer_after
    assert peer_after["agent_capabilities"]["available"] == ["network"]
    assert peer_after["agent_capabilities"]["unavailable"] == [
        "external_evidence_poll"
    ]


def test_failed_read_and_unrelated_local_success_do_not_record_availability(tmp_path, monkeypatch):
    from loopx.capabilities.issue_fix import pr_gate_reconcile as reconcile

    _, runtime, registry = _write_fixture(tmp_path, required_capability="external_evidence_poll", provider="sqlite")
    receipts = [{"binding": {"todo_id": key, "permalink": "https://github.com/owner/repo/pull/1"}}
                for key in ("failed", "already-closed")]
    monkeypatch.setattr(reconcile, "load_issue_fix_pr_review_ack_receipts", lambda **kwargs: receipts)

    def reconcile_one(*, todo_id, **kwargs):
        if todo_id == "failed":
            raise ValueError("provider unavailable")
        return {"ok": True, "external_read_performed": False, "skip_reason": "todo_not_open"}

    monkeypatch.setattr(reconcile, "reconcile_issue_fix_pr_review", reconcile_one)
    result = reconcile.reconcile_acknowledged_issue_fix_pr_reviews(
        registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID,
        agent_id=AGENT_ID, project=None, fetch_metadata=True,
    )
    assert (result["external_read_count"], result["failure_count"]) == (1, 1)
    assert not (runtime / "agent-runtime-capabilities").exists()


def test_fresh_agent_auto_probes_external_evidence_poll_by_default_and_honors_sandbox_or_revocation(
    tmp_path, monkeypatch
):
    import contextlib
    import io

    from loopx.capabilities.issue_fix import pr_lifecycle
    from loopx.todos import add_goal_todo, list_goal_todos

    _, runtime, registry = _write_fixture(
        tmp_path, required_capability="external_evidence_poll", provider="sqlite"
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_gh.chmod(0o755)
    gh_cfg = tmp_path / "gh-config"
    gh_cfg.mkdir()
    (gh_cfg / "hosts.yml").write_text(
        "github.com:\n    user: fixture\n    git_protocol: https\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GH_CONFIG_DIR", str(gh_cfg))
    monkeypatch.setenv("PATH", f"{fake_bin}:{tmp_path}")
    monkeypatch.delenv("CODEX_SANDBOX_NETWORK_DISABLED", raising=False)
    monkeypatch.delenv("LOOPX_OFFLINE", raising=False)

    def _run_inproc(*argv: str):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(
                [
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(runtime),
                    "--format",
                    "json",
                    *argv,
                ]
            )
        return code, json.loads(buf.getvalue())

    # 1. Read-only inspection on a brand-new Agent immediately sees probed availability without writing memory.
    rc, inspected = _run_inproc(
        "agent-capabilities", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID
    )
    assert rc == 0, inspected
    assert inspected["availability"]["probed"] == ["external_evidence_poll"]
    assert inspected["availability"]["effective"] == ["external_evidence_poll"]
    assert not (runtime / "agent-runtime-capabilities").exists()

    # 2. Zero-flag heartbeat-prompt automatically projects heartbeat-prequota for the fresh Agent.
    rc, prompt = _run_inproc(
        "heartbeat-prompt",
        "--thin",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
    )
    assert rc == 0, prompt
    assert "heartbeat-prequota" in prompt["task_body"]

    # 3. Turn 1 executing quota should-run auto-probes & polls upstream PR wait out of the box.
    wait_todo = add_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id=GOAL_ID,
        role="agent",
        claimed_by=AGENT_ID,
        text="Resume automatically when upstream PR merges",
        resume_when="pr_merged:owner/repo#77",
    )["todo_id"]
    calls = []

    def fake_fetch(reference, **kwargs):
        calls.append(reference)
        return {
            "url": f"https://github.com/{reference['repo']}/pull/{reference['number']}",
            "state": "MERGED",
            "mergedAt": "2026-11-23T08:00:00Z",
        }

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fake_fetch)
    rc, guard = _run_inproc(
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--context-delivery",
        "off",
    )
    assert rc == 0, guard
    assert guard["capability_gate"]["action"] == "run"
    assert len(calls) == 1
    assert (
        list_goal_todos(
            registry_path=registry,
            runtime_root_arg=str(runtime),
            goal_id=GOAL_ID,
            todo_id=wait_todo,
        )["todo"]["resume_ready"]
        is True
    )

    # A successful first-turn probe alone must not become a durable declaration.
    assert not (runtime / "agent-runtime-capabilities").exists()
    scope = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    monkeypatch.setenv("LOOPX_AUTO_CAPABILITY_PROBE", "0")
    assert _run_inproc("agent-capabilities", *scope)[1]["availability"]["effective"] == []
    monkeypatch.delenv("LOOPX_AUTO_CAPABILITY_PROBE")
    _run_inproc("agent-capabilities", *scope, "--available-capability", "external_evidence_poll", "--execute")
    pending = add_goal_todo(
        registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID, role="agent",
        claimed_by=AGENT_ID, text="Wait for a second review", resume_when="pr_merged:owner/repo#78",
    )["todo_id"]
    for flag in ("LOOPX_OFFLINE", "CODEX_SANDBOX_NETWORK_DISABLED"):
        monkeypatch.setenv(flag, "1")
        rc, status = _run_inproc("agent-capabilities", *scope)
        assert rc == 0 and status["availability"]["effective"] == [], status
        assert "external_evidence_poll" in status["agent_capabilities"]["available"]
        assert "heartbeat-prequota" not in _run_inproc("heartbeat-prompt", "--thin", "--codex-app", *scope)[1]["task_body"]
        rc, poll = _run_inproc("heartbeat-prequota", *scope)
        assert rc == 0 and poll["checks"]["pr_dependencies"]["external_read_count"] == 0, poll
        assert len(calls) == 1
        assert not list_goal_todos(registry_path=registry, runtime_root_arg=str(runtime),
                                   goal_id=GOAL_ID, todo_id=pending)["todo"]["resume_ready"]
        monkeypatch.delenv(flag)

    rc, recovered = _run_inproc("heartbeat-prequota", *scope)
    assert rc == 0 and recovered["checks"]["pr_dependencies"]["external_read_count"] == 1, recovered
    assert len(calls) == 2
    assert list_goal_todos(
        registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID, todo_id=pending,
    )["todo"]["resume_ready"]

    rc, revoked = _run_inproc(
        "agent-capabilities",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--unavailable-capability",
        "external_evidence_poll",
        "--execute",
    )
    assert rc == 0, revoked
    assert revoked["availability"]["probed"] == ["external_evidence_poll"]
    assert revoked["availability"]["effective"] == []
