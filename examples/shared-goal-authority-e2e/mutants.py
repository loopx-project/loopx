"""Deliberate regressions for bounded shadow qualification, in isolated copies only.

Each case first requires its unchanged oracle to pass. A mutation counts as
killed only when that same test reports an assertion failure, never an import,
syntax, process timeout, or setup failure. No live goal or checkout is edited.
"""
from __future__ import annotations

import argparse
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
        '    state_text = ""', '    state_text = state_path.read_text(encoding="utf-8")')),), 'tests/control_plane/test_canonical_goal_governance.py::test_empty_canonical_is_authoritative_and_missing_display_is_not_repaired'),
    Case('delivery_wait_target_unbound', (('loopx/control_plane/todos/resume_condition.ts', replacement(
        'condition.target_todo_id !== spec.target || ', '')),),
         'tests/control_plane_ts/delivery_response.test.ts', 'exact dependency identity'),
    Case('delivery_wait_unknown_class', (('loopx/control_plane/todos/resume_condition.ts', replacement(
        '["advancement_task", "user_gate", "user_action", "blocker"].includes(String(condition.target_task_class))',
        'true')),),
         'tests/control_plane_ts/delivery_response.test.ts', 'exact dependency identity'),
    Case('lineage', ((COORDINATION + 'local_authority_shadow.ts', replacement('  requireLineage(entry.capture_lineage_id === binding.capture_lineage_id, "stale_generation");', '  // DELIBERATE MUTANT: omit active lineage validation.')),),
         'tests/control_plane_ts/local_authority_shadow_outbox.test.ts', 'self-consistent foreign'),
    Case('previous_partition', ((COORDINATION + 'local_authority_shadow.ts', replacement('  requireLineage(request.entry.source.previous_partition_digest === digest, "source_partition_continuity_unproved");', '  // DELIBERATE MUTANT: omit previous partition proof.')),),
         'tests/control_plane_ts/local_authority_shadow_outbox.test.ts', 'missing primary mutation'),
    Case('qualification_history', ((COORDINATION + 'runtime_shadow.ts', replacement('    const lineage = await loadValidatedShadowLineage(store, request.runtime_root, request.goal_id, binding);', '    const page = await store.scanCommitted(null, 10000);\n    const lineage = { head: await store.loadAuthority(), transactions: page.transactions,\n      last_sequences: {}, last_applied_sequences: {}, write_classes: ["todo_add"] };')),),
         'tests/control_plane_ts/coordination_runtime_shadow.test.ts', 'observation transaction mixed'),
    Case('management_request_digest', ((COORDINATION + "shadow_management.ts", replacement('if (state.operation.request_digest !== digest) throw new ShadowManagementError("management_operation_identity_mismatch");', 'if (false) throw new ShadowManagementError("management_operation_identity_mismatch");')),),
         "tests/control_plane_ts/shadow_management.test.ts", 'management request digest'),
    Case('management_manifest_hash', ((COORDINATION + "shadow_management.ts", replacement('managementDigest(manifest) !== state.operation.manifest_digest\n      || !supportedSchema\n', 'false\n      || !supportedSchema\n')),),
         "tests/control_plane_ts/shadow_management.test.ts", 'management manifest hash'),
    Case('management_phase', ((COORDINATION + "shadow_management.ts", replacement('operation.kind !== kind || !phases.includes(String(operation.phase))', 'operation.kind !== kind')),),
         "tests/control_plane_ts/shadow_management.test.ts", 'management phase validation'),
    Case('management_goal_binding', ((COORDINATION + "shadow_management.ts", replacement(' || value.goal_id !== goal\n      || ', '\n      || ')),),
         "tests/control_plane_ts/shadow_management.test.ts", 'management goal binding'),
    Case('management_candidate_lineage', ((COORDINATION + "shadow_management.ts", replacement('candidate.capture_lineage_id !== expectedLineage', 'false')),),
         "tests/control_plane_ts/shadow_management.test.ts", 'rollback refuses a different valid candidate lineage'),
]

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
    function = replacement(
        '    }, {businessWrite: "todo_update"});\n  } catch (error) {',
        "    })();\n  } catch (error) {",
    )(function)
    return source[:start] + function + source[end:]


WRITER_TEST = "tests/control_plane/test_shadow_writer_boundaries.py::"
CASES.extend([
    Case("management_result_binding", ((COORDINATION + "shadow_management.ts", replacement(
        "  await validateReplayResult(request, manifest, prior.result, state);",
        "  // DELIBERATE MUTANT: trust a cached result from another operation.")),),
         "tests/control_plane/test_shadow_management_variant_e2e.py::test_rollback_result_cannot_borrow_another_goals_archive_evidence[historical]"),
    Case("native_update_maintenance", ((COORDINATION + "local_authority_runtime.ts",
         remove_native_update_maintenance),),
         "tests/control_plane/test_shadow_native_todo_update_e2e.py::test_native_update_holds_before_primary_for_management[native-bootstrapping-cli]"),
    Case("archive_oldest_selection", ((COORDINATION + "todo_archive_selection.ts", replacement(
        "movable.slice(0, moveCount)", "movable.slice(-moveCount)")),),
         "tests/control_plane_ts/todo_archive_selection.test.ts",
         "archive selection preserves imported order"),
    Case("remove_refresh_source_recheck", (("loopx/state_refresh.py", replacement(
        "            if normalized_next_action:",
        "            if False and normalized_next_action:  # DELIBERATE MUTANT: bypass source recheck.")),),
         "tests/control_plane/test_next_action_writeback.py::test_final_commit_rechecks_relevant_source_facts[task]"),
    Case("public_refresh_writes_owned_paragraph", (("loopx/state_refresh.py", replacement(
        "if not dry_run:\n                runs_dir.mkdir(parents=True, exist_ok=True)\n                json_path, markdown_path = reserve_unique_run_paths(runs_dir, generated_at)",
        "if not dry_run:\n                resolved_state_file.write_text(\n                    current_text + \"\\n- \" + normalized_next_action, encoding=\"utf-8\"\n                )\n                runs_dir.mkdir(parents=True, exist_ok=True)\n                json_path, markdown_path = reserve_unique_run_paths(runs_dir, generated_at)")),),
         WRITER_TEST + "test_public_refresh_step_does_not_write_owned_paragraph"),
    Case("fence_unshared_state_lock", ((COORDINATION + "legacy_writer_fence.ts", replacement(
        "withFileMutationLock(statePath, () =>",
        'withFileMutationLock(statePath + ".mutant-unshared", () =>')),),
         "tests/control_plane_ts/shadow_native_writer_boundary.test.ts",
         "^fence engagement waits for an existing state writer before publication$"),
])

# The retained exact-release path still observes the durable promotion fence.
CASES.append(Case("native_fence_remediation_truncated", (
    (COORDINATION + "legacy_writer_fence.ts", replacement(
        'export const LEGACY_WRITER_FENCED_REMEDIATION =\n  "legacy coordination writer is fenced; use the promoted canonical authority ({authority_mode}) for goal {goal_id}; fence {fence_id}; the primary record was not changed";',
        'export const LEGACY_WRITER_FENCED_REMEDIATION =\n  "legacy coordination writer is fenced";')),
), "tests/control_plane_ts/task_lease_lifecycle.test.ts",
   pattern="^exact legacy release honors an engaged promotion fence without writes$"))
CASES.append(Case("python_fence_remediation_truncated", (
    (COORDINATION + "legacy_writer_fence.py", replacement(
        'LEGACY_WRITER_FENCED_REMEDIATION = (\n    "legacy coordination writer is fenced; use the promoted canonical authority "\n    "({authority_mode}) for goal {goal_id}; fence {fence_id}; "\n    "the primary record was not changed"\n)',
        'LEGACY_WRITER_FENCED_REMEDIATION = "legacy coordination writer is fenced"')),
), "tests/control_plane/test_legacy_coordination_writer_fence.py::test_present_fence_delegates_to_typescript_and_blocks"))
CASES.append(Case("fence_envelope_schema_leak", (
    (COORDINATION + "legacy_writer_fence.ts", replacement(
        "    this.payload = { write_check: writeCheck };",
        "    this.payload = writeCheck;")),
), "tests/control_plane_ts/task_lease_lifecycle.test.ts",
   pattern="^exact legacy release honors an engaged promotion fence without writes$"))
CASES.append(Case("lifecycle_fence_guard_skipped", (
    ("loopx/control_plane/work_items/task_lease_lifecycle.ts", replacement(
        "  await requireLegacyCoordinationPrimaryWriteAllowed(request.runtime_root, request.goal_id);\n"
        "  const decision = retainedReleaseDecision(request, existing, at);",
        "  const decision = retainedReleaseDecision(request, existing, at);")),
), "tests/control_plane_ts/task_lease_lifecycle.test.ts",
   pattern="^exact legacy release honors an engaged promotion fence without writes$"))


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
        # TS oracles import the repository's supported-Python discovery helper.
        # Freeze that dependency too; a missing import is not a killed mutant.
        support_files = (
            "package.json", "pyproject.toml", "scripts/test-python.mjs",
            "scripts/loopx-python.sh",
        )
        for name in support_files:
            (frozen / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, frozen / name)
        if (source / "node_modules").is_dir():
            (frozen / "node_modules").symlink_to(source / "node_modules", target_is_directory=True)
        manifest = {str(path.relative_to(frozen)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for folder in ("loopx", "tests") for path in (frozen / folder).rglob("*") if path.is_file()}
        manifest.update({name: hashlib.sha256((frozen / name).read_bytes()).hexdigest()
                         for name in support_files})
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
                assertion = (
                    "AssertionError" in log
                    or "Failed: DID NOT RAISE" in log
                    or re.search(r"^E\s+assert ", log, re.MULTILINE) is not None
                )
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
