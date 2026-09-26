"""Pytest projection of the shared-goal-authority E2E stage ladder.

Each ladder row becomes one parametrized test. Rows whose environment gate is
closed skip with ``unverified: <reason>`` so a green pytest run never implies
that a live provider was exercised; the ladder's own exit policy is pinned
separately so the standalone runner cannot report green while unverified.
"""

from __future__ import annotations

import ast
import json
import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from loopx.control_plane.testing import authority_e2e_ladder as ladder


LIVE_ENVIRONMENT_VARIABLES = (
    ladder.POSTGRES_URL_VARIABLE,
    ladder.NOKV_AUTHORITY_LIVE_FLAG,
    *ladder.NOKV_AUTHORITY_VARIABLES,
)
GATED_ROW_IDS = (
    "s2a.nokv_live_qualification",
    "s2b.postgresql_conformance_live",
)
PENDING_ONLY_ROW_ID = "s2c2.sustained_parity_soak"
PENDING_ROW_IDS = (PENDING_ONLY_ROW_ID,)
LOCAL_FILE_ROW_ID = "s0.native_file_conformance"
FULL_LADDER_VARIABLE = "LOOPX_LADDER_FULL"
# Rows whose assertions the in-repo CLI E2E suite already pins through the same
# product path. The pytest job runs close to its time budget, so the default CI
# projection keeps the rows that only the ladder exercises and defers these to
# the example runner (or LOOPX_LADDER_FULL=1).
# Rows the default CI projection skips, each named with the product-CLI E2E
# test that pins the same assertions; a guard below fails when a twin is
# renamed or deleted so the skip cannot outlive its coverage.
CLI_E2E_TWIN_FILE = Path(__file__).with_name("test_local_authority_shadow_cli_e2e.py")
CLI_E2E_COVERAGE = {
    "s2c1.retired_observation_upgrade": "test_retired_setting_cannot_enable_capture_or_satisfy_bootstrap",
}
CLI_E2E_COVERED_ROW_IDS = tuple(CLI_E2E_COVERAGE)
CLI_E2E_COVERAGE_REASON = (
    "pinned by tests/control_plane/test_local_authority_shadow_cli_e2e.py; "
    "run examples/shared-goal-authority-e2e/ladder.py or set LOOPX_LADDER_FULL=1"
)
# The Stage 2C parity half: every row below is executable through the public
# CLI and the shadow management interfaces. They carry the ``stage2c_e2e``
# marker so CI runs them in the stage2c correctness job with the other real
# CLI, process-death and recovery suites instead of the pytest shards.
STAGE_2C2_ROW_IDS = (
    "s2c2.outbox_prepared_then_committed_entries",
    "s2c2.drain_idempotent",
    "s2c2.sigkill_between_primary_write_and_drain",
    "s2c2.sigkill_mid_drain",
    "s2c2.rollback_with_pending_entries",
    "s2c2.parity_equal",
    "s2c2.parity_divergent_detects_foreign_edit",
    "s2c2.event_only_todo_source_holds",
    "s2c2.migration_seeds_and_drains",
    "s2c2.growth_measurement_gate",
    "s2c2.archive_after_leased_completion_parity",
)
STAGE_2C2_POSIX_ONLY_ROW_IDS = (
    "s2c2.sigkill_between_primary_write_and_drain",
    "s2c2.sigkill_mid_drain",
    "s2c2.rollback_with_pending_entries",
)


def _row_parameters() -> Iterator[object]:
    full_ladder = os.environ.get(FULL_LADDER_VARIABLE) == "1"
    for row in ladder.LADDER_ROWS:
        marks = []
        if row.posix_only:
            marks.append(
                pytest.mark.skipif(os.name == "nt", reason="requires POSIX cross-process flock and SIGKILL")
            )
        if row.id in CLI_E2E_COVERED_ROW_IDS and not full_ladder:
            marks.append(pytest.mark.skip(reason=CLI_E2E_COVERAGE_REASON))
        if row.stage == "0" and not full_ladder:
            marks.append(pytest.mark.skip(reason=(
                "native provider suites run in npm run test:control-plane; "
                "run the standalone ladder or set LOOPX_LADDER_FULL=1 for integrated evidence"
            )))
        if row.stage == "2c2":
            marks.append(pytest.mark.stage2c_e2e)
        yield pytest.param(row, id=row.id, marks=marks)


@pytest.mark.parametrize("row", list(_row_parameters()))
def test_ladder_row_passes_or_is_declared_unverified(
    row: ladder.LadderRow,
    tmp_path: Path,
) -> None:
    result = ladder.run_row(row, root=tmp_path, environ=os.environ)
    if result.status == "unverified":
        pytest.skip(f"unverified: {result.reason_code}")
    report = ladder.build_report(
        [result],
        pending=(),
        allow_unverified=False,
        environ=os.environ,
        forbidden=ladder.default_forbidden_tokens([tmp_path], os.environ),
    )
    reported = report["rows"][0]
    assert reported["status"] == "pass", (reported["reason_code"], reported["evidence"])
    assert report["summary"] == {"pass": 1, "fail": 0, "unverified": 0, "pending": 0, "executed": 1, "privacy_violations": 0}
    assert report["exit_policy"]["exit_code"] == 0


def test_registry_vocabulary_and_pending_rows_are_declared_not_claimed() -> None:
    row_ids = [row.id for row in ladder.LADDER_ROWS]
    assert set(CLI_E2E_COVERED_ROW_IDS) < set(row_ids)
    pending_ids = [row.id for row in ladder.PENDING_ROWS]
    assert len(set(row_ids)) == len(row_ids)
    assert set(row_ids).isdisjoint(pending_ids)
    assert [row.id for row in ladder.LADDER_ROWS if row.stage == "2c2"] == list(STAGE_2C2_ROW_IDS)
    assert pending_ids == list(PENDING_ROW_IDS)
    assert {row.stage for row in ladder.LADDER_ROWS} == {"0", "1", "2a", "2b", "2c1", "2c2"}
    assert {row.stage for row in ladder.PENDING_ROWS} == {"2c2"}
    assert all("#3819" not in row.pending_until for row in ladder.PENDING_ROWS)
    for row_id in STAGE_2C2_ROW_IDS:
        row = ladder.row_by_id(row_id)
        assert row.product_path == "real_cli" and row.gate == "deterministic"
        assert row.posix_only is (row_id in STAGE_2C2_POSIX_ONLY_ROW_IDS)
    assert all(row.gate in ladder.GATES for row in ladder.LADDER_ROWS)
    assert all(row.product_path in ladder.PRODUCT_PATHS for row in ladder.LADDER_ROWS)
    with pytest.raises(ValueError):
        ladder.LadderRow(
            id="bad.stage",
            stage="9",
            title="invalid",
            product_path="real_cli",
            gate="deterministic",
            posix_only=False,
            run=lambda _context: ladder.passed(),
        )


def test_main_never_reports_green_while_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in LIVE_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    report_path = tmp_path / "report.json"
    argv = ["--report-json", str(report_path)]
    for row_id in GATED_ROW_IDS:
        argv.extend(["--row", row_id])

    assert ladder.main(argv) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == ladder.REPORT_SCHEMA
    assert report["summary"] == {
        "pass": 0,
        "fail": 0,
        "unverified": 2,
        "pending": 0,
        "executed": 2,
        "privacy_violations": 0,
    }
    assert {row["status"] for row in report["rows"]} == {"unverified"}
    assert {row["reason_code"] for row in report["rows"]} == {
        "nokv_authority_env_missing",
        "postgres_url_missing",
    }
    assert report["exit_policy"] == {
        "allow_unverified": False,
        "allow_pending": False,
        "exit_code": 1,
        "rule": ladder.EXIT_POLICY_RULE,
    }
    assert report["bindings"]["postgres_url_sha256_prefix"] is None
    assert report["bindings"]["nokv_client_config_sha256"] is None
    assert report["bindings"]["loopx_commit"] is None or len(report["bindings"]["loopx_commit"]) == 40
    captured = capsys.readouterr()
    assert "unverified rows:" in captured.err

    assert ladder.main([*argv, "--allow-unverified"]) == 0
    relaxed = json.loads(report_path.read_text(encoding="utf-8"))
    assert relaxed["summary"]["unverified"] == 2
    assert relaxed["exit_policy"]["allow_unverified"] is True
    assert relaxed["exit_policy"]["exit_code"] == 0
    capsys.readouterr()


def test_stage_2a_row_reports_specific_unverified_reasons_for_each_missing_input(
    tmp_path: Path,
) -> None:
    row = ladder.row_by_id("s2a.nokv_live_qualification")
    assert row.gate == "env:nokv_authority"
    assert row.stage == "2a"
    base = {name: value for name, value in os.environ.items() if name not in LIVE_ENVIRONMENT_VARIABLES}

    gated = ladder.run_row(row, root=tmp_path, environ=base)
    assert gated.status == "unverified"
    assert gated.reason_code == "nokv_authority_env_missing"
    assert gated.evidence["missing_variables"] == sorted(
        [ladder.NOKV_AUTHORITY_LIVE_FLAG, *ladder.NOKV_AUTHORITY_VARIABLES]
    )

    config = tmp_path / "nokv-client.json"
    config.write_text(json.dumps({"root_id": "0" * 32, "object_store": {"kind": "memory"}}), encoding="utf-8")
    inputs = {
        **base,
        ladder.NOKV_AUTHORITY_LIVE_FLAG: "0",
        ladder.NOKV_AUTHORITY_CONFIG_VARIABLE: str(config),
        ladder.NOKV_AUTHORITY_PYTHON_VARIABLE: "relative/python",
        ladder.NOKV_AUTHORITY_WORKBENCH_VARIABLE: "ladder-workbench",
    }
    not_enabled = ladder.run_row(row, root=tmp_path, environ=inputs)
    assert not_enabled.status == "unverified"
    assert not_enabled.reason_code == "loopx_nokv_authority_live_not_enabled"

    inputs[ladder.NOKV_AUTHORITY_LIVE_FLAG] = "1"
    relative_python = ladder.run_row(row, root=tmp_path, environ=inputs)
    assert relative_python.status == "unverified"
    assert relative_python.reason_code == "nokv_authority_python_missing"

    inputs[ladder.NOKV_AUTHORITY_CONFIG_VARIABLE] = str(tmp_path / "absent.json")
    missing_config = ladder.run_row(row, root=tmp_path, environ=inputs)
    assert missing_config.status == "unverified"
    assert missing_config.reason_code == "nokv_authority_config_missing"

    # Configuration values are secrets: every string leaf becomes a forbidden token.
    inputs[ladder.NOKV_AUTHORITY_CONFIG_VARIABLE] = str(config)
    tokens = ladder.default_forbidden_tokens([tmp_path], inputs)
    assert str(config) in tokens
    assert "0" * 32 in tokens
    assert "memory" in tokens
    assert ladder.collect_bindings(inputs)["nokv_client_config_sha256"] is not None


@pytest.mark.stage2c_e2e
def test_stage_2c2_row_passes_when_the_ladder_root_is_reached_through_a_symlink(tmp_path: Path) -> None:
    """macOS's default temp directory is a symlink; the shadow rows must not care (#4892)."""

    real = tmp_path / "real-root"
    real.mkdir()
    link = tmp_path / "link-root"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    row = ladder.row_by_id("s2c2.outbox_prepared_then_committed_entries")
    result = ladder.run_row(row, root=link, environ=os.environ)
    if result.status == "unverified":
        pytest.skip(f"unverified: {result.reason_code}")
    assert result.status == "pass", (result.reason_code, result.evidence)


def test_nokv_sdk_pin_and_fence_checks_agree_across_helper_ladder_and_probe() -> None:
    from loopx.control_plane.coordination import nokv_jsonl_helper as helper

    assert ladder.QUALIFIED_NOKV_SDK_VERSION == helper.QUALIFIED_NOKV_SDK_VERSION == "0.11.1"
    assert ladder.QUALIFIED_NOKV_API_VERSION == helper.QUALIFIED_NOKV_API_VERSION == 1
    probe = (ladder.REPO_ROOT / ladder.NOKV_QUALIFICATION_SCRIPT).read_text(encoding="utf-8")
    assert f'export const QUALIFIED_NOKV_SDK_VERSION = "{ladder.QUALIFIED_NOKV_SDK_VERSION}";' in probe
    assert f"export const QUALIFIED_NOKV_API_VERSION = {ladder.QUALIFIED_NOKV_API_VERSION};" in probe
    for check_id in ladder.NOKV_INCARNATION_FENCE_CHECKS:
        assert f'passed("{check_id}")' in probe


def test_pending_rows_never_exit_green_without_allow_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in LIVE_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    report_path = tmp_path / "report.json"

    # Pending-only selection: zero executions must not read as success.
    assert ladder.main(["--row", PENDING_ONLY_ROW_ID, "--report-json", str(report_path)]) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"] == {"pass": 0, "fail": 0, "unverified": 0, "pending": 1, "executed": 0, "privacy_violations": 0}
    assert report["rows"] == []
    assert report["pending"][0]["id"] == PENDING_ONLY_ROW_ID
    assert report["pending"][0]["status"] == "pending"
    assert report["exit_policy"]["exit_code"] == 1
    assert "pending rows (not verified):" in capsys.readouterr().err

    assert ladder.main(["--row", PENDING_ONLY_ROW_ID, "--allow-pending", "--report-json", str(report_path)]) == 0
    allowed = json.loads(report_path.read_text(encoding="utf-8"))
    assert allowed["exit_policy"] == {
        "allow_unverified": False,
        "allow_pending": True,
        "exit_code": 0,
        "rule": ladder.EXIT_POLICY_RULE,
    }
    assert allowed["summary"]["pending"] == 1
    capsys.readouterr()

    # A stage whose only remaining declaration is pending behaves the same way;
    # the executable 2c2 rows are removed from the registry for this check only.
    with pytest.MonkeyPatch.context() as registry:
        registry.setattr(ladder, "LADDER_ROWS", tuple(row for row in ladder.LADDER_ROWS if row.stage != "2c2"))
        assert ladder.main(["--stage", "2c2", "--report-json", str(report_path)]) == 1
    capsys.readouterr()

    # Mixed selection: one executable pass does not excuse a pending obligation.
    monkeypatch.setattr(ladder, "LADDER_ROWS", tuple(
        replace(row, run=lambda _context: ladder.passed()) if row.id == LOCAL_FILE_ROW_ID else row
        for row in ladder.LADDER_ROWS
    ))
    mixed = ["--row", LOCAL_FILE_ROW_ID, "--row", PENDING_ONLY_ROW_ID, "--report-json", str(report_path)]
    assert ladder.main(mixed) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"] == {"pass": 1, "fail": 0, "unverified": 0, "pending": 1, "executed": 1, "privacy_violations": 0}
    capsys.readouterr()
    assert ladder.main([*mixed, "--allow-pending"]) == 0
    capsys.readouterr()

    # List-only never claims verification: it prints the registry and exits 0.
    assert ladder.main(["--list", "--row", PENDING_ONLY_ROW_ID]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["schema_version"] == ladder.LIST_SCHEMA
    assert listing["rows"] == []
    assert [row["id"] for row in listing["pending"]] == [PENDING_ONLY_ROW_ID]
    assert "exit_policy" not in listing


def test_privacy_scan_turns_leaks_into_failures(tmp_path: Path) -> None:
    row = ladder.row_by_id(LOCAL_FILE_ROW_ID)
    leaking = ladder.RowResult(
        row=row,
        status="pass",
        reason_code=None,
        evidence={"pointer": str(tmp_path / "leaked-root")},
        duration_ms=1,
    )
    clean = ladder.RowResult(
        row=ladder.row_by_id("s1.cli_document_decodes_through_ts_store"),
        status="pass",
        reason_code=None,
        evidence={"cursor": "3"},
        duration_ms=1,
    )

    report = ladder.build_report(
        [leaking, clean],
        pending=ladder.PENDING_ROWS,
        allow_unverified=True,
        environ=os.environ,
        forbidden=ladder.default_forbidden_tokens([tmp_path], os.environ),
    )

    statuses = {row["id"]: row for row in report["rows"]}
    assert statuses[leaking.row.id]["status"] == "fail"
    assert statuses[leaking.row.id]["reason_code"] == "privacy_violation"
    assert str(tmp_path) not in json.dumps(report)
    assert statuses[clean.row.id]["status"] == "pass"
    assert report["summary"] == {
        "pass": 1,
        "fail": 1,
        "unverified": 0,
        "pending": len(ladder.PENDING_ROWS),
        "executed": 2,
        "privacy_violations": 1,
    }
    assert report["exit_policy"]["exit_code"] == 1
    assert {row["status"] for row in report["pending"]} == {"pending"}


def test_privacy_leak_confined_to_bindings_still_fails_the_run() -> None:
    row = ladder.row_by_id("s1.cli_document_decodes_through_ts_store")
    clean = ladder.RowResult(row=row, status="pass", reason_code=None, evidence={"cursor": "3"}, duration_ms=1)
    # The probe manifest names this module, so the token can only leak through
    # the bindings; the row itself stays clean.
    token = "authority_e2e_ladder.py"
    assert token not in json.dumps(clean.as_dict())

    report = ladder.build_report(
        [clean],
        pending=(),
        allow_unverified=True,
        allow_pending=True,
        environ=os.environ,
        forbidden=[token],
    )

    assert report["rows"][0]["status"] == "pass"
    assert report["bindings"]["privacy_violation"] is True
    assert all(value is None for key, value in report["bindings"].items() if key != "privacy_violation")
    assert token not in json.dumps(report)
    assert report["summary"] == {
        "pass": 1,
        "fail": 0,
        "unverified": 0,
        "pending": 0,
        "executed": 1,
        "privacy_violations": 1,
    }
    assert report["exit_policy"]["exit_code"] == 1
    # No relaxation flag reaches a privacy violation.
    assert ladder.exit_code_for(report["summary"], allow_unverified=True, allow_pending=True) == 1


def test_ci_projection_skips_only_rows_whose_cli_e2e_twin_still_exists() -> None:
    tree = ast.parse(CLI_E2E_TWIN_FILE.read_text(encoding="utf-8"))
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    for row_id, twin in CLI_E2E_COVERAGE.items():
        ladder.row_by_id(row_id)
        assert twin in defined, f"{row_id} is skipped by default but its twin {twin} no longer exists"


def test_list_prints_rows_and_pending_declarations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert ladder.main(["--list"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in listing["rows"]] == [row.id for row in ladder.LADDER_ROWS]
    assert [row["id"] for row in listing["pending"]] == [row.id for row in ladder.PENDING_ROWS]
    assert all(row["status"] == "pending" for row in listing["pending"])

    assert ladder.main(["--list", "--stage", "2c2"]) == 0
    stage_listing = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in stage_listing["rows"]] == list(STAGE_2C2_ROW_IDS)
    assert [row["id"] for row in stage_listing["pending"]] == list(PENDING_ROW_IDS)
    assert {row["stage"] for row in stage_listing["pending"]} == {"2c2"}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("output,returncode,expected", [
    ("# tests 3\n# pass 3\n# fail 0\n# skipped 0", 0, "pass"),
    ("# tests 0\n# pass 0\n# fail 0\n# skipped 0", 0, "fail"),
    ("# tests 3\n# pass 2\n# fail 0\n# skipped 1", 0, "fail"),
    ("# tests 3\n# pass 2\n# fail 1\n# skipped 0", 0, "fail"),
    ("# tests 3\n# pass 2\n# fail 0\n# skipped 0", 0, "fail"),
    ("# tests 3\n# pass 3\n# fail 0\n# skipped 0", 1, "fail"),
    ("TAP version 13\nok 1 - interrupted before the trailer", 0, "fail"),
])
def test_native_local_qualification_rejects_partial_evidence(
    provider: str, output: str, returncode: int, expected: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane.testing.authority_e2e_fixtures import parse_tap_summary

    calls = []
    monkeypatch.setattr(ladder, "node_executable", lambda: "node")

    def run(argv, **kwargs):
        calls.append(argv)
        return parse_tap_summary(output, returncode=returncode)

    monkeypatch.setattr(ladder, "tap_summary", run)
    row = ladder.row_by_id(f"s0.native_{provider}_conformance")
    result = ladder.run_row(row, root=tmp_path, environ={})
    assert result.status == expected
    assert calls[0][-1] == ladder.LOCAL_CONFORMANCE_TESTS[provider].as_posix()
    assert "--test-name-pattern" not in calls[0]
    if expected == "pass":
        assert result.evidence["provider"] == provider
        assert result.evidence["tap_pass"] == 3


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_local_qualification_missing_runtime_or_suite_is_unverified(
    provider: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = ladder.row_by_id(f"s0.native_{provider}_conformance")
    monkeypatch.setattr(ladder, "node_executable", lambda: None)
    missing_node = ladder.run_row(row, root=tmp_path, environ={})
    assert (missing_node.status, missing_node.reason_code) == ("unverified", "node_missing")
    monkeypatch.setattr(ladder, "node_executable", lambda: "node")
    monkeypatch.setattr(ladder, "REPO_ROOT", tmp_path)
    missing_suite = ladder.run_row(row, root=tmp_path, environ={})
    assert (missing_suite.status, missing_suite.reason_code) == ("unverified", "local_conformance_suite_missing")


def test_retired_prototype_rows_are_not_relabelled_as_native_evidence(capsys) -> None:
    for row_id in ("s0.file_matrix_twelve_rows", "s0.nokv_live_matrix"):
        assert ladder.main(["--row", row_id]) == 2
        capsys.readouterr()


def test_native_ladder_suites_remain_in_the_regular_typescript_test_job() -> None:
    package = json.loads((ladder.REPO_ROOT / "package.json").read_text())
    assert "tests/control_plane_ts/*.test.ts" in package["scripts"]["test:control-plane"]
    regular_suites = set(ladder.REPO_ROOT.glob("tests/control_plane_ts/*.test.ts"))
    assert {ladder.REPO_ROOT / path for path in ladder.LOCAL_CONFORMANCE_TESTS.values()} <= regular_suites
    assert {row.id for row in ladder.LADDER_ROWS if row.stage == "0"} == {
        "s0.native_file_conformance", "s0.native_sqlite_conformance",
    }
