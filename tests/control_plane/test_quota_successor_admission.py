from pathlib import Path

import pytest

from tests.control_plane import test_quota_settlement_cli as fixtures

scheduler_host_home = fixtures.scheduler_host_home


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_successor_waits_for_original_turn_settlement(
    tmp_path: Path, scheduler_host_home: Path, provider: str,
) -> None:
    project, runtime, registry = fixtures._write_fixture(tmp_path)
    fixtures._configure_read_only_todo(project)
    fixtures._initialize_fixture_authority(project, runtime, provider=provider)
    route = ("--goal-id", fixtures.GOAL_ID, "--agent-id", fixtures.AGENT_ID)
    guard = ("quota", "should-run", "--codex-app", *route)
    original = ("--todo-id", fixtures.TODO_ID, "--turn-instance-id", fixtures.TURN_ID)
    successor_turn = "turn-successor-after-original-settlement"

    def run(*args: str):
        return fixtures._run_cli(registry, runtime, *args)

    def set_host_rule(rule: str) -> None:
        fixtures.write_codex_heartbeat_fixture(
            scheduler_host_home, goal_id=fixtures.GOAL_ID, agent_id=fixtures.AGENT_ID, rrule=rule,
        )

    def ack_args(packet: dict) -> list[str]:
        return packet["scheduler_hint"]["app_automation"]["ack_hint"]["cli_args"]

    # Exercise real host apply and ACK under a configured owner minimum.
    rc, cadence = run(
        "automation-cadence", *route, "--min-interval-minutes", "60",
        "--expected-revision", "0", "--owner-reference", "fixture-owner-minimum", "--execute",
    )
    assert rc == 0 and cadence["written"], cadence
    set_host_rule("FREQ=MINUTELY;INTERVAL=3")
    rc, first = run(*guard, *original)
    assert rc == 0, first
    identity = first["heartbeat_receipt"]["settlement_identity"]
    rc, completed = run(
        "todo", "complete", *route, *original,
        "--evidence", "original delivery validated",
        "--next-agent-todo", "Continue the explicit successor delivery.",
        "--next-claimed-by", fixtures.AGENT_ID, "--next-action-kind", "implement",
    )
    assert rc == 0, completed
    successor = completed["next_todos"][0]["todo_id"]
    checkpoint_args = fixtures._default_checkpoint_args(registry, runtime, (*route[2:], *original), tmp_path)
    rc, writeback = run(
        "refresh-state", *route, *original, *checkpoint_args,
        "--classification", "validated_progress", "--delivery-batch-scale", "single_surface",
        "--delivery-outcome", "outcome_progress", "--no-global-sync", "--suppress-external-sinks",
    )
    assert rc == 0 and writeback["settlement_result"]["ok"], writeback

    successor_args = (*guard, "--todo-id", successor, "--turn-instance-id", successor_turn)
    rc, pending = run(*successor_args)
    assert rc == 0, pending
    assert pending["effective_action"] == "unsettled_host_turn_recovery", pending
    recovery = pending["unsettled_host_turn_recovery"]
    assert recovery["prior_turn_instance_id"] == fixtures.TURN_ID
    assert recovery["binding_id"] == fixtures.TODO_ID
    assert recovery["settlement_effect_id"] == identity["effect_id"]
    assert recovery["missing_receipts"] == ["quota_spend_receipt"]
    actions = pending["interaction_contract"]["cli_channel"]["next_cli_actions"]
    original_guard = next(action for action in actions if
        f"--turn-instance-id {fixtures.TURN_ID} --todo-id {fixtures.TODO_ID}" in action)
    retry = next(action for action in actions if f"--turn-instance-id {successor_turn}" in action)
    assert f"--todo-id {successor}" in retry
    assert pending["heartbeat_receipt"]["status"] == "not_committed"
    assert not pending.get("scheduler_hint")
    assert fixtures._heartbeat_receipt_count(runtime, successor_turn) == 0
    assert fixtures._spend_run_count(runtime) == 0

    rc, original_reentry = fixtures._run_generated_cli(original_guard, registry_path=registry)
    assert rc == 0, original_reentry
    assert original_reentry["heartbeat_receipt"]["settlement_identity"] == identity
    spend = original_reentry["settlement_owed"]["command"]
    rc, spent = fixtures._run_generated_cli(spend, registry_path=registry)
    assert rc == 0 and spent["settlement_result"]["ok"], spent
    rc, replayed = fixtures._run_generated_cli(spend, registry_path=registry)
    assert rc == 0 and replayed["idempotent_replay"], replayed
    first_ack = ack_args(first)
    set_host_rule(first_ack[first_ack.index("--applied-rrule") + 1])
    rc, ack = run(*first_ack)
    assert rc == 0 and ack["scheduler_commit"]["written"], ack
    scheduler_path = Path(ack["scheduler_state_path"])
    original_state = scheduler_path.read_bytes()

    rc, admitted = fixtures._run_generated_cli(retry, registry_path=registry)
    assert rc == 0, admitted
    assert admitted["heartbeat_receipt"]["status"] == "committed"
    assert admitted["heartbeat_receipt"]["settlement_identity"]["todo_id"] == successor
    assert fixtures._heartbeat_receipt_count(runtime, successor_turn) == 1
    next_ack = ack_args(admitted)
    requested_rule = next_ack[next_ack.index("--applied-rrule") + 1]
    (scheduler_host_home / "automations" / "scheduler-fixture" / "automation.toml").unlink()
    rc, refused = run(*next_ack)
    assert rc == 1 and refused["scheduler_commit"]["reason_code"] == "rrule_mismatch", refused
    assert scheduler_path.read_bytes() == original_state
    wrong_rule = "FREQ=MINUTELY;INTERVAL=60" if requested_rule != "FREQ=MINUTELY;INTERVAL=60" else "FREQ=MINUTELY;INTERVAL=30"
    set_host_rule(wrong_rule)
    rc, refused = run(*next_ack)
    assert rc == 1 and refused["scheduler_commit"]["reason_code"] == "rrule_mismatch", refused
    assert scheduler_path.read_bytes() == original_state
    set_host_rule(requested_rule)
    rc, ack = run(*next_ack)
    assert rc == 0 and ack["scheduler_commit"]["written"], ack
    assert fixtures._spend_run_count(runtime) == 1
