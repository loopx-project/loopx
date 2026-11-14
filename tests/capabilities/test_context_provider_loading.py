"""Real imports and local provider use preserve the selection boundary."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def _run(code: str) -> None:
    # Resolve the intended checkout from invocation cwd, including baseline
    # sensitivity runs; the test file location is not the production revision.
    prefix = "import loopx, pathlib; assert pathlib.Path(loopx.__file__).resolve().is_relative_to(pathlib.Path.cwd().resolve())\n"
    result = subprocess.run(
        [sys.executable, "-c", prefix + textwrap.dedent(code)],
        cwd=Path.cwd(),
        env={**os.environ, "LOOPX_USAGE_PING": "0"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_context_types_do_not_select_provider_implementations() -> None:
    _run("""
        import sys
        from loopx.capabilities.context_providers.base import ContextProviderItem
        from loopx.capabilities.context_providers import canonical_context_text
        item = ContextProviderItem(resource_ref='source:fixture', summary='safe', content='retained source')
        assert item.summary == 'safe'
        assert canonical_context_text('  retained source\\r\\n') == '  retained source'
        assert 'loopx.capabilities.context_providers.openviking' not in sys.modules
        assert 'loopx.capabilities.context_providers.bm25' not in sys.modules
        assert 'loopx.capabilities.context_providers.lexical_retrieval' not in sys.modules
    """)


def test_provider_registry_rejections_and_custom_builder_remain_cold() -> None:
    _run("""
        import sys
        from loopx.capabilities.context_providers import build_context_provider, register_context_provider
        for value in ('', 'unregistered'):
            try:
                build_context_provider({'provider': value})
            except ValueError as exc:
                assert str(exc) == 'unsupported context provider: ' + (value or '<missing>')
            else:
                raise AssertionError('unknown provider accepted')
        for value in ('bm25', 'openviking'):
            try:
                register_context_provider(value, lambda config: None)
            except ValueError as exc:
                assert str(exc) == 'context provider already registered: ' + value
            else:
                raise AssertionError('builtin registration replaced')
        calls = []
        marker = object()
        def build(config):
            calls.append(config)
            return marker
        register_context_provider('fixture-custom', build)
        config = {'provider': 'fixture-custom', 'fixture_option': True}
        assert build_context_provider(config) is marker and calls == [config]
        assert 'loopx.capabilities.context_providers.openviking' not in sys.modules
        assert 'loopx.capabilities.context_providers.bm25' not in sys.modules
    """)


def test_local_bm25_retrieval_does_not_select_openviking() -> None:
    _run("""
        import json, pathlib, sys, tempfile
        from loopx.capabilities.context_providers import build_context_provider
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / 'corpus.json'
            path.write_text(json.dumps({'schema_version': 'local_bm25_corpus_v0',
                'scope_ref': 'corpus:test', 'documents': [
                    {'id': 'match', 'content': 'alpha alpha beta'},
                    {'id': 'other', 'content': 'beta gamma'}]}))
            provider = build_context_provider({'provider': 'bm25', 'corpus_path': str(path)})
            from loopx.capabilities.context_providers import BM25ContextProvider
            assert isinstance(provider, BM25ContextProvider)
            result = provider.retrieve(namespace='decision_context', scope_ref='corpus:test',
                query='alpha', query_summary='source match', max_results=3,
                timeout_seconds=5, observed_at='2026-01-01T00:00:00Z')
            assert result.status == 'completed' and len(result.items) == 1
            assert result.items[0].content == 'alpha alpha beta'
            public = json.dumps(result.public_packet())
            assert str(path) not in public and 'alpha alpha beta' not in public
            rejected = provider.retrieve(namespace='decision_context', scope_ref='corpus:foreign',
                query='alpha', query_summary='source match', max_results=3,
                timeout_seconds=5, observed_at='2026-01-01T00:00:00Z')
            assert rejected.reason_code == 'scope_mismatch'
        assert 'loopx.capabilities.context_providers.openviking' not in sys.modules
    """)


def test_binding_normalization_does_not_select_provider_transport() -> None:
    _run("""
        import sys
        from loopx.capabilities.reward_memory import application, experiment
        scope = application.classify_openviking_scope('viking://user/alice/peers/worker/memories')
        assert scope.user_scope_id == 'alice' and scope.actor_scope_id == 'worker'
        assert scope.actor_binding_required and scope.write_strategy == 'content_write'
        assert experiment.classify_openviking_scope is application.classify_openviking_scope
        try:
            application.classify_openviking_scope('viking://user/../memories')
        except ValueError as exc:
            assert str(exc) == 'OpenViking scope_ref must use safe non-empty path segments'
        else:
            raise AssertionError('invalid scope accepted')
        assert 'loopx.capabilities.context_providers.openviking' not in sys.modules
        assert 'loopx.capabilities.context_providers.bm25' not in sys.modules
    """)


def test_scope_public_identity_and_serialization_remain_compatible() -> None:
    _run("""
        import pickle, sys
        from loopx.capabilities.context_providers.openviking_scope import OpenVikingScope, classify_openviking_scope
        legacy = 'loopx.capabilities.context_providers.openviking'
        assert OpenVikingScope.__module__ == legacy
        assert classify_openviking_scope.__module__ == legacy
        scope = classify_openviking_scope('viking://user/alice/peers/worker/memories')
        assert legacy not in sys.modules
        # Explicit serialization may resolve the compatibility module; ordinary
        # binding classification above must remain independent of transport.
        for value in (scope, classify_openviking_scope):
            serialized = pickle.dumps(value)
            assert b'loopx.capabilities.context_providers.openviking_scope' not in serialized
            restored = pickle.loads(serialized)
            assert restored == value
        from loopx.capabilities.context_providers.openviking import OpenVikingScope as exported
        assert exported is OpenVikingScope
    """)


def test_disabled_retirement_context_does_not_select_inspector() -> None:
    _run("""
        import sys
        from loopx.capabilities.anti_entropy.turn_context import anti_entropy_context_result
        disabled = anti_entropy_context_result({}, goal_id='fixture', decision={})
        assert disabled == {'readback': {}, 'context_fields': {}, 'instructions': [], 'result_fields': {}}
        assert 'loopx.capabilities.anti_entropy.agent_tool' not in sys.modules
        assert 'loopx.capabilities.anti_entropy.oracles' not in sys.modules
        assert 'loopx.capabilities.anti_entropy.feedback' not in sys.modules
        active = anti_entropy_context_result({'retirement_basis': {'target_symbols': ['old_api']}},
            goal_id='fixture', decision={})
        assert active['readback']['anti_entropy_guidance']['target_symbols'] == ['old_api']
        assert active['context_fields']['anti_entropy_context']['inspect_tool']['name'] == 'anti_entropy_inspect'
        assert any('required' in line for line in active['instructions'])
    """)


@pytest.mark.parametrize("configured", [False, True])
def test_disabled_turn_recall_does_not_select_execution_helpers(configured: bool) -> None:
    _run(f"""
        import sys
        from pathlib import Path
        from loopx.capabilities.agent_turn_recall import runtime
        calls = []
        status = {{'status': 'disabled', 'automatic_recall': False}}
        config = {{'automation': {{'automatic_ingest': False, 'automatic_recall': False}}}} if {configured!r} else None
        def resolve(**kwargs):
            calls.append(kwargs)
            return status, config
        runtime.resolve_reward_memory_experiment = resolve
        result = runtime.run_configured_agent_turn_recall_fail_open(
            registry_path=Path('fixture-registry.json'), goal_id='fixture-goal',
            agent_id='fixture-agent', quota_decision={{}}, turn_instance_id='fixture-turn',
            execute=True)
        assert len(calls) == 1
        assert result['status'] == ('explicitly_disabled' if {configured!r} else 'disabled')
        assert result['context'] is None and result['provider_call_count'] == 0
        assert result['quota_spend_performed'] is False
        assert result['external_writes_performed'] is False
        assert result['grants_new_action_authority'] is False
        assert 'loopx.capabilities.reward_memory.runtime_hooks' not in sys.modules
        assert 'loopx.capabilities.reward_memory.read_authority' not in sys.modules
    """)


@pytest.mark.parametrize("package", ["context_providers", "anti_entropy", "agent_turn_recall"])
@pytest.mark.parametrize("mode", ["attribute", "from", "star"])
def test_explicit_exports_remain_original_owner_objects(
    package: str, mode: str
) -> None:
    _run(f"""
        import importlib
        name = 'loopx.capabilities.' + {package!r}
        module = importlib.import_module(name)
        namespace = {{}}
        if {mode!r} == 'star':
            exec('from ' + name + ' import *', namespace)
            assert set(module.__all__) <= set(namespace)
        elif {mode!r} == 'from':
            exec('from ' + name + ' import ' + ', '.join(module.__all__), namespace)
        else:
            namespace = {{key: getattr(module, key) for key in module.__all__}}
        for key in module.__all__:
            assert namespace[key] is getattr(module, key)
        if {package!r} == 'context_providers':
            owner = importlib.import_module(name + '.factory')
            assert namespace['build_context_provider'] is owner.build_context_provider
            assert namespace['OpenVikingContextProvider'] is importlib.import_module(name + '.openviking').OpenVikingContextProvider
        elif {package!r} == 'anti_entropy':
            owner = importlib.import_module(name + '.agent_tool')
            assert namespace['anti_entropy_inspect'] is owner.anti_entropy_inspect
        else:
            owner = importlib.import_module(name + '.runtime')
            assert namespace['run_configured_agent_turn_recall'] is owner.run_configured_agent_turn_recall
            assert namespace['run_agent_turn_recall'] is importlib.import_module(name + '.core').run_agent_turn_recall
        try:
            getattr(module, 'missing_fixture_export')
        except AttributeError:
            pass
        else:
            raise AssertionError('unknown export accepted')
    """)
