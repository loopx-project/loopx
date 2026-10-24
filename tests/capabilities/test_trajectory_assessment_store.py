"""Scoped pack-to-process journey with actual files and a disposable child."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from loopx.control_plane.effect_runtime import EffectRuntimeRejected

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
    evidence = "Prior question\nThe native measurement did not improve utility · 原始测量。\n".encode(
        "utf-8"
    )
    (sources / "result.txt").write_bytes(evidence)
    data = {
        "schema_version": "trajectory_assessment_input_v1",
        "scope": dict(
            goal_id="solver",
            case_id="case",
            run_id="run",
            revision="fixed",
            window="one episode · 观察窗口",
        ),
        "objective": "Improve native utility · 原始目标",
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
    input_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    scope = dict(runtime_root=tmp_path / "runtime", owner_goal_id="research-owner")
    registered = register_pack(
        **scope, input_path=input_path, source_root=sources, title="Case · 原始目标"
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
                reason="A negative native test still tests benefit · 负向测量。",
                citations=[dict(source_id="source", first_line=2, last_line=2)],
            )
        ],
        "limitations": ["Synthetic process; not model quality evidence"],
    }
    executable = tmp_path / "codex-fixture"
    executable.write_text(
        f"""#!{sys.executable}
import json,sys
from pathlib import Path
assert "\\u539f\\u59cb\\u76ee\\u6807" in sys.stdin.buffer.read().decode("utf-8")
Path(sys.argv[sys.argv.index('-o')+1]).write_bytes({json.dumps(output, ensure_ascii=False).encode("utf-8")!r})
sys.stderr.buffer.write("\\u5408\\u6210\\u8bca\\u65ad".encode("utf-8"))
sys.stdout.buffer.write((json.dumps({{'type':'turn.completed','message':'\\u5206\\u6790\\u5b8c\\u6210','usage':{{'input_tokens':10,'output_tokens':2}}}},ensure_ascii=False)+'\\n').encode("utf-8"))
""",
        encoding="utf-8",
    )
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
    assert (
        citation["text"]
        == "The native measurement did not improve utility · 原始测量。"
    )
    with pytest.raises(PackUnavailable, match="citation_not_in_result"):
        read_citation(**operation, source_id="source", first_line=1, last_line=2)

    (sources / "result.txt").write_text("changed", encoding="utf-8")
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


def test_continuation_reuses_identical_excerpts_despite_other_capture_bytes(pack):
    scope, operation, registered, path, sources, provider = pack
    assert execute_pack(**operation, codex=provider)["status"] == "succeeded"
    original = inspect_pack(**scope, pack_id=registered["pack_id"])["result"]
    old_directory = next((scope["runtime_root"] / "benchmark" / "trajectory-assessments").glob("*/jobs")) / operation["analysis_key"]
    old_bytes = {p.name: p.read_bytes() for p in old_directory.iterdir() if p.is_file()}
    evidence = (sources / "result.txt").read_bytes() + b"Unselected capture metadata changed\n"
    (sources / "result.txt").write_bytes(evidence)
    data = json.loads(path.read_text())
    data["scope"]["revision"] = "capture-2"
    data["sources"][0]["artifact_sha256"] = hashlib.sha256(evidence).hexdigest()
    path.write_text(json.dumps(data))
    successor = register_pack(**scope, input_path=path, source_root=sources, title="Updated capture", base_pack_id=registered["pack_id"])
    assert register_pack(**scope, input_path=path, source_root=sources, title="Updated capture", base_pack_id=registered["pack_id"])["pack_id"] == successor["pack_id"]
    before = inspect_pack(**scope)
    assert before["job"] is None and before["result"] is None
    assert before["continuation"]["can_reuse"] is True
    assert before["continuation"]["sources"] == dict(added=[], revised=[], removed=[])
    new_operation = {**scope, "pack_id": successor["pack_id"], "analysis_key": successor["analysis_key"]}
    completed = execute_pack(**new_operation, codex="must-not-run")
    assert completed["reused"] and completed["reused_from_analysis_key"] == operation["analysis_key"]
    reused_output = json.loads((old_directory.parent / successor["analysis_key"] / "model-output.json").read_text())
    assert set(reused_output) == {"assessments", "limitations"}
    view = inspect_pack(**scope)
    assert view["result"]["scope"]["revision"] == "capture-2"
    assert view["result"]["metrics"] == original["metrics"]
    assert view["result"]["evidence_manifest"][0]["artifact_sha256"] == data["sources"][0]["artifact_sha256"]
    assert read_citation(**new_operation, source_id="source", first_line=2, last_line=2)["applicability"] == "current"
    assert all((old_directory / name).read_bytes() == contents for name, contents in old_bytes.items())


def test_new_evidence_reassesses_old_judgments_and_complete_denominator(pack, tmp_path):
    scope, operation, registered, path, sources, provider = pack
    execute_pack(**operation, codex=provider)
    previous = inspect_pack(**scope)["result"]
    data = json.loads(path.read_text())
    later = b"The earlier proxy was not representative; no native test occurred.\n"
    (sources / "later.txt").write_bytes(later)
    data["sources"].append(dict(id="later", artifact="later.txt", artifact_sha256=hashlib.sha256(later).hexdigest(), first_line=1, text=later.decode().strip()))
    data["episodes"][0]["source_ids"].append("later")
    data["episodes"].append(dict(id="follow-up", question="Did follow-up test utility?", source_ids=["later"], eligible_metrics=["benefit_test"]))
    data["selection"]["population_episodes"] = 2
    data["scope"].update(revision="corrected", window="original and later evidence")
    path.write_text(json.dumps(data))
    successor = register_pack(**scope, input_path=path, source_root=sources, title="Later evidence", base_pack_id=registered["pack_id"])
    before = inspect_pack(**scope)
    assert before["continuation"]["can_reuse"] is False
    assert before["continuation"]["reassessed_episode_ids"] == ["episode", "follow-up"]
    assert before["continuation"]["new_pair_count"] == 1
    revised = {"assessments": [dict(episode_id=e, metric="benefit_test", verdict=v, reason="Later evidence corrects the earlier interpretation", citations=[dict(source_id="later", first_line=1, last_line=1)] if v != "unknown" else []) for e, v in [("episode", "no"), ("follow-up", "unknown")]], "limitations": ["Synthetic counterevidence"]}
    executable = tmp_path / "reassessment"
    executable.write_text(f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\nprompt=sys.stdin.read()\nassert 'The earlier proxy' in prompt and '原始测量' in prompt\nPath(sys.argv[sys.argv.index('-o')+1]).write_text({json.dumps(revised)!r})\n")
    executable.chmod(0o700)
    new_operation = {**scope, "pack_id": successor["pack_id"], "analysis_key": successor["analysis_key"]}
    execute_pack(**new_operation, codex=str(executable))
    view = inspect_pack(**scope)
    benefit = next(row for row in view["result"]["metrics"] if row["metric"] == "benefit_test")
    assert (benefit["yes"], benefit["no"], benefit["unknown"], benefit["eligible"]) == (0, 1, 1, 2)
    assert view["continuation"]["verdict_changes"] == [dict(episode_id="episode", metric="benefit_test", before="yes", after="no")]
    assert inspect_pack(**scope, pack_id=registered["pack_id"])["result"] == previous


def test_continuation_does_not_cross_owner_run_or_revoked_source(pack, tmp_path):
    scope, operation, registered, path, sources, provider = pack
    execute_pack(**operation, codex=provider)
    data = json.loads(path.read_text())
    data["scope"]["run_id"] = "unrelated"
    path.write_text(json.dumps(data))
    with pytest.raises(EffectRuntimeRejected, match="different Goal/case/run"):
        register_pack(**scope, input_path=path, source_root=sources, title="Wrong run", base_pack_id=registered["pack_id"])
    with pytest.raises(PackUnavailable, match="pack_not_registered"):
        register_pack(**{**scope, "owner_goal_id": "other-owner"}, input_path=path, source_root=sources, title="Wrong owner", base_pack_id=registered["pack_id"])
    data["scope"].update(run_id="run", revision="current")
    path.write_text(json.dumps(data))
    copied = tmp_path / "current-sources"
    copied.mkdir()
    (copied / "result.txt").write_bytes((sources / "result.txt").read_bytes())
    successor = register_pack(**scope, input_path=path, source_root=copied, title="Current snapshot", base_pack_id=registered["pack_id"])
    (sources / "result.txt").unlink()
    view = inspect_pack(**scope)
    assert view["availability"] == "available"
    assert not view["continuation"]["base_available"] and not view["continuation"]["can_reuse"]
    assert view["continuation"]["reassessed_episode_ids"] == ["episode"]
    result = execute_pack(**scope, pack_id=successor["pack_id"], analysis_key=successor["analysis_key"], codex=provider)
    assert result["status"] == "succeeded" and not result["reused"]


def test_native_results_register_through_real_cli_and_read_without_analysis(pack, tmp_path):
    scope, _, original, input_path, sources, _ = pack
    native = {
        "schema_version": "trajectory_native_outcomes_v1",
        "scope": json.loads(input_path.read_text())["scope"],
        "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:10:00Z",
        "complete": True, "metric": {"name": "error", "unit": "points", "higher_is_better": False},
        "retained_result_id": "native-1",
        "samples": [{"submission_index": 1, "result_id": "native-1", "candidate_ref": "candidate-a",
            "submitted_at": "2026-01-01T00:01:00Z", "observed_at": "2026-01-01T00:02:00Z",
            "valid": True, "value": 2, "citation": {"source_id": "source", "first_line": 2, "last_line": 2}}],
    }
    native_path = sources / "native-outcomes.json"
    native_path.write_text(json.dumps(native))
    registry = tmp_path / "native-registry.json"
    registry.write_text(json.dumps({"goals": []}))
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(scope["runtime_root"]), "--format", "json", "benchmark", "trajectory-pack-register",
        "--goal-id", scope["owner_goal_id"], "--input-json", str(input_path), "--source-root", str(sources),
        "--title", "Native outcome window", "--native-outcomes-json", native_path.name,
        "--base-pack-id", original["pack_id"]],
        capture_output=True, text=True, check=True)
    registered = json.loads(result.stdout)
    assert registered["analysis_key"] == original["analysis_key"]
    assert "native_outcomes" not in original
    assert registered["native_outcomes"]["projection"]["initial"]["value"] == 2
    view = inspect_pack(**scope, pack_id=registered["pack_id"])
    assert view["job"] is None and view["result"] is None
    assert view["continuation"]["base_pack_id"] == original["pack_id"]
    assert not view["continuation"]["base_available"]
    assert view["native_outcomes"]["projection"]["retained"]["candidate_ref"] == "candidate-a"
    citation = read_citation(**scope, pack_id=registered["pack_id"], analysis_key=registered["analysis_key"],
        source_id="source", first_line=2, last_line=2)
    assert citation["text"] == "The native measurement did not improve utility · 原始测量。"
    with pytest.raises(PackUnavailable, match="citation_not_in_result"):
        read_citation(**scope, pack_id=registered["pack_id"], analysis_key=registered["analysis_key"],
            source_id="source", first_line=1, last_line=2)
    native["samples"][0]["value"] = 3
    native_path.write_text(json.dumps(native))
    old = inspect_pack(**scope, pack_id=registered["pack_id"])
    assert old["native_outcomes"]["applicability"] == "historical"
    assert old["native_outcomes"]["projection"]["initial"]["value"] == 2
    assert read_citation(**scope, pack_id=registered["pack_id"], analysis_key=registered["analysis_key"],
        source_id="source", first_line=2, last_line=2)["applicability"] == "historical"
    assert not list((scope["runtime_root"] / "benchmark" / "trajectory-assessments").glob("*/jobs/*"))


def test_native_improvement_citations_are_readable_without_model_execution(pack):
    scope, _, _, input_path, sources, _ = pack
    evidence = "Native results\nolder best: 100\nbaseline: 12\nretained: 15\nApplied retained source; verified hash\n"
    (sources / "result.txt").write_text(evidence)
    data = json.loads(input_path.read_text())
    data["sources"][0].update(first_line=1, text=evidence,
        artifact_sha256=hashlib.sha256(evidence.encode()).hexdigest())
    input_path.write_text(json.dumps(data))
    native = {
        "schema_version": "trajectory_native_outcomes_v1", "scope": data["scope"],
        "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:10:00Z", "complete": False,
        "metric": {"name": "utility", "unit": "points", "higher_is_better": True},
        "retained_result_id": "result-9",
        "comparison": {"baseline_result_id": "result-7",
            "retention_citation": {"source_id": "source", "first_line": 5, "last_line": 5}},
        "samples": [{"submission_index": index, "result_id": f"result-{index}", "candidate_ref": f"candidate-{index}",
            "submitted_at": "2026-01-01T00:01:00Z", "observed_at": "2026-01-01T00:02:00Z",
            "valid": True, "value": value, "citation": {"source_id": "source", "first_line": line, "last_line": line}}
            for index, value, line in [(2, 100, 2), (7, 12, 3), (9, 15, 4)]],
    }
    path = sources / "comparison.json"
    path.write_text(json.dumps(native))
    registered = register_pack(**scope, input_path=input_path, source_root=sources,
        title="Source-bound retained improvement", native_outcomes_json=path.name)
    operation = {**scope, "pack_id": registered["pack_id"], "analysis_key": registered["analysis_key"]}
    view = inspect_pack(**scope, pack_id=registered["pack_id"])
    assert view["native_outcomes"]["projection"]["retained_improvement"]["value"] == 3
    assert view["native_outcomes"]["projection"]["initial"] is None
    assert view["result"] is None and view["job"] is None
    assert read_citation(**operation, source_id="source", first_line=3, last_line=3)["text"] == "baseline: 12"
    assert read_citation(**operation, source_id="source", first_line=5, last_line=5)["text"] == "Applied retained source; verified hash"
    with pytest.raises(PackUnavailable, match="citation_not_in_result"):
        read_citation(**operation, source_id="source", first_line=1, last_line=1)
    path.write_text("{}")
    old = inspect_pack(**scope, pack_id=registered["pack_id"])
    assert old["native_outcomes"]["applicability"] == "historical"
    assert old["native_outcomes"]["projection"]["retained_improvement"]["value"] == 3
    assert read_citation(**operation, source_id="source", first_line=5, last_line=5)["applicability"] == "historical"


def test_native_capture_cannot_borrow_another_run_or_escape_source_root(pack):
    scope, _, _, input_path, sources, _ = pack
    path = sources / "wrong-native.json"
    raw = {"schema_version": "trajectory_native_outcomes_v1", "scope": {
        **json.loads(input_path.read_text())["scope"], "run_id": "foreign"}}
    path.write_text(json.dumps(raw))
    with pytest.raises(EffectRuntimeRejected, match="scope differs"):
        register_pack(**scope, input_path=input_path, source_root=sources, title="wrong", native_outcomes_json=path.name)
    with pytest.raises(PackUnavailable, match="invalid_source_path"):
        register_pack(**scope, input_path=input_path, source_root=sources, title="wrong", native_outcomes_json="../input.json")


@pytest.mark.skipif(
    os.name != "posix", reason="C locale coercion is a POSIX regression"
)
def test_utf8_pack_and_child_roundtrip_with_ascii_default_locale(pack):
    scope, _, registered, input_path, sources, executable = pack
    script = r"""
import codecs, locale, sys
from pathlib import Path
from loopx.capabilities.benchmark_toolkit.trajectory_assessment import prepare, read_job
from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import (
    PackUnavailable, discover, execute_pack, inspect_pack, read_citation, register_pack,
)
assert sys.flags.utf8_mode == 0
assert codecs.lookup(locale.getencoding()).name == "ascii"
input_path, sources, runtime = map(Path, sys.argv[1:4])
scope = dict(runtime_root=runtime, owner_goal_id="research-owner")
prepared = prepare(input_path, model="gpt-6.1-sol", effort="max", service_tier="fast")
assert prepared["input"]["objective"] == "Improve native utility · 原始目标"
registered = register_pack(**scope, input_path=input_path, source_root=sources, title="Case · 原始目标")
assert registered["pack_id"] == sys.argv[4]
assert registered["analysis_key"] == prepared["analysis_key"]
assert discover(**scope)["packs"][0]["title"] == "Case · 原始目标"
assert inspect_pack(**scope)["job"] is None
operation = dict(**scope, pack_id=registered["pack_id"], analysis_key=registered["analysis_key"])
first = execute_pack(**operation, codex=sys.argv[5])
assert first["status"] == "succeeded" and first["usage"] == dict(input_tokens=10, output_tokens=2)
view = inspect_pack(**scope)
assert view["result"]["assessments"][0]["reason"] == "A negative native test still tests benefit · 负向测量。"
assert view["result"]["metrics"][1]["yes"] == 1
assert "directory" not in view["job"] and "process_id" not in view["job"]
assert execute_pack(**operation, codex="must-not-run")["reused"] is True
directory = next(runtime.glob("benchmark/trajectory-assessments/*/jobs/*"))
assert read_job(directory)["scope"]["window"] == "one episode · 观察窗口"
assert "原始目标" in (directory / "prompt.txt").read_text(encoding="utf-8")
assert "分析完成" in (directory / "events.jsonl").read_text(encoding="utf-8")
assert (directory / "stderr.txt").read_text(encoding="utf-8") == "合成诊断"
citation = dict(source_id="source", first_line=2, last_line=2)
assert read_citation(**operation, **citation)["text"] == "The native measurement did not improve utility · 原始测量。"
(sources / "result.txt").write_bytes(b"changed")
assert discover(**scope)["packs"][0]["reason_code"] == "source_changed"
try:
    execute_pack(**operation, codex="must-not-run")
except PackUnavailable as error:
    assert str(error) == "source_changed"
else:
    raise AssertionError("Changed sources must not run a new analysis")
assert inspect_pack(**scope)["result"] == view["result"]
assert read_citation(**operation, **citation)["applicability"] == "historical"
"""
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8=0",
            "-c",
            script.encode("ascii", "backslashreplace").decode("ascii"),
            str(input_path),
            str(sources),
            str(scope["runtime_root"]),
            registered["pack_id"],
            executable,
        ],
        env={
            **os.environ,
            "LC_ALL": "C",
            "LANG": "C",
            "PYTHONUTF8": "0",
            "PYTHONCOERCECLOCALE": "0",
            "PYTHONIOENCODING": "ascii",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


def test_digest_rejections_preserve_registration_and_create_no_job(pack):
    scope, operation, _, _, _, _ = pack
    root = scope["runtime_root"]
    before = {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    malformed = (
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "sha256:" + "a" * 64,
        " " + "a" * 64,
        "a" * 64 + " ",
        "\n" + "a" * 64,
        "a" * 64 + "\n",
        "a" * 64 + "\r\n",
        "a" * 64 + "\0",
        True,
        64,
        {"id": "a" * 64},
        ["a" * 64],
    )
    for value in malformed:
        with pytest.raises(PackUnavailable, match="^invalid_identifier$"):
            inspect_pack(**scope, pack_id=value)
    for value in (*malformed, "", None, False, 0, {}, []):
        with pytest.raises(PackUnavailable, match="^invalid_identifier$"):
            execute_pack(**{**operation, "analysis_key": value}, codex="must-not-run")
    for value in ("0" * 64, "0123456789abcdef" * 4, "f" * 64):
        with pytest.raises(PackUnavailable, match="^pack_not_registered$"):
            inspect_pack(**scope, pack_id=value)
    assert {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    } == before


def test_registration_requires_real_hash_literal_excerpt_and_contained_path(
    pack, tmp_path
):
    scope, _, _, input_path, sources, _ = pack
    original = json.loads(input_path.read_text(encoding="utf-8"))
    for change, message in (
        ({"text": "invented"}, "excerpt_mismatch"),
        ({"artifact_sha256": "a" * 64}, "source_changed"),
        ({"artifact": "../outside.txt"}, "invalid_source_path"),
    ):
        data = json.loads(json.dumps(original))
        data["sources"][0].update(change)
        input_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(PackUnavailable, match=message):
            register_pack(
                **scope, input_path=input_path, source_root=sources, title="bad"
            )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (sources / "link.txt").symlink_to(outside)
    original["sources"][0]["artifact"] = "link.txt"
    input_path.write_text(json.dumps(original), encoding="utf-8")
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


@pytest.mark.parametrize("encoding", [None, "source_text_aliases_v1", "source_text_aliases_with_literals_v1", "source_text_aliases_with_literal_values_v1"])
@pytest.mark.parametrize("context", [False, True])
def test_full_history_display_uses_the_same_job_and_preserves_original_versions(pack, tmp_path, encoding, context):
    scope, _, _, input_path, sources, _ = pack
    data = json.loads(input_path.read_text())
    history = {"ok": True, "goal_id": "solver", "total": 2, "next_cursor": None, "items": [
        dict(schema_version="loopx_explore_result_event_v0", event_id="question-old", result_id="question", goal_id="solver", event_kind="node",
             node_kind="question", recorded_at="2026-01-01T00:00:00Z", title="Does V18 help?", summary="Not established.", status="open"),
        dict(schema_version="loopx_explore_result_event_v0", event_id="question-new", result_id="question", goal_id="solver", event_kind="node",
             node_kind="question", recorded_at="2026-01-02T00:00:00Z", title="Does V19 help?", summary="Still unknown.", status="blocked"),
    ]}
    if encoding == "source_text_aliases_with_literal_values_v1":
        history["items"][0]["summary"] = "Trial4failed2checks"
    text = json.dumps(history, ensure_ascii=False)
    (sources / "history.json").write_text(text)
    data["sources"].append(dict(id="history", artifact="history.json", artifact_sha256=hashlib.sha256(text.encode()).hexdigest(), first_line=1, text=text))
    data["display_snapshot"] = dict(schema_version="research_display_snapshot_v1", source_id="history", source_namespace="source-a")
    if encoding is not None:
        data["display_snapshot"]["translation_encoding"] = encoding
    context_citation = dict(source_id="source", first_line=2, last_line=2)
    if context:
        data["display_snapshot"]["contexts"] = [dict(event_id="question-old", field="summary", citations=[context_citation])]
    input_path.write_text(json.dumps(data))
    registered = register_pack(**scope, input_path=input_path, source_root=sources, title="Whole history · synthetic")
    current = inspect_pack(**scope, pack_id=registered["pack_id"], output_language="zh")
    assert current["display_request"]["eligible"] == 4 and current["job"] is None
    provider = tmp_path / "display-provider"
    provider.write_text(f"#!{sys.executable}\n" + '''
import json,sys
from pathlib import Path
prompt = sys.stdin.read()
fields = json.loads(prompt.split("\\n\\ndisplay_fields:\\n")[1])
def translation(field):
    if field.get("literals") == ["Trial4failed2checks"]:
        assert field["explanation_numbers"] == ["4", "2"]
        return "第4次尝试有2项检查未通过（" + field["text"] + "）。"
    return "译文：" + field["text"]
output = {"assessments":[{"episode_id":"episode","metric":"benefit_test","verdict":"unknown","reason":"合成过程测试，未验证实际收益。","citations":[]}],
          "limitations":["合成译文，非模型质量验收。"], "episode_questions":[{"episode_id":"episode","question":"是否有帮助？"}],
          "display_translations":[{"field_id":f["field_id"], "text":translation(f)} for f in fields[:-1]]}
Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(output))
print(json.dumps({"type":"turn.completed", "usage":{"input_tokens":19,"output_tokens":7}}))
''')
    provider.chmod(0o700)
    operation = dict(**scope, pack_id=registered["pack_id"], analysis_key=current["analysis_key"], output_language="zh")
    assert execute_pack(**operation, codex=str(provider))["status"] == "succeeded"
    result = inspect_pack(**scope, pack_id=registered["pack_id"], output_language="zh")
    assert result["result"]["display"]["coverage"] == dict(eligible=4, translated=3, missing=1, invalid=0)
    if context:
        field = next(field for field in result["result"]["display"]["fields"] if field["field"] == "summary")
        assert field["context_citations"] == [context_citation]
        assert read_citation(**operation, **context_citation)["text"] == data["sources"][0]["text"]
        with pytest.raises(PackUnavailable, match="citation_not_in_result"):
            read_citation(**operation, source_id="history", first_line=1, last_line=1)
    else:
        assert not any("context_citations" in field for field in result["result"]["display"]["fields"])
    if encoding == "source_text_aliases_with_literal_values_v1":
        assert any(field["text"] == "第4次尝试有2项检查未通过（Trial4failed2checks）。"
                   for field in result["result"]["display"]["fields"])
    assert result["job"]["usage"]["input_tokens"] == 19  # one shared attempt, not four new jobs
    assert len(result["result"]["metrics"]) == 6 and result["result"]["metrics"][1]["unknown"] == 1
    assert execute_pack(**operation, codex="must-not-run")["reused"] is True
    assert inspect_pack(**scope, pack_id=registered["pack_id"], output_language="en")["result"] is None
    with pytest.raises(PackUnavailable, match="analysis_key"):
        execute_pack(**{**operation, "output_language": "en"}, codex="must-not-run")
    successful_provider = provider.read_text().replace("for f in fields[:-1]", "for f in fields")
    provider.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(9)\n")
    with pytest.raises(ValueError, match="Codex exited 9"):
        execute_pack(**operation, codex=str(provider), retry=True)
    prior = inspect_pack(**scope, pack_id=registered["pack_id"], output_language="zh")
    assert prior["job"]["status"] == "failed" and prior["job"]["attempt"] == 2
    assert prior["result_attempt"] == 1 and prior["result_job"]["usage"]["input_tokens"] == 19
    assert prior["result"] == result["result"]
    provider.write_text(successful_provider)
    assert execute_pack(**operation, codex=str(provider), retry=True)["status"] == "succeeded"
    result = inspect_pack(**scope, pack_id=registered["pack_id"], output_language="zh")
    assert result["result"]["display"]["coverage"]["missing"] == 0 and result["result_attempt"] == 3
    assert execute_pack(**operation, codex="must-not-run", retry=True)["reused"] is True
    old = result["result"]["display"]
    (sources / "history.json").write_text(text + " ")
    changed = inspect_pack(**scope, pack_id=registered["pack_id"], output_language="zh")
    assert changed["applicability"] == "historical" and changed["result"]["display"] == old
    with pytest.raises(PackUnavailable, match="source_changed"):
        execute_pack(**operation, codex="must-not-run")
    assert json.loads(input_path.read_text()) == data


def test_method_upgrade_reads_verified_old_result_without_rejudging(pack, monkeypatch):
    from loopx.capabilities.benchmark_toolkit import (
        trajectory_assessment_store as store,
    )

    scope, operation, _, _, _, provider = pack
    execute_pack(**operation, codex=provider)
    current = inspect_pack(**scope)
    original_prepare = store._prepared

    def upgraded_method(record, output_language=None):
        # Simulate a new method identity at the typed-owner boundary. The real
        # old method, child process and stored receipt were exercised above.
        return {
            **original_prepare(record, output_language),
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
    result_file.write_text("{}", encoding="utf-8")
    with pytest.raises(PackUnavailable, match="historical_result_unverified"):
        inspect_pack(**scope)


def _usage_fixture():
    def counts(i, c, o):
        return dict(input_tokens=i, cached_input_tokens=c, output_tokens=o)

    events = [
        dict(type="session_meta", payload=dict(id="solver-session")),
        dict(type="event_msg", payload=dict(type="task_started", turn_id="a")),
        dict(
            type="event_msg",
            payload=dict(
                type="token_count",
                info=dict(
                    total_token_usage=counts(100, 60, 10),
                    last_token_usage=counts(100, 60, 10),
                ),
            ),
        ),
        dict(type="event_msg", payload=dict(type="task_complete", turn_id="a")),
        dict(type="event_msg", payload=dict(type="task_started", turn_id="b")),
        dict(
            type="event_msg",
            payload=dict(
                type="token_count",
                info=dict(
                    total_token_usage=counts(140, 80, 15),
                    last_token_usage=counts(40, 20, 5),
                ),
            ),
        ),
        dict(type="event_msg", payload=dict(type="task_complete", turn_id="b")),
    ]
    for i, event in enumerate(events):
        event["timestamp"] = f"2026-01-01T00:00:0{i}Z"
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _request_usage_fixture():
    """Two Turns; one compaction request absent from the legacy counters."""

    def tokens(i, c, o):
        return dict(
            input_tokens=i,
            cached_input_tokens=c,
            output_tokens=o,
            cache_write_input_tokens=0,
            reasoning_output_tokens=0,
            total_tokens=i + o,
        )

    def request(response, turn, usage, thread, turn_usage=None):
        return dict(
            type="token_usage_record",
            payload=dict(
                thread_id="solver-session",
                session_id="solver-session",
                turn_id=turn,
                root_turn_id=turn,
                response_id=response,
                usage=usage,
                thread_token_usage=thread,
                turn_token_usage=turn_usage or usage,
                private_response_text="SYNTHETIC-PRIVATE-DO-NOT-TRANSPORT",
            ),
        )

    rows = [json.loads(line) for line in _usage_fixture().splitlines()]
    compact = request(
        "compact", "a", tokens(20, 10, 7), tokens(120, 70, 17), tokens(120, 70, 17)
    )
    rows[2:2] = [request("one", "a", tokens(100, 60, 10), tokens(100, 60, 10))]
    rows[4:4] = [
        compact,
        dict(
            type="compacted",
            payload=dict(
                compaction_response_id="compact",
                latest_token_usage_record=compact["payload"],
                replacement_history="SYNTHETIC-PRIVATE-DO-NOT-TRANSPORT",
            ),
        ),
    ]
    rows[8:8] = [request("two", "b", tokens(40, 20, 5), tokens(160, 90, 22))]
    for i, row in enumerate(rows):
        row["timestamp"] = (
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i)
        ).isoformat()
    return "\n".join(json.dumps(row) for row in rows) + "\n"

@pytest.mark.parametrize("counter_basis", ["ordinary_requests", "all_requests"])
def test_request_usage_native_cli_replay_privacy_and_historical_readback(
    pack, monkeypatch, counter_basis
):
    import loopx.capabilities.benchmark_toolkit.trajectory_assessment_store as store

    scope, _, old, input_path, sources, _ = pack
    capture = sources / "usage.jsonl"
    rows = _request_usage_fixture().splitlines()
    if counter_basis == "all_requests":
        data = [json.loads(row) for row in rows]
        counter = data[9]["payload"]["info"]
        counter["total_token_usage"].update(input_tokens=160, cached_input_tokens=90, output_tokens=22, total_tokens=182)
        counter["last_token_usage"].update(input_tokens=60, cached_input_tokens=30, output_tokens=12, total_tokens=72)
        rows = [json.dumps(row) for row in data]
    # Exercise the real large-snapshot transport with repeated observations,
    # while the independent three-request total remains 160 input / 22 output.
    rows[3:3] = [rows[2]] * 2500
    capture.write_text("\n".join(rows) + "\n")
    args = dict(
        **scope,
        input_path=input_path,
        source_root=sources,
        title=old["title"],
        usage_jsonl="usage.jsonl",
        usage_session_id="solver-session",
    )
    actual = store.effect_runtime_result
    transported = []

    def observe(command, params, **kwargs):
        if command == "benchmark.trajectory.codex-usage":
            transported.append(params)
        return actual(command, params, **kwargs)

    monkeypatch.setattr(store, "effect_runtime_result", observe)
    registered = register_pack(**args)
    view = inspect_pack(**scope)
    facts = view["solver_usage"]["projection"]
    assert facts["input_tokens"] == (160 if counter_basis == "all_requests" else 140)
    assert facts["output_tokens"] == (22 if counter_basis == "all_requests" else 15)
    coverage = facts["request_coverage"]
    assert coverage["status"] == "verified"
    assert coverage["counter_basis"] == counter_basis
    assert coverage["all_requests"]["output_tokens"] == 22
    assert coverage["compaction"]["output_tokens"] == 7
    assert coverage["unique_requests"] == 3 and coverage["compaction_requests"] == 1
    assert coverage["duplicate_records"] == 2500
    assert len(json.dumps(transported).encode()) > 1024 * 1024
    assert transported and "SYNTHETIC-PRIVATE" not in json.dumps(transported)
    assert "SYNTHETIC-PRIVATE" not in json.dumps(view)
    registry = sources.parent / "request-registry.json"
    registry.write_text('{"projects":{}}')
    prefix = [
        sys.executable,
        "-m",
        "loopx.entrypoint",
        "--registry",
        str(registry),
        "--runtime-root",
        str(scope["runtime_root"]),
        "--format",
        "json",
        "benchmark",
    ]
    cli_args = [
        "trajectory-pack-register",
        "--goal-id",
        scope["owner_goal_id"],
        "--input-json",
        str(input_path),
        "--source-root",
        str(sources),
        "--title",
        old["title"],
        "--usage-jsonl",
        "usage.jsonl",
        "--usage-session-id",
        "solver-session",
    ]
    replay = subprocess.run([*prefix, *cli_args], capture_output=True, text=True)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert json.loads(replay.stdout)["pack_id"] == registered["pack_id"]
    read = subprocess.run(
        [*prefix, "trajectory-packs", "--goal-id", scope["owner_goal_id"]],
        capture_output=True,
        text=True,
    )
    assert read.returncode == 0
    current = next(
        p
        for p in json.loads(read.stdout)["packs"]
        if p["pack_id"] == registered["pack_id"]
    )
    assert current["solver_usage"] == view["solver_usage"]
    assert inspect_pack(**scope, pack_id=registered["pack_id"])["job"] is None
    # A partial serialized record is not silently promoted into complete coverage.
    capture.write_text(capture.read_text() + '{"type":"token_usage_record"')
    with pytest.raises(ValueError):
        register_pack(**args)
    stale = inspect_pack(**scope, pack_id=registered["pack_id"])["solver_usage"]
    assert stale["reason_code"] == "source_changed" and stale["projection"] == facts
    # A valid counter capture with inconsistent optional evidence remains readable.
    capture.write_text(
        _request_usage_fixture().replace('"response_id": "two"', '"response_id": "one"')
    )
    invalid = register_pack(**args)
    failed = inspect_pack(**scope, pack_id=invalid["pack_id"])["solver_usage"][
        "projection"
    ]
    assert failed["request_coverage"]["status"] == "unavailable"
    assert (
        failed["input_tokens"] == 140
        and "all_requests" not in failed["request_coverage"]
    )
    assert (
        inspect_pack(**scope, pack_id=registered["pack_id"])["solver_usage"][
            "projection"
        ]
        == facts
    )
    assert not list(scope["runtime_root"].rglob("receipt.json"))


def test_legacy_saved_request_coverage_is_read_without_inventing_counter_basis(pack):
    import loopx.capabilities.benchmark_toolkit.trajectory_assessment_store as store

    scope, _, old, input_path, sources, _ = pack
    (sources / "usage.jsonl").write_text(_request_usage_fixture())
    current = register_pack(**scope, input_path=input_path, source_root=sources,
        title=old["title"], usage_jsonl="usage.jsonl", usage_session_id="solver-session")
    owner, record = store._resolve(**scope, pack_id=current["pack_id"])
    # A separately registered synthetic historical record, never an active Goal
    # migration or a rewrite of the new capture's identity.
    del record["solver_usage"]["projection"]["request_coverage"]["counter_basis"]
    legacy_id = store._pack_id(scope["owner_goal_id"], record)
    path = owner / "packs" / f"{legacy_id}.json"
    path.write_text(json.dumps(record))
    index = json.loads((owner / "packs.json").read_text())
    index["pack_ids"].append(legacy_id)
    (owner / "packs.json").write_text(json.dumps(index))
    before = path.read_bytes()
    coverage = inspect_pack(**scope, pack_id=legacy_id)["solver_usage"]["projection"]["request_coverage"]
    assert coverage["status"] == "verified" and "counter_basis" not in coverage
    assert coverage["all_requests"]["input_tokens"] == 160
    assert path.read_bytes() == before


def test_optional_solver_usage_is_read_only_scoped_and_separate_from_model(pack):
    scope, _, old, input_path, sources, _ = pack
    assert "solver_usage" not in inspect_pack(**scope)
    (sources / "usage.jsonl").write_text(_usage_fixture())
    args = dict(
        **scope,
        input_path=input_path,
        source_root=sources,
        title=old["title"],
        usage_jsonl="usage.jsonl",
        usage_session_id="solver-session",
    )
    registered = register_pack(**args)
    assert registered["pack_id"] != old["pack_id"]
    assert registered["analysis_key"] == old["analysis_key"]
    assert register_pack(**args) == registered
    view = inspect_pack(**scope)
    cost = view["solver_usage"]
    assert cost["projection"]["input_tokens"] == 140
    assert cost["projection"]["uncached_input_tokens"] == 60
    assert cost["projection"]["turns"][1]["usage"]["input_tokens"] == 40
    assert view["job"] is None and view["result"] is None
    assert discover(**scope)["packs"][-1]["solver_usage"] == cost
    assert not list(scope["runtime_root"].rglob("receipt.json"))
    (sources / "usage.jsonl").write_text("changed capture")
    stale = inspect_pack(**scope)["solver_usage"]
    assert stale["applicability"] == "historical"
    assert stale["reason_code"] == "source_changed"
    assert stale["projection"] == cost["projection"]
    (sources / "usage.jsonl").unlink()
    assert inspect_pack(**scope)["solver_usage"]["reason_code"] == "source_unavailable"
    assert "solver_usage" not in inspect_pack(**scope, pack_id=old["pack_id"])


def test_usage_registration_rejects_wrong_identity_paths_gaps_and_tampering(pack):
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected

    scope, _, old, input_path, sources, _ = pack
    args = dict(
        **scope,
        input_path=input_path,
        source_root=sources,
        title=old["title"],
        usage_jsonl="usage.jsonl",
        usage_session_id="solver-session",
    )
    path = sources / "usage.jsonl"
    path.write_text(_usage_fixture())
    with pytest.raises(ValueError, match="supplied together"):
        register_pack(**{**args, "usage_session_id": None})
    with pytest.raises(EffectRuntimeRejected, match="session"):
        register_pack(**{**args, "usage_session_id": "wrong-session"})
    with pytest.raises(PackUnavailable, match="invalid_source_path"):
        register_pack(**{**args, "usage_jsonl": "../usage.jsonl"})
    outside = sources.parent / "outside.jsonl"
    outside.write_text(_usage_fixture())
    (sources / "escaping.jsonl").symlink_to(outside)
    with pytest.raises(PackUnavailable, match="invalid_source_path"):
        register_pack(**{**args, "usage_jsonl": "escaping.jsonl"})
    path.write_text(
        _usage_fixture().replace('"input_tokens": 140', '"input_tokens": 240')
    )
    with pytest.raises(EffectRuntimeRejected, match="gap"):
        register_pack(**args)
    path.write_text(_usage_fixture())
    registered = register_pack(**args)
    stored = next(scope["runtime_root"].rglob(registered["pack_id"] + ".json"))
    row = json.loads(stored.read_text())
    row["solver_usage"]["projection"]["input_tokens"] = 0
    stored.write_text(json.dumps(row))
    with pytest.raises(PackUnavailable, match="invalid_registration"):
        inspect_pack(**scope)


def test_large_usage_real_cli_register_readback_and_replay(pack, monkeypatch):
    from loopx.control_plane import effect_runtime

    scope, _, old, input_path, sources, _ = pack
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [dict(type="session_meta", payload=dict(id="solver-session"))]
    for turn in range(1, 4501):
        payloads = [
            dict(type="task_started", turn_id=str(turn)),
            dict(
                type="token_count",
                info=dict(
                    total_token_usage=dict(
                        input_tokens=100 * turn,
                        cached_input_tokens=60 * turn,
                        output_tokens=10 * turn,
                    ),
                    last_token_usage=dict(
                        input_tokens=100, cached_input_tokens=60, output_tokens=10
                    ),
                ),
            ),
            dict(type="task_complete", turn_id=str(turn)),
        ]
        for offset, payload in enumerate(payloads):
            rows.append(
                dict(
                    type="event_msg",
                    payload=payload,
                    timestamp=(
                        start + timedelta(seconds=3 * turn + offset)
                    ).isoformat(),
                )
            )
    capture = sources / "usage.jsonl"
    capture.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    # This is larger even after the adapter's content reduction, not merely a
    # large raw transcript made small by discarding model messages.
    params = dict(
        session_id="solver-session",
        events=[
            dict(line=i + 1, timestamp=None, **row)
            if "timestamp" not in row
            else dict(line=i + 1, **row)
            for i, row in enumerate(rows)
        ],
    )
    assert (
        len(json.dumps(params, separators=(",", ":")).encode())
        > effect_runtime.MAX_REQUEST_BYTES
    )
    registry = sources.parent / "registry.json"
    registry.write_text('{"projects":{}}')
    prefix = [
        sys.executable,
        "-m",
        "loopx.entrypoint",
        "--registry",
        str(registry),
        "--runtime-root",
        str(scope["runtime_root"]),
        "--format",
        "json",
        "benchmark",
    ]

    def cli(*args):
        result = subprocess.run([*prefix, *args], text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    args = [
        "trajectory-pack-register",
        "--goal-id",
        scope["owner_goal_id"],
        "--input-json",
        str(input_path),
        "--source-root",
        str(sources),
        "--title",
        old["title"],
        "--usage-jsonl",
        "usage.jsonl",
        "--usage-session-id",
        "solver-session",
    ]
    registered = cli(*args)
    assert cli(*args) == registered
    view = inspect_pack(**scope, pack_id=registered["pack_id"])
    cost = view["solver_usage"]
    assert cost["artifact_sha256"] == hashlib.sha256(capture.read_bytes()).hexdigest()
    facts = cost["projection"]
    assert facts["completed_turns"] == 4500
    assert facts["input_tokens"] == 450000
    assert facts["cached_input_tokens"] == 270000
    assert facts["uncached_input_tokens"] == 180000
    assert facts["output_tokens"] == 45000
    assert facts["turns"][0]["first_line"] == 2
    assert facts["turns"][-1]["last_line"] == 13501
    assert all(t["usage"]["input_tokens"] == 100 for t in facts["turns"])
    listing = cli("trajectory-packs", "--goal-id", scope["owner_goal_id"])
    assert (
        len(listing["packs"]) == 2
    )  # original optional-off pack plus one replayed pack
    assert (
        next(p for p in listing["packs"] if p["pack_id"] == registered["pack_id"])[
            "solver_usage"
        ]
        == cost
    )
    assert "solver_usage" not in inspect_pack(**scope, pack_id=old["pack_id"])
    assert view["job"] is None and view["result"] is None

    # The method opts into a bounded channel, not a larger ordinary RPC wire.
    with pytest.raises(
        effect_runtime.EffectRuntimeRejected, match="request is oversized"
    ):
        effect_runtime.effect_runtime_result("benchmark.trajectory.codex-usage", params)
    monkeypatch.setattr(
        effect_runtime, "MAX_LOCAL_SNAPSHOT_BYTES", effect_runtime.MAX_REQUEST_BYTES
    )
    with pytest.raises(
        effect_runtime.EffectRuntimeRejected, match="local snapshot is oversized"
    ):
        register_pack(
            **scope,
            input_path=input_path,
            source_root=sources,
            title="over limit",
            usage_jsonl="usage.jsonl",
            usage_session_id="solver-session",
        )
    assert len(discover(**scope)["packs"]) == 2


def test_observed_usage_preserves_incomplete_cost_and_default_gate(pack):
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected

    scope, _, old, input_path, sources, _ = pack
    args = dict(
        **scope,
        input_path=input_path,
        source_root=sources,
        title=old["title"],
        usage_jsonl="usage.jsonl",
        usage_session_id="solver-session",
    )
    path = sources / "usage.jsonl"
    path.write_text("\n".join(_usage_fixture().splitlines()[:-1]) + "\n")
    before = path.read_bytes()
    with pytest.raises(EffectRuntimeRejected, match="completed Turn"):
        register_pack(**args)
    registered = register_pack(**args, include_incomplete_usage=True)
    assert registered["analysis_key"] == old["analysis_key"]
    view = inspect_pack(**scope)["solver_usage"]
    assert view["projection"]["method"] == "codex_observed_session_counters_v1"
    assert view["projection"]["input_tokens"] == 140
    assert view["projection"]["incomplete_turn"]["usage"]["input_tokens"] == 40
    assert view["projection"]["completed_turns"] == 1
    assert register_pack(**args, include_incomplete_usage=True) == registered
    assert path.read_bytes() == before
    assert inspect_pack(**scope)["job"] is None
    with pytest.raises(ValueError, match="requires a usage capture"):
        register_pack(
            **{**args, "usage_jsonl": None, "usage_session_id": None},
            include_incomplete_usage=True,
        )
    path.write_bytes(before + b'{"broken":')
    with pytest.raises(ValueError):
        register_pack(**args, include_incomplete_usage=True)
    assert inspect_pack(**scope)["solver_usage"]["applicability"] == "historical"
    path.unlink()
    assert inspect_pack(**scope)["solver_usage"]["reason_code"] == "source_unavailable"


def test_observed_usage_real_cli_without_model_dispatch(pack):
    scope, _, old, input_path, sources, _ = pack
    (sources / 'usage.jsonl').write_text('\n'.join(_usage_fixture().splitlines()[:-1]) + '\n')
    registry = sources.parent / 'registry-observed.json'
    registry.write_text('{"projects":{}}')
    prefix = [sys.executable, '-m', 'loopx.entrypoint', '--registry', str(registry),
              '--runtime-root', str(scope['runtime_root']), '--format', 'json', 'benchmark']
    args = ['trajectory-pack-register', '--goal-id', scope['owner_goal_id'],
            '--input-json', str(input_path), '--source-root', str(sources), '--title', old['title'],
            '--usage-jsonl', 'usage.jsonl', '--usage-session-id', 'solver-session']
    default = subprocess.run([*prefix, *args], text=True, capture_output=True)
    assert default.returncode != 0 and 'completed Turn' in default.stdout + default.stderr
    observed = subprocess.run([*prefix, *args, '--include-incomplete-usage'], text=True, capture_output=True)
    assert observed.returncode == 0, observed.stdout + observed.stderr
    record = json.loads(observed.stdout)
    discovery = subprocess.run([*prefix, 'trajectory-packs', '--goal-id', scope['owner_goal_id']], text=True, capture_output=True)
    assert discovery.returncode == 0
    view = next(x for x in json.loads(discovery.stdout)['packs'] if x['pack_id'] == record['pack_id'])
    assert view['solver_usage']['projection']['input_tokens'] == 140
    assert view['solver_usage']['projection']['incomplete_turn']['usage']['input_tokens'] == 40
    assert inspect_pack(**scope, pack_id=record['pack_id'])['job'] is None
    assert not list(scope['runtime_root'].rglob('receipt.json'))


@pytest.mark.parametrize("coverage", ["complete", "partial", "conflicting", "invalid_cache"])
def test_loopx_input_attribution_real_transport_and_privacy(pack, monkeypatch, coverage):
    import loopx.capabilities.benchmark_toolkit.trajectory_assessment_store as store

    scope, _, old, input_path, sources, _ = pack
    rows = [json.loads(line) for line in _request_usage_fixture().splitlines()]
    annotations = []
    for response, turn, total, loopx, cached in [
        ("one", "a", 100, 20, 12), ("compact", "a", 20, 5, 4),
        ("two", "b", 40, 15, 10),
    ]:
        annotations.append(dict(type="loopx_input_token_attribution", payload=dict(
            method="provider_input_token_attribution_v1", response_id=response,
            session_id="solver-session", thread_id="solver-session", turn_id=turn,
            root_turn_id=turn, input_tokens=total, loopx_input_tokens=loopx,
            loopx_cached_input_tokens=cached,
            raw_packet="SYNTHETIC-PRIVATE-PACKET-DO-NOT-TRANSPORT")))
    if coverage == "partial":
        annotations = annotations[:1]
    if coverage == "conflicting":
        conflict = json.loads(json.dumps(annotations[0]))
        conflict["payload"]["loopx_input_tokens"] = 21
        annotations.append(conflict)
    if coverage == "invalid_cache":
        annotations[0]["payload"]["loopx_cached_input_tokens"] = True
    path = sources / "usage.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in [*rows, *annotations]) + "\n")
    original = path.read_bytes()
    actual = store.effect_runtime_result
    transported = []

    def observe(command, params, **kwargs):
        if command == "benchmark.trajectory.codex-usage":
            transported.append(params)
        return actual(command, params, **kwargs)

    monkeypatch.setattr(store, "effect_runtime_result", observe)
    registered = register_pack(**scope, input_path=input_path, source_root=sources,
        title="Synthetic attributed input", usage_jsonl="usage.jsonl", usage_session_id="solver-session")
    assert registered["analysis_key"] == old["analysis_key"]
    view = inspect_pack(**scope, pack_id=registered["pack_id"])
    usage = view["solver_usage"]["projection"]
    assert usage["input_tokens"] == 140
    assert usage["request_coverage"]["status"] == "verified"
    attribution = usage["loopx_input"]
    assert attribution["status"] == ("unavailable" if coverage in {"conflicting", "invalid_cache"} else coverage)
    if coverage == "complete":
        assert attribution["loopx_input_tokens"] == 40
        assert attribution["total_input_tokens"] == 160
        assert attribution["input_fraction"] == 0.25
        assert attribution["uncached_input_fraction"] == 0.2
    elif coverage == "partial":
        assert attribution["input_fraction"] is None
        assert attribution["input_fraction_bounds"] == [0.125, 0.5]
    else:
        assert "input_fraction" not in attribution
    assert transported and "SYNTHETIC-PRIVATE-PACKET" not in json.dumps(transported)
    assert path.read_bytes() == original
    assert view["job"] is None and view["result"] is None
    assert "loopx_input" not in inspect_pack(**scope, pack_id=old["pack_id"]).get("solver_usage", {}).get("projection", {})
