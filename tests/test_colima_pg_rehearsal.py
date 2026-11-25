"""Verification test suite for Colima / Docker PostgreSQL 16 dual-actor rehearsal

and Volcengine cn-beijing deployment templates.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import IO

import pytest

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

    load_credentials = [
        line.split("=", 1)[1].strip()
        for line in svc_content.splitlines()
        if line.strip().startswith("LoadCredential=")
    ]
    assert "remote-policy.json:/etc/loopx/remote-policy.json" in load_credentials
    assert "authority-cert.pem:/etc/ssl/loopx/internal-cert.pem" in load_credentials
    assert "authority-key.pem:/etc/ssl/loopx/internal-key.pem" in load_credentials
    assert all("authority.env" not in entry for entry in load_credentials)
    assert all("/etc/ssl/loopx/key.pem" not in entry for entry in load_credentials)

    exec_lines = [
        line.split("=", 1)[1].strip()
        for line in svc_content.splitlines()
        if line.strip().startswith("ExecStart=")
    ]
    assert len(exec_lines) == 1
    exec_cmd = exec_lines[0]
    assert "--policy %d/remote-policy.json" in exec_cmd
    assert "--cert %d/authority-cert.pem" in exec_cmd
    assert "--key %d/authority-key.pem" in exec_cmd
    assert "/etc/loopx/" not in exec_cmd
    assert "/etc/ssl/loopx/" not in exec_cmd

    # Runbook permission, unit/Caddy installation, and isolation contract
    assert "LoadCredential=" in doc_content
    assert "%d/remote-policy.json" in doc_content
    assert "/etc/ssl/loopx/internal-key.pem" in doc_content
    assert "/etc/ssl/loopx/key.pem" in doc_content
    assert "/etc/systemd/system/remote-authority-systemd.service" in doc_content
    assert "/etc/caddy/Caddyfile" in doc_content
    assert "caddy validate" in doc_content
    assert "root:caddy" in doc_content
    assert "0640" in doc_content
    assert "0600" in doc_content
    assert "EACCES" in doc_content
    unit_install_idx = doc_content.index("/etc/systemd/system/remote-authority-systemd.service")
    reload_idx = doc_content.index("systemctl daemon-reload")
    enable_idx = doc_content.index("systemctl enable --now remote-authority-systemd.service")
    assert unit_install_idx < reload_idx < enable_idx

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

    sh_sim = subprocess.run([str(linked_sh), "--simulate"], capture_output=True, text=True, check=True, cwd=linked_root)
    sh_data = json.loads(sh_sim.stdout)
    assert sh_data["ok"] is True
    assert len(sh_data["steps"]) == 8

    sh_bad_url = subprocess.run([str(linked_sh), "--database-url", "postgresql://invalid"], capture_output=True, text=True, cwd=linked_root)
    assert sh_bad_url.returncode != 0

    ts_sim = subprocess.run(
        ["node", "--no-warnings", "--experimental-sqlite", "--experimental-strip-types", str(linked_ts), "--simulate"],
        capture_output=True, text=True, check=True, cwd=linked_root,
    )
    ts_data = json.loads(ts_sim.stdout)
    assert ts_data["ok"] is True
    assert len(ts_data["steps"]) == 8

    ts_bad_url = subprocess.run(
        ["node", "--no-warnings", "--experimental-sqlite", "--experimental-strip-types", str(linked_ts), "--database-url", "postgresql://invalid"],
        capture_output=True, text=True, cwd=linked_root,
    )
    assert ts_bad_url.returncode != 0


def _docker_env() -> dict[str, str]:
    env = dict(os.environ)
    if "DOCKER_HOST" not in env and "DOCKER_CONFIG" not in env and os.name == "posix":
        import pwd

        docker_cfg = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".docker"
        if docker_cfg.is_dir():
            env["DOCKER_CONFIG"] = str(docker_cfg)
    return env


def _docker_images_ready() -> tuple[bool, str]:
    env = _docker_env()
    info = subprocess.run(["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True, timeout=5, env=env)
    if info.returncode != 0 or info.stdout.strip() != "linux":
        return False, "Linux Docker daemon unavailable"
    for image in ("node:22-bookworm", "postgres:16-alpine"):
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, timeout=5, env=env).returncode != 0:
            return False, f"Required cached Docker image missing: {image}"
    return True, ""


def _resolve_host_node_modules_root(repo_root: Path) -> Path | None:
    if (repo_root / "node_modules" / "pg" / "package.json").is_file():
        return repo_root
    common = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"], capture_output=True, text=True, timeout=5)
    if common.returncode == 0 and common.stdout.strip():
        raw_dir = Path(common.stdout.strip())
        resolved = raw_dir if raw_dir.is_absolute() else (repo_root / raw_dir).resolve()
        if (resolved.parent / "node_modules" / "pg" / "package.json").is_file():
            return resolved.parent
    return None


def _resolve_node_pkg(nm_dir: Path, pkg_name: str, parent_dir: Path | None, parent_arc: str) -> tuple[str, Path] | None:
    candidates: list[tuple[str, Path]] = []
    if parent_dir is not None:
        candidates.append((f"{parent_arc}/node_modules/{pkg_name}", parent_dir / "node_modules" / pkg_name))
    candidates.append((pkg_name, nm_dir / pkg_name))
    for arc_rel, candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if (resolved / "package.json").is_file():
            return arc_rel, resolved
    return None


def _collect_runtime_node_packages(nm_dir: Path, root_pkg: str = "pg") -> dict[str, Path]:
    pending: list[tuple[str, bool, Path | None, str]] = [(root_pkg, True, None, "")]
    staged: dict[str, Path] = {}
    seen_targets: set[tuple[str, bool]] = set()
    while pending:
        pkg_name, required, parent_dir, parent_arc = pending.pop(0)
        found = _resolve_node_pkg(nm_dir, pkg_name, parent_dir, parent_arc)
        if found is None:
            if required:
                raise AssertionError(f"Required npm package {pkg_name!r} missing or dangling under {nm_dir}")
            continue
        arc_rel, resolved = found
        if (arc_rel, required) in seen_targets:
            continue
        seen_targets.add((arc_rel, required))
        staged[arc_rel] = resolved
        meta = json.loads((resolved / "package.json").read_text(encoding="utf-8"))
        for dep in meta.get("dependencies") or {}:
            pending.append((dep, True, resolved, arc_rel))
        for dep in meta.get("optionalDependencies") or {}:
            pending.append((dep, False, resolved, arc_rel))
    return staged


def _build_opt_loopx_tar(repo_root: Path, nm_root: Path, archive: IO[bytes]) -> None:
    def _normalize(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if "__pycache__" in ti.name.split("/") or ti.issym() or ti.islnk():
            return None
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        ti.mode = 0o755 if ti.isdir() or (ti.mode & 0o111) else 0o644
        return ti

    nm_dir = (nm_root / "node_modules").resolve(strict=True)
    runtime_pkgs = _collect_runtime_node_packages(nm_dir, "pg")
    with tarfile.open(fileobj=archive, mode="w", dereference=False) as tf:
        for rel in ("package.json", "loopx/control_plane", "examples/coordination"):
            tf.add((repo_root / rel).resolve(strict=True), arcname=f"loopx/{rel}", filter=_normalize)
        tf.add(nm_dir, arcname="loopx/node_modules", recursive=False, filter=_normalize)
        for arc_rel, resolved in runtime_pkgs.items():
            tf.add(resolved, arcname=f"loopx/node_modules/{arc_rel}", filter=_normalize)


def _run_node_container_with_opt_loopx(
    docker_env: dict[str, str], net_name: str, repo_root: Path, container_script: str, env_vars: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    env_args = [item for k, v in env_vars.items() for item in ("-e", f"{k}={v}")]
    probe = subprocess.run(
        [
            "docker", "run", "--rm", "-v", f"{repo_root}:/opt/loopx:ro", "node:22-bookworm",
            "test", "-f", "/opt/loopx/node_modules/pg/package.json",
            "-a", "-f", "/opt/loopx/examples/coordination/remote-authority-service.ts",
        ],
        capture_output=True, text=True, timeout=15, env=docker_env,
    )
    if probe.returncode == 0:
        return subprocess.run(
            ["docker", "run", "--rm", "-i", "--network", net_name, "-v", f"{repo_root}:/opt/loopx:ro", "-w", "/opt/loopx", *env_args, "node:22-bookworm", "bash", "-s"],
            input=container_script, capture_output=True, text=True, timeout=90, env=docker_env,
        )

    nm_root = _resolve_host_node_modules_root(repo_root)
    assert nm_root is not None, "Host node_modules/pg is required for container check"
    cid = subprocess.run(
        ["docker", "create", "-i", "--network", net_name, "-w", "/opt/loopx", *env_args, "node:22-bookworm", "bash", "-s"],
        check=True, capture_output=True, text=True, env=docker_env,
    ).stdout.strip()
    try:
        with tempfile.TemporaryFile() as archive:
            _build_opt_loopx_tar(repo_root, nm_root, archive)
            archive.seek(0)
            subprocess.run(["docker", "cp", "-", f"{cid}:/opt"], stdin=archive, check=True, capture_output=True, env=docker_env)
        return subprocess.run(
            ["docker", "start", "-a", "-i", cid], input=container_script, capture_output=True, text=True, timeout=90, env=docker_env,
        )
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, text=True, env=docker_env)


def _assert_nonroot_load_credential_snapshot(repo_root: Path) -> None:
    ready, reason = _docker_images_ready()
    if not ready:
        pytest.skip(reason)

    docker_env = _docker_env()
    suffix = uuid.uuid4().hex[:10]
    net_name = f"loopx-cred-net-{suffix}"
    pg_name = f"loopx-cred-pg-{suffix}"
    pg_admin_pw = f"pgadm_{suffix}"
    pg_app_pw = f"pgapp_{suffix}"

    subprocess.run(["docker", "network", "create", net_name], check=True, capture_output=True, text=True, env=docker_env)
    try:
        subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", pg_name, "--network", net_name, "-e", f"POSTGRES_PASSWORD={pg_admin_pw}", "postgres:16-alpine"],
            check=True, capture_output=True, text=True, env=docker_env,
        )
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            if subprocess.run(["docker", "exec", pg_name, "pg_isready", "-U", "postgres"], capture_output=True, text=True, env=docker_env).returncode == 0:
                break
            time.sleep(0.5)
        else:
            pytest.fail("PostgreSQL container did not become ready within 25s")

        container_script = r"""
set -eu
ln -sf "$(command -v node)" /usr/bin/node
groupadd -r caddy
useradd -r -g caddy -s /usr/sbin/nologin caddy

install -d -o root -g root -m 0700 /etc/loopx
install -d -o root -g caddy -m 0750 /etc/ssl/loopx

openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -subj "/CN=127.0.0.1" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
  -keyout /etc/ssl/loopx/internal-key.pem -out /etc/ssl/loopx/internal-cert.pem 2>/dev/null
chown root:root /etc/ssl/loopx/internal-key.pem /etc/ssl/loopx/internal-cert.pem
chmod 0600 /etc/ssl/loopx/internal-key.pem
chmod 0644 /etc/ssl/loopx/internal-cert.pem
install -m 0644 /etc/ssl/loopx/internal-cert.pem /run/public-internal-ca.pem

openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -subj "/CN=gateway.loopx.internal" \
  -keyout /etc/ssl/loopx/key.pem -out /etc/ssl/loopx/cert.pem 2>/dev/null
chown root:root /etc/ssl/loopx/cert.pem
chmod 0644 /etc/ssl/loopx/cert.pem
chown root:caddy /etc/ssl/loopx/key.pem
chmod 0640 /etc/ssl/loopx/key.pem

STORE_ID="postgresql:$(node -e 'console.log(require("crypto").randomBytes(16).toString("hex"))')"
BEARER_SECRET="cred-test-secret-$(node -e 'console.log(require("crypto").randomBytes(16).toString("hex"))')"
ADMIN_URL="postgresql://postgres:${PG_ADMIN_PW}@${PG_HOST}:5432/postgres"
APP_URL="postgresql://loopx_app:${PG_APP_PW}@${PG_HOST}:5432/postgres"
export ADMIN_URL APP_URL PG_APP_PW STORE_ID BEARER_SECRET

node --no-warnings --experimental-strip-types --input-type=module - <<'SEED_EOF'
import {createHash} from "node:crypto";
import {writeFileSync} from "node:fs";
import {Pool} from "pg";
import {coordinationTodoReadModel} from "./loopx/control_plane/coordination/coordination_projection.ts";
import {
  canonicalTodoDomainRecord,
  TODO_DOMAIN_ITEM_SCHEMA,
  TODO_DOMAIN_READ_RECORD_SCHEMA,
} from "./loopx/control_plane/coordination/coordination_state_contract.ts";
import {setupPostgreSqlAuthoritySchemaAndRole} from "./loopx/control_plane/coordination/postgresql_authority_seed.ts";
import {PostgreSqlAuthorityStore} from "./loopx/control_plane/coordination/postgresql_authority_store.ts";
import {REMOTE_AUTHORITY_POLICY} from "./loopx/control_plane/coordination/remote_authority_policy.ts";
import {materializeTodoRecordForSchema} from "./loopx/control_plane/coordination/todo_presentation.ts";

const makeDb = (pool) => ({connect: async () => {const c = await pool.connect(); return {query: (s, a) => c.query(s, a ? [...a] : undefined), release: (e) => c.release(e)};}});
const adminPool = new Pool({connectionString: process.env.ADMIN_URL});
try {
  await setupPostgreSqlAuthoritySchemaAndRole({database: makeDb(adminPool), storeIdentity: process.env.STORE_ID, runtimeRole: "loopx_app"});
  await adminPool.query(`ALTER ROLE loopx_app LOGIN PASSWORD '${process.env.PG_APP_PW}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT`);
  await adminPool.query("GRANT CONNECT ON DATABASE postgres TO loopx_app");
} finally {await adminPool.end();}
const appPool = new Pool({connectionString: process.env.APP_URL});
try {
  const store = new PostgreSqlAuthorityStore(makeDb(appPool), {tenant_id: "tenant-cred", goal_id: "goal-cred", expected_store_identity: process.env.STORE_ID});
  const todo = materializeTodoRecordForSchema(canonicalTodoDomainRecord({
    schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_cred_verify", role: "agent", status: "open", done: false, archive_state: "active",
    text: "Verify systemd LoadCredential non-root readback", priority: "P1", task_class: "advancement_task", required_write_scopes: ["todo_cred_verify/**"],
  }, "cred verify todo domain"), TODO_DOMAIN_READ_RECORD_SCHEMA, "cred verify todo");
  const res = await store.commitAuthority({
    operation_id: "seed-systemd-cred-test", expected_provider_revision: null, events: [], receipts: [],
    next_projection: {goal_id: "goal-cred", handoff_mode: "hard_lease", todos: [todo], leases: [], todo_read_model: coordinationTodoReadModel([todo], TODO_DOMAIN_READ_RECORD_SCHEMA)},
  });
  if (res.status !== "applied") throw new Error(`Seed failed: ${JSON.stringify(res)}`);
} finally {await appPool.end();}

const policy = {
  schema_version: REMOTE_AUTHORITY_POLICY,
  scopes: [{
    tenant_id: "tenant-cred",
    goal_id: "goal-cred",
    store_identity: process.env.STORE_ID,
    registered_agents: ["operator.local"],
  }],
  principals: [{
    principal_id: "principal-operator",
    credential_sha256: createHash("sha256").update(process.env.BEARER_SECRET).digest("hex"),
    grants: [{
      tenant_id: "tenant-cred",
      goal_id: "goal-cred",
      actor_id: "operator.local",
      operations: ["read", "snapshot"],
    }],
  }],
};
writeFileSync("/etc/loopx/remote-policy.json", JSON.stringify(policy));
SEED_EOF

chown root:root /etc/loopx/remote-policy.json
chmod 0600 /etc/loopx/remote-policy.json

printf 'LOOPX_AUTHORITY_DATABASE_URL=%s\n' "$APP_URL" > /etc/loopx/authority.env
chown root:root /etc/loopx/authority.env
chmod 0600 /etc/loopx/authority.env

install -d -o root -g root -m 0755 /etc/systemd/system /etc/caddy
install -d -o caddy -g caddy -m 0750 /var/log/caddy
install -o root -g root -m 0644 /opt/loopx/examples/coordination/volcengine/remote-authority-systemd.service \
  /etc/systemd/system/remote-authority-systemd.service
install -o root -g root -m 0644 /opt/loopx/examples/coordination/volcengine/gateway-caddy-tls.Caddyfile \
  /etc/caddy/Caddyfile

UNIT_FILE="/etc/systemd/system/remote-authority-systemd.service"
EXEC_TEMPLATE="$(sed -n 's/^ExecStart=//p' "$UNIT_FILE")"
CRED_DIR="/run/credentials/remote-authority-systemd.service"
install -d -o 1000 -g 1000 -m 0500 "$CRED_DIR"
for spec in $(sed -n 's/^LoadCredential=//p' "$UNIT_FILE"); do
  cid="${spec%%:*}"
  csrc="${spec#*:}"
  install -o 1000 -g 1000 -m 0400 "$csrc" "$CRED_DIR/$cid"
done

# 1. Negative check: non-root reading raw 0600 root-owned key directly must fail EACCES (exit 1)
RAW_ERR_LOG="/run/raw-eacces.log"
set +e
LOOPX_AUTHORITY_DATABASE_URL="$APP_URL" setpriv --reuid=1000 --regid=1000 --clear-groups \
  /usr/bin/node --no-warnings --experimental-strip-types examples/coordination/remote-authority-service.ts \
  --policy "$CRED_DIR/remote-policy.json" --cert "$CRED_DIR/authority-cert.pem" \
  --key /etc/ssl/loopx/internal-key.pem --port 8443 --host 127.0.0.1 > /dev/null 2>"$RAW_ERR_LOG"
RAW_KEY_RC=$?
set -e
grep -q "EACCES" "$RAW_ERR_LOG"
[ "$RAW_KEY_RC" -eq 1 ]

# 2. Caddy isolation: node cannot read Caddy key/raw policy/db env; caddy reads only cert.pem & key.pem
! setpriv --reuid=1000 --regid=1000 --clear-groups head -c 1 /etc/ssl/loopx/key.pem 2>/dev/null
! setpriv --reuid=1000 --regid=1000 --clear-groups head -c 1 /etc/ssl/loopx/internal-key.pem 2>/dev/null
! setpriv --reuid=1000 --regid=1000 --clear-groups head -c 1 /etc/loopx/remote-policy.json 2>/dev/null
! setpriv --reuid=1000 --regid=1000 --clear-groups head -c 1 /etc/loopx/authority.env 2>/dev/null

CADDY_UID="$(id -u caddy)"
CADDY_GID="$(id -g caddy)"
setpriv --reuid="$CADDY_UID" --regid="$CADDY_GID" --clear-groups head -c 1 /etc/ssl/loopx/cert.pem >/dev/null
setpriv --reuid="$CADDY_UID" --regid="$CADDY_GID" --clear-groups head -c 1 /etc/ssl/loopx/key.pem >/dev/null
! setpriv --reuid="$CADDY_UID" --regid="$CADDY_GID" --clear-groups head -c 1 /etc/ssl/loopx/internal-key.pem 2>/dev/null
! setpriv --reuid="$CADDY_UID" --regid="$CADDY_GID" --clear-groups head -c 1 /etc/loopx/remote-policy.json 2>/dev/null
! setpriv --reuid="$CADDY_UID" --regid="$CADDY_GID" --clear-groups head -c 1 /etc/loopx/authority.env 2>/dev/null

# 3. Positive check: non-root ExecStart using %d/ credentials + separate EnvironmentFile listens and serves snapshot
EXEC_CMD="${EXEC_TEMPLATE//%d/$CRED_DIR}"
SVC_OUT="/run/svc-stdout.log"
set -a
. /etc/loopx/authority.env
set +a
CREDENTIALS_DIRECTORY="$CRED_DIR" setpriv --reuid=1000 --regid=1000 --clear-groups \
  sh -c "exec $EXEC_CMD" >"$SVC_OUT" 2>&1 &
SVC_PID=$!
unset LOOPX_AUTHORITY_DATABASE_URL

LISTENING_JSON=""
for _ in $(seq 1 50); do
  if grep -q '"status":"listening"' "$SVC_OUT" 2>/dev/null; then
    LISTENING_JSON="$(grep '"status":"listening"' "$SVC_OUT" | head -n 1)"
    break
  fi
  if ! kill -0 "$SVC_PID" 2>/dev/null; then
    cat "$SVC_OUT" >&2
    exit 1
  fi
  sleep 0.2
done
[ -n "$LISTENING_JSON" ]
SVC_UID="$(ps -o uid= -p "$SVC_PID" | tr -d ' ')"
[ "$SVC_UID" = "1000" ]

SNAPSHOT_JSON="$(printf '{"schema_version":"loopx_remote_authority_request_v0","tenant_id":"tenant-cred","goal_id":"goal-cred","actor_id":"operator.local","store_identity":"%s","operation":"snapshot"}' "$STORE_ID" | \
  LOOPX_AUTHORITY_CREDENTIAL="$BEARER_SECRET" setpriv --reuid=1000 --regid=1000 --clear-groups \
  /usr/bin/node --no-warnings --experimental-strip-types examples/coordination/remote-authority-client.ts \
  --endpoint https://127.0.0.1:8443 --ca /run/public-internal-ca.pem)"

kill -TERM "$SVC_PID"
wait "$SVC_PID" || true

node -e '
const listening = JSON.parse(process.argv[1]);
const snapshot = JSON.parse(process.argv[2]);
console.log(JSON.stringify({
  raw_key_eacces_exit: Number(process.argv[3]),
  service_uid: Number(process.argv[4]),
  listening,
  snapshot_status: snapshot.status,
  goal_id: snapshot.goal_id,
  store_identity: snapshot.store_identity,
  todo_ids: (snapshot.todos || []).map(t => t.todo_id),
  caddy_isolated: true,
}));
' "$LISTENING_JSON" "$SNAPSHOT_JSON" "$RAW_KEY_RC" "$SVC_UID"
"""

        res = _run_node_container_with_opt_loopx(
            docker_env, net_name, repo_root, container_script,
            {"PG_HOST": pg_name, "PG_ADMIN_PW": pg_admin_pw, "PG_APP_PW": pg_app_pw},
        )
        assert res.returncode == 0, f"Container verification failed (rc={res.returncode}):\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        report = json.loads(res.stdout.strip().splitlines()[-1])
        assert report["raw_key_eacces_exit"] == 1
        assert report["service_uid"] == 1000
        assert report["listening"] == {"status": "listening", "host": "127.0.0.1", "port": 8443}
        assert report["snapshot_status"] == "snapshot"
        assert report["goal_id"] == "goal-cred"
        assert report["todo_ids"] == ["todo_cred_verify"]
        assert report["caddy_isolated"] is True
    finally:
        subprocess.run(["docker", "rm", "-f", pg_name], capture_output=True, text=True, env=docker_env)
        subprocess.run(["docker", "network", "rm", net_name], capture_output=True, text=True, env=docker_env)


def test_systemd_load_credential_nonroot_linux_isolation_and_snapshot() -> None:
    """Verify non-root EACCES on raw 0600 root keys vs LoadCredential readback in Linux."""
    _assert_nonroot_load_credential_snapshot(REPO_ROOT)


def test_detached_worktree_staging_ignores_dangling_unrelated_link(tmp_path: Path) -> None:
    """Verify detached worktree staging tolerates dangling/looping unrelated links and fails cleanly on missing pg deps."""
    nm_root = _resolve_host_node_modules_root(REPO_ROOT)
    assert nm_root is not None
    wt = tmp_path / "detached-wt"
    (wt / "loopx").mkdir(parents=True)
    (wt / "examples").mkdir(parents=True)
    (wt / "node_modules").mkdir()
    (wt / "package.json").symlink_to(REPO_ROOT / "package.json")
    (wt / "loopx" / "control_plane").symlink_to(REPO_ROOT / "loopx" / "control_plane")
    (wt / "examples" / "coordination").symlink_to(REPO_ROOT / "examples" / "coordination")
    for entry in (nm_root / "node_modules").iterdir():
        if not entry.name.startswith("."):
            (wt / "node_modules" / entry.name).symlink_to(entry.resolve())
    (wt / "node_modules" / f"unused-local-link-{uuid.uuid4().hex[:6]}").symlink_to(tmp_path / "deleted-target")
    (wt / "node_modules" / ".node_modules-loop").symlink_to(wt / "node_modules")
    # Missing required transitive dependency must fail with explicit package name.
    broken_nm = tmp_path / "broken_nm" / "node_modules"
    broken_nm.mkdir(parents=True)
    for entry in (nm_root / "node_modules").iterdir():
        if not entry.name.startswith(".") and entry.name != "postgres-array":
            (broken_nm / entry.name).symlink_to(entry.resolve())
    with pytest.raises(AssertionError, match="postgres-array"):
        _collect_runtime_node_packages(broken_nm, "pg")
    _assert_nonroot_load_credential_snapshot(wt)
