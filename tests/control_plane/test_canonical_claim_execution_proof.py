"""The public claim command must never grant execution from historical proof."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.markdown import render_todo_markdown

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("retirement", ["release", "transfer"])
def test_claim_replay_uses_current_execution(tmp_path, monkeypatch, provider, retirement):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    goal, target = "claim-execution", "todo_claim_execution"
    state.write_text("# Claim execution\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    projection = build_todo_runtime_shadow_projection(goal_id=goal, handoff_mode="hard_lease", leases=[], todos=[{
        "schema_version": "todo_item_v0", "todo_id": target, "role": "agent", "status": "open", "done": False,
        "text": "Adopt work and obtain current execution", "archive_state": "active", "source_section": "Agent Todo",
        "index": 1, "task_class": "advancement_task", "required_write_scopes": ["src/**"],
    }])
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)
    state.unlink()  # Recovery must not rebuild authority from missing Markdown.

    def cli(command, *args, expected_exit=0):
        process = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
            *command, "--goal-id", goal, "--todo-id", target, *args], cwd=REPO,
            capture_output=True, text=True, timeout=90, check=False)
        assert process.returncode == expected_exit, process.stdout + process.stderr
        return json.loads(process.stdout)

    request = ["--claimed-by", "agent-a", "--agent-id", "agent-a", "--claim-operation-id", "claim-original",
               "--task-lease-idempotency-key", "execution-a", "--task-lease-expected-version", "0"]
    lease_args = ["--owner", "agent-a", "--idempotency-key", "execution-a"]
    try:
        preview = cli(["todo", "claim"], *request, "--dry-run")
        assert preview["status"] == "planned"
        assert not cli(["task-lease", "inspect"])["active"]
        first = cli(["todo", "claim"], *request)
        assert first["status"] == "applied"
        assert first["lease"]["version"] == first["lease"]["lease_epoch"] == 1
        assert first["lease"]["write_scopes"] == ["src/**"]
        renewed = cli(["task-lease", "renew"], *lease_args, "--expected-version", "1", "--ttl-seconds", "600")
        replay = cli(["todo", "claim"], *request)
        assert replay["status"] == "replayed"
        assert replay["changed"] is False
        assert replay["lease"] == renewed["lease"]
        assert replay["lease"]["version"] == 2
        assert replay["original_receipt"] == first["original_receipt"]
        assert replay["provider_revision"] == first["provider_revision"]
        assert replay["current_provider_revision"] != first["provider_revision"]
        transition = ["--expected-version", "2"]
        if retirement == "transfer":
            transition += ["--new-owner", "agent-b", "--new-idempotency-key", "execution-b", "--transfer-claim"]
        retired = cli(["task-lease", retirement], *lease_args, *transition)
        assert retired["ok"]
        state.unlink(missing_ok=True)  # Maintenance may refresh display; remove it before recovery.
        before = cli(["task-lease", "inspect"])
        rejected = cli(["todo", "claim"], *request, expected_exit=1)
        assert rejected["ok"] is False
        assert rejected["error_code"] == ("idempotency_key_reuse" if retirement == "release" else "owner_conflicts_with_claim")
        assert not rejected.get("lease"), "a rejected old execution cannot return an active lease"
        assert cli(["task-lease", "inspect"]) == before
        assert not state.exists(), "rejected recovery cannot revive a legacy writer"
        readback = cli(["todo", "list"])
        assert first["source_authority"] == provider + "_v0"
        assert readback["todo"]["claimed_by"] == ("agent-b" if retirement == "transfer" else "agent-a")
        assert not (runtime / "goals" / goal / "task-leases" / f"{target}.json").exists()
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; effect_runtime_result('runtime.shutdown',{},retry_safe=False)"],
            cwd=REPO, capture_output=True, text=True, timeout=30, check=True)


@pytest.fixture(params=["file", "sqlite"])
def expired_managed_claim(tmp_path, monkeypatch, request):
    """A real accepted receipt with a past synthetic clock, without waiting."""
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    goal, target = "expired-claim", "todo_expired_claim"
    state.write_text("# Expired execution\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    projection = build_todo_runtime_shadow_projection(goal_id=goal, handoff_mode="hard_lease", leases=[], todos=[{
        "schema_version": "todo_item_v0", "todo_id": target, "role": "agent", "status": "open", "done": False,
        "text": "Finish the original validated work", "archive_state": "active", "source_section": "Agent Todo",
        "index": 1, "task_class": "advancement_task", "required_write_scopes": ["src/**"],
    }])
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=request.param)
    module = REPO / "loopx/control_plane/coordination"
    script = (
        f"import {{openLocalAuthorityStore}} from {json.dumps((module / 'local_authority_provider.ts').as_uri())};"
        f"import {{executeCoordinationTodoClaim}} from {json.dumps((module / 'todo_claim.ts').as_uri())};"
        f"import {{prepareCoordinationProjectionCommit}} from {json.dumps((module / 'coordination_projection.ts').as_uri())};"
        "let text='';for await(const chunk of process.stdin)text+=chunk;const input=JSON.parse(text);"
        "const store=await openLocalAuthorityStore(input.runtime,input.goal);let result;"
        "if(input.todo_patch){const head=await store.loadAuthority();"
        "const todo=head.head.todos.find(todo=>todo.todo_id===input.todo);"
        "result=await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id:input.goal,"
        "operation_id:'synthetic-edit-'+head.provider_revision,expected_provider_revision:head.provider_revision,projection:head.head,"
        "mutations:[{kind:'todo_upsert',todo:{...todo,...input.todo_patch}}]}));}"
        "else if(input.patch){const head=await store.loadAuthority();"
        "result=await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id:input.goal,"
        "operation_id:'synthetic-identity-change',expected_provider_revision:head.provider_revision,projection:head.head,"
        "mutations:[{kind:'lease_upsert',lease:{...head.head.leases[0],...input.patch}}]}));}"
        "else result=await executeCoordinationTodoClaim(store,{goal_id:input.goal,todo_id:input.todo,"
        "actor_agent_id:'agent-a',claimed_by:'agent-a',expected_role:'agent',registered_agents:['agent-a','agent-b'],"
        "operation_id:'expired-original-claim',manage_execution:true,dry_run:false,now:new Date('2001-01-01T00:00:00Z')});"
        "process.stdout.write(JSON.stringify(result));"
    )

    def author(patch=None, *, todo_patch=None):
        result = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
            input=json.dumps({"runtime": str(runtime), "goal": goal, "todo": target, "patch": patch, "todo_patch": todo_patch}),
            capture_output=True, text=True, timeout=45, check=True)
        accepted = json.loads(result.stdout)
        assert accepted["status"] == "applied", accepted
        return accepted

    original = author()
    state.unlink()  # Display files cannot restore execution authority.
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    selected = ["--goal-id", goal, "--todo-id", target]

    def cli(arguments, expected_exit=0, *, use_fixture_route=True):
        process = subprocess.run([sys.executable, "-m", "loopx.cli", *(prefix if use_fixture_route else []), *arguments], cwd=REPO,
            capture_output=True, text=True, timeout=90, check=False)
        assert process.returncode == expected_exit, process.stdout + process.stderr
        return json.loads(process.stdout)

    def snapshot():
        task = cli(["todo", "list", *selected])
        return task["todo"], task["authority_read"]["provider_revision"], cli(["task-lease", "inspect", *selected])

    yield cli, selected, snapshot, author, original
    subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; effect_runtime_result('runtime.shutdown',{},retry_safe=False)"],
        cwd=REPO, capture_output=True, text=True, timeout=30, check=True)


@pytest.mark.parametrize("command", ["update", "complete"])
def test_expired_claim_packet_recovers_without_help_or_list(expired_managed_claim, command, record_property):
    cli, selected, snapshot, _, original = expired_managed_claim
    extra = ["--update-operation-id", "original-progress", "--note", "Validated progress"] if command == "update" else [
        "--evidence", "evidence:validated-output"]
    attempt = ["todo", command, *selected, "--agent-id", "agent-a", "--claim-operation-id", "expired-original-claim", *extra]
    before = snapshot()
    rejected = cli(attempt, expected_exit=1)
    assert rejected["error_code"] == "claim_execution_unavailable"
    recovery = rejected["recovery"]
    assert recovery["action"] == "claim_task" and recovery["lease_state"] == "expired"
    assert recovery["execution_authority_granted"] is False
    assert "inspect" not in recovery and "acquire" not in recovery
    assert recovery["claim"]["command"].split()[:3] == ["loopx", "todo", "claim"]
    assert recovery["retry"]["command"].split()[:3] == ["loopx", "todo", command]
    rendered = render_todo_markdown(rejected)
    assert "todo claim" in rendered and f"todo {command}" in rendered
    assert "--claim-operation-id" in rendered and "task-lease" not in rendered
    assert snapshot() == before, "a rejected operation and its recovery preview have no effects"
    prepared = cli(recovery["claim"]["cli_args"], use_fixture_route=False)
    assert prepared["ok"] and prepared["execution_managed"]
    current = snapshot()
    assert current[2]["lease"]["lease_epoch"] > original["lease"]["lease_epoch"]
    assert current[2]["lease"]["idempotency_key"] != original["lease"]["idempotency_key"]
    stale = cli(attempt, expected_exit=1)
    assert stale["error_code"] == "claim_execution_unavailable"
    assert stale["recovery"]["action"] == "reconcile_lease_owner" and "claim" not in stale["recovery"]
    assert snapshot() == current
    retry = [prepared["claim_operation_id"] if value == "expired-original-claim" else value for value in attempt]
    assert cli(retry)["ok"]
    completed = snapshot()
    if command == "update":
        assert completed[0]["note"] == "Validated progress"
    else:
        assert completed[0]["status"] == "done"
    assert cli(retry)["ok"] and snapshot() == completed, "lost replies replay the accepted operation"
    record_property("recovery_interaction", json.dumps({
        "commands": [f"todo {command} (refused)", "todo claim (explicit)", f"todo {command} (retry)"],
        "discovery_help_or_list_calls": 0, "verification_reads_excluded": True,
    }))


def test_supersede_rejects_expired_and_replaced_claim(expired_managed_claim):
    cli, selected, snapshot, _, _ = expired_managed_claim
    attempt = ["todo", "supersede", *selected, "--agent-id", "agent-a",
               "--claim-operation-id", "expired-original-claim", "--reason", "Replace obsolete work",
               "--next-agent-todo", "Deliver replacement work"]
    before = snapshot()
    rejected = cli(attempt, expected_exit=1)
    assert rejected["error_code"] == "claim_execution_unavailable"
    assert snapshot() == before
    prepared = cli(["todo", "claim", *selected, "--agent-id", "agent-a", "--claimed-by", "agent-a",
                    "--claim-operation-id", "replacement-claim"])
    assert prepared["ok"] and prepared["execution_managed"]
    current = snapshot()
    rejected = cli(attempt, expected_exit=1)
    assert rejected["error_code"] == "claim_execution_unavailable"
    assert snapshot() == current


@pytest.mark.parametrize("change", [
    {"owner": "agent-b"}, {"lease_epoch": 2, "version": 2},
    {"write_repository": "git:github.com/example/other"},
    {"write_workspace": {"host": "1" * 64, "common_directory": "2" * 64, "worktree": "3" * 64,
                         "repository": "git:github.com/example/project"}},
])
def test_changed_execution_packet_never_recommends_claim_adoption(expired_managed_claim, change):
    cli, selected, snapshot, author, _ = expired_managed_claim
    author(change)
    before = snapshot()
    for command in ("update", "complete"):
        extra = ["--note", "Reviewed progress"] if command == "update" else ["--evidence", "evidence:output"]
        if command == "update":
            extra += ["--update-expected-provider-revision", before[1]]
        rejected = cli(["todo", command, *selected, "--agent-id", "agent-a", "--claim-operation-id",
                        "expired-original-claim", *extra], expected_exit=1)
        assert rejected["error_code"] == "claim_execution_unavailable"
        recovery = rejected["recovery"]
        assert recovery["action"] == "reconcile_lease_owner" and recovery["execution_authority_granted"] is False
        assert "claim" not in recovery and "retry" not in recovery
        assert "todo list" in render_todo_markdown(rejected)
        assert cli(recovery["inspect"]["cli_args"], use_fixture_route=False)["todo"] == before[0]
    assert snapshot() == before


@pytest.mark.parametrize(
    ("claim_reference", "intervening_edit"),
    [
        ("expired-original-claim", "none"),
        ("expired-original-claim", "before-claim"),
        ("expired-original-claim", "after-read"),
        (None, "after-read"),
    ],
    ids=["no-race", "edit-before-claim", "edit-after-read", "omitted-proof"],
)
def test_expired_claim_with_cas_reconciles_only_selected_todo(
    expired_managed_claim, claim_reference, intervening_edit, record_property,
):
    cli, selected, snapshot, author, _ = expired_managed_claim
    original = snapshot()
    attempt = ["todo", "update", *selected, "--agent-id", "agent-a",
               "--update-operation-id", "original-reviewed-edit",
               "--update-expected-provider-revision", original[1], "--note", "Validated progress"]
    if claim_reference is None:
        # Clearing a wait condition requires execution even if copy edits do not.
        attempt.append("--clear-resume-when")
    else:
        attempt.extend(["--claim-operation-id", claim_reference])
    before = snapshot()
    refused = cli(attempt, expected_exit=1)
    expected_error = (
        "handoff_mode_requires_lease" if claim_reference is None else "claim_execution_unavailable"
    )
    assert refused.get("error_code") == expected_error, json.dumps(refused)
    recovery = refused["recovery"]
    assert recovery["action"] == "claim_task" and recovery["execution_authority_granted"] is False
    assert snapshot() == before
    assert recovery["inspect"]["cli_args"][-2:] == selected[-2:]
    assert recovery["retry"]["requires_flags"] == ["--claim-operation-id", "--update-expected-provider-revision"]
    retry = recovery["retry"]["cli_args"]
    assert "expired-original-claim" not in retry
    assert retry[retry.index("--claim-operation-id") + 1] == "<claim_operation_id_from_claim>"
    assert retry[retry.index("--update-expected-provider-revision") + 1] == "<provider_revision_from_reconciled_todo_read>"
    rendered = render_todo_markdown(refused)
    assert rendered.index("todo claim") < rendered.index("todo list") < rendered.index("todo update")
    if intervening_edit == "before-claim":
        author(todo_patch={"text": "Preserve another writer's accepted correction"})
        before = snapshot()
    prepared = cli(recovery["claim"]["cli_args"], use_fixture_route=False)
    assert prepared["ok"] and prepared["todo_changed"] is False
    # Claim only describes its own change, not changes since the old caller read.
    current = cli(recovery["inspect"]["cli_args"], use_fixture_route=False)
    assert current["todo"] == before[0]
    revision = current["authority_read"]["provider_revision"]
    assert revision != original[1]
    if claim_reference is None:
        stale = cli([*attempt, "--claim-operation-id", prepared["claim_operation_id"]], expected_exit=1)
        assert stale["error_code"] == "provider_revision_mismatch"
        assert snapshot()[0] == current["todo"]
    retry = [prepared["claim_operation_id"] if value == "<claim_operation_id_from_claim>" else
             revision if value == "<provider_revision_from_reconciled_todo_read>" else value for value in retry]
    if intervening_edit == "after-read":
        author(todo_patch={"text": "Preserve a concurrent correction after inspection"})
        concurrent = snapshot()
        rejected = cli(retry, expected_exit=1, use_fixture_route=False)
        assert rejected["error_code"] == "provider_revision_mismatch"
        assert snapshot() == concurrent, "recovery must not silently rebase a concurrent edit"
        current = cli(recovery["inspect"]["cli_args"], use_fixture_route=False)
        retry[retry.index("--update-expected-provider-revision") + 1] = current["authority_read"]["provider_revision"]
    accepted = cli(retry, use_fixture_route=False)
    assert accepted["ok"]
    final = snapshot()
    assert final[0]["text"] == current["todo"]["text"]
    assert final[0]["note"] == "Validated progress"
    assert final[0]["status"] == "open"
    assert final[0].get("resume_when") is None
    assert cli(retry, use_fixture_route=False)["ok"] and snapshot() == final
    record_property("recovery_interaction", json.dumps({
        "commands": ["todo update (refused)", "todo claim", "todo list --todo-id", "todo update (reconciled retry)"],
        "whole_goal_reads": 0, "automatic_revision_rebase": False,
        "intervening_edit": intervening_edit, "verification_reads_excluded": True,
    }))


@pytest.mark.parametrize("reference", ["old-reference", "-old-reference"])
def test_cas_retry_projection_preserves_intent_and_routes_without_stale_references(tmp_path, reference):
    from loopx.cli import build_parser
    from loopx.cli_commands.todo_event import _route_native_recovery

    args = build_parser().parse_args([
        "todo", "update", "--goal-id", "example-goal", "--todo-id", "todo_example", "--agent-id", "agent-a",
        "--claim-operation-id=" + reference, "--update-operation-id", "original-intent",
        "--update-expected-provider-revision=" + reference, "--note=--update-expected-provider-revision",
    ])
    packet = {"recovery": {"retry": {"command": "loopx todo update",
        "requires_flags": ["--claim-operation-id", "--update-expected-provider-revision"],
        "reference_source": "claim_operation_id_from_claim"}}}
    _route_native_recovery(packet, args, registry_path=tmp_path / "registry.json", runtime_root_arg=str(tmp_path / "runtime"))
    retried = build_parser().parse_args(packet["recovery"]["retry"]["cli_args"])
    assert retried.claim_operation_id == "<claim_operation_id_from_claim>"
    assert retried.update_expected_provider_revision == "<provider_revision_from_reconciled_todo_read>"
    assert retried.update_operation_id == "original-intent" and retried.note == "--update-expected-provider-revision"
    assert retried.registry == str(tmp_path / "registry.json") and retried.runtime_root == str(tmp_path / "runtime")
