"""Bounded M1/M2/M3 scenarios and deterministic constraint/efficiency oracles."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
from typing import Any

from benchmark.runtime.session_observation import collect_context_policy_telemetry
from loopx.control_plane.goals.acceptance import (
    inspect_goal_acceptance,
    refine_goal_outcome,
)
from loopx.control_plane.quota.turn_envelope import (
    build_semantic_unit_fold_swap,
    evaluate_epoch_prefetch_prompt_gate,
    evaluate_epoch_prefetch_swap,
    record_semantic_unit_anchor,
)
from loopx.control_plane.testing import canary_harness as _canary_harness
from loopx.control_plane.testing.canary_harness import (
    assert_todo_read_unchanged,
    import_fixture_authority,
    run_json_cli_result,
    write_fixture_registry,
)
from loopx.control_plane.todos.list_readback import list_goal_todos
from loopx.control_plane.turn_driver.codex_context import codex_context_hook
from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference
from loopx.control_plane.turn_driver.epoch_prefetch import (
    apply_turn_epoch_prefetch_to_request,
)

from .metrics import (
    BehaviorConstraintResult,
    EfficiencyMetrics,
    MicrobenchDeadlineExpired,
    measure_text_tokens,
    require_remaining_budget,
    resolve_deadline,
)

MICROBENCH_SCENARIO_IDS = (
    "m1_envelope_scoped_recovery",
    "m2_epoch_prefetch_semantic_fold",
    "m3_claim_block_settle_invariants",
)

M1_GOAL_ID = "microbench-m1-goal"
M1_AGENT_ID = "agent-m1"
M1_BLOCKED_TODO_ID = "todo_primary_blocked"
M1_SELECTED_TODO_ID = "todo_selected_work"
M1_PROOF_REL = "fixture/m1-proof.json"
M1_PROOF_TOKEN = "M1_RECOVERY_PROOF_OK"

M3_GATE_GOAL_ID = "microbench-m3-gate-goal"
M3_CLAIM_GOAL_ID = "microbench-m3-claim-goal"
M3_AGENT_PRIMARY = "agent-primary"
M3_AGENT_PEER = "agent-peer"
M3_GATE_TODO_ID = "todo_m3_user_gate"
M3_CLAIM_TODO_ID = "todo_m3_claimable"
M3_ARTIFACT_REL = "fixture/m3-artifact.json"
M3_ARTIFACT_TOKEN = "M3_SETTLE_VALID_01"


@dataclass(frozen=True)
class PreparedMicrobenchFixture:
    """Self-contained synthetic fixture inside an isolated temp root."""

    scenario_id: str
    root: Path
    project_root: Path
    runtime_root: Path
    registry_path: Path
    captures_dir: Path
    native_dir: Path
    artifact_rel_path: str | None = None
    expected_token: str | None = None
    metadata: dict[str, Any] | None = None


def _run_cli_with_source(
    *args: str,
    registry_path: Path,
    runtime_root: Path,
    source_root: Path | None = None,
    cwd: Path | None = None,
    env_overrides: Mapping[str, str | None] | None = None,
    deadline_monotonic: float | None = None,
) -> tuple[int, dict[str, Any]]:
    deadline = resolve_deadline(deadline_monotonic)
    timeout_s = 60.0
    if deadline is not None:
        timeout_s = min(60.0, require_remaining_budget(deadline, stage="cli"))
    if source_root is None and env_overrides is None and deadline is None:
        return run_json_cli_result(
            *args,
            registry_path=registry_path,
            runtime_root=runtime_root,
            cwd=cwd,
        )
    env = os.environ.copy()
    env.pop("LOOPX_RELEASE_ROOT", None)
    if source_root is not None:
        env["PYTHONPATH"] = str(source_root)
    if env_overrides is None:
        env["LOOPX_CONTEXT_DELIVERY"] = "off"
    else:
        for key, value in env_overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "--format",
        "json",
        *args,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=cwd or registry_path.parent.parent,
            check=False,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        if deadline is not None:
            raise MicrobenchDeadlineExpired("cli_timeout") from exc
        raise
    if not completed.stdout.strip():
        raise AssertionError(
            f"empty CLI output ({completed.returncode}) for {command!r}: {completed.stderr}"
        )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise AssertionError(f"expected JSON object for {command!r}: {payload!r}")
    return completed.returncode, payload


_CANARY_CLI_PATCH_LOCK = threading.RLock()


@contextmanager
def _bounded_canary_cli(deadline_monotonic: float | None) -> Iterator[None]:
    """Ensure CLI invocations inside ``canary_harness.import_fixture_authority`` obey deadlines."""
    deadline = resolve_deadline(deadline_monotonic)
    if deadline is None:
        yield
        return
    with _CANARY_CLI_PATCH_LOCK:
        orig_run_json_cli = _canary_harness.run_json_cli

        def _bounded_run_json_cli(
            *args: str,
            registry_path: Path,
            runtime_root: Path | None = None,
            cwd: Path | None = None,
            include_returncode: bool = True,
        ) -> dict[str, Any]:
            if runtime_root is None:
                return orig_run_json_cli(
                    *args,
                    registry_path=registry_path,
                    runtime_root=runtime_root,
                    cwd=cwd,
                    include_returncode=include_returncode,
                )
            rc, payload = _run_cli_with_source(
                *args,
                registry_path=registry_path,
                runtime_root=runtime_root,
                cwd=cwd,
                deadline_monotonic=deadline,
            )
            if rc != 0:
                raise AssertionError(json.dumps(payload))
            if include_returncode:
                payload["_returncode"] = rc
            return payload

        _canary_harness.run_json_cli = _bounded_run_json_cli
        try:
            yield
        finally:
            _canary_harness.run_json_cli = orig_run_json_cli


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _mkdir_private(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)
    return path


def _materialize_pointer_selections(selections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    root: dict[str, Any] = {}
    for row in selections:
        pointer = str(row.get("pointer") or "")
        if not pointer.startswith("/"):
            continue
        tokens = [
            part.replace("~1", "/").replace("~0", "~")
            for part in pointer.lstrip("/").split("/")
        ]
        if not tokens:
            continue
        cursor: Any = root
        for idx, tok in enumerate(tokens):
            is_last = idx == len(tokens) - 1
            nxt_is_idx = not is_last and tokens[idx + 1].isdigit()
            if tok.isdigit() and isinstance(cursor, list):
                pos = int(tok)
                while len(cursor) <= pos:
                    cursor.append(None)
                if is_last:
                    cursor[pos] = row.get("value")
                else:
                    if cursor[pos] is None or not isinstance(cursor[pos], (dict, list)):
                        cursor[pos] = [] if nxt_is_idx else {}
                    cursor = cursor[pos]
            elif isinstance(cursor, dict):
                if is_last:
                    cursor[tok] = row.get("value")
                else:
                    existing = cursor.get(tok)
                    if existing is None or (
                        nxt_is_idx and not isinstance(existing, list)
                    ) or (
                        not nxt_is_idx and not isinstance(existing, dict)
                    ):
                        cursor[tok] = [] if nxt_is_idx else {}
                    cursor = cursor[tok]
    return root


def _unwrap_context_capture_payload(view: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    if "recovery" not in view:
        return view, None
    _, ref = read_context_read_reference(view["recovery"]["reference_file"])
    req_path = Path(ref["path"])
    payload = json.loads(req_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return view, req_path
    return payload, req_path


def _spend_run_count(runtime_root: Path, goal_id: str) -> int:
    index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    if not index_path.is_file():
        return 0
    return sum(
        1
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("classification") == "quota_slot_spent"
    )


def prepare_m1_fixture(
    root: Path,
    *,
    historical_outcomes: int = 5,
    source_root: Path | None = None,
    deadline_monotonic: float | None = None,
) -> PreparedMicrobenchFixture:
    """Create isolated M1 fixture (#9498/#9504): blocked P0 + runnable P1 + historical acceptance."""
    deadline = resolve_deadline(deadline_monotonic)
    if deadline is not None:
        require_remaining_budget(deadline, stage="prepare_m1_fixture")
    project = root / "project"
    runtime = root / "runtime"
    registry_path = project / ".loopx" / "registry.json"
    captures_dir = _mkdir_private(root / "captures")
    native_dir = _mkdir_private(root / "native")

    hist_todo_lines = "".join(
        f"- [x] [P2] Completed historical baseline slice {idx}.\n"
        f"  <!-- loopx:todo todo_id=todo_hist_{idx} status=done claimed_by=peer-hist "
        "task_class=advancement_task action_kind=implement -->\n"
        for idx in range(historical_outcomes)
    )
    state_rel = f".codex/goals/{M1_GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_rel
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Validate scoped envelope delivery while keeping blocked P0 visible."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# M1 Scoped Envelope Fixture\n\n"
        "## Objective\n\n"
        "Validate scoped envelope delivery while keeping blocked P0 visible.\n\n"
        "## Next Action\n\n"
        f"- Execute `{M1_SELECTED_TODO_ID}` after inspecting `{M1_PROOF_REL}`.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P0] Wait for external gate clearance on the blocked primary lane.\n"
        f"  <!-- loopx:todo todo_id={M1_BLOCKED_TODO_ID} status=blocked claimed_by={M1_AGENT_ID} "
        "task_class=advancement_task action_kind=implement -->\n"
        f"- [ ] [P1] Read `{M1_PROOF_REL}` and verify the selected lane proof token.\n"
        f"  <!-- loopx:todo todo_id={M1_SELECTED_TODO_ID} status=open claimed_by={M1_AGENT_ID} "
        "task_class=advancement_task action_kind=validate -->\n"
        f"{hist_todo_lines}",
        encoding="utf-8",
    )
    proof_path = project / M1_PROOF_REL
    proof_path.parent.mkdir(parents=True, exist_ok=True)
    proof_path.write_text(
        json.dumps(
            {
                "lane": "selected",
                "proof_token": M1_PROOF_TOKEN,
                "blocked_primary": M1_BLOCKED_TODO_ID,
                "selected_todo_id": M1_SELECTED_TODO_ID,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=M1_GOAL_ID,
        domain="microbench-m1",
        adapter_kind="read_only_project_map_v0",
        adapter_status="connected-read-only",
        status="active",
        registered_agents=[M1_AGENT_ID, "peer-hist"],
    )
    with _bounded_canary_cli(deadline):
        import_fixture_authority(registry_path, M1_GOAL_ID)

    # Seed historical + current acceptance outcomes to exercise turn-scoped trimming (#9498).
    acceptance_outcomes: list[dict[str, Any]] = []
    for idx in range(historical_outcomes):
        reqs = [
            {
                "id": f"hreq-{idx}-{j}",
                "description": (
                    f"Historical acceptance clause {idx}.{j}: preserve invariant checks "
                    "without replaying obsolete verbose diagnostics inline."
                ),
            }
            for j in range(6)
        ]
        acceptance_outcomes.append(
            {
                "outcome_id": f"outcome-hist-{idx}",
                "owner_agent_id": "peer-hist",
                "objective": f"Historical outcome {idx} completed in an earlier slice.",
                "todo_ids": [f"todo_hist_{idx}"],
                "storage_format": "records",
                "source": {"ref": f"spec:hist-{idx}", "summary": f"Historical provenance {idx}."},
                "requirements": reqs,
            }
        )
    acceptance_outcomes.append(
        {
            "outcome_id": "outcome-current",
            "owner_agent_id": M1_AGENT_ID,
            "objective": f"Verify `{M1_PROOF_REL}` proof token while keeping `{M1_BLOCKED_TODO_ID}` blocked.",
            "todo_ids": [M1_SELECTED_TODO_ID],
            "storage_format": "records",
            "source": {"ref": "spec:m1-current", "summary": "Current M1 verification contract."},
            "requirements": [
                {
                    "id": "m1-req-1",
                    "description": f"Select {M1_SELECTED_TODO_ID} and keep {M1_BLOCKED_TODO_ID} blocked.",
                },
                {
                    "id": "m1-req-2",
                    "description": f"Extract {M1_PROOF_TOKEN} from {M1_PROOF_REL}.",
                },
            ],
        }
    )
    if source_root is None:
        inspected = inspect_goal_acceptance(
            registry_path=registry_path,
            goal_id=M1_GOAL_ID,
            runtime_root=str(runtime),
            agent_id=M1_AGENT_ID,
        )
        expected_rev = str(inspected.get("provider_revision") or "")
        for idx, outcome in enumerate(acceptance_outcomes):
            res = refine_goal_outcome(
                registry_path=registry_path,
                goal_id=M1_GOAL_ID,
                refinement=outcome,
                expected_provider_revision=expected_rev,
                operation_id=f"op-seed-m1-{idx}-{outcome['outcome_id']}",
                agent_id=str(outcome["owner_agent_id"]),
                runtime_root=str(runtime),
                execute=True,
            )
            expected_rev = str(res.get("provider_revision") or expected_rev)
    else:
        rev_rc, inspected = _run_cli_with_source(
            "goal-acceptance",
            "inspect",
            "--goal-id",
            M1_GOAL_ID,
            "--agent-id",
            M1_AGENT_ID,
            registry_path=registry_path,
            runtime_root=runtime,
            source_root=source_root,
        )
        assert rev_rc == 0, inspected
        expected_rev = str(inspected.get("provider_revision") or "")
        for idx, outcome in enumerate(acceptance_outcomes):
            doc_path = root / f"{outcome['outcome_id']}.json"
            doc_path.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
            refine_args = [
                "goal-acceptance",
                "refine",
                "--goal-id",
                M1_GOAL_ID,
                "--agent-id",
                str(outcome["owner_agent_id"]),
                "--document",
                str(doc_path),
                "--operation-id",
                f"op-seed-m1-{idx}-{outcome['outcome_id']}",
                "--execute",
            ]
            if expected_rev:
                refine_args.extend(["--expected-provider-revision", expected_rev])
            rc, res = _run_cli_with_source(
                *refine_args,
                registry_path=registry_path,
                runtime_root=runtime,
                source_root=source_root,
            )
            assert rc == 0, res
            expected_rev = str(res.get("provider_revision") or expected_rev)

    return PreparedMicrobenchFixture(
        scenario_id="m1_envelope_scoped_recovery",
        root=root,
        project_root=project,
        runtime_root=runtime,
        registry_path=registry_path,
        captures_dir=captures_dir,
        native_dir=native_dir,
        artifact_rel_path=M1_PROOF_REL,
        expected_token=M1_PROOF_TOKEN,
    )


def _find_source(payload: Mapping[str, Any], source_name: str) -> Mapping[str, Any] | None:
    ic = payload.get("interaction_contract")
    if not isinstance(ic, Mapping):
        return None
    agent_ch = ic.get("agent_channel")
    if not isinstance(agent_ch, Mapping):
        return None
    wc = agent_ch.get("work_context")
    if not isinstance(wc, Mapping):
        return None
    sources = wc.get("sources")
    if not isinstance(sources, list):
        return None
    for src in sources:
        if isinstance(src, Mapping) and src.get("source") == source_name:
            return src
    return None


def _m1_arm_capture(
    fixture: PreparedMicrobenchFixture,
    *,
    arm: str,
    context_delivery: str,
    source_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], Path | None, BehaviorConstraintResult, EfficiencyMetrics]:
    arm_dir = _mkdir_private(fixture.captures_dir / arm)
    native_dir = _mkdir_private(fixture.native_dir / arm)

    thread_id = f"m1-{arm}-thread"
    env_overrides: dict[str, str | None] = {"CODEX_THREAD_ID": thread_id}
    args = [
        "quota",
        "should-run",
        "--codex-app",
        "--context-delivery",
        context_delivery,
        "--goal-id",
        M1_GOAL_ID,
        "--agent-id",
        M1_AGENT_ID,
        "--todo-id",
        M1_SELECTED_TODO_ID,
        "--turn-instance-id",
        f"turn-m1-{arm}",
    ]
    if context_delivery == "on":
        codex_context_hook(
            str(native_dir),
            {
                "hook_event_name": "SessionStart",
                "session_id": thread_id,
                "cwd": str(fixture.project_root),
                "source": "startup",
            },
        )
        args.extend(
            [
                "--delta-delivery",
                "auto",
                "--codex-context-dir",
                str(native_dir),
                "--decision-output-root",
                str(arm_dir.resolve()),
            ]
        )
    else:
        env_overrides["CODEX_THREAD_ID"] = None

    rc, view = _run_cli_with_source(
        *args,
        registry_path=fixture.registry_path,
        runtime_root=fixture.runtime_root,
        source_root=source_root,
        env_overrides=env_overrides,
    )
    assert rc == 0, view

    decision_path: Path | None = None
    delivered = view
    readback_ok = True
    hist_readback_ok = True
    scoped_trimmed_ok = True
    pointer_compacted = False
    round_trips = 1
    measured_text = json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    if context_delivery == "on" and "first_read_command" in view:
        first_tokens = shlex.split(str(view["first_read_command"]))
        first_ref_path = first_tokens[first_tokens.index("--reference-file") + 1]
        fr_rc, fr_out = _run_cli_with_source(
            "quota",
            "context-read",
            "--reference-file",
            first_ref_path,
            "--offset",
            "0",
            "--page-bytes",
            "64000",
            "--max-page-output-bytes",
            "64000",
            registry_path=fixture.registry_path,
            runtime_root=fixture.runtime_root,
            source_root=source_root,
        )
        assert fr_rc == 0, fr_out
        round_trips += 1
        first_doc = json.loads(str(fr_out["content"]))
        selections = first_doc.get("selections") if isinstance(first_doc.get("selections"), list) else []
        delivered = _materialize_pointer_selections(
            [row for row in selections if isinstance(row, Mapping)]
        )
        measured_text = json.dumps(
            {"navigation": view, "first_read": first_doc},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        decision_files = list(arm_dir.rglob("decision.json"))
        if decision_files:
            decision_path = decision_files[0]
        elif "recovery" in view:
            _, rec_ref = read_context_read_reference(view["recovery"]["reference_file"])
            decision_path = Path(rec_ref["path"])
    elif "recovery" in view:
        delivered, req_path = _unwrap_context_capture_payload(view)
        if req_path is not None:
            decision_files = list(arm_dir.rglob("decision.json"))
            decision_path = decision_files[0] if decision_files else req_path
        measured_text = json.dumps(delivered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    # Evaluate M1 invariants on the delivered model view + recovery readback.
    selected_todo = _mapping(delivered.get("selected_todo"))
    blocked_fb = _mapping(delivered.get("blocked_priority_fallback"))
    blocked_ids = [
        row.get("todo_id")
        for row in _list(blocked_fb.get("blocked_items"))
        if isinstance(row, Mapping)
    ]
    ga_src = _find_source(delivered, "goal_acceptance")
    ga_content = _mapping(_mapping(ga_src).get("content"))
    ga_contract = _mapping(ga_content.get("goal_acceptance_contract"))
    outcome_reqs = _mapping(ga_contract.get("outcome_requirements"))
    outcomes = _list(outcome_reqs.get("outcomes"))

    if context_delivery == "on" and decision_path is not None and decision_path.is_file():
        decision_bytes = decision_path.read_bytes()
        decision_sha = hashlib.sha256(decision_bytes).hexdigest()
        decision_full = json.loads(decision_bytes.decode("utf-8"))
        pointer_compacted = (
            bool(view.get("first_read_command"))
            and len(measured_text.encode("utf-8")) < len(decision_bytes)
        )
        hist_rows = [
            row
            for row in outcomes
            if isinstance(row, Mapping) and str(row.get("id", "")).startswith("outcome-hist-")
        ]
        curr_rows = [
            row
            for row in outcomes
            if isinstance(row, Mapping) and row.get("id") == "outcome-current"
        ]
        scoped_trimmed_ok = (
            bool(hist_rows)
            and all("requirements" not in row for row in hist_rows)
            and bool(curr_rows)
            and isinstance(curr_rows[0].get("requirements"), list)
            and len(curr_rows[0]["requirements"]) == 2
        )
        hist_pointer = "/interaction_contract/agent_channel/work_context/sources/1/content/goal_acceptance_contract/outcome_requirements/outcomes/0/requirements"
        rb_rc, rb_out = _run_cli_with_source(
            "quota",
            "context-read",
            "--capture-file",
            str(decision_path),
            "--capture-sha256",
            decision_sha,
            "--select",
            "/blocked_priority_fallback",
            "--select",
            hist_pointer,
            registry_path=fixture.registry_path,
            runtime_root=fixture.runtime_root,
            source_root=source_root,
        )
        round_trips += 1
        if rb_rc != 0:
            readback_ok = False
            hist_readback_ok = False
        else:
            sels = {
                str(row.get("pointer")): row.get("value")
                for row in (json.loads(rb_out["content"]).get("selections", []) or [])
                if isinstance(row, Mapping)
            }
            recovered_fb = sels.get("/blocked_priority_fallback")
            readback_ok = (
                "/blocked_priority_fallback" in sels
                and recovered_fb == decision_full.get("blocked_priority_fallback")
            )
            if isinstance(recovered_fb, Mapping):
                blocked_ids = [
                    row.get("todo_id")
                    for row in (recovered_fb.get("blocked_items") or [])
                    if isinstance(row, Mapping)
                ]
            hist_vals = sels.get(hist_pointer)
            hist_readback_ok = (
                isinstance(hist_vals, list)
                and len(hist_vals) == 6
                and scoped_trimmed_ok
            )

    checks = {
        "effective_action_normal_run": delivered.get("effective_action") == "normal_run",
        "selected_todo_matches_runnable": selected_todo.get("todo_id") == M1_SELECTED_TODO_ID,
        "blocked_p0_preserved": M1_BLOCKED_TODO_ID in blocked_ids,
        "anti_drift_not_blocked_todo": selected_todo.get("todo_id") != M1_BLOCKED_TODO_ID,
        "blocked_fallback_readback_parity": readback_ok,
        "historical_acceptance_readback_parity": hist_readback_ok,
        "slim_pointer_active_when_on": (pointer_compacted if context_delivery == "on" else True),
    }
    violations = tuple(name for name, ok in checks.items() if not ok)
    prompt_tokens, _ = measure_text_tokens(measured_text)
    efficiency = EfficiencyMetrics(
        wire_bytes=len(measured_text.encode("utf-8")),
        prompt_tokens=prompt_tokens,
        round_trips=round_trips,
        tool_calls=round_trips,
    )
    return (
        view,
        delivered,
        decision_path,
        BehaviorConstraintResult(passed=not violations, checks=checks, violations=violations),
        efficiency,
    )


def run_m1_deterministic_arm(
    fixture: PreparedMicrobenchFixture,
    *,
    arm: str,
    source_root: Path | None = None,
    context_delivery: str | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Run deterministic Base (`off`) or Head (`on`) arm for M1 (#9498/#9504)."""
    if context_delivery not in (None, "auto", "off", "on"):
        raise ValueError(f"unsupported context_delivery: {context_delivery!r}")
    deadline = resolve_deadline(deadline_monotonic)
    if deadline is not None:
        require_remaining_budget(deadline, stage=f"m1_deterministic:{arm}")
    delivery = (
        ("off" if arm == "base" else "on")
        if context_delivery in (None, "auto")
        else str(context_delivery)
    )
    view, delivered, decision_path, behavior, efficiency = _m1_arm_capture(
        fixture,
        arm=arm,
        context_delivery=delivery,
        source_root=source_root,
    )
    return {
        "scenario_id": fixture.scenario_id,
        "arm": arm,
        "context_delivery": delivery,
        "has_recovery_capture": decision_path is not None,
        "behavior_constraint": behavior,
        "efficiency": efficiency,
        "delivered_payload": delivered,
        "cli_view": view,
        "decision_path": decision_path,
    }


def prepare_m2_fixture(
    root: Path,
    *,
    deadline_monotonic: float | None = None,
) -> PreparedMicrobenchFixture:
    """Create isolated M2 fixture (#9503 Stage-then-Swap & Semantic-Unit Fold)."""
    deadline = resolve_deadline(deadline_monotonic)
    if deadline is not None:
        require_remaining_budget(deadline, stage="prepare_m2_fixture")
    project = root / "project"
    runtime = root / "runtime"
    project.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    captures_dir = _mkdir_private(root / "captures")
    native_dir = _mkdir_private(root / "native")
    registry_path = project / ".loopx" / "registry.json"
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id="microbench-m2-goal",
        domain="microbench-m2",
        adapter_kind="read_only_project_map_v0",
        adapter_status="connected-read-only",
        registered_agents=["agent-m2"],
    )

    failure_facts = [
        "regression: retry worker ignores expired lease backoff ceiling",
        "guard: duplicate spend rejected with idempotent replay check",
    ]
    open_obligations = [
        "verify backoff ceiling test on sqlite provider",
        "preserve terminal settlement receipt integrity",
    ]
    raw_lines = [
        f"[step {i:02d}] diagnostic scan of fixture worker buffer line {i} — unchanged baseline verify ok"
        for i in range(60)
    ]
    raw_lines.append("CRITICAL_FAILURE_FACT_0: " + failure_facts[0])
    raw_lines.append("CRITICAL_FAILURE_FACT_1: " + failure_facts[1])
    raw_lines.append("OPEN_OBLIGATION_0: " + open_obligations[0])
    raw_lines.append("OPEN_OBLIGATION_1: " + open_obligations[1])
    raw_text = "\n".join(raw_lines) + "\n"
    raw_path = project / "fixture" / "m2-exploratory.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = raw_text.encode("utf-8")
    raw_path.write_bytes(raw_bytes)
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    return PreparedMicrobenchFixture(
        scenario_id="m2_epoch_prefetch_semantic_fold",
        root=root,
        project_root=project,
        runtime_root=runtime,
        registry_path=registry_path,
        captures_dir=captures_dir,
        native_dir=native_dir,
        artifact_rel_path="fixture/m2-exploratory.log",
        expected_token=raw_sha[:16],
        metadata={
            "raw_path": str(raw_path),
            "raw_sha256": raw_sha,
            "raw_text": raw_text,
            "raw_bytes": len(raw_bytes),
            "failure_facts": failure_facts,
            "open_obligations": open_obligations,
        },
    )


def run_m2_deterministic_arm(
    fixture: PreparedMicrobenchFixture,
    *,
    arm: str,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Run deterministic Base (`KeepRaw`) vs Head (`FoldToReceipt`/`EpochReset` + negative gate)."""
    deadline = resolve_deadline(deadline_monotonic)
    if deadline is not None:
        require_remaining_budget(deadline, stage=f"m2_deterministic:{arm}")
    meta = dict(fixture.metadata or {})
    raw_text = str(meta["raw_text"])
    raw_path = Path(str(meta["raw_path"]))
    raw_sha = str(meta["raw_sha256"])
    failure_facts = list(meta["failure_facts"])
    open_obligations = list(meta["open_obligations"])
    source_ref = {"path": str(raw_path), "sha256": raw_sha}

    baseline_tokens, tokenizer_meta = measure_text_tokens(raw_text)
    if baseline_tokens is None:
        baseline_tokens = max(1, len(raw_text) // 4)
        tokenizer_meta = {"name": "fallback_char_div4", "encoding": "o200k_base"}

    compact_replacement_obj = {
        "schema_version": "loopx_semantic_fold_summary_v0",
        "todo_id": "todo_m2_unit",
        "source_ref": {"sha256": raw_sha},
        "failure_facts": failure_facts,
        "open_obligations": open_obligations,
    }
    compact_replacement = json.dumps(compact_replacement_obj, sort_keys=True, separators=(",", ":"))
    replacement_tokens, _ = measure_text_tokens(compact_replacement)
    if replacement_tokens is None:
        replacement_tokens = max(1, len(compact_replacement) // 4)

    prefix_digest = hashlib.sha256(b"m2-clean-prefix-q").hexdigest()
    anchor = record_semantic_unit_anchor(
        todo_id="todo_m2_unit",
        turn_instance_id="turn-m2-1",
        anchor_step_index=3,
        anchor_surface_seq=8,
        prefix_digest=prefix_digest,
        context_epoch="epoch-m2",
    )

    if arm == "base":
        prompt_text = raw_text
        prompt_bytes = len(prompt_text.encode("utf-8"))
        prompt_tokens = baseline_tokens
        checks = {
            "raw_failure_facts_present": all(f in prompt_text for f in failure_facts),
            "raw_open_obligations_present": all(o in prompt_text for o in open_obligations),
            "raw_state_unfolded": True,
        }
        violations = tuple(k for k, ok in checks.items() if not ok)
        return {
            "scenario_id": fixture.scenario_id,
            "arm": "base",
            "behavior_constraint": BehaviorConstraintResult(passed=True, checks=checks, violations=violations),
            "efficiency": EfficiencyMetrics(
                wire_bytes=prompt_bytes,
                prompt_tokens=prompt_tokens,
                tool_calls=0,
                round_trips=1,
            ),
            "prompt_payload": {
                "mode": "KeepRaw",
                "exploratory_text": raw_text,
                "failure_facts": failure_facts,
                "open_obligations": open_obligations,
            },
            "semantic_unit_settlement_hook_available": _has_todo_unit_settlement(),
        }

    # Head arm: exercise real EpochReset prompt gate + FoldToReceipt + fail-closed KeepRaw.
    verified_page_json = json.dumps(
        {
            "ok": True,
            "schema_version": "context_capture_selection_page_v1",
            "sha256": raw_sha,
            "offset": 0,
            "next_offset": None,
            "total_bytes": len(compact_replacement.encode("utf-8")),
            "eof": True,
            "content": compact_replacement,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    pos_swap = evaluate_epoch_prefetch_swap(
        epoch_prefetch={
            "enabled": True,
            "prefetch_command": ["cat", str(raw_path)],
            "min_net_savings_tokens": 64,
        },
        context_epoch="epoch-m2",
        reclaimed_turns=3,
        dropped_failures=0,
        failure_facts=failure_facts,
        open_obligations=open_obligations,
        source_ref=source_ref,
        exit_code=0,
        raw_bytes_read=len(raw_text.encode("utf-8")),
        prefetched_output=verified_page_json,
        baseline_bytes=len(raw_text.encode("utf-8")),
        baseline_tokens=baseline_tokens,
        hydrated_tokens=replacement_tokens,
        tokenizer=tokenizer_meta,
        require_token_gate=True,
    )

    def _render_request(req: Mapping[str, Any]) -> str:
        ep = req.get("epoch_prefetch") if isinstance(req.get("epoch_prefetch"), Mapping) else None
        ep_prompt = (
            {
                "context_epoch": ep.get("context_epoch"),
                "source_ref": ep.get("source_ref"),
                "header_fields": ep.get("header_fields"),
                "hydrated_text": ep.get("hydrated_text"),
            }
            if ep and ep.get("operator") == "EpochReset"
            else None
        )
        return json.dumps(
            {
                "exploratory_context": req.get("exploratory_context"),
                "epoch_prefetch_context": ep_prompt,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    pos_request = apply_turn_epoch_prefetch_to_request(
        {"exploratory_context": {"raw_text": raw_text}},
        pos_swap,
        replace_exploratory_context=True,
        epoch_prefetch={"enabled": True, "min_net_savings_tokens": 64, "replace_exploratory_context": True},
        host_input_renderer=_render_request,
    )
    pos_ep = _mapping(pos_request.get("epoch_prefetch"))

    # Negative case 1: prompt bloat -> prompt gate must abort atomically to KeepRaw.
    neg_prompt_gate = evaluate_epoch_prefetch_prompt_gate(
        epoch_prefetch={"enabled": True, "min_net_savings_tokens": 64},
        has_inline_exploratory=True,
        replace_exploratory_context=True,
        host_input_measurable=True,
        rendered_prompt_baseline_bytes=800,
        rendered_prompt_delivered_bytes=1200,
        rendered_prompt_baseline_tokens=200,
        rendered_prompt_delivered_tokens=310,
        tokenizer=tokenizer_meta,
    )

    # Positive & Negative #9503 semantic-unit fold swaps:
    committed_fold = build_semantic_unit_fold_swap(
        enabled=True,
        anchor=anchor,
        todo_id="todo_m2_unit",
        turn_instance_id="turn-m2-1",
        context_epoch="epoch-m2",
        prefix_digest=prefix_digest,
        now_step_index=10,
        now_surface_seq=22,
        completion_receipt={
            "todo_id": "todo_m2_unit",
            "completion_receipt_id": "cqr_m2_unit",
            "exit_code": 0,
            "sha256": raw_sha,
            "failure_facts": failure_facts,
            "open_obligations": open_obligations,
            "source_ref": source_ref,
        },
        baseline_tokens=baseline_tokens,
        replacement_tokens=replacement_tokens,
        tokenizer=tokenizer_meta,
    )
    rolled_back_fold = build_semantic_unit_fold_swap(
        enabled=True,
        anchor=anchor,
        todo_id="todo_m2_unit",
        turn_instance_id="turn-m2-1",
        context_epoch="epoch-m2",
        prefix_digest=prefix_digest,
        now_step_index=10,
        now_surface_seq=22,
        completion_receipt=None,
        baseline_tokens=baseline_tokens,
        replacement_tokens=replacement_tokens,
        tokenizer=tokenizer_meta,
        failure_facts=failure_facts,
        open_obligations=open_obligations,
    )

    # Record synthetic telemetry into isolated runtime dir and verify collector parity.
    telemetry_turn = fixture.runtime_root / "goals" / "microbench-m2-goal" / "turns"
    telemetry_turn.mkdir(parents=True, exist_ok=True)
    (telemetry_turn / "turn-m2-pos.json").write_text(
        json.dumps({"turn_key": "turn-m2-pos", "epoch_prefetch": dict(pos_ep)}),
        encoding="utf-8",
    )
    (telemetry_turn / "turn-m2-neg.json").write_text(
        json.dumps({"turn_key": "turn-m2-neg", "epoch_prefetch": dict(neg_prompt_gate)}),
        encoding="utf-8",
    )
    telemetry = collect_context_policy_telemetry(
        fixture.runtime_root,
        goal_id="microbench-m2-goal",
    )

    head_prompt_text = _render_request(pos_request)
    head_bytes = len(head_prompt_text.encode("utf-8"))
    head_tokens, _ = measure_text_tokens(head_prompt_text)

    checks = {
        "positive_prefetch_gate_selected_epoch_reset": pos_ep.get("operator") == "EpochReset",
        "positive_semantic_fold_selected_fold_to_receipt": committed_fold.get("operator") == "FoldToReceipt",
        "prefix_kv_preserved_on_fold": committed_fold.get("preserves_prefix_kv") is True,
        "anti_whitewashing_failure_facts_preserved": committed_fold.get("preserved_failure_facts") == failure_facts,
        "anti_whitewashing_open_obligations_preserved": committed_fold.get("preserved_open_obligations") == open_obligations,
        "negative_net_savings_fails_closed_to_keep_raw": (
            neg_prompt_gate.get("operator") == "KeepRaw"
            and neg_prompt_gate.get("preserves_raw_state") is True
            and neg_prompt_gate.get("bytes_saved") == 0
        ),
        "missing_receipt_fold_fails_closed_to_keep_raw": (
            rolled_back_fold.get("operator") == "KeepRaw"
            and rolled_back_fold.get("fallback_reason") == "missing_completion_receipt"
            and rolled_back_fold.get("preserved_failure_facts") == failure_facts
            and rolled_back_fold.get("preserved_open_obligations") == open_obligations
        ),
        "net_token_reduction_positive": (
            isinstance(head_tokens, int) and head_tokens < baseline_tokens
        ),
        "telemetry_operator_counts_consistent": (
            telemetry["operator_counts"]["EpochReset"] == 1
            and telemetry["operator_counts"]["KeepRaw"] == 1
        ),
    }
    violations = tuple(name for name, ok in checks.items() if not ok)
    return {
        "scenario_id": fixture.scenario_id,
        "arm": "head",
        "behavior_constraint": BehaviorConstraintResult(passed=not violations, checks=checks, violations=violations),
        "efficiency": EfficiencyMetrics(
            wire_bytes=head_bytes,
            prompt_tokens=head_tokens,
            tool_calls=1,
            round_trips=1,
        ),
        "prompt_payload": {
            "mode": "FoldToReceipt",
            "fold_receipt": {
                "todo_id": "todo_m2_unit",
                "completion_receipt_id": "cqr_m2_unit",
                "source_sha256": raw_sha,
                "failure_facts": failure_facts,
                "open_obligations": open_obligations,
            },
            "telemetry": telemetry,
        },
        "semantic_unit_settlement_hook_available": _has_todo_unit_settlement(),
        # Mechanism verification calls fold/prefetch helpers rather than executing and
        # reading back a live production turn-driver settlement entrypoint.
        "production_settlement_executed": False,
        "production_settlement_readback_ok": False,
    }


def _has_todo_unit_settlement() -> bool:
    try:
        mod = importlib.import_module("loopx.control_plane.turn_driver.todo_unit_settlement")
    except ImportError:
        return False
    return hasattr(mod, "prepare_turn_semantic_unit_context")


def prepare_m3_fixture(
    root: Path,
    *,
    deadline_monotonic: float | None = None,
) -> PreparedMicrobenchFixture:
    """Create isolated M3 fixture: (a) blocked user_gate + (b) claim/settle lifecycle."""
    deadline = resolve_deadline(deadline_monotonic)
    if deadline is not None:
        require_remaining_budget(deadline, stage="prepare_m3_fixture")
    project = root / "project"
    runtime = root / "runtime"
    captures_dir = _mkdir_private(root / "captures")
    native_dir = _mkdir_private(root / "native")

    # Goal A: blocked on human decision (no runnable fallback).
    gate_proj = project / "gate_case"
    gate_registry = gate_proj / ".loopx" / "registry.json"
    gate_state_rel = f".codex/goals/{M3_GATE_GOAL_ID}/ACTIVE_GOAL_STATE.md"
    gate_state = gate_proj / gate_state_rel
    gate_state.parent.mkdir(parents=True, exist_ok=True)
    gate_state.write_text(
        "---\n"
        "status: active-read-only\n"
        "owner_mode: goal\n"
        'objective: "Respect explicit blocking user gate without bypassing or spending."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# M3a Blocked Gate Fixture\n\n"
        "## User Todo\n\n"
        "- [ ] [P0-user] Confirm external boundary before any delivery.\n"
        f"  <!-- loopx:todo todo_id={M3_GATE_TODO_ID} status=open "
        f"task_class=user_gate action_kind=approve_delivery blocks_agent={M3_AGENT_PRIMARY} "
        "decision_scope=direction:action:m3gate priority=P0-USER -->\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=gate_proj,
        runtime_root=runtime,
        registry_path=gate_registry,
        goal_id=M3_GATE_GOAL_ID,
        domain="microbench-m3-gate",
        adapter_kind="read_only_project_map_v0",
        adapter_status="connected-read-only",
        status="active-read-only",
        registered_agents=[M3_AGENT_PRIMARY],
    )
    with _bounded_canary_cli(deadline):
        import_fixture_authority(gate_registry, M3_GATE_GOAL_ID)

    # Goal B: unclaimed open deliverable with two registered agents.
    claim_proj = project / "claim_case"
    claim_registry = claim_proj / ".loopx" / "registry.json"
    claim_state_rel = f".codex/goals/{M3_CLAIM_GOAL_ID}/ACTIVE_GOAL_STATE.md"
    claim_state = claim_proj / claim_state_rel
    claim_state.parent.mkdir(parents=True, exist_ok=True)
    claim_state.write_text(
        "---\n"
        "status: active-read-only\n"
        "owner_mode: goal\n"
        'objective: "Claim, validate, settle once, and guard duplicate spend."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# M3b Claim/Settle Fixture\n\n"
        "## Agent Todo\n\n"
        f"- [ ] [P1] Read `{M3_ARTIFACT_REL}`, verify `{M3_ARTIFACT_TOKEN}`, and settle once.\n"
        f"  <!-- loopx:todo todo_id={M3_CLAIM_TODO_ID} status=open "
        "task_class=advancement_task action_kind=validate -->\n",
        encoding="utf-8",
    )
    artifact_path = claim_proj / M3_ARTIFACT_REL
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps({"artifact_id": M3_ARTIFACT_TOKEN, "verified": True}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=claim_proj,
        runtime_root=runtime,
        registry_path=claim_registry,
        goal_id=M3_CLAIM_GOAL_ID,
        domain="microbench-m3-claim",
        adapter_kind="read_only_project_map_v0",
        adapter_status="connected-read-only",
        status="active-read-only",
        registered_agents=[M3_AGENT_PRIMARY, M3_AGENT_PEER],
    )
    with _bounded_canary_cli(deadline):
        import_fixture_authority(claim_registry, M3_CLAIM_GOAL_ID)

    return PreparedMicrobenchFixture(
        scenario_id="m3_claim_block_settle_invariants",
        root=root,
        project_root=claim_proj,
        runtime_root=runtime,
        registry_path=claim_registry,
        captures_dir=captures_dir,
        native_dir=native_dir,
        artifact_rel_path=M3_ARTIFACT_REL,
        expected_token=M3_ARTIFACT_TOKEN,
        metadata={
            "gate_project": str(gate_proj),
            "gate_registry": str(gate_registry),
            "claim_project": str(claim_proj),
            "claim_registry": str(claim_registry),
        },
    )


def run_m3_deterministic_arm(
    fixture: PreparedMicrobenchFixture,
    *,
    arm: str = "head",
    source_root: Path | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Verify M3 basic claim, blocking gate, single settlement, and replay guards."""
    deadline = resolve_deadline(deadline_monotonic)
    if deadline is not None:
        require_remaining_budget(deadline, stage=f"m3_deterministic:{arm}")
    meta = dict(fixture.metadata or {})
    gate_registry = Path(str(meta["gate_registry"]))
    claim_registry = Path(str(meta["claim_registry"]))
    claim_proj = Path(str(meta["claim_project"]))
    runtime = fixture.runtime_root
    turn_m3a = f"turn-m3a-{arm}"
    turn_m3b = f"turn-m3b-{arm}"

    # M3a: Blocked gate must stop work and block premature spend.
    gate_rc, gate_guard = _run_cli_with_source(
        "quota",
        "should-run",
        "--codex-app",
        "--context-delivery",
        "on",
        "--goal-id",
        M3_GATE_GOAL_ID,
        "--agent-id",
        M3_AGENT_PRIMARY,
        "--turn-instance-id",
        turn_m3a,
        registry_path=gate_registry,
        runtime_root=runtime,
        source_root=source_root,
    )
    gate_guard, _ = _unwrap_context_capture_payload(gate_guard)
    gate_spend_rc, _gate_spend = _run_cli_with_source(
        "quota",
        "spend-slot",
        "--goal-id",
        M3_GATE_GOAL_ID,
        "--agent-id",
        M3_AGENT_PRIMARY,
        "--turn-instance-id",
        turn_m3a,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        registry_path=gate_registry,
        runtime_root=runtime,
        source_root=source_root,
    )
    gate_ic = _mapping(gate_guard.get("interaction_contract"))
    gate_rp = _mapping(gate_ic.get("response_plan"))
    gate_user = _mapping(gate_ic.get("user_channel"))
    gate_cli = _mapping(gate_ic.get("cli_channel"))

    # M3b: Exclusive claim -> quota should-run -> premature spend blocked -> C1 settle once -> replay idempotent.
    claim_rc, claim_res = _run_cli_with_source(
        "todo",
        "claim",
        "--goal-id",
        M3_CLAIM_GOAL_ID,
        "--todo-id",
        M3_CLAIM_TODO_ID,
        "--claimed-by",
        M3_AGENT_PRIMARY,
        "--agent-id",
        M3_AGENT_PRIMARY,
        "--claim-operation-id",
        f"op-claim-{arm}",
        registry_path=claim_registry,
        runtime_root=runtime,
        source_root=source_root,
    )
    peer_rc, _peer_res = _run_cli_with_source(
        "todo",
        "claim",
        "--goal-id",
        M3_CLAIM_GOAL_ID,
        "--todo-id",
        M3_CLAIM_TODO_ID,
        "--claimed-by",
        M3_AGENT_PEER,
        "--agent-id",
        M3_AGENT_PEER,
        "--claim-operation-id",
        f"op-peer-steal-{arm}",
        registry_path=claim_registry,
        runtime_root=runtime,
        source_root=source_root,
    )
    guard_rc, claim_guard = _run_cli_with_source(
        "quota",
        "should-run",
        "--codex-app",
        "--context-delivery",
        "on",
        "--goal-id",
        M3_CLAIM_GOAL_ID,
        "--agent-id",
        M3_AGENT_PRIMARY,
        "--todo-id",
        M3_CLAIM_TODO_ID,
        "--turn-instance-id",
        turn_m3b,
        registry_path=claim_registry,
        runtime_root=runtime,
        source_root=source_root,
    )
    claim_guard, _ = _unwrap_context_capture_payload(claim_guard)
    premature_rc, _ = _run_cli_with_source(
        "quota",
        "spend-slot",
        "--goal-id",
        M3_CLAIM_GOAL_ID,
        "--agent-id",
        M3_AGENT_PRIMARY,
        "--todo-id",
        M3_CLAIM_TODO_ID,
        "--turn-instance-id",
        turn_m3b,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        registry_path=claim_registry,
        runtime_root=runtime,
        source_root=source_root,
    )
    spend_before_settle = _spend_run_count(runtime, M3_CLAIM_GOAL_ID)

    settlement_plan = (
        claim_guard.get("interaction_contract", {})
        .get("cli_channel", {})
        .get("settlement_plan", {})
    )
    steps = settlement_plan.get("ordered_steps", []) if isinstance(settlement_plan, Mapping) else []
    writeback_tmpl = next(
        (
            str(step["command_template"])
            for step in steps
            if isinstance(step, Mapping) and step.get("kind") == "durable_writeback" and step.get("command_template")
        ),
        "",
    )
    settle_ok = False
    replay_idempotent_ok = False
    readback_unchanged_ok = False
    if writeback_tmpl:
        cmd_text = (
            writeback_tmpl.replace("<validated_progress>", "validated_progress")
            .replace("<scale>", "single_surface")
            .replace("<outcome>", "outcome_progress")
            + " --progress-result-class advanced --progress-evidence-id m3-proof-ok "
            "--delivery-boundary in_flight_continuation --no-global-sync --suppress-external-sinks"
        )
        tokens = shlex.split(cmd_text)
        idx = next(i for i, tok in enumerate(tokens) if tok in {"refresh-state", "quota"})
        settle_args = tokens[idx:]
        def _read_claim_todos() -> dict[str, Any]:
            if source_root is None:
                return list_goal_todos(
                    registry_path=claim_registry,
                    goal_id=M3_CLAIM_GOAL_ID,
                    runtime_root_arg=str(runtime),
                )
            rc, out = _run_cli_with_source(
                "todo",
                "list",
                "--goal-id",
                M3_CLAIM_GOAL_ID,
                registry_path=claim_registry,
                runtime_root=runtime,
                source_root=source_root,
            )
            assert rc == 0, out
            return out

        s1_rc, s1 = _run_cli_with_source(
            *settle_args,
            registry_path=claim_registry,
            runtime_root=runtime,
            source_root=source_root,
            cwd=claim_proj,
        )
        read_after_s1 = _read_claim_todos()
        s2_rc, s2 = _run_cli_with_source(
            *settle_args,
            registry_path=claim_registry,
            runtime_root=runtime,
            source_root=source_root,
            cwd=claim_proj,
        )
        read_after_s2 = _read_claim_todos()
        settle_ok = (
            s1_rc == 0
            and isinstance(s1.get("settlement_progress"), Mapping)
            and s1["settlement_progress"].get("state") == "settled"
            and _spend_run_count(runtime, M3_CLAIM_GOAL_ID) == 1
        )
        replay_idempotent_ok = (
            s2_rc == 0
            and s2.get("idempotent_replay") is True
            and _spend_run_count(runtime, M3_CLAIM_GOAL_ID) == 1
        )
        try:
            readback_assert: Any = assert_todo_read_unchanged
            readback_assert(read_after_s2, read_after_s1)
            readback_unchanged_ok = True
        except AssertionError:
            readback_unchanged_ok = False

    gate_payload_bytes = len(json.dumps(gate_guard, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    claim_payload_bytes = len(json.dumps(claim_guard, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    combined_text = json.dumps({"gate": gate_guard, "claim": claim_guard}, sort_keys=True, separators=(",", ":"))
    prompt_tokens, _ = measure_text_tokens(combined_text)

    checks = {
        "gate_blocks_execution_ask_user": (
            gate_rc == 0
            and gate_guard.get("should_run") is False
            and gate_user.get("action_required") is True
            and gate_rp.get("decision") == "ask_user"
            and gate_cli.get("spend_allowed_now") is False
        ),
        "gate_refuses_spend": gate_spend_rc != 0 and _spend_run_count(runtime, M3_GATE_GOAL_ID) == 0,
        "claim_grants_primary_owner": claim_rc == 0 and claim_res.get("claimed_by") == M3_AGENT_PRIMARY,
        "peer_claim_steal_rejected": peer_rc != 0,
        "claim_guard_allows_primary": guard_rc == 0 and claim_guard.get("should_run") is True,
        "premature_spend_refused": premature_rc != 0 and spend_before_settle == 0,
        "settled_once_and_spent_once": settle_ok,
        "replay_settle_idempotent_no_double_spend": replay_idempotent_ok,
        "todo_readback_unchanged_on_replay": readback_unchanged_ok,
    }
    violations = tuple(name for name, ok in checks.items() if not ok)
    return {
        "scenario_id": fixture.scenario_id,
        "arm": arm,
        "behavior_constraint": BehaviorConstraintResult(passed=not violations, checks=checks, violations=violations),
        "efficiency": EfficiencyMetrics(
            wire_bytes=gate_payload_bytes + claim_payload_bytes,
            prompt_tokens=prompt_tokens,
            tool_calls=6,
            round_trips=6,
        ),
        "gate_guard": gate_guard,
        "claim_guard": claim_guard,
    }
