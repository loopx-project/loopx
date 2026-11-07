"""Existing transport exports stay compatible while pure callers stay cold."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

# Existing caller reexports, not a new transport API or state vocabulary.
CALLERS = {
    "loopx.doctor_git": ("terminate_process_tree",),
    "loopx.contract": ("terminate_process_tree",),
    "loopx.control_plane.runtime.validation_command": ("terminate_process_tree",),
    "loopx.control_plane.turn_driver.host_bridge_transport": (
        "force_terminate_process", "write_process_stdin_before_deadline",
    ),
}


def _run(code: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "LOOPX_USAGE_PING": "0"},
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("module_name", CALLERS)
def test_pure_caller_import_leaves_process_transport_unselected(module_name: str) -> None:
    _run(rf"""
        import importlib, sys
        effects = []
        sys.addaudithook(lambda event, args: effects.append(event)
            if event in ('subprocess.Popen', 'socket.connect') else None)
        module = importlib.import_module({module_name!r})
        assert 'loopx.extensions.process_runtime' not in sys.modules
        assert set({CALLERS[module_name]!r}) <= set(dir(module))
        assert effects == []
        try:
            getattr(module, 'missing_transport_export')
        except AttributeError:
            pass
        else:
            raise AssertionError('unknown attribute accepted')
        assert 'loopx.extensions.process_runtime' not in sys.modules
    """)


@pytest.mark.parametrize("module_name", CALLERS)
@pytest.mark.parametrize("mode", ["attribute", "from", "star"])
def test_explicit_transport_export_is_original_function(module_name: str, mode: str) -> None:
    _run(rf"""
        import importlib, inspect
        module = importlib.import_module({module_name!r})
        names = {CALLERS[module_name]!r}
        namespace = {{}}
        if {mode!r} == 'from':
            exec('from {module_name} import ' + ', '.join(names), namespace)
        elif {mode!r} == 'star':
            exec('from {module_name} import *', namespace)
        else:
            namespace.update((name, getattr(module, name)) for name in names)
        owner = importlib.import_module('loopx.extensions.process_runtime')
        for name in names:
            value = namespace[name]
            assert value is getattr(owner, name)
            assert value is getattr(module, name)
            assert inspect.signature(value) == inspect.signature(getattr(owner, name))
        if {mode!r} == 'star':
            assert {{name for name in namespace if not name.startswith('_')}} == {{
                name for name in dir(module) if not name.startswith('_')}}
    """)


@pytest.mark.parametrize("module_name", CALLERS)
def test_preloaded_owner_preserves_import_time_binding(module_name: str) -> None:
    _run(rf"""
        import importlib
        owner = importlib.import_module('loopx.extensions.process_runtime')
        names = {CALLERS[module_name]!r}
        def before(*args, **kwargs): pass
        def after(*args, **kwargs): pass
        for name in names:
            setattr(owner, name, before)
        module = importlib.import_module({module_name!r})
        for name in names:
            setattr(owner, name, after)
            assert getattr(module, name) is before
    """)


@pytest.mark.parametrize("module_name", CALLERS)
def test_caller_patch_points_remain_internal_bindings(module_name: str) -> None:
    _run(rf"""
        import importlib, io, time
        from pathlib import Path
        module = importlib.import_module({module_name!r})
        names = {CALLERS[module_name]!r}
        calls = []
        def patched(*args, **kwargs):
            calls.append((args, kwargs))
        for name in names:
            setattr(module, name, patched)
        class Process:
            stdout = io.StringIO()
            stderr = io.StringIO()
            returncode = 0
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def wait(self, **kwargs): return 0
            def poll(self): return 0
            def communicate(self, **kwargs): raise KeyboardInterrupt()
        process = Process()
        if {module_name!r} in ('loopx.doctor_git', 'loopx.contract'):
            module._retire_owned_probe(process)
            assert calls == [((process,), {{'grace_seconds': 0.2}})]
        elif {module_name!r}.endswith('validation_command'):
            module.subprocess.Popen = lambda *args, **kwargs: process
            try:
                module.run_caller_validation(Path.cwd(), validation_argv=['fixture'],
                    validation_label='fixture', timeout_seconds=1)
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError('cancellation lost')
            assert calls == [((process,), {{'grace_seconds': 0}})]
        else:
            process.stdout = io.StringIO('{{"kind":"ready"}}\n')
            bridge = module.GuardedHostBridge(process,
                deadline=time.monotonic()+2, cleanup_timeout=1)
            try:
                bridge.send('fixture\n')
                assert calls[0][0] == (process, 'fixture\n')
                assert calls[0][1]['cancel_and_reap'] == bridge.cancel
            finally:
                bridge.close_output()
            calls.clear()
            process.stdout = io.StringIO()
            bridge = module.GuardedHostBridge(process,
                deadline=time.monotonic()+2, cleanup_timeout=1)
            try:
                bridge.cancel()
                assert calls == [((process,), {{'process_tree_owned': True}})]
                assert bridge.not_launched
            finally:
                bridge.close_output()
    """)


@pytest.mark.parametrize("module_name", CALLERS)
def test_first_export_binds_the_original_owner_bundle(module_name: str) -> None:
    _run(f"""
        import importlib
        module = importlib.import_module({module_name!r})
        names = {CALLERS[module_name]!r}
        first = getattr(module, names[0])
        owner = importlib.import_module('loopx.extensions.process_runtime')
        originals = {{name: getattr(owner, name) for name in names}}
        def replacement(*args, **kwargs): pass
        for name in names:
            setattr(owner, name, replacement)
            assert getattr(module, name) is originals[name]
        assert first is originals[names[0]]
    """)


@pytest.mark.parametrize("module_name", CALLERS)
def test_reload_rebinds_exports_from_the_selected_owner(module_name: str) -> None:
    _run(f"""
        import importlib
        module = importlib.import_module({module_name!r})
        names = {CALLERS[module_name]!r}
        getattr(module, names[0])
        owner = importlib.import_module('loopx.extensions.process_runtime')
        def owner_patch(*args, **kwargs): pass
        def caller_patch(*args, **kwargs): pass
        for name in names:
            setattr(owner, name, owner_patch)
            setattr(module, name, caller_patch)
        importlib.reload(module)
        for name in names:
            assert getattr(module, name) is owner_patch
    """)
