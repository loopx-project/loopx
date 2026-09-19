#!/usr/bin/env python3
"""Compare Monitor polling and observation updates on a read-only Goal snapshot.

Only disposable copies receive synthetic Monitor/lease records. The report is
bounded and excludes source text, identifiers, paths and connection strings.
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
import {createHash, randomUUID} from 'node:crypto';
import {mkdtemp, mkdir, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {Pool} from 'pg';
let raw=''; for await (const chunk of process.stdin) raw+=chunk;
const input=JSON.parse(raw);
const moduleAt=(root,path)=>import(pathToFileURL(join(root,'loopx/control_plane',path)).href);
const {pollLocalCoordinationMonitor: current,updateLocalCoordinationTodo: update}=await moduleAt(input.repo,'coordination/local_authority_runtime.ts');
const {pollLocalCoordinationMonitor: baseline,updateLocalCoordinationTodo: baselineUpdate}=await moduleAt(input.baseline_repo,'coordination/local_authority_runtime.ts');
const {FileAuthorityStore}=await moduleAt(input.repo,'coordination/file_authority_store.ts');
const {SqliteAuthorityStore}=await moduleAt(input.repo,'coordination/sqlite_authority_store.ts');
const {PostgreSqlAuthorityStore,installPostgreSqlAuthorityStoreSchema}=await moduleAt(input.repo,'coordination/postgresql_authority_store.ts');
const {PostgreSqlAuthorityService}=await moduleAt(input.repo,'coordination/postgresql_authority_service.ts');
const {selectLocalSqliteAuthority}=await moduleAt(input.repo,'coordination/local_authority_provider.ts');
const {coordinationTodoReadModel}=await moduleAt(input.repo,'coordination/coordination_projection.ts');
const {canonicalAuthoritySha256: digest}=await moduleAt(input.repo,'coordination/authority_store_codec.ts');
const {engageLegacyCoordinationWriterFence}=await moduleAt(input.repo,'coordination/legacy_writer_fence.ts');
const goal=input.goal_id, target='todo_monitor_rehearsal';
const initial=structuredClone(input.projection);
assert(!initial.todos.some(t=>t.todo_id===target));
initial.todos.push({schema_version:'todo_item_v0',todo_id:target,role:'agent',status:'open',done:false,
  text:'Observe isolated public changes',archive_state:'active',source_section:'Agent Todo',
  claimed_by:'agent-a',excluded_agents:[],task_class:'continuous_monitor',target_key:'isolated-watch',
  cadence:'1h',watch_only:'true',next_due_at:'2026-09-01T00:00:00Z'});
initial.todos.sort((a,b)=>a.todo_id<b.todo_id?-1:a.todo_id>b.todo_id?1:0);
initial.todo_read_model=coordinationTodoReadModel(initial.todos,initial.todo_read_model.schema_version);
initial.handoff_mode='legacy';
const proof={idempotency_key:'monitor-rehearsal-execution',expected_version:3};
const lease={schema_version:'task_lease_v0',goal_id:goal,todo_id:target,owner:'agent-a',
  idempotency_key:proof.idempotency_key,version:3,lease_epoch:2,status:'active',write_scopes:[],
  acquired_at:'2026-01-01T00:00:00Z',updated_at:'2026-01-01T00:00:00Z',expires_at:'2099-01-01T00:00:00Z'};
const root=await mkdtemp(join(tmpdir(),'loopx-monitor-rehearsal-'));
const pool=new Pool({connectionString:process.env.LOOPX_TEST_POSTGRES_URL,max:4});
const database={connect:async()=>{const c=await pool.connect();return {query:async(t,v)=>c.query(t,v),release:e=>c.release(e)};}};
const tenant=`monitor-rehearsal-${randomUUID()}`;
const heads={}, compatible={}, report={};
try {
  await installPostgreSqlAuthorityStoreSchema(database,`postgresql:${'b'.repeat(32)}`);
  for (const arm of ['baseline','file','sqlite','postgresql']) {
    const runtime=join(root,arm); await mkdir(runtime,{recursive:true});
    const display=join(runtime,'state.md'); await writeFile(display,'# Disposable display\n');
    let store=new FileAuthorityStore(join(runtime,'authority/file-v0'),goal), dependencies={};
    if(arm==='sqlite') {
      assert.equal((await selectLocalSqliteAuthority(runtime,goal,true)).ok,true);
      store=new SqliteAuthorityStore(join(runtime,'authority/sqlite-v0'),goal);
    }
    if(arm==='postgresql') {
      store=new PostgreSqlAuthorityStore(database,{tenant_id:tenant,goal_id:goal});
      const identity=await store.storeIdentity(); assert.equal(identity.status,'available');
      const selection={schema_version:'loopx_local_authority_provider_v0',provider:'postgresql',goal_id:goal,
        tenant_id:tenant,store_identity:identity.store_identity};
      await mkdir(join(runtime,'authority'),{recursive:true});
      await writeFile(join(runtime,'authority',`provider-${createHash('sha256').update(goal).digest('hex')}.json`),JSON.stringify(selection));
      const service=new PostgreSqlAuthorityService({database,
        authenticatePrincipal:()=>({status:'authenticated',principal:{principal_id:'isolated-rehearsal'}}),
        authorizeTenant:(principal,selected)=>principal==='isolated-rehearsal'&&selected===tenant?{status:'allowed'}:
          {status:'denied',reason_code:'wrong_tenant',reason:'outside disposable tenant'}});
      dependencies={openPostgresqlStore:async selected=>{
        const opened=await service.openStore({credential:null,...selected});
        assert.equal(opened.status,'opened'); return opened.store;
      }};
    }
    const seed=await store.commitAuthority({expected_provider_revision:null,operation_id:'seed',events:[],receipts:[],next_projection:initial});
    assert.equal(seed.status,'applied');
    assert.equal((await engageLegacyCoordinationWriterFence({schema_version:'loopx_legacy_coordination_writer_fence_engage_request_v0',
      runtime_root:runtime,goal_id:goal,state_path:display,fence:{schema_version:'loopx_legacy_coordination_writer_fence_v0',
        state:'engaged',goal_id:goal,fence_id:'rehearsal',source_version:'state:1',source_projection_sha256:digest(initial),
        expected_shadow_provider_revision:seed.provider_revision}})).status,'applied');
    await rm(display);
    const poll=arm==='baseline'?baseline:current;
    const request={schema_version:'loopx_coordination_monitor_poll_request_v0',runtime_root:runtime,goal_id:goal,
      operation_id:'unchanged',actor_agent_id:'agent-a',registered_agents:['agent-a','agent-b'],dry_run:false,
      observation:{todo_id:target,generated_at:'2026-09-01T00:00:00Z',result_hash:'first',material_change:false},intent:{}};
    const firstPoll=await poll(request,dependencies);
    assert.equal(firstPoll.status,'applied',JSON.stringify(firstPoll));
    const first=await store.loadAuthority(); assert.equal(first.status,'loaded'); compatible[arm]=first.head;
    assert.equal((await poll(request,dependencies)).status,'replayed');
    const leased={...first.head,handoff_mode:'hard_lease',leases:[...first.head.leases,lease].sort((a,b)=>a.todo_id<b.todo_id?-1:a.todo_id>b.todo_id?1:0)};
    assert.equal((await store.commitAuthority({operation_id:'seed-execution',expected_provider_revision:first.provider_revision,
      events:[],receipts:[],next_projection:leased})).status,'applied');
    const before=await store.loadAuthority();
    const changed={...request,schema_version:'loopx_coordination_monitor_poll_request_v1',operation_id:'changed',lease_proof:proof,
      observation:{...request.observation,generated_at:'2026-09-01T01:00:00Z',result_hash:'second',material_change:true},
      intent:{next_agent_todo:'Validate isolated change',next_action_kind:'validate'}};
    for(const invalid of [null,{...proof,expected_version:2},{...proof,idempotency_key:'wrong'}]) {
      assert.equal((await poll({...changed,lease_proof:invalid},dependencies)).status,'failed');
      assert.deepEqual(await store.loadAuthority(),before);
    }
    assert.equal((await poll({...changed,dry_run:true},dependencies)).status,'planned');
    assert.deepEqual(await store.loadAuthority(),before);
    const applied=await poll(changed,dependencies); assert.equal(applied.status,'applied');
    const after=await store.loadAuthority(); assert.equal(after.status,'loaded');
    assert.deepEqual(after.head.leases,leased.leases);
    assert.equal(after.head.todos.length,initial.todos.length+1);
    assert.equal(after.head.todos.find(t=>t.todo_id===target).material_change_generation,1);
    assert.deepEqual(after.head.todos.filter(t=>input.projection.todos.some(old=>old.todo_id===t.todo_id)),input.projection.todos);
    assert.equal((await poll(changed,dependencies)).status,'replayed');
    assert.deepEqual(await store.loadAuthority(),after);
    const expired={...after.head,leases:after.head.leases.map(l=>l.todo_id===target?{...l,expires_at:'2020-01-01T00:00:00Z'}:l)};
    await store.commitAuthority({operation_id:'expire-fixture',expected_provider_revision:after.provider_revision,events:[],receipts:[],next_projection:expired});
    const retired=await store.loadAuthority();
    assert.equal((await poll({...changed,operation_id:'expired-new-observation',intent:{},
      observation:{...changed.observation,generated_at:'2026-09-01T02:00:00Z',result_hash:'third',material_change:false}},dependencies)).status,'failed',
      'expired execution cannot commit another observation');
    assert.equal((await poll(changed,dependencies)).status,'replayed');
    assert.deepEqual(await store.loadAuthority(),retired);
    // A completed, lease-free Monitor can resume observation. It cannot reuse
    // a prior execution grant or treat a historical receipt as current state.
    const completedTodos=retired.head.todos.map(t=>t.todo_id===target?{...t,status:'done',done:true,
      completed_at:'2026-09-01T04:00:00Z',no_followup:true,completion_continuation:'no_followup'}:t);
    const completed={...retired.head,todos:completedTodos,
      leases:retired.head.leases.filter(l=>l.todo_id!==target),handoff_mode:'legacy',
      todo_read_model:coordinationTodoReadModel(completedTodos,retired.head.todo_read_model.schema_version)};
    assert.equal((await store.commitAuthority({operation_id:'complete-fixture',expected_provider_revision:retired.provider_revision,
      events:[],receipts:[],next_projection:completed})).status,'applied');
    const registryPath=join(runtime,'registry.json'),registryText=JSON.stringify({registered_agents:['agent-a','agent-b']});
    await writeFile(registryPath,registryText);
    const observation={schema_version:'loopx_local_coordination_todo_update_request_v4',runtime_root:runtime,goal_id:goal,
      todo_id:target,role:'agent',actor_agent_id:'agent-a',registered_agents:['agent-a','agent-b'],lifecycle_grants:[],
      registry_source:{path:registryPath,sha256:createHash('sha256').update(registryText).digest('hex')},
      operation_id:'reactivate',observed_at:'2030-01-01T00:00:00Z',dry_run:false,patch:{},clear_fields:[],
      planning_intent:{status:'open',no_followup:false},monitor_observation:{generated_at:'2030-01-01T00:00:00Z',
        result_hash:'second',material_change:true,monitor_effect_id:'reactivate',cadence:'1h'}};
    const closed=await store.loadAuthority();
    if(arm==='baseline') {
      const unsupported=await baselineUpdate(observation,dependencies);
      assert.equal(unsupported.status,'failed'); assert.match(unsupported.reason,/schema mismatch/);
      assert.deepEqual(await store.loadAuthority(),closed);
      report[arm]={poll_parity:true,observation_update:'unsupported_no_write'}; continue;
    }
    assert.equal((await update({...observation,dry_run:true},dependencies)).status,'planned');
    assert.deepEqual(await store.loadAuthority(),closed);
    assert.equal((await update({...observation,patch:{text:'mixed edit'}},dependencies)).status,'failed');
    assert.deepEqual(await store.loadAuthority(),closed);
    const resumed=await update(observation,dependencies); assert.equal(resumed.status,'applied',JSON.stringify(resumed));
    assert.equal(resumed.monitor_poll_transition.material_change_generation,2);
    const afterResume=await store.loadAuthority(); assert.equal(afterResume.status,'loaded');
    const resumedTodo=afterResume.head.todos.find(t=>t.todo_id===target);
    assert.equal(resumedTodo.status,'open'); assert.equal(resumedTodo.done,false);
    assert(!Object.hasOwn(resumedTodo,'completed_at')); assert(!Object.hasOwn(resumedTodo,'completion_continuation'));
    assert.deepEqual(afterResume.head.leases,completed.leases);
    assert.deepEqual(afterResume.head.todos.filter(t=>input.projection.todos.some(old=>old.todo_id===t.todo_id)),input.projection.todos);
    const closedAgain=afterResume.head.todos.map(t=>t.todo_id===target?{...t,status:'done',done:true,completed_at:'2030-01-01T01:00:00Z'}:t);
    await store.commitAuthority({operation_id:'complete-again',expected_provider_revision:afterResume.provider_revision,
      events:[],receipts:[],next_projection:{...afterResume.head,todos:closedAgain,
        todo_read_model:coordinationTodoReadModel(closedAgain,afterResume.head.todo_read_model.schema_version)}});
    const final=await store.loadAuthority();
    assert.equal((await update(observation,dependencies)).status,'replayed');
    assert.equal((await update({...observation,operation_id:'stale-new-id'},dependencies)).status,'failed');
    assert.deepEqual(await store.loadAuthority(),final);
    heads[arm]=final.head;
    report[arm]={poll_parity:true,observation_update:'applied',same_hash_reactivation_generation:2,
      historical_replay_keeps_completion:true,stale_observation:'rejected',non_target_unchanged:true};
  }
  for(const arm of ['file','sqlite','postgresql']) assert.deepEqual(compatible[arm],compatible.baseline);
  assert.deepEqual(heads.file,heads.sqlite); assert.deepEqual(heads.file,heads.postgresql);
  process.stdout.write(JSON.stringify({schema_version:'authority_monitor_rehearsal_v0',
    source_todos:input.projection.todos.length,source_leases:input.projection.leases.length,
    compatible_head_sha256:digest(compatible.baseline),final_head_sha256:digest(heads.file),
    provider_heads_equal:true,arms:report}));
} finally {
  await pool.query('DELETE FROM loopx_control_plane.authority_heads WHERE tenant_id=$1',[tenant]);
  await pool.end(); await rm(root,{recursive:true,force:true});
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--baseline-repo", type=Path, required=True)
    parser.add_argument("--execute-isolated-postgresql", action="store_true")
    parser.add_argument("--private-diagnostics", type=Path)
    args = parser.parse_args()
    if not args.execute_isolated_postgresql or not os.environ.get("LOOPX_TEST_POSTGRES_URL"):
        raise SystemExit("an explicitly isolated PostgreSQL server is required")
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
        raise SystemExit(f"isolated Monitor rehearsal failed (exit {child.returncode}); no qualification; diagnostics stay private")
    after_projection, after_snapshot = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime, state_path=state, registry_path=registry_path)
    if projection != after_projection or snapshot != after_snapshot or registry_path.read_bytes() != registry_bytes:
        raise SystemExit("live source changed during rehearsal; rerun from a stable snapshot")
    result = json.loads(child.stdout)
    result.update(baseline_revision=revision, source_snapshot_sha256=source_digest, source_unchanged=True)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
