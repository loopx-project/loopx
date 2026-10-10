"""Independent acceptance of Turn generation, rejection and source adoption."""

from copy import deepcopy
import json
import runpy
import subprocess
import sys

import pytest

from scripts import generate_turn_contract as generator

RESULTS = [
    "validated_progress",
    "validated_completion",
    "repair_required",
    "replan_required",
    "user_action_required",
    "wait",
    "iteration_failed",
    "host_failure",
    "validation_failed",
    "writeback_failed",
    "quota_spend_failed",
    "terminal_closeout_failed",
]
PROJECTION = {
    "ready_for_host": "run_now",
    "capability_action_required": "capability_action_required",
    "repair_required": "repair",
    "replan_required": "replan",
    "user_action_required": "user_action_required",
    "wait": "wait",
    "blocked": "wait",
    "contract_error": None,
}


@pytest.mark.parametrize('name', ['turn_route', 'loop_disposition'])
def test_new_typescript_owner_coverage_cannot_be_dropped(name):
    smoke = runpy.run_path(str(generator.ROOT / 'examples/semantic-vocabulary-drift-smoke.py'))
    registry = deepcopy(smoke['load_registry']())
    registry['vocabularies'][name]['owners']['typescript'] = None
    with pytest.raises(smoke['Drift'], match='owner_symbols'):
        smoke['check_coverage_floor'](registry)


def test_existing_imports_share_generated_identity_and_exact_spelling():
    from loopx.control_plane.turn_driver import transaction, driver, loop_controller
    from loopx.control_plane.turn_driver import turn_contract_generated as shared

    assert transaction.LoopXTurnResultKind is shared.LoopXTurnResultKind
    assert driver.LoopXTurnRoute is shared.LoopXTurnRoute
    assert loop_controller.LoopDisposition is shared.LoopDisposition
    assert [x.value for x in transaction.LoopXTurnResultKind] == RESULTS
    for route, disposition in PROJECTION.items():
        if disposition is None:
            with pytest.raises(ValueError, match="envelope contract"):
                shared.project_turn_route(shared.LoopXTurnRoute(route))
        else:
            assert (
                shared.project_turn_route(shared.LoopXTurnRoute(route)).value
                == disposition
            )


@pytest.mark.parametrize(
    "defect",
    [
        "missing_route",
        "unknown_output",
        "alias",
        "unknown_partition",
        "unknown_check",
        "duplicate_rule",
    ],
)
def test_invalid_contract_is_rejected_before_generation(defect):
    contract = deepcopy(generator.read_contract())
    if defect == "missing_route":
        del contract["route_projection"]["blocked"]
    elif defect == "unknown_output":
        contract["rules"][-1]["disposition"] = "not_registered"
    elif defect == "alias":
        contract["vocabularies"]["turn_result_kind"]["ALIAS"] = "wait"
    elif defect == "unknown_partition":
        contract["rules"][-1]["when"]["invented"] = [True]
    elif defect == "unknown_check":
        contract["rules"][0]["check"] = "invented"
    else:
        contract["rules"].append(deepcopy(contract["rules"][-1]))
    with pytest.raises(ValueError):
        generator.validate_contract(contract)


def test_generator_check_is_deterministic_and_does_not_repair(tmp_path, monkeypatch):
    expected = generator.build_artifacts()
    assert expected == generator.build_artifacts()
    path = tmp_path / "binding.py"
    path.write_text("stale\n")
    monkeypatch.setattr(generator, "build_artifacts", lambda: {path: "new\n"})
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generator", "--check"])
    assert generator.main() == 1
    assert path.read_text() == "stale\n"


def test_typescript_settlement_reexports_generated_result_set():
    result = subprocess.run(
        [
            "node",
            "--no-warnings",
            "--experimental-strip-types",
            "--input-type=module",
            "-e",
            'import {TURN_RESULT_KINDS as a} from "./loopx/control_plane/turn_driver/settlement.ts";'
            'import {TURN_RESULT_KINDS as b, ROUTE_TO_DISPOSITION} from "./loopx/control_plane/turn_driver/turn_contract_generated.ts";'
            "console.log(JSON.stringify({same:a===b,values:a,projection:ROUTE_TO_DISPOSITION}));",
        ],
        cwd=generator.ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    actual = json.loads(result.stdout)
    assert actual == {"same": True, "values": RESULTS, "projection": PROJECTION}


def test_projection_verifier_rejects_an_exception_on_a_mapped_route(monkeypatch):
    from loopx.control_plane.turn_driver import turn_contract_generated as shared

    smoke = runpy.run_path(
        str(generator.ROOT / "examples/semantic-vocabulary-drift-smoke.py")
    )
    registry = smoke["load_registry"]()
    smoke["check_projections"](registry)
    original = shared.project_turn_route

    def broken(route):
        if route.value == "ready_for_host":
            raise ValueError("wrong refusal")
        return original(route)

    monkeypatch.setattr(shared, "project_turn_route", broken)
    with pytest.raises(smoke["Drift"]):
        smoke["check_projections"](registry)


def test_result_kind_cannot_determine_the_controller_output_alone():
    from loopx.semantics.turn_contract_witness import controller_input
    from loopx.control_plane.turn_driver.loop_controller import decide_loop_disposition

    assert [
        decide_loop_disposition(**controller_input(case, route))["disposition"]
        for case, route in [
            ("validated_progress", "ready"),
            ("exhausted_progress", "ready"),
            ("validated_progress", "user"),
            ("validated_progress", "capability"),
        ]
    ] == ["run_now", "replan", "user_action_required", "capability_action_required"]


def test_controller_and_projection_witnesses_cover_live_dispositions():
    from loopx.semantics.turn_contract_witness import (
        probe_controller_production,
        probe_projection_production,
    )

    rows = probe_controller_production() + probe_projection_production()
    assert set().union(*(r.values for r in rows)) == {
        "run_now",
        "capability_action_required",
        "wait",
        "stop",
        "user_action_required",
        "repair",
        "replan",
        "terminal",
    }
    assert all(r.form == "input_witness" for r in rows)


@pytest.mark.parametrize(
    "defect",
    ["missing_rule", "unregistered_output", "disconnected_table", "constant_emitter"],
)
def test_controller_production_rejects_contract_or_wiring_regressions(
    monkeypatch, defect
):
    from loopx.control_plane.turn_driver import loop_controller as c
    from loopx.semantics.turn_contract_witness import probe_controller_production

    table = deepcopy(c._LOOP_CONTROLLER_CONTRACT)
    if defect == "missing_rule":
        table["rules"] = [
            r for r in table["rules"] if r["id"] != "receipt_iteration_failed"
        ]
    elif defect == "unregistered_output":
        table["rules"][-1]["disposition"] = "unknown"
    if defect == "constant_emitter":
        monkeypatch.setattr(
            c, "decide_loop_disposition", lambda **kw: {"disposition": "run_now"}
        )
    else:
        monkeypatch.setattr(c, "_LOOP_CONTROLLER_CONTRACT", table)
    with pytest.raises(ValueError):
        probe_controller_production()


def test_generated_provenance_is_not_a_filename_exception(tmp_path, monkeypatch):
    path = tmp_path / "turn_contract_generated.ts"
    path.write_text("hand maintained")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(
        generator, "build_artifacts", lambda: {path: "generated content"}
    )
    with pytest.raises(ValueError, match="stale generated"):
        generator.verified_generated_paths()


@pytest.mark.parametrize("defect", ["remove_stop", "terminal_becomes_repair"])
def test_live_witness_fails_even_if_changed_table_is_freshly_generated(
    monkeypatch, defect
):
    from loopx.control_plane.turn_driver import turn_contract_generated as shared
    from loopx.semantics.turn_contract_witness import probe_controller_production

    rules = deepcopy(shared.TURN_CONTROLLER_CONTRACT["rules"])
    if defect == "remove_stop":
        rules = [row for row in rules if row["id"] != "receipt_iteration_failed"]
    else:
        next(row for row in rules if row["id"] == "initial_terminal")["disposition"] = (
            "repair"
        )
    monkeypatch.setitem(shared.TURN_CONTROLLER_CONTRACT, "rules", rules)
    # Isolate the liveness obligation from the separately tested byte-provenance
    # gate: model a freshly regenerated but semantically wrong contract. The
    # real controller still interprets the changed rules and the oracle stays
    # the independent input/output cases, not the changed data.
    monkeypatch.setattr(generator, "verified_generated_paths", lambda: frozenset())
    monkeypatch.setattr(
        generator, "read_contract", lambda: shared.TURN_CONTROLLER_CONTRACT
    )
    with pytest.raises(ValueError):
        probe_controller_production()


def test_new_independent_twin_cannot_hide_behind_generated_pair(monkeypatch):
    import re

    smoke = runpy.run_path(
        str(generator.ROOT / "examples/semantic-vocabulary-drift-smoke.py")
    )
    registry = smoke["load_registry"]()
    report = smoke["check_dual_runtime_twins"](registry)
    counts = re.fullmatch(
        r"twins_raw=(\d+) generated_verified=(\d+) independently_maintained=(\d+)/(\d+)",
        report,
    )
    assert counts is not None
    raw, generated, maintained, budget = map(int, counts.groups())
    # Require both reviewed generators while allowing later verified pairs.
    from scripts.generate_semantic_bindings import verified_generated_paths as semantic_generated_paths

    assert "loopx/control_plane/turn_driver/turn_contract_generated.py" in generator.verified_generated_paths()
    assert "loopx/control_plane/content_digest.py" in semantic_generated_paths()
    assert generated >= 2
    assert raw == maintained + generated
    from loopx.semantics.inventory import SourceFile

    target = smoke["check_dual_runtime_twins"].__globals__
    original = target["load_sources"]
    monkeypatch.setitem(
        target,
        "load_sources",
        lambda *a: (
            original(*a)
            + [
                SourceFile(f"loopx/control_plane/unreviewed_{index}{suffix}", suffix, "")
                for index in range(budget - maintained + 1)
                for suffix in (".py", ".ts")
            ]
        ),
    )
    with pytest.raises(smoke["Drift"], match="independently maintained"):
        smoke["check_dual_runtime_twins"](registry)
