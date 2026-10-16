import assert from "node:assert/strict";
import {mkdir, mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {dirname, join} from "node:path";
import {tmpdir} from "node:os";
import test from "node:test";
import {atomicWriteJson} from "../../loopx/control_plane/effect_runtime_io.ts";
import {executeTaskLeaseLifecycle, TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION} from "../../loopx/control_plane/work_items/task_lease_lifecycle.ts";

// Fixed 1.x transaction bytes: only $ROOT relocates. The candidate never
// generates its own historical hashes, identities, tokens or generations.
const history = JSON.parse(await readFile(new URL(
  "../control_plane/fixtures/retained_lease_receipts_1x.json", import.meta.url), "utf8"));
async function restore(t: test.TestContext, name: string) {
  assert.equal(history.source_commit, "60d833b13c85135587b725013b247e827663ffb9");
  const root = await mkdtemp(join(tmpdir(), "loopx-retained-1x-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const original = JSON.parse(JSON.stringify(history.scenarios[name]).replaceAll("$ROOT", root));
  for (const [relative, value] of Object.entries(original.files)) {
    const path = join(root, "runtime", relative);
    await mkdir(dirname(path), {recursive: true});
    await writeFile(path, JSON.stringify(value));
  }
  const leasePath = join(root, "runtime/goals/goal-a/task-leases/todo_target.json");
  const receipt = Object.keys(original.files).find(path => path.includes("/.lifecycle"));
  return {...original, root, leasePath,
    receiptPath: receipt ? join(root, "runtime", receipt) : null};
}

for (const operation of ["renew", "transfer"] as const) {
  for (const phase of ["committed", "prepared"] as const) {
    for (const later of [false, true]) {
      test(`1.x ${operation} ${phase} recovery preserves ${later ? "later" : "original"} lease`, async t => {
        const f = await restore(t, operation);
        const receipt = JSON.parse(await readFile(f.receiptPath!, "utf8"));
        await atomicWriteJson(f.receiptPath!, {...receipt, state: phase});
        if (later) {
          const lease = JSON.parse(await readFile(f.leasePath, "utf8"));
          await atomicWriteJson(f.leasePath, {...lease, version: 9, lease_epoch: 9, idempotency_key: "later"});
        }
        const before = await readFile(f.leasePath, "utf8");
        const result = await executeTaskLeaseLifecycle(f.request, {
          now: () => new Date("2026-09-01T03:03:00Z"),
          beforeWrite: () => assert.fail("receipt recovery must not repeat a lease write"),
        });
        assert.equal(result.ok, !later, JSON.stringify(result));
        if (!later) {
          assert.equal(result.idempotent, true);
          assert.equal(JSON.parse(await readFile(f.receiptPath!, "utf8")).state, "committed");
        }
        assert.equal(await readFile(f.leasePath, "utf8"), before);
      });
    }
  }
}

test("1.x original release retains CAS, expiry cleanup and original generation", async t => {
  const f = await restore(t, "original");
  const before = await readFile(f.leasePath, "utf8");
  const release = {...f.request, schema_version: TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,
    operation: "release", expected_version: 1, authority: null};
  for (const change of [{owner: "agent-b"}, {idempotency_key: "other"}, {expected_version: 0}]) {
    assert.equal((await executeTaskLeaseLifecycle({...release, ...change})).ok, false);
    assert.equal(await readFile(f.leasePath, "utf8"), before);
  }
  const result = await executeTaskLeaseLifecycle(release, {now: () => new Date("2026-09-02T00:00:00Z")});
  assert.equal(result.released, true, JSON.stringify(result));
  const after = await readFile(f.leasePath, "utf8");
  const lease = JSON.parse(after), original = JSON.parse(before);
  assert.equal(lease.status, "released");
  assert.equal(lease.version, original.version);
  assert.equal(lease.lease_epoch, original.lease_epoch);
  assert.equal((await executeTaskLeaseLifecycle(release)).idempotent, true);
  assert.equal(await readFile(f.leasePath, "utf8"), after);
});

test("1.x held receipt repairs lost close response after the exact release committed", async t => {
  const f = await restore(t, "terminal-held");
  const original = JSON.parse(await readFile(f.receiptPath!, "utf8"));
  const close = {...f.request, operation: "fence_close", authority: null,
    owner: null, idempotency_key: null, expected_version: null,
    lock_token: original.lock_token, fence_operation_id: original.operation_id,
    committed: true, release_lease: true, fence_owner: original.fence_owner,
    fence_idempotency_key: original.fence_idempotency_key,
    fence_expected_version: original.fence_expected_version,
    fence_expected_lease_epoch: original.fence_expected_lease_epoch};
  // Independent post-write/pre-close crash window. No held lock is invented:
  // only a released matching generation may finish this old close receipt.
  const lease = JSON.parse(await readFile(f.leasePath, "utf8"));
  const before = await executeTaskLeaseLifecycle(close);
  assert.equal(before.error_code, "fence_token_invalid");
  assert.deepEqual(JSON.parse(await readFile(f.leasePath, "utf8")), lease);
  await atomicWriteJson(f.leasePath, {...lease, status: "released", released_at: "2026-09-01T03:03:00Z"});
  const first = await executeTaskLeaseLifecycle(close);
  assert.equal(first.ok, true, JSON.stringify(first));
  assert.equal(first.released, true);
  const after = await readFile(f.leasePath, "utf8");
  assert.equal((await executeTaskLeaseLifecycle(close)).idempotent, true);
  assert.equal(await readFile(f.leasePath, "utf8"), after);
});
