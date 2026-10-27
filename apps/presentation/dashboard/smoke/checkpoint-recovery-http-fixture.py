"""Isolated production checkpoint owner with a deterministic SDK transport.

Invoked from the repository root by the packaged browser smoke. No paid model,
real Lark, active operator Goal or user browser profile is used.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

ROOT = Path.cwd()
sys.path[:0] = [str(ROOT), str(ROOT/'tests'), str(ROOT/'tests/control_plane')]
import pytest
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.todos import add_goal_todo
from tests.control_plane import test_default_checkpoint_continuation as native
from tests import test_goal_mode_mcp_settlement as mcp


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def main():
    out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=False)
    isolated=out/'native';isolated.mkdir()
    patches=pytest.MonkeyPatch()
    goal_id,agent_id='product-release','lead'
    for module in (native,mcp):
        patches.setattr(module,'GOAL_ID',goal_id)
        patches.setattr(module,'AGENT_ID',agent_id)
    registry,state,todo=native._fixture('file',isolated,patches)
    payload=json.loads(registry.read_text());goal=payload['goals'][0]
    goal['domain']='Checkpoint recovery demonstration'
    goal['coordination']['registered_agents']=[agent_id,'reviewer','codex']
    goal['spawn_policy']={'mode':'multi_subagent','allowed':True,'max_children':2,
                          'execution_config':'.loopx/config/delegations.json'}
    registry.write_text(json.dumps(payload),encoding='utf-8')
    project=state.parent
    config=project/'.loopx/config/delegations.json';config.parent.mkdir(parents=True,exist_ok=True)
    config.write_text(json.dumps({'schema_version':'loopx_local_delegation_v0','bindings':[{
        'id':'review','agent_id':'reviewer','todo_id':'review-task','requesters':[agent_id],
        'workspace':str(project),'host_args':['--host','dsh'],'timeout_seconds':60,
        'output_refs':['output.json']}]}),encoding='utf-8')
    control=mcp._control(registry)
    original_guard=json.loads(control.should_run())
    first=json.loads(control.complete_task(todo,agent_id,'Candidate verified.',no_follow_up=True))
    assert first['stage']=='direction_pending' and first['completed'] is True
    original=first['settlement_identity']
    runtime_root=isolated/'runtime'
    index=runtime_root/'goals'/goal_id/'runs/index.jsonl'
    store=ChatSessionStore(runtime_root)
    sid='session-goal-product-release-codex'
    store.create_session(goal_id=goal_id,agent_id='codex',channel_id='goal.product-release',
                         upstream_thread_id='synthetic-original-sdk',session_id=sid,
                         adapter_kind='codex_app_server',upstream_mode='chat')
    settings={'agent_id':agent_id,'token_budget':10000,'execution_config_ref':'.loopx/config/delegations.json',
              'config_digest':hashlib.sha256(config.read_bytes()).hexdigest()}
    store.update_session(sid,loopx_tools=True,native_goal={'status':'paused','tokensUsed':0},
                         loopx_mode={'enabled':True,'paused':True,'settings':settings})
    patches.setenv('LOOPX_CHAT_CODEX_HOME',str(isolated/'codex-home'))
    controller=ChatRuntimeController(store=store,codex_bin='missing-codex',registry_path=registry)
    adapter=SimpleNamespace(goal_driver=SimpleNamespace(stopped=threading.Event()),
                            session=SimpleNamespace(),upstream_thread_id='synthetic-original-sdk',
                            close_session=lambda:None,healthcheck=lambda:True)
    patches.setattr(controller,'_ensure_adapter_locked',lambda *a,**k:adapter)
    controller.adapters[sid]=adapter
    dispatches=[]; failures=[]; decisions=[]

    def native_snapshot():
        items=read_canonical_todos_if_promoted(runtime_root=runtime_root,goal_id=goal_id)['todos']
        row=next(item for item in items if item['todo_id']==todo)
        facts={key:row.get(key) for key in ('todo_id','status','evidence','completed_at','completion_turn_key')}
        events=[json.loads(line) for line in index.read_text(encoding='utf-8').splitlines()] if index.exists() else []
        return {'facts':facts,'result_sha256':digest(facts),
                'counts':{'result':int(row['status']=='done' and row['evidence']=='Candidate verified.'),
                          'debit':sum(e.get('classification')=='quota_slot_spent' for e in events)},
                'session_id':store.load_session(sid)['session_id'],
                'upstream_thread_id':store.load_session(sid)['upstream_thread_id'],
                'native_events':events,'dispatches':list(dispatches),'decisions':list(decisions)}

    def provider(**kwargs):
        dispatches.append({'session_id':kwargs['session_id'],'turn_id':kwargs['turn_id'],
                           'upstream_thread_id':adapter.upstream_thread_id,'phase':'checkpoint_direction'})
        lock=None
        try:
            assert kwargs['session_id']==sid and kwargs['loopx_execution'] is True
            lock=controller.loopx_mode.prepare(sid,kwargs['turn_id'],adapter,lambda *a:{},lambda *a:None)
            read=adapter.session.read_tool_handler('loopx_collaboration',{
                'action':'read','operation_id':todo,'brief':{'purpose':'first_delivery'}})
            assert read['ok'] and read['settlement_identity']==original,read
            decision=adapter.session.read_tool_handler('loopx_collaboration',{
                'action':'resume','operation_id':todo,'brief':{'purpose':'first_delivery',
                    'read_context_id':read['read_context_id'],
                    'vision_unchanged_reason':'The retained candidate and freshly observed remaining obligations preserve the route.'}})
            assert decision['ok'] and decision['settlement_complete'] and decision['settlement_identity']==original,decision
            decisions.append({'read':read,'settlement':decision})
            store.update_turn(sid,kwargs['turn_id'],status='completed',response={'text':'Original checkpoint settled.'})
            store.release_active_turn(sid,kwargs['turn_id'],last_activity_at='2026-10-19T00:00:00Z',last_error_code=None)
            store.update_session(sid,loopx_mode={'enabled':True,'paused':True,'settings':settings})
        except Exception as exc:
            failures.append(repr(exc));raise
        finally:
            if lock:lock.__exit__(None,None,None)

    patches.setattr(controller,'_run_started_turn',provider)
    server=ChatHTTPServer(('127.0.0.1',0),ChatRequestHandler)
    server.verbose=False;server.registry_path=registry;server.runtime_root=runtime_root
    server.chat_store=store;server.runtime_controller=controller
    http=[]
    send=ChatRequestHandler._send_json
    def observed_send(handler,body,*,status=200):
        if handler.path.startswith('/api/chat/sessions/') and handler.path.endswith('/loopx'):
            http.append({'method':handler.command,'path':handler.path,'status':status,'response':body})
        return send(handler,body,status=status)
    patches.setattr(ChatRequestHandler,'_send_json',observed_send)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    baseline=native_snapshot(); assert baseline['counts']=={'result':1,'debit':0}
    (out/'before.json').write_text(json.dumps(baseline,indent=2),encoding='utf-8')
    (out/'first-result.json').write_text(json.dumps(first,indent=2),encoding='utf-8')
    print(json.dumps({'origin':f'http://127.0.0.1:{server.server_port}','session':store.load_session(sid),
                      'original_identity':original}),flush=True)
    try:
        for line in sys.stdin:
            command=line.strip()
            if command=='inspect':
                value=native_snapshot();value['failures']=list(failures)
            elif command=='replay':
                assert len(decisions)==1 and not failures
                from loopx.chat_coordinator_checkpoint import CoordinatorCheckpoints
                checkpoint=CoordinatorCheckpoints(registry,runtime_root,goal_id,agent_id)
                # Replay the exact successful decision, without another SDK dispatch.
                decision=decisions[0]
                repeated=checkpoint.resume(todo,{'read_context_id':decision['read']['read_context_id'],
                    'vision_unchanged_reason':'The retained candidate and freshly observed remaining obligations preserve the route.'})
                assert repeated['ok'] and repeated['settlement_identity']==original,repeated
                value={**native_snapshot(),'replayed_identity':repeated['settlement_identity'],'replay':repeated}
            elif command=='save':
                assert not failures,failures
                value={**native_snapshot(),'http':http,'original_identity':original,
                       'settled_identity':decisions[0]['settlement']['settlement_identity']}
            else:raise ValueError('Unknown fixture command')
            (out/(command+'.json')).write_text(json.dumps(value,indent=2),encoding='utf-8')
            print(json.dumps(value),flush=True)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
        controller.close();patches.undo()


if __name__=='__main__':main()
