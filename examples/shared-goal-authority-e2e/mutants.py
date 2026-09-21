"""Deliberate regressions for bounded shadow qualification, in isolated copies only.

Each case first requires its unchanged oracle to pass. A mutation counts as
killed only when that same test reports an assertion failure, never an import,
syntax, process timeout, or setup failure. No live goal or checkout is edited.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

COORDINATION = "loopx/control_plane/coordination/"


def replacement(before: str, after: str) -> Callable[[str], str]:
    def apply(source: str) -> str:
        if source.count(before) != 1:
            raise ValueError("mutation locator drift; review source and oracle")
        return source.replace(before, after)
    return apply


@dataclass(frozen=True)
class Case:
    name: str
    edits: tuple[tuple[str, Callable[[str], str]], ...]
    test: str
    pattern: str | None = None

    def command(self) -> list[str]:
        if self.pattern is None:
            return [sys.executable, "-m", "pytest", "-q", "--tb=short", self.test]
        return ["node", "--no-warnings", "--experimental-strip-types", "--test",
                "--test-name-pattern=" + self.pattern, self.test]


CASES = [
    Case("decision_scope_role_guard_removed", (("loopx/control_plane/todos/decision_metadata.ts", replacement(
        '    if (intent.decision_scope !== null && (role !== "user" || taskClass !== "user_gate")) {',
        '    if (false) {')),),
         "tests/control_plane_ts/decision_metadata.test.ts",
         "user gates own decision_scope and agent work owns required_decision_scopes"),
    Case("decision_scope_dedup_removed", (("loopx/control_plane/todos/decision_metadata.ts", replacement(
        "    if (!seen.has(key)) {", "    if (true) {")),),
         "tests/control_plane_ts/decision_metadata.test.ts",
         "decision metadata normalizes compact and object forms without duplicate scopes"),
    Case('todo_global_gate_inferred', (('loopx/control_plane/todos/authoring_scope.ts', replacement(
        '      : todo.global_gate as boolean | null ?? null;',
        '      : (todo.global_gate || intent.goal_bound) as boolean | null ?? null;')),),
         'tests/control_plane_ts/todo_authoring_scope.test.ts', 'global blocking is never inferred'),
    Case('todo_explicit_scope_overwritten', (('loopx/control_plane/todos/authoring_scope.ts', replacement(
        'if (requestedBound) fail(', 'if (false) fail(')),),
         'tests/control_plane_ts/todo_authoring_scope.test.ts', 'explicit continuation and gate'),
    Case('todo_successor_scope_unbound', (('loopx/control_plane/todos/authoring_scope.ts', replacement(
        'if (blocks && (goal || !bound || bound !== blocks)) return "agent_binding_conflict";', '')),),
         'tests/control_plane_ts/todo_authoring_scope.test.ts', 'resolved successor scope'),
    Case('monitor_route_drops_invalid_capability', (('loopx/control_plane/todos/work_requirements.ts', replacement(
        '      throw new EffectRuntimeRequestError(`${label} must contain public-safe capability tokens; invalid entries cannot be dropped`);',
        '      continue;')),),
         'tests/control_plane_ts/monitor_successor.test.ts', 'invalid successor intent is rejected'),
    Case('monitor_route_material_guard_removed', (('loopx/control_plane/scheduler/monitor_successor.ts', replacement(
        'if ((agentTodo || userTodo) && !material)', 'if (false)')),),
         'tests/control_plane_ts/monitor_successor.test.ts', 'invalid successor intent is rejected'),
    Case('monitor_route_rewrites_fingerprint', (('loopx/control_plane/quota/monitor_poll_commit.ts', replacement(
        '  monitorSuccessorIntent(result);', '  Object.assign(result, monitorSuccessorIntent(result));')),),
         'tests/control_plane_ts/quota_monitor_poll_commit.test.ts', 'preserves the legacy pending observation fingerprint'),
    Case('governance_exclusion_ignored', (('loopx/control_plane/goals/shared_goal_work.ts', replacement(
        'else if (!excluded)', 'else if (true)')),),
         'tests/control_plane_ts/shared_goal_work.test.ts', 'alignment selection respects exclusions'),
    Case('canonical_zero_basis_stale_admitted', (('loopx/control_plane/goals/goal_amendment_proposal.ts', replacement(
        'if (proposal.base_source_basis_digest !== derived.source_basis_digest) facts.push("base_source_basis_digest_mismatch");',
        'if (false) facts.push("base_source_basis_digest_mismatch");')),),
         'tests/control_plane_ts/goal_amendment_proposal.test.ts', 'canonical Todo bases cannot'),
    Case('governance_reads_legacy_after_promotion', (('loopx/control_plane/goals/shared_goal_work_source.py', replacement(
        'if canonical is None:', 'if True:')),), 'tests/control_plane/test_canonical_goal_governance.py::test_empty_canonical_is_authoritative_and_missing_display_is_not_repaired'),
    Case('delivery_wait_target_unbound', (('loopx/control_plane/todos/resume_condition.ts', replacement(
        'condition.target_todo_id !== spec.target || ', '')),),
         'tests/control_plane_ts/delivery_response.test.ts', 'exact dependency identity'),
    Case('delivery_wait_unknown_class', (('loopx/control_plane/todos/resume_condition.ts', replacement(
        '["advancement_task", "user_gate", "user_action", "blocker"].includes(String(condition.target_task_class))',
        'true')),),
         'tests/control_plane_ts/delivery_response.test.ts', 'exact dependency identity'),
    Case('rollout_cwd_root', (('loopx/cli_rollout.py', replacement(
        'resolve_runtime_root(registry, runtime_root_arg, registry_path=registry_path)',
        'resolve_runtime_root(registry, runtime_root_arg)')),),
         'tests/control_plane/test_shadow_observable_e2e.py::test_registry_relative_root_does_not_depend_on_callers_cwd[disabled]'),
    Case('native_note_dropped', ((COORDINATION + 'todo_update_intent.ts', replacement(
        '  const next: JsonObject = {...todo, ...input.patch};',
        '  const next: JsonObject = {...todo, ...input.patch};\n  if ("note" in input.patch) next.note = todo.note;')),),
         'tests/control_plane/test_shadow_observable_native_e2e.py::test_native_unclaimed_edit_and_explicit_note_clear[disabled]'),
    Case('native_unclaimed_edit_rejected', ((COORDINATION + 'todo_lifecycle_decision.ts', replacement(
        '  if (todo.claimed_by !== null && todo.claimed_by !== actor) return "claim_owner_mismatch";',
        '  if (todo.claimed_by === null || todo.claimed_by !== actor) return "claim_owner_mismatch";')),),
         'tests/control_plane/test_shadow_observable_native_e2e.py::test_native_unclaimed_edit_and_explicit_note_clear[disabled]'),
    Case('native_diagnostic_truncated', ((COORDINATION + 'todo_update_admission.ts', replacement(
        '? "Todo update cannot edit another claim owner\'s work"',
        '? "Update rejected"')),),
         'tests/control_plane/test_shadow_observable_native_e2e.py::test_canonical_argument_intent_and_atomic_claim[disabled]'),
    Case('cursor_baseline_digest', ((COORDINATION + 'local_authority_shadow_adapter.py', replacement(
        '        return None if marker is None else marker["partition_digest"]',
        '''        head = transaction["projection"]
        return partition_digest({"handoff_mode": head["handoff_mode"], "todos": head["todos"]}
            if self._partition == TODO_PARTITION else {"leases": head["leases"]})''')),),
         'tests/control_plane/test_shadow_cursor_recovery_e2e.py::test_abandoned_cursor_survives_all_consumers[2-0-todos]'),
    Case('qualification_baseline_digest', ((COORDINATION + 'runtime_shadow.ts', replacement(
        'const digest = marker === null ? null : (marker as JsonObject).partition_digest;',
        'const digest = marker === null ? localAuthorityShadowHeadDigest(anchor.projection) : (marker as JsonObject).partition_digest;')),),
         'tests/control_plane/test_shadow_cursor_recovery_e2e.py::test_abandoned_cursor_survives_all_consumers[2-0-leases]'),
    Case('cursor_digest_unchecked', (
        (COORDINATION + 'runtime_shadow.ts', replacement(
            'if (digest !== cursor.last_partition_digest) throw new ShadowLineageError("outbox_cursor_unproved");',
            '// DELIBERATE MUTANT: accept any syntactically valid cursor digest.')),
        (COORDINATION + 'local_authority_shadow_adapter.py', replacement(
            '                or self._cursor_digest(anchor) != cursor["last_partition_digest"]\n', ''))),
         'tests/control_plane/test_shadow_cursor_recovery_e2e.py::test_forged_applied_digest_holds_every_consumer_without_rewriting_bytes[True-todos]'),
    Case('lineage', ((COORDINATION + 'local_authority_shadow.ts', replacement('  requireLineage(entry.capture_lineage_id === binding.capture_lineage_id, "stale_generation");', '  // DELIBERATE MUTANT: omit active lineage validation.')),),
         'tests/control_plane_ts/local_authority_shadow_outbox.test.ts', 'self-consistent foreign'),
    Case('previous_partition', ((COORDINATION + 'local_authority_shadow.ts', replacement('  requireLineage(request.entry.source.previous_partition_digest === digest, "source_partition_continuity_unproved");', '  // DELIBERATE MUTANT: omit previous partition proof.')),),
         'tests/control_plane_ts/local_authority_shadow_outbox.test.ts', 'missing primary mutation'),
    Case('qualification_history', ((COORDINATION + 'runtime_shadow.ts', replacement('    const lineage = await loadValidatedShadowLineage(store, request.runtime_root, request.goal_id, binding);', '    const page = await store.scanCommitted(null, 10000);\n    const lineage = { head: await store.loadAuthority(), transactions: page.transactions,\n      last_sequences: {}, last_applied_sequences: {}, write_classes: ["todo_add"] };')),),
         'tests/control_plane_ts/coordination_runtime_shadow.test.ts', 'observation transaction mixed'),
    Case('management_request_digest', ((COORDINATION + "shadow_management.ts", replacement('if (state.operation.request_digest !== digest) throw new ShadowManagementError("management_operation_identity_mismatch");', 'if (false) throw new ShadowManagementError("management_operation_identity_mismatch");')),),
         "tests/control_plane_ts/shadow_management.test.ts", 'management request digest'),
    Case('management_manifest_hash', ((COORDINATION + "shadow_management.ts", replacement('managementDigest(manifest) !== state.operation.manifest_digest\n      || manifest.schema_version !== SHADOW_MANAGEMENT_MANIFEST_SCHEMA\n', 'false\n      || manifest.schema_version !== SHADOW_MANAGEMENT_MANIFEST_SCHEMA\n')),),
         "tests/control_plane_ts/shadow_management.test.ts", 'management manifest hash'),
    Case('management_phase', ((COORDINATION + "shadow_management.ts", replacement('operation.kind !== kind || !phases.includes(String(operation.phase))', 'operation.kind !== kind')),),
         "tests/control_plane_ts/shadow_management.test.ts", 'management phase validation'),
    Case('management_goal_binding', ((COORDINATION + "shadow_management.ts", replacement(' || value.goal_id !== goal\n      || ', '\n      || ')),),
         "tests/control_plane_ts/shadow_management.test.ts", 'management goal binding'),
    Case('management_candidate_lineage', ((COORDINATION + "shadow_management.ts", replacement('candidate.capture_lineage_id !== expectedLineage', 'false')),),
         "tests/control_plane_ts/shadow_management.test.ts", 'rollback refuses a different valid candidate lineage'),
]

PREPARED_WRITE = '''            durable_write_json(
                self._directory / entry_file_name(seq, entry_id, "prepared"),
                record,
            )'''
CASES.extend([
    Case("receipt_bytes", ((COORDINATION + "local_authority_shadow_adapter.py", replacement(
        "            expected = receipt.get(key)",
        "            expected = outbox.raw_bytes_digest(path.read_bytes())")),),
        "tests/control_plane/test_shadow_drain_adversarial.py::test_raw_residue_mismatch_preserves_every_file_before_any_cleanup"),
    Case("cursor_regression", ((COORDINATION + "local_authority_shadow_adapter.py", replacement(
        "last_seq=len(history),", "last_seq=1,")),),
        "tests/control_plane/test_shadow_drain_e2e.py::test_public_primary_maps_one_to_one_to_receipts_and_replays_idempotently"),
    Case("early_committed", ((COORDINATION + "local_authority_shadow_outbox.py", replacement(
        PREPARED_WRITE, PREPARED_WRITE + '''
            durable_write_json(
                self._directory / entry_file_name(seq, entry_id, "committed"),
                {"schema_version": OUTBOX_COMMIT_SCHEMA, "entry_id": entry_id,
                 "capture_lineage_id": self._lineage_id, "committed_at": utc_now_text()},
            )''')),),
        "tests/control_plane/test_shadow_drain_e2e.py::test_primary_sigkill_preserves_complete_bytes_and_proves_before_marker[before_replace]"),
])

# Ladder parity-half rows (s2c2.*) drive the same lifecycle through the public
# CLI; these mutants remove one operator-visible truth each and must turn the
# corresponding row red.
LADDER_ROW = "tests/control_plane/test_shared_goal_authority_e2e.py::test_ladder_row_passes_or_is_declared_unverified"
CASES.extend([
    Case("status_hides_prepared_only", ((COORDINATION + "local_authority_shadow_outbox.py", replacement(
        '            "committed_pending": sum(1 for entry in entries if entry.is_committed),\n'
        '            "prepared_only": sum(1 for entry in entries if not entry.is_committed),',
        '            "committed_pending": len(entries),\n'
        '            "prepared_only": 0,')),),
        LADDER_ROW + "[s2c2.sigkill_between_primary_write_and_drain]"),
    Case("qualification_ignores_drift", ((COORDINATION + "runtime_shadow.ts", replacement(
        "const matched = localAuthorityShadowHeadDigest(request.projection) === localAuthorityShadowHeadDigest(lineage.head.head);",
        "const matched = true;")),),
        LADDER_ROW + "[s2c2.parity_divergent_detects_foreign_edit]"),
    Case("replay_counted_as_delivery", ((COORDINATION + "local_authority_shadow_adapter.py", replacement(
        '                    self._result.replayed += 1\n                    self._result.no_op += int(receipt["no_op"])',
        '                    self._result.delivered += 1\n                    self._result.no_op += int(receipt["no_op"])')),),
        LADDER_ROW + "[s2c2.sigkill_mid_drain]"),
])


def remove_fence(source: str) -> str:
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "require_legacy_coordination_write_allowed")
    lines = source.splitlines(keepends=True)
    return "".join(lines[:function.body[0].lineno - 1]) + "    return\n" + "".join(lines[function.end_lineno:])


def typescript_exported_function_span(source: str, name: str) -> tuple[int, int]:
    """Bound one top-level exported function without depending on its successor."""

    signature = f"export async function {name}("
    start = source.index(signature)
    next_export = re.search(r"(?m)^export (?:async )?function ", source[start + len(signature):])
    end = len(source) if next_export is None else start + len(signature) + next_export.start()
    return start, end


def remove_native_update_maintenance(source: str) -> str:
    start, end = typescript_exported_function_span(
        source, "updateLocalCoordinationTodo"
    )
    function = source[start:end]
    function = replacement(
        "return await withCanonicalWriter(root, goalId, input.dry_run === true, async () => {",
        "return await (async () => {",
    )(function)
    function = replacement("    });\n  } catch (error) {", "    })();\n  } catch (error) {")(function)
    return source[:start] + function + source[end:]


def move_guard_outside_lock(name: str) -> Callable[[str], str]:
    def apply(source: str) -> str:
        function = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "legacy_todo_write_transaction")
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Expr)
                 and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                 and node.value.func.id == name]
        if len(calls) != 1:
            raise ValueError("guard locator drift; review lock protocol")
        call = calls[0]
        lines = source.splitlines(keepends=True)
        segment = "".join(lines[call.lineno - 1:call.end_lineno])
        text = "".join(lines[:call.lineno - 1] + lines[call.end_lineno:])
        start = text.index("    with exclusive_cross_runtime_file_lock(\n",
                           text.index("def legacy_todo_write_transaction("))
        # Preserve the exact arguments while moving only the actual guard.
        unindented = "".join(line[4:] for line in segment.splitlines(keepends=True))
        return text[:start] + "    if not dry_run:\n" + unindented + text[start:]
    return apply


WRITER_TEST = "tests/control_plane/test_shadow_writer_boundaries.py::"
FENCE_TEST = WRITER_TEST + "test_cli_waiting_for_todo_mutex_rechecks_fence_after_engagement"
CASES.extend([
    Case("management_result_binding", ((COORDINATION + "shadow_management.ts", replacement(
        "  await validateReplayResult(request, manifest, prior.result, state);",
        "  // DELIBERATE MUTANT: trust a cached result from another operation.")),),
         "tests/control_plane/test_shadow_management_variant_e2e.py::test_rollback_result_cannot_borrow_another_goals_archive_evidence[historical]"),
    Case("cross_goal_source_guard", ((COORDINATION + "legacy_writer_fence.py", replacement(
        "    resolved_source = state_file.resolve(strict=False)",
        "    return  # DELIBERATE MUTANT: allow another goal to bypass source authority.\n    resolved_source = state_file.resolve(strict=False)")),),
         "tests/control_plane/test_shadow_writer_variant_e2e.py::test_other_goal_cannot_write_a_protected_goal_source_via_state_override[active_capture]"),
    Case("cleanup_hides_verified_commit", ((COORDINATION + "local_authority_shadow_adapter.py", replacement(
        "                self._record_view(view)\n                self._reconcile(transactions, delivered_entry_id=entry.entry_id)",
        "                self._reconcile(transactions, delivered_entry_id=entry.entry_id)\n                self._record_view(view)")),),
         "tests/control_plane/test_shadow_drain_adversarial.py::test_cleanup_permission_failure_reports_verified_commit_and_recovers[before_commit]"),
    Case("native_update_maintenance", ((COORDINATION + "local_authority_runtime.ts",
         remove_native_update_maintenance),),
         "tests/control_plane/test_shadow_native_todo_update_e2e.py::test_native_update_holds_before_primary_for_management[native-bootstrapping-cli]"),
    Case("archive_oldest_selection", ((COORDINATION + "todo_archive_selection.ts", replacement(
        "movable.slice(0, moveCount)", "movable.slice(-moveCount)")),),
         "tests/control_plane_ts/todo_archive_selection.test.ts",
         "archive selection preserves imported order"),
    Case("remove_fence", ((COORDINATION + "legacy_writer_fence.py", remove_fence),), FENCE_TEST),
    Case("fence_outside_lock", ((COORDINATION + "legacy_writer_fence.py",
         move_guard_outside_lock("require_legacy_coordination_write_allowed")),), FENCE_TEST),
    Case("source_binding_outside_lock", ((COORDINATION + "legacy_writer_fence.py",
         move_guard_outside_lock("require_registry_source_write_allowed")),),
         WRITER_TEST + "test_waiting_override_writer_rechecks_registry_binding_inside_shared_state_lock"),
    Case("remove_refresh_cas", (("loopx/state_refresh.py", replacement(
        "if current_state_text != expected_write_state_text:",
        "if False:  # DELIBERATE MUTANT: bypass stale-state rejection.")),),
         WRITER_TEST + "test_concurrent_public_refresh_preserves_the_newer_owned_paragraph"),
    Case("fence_unshared_state_lock", ((COORDINATION + "legacy_writer_fence.ts", replacement(
        "withFileMutationLock(statePath, () =>",
        'withFileMutationLock(statePath + ".mutant-unshared", () =>')),),
         WRITER_TEST + "test_real_writer_commits_before_a_later_fence_is_published[True]"),
    Case("remove_bound_state_path", ((COORDINATION + "legacy_writer_fence.py", replacement(
        "if bound_source.resolve(strict=False) != state_file.resolve(strict=False):",
        "if False:")),),
         "tests/control_plane/test_shadow_drain_adversarial.py::test_state_file_override_cannot_attribute_an_unbound_source_to_active_lineage"),
    Case("bootstrap_unregistered_root", ((COORDINATION + "runtime_shadow.ts", replacement(
        "if (registeredRoot !== request.runtime_root)",
        "if (false && registeredRoot !== request.runtime_root)")),),
         "tests/control_plane/test_runtime_shadow_bounded_e2e.py::test_controller_cannot_bind_the_registered_source_to_an_override_runtime_root"),
    Case("bootstrap_unregistered_state", ((COORDINATION + "runtime_shadow.ts", replacement(
        "if (resolve(String(snapshot.state_path)) !== resolve(String(snapshot.registered_state_path)))",
        "if (false && resolve(String(snapshot.state_path)) !== resolve(String(snapshot.registered_state_path)))")),),
         "tests/control_plane/test_runtime_shadow_bounded_e2e.py::test_controller_cannot_bind_an_alternate_state_file_before_or_after_bootstrap"),
    Case("locale_dependent_lease_order", ((COORDINATION + "runtime_shadow.ts", replacement(
        '''.sort((left, right) => {
    if (left < right) return -1;
    if (left > right) return 1;
    return 0;
  });''', ".sort((left, right) => left.localeCompare(right));")),),
         "tests/control_plane/test_runtime_shadow_bounded_e2e.py::test_source_snapshot_preserves_inventory_without_projecting_orphan_leases"),
])

# Restore both halves of the obsolete mirror: the public CLI hook and an actual
# second FileAuthorityStore commit. A fabricated RPC result would prove nothing.
MIRROR_HOOK = '''    if payload.get("ok") and payload.get("added") and not payload.get("dry_run"):
        from ..control_plane.effect_runtime import effect_runtime_result as restored_mirror
        restored_mirror("coordination.runtime_shadow.commit", {
            "runtime_root": str(resolve_runtime_root(load_registry(registry_path), runtime_root_arg)),
            "goal_id": args.goal_id, "operation_id": "restored-snapshot:" + payload["todo_id"],
        })
'''
MIRROR_COMMIT = '''  const request = requireJsonObject(_value, "restored snapshot request");
  const root = String(request.runtime_root); const goal = String(request.goal_id);
  return await withShadowMaintenanceLock(root, goal, async () => {
    const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), goal, { existingOnly: true });
    const head = await store.loadAuthority();
    if (head.status !== "loaded") throw new Error("restored mirror needs a real existing baseline");
    const result = await store.commitAuthority({expected_provider_revision: head.provider_revision,
      operation_id: String(request.operation_id), next_projection: head.head,
      events: [{schema_version: "loopx_coordination_runtime_shadow_event_v0", event_kind: "todo_add"}],
      receipts: [{schema_version: "loopx_coordination_runtime_shadow_receipt_v0", operation_id: request.operation_id}]});
    return {...result};
  });'''
CASES.append(Case("duplicate_mirror", (
    ("loopx/cli_commands/todo.py", replacement("    print_payload(\n        payload,\n",
        MIRROR_HOOK + "    print_payload(\n        payload,\n")),
    (COORDINATION + "runtime_shadow.ts", replacement(
        '''  return { schema_version: COORDINATION_RUNTIME_SHADOW_RESULT_SCHEMA, status: "failed",
    reason_code: "legacy_lineage_read_only", primary_writeback_preserved: true, decision_read_from_shadow: false };''',
        MIRROR_COMMIT)),
), "tests/control_plane/test_shadow_drain_e2e.py::test_public_mutation_has_no_second_snapshot_mirror"))

# Fenced sibling-caller parity: each mutant is caught by exactly one parity row.
# Promoted CLI acquisition now succeeds through canonical authority. Probe the
# retained native legacy entrypoint for its fence diagnostic/envelope contract.
CASES.append(Case("native_fence_remediation_truncated", (
    (COORDINATION + "legacy_writer_fence.ts", replacement(
        'export const LEGACY_WRITER_FENCED_REMEDIATION =\n  "legacy coordination writer is fenced; use the promoted canonical authority ({authority_mode}) for goal {goal_id}; fence {fence_id}; the primary record was not changed";',
        'export const LEGACY_WRITER_FENCED_REMEDIATION =\n  "legacy coordination writer is fenced";')),
), "tests/control_plane_ts/legacy_writer_fence_caller_parity.test.ts", pattern="^fence parity: ts-acquire-engaged$"))
CASES.append(Case("python_fence_remediation_truncated", (
    (COORDINATION + "legacy_writer_fence.py", replacement(
        'LEGACY_WRITER_FENCED_REMEDIATION = (\n    "legacy coordination writer is fenced; use the promoted canonical authority "\n    "({authority_mode}) for goal {goal_id}; fence {fence_id}; "\n    "the primary record was not changed"\n)',
        'LEGACY_WRITER_FENCED_REMEDIATION = "legacy coordination writer is fenced"')),
), "tests/control_plane/test_legacy_coordination_writer_fence.py::test_present_fence_delegates_to_typescript_and_blocks"))
CASES.append(Case("fence_envelope_schema_leak", (
    (COORDINATION + "legacy_writer_fence.ts", replacement(
        "    this.payload = { write_check: writeCheck };",
        "    this.payload = writeCheck;")),
), "tests/control_plane_ts/legacy_writer_fence_caller_parity.test.ts", pattern="^fence parity: ts-acquire-engaged$"))
CASES.append(Case("fence_acquire_receipt_fabricated", (
    ("loopx/control_plane/work_items/task_lease_acquire.ts", replacement(
        '  return { code: error.code, message: error.message, payload: error.payload, stage: "validation", kind: "permission_denied" };',
        '  return { code: error.code, message: error.message, payload: error.payload };')),
), "tests/control_plane_ts/legacy_writer_fence_caller_parity.test.ts", pattern="^fence parity: ts-acquire-engaged$"))
CASES.append(Case("lifecycle_fence_guard_skipped", (
    ("loopx/control_plane/work_items/task_lease_lifecycle.ts", replacement(
        "        await requireLegacyCoordinationPrimaryWriteAllowed(request!.runtime_root, request!.goal_id);\n",
        "")),
), "tests/control_plane_ts/legacy_writer_fence_caller_parity.test.ts", pattern="^fence parity: ts-release-engaged$"))


def run(case: Case, directory: Path, log: Path) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("PYTHON", "LOOPX", "NODE", "COVERAGE"))}
    environment.update(PYTHONPATH=str(directory), PYTHONNOUSERSITE="1")
    result = subprocess.run(case.command(), cwd=directory, env=environment,
                            capture_output=True, text=True, timeout=120, check=False)
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True, help="Private log directory outside tracked source")
    parser.add_argument("--case", action="append", choices=[case.name for case in CASES])
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = [case for case in CASES if args.case is None or case.name in args.case]
    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="loopx-stage2c-mutants-") as temporary:
        frozen = Path(temporary) / "source"
        for folder in ("loopx", "tests"):
            shutil.copytree(source / folder, frozen / folder,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        for name in ("package.json", "pyproject.toml"):
            shutil.copy2(source / name, frozen / name)
        if (source / "node_modules").is_dir():
            (frozen / "node_modules").symlink_to(source / "node_modules", target_is_directory=True)
        manifest = {str(path.relative_to(frozen)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for folder in ("loopx", "tests") for path in (frozen / folder).rglob("*") if path.is_file()}
        (output / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        for case in cases:
            originals = {path: (frozen / path).read_text() for path, _ in case.edits}
            replacements = {path: edit(originals[path]) for path, edit in case.edits}
            control = run(case, frozen, output / (case.name + "-GREEN.log"))
            if control.returncode != 0 or not any(token in control.stdout for token in ("1 passed", "pass 1")):
                raise AssertionError(f"{case.name}: unchanged oracle did not pass")
            patch = "".join("".join(difflib.unified_diff(
                originals[path].splitlines(keepends=True), text.splitlines(keepends=True),
                fromfile=path, tofile=path)) for path, text in replacements.items())
            (output / (case.name + ".diff")).write_text(patch)
            try:
                for path, text in replacements.items():
                    (frozen / path).write_text(text)
                # Timestamp/size-based Python bytecode caches must not mask an edit.
                for cache in (frozen / "loopx").rglob("__pycache__"):
                    shutil.rmtree(cache)
                mutant = run(case, frozen, output / (case.name + "-RED.log"))
                log = mutant.stdout + mutant.stderr
                # Pytest assertion rewriting can render rich comparisons as
                # "E   assert ..." without spelling the exception class.
                assertion = "AssertionError" in log or re.search(r"^E\s+assert ", log, re.MULTILINE) is not None
                killed = (mutant.returncode == 1 and assertion
                          and any(token in log for token in ("1 failed", "fail 1"))
                          and not any(token in log for token in ("SyntaxError", "ImportError", "ModuleNotFoundError")))
            finally:
                for path, text in originals.items():
                    (frozen / path).write_text(text)
                for cache in (frozen / "loopx").rglob("__pycache__"):
                    shutil.rmtree(cache)
            results.append({"name": case.name, "test": case.test, "pattern": case.pattern,
                            "control_exit": control.returncode, "mutant_exit": mutant.returncode,
                            "killed_by_assertion": killed})
            report = {"selected": len(cases), "executed": len(results),
                      "killed": sum(row["killed_by_assertion"] for row in results), "results": results}
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(results[-1]), flush=True)
    return 0 if all(row["killed_by_assertion"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
