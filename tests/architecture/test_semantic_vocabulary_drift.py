"""Run the semantic vocabulary drift smoke inside the pull-request pytest sweep.

The canary fleet discovers ``examples/**/*-smoke.py`` on its own, but the fleet
runs after merge and on a schedule, and ``loopx canary premerge`` selects smokes
by changed-path tokens. Neither is a commit-time check for a diff that only
touches ``loopx/``. This wrapper is the PR-path obligation named in the RFC
``docs/architecture/rfcs/semantic-vocabulary-convergence-v0.md`` (Section 10):
the smoke fails closed here on every pull request that runs the Python tests.
"""

from __future__ import annotations

import copy
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE = REPO_ROOT / "examples" / "semantic-vocabulary-drift-smoke.py"


def test_semantic_vocabulary_registry_matches_the_code() -> None:
    completed = subprocess.run(
        [sys.executable, "-B", str(SMOKE)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (
        "semantic vocabulary drift smoke failed; the registry, computed inventory, or an "
        "anchor no longer matches the code:\n" + completed.stdout + completed.stderr
    )
    assert completed.stdout.startswith("semantic-vocabulary-drift-smoke: ok"), (
        completed.stdout
    )
    # The domain sizes are part of the report, not only of the registry: a reader
    # of the smoke output must see how much each invariant actually covers.
    report = completed.stdout
    assert "\n  formal_domain=F1:" in report, report
    for token in ("kernel_with_producers=", "cross_runtime_unverified=",
                  "producer_scan_reach=", "scope_declarations=", "declared_contexts="):
        assert token in report, (token, report)
    assert "can never become evidence)" in report, report


@pytest.mark.parametrize("mutation", ["twin_budget", "twin_root", "scan_root"])
def test_registry_cannot_relax_scan_scope_or_twin_budget(mutation: str) -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    if mutation == "twin_budget":
        registry["dual_runtime_twins"]["module_budget"] += 1
    elif mutation == "twin_root":
        registry["dual_runtime_twins"]["root"] = "loopx/semantics"
    else:
        registry["vocabularies"]["effective_action"]["literal_scan"]["roots"] = ["loopx/control_plane"]
    with pytest.raises(smoke["Drift"]):
        smoke["check_coverage_floor"](registry)
        smoke["check_dual_runtime_twins"](registry)


@pytest.mark.parametrize("suffix", [".py", ".ts"])
@pytest.mark.parametrize("quote", ["'", '\"'])
def test_literal_scan_rejects_unknown_value_with_either_quote(suffix: str, quote: str) -> None:
    smoke = runpy.run_path(str(SMOKE))
    text = f"effective_action = {quote}unregistered_action{quote}"
    sources = [smoke["SourceFile"]("loopx/probe" + suffix, suffix, text)]
    with pytest.raises(smoke["Drift"], match="unregistered_action"):
        smoke["check_literal_vocabularies"](smoke["load_registry"](), sources)


def test_candidate_decisions_are_exhaustive_and_default_to_unknown() -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = smoke["load_registry"]()
    candidate_decisions = registry["formal_model"]["candidate_decisions"]
    assert candidate_decisions["default"] == "unknown"
    assert set(candidate_decisions["values"]) == {
        "reuse_existing",
        "extend_vocabulary",
        "create_vocabulary",
        "local_only",
        "external_input",
        "compatibility_only",
        "unknown",
    }

    registry["formal_model"]["candidate_decisions"]["default"] = "reuse_existing"
    with pytest.raises(smoke["Drift"], match="default unresolved candidates"):
        smoke["check_formal_model"](registry["formal_model"], registry)


def test_bounded_producer_scan_rejects_unregistered_write() -> None:
    smoke = runpy.run_path(str(SMOKE))
    source = smoke["SourceFile"](
        "loopx/control_plane/quota/probe.py",
        ".py",
        'def produce():\n    return {"effective_action": "unregistered_action"}\n',
    )
    with pytest.raises(smoke["Drift"], match="unregistered_action"):
        smoke["check_producers"](
            {
                "relations": {"shared_field_names": [{"field": "effective_action", "slots": [{
                    "slot": "should_run.effective_action",
                    "vocabularies": ["effective_action", "agent_scope_frontier_action"],
                }]}]},
                "vocabularies": {
                    "agent_scope_frontier_action": {"values": ["frontier_wait"]},
                    "effective_action": {
                        "tier": "kernel",
                        "owners": {"python": None, "typescript": None},
                        "values": ["registered_action"],
                        "producers": ["loopx/control_plane/quota/probe.py::produce"],
                        "literal_scan": {"field": "effective_action", "roots": ["loopx"], "suffixes": [".py"]},
                    }
                }
            },
            [source],
        )


def test_bounded_producer_scan_does_not_treat_consumer_reads_as_writes() -> None:
    smoke = runpy.run_path(str(SMOKE))
    source = smoke["SourceFile"](
        "loopx/control_plane/quota/probe.py",
        ".py",
        'def consume(payload):\n    return payload.get("effective_action") == "registered_action"\n',
    )
    assert smoke["_producer_literals"]("effective_action", source) == set()


@pytest.mark.parametrize('suffix, text, expected', [
    ('.py', 'effective_action == Action.NORMAL.value or state == "not_an_action"', set()),
    ('.ts', 'effective_action === Action.NORMAL || state === "not_an_action";', set()),
    ('.py', '# effective_action = "comment"\nexample = \'effective_action == "example"\'', set()),
    ('.ts', '// effective_action = "comment"\nconst example = \'effective_action === "example"\';', set()),
    ('.py', 'effective_action = "run" if state == "condition" else "wait"', {'run', 'wait'}),
    ('.ts', 'effective_action = state === "condition" ? "run" : "wait";', {'run', 'wait'}),
    ('.py', 'str(packet.get("effective_action") or "") in {"run", "wait"}', {'run', 'wait'}),
    ('.ts', '["run", "wait"].includes(packet.effective_action);', {'run', 'wait'}),
    ('.py', 'match packet["effective_action"]:\n    case "run" | "wait": pass', {'run', 'wait'}),
    ('.ts', 'switch (packet.effective_action) { case "run": break; case "wait": break; }', {'run', 'wait'}),
    ('.ts', 'const packet = {["effective_action"]: "run"};', {'run'}),
    ('.ts', 'const packet = {[`effective_action`]: "run"};', {'run'}),
    ('.ts', 'const packet = {[effective_action]: "not_a_static_key"};', set()),
    ('.py', '(p.get("effective_action") and p["state"]) == "eligible"', set()),
    ('.py', '(p.get("effective_action") or p["state"]) == "eligible"', set()),
    ('.ts', '(packet.effective_action || packet.state) === "eligible";', set()),
    ('.ts', '(packet.effective_action ?? "") === "run";', {'run'}),
])
def test_literal_uses_belong_to_the_field_not_neighboring_syntax(suffix, text, expected):
    smoke = runpy.run_path(str(SMOKE))
    source = smoke['SourceFile']('loopx/probe' + suffix, suffix, text)
    assert set(smoke['scan_literals']('effective_action', ['loopx'], [suffix], [source])) == expected


@pytest.mark.parametrize('value', ['normal_run', 'agent_scope_wait'])
@pytest.mark.parametrize('suffix', ['.py', '.ts'])
def test_registered_root_action_literals_still_require_owner_import(value, suffix):
    smoke = runpy.run_path(str(SMOKE))
    source = smoke['SourceFile']('loopx/probe' + suffix, suffix, f'effective_action = "{value}"')
    with pytest.raises(smoke['Drift'], match='import EffectiveAction or AgentScopeFrontierAction'):
        smoke['check_literal_vocabularies'](smoke['load_registry'](), [source])


def test_real_monitor_membership_cannot_revert_to_bare_action_literals():
    smoke = runpy.run_path(str(SMOKE))
    path = 'loopx/control_plane/quota/monitor_poll_commit.ts'
    source = (REPO_ROOT / path).read_text()
    old = 'AgentScopeFrontierAction.AGENT_SCOPE_WAIT, EffectiveAction.MONITOR_QUIET_SKIP'
    assert old in source
    changed = source.replace(old, '"agent_scope_wait", "monitor_quiet_skip"')
    with pytest.raises(smoke['Drift'], match='bare action literals'):
        smoke['check_literal_vocabularies'](smoke['load_registry'](), [smoke['SourceFile'](path, '.ts', changed)])


@pytest.mark.parametrize('name, metadata, selection', [
    ('effective_action', 'call_producers', ['state']),
    ('loop_disposition', 'call_producers', ['state']),
    ('agent_scope_frontier_action', 'return_paths', ['action']),
    ('turn_route', 'return_paths', ['action']),
])
def test_registry_cannot_add_unanchored_output_selectors(name, metadata, selection):
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke['load_registry']())
    registry['vocabularies'][name].setdefault(metadata, {})[
        'loopx/control_plane/quota/decision_summary.py::quota_effective_action'
    ] = selection
    with pytest.raises(smoke['Drift'], match='anchored output evidence exactly'):
        smoke['check_coverage_floor'](registry)


SYNTHETIC_FORK_NAME = "SYNTHETIC_RENAME_SAMPLE_MARKERS"
SYNTHETIC_FORK_MODULES = (
    "loopx/state_projection.py",
    "loopx/control_plane/goals/active_state_metadata.py",
)


def _with_synthetic_fork(smoke, sources):
    """Inject a synthetic multi-value fork into two in-memory modules.

    The rename-laundering limit is a property of the inventory machinery, not
    of whichever real fork happens to be undeclared today. Track A retires real
    forks one by one (PR #4643 retired two of the three this file used to rent),
    so these pins carry their own sample instead: two modules, one name, two
    disagreeing value sets -- exactly what makes a multi-value fork.
    """
    texts = {
        SYNTHETIC_FORK_MODULES[0]: f'\n{SYNTHETIC_FORK_NAME} = ("synthetic_left", "left_two")\n',
        SYNTHETIC_FORK_MODULES[1]: f'\n{SYNTHETIC_FORK_NAME} = ("synthetic_right", "right_two")\n',
    }
    return [
        smoke["SourceFile"](source.path, source.suffix, source.text + texts[source.path])
        if source.path in texts
        else source
        for source in sources
    ]


def _rename_synthetic_side(smoke, sources, path):
    return [
        smoke["SourceFile"](
            source.path,
            source.suffix,
            source.text.replace(SYNTHETIC_FORK_NAME, SYNTHETIC_FORK_NAME + "_RENAMED", 1),
        )
        if source.path == path
        else source
        for source in sources
    ]


def test_bounded_context_scope_excludes_only_declared_multi_value_fork() -> None:
    """A declaration is what removes a fork from the budget; prove it by removal.

    The undeclared count itself is debt population, not a pin -- Track A lowers
    it PR by PR. What must hold is the exclusion: dropping a declaration puts
    its fork back into the semantic budget, exactly one.
    """
    smoke = runpy.run_path(str(SMOKE))
    registry = smoke["load_registry"]()
    sources = smoke["load_sources"](REPO_ROOT)
    inventory = smoke["build_inventory"](REPO_ROOT, sources=sources)
    with_declaration = smoke["check_scope_declarations"](registry, inventory)

    without = copy.deepcopy(registry)
    del without["scope_declarations"]["SOURCE_SURFACES"]
    assert (
        smoke["check_scope_declarations"](without, inventory) == with_declaration + 1
    ), "a declared fork is excluded from the semantic budget only while declared"


def test_renaming_one_side_of_a_fork_launders_the_semantic_budget() -> None:
    """Pin the RFC Section 9 known limit so a future fix cannot be a silent edit.

    Collisions are keyed by name, so renaming one module's definition removes the
    name from ``multi_value_forks`` and lowers the semantic budget by one while
    the drift stays in the tree. This test asserts the limit as it is documented,
    not as it should be: if a change makes renaming refuse to lower the budget,
    this test must fail so the RFC's known-limits section is updated with it.
    """
    smoke = runpy.run_path(str(SMOKE))
    registry = smoke["load_registry"]()
    plain = smoke["load_sources"](REPO_ROOT)
    base = smoke["check_scope_declarations"](registry, smoke["build_inventory"](REPO_ROOT, sources=plain))

    forked = _with_synthetic_fork(smoke, plain)
    before = smoke["build_inventory"](REPO_ROOT, sources=forked)
    assert smoke["check_scope_declarations"](registry, before) == base + 1

    renamed = _rename_synthetic_side(smoke, forked, SYNTHETIC_FORK_MODULES[0])
    after = smoke["build_inventory"](REPO_ROOT, sources=renamed)
    assert smoke["check_scope_declarations"](registry, after) == base, (
        "a rename no longer lowers the semantic budget; the RFC known-limits entry "
        "('Renames launder a collision') is now stale and must be revised"
    )
    assert after["summary"]["multi_value_forks"] == before["summary"]["multi_value_forks"] - 1


def test_divergent_value_sets_lists_the_names_a_rename_would_hide() -> None:
    """The reviewer-facing signal for the laundering limit above.

    ``merge_candidate_groups`` groups *different* names with *identical* value
    sets, so it cannot see a fork at all -- a fork is one name whose value sets
    disagree. Renaming one side hides the name from both the budget and that
    grouping, so this advisory is keyed by name and still lists the abandoned
    name whenever the surviving definitions disagree.
    """
    from loopx.semantics.inventory import divergent_value_sets

    smoke = runpy.run_path(str(SMOKE))
    sources = smoke["load_sources"](REPO_ROOT)
    inventory = smoke["build_inventory"](REPO_ROOT, sources=_with_synthetic_fork(smoke, sources))
    rows = divergent_value_sets(inventory)
    listed = {row["name"] for row in rows}
    assert SYNTHETIC_FORK_NAME in listed
    assert {row["value_sets"] for row in rows if row["name"] == SYNTHETIC_FORK_NAME} == {2}
    # The one real undeclared fork that survives PR #4643; when Track A retires
    # it, this assertion retires with it. Until then the advisory must name it.
    assert "RAW_MATERIAL_KEY_HINTS" in listed

    # The advisory is not a budget input: it must not appear in the committed
    # inventory, which stays the single computed authority.
    assert "divergent_value_sets" not in inventory


def _restate(smoke, sources, path, name, replacement):
    return [
        smoke["SourceFile"](source.path, source.suffix, source.text.replace(name, replacement, 1))
        if source.path == path
        else source
        for source in sources
    ]


def test_rename_visibility_splits_into_three_cases() -> None:
    """The complete boundary, measured, so no reader has to re-derive it.

    Case 1: a partial rename of a **declared** name is rejected outright -- the
    declaration names every defining module and the renamed side no longer
    matches, so the rename cannot lower the budget.

    Cases 2 and 3 are the RFC Section 9 limit, and this test records it as it
    actually behaves rather than as the limit's heading suggests: renaming one
    side leaves the name with a single definition, so it stops being a fork and
    drops out of ``divergent_value_sets`` exactly as it drops out of the budget.
    The advisory makes *surviving* forks visible by name; it does not detect the
    rename. Both cases assert that so a future claim of coverage fails here.
    """
    from loopx.semantics.inventory import divergent_value_sets

    smoke = runpy.run_path(str(SMOKE))
    registry = smoke["load_registry"]()
    plain = smoke["load_sources"](REPO_ROOT)
    forked = _with_synthetic_fork(smoke, plain)

    # Case 1: declared name, one side renamed -> the declaration no longer resolves.
    declared = _restate(smoke, plain, "loopx/global_todos.py", "SOURCE_SURFACES", "GT_SOURCE_SURFACES")
    with pytest.raises(smoke["Drift"], match="every defining module"):
        smoke["check_scope_declarations"](registry, smoke["build_inventory"](REPO_ROOT, sources=declared))

    # Case 2: undeclared name, one side renamed -> gone from the budget AND the advisory.
    partial = smoke["build_inventory"](
        REPO_ROOT, sources=_rename_synthetic_side(smoke, forked, SYNTHETIC_FORK_MODULES[0])
    )
    assert SYNTHETIC_FORK_NAME not in {
        entry["name"] for entry in partial["duplicate_definitions"]["multi_value_forks"]
    }
    assert SYNTHETIC_FORK_NAME not in {row["name"] for row in divergent_value_sets(partial)}

    # Case 3: every side renamed -> also invisible; indistinguishable from an honest rename.
    whole = smoke["build_inventory"](
        REPO_ROOT,
        sources=_rename_synthetic_side(
            smoke, _rename_synthetic_side(smoke, forked, SYNTHETIC_FORK_MODULES[0]), SYNTHETIC_FORK_MODULES[1]
        ),
    )
    assert SYNTHETIC_FORK_NAME not in {
        entry["name"] for entry in whole["duplicate_definitions"]["multi_value_forks"]
    }
    assert SYNTHETIC_FORK_NAME not in {row["name"] for row in divergent_value_sets(whole)}

    # The surviving forks are what the advisory does list, by name.
    assert "RAW_MATERIAL_KEY_HINTS" in {row["name"] for row in divergent_value_sets(partial)}


def test_bounded_context_scope_requires_every_distinct_defining_module() -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    registry["scope_declarations"]["SOURCE_SURFACES"]["contexts"] = registry["scope_declarations"]["SOURCE_SURFACES"]["contexts"][:-1]
    sources = smoke["load_sources"](REPO_ROOT)
    inventory = smoke["build_inventory"](REPO_ROOT, sources=sources)
    with pytest.raises(smoke["Drift"], match="every defining module"):
        smoke["check_scope_declarations"](registry, inventory)


@pytest.mark.parametrize("text, expected", [
    ('payload["effective_action"] = "new_action"', {"new_action"}),
    ('route.effective_action: str = "new_action"', {"new_action"}),
    ('Packet(effective_action="new_action")', {"new_action"}),
    ('payload = {"effective_action":\n "left" if flag == "condition" else "right"}', {"left", "right"}),
    ('effective_action = payload.get("effective_action", "fallback")', set()),
    ('effective_action == "not_produced"', set()),
    ('# effective_action = "comment"', set()),
    ('example = \'effective_action = "example"\'', set()),
])
def test_python_production_forms_separate_result_from_context(text, expected) -> None:
    smoke = runpy.run_path(str(SMOKE))
    source = smoke["SourceFile"]("loopx/control_plane/quota/probe.py", ".py", text)
    assert smoke["_producer_literals"]("effective_action", source) == expected


def test_return_producer_scope_cannot_be_removed_from_registry():
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke['load_registry']())
    registry['vocabularies']['effective_action']['return_producers'] = []
    with pytest.raises(smoke['Drift'], match='RETURN_PRODUCER_ANCHOR'):
        smoke['check_coverage_floor'](registry)


@pytest.mark.parametrize('name', ['turn_route', 'loop_disposition', 'agent_scope_frontier_action'])
def test_registered_kernel_producer_coverage_cannot_be_removed(name):
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke['load_registry']())
    registry['vocabularies'][name].pop('producers')
    with pytest.raises(smoke['Drift'], match='PRODUCER_VOCABULARY_ANCHOR'):
        smoke['check_coverage_floor'](registry)


@pytest.mark.parametrize('metadata,name', [
    ('call_producers', 'loop_disposition'),
    ('call_producers', 'agent_scope_frontier_action'),
    ('call_producers', 'turn_result_kind'),
    ('return_paths', 'turn_route'),
    ('return_paths', 'turn_result_kind'),
])
def test_explicit_output_evidence_cannot_be_removed_or_redirected(metadata, name):
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke['load_registry']())
    registry['vocabularies'][name][metadata] = {}
    with pytest.raises(smoke['Drift'], match='anchored output evidence'):
        smoke['check_coverage_floor'](registry)


@pytest.mark.parametrize('name', [
    'turn_result_kind',
    'turn_route',
    'loop_disposition',
    'agent_scope_frontier_action',
])
def test_turn_kernel_values_each_carry_a_note(name):
    """A Turn kernel value with no note sends every reader back to the code.

    The registry already settles who owns a vocabulary and which values are
    legal. These four decide what one Turn did and what the outer loop does
    next, so a new value here is a new control-flow case. Requiring the note in
    the same diff keeps that case reviewable instead of leaving it as a bare
    token whose meaning lives only in the controller rules.
    """
    smoke = runpy.run_path(str(SMOKE))
    vocabulary = smoke['load_registry']()['vocabularies'][name]
    notes = vocabulary.get('value_notes', {})
    undocumented = [
        value for value in vocabulary['values']
        if not str(notes.get(value) or '').strip()
    ]
    assert not undocumented, f'{name}: values with no value_notes entry: {undocumented}'


@pytest.mark.parametrize("legacy_report", [None, "not even JSON"])
def test_live_inventory_ignores_missing_or_stale_reports(tmp_path, monkeypatch, legacy_report):
    smoke = runpy.run_path(str(SMOKE))
    registry = smoke["load_registry"]()
    sources = smoke["load_sources"](REPO_ROOT)
    if legacy_report is not None:
        path = tmp_path / "loopx/semantics/inventory_v0.json"
        path.parent.mkdir(parents=True)
        path.write_text(legacy_report)
    monkeypatch.setitem(smoke["check_inventory"].__globals__, "REPO_ROOT", tmp_path)
    inventory, _ = smoke["check_inventory"](registry, sources)
    assert inventory["summary"]["source_files"] == len(sources)
    # A newly observed duplicate must still fail; an old or missing report cannot hide it.
    duplicate = [smoke["SourceFile"](f"loopx/q9_{name}.py", ".py", 'Q9_DUPLICATE = "same"\n')
                 for name in ("first", "second")]
    with pytest.raises(smoke["Drift"], match="same_runtime_forks grew"):
        smoke["check_inventory"](registry, sources + duplicate)


@pytest.mark.parametrize('name', ['effective_action', 'lease_action'])
def test_remaining_kernel_values_each_carry_a_note(name):
    """The two kernel vocabularies that are not Turn control flow still need notes.

    ``effective_action`` is the overloaded should-run slot M1 is due to split, so
    a value here is only legible once the registry says which condition produces
    it; ``lease_action`` is legacy and every value is compatibility-only, which
    is exactly the kind of disposition a reader cannot infer from the name. The
    note is required in the diff that adds a value, not afterwards.
    """
    smoke = runpy.run_path(str(SMOKE))
    vocabulary = smoke['load_registry']()['vocabularies'][name]
    notes = vocabulary.get('value_notes', {})
    undocumented = [
        value for value in vocabulary['values']
        if not str(notes.get(value) or '').strip()
    ]
    assert not undocumented, f'{name}: values with no value_notes entry: {undocumented}'


def _invariant(registry: dict, invariant_id: str) -> dict:
    return next(item for item in registry["formal_model"]["invariants"] if item["id"] == invariant_id)


def test_f1_f2_domain_names_exactly_the_vocabularies_the_producer_check_walks() -> None:
    """The declared domain must be the set ``check_producers`` really visits.

    F1 and F2 were unconditional claims over every vocabulary while the check
    skipped 20 of 26. This ties the quantifier in the statement to the predicate
    the scanner uses, so widening one without the other fails.
    """
    smoke = runpy.run_path(str(SMOKE))
    registry = smoke["load_registry"]()
    vocabularies = registry["vocabularies"]
    walked = {name for name, entry in vocabularies.items() if "producers" in entry}
    # The domain was the kernel tier while that happened to be the set the
    # check walked. It is no longer: a cross-runtime vocabulary carrying
    # executed production evidence is walked too, so the selector names the
    # predicate rather than a tier it no longer matches.
    assert walked >= {name for name, entry in vocabularies.items() if entry["tier"] == "kernel"}
    skipped = {entry["tier"] for name, entry in vocabularies.items() if name not in walked}
    assert skipped == {"cross_runtime"}
    for invariant_id in ("F1_producer_closedness", "F2_canonical_value_liveness"):
        domain = _invariant(registry, invariant_id)["domain"]
        assert domain["quantifies_over"] == "vocabularies[producers].producers"
        assert domain["verified"] == len(walked)
        assert domain["registered"] == len(vocabularies)
        assert domain["evidence_bound"] == "producer_scan_reach"


@pytest.mark.parametrize("denial", [
    " The cross_runtime tier declares no producers.",
    " No vocabulary in the cross_runtime tier has any producer.",
])
def test_canonical_producer_statement_rejects_contradictory_paraphrases(denial: str) -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    _invariant(registry, "F2_canonical_value_liveness")["statement"] += denial
    with pytest.raises(smoke["Drift"], match="canonical producer-domain projection"):
        smoke["check_formal_model"](registry["formal_model"], registry)


def test_generated_domain_accepts_true_kernel_comparison() -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = smoke["load_registry"]()
    model = registry["formal_model"]
    assert "Kernel(V) ⊆ Producers(V)." in model["universes"]["vocabularies"]
    smoke["check_formal_model"](model, registry)
    domain = smoke["ProducerDomain"].from_registry(registry)
    assert domain.kernel < domain.walked
    assert domain.walked - domain.kernel == {"settlement_binding_kind"}
    assert domain.outside_by_tier == (("cross_runtime", 19),)


def test_canonical_domain_rejects_old_kernel_only_universe() -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    registry["formal_model"]["universes"]["vocabularies"] = (
        "V: registered vocabulary identifiers; Kernel(V) is the only producer domain"
    )
    with pytest.raises(smoke["Drift"], match="canonical producer-domain projection"):
        smoke["check_formal_model"](registry["formal_model"], registry)


def test_domain_membership_change_requires_regenerated_prose() -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    # A membership change must not leave yesterday's tier/count claim green,
    # even if the independent numeric domain entries were already updated.
    registry["vocabularies"]["settlement_binding_kind"].pop("producers")
    for invariant_id in ("F1_producer_closedness", "F2_canonical_value_liveness"):
        _invariant(registry, invariant_id)["domain"]["verified"] = 6
    with pytest.raises(smoke["Drift"], match="canonical producer-domain projection"):
        smoke["check_formal_model"](registry["formal_model"], registry)
    projected = smoke["producer_domain_prose"](registry)
    assert "20 cross_runtime" in projected["F1_producer_closedness.statement"]
    assert "6 kernel and 0 outside" in projected["universes.vocabularies"]


@pytest.mark.parametrize("invariant_id", sorted({
    "F1_producer_closedness",
    "F2_canonical_value_liveness",
    "F3_consumer_domain_closedness",
    "F4_scope_separation",
    "F5_projection_totality",
    "F6_persistence_version_compatibility",
}))
def test_every_invariant_must_declare_a_domain(invariant_id: str) -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    _invariant(registry, invariant_id).pop("domain")
    with pytest.raises(smoke["Drift"], match="invalid shape"):
        smoke["check_formal_model"](registry["formal_model"], registry)


@pytest.mark.parametrize("invariant_id, field, value", [
    ("F1_producer_closedness", "verified", 26),
    ("F1_producer_closedness", "verified", 5),
    ("F1_producer_closedness", "registered", 6),
    ("F2_canonical_value_liveness", "verified", 26),
    ("F4_scope_separation", "verified", 1),
    ("F5_projection_totality", "registered", 9),
])
def test_declared_domain_size_must_match_the_derived_one(invariant_id, field, value) -> None:
    """A domain size is counted from the registry, never taken on trust."""
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    _invariant(registry, invariant_id)["domain"][field] = value
    with pytest.raises(smoke["Drift"], match=f"formal invariant {invariant_id}"):
        smoke["check_formal_model"](registry["formal_model"], registry)


@pytest.mark.parametrize("invariant_id", ["F3_consumer_domain_closedness", "F6_persistence_version_compatibility"])
def test_an_unenforced_invariant_cannot_claim_verified_members(invariant_id: str) -> None:
    """Advisory and unproved stages walk nothing; the count has to say so."""
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    _invariant(registry, invariant_id)["domain"]["verified"] = 1
    with pytest.raises(smoke["Drift"], match="an unenforced stage walks nothing"):
        smoke["check_formal_model"](registry["formal_model"], registry)


def test_an_enforced_invariant_cannot_declare_an_empty_domain(monkeypatch) -> None:
    """An enforced stage over an empty set is vacuous, not proven."""
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    # The anchor is what normally forbids this pairing; move it so the emptiness
    # rule itself is the one under test.
    monkeypatch.setitem(
        smoke["check_invariant_domain"].__globals__["FORMAL_DOMAIN_ANCHOR"],
        "F4_scope_separation", ("persists_edges[*]", "declared_defining_modules"),
    )
    domain = _invariant(registry, "F4_scope_separation")["domain"]
    domain["quantifies_over"] = "persists_edges[*]"
    domain["verified"] = 0
    domain["registered"] = 0
    with pytest.raises(smoke["Drift"], match="over an empty domain"):
        smoke["check_formal_model"](registry["formal_model"], registry)


@pytest.mark.parametrize("invariant_id, selector", [
    # Every swap below names a selector the code knows, so only the anchor stops
    # an invariant from widening the domain its statement quantifies over.
    ("F1_producer_closedness", "vocabularies[*]"),
    ("F2_canonical_value_liveness", "vocabularies[*]"),
    ("F4_scope_separation", "projections[*]"),
    ("F5_projection_totality", "scope_declarations[*].contexts"),
])
def test_an_invariant_cannot_widen_its_own_domain_by_data_edit(invariant_id, selector) -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    _invariant(registry, invariant_id)["domain"]["quantifies_over"] = selector
    with pytest.raises(smoke["Drift"], match="FORMAL_DOMAIN_ANCHOR pins"):
        smoke["check_formal_model"](registry["formal_model"], registry)


def test_every_invariant_is_anchored_to_a_domain() -> None:
    smoke = runpy.run_path(str(SMOKE))
    anchor = smoke["FORMAL_DOMAIN_ANCHOR"]
    assert set(anchor) == smoke["FORMAL_INVARIANTS"]
    for selector, bound in anchor.values():
        assert selector in smoke["FORMAL_DOMAIN_SELECTORS"]
        assert bound in smoke["FORMAL_EVIDENCE_BOUNDS"]


def test_a_domain_selector_cannot_be_invented_by_registry_data() -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    _invariant(registry, "F1_producer_closedness")["domain"]["quantifies_over"] = "vocabularies[everything]"
    with pytest.raises(smoke["Drift"], match="selectors are code owned"):
        smoke["check_formal_model"](registry["formal_model"], registry)


@pytest.mark.parametrize("invariant_id, bound", [
    ("F1_producer_closedness", "unmodelled"),
    ("F1_producer_closedness", "inventory_only"),
    ("F3_consumer_domain_closedness", "producer_scan_reach"),
    ("F5_projection_totality", "unmodelled"),
])
def test_an_evidence_bound_cannot_contradict_the_enforcement_stage(monkeypatch, invariant_id, bound) -> None:
    """A bound that says nothing was walked cannot sit on an enforced stage, and
    a walked-evidence bound cannot sit on an advisory or unproved one."""
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    anchor = smoke["check_invariant_domain"].__globals__["FORMAL_DOMAIN_ANCHOR"]
    monkeypatch.setitem(anchor, invariant_id, (anchor[invariant_id][0], bound))
    _invariant(registry, invariant_id)["domain"]["evidence_bound"] = bound
    with pytest.raises(smoke["Drift"], match="evidence bound"):
        smoke["check_formal_model"](registry["formal_model"], registry)


def test_an_unknown_evidence_bound_is_rejected() -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    _invariant(registry, "F1_producer_closedness")["domain"]["evidence_bound"] = "trust_me"
    with pytest.raises(smoke["Drift"], match="unknown evidence bound"):
        smoke["check_formal_model"](registry["formal_model"], registry)


@pytest.mark.parametrize("extra", [{"note": "why"}, {}])
def test_domain_key_set_is_closed(extra: dict) -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    domain = _invariant(registry, "F5_projection_totality")["domain"]
    if extra:
        domain.update(extra)
    else:
        domain.pop("evidence_bound")
    with pytest.raises(smoke["Drift"], match="domain keys must be exactly"):
        smoke["check_formal_model"](registry["formal_model"], registry)


def test_adding_a_vocabulary_forces_the_declared_domain_to_move() -> None:
    """The population count is derived, so a registry that grows fails until the
    invariant's domain admits it. This is the I6 same-diff rule for a claim."""
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    registry["vocabularies"]["probe_vocabulary"] = {
        "meaning": "probe", "tier": "cross_runtime", "status": "canonical",
        "owners": {"python": None, "typescript": None}, "values": ["probe_value"],
    }
    with pytest.raises(smoke["Drift"], match="the registry holds 27"):
        smoke["check_formal_model"](registry["formal_model"], registry)


def test_producer_scan_reach_is_measured_not_pinned() -> None:
    """F1/F2's evidence bound is a file reach; the report derives it each run."""
    smoke = runpy.run_path(str(SMOKE))
    sources = smoke["load_sources"](REPO_ROOT)
    scanned, tracked = smoke["producer_scan_reach"](sources)
    assert 0 < scanned < tracked == len(sources)
    roots = smoke["PRODUCER_ROOTS"]
    files = smoke["PRODUCER_FILES"]
    assert scanned == sum(
        1 for source in sources
        if source.path in files or any(source.path.startswith(root + "/") for root in roots)
    )


def test_permanently_unresolvable_blockers_are_counted_apart() -> None:
    """Two blocker labels can never become evidence; the total says how many."""
    smoke = runpy.run_path(str(SMOKE))
    sites = [
        "loopx/a.py::f:1 [argument_name_only]",
        "loopx/a.py::f:2 [annotation_only]",
        "loopx/a.py::f:3 [call_result]",
        "loopx/a.py::f:4 [typescript_dynamic]",
    ]
    assert smoke["count_permanently_unresolvable"](sites) == 2
    assert set(smoke["PERMANENTLY_UNRESOLVABLE_BLOCKERS"]) == {"annotation_only", "argument_name_only"}


def test_inventory_report_discloses_budget_slack(monkeypatch):
    """Budget slack (budget above the measured value) must be disclosed.

    The guard only fails on overflow (measured > budget), so a merge that
    reverts a tightened budget passes silently unless the report line shows
    the reopened headroom.  See the same_runtime_forks hunk straddle when
    merging two budget-tightening branches.
    """
    import copy

    smoke = runpy.run_path(str(SMOKE))
    registry = smoke["load_registry"]()
    sources = smoke["load_sources"](REPO_ROOT)
    # Pinned budgets disclose no slack for the counters this change locks
    # (the multi_value_twins slack belongs to the multi-value single-source
    # batch, not this one).
    _, pinned = smoke["check_inventory"](registry, sources)
    assert "slack=conflicting_values" not in pinned, pinned
    assert "slack=conflicting_definitions" not in pinned, pinned
    # A two-file budget revert (the merge-trap shape: registry and anchor
    # move back together, so the equality anchor stays satisfied) must show
    # up as disclosed slack instead of passing silently.
    widened = copy.deepcopy(registry)
    widened["inventory_ratchets"]["conflicting_values"] += 2
    monkeypatch.setitem(
        smoke["BUDGET_ANCHOR"], "conflicting_values", widened["inventory_ratchets"]["conflicting_values"],
    )
    _, line = smoke["check_inventory"](widened, sources)
    assert "slack=conflicting_values=2" in line, line


def _retirement_registry(python_surface: int, typescript_surface: int) -> dict:
    """A one-field ledger shaped like the real one, for the B3 budget checks."""
    return {"retirement_ledger": {"should_run_legacy_decision_fields": {"fields": {
        "protocol_action_packet": {
            "python_module_budget": 5,
            "typescript_module_budget": 2,
            "python_migration_surface": python_surface,
            "typescript_migration_surface": typescript_surface,
        },
    }}}}


def test_a_new_reader_of_a_legacy_field_exceeds_its_migration_surface() -> None:
    smoke = runpy.run_path(str(SMOKE))
    readers = [
        smoke["SourceFile"](f"loopx/probe_{index}.py", ".py", 'value = payload["protocol_action_packet"]')
        for index in range(6)
    ]
    with pytest.raises(smoke["Drift"], match="modules migrated"):
        smoke["check_reader_metric"](_retirement_registry(5, 2), readers)


def test_migration_surface_budget_cannot_move_without_its_anchor() -> None:
    smoke = runpy.run_path(str(SMOKE))
    with pytest.raises(smoke["Drift"], match="MIGRATION_SURFACE_ANCHOR"):
        smoke["check_reader_metric"](_retirement_registry(6, 2), [])


def test_the_reader_metric_reports_all_five_facts_beside_the_role_labels() -> None:
    smoke = runpy.run_path(str(SMOKE))
    sources = [smoke["SourceFile"](
        "loopx/projection.py", ".py",
        'payload["protocol_action_packet"] = payload.get("protocol_action_packet")',
    )]
    _, detail = smoke["check_reader_metric"](_retirement_registry(5, 2), sources)
    line = next(item for item in detail if item.startswith("protocol_action_packet.py"))
    assert "reader=1 writer=0" in line, line
    assert "reads=1 writes=1" in line, (
        "one module both reads and writes the field; the role label keeps only the first, "
        f"so the overlapping facts have to be reported beside it: {line}"
    )
    for label in ("binds", "unresolved_use", "mention_only"):
        assert f"{label}=" in line, f"{label} missing from the reported facts: {line}"


def test_an_unresolved_name_carrier_is_inside_the_migration_surface() -> None:
    smoke = runpy.run_path(str(SMOKE))
    sources = [smoke["SourceFile"](
        "loopx/carrier.py", ".py",
        'LEGACY = ["protocol_action_packet"]\nfor name in LEGACY:\n    emit(name)\n',
    )]
    _, detail = smoke["check_reader_metric"](_retirement_registry(5, 2), sources)
    line = next(item for item in detail if item.startswith("protocol_action_packet.py"))
    assert "surface=1/5" in line and "unresolved=1" in line, (
        "the field name is in this module as data; that is work to investigate before the "
        f"field can go, so it belongs to the surface: {line}"
    )


def test_prose_and_same_prefix_identifiers_do_not_consume_the_migration_surface() -> None:
    smoke = runpy.run_path(str(SMOKE))
    sources = [
        smoke["SourceFile"]("loopx/prose.py", ".py", '"""protocol_action_packet is published downstream."""'),
        smoke["SourceFile"]("loopx/prefix.py", ".py", 'value = payload["protocol_action_packet_v2"]'),
    ]
    report, detail = smoke["check_reader_metric"](_retirement_registry(5, 2), sources)
    assert any("surface=0/5" in line and "mention=1" in line for line in detail), detail
    assert any("dynamic_mapping_key_sites=0" in line for line in report), report

def _with_unexecuted_projection(registry: dict) -> dict:
    registry = copy.deepcopy(registry)
    registry["projections"]["unexecuted_probe"] = {
        "meaning": "A projection whose owner module and function do not exist.",
        "owner": "loopx/semantics/does_not_exist.py::no_such_function",
        "mapping": {"a": "b"},
    }
    return registry


def test_an_unexecuted_projection_cannot_count_itself_as_verified() -> None:
    """F5 claims the evidence bound ``executable_owner_function``.

    Before this, ``verified`` was ``len(registry["projections"])`` -- the
    registry's own row count standing in for evidence -- while
    ``check_projections`` only ever imported one hardcoded projection. Adding a
    projection whose owner function does not exist anywhere in the tree passed
    the full smoke and reported ``F5:2/2``. A declaration was counting as its
    own proof.
    """
    smoke = runpy.run_path(str(SMOKE))
    registry = _with_unexecuted_projection(smoke["load_registry"]())
    walked, population = smoke["FORMAL_DOMAIN_SELECTORS"]["projections[*]"](registry)
    assert (walked, population) == (1, 2)


def test_declaring_an_unexecuted_projection_as_verified_fails() -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = _with_unexecuted_projection(smoke["load_registry"]())
    for invariant in registry["formal_model"]["invariants"]:
        if invariant["id"] == "F5_projection_totality":
            invariant["domain"]["registered"] = 2
            invariant["domain"]["verified"] = 2
    with pytest.raises(smoke["Drift"], match="claims 2 verified members"):
        smoke["check_formal_model"](registry["formal_model"], registry)


def test_a_projection_owner_the_check_does_not_run_is_rejected() -> None:
    """The owner string must name the function ``check_projections`` imports.

    Otherwise the registry can point at any module while the check keeps
    exercising the real one, and the invariant credits a function it never ran.
    """
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    registry["projections"]["turn_route_to_loop_disposition"]["owner"] = (
        "loopx/semantics/elsewhere.py::some_other_function"
    )
    with pytest.raises(smoke["Drift"], match="crediting a function it does not run"):
        smoke["check_projections"](registry)


def test_every_executed_projection_must_be_registered() -> None:
    smoke = runpy.run_path(str(SMOKE))
    registry = copy.deepcopy(smoke["load_registry"]())
    del registry["projections"]["turn_route_to_loop_disposition"]
    with pytest.raises(smoke["Drift"], match="is not registered"):
        smoke["check_projections"](registry)
