"""Independent native and HTTP consumers of the shared changed writer boundary."""
from __future__ import annotations

import json
import sys

import pytest

from test_shadow_observable_e2e import Caller, SOURCE, caller as caller

pytestmark = pytest.mark.stage2c_e2e

NATIVE = r"""
import {join} from 'node:path';
const input = JSON.parse(process.argv[1]);
const {FileAuthorityStore} = await import(input.base + '/file_authority_store.ts');
const store = new FileAuthorityStore(join(input.root, 'authority', 'file-v0'), 'observable');
let result;
if (input.action === 'seed') {
  result = await store.commitAuthority({expected_provider_revision:null, operation_id:'caller-fixture',
    events:[], next_projection:input.request, receipts:[]});
  const {engageLegacyCoordinationWriterFence} = await import(input.base + '/legacy_writer_fence.ts');
  const {canonicalAuthoritySha256} = await import(input.base + '/authority_store_codec.ts');
  const fence = await engageLegacyCoordinationWriterFence({schema_version:'loopx_legacy_coordination_writer_fence_engage_request_v0',
    runtime_root:input.root,goal_id:'observable',state_path:input.state,
    fence:{schema_version:'loopx_legacy_coordination_writer_fence_v0',state:'engaged',goal_id:'observable',
      fence_id:'caller-fixture',source_version:'caller-fixture',source_projection_sha256:canonicalAuthoritySha256(input.request),
      expected_shadow_provider_revision:result.provider_revision}});
  if(fence.status !== 'applied') throw new Error(JSON.stringify(fence));
} else if (input.action === 'read') {
  result = {head:await store.loadAuthority(), history:await store.scanCommitted(null, 100)};
} else {
  const owner = await import(input.base + '/local_authority_runtime.ts');
  result = await owner[input.action](input.request);
}
process.stdout.write(JSON.stringify(result) + '\n');
"""


def native(w: Caller, action: str, request: dict) -> dict:
    value = {'base': (SOURCE / 'loopx/control_plane/coordination').as_uri(),
        'root': str(w.root), 'state': str(w.state), 'action': action, 'request': request}
    return w.invoke(['node', '--no-warnings', '--experimental-strip-types', '--input-type=module',
                     '-e', NATIVE, json.dumps(value)], ['native', action, json.dumps(request)])


def test_canonical_argument_intent_and_atomic_claim(caller: Caller) -> None:
    w = caller
    todo = w.add('Native canonical input', '--note', 'Preserve operator note', '--required-write-scope', 'src/**')
    record = w.read(todo)
    builder = "from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection as build; import json,sys; value=build(goal_id='observable', todos=[json.loads(sys.argv[1])]); value['handoff_mode']='hard_lease'; print(json.dumps(value))"
    projection = w.invoke([sys.executable, '-c', builder, json.dumps(record)], ['fixture-projection', json.dumps(record)])
    assert native(w, 'seed', projection)['status'] == 'applied'
    w.state.unlink()
    before = native(w, 'read', {})
    claim = ('todo', 'claim', '--todo-id', todo, '--claimed-by', 'agent-a', '--agent-id', 'agent-a',
        '--claim-operation-id', 'caller-atomic-claim', '--task-lease-idempotency-key', 'caller-ownership',
        '--task-lease-expected-version', '0')
    assert w.call(*claim, '--dry-run')['status'] == 'planned'
    assert native(w, 'read', {}) == before
    applied = w.call(*claim)
    assert applied['status'] == 'applied', applied
    assert applied['projection_delivery'] == 'delivered', applied
    recovered = w.state.read_bytes()
    assert b'claimed_by=agent-a' in recovered
    replay = w.call(*claim)
    assert replay['status'] == 'replayed' and replay['original_receipt'] == applied['original_receipt']
    stored = native(w, 'read', {})['head']['head']
    assert stored['todos'][0]['claimed_by'] == 'agent-a'
    assert stored['leases'][0]['owner'] == 'agent-a' and stored['leases'][0]['write_scopes'] == ['src/**']
    before = native(w, 'read', {})
    rejected = w.call('todo', 'update', '--todo-id', todo, '--agent-id', 'agent-b', '--note', 'Foreign owner edit')
    assert rejected.get('error_code') == 'update_owner_mismatch', rejected
    assert rejected['error'] == rejected['reason'] == "Todo update cannot edit another claim owner's work"
    assert native(w, 'read', {}) == before
    assert w.state.read_bytes() == recovered


def test_native_unclaimed_edit_and_explicit_note_clear(caller: Caller) -> None:
    w = caller
    todo = w.add('Unclaimed correction', '--note', 'Keep until explicitly cleared')
    record = w.read(todo)
    builder = "from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection as build; import json,sys; value=build(goal_id='observable', todos=[json.loads(sys.argv[1])]); value['handoff_mode']='soft_claim'; print(json.dumps(value))"
    projection = w.invoke([sys.executable, '-c', builder, json.dumps(record)], ['fixture-projection', json.dumps(record)])
    assert native(w, 'seed', projection)['status'] == 'applied'
    w.state.unlink()
    updated = w.call('todo', 'update', '--todo-id', todo, '--agent-id', 'agent-b', '--note', 'Claim-neutral context')
    assert updated['ok'] is True and updated['projection_delivery'] == 'delivered', updated
    recovered = w.state.read_bytes()
    assert todo.encode() in recovered
    stored = native(w, 'read', {})['head']['head']
    assert stored['todos'][0]['note'] == 'Claim-neutral context'
    assert not stored['todos'][0].get('claimed_by') and stored['leases'] == []
    request = {'schema_version': 'loopx_local_coordination_todo_update_request_v0',
        'runtime_root': str(w.root), 'goal_id': 'observable', 'todo_id': todo, 'role': 'agent',
        'actor_agent_id': 'agent-b', 'registered_agents': ['agent-a', 'agent-b'],
        'operation_id': 'caller-native-clear', 'patch': {}, 'clear_fields': ['note'],
        'dry_run': False, 'observed_at': '2026-09-07T00:00:00Z'}
    cleared = native(w, 'updateLocalCoordinationTodo', request)
    assert cleared['status'] == 'applied', cleared
    after = native(w, 'read', {})
    assert 'note' not in after['head']['head']['todos'][0]
    assert after['head']['head']['leases'] == stored['leases']
    rejected = native(w, 'updateLocalCoordinationTodo', {**request, 'operation_id': 'rejected',
        'todo_id': 'todo_missing', 'actor_agent_id': 'unknown', 'patch': {'text': 'Must not persist'}, 'clear_fields': []})
    assert rejected['status'] in {'failed', 'rejected'}, rejected
    assert native(w, 'read', {}) == after
    # Native transactions do not own display delivery; only the CLI facade
    # regenerated it. Neither direct-native update nor rejection rewrites it.
    assert w.state.read_bytes() == recovered


HTTP = r"""
import http.client, json, pathlib, sys, threading
from loopx.status_server import StatusHTTPServer, StatusRequestHandler
args=json.loads(sys.argv[1]); server=StatusHTTPServer(('127.0.0.1',0),StatusRequestHandler)
server.verbose=False; server.registry_path=pathlib.Path(args['registry']); server.runtime_root_override=args['root']
server.reward_write_enabled=True; server.reward_dry_run_path='/reward/dry-run'; server.reward_append_path='/reward/append'
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
rows=[]
def post(path,body):
 conn=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=10)
 conn.request('POST',path,json.dumps(body),{'Content-Type':'application/json','Origin':'http://localhost'})
 response=conn.getresponse(); payload=json.loads(response.read()); conn.close()
 rows.append({'request':body,'status':response.status,'body':payload}); return payload
try:
 body={'goal_id':'observable','run_generated_at':'2026-09-01T00:00:00Z',
       'recorded_at':'2026-09-01T01:00:00Z','decision':'continue','reward':'positive',
       'reason_summary':'HTTP argument readback','write_active_state_summary':True}
 post('/reward/append',body)
 preview=post('/reward/dry-run',{k:v for k,v in body.items() if k!='write_active_state_summary'})
 if 'preview_id' in preview:
  post('/reward/append',{**body,'preview_id':preview['preview_id']})
finally:
 server.shutdown(); server.server_close(); thread.join(timeout=5)
print(json.dumps({'rows':rows}))
"""


def test_http_reward_preserves_preview_gate_and_persisted_arguments(caller: Caller) -> None:
    w = caller
    todo = w.add('HTTP must preserve Todo ownership', '--claimed-by', 'agent-a')
    record = w.read(todo)
    index = w.root / 'goals' / 'observable' / 'runs' / 'index.jsonl'
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps({'generated_at': '2026-09-01T00:00:00Z', 'json_path': 'run.json',
        'markdown_path': 'run.md', 'classification': 'continue'}) + '\n')
    result = w.invoke([sys.executable, '-c', HTTP, json.dumps({'registry': str(w.registry), 'root': str(w.root)})],
        ['HTTP', 'reward preview then append'])
    first, preview, appended = result['rows']
    assert first['status'] == 400 and first['body']['error'] == 'preview_id is required'
    assert preview['status'] == 200 and preview['body']['dry_run'] is True
    assert appended['status'] == 200 and appended['body']['appended'] is True
    assert len(index.read_text().splitlines()) == 2
    assert 'HTTP argument readback' in index.read_text() and 'HTTP argument readback' in w.state.read_text()
    assert w.read(todo) == record


def test_event_writer_retains_primary_semantics_and_cannot_qualify(caller: Caller) -> None:
    w = caller
    seed = """
import json,sys
from pathlib import Path
from loopx.event_sourced_state import AppendOnlyStateEventStore, TODO_ADDED, make_state_event
store=AppendOnlyStateEventStore(Path(sys.argv[1]))
store.append(make_state_event(event_id='evt-caller-fixture', goal_id='observable',event_type=TODO_ADDED,
 refs={'todo_id':'todo_event_fixture'},payload={'role':'agent','title':'Event-owned task',
 'task_class':'advancement_task','claimed_by':'agent-a'},recorded_at='2026-09-01T00:00:00Z'))
print(json.dumps({'events':len(store.load())}))
"""
    log = w.path / 'events.jsonl'
    assert w.invoke([sys.executable, '-c', seed, str(log)], ['event-source', 'seed'])['events'] == 1
    before = w.state.read_bytes()
    completed = w.call('todo', 'complete', '--todo-id', 'todo_event_fixture', '--agent-id', 'agent-a',
        '--evidence', 'validation://event-caller', '--no-follow-up')
    assert completed['ok'] is True, completed
    assert w.state.read_bytes() == before
    assert w.read('todo_event_fixture')['done'] is True
    assert len(log.read_text().splitlines()) > 1
    if w.mode == 'enabled':
        result = w.call('coordination-shadow', 'qualify')
        assert result['ok'] is False and result.get('error') == 'event_log_writer_not_bound', result
