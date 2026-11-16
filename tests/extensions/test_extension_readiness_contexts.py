"""Real CLI qualification across independent Python module installations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.control_plane import test_refresh_external_delivery as settlement_fixtures
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, TODO_ID, TURN_ID
from loopx.extensions.lark.goal_channel_contracts import (
    GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
    human_gate_auto_notify_marker_path,
    write_human_gate_auto_notify_marker,
    write_goal_channel_binding,
)

from loopx.extensions.runtime import (
    doctor_installed_extension,
    extension_status,
    install_extension,
    resolve_extension_activation,
)


EXTENSION_ID = "test-context-provider"
MODULE = "fixture_context_provider"


def _manifest(path: Path, version: str = "1.0.0") -> Path:
    path.write_text(f'''schema_version = "loopx_extension_manifest_v0"
id = "{EXTENSION_ID}"
version = "{version}"
requires_loopx_api = ">=1,<2"
permissions = ["semantic_preference.read"]
[runtime]
protocol = "semantic_preference_provider_v0"
python_module = "{MODULE}"
doctor_args = ["--doctor"]
required_permissions = ["semantic_preference.read"]
timeout_seconds = 5
[[implements]]
capability_id = "semantic-preference"
protocol = "semantic_preference_provider_v0"
''', encoding="utf-8")
    return path


def test_real_cli_keeps_independent_runtime_proofs_and_revokes_only_failed_probe(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    state_file = runtime_root / "extensions" / "state.json"
    manifest = _manifest(tmp_path / "extension.toml")
    contexts = [tmp_path / name for name in ("installed", "source")]
    for context in contexts:
        context.mkdir()
        (context / f"{MODULE}.py").write_text(
            f'# qualified fixture artifact: {context.name}\n'
            'import os\nraise SystemExit(int(os.environ.get("FIXTURE_PROBE_EXIT", "0")))\n',
            encoding="utf-8",
        )
    repo = Path(__file__).resolve().parents[2]

    def cli(context: Path, *args: str, failure: bool = False) -> dict:
        environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("LOOPX_", "PYTHON", "FIXTURE_"))
        }
        environment.update(
            PYTHONPATH=os.pathsep.join((str(context), str(repo))),
            LOOPX_USAGE_PING="0", DO_NOT_TRACK="1",
            FIXTURE_PROBE_EXIT="2" if failure else "0",
        )
        result = subprocess.run(
            [sys.executable, "-c", "from loopx.entrypoint import main; raise SystemExit(main())",
             "--format", "json", "--runtime-root", str(runtime_root),
             "--registry", str(tmp_path / "registry.json"), "extension", *args],
            cwd=context, env=environment, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def ready(context: Path) -> bool:
        return cli(context, "list")["extensions"][0]["doctor_verified"]

    installed, source = contexts
    first = cli(installed, "install", "--manifest", str(manifest), "--execute")
    assert first["doctor"]["verified"] is True
    assert ready(installed) is True
    before_preview = state_file.read_bytes()
    assert ready(source) is False
    assert cli(source, "doctor", EXTENSION_ID)["verified"] is False
    assert state_file.read_bytes() == before_preview
    second = cli(source, "doctor", EXTENSION_ID, "--execute")
    assert second["verified"] is True
    assert second["entrypoint_identity"] != first["doctor"]["entrypoint_identity"]
    assert ready(installed) and ready(source)

    failed = cli(source, "doctor", EXTENSION_ID, "--execute", failure=True)
    assert failed["verified"] is False and failed["entrypoint_identity"] is None
    assert failed["probed_entrypoint_identity"] == second["entrypoint_identity"]
    assert ready(source) is False
    assert ready(installed) is True
    assert cli(source, "doctor", EXTENSION_ID, "--execute")["verified"] is True
    assert ready(installed) and ready(source)

    # Changing the manifest is a new authority revision, not a cache hit.
    v2 = _manifest(tmp_path / "v2.toml", "2.0.0")
    assert cli(installed, "upgrade", "--manifest", str(v2), "--execute")["changed"] is True
    assert ready(installed) is True and ready(source) is False
    cli(source, "doctor", EXTENSION_ID, "--execute")
    cli(installed, "rollback", EXTENSION_ID, "--execute")
    assert ready(installed) is True and ready(source) is False
    cli(installed, "disable", EXTENSION_ID, "--execute")
    assert ready(installed) is False and ready(source) is False


def test_legacy_single_proof_is_read_only_then_migrated_by_real_doctor(tmp_path: Path) -> None:
    # Use the packaged Python provider: no simulated identity or doctor result.
    manifest = Path(__file__).resolve().parents[2] / "loopx/extensions/lark/extension.toml"
    state_file = tmp_path / "state.json"
    installed = install_extension(manifest, state_file=state_file, execute=True)
    state = json.loads(state_file.read_text())
    entry = state["extensions"]["loopx-lark"]
    identity = entry.pop("doctor_verified_entrypoint_identities")[0]
    entry["doctor_verified_entrypoint_identity"] = identity
    state_file.write_text(json.dumps(state))
    legacy_bytes = state_file.read_bytes()
    assert resolve_extension_activation("loopx-lark", state_file=state_file)["doctor_verified"]
    assert state_file.read_bytes() == legacy_bytes
    result = doctor_installed_extension("loopx-lark", state_file=state_file, execute=True)
    assert result["verified"] and result["entrypoint_identity"] == identity
    migrated = json.loads(state_file.read_text())["extensions"]["loopx-lark"]
    assert "doctor_verified_entrypoint_identity" not in migrated
    assert migrated["doctor_verified_entrypoint_identities"] == [identity]
    assert migrated["active_revision"] == installed["revision"]
    assert extension_status(state_file=state_file)["extensions"][0]["doctor_verified"]


@pytest.mark.parametrize("selection", ["registry", "environment"])
def test_real_extension_cli_follows_selected_registry_with_conflicting_defaults(
    tmp_path: Path, selection: str,
) -> None:
    home = tmp_path / "home"
    defaults = [home / ".loopx", home / ".codex" / "loopx"]
    for root in defaults:
        root.mkdir(parents=True)
        (root / "preserve.txt").write_text("existing independent state")
    project = tmp_path / "project"
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"goals": [], "common_runtime_root": "selected state"}))
    runtime = project / "selected state"
    context = tmp_path / "unrelated working directory"
    context.mkdir()
    (context / f"{MODULE}.py").write_text("raise SystemExit(0)\n")
    manifest = _manifest(tmp_path / "extension.toml")
    repo = Path(__file__).resolve().parents[2]
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("LOOPX_", "PYTHON", "CODEX_"))
    }
    environment.update(
        HOME=str(home), PYTHONPATH=os.pathsep.join((str(context), str(repo))),
        LOOPX_USAGE_PING="0", DO_NOT_TRACK="1",
    )
    prefix = [sys.executable, "-c", "from loopx.entrypoint import main; raise SystemExit(main())",
              "--format", "json"]

    def run(*args: str, env: dict | None = None) -> dict:
        result = subprocess.run(
            [*prefix, *args], cwd=context, env=env or environment,
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    installed = run("--runtime-root", str(runtime), "extension", "install",
                    "--manifest", str(manifest), "--execute")
    assert installed["doctor"]["verified"] is True
    state = runtime / "extensions" / "state.json"
    before = state.read_bytes()
    route = ["--registry", str(registry)] if selection == "registry" else []
    selected_env = {**environment, "LOOPX_REGISTRY": str(registry)}
    listed = run(*route, "extension", "list", env=selected_env if not route else None)
    assert [item["id"] for item in listed["extensions"]] == [EXTENSION_ID]
    assert listed["extensions"][0]["doctor_verified"] is True
    assert state.read_bytes() == before
    checked = run(*route, "extension", "doctor", "--all-enabled", "--execute",
                  env=selected_env if not route else None)
    assert checked["status"] == "ready" and checked["verified_count"] == 1
    for root in defaults:
        assert sorted(path.name for path in root.iterdir()) == ["preserve.txt"]
        assert (root / "preserve.txt").read_text() == "existing independent state"


@pytest.mark.parametrize("override", ["runtime-root", "state-file"])
def test_real_extension_cli_explicit_storage_does_not_open_unused_registry(
    tmp_path: Path, override: str,
) -> None:
    registry = tmp_path / "unreadable-registry.json"
    registry.write_text("not registry JSON")
    state = tmp_path / "selected" / "extensions" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"schema_version": "loopx_extension_state_v0", "extensions": {}}))
    prefix = ["--runtime-root", str(state.parent.parent)] if override == "runtime-root" else []
    suffix = ["--state-file", str(state)] if override == "state-file" else []
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("LOOPX_", "PYTHON", "CODEX_"))}
    environment.update(LOOPX_USAGE_PING="0", DO_NOT_TRACK="1",
                       PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    before = {path: path.read_bytes() for path in (registry, state)}
    result = subprocess.run(
        [sys.executable, "-c", "from loopx.entrypoint import main; raise SystemExit(main())",
         "--format", "json", "--registry", str(registry), *prefix,
         "extension", "list", *suffix],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["extensions"] == []
    assert {path: path.read_bytes() for path in before} == before


def test_real_extension_cli_does_not_choose_between_conflicting_defaults(tmp_path: Path) -> None:
    home = tmp_path / "home"
    for relative in (".loopx", ".codex/loopx"):
        root = home / relative
        root.mkdir(parents=True)
        (root / "preserve.txt").write_text("independent state")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("LOOPX_", "PYTHON", "CODEX_"))}
    environment.update(HOME=str(home), LOOPX_USAGE_PING="0", DO_NOT_TRACK="1",
                       PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    before = {str(path.relative_to(home)): path.read_bytes()
              for path in home.rglob("*") if path.is_file()}
    result = subprocess.run(
        [sys.executable, "-c", "from loopx.entrypoint import main; raise SystemExit(main())",
         "--format", "json", "extension", "doctor", "--all-enabled", "--execute"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "Both default LoopX runtime roots contain state" in result.stderr
    assert {str(path.relative_to(home)): path.read_bytes()
            for path in home.rglob("*") if path.is_file()} == before


settlement_session = settlement_fixtures.session


def test_real_refresh_without_selected_notice_settles_with_unavailable_extension(
    settlement_session,
) -> None:
    _, runtime, registry, args, run, journal, index = settlement_session
    binding_path = registry.parent / "goal-channel.json"
    write_goal_channel_binding(binding_path, {
        "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
        "bindings": {GOAL_ID: {
            "goal_id": GOAL_ID, "provider": "lark", "enabled": True,
            "automation": {"human_gate_auto_notify_enabled": True}, "receipts": {},
        }},
    })
    write_human_gate_auto_notify_marker(human_gate_auto_notify_marker_path(binding_path, GOAL_ID))
    assert not (runtime / "extensions/state.json").exists()
    first = run(args)
    assert first["appended"] is True
    assert first["goal_channel_gate_sync"]["status"] == "not_selected"
    assert first["goal_channel_gate_sync"]["external_write_performed"] is False
    written = index.read_bytes()
    replay = run(args)
    assert replay["idempotent_replay"] is True
    assert replay["settlement_identity"] == first["settlement_identity"]
    assert index.read_bytes() == written
    spend = ["quota", "spend-slot", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
             "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID,
             "--slots", "1", "--source", "heartbeat", "--execute"]
    run(spend)
    run(spend)
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    assert sum(row.get("classification") == "validated_change" for row in rows) == 1
