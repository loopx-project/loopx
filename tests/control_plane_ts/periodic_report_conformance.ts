import assert from 'node:assert/strict';
import test from 'node:test';
import {canonicalReadRoot} from './local_promotion_fixture.ts';
import type {AuthorityStoreConformanceFactory} from './authority_store_conformance.ts';
import {productionScaleCoordinationFixture} from './production_scale_coordination_fixture.ts';
import {coordinationTodoReadModel} from '../../loopx/control_plane/coordination/coordination_projection.ts';
import {listLocalCoordinationTodos} from '../../loopx/control_plane/coordination/local_authority_read.ts';
import {LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA} from '../../loopx/control_plane/coordination/coordination_state_contract.generated.ts';
import {selectPeriodicReportProgress,selectPeriodicReportApprovalRetry} from '../../loopx/control_plane/capabilities/periodic_report_progress.ts';
import type {JsonObject} from '../../loopx/control_plane/effect_program.ts';

export function registerPeriodicReportConformance(name: string, factory: AuthorityStoreConformanceFactory): void {
  for (const shape of ['native','legacy'] as const) test(`${name}: periodic report full source (${shape})`, async context => {
    const {store} = await factory(context);
    const goal = 'report-source', fixture = productionScaleCoordinationFixture(goal,shape);
    const todos = fixture.projection.todos as JsonObject[];
    const own = todos.find(row => row.role === 'agent' && row.status === 'done')!;
    const peer = todos.find(row => row.role === 'agent' && row.status === 'done' && row !== own)!;
    Object.assign(own,{claimed_by:'reporter',updated_at:'2026-09-21T18:00:00+08:00',completed_at:'2026-09-21T18:00:00+08:00'});
    Object.assign(peer,{claimed_by:'peer',updated_at:'2026-09-21T11:00:00.000001Z',completed_at:'2026-09-21T11:00:00.000001Z'});
    const decision = todos.find(row => row.role === 'user' && row.status === 'done')!;
    Object.assign(decision,{action_kind:'approve_periodic_report_payload',decision_outcome:'reject',
      bound_agent:'reporter',decision_scope:{kind:'other',granularity:'action',scope_key:'report-1'},
      updated_at:'2026-09-21T11:00:00Z',archive_state:'archive',
      ...(shape === 'legacy' ? {source_section:'Completed Work Archive'} : {})});
    fixture.projection.todo_read_model = coordinationTodoReadModel(todos,(fixture.projection.todo_read_model as JsonObject).schema_version);
    const seeded = await store.commitAuthority({operation_id:'report-source',expected_provider_revision:null,
      next_projection:fixture.projection,events:[],receipts:[]});
    assert.equal(seeded.status,'applied');
    const before = await store.loadAuthority();
    const source = await listLocalCoordinationTodos({schema_version:LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA,
      goal_id:goal,runtime_root:await canonicalReadRoot(context,goal)}, {createStore:()=>store});
    assert.equal(source.status,'loaded');
    const records = source.todos as JsonObject[];
    assert.equal(records.length,fixture.expected_initial_todo_count);
    const agents = records.filter(row => row.role === 'agent' && row.archive_state === 'active');
    const selected = selectPeriodicReportProgress({schema_version:'periodic_report_progress_selection_request_v0',
      agent_id:'reporter',completed_at:'2026-09-21T12:00:00Z',items:agents.map(row=>({...row,actionable:false}))});
    const outcomeIds = (selected.outcomes as JsonObject[]).map(item=>agents[Number(item.index)].todo_id);
    assert.equal(outcomeIds[0],own.todo_id);
    assert.ok(outcomeIds.includes(peer.todo_id));
    const retry = selectPeriodicReportApprovalRetry({schema_version:'periodic_report_approval_retry_request_v0',
      agent_id:'reporter',approval_scope:'other:action:report-1',items:records.filter(row=>row.role==='user')});
    assert.match(String(retry.revision),/^[a-f0-9]{16}$/);
    assert.deepEqual(await store.loadAuthority(),before,'report reads never mutate provider state');
  });
}
