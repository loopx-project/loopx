import assert from "node:assert/strict";
import test from "node:test";
import {
  ColimaPgCoordinationRehearsal,
  type RehearsalReport,
} from "../../examples/coordination/colima-pg16-e2e-rehearsal.ts";

function assertAllRehearsalSteps(
  report: RehearsalReport,
  expectedMode: RehearsalReport["mode"],
  expectedBoundary: RehearsalReport["execution_boundary"],
): void {
  assert.equal(report.schema_version, "colima_pg16_coordination_rehearsal_report_v0");
  assert.equal(report.ok, true, `Rehearsal failed with error: ${report.error ?? JSON.stringify(report.steps)}`);
  assert.equal(report.mode, expectedMode);
  assert.equal(report.execution_boundary, expectedBoundary);
  assert.equal(report.transport, "https_tls");
  assert.equal(report.clock_mode, "controlled_service_clock");
  assert.equal(report.runtime_role_verified, expectedMode === "real_pg");
  assert.equal(report.tenant_id, "tenant-canary");
  assert.equal(report.goal_id, "loopx-quality-review");
  assert.equal(report.actors.actor_a, "operator.local");
  assert.equal(report.actors.actor_b, "worker.cloud");
  assert.equal(report.steps.length, 8);

  const stepsByName = new Map(report.steps.map(s => [s.step, s]));

  // Step 1: Schema initialization
  const step1 = stepsByName.get("schema_initialization_and_role_assembly");
  assert.ok(step1);
  assert.equal(step1.ok, true);
  assert.equal(step1.detail.status, "installed");
  assert.match(String(step1.detail.runtime_role), /^loopx_app_[0-9a-f]+$/);

  // Step 2: Genesis seed ingestion
  const step2 = stepsByName.get("genesis_seed_ingestion");
  assert.ok(step2);
  assert.equal(step2.ok, true);
  assert.equal(step2.detail.status, "seeded");
  assert.equal(step2.detail.cursor, "1");

  // Step 3: Actor A lease claim over HTTPS/TLS
  const step3 = stepsByName.get("actor_a_lease_claim");
  assert.ok(step3);
  assert.equal(step3.ok, true);
  assert.equal(step3.detail.result_status, "applied");
  assert.equal(step3.detail.claimed_by, "operator.local");
  assert.equal(step3.detail.transport, "https_tls");

  // Step 4: Active lease conflict rejection
  const step4 = stepsByName.get("active_lease_conflict_rejection");
  assert.ok(step4);
  assert.equal(step4.ok, true);
  assert.equal(step4.detail.status, "result");
  assert.ok(step4.detail.result_status === "rejected" || step4.detail.result_status === "failed");

  // Step 5: Default-deny boundary enforcement
  const step5 = stepsByName.get("default_deny_boundary_enforcement");
  assert.ok(step5);
  assert.equal(step5.ok, true);
  assert.equal(step5.detail.unauthorized_goal_status, "rejected");
  assert.equal(step5.detail.unauthorized_cred_status, "rejected");
  assert.equal(step5.detail.unauthorized_cred_reason, "principal_unauthenticated");

  // Step 6: Lease expiration takeover & stale-actor fencing
  const step6 = stepsByName.get("lease_expiration_takeover");
  assert.ok(step6);
  assert.equal(step6.ok, true);
  assert.equal(step6.detail.result_status, "applied");
  assert.equal(step6.detail.claimed_by, "worker.cloud");
  assert.equal(step6.detail.previous_lease_epoch, 1);
  assert.equal(step6.detail.new_lease_epoch, 2);
  assert.equal(step6.detail.stale_actor_fenced, true);
  // Prove takeover is driven from observed expires_at via controlled service clock rather than wall-clock sleep.
  const observedExpiresMs = Date.parse(String(step3.detail.expires_at));
  const advancedClockMs = Date.parse(String(step6.detail.advanced_service_clock));
  assert.ok(Number.isFinite(observedExpiresMs));
  assert.ok(Number.isFinite(advancedClockMs));
  assert.ok(observedExpiresMs - Date.now() > 60_000, "canonical lease expires ~2700s in the future");
  assert.equal(step6.detail.observed_expires_at, step3.detail.expires_at);
  assert.equal(advancedClockMs, observedExpiresMs + 1_000);

  // Step 7: Actor B task completion + historical recovery readback
  const step7 = stepsByName.get("actor_b_task_completion");
  assert.ok(step7);
  assert.equal(step7.ok, true);
  assert.equal(step7.detail.result_status, "applied");
  assert.equal(step7.detail.completed, true);
  assert.equal(step7.detail.recover_status, "found");
  assert.equal(step7.detail.historical_only, true);

  // Step 8: Cross-machine snapshot & direct authority readback
  const step8 = stepsByName.get("cross_machine_snapshot_readback");
  assert.ok(step8);
  assert.equal(step8.ok, true);
  assert.equal(step8.detail.status, "snapshot");
  assert.equal(typeof step8.detail.receipt_sha256, "string");
  assert.equal((step8.detail.receipt_sha256 as string).length, 64);
  assert.equal(step8.detail.todo_count, 1);
  assert.equal(step8.detail.read_status, "found");
  assert.equal(step8.detail.direct_cursor, "5");
}

test("Colima/PG16 coordination rehearsal completes all 8 HTTPS/TLS steps in simulation mode", async () => {
  const rehearsal = new ColimaPgCoordinationRehearsal({
    simulate: true,
    leaseTtlSeconds: 1,
  });
  const report = await rehearsal.run();
  assertAllRehearsalSteps(report, "simulation", "in_memory_simulated_pg_tls");
});

test(
  "Colima/PG16 coordination rehearsal qualifies all 8 steps against real PostgreSQL with restricted role and TLS",
  {skip: !process.env.LOOPX_TEST_POSTGRES_URL},
  async () => {
    const rehearsal = new ColimaPgCoordinationRehearsal({
      databaseUrl: process.env.LOOPX_TEST_POSTGRES_URL,
      simulate: false,
    });
    const report = await rehearsal.run();
    assertAllRehearsalSteps(report, "real_pg", "isolated_real_pg16_tls");
  },
);
