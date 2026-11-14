"""Unit and integration tests for loopx postgres CLI commands."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_postgres_setup_dry_run():
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.entrypoint",
            "--format",
            "json",
            "postgres",
            "setup",
            "--database-url",
            "postgresql://mock_user:mock_pass@localhost:5432/mock_db",
            "--runtime-role",
            "loopx_worker",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert payload["status"] == "dry_run"
    assert payload["schema_version"] == "loopx_postgresql_authority_store_v0"
    assert payload["runtime_role"] == "loopx_worker"
    assert payload["database_url_configured"] is True


def test_postgres_setup_missing_url(monkeypatch):
    monkeypatch.delenv("LOOPX_POSTGRES_URL", raising=False)
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.entrypoint",
            "--format",
            "json",
            "postgres",
            "setup",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 1
    payload = json.loads(process.stdout)
    assert payload["status"] == "failed"
    assert "required" in payload.get("reason", "").lower()


def test_postgres_local_to_pg_seed_export(tmp_path):
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    state = tmp_path / "state.md"
    goal = "seed-export-goal"

    runtime.mkdir(parents=True, exist_ok=True)
    state.write_text("# Synthetic state for seed export\n")
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime),
                "goals": [{"id": goal, "repo": str(tmp_path), "state_file": state.name}],
            }
        )
    )

    # Initialize local authority store using FileAuthorityStore via Node
    module = REPO / "loopx/control_plane/coordination/file_authority_store.ts"
    store_dir = runtime / "authority" / "file-v0"
    store_dir.mkdir(parents=True, exist_ok=True)

    script = (
        f"import {{FileAuthorityStore}} from {json.dumps(module.as_uri())};"
        "const store = new FileAuthorityStore(process.argv[1], process.argv[2]);"
        "await store.commitAuthority({"
        "  operation_id: 'op-init',"
        "  expected_provider_revision: null,"
        "  events: [],"
        "  receipts: [],"
        "  next_projection: {"
        "    schema_version: 'loopx_authority_projection_v0',"
        "    goal_id: process.argv[2],"
        "    owner_identity: 'ou_1234567890abcdef1234567890',"
        "    channel: { chat_id: 'oc_abcdef1234567890abcdef12345678' },"
        "    todos: ["
        "      {"
        "        id: 'todo-1',"
        "        title: 'Work on feature with .local/path and Feishu contact',"
        "        required_write_scopes: ['/Users/test/Developer/loopx/packages/core']"
        "      }"
        "    ]"
        "  }"
        "});"
    )
    subprocess.run(
        ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script, str(store_dir), goal],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    output_seed = tmp_path / "genesis_seed.json"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.entrypoint",
            "--registry",
            str(registry),
            "--format",
            "json",
            "postgres",
            "local-to-pg-seed",
            "--goal-id",
            goal,
            "--tenant-id",
            "tenant-alpha",
            "--output",
            str(output_seed),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert payload["status"] == "exported"
    assert payload["cursor"] == "1"
    assert payload["goal_id"] == goal
    assert payload["tenant_id"] == "tenant-alpha"
    assert bool(payload.get("seed_digest"))

    assert output_seed.is_file()
    seed_content = json.loads(output_seed.read_text())
    assert seed_content["schema_version"] == "loopx_pg_genesis_seed_v0"
    assert seed_content["cursor"] == "1"
    # Verify Feishu IDs and local paths were sanitized
    raw_str = json.dumps(seed_content)
    assert "ou_1234567890abcdef1234567890" not in raw_str
    assert "oc_abcdef1234567890abcdef12345678" not in raw_str
    assert "/Users/test/Developer" not in raw_str
