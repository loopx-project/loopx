from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from loopx.capabilities.external_research import cli
from loopx.control_plane.effect_runtime import restart_effect_runtime


def _print_payload(payload, _format, _renderer):
    _print_payload.payload = payload


def test_discover_projects_connector_registry_as_inventory_only(
    tmp_path: Path, monkeypatch
) -> None:
    registry_path = tmp_path / "connectors.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "connector_registry_v1",
                "connectors": [
                    {
                        "id": "official-docs",
                        "name": "Official docs",
                        "layer": "L1",
                        "kind": "documentation",
                        "status": "supported",
                        "value_tier": "P1",
                    }
                ],
                "usage": {},
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_runtime(method, params):
        captured["method"] = method
        captured["params"] = params
        return {
            "schema_version": "loopx_external_evidence_discovery_v0",
            "status": "inventory_only",
        }

    monkeypatch.setattr(cli, "effect_runtime_result", fake_runtime)
    args = argparse.Namespace(
        command="external-evidence",
        external_evidence_action="discover",
        provider_inventory_json=None,
        connector_registry=str(registry_path),
    )
    assert (
        cli.handle_external_evidence_command(
            args,
            output_format=lambda _args: "json",
            print_payload=_print_payload,
        )
        == 0
    )
    assert captured["method"] == "external_evidence.discover"
    connector = next(
        provider
        for provider in captured["params"]["providers"]
        if provider["provider_id"] == "connector:official-docs"
    )
    assert connector == {
        "provider_id": "connector:official-docs",
        "provider_kind": "connector",
        "protocol": "external_evidence_research_v0",
        "declared": True,
        "installed": False,
        "enabled": False,
        "ready": False,
        "unavailable_reason": "connector_registry_is_inventory_not_readiness",
    }


def test_plan_projects_registry_as_inventory_not_readiness(
    tmp_path: Path, monkeypatch
) -> None:
    provider_path = tmp_path / "providers.json"
    provider_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "provider_id": "host:external-research",
                        "provider_kind": "method",
                        "protocol": "external_evidence_research_v0",
                        "declared": True,
                        "installed": True,
                        "enabled": True,
                        "ready": True,
                        "unavailable_reason": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "connectors.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "connector_registry_v1",
                "connectors": [
                    {
                        "id": "official-docs",
                        "name": "Official docs",
                        "layer": "L1",
                        "kind": "documentation",
                        "status": "supported",
                        "value_tier": "P1",
                    }
                ],
                "usage": {},
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_runtime(method, params):
        captured["method"] = method
        captured["params"] = params
        return {"schema_version": "loopx_external_evidence_plan_v0", "status": "ready"}

    monkeypatch.setattr(cli, "effect_runtime_result", fake_runtime)
    args = argparse.Namespace(
        command="external-evidence",
        external_evidence_action="plan",
        objective="Inspect current behavior",
        user_activity="Choose a provider",
        decision="Whether to adopt",
        evidence_kind=["current_behavior"],
        constraint=[],
        provider_inventory_json=str(provider_path),
        connector_registry=str(registry_path),
        preferred_provider_id="host:external-research",
    )
    assert (
        cli.handle_external_evidence_command(
            args,
            output_format=lambda _args: "json",
            print_payload=_print_payload,
        )
        == 0
    )
    assert captured["method"] == "external_evidence.plan"
    providers = captured["params"]["providers"]
    connector = next(
        row for row in providers if row["provider_id"] == "connector:official-docs"
    )
    assert connector["ready"] is False
    assert (
        connector["unavailable_reason"]
        == "connector_registry_is_inventory_not_readiness"
    )


def test_admit_passes_parent_decision_to_typed_owner(
    tmp_path: Path, monkeypatch
) -> None:
    plan_path = tmp_path / "plan.json"
    receipt_path = tmp_path / "receipt.json"
    plan_path.write_text(
        json.dumps({"schema_version": "loopx_external_evidence_plan_v0"})
    )
    receipt_path.write_text(
        json.dumps({"schema_version": "loopx_external_evidence_receipt_v0"})
    )
    captured = {}

    def fake_runtime(method, params):
        captured["method"] = method
        captured["params"] = params
        return {
            "schema_version": "loopx_external_evidence_admission_v0",
            "disposition": "reject",
        }

    monkeypatch.setattr(cli, "effect_runtime_result", fake_runtime)
    args = argparse.Namespace(
        command="external-evidence",
        external_evidence_action="admit",
        plan_json=str(plan_path),
        receipt_json=str(receipt_path),
        decision="reject",
        reason="Insufficient direct evidence",
        admit_source=[],
    )
    assert (
        cli.handle_external_evidence_command(
            args,
            output_format=lambda _args: "json",
            print_payload=_print_payload,
        )
        == 0
    )
    assert captured["method"] == "external_evidence.admit"
    assert captured["params"]["decision"]["disposition"] == "reject"


def test_receipt_passes_observed_execution_to_typed_owner(
    tmp_path: Path, monkeypatch
) -> None:
    plan_path = tmp_path / "plan.json"
    receipt_path = tmp_path / "receipt.json"
    plan_path.write_text(
        json.dumps({"schema_version": "loopx_external_evidence_plan_v0"})
    )
    receipt_path.write_text(
        json.dumps({"schema_version": "loopx_external_evidence_receipt_v0"})
    )
    captured = {}

    def fake_runtime(method, params):
        captured["method"] = method
        captured["params"] = params
        return {
            "schema_version": "loopx_external_evidence_receipt_observation_v0",
            "status": "succeeded",
        }

    monkeypatch.setattr(cli, "effect_runtime_result", fake_runtime)
    args = argparse.Namespace(
        command="external-evidence",
        external_evidence_action="receipt",
        plan_json=str(plan_path),
        receipt_json=str(receipt_path),
    )
    assert (
        cli.handle_external_evidence_command(
            args,
            output_format=lambda _args: "json",
            print_payload=_print_payload,
        )
        == 0
    )
    assert captured["method"] == "external_evidence.receipt"
    assert (
        captured["params"]["plan"]["schema_version"]
        == "loopx_external_evidence_plan_v0"
    )


def test_source_cli_reaches_typescript_owner(tmp_path: Path) -> None:
    provider_path = tmp_path / "providers.json"
    provider_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "provider_id": "host:external-research",
                        "provider_kind": "method",
                        "protocol": "external_evidence_research_v0",
                        "declared": True,
                        "installed": True,
                        "enabled": True,
                        "ready": True,
                        "unavailable_reason": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "external-evidence",
            "plan",
            "--objective",
            "Inspect current behavior",
            "--user-activity",
            "Choose a provider",
            "--decision",
            "Whether to adopt",
            "--evidence-kind",
            "current_behavior",
            "--provider-inventory-json",
            str(provider_path),
            "--preferred-provider-id",
            "host:external-research",
            "--format",
            "json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "loopx_external_evidence_plan_v0"
    assert payload["status"] == "ready"
    assert payload["selected_provider"]["provider_id"] == "host:external-research"


def test_source_cli_discovers_inventory_without_claiming_readiness(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "connectors.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "connector_registry_v1",
                "connectors": [
                    {
                        "id": "official-docs",
                        "name": "Official docs",
                        "layer": "L1",
                        "kind": "documentation",
                        "status": "supported",
                        "value_tier": "P1",
                    }
                ],
                "usage": {},
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "external-evidence",
            "discover",
            "--connector-registry",
            str(registry_path),
            "--format",
            "json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "loopx_external_evidence_discovery_v0"
    assert payload["status"] == "inventory_only"
    assert payload["summary"]["provider_count"] >= 1
    assert payload["summary"]["connector_count"] == payload["summary"]["provider_count"]
    assert payload["summary"]["ready_count"] == 0
    assert payload["truth_contract"]["execution_observed"] is False


@pytest.mark.parametrize("field,value", [
    ("selected_provider", None),
    ("selected_provider", []),
    ("selected_provider", "invalid"),
    ("selected_provider", 1),
    ("request", None),
    ("request", []),
    ("request", "invalid"),
    ("request", 1),
])
def test_execute_reports_typed_errors_before_provider_and_recovers(
    tmp_path: Path, monkeypatch, field: str, value: object,
) -> None:
    from loopx.extensions import public_github_research as provider

    source = "https://github.com/example/public/blob/" + "a" * 40 + "/README.md"
    plan = cli.effect_runtime_result("external_evidence.plan", {
        "request": {
            "objective": "Inspect a synthetic source",
            "user_activity": "Choose a research provider",
            "decision": "Whether to use the source",
            "evidence_kinds": ["literal_match"], "source_refs": [source],
        },
        "providers": [{
            "provider_id": provider.PROVIDER_ID, "provider_kind": "method",
            "protocol": "external_evidence_research_v0", "declared": True,
            "installed": True, "enabled": True, "ready": True,
            "unavailable_reason": None,
        }],
    })
    malformed = {**plan, field: value}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(malformed), encoding="utf-8")
    reads = []

    def read(url):
        reads.append(url)
        return b'{"private": false}' if url.startswith("https://api.github.com/") else b"Synthetic source\n"

    monkeypatch.setattr(provider, "_read", read)
    payloads = []
    args = argparse.Namespace(
        command="external-evidence", external_evidence_action="execute",
        plan_json=str(path), execute=True,
    )
    def emit(payload, *_):
        payloads.append(payload)
    assert cli.handle_external_evidence_command(
        args, output_format=lambda _: "json", print_payload=emit,
    ) == 1
    assert payloads[-1]["schema_version"] == "loopx_external_evidence_error_v0"
    assert payloads[-1]["status"] == "invalid_request"
    assert reads == []
    assert malformed == {**plan, field: value}

    path.write_text(json.dumps(plan), encoding="utf-8")
    assert cli.handle_external_evidence_command(
        args, output_format=lambda _: "json", print_payload=emit,
    ) == 0
    assert len(reads) == 2
    assert payloads[-1]["receipt"]["status"] == "succeeded"
    assert payloads[-1]["observation"]["plan_id"] == plan["plan_id"]
    assert payloads[-1]["execution"]["automatic_admission"] is False


def test_source_cli_execute_keeps_blocked_plan_structured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    providers = tmp_path / "providers.json"
    providers.write_text('{"providers": []}', encoding="utf-8")
    command = [
        sys.executable, "-m", "loopx.entrypoint", "--runtime-root",
        str(tmp_path / "runtime"), "--registry", str(tmp_path / "registry.json"),
        "--format", "json", "external-evidence",
    ]
    result = subprocess.run(command + [
        "plan", "--objective", "Inspect synthetic availability", "--user-activity",
        "Choose a provider", "--decision", "Whether to collect evidence",
        "--evidence-kind", "current_behavior", "--provider-inventory-json", str(providers),
    ], capture_output=True, text=True, check=True, timeout=30)
    plan = json.loads(result.stdout)
    assert plan["status"] == "blocked" and plan["selected_provider"] is None
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    result = subprocess.run(command + [
        "execute", "--plan-json", str(path), "--execute",
    ], capture_output=True, text=True, check=False, timeout=30)
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["status"] == "invalid_request"
    assert "ready plan" in payload["error"]
    assert "Traceback" not in result.stderr
    assert json.loads(path.read_text(encoding="utf-8")) == plan


@pytest.mark.parametrize("duplicate", [
    "https://example.com/original", "  https://example.com/original  ",
])
def test_source_cli_rejects_duplicate_admission_and_accepts_corrected_input(
    tmp_path: Path, monkeypatch, duplicate: str,
) -> None:
    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(variable, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")

    def run(*args: str):
        result = subprocess.run(
            [sys.executable, "-m", "loopx.entrypoint", "--runtime-root",
             str(tmp_path / "runtime"), "--registry", str(tmp_path / "registry.json"),
             "external-evidence", *args, "--format", "json"],
            cwd=Path(__file__).resolve().parents[2], capture_output=True,
            text=True, encoding="utf-8", check=False, timeout=30,
        )
        return result.returncode, json.loads(result.stdout)

    def save(name: str, payload: dict) -> str:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    try:
        providers = save("providers.json", {"providers": [{
            "provider_id": "host:external-research", "provider_kind": "method",
            "protocol": "external_evidence_research_v0", "declared": True,
            "installed": True, "enabled": True, "ready": True,
            "unavailable_reason": None,
        }]})
        code, plan = run(
            "plan", "--objective", "Inspect public evidence", "--user-activity",
            "Choose a provider", "--decision", "Whether to adopt",
            "--evidence-kind", "current_behavior", "--provider-inventory-json", providers,
        )
        assert code == 0, plan
        source_ref = "https://example.com/original"
        receipt = {
            "schema_version": "loopx_external_evidence_receipt_v0",
            "plan_id": plan["plan_id"], "request_id": plan["request"]["request_id"],
            "provider_id": "host:external-research", "provider_kind": "method",
            "status": "succeeded", "summary": "Synthetic public evidence",
            "completed_at": "2026-09-28T00:01:00Z", "sources": [{
                "source_ref": source_ref, "source_family": "public-fixture",
                "basis": "observed", "finding": "The fixture supports the decision.",
                "accessed_at": "2026-09-28T00:00:00Z",
                "content_digest": "sha256:" + "a" * 64,
            }],
        }
        admit_args = (
            "admit", "--plan-json", save("plan.json", plan),
            "--receipt-json", save("receipt.json", receipt),
            "--decision", "admit", "--reason", "Direct evidence",
            "--admit-source", source_ref,
        )
        code, rejected = run(*admit_args, "--admit-source", duplicate)
        assert code == 1, rejected
        assert rejected["status"] == "invalid_request"
        assert "decision.admitted_source_refs must be unique" in rejected["error"]
        assert "admission_id" not in rejected

        code, admission = run(*admit_args)
        assert code == 0, admission
        assert admission["admitted_source_refs"] == [source_ref]
        code, retirement = run(
            "retire", "--admission-json", save("admission.json", admission),
            "--downstream-source", source_ref,
        )
        assert code == 0, retirement
        assert retirement["status"] == "retire_ready"
    finally:
        restart_effect_runtime()


@pytest.mark.parametrize("publication_date", [None, "2026-09-20"])
def test_source_cli_preserves_receipt_clocks_and_idempotent_ledger(
    tmp_path: Path, monkeypatch, publication_date: str | None,
) -> None:
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    project = tmp_path / "research"
    project.mkdir()

    def run(*args: str):
        result = subprocess.run(
            [sys.executable, "-m", "loopx.entrypoint", "--runtime-root",
             str(tmp_path / "runtime"), "--registry", str(tmp_path / "registry.json"),
             "--format", "json", *args],
            cwd=Path(__file__).resolve().parents[2], capture_output=True,
            text=True, encoding="utf-8", check=False, timeout=30,
        )
        return result.returncode, json.loads(result.stdout)

    def save(name: str, payload: dict) -> str:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    try:
        objective = "Inspect captured source clocks"
        code, started = run("deepresearch", "start", "--project", str(project),
                            "--question", objective, "--max-sources", "1")
        assert code == 0, started
        ledger = Path(started["state_path"])
        providers = save("providers.json", {"providers": [{
            "provider_id": "host:external-research", "provider_kind": "method",
            "protocol": "external_evidence_research_v0", "declared": True,
            "installed": True, "enabled": True, "ready": True,
        }]})
        code, plan = run("external-evidence", "plan", "--objective", objective,
                         "--user-activity", "Review a capture", "--decision", "Adopt source",
                         "--evidence-kind", "original_source", "--provider-inventory-json", providers)
        assert code == 0, plan
        plan_path = save("plan.json", plan)
        source_ref = "https://example.com/captured"
        accessed_at = "2026-09-21T10:12:13.123456Z"
        receipt = {
            "schema_version": "loopx_external_evidence_receipt_v0",
            "plan_id": plan["plan_id"], "request_id": plan["request"]["request_id"],
            "provider_id": "host:external-research", "provider_kind": "method",
            "status": "succeeded", "summary": "A preserved capture",
            "completed_at": "2026-09-22T00:00:00Z", "sources": [{
                "source_ref": source_ref, "source_family": "public-fixture",
                "basis": "observed", "finding": "The captured source supports the question.",
                "accessed_at": accessed_at, "publication_date": publication_date,
                "content_digest": "sha256:" + "b" * 64,
            }],
        }
        receipt_path = save("receipt.json", receipt)
        code, admission = run("external-evidence", "admit", "--plan-json", plan_path,
                              "--receipt-json", receipt_path, "--decision", "admit",
                              "--reason", "Reviewed capture", "--admit-source", source_ref)
        assert code == 0, admission
        args = ("external-evidence", "readback", "--plan-json", plan_path,
                "--receipt-json", receipt_path, "--admission-json", save("admission.json", admission),
                "--project", str(project))
        before = ledger.read_bytes()
        code, pending = run(*args)
        assert code == 0 and pending["downstream_source_refs"] == []
        assert ledger.read_bytes() == before  # Read-only never inserts a source.

        # Changing a source clock invalidates the parent admission, not just a display field.
        receipt["sources"][0]["accessed_at"] = "2026-09-23T00:00:00Z"
        save("receipt.json", receipt)
        code, rejected = run(*args, "--execute")
        assert code == 1 and rejected["status"] == "invalid_request"
        assert ledger.read_bytes() == before
        receipt["sources"][0]["accessed_at"] = accessed_at
        save("receipt.json", receipt)

        code, applied = run(*args, "--execute")
        assert code == 0 and applied["downstream_source_refs"] == [source_ref]
        assert applied["retirement"]["retire_ready"] is True
        state = json.loads(ledger.read_text())
        row = state["sources"][0]
        assert row["accessed_at"] == accessed_at
        assert row["publication_date"] == publication_date  # Unknown stays unknown.
        assert row["recorded_at"] != accessed_at
        assert row["external_evidence"]["admission_id"] == admission["admission_id"]
        committed = ledger.read_bytes()
        code, replayed = run(*args, "--execute")
        assert code == 0 and replayed["downstream_source_refs"] == [source_ref]
        assert ledger.read_bytes() == committed  # Replaying does not refresh either clock.

        # Existing rows remain immutable: replay is not an implicit clock migration.
        row.pop("recorded_at")
        row.pop("publication_date")
        row["accessed_at"] = "2026-09-22T02:00:00Z"
        ledger.write_text(json.dumps(state), encoding="utf-8")
        legacy = ledger.read_bytes()
        code, replayed = run(*args, "--execute")
        assert code == 0 and ledger.read_bytes() == legacy

        direct = tmp_path / "direct"
        direct.mkdir()
        code, started = run("deepresearch", "start", "--project", str(direct),
                            "--question", "Review a direct source")
        assert code == 0, started
        code, added = run("deepresearch", "add-source", "--project", str(direct),
                          "--url-or-path", source_ref, "--tool", "local_read",
                          "--claims-json", '[{"text":"A direct observation","stance":"neutral"}]')
        assert code == 0, added
        direct_row = json.loads(Path(started["state_path"]).read_text())["sources"][0]
        assert direct_row["accessed_at"]
        assert "recorded_at" not in direct_row and "publication_date" not in direct_row
    finally:
        restart_effect_runtime()
