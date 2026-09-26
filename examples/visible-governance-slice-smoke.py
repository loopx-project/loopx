#!/usr/bin/env python3
"""Smoke-test the ``visible_governance_slice_v0`` governance projection.

Builds a synthetic multi-agent fixture with soft claims, a task lease,
decision scopes, and a user gate, then projects the full governance slice
and validates every section.  All assertions are public-safe -- no raw
paths, credentials, or private payloads.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.canary_harness import (  # noqa: E402
    run_json_cli,
    write_fixture_registry,
)
from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.status import collect_status  # noqa: E402
from loopx.todos import (  # noqa: E402
    add_goal_todo,
    complete_goal_todo,
    list_goal_todos,
)
from loopx.visible_governance import (  # noqa: E402
    build_visible_governance_slice,
    render_visible_governance_markdown,
)


GOAL_ID = "governance-slice-smoke"
AGENT_ALPHA = "codex-alpha"
AGENT_BETA = "codex-beta"


def _assert_public_safe_payload(payload: dict, *, label: str) -> None:
    """Verify a payload contains no raw paths, URLs, or credential markers."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    assert "/Users/" not in text, f"{label}: absolute path leak"
    assert "https://" not in text, f"{label}: URL leak"
    assert "pass" + "word" not in text.lower(), f"{label}: credential leak"
    assert "api_" + "key" not in text.lower(), f"{label}: API key leak"
    assert "C:\\" not in text, f"{label}: Windows path leak"


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a multi-agent fixture with claims, gates, and a task lease."""
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    registry = project / ".loopx" / "registry.json"
    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(
        "---\nstatus: active\n---\n\n"
        "# Active Goal State\n\n"
        "## Agent Todo\n\n"
        "- [ ] Alpha advancement task\n"
        "- [ ] Beta advancement task\n\n"
        "## User Todo\n\n"
        "- [ ] Review and approve the release scope\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=GOAL_ID,
        domain="loopx-platform",
        adapter_kind="harness_self_improvement",
        registered_agents=[AGENT_ALPHA, AGENT_BETA],
        quota_allowed_slots=10,
    )
    return registry, runtime, state


def _add_agent_todo(
    registry: Path,
    *,
    text: str,
    claimed_by: str,
    task_class: str = "advancement_task",
    required_decision_scopes: list[str] | None = None,
) -> dict:
    """Add an agent todo, optionally with required decision scopes.

    Scopes use the ``kind:granularity:scope_key`` string format
    (e.g. ``"release:action:governance-bridge"``).
    """
    kwargs: dict = {
        "registry_path": registry,
        "goal_id": GOAL_ID,
        "role": "agent",
        "text": text,
        "task_class": task_class,
        "claimed_by": claimed_by,
    }
    if required_decision_scopes:
        kwargs["required_decision_scopes"] = required_decision_scopes
    return add_goal_todo(**kwargs)


def _add_user_gate(
    registry: Path,
    *,
    text: str,
    blocks_agent: str | None = None,
    unblocks_todo_id: str | None = None,
    decision_scope: str | None = None,
) -> dict:
    """Add a user gate, optionally linked to an agent todo or scope.

    ``decision_scope`` uses the ``kind:granularity:scope_key`` string format.
    """
    kwargs: dict = {
        "registry_path": registry,
        "goal_id": GOAL_ID,
        "role": "user",
        "text": text,
        "task_class": "user_gate",
    }
    if blocks_agent:
        kwargs["blocks_agent"] = blocks_agent
    if unblocks_todo_id:
        kwargs["unblocks_todo_id"] = unblocks_todo_id
    if decision_scope:
        kwargs["decision_scope"] = decision_scope
    return add_goal_todo(**kwargs)


def _add_user_action(
    registry: Path,
    *,
    text: str,
    bound_agent: str | None = None,
) -> dict:
    """Add a non-blocking user action."""
    kwargs: dict = {
        "registry_path": registry,
        "goal_id": GOAL_ID,
        "role": "user",
        "text": text,
        "task_class": "user_action",
    }
    if bound_agent:
        kwargs["bound_agent"] = bound_agent
    return add_goal_todo(**kwargs)


def main() -> None:
    import tempfile

    print("=== Visible Governance Slice Smoke ===\n")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        registry, runtime, state = _write_fixture(tmp_path)

        # ── 1. Create agent todos with claims ──
        alpha_todo = _add_agent_todo(
            registry,
            text="Alpha builds the core governance bridge.",
            claimed_by=AGENT_ALPHA,
            required_decision_scopes=["direction:action:governance-bridge"],
        )
        beta_todo = _add_agent_todo(
            registry,
            text="Beta reviews the integration surface.",
            claimed_by=AGENT_BETA,
        )
        print(
            f"  [OK] Agent todos: alpha={alpha_todo['todo_id']}, "
            f"beta={beta_todo['todo_id']}"
        )

        # ── 2. Create a user gate that covers alpha's required scope ──
        gate = _add_user_gate(
            registry,
            text="Approve the governance bridge release scope.",
            blocks_agent=AGENT_ALPHA,
            decision_scope="direction:goal:*",
        )
        print(f"  [OK] User gate: {gate['todo_id']} (goal-scoped, blocks alpha)")

        # ── 3. Create a non-blocking user action ──
        action = _add_user_action(
            registry,
            text="Review the dashboard budget projection.",
            bound_agent=AGENT_ALPHA,
        )
        print(f"  [OK] User action: {action['todo_id']}")

        # ── 4. Acquire a task lease for alpha ──
        lease_result = run_json_cli(
            "task-lease",
            "acquire",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            alpha_todo["todo_id"],
            "--owner",
            AGENT_ALPHA,
            "--idempotency-key",
            "smoke-lease-key-001",
            "--ttl-seconds",
            "2700",
            "--write-scope",
            "governance-bridge/*",
            registry_path=registry,
            runtime_root=runtime,
        )
        assert lease_result["ok"] is True, f"lease acquire failed: {lease_result}"
        assert lease_result["acquired"] is True
        print(f"  [OK] Task lease acquired for alpha on {alpha_todo['todo_id']}")

        # ── 5. Collect status ──
        status_payload = collect_status(
            registry_path=registry,
            runtime_root_override=str(runtime),
            scan_roots=[Path(__file__).resolve()],
            limit=20,
        )
        assert isinstance(status_payload, dict)
        print("  [OK] Status collected")

        # ── 6. Build quota should_run for alpha ──
        decision = build_quota_should_run(
            status_payload,
            goal_id=GOAL_ID,
            agent_id=AGENT_ALPHA,
        )
        assert isinstance(decision, dict)
        assert "effective_action" in decision
        print(f"  [OK] Quota should_run built: effective={decision['effective_action']}")

        # ── 7. Build visible governance slice ──
        gov_slice = build_visible_governance_slice(
            status_payload,
            goal_id=GOAL_ID,
            agent_id=AGENT_ALPHA,
            include_task_leases=True,
        )
        assert gov_slice["ok"] is True, f"slice not ok: {gov_slice}"
        assert gov_slice["schema_version"] == "visible_governance_slice_v0"
        assert gov_slice["goal_id"] == GOAL_ID
        assert gov_slice["agent_id"] == AGENT_ALPHA

        # ── 8. Validate every section ──

        # claims
        claims = gov_slice["claims"]
        assert isinstance(claims, dict)
        assert claims["total_claims"] >= 2, f"expected >= 2 claims, got {claims}"
        assert claims["claiming_agent_count"] >= 2
        assert claims["current_agent_claims"] >= 1
        assert AGENT_ALPHA in claims["by_agent"]
        assert AGENT_BETA in claims["by_agent"]
        for c in claims["claims"]:
            assert "todo_id" in c
            assert "claimed_by" in c
        print(
            f"  [OK] Claims: {claims['total_claims']} claims "
            f"across {claims['claiming_agent_count']} agents"
        )

        # leases
        leases = gov_slice["leases"]
        assert isinstance(leases, dict)
        assert leases["active_leases"] >= 1
        assert leases["current_agent_active_leases"] >= 1
        assert leases["nature"] == "optional runtime fence, not authority"
        for lease_entry in leases["leases"]:
            assert "todo_id" in lease_entry
            assert "owner" in lease_entry
            assert "expires_at" in lease_entry
        print(
            f"  [OK] Leases: {leases['active_leases']} active, "
            f"nature='{leases['nature']}'"
        )

        # quota
        quota = gov_slice["quota"]
        assert isinstance(quota, dict)
        for key in ("should_run", "effective_action", "state"):
            assert key in quota, f"quota missing {key}"
        print(f"  [OK] Quota: should_run={quota['should_run']}, action={quota['effective_action']}")

        # scheduler hints
        sched = gov_slice["scheduler_hints"]
        if sched is not None:
            assert isinstance(sched, dict)
            for key in ("action", "cadence_class", "arbitration"):
                assert key in sched, f"scheduler_hints missing {key}"
            print(f"  [OK] Scheduler hints: cadence={sched['cadence_class']}")
        else:
            print("  [OK] Scheduler hints: None (no agent-lane context)")

        # decision scopes
        scopes = gov_slice["decision_scopes"]
        assert isinstance(scopes, dict)
        consistency = scopes["required_scope_consistency"]
        assert isinstance(consistency, dict)
        assert "status" in consistency
        print(
            f"  [OK] Decision scopes: consistency={consistency['status']}, "
            f"checked={consistency['checked_agent_todo_count']} todos"
        )

        # authority boundary (implementation availability, not per-Goal promotion)
        boundary = gov_slice["authority_boundary"]
        assert isinstance(boundary, list)
        assert len(boundary) >= 5, f"expected >= 5 RFC entries, got {len(boundary)}"
        expected_shipped = {
            "3 -- State Classification": True,
            "4 -- Coordination Ledger Shape": False,
            "5.1 -- claim_work command": False,
            "7 -- Receipt Retention": True,
            "8 -- Local vs Shared Mode": True,
            "Appendix B -- handoff_mode": True,
            "9 -- Offline/Local Boundaries": False,
        }
        by_section = {entry["rfc_section"]: entry for entry in boundary}
        for section, expect_shipped in expected_shipped.items():
            assert section in by_section, f"missing RFC section: {section}"
            entry = by_section[section]
            assert "proposal" in entry
            assert "shipped_equivalent" in entry
            assert "gap" in entry
            assert entry["shipped"] is expect_shipped, (
                f"{section}: shipped={entry['shipped']!r}, "
                f"expected {expect_shipped} after native prototype retirement"
            )
        claim_work = by_section["5.1 -- claim_work command"]
        claim_text = (
            f"{claim_work['shipped_equivalent']} {claim_work['gap']}".lower()
        )
        assert "retired" in claim_text and "todo_claim.ts" in claim_text
        assert "not current execution authority" in claim_text
        shipped_count = sum(1 for e in boundary if e["shipped"])
        assert shipped_count == sum(
            1 for value in expected_shipped.values() if value
        ), f"unexpected shipped count: {shipped_count}"
        print(
            f"  [OK] Authority boundary: {len(boundary)} RFC sections, "
            f"{shipped_count} implemented, "
            f"{len(boundary) - shipped_count} original proposals not shipped"
        )

        # ── Negative: active lease is never write authority ──
        assert leases["active_leases"] >= 1
        assert leases["nature"] == "optional runtime fence, not authority"
        lease_blob = json.dumps(leases, sort_keys=True).lower()
        for forbidden in (
            "write authority",
            "runtime authority",
            "browser write",
            "write api",
        ):
            assert forbidden not in lease_blob, (
                f"leases projection must not present '{forbidden}'"
            )
        # The coverage-only claim_work reference must not imply lease authority.
        assert claim_work["shipped"] is False
        assert "optional runtime fence" in leases["nature"]
        print(
            "  [OK] Negative: active leases remain fence-only "
            "(not write authority / no browser write API)"
        )

        # ── 9. Public safety scan ──
        _assert_public_safe_payload(gov_slice, label="governance-slice")
        print("  [OK] Public safety scan passes")

        # ── 10. include_task_leases=False ──
        no_lease_slice = build_visible_governance_slice(
            status_payload,
            goal_id=GOAL_ID,
            agent_id=AGENT_ALPHA,
            include_task_leases=False,
        )
        assert no_lease_slice["leases"] is None
        print("  [OK] include_task_leases=False omits leases section")

        # ── 11. Missing goal_id ──
        bad_slice = build_visible_governance_slice(status_payload, goal_id="")
        assert bad_slice["ok"] is False
        assert "error" in bad_slice
        print("  [OK] Empty goal_id returns error")

        # ── 12. agent_id=None works ──
        no_agent_slice = build_visible_governance_slice(
            status_payload,
            goal_id=GOAL_ID,
            agent_id=None,
        )
        assert no_agent_slice["ok"] is True
        assert no_agent_slice["agent_id"] is None
        print("  [OK] agent_id=None produces valid slice")

        # ── 13. Markdown rendering ──
        markdown = render_visible_governance_markdown(gov_slice)
        assert "Visible Governance Slice" in markdown
        assert GOAL_ID in markdown
        assert AGENT_ALPHA in markdown
        assert "Claims (Soft Ownership)" in markdown
        assert "Task Leases (Optional Runtime Fence)" in markdown
        assert "Quota Decision" in markdown
        assert "Scheduler Hints" in markdown
        assert "Decision Scopes" in markdown
        assert "Authority Boundary" in markdown
        assert "optional runtime fence, not authority" in markdown
        assert "Proposals shipped" in markdown
        assert "Stage-2" in markdown or "Stage 2" in markdown or "additive" in markdown
        assert "not write authority" in markdown.lower() or (
            "optional fence, not write authority" in markdown.lower()
        )
        # Verify no private markers in rendered markdown
        assert "/Users/" not in markdown
        assert "https://" not in markdown
        assert "C:\\" not in markdown
        print("  [OK] Markdown render includes all sections")
        print("  [OK] Markdown is public-safe")
        print("  [OK] Markdown keeps Stage-2 / lease-authority boundary explicit")

        # ── 14. Complete the gate and verify standing authority ──
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=gate["todo_id"],
            agent_id=AGENT_ALPHA,
            decision_outcome="approve",
        )
        status2 = collect_status(
            registry_path=registry,
            runtime_root_override=str(runtime),
            scan_roots=[Path(__file__).resolve()],
            limit=20,
        )
        gov_slice2 = build_visible_governance_slice(
            status2,
            goal_id=GOAL_ID,
            agent_id=AGENT_ALPHA,
        )
        standing = gov_slice2["decision_scopes"]["standing_decision_authority"]
        if standing is not None:
            assert standing["active_count"] >= 1
            print(
                f"  [OK] Standing authority after gate approval: "
                f"{standing['active_count']} active receipts"
            )
        else:
            print("  [OK] Standing authority: None (gate scoped to different items)")

        # ── 15. Verify the slice is read-only (no mutations in builder) ──
        todos_before = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)
        _recheck = build_visible_governance_slice(
            status2,
            goal_id=GOAL_ID,
            agent_id=AGENT_BETA,
            include_task_leases=True,
        )
        todos_after = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)
        assert todos_before == todos_after, (
            "build_visible_governance_slice must not mutate todos"
        )
        print("  [OK] Slice builder is read-only (no mutations)")

    print("\n=== All smoke checks passed ===")


if __name__ == "__main__":
    main()
