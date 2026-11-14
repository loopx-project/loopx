"""Verification test suite for Colima / Docker PostgreSQL 16 dual-actor rehearsal

and Volcengine cn-beijing deployment templates.
"""

from __future__ import annotations

import json
import os
import subprocess
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

    volc_dir = REPO_ROOT / "examples" / "coordination" / "volcengine"
    assert volc_dir.is_dir()

    # Terraform template
    tf_path = volc_dir / "main.tf"
    assert tf_path.exists()
    tf_content = tf_path.read_text(encoding="utf-8")
    assert 'variable "region"' in tf_content
    assert "volcengine_rds_postgresql_instance" in tf_content
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
