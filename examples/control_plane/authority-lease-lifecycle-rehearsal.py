#!/usr/bin/env python3
"""Replay a read-only Goal snapshot through an immutable legacy and local providers.

Only disposable copies receive a synthetic open Todo/lease and mutations. The
report contains counts, source/observation digests and explicit semantic deltas;
source text, actor IDs and paths are never printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from loopx.control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot  # noqa: E402
from loopx.history import load_registry  # noqa: E402
from loopx.paths import resolve_runtime_root  # noqa: E402
from loopx.state_refresh import resolve_goal_state  # noqa: E402

NODE_REHEARSAL = r"""
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
let raw=''; for await (const chunk of process.stdin) raw+=chunk;
const input=JSON.parse(raw);
const moduleAt=(root,path)=>import(pathToFileURL(join(root,'loopx/control_plane',path)).href);
const {executeTaskLeaseAcquire: currentAcquire}=await moduleAt(input.repo,'work_items/task_lease_acquire.ts');
const {executeTaskLeaseAcquire: legacyAcquire}=await moduleAt(input.baseline_repo,'work_items/task_lease_acquire.ts');
const {executeTaskLeaseLifecycle: current}=await moduleAt(input.repo,'work_items/task_lease_lifecycle.ts');
const {executeTaskLeaseLifecycle: legacy}=await moduleAt(input.baseline_repo,'work_items/task_lease_lifecycle.ts');
const {executeCoordinationTodoUpdate: update}=await moduleAt(input.repo,'coordination/todo_update.ts');
const {FileAuthorityStore}=await moduleAt(input.repo,'coordination/file_authority_store.ts');
const {SqliteAuthorityStore}=await moduleAt(input.repo,'coordination/sqlite_authority_store.ts');
const {selectLocalSqliteAuthority}=await moduleAt(input.repo,'coordination/local_authority_provider.ts');
const {coordinationTodoReadModel}=await moduleAt(input.repo,'coordination/coordination_projection.ts');
const {canonicalAuthoritySha256}=await moduleAt(input.repo,'coordination/authority_store_codec.ts');
const {engageLegacyCoordinationWriterFence}=await moduleAt(input.repo,'coordination/legacy_writer_fence.ts');
const digest=value=>canonicalAuthoritySha256(value);
const goal=input.goal_id, target='todo_lifecycle_rehearsal', acquisition='todo_acquire_rehearsal', handover='todo_claim_transfer_rehearsal';
assert(!input.projection.todos.some(t=>t.todo_id===handover));
assert(!input.projection.todos.some(t=>t.todo_id===acquisition));
assert(!input.projection.todos.some(t=>t.todo_id===target));
const initial=structuredClone(input.projection);
initial.todos.push({schema_version:'todo_item_v0',todo_id:target,role:'agent',status:'open',done:false,
  text:'Isolated lease lifecycle rehearsal',archive_state:'active',source_section:'Agent Todo',
  claimed_by:null,excluded_agents:[],task_class:'advancement_task'});
initial.todos.push({...initial.todos.at(-1),todo_id:acquisition,text:'Isolated acquisition and takeover rehearsal'});
initial.todos.push({...initial.todos.at(-1),todo_id:handover,text:'Isolated claimed execution handover',claimed_by:'agent-a'});
initial.todos.sort((a,b)=>a.todo_id<b.todo_id?-1:a.todo_id>b.todo_id?1:0);
initial.todo_read_model=coordinationTodoReadModel(initial.todos,initial.todo_read_model.schema_version);
initial.handoff_mode='hard_lease';
const original={schema_version:'task_lease_v0',goal_id:goal,todo_id:target,owner:'agent-a',
  idempotency_key:'rehearsal-a',version:3,lease_epoch:7,status:'active',write_scopes:['src/**'],
  acquire_ttl_seconds:600,acquired_at:'2026-09-13T10:00:00Z',updated_at:'2026-09-13T10:00:00Z',expires_at:'2026-09-13T10:10:00Z'};
initial.leases.push(original,{...original,todo_id:handover,idempotency_key:'handover-a',write_scopes:['handover/**']});
initial.leases.sort((a,b)=>a.todo_id<b.todo_id?-1:a.todo_id>b.todo_id?1:0);
const root=await mkdtemp(join(tmpdir(),'loopx-lease-rehearsal-'));
const report={}; const observations={}; const finalHeads={};
try {
  for (const arm of ['legacy','file','sqlite']) {
    const runtime=join(root,arm); await mkdir(runtime,{recursive:true});
    const source=join(runtime,'facts.json'), bytes=JSON.stringify({registered_agents:['agent-a','agent-b']});
    await writeFile(source,bytes);
    const authority={handoff_mode:'hard_lease',registered_agent_candidates:[['agent-a','agent-b']],
      todos:initial.todos, todo_projection_error:null,
      source_receipts:[{source_id:'registry',path:source,state:'file',sha256:createHash('sha256').update(bytes).digest('hex')}]};
    let store=null; const dependencies={};
    const leaseDir=join(runtime,'goals',goal,'task-leases'); await mkdir(leaseDir,{recursive:true});
    // Every arm carries identical contradictory legacy files after promotion.
    for (const row of initial.leases) await writeFile(join(leaseDir,`${row.todo_id}.json`),JSON.stringify(row));
    const legacyBefore=await readFile(join(leaseDir,`${target}.json`));
    if (arm==='file') store=new FileAuthorityStore(join(runtime,'authority/file-v0'),goal);
    if (arm==='sqlite') {
      assert.equal((await selectLocalSqliteAuthority(runtime,goal,true)).ok,true);
      store=new SqliteAuthorityStore(join(runtime,'authority/sqlite-v0'),goal);
    }
    if (store) {
      assert.equal((await store.commitAuthority({expected_provider_revision:null,operation_id:'seed',events:[],receipts:[],next_projection:initial})).status,'applied');
      const h=await store.loadAuthority();assert.equal(h.status,'loaded');
      assert.equal((await engageLegacyCoordinationWriterFence({schema_version:'loopx_legacy_coordination_writer_fence_engage_request_v0',
        runtime_root:runtime,goal_id:goal,state_path:source,fence:{schema_version:'loopx_legacy_coordination_writer_fence_v0',
        state:'engaged',goal_id:goal,fence_id:'rehearsal',source_version:'state:1',source_projection_sha256:digest(initial),
        expected_shadow_provider_revision:h.provider_revision}})).status,'applied');
    }
    const acquireBase={schema_version:store?'loopx_canonical_task_lease_acquire_request_v0':'loopx_task_lease_acquire_native_v0',
      runtime_root:runtime,goal_id:goal,todo_id:acquisition,owner:'agent-a',idempotency_key:'acquire-a',
      expected_version:0,ttl_seconds:600,write_scopes:['isolated-acquisition/**'],authority};
    const invokeAcquire=(request,now)=>(store?currentAcquire:legacyAcquire)(request,{...dependencies,now:()=>new Date(now)});
    const acquired=await invokeAcquire(acquireBase,'2026-09-13T10:05:00Z');
    assert.equal(acquired.ok,true,`${arm} acquire: ${acquired.error_code}`);
    assert.deepEqual(acquired.lease,{schema_version:'task_lease_v0',goal_id:goal,todo_id:acquisition,
      owner:'agent-a',idempotency_key:'acquire-a',version:1,lease_epoch:1,status:'active',
      write_scopes:['isolated-acquisition/**'],acquire_ttl_seconds:600,acquired_at:'2026-09-13T10:05:00Z',
      updated_at:'2026-09-13T10:05:00Z',expires_at:'2026-09-13T10:15:00Z'});
    const acquireReplay=await invokeAcquire(acquireBase,'2026-09-13T10:06:00Z');
    if(store) {assert.equal(acquireReplay.status,'replayed');assert.deepEqual(acquireReplay.lease,acquired.lease);}
    else assert.equal(acquireReplay.error_code,'version_mismatch');
    const takeover=await invokeAcquire({...acquireBase,owner:'agent-b',idempotency_key:'acquire-b',expected_version:1},'2026-09-14T10:05:00Z');
    assert.equal(takeover.ok,true,`${arm} takeover: ${takeover.error_code}`);
    assert.deepEqual(takeover.lease,{...acquired.lease,owner:'agent-b',idempotency_key:'acquire-b',version:2,lease_epoch:2,
      acquired_at:'2026-09-14T10:05:00Z',updated_at:'2026-09-14T10:05:00Z',expires_at:'2026-09-14T10:15:00Z'});
    if(store) {
      const staleAcquire=await invokeAcquire(acquireBase,'2026-09-14T10:06:00Z');
      assert.equal(staleAcquire.error_code,'idempotency_key_reuse');
      assert.equal((await import('node:fs')).existsSync(join(leaseDir,`${acquisition}.json`)),false);
    }
    const base={schema_version:store?'loopx_canonical_task_lease_lifecycle_request_v0':'loopx_task_lease_lifecycle_native_v0',
      runtime_root:runtime,goal_id:goal,todo_id:target,owner:'agent-a',idempotency_key:'rehearsal-a',
      expected_version:3,ttl_seconds:600,authority,current_time:'2026-09-13T10:05:00Z'};
    const transfer={...base,operation:'transfer',new_owner:'agent-b',new_idempotency_key:'rehearsal-b'};
    const renew={...base,operation:'renew',owner:'agent-b',idempotency_key:'rehearsal-b',expected_version:4};
    const release={...renew,operation:'release',expected_version:5,ttl_seconds:null,current_time:'2026-09-14T10:05:00Z'};
    const invoke=request=>(store?current:legacy)(request,dependencies);
    const results=[];
    for (const request of [transfer,renew,release]) {
      const result=await invoke(request); assert.equal(result.ok,true,`${arm}:${result.error_code}`);
      results.push(result);
      if (store) assert.equal(result.source_authority,`${arm}_v0`);
    }
    assert.deepEqual(results[0].lease,{...original,owner:'agent-b',idempotency_key:'rehearsal-b',version:4,lease_epoch:8,
      updated_at:'2026-09-13T10:05:00Z',expires_at:'2026-09-13T10:15:00Z'});
    assert.deepEqual(results[1].lease,{...results[0].lease,version:5});
    assert.deepEqual(results[2].lease,{...results[1].lease,status:'released',updated_at:'2026-09-14T10:05:00Z',released_at:'2026-09-14T10:05:00Z'});
    const stale=await invoke({...release,owner:'agent-a',idempotency_key:'rehearsal-a',expected_version:3});
    assert.equal(stale.error_code,'version_mismatch'); assert.equal(stale.actual_version,5);
    const invalidReceiver=await invoke({...transfer,new_owner:'unknown-agent',expected_version:5});
    assert.equal(invalidReceiver.error_code,'owner_not_registered');
    const replay=await invoke(transfer);
    const claimedTransfer={...transfer,todo_id:handover,idempotency_key:'handover-a',new_idempotency_key:'handover-b'};
    assert.equal((await invoke(claimedTransfer)).error_code,'owner_conflicts_with_claim');
    const jointRequest={...claimedTransfer,schema_version:'loopx_canonical_task_lease_claim_transfer_request_v0',transfer_claim:true};
    const joint=await invoke(jointRequest);
    if (store) {
      assert.equal(joint.ok,true);assert.equal(joint.claimed_by,'agent-b');assert.equal(joint.todo_changed,true);
      assert.equal(joint.lease.owner,'agent-b');assert.equal(joint.lease.version,4);assert.equal(joint.lease.lease_epoch,8);
      const edit={goal_id:goal,todo_id:handover,actor_agent_id:'agent-b',expected_role:'agent',registered_agents:['agent-a','agent-b'],
        operation_id:'recipient-edit',patch:{note:'Receiver continues the same work'},clear_fields:[],dry_run:false,
        lease_idempotency_key:'handover-b',lease_expected_version:4,now:new Date('2026-09-13T10:06:00Z')};
      assert.equal((await update(store,{...edit,actor_agent_id:'agent-a',lease_idempotency_key:'handover-a',lease_expected_version:3})).reason_code,'update_owner_mismatch');
      assert.equal((await update(store,edit)).status,'applied');
      const recoveredJoint=await invoke(jointRequest);assert.equal(recoveredJoint.status,'replayed');
      assert.deepEqual(recoveredJoint.original_receipt,joint.original_receipt);
      assert.equal(replay.status,'replayed');assert.deepEqual(replay.original_receipt,results[0].original_receipt);
      assert.deepEqual(replay.lease,results[0].lease);
      const final=await store.loadAuthority();assert.equal(final.status,'loaded');assert.equal(final.cursor,'8');
      assert.deepEqual(final.head.todos,initial.todos.map(t=>t.todo_id===handover?{...t,claimed_by:'agent-b',
        last_actor_agent_id:'agent-b',note:'Receiver continues the same work',updated_at:'2026-09-13T10:06:00Z'}:t));
      assert.deepEqual(final.head.leases.filter(l=>![target,acquisition,handover].includes(l.todo_id)),
        initial.leases.filter(l=>![target,handover].includes(l.todo_id)));
      assert.deepEqual(await readFile(join(leaseDir,`${handover}.json`)),Buffer.from(JSON.stringify(initial.leases.find(l=>l.todo_id===handover))));
      assert.deepEqual(await readFile(join(leaseDir,`${target}.json`)),legacyBefore);
      finalHeads[arm]=final.head;
      report[arm]={passed:true,commits:8,historical_replay:'original_receipt',acquire_retry:'current_proof',retired_acquire_rejected:true,
        atomic_claim_transfer:true,old_owner_rejected:true,recipient_update:true};
    } else {
      assert.equal(joint.ok,false);assert.equal(joint.error_code,'schema_mismatch');
      assert.equal(replay.error_code,'lifecycle_receipt_state_mismatch');
      report[arm]={passed:true,historical_replay:'baseline_rejected_after_later_mutation',atomic_claim_transfer:'unsupported'};
    }
    observations[arm]=[acquired,takeover,...results].map(r=>({lease:r.lease}));
  }
  for (const arm of ['file','sqlite']) assert.deepEqual(observations[arm],observations.legacy);
  assert.deepEqual(finalHeads.file,finalHeads.sqlite);
  process.stdout.write(JSON.stringify({schema_version:'authority_lease_lifecycle_rehearsal_v0',
    initial_todos:initial.todos.length,initial_leases:initial.leases.length,fixture_sha256:digest(initial),
    observation_sha256:digest(observations.file),provider_head_sha256:digest(finalHeads.file),arms:report,
    intentional_delta:'canonical_receipt_recovery_and_current_acquire_proof;_explicit_create_CAS_retry_no_longer_mismatches;_explicit_atomic_claim_transfer',non_target_records_unchanged:true}));
} finally {
  await rm(root,{recursive:true,force:true});
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--baseline-repo", type=Path, required=True)
    parser.add_argument("--private-diagnostics", type=Path,
                        help="optional owner-only file for raw failure diagnostics; never publish it")
    args = parser.parse_args()
    baseline = args.baseline_repo.resolve()
    def git(*command):
        return subprocess.check_output(["git", "-C", str(baseline), *command], text=True).strip()
    if git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("baseline must have no tracked changes")
    revision = git("rev-parse", "HEAD")
    registry_path = args.registry.resolve()
    registry_bytes = registry_path.read_bytes()
    registry = load_registry(registry_path)
    goal = next(g for g in registry["goals"] if g["id"] == args.goal_id)
    runtime = resolve_runtime_root(registry, None, registry_path=registry_path)
    _, _, state = resolve_goal_state(registry=registry, goal_id=args.goal_id, project_override=None, state_file_override=None)
    projection, snapshot = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime, state_path=state, registry_path=registry_path)
    source_digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    child = subprocess.run(["node", "--no-warnings", "--experimental-sqlite", "--experimental-strip-types", "--input-type=module", "-e", NODE_REHEARSAL],
        input=json.dumps({"repo": str(REPOSITORY), "baseline_repo": str(baseline), "goal_id": args.goal_id, "projection": projection}),
        cwd=REPOSITORY, capture_output=True, text=True, timeout=180, check=False)
    if child.returncode:
        if args.private_diagnostics:
            descriptor = os.open(args.private_diagnostics, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(child.stderr)
        raise SystemExit(f"isolated lifecycle rehearsal failed (exit {child.returncode}); no qualification; use --private-diagnostics for owner-only diagnosis")
    after_projection, after_snapshot = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime, state_path=state, registry_path=registry_path)
    if projection != after_projection or snapshot != after_snapshot or registry_path.read_bytes() != registry_bytes:
        raise SystemExit("live source changed during rehearsal; rerun from a stable snapshot")
    result = json.loads(child.stdout)
    result.update(baseline_revision=revision, source_snapshot_sha256=source_digest, source_unchanged=True)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
