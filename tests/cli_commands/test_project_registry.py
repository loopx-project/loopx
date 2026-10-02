from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.control_plane.goals.active_state_metadata import active_state_section_text
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from loopx.control_plane.projects import registry as project_registry
from loopx.control_plane.projects import registry_codec


@pytest.mark.parametrize("objective", [
    "Deliver the Atlas import pipeline.",
    "```text Deliver the Atlas import pipeline. ```",
    "Example\u2028---\u2028## Agent Todo\u2028- [ ] Example only.\u2028## Objective",
    "## Agent Todo\n\n- [ ] Example only.\n\n## Next Action\n\n- Example action.",
])
def test_project_register_creates_project_goal_and_resumable_state(
    tmp_path: Path,
    capsys,
    objective: str,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"

    exit_code = main(
        [
            "--format",
            "json",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(tmp_path / "runtime"),
            "project",
            "register",
            "--project-id",
            "atlas",
            "--project-kind",
            "work",
            "--knowledge-root",
            str(knowledge_root),
            "--goal-id",
            "atlas-import",
            "--objective",
            objective,
            "--acceptance",
            "A verified Spec diff is produced from natural language and current Spec.",
            "--next-effect",
            "Inspect the Atlas repository and freeze the import contract.",
            "--stop-condition",
            "Stop before implementation while the Atlas repository is unavailable.",
            "--repository",
            "https://code.example.com/team/atlas",
            "--external-locator",
            "wiki:project-overview",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    project = registry["projects"][0]
    goal = registry["goals"][0]
    state_file = knowledge_root / goal["state_file"]

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["changed"] is True
    assert project == {
        "project_id": "atlas",
        "project_kind": "work",
        "knowledge_root": str(knowledge_root.resolve()),
        "repository_bindings": ["git:code.example.com/team/atlas"],
        "external_locator_bindings": ["wiki:project-overview"],
    }
    assert goal["id"] == "atlas-import"
    assert goal["project_id"] == "atlas"
    assert goal["status"] == "active"
    assert goal["adapter"] == {
        "kind": "read_only_project_map_v0",
        "status": "connected",
    }
    assert goal["brief"] == {
        "non_goals": [],
        "acceptance": [
            "A verified Spec diff is produced from natural language and current Spec."
        ],
        "unknowns": [],
        "next_effect": "Inspect the Atlas repository and freeze the import contract.",
        "stop_condition": (
            "Stop before implementation while the Atlas repository is unavailable."
        ),
    }
    assert state_file.exists()
    state = state_file.read_text(encoding="utf-8")
    assert "## Acceptance" in state
    assert "## Next Action" in state
    assert "## Stop Condition" in state
    items, archive, sources = parse_todo_source(state)
    assert sources == {"user": "User Todo / Owner Review Reading Queue", "agent": "Agent Todo"}
    assert items == {"user": [], "agent": []}
    assert archive == []
    assert active_state_section_text(state, "Objective") == " ".join(objective.split())


def test_project_register_defaults_to_knowledge_root_when_global_registry_exists(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root = tmp_path / "atlas"
    runtime_root = tmp_path / "runtime"
    global_registry = runtime_root / "registry.global.json"
    global_registry.parent.mkdir(parents=True)
    global_registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": []}),
        encoding="utf-8",
    )
    global_before = global_registry.read_bytes()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOOPX_REGISTRY", raising=False)

    assert (
        main(
            [
                "--format",
                "json",
                "--runtime-root",
                str(runtime_root),
                "project",
                "register",
                "--project-id",
                "atlas",
                "--project-kind",
                "work",
                "--knowledge-root",
                str(knowledge_root),
                "--goal-id",
                "atlas-import",
                "--objective",
                "Deliver the Atlas import pipeline.",
                "--acceptance",
                "A verified import report is produced.",
                "--next-effect",
                "Inspect Atlas.",
                "--stop-condition",
                "Stop while Atlas is unavailable.",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    local_registry = knowledge_root / ".loopx" / "registry.json"

    assert payload["registry"] == str(local_registry)
    assert local_registry.exists()
    assert global_registry.read_bytes() == global_before


def test_project_register_respects_registry_environment_configuration(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root = tmp_path / "atlas"
    configured_registry = tmp_path / "configured" / "registry.json"
    runtime_root = tmp_path / "runtime"
    global_registry = runtime_root / "registry.global.json"
    global_registry.parent.mkdir(parents=True)
    global_registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": []}),
        encoding="utf-8",
    )
    global_before = global_registry.read_bytes()
    monkeypatch.setenv("LOOPX_REGISTRY", str(configured_registry))

    assert (
        main(
            [
                "--format",
                "json",
                "--runtime-root",
                str(runtime_root),
                "project",
                "register",
                "--project-id",
                "atlas",
                "--project-kind",
                "work",
                "--knowledge-root",
                str(knowledge_root),
                "--goal-id",
                "atlas-import",
                "--objective",
                "Deliver the Atlas import pipeline.",
                "--acceptance",
                "A verified import report is produced.",
                "--next-effect",
                "Inspect Atlas.",
                "--stop-condition",
                "Stop while Atlas is unavailable.",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["registry"] == str(configured_registry)
    assert configured_registry.exists()
    assert not (knowledge_root / ".loopx" / "registry.json").exists()
    assert global_registry.read_bytes() == global_before


def test_project_register_serializes_one_state_across_distinct_registries(
    tmp_path: Path,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_paths = [tmp_path / f"registry-{index}.json" for index in range(8)]

    def register(registry_path: Path) -> dict[str, object]:
        return project_registry.register_project_goal(
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            project_id="atlas",
            project_kind="work",
            knowledge_root=knowledge_root,
            goal_id="atlas-import",
            objective="Deliver the Atlas import pipeline.",
            non_goals=[],
            acceptance=["A verified import report is produced."],
            unknowns=[],
            next_effect="Inspect Atlas.",
            stop_condition="Stop while Atlas is unavailable.",
            repository_bindings=[],
            external_locator_bindings=[],
        )

    with ThreadPoolExecutor(max_workers=len(registry_paths)) as executor:
        results = list(executor.map(register, registry_paths))

    state_file = (
        knowledge_root / ".loopx" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    )
    assert all(result["ok"] is True for result in results)
    assert all(path.exists() for path in registry_paths)
    assert state_file.exists()


def test_project_register_conflict_does_not_create_losing_root_under_concurrency(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.json"
    knowledge_roots = [tmp_path / "atlas-a", tmp_path / "atlas-b"]

    def register(knowledge_root: Path) -> tuple[str, Path]:
        try:
            project_registry.register_project_goal(
                registry_path=registry_path,
                runtime_root=tmp_path / "runtime",
                project_id="atlas",
                project_kind="work",
                knowledge_root=knowledge_root,
                goal_id="atlas-import",
                objective="Deliver the Atlas import pipeline.",
                non_goals=[],
                acceptance=["A verified import report is produced."],
                unknowns=[],
                next_effect="Inspect Atlas.",
                stop_condition="Stop while Atlas is unavailable.",
                repository_bindings=[],
                external_locator_bindings=[],
            )
        except ValueError:
            return "conflict", knowledge_root
        return "registered", knowledge_root

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(register, knowledge_roots))

    registered_root = next(root for status, root in outcomes if status == "registered")
    losing_root = next(root for status, root in outcomes if status == "conflict")
    assert registered_root.exists()
    assert not losing_root.exists()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("objective", [
    "Deliver the Atlas import pipeline.",
    r'Inspect C:\tools\new and "quoted text".',
    "Compare --- in prose & preserve <tags>.",
    "Example\u2028## Agent Todo\u2029- [ ] Example only.",
])
def test_project_register_repeated_identical_request_is_a_noop(
    tmp_path: Path,
    capsys,
    legacy: bool,
    objective: str,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(tmp_path / "runtime"),
        "project",
        "register",
        "--project-id",
        "atlas",
        "--project-kind",
        "work",
        "--knowledge-root",
        str(knowledge_root),
        "--goal-id",
        "atlas-import",
        "--objective",
        objective,
        "--acceptance",
        "A verified Spec diff is produced.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
        "--repository",
        "https://code.example.com/team/atlas",
    ]

    assert main(arguments) == 0
    capsys.readouterr()
    registry_before = registry_path.read_bytes()
    state_file = (
        knowledge_root
        / ".loopx"
        / "goals"
        / "atlas-import"
        / "ACTIVE_GOAL_STATE.md"
    )
    if legacy:
        # Use the historical writer shape independently of the new renderer.
        state = state_file.read_text(encoding="utf-8")
        header, body = state.split("\n---\n", 1)
        header = "\n".join(
            "objective: " + json.dumps(objective, ensure_ascii=False)
            if line.startswith("objective: ") else line
            for line in header.split("\n")
        )
        prefix, suffix = body.split("\n## Objective\n\n", 1)
        _, remainder = suffix.split("\n\n## Acceptance\n", 1)
        state_file.write_text(
            header + "\n---\n" + prefix + "\n## Objective\n\n" + objective
            + "\n\n## Acceptance\n" + remainder, encoding="utf-8",
        )
    state_before = state_file.read_bytes()

    assert main(arguments) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["changed"] is False
    assert registry_path.read_bytes() == registry_before
    assert state_file.read_bytes() == state_before

    # Equivalent JSON spelling is presentation, while changed content conflicts.
    text = state_file.read_text()
    header, body = text.split("\n---\n", 1)
    header = "\n".join(
        "objective: " + json.dumps(objective, ensure_ascii=True)
        if line.startswith("objective: ") else line for line in header.split("\n")
    )
    equivalent = header + "\n---\n" + body
    state_file.write_text(equivalent)
    assert main(arguments) == 0
    capsys.readouterr()
    assert state_file.read_text() == equivalent
    for drifted in (equivalent.replace("status: active", "status: paused", 1),
                    equivalent.replace("A verified Spec diff is produced.", "Different acceptance.", 1)):
        state_file.write_text(drifted)
        assert main(arguments) == 1
        assert "conflicts with registration" in capsys.readouterr().out
        assert state_file.read_text() == drifted
        assert registry_path.read_bytes() == registry_before


def test_project_register_reuses_legacy_state_from_strict_registry(
    tmp_path: Path, capsys,
) -> None:
    project = tmp_path / "atlas"
    registry_path = project / ".loopx" / "registry.json"
    args = [
        "--format", "json", "--registry", str(registry_path),
        "--runtime-root", str(tmp_path / "runtime"),
        "project", "register", "--project-id", "atlas", "--project-kind", "work",
        "--knowledge-root", str(project), "--goal-id", "atlas-import",
        "--objective", "Continue the Atlas import pipeline.",
        "--acceptance", "Preserve the registered Goal state.",
        "--next-effect", "Inspect the existing registration.",
        "--stop-condition", "Stop before changing the import contract.",
    ]
    assert main(args) == 0
    capsys.readouterr()

    new_state = project / ".loopx" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    legacy_state = project / ".codex" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    legacy_state.parent.mkdir(parents=True)
    new_state.rename(legacy_state)
    registry = registry_codec.load_project_registry(registry_path)
    registry["goals"][0]["state_file"] = ".codex/goals/atlas-import/ACTIVE_GOAL_STATE.md"
    digest = hashlib.sha256(json.dumps(
        registry, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    envelope = [
        {"schema_version": "loopx_project_registry_envelope_v1",
         "minimum_writer_protocol": "goal_instance_v1", "payload_sha256": f"sha256:{digest}"},
        registry,
    ]
    registry_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = registry_path.read_bytes()

    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["changed"] is False
    assert result["state_file"] == str(legacy_state)
    assert registry_path.read_bytes() == before
    assert legacy_state.exists() and not new_state.exists()


@pytest.mark.parametrize("field", ["projects", "goals"])
def test_project_register_rejects_malformed_registry_collections(
    tmp_path: Path,
    capsys,
    field: str,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps({"schema_version": "0.1", field: {}}),
        encoding="utf-8",
    )
    before = registry_path.read_bytes()

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "register",
                "--project-id",
                "atlas",
                "--project-kind",
                "work",
                "--knowledge-root",
                str(knowledge_root),
                "--goal-id",
                "atlas-import",
                "--objective",
                "Deliver the Atlas import pipeline.",
                "--acceptance",
                "A verified import report is produced.",
                "--next-effect",
                "Inspect Atlas.",
                "--stop-condition",
                "Stop while Atlas is unavailable.",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)

    assert f"registry {field} must be a list" in payload["error"]
    assert registry_path.read_bytes() == before


@pytest.mark.parametrize(
    ("field", "identity"),
    [("projects", "project_id"), ("goals", "id")],
)
def test_project_register_rejects_duplicate_registry_records(
    tmp_path: Path,
    capsys,
    field: str,
    identity: str,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "register",
        "--project-id",
        "atlas",
        "--project-kind",
        "work",
        "--knowledge-root",
        str(knowledge_root),
        "--goal-id",
        "atlas-import",
        "--objective",
        "Deliver the Atlas import pipeline.",
        "--acceptance",
        "A verified import report is produced.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
    ]
    assert main(arguments) == 0
    capsys.readouterr()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry[field].append(dict(registry[field][0]))
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    before = registry_path.read_bytes()

    assert main(arguments) == 1
    payload = json.loads(capsys.readouterr().out)

    assert f"duplicate {field} {identity}" in payload["error"]
    assert registry_path.read_bytes() == before


@pytest.mark.parametrize(
    ("option", "original", "replacement"),
    [
        ("--non-goal", "Do not implement the legacy exporter.", "Do not implement the reporting pipeline."),
        ("--acceptance", "A verified Spec diff is produced.", "A validated Spec is produced."),
        ("--unknown", "The Atlas checkout is unavailable.", "The sandbox is unavailable."),
        ("--next-effect", "Inspect Atlas.", "Inspect the sandbox."),
        (
            "--stop-condition",
            "Stop while Atlas is unavailable.",
            "Stop while the sandbox is unavailable.",
        ),
    ],
)
def test_project_register_changed_goal_brief_fails_without_writing(
    tmp_path: Path,
    capsys,
    option: str,
    original: str,
    replacement: str,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "register",
        "--project-id",
        "atlas",
        "--project-kind",
        "work",
        "--knowledge-root",
        str(knowledge_root),
        "--goal-id",
        "atlas-import",
        "--objective",
        "Deliver the Atlas import pipeline.",
        "--non-goal",
        "Do not implement the legacy exporter.",
        "--acceptance",
        "A verified Spec diff is produced.",
        "--unknown",
        "The Atlas checkout is unavailable.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
    ]
    assert main(arguments) == 0
    capsys.readouterr()
    registry_before = registry_path.read_bytes()
    state_file = (
        knowledge_root
        / ".loopx"
        / "goals"
        / "atlas-import"
        / "ACTIVE_GOAL_STATE.md"
    )
    state_before = state_file.read_bytes()
    changed_arguments = list(arguments)
    changed_arguments[changed_arguments.index(option) + 1] = replacement

    assert main(changed_arguments) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert "goal_id conflicts with existing registration" in payload["error"]
    assert registry_path.read_bytes() == registry_before
    assert state_file.read_bytes() == state_before


def test_project_register_removes_new_state_when_registry_write_fails(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    state_file = (
        knowledge_root / ".loopx" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    )
    arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "register",
        "--project-id",
        "atlas",
        "--project-kind",
        "work",
        "--knowledge-root",
        str(knowledge_root),
        "--goal-id",
        "atlas-import",
        "--objective",
        "Deliver the Atlas import pipeline.",
        "--acceptance",
        "A verified import report is produced.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
    ]
    original_write = registry_codec.ProjectRegistryTransaction.commit

    def fail_write(
        _transaction: registry_codec.ProjectRegistryTransaction,
        _payload: dict[str, object],
    ) -> bool:
        raise OSError("simulated registry write failure")

    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        fail_write,
    )

    assert main(arguments) == 1
    payload = json.loads(capsys.readouterr().out)

    assert "simulated registry write failure" in payload["error"]
    assert not registry_path.exists()
    assert not state_file.exists()

    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        original_write,
    )
    assert main(arguments) == 0
    capsys.readouterr()
    assert registry_path.exists()
    assert state_file.exists()


def test_project_register_removes_partial_state_when_state_write_fails(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    state_file = (
        knowledge_root / ".loopx" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    )
    arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "register",
        "--project-id",
        "atlas",
        "--project-kind",
        "work",
        "--knowledge-root",
        str(knowledge_root),
        "--goal-id",
        "atlas-import",
        "--objective",
        "Deliver the Atlas import pipeline.",
        "--acceptance",
        "A verified import report is produced.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
    ]
    original_write = project_registry._atomic_write_text

    def fail_state_write(path: Path, data: str) -> None:
        path.write_text(data[:10], encoding="utf-8")
        raise OSError("simulated state write failure")

    monkeypatch.setattr(project_registry, "_atomic_write_text", fail_state_write)

    assert main(arguments) == 1
    payload = json.loads(capsys.readouterr().out)

    assert "simulated state write failure" in payload["error"]
    assert not registry_path.exists()
    assert not state_file.exists()

    monkeypatch.setattr(project_registry, "_atomic_write_text", original_write)
    assert main(arguments) == 0
    capsys.readouterr()
    assert registry_path.exists()
    assert state_file.exists()


def test_project_register_recovers_exact_state_after_interruption(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    state_file = (
        knowledge_root / ".loopx" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    )
    arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "register",
        "--project-id",
        "atlas",
        "--project-kind",
        "work",
        "--knowledge-root",
        str(knowledge_root),
        "--goal-id",
        "atlas-import",
        "--objective",
        "Deliver the Atlas import pipeline.",
        "--acceptance",
        "A verified import report is produced.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
    ]
    original_write = registry_codec.ProjectRegistryTransaction.commit

    def interrupt_after_state(
        _transaction: registry_codec.ProjectRegistryTransaction,
        _payload: dict[str, object],
    ) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        interrupt_after_state,
    )
    with pytest.raises(KeyboardInterrupt):
        main(arguments)

    assert state_file.exists()
    assert not registry_path.exists()

    def fail_retry(
        _transaction: registry_codec.ProjectRegistryTransaction,
        _payload: dict[str, object],
    ) -> bool:
        raise OSError("simulated retry failure")

    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        fail_retry,
    )
    assert main(arguments) == 1
    retry_payload = json.loads(capsys.readouterr().out)
    assert "simulated retry failure" in retry_payload["error"]
    assert state_file.exists()

    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        original_write,
    )
    assert main(arguments) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["changed"] is True
    assert registry_path.exists()
    assert state_file.exists()
    assert not list(state_file.parent.glob("*.tmp"))


def test_project_register_repairs_missing_state_for_matching_records(
    tmp_path: Path,
    capsys,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    state_file = (
        knowledge_root / ".loopx" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    )
    arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "register",
        "--project-id",
        "atlas",
        "--project-kind",
        "work",
        "--knowledge-root",
        str(knowledge_root),
        "--goal-id",
        "atlas-import",
        "--objective",
        "Deliver the Atlas import pipeline.",
        "--acceptance",
        "A verified import report is produced.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
    ]
    assert main(arguments) == 0
    capsys.readouterr()
    state_file.unlink()

    assert main(arguments) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["changed"] is True
    assert state_file.exists()


def test_project_register_rejects_tampered_matching_state(
    tmp_path: Path,
    capsys,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    state_file = (
        knowledge_root / ".loopx" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    )
    arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "register",
        "--project-id",
        "atlas",
        "--project-kind",
        "work",
        "--knowledge-root",
        str(knowledge_root),
        "--goal-id",
        "atlas-import",
        "--objective",
        "Deliver the Atlas import pipeline.",
        "--acceptance",
        "A verified import report is produced.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
    ]
    assert main(arguments) == 0
    capsys.readouterr()
    original = state_file.read_text(encoding="utf-8")
    state_file.write_text(
        original.replace("- Inspect Atlas.", "- Implement Atlas without inspection."),
        encoding="utf-8",
    )

    assert main(arguments) == 1
    payload = json.loads(capsys.readouterr().out)

    assert "goal state file conflicts with registration" in payload["error"]


def test_project_register_rejects_second_goal_for_existing_project(
    tmp_path: Path,
    capsys,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "register",
        "--project-id",
        "atlas",
        "--project-kind",
        "work",
        "--knowledge-root",
        str(knowledge_root),
        "--goal-id",
        "atlas-import",
        "--objective",
        "Deliver the Atlas import pipeline.",
        "--acceptance",
        "A verified import report is produced.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
    ]
    assert main(arguments) == 0
    capsys.readouterr()
    registry_before = registry_path.read_bytes()
    second_goal_arguments = list(arguments)
    second_goal_arguments[second_goal_arguments.index("--goal-id") + 1] = "atlas-export"

    assert main(second_goal_arguments) == 1
    payload = json.loads(capsys.readouterr().out)

    assert "project_id already has its first registered goal" in payload["error"]
    assert registry_path.read_bytes() == registry_before
    assert not (
        knowledge_root
        / ".loopx"
        / "goals"
        / "atlas-export"
        / "ACTIVE_GOAL_STATE.md"
    ).exists()


def test_project_register_conflict_fails_without_writing(
    tmp_path: Path,
    capsys,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    common = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "register",
        "--project-id",
        "atlas",
        "--goal-id",
        "atlas-import",
        "--objective",
        "Deliver the Atlas import pipeline.",
        "--acceptance",
        "A verified Spec diff is produced.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
    ]
    assert (
        main(
            [
                *common,
                "--project-kind",
                "work",
                "--knowledge-root",
                str(knowledge_root),
            ]
        )
        == 0
    )
    capsys.readouterr()
    registry_before = registry_path.read_bytes()
    conflicting_root = tmp_path / "different-root"

    assert (
        main(
            [
                *common,
                "--project-kind",
                "personal",
                "--knowledge-root",
                str(conflicting_root),
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert "conflicts with existing registration" in payload["error"]
    assert registry_path.read_bytes() == registry_before
    assert not conflicting_root.exists()


def test_project_bind_session_selects_one_active_foreground_goal(
    tmp_path: Path,
    capsys,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "register",
                "--project-id",
                "atlas",
                "--project-kind",
                "work",
                "--knowledge-root",
                str(knowledge_root),
                "--goal-id",
                "atlas-import",
                "--objective",
                "Deliver the Atlas import pipeline.",
                "--acceptance",
                "A verified Spec diff is produced.",
                "--next-effect",
                "Inspect Atlas.",
                "--stop-condition",
                "Stop while Atlas is unavailable.",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "bind-session",
                "--session-id",
                "session-public-fixture",
                "--goal-id",
                "atlas-import",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    assert payload["changed"] is True
    assert payload["project_id"] == "atlas"
    assert registry["session_bindings"] == [
        {
            "session_id": "session-public-fixture",
            "foreground_goal_id": "atlas-import",
        }
    ]


@pytest.mark.parametrize(
    ("goals", "error"),
    [
        ([], "goal_id is not registered"),
        (
            [
                {
                    "id": "atlas-import",
                    "project_id": "atlas",
                    "status": "complete",
                }
            ],
            "foreground goal is not active",
        ),
        (
            [{"id": "atlas-import", "status": "active"}],
            "foreground goal has no project_id",
        ),
    ],
)
def test_project_bind_session_rejects_invalid_foreground_goal_without_writing(
    tmp_path: Path,
    capsys,
    goals: list[dict[str, str]],
    error: str,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    registry = {
        "schema_version": "0.1",
        "registry_role": "global-local",
        "projects": [
            {
                "project_id": "atlas",
                "project_kind": "work",
                "knowledge_root": str(tmp_path / "atlas"),
                "repository_bindings": [],
                "external_locator_bindings": [],
            }
        ],
        "goals": goals,
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    before = registry_path.read_bytes()

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "bind-session",
                "--session-id",
                "session-public-fixture",
                "--goal-id",
                "atlas-import",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)

    assert error in payload["error"]
    assert registry_path.read_bytes() == before


def test_project_unbind_session_removes_only_the_expected_session(
    tmp_path: Path,
    capsys,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": "atlas",
                        "project_kind": "work",
                        "knowledge_root": str(tmp_path / "atlas"),
                        "repository_bindings": [],
                        "external_locator_bindings": [],
                    }
                ],
                "goals": [
                    {
                        "id": "atlas-import",
                        "project_id": "atlas",
                        "status": "complete",
                    }
                ],
                "session_bindings": [
                    {
                        "session_id": "session-current",
                        "foreground_goal_id": "atlas-import",
                    },
                    {
                        "session_id": "session-other",
                        "foreground_goal_id": "atlas-import",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "unbind-session",
                "--session-id",
                "session-current",
                "--goal-id",
                "atlas-import",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    assert payload["changed"] is True
    assert payload["project_id"] == "atlas"
    assert payload["binding"] == {
        "session_id": "session-current",
        "foreground_goal_id": "atlas-import",
    }
    assert registry["session_bindings"] == [
        {
            "session_id": "session-other",
            "foreground_goal_id": "atlas-import",
        }
    ]


def test_project_unbind_session_is_idempotent_and_rejects_stale_goal(
    tmp_path: Path,
    capsys,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": "atlas",
                        "project_kind": "work",
                        "knowledge_root": str(tmp_path / "atlas"),
                        "repository_bindings": [],
                        "external_locator_bindings": [],
                    }
                ],
                "goals": [
                    {
                        "id": "atlas-import",
                        "project_id": "atlas",
                        "status": "active",
                    },
                    {
                        "id": "atlas-other",
                        "project_id": "atlas",
                        "status": "active",
                    },
                ],
                "session_bindings": [
                    {
                        "session_id": "session-current",
                        "foreground_goal_id": "atlas-import",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = registry_path.read_bytes()

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "unbind-session",
                "--session-id",
                "session-current",
                "--goal-id",
                "atlas-other",
            ]
        )
        == 1
    )
    mismatch = json.loads(capsys.readouterr().out)
    assert mismatch["ok"] is False
    assert "does not match expected foreground goal" in mismatch["error"]
    assert registry_path.read_bytes() == before

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "unbind-session",
                "--session-id",
                "session-missing",
                "--goal-id",
                "atlas-import",
            ]
        )
        == 0
    )
    missing = json.loads(capsys.readouterr().out)
    assert missing["changed"] is False
    assert registry_path.read_bytes() == before


def test_project_commands_reject_malformed_session_bindings(
    tmp_path: Path,
    capsys,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": "atlas",
                        "repository_bindings": ["git:code.example.com/team/atlas"],
                    }
                ],
                "goals": [
                    {
                        "id": "atlas-import",
                        "project_id": "atlas",
                        "status": "active",
                    }
                ],
                "session_bindings": {},
            }
        ),
        encoding="utf-8",
    )
    before = registry_path.read_bytes()

    for command in [
        [
            "bind-session",
            "--session-id",
            "session-public-fixture",
            "--goal-id",
            "atlas-import",
        ],
        [
            "unbind-session",
            "--session-id",
            "session-public-fixture",
            "--goal-id",
            "atlas-import",
        ],
        [
            "resolve",
            "--session-id",
            "session-public-fixture",
            "--repository",
            "https://code.example.com/team/atlas",
        ],
    ]:
        assert (
            main(
                [
                    "--format",
                    "json",
                    "--registry",
                    str(registry_path),
                    "project",
                    *command,
                ]
            )
            == 1
        )
        payload = json.loads(capsys.readouterr().out)
        assert "registry session_bindings must be a list" in payload["error"]
        assert registry_path.read_bytes() == before


def test_project_resolve_uses_session_binding_and_goal_project_id(
    tmp_path: Path,
    capsys,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": "atlas",
                        "project_kind": "work",
                        "knowledge_root": str(tmp_path / "atlas"),
                        "repository_bindings": [],
                        "external_locator_bindings": [],
                    }
                ],
                "goals": [
                    {
                        "id": "atlas-import",
                        "project_id": "atlas",
                        "status": "active",
                    },
                    {
                        "id": "other-active-goal",
                        "project_id": "atlas",
                        "status": "active",
                    },
                ],
                "session_bindings": [
                    {
                        "session_id": "session-public-fixture",
                        "foreground_goal_id": "atlas-import",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "resolve",
                "--session-id",
                "session-public-fixture",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["resolution"] == "resolved"
    assert payload["source"] == "session_binding"
    assert payload["project_id"] == "atlas"
    assert payload["foreground_goal_id"] == "atlas-import"


def test_project_resolve_falls_through_unbound_session_to_repository(
    tmp_path: Path,
    capsys,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": "atlas",
                        "repository_bindings": ["git:code.example.com/team/atlas"],
                    }
                ],
                "goals": [],
                "session_bindings": [],
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "resolve",
                "--session-id",
                "unbound-session",
                "--repository",
                "https://code.example.com/team/atlas",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["resolution"] == "resolved"
    assert payload["source"] == "repository_binding"
    assert payload["project_id"] == "atlas"


def test_project_resolve_stale_session_does_not_fall_through_to_repository(
    tmp_path: Path,
    capsys,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": "atlas",
                        "repository_bindings": ["git:code.example.com/team/atlas"],
                    }
                ],
                "goals": [
                    {
                        "id": "atlas-import",
                        "project_id": "atlas",
                        "status": "inactive",
                    }
                ],
                "session_bindings": [
                    {
                        "session_id": "stale-session",
                        "foreground_goal_id": "atlas-import",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "resolve",
                "--session-id",
                "stale-session",
                "--repository",
                "https://code.example.com/team/atlas",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["resolution"] == "unresolved"
    assert payload["source"] == "session_binding"
    assert payload["foreground_goal_id"] == "atlas-import"


@pytest.mark.parametrize(
    ("project_count", "expected_exit", "expected_resolution"),
    [(1, 0, "resolved"), (2, 1, "ambiguous")],
)
def test_project_resolve_default_output_shows_resolution(
    tmp_path: Path,
    capsys,
    project_count: int,
    expected_exit: int,
    expected_resolution: str,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": f"atlas-{index}",
                        "repository_bindings": ["git:code.example.com/team/atlas"],
                    }
                    for index in range(project_count)
                ],
                "goals": [],
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--registry",
                str(registry_path),
                "project",
                "resolve",
                "--repository",
                "https://code.example.com/team/atlas",
            ]
        )
        == expected_exit
    )
    output = capsys.readouterr().out

    assert f"- resolution: `{expected_resolution}`" in output
    assert "- source: `repository_binding`" in output
    if project_count == 1:
        assert "- project_id: `atlas-0`" in output


@pytest.mark.parametrize(
    ("option", "value", "source"),
    [
        (
            "--repository",
            "git@code.example.com:team/atlas.git",
            "repository_binding",
        ),
        (
            "--external-locator",
            "wiki:project-overview",
            "external_locator_binding",
        ),
    ],
)
def test_project_resolve_uses_one_exact_locator_match(
    tmp_path: Path,
    capsys,
    option: str,
    value: str,
    source: str,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": "atlas",
                        "project_kind": "work",
                        "knowledge_root": str(tmp_path / "atlas"),
                        "repository_bindings": ["git:code.example.com/team/atlas"],
                        "external_locator_bindings": ["wiki:project-overview"],
                    }
                ],
                "goals": [],
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "resolve",
                option,
                value,
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["resolution"] == "resolved"
    assert payload["source"] == source
    assert payload["project_id"] == "atlas"
    assert payload["foreground_goal_id"] is None


@pytest.mark.parametrize(
    ("option", "binding_field", "query", "match_count", "resolution"),
    [
        (
            "--repository",
            "repository_bindings",
            "https://code.example.com/team/atlas",
            0,
            "unresolved",
        ),
        (
            "--repository",
            "repository_bindings",
            "https://code.example.com/team/atlas",
            2,
            "ambiguous",
        ),
        (
            "--external-locator",
            "external_locator_bindings",
            "issue:EXAMPLE-1",
            0,
            "unresolved",
        ),
        (
            "--external-locator",
            "external_locator_bindings",
            "issue:EXAMPLE-1",
            2,
            "ambiguous",
        ),
    ],
)
def test_project_resolve_rejects_zero_or_multiple_locator_matches(
    tmp_path: Path,
    capsys,
    option: str,
    binding_field: str,
    query: str,
    match_count: int,
    resolution: str,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    binding = (
        "git:code.example.com/team/atlas"
        if binding_field == "repository_bindings"
        else query
    )
    projects = [
        {
            "project_id": f"project-{index}",
            "project_kind": "work",
            "knowledge_root": str(tmp_path / f"project-{index}"),
            "repository_bindings": [],
            "external_locator_bindings": [],
            binding_field: [binding],
        }
        for index in range(match_count)
    ]
    registry_path.write_text(
        json.dumps({"projects": projects, "goals": []}),
        encoding="utf-8",
    )
    before = registry_path.read_bytes()

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "resolve",
                option,
                query,
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["resolution"] == resolution
    assert payload["project_id"] is None
    assert registry_path.read_bytes() == before


@pytest.mark.parametrize(
    ("repository_bindings", "expected_project", "expected_source", "expected_exit"),
    [
        (["git:example.com/team/atlas"], "project-repository", "repository_binding", 0),
        ([], "project-external", "external_locator_binding", 0),
        (
            ["git:example.com/team/atlas", "git:example.com/team/atlas"],
            None,
            "repository_binding",
            1,
        ),
    ],
)
def test_project_resolve_checks_repository_before_external_locator(
    tmp_path: Path,
    capsys,
    repository_bindings: list[str],
    expected_project: str | None,
    expected_source: str,
    expected_exit: int,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    projects = [
        {
            "project_id": "project-repository",
            "repository_bindings": repository_bindings[:1],
            "external_locator_bindings": [],
        },
        {
            "project_id": "project-external",
            "repository_bindings": repository_bindings[1:],
            "external_locator_bindings": ["wiki:project-overview"],
        },
    ]
    registry_path.write_text(
        json.dumps({"projects": projects, "goals": []}),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "resolve",
                "--repository",
                "https://example.com/team/atlas",
                "--external-locator",
                "wiki:project-overview",
            ]
        )
        == expected_exit
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["source"] == expected_source
    assert payload["project_id"] == expected_project
    assert payload["resolution"] == ("resolved" if expected_exit == 0 else "ambiguous")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository_bindings", "git:example.com/team/atlas-overview"),
        ("external_locator_bindings", "wiki:project-overview-notes"),
    ],
)
def test_project_resolve_rejects_scalar_binding_fields(
    tmp_path: Path,
    capsys,
    field: str,
    value: str,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": [{"project_id": "atlas", field: value}],
                "goals": [],
            }
        ),
        encoding="utf-8",
    )
    option = "--repository" if field == "repository_bindings" else "--external-locator"
    query = (
        "https://example.com/team/atlas"
        if field == "repository_bindings"
        else "wiki:project-overview"
    )

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "resolve",
                option,
                query,
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)

    assert f"ProjectRecord {field} must be a list" in payload["error"]


@pytest.mark.parametrize(
    ("extra_args", "expected_project", "expected_source"),
    [
        (
            [
                "--project-id",
                "project-explicit",
                "--session-id",
                "session-public-fixture",
                "--repository",
                "https://example.com/session/repo",
            ],
            "project-explicit",
            "explicit_project_id",
        ),
        (
            [
                "--session-id",
                "session-public-fixture",
                "--repository",
                "https://example.com/explicit/repo",
            ],
            "project-session",
            "session_binding",
        ),
    ],
)
def test_project_resolve_uses_fixed_evidence_precedence(
    tmp_path: Path,
    capsys,
    extra_args: list[str],
    expected_project: str,
    expected_source: str,
) -> None:
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": "project-explicit",
                        "repository_bindings": ["git:example.com/explicit/repo"],
                    },
                    {
                        "project_id": "project-session",
                        "repository_bindings": ["git:example.com/session/repo"],
                    },
                ],
                "goals": [
                    {
                        "id": "goal-session",
                        "project_id": "project-session",
                        "status": "active",
                    }
                ],
                "session_bindings": [
                    {
                        "session_id": "session-public-fixture",
                        "foreground_goal_id": "goal-session",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "project",
                "resolve",
                *extra_args,
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["project_id"] == expected_project
    assert payload["source"] == expected_source


def test_sync_global_preserves_project_and_goal_relationship(
    tmp_path: Path,
    capsys,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    runtime_root = tmp_path / "runtime"
    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "project",
                "register",
                "--project-id",
                "atlas",
                "--project-kind",
                "work",
                "--knowledge-root",
                str(knowledge_root),
                "--goal-id",
                "atlas-import",
                "--objective",
                "Deliver the Atlas import pipeline.",
                "--acceptance",
                "A verified Spec diff is produced.",
                "--next-effect",
                "Inspect Atlas.",
                "--stop-condition",
                "Stop while Atlas is unavailable.",
                "--repository",
                "https://code.example.com/team/atlas",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "sync-global",
            ]
        )
        == 0
    )
    capsys.readouterr()
    global_registry = json.loads(
        (runtime_root / "registry.global.json").read_text(encoding="utf-8")
    )

    assert global_registry["projects"] == [
        {
            "project_id": "atlas",
            "project_kind": "work",
            "knowledge_root": str(knowledge_root.resolve()),
            "repository_bindings": ["git:code.example.com/team/atlas"],
            "external_locator_bindings": [],
        }
    ]
    assert global_registry["goals"][0]["project_id"] == "atlas"


@pytest.mark.parametrize("target", ["source", "global"])
@pytest.mark.parametrize(
    ("malformation", "expected_error"),
    [
        ("non-list", "projects must be a list"),
        ("duplicate", "duplicate project_id"),
        ("binding-scalar", "ProjectRecord repository_bindings must be a list"),
    ],
)
def test_sync_global_rejects_malformed_project_collections(
    tmp_path: Path,
    capsys,
    target: str,
    malformation: str,
    expected_error: str,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    runtime_root = tmp_path / "runtime"
    register_arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "project",
        "register",
        "--project-id",
        "atlas",
        "--project-kind",
        "work",
        "--knowledge-root",
        str(knowledge_root),
        "--goal-id",
        "atlas-import",
        "--objective",
        "Deliver the Atlas import pipeline.",
        "--acceptance",
        "A verified import report is produced.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
    ]
    assert main(register_arguments) == 0
    capsys.readouterr()
    source = json.loads(registry_path.read_text(encoding="utf-8"))
    project = source["projects"][0]
    global_path = runtime_root / "registry.global.json"
    if target == "source":
        target_path = registry_path
        payload = source
    else:
        target_path = global_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": "0.1", "goals": []}
    if malformation == "non-list":
        payload["projects"] = {}
    elif malformation == "duplicate":
        payload["projects"] = [project, dict(project)]
    else:
        malformed_project = dict(project)
        malformed_project["repository_bindings"] = "git:example.com/team/atlas"
        payload["projects"] = [malformed_project]
    target_path.write_text(json.dumps(payload), encoding="utf-8")
    before = target_path.read_bytes()

    assert (
        main(
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "sync-global",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)

    assert expected_error in payload["error"]
    assert target_path.read_bytes() == before
