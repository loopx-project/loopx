"""Incremental end-to-end stage ladder for the shared-goal-authority RFC.

One ladder, one row per completed RFC stage claim, one exit policy: the run
is green only when every selected row passed. A row that cannot run in this
environment (no PostgreSQL, no NoKV stack, no POSIX signals) reports
``unverified`` and, unless ``--allow-unverified`` is given, the ladder exits
non-zero. Rows for stages that later PRs will land are declared as
``pending`` so the report never silently claims them.

Rows drive the product through ``python -m loopx.cli`` (``real_cli``) or run
the retained store-level probes (``store_direct``); this module adds no
product path of its own.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from .authority_e2e_row_support import (
    AGENT_A,
    RowAssertionError,
    RowContext,
    RowOutcome,
    acquire_lease,
    add_todo,
    expect,
    passed,
    sha256_hex,
    unverified,
)
from .authority_e2e_rows_stage2c import row_retired_observation_upgrade
from .authority_e2e_rows_stage2c2 import (
    capture_workspace,
    delivered,
    row_archive_after_leased_completion_parity,
    row_drain_idempotent,
    row_event_only_todo_source_holds,
    row_growth_measurement_gate,
    row_migration_seeds_and_drains,
    row_outbox_prepared_then_committed_entries,
    row_parity_divergent_detects_foreign_edit,
    row_parity_equal,
    row_rollback_with_pending_entries,
    row_sigkill_between_primary_write_and_drain,
    row_sigkill_mid_drain,
)
from .authority_e2e_fixtures import (
    REPO_ROOT,
    CliOutputError,
    TS_READBACK_PROBE,
    JsonObject,
    node_executable,
    parse_json_object,
    run_cli,
    tap_summary,
    ts_readback,
    unique_goal_id,
)

REPORT_SCHEMA = "loopx_shared_goal_authority_e2e_report_v0"
LIST_SCHEMA = "loopx_shared_goal_authority_e2e_rows_v0"
STAGES: tuple[str, ...] = ("0", "1", "2a", "2b", "2c1", "2c2")
PRODUCT_PATHS: tuple[str, ...] = ("real_cli", "store_direct")
GATES: tuple[str, ...] = (
    "deterministic",
    "env:postgresql",
    "env:nokv_authority",
    "env:nokv_legacy",
)
ROW_STATUSES: tuple[str, ...] = ("pass", "fail", "unverified")
EXIT_POLICY_RULE = (
    "exit 0 iff fail == 0 and privacy_violations == 0 "
    "and (unverified == 0 or allow_unverified) and (pending == 0 or allow_pending)"
)

POSTGRES_URL_VARIABLE = "LOOPX_TEST_POSTGRES_URL"
NOKV_LIVE_FLAG = "NOKV_COORDINATION_LIVE"
NOKV_STACK_VARIABLES: tuple[str, ...] = (
    "NOKV_ETCD",
    "NOKV_ETCD_PREFIX",
    "NOKV_ROOT_ID",
    "NOKV_BUCKET",
    "NOKV_OBJECT_ENDPOINT",
    "NOKV_OBJECT_ROOT",
    "NOKV_OBJECT_KEY",
    "NOKV_OBJECT_SECRET",
)
NOKV_SECRET_VARIABLES: tuple[str, ...] = ("NOKV_OBJECT_KEY", "NOKV_OBJECT_SECRET")
# Stage 2A qualification inputs: the probe writes durable test data into an
# existing workbench, so it needs an explicit opt-in flag plus the ignored
# client configuration file, the Python executable that resolves the qualified
# NoKV SDK, and the workbench name. Tenant and goal ids are minted per run.
NOKV_AUTHORITY_LIVE_FLAG = "LOOPX_NOKV_AUTHORITY_LIVE"
NOKV_AUTHORITY_CONFIG_VARIABLE = "LOOPX_NOKV_AUTHORITY_CONFIG_JSON"
NOKV_AUTHORITY_PYTHON_VARIABLE = "LOOPX_NOKV_AUTHORITY_PYTHON"
NOKV_AUTHORITY_WORKBENCH_VARIABLE = "LOOPX_NOKV_AUTHORITY_WORKBENCH"
NOKV_AUTHORITY_VARIABLES: tuple[str, ...] = (
    NOKV_AUTHORITY_CONFIG_VARIABLE,
    NOKV_AUTHORITY_PYTHON_VARIABLE,
    NOKV_AUTHORITY_WORKBENCH_VARIABLE,
)
LIVE_OPT_IN_FLAGS: tuple[str, ...] = (NOKV_LIVE_FLAG, NOKV_AUTHORITY_LIVE_FLAG)
GATE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "deterministic": (),
    "env:postgresql": (POSTGRES_URL_VARIABLE,),
    "env:nokv_authority": (NOKV_AUTHORITY_LIVE_FLAG, *NOKV_AUTHORITY_VARIABLES),
    "env:nokv_legacy": (NOKV_LIVE_FLAG, *NOKV_STACK_VARIABLES),
}
GATE_UNVERIFIED_REASON: dict[str, str] = {
    "env:postgresql": "postgres_url_missing",
    "env:nokv_authority": "nokv_authority_env_missing",
    "env:nokv_legacy": "nokv_live_env_missing",
}

LIVE_E2E_SCRIPT = Path("examples") / "nokv-shadow-provider" / "live_e2e.py"
PG_INTEGRATION_TEST = (
    Path("tests") / "control_plane_ts" / "postgresql_authority_store.integration.test.ts"
)
NOKV_QUALIFICATION_SCRIPT = Path("examples") / "nokv-authority-store" / "live-qualification.ts"
NOKV_HELPER = Path("loopx") / "control_plane" / "coordination" / "nokv_jsonl_helper.py"
NOKV_QUALIFICATION_REPORT_SCHEMA = "loopx_nokv_authority_live_qualification_v0"
NOKV_QUALIFICATION_SCOPE = "stage_2a_single_node_store_conformance"
QUALIFIED_NOKV_SDK_VERSION = "0.11.1"
QUALIFIED_NOKV_API_VERSION = 1
# NoKV 0.11.1 refuses a publication fenced on a stale workbench incarnation
# before any durable row or object exists; the live probe must prove it.
NOKV_INCARNATION_FENCE_CHECKS: tuple[str, ...] = (
    "stale_incarnation_fence_rejected",
    "stale_incarnation_fence_left_generation_unchanged",
)
PROBE_SOURCES: tuple[Path, ...] = (
    LIVE_E2E_SCRIPT,
    TS_READBACK_PROBE,
    PG_INTEGRATION_TEST,
    NOKV_QUALIFICATION_SCRIPT,
    NOKV_HELPER,
    Path("loopx") / "control_plane" / "testing" / "authority_e2e_ladder.py",
    Path("loopx") / "control_plane" / "testing" / "authority_e2e_fixtures.py",
    Path("loopx") / "control_plane" / "testing" / "authority_e2e_row_support.py",
    Path("loopx") / "control_plane" / "testing" / "authority_e2e_rows_stage2c.py",
    Path("loopx") / "control_plane" / "testing" / "authority_e2e_rows_stage2c2.py",
)
FILE_MATRIX_ROWS: tuple[str, ...] = (
    "same_todo_one_winner",
    "independent_todo_applies",
    "replay_returns_original_receipt",
    "identity_mismatch_rejected",
    "stale_revision_conflicts",
    "lost_response_recovers_receipt",
    "receipts_retained",
    "authority_revision_advanced_twice",
    "renew_extends_the_active_lease",
    "expired_lease_reclaimed_with_new_epoch",
    "superseded_executor_cannot_write_back",
    "complete_creates_claimable_successor_atomically",
)
NOKV_ONLY_MATRIX_ROW = "restored_lineage_fails_closed"
MINIMUM_POSTGRES_TAP_PASSES = 9
@dataclass(frozen=True)
class LadderRow:
    id: str
    stage: str
    title: str
    product_path: str
    gate: str
    posix_only: bool
    run: Callable[[RowContext], RowOutcome]

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise ValueError(f"row {self.id}: unsupported stage {self.stage!r}")
        if self.product_path not in PRODUCT_PATHS:
            raise ValueError(f"row {self.id}: unsupported product_path {self.product_path!r}")
        if self.gate not in GATES:
            raise ValueError(f"row {self.id}: unsupported gate {self.gate!r}")

    def describe(self) -> JsonObject:
        return {
            "id": self.id,
            "stage": self.stage,
            "title": self.title,
            "product_path": self.product_path,
            "gate": self.gate,
            "posix_only": self.posix_only,
        }


@dataclass(frozen=True)
class PendingRow:
    id: str
    stage: str
    pending_until: str

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise ValueError(f"pending row {self.id}: unsupported stage {self.stage!r}")

    def describe(self) -> JsonObject:
        return {
            "id": self.id,
            "stage": self.stage,
            "status": "pending",
            "pending_until": self.pending_until,
        }


@dataclass(frozen=True)
class RowResult:
    row: LadderRow
    status: str
    reason_code: str | None
    evidence: JsonObject
    duration_ms: int

    def as_dict(self) -> JsonObject:
        return {
            **self.row.describe(),
            "status": self.status,
            "reason_code": self.reason_code,
            "evidence": dict(self.evidence),
            "duration_ms": self.duration_ms,
        }


def _run_live_matrix_script(environ: Mapping[str, str], *, live: bool) -> JsonObject:
    env = dict(environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    if not live:
        env.pop(NOKV_LIVE_FLAG, None)
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / LIVE_E2E_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=900,
        check=False,
    )
    matrix = parse_json_object(completed.stdout)
    matrix["_exit_code"] = completed.returncode
    return matrix


def _matrix_rows(matrix: Mapping[str, object], key: str) -> dict[str, object]:
    rows = matrix.get(key)
    expect(isinstance(rows, dict), f"live matrix must report {key}")
    assert isinstance(rows, dict)
    return {str(name): value for name, value in rows.items()}


def _false_rows(rows: Mapping[str, object]) -> list[str]:
    return sorted(name for name, value in rows.items() if value is not True)


# ---------------------------------------------------------------------------
# Stage 0: recoverable reference foundation (store_direct)
# ---------------------------------------------------------------------------


def _row_file_matrix_twelve_rows(context: RowContext) -> RowOutcome:
    matrix = _run_live_matrix_script(context.environ, live=False)
    file_rows = _matrix_rows(matrix, "file_provider")
    expect(
        set(file_rows) == set(FILE_MATRIX_ROWS),
        "file provider matrix must contain exactly the twelve known rows",
    )
    expect(not _false_rows(file_rows), "every file provider matrix row must be true")
    expect(matrix["_exit_code"] == 0, "live matrix script must exit 0 without a stack")
    return passed(matrix_rows=len(file_rows), script_exit_code=matrix["_exit_code"])


def _row_nokv_live_matrix(context: RowContext) -> RowOutcome:
    matrix = _run_live_matrix_script(context.environ, live=True)
    nokv_rows = _matrix_rows(matrix, "nokv_provider")
    if "unverified" in nokv_rows:
        reason = str(nokv_rows["unverified"])
        code = "nokv_sdk_missing" if "SDK" in reason else "nokv_matrix_unverified"
        return unverified(code)
    expected = {*FILE_MATRIX_ROWS, NOKV_ONLY_MATRIX_ROW}
    expect(set(nokv_rows) == expected, "NoKV matrix must contain the shared rows plus the lineage row")
    expect(not _false_rows(nokv_rows), "every NoKV matrix row must be true")
    parity = _matrix_rows(matrix, "file_nokv_parity")
    expect(parity.get("identical_row_outcomes") is True, "file and NoKV rows must be identical")
    expect(parity.get("rows") == len(FILE_MATRIX_ROWS), "parity must cover the twelve shared rows")
    expect(matrix["_exit_code"] == 0, "live matrix script must exit 0")
    return passed(
        nokv_rows=len(nokv_rows),
        parity_rows=parity.get("rows"),
        restored_lineage_fails_closed=True,
        script_exit_code=matrix["_exit_code"],
    )


# ---------------------------------------------------------------------------
# Stage 1: provider-neutral boundary, read back through the TypeScript store
# ---------------------------------------------------------------------------


def _row_cli_document_decodes_through_ts_store(context: RowContext) -> RowOutcome:
    if node_executable() is None:
        return unverified("node_missing")
    workspace = capture_workspace(context, "ladder-s1")
    added = add_todo(workspace, "Decode this source transaction through the TypeScript store.")
    todo_id = str(added["todo_id"])
    acquired = acquire_lease(
        workspace,
        todo_id=todo_id,
        owner=AGENT_A,
        idempotency_key="ladder-s1-lease",
    )
    updated = run_cli(
        workspace,
        "todo",
        "update",
        "--goal-id",
        workspace.goal_id,
        "--todo-id",
        todo_id,
        "--note",
        "A public-safe note recorded after the lease.",
        "--agent-id",
        AGENT_A,
    )
    for label, payload in (("todo add", added), ("task-lease acquire", acquired), ("todo update", updated)):
        delivered(payload, label=label)
    probe = ts_readback(workspace, directory=workspace.runtime_root / "authority-shadow" / "file-v0")
    expect(probe is not None, "read-back probe requires node")
    assert probe is not None
    load, scan = probe.get("load"), probe.get("scan")
    expect(isinstance(load, dict) and load.get("status") == "loaded", "TS store must load the CLI document")
    expect(isinstance(load, dict) and load.get("cursor") == "4", "bootstrap and three writes must advance four cursors")
    expect(isinstance(scan, dict), "scan must return a page summary")
    assert isinstance(scan, dict)
    operations = scan.get("operation_ids")
    expect(isinstance(operations, list) and len(operations) == len(set(operations)) == 4,
           "paged scan must return four distinct transactions")
    expect(scan.get("cursors") == ["1", "2", "3", "4"], "transactions must retain source order")
    assert isinstance(operations, list)
    receipt_probe = ts_readback(workspace, receipt=str(operations[1]),
                               directory=workspace.runtime_root / "authority-shadow" / "file-v0")
    expect(isinstance(receipt_probe, dict), "receipt probe must load")
    assert isinstance(receipt_probe, dict)
    receipt = receipt_probe.get("receipt")
    expect(isinstance(receipt, dict) and receipt.get("status") == "found", "readReceipt must find the first source write")
    return passed(cursor="4", scan_pages=scan.get("pages"), transaction_count=4)


# ---------------------------------------------------------------------------
# Stage 2A: NoKV candidate conformance against a live single-node stack
# ---------------------------------------------------------------------------


def _absolute_existing_path(value: str | None, *, must_be_file: bool) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        return None
    if must_be_file and not path.is_file():
        return None
    if not must_be_file and not path.exists():
        return None
    return path


def _nokv_authority_config_sha256(path: Path) -> str:
    """Digest of the canonical client configuration; the values never leave the file."""

    document = json.loads(path.read_text(encoding="utf-8"))
    return sha256_hex(json.dumps(document, sort_keys=True, separators=(",", ":")))


def _row_nokv_live_qualification(context: RowContext) -> RowOutcome:
    node = node_executable()
    if node is None:
        return unverified("node_missing")
    config_path = _absolute_existing_path(
        context.environ.get(NOKV_AUTHORITY_CONFIG_VARIABLE), must_be_file=True
    )
    if config_path is None:
        return unverified("nokv_authority_config_missing", variable=NOKV_AUTHORITY_CONFIG_VARIABLE)
    python = _absolute_existing_path(
        context.environ.get(NOKV_AUTHORITY_PYTHON_VARIABLE), must_be_file=True
    )
    if python is None:
        return unverified("nokv_authority_python_missing", variable=NOKV_AUTHORITY_PYTHON_VARIABLE)
    workbench = str(context.environ.get(NOKV_AUTHORITY_WORKBENCH_VARIABLE) or "")
    tenant_id = unique_goal_id("ladder-tenant")
    goal_id = unique_goal_id("ladder-2a")
    completed = subprocess.run(
        [
            node,
            "--no-warnings",
            "--experimental-strip-types",
            str(REPO_ROOT / NOKV_QUALIFICATION_SCRIPT),
            "--execute-live",
            "--config-json",
            str(config_path),
            "--python-executable",
            str(python),
            "--tenant-id",
            tenant_id,
            "--goal-id",
            goal_id,
            "--workbench",
            workbench,
        ],
        cwd=REPO_ROOT,
        env=dict(context.environ),
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=600,
        check=False,
    )
    if completed.returncode != 0:
        failure: JsonObject = {}
        try:
            failure = parse_json_object(completed.stderr.strip().splitlines()[-1])
        except (CliOutputError, IndexError):
            pass
        raise RowAssertionError(
            f"qualification probe exited {completed.returncode}: "
            f"{failure.get('reason_code') or 'no typed failure on stderr'}"
        )
    report = parse_json_object(completed.stdout)
    expect(
        report.get("schema_version") == NOKV_QUALIFICATION_REPORT_SCHEMA
        and report.get("ok") is True,
        "qualification report must carry the reviewed schema and ok=true",
    )
    expect(
        report.get("qualification_scope") == NOKV_QUALIFICATION_SCOPE,
        "qualification scope must be single-node store conformance",
    )
    checks = report.get("checks")
    expect(isinstance(checks, list) and len(checks) > 0, "qualification must report its checks")
    assert isinstance(checks, list)
    check_ids = [str(check.get("id")) for check in checks if isinstance(check, dict)]
    expect(
        len(check_ids) == len(checks)
        and all(check.get("status") == "passed" for check in checks if isinstance(check, dict)),
        "every qualification check must have passed",
    )
    expect(
        all(check_id in check_ids for check_id in NOKV_INCARNATION_FENCE_CHECKS),
        "qualification must prove the stale-incarnation publication fence",
    )
    expect(
        report.get("nokv_sdk_version") == QUALIFIED_NOKV_SDK_VERSION
        and report.get("nokv_api_version") == QUALIFIED_NOKV_API_VERSION,
        "qualification must name the qualified NoKV SDK and API versions",
    )
    expect(
        report.get("authority_source_changed") is False
        and report.get("availability_or_ha_proven") is False,
        "qualification must not claim promotion or availability",
    )
    return passed(
        qualification_scope=NOKV_QUALIFICATION_SCOPE,
        check_count=len(check_ids),
        check_ids=check_ids,
        incarnation_fence_checks=list(NOKV_INCARNATION_FENCE_CHECKS),
        final_generation=report.get("final_generation"),
        final_cursor=report.get("final_cursor"),
        nokv_sdk_version=report.get("nokv_sdk_version"),
        nokv_api_version=report.get("nokv_api_version"),
        config_sha256_prefix=_nokv_authority_config_sha256(config_path)[:12],
        workbench_sha256_prefix=sha256_hex(workbench)[:12],
        tenant_id=tenant_id,
        goal_id=goal_id,
        durable_test_data_left=report.get("durable_test_data_left") is True,
    )


# ---------------------------------------------------------------------------
# Stage 2B: PostgreSQL candidate conformance against a live database
# ---------------------------------------------------------------------------


def _pg_package_version() -> str | None:
    manifest = REPO_ROOT / "node_modules" / "pg" / "package.json"
    if not manifest.exists():
        return None
    try:
        version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return str(version) if version else None


def _row_postgresql_conformance_live(context: RowContext) -> RowOutcome:
    node = node_executable()
    if node is None:
        return unverified("node_missing")
    pg_version = _pg_package_version()
    if pg_version is None:
        return unverified("pg_dependency_missing")
    summary = tap_summary(
        [
            node,
            "--no-warnings",
            "--experimental-strip-types",
            "--test",
            "--test-reporter=tap",
            str(PG_INTEGRATION_TEST),
        ],
        env=context.environ,
        timeout=900,
    )
    expect(summary.failed == 0, "PostgreSQL conformance must report zero TAP failures")
    expect(summary.skipped == 0, "PostgreSQL conformance must not skip; the URL was not honoured")
    expect(
        summary.passed is not None and summary.passed >= MINIMUM_POSTGRES_TAP_PASSES,
        "PostgreSQL conformance must pass the conformance suite plus provider tests",
    )
    expect(summary.returncode == 0, "node test runner must exit 0")
    return passed(
        tap_pass=summary.passed,
        tap_fail=summary.failed,
        tap_skipped=summary.skipped,
        postgres_url_sha256_prefix=sha256_hex(context.environ.get(POSTGRES_URL_VARIABLE, ""))[:12],
        pg_package_version=pg_version,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


LADDER_ROWS: tuple[LadderRow, ...] = (
    LadderRow(
        id="s0.file_matrix_twelve_rows",
        stage="0",
        title="Twelve shared lifecycle scenarios pass on the file coordination provider",
        product_path="store_direct",
        gate="deterministic",
        posix_only=False,
        run=_row_file_matrix_twelve_rows,
    ),
    LadderRow(
        id="s0.nokv_live_matrix",
        stage="0",
        title="The same matrix passes on a live NoKV stack with identical outcomes",
        product_path="store_direct",
        gate="env:nokv_legacy",
        posix_only=False,
        run=_row_nokv_live_matrix,
    ),
    LadderRow(
        id="s1.cli_document_decodes_through_ts_store",
        stage="1",
        title="CLI-written candidate documents decode through the TypeScript FileAuthorityStore",
        product_path="real_cli",
        gate="deterministic",
        posix_only=False,
        run=_row_cli_document_decodes_through_ts_store,
    ),
    LadderRow(
        id="s2a.nokv_live_qualification",
        stage="2a",
        title="The merged NoKV candidate passes its live single-node qualification",
        product_path="store_direct",
        gate="env:nokv_authority",
        posix_only=False,
        run=_row_nokv_live_qualification,
    ),
    LadderRow(
        id="s2b.postgresql_conformance_live",
        stage="2b",
        title="PostgreSQL candidate passes the conformance suite against a live database",
        product_path="store_direct",
        gate="env:postgresql",
        posix_only=False,
        run=_row_postgresql_conformance_live,
    ),
    LadderRow(
        id="s2c1.retired_observation_upgrade",
        stage="2c1",
        title="Retired settings cannot write; explicit replacement bootstrap captures the next transaction",
        product_path="real_cli",
        gate="deterministic",
        posix_only=False,
        run=row_retired_observation_upgrade,
    ),
    LadderRow(
        id="s2c2.outbox_prepared_then_committed_entries",
        stage="2c2",
        title="Python and TypeScript writers leave prepared records with committed markers that one drain delivers once",
        product_path="real_cli",
        gate="deterministic",
        posix_only=False,
        run=row_outbox_prepared_then_committed_entries,
    ),
    LadderRow(
        id="s2c2.drain_idempotent",
        stage="2c2",
        title="Bounded drains are cumulative, an idle drain changes nothing, and a writer replay mints no entry",
        product_path="real_cli",
        gate="deterministic",
        posix_only=False,
        run=row_drain_idempotent,
    ),
    LadderRow(
        id="s2c2.sigkill_between_primary_write_and_drain",
        stage="2c2",
        title="A SIGKILL around the primary replace leaves a prepared-only entry that drain settles from the primary bytes",
        product_path="real_cli",
        gate="deterministic",
        posix_only=True,
        run=row_sigkill_between_primary_write_and_drain,
    ),
    LadderRow(
        id="s2c2.sigkill_mid_drain",
        stage="2c2",
        title="A SIGKILL inside the inline drain is recovered from exact receipts without a second delivery",
        product_path="real_cli",
        gate="deterministic",
        posix_only=True,
        run=row_sigkill_mid_drain,
    ),
    LadderRow(
        id="s2c2.rollback_with_pending_entries",
        stage="2c2",
        title="Rollback archives pending entries, holds capture until rebootstrap, and replays its historical result",
        product_path="real_cli",
        gate="deterministic",
        posix_only=True,
        run=row_rollback_with_pending_entries,
    ),
    LadderRow(
        id="s2c2.parity_equal",
        stage="2c2",
        title="Sustained interleaving of Python and TypeScript writers keeps every bounded qualification matched",
        product_path="real_cli",
        gate="deterministic",
        posix_only=False,
        run=row_parity_equal,
    ),
    LadderRow(
        id="s2c2.parity_divergent_detects_foreign_edit",
        stage="2c2",
        title="A direct primary edit is reported as drift, holds later captures, and recovers only by rollback and rebootstrap",
        product_path="real_cli",
        gate="deterministic",
        posix_only=False,
        run=row_parity_divergent_detects_foreign_edit,
    ),
    LadderRow(
        id="s2c2.event_only_todo_source_holds",
        stage="2c2",
        title="A retired Todo event source refuses reads and primary writes without changing either source",
        product_path="real_cli",
        gate="deterministic",
        posix_only=False,
        run=row_event_only_todo_source_holds,
    ),
    LadderRow(
        id="s2c2.migration_seeds_and_drains",
        stage="2c2",
        title="migrate-state refuses an active capture source; after rollback the migrated goal bootstraps a fresh lineage that drains",
        product_path="real_cli",
        gate="deterministic",
        posix_only=False,
        run=row_migration_seeds_and_drains,
    ),
    LadderRow(
        id="s2c2.growth_measurement_gate",
        stage="2c2",
        title="file-v0 history growth is measured per transaction and gated on retention integrity, claiming no capacity horizon",
        product_path="real_cli",
        gate="deterministic",
        posix_only=False,
        run=row_growth_measurement_gate,
    ),
    LadderRow(
        id="s2c2.archive_after_leased_completion_parity",
        stage="2c2",
        title="archiving a Todo whose released lease stays on disk keeps the candidate head matched and qualifiable",
        product_path="real_cli",
        gate="deterministic",
        posix_only=False,
        run=row_archive_after_leased_completion_parity,
    ),
)

PENDING_ROWS: tuple[PendingRow, ...] = (
    PendingRow(
        "s2c2.sustained_parity_soak",
        "2c2",
        "a >=10-day synthetic-goal soak of the selected local profile (RFC section 7.2, lane L); "
        "bounded qualification reports sustained_parity_verdict=not_evaluated",
    ),
)


def row_by_id(row_id: str) -> LadderRow:
    for row in LADDER_ROWS:
        if row.id == row_id:
            return row
    raise KeyError(row_id)


# ---------------------------------------------------------------------------
# Gates, redaction, and row execution
# ---------------------------------------------------------------------------


def gate_unverified_reason(gate: str, environ: Mapping[str, str]) -> tuple[str, JsonObject] | None:
    """Return the unverified reason for an env gate whose stack is absent."""

    required = GATE_REQUIREMENTS[gate]
    missing = sorted(name for name in required if not environ.get(name))
    if missing:
        return GATE_UNVERIFIED_REASON[gate], {"missing_variables": missing}
    for flag in LIVE_OPT_IN_FLAGS:
        if flag in required and environ.get(flag) != "1":
            return f"{flag.lower()}_not_enabled", {"flag": flag}
    return None


def default_forbidden_tokens(roots: Iterable[Path], environ: Mapping[str, str]) -> list[str]:
    """Substrings whose presence in a report is a privacy leak."""

    tokens: set[str] = set()
    for root in roots:
        tokens.add(str(root))
        tokens.add(str(Path(root).resolve()))
    temp_root = Path(tempfile.gettempdir())
    tokens.update({str(temp_root), str(temp_root.resolve())})
    tokens.add(environ.get("HOME") or str(Path.home()))
    tokens.update({str(REPO_ROOT), str(REPO_ROOT.resolve())})
    for name in (POSTGRES_URL_VARIABLE, *NOKV_STACK_VARIABLES, *NOKV_AUTHORITY_VARIABLES):
        value = environ.get(name)
        if value:
            tokens.add(value)
    tokens.update(_nokv_authority_config_tokens(environ))
    return sorted((token for token in tokens if len(token) >= 4), key=len, reverse=True)


def _nokv_authority_config_tokens(environ: Mapping[str, str]) -> set[str]:
    """Every string leaf of the ignored client configuration is a forbidden token."""

    path = _absolute_existing_path(environ.get(NOKV_AUTHORITY_CONFIG_VARIABLE), must_be_file=True)
    if path is None:
        return set()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    tokens: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, str):
            tokens.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(document)
    return tokens


def redact(text: str, forbidden: Sequence[str]) -> str:
    for token in forbidden:
        text = text.replace(token, "<redacted>")
    return text


def _failure_result(
    row: LadderRow,
    exc: BaseException,
    *,
    forbidden: Sequence[str],
    started: float,
) -> RowResult:
    reason_code = "assertion_failed" if isinstance(exc, AssertionError) else "row_exception"
    return RowResult(
        row=row,
        status="fail",
        reason_code=reason_code,
        evidence={
            "error_type": type(exc).__name__,
            "message": redact(str(exc), forbidden)[:500],
        },
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def run_row(
    row: LadderRow,
    *,
    root: Path,
    environ: Mapping[str, str] | None = None,
) -> RowResult:
    """Run one row in ``root`` and classify its outcome without raising."""

    started = time.monotonic()
    env: Mapping[str, str] = dict(os.environ) if environ is None else environ
    forbidden = default_forbidden_tokens([root], env)
    if row.posix_only and os.name == "nt":
        return RowResult(row, "unverified", "posix_only", {"platform": os.name}, 0)
    gate = gate_unverified_reason(row.gate, env)
    if gate is not None:
        return RowResult(row, "unverified", gate[0], gate[1], 0)
    try:
        outcome = row.run(RowContext(root=root, environ=env))
    except Exception as exc:
        # Every row failure becomes a typed, redacted result; nothing escapes.
        return _failure_result(row, exc, forbidden=forbidden, started=started)
    if outcome.status not in {"pass", "unverified"}:
        return _failure_result(
            row,
            RowAssertionError(f"row returned unsupported status {outcome.status!r}"),
            forbidden=forbidden,
            started=started,
        )
    return RowResult(
        row=row,
        status=outcome.status,
        reason_code=outcome.reason_code,
        evidence=outcome.evidence,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


# ---------------------------------------------------------------------------
# Report, bindings, privacy scan, exit policy
# ---------------------------------------------------------------------------


def _git_output(*arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _probe_digests() -> list[JsonObject]:
    digests: list[JsonObject] = []
    for relative in PROBE_SOURCES:
        path = REPO_ROOT / relative
        if path.exists():
            digests.append({"path": relative.as_posix(), "sha256": sha256_hex(path.read_bytes())})
    return digests


def _nokv_client_config_digest(environ: Mapping[str, str]) -> str | None:
    config_path = _absolute_existing_path(
        environ.get(NOKV_AUTHORITY_CONFIG_VARIABLE), must_be_file=True
    )
    if config_path is not None:
        try:
            return _nokv_authority_config_sha256(config_path)
        except (OSError, ValueError):
            return None
    public = {
        name: environ[name]
        for name in NOKV_STACK_VARIABLES
        if name not in NOKV_SECRET_VARIABLES and environ.get(name)
    }
    if len(public) != len(NOKV_STACK_VARIABLES) - len(NOKV_SECRET_VARIABLES):
        return None
    return sha256_hex(json.dumps(public, sort_keys=True, separators=(",", ":")))


def _nokv_sdk_version() -> str | None:
    for distribution in ("nokv", "nokv-python"):
        try:
            return metadata.version(distribution)
        except metadata.PackageNotFoundError:
            continue
    return None


def collect_bindings(environ: Mapping[str, str]) -> JsonObject:
    """Pin what the report was produced against; ``None`` means unknown."""

    commit = _git_output("rev-parse", "HEAD")
    status = _git_output("status", "--porcelain")
    postgres_url = environ.get(POSTGRES_URL_VARIABLE)
    return {
        "loopx_commit": commit.strip() if commit else None,
        "loopx_tree_dirty": bool(status.strip()) if status is not None else None,
        "probe_sha256": _probe_digests(),
        "nokv_client_config_sha256": _nokv_client_config_digest(environ),
        "nokv_sdk_version": _nokv_sdk_version(),
        "postgres_url_sha256_prefix": sha256_hex(postgres_url)[:12] if postgres_url else None,
        "pg_package_version": _pg_package_version(),
    }


def exit_code_for(
    summary: Mapping[str, int],
    *,
    allow_unverified: bool,
    allow_pending: bool = False,
) -> int:
    """Green means every selected obligation was verified, not that a report exists.

    A pending row is a selected obligation with no executable evidence, so it
    blocks a green exit exactly like an unverified row unless the caller
    explicitly allows it. A privacy violation anywhere in the report, in a row
    or confined to the bindings, is a failed evidence run even after the leak
    was redacted; no flag relaxes it.
    """

    if summary["fail"] != 0:
        return 1
    if summary.get("privacy_violations", 0) != 0:
        return 1
    if summary["unverified"] != 0 and not allow_unverified:
        return 1
    if summary.get("pending", 0) != 0 and not allow_pending:
        return 1
    return 0


def _finalize_report(
    rows: Sequence[JsonObject],
    pending: Sequence[JsonObject],
    bindings: JsonObject,
    *,
    allow_unverified: bool,
    allow_pending: bool,
    generated_at: str,
) -> JsonObject:
    summary = {status: sum(1 for row in rows if row["status"] == status) for status in ROW_STATUSES}
    summary["pending"] = len(pending)
    summary["executed"] = len(rows)
    # Row leaks are already failures; a leak confined to the bindings has no
    # row to fail, so the count is what the exit policy consumes.
    summary["privacy_violations"] = sum(
        1 for row in rows if row.get("reason_code") == "privacy_violation"
    ) + (1 if bindings.get("privacy_violation") else 0)
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at": generated_at,
        "rows": list(rows),
        "pending": list(pending),
        "summary": summary,
        "bindings": bindings,
        "exit_policy": {
            "allow_unverified": allow_unverified,
            "allow_pending": allow_pending,
            "exit_code": exit_code_for(
                summary,
                allow_unverified=allow_unverified,
                allow_pending=allow_pending,
            ),
            "rule": EXIT_POLICY_RULE,
        },
    }


def _leaked_tokens(value: object, forbidden: Sequence[str]) -> list[str]:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return [token for token in forbidden if token in serialized]


def assert_public_safe(report: JsonObject, *, forbidden: Sequence[str]) -> JsonObject:
    """Turn any leak of a forbidden substring into ``fail/privacy_violation``.

    A leak confined to the bindings nulls every binding and still fails the
    run through ``summary.privacy_violations``.
    """

    rows: list[JsonObject] = []
    for row in report["rows"]:
        leaked = _leaked_tokens(row, forbidden)
        if leaked:
            rows.append(
                {
                    **row,
                    "status": "fail",
                    "reason_code": "privacy_violation",
                    "evidence": {"leaked_token_count": len(leaked)},
                }
            )
        else:
            rows.append(dict(row))
    bindings = dict(report["bindings"])
    if _leaked_tokens(bindings, forbidden):
        bindings = {key: None for key in bindings}
        bindings["privacy_violation"] = True
    return _finalize_report(
        rows,
        report["pending"],
        bindings,
        allow_unverified=bool(report["exit_policy"]["allow_unverified"]),
        allow_pending=bool(report["exit_policy"].get("allow_pending", False)),
        generated_at=str(report["generated_at"]),
    )


def build_report(
    results: Sequence[RowResult],
    *,
    pending: Sequence[PendingRow],
    allow_unverified: bool,
    allow_pending: bool = False,
    environ: Mapping[str, str] | None = None,
    forbidden: Sequence[str] | None = None,
) -> JsonObject:
    """Assemble the report and run the privacy scan over it."""

    env: Mapping[str, str] = dict(os.environ) if environ is None else environ
    tokens = list(forbidden) if forbidden is not None else default_forbidden_tokens([], env)
    report = _finalize_report(
        [result.as_dict() for result in results],
        [row.describe() for row in pending],
        collect_bindings(env),
        allow_unverified=allow_unverified,
        allow_pending=allow_pending,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    return assert_public_safe(report, forbidden=tokens)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ladder",
        description="Run the shared-goal-authority stage ladder against this checkout.",
    )
    parser.add_argument("--stage", action="append", choices=STAGES, help="Only run rows of this stage; repeatable.")
    parser.add_argument("--row", action="append", help="Only run this row id; repeatable.")
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="Exit 0 even when env-gated rows could not run; failures still exit 1.",
    )
    parser.add_argument(
        "--allow-pending",
        action="store_true",
        help=(
            "Exit 0 even when the selection includes declared-but-unimplemented "
            "rows; they are still reported as pending, never as pass."
        ),
    )
    parser.add_argument("--report-json", help="Write the JSON report to this path as well as stdout.")
    parser.add_argument("--list", action="store_true", help="Print the row registry and pending rows, then exit.")
    return parser


def _selected(
    stages: Sequence[str] | None,
    row_ids: Sequence[str] | None,
) -> tuple[list[LadderRow], list[PendingRow]]:
    rows = [row for row in LADDER_ROWS if not stages or row.stage in stages]
    pending = [row for row in PENDING_ROWS if not stages or row.stage in stages]
    if row_ids:
        wanted = set(row_ids)
        rows = [row for row in rows if row.id in wanted]
        pending = [row for row in pending if row.id in wanted]
    return rows, pending


def _run_selected(rows: Sequence[LadderRow], environ: Mapping[str, str]) -> tuple[list[RowResult], list[str]]:
    results: list[RowResult] = []
    tokens: set[str] = set()
    for row in rows:
        with tempfile.TemporaryDirectory(prefix="loopx-authority-ladder-") as scratch:
            root = Path(scratch)
            tokens.update(default_forbidden_tokens([root], environ))
            results.append(run_row(row, root=root, environ=environ))
    return results, sorted(tokens, key=len, reverse=True)


def _print_summary(report: JsonObject) -> None:
    for status in ("fail", "unverified"):
        ids = [f"{row['id']} ({row['reason_code']})" for row in report["rows"] if row["status"] == status]
        if ids:
            print(f"{status} rows: {', '.join(ids)}", file=sys.stderr)
    pending_ids = [f"{row['id']} ({row['pending_until']})" for row in report["pending"]]
    if pending_ids:
        print(f"pending rows (not verified): {', '.join(pending_ids)}", file=sys.stderr)
    if report["bindings"].get("privacy_violation"):
        print("privacy violation confined to report bindings: redacted, the run fails", file=sys.stderr)
    print(
        "summary: " + ", ".join(f"{key}={value}" for key, value in sorted(report["summary"].items()))
        + f"; exit_code={report['exit_policy']['exit_code']}",
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    rows, pending = _selected(args.stage, args.row)
    if args.list:
        listing = {
            "schema_version": LIST_SCHEMA,
            "rows": [row.describe() for row in rows],
            "pending": [row.describe() for row in pending],
        }
        print(json.dumps(listing, indent=2, sort_keys=True))
        return 0
    if not rows and not pending:
        print("no rows match the requested selection", file=sys.stderr)
        return 2
    environ = dict(os.environ)
    results, tokens = _run_selected(rows, environ)
    report = build_report(
        results,
        pending=pending,
        allow_unverified=bool(args.allow_unverified),
        allow_pending=bool(args.allow_pending),
        environ=environ,
        forbidden=tokens or default_forbidden_tokens([], environ),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.report_json:
        Path(args.report_json).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    _print_summary(report)
    return int(report["exit_policy"]["exit_code"])


__all__ = [
    "EXIT_POLICY_RULE",
    "FILE_MATRIX_ROWS",
    "GATES",
    "GATE_REQUIREMENTS",
    "LADDER_ROWS",
    "LIVE_OPT_IN_FLAGS",
    "NOKV_AUTHORITY_CONFIG_VARIABLE",
    "NOKV_AUTHORITY_LIVE_FLAG",
    "NOKV_AUTHORITY_PYTHON_VARIABLE",
    "NOKV_AUTHORITY_VARIABLES",
    "NOKV_AUTHORITY_WORKBENCH_VARIABLE",
    "NOKV_LIVE_FLAG",
    "NOKV_STACK_VARIABLES",
    "PENDING_ROWS",
    "POSTGRES_URL_VARIABLE",
    "PRODUCT_PATHS",
    "REPORT_SCHEMA",
    "ROW_STATUSES",
    "STAGES",
    "LadderRow",
    "PendingRow",
    "RowAssertionError",
    "RowContext",
    "RowOutcome",
    "RowResult",
    "assert_public_safe",
    "build_report",
    "collect_bindings",
    "default_forbidden_tokens",
    "exit_code_for",
    "gate_unverified_reason",
    "main",
    "passed",
    "redact",
    "row_by_id",
    "run_row",
    "unverified",
]


if __name__ == "__main__":
    raise SystemExit(main())
