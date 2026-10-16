"""Scoped pack-to-process journey with actual files and a disposable child."""

from __future__ import annotations

import hashlib
import json
import sys

import pytest

from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import (
    PackUnavailable,
    cancel_pack,
    discover,
    execute_pack,
    inspect_pack,
    read_citation,
    register_pack,
)


@pytest.fixture
def pack(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    evidence = b"Prior question\nThe native measurement did not improve utility.\n"
    (sources / "result.txt").write_bytes(evidence)
    data = {
        "schema_version": "trajectory_assessment_input_v1",
        "scope": dict(
            goal_id="solver",
            case_id="case",
            run_id="run",
            revision="fixed",
            window="one episode",
        ),
        "objective": "Improve native utility",
        "selection": dict(
            rule="synthetic complete population", population_episodes=1, exhaustive=True
        ),
        "sources": [
            dict(
                id="source",
                artifact="result.txt",
                artifact_sha256=hashlib.sha256(evidence).hexdigest(),
                first_line=2,
                text=evidence.decode().split("\n")[1],
            )
        ],
        "episodes": [
            dict(
                id="episode",
                question="Does it help?",
                source_ids=["source"],
                eligible_metrics=["benefit_test"],
            )
        ],
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(data))
    scope = dict(runtime_root=tmp_path / "runtime", owner_goal_id="research-owner")
    registered = register_pack(
        **scope, input_path=input_path, source_root=sources, title="Case · one episode"
    )
    operation = {
        **scope,
        "pack_id": registered["pack_id"],
        "analysis_key": registered["analysis_key"],
    }
    output = {
        "assessments": [
            dict(
                episode_id="episode",
                metric="benefit_test",
                verdict="yes",
                reason="A negative native test still tests benefit.",
                citations=[dict(source_id="source", first_line=2, last_line=2)],
            )
        ],
        "limitations": ["Synthetic process; not model quality evidence"],
    }
    executable = tmp_path / "codex-fixture"
    executable.write_text(f"""#!{sys.executable}
import json,sys
from pathlib import Path
sys.stdin.read()
Path(sys.argv[sys.argv.index('-o')+1]).write_text({json.dumps(json.dumps(output))})
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':10,'output_tokens':2}}}}))
""")
    executable.chmod(0o700)
    return scope, operation, registered, input_path, sources, str(executable)


def test_scope_discovery_execute_read_citation_and_changed_source_history(pack):
    scope, operation, registered, _, sources, provider = pack
    listing = discover(**scope)
    assert listing["default_pack_id"] == registered["pack_id"]
    assert (
        listing["packs"][0]["scope"]["goal_id"] == "solver"
    )  # never relabel owner as analyzed solver
    assert "sources" not in listing["packs"][0]
    before = inspect_pack(**scope)
    assert before["job"] is None and before["result"] is None
    assert not list(
        (scope["runtime_root"] / "benchmark" / "trajectory-assessments").glob(
            "*/jobs/*"
        )
    )
    assert execute_pack(**operation, codex=provider)["status"] == "succeeded"
    view = inspect_pack(**scope)
    assert view["result"]["metrics"][1]["yes"] == 1
    assert "directory" not in view["job"] and "process_id" not in view["job"]
    assert execute_pack(**operation, codex="must-not-run")["reused"] is True
    citation = read_citation(**operation, source_id="source", first_line=2, last_line=2)
    assert citation["text"] == "The native measurement did not improve utility."
    with pytest.raises(PackUnavailable, match="citation_not_in_result"):
        read_citation(**operation, source_id="source", first_line=1, last_line=2)
    (sources / "result.txt").write_text("changed")
    assert discover(**scope)["packs"][0]["reason_code"] == "source_changed"
    with pytest.raises(PackUnavailable, match="source_changed"):
        execute_pack(**operation, codex="must-not-run")
    historical = inspect_pack(**scope)
    assert (
        historical["applicability"] == "historical"
        and historical["result"] == view["result"]
    )
    assert (
        read_citation(**operation, source_id="source", first_line=2, last_line=2)[
            "text"
        ]
        == citation["text"]
    )


def test_owner_isolation_unknown_ids_and_keys_are_rejected(pack):
    scope, operation, _, _, _, _ = pack
    other = {**scope, "owner_goal_id": "other-owner"}
    assert discover(**other)["packs"] == []
    with pytest.raises(PackUnavailable, match="pack_not_registered"):
        inspect_pack(**other, pack_id=operation["pack_id"])
    for value in ("../escape", "/tmp/arbitrary", "a" * 64):
        with pytest.raises(PackUnavailable):
            inspect_pack(**scope, pack_id=value)
    with pytest.raises(PackUnavailable, match="analysis_key_mismatch"):
        execute_pack(**{**operation, "analysis_key": "a" * 64}, codex="must-not-run")
    with pytest.raises(PackUnavailable, match="analysis_key_mismatch"):
        cancel_pack(**{**operation, "analysis_key": "a" * 64})


def test_registration_requires_real_hash_literal_excerpt_and_contained_path(
    pack, tmp_path
):
    scope, _, _, input_path, sources, _ = pack
    original = json.loads(input_path.read_text())
    for change, message in (
        ({"text": "invented"}, "excerpt_mismatch"),
        ({"artifact_sha256": "a" * 64}, "source_changed"),
        ({"artifact": "../outside.txt"}, "invalid_source_path"),
    ):
        data = json.loads(json.dumps(original))
        data["sources"][0].update(change)
        input_path.write_text(json.dumps(data))
        with pytest.raises(PackUnavailable, match=message):
            register_pack(
                **scope, input_path=input_path, source_root=sources, title="bad"
            )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (sources / "link.txt").symlink_to(outside)
    original["sources"][0]["artifact"] = "link.txt"
    input_path.write_text(json.dumps(original))
    with pytest.raises(PackUnavailable, match="invalid_source_path"):
        register_pack(**scope, input_path=input_path, source_root=sources, title="bad")
    assert len(discover(**scope)["packs"]) == 1


def test_missing_source_blocks_new_execution_and_leaves_registration_visible(pack):
    scope, operation, _, _, sources, _ = pack
    (sources / "result.txt").unlink()
    view = inspect_pack(**scope)
    assert (
        view["reason_code"] == "source_unavailable"
        and view["availability"] == "unavailable"
    )
    with pytest.raises(PackUnavailable, match="source_unavailable"):
        execute_pack(**operation, codex="must-not-run")


def test_method_upgrade_reads_verified_old_result_without_rejudging(pack, monkeypatch):
    from loopx.capabilities.benchmark_toolkit import (
        trajectory_assessment_store as store,
    )

    scope, operation, _, _, _, provider = pack
    execute_pack(**operation, codex=provider)
    current = inspect_pack(**scope)
    original_prepare = store._prepared

    def upgraded_method(record):
        # Simulate a new method identity at the typed-owner boundary. The real
        # old method, child process and stored receipt were exercised above.
        return {
            **original_prepare(record),
            "analysis_key": "b" * 64,
            "method_version": "future-method",
        }

    monkeypatch.setattr(store, "_prepared", upgraded_method)
    historical = inspect_pack(**scope)
    assert historical["reason_code"] == "method_changed"
    assert historical["applicability"] == "historical"
    assert historical["result"] == current["result"]
    assert historical["result"]["method_version"] != "future-method"
    assert (
        read_citation(**operation, source_id="source", first_line=2, last_line=2)[
            "applicability"
        ]
        == "historical"
    )
    with pytest.raises(PackUnavailable, match="method_changed"):
        execute_pack(**operation, codex="must-not-run")
    result_file = next(
        scope["runtime_root"].glob(
            "benchmark/trajectory-assessments/*/jobs/*/assessment.json"
        )
    )
    result_file.write_text("{}")
    with pytest.raises(PackUnavailable, match="historical_result_unverified"):
        inspect_pack(**scope)
