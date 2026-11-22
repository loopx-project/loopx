#!/usr/bin/env python3
"""Retrodict the RFC Appendix E incident lessons against today's guard.

``loopx/semantics/incident_v0.json`` records the lessons the RFC's Appendix E
collected from real escapes, each paired with a probe. A probe asks one
question: **does the guard as it exists now reject the defect form this lesson
describes?** It never asks whether a historical revision passed, because the
guard did not exist then and that answer is always no for every lesson.

Each probe drives the real pre-merge guard rather than a reimplementation, so a
probe cannot stay green after the check it exercises is removed. Negative
controls run the other direction: they are legal variations the guard must
accept, which is what stops a fail-always check from scoring as coverage.

The ledger's ``expectation`` per lesson is authored from the lesson text. Where
the measurement disagrees, the mismatch is the finding and is printed last.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.semantics.inventory import DEFAULT_ROOT, merge_candidate_groups  # noqa: E402

SMOKE_PATH = REPO_ROOT / "examples" / "semantic-vocabulary-drift-smoke.py"
LEDGER_PATH = REPO_ROOT / "loopx" / "semantics" / "incident_v0.json"
LEDGER_SCHEMA_VERSION = "loopx_semantic_incident_ledger_v0"
LEDGER_KEYS = {"schema_version", "meaning", "source", "measured_at", "incidents", "negative_controls", "caught_baseline"}
INCIDENT_KEYS = {"id", "class", "defect", "expected_rule", "probe", "expectation"}
CONTROL_KEYS = {"id", "class", "meaning", "probe"}
VERDICTS = {"caught", "uncovered"}


class LedgerError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LedgerError(message)


def appendix_e_lessons(rfc_path: Path) -> int:
    """Count the lessons the RFC's Appendix E records.

    The ledger is complete only if it probes every lesson the RFC collected, so
    a lesson added to the appendix without a probe fails the harness instead of
    being remembered by hand.
    """
    text = rfc_path.read_text(encoding="utf-8")
    require("## Appendix E" in text, "the RFC has no Appendix E; the ledger's source moved")
    section = text.split("## Appendix E", 1)[1]
    tail = section.split("## Appendix F", 1)[0] if "## Appendix F" in section else section
    return sum(1 for line in tail.splitlines() if line.startswith("- "))


def load_ledger() -> dict[str, Any]:
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    require(set(ledger) == LEDGER_KEYS, f"ledger keys must be exactly {sorted(LEDGER_KEYS)}")
    require(ledger["schema_version"] == LEDGER_SCHEMA_VERSION, "ledger schema_version drift")
    require(ledger["meaning"].strip() and ledger["source"].strip(), "ledger needs a meaning and a source")
    require(LEDGER_PATH.parent.joinpath("vocabulary_v0.json").is_file(), "ledger must sit beside the vocabulary registry")
    ids = [incident["id"] for incident in ledger["incidents"]]
    require(len(ids) == len(set(ids)), "incident ids repeat")
    for incident in ledger["incidents"]:
        require(set(incident) == INCIDENT_KEYS, f"{incident.get('id')}: incident keys must be exactly {sorted(INCIDENT_KEYS)}")
        require(incident["expectation"] in VERDICTS, f"{incident['id']}: expectation must be one of {sorted(VERDICTS)}")
        for key in ("class", "defect", "expected_rule", "probe"):
            require(incident[key].strip(), f"{incident['id']}: {key} must be non-empty")
    control_ids = [control["id"] for control in ledger["negative_controls"]]
    require(len(control_ids) == len(set(control_ids)), "negative control ids repeat")
    for control in ledger["negative_controls"]:
        require(set(control) == CONTROL_KEYS, f"{control.get('id')}: control keys must be exactly {sorted(CONTROL_KEYS)}")
    baseline = ledger["caught_baseline"]
    require(set(baseline) == {"meaning", "would_have_caught", "total", "negative_control_false_positives"},
            "caught_baseline keys must be meaning, would_have_caught, total, and negative_control_false_positives")
    require(baseline["total"] == len(ledger["incidents"]), "caught_baseline.total must equal the incident count")
    return ledger


class Guard:
    """The pre-merge guard's own module, executed so its globals are patchable.

    Some probes fault-inject an input: they replace ``source`` with a reader
    that returns a drifted module, or ``load_sources`` with one that returns a
    narrowed tree, and then call the unmodified check function. That only works
    against the module's real globals dict, which ``runpy.run_path`` does not
    give -- it returns a copy, so an injection there is a silent no-op and the
    probe would report 'uncovered' without measuring anything. Importing the
    file as a module keeps the injection live, and every injection re-reads its
    own effect before the verdict is trusted.
    """

    def __init__(self) -> None:
        spec = importlib.util.spec_from_file_location("_semantic_drift_smoke_probe", SMOKE_PATH)
        require(spec is not None and spec.loader is not None, "the guard module could not be loaded")
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)
        self.ns = self.module.__dict__
        self.registry = self.ns["load_registry"]()
        self.sources = self.ns["load_sources"](self.ns["REPO_ROOT"])
        self.inventory = self.ns["build_inventory"](self.ns["REPO_ROOT"], sources=self.sources)

    def rejects(self, call: Any) -> tuple[bool, str]:
        try:
            call()
        except Exception as error:  # noqa: BLE001 - any rejection is the signal
            first = str(error).splitlines()[0][:140]
            return True, f"{type(error).__name__}: {first}"
        return False, "accepted"

    def with_registry_file(self, mutated: dict[str, Any], call: Any) -> tuple[bool, str]:
        """Run a loader-level check against mutated registry content.

        The patch is proven live before the verdict is used: the committed
        registry is written to the same temporary path and must read back
        equal. A no-op patch fails the self-check instead of passing silently.
        """
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        handle.close()
        temporary = Path(handle.name)
        original = self.ns["REGISTRY_PATH"]
        try:
            temporary.write_text(json.dumps(self.registry), encoding="utf-8")
            self.ns["REGISTRY_PATH"] = temporary
            require(self.ns["load_registry"]() == self.registry, "registry injection is not live; the loader read the committed file")
            temporary.write_text(json.dumps(mutated), encoding="utf-8")
            return self.rejects(call)
        finally:
            self.ns["REGISTRY_PATH"] = original
            temporary.unlink(missing_ok=True)

    def semantic_forks(self, registry: dict[str, Any] | None = None) -> int:
        return self.ns["check_scope_declarations"](registry or self.registry, self.inventory)


# --- incident probes -----------------------------------------------------------------


def probe_cross_runtime_value_drift(guard: Guard) -> tuple[bool, str]:
    """E01: change the implementation on one runtime and see whether the other is compared."""
    target = None
    for name, vocabulary in guard.registry["vocabularies"].items():
        owner = vocabulary["owners"]["typescript"]
        if owner and "::" in owner and owner.split("::")[0].endswith(".ts"):
            target = (name, owner)
            break
    require(target is not None, "no registered cross-runtime pair to mutate; the probe cannot measure anything")
    name, owner = target
    module, _symbol = owner.split("::")
    victim = guard.ns["owner_values"](owner)[-1]
    original = guard.ns["source"]
    applied = []

    def drifted(path: str):
        file = original(path)
        if path != module:
            return file
        applied.append(path)
        if victim not in file.text:
            return file
        return guard.ns["SourceFile"](path=file.path, suffix=file.suffix, text=file.text.replace(victim, f"{victim}_drift", 1))

    guard.ns["source"] = drifted
    try:
        require("drift" in guard.ns["owner_values"](owner)[-1], "implementation injection is not live")
        rejected, detail = guard.rejects(lambda: guard.ns["check_owned_vocabularies"](guard.registry, guard.inventory))
    finally:
        guard.ns["source"] = original
    require(applied and set(applied) == {module}, f"the drifted module was never read (read={sorted(set(applied))})")
    return rejected, f"{name} {owner}: {victim} -> {victim}_drift rejected={rejected} ({detail})"


def probe_fixture_copy_visible(guard: Guard) -> tuple[bool, str]:
    """E02: a copy outside the scanned roots is invisible unless the boundary is declared."""
    roots = sorted({source.path.split("/")[0] for source in guard.sources})
    outside = [source.path for source in guard.sources if not source.path.startswith("loopx/")]
    declared = guard.ns["LITERAL_SCAN_ROOTS"]
    return bool(outside), f"scanned_roots={roots} declared_roots={declared} sources_outside={len(outside)}"


def probe_malformed_value_reported(guard: Guard) -> tuple[bool, str]:
    """E03: a digit-bearing value is captured, and a malformed one is reported rather than skipped."""
    shape = guard.ns["VALUE_SHAPE"]
    digit_accepted = shape.match("quota_skip_v2") is not None
    malformed_reported = shape.match("Quota-Skip") is None
    synthetic = guard.ns["SourceFile"](
        path="loopx/_probe.py", suffix=".py", text='packet = {"effective_action": "quota_skip_v2"}\n'
    )
    rejected, detail = guard.rejects(
        lambda: guard.ns["collect_literal_uses"](guard.ns["REPO_ROOT"], "effective_action", [synthetic])
    )
    captured = "quota_skip_v2" in guard.ns["collect_literal_uses"](guard.ns["REPO_ROOT"], "effective_action", [synthetic])
    return digit_accepted and malformed_reported and captured, (
        f"shape_accepts_digit={digit_accepted} shape_rejects_malformed={malformed_reported} "
        f"scanner_captures_digit_value={captured} scanner_error={detail if rejected else 'none'}"
    )


def probe_typescript_blind_guard(guard: Guard) -> tuple[bool, str]:
    """E04: dropping a runtime the registry claims to cover must fail."""
    mutated = copy.deepcopy(guard.registry)
    for vocabulary in mutated["vocabularies"].values():
        if "literal_scan" in vocabulary:
            vocabulary["literal_scan"]["suffixes"] = [
                suffix for suffix in vocabulary["literal_scan"]["suffixes"] if suffix != ".ts"
            ]
    rejected, detail = guard.rejects(lambda: guard.ns["check_coverage_floor"](mutated))
    typescript_sources = sum(1 for source in guard.sources if source.suffix == ".ts")
    return rejected, f"dropping .ts rejected={rejected}; ts_sources_scanned={typescript_sources} ({detail})"


def probe_registry_weakness_rejected(guard: Guard) -> tuple[bool, str]:
    """E05: a data edit must not be able to weaken the validator."""
    lowered = copy.deepcopy(guard.registry)
    lowered["coverage_floor"]["vocabularies"] = 0
    floor_rejected, _ = guard.rejects(lambda: guard.ns["check_coverage_floor"](lowered))

    bad_owner = copy.deepcopy(guard.registry)
    vocabulary_name = next(name for name, item in bad_owner["vocabularies"].items() if item["owners"]["python"])
    bad_owner["vocabularies"][vocabulary_name]["owners"]["python"] = "not_a_module_symbol"
    owner_rejected, owner_detail = guard.with_registry_file(bad_owner, guard.ns["load_registry"])

    narrowed_scan = copy.deepcopy(guard.registry)
    for vocabulary in narrowed_scan["vocabularies"].values():
        if "literal_scan" in vocabulary:
            vocabulary["literal_scan"]["roots"] = ["loopx/control_plane"]
    roots_rejected, _ = guard.rejects(lambda: guard.ns["check_coverage_floor"](narrowed_scan))
    return floor_rejected and owner_rejected and roots_rejected, (
        f"floor_lowered_rejected={floor_rejected} owner_shape_rejected={owner_rejected} "
        f"scan_roots_narrowed_rejected={roots_rejected} ({owner_detail})"
    )


def probe_definitions_ratcheted(guard: Guard) -> tuple[bool, str]:
    """E06: budgets must count definitions, not only names."""
    budgets = sorted(key for key in guard.ns["RATCHET_KEYS"] if key.endswith("_definitions"))
    anchored = sorted(key for key in guard.ns["BUDGET_ANCHOR"] if key.endswith("_definitions"))
    return len(budgets) >= 3 and budgets == anchored, f"definition_budgets={budgets} anchored={len(anchored)}"


def probe_commit_time_selection(guard: Guard) -> tuple[bool, str]:
    """E07: the guard must be selected before merge, not discovered by the fleet."""
    from loopx.canary.premerge import build_premerge_validation_gate
    from loopx.canary.runner import MAINTAINABILITY_RATCHET_SCRIPT

    planner = (REPO_ROOT / "loopx" / "canary" / "planner.py").read_text(encoding="utf-8")
    command = "examples/semantic-vocabulary-drift-smoke.py"
    planned = command in planner
    gate = build_premerge_validation_gate(
        changed_files=["loopx/_probe_semantic_guard.py"],
        execute=False,
        repo_root=REPO_ROOT,
    )
    selected_commands = [
        str(check.get("command") or "")
        for check in (gate.get("catalog_run") or {}).get("selected_checks") or []
        if isinstance(check, dict)
    ]
    selected_by_premerge = any(command in item for item in selected_commands)
    not_advisory_baseline = command != MAINTAINABILITY_RATCHET_SCRIPT
    guarded_roots = [hint for hint in ("loopx/", "scripts/", "examples/") if f'"{hint}"' in planner]
    return planned and selected_by_premerge and not_advisory_baseline, (
        f"named_in_planner={planned} selected_by_premerge={selected_by_premerge} "
        f"not_advisory_baseline={not_advisory_baseline} planner_trigger_hints={guarded_roots}"
    )


def probe_anchor_equality(guard: Guard) -> tuple[bool, str]:
    """E08: neither direction of header drift may pass an equality anchor."""
    key = "multi_value_twins"
    forks = guard.semantic_forks()
    check = guard.ns["evaluate_inventory_budget_findings"]
    summary = guard.inventory["summary"]

    def run(offset: int) -> bool:
        mutated = copy.deepcopy(guard.registry)
        mutated["inventory_ratchets"][key] = guard.ns["BUDGET_ANCHOR"][key] + offset
        rejected, _ = guard.rejects(lambda: check(summary, forks, mutated["inventory_ratchets"]))
        return rejected

    lowered, raised = run(-1), run(+1)
    floor_lowered, _ = guard.rejects(
        lambda: guard.ns["check_coverage_floor"]({**copy.deepcopy(guard.registry), "coverage_floor": {
            **guard.registry["coverage_floor"], "vocabularies": guard.registry["coverage_floor"]["vocabularies"] - 1}})
    )
    return lowered and raised and floor_lowered, (
        f"budget_lowered_rejected={lowered} budget_raised_rejected={raised} coverage_floor_lowered_rejected={floor_lowered}"
    )


def probe_slot_relation(guard: Guard) -> tuple[bool, str]:
    """E09: a field carrying several vocabularies records the slots as a relation."""
    groups = guard.registry["relations"].get("shared_field_names", [])
    entry = next((group for group in groups if group.get("field") == "effective_action"), None)
    slots = [slot.get("slot") for slot in (entry or {}).get("slots", [])]
    reanchored = copy.deepcopy(guard.registry)
    for group in reanchored["relations"]["shared_field_names"]:
        for slot in group.get("slots", []):
            if "slot" in slot:
                slot["slot"] = "somewhere.else"
    rejected, detail = guard.rejects(lambda: guard.ns["check_relations"](reanchored))
    return bool(entry) and "should_run.effective_action" in slots and rejected, (
        f"registered_slots={slots} unanchored_slot_rejected={rejected} ({detail})"
    )


def probe_no_committed_snapshot(guard: Guard) -> tuple[bool, str]:
    """E10: the guard's input is computed at check time, not read from a snapshot."""
    tracked = subprocess.run(
        ["git", "ls-files", "--", "loopx/semantics/inventory_v0.json"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    ).stdout.strip()
    text = SMOKE_PATH.read_text(encoding="utf-8")
    computed = "build_inventory(REPO_ROOT" in text
    return not tracked and computed, f"tracked_snapshot={tracked!r} inventory_computed_at_check_time={computed}"


def probe_scope_declaration(guard: Guard) -> tuple[bool, str]:
    """E11: a rename cannot remove debt, and every defining module must be named."""
    declarations = guard.registry["scope_declarations"]
    if not declarations:
        return False, "no scope declarations are recorded"
    name = next(iter(declarations))
    truncated = copy.deepcopy(guard.registry)
    truncated["scope_declarations"][name]["contexts"] = []
    missing_rejected, _ = guard.rejects(lambda: guard.ns["check_scope_declarations"](truncated, guard.inventory))

    renamed = copy.deepcopy(guard.registry)
    renamed["scope_declarations"][f"{name}_renamed"] = renamed["scope_declarations"].pop(name)
    rename_rejected, rename_detail = guard.rejects(lambda: guard.ns["check_scope_declarations"](renamed, guard.inventory))
    return missing_rejected and rename_rejected, (
        f"declared={sorted(declarations)} missing_context_rejected={missing_rejected} "
        f"rename_rejected={rename_rejected} ({rename_detail})"
    )


def probe_scan_reach_disclosed(guard: Guard) -> tuple[bool, str]:
    """E12: narrow the scanner's real reach and see whether any check notices.

    A document can widen its scope claim without the scanner widening with it.
    The probe drops a subtree that holds no registered owner symbol and no
    scanned literal field, then runs every reach-consuming check. Each of them
    bounds its count from above, so a narrowed reach that still satisfies the
    floors is invisible and the scope claim is unverified.
    """
    dropped_prefix = "loopx/extensions/"
    full = guard.ns["load_sources"](guard.ns["REPO_ROOT"])
    kept = [source for source in full if not source.path.startswith(dropped_prefix)]
    dropped = len(full) - len(kept)
    require(dropped > 0, f"{dropped_prefix} holds no sources; the probe cannot narrow anything")
    original = guard.ns["load_sources"]

    def narrowed(repo_root: str, root: str | None = None):
        scope = root if root is not None else DEFAULT_ROOT
        return [source for source in kept if source.path.startswith(scope)]

    guard.ns["load_sources"] = narrowed
    try:
        require(len(guard.ns["load_sources"](guard.ns["REPO_ROOT"])) == len(kept), "reach injection is not live")
        outcomes = {}
        outcomes["literal_vocabularies"] = guard.rejects(
            lambda: guard.ns["check_literal_vocabularies"](guard.registry, kept)
        )[0]
        outcomes["retirement_budgets"] = guard.rejects(
            lambda: guard.ns["check_retirement_budgets"](guard.registry, kept)
        )[0]
        outcomes["dual_runtime_twins"] = guard.rejects(lambda: guard.ns["check_dual_runtime_twins"](guard.registry))[0]
    finally:
        guard.ns["load_sources"] = original
    return any(outcomes.values()), (
        f"dropped_sources={dropped} ({dropped_prefix}) reaching_checks_that_noticed={sorted(k for k, v in outcomes.items() if v)}"
    )


def probe_target_table(guard: Guard) -> tuple[bool, str]:
    """E13: the RFC must publish a target table, or the work has no completion test."""
    rfc = (REPO_ROOT / guard.registry["rfc"]).read_text(encoding="utf-8")
    marker = "Target when this RFC closes"
    if marker not in rfc:
        return False, "no target table in the RFC"
    body = rfc.split(marker, 1)[1].splitlines()[1:]
    rows = 0
    for line in body:
        if not line.startswith("|"):
            if rows:
                break
            continue
        if set(line) <= set("| -"):
            continue
        rows += 1
    return rows >= 8, f"target_table_rows={rows}"


def probe_dangling_decision(guard: Guard) -> tuple[bool, str]:
    """E14: a decision waiting on an undefined check is a dangling reference."""
    rfc = (REPO_ROOT / guard.registry["rfc"]).read_text(encoding="utf-8")
    if "## 12. Open decisions" not in rfc:
        return False, "no open decisions section"
    section = rfc.split("## 12. Open decisions", 1)[1].split("## Appendix A", 1)[0]
    items = re.findall(r"^\d+\.\s\*\*(.+?)\*\*", section, re.M)
    guard_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted((REPO_ROOT / "loopx" / "canary").glob("*.py"))
    )
    enforcing = "open_decision" in guard_text
    return enforcing, f"open_decision_items={len(items)} check_enforcing_named_inputs={enforcing}"


def probe_roles_table(guard: Guard) -> tuple[bool, str]:
    """E15: a role word is defined only when it is a table with a check per role."""
    model = guard.registry["formal_model"]
    roles = sorted(model["roles"])
    hierarchy = model["role_hierarchy"]
    invariants = {item["id"] for item in model["invariants"]}
    role_invariants = sorted(invariants & {"F1_producer_closedness", "F3_consumer_domain_closedness"})
    anchor = sorted(guard.ns["PRODUCER_VOCABULARY_ANCHOR"])
    return bool(roles) and bool(hierarchy) and bool(role_invariants) and bool(anchor), (
        f"roles={roles} hierarchy={sorted(hierarchy)} role_invariants={role_invariants} producer_vocabulary_anchor={anchor}"
    )


# --- negative controls: legal variations the guard must accept -----------------------


def control_single_value_ignored(guard: Guard) -> tuple[bool, str]:
    """A one-value set must not be reported as a collision candidate."""
    fake = {section: [] for section in ("python_enums", "python_closed_sets", "python_literal_aliases", "typescript_const_arrays")}
    fake["python_closed_sets"] = [
        {"name": "ONLY_ONE", "module": "loopx/_probe_a.py", "values": ["solo"]},
        {"name": "ALSO_ONE", "module": "loopx/_probe_b.py", "values": ["solo"]},
    ]
    groups = merge_candidate_groups(fake)
    return bool(groups), f"groups_for_single_value_sets={groups}"


def control_unrelated_not_autoregistered(guard: Guard) -> tuple[bool, str]:
    """Two names sharing a generic value set must not become a same_concept relation."""
    pairs = [group["members"] for group in guard.registry["relations"]["same_concept"]]
    offending = [
        pair for pair in pairs
        if any("confidence_levels" in member for member in pair)
        and any("edge_case_complexities" in member for member in pair)
    ]
    return bool(offending), f"same_concept_groups={len(pairs)} offending={offending}"


def control_equality_anchor_accepts_current(guard: Guard) -> tuple[bool, str]:
    """The equality anchors must accept the committed registry."""
    check = guard.ns["evaluate_inventory_budget_findings"]
    rejected, detail = guard.rejects(
        lambda: check(guard.inventory["summary"], guard.semantic_forks(), guard.registry["inventory_ratchets"])
    )
    return rejected, f"committed_registry_rejected={rejected} ({detail})"


def control_owner_shape_accepts_current(guard: Guard) -> tuple[bool, str]:
    """The registry's own owners must pass the shape and value checks."""
    rejected, detail = guard.rejects(lambda: guard.ns["check_owned_vocabularies"](guard.registry, guard.inventory))
    return rejected, f"committed_registry_rejected={rejected} ({detail})"


def control_substring_not_counted(guard: Guard) -> tuple[bool, str]:
    """A longer identifier containing the field token must not be counted."""
    field = next(iter(guard.registry["retirement_ledger"]["should_run_legacy_decision_fields"]["fields"]))
    counter = guard.ns["count_identifier_modules"]
    exact = guard.ns["SourceFile"](path="loopx/_probe.py", suffix=".py", text=f"value = {field}\n")
    extended = guard.ns["SourceFile"](path="loopx/_probe.py", suffix=".py", text=f"value = {field}_extra\n")
    counted_exact = counter(field, ".py", [exact])
    counted_extended = counter(field, ".py", [extended])
    return counted_exact != 1 or counted_extended != 0, (
        f"field={field} exact_count={counted_exact} longer_identifier_count={counted_extended}"
    )


INCIDENT_PROBES = {
    "cross_runtime_value_drift": probe_cross_runtime_value_drift,
    "fixture_copy_visible": probe_fixture_copy_visible,
    "malformed_value_reported": probe_malformed_value_reported,
    "typescript_blind_guard": probe_typescript_blind_guard,
    "registry_weakness_rejected": probe_registry_weakness_rejected,
    "definitions_ratcheted": probe_definitions_ratcheted,
    "commit_time_selection": probe_commit_time_selection,
    "anchor_equality": probe_anchor_equality,
    "slot_relation": probe_slot_relation,
    "no_committed_snapshot": probe_no_committed_snapshot,
    "scope_declaration": probe_scope_declaration,
    "scan_reach_disclosed": probe_scan_reach_disclosed,
    "target_table": probe_target_table,
    "dangling_decision": probe_dangling_decision,
    "roles_table": probe_roles_table,
}

CONTROL_PROBES = {
    "single_value_ignored": control_single_value_ignored,
    "unrelated_not_autoregistered": control_unrelated_not_autoregistered,
    "equality_anchor_accepts_current": control_equality_anchor_accepts_current,
    "owner_shape_accepts_current": control_owner_shape_accepts_current,
    "substring_not_counted": control_substring_not_counted,
}


def main() -> int:
    ledger = load_ledger()
    guard = Guard()
    lessons = appendix_e_lessons(REPO_ROOT / guard.registry["rfc"])
    require(lessons == len(ledger["incidents"]), (
        f"Appendix E records {lessons} lessons but the ledger probes {len(ledger['incidents'])}; "
        "every lesson needs a probe and every probe needs a lesson"
    ))
    for ledger_key, probes in (("incidents", INCIDENT_PROBES), ("negative_controls", CONTROL_PROBES)):
        for entry in ledger[ledger_key]:
            require(entry["probe"] in probes, f"{entry['id']}: unknown probe {entry['probe']!r}")

    caught, mismatches, details = 0, [], []
    for incident in ledger["incidents"]:
        observed, detail = INCIDENT_PROBES[incident["probe"]](guard)
        verdict = "caught" if observed else "uncovered"
        caught += observed
        details.append((incident["id"], incident["class"], verdict, detail))
        if verdict != incident["expectation"]:
            mismatches.append(f"{incident['id']} {incident['class']}: expected {incident['expectation']}, measured {verdict}")

    false_positives = []
    for control in ledger["negative_controls"]:
        violated, detail = CONTROL_PROBES[control["probe"]](guard)
        if violated:
            false_positives.append(f"{control['id']} {control['class']}: rejected a legal variation ({detail})")

    total = len(ledger["incidents"])
    print("semantic-incident-retrodiction: " + ("ok" if not mismatches else "mismatches"))
    print(f"  appendix_e_lessons={lessons} ledger_incidents={total}")
    print(f"  would_have_caught={caught}/{total}")
    print(f"  negative_control_false_positives={len(false_positives)}/{len(ledger['negative_controls'])}")
    baseline = ledger["caught_baseline"]
    print(f"  caught_baseline={baseline['would_have_caught']}/{baseline['total']}")
    for incident_id, incident_class, verdict, detail in details:
        print(f"  {incident_id} {verdict:9s} {incident_class}: {detail}")
    for mismatch in mismatches:
        print(f"  expectation-mismatch: {mismatch}")
    if '--report' in sys.argv[1:]:
        for incident in ledger["incidents"]:
            print(f"  {incident['id']} defect: {incident['defect']}")
            print(f"  {incident['id']} rule:   {incident['expected_rule']}")
    if '--json' in sys.argv[1:]:
        print(json.dumps({
            "would_have_caught": caught,
            "total": total,
            "negative_control_false_positives": len(false_positives),
            "mismatches": mismatches,
            "verdicts": {incident_id: verdict for incident_id, _c, verdict, _d in details},
        }, indent=2, sort_keys=True))

    require(caught >= baseline["would_have_caught"],
            f"retrodiction regressed: {caught}/{total} below the frozen baseline {baseline['would_have_caught']}/{total}; "
            "a lesson that was caught must stay caught")
    require(len(false_positives) <= baseline["negative_control_false_positives"],
            "negative controls regressed: " + "; ".join(false_positives))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LedgerError as error:
        print(f"semantic-incident-retrodiction: FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from error
