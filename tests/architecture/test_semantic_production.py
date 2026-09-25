"""Guard the producer/owner distinction using independent finite counterexamples."""
from __future__ import annotations

import pytest

from loopx.semantics.production import collect_production, validate_production, quota_action_domain
from loopx.semantics.python_production import Production
from loopx.semantics.inventory import SourceFile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = 'loopx/control_plane/quota/probe.py::emit'


def test_composed_field_does_not_widen_canonical_return_or_establish_owner_liveness():
    v = vocabulary()
    field_domain = {'run', 'wait', 'frontier_wait'}
    normal = Production(SITE, 1, 'dict', frozenset({'run', 'wait'}), False)
    frontier = Production(SITE, 2, 'dict', frozenset({'frontier_wait'}), False)
    assert validate_production('action', v, [normal, frontier], field_domain=field_domain) == []
    with pytest.raises(ValueError, match='no observed producer'):
        validate_production('action', v, [frontier], field_domain=field_domain)
    returned = Production(SITE, 3, 'return', frozenset({'frontier_wait'}), False)
    with pytest.raises(ValueError, match='unregistered values'):
        validate_production('action', v, [normal, returned], field_domain=field_domain)
    foreign = Production(SITE + '_undeclared', 4, 'dict', frozenset({'frontier_wait'}), False)
    with pytest.raises(ValueError, match='undeclared producer'):
        validate_production('action', v, [normal, foreign], field_domain=field_domain)


@pytest.mark.parametrize('mutation', ['missing', 'widened', 'overlap'])
def test_quota_union_cannot_be_weakened_or_made_ambiguous(mutation):
    registry = {'vocabularies': {
        'effective_action': {'values': ['normal_run']},
        'agent_scope_frontier_action': {'values': ['agent_scope_wait']},
    }, 'relations': {'shared_field_names': [{'field': 'effective_action', 'slots': [{
        'slot': 'should_run.effective_action',
        'vocabularies': ['effective_action', 'agent_scope_frontier_action'],
    }]}]}}
    assert quota_action_domain(registry) == {'normal_run', 'agent_scope_wait'}
    if mutation == 'missing':
        registry['relations']['shared_field_names'] = []
    elif mutation == 'widened':
        registry['relations']['shared_field_names'][0]['slots'][0]['vocabularies'].append('lease_action')
    else:
        registry['vocabularies']['agent_scope_frontier_action']['values'].append('normal_run')
    with pytest.raises(ValueError, match='anchored|disjoint'):
        quota_action_domain(registry)


def vocabulary():
    return {'values': ['run', 'wait'], 'producers': [SITE], 'owners': {'python': None},
            'literal_scan': {'field': 'action'}}


def row(value, *, site=SITE, unresolved=False, blocker=None):
    return Production(site, 1, 'return', frozenset([value]) if value else frozenset(), unresolved, blocker)


def test_owner_values_never_satisfy_production_liveness():
    with pytest.raises(ValueError, match='no observed producer'):
        validate_production('action', vocabulary(), [row('run')])


def test_undefined_producer_value_is_rejected():
    with pytest.raises(ValueError, match='unregistered values'):
        validate_production('action', vocabulary(), [row('run'), row('typo')])


def test_registering_an_unrelated_function_does_not_cover_a_writer():
    with pytest.raises(ValueError, match='undeclared producer sites'):
        validate_production('action', vocabulary(), [row('run'), row('wait', site=SITE.replace('emit', 'hidden'))])


def test_dynamic_path_remains_visible_and_cannot_supply_missing_value():
    with pytest.raises(ValueError, match='no observed producer'):
        validate_production('action', vocabulary(), [row('run'), row(None, unresolved=True, blocker='call_result')])
    unknown = validate_production('action', vocabulary(),
                                  [row('run'), row('wait'), row(None, unresolved=True, blocker='call_result')])
    # The reported entry names the site and why it stayed unknown. Passing the
    # blocker explicitly keeps a lost label from passing as the `other` fallback.
    assert unknown == [f'{SITE}:1 [call_result]']


def test_compatibility_values_must_have_no_observed_production():
    v = vocabulary()
    v['compatibility_only'] = {'wait': {'reason': 'Old reader', 'retirement': 'M1'}}
    assert validate_production('action', v, [row('run')]) == []
    with pytest.raises(ValueError, match='compatibility-only values are produced'):
        validate_production('action', v, [row('run'), row('wait')])


@pytest.mark.parametrize('source, expected', [
    ("function emit() { return {action: flag === 'condition' ? 'run' : 'wait'}; }", {'run', 'wait'}),
    ("function emit() { output['action'] = 'run'; output.action = 'wait'; }", {'run', 'wait'}),
    ("function read() { if (p.action === 'run') console.log('action'); }", set()),
    ("// action: 'comment'\nconst example = `action: 'example'`;", set()),
])
def test_typescript_parser_observes_results_not_context(source, expected):
    rows = collect_production(ROOT, vocabulary(), [SourceFile('loopx/control_plane/quota/probe.ts', '.ts', source)])
    assert set().union(*(r.values for r in rows)) == expected


def test_declared_return_is_scanned_in_real_python_syntax():
    v = vocabulary()
    v['return_producers'] = [SITE]
    rows = collect_production(ROOT, v, [SourceFile(SITE.split('::')[0], '.py', 'def emit():\n return "unregistered"\n')])
    with pytest.raises(ValueError, match='unregistered'):
        validate_production('action', v, rows)


@pytest.mark.parametrize('vocabulary_name, module, original', [
    ('turn_route', 'loopx/control_plane/turn_driver/driver.py', 'return LoopXTurnRoute.CONTRACT_ERROR'),
    ('loop_disposition', 'loopx/control_plane/turn_driver/turn_contract_generated.py', 'return LoopDisposition(value)'),
])
def test_real_return_producer_rejects_an_unregistered_result(vocabulary_name, module, original):
    import json
    from loopx.semantics.inventory import load_sources
    v = json.loads((ROOT / 'loopx/semantics/vocabulary_v0.json').read_text())['vocabularies'][vocabulary_name]
    sources = load_sources(ROOT)
    replacement = 'return "unknown_action"'
    found = False
    mutated = []
    for source in sources:
        if source.path == module:
            assert original in source.text
            source = SourceFile(source.path, source.suffix, source.text.replace(original, replacement, 1))
            found = True
        mutated.append(source)
    assert found
    with pytest.raises(ValueError, match='producer writes unregistered values'):
        validate_production(vocabulary_name, v, collect_production(ROOT, v, mutated))


def test_real_turn_decoder_supplies_typed_input_witnesses():
    from loopx.semantics.production import probe_turn_result_input_domain
    values = ['validated_progress', 'validated_completion', 'repair_required',
              'replan_required', 'user_action_required', 'wait', 'iteration_failed',
              'host_failure', 'validation_failed', 'writeback_failed',
              'quota_spend_failed', 'terminal_closeout_failed']
    v = {'values': values, 'input_producer': 'loopx/control_plane/turn_driver/transaction.py::_result_kind'}
    rows = probe_turn_result_input_domain(v)
    assert {r.form for r in rows} == {'input_witness'}
    assert set().union(*(r.values for r in rows)) == set(values)
    assert all(not r.unresolved for r in rows)


@pytest.mark.parametrize('defect', ['constant_result', 'untyped_result', 'unknown_admitted'])
def test_input_witness_probe_rejects_decoder_contract_regressions(monkeypatch, defect):
    from types import SimpleNamespace
    from loopx.control_plane.turn_driver import transaction
    from loopx.semantics.production import probe_turn_result_input_domain
    original = transaction._result_kind

    def defective(value, errors):
        if defect == 'constant_result':
            return transaction.LoopXTurnResultKind.WAIT
        if defect == 'untyped_result':
            return SimpleNamespace(value=value)
        if value == 'unknown_result_kind':
            return transaction.LoopXTurnResultKind.WAIT
        return original(value, errors)

    monkeypatch.setattr(transaction, '_result_kind', defective)
    v = {'values': ['repair_required'], 'input_producer': 'loopx/control_plane/turn_driver/transaction.py::_result_kind'}
    with pytest.raises(ValueError, match='decoder'):
        probe_turn_result_input_domain(v)


def test_legacy_lease_values_stay_visible_without_claiming_production():
    import json
    from loopx.semantics.inventory import load_sources
    v = json.loads((ROOT / 'loopx/semantics/vocabulary_v0.json').read_text())['vocabularies']['lease_action']
    assert v['status'] == 'legacy'
    assert v['producers'] == []
    assert set(v['compatibility_only']) == {'acquire', 'renew', 'transfer', 'release'}
    rows = collect_production(ROOT, v, load_sources(ROOT))
    assert not any(r.values for r in rows)
    assert validate_production('lease_action', v, rows) == []


def test_new_lease_producer_invalidates_compatibility_only_claim():
    import json
    from loopx.semantics.inventory import load_sources
    v = json.loads((ROOT / 'loopx/semantics/vocabulary_v0.json').read_text())['vocabularies']['lease_action']
    sources = load_sources(ROOT) + [SourceFile('loopx/control_plane/coordination/new_writer.py', '.py',
        'from .authority_core import LeaseAction\ndef emit():\n return LeaseAction.ACQUIRE\n')]
    with pytest.raises(ValueError, match='compatibility-only values are produced'):
        validate_production('lease_action', v, collect_production(ROOT, v, sources))


def test_typescript_syntax_failure_reports_only_source_location():
    source = SourceFile('loopx/control_plane/quota/broken.ts', '.ts', 'const secret = "fixture-only";\nfunction invalid( {')
    with pytest.raises(ValueError, match=r'broken.ts:2: invalid TypeScript source') as error:
        collect_production(ROOT, vocabulary(), [source])
    assert 'fixture-only' not in str(error.value)


@pytest.mark.parametrize('suffix, text', [
    ('.py', 'def project(value):\n return {"action": value}\n'),
    ('.ts', 'function project(value) { return {action: value}; }'),
])
def test_generic_effect_files_are_scanned_without_widening_to_all_control_plane(suffix, text):
    effect = 'loopx/control_plane/effect_program' + suffix
    sibling = 'loopx/control_plane/unrelated' + suffix
    rows = collect_production(ROOT, vocabulary(), [SourceFile(effect, suffix, text), SourceFile(sibling, suffix, text)])
    assert {r.site.split('::')[0] for r in rows} == {effect}
    assert all(r.unresolved and not r.values for r in rows)
    with pytest.raises(ValueError, match='no observed producer'):
        validate_production('action', vocabulary(), rows)


@pytest.mark.parametrize('suffix,text', [
    ('.ts', 'function turn() { return {action: null}; }\nfunction quota(input) { return {action: input.action}; }'),
    ('.py', 'def turn():\n return {"action": None}\ndef quota(value):\n return {"action": value}\n'),
])
def test_effect_null_and_quota_passthrough_have_distinct_evidence(suffix, text):
    rows = collect_production(ROOT, vocabulary(), [SourceFile(
        'loopx/control_plane/effect_program' + suffix, suffix, text,
    )])
    assert {r.site.rsplit('::', 1)[1]: (r.values, r.unresolved) for r in rows} == {
        'turn': (frozenset(), False), 'quota': (frozenset(), True),
    }


def test_real_effect_adapters_are_observed_as_unresolved_passthrough():
    import json
    from loopx.semantics.inventory import load_sources

    v = json.loads((ROOT / 'loopx/semantics/vocabulary_v0.json').read_text())['vocabularies']['effective_action']
    paths = {'loopx/control_plane/effect_program.py', 'loopx/control_plane/effect_program.ts'}
    owner = v['owners']['python'].split('::')[0]
    sources = [source for source in load_sources(ROOT) if source.path in paths | {owner}]
    rows = collect_production(ROOT, v, sources)
    assert {r.site.split('::')[0] for r in rows} == paths
    assert not set().union(*(r.values for r in rows))
    assert {r.site.split('::')[0] for r in rows if r.unresolved} == paths


def test_explicit_call_builder_metadata_binds_only_actual_tracked_parameters():
    v = vocabulary()
    v['owners']['python'] = 'loopx/control_plane/quota/owner.py::Action'
    v['call_producers'] = {'loopx/control_plane/quota/builder.py::emit': ['verdict']}
    sources = [
        SourceFile('loopx/control_plane/quota/owner.py', '.py', 'class Action:\n RUN="run"\n WAIT="wait"\n'),
        SourceFile('loopx/control_plane/quota/builder.py', '.py', 'def emit(verdict, *, reason):\n return {"verdict": verdict}\n'),
        SourceFile(SITE.split('::')[0], '.py', 'from .owner import Action\nfrom .builder import emit as output\ndef emit():\n output(Action.RUN, reason=Action.WAIT)\n'),
    ]
    rows = collect_production(ROOT, v, sources)
    assert set().union(*(r.values for r in rows)) == {'run'}
    v['call_producers']['loopx/control_plane/quota/builder.py::emit'] = ['nonexistent']
    with pytest.raises(ValueError, match='builder signature'):
        collect_production(ROOT, v, sources)


def test_registered_consumer_cannot_replace_a_removed_writer():
    v = vocabulary()
    owner = 'loopx/control_plane/quota/owner.py'
    v['owners']['python'] = owner + '::Action'
    sources = [
        SourceFile(owner, '.py', 'class Action:\n RUN="run"\n WAIT="wait"\n'),
        SourceFile(SITE.split('::')[0], '.py',
                   'from .owner import Action\ndef emit(packet):\n choices = (Action.RUN, Action.WAIT)\n return predicate(packet, choices)\n'),
    ]
    with pytest.raises(ValueError, match='no observed producer'):
        validate_production('action', v, collect_production(ROOT, v, sources))


def test_real_reexported_turn_owner_is_attributed_through_one_hop():
    import json
    from loopx.semantics.inventory import load_sources
    # loop_controller imports LoopXTurnRoute from driver, which re-exports the
    # generated owner; the registered return producer must still resolve.
    v = json.loads((ROOT / 'loopx/semantics/vocabulary_v0.json').read_text())['vocabularies']['turn_route']
    rows = collect_production(ROOT, v, load_sources(ROOT))
    site = 'loopx/control_plane/turn_driver/loop_controller.py::_envelope_route'
    resolved = {value for r in rows if r.site == site and not r.unresolved for value in r.values}
    assert 'user_action_required' in resolved


def test_input_witness_runs_only_for_the_anchored_site():
    from loopx.semantics.production import INPUT_WITNESSES
    values = ['validated_progress', 'validated_completion', 'repair_required',
              'replan_required', 'user_action_required', 'wait', 'iteration_failed',
              'host_failure', 'validation_failed', 'writeback_failed',
              'quota_spend_failed', 'terminal_closeout_failed']
    base = {'values': values, 'producers': [], 'owners': {'python': None}}
    assert collect_production(ROOT, {**base, 'input_producer': 'loopx/elsewhere.py::decode'}, []) == []
    anchored = {**base, 'input_producer': 'loopx/control_plane/turn_driver/transaction.py::_result_kind'}
    assert anchored['input_producer'] in INPUT_WITNESSES
    rows = collect_production(ROOT, anchored, [])
    assert {r.form for r in rows} == {'input_witness'}
    assert set().union(*(r.values for r in rows)) == set(values)


def test_reported_sites_carry_a_blocker_label_and_summarise():
    import runpy

    from loopx.semantics.inventory import load_sources

    smoke = runpy.run_path(str(ROOT / 'examples/semantic-vocabulary-drift-smoke.py'),
                           run_name='not_main')
    registry = smoke['load_registry']()
    sites = smoke['check_producers'](registry, load_sources(ROOT))
    assert sites, 'the repository still has unresolved producer sites to describe'
    assert all(site.endswith(']') and ' [' in site for site in sites)
    summary = smoke['summarise_blockers'](sites)
    counted = sum(int(part.split('=')[1]) for part in summary.split(','))
    assert counted == len(sites)
    # These two can never become evidence, so they must stay separable from the
    # paths a future slice could still resolve.
    assert 'argument_name_only=' in summary
    assert 'annotation_only=' in summary


def test_typescript_scan_names_the_missing_setup_step(monkeypatch):
    import subprocess

    from loopx.semantics import production

    source = [SourceFile('loopx/example.ts', '.ts', 'export const x = 1;\n')]
    monkeypatch.setattr(production.shutil, 'which', lambda name: None)
    with pytest.raises(ValueError, match='needs Node.js on PATH'):
        production.run_typescript_scan(ROOT, source, {})

    monkeypatch.setattr(production.shutil, 'which', lambda name: '/usr/bin/node')
    missing = "Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'typescript' imported from x.mjs"
    monkeypatch.setattr(
        production.subprocess, 'run',
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, stdout='', stderr=missing),
    )
    with pytest.raises(ValueError) as raised:
        production.run_typescript_scan(ROOT, source, {})
    assert str(raised.value) == production.NPM_DEV_DEPENDENCIES_MISSING
    assert 'x.mjs' not in str(raised.value), 'parser stderr must never reach public diagnostics'
