#!/usr/bin/env python3
"""Smoke-test registry boundary classification and CLI import wiring."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import loopx.registry as registry_module  # noqa: E402
from loopx.registry import inspect_registry_boundary  # noqa: E402

DOC = REPO_ROOT / "docs" / "operations" / "authority-source-registration.md"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def registry_payload(project: Path, runtime: Path, *, registry_role: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "0.1",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "common_runtime_root": str(runtime),
        "goals": [
            {
                "id": "registry-boundary-goal",
                "domain": "registry-boundary",
                "status": "active",
                "repo": str(project),
                "state_file": ".codex/goals/registry-boundary-goal/ACTIVE_GOAL_STATE.md",
                "adapter": {
                    "kind": "fixture_connected_readonly_v0",
                    "status": "connected-read-only",
                },
                "authority_registry": {
                    "path": ".loopx/authority.registry-boundary.json",
                    "read_status": "registered",
                    "topic_authority": {
                        "registry_boundary": "public-registry-boundary-note",
                    },
                    "project_materials": {
                        "public-registry-boundary-note": {
                            "schema_version": "authority_source_registration_v0",
                            "id": "public-registry-boundary-note",
                            "role": "public_registry_boundary_contract",
                            "source_kind": "repository_doc",
                            "freshness": "current",
                            "boundary": "public",
                            "source_ref_redacted": True,
                        },
                    },
                },
            }
        ],
    }
    if registry_role:
        payload["registry_role"] = registry_role
    return payload


def public_projection_payload() -> dict[str, Any]:
    return {
        "schema_version": "registry_public_projection_v0",
        "registry_role": "public-safe-projection",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "goals": [
            {
                "id": "registry-boundary-goal",
                "domain": "registry-boundary",
                "status": "active",
                "authority_source_count": 2,
                "authority_registry": {
                    "declared": True,
                    "required": True,
                    "path_exists": True,
                    "read_status": "registered",
                    "topic_authority_count": 4,
                    "project_material_count": 3,
                    "conflict_risk": "low",
                },
            }
        ],
    }


def run_cli(path: Path, *extra: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "registry-boundary",
            "--path",
            str(path),
            *extra,
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    assert payload["path_label"] == path.name, payload
    return payload


def assert_doc_contract() -> None:
    text = DOC.read_text(encoding="utf-8")
    for marker in (
        "registry-boundary",
        "project-local private registry",
        "shared global-local registry",
        "public-safe projection",
        "do not push runtime registry files to GitHub",
    ):
        assert marker in text, marker


def assert_registry_boundary_degrades_without_git(root: Path, runtime: Path) -> None:
    no_git_registry = root / "no-git-project" / ".loopx" / "registry.json"
    write_json(no_git_registry, registry_payload(no_git_registry.parent.parent, runtime))
    original_run = registry_module.subprocess.run
    original_popen = registry_module.subprocess.Popen

    def reject_git(argv: Any) -> None:
        if isinstance(argv, list) and argv and argv[0] == "git":
            raise FileNotFoundError("git")

    def run_without_git(command: Any, *args: Any, **kwargs: Any) -> Any:
        reject_git(command)
        return original_run(command, *args, **kwargs)

    def popen_without_git(command: Any, *args: Any, **kwargs: Any) -> Any:
        reject_git(command)
        return original_popen(command, *args, **kwargs)

    # The probe reaches Git through both seams: subprocess.run for the short
    # queries and subprocess.Popen (#8115) for the tree-killable probe. A
    # no-git fixture that patches only one of them silently reports a healthy
    # git.available, so the fail-closed branch never runs.
    registry_module.subprocess.run = run_without_git
    registry_module.subprocess.Popen = popen_without_git
    try:
        payload = inspect_registry_boundary(no_git_registry)
    finally:
        registry_module.subprocess.run = original_run
        registry_module.subprocess.Popen = original_popen

    assert payload["ok"] is True, payload
    assert payload["classification"] == "project_local_private_registry", payload
    assert payload["git"]["available"] is False, payload
    assert payload["git"]["probe_status"] == "git_unavailable", payload
    assert payload["git"]["inside_worktree"] is False, payload
    assert payload["risks"] == [], payload


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-registry-boundary-") as tmp:
        root = Path(tmp) / "project"
        runtime = root / "runtime"
        root.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        (root / ".gitignore").write_text(
            ".loopx/\n"
            "runtime/\n"
            "registry.public.json\n",
            encoding="utf-8",
        )

        local_registry = root / ".loopx" / "registry.json"
        write_json(local_registry, registry_payload(root, runtime))
        local_payload = inspect_registry_boundary(local_registry)
        assert local_payload["ok"] is True, local_payload
        assert local_payload["classification"] == "project_local_private_registry", local_payload
        assert local_payload["boundary_kind"] == "project_local_private_registry", local_payload
        assert local_payload["github_push_allowed"] is False, local_payload
        assert local_payload["should_be_gitignored"] is True, local_payload
        assert local_payload["git"]["ignored"] is True, local_payload
        assert local_payload["path_recorded"] is False, local_payload

        global_registry = runtime / "registry.global.json"
        global_payload = registry_payload(root, runtime, registry_role="global-local")
        global_payload["goals"][0]["source_registry"] = str(local_registry)
        write_json(global_registry, global_payload)
        shared = inspect_registry_boundary(global_registry)
        assert shared["classification"] == "shared_local_registry", shared
        assert shared["boundary_kind"] == "global_local_private_registry", shared
        assert shared["github_push_allowed"] is False, shared
        assert shared["git"]["ignored"] is True, shared
        assert "global_registry_is_local_control_plane_only" in shared["risks"], shared

        public_projection = root / "registry.public.json"
        write_json(public_projection, public_projection_payload())
        projection = run_cli(public_projection, "--require-gitignored")
        assert projection["classification"] == "public_safe_projection", projection
        assert projection["boundary_kind"] == "public_safe_registry_projection", projection
        assert projection["github_push_allowed"] is False, projection
        assert projection["git"]["ignored"] is True, projection
        assert "public_safe_projection_is_not_runtime_publish_artifact" in projection["risks"], projection

        public_fixture = root / "examples" / "registry.example.json"
        write_json(
            public_fixture,
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01",
                "common_runtime_root": "./runtime",
                "goals": [
                    {
                        "id": "example-registry-goal",
                        "domain": "example",
                        "status": "active",
                        "repo": "example/project",
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "adapter": {"kind": "example_adapter_v0", "status": "connected"},
                    }
                ],
            },
        )
        subprocess.run(
            ["git", "add", ".gitignore", "examples/registry.example.json"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        public_payload = inspect_registry_boundary(public_fixture)
        assert public_payload["ok"] is True, public_payload
        assert public_payload["classification"] == "public_example_fixture", public_payload
        assert public_payload["boundary_kind"] == "public_fixture_registry_projection", public_payload
        assert public_payload["github_push_allowed"] is True, public_payload
        assert public_payload["git"]["tracked"] is True, public_payload

        leaky_fixture = root / "examples" / "registry.leaky-example.json"
        write_json(
            leaky_fixture,
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01",
                "common_runtime_root": "./runtime",
                "goals": [
                    {
                        "id": "leaky-registry-goal",
                        "domain": "example",
                        "status": "active",
                        "repo": "example/project",
                        "source_ref": "redacted-source-ref",
                    }
                ],
            },
        )
        leaky = inspect_registry_boundary(leaky_fixture)
        assert leaky["classification"] == "public_example_fixture", leaky
        assert leaky["github_push_allowed"] is False, leaky
        assert leaky["private_marker_count"] > 0, leaky
        assert "public_registry_contains_private_markers" in leaky["risks"], leaky
        assert leaky["ok"] is False, leaky

        cli_payload = run_cli(local_registry, "--require-gitignored")
        assert cli_payload["ok"] is True, cli_payload
        rendered = json.dumps(cli_payload, sort_keys=True)
        assert str(root) not in rendered, rendered

        assert_registry_boundary_degrades_without_git(root, runtime)

    assert_doc_contract()


if __name__ == "__main__":
    run()
    print("registry-boundary-contract-smoke ok")
