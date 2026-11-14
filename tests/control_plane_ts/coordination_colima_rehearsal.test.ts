import assert from "node:assert/strict";
import test from "node:test";
import {
  ColimaPgCoordinationRehearsal,
} from "../../examples/coordination/colima-pg16-e2e-rehearsal.ts";

test("Colima/PG16 coordination rehearsal completes all 8 verification steps in simulation mode", async () => {
  const rehearsal = new ColimaPgCoordinationRehearsal({
    simulate: true,
    leaseTtlSeconds: 1,
  });

  const report = await rehearsal.run();

  // Root contract assertions
  assert.equal(report.schema_version, "colima_pg16_coordination_rehearsal_report_v0");
  assert.equal(report.ok, true, `Rehearsal failed with error: ${report.error}`);
  assert.equal(report.mode, "simulation");
  assert.equal(report.tenant_id, "tenant-canary");
  assert.equal(report.goal_id, "loopx-quality-review");
  assert.equal(report.actors.actor_a, "ruiteng.macbook");
  assert.equal(report.actors.actor_b, "lusen.cloud");
  assert.equal(report.steps.length, 8);

  const stepsByName = new Map(report.steps.map(s => [s.step, s]));

  // Step 1: Schema initialization
  const step1 = stepsByName.get("schema_initialization_and_role_assembly");
  assert.ok(step1);
  assert.equal(step1.ok, true);
  assert.equal(step1.detail.status, "installed");
  assert.equal(step1.detail.runtime_role, "loopx_app");

  // Step 2: Genesis seed ingestion
  const step2 = stepsByName.get("genesis_seed_ingestion");
  assert.ok(step2);
  assert.equal(step2.ok, true);
  assert.equal(step2.detail.status, "applied");
  assert.equal(step2.detail.cursor, "1");

  // Step 3: Actor A lease claim
  const step3 = stepsByName.get("actor_a_lease_claim");
  assert.ok(step3);
  assert.equal(step3.ok, true);
  assert.equal(step3.detail.result_status, "applied");
  assert.equal(step3.detail.claimed_by, "ruiteng.macbook");

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

  // Step 6: Lease expiration takeover
  const step6 = stepsByName.get("lease_expiration_takeover");
  assert.ok(step6);
  assert.equal(step6.ok, true);
  assert.equal(step6.detail.result_status, "applied");
  assert.equal(step6.detail.claimed_by, "lusen.cloud");

  // Step 7: Actor B task completion
  const step7 = stepsByName.get("actor_b_task_completion");
  assert.ok(step7);
  assert.equal(step7.ok, true);
  assert.equal(step7.detail.result_status, "applied");
  assert.equal(step7.detail.completed, true);

  // Step 8: Cross-machine snapshot readback
  const step8 = stepsByName.get("cross_machine_snapshot_readback");
  assert.ok(step8);
  assert.equal(step8.ok, true);
  assert.equal(step8.detail.status, "snapshot");
  assert.equal(typeof step8.detail.receipt_sha256, "string");
  assert.equal((step8.detail.receipt_sha256 as string).length, 64);
  assert.equal(step8.detail.todo_count, 1);
});
