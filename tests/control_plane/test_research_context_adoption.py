"""Actual bootstrap/SQLite, artifact, session and leased-continuation owners.

This stage adopts Todo context, not a native/whole research-state continuation.
No model call or historical lease/effect is activated by these fixtures.
"""
import hashlib
import json
import time
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.hosts.research_checkpoint import canonical_context, research_context, verify_context_sources, adopt_todo_context, _encoded
from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.turn_driver.codex_sessions import _store_codex_cli_session


@pytest.fixture(autouse=True)
def no_benchmark_dependency(monkeypatch):
    """Ordinary Goal continuation must work with benchmark unavailable."""
    import builtins
    original = builtins.__import__
    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        package = (globals or {}).get("__package__", "")
        if name == "benchmark" or name.startswith("benchmark.") or (level and package.startswith("benchmark.")):
            raise ImportError("benchmark is unavailable to this ordinary Goal")
        return original(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, "__import__", guarded)


@pytest.mark.parametrize("lost_response,with_knowledge", [
    (lost, history) for history in (False, True)
    for lost in (None, "prepare", "adopt", "projection", "knowledge") if history or lost != "knowledge"
])
def test_retained_context_is_adopted_only_with_fresh_current_ownership(tmp_path, monkeypatch, capsys, lost_response, with_knowledge):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    workspace = tmp_path / 'branch'
    workspace.mkdir()
    def setup(name, agent):
        registry, root = tmp_path/name/'registry.json', tmp_path/name/'runtime'
        prefix = ['--format','json','--registry',str(registry),'--runtime-root',str(root)]
        def run(*args):
            code = main([*prefix,*args])
            output = capsys.readouterr().out
            assert code == 0, output
            return json.loads(output)
        run('bootstrap','--project',str(workspace),'--goal-id',name,'--objective','Investigate synthetic candidates.',
            '--adapter-kind','read_only_project_map_v0','--adapter-status','connected-read-only','--write-scope','**','--no-global-sync')
        run('configure-goal','--goal-id',name,'--registered-agent',agent,'--agent-work-mode',agent+'=active',
            '--explore-mode','planning' if with_knowledge else 'off','--execute')
        todo = run('todo','add','--goal-id',name,'--role','agent','--claimed-by',agent,
            '--task-class','advancement_task','--required-capability','shell','--text','Investigate the retained synthetic candidates.')['todo_id']
        return registry,root,todo,run
    source_registry,source_root,source_todo,source_run = setup('source','source-actor')
    destination_registry,destination_root,destination_todo,run = setup('branch','branch-actor')
    source_env={'LOOPX_RUNTIME_ROOT':str(source_root),'LOOPX_GOAL_ID':'source'}
    source_revision=source_run('todo','list','--goal-id','source','--todo-id',source_todo)['authority_read']['provider_revision']
    source_run('task-lease','acquire','--goal-id','source','--todo-id',source_todo,'--owner','source-actor','--idempotency-key','source-lease')
    # The evidence includes a rejected approach, with its original status/owner.
    source_current=source_run('todo','list','--goal-id','source','--todo-id',source_todo)['authority_read']['provider_revision']
    source_run('todo','update','--goal-id','source','--todo-id',source_todo,'--agent-id','source-actor',
        '--note','Candidate 3 failed the synthetic check; preserve candidate 7.',
        '--update-operation-id','source-evidence','--update-expected-provider-revision',source_current,
        '--task-lease-idempotency-key','source-lease','--task-lease-expected-version','1')
    if with_knowledge:
        from loopx.capabilities.explore.result_log import (
            append_explore_result_events, build_explore_node_event, build_explore_finding_event, explore_result_log_path,
        )
        source_events = [build_explore_node_event(goal_id='source', node_id='candidate-3',
                title='Rejected candidate', status='dead_end'),
            build_explore_finding_event(goal_id='source', node_id='candidate-3', finding_id='negative-3',
                title='Candidate 3 failed', status='refuted', tags=['writeback-result'],
                summary='The captured test rejected candidate 3; candidate 7 remains untested.', evidence_refs=['validation:candidate-3'])]
        append_explore_result_events(explore_result_log_path(source_root, 'source'), source_events, expected_goal_id='source')
    source_context=(research_context if with_knowledge else canonical_context)(source_env,deadline=time.time()+30)
    assert source_context['snapshot']['provider_revision'] != source_revision
    assert 'leases' not in source_context['snapshot']
    raw=_encoded(source_context)
    export=tmp_path/'context.json'
    export.write_bytes(raw)
    unit=tmp_path/'point'
    components=[{'id':'research_context','source_path':str(export)}]
    probe=effect_runtime_result('runtime.checkpoint_artifacts',{'action':'probe','components':components})
    captured=effect_runtime_result('runtime.checkpoint_artifacts',{'action':'capture','destination':str(unit),
        'components':components,'expected_source_sha256':probe['source_sha256'],
        'point_metadata':{'native_session':{'session_id':'source-session'},
            'loopx_state':{'goal_id':'source','agent_id':'source-actor'},
            'research_context':{'component_id':'research_context','member_path':'','sha256':hashlib.sha256(raw).hexdigest(),
                'provider_revision':source_context['snapshot']['provider_revision'],'store_identity':source_context['snapshot']['store_identity']}}})
    digest=captured['sha256']
    env={'LOOPX_REGISTRY':str(destination_registry),'LOOPX_RUNTIME_ROOT':str(destination_root),
        'LOOPX_PROJECT':str(workspace),'LOOPX_GOAL_ID':'branch','LOOPX_AGENT_ID':'branch-actor'}
    _store_codex_cli_session(destination_root,lineage={'goal_id':'branch','agent_id':'branch-actor'},
        session_scope='agent',session_id='branch-session',session_profile_digest='synthetic-profile')
    # This owner-focused fixture substitutes only native history verification.
    # The real Host + CLI + canonical context journey lives in test_native_checkpoint.
    monkeypatch.setattr('loopx.hosts.research_checkpoint._context_session', lambda *_: 'branch-session')
    before=run('todo','list','--goal-id','branch','--todo-id',destination_todo)
    args=dict(source_todo_id=source_todo,todo_id=destination_todo,operation_id='context-adoption',
        expected_revision=before['authority_read']['provider_revision'],lease_key='branch-lease',lease_version=1)
    with pytest.raises(ValueError,match='execution authority changed'):
        adopt_todo_context(env,unit,digest,**args)
    assert not (workspace/'.local/checkpoint-context').exists()
    lease=run('task-lease','acquire','--goal-id','branch','--todo-id',destination_todo,
        '--owner','branch-actor','--idempotency-key','branch-lease')
    assert lease['lease']['owner']=='branch-actor' and lease['lease']['version']==1
    current=run('todo','list','--goal-id','branch','--todo-id',destination_todo)
    args['expected_revision']=current['authority_read']['provider_revision']
    with pytest.raises(ValueError,match='absent'):
        adopt_todo_context(env,unit,digest,**{**args,'source_todo_id':'missing'})
    with pytest.raises(ValueError,match='execution authority changed'):
        adopt_todo_context(env,unit,digest,**{**args,'expected_revision':'stale-revision'})
    outside=tmp_path/'outside'
    outside.mkdir()
    (workspace/'.local').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):
        adopt_todo_context(env,unit,digest,**args)
    assert list(outside.iterdir())==[]
    (workspace/'.local').unlink()
    # A complete slow read may finish after the caller's deadline. Neither
    # initial verification nor the last pre-prepare verification may authorize
    # a continuation write then; immutable staging remains safe to replay.
    if lost_response is None and with_knowledge:
        from types import SimpleNamespace
        from loopx.hosts import research_checkpoint
        original_rpc = research_checkpoint.effect_runtime_result
        original_handoff = research_checkpoint._handoff
        for expire_at in (1, 2):
            clock, reads, writes = [100.0], [], []
            def slow_read(method, payload, **kwargs):
                if method == "runtime.checkpoint_artifacts" and payload["action"] == "verify":
                    reads.append(kwargs["timeout"])
                    assert kwargs["timeout"] == 40
                    result = original_rpc(method, payload, **kwargs)
                    if len(reads) == expire_at:
                        clock[0] = 141.0
                    return result
                return original_rpc(method, payload, **kwargs)
            def track_handoff(env, payload):
                if payload["action"] != "inspect":
                    writes.append(payload["action"])
                return original_handoff(env, payload)
            with monkeypatch.context() as bounded:
                bounded.setattr(research_checkpoint, "time", SimpleNamespace(time=lambda: clock[0]))
                bounded.setattr(research_checkpoint, "effect_runtime_result", slow_read)
                bounded.setattr(research_checkpoint, "_handoff", track_handoff)
                with pytest.raises(TimeoutError, match="original context adoption deadline"):
                    adopt_todo_context(env, unit, digest, **args, deadline=140)
            assert len(reads) == expire_at and not writes
        assert run('todo','list','--goal-id','branch','--todo-id',destination_todo)['authority_read']['provider_revision'] == args['expected_revision']
    source_run('todo','update','--goal-id','source','--todo-id',source_todo,'--agent-id','source-actor',
        '--note','Candidate 999 is later evidence excluded from the captured point.',
        '--update-operation-id','later-source-evidence','--update-expected-provider-revision',source_context['snapshot']['provider_revision'],
        '--task-lease-idempotency-key','source-lease','--task-lease-expected-version','1')
    source_before=source_run('todo','list','--goal-id','source','--todo-id',source_todo)
    if lost_response:
        with monkeypatch.context() as interrupted:
            if lost_response == "projection":
                def lost_projection(*args, **kwargs):
                    raise OSError("synthetic projection response lost")
                interrupted.setattr("loopx.control_plane.todos.provider_projection.project_current_canonical_todos", lost_projection)
            elif lost_response == "knowledge":
                from loopx.capabilities.explore import result_log
                original_append = result_log.append_explore_result_events
                def lost_append(*args, **kwargs):
                    original_append(*args, **kwargs)
                    raise OSError("synthetic committed knowledge response lost")
                interrupted.setattr(result_log, "append_explore_result_events", lost_append)
            else:
                from loopx.hosts import research_checkpoint
                original_handoff = research_checkpoint._handoff
                def lost_handoff(env, payload):
                    result = original_handoff(env, payload)
                    if payload["action"] == lost_response:
                        raise OSError("synthetic committed response lost")
                    return result
                interrupted.setattr(research_checkpoint, "_handoff", lost_handoff)
            expected_error = ValueError if lost_response == "projection" else OSError
            expected_message = "original projection receipt" if lost_response == "projection" else "response lost"
            with pytest.raises(expected_error, match=expected_message):
                adopt_todo_context(env,unit,digest,**args)
        interrupted_todo=run('todo','list','--goal-id','branch','--todo-id',destination_todo)
        assert json.loads(interrupted_todo['todo']['note'])['source_session']=='source-session'
        # A replacement identity cannot recover the original accepted operation.
        with pytest.raises(ValueError, match='continuation rejected'):
            adopt_todo_context(env,unit,digest,**{**args,'operation_id':'replacement-operation'})
        assert run('todo','list','--goal-id','branch','--todo-id',destination_todo)['todo']==interrupted_todo['todo']
    if with_knowledge and lost_response is None:
        # Exercise the shipped ordinary CLI, with only native observation
        # substituted above; the artifact/SQLite/claim/lease owners are real.
        for key, value in {"CODEX_BIN": "synthetic-native", "CODEX_HOME": str(tmp_path / "home"),
                           "MODEL_NAME": "fixture", "REASONING_EFFORT": "high"}.items():
            monkeypatch.setenv(key, value)
        result = run('research-checkpoint', 'adopt-context', '--goal-id', 'branch', '--agent-id', 'branch-actor',
            '--project', str(workspace), '--sandbox', 'read-only', '--root', str(unit), '--expected-sha256', digest,
            '--source-todo-id', source_todo, '--todo-id', destination_todo, '--operation-id', args['operation_id'],
            '--expected-revision', args['expected_revision'], '--lease-key', args['lease_key'], '--lease-version', '1', '--execute')
    else:
        result=adopt_todo_context(env,unit,digest,**args)
    assert result['context_adopted'] and result['model_calls']==0 and not result['research_point_qualified']
    if with_knowledge:
        log = explore_result_log_path(destination_root, 'branch')
        imported_bytes = log.read_bytes()
        assert result['explore_knowledge']['requested_event_count'] == 2
        delivered = run('explore', 'turn-context', '--goal-id', 'branch', '--agent-id', 'branch-actor')
        finding = delivered['graph']['writeback_results'][0]
        assert finding['finding_id'] == 'negative-3' and finding['status'] == 'refuted'
        assert finding['summary'] == source_events[-1]['summary']
        assert 'checkpoint:' + digest in imported_bytes.decode()
    else:
        assert 'explore_knowledge' not in result
        assert not (destination_root/'goals/branch/explore-result-log.jsonl').exists()
    assert Path(result['artifact']).read_bytes()==raw
    after=run('todo','list','--goal-id','branch','--todo-id',destination_todo)
    assert after['todo']['claimed_by']=='branch-actor' and after['todo']['status']=='open'
    for key,value in current['todo'].items():
        if key not in {'note','updated_at','last_actor_agent_id'}:
            assert after['todo'].get(key)==value
    note=json.loads(after['todo']['note'])
    assert note['source_session']=='source-session' and 'checkpoint:'+digest in note['source_refs']
    inspected=run('handoff','inspect','--goal-id','branch','--todo-id',destination_todo,'--agent-id','branch-actor',
        '--session-id','branch-session','--workspace',str(workspace),'--artifact',str(Path(result['artifact']).relative_to(workspace)),
        '--task-lease-idempotency-key','branch-lease','--task-lease-expected-version','1')
    assert inspected['can_adopt'] and inspected['availability']['ready']
    assert 'source-revision:'+source_context['snapshot']['provider_revision'] in inspected['evidence_refs']
    assert 'Candidate 3 failed' in Path(result['artifact']).read_text()
    assert 'Candidate 999' not in Path(result['artifact']).read_text()
    assert source_run('todo','list','--goal-id','source','--todo-id',source_todo)==source_before
    assert run('task-lease','inspect','--goal-id','branch','--todo-id',destination_todo)['lease']==lease['lease']
    if with_knowledge and lost_response is None:
        _ordinary_review_and_work(run, workspace, tmp_path, destination_todo, digest, monkeypatch)
    # The adapter does not mint another receipt/session when retried after commit.
    replay=adopt_todo_context(env,unit,digest,**args)
    assert replay['continuation']['adoption']['status']=='replayed'
    assert replay['continuation']['provider_revision']==result['continuation']['provider_revision']
    if with_knowledge:
        assert replay['explore_knowledge']['appended_event_count'] == 0
        assert log.read_bytes() == imported_bytes
    assert run('todo','list','--goal-id','branch','--todo-id',destination_todo)==after
    with pytest.raises(ValueError,match='execution authority changed'):
        adopt_todo_context(env,unit,digest,**{**args,'lease_version':999})
    with pytest.raises(ValueError,match='already has context'):
        adopt_todo_context(env,unit,digest,**{**args,'operation_id':'different-operation',
            'expected_revision':after['authority_read']['provider_revision']})
    assert run('todo','list','--goal-id','branch','--todo-id',destination_todo)['todo']==after['todo']

    target=Path(result['artifact'])
    target.write_bytes(b'changed retained context')
    with pytest.raises(ValueError,match='artifact already differs'):
        adopt_todo_context(env,unit,digest,**args)
    target.write_bytes(raw)
    run('todo','update','--goal-id','branch','--todo-id',destination_todo,'--agent-id','branch-actor',
        '--note','New destination evidence must not be replaced by a historical retry.',
        '--update-operation-id','new-destination-evidence','--update-expected-provider-revision',after['authority_read']['provider_revision'],
        '--task-lease-idempotency-key','branch-lease','--task-lease-expected-version','1')
    moved=run('todo','list','--goal-id','branch','--todo-id',destination_todo)
    with pytest.raises(ValueError,match='continuation rejected'):
        adopt_todo_context(env,unit,digest,**args)
    assert run('todo','list','--goal-id','branch','--todo-id',destination_todo)==moved


def _ordinary_review_and_work(run, workspace, tmp_path, todo, digest, monkeypatch, work=None):
    """Real current-owner replan consumes inherited counterevidence, then work."""
    monkeypatch.chdir(workspace)
    run('configure-goal', '--goal-id', 'branch', '--execution-replan-after-turns', '1',
        '--execution-stride-strategy', 'evidence_conditioned_v1', '--execute')
    actor = ['--goal-id', 'branch', '--agent-id', 'branch-actor']
    def settle(turn, vision, *, review=False):
        path = tmp_path / (turn + '.json')
        path.write_text(json.dumps(vision))
        written = run('refresh-state', *actor, '--todo-id', todo, '--turn-instance-id', turn,
            '--classification', 'ordinary_checkpoint_research', '--delivery-batch-scale', 'single_surface',
            '--delivery-outcome', 'outcome_progress', '--delivery-boundary', 'semantic_closeout' if review else 'in_flight_continuation',
            '--agent-vision-json', str(path), '--no-global-sync', '--suppress-external-sinks')
        run('quota', 'spend-slot', *actor, '--todo-id', todo, '--turn-instance-id', turn,
            '--slots', '1', '--source', 'heartbeat', '--execute')
        return written
    vision = {'schema_version': 'goal_vision_replan_contract_v0', 'state': 'vision_patch_proposed',
        'vision_patch': {'acceptance_summary': 'Use retained counterevidence in the next complete experiment.'},
        'path_delta': {'outcome': 'continue', 'prior_assumption': 'Retained evidence needs current verification.',
            'observed_reality': 'The current ordinary Goal reads the inherited rejected candidate.',
            'retained': ['Candidate 3 is rejected; keep candidate 7.'], 'evidence_refs': ['checkpoint:' + digest]}}
    initial = run('quota', 'should-run', *actor, '--todo-id', todo, '--turn-instance-id', 'ordinary-preparation',
        '--runtime-profile', 'generic_cli', '--available-capability', 'shell')
    assert initial['interaction_contract']['agent_channel']['delivery_allowed']
    settle('ordinary-preparation', vision)
    review = run('quota', 'should-run', *actor, '--turn-instance-id', 'ordinary-review',
        '--runtime-profile', 'generic_cli', '--available-capability', 'shell')
    assert review['interaction_contract']['mode'] == 'autonomous_replan'
    review = run('quota', 'should-run', *actor, '--todo-id', todo, '--turn-instance-id', 'ordinary-review',
        '--runtime-profile', 'generic_cli', '--available-capability', 'shell')
    control = review['replan_action_packet']['stride_control']
    assert any(row['finding_id'] == 'negative-3' for row in control['explore_basis']['finding_refs'])
    decision = json.loads(json.dumps(control['minimal_example']))
    decision.update(basis='supported_progress', action='grow', axes={
        'work_slice': {'plan': 'Complete candidates 8 and 9 in one accepted work unit.',
            'reason': 'The preparation already verified retained candidate 7 and the source counterexample.'},
        'route_distance': {'plan': 'Keep the candidate increment mechanism.',
            'reason': 'Candidate 3 is refuted; this fixture tests continuation, not a new prediction route.'},
        'validation_scope': {'plan': 'Read both candidate values and retained rejection 3 from the result file.',
            'reason': 'Both results are needed to interpret this complete experiment.'}}, next_probe={
        'scope': 'One two-candidate ordinary Goal experiment.', 'prediction': 'Candidates 8 and 9 are both retained.',
        'adoption_criterion': 'The result contains both values and excludes rejected candidate 3.',
        'cost_bound': 'One current authorized work Turn.',
        'minimum_evidence': 'Actual result bytes, source-bound counterexample and current settlement.'})
    decision['explore_review'].update(disposition='used', considered_refs=['negative-3'],
        reason='The captured counterexample excludes candidate 3; complete the next two candidates from 7.')
    revised = json.loads(json.dumps(vision))
    revised['stride_decision'] = decision
    revised['path_delta'].update(outcome='replan', observed_reality='Current review read the source-bound refuted finding.',
        changed=['Complete candidates 8 and 9 together, keeping the rejected route excluded.'])
    reviewed = settle('ordinary-review', revised, review=True)
    from loopx.history import load_index_snapshot
    indexed = load_index_snapshot(Path(reviewed['index_path']), include_artifact_status=False).records
    accepted = next(row for row in indexed if row.get('turn_instance_id') == 'ordinary-review')
    actual_decision = accepted['agent_vision']['stride_decision']
    assert actual_decision['action'] == 'grow'
    assert actual_decision['explore_review']['disposition'] == 'used'
    assert actual_decision['explore_review']['considered_refs'] == ['negative-3']
    # The accepted decision is read from canonical history, not the submitted JSON.
    following = run('quota', 'should-run', *actor, '--todo-id', todo, '--turn-instance-id', 'ordinary-experiment',
        '--runtime-profile', 'generic_cli', '--available-capability', 'shell')
    assert following['interaction_contract']['agent_channel']['delivery_allowed']
    # Source history and operational observations are never used as current work.
    result_path = workspace / 'complete-experiment.json'
    if work is None:
        result_path.write_text(json.dumps({'candidates': [8, 9], 'rejected': [3], 'retained_start': 7}))
    else:
        work(following)
    assert json.loads(result_path.read_bytes())['candidates'] == [8, 9]
    # Settlement proves actual current work can continue under the original lease.
    revised.pop('stride_decision')
    revised['path_delta'].update(outcome='continue', observed_reality='Candidates 8 and 9 were completed in one ordinary work unit.')
    settle('ordinary-experiment', revised)


def test_research_history_is_complete_and_bound_to_original_source(tmp_path, monkeypatch, capsys):
    from loopx.capabilities.explore.result_log import (
        append_explore_result_events, build_explore_node_event, build_explore_edge_event,
        build_explore_finding_event, explore_result_log_path,
    )
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, root = tmp_path / "registry.json", tmp_path / "runtime"
    prefix = ["--format", "json", "--registry", str(registry), "--runtime-root", str(root)]
    assert main([*prefix, "bootstrap", "--project", str(tmp_path), "--goal-id", "source",
        "--objective", "Investigate synthetic candidates.", "--adapter-kind", "read_only_project_map_v0",
        "--adapter-status", "connected-read-only", "--write-scope", "**", "--no-global-sync"]) == 0
    capsys.readouterr()
    env = {"LOOPX_RUNTIME_ROOT": str(root), "LOOPX_GOAL_ID": "source"}
    log = explore_result_log_path(root, "source")
    events = [build_explore_node_event(goal_id="source", node_id="candidate-3", title="Rejected candidate",
                                      status="dead_end", summary="Fresh samples refuted candidate 3."),
              build_explore_node_event(goal_id="source", node_id="candidate-7", title="Retained candidate",
                                      status="exploring"),
              build_explore_edge_event(goal_id="source", from_node="candidate-7", to_node="candidate-3",
                                       edge_type="refutes")]
    # More than the display finding limit: a checkpoint must retain the earliest
    # counterexample as well as recent observations, with original topology.
    events += [build_explore_finding_event(goal_id="source", node_id="candidate-3", finding_id=f"finding-{i}",
                                          title=f"Negative observation {i}", status="confirmed") for i in range(45)]
    append_explore_result_events(log, events, expected_goal_id="source")
    index = root / "goals/source/runs/index.jsonl"
    # Synthetic persisted source records test transport, not vision judgment.
    rows = [{"goal_id": "source", "agent_id": "source-actor", "generated_at": f"2026-01-01T00:00:{i:02d}Z",
             "agent_vision": {"vision_patch": {"current_path": f"Complete experiment {i}"},
                              "stride_decision": {"original": i}}} for i in range(2)]
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    captured = research_context(env, deadline=time.time() + 30)
    assert captured["research_evidence"]["explore_events"] == events
    assert captured["research_evidence"]["vision_history"] == rows
    assert "leases" not in captured["snapshot"]
    components = [{"id": "runtime", "source_path": str(root)}]
    probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
    verify_context_sources(captured, components, probe["source_witness"])
    raw = index.read_bytes()
    index.write_bytes(raw + json.dumps({**rows[-1], "generated_at": "2026-01-01T01:00:00Z"}).encode() + b"\n")
    later = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
    with pytest.raises(ValueError, match="differs from the captured context"):
        verify_context_sources(captured, components, later["source_witness"])
    with pytest.raises(ValueError, match="outside the captured inventory"):
        verify_context_sources(captured, [], {})
    for bad in (b'{', b'not-json\n', b'[]\n', b'{"goal_id":"foreign"}\n'):
        index.write_bytes(raw + bad)
        with pytest.raises(ValueError):
            research_context(env, deadline=time.time() + 30)
    index.write_bytes(raw)
    original = log.read_bytes()
    log.write_bytes(original + b'not-json\n')
    with pytest.raises(ValueError, match="invalid Explore result"):
        research_context(env, deadline=time.time() + 30)
    log.write_bytes(original)
    log.unlink()
    absent = research_context(env, deadline=time.time() + 30)
    assert absent["research_evidence"]["explore_events"] == []
    log.write_bytes(original)
    appeared = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
    with pytest.raises(ValueError, match="appeared before sealing"):
        verify_context_sources(absent, components, appeared["source_witness"])
    # An append during the canonical read is also rejected, not silently mixed.
    from loopx.hosts import research_checkpoint
    original_read = research_checkpoint.canonical_context
    def racing_read(*args, **kwargs):
        result = original_read(*args, **kwargs)
        index.write_bytes(raw + b'{}\n')
        return result
    monkeypatch.setattr(research_checkpoint, "canonical_context", racing_read)
    with pytest.raises(ValueError, match="moved during the context read"):
        research_context(env, deadline=time.time() + 30)


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), True, "later", 0, -1])
def test_adoption_rejects_invalid_or_expired_deadline_before_io(tmp_path, monkeypatch, deadline):
    from loopx.hosts import research_checkpoint
    def forbidden(*args, **kwargs):
        raise AssertionError("expired/invalid admission must not read artifact or authority")
    monkeypatch.setattr(research_checkpoint, "effect_runtime_result", forbidden)
    with pytest.raises((ValueError, TimeoutError), match="deadline"):
        adopt_todo_context({}, tmp_path / "absent", "digest", source_todo_id="source",
            todo_id="destination", operation_id="original", expected_revision="revision",
            lease_key="lease", lease_version=1, deadline=deadline)
