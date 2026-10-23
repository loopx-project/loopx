"""Fresh CLI reads retain provider, filter, error and presentation semantics."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)

from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)

REPO = Path(__file__).resolve().parents[2]
GOAL = "read-transport"
OWN = "todo_owned"


@pytest.fixture(params=["file", "sqlite"])
def read_cli(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    project = tmp_path / "project with spaces"
    project.mkdir()
    state, runtime, registry = project / "state.md", tmp_path / "runtime", tmp_path / "registry.json"
    rows = [
        {"todo_id": OWN, "text": "Owned full requirement", "claimed_by": "agent-a"},
        {"todo_id": "todo_unclaimed", "text": "Unclaimed full requirement"},
        {"todo_id": "todo_foreign", "text": "Foreign full requirement", "claimed_by": "agent-b"},
    ]
    state.write_text("# Goal\n\n## User Todo / Owner Review Reading Queue\n\n## Agent Todo\n\n" + "".join(
        f"- [ ] {row['text']}\n  <!-- loopx:todo todo_id={row['todo_id']} status=open "
        f"task_class=advancement_task claimed_by={row.get('claimed_by', '')} -->\n"
        for row in rows
    ) + "\n## Completed Work Archive\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "repo": str(project), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    if request.param != "unmigrated":
        todos = [{**row, "schema_version": "todo_item_v0", "role": "agent", "status": "open",
                  "done": False, "task_class": "advancement_task", "archive_state": "active",
                  "source_section": "Agent Todo", "index": i} for i, row in enumerate(rows, 1)]
        projection = build_todo_runtime_shadow_projection(goal_id=GOAL, todos=todos, handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, GOAL, projection, state_path=state, provider=request.param)
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime)]

    def run(*tail, format_name="json", expected_exit=0, isolated=False):
        args = [*prefix, "--format", format_name, "todo", "list", "--goal-id", GOAL, *tail]
        if isolated:
            script = (
                "import sys;from loopx.entrypoint import main;code=main(sys.argv[1:]);"
                "assert code==0;"
                "assert 'loopx.control_plane.todos.mutation_api' not in sys.modules;"
                "assert 'loopx.state_refresh' not in sys.modules;"
                "assert 'loopx.cli_commands.todo' not in sys.modules;"
                "assert 'loopx.capabilities.periodic_report.post_writeback_hook' not in sys.modules"
            )
            command = [sys.executable, "-c", script, *args]
        else:
            command = [sys.executable, "-m", "loopx.cli", *args]
        result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=45,
                                env={**os.environ, "PYTHONPATH": str(REPO)}, check=False)
        assert result.returncode == expected_exit, result.stdout + result.stderr
        return json.loads(result.stdout) if format_name == "json" else result.stdout

    yield request.param, run, prefix, state
    subprocess.run(
        [sys.executable, "-c", ("from loopx.control_plane.effect_runtime import effect_runtime_result;"
         "effect_runtime_result('runtime.shutdown', {}, retry_safe=False)")],
        cwd=REPO, capture_output=True, text=True, timeout=30, check=True,
    )


@pytest.mark.parametrize("read_cli", ["unmigrated"], indirect=True)
def test_unmigrated_query_variants_refuse_without_changing_source(read_cli):
    _, run, _, state = read_cli
    before = state.read_bytes()
    for options in [(), ("--todo-id", OWN), ("--limit", "1"), ("--thin",)]:
        result = run(*options, expected_exit=1)
        assert result["error_code"] == "canonical_authority_required"
        assert result["legacy_fallback_used"] is False
        assert result["write_admitted"] is False
    assert state.read_bytes() == before


def test_read_filters_errors_and_fresh_provider(read_cli):
    provider, run, prefix, state = read_cli
    state_before = state.read_bytes()
    full = run()
    assert full["ok"] and full["read_only"] and full["dry_run"]
    assert full["authority_read"]["source_authority"] == f"{provider}_v0"
    assert {item["todo_id"] for item in full["todos"]} == {OWN, "todo_unclaimed", "todo_foreign"}
    lane = run("--agent-id", "agent-a")
    assert {item["todo_id"] for item in lane["todos"]} == {OWN, "todo_unclaimed"}
    assert len(run("--thin")["todos"]) == 2
    assert len(run("--limit", "1")["todos"]) == 1
    exact = run("--todo-id", OWN)
    assert exact["todo"]["text"] == "Owned full requirement"
    assert exact["todo_detail_projection"]["source_complete"]
    assert "Owned full requirement" in run("--todo-id", OWN, format_name="markdown")
    assert run("--status", "done")["todo_count"] == 0
    assert run("--role", "user")["todo_count"] == 0
    for tail, error in [
        (["--limit", "0"], "todo list --limit must be at least 1"),
        (["--todo-id", OWN, "--thin"], "Exact Todo reads return full requirements; remove --thin or omit --todo-id for a bounded list"),
        (["--turn-instance-id", "unrelated-turn"], "--turn-instance-id is supported only by todo complete/supersede settlement"),
    ]:
        assert run(*tail, expected_exit=1)["error"] == error
    assert "unsupported: --text" in run("--text", "mutation", expected_exit=1)["error"]
    assert state.read_bytes() == state_before
    after = run()
    assert after["todos"] == full["todos"]
    assert after.get("authority_read") == full.get("authority_read")
    added = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *prefix, "--format", "json", "todo", "add",
         "--goal-id", GOAL, "--role", "agent", "--text", "Fresh canonical requirement",
         "--claimed-by", "agent-a"], cwd=REPO, capture_output=True, text=True, timeout=45, check=False,
    )
    assert added.returncode == 0, added.stdout + added.stderr
    fresh = run()
    assert fresh["todo_count"] == 4
    current = next(item for item in fresh["todos"] if item["todo_id"] == json.loads(added.stdout)["todo_id"])
    assert (current["text"], current["priority"]) == ("[P1] Fresh canonical requirement", "P1")
    assert fresh["authority_read"]["provider_revision"] != full["authority_read"]["provider_revision"]
    assert fresh["authority_read"]["decision_read_from_provider"]
    assert fresh["authority_read"]["legacy_fallback_used"] is False


def test_display_limits_report_complete_sources_and_actual_shown_items(read_cli):
    _, run, _, _ = read_cli
    for options, available in [((), 3), (("--agent-id", "agent-a"), 2),
                               (("--limit", "1"), 3), (("--thin",), 3)]:
        payload = run(*options)
        envelope = payload["projection_envelope"]
        coverage = envelope["coverage"]
        assert envelope["alert"] is False
        assert coverage["complete"] is True and coverage["omitted"] == []
        assert coverage["expected_count"] == coverage["included_count"] == available
        assert coverage["available_count"] == available
        assert coverage["shown_count"] == len(payload["todos"])
        assert coverage["truncated"] == (len(payload["todos"]) < available)


def test_read_entrypoint_does_not_load_mutation_owners(read_cli):
    _, run, _, _ = read_cli
    assert run(isolated=True)["todo_count"] == 3
    thin = run("--thin", isolated=True)
    assert thin["todo_count"] == 3  # Source count is retained by the bounded view.
    assert len(thin["todos"]) == 2
    assert run("--todo-id", OWN, isolated=True)["todo"]["todo_id"] == OWN


def test_read_source_failure_and_recovery_do_not_reuse_a_snapshot(read_cli):
    provider, run, prefix, state = read_cli
    before = run()
    state_bytes = state.read_bytes()
    runtime = Path(prefix[prefix.index("--runtime-root") + 1])
    authority = runtime / "authority" / f"{provider}-v0"
    unavailable = runtime / "unavailable-provider"
    authority.rename(unavailable)
    try:
        failed = run(expected_exit=1)
        assert failed["ok"] is False and failed["error"]
        assert failed["error_code"]
        assert failed["source_authority"] == f"{provider}_v0"
        assert failed["legacy_fallback_used"] is False
        assert state.read_bytes() == state_bytes
    finally:
        unavailable.rename(authority)
    recovered = run()
    assert recovered["todos"] == before["todos"]
    assert recovered.get("authority_read") == before.get("authority_read")


def test_todo_help_does_not_load_mutation_owners():
    script = (
        "import sys;from loopx.entrypoint import main;"
        "\ntry: main(['todo','--help'])"
        "\nexcept SystemExit as exc: assert exc.code==0"
        "\nassert 'loopx.control_plane.todos.mutation_api' not in sys.modules"
        "\nassert 'loopx.state_refresh' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO,
                            capture_output=True, text=True, timeout=45, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
