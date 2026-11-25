"""Verification test suite for Colima / Docker PostgreSQL 16 dual-actor rehearsal

and Volcengine cn-beijing deployment templates.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_rehearsal_shell_script_help() -> None:
    """Verify that the shell wrapper outputs help information cleanly."""
    script_path = REPO_ROOT / "examples" / "coordination" / "colima-pg16-e2e-rehearsal.sh"
    assert script_path.exists()
    assert script_path.is_file()

    result = subprocess.run(
        [str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--simulate" in result.stdout
    assert "--rehearsal" in result.stdout
    assert "--database-url" in result.stdout
    assert "--start-colima" in result.stdout


def test_rehearsal_explicit_mode_fails_closed_without_docker() -> None:
    """Explicit --rehearsal must fail closed rather than silently downgrading to --simulate."""
    script_path = REPO_ROOT / "examples" / "coordination" / "colima-pg16-e2e-rehearsal.sh"
    env = {**os.environ, "DOCKER_HOST": "unix:///nonexistent/loopx-no-docker.sock"}
    result = subprocess.run(
        [str(script_path), "--rehearsal"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )
    assert result.returncode != 0
    assert "simulation" not in result.stdout.lower()


def test_rehearsal_simulation_e2e() -> None:
    """Verify that rehearsal runs end-to-end in simulation mode with ok: true."""
    script_path = REPO_ROOT / "examples" / "coordination" / "colima-pg16-e2e-rehearsal.sh"
    result = subprocess.run(
        [str(script_path), "--simulate"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    data = json.loads(result.stdout)

    assert data["schema_version"] == "colima_pg16_coordination_rehearsal_report_v0"
    assert data["ok"] is True
    assert data["mode"] == "simulation"
    assert data["execution_boundary"] == "in_memory_simulated_pg_tls"
    assert data["transport"] == "https_tls"
    assert data["clock_mode"] == "controlled_service_clock"
    assert data["runtime_role_verified"] is False
    assert data["server_version"] is None
    assert data["server_version_num"] is None
    assert data["server_major_version"] is None
    assert data["pg16_qualified"] is False
    assert data["tenant_id"] == "tenant-canary"
    assert data["goal_id"] == "loopx-quality-review"
    assert data["actors"]["actor_a"] == "operator.local"
    assert data["actors"]["actor_b"] == "worker.cloud"

    expected_steps = [
        "schema_initialization_and_role_assembly",
        "genesis_seed_ingestion",
        "actor_a_lease_claim",
        "active_lease_conflict_rejection",
        "default_deny_boundary_enforcement",
        "lease_expiration_takeover",
        "actor_b_task_completion",
        "cross_machine_snapshot_readback",
    ]
    step_names = [s["step"] for s in data["steps"]]
    assert step_names == expected_steps

    for step in data["steps"]:
        assert step["ok"] is True, f"Step {step['step']} failed: {step['detail']}"


def test_rehearsal_ts_cli_direct() -> None:
    """Verify that the TypeScript harness can be directly invoked via node."""
    ts_path = REPO_ROOT / "examples" / "coordination" / "colima-pg16-e2e-rehearsal.ts"
    result = subprocess.run(
        [
            "node",
            "--no-warnings",
            "--experimental-sqlite",
            "--experimental-strip-types",
            str(ts_path),
            "--simulate",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["transport"] == "https_tls"
    assert len(data["steps"]) == 8


def test_rehearsal_rejects_empty_database_url_and_conflicting_flags() -> None:
    """Explicit --database-url '' or conflicting flags must fail closed without simulation fallback."""
    script_path = REPO_ROOT / "examples" / "coordination" / "colima-pg16-e2e-rehearsal.sh"
    ts_path = REPO_ROOT / "examples" / "coordination" / "colima-pg16-e2e-rehearsal.ts"

    shell_empty = subprocess.run(
        [str(script_path), "--database-url", ""],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert shell_empty.returncode != 0
    assert "simulation" not in shell_empty.stdout.lower()

    ts_empty = subprocess.run(
        [
            "node",
            "--no-warnings",
            "--experimental-sqlite",
            "--experimental-strip-types",
            str(ts_path),
            "--database-url",
            "",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert ts_empty.returncode != 0
    assert "simulation" not in ts_empty.stdout.lower()

    ts_conflict = subprocess.run(
        [
            "node",
            "--no-warnings",
            "--experimental-sqlite",
            "--experimental-strip-types",
            str(ts_path),
            "--simulate",
            "--database-url",
            "postgresql://postgres:secret@127.0.0.1:5432/postgres",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert ts_conflict.returncode != 0
    assert "simulation" not in ts_conflict.stdout.lower()


def test_rehearsal_shell_rejects_conflicting_modes_both_orders_without_side_effects(
    tmp_path: Path,
) -> None:
    """Conflicting shell mode flags fail closed in all orders before Docker, Node, or PG I/O."""
    script_path = REPO_ROOT / "examples" / "coordination" / "colima-pg16-e2e-rehearsal.sh"
    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    side_effect_log = tmp_path / "invocations.log"
    for tool in ("docker", "colima", "node"):
        shim = shim_dir / tool
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s %s\\n" "{tool}" "$*" >> "{side_effect_log}"\n'
            "exit 99\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)

    # Bind a listening TCP socket to assert zero PostgreSQL connections or DDL occur.
    connections = 0
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    port = server.getsockname()[1]
    stop_event = threading.Event()

    def accept_loop() -> None:
        nonlocal connections
        server.settimeout(0.1)
        while not stop_event.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            connections += 1
            conn.close()

    listener = threading.Thread(target=accept_loop, daemon=True)
    listener.start()
    try:
        db_url = f"postgresql://postgres:secret@127.0.0.1:{port}/postgres"
        env = {
            **os.environ,
            "PATH": f"{shim_dir}:{os.environ.get('PATH', '')}",
        }
        conflict_cases = [
            ["--simulate", "--database-url", db_url],
            ["--database-url", db_url, "--simulate"],
            ["--simulate", "--rehearsal"],
            ["--rehearsal", "--simulate"],
            ["--simulate", "--start-colima"],
            ["--start-colima", "--simulate"],
            ["--rehearsal", "--database-url", db_url],
            ["--database-url", db_url, "--rehearsal"],
            ["--start-colima", "--database-url", db_url],
            ["--database-url", db_url, "--start-colima"],
            ["--database-url", db_url, "--database-url", db_url],
        ]
        for args in conflict_cases:
            res = subprocess.run(
                [str(script_path), *args],
                capture_output=True,
                text=True,
                env=env,
                cwd=REPO_ROOT,
            )
            assert res.returncode == 1, f"{args}: rc={res.returncode}, stderr={res.stderr}"
            assert res.stdout == "", f"{args}: unexpected stdout {res.stdout!r}"
            assert "mutually exclusive" in res.stderr.lower(), res.stderr
        assert not side_effect_log.exists(), side_effect_log.read_text(encoding="utf-8")
        assert connections == 0
    finally:
        stop_event.set()
        server.close()
        listener.join(timeout=2)


def _pg_frame(msg_type: bytes, body: bytes) -> bytes:
    return msg_type + struct.pack("!I", 4 + len(body)) + body


def test_rehearsal_refuses_pg15_before_writes_via_shell_entrypoint() -> None:
    """Connecting --database-url to PostgreSQL 15.20 must fail before any write and never emit PG16 boundary."""
    queries: list[str] = []
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    port = server.getsockname()[1]
    stop_event = threading.Event()

    def serve() -> None:
        server.settimeout(0.2)
        while not stop_event.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                buf = b""
                startup_done = False
                while not stop_event.is_set():
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while buf:
                        if not startup_done:
                            if len(buf) < 8:
                                break
                            msg_len = struct.unpack("!I", buf[:4])[0]
                            if len(buf) < msg_len:
                                break
                            code = struct.unpack("!I", buf[4:8])[0]
                            buf = buf[msg_len:]
                            if code == 80877103:
                                conn.sendall(b"N")
                                continue
                            startup_done = True
                            conn.sendall(
                                _pg_frame(b"R", struct.pack("!I", 0))
                                + _pg_frame(b"S", b"server_version\x0015.20\x00")
                                + _pg_frame(b"K", struct.pack("!II", 1, 2))
                                + _pg_frame(b"Z", b"I")
                            )
                            continue
                        if len(buf) < 5:
                            break
                        msg_type = buf[:1]
                        msg_len = struct.unpack("!I", buf[1:5])[0]
                        if len(buf) < 1 + msg_len:
                            break
                        payload = buf[5 : 1 + msg_len]
                        buf = buf[1 + msg_len :]
                        if msg_type == b"X":
                            return
                        if msg_type == b"Q":
                            sql = payload.rstrip(b"\x00").decode("utf-8").strip()
                            queries.append(sql)
                            if sql.lower() == "show server_version":
                                col = b"server_version\x00"
                                val = b"15.20"
                            elif sql.lower() == "show server_version_num":
                                col = b"server_version_num\x00"
                                val = b"150020"
                            else:
                                err = b"SERROR\x00C42000\x00Munexpected write\x00\x00"
                                conn.sendall(_pg_frame(b"E", err) + _pg_frame(b"Z", b"I"))
                                continue
                            row_desc = _pg_frame(
                                b"T",
                                struct.pack("!H", 1) + col + struct.pack("!IhIhih", 0, 0, 25, -1, -1, 0),
                            )
                            data_row = _pg_frame(b"D", struct.pack("!HI", 1, len(val)) + val)
                            cmd_complete = _pg_frame(b"C", b"SHOW\x00")
                            ready = _pg_frame(b"Z", b"I")
                            conn.sendall(row_desc + data_row + cmd_complete + ready)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        script_path = REPO_ROOT / "examples" / "coordination" / "colima-pg16-e2e-rehearsal.sh"
        result = subprocess.run(
            [str(script_path), "--database-url", f"postgresql://postgres:secret@127.0.0.1:{port}/postgres"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert data["mode"] == "real_pg"
        assert data["execution_boundary"] == "isolated_real_postgresql_tls"
        assert data["server_version"] == "15.20"
        assert data["server_version_num"] == 150020
        assert data["server_major_version"] == 15
        assert data["pg16_qualified"] is False
        assert data["runtime_role_verified"] is False
        assert data["steps"] == []
        assert "15.20" in data.get("error", "")
        assert queries == ["SHOW server_version", "SHOW server_version_num"]
    finally:
        stop_event.set()
        server.close()
        thread.join(timeout=2)


def test_volcengine_deployment_artifacts() -> None:
    """Verify presence and validity of all Volcengine cn-beijing deployment assets."""
    doc_path = (
        REPO_ROOT
        / "docs"
        / "reference"
        / "deployment"
        / "volcengine-cn-beijing-pg-gateway.md"
    )
    assert doc_path.exists(), "Deployment reference document missing"
    doc_content = doc_path.read_text(encoding="utf-8")
    assert "严禁未经用户确认自动购买云资源或改线上配置" in doc_content
    assert "cn-beijing" in doc_content
    assert "loopx_app" in doc_content
    assert "ark.cn-beijing.volces.com" in doc_content
    assert "operator.local" in doc_content
    assert "worker.cloud" in doc_content

    # Verify public-boundary neutrality: no machine-specific or pre-existing local credential files
    for forbidden in (
        "operations-ark-video.json",
        "复用本机",
        "macOS Apple Silicon dev",
        "gateway.loopx.bj.volces.com",
    ):
        assert forbidden not in doc_content, f"Non-public local assumption {forbidden!r} in deployment doc"
    assert "EnvironmentFile" in doc_content or "环境变量" in doc_content
    assert "LOOPX_AUTHORITY_CREDENTIAL" in doc_content
    assert "ARK_API_KEY" in doc_content
    # Verify Stage 2B setup, restricted-role seed execution, and target readback instructions
    assert "postgres setup --runtime-role loopx_app --execute" in doc_content
    assert "local-to-pg-seed" in doc_content
    assert "--store-identity" in doc_content
    assert "--execute" in doc_content
    assert "ALTER ROLE loopx_app" in doc_content
    assert "GRANT CONNECT ON DATABASE" in doc_content
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT" in doc_content
    assert "PostgreSqlAuthorityStore" in doc_content
    assert "loadAuthority()" in doc_content
    assert '"operation": "snapshot"' in doc_content

    volc_dir = REPO_ROOT / "examples" / "coordination" / "volcengine"
    assert volc_dir.is_dir()

    # Terraform template: check valid published provider series (< 0.1.0) and real resource fields
    tf_path = volc_dir / "main.tf"
    assert tf_path.exists()
    tf_content = tf_path.read_text(encoding="utf-8")
    assert 'variable "region"' in tf_content
    assert 'source  = "volcengine/volcengine"' in tf_content
    assert "~> 0.1.20" not in tf_content
    assert ">= 0.0.186, < 0.1.0" in tf_content
    assert "volcengine_rds_postgresql_instance" in tf_content
    assert "node_spec" in tf_content
    assert "primary_zone_id" in tf_content
    assert "secondary_zone_id" in tf_content
    assert "charge_info" in tf_content
    assert ".endpoints" in tf_content
    # Guardrail: computed-only or non-schema root attributes must not appear as instance arguments
    rds_block = tf_content.split('resource "volcengine_rds_postgresql_instance"')[1].split("output ")[0]
    for invalid_arg in ("instance_type ", "storage_type ", "vpc_id ", "charge_type ="):
        # charge_type inside charge_info block is valid, so check root-level indentation (2 spaces)
        if invalid_arg == "charge_type =":
            assert "\n  charge_type " not in rds_block
        else:
            assert f"\n  {invalid_arg}" not in rds_block
    assert "prevent_destroy = true" in tf_content

    # Caddyfile
    caddy_path = volc_dir / "gateway-caddy-tls.Caddyfile"
    assert caddy_path.exists()
    caddy_content = caddy_path.read_text(encoding="utf-8")
    assert "reverse_proxy 127.0.0.1:8443" in caddy_content

    # Systemd service
    svc_path = volc_dir / "remote-authority-systemd.service"
    assert svc_path.exists()
    svc_content = svc_path.read_text(encoding="utf-8")
    assert "DynamicUser=yes" in svc_content
    assert "EnvironmentFile=/etc/loopx/authority.env" in svc_content
    assert "REPLACE_WITH_RESTRICTED_APP_PASSWORD" not in svc_content
    assert "remote-authority-service.ts" in svc_content

    # Least privilege IAM policy
    iam_path = volc_dir / "least-privilege-iam-policy.json"
    assert iam_path.exists()
    iam_json = json.loads(iam_path.read_text(encoding="utf-8"))
    assert "Statement" in iam_json
    actions = iam_json["Statement"][0]["Action"]
    assert "rds_postgresql:DescribeDBInstances" in actions
    # Guardrail: Mutating actions must NOT be present in worker policy
    for action in actions:
        assert not action.startswith("rds_postgresql:Create"), f"Mutating action {action} in policy"
        assert not action.startswith("rds_postgresql:Delete"), f"Destructive action {action} in policy"


def test_rehearsal_symlink_directory_invocation(tmp_path: Path) -> None:
    """Invoking .sh or .ts via a symlinked directory must still run and never false-pass 0."""
    linked_root = tmp_path / "symlink-worktree"
    linked_root.symlink_to(REPO_ROOT, target_is_directory=True)
    linked_sh = linked_root / "examples" / "coordination" / "colima-pg16-e2e-rehearsal.sh"
    linked_ts = linked_root / "examples" / "coordination" / "colima-pg16-e2e-rehearsal.ts"

    sh_sim = subprocess.run(
        [str(linked_sh), "--simulate"],
        capture_output=True,
        text=True,
        check=True,
        cwd=linked_root,
    )
    sh_data = json.loads(sh_sim.stdout)
    assert sh_data["ok"] is True
    assert len(sh_data["steps"]) == 8

    sh_bad_url = subprocess.run(
        [str(linked_sh), "--database-url", "postgresql://invalid"],
        capture_output=True,
        text=True,
        cwd=linked_root,
    )
    assert sh_bad_url.returncode != 0

    ts_sim = subprocess.run(
        [
            "node",
            "--no-warnings",
            "--experimental-sqlite",
            "--experimental-strip-types",
            str(linked_ts),
            "--simulate",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=linked_root,
    )
    ts_data = json.loads(ts_sim.stdout)
    assert ts_data["ok"] is True
    assert len(ts_data["steps"]) == 8

    ts_bad_url = subprocess.run(
        [
            "node",
            "--no-warnings",
            "--experimental-sqlite",
            "--experimental-strip-types",
            str(linked_ts),
            "--database-url",
            "postgresql://invalid",
        ],
        capture_output=True,
        text=True,
        cwd=linked_root,
    )
    assert ts_bad_url.returncode != 0
