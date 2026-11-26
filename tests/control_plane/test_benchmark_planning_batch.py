"""Real initial planning, retry and admission through the shared benchmark worker."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import pytest

from benchmark.runtime.planning import task_plan_packet
from benchmark.runtime.sforge_entry import prepare_entry
from benchmark.runtime.worker import loopx_command, run_once
from tests.control_plane.host_process_fixture import write_python_host_fixture
from tests.control_plane.test_benchmark_current_admission import _environment, fixture


PLANNER = r'''
import json, os, re, shlex, subprocess, sys
from pathlib import Path

print(json.dumps({"type": "thread.started", "thread_id": "batch-planning-fixture"}), flush=True)
hint, body = sys.stdin.read().split("Host-supplied planning checkpoint:\n", 1)
packet = json.loads(body)
run = shlex.split(re.search(r"Run (.+?) <proposal\.json>", hint).group(1))
control = Path(os.environ["LOOPX_PLANNING_RESULT"]).parent
path = control / "proposal.json"
if not path.exists():
    proposal = {
        "input_digest": packet["input_digest"],
        "expected_provider_revision": packet["goal_acceptance"]["provider_revision"],
        "outcome": {
            "objective": "Deliver and verify a correct fixture calculation.",
            "requirements": [{"id": "correct", "description": "Calculate and preserve the expected result."}],
            "source": {"ref": "task", "summary": "Deliver and verify a correct fixture calculation."},
        },
        "todos": [
            {"text": "[P0] Implement the fixture calculation.", "action_kind": "implement"},
            {"text": "[P1] Verify the result.", "action_kind": "validate"},
            {"text": "[P2] Deliver the verified result.", "action_kind": "submit", "target_key": "fixture-result"},
        ],
    }
    path.write_text(json.dumps(proposal))
if os.environ.get("FIXTURE_CHANGED_PROPOSAL"):
    proposal = json.loads(path.read_text())
    proposal["todos"][0]["text"] = "Replace the interrupted plan."
    path = control / "changed-proposal.json"
    path.write_text(json.dumps(proposal))
result = subprocess.run([*run, str(path)], capture_output=True, text=True)
(control / "tool-response.json").write_text(result.stdout)
if result.returncode:
    raise SystemExit(result.returncode)
Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(result.stdout)
'''


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("drop_receipt", [False, True], ids=["fresh", "lost-response"])
def test_batch_planning_recovers_original_work_before_execution(tmp_path, monkeypatch, provider, drop_receipt):
    env, project, runtime, source, _ = _environment(tmp_path, monkeypatch, provider, replan="empty")
    control = tmp_path / "planning"
    control.mkdir()
    result = control / "result.json"
    dropped = control / "dropped-receipt"
    original_cli = env["LOOPX_CLI"]
    env.update(
        LOOPX_TASK_ENTRY="loopx-planned", LOOPX_TASK_STAGE="plan", LOOPX_PLANNING_RESULT=str(result),
        LOOPX_PHASE_DEADLINE_EPOCH=str(time.time() + 600),
        CODEX_BIN=str(write_python_host_fixture(tmp_path / "planner", f"#!{sys.executable}\n" + PLANNER)),
    )
    if drop_receipt:
        env["LOOPX_CLI"] = str(write_python_host_fixture(tmp_path / "drop-receipt", f"""#!{sys.executable}
import subprocess, sys
from pathlib import Path
args = sys.argv[1:]
response = subprocess.run([{original_cli!r}, *args], capture_output=True)
marker = Path({str(dropped)!r})
effect = args[args.index("--operation-id") + 1] if "--operation-id" in args else ""
if response.returncode == 0 and "add" in args and effect.endswith(":todo:1") and not marker.exists():
    marker.touch()
    raise SystemExit(1)
sys.stdout.buffer.write(response.stdout)
sys.stderr.buffer.write(response.stderr)
raise SystemExit(response.returncode)
"""))

    def read():
        return task_plan_packet(env, loopx_command(env))

    first = run_once(env)
    original_ids = set()
    if drop_receipt:
        # The second Todo's write succeeded. Its lost response leaves the
        # binding and the caller's ready result absent.
        assert not first["ok"] and dropped.exists() and not result.exists()
        assert json.loads((control / "tool-response.json").read_text())["error"] == (
            "LoopX todo add returned no JSON receipt"
        )
        partial = read()
        original_ids = set(partial["runnable_todo_ids"])
        assert len(original_ids) == len(partial["existing_todos"]) == 2
        assert not partial["goal_acceptance"]["planning_requirements"]["linked_todo_ids"]

        assert not run_once(env | {"FIXTURE_CHANGED_PROPOSAL": "yes"})["ok"]
        assert "declaration is missing or changed" in json.loads((control / "tool-response.json").read_text())["error"]
        assert set(read()["runnable_todo_ids"]) == original_ids and not result.exists()
        # SForge uses the same worker for its timed initial plan.
        assert prepare_entry(env)
    else:
        assert first["ok"], (Path(env["LOOPX_WAKE_LOG_DIR"]) / first["turn_id"] / "stderr.log").read_text()

    saved = json.loads(result.read_text())
    ids = saved["todo_ids"]
    assert len(ids) == 3 and original_ids < set(ids)
    assert saved["state_readback_verified"] is True
    assert saved["goal_requirements_readback"]["linked_todo_ids"] == ids
    assert saved["execution_owner"] == "caller"
    stored = {item["todo_id"]: item for item in read()["existing_todos"]}
    assert set(stored) == set(ids)
    assert [stored[item]["action_kind"] for item in ids] == ["implement", "validate", "submit"]
    assert stored[ids[2]]["target_key"] == "fixture-result"
    assert all(stored[item]["claimed_by"] == fixture.AGENT_ID for item in ids)

    replay = run_once(env)
    assert replay["ok"] and replay["planning"]["todo_ids"] == ids
    assert len(read()["existing_todos"]) == 3
    assert fixture._spend_run_count(runtime) == 0
    log = runtime / "goals" / fixture.GOAL_ID / "rollout-event-log.jsonl"
    assert not log.exists() or "quota_should_run" not in log.read_text()

    code, admitted = fixture._run_cli(
        source, runtime, "quota", "should-run", "--goal-id", fixture.GOAL_ID,
        "--agent-id", fixture.AGENT_ID, "--todo-id", ids[0], "--runtime-profile", "generic_cli",
        "--context-delivery", "off", "--delta-delivery", "off",
        "--turn-instance-id", "batch-fixture-execution", cwd=project,
    )
    assert code == 0 and admitted["should_run"] and admitted["selected_todo"]["todo_id"] == ids[0]
    events = fixture._heartbeat_receipt_events(runtime, "batch-fixture-execution")
    assert [entry["event_id"] for entry in events] == [admitted["heartbeat_receipt"]["event_id"]]
    assert fixture._spend_run_count(runtime) == 0


def test_planning_batch_cli_help_and_schema_are_self_contained():
    import subprocess
    from benchmark.runtime.planning_batch import planning_batch_guidance, planning_batch_schema

    help_proc = subprocess.run(
        [sys.executable, "-m", "benchmark.runtime.planning_batch", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert help_proc.returncode == 0, help_proc.stderr
    assert "--schema" in help_proc.stdout
    assert "outcome.source.ref" in help_proc.stdout
    assert "UTF-16 code units" in help_proc.stdout

    schema_proc = subprocess.run(
        [sys.executable, "-m", "benchmark.runtime.planning_batch", "--schema"],
        capture_output=True, text=True, timeout=30,
    )
    assert schema_proc.returncode == 0, schema_proc.stderr
    schema = json.loads(schema_proc.stdout)
    assert schema == planning_batch_schema()
    assert schema["length_unit"] == "utf16_code_units"
    assert schema["fields"]["outcome"]["properties"]["source"]["ref"]["max_utf16_code_units"] == 128
    assert schema["fields"]["outcome"]["properties"]["requirements"]["max_items"] == 32
    guidance = planning_batch_guidance()
    assert "--schema" in guidance
    assert 'status="ready"' in guidance
    assert "UTF-16 code units" in guidance


def _base_proposal():
    return {
        "input_digest": "digest-1",
        "expected_provider_revision": "rev-1",
        "outcome": {
            "objective": "Deliver fix",
            "requirements": [{"id": "req-1", "description": "Requirement passes"}],
            "source": {"ref": "checkpoint:init", "summary": "Initial planning checkpoint"},
        },
        "todos": [{"text": "[P0] Implement fix", "action_kind": "implement"}],
    }


@pytest.mark.parametrize(("mutate", "expected_substrings", "forbidden"), [
    (
        lambda p: p["outcome"]["source"].__setitem__("ref", "/frontier/task/repo/SECRET_REF/" + "x" * 120),
        ("outcome.source.ref", "got 151 UTF-16 code units, max 128", "no '/' paths or spaces"),
        "SECRET_REF",
    ),
    (
        lambda p: p["outcome"]["requirements"][0].__setitem__("id", "bad id with spaces"),
        ("outcome.requirements[0].id", "opaque identifier"),
        "bad id with spaces",
    ),
    (
        lambda p: p["todos"][0].__setitem__("action_kind", "do invalid action"),
        ("todos[0].action_kind", "^[a-z][a-z0-9_-]{0,63}$"),
        "do invalid action",
    ),
    (
        lambda p: p["outcome"].__setitem__("PRIVATE_EXTRA_KEY", "SECRET_VAL"),
        ("outcome", "unsupported field count: 1", "allowed: objective, requirements, source"),
        "PRIVATE_EXTRA_KEY",
    ),
    (
        lambda p: p["outcome"]["source"].__setitem__("summary", "S" * 513),
        ("outcome.source.summary", "got 513 UTF-16 code units, max 512"),
        "SSSSSSSS",
    ),
    (
        lambda p: p["outcome"].__setitem__("objective", "O" * 1025),
        ("outcome.objective", "got 1025 UTF-16 code units, max 1024"),
        "OOOOOOOO",
    ),
    (
        lambda p: p["outcome"]["requirements"][0].__setitem__("description", "D" * 2001),
        ("outcome.requirements[0].description", "got 2001 UTF-16 code units, max 2000"),
        "DDDDDDDD",
    ),
    (
        lambda p: p["outcome"].__setitem__("objective", "\U0001f600" * 513),
        ("outcome.objective", "got 1026 UTF-16 code units, max 1024"),
        "\U0001f600",
    ),
    (
        lambda p: p["outcome"]["requirements"][0].__setitem__("description", "\U0001f600" * 1001),
        ("outcome.requirements[0].description", "got 2002 UTF-16 code units, max 2000"),
        "\U0001f600",
    ),
    (
        lambda p: p["outcome"]["source"].__setitem__("summary", "\U0001f600" * 257),
        ("outcome.source.summary", "got 514 UTF-16 code units, max 512"),
        "\U0001f600",
    ),
    (
        lambda p: p["outcome"].__setitem__(
            "requirements",
            [{"id": f"r-{i}", "description": f"desc {i}"} for i in range(33)],
        ),
        ("outcome.requirements exceeds capacity", "got 33 items, max 32"),
        None,
    ),
    (
        lambda p: p["todos"][0].__setitem__("target_key", None),
        ("todos[0].target_key", "non-empty text"),
        None,
    ),
])
def test_planning_batch_upfront_validation_rejects_invalid_proposals_without_loopx(
    tmp_path, mutate, expected_substrings, forbidden,
):
    import subprocess
    from benchmark.runtime.planning_batch import apply_initial_plan

    proposal = _base_proposal()
    mutate(proposal)
    env = {"LOOPX_TASK_STAGE": "plan", "LOOPX_CLI": str(tmp_path / "missing-loopx")}
    with pytest.raises(ValueError) as exc_info:
        apply_initial_plan(env, proposal)
    message = str(exc_info.value)
    for snippet in expected_substrings:
        assert snippet in message, message
    if forbidden:
        assert forbidden not in message

    proposal_path = tmp_path / "bad-proposal.json"
    proposal_path.write_text(json.dumps(proposal))
    proc = subprocess.run(
        [sys.executable, "-m", "benchmark.runtime.planning_batch", str(proposal_path)],
        env={"PATH": "", "PYTHONPATH": str(Path(__file__).resolve().parents[2]), "LOOPX_TASK_STAGE": "plan"},
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    for snippet in expected_substrings:
        assert snippet in payload["error"], payload
    if forbidden:
        assert forbidden not in proc.stdout


def test_planning_batch_preserves_upstream_error_detail_on_refine_rejection(tmp_path, monkeypatch):
    from benchmark.runtime import planning_batch

    fake_cli = write_python_host_fixture(tmp_path / "fake-loopx", f"""#!{sys.executable}
import json, sys
args = sys.argv[1:]
if "todo" in args and "plan" in args:
    print(json.dumps({{
        "ok": True,
        "schema_version": "loopx_task_planning_v0",
        "goal_id": "goal-1",
        "agent_id": "agent-1",
        "execution_handoff": {{"owner": "caller"}},
        "input_digest": "digest-1",
        "existing_todos": [],
        "goal_waiting_on": [],
        "blocking_todo_ids": [],
        "goal_acceptance": {{
            "provider_revision": "rev-1",
            "goal_acceptance_contract": {{}},
        }},
    }}))
    raise SystemExit(0)
if "goal-acceptance" in args and "refine" in args:
    print(json.dumps({{
        "ok": False,
        "error": "goal_acceptance_invalid_request",
        "error_code": "goal_acceptance_invalid_request",
        "error_detail": "source ref must be bounded nonempty text (got 144 UTF-16 code units, max 128)",
    }}))
    raise SystemExit(1)
raise AssertionError(args)
""")
    proposal_path = tmp_path / "proposal.json"
    proposal = _base_proposal()
    proposal["todos"][0]["target_key"] = "refine-target"
    proposal_path.write_text(json.dumps(proposal))
    monkeypatch.setenv("LOOPX_TASK_STAGE", "plan")
    monkeypatch.setenv("LOOPX_CLI", str(fake_cli))
    monkeypatch.setenv("LOOPX_GOAL_ID", "goal-1")
    monkeypatch.setenv("LOOPX_AGENT_ID", "agent-1")
    monkeypatch.setenv("LOOPX_PROJECT", str(tmp_path))
    monkeypatch.setenv("LOOPX_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.setenv("LOOPX_RUNTIME_ROOT", str(tmp_path / "runtime"))
    task_doc = tmp_path / "task.md"
    task_doc.write_text("Task description\n")
    monkeypatch.setenv("LOOPX_TASK_DOC", str(task_doc))

    import contextlib
    import io
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = planning_batch.main([str(proposal_path)])
    assert code == 1
    payload = json.loads(stdout.getvalue())
    assert payload["ok"] is False
    assert payload["stage"] == "goal-acceptance refine"
    assert payload["error_code"] == "goal_acceptance_invalid_request"
    assert payload["error_detail"] == "source ref must be bounded nonempty text (got 144 UTF-16 code units, max 128)"
    assert payload["error_detail"] in payload["error"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_planning_batch_non_bmp_utf16_boundaries_match_real_cli(tmp_path, monkeypatch, provider):
    from benchmark.runtime.planning_batch import apply_initial_plan

    env, _, _, _, _ = _environment(tmp_path, monkeypatch, provider, replan="empty")
    env["LOOPX_TASK_STAGE"] = "plan"
    packet = task_plan_packet(env, loopx_command(env))
    emoji = "\U0001f600"

    # Over-limit non-BMP counts (1026, 2002, 514 UTF-16 code units) fail upfront in
    # planning_batch using the same UTF-16 unit rule enforced by TS goal acceptance.
    base = {
        "input_digest": packet["input_digest"],
        "expected_provider_revision": packet["goal_acceptance"]["provider_revision"],
        "outcome": {
            "objective": emoji * 512,
            "requirements": [{"id": "req-1", "description": emoji * 1000}],
            "source": {"ref": "checkpoint:emoji", "summary": emoji * 256},
        },
        "todos": [{"text": "[P0] Deliver non-BMP boundary test", "action_kind": "implement"}],
    }
    for bad_outcome, snippet in [
        ({**base["outcome"], "objective": emoji * 513}, "got 1026 UTF-16 code units, max 1024"),
        ({**base["outcome"], "requirements": [{"id": "req-1", "description": emoji * 1001}]}, "got 2002 UTF-16 code units, max 2000"),
        ({**base["outcome"], "source": {"ref": "checkpoint:emoji", "summary": emoji * 257}}, "got 514 UTF-16 code units, max 512"),
    ]:
        with pytest.raises(ValueError, match=snippet):
            apply_initial_plan(env, {**base, "outcome": bad_outcome})

    # Exact-limit non-BMP counts (512, 1000, 256 emoji = 1024, 2000, 512 UTF-16 units)
    # pass planning_batch and commit through the real File/SQLite goal-acceptance CLI.
    ready = apply_initial_plan(env, base)
    assert ready["status"] == "ready"
    assert len(ready["todo_ids"]) == 1
    after = task_plan_packet(env, loopx_command(env))
    outcome = after["goal_acceptance"]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert outcome["objective"] == emoji * 512
    assert outcome["requirements"][0]["description"] == emoji * 1000
    assert outcome["sources"][0]["summary"] == emoji * 256


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_planning_batch_packet_survives_gbk_parent_locale(tmp_path, monkeypatch, provider):
    import subprocess
    from benchmark.runtime.planning_batch import apply_initial_plan

    env, _, _, _, _ = _environment(tmp_path, monkeypatch, provider, replan="empty")
    env["LOOPX_TASK_STAGE"] = "plan"
    objective = "中文目标：统计并交付结果。"
    Path(env["LOOPX_TASK_DOC"]).write_text(objective + "\n", encoding="utf-8")
    # A cp936 host decodes an unpinned text-mode child read as gbk, while the
    # wrapped CLI always emits UTF-8 JSON (ensure_ascii=False).
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "gbk")

    packet = task_plan_packet(env, loopx_command(env))
    todo_text = "[P0] 统计中文结果。"
    proposal = {
        "input_digest": packet["input_digest"],
        "expected_provider_revision": packet["goal_acceptance"]["provider_revision"],
        "outcome": {
            "objective": objective,
            "requirements": [{"id": "deliver", "description": "交付并验证中文结果。"}],
            "source": {"ref": "checkpoint:utf8", "summary": "中文规划回执。"},
        },
        "todos": [{"text": todo_text, "action_kind": "implement"}],
    }
    ready = apply_initial_plan(env, proposal)
    assert ready["status"] == "ready"
    after = task_plan_packet(env, loopx_command(env))
    stored = {item["todo_id"]: item for item in after["existing_todos"]}
    assert stored[ready["todo_ids"][0]]["text"] == todo_text
    outcome = after["goal_acceptance"]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert outcome["objective"] == objective


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_planning_batch_target_key_boundary_real_cli_state(tmp_path, monkeypatch, provider):
    import os
    import subprocess

    env, _, _, _, _ = _environment(tmp_path, monkeypatch, provider, replan="empty")
    env["LOOPX_TASK_STAGE"] = "plan"
    before = task_plan_packet(env, loopx_command(env))
    cli_env = {**os.environ, **env}

    # Explicit null target_key must reject upfront (rc=1) with zero Goal or Todo state changes.
    bad_path = tmp_path / "null-target-key.json"
    bad_path.write_text(json.dumps({
        "input_digest": before["input_digest"],
        "expected_provider_revision": before["goal_acceptance"]["provider_revision"],
        "outcome": {
            "objective": "Verify target_key boundary.",
            "requirements": [{"id": "req-1", "description": "Valid string passes and null rejects."}],
            "source": {"ref": "checkpoint:target-key", "summary": "Target key CLI regression."},
        },
        "todos": [{"text": "[P0] Null target key todo", "action_kind": "implement", "target_key": None}],
    }))
    bad_proc = subprocess.run(
        [sys.executable, "-m", "benchmark.runtime.planning_batch", str(bad_path)],
        env=cli_env, cwd=str(tmp_path), capture_output=True, text=True, timeout=120,
    )
    assert bad_proc.returncode == 1
    assert "todos[0].target_key" in json.loads(bad_proc.stdout)["error"]
    after_bad = task_plan_packet(env, loopx_command(env))
    assert after_bad["goal_acceptance"]["provider_revision"] == before["goal_acceptance"]["provider_revision"]
    assert after_bad["existing_todos"] == []

    # Omitted and valid-string target_key both commit cleanly on the same real CLI boundary.
    good_path = tmp_path / "valid-target-key.json"
    good_path.write_text(json.dumps({
        "input_digest": after_bad["input_digest"],
        "expected_provider_revision": after_bad["goal_acceptance"]["provider_revision"],
        "outcome": {
            "objective": "Verify target_key boundary.",
            "requirements": [{"id": "req-1", "description": "Valid string passes and null rejects."}],
            "source": {"ref": "checkpoint:target-key", "summary": "Target key CLI regression."},
        },
        "todos": [
            {"text": "[P0] Omitted target key todo", "action_kind": "implement"},
            {"text": "[P1] Valid target key todo", "action_kind": "validate", "target_key": "bench-target"},
        ],
    }))
    good_proc = subprocess.run(
        [sys.executable, "-m", "benchmark.runtime.planning_batch", str(good_path)],
        env=cli_env, cwd=str(tmp_path), capture_output=True, text=True, timeout=120,
    )
    assert good_proc.returncode == 0, good_proc.stdout + good_proc.stderr
    assert json.loads(good_proc.stdout)["status"] == "ready"
    after_good = task_plan_packet(env, loopx_command(env))
    by_text = {item["text"]: item for item in after_good["existing_todos"]}
    assert by_text["[P0] Omitted target key todo"].get("target_key") is None
    assert by_text["[P1] Valid target key todo"]["target_key"] == "bench-target"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_planning_batch_cross_version_interrupted_retry_preserves_padded_ids_and_mixed_case_action_kind(
    tmp_path, monkeypatch, provider,
):
    import hashlib
    import os
    import subprocess

    env, project, _, _, _ = _environment(tmp_path, monkeypatch, provider, replan="empty")
    env["LOOPX_TASK_STAGE"] = "plan"
    cli = loopx_command(env)
    before = task_plan_packet(env, cli)
    cli_env = {**os.environ, **env}

    # Unchanged proposal matching pre-upgrade accepted input: whitespace-padded
    # opaque IDs plus mixed-case action tokens ("  Implement  ", "Validate").
    proposal = {
        "input_digest": before["input_digest"],
        "expected_provider_revision": before["goal_acceptance"]["provider_revision"],
        "outcome": {
            "objective": "Verify cross-version interrupted retry recovery.",
            "requirements": [{"id": "  req-legacy  ", "description": "Preserve trimmed ID and raw-case digest."}],
            "source": {"ref": "  checkpoint:legacy  ", "summary": "Legacy interrupted batch retry."},
        },
        "todos": [
            {"text": "[P0] Implement legacy step", "action_kind": "  Implement  "},
            {"text": "[P1] Validate legacy step", "action_kind": "Validate"},
        ],
    }
    proposal_path = tmp_path / "legacy-proposal.json"
    proposal_path.write_text(json.dumps(proposal))

    # Simulate a pre-upgrade partial commit where baseline stripped whitespace
    # while keeping raw-case action_kind in the proposal digest before binding.
    baseline_normalized = {
        "input_digest": before["input_digest"],
        "expected_provider_revision": before["goal_acceptance"]["provider_revision"],
        "outcome": {
            "objective": "Verify cross-version interrupted retry recovery.",
            "requirements": [{"id": "req-legacy", "description": "Preserve trimmed ID and raw-case digest."}],
            "source": {"ref": "checkpoint:legacy", "summary": "Legacy interrupted batch retry."},
        },
        "todos": [
            {"text": "[P0] Implement legacy step", "action_kind": "Implement"},
            {"text": "[P1] Validate legacy step", "action_kind": "Validate"},
        ],
    }
    legacy_digest = hashlib.sha256(json.dumps(baseline_normalized, sort_keys=True).encode()).hexdigest()
    legacy_outcome_id = f"benchmark-plan-{legacy_digest[:32]}"
    operation = f"benchmark-plan:{before['input_digest']}"

    declaration_path = tmp_path / "legacy-declare.json"
    declaration_path.write_text(json.dumps({
        **baseline_normalized["outcome"],
        "outcome_id": legacy_outcome_id,
        "owner_agent_id": before["agent_id"],
    }))
    declare_proc = subprocess.run(
        [
            *cli, "goal-acceptance", "refine",
            "--goal-id", before["goal_id"], "--agent-id", before["agent_id"],
            "--document", str(declaration_path),
            "--expected-provider-revision", before["goal_acceptance"]["provider_revision"],
            "--operation-id", f"{operation}:declare", "--execute",
        ],
        cwd=str(project), env=cli_env, capture_output=True, text=True, timeout=120,
    )
    assert declare_proc.returncode == 0, declare_proc.stdout + declare_proc.stderr
    assert json.loads(declare_proc.stdout)["ok"] is True

    first_todo_proc = subprocess.run(
        [
            *cli, "todo", "add",
            "--goal-id", before["goal_id"], "--project", str(project),
            "--role", "agent", "--claimed-by", before["agent_id"],
            "--task-class", "advancement_task",
            "--text=[P0] Implement legacy step", "--action-kind=Implement",
            "--operation-id", f"{operation}:todo:0", "--execute",
        ],
        cwd=str(project), env=cli_env, capture_output=True, text=True, timeout=120,
    )
    assert first_todo_proc.returncode == 0, first_todo_proc.stdout + first_todo_proc.stderr
    first_todo_receipt = json.loads(first_todo_proc.stdout)
    assert first_todo_receipt["ok"] is True
    first_todo_id = first_todo_receipt["todo_id"]

    # Interrupted recovery on candidate must finish writing todo:1 and bind the
    # original declaration + first_todo_id from the unchanged legacy proposal.
    retry_proc = subprocess.run(
        [sys.executable, "-m", "benchmark.runtime.planning_batch", str(proposal_path)],
        env=cli_env, cwd=str(tmp_path), capture_output=True, text=True, timeout=120,
    )
    assert retry_proc.returncode == 0, retry_proc.stdout + retry_proc.stderr
    retry_payload = json.loads(retry_proc.stdout)
    assert retry_payload["status"] == "ready"
    todo_ids = retry_payload["todo_ids"]
    assert len(todo_ids) == 2 and todo_ids[0] == first_todo_id

    after = task_plan_packet(env, cli)
    assert after["goal_acceptance"]["planning_requirements"]["linked_todo_ids"] == todo_ids
    outcomes = after["goal_acceptance"]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
    assert len(outcomes) == 1
    assert outcomes[0]["id"] == legacy_outcome_id
    assert outcomes[0]["requirements"][0]["id"] == "req-legacy"
    assert outcomes[0]["sources"][0]["ref"] == "checkpoint:legacy"
    by_id = {item["todo_id"]: item for item in after["existing_todos"]}
    assert set(by_id) == set(todo_ids)
    assert [by_id[item]["action_kind"] for item in todo_ids] == ["implement", "validate"]
