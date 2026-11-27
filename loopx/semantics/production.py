"""Collect and check bounded semantic production evidence for repository CI."""
from __future__ import annotations

import ast
import atexit
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

from ..control_plane.runtime.node_probe import node_typescript_flags
from .inventory import SourceFile
from .python_production import (
    _TREES as _PYTHON_PRODUCTION_TREES,
    Production,
    enum_members,
    scan_python_production,
)


# Matched verbatim by tests/conftest.py to collapse repeated failures into one hint.
NPM_DEV_DEPENDENCIES_MISSING = (
    'repository npm dev dependencies are not installed (the typescript package is missing); '
    'run `npm ci --ignore-scripts` once from the repository root'
)


# This source boundary is code owned. It is not adjustable through registry data.
PRODUCER_ROOTS = (
    'loopx/cli_commands', 'loopx/control_plane/agents',
    'loopx/control_plane/quota', 'loopx/control_plane/todos', 'loopx/control_plane/coordination',
    'loopx/control_plane/turn_driver', 'loopx/control_plane/work_items',
)
PRODUCER_FILES = frozenset({
    'loopx/control_plane/effect_program.py', 'loopx/control_plane/effect_program.ts',
})

# Root should-run actions preserve two disjoint, independently owned domains.
# Registry data cannot add another union arm to weaken field closedness.
QUOTA_ACTION_VOCABULARIES = ('effective_action', 'agent_scope_frontier_action')


def quota_action_domain(registry: dict[str, Any]) -> set[str]:
    slots = [slot for entry in registry['relations']['shared_field_names']
             if entry.get('field') == 'effective_action'
             for slot in entry['slots'] if slot.get('slot') == 'should_run.effective_action']
    if len(slots) != 1 or slots[0].get('vocabularies') != list(QUOTA_ACTION_VOCABULARIES):
        raise ValueError('should_run.effective_action must retain its anchored decision/frontier union')
    result: set[str] = set()
    for name in QUOTA_ACTION_VOCABULARIES:
        values = set(registry['vocabularies'][name]['values'])
        if result & values:
            raise ValueError('should_run.effective_action union arms must be disjoint')
        result.update(values)
    return result


def collect_production(
    root: Path,
    vocabulary: dict[str, Any],
    sources: list[SourceFile],
    *,
    ts_rows: Sequence[dict[str, Any]] | None = None,
) -> list[Production]:
    by_path = {s.path: s for s in sources}
    owner = vocabulary['owners'].get('python')
    enums = {}
    if owner:
        module, symbol = owner.split('::')
        if module not in by_path:
            raise ValueError(f'producer owner must be a tracked source: {owner}')
        enums[owner] = enum_members(by_path[module], symbol)
    field = vocabulary.get('literal_scan', {}).get('field')
    returns = vocabulary.get('return_producers', [])
    return_paths = vocabulary.get('return_paths', {})
    if not set(return_paths) <= set(returns):
        raise ValueError('return paths must name declared return producers')
    if any(not isinstance(path, list) or not path or any(type(key) not in (str, int) for key in path)
           for path in return_paths.values()):
        raise ValueError('return paths must be nonempty literal field/index paths')
    calls = _call_arguments(vocabulary.get('call_producers', {}), by_path)
    selected = [s for s in sources if s.path in PRODUCER_FILES
                or any(s.path.startswith(p + '/') for p in PRODUCER_ROOTS)]
    rows = []
    scan_python = bool(field or enums or calls or any(site.split('::')[0].endswith('.py') for site in returns))
    if scan_python:
        prior_trees = set(_PYTHON_PRODUCTION_TREES)
        try:
            for source in selected:
                if source.suffix == '.py':
                    names = frozenset(site.split('::')[1] for site in returns if site.split('::')[0] == source.path)
                    paths = {site.split('::')[1]: tuple(path) for site, path in return_paths.items()
                             if site.split('::')[0] == source.path}
                    rows.extend(scan_python_production(source, field=field, enums=enums, return_functions=names,
                                                      return_paths=paths, call_arguments=calls, modules=by_path))
        finally:
            for cached_source in list(_PYTHON_PRODUCTION_TREES):
                if cached_source not in prior_trees and cached_source in _PYTHON_PRODUCTION_TREES:
                    del _PYTHON_PRODUCTION_TREES[cached_source]
    if ts_rows is not None:
        rows.extend(normalize_typescript_production_rows(ts_rows))
    else:
        rows.extend(_typescript_scan(root, [s for s in selected if s.suffix == '.ts'], field, returns))
    if witness := INPUT_WITNESSES.get(vocabulary.get('input_producer') or ''):
        rows.extend(witness(vocabulary))
    return rows


def _call_arguments(declarations: dict[str, list[str]], sources: dict[str, SourceFile]) -> dict[str, dict[str, int | None]]:
    """Bind reviewed output-builder parameters to their actual source signature.

    This is a finite caller contract, not interprocedural inference. The registry
    names the output argument; tracked source proves the target/signature, and
    the Python scanner proves an unshadowed local or imported call binding.
    """
    calls = {}
    for site, names in declarations.items():
        path, symbol = site.split('::')
        if path not in sources or sources[path].suffix != '.py':
            raise ValueError(f'call producer must name a tracked Python builder: {site}')
        functions = [n for n in ast.parse(sources[path].text, filename=path).body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == symbol]
        if len(functions) != 1:
            raise ValueError(f'call producer requires one top-level builder: {site}')
        args = functions[0].args
        parameters: dict[str, int | None] = {arg.arg: i for i, arg in enumerate([*args.posonlyargs, *args.args])}
        parameters.update({arg.arg: None for arg in args.kwonlyargs})
        if not names or len(set(names)) != len(names) or not set(names) <= parameters.keys():
            raise ValueError(f'call producer arguments must match the builder signature: {site}')
        calls[site] = {name: parameters[name] for name in names}
    return calls


def normalize_typescript_production_rows(rows: Sequence[dict[str, Any]]) -> list[Production]:
    # The parser names the same blocker vocabulary as the Python scanner;
    # ``typescript_dynamic`` stays the fallback for a form it cannot classify.
    return [Production(r['site'], r['line'], r['form'], frozenset(r['values']), r['unresolved'],
                       (r.get('blocker') or 'typescript_dynamic') if r['unresolved'] else None)
            for r in rows]


def _typescript_scan(
    root: Path, ts_sources: list[SourceFile], field: str | None,
    returns: list[str], mode: str = 'production',
) -> list[Production]:
    if not ts_sources or (not field and not returns):
        return []
    # A return-owned vocabulary need not also claim a global field name.
    # Omit the field entirely so dynamic object keys cannot match JSON null.
    request = {'return_functions': returns, 'mode': mode}
    if field:
        request['field'] = field
    rows = run_typescript_scan(root, ts_sources, request)
    return normalize_typescript_production_rows(rows)


_REPO_SCAN_SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/semantic_production_scan.mjs'
_DEFAULT_WHICH = shutil.which
_DEFAULT_SUBPROCESS_RUN = subprocess.run
_DEFAULT_TS_SCAN_TIMEOUT_SECONDS = 60.0
_MAX_TS_SERVER_STDERR_BYTES = 65536
_TS_SERVER_LOCK = threading.Lock()
_TS_SERVER: subprocess.Popen[bytes] | None = None
_TS_SERVER_KEY: tuple[str, str] | None = None
_TS_SERVER_STDOUT_BUF = bytearray()


def _close_pipe(stream: Any) -> None:
    if stream is not None:
        try:
            stream.close()
        except OSError:
            pass


def _shutdown_ts_server_locked(*, force: bool = False) -> None:
    global _TS_SERVER, _TS_SERVER_KEY
    proc = _TS_SERVER
    _TS_SERVER = None
    _TS_SERVER_KEY = None
    _TS_SERVER_STDOUT_BUF.clear()
    if proc is None:
        return
    try:
        _close_pipe(proc.stdin)
        if not force:
            try:
                proc.wait(timeout=1)
                return
            except subprocess.TimeoutExpired:
                pass
            except OSError:
                return
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            pass
    finally:
        _close_pipe(proc.stdout)
        _close_pipe(proc.stderr)


def _shutdown_ts_server() -> None:
    with _TS_SERVER_LOCK:
        _shutdown_ts_server_locked()


atexit.register(_shutdown_ts_server)


def _raise_syntax_error(error: Any, ts_paths: set[str]) -> None:
    if (isinstance(error, dict) and error.get('code') == 'typescript_syntax'
            and error.get('path') in ts_paths
            and isinstance(error.get('line'), int) and error['line'] > 0):
        raise ValueError(f"{error['path']}:{error['line']}: invalid TypeScript source; repair syntax before semantic scanning")


def _drain_nonblocking_fd(
    fd: int, buf: bytearray, *, deadline: float, cmd: list[str], timeout: float,
) -> bool:
    """Drain immediately available bytes from a non-blocking stderr pipe within deadline."""
    while True:
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(cmd, timeout)
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            return False
        except OSError:
            return True
        if not chunk:
            return True
        buf.extend(chunk)
        if len(buf) > _MAX_TS_SERVER_STDERR_BYTES:
            del buf[: len(buf) - _MAX_TS_SERVER_STDERR_BYTES]
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(cmd, timeout)


def _exchange_ts_server_request(
    proc: subprocess.Popen[bytes], encoded: bytes, cmd: list[str], deadline: float, timeout: float,
) -> tuple[bytes, str, bool]:
    """Write one NDJSON line and read one response line under a single deadline."""
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    stdin_fd = proc.stdin.fileno()
    stdout_fd = proc.stdout.fileno()
    stderr_fd = proc.stderr.fileno()
    for fd in (stdin_fd, stdout_fd, stderr_fd):
        os.set_blocking(fd, False)
    write_view = memoryview(encoded)
    write_failed = False
    stdout_buf = _TS_SERVER_STDOUT_BUF
    stderr_tail = bytearray()
    stdout_eof = False
    stderr_eof = False
    eof_drain_deadline: float | None = None

    def _check_deadline() -> None:
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(cmd, timeout)

    def _finish_eof() -> tuple[bytes, str, bool]:
        _drain_nonblocking_fd(
            stderr_fd, stderr_tail, deadline=deadline, cmd=cmd, timeout=timeout,
        )
        _check_deadline()
        return b'', stderr_tail.decode('utf-8', errors='replace'), write_failed

    with selectors.DefaultSelector() as sel:
        sel.register(stdin_fd, selectors.EVENT_WRITE)
        sel.register(stdout_fd, selectors.EVENT_READ)
        sel.register(stderr_fd, selectors.EVENT_READ)
        while True:
            _check_deadline()
            if not write_view:
                newline_idx = stdout_buf.find(b'\n')
                if newline_idx != -1:
                    _drain_nonblocking_fd(
                        stderr_fd, stderr_tail, deadline=deadline, cmd=cmd, timeout=timeout,
                    )
                    _check_deadline()
                    line = bytes(stdout_buf[:newline_idx])
                    del stdout_buf[: newline_idx + 1]
                    return line, stderr_tail.decode('utf-8', errors='replace'), False
            if (stdout_eof or write_failed) and (stderr_eof or not write_view):
                if stdout_eof and stderr_eof:
                    return _finish_eof()
                if eof_drain_deadline is None:
                    eof_drain_deadline = min(deadline, time.monotonic() + 0.2)
            now = time.monotonic()
            if now >= deadline:
                raise subprocess.TimeoutExpired(cmd, timeout)
            if eof_drain_deadline is not None and now >= eof_drain_deadline and (stdout_eof or write_failed):
                return _finish_eof()
            active_deadline = min(deadline, eof_drain_deadline) if eof_drain_deadline is not None else deadline
            remaining = active_deadline - now
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout)
            events = sel.select(timeout=remaining)
            _check_deadline()
            if not events:
                if eof_drain_deadline is not None and (stdout_eof or write_failed):
                    return _finish_eof()
                raise subprocess.TimeoutExpired(cmd, timeout)
            for key, mask in events:
                _check_deadline()
                if key.fd == stdin_fd and (mask & selectors.EVENT_WRITE):
                    try:
                        written = os.write(stdin_fd, write_view)
                    except BlockingIOError:
                        continue
                    except OSError:
                        write_failed = True
                        write_view = memoryview(b'')
                        sel.unregister(stdin_fd)
                        continue
                    if written <= 0:
                        write_failed = True
                        write_view = memoryview(b'')
                        sel.unregister(stdin_fd)
                        continue
                    write_view = write_view[written:]
                    if not write_view:
                        sel.unregister(stdin_fd)
                    _check_deadline()
                elif key.fd == stderr_fd and (mask & selectors.EVENT_READ):
                    if _drain_nonblocking_fd(
                        stderr_fd, stderr_tail, deadline=deadline, cmd=cmd, timeout=timeout,
                    ):
                        stderr_eof = True
                        sel.unregister(stderr_fd)
                elif key.fd == stdout_fd and (mask & selectors.EVENT_READ):
                    while True:
                        _check_deadline()
                        try:
                            chunk = os.read(stdout_fd, 65536)
                        except BlockingIOError:
                            break
                        except OSError:
                            stdout_eof = True
                            sel.unregister(stdout_fd)
                            break
                        if not chunk:
                            stdout_eof = True
                            sel.unregister(stdout_fd)
                            break
                        stdout_buf.extend(chunk)
                        _check_deadline()
                        if b'\n' in chunk:
                            break


def _invoke_typescript_via_server(
    node: str, script_path: Path, payload: dict[str, Any], ts_paths: set[str],
    *, timeout: float | None = None,
) -> Any:
    global _TS_SERVER, _TS_SERVER_KEY
    effective_timeout = _DEFAULT_TS_SCAN_TIMEOUT_SECONDS if timeout is None else timeout
    deadline = time.monotonic() + effective_timeout
    key = (node, str(script_path))
    cmd = [node, str(script_path), '--server']
    encoded = (json.dumps(payload) + '\n').encode('utf-8')
    lock_wait = max(0.0, deadline - time.monotonic())
    if not _TS_SERVER_LOCK.acquire(timeout=lock_wait):
        raise subprocess.TimeoutExpired(cmd, effective_timeout)
    try:
        if _TS_SERVER is None or _TS_SERVER.poll() is not None or _TS_SERVER_KEY != key:
            _shutdown_ts_server_locked()
            _TS_SERVER = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            )
            _TS_SERVER_KEY = key
        proc = _TS_SERVER
        if deadline - time.monotonic() <= 0:
            _shutdown_ts_server_locked(force=True)
            raise subprocess.TimeoutExpired(cmd, effective_timeout)
        try:
            response_line, stderr_text, write_failed = _exchange_ts_server_request(
                proc, encoded, cmd, deadline, effective_timeout,
            )
        except BaseException:
            _shutdown_ts_server_locked(force=True)
            raise
        if write_failed or not response_line:
            _shutdown_ts_server_locked(force=True)
            if 'ERR_MODULE_NOT_FOUND' in stderr_text and "'typescript'" in stderr_text:
                raise ValueError(NPM_DEV_DEPENDENCIES_MISSING)
            raise ValueError('TypeScript production parser failed; run npm ci --ignore-scripts and check the Node runtime')
        try:
            parsed = json.loads(response_line.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _shutdown_ts_server_locked(force=True)
            raise ValueError('TypeScript production parser failed; run npm ci --ignore-scripts and check the Node runtime') from exc
    finally:
        _TS_SERVER_LOCK.release()
    if not isinstance(parsed, dict) or not parsed.get('ok'):
        error = parsed.get('error') if isinstance(parsed, dict) else None
        _raise_syntax_error(error, ts_paths)
        raise ValueError('TypeScript production parser failed; run npm ci --ignore-scripts and check the Node runtime')
    return parsed.get('result')


def _invoke_typescript_scan(
    root: Path, payload: dict[str, Any], ts_paths: set[str],
    *, timeout: float | None = None,
) -> Any:
    """Invoke the Node TypeScript AST scanner with one bounded error boundary."""
    node = shutil.which('node')
    if node is None:
        raise ValueError('TypeScript semantic scan needs Node.js on PATH; see the Node.js requirement in CONTRIBUTING.md')
    effective_timeout = _DEFAULT_TS_SCAN_TIMEOUT_SECONDS if timeout is None else timeout
    script_path = root / 'scripts/semantic_production_scan.mjs'
    if (os.name == 'posix' and shutil.which is _DEFAULT_WHICH and subprocess.run is _DEFAULT_SUBPROCESS_RUN
            and script_path.resolve() == _REPO_SCAN_SCRIPT.resolve()):
        return _invoke_typescript_via_server(node, script_path, payload, ts_paths, timeout=effective_timeout)
    completed = subprocess.run(
        [node, str(script_path)],
        input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", timeout=effective_timeout, check=False,
    )
    if completed.returncode:
        # Accept only a bounded location from the parser, never echo source
        # text or arbitrary subprocess stderr into public diagnostics.
        try:
            failure = json.loads(completed.stdout)
        except json.JSONDecodeError:
            failure = None
        error = failure.get('error') if isinstance(failure, dict) else None
        _raise_syntax_error(error, ts_paths)
        if 'ERR_MODULE_NOT_FOUND' in completed.stderr and "'typescript'" in completed.stderr:
            raise ValueError(NPM_DEV_DEPENDENCIES_MISSING)
        raise ValueError('TypeScript production parser failed; run npm ci --ignore-scripts and check the Node runtime')
    return json.loads(completed.stdout)


def run_typescript_scan(
    root: Path, ts_sources: list[SourceFile], request: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run repository semantic AST analysis with one bounded error boundary."""
    if not ts_sources:
        return []
    payload = {**request, 'sources': [{'path': s.path, 'text': s.text} for s in ts_sources]}
    result = _invoke_typescript_scan(root, payload, {s.path for s in ts_sources})
    if not isinstance(result, list):
        raise ValueError('TypeScript production parser returned malformed scan payload')
    return result


def run_typescript_batch_scan(
    root: Path, ts_sources: Sequence[SourceFile], requests: Sequence[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Run multiple TS AST queries in a single Node process, parsing each file once."""
    if not ts_sources or not requests:
        return {str(req['id']): [] for req in requests}
    payload = {
        'sources': [{'path': s.path, 'text': s.text} for s in ts_sources],
        'requests': list(requests),
    }
    parsed = _invoke_typescript_scan(root, payload, {s.path for s in ts_sources})
    results = parsed.get('results') if isinstance(parsed, dict) else None
    if not isinstance(results, dict):
        raise ValueError('TypeScript production parser returned malformed batch payload')
    return results


def submit_typescript_batch_scan(
    root: Path, ts_sources: Sequence[SourceFile], requests: Sequence[dict[str, Any]],
) -> Callable[[], dict[str, list[dict[str, Any]]]]:
    """Start a batched Node TS AST scan asynchronously and return a join callable."""
    if not ts_sources or not requests:
        empty: dict[str, list[dict[str, Any]]] = {str(req['id']): [] for req in requests}
        return lambda: empty
    if shutil.which('node') is None:
        raise ValueError('TypeScript semantic scan needs Node.js on PATH; see the Node.js requirement in CONTRIBUTING.md')
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(run_typescript_batch_scan, root, ts_sources, requests)

    def _finish() -> dict[str, list[dict[str, Any]]]:
        try:
            return future.result()
        finally:
            executor.shutdown(wait=True)

    return _finish


def collect_literal_uses(
    root: Path,
    field: str,
    sources: list[SourceFile],
    *,
    ts_rows: Sequence[dict[str, Any]] | None = None,
) -> dict[str, set[str]]:
    """Observe literal field writes/dispatch, with a deliberately bounded grammar."""
    from .python_production import python_literal_uses

    observed: dict[str, set[str]] = {}
    for source in sources:
        if source.suffix == '.py':
            for value in python_literal_uses(source, field):
                observed.setdefault(value, set()).add(source.path)
    ts_prod = (
        normalize_typescript_production_rows(ts_rows)
        if ts_rows is not None
        else _typescript_scan(root, [s for s in sources if s.suffix == '.ts'], field, [], 'literal_uses')
    )
    for row in ts_prod:
        for value in row.values:
            observed.setdefault(value, set()).add(row.site.split('::')[0])
    return observed


def validate_production(
    name: str, vocabulary: dict[str, Any], rows: list[Production],
    *, field_domain: set[str] | None = None,
) -> list[str]:
    """F1/F2 checks on observed results; owner members do not establish liveness.

    Unresolved sites are returned explicitly. Their unknown portion supplies
    no value evidence; known output alternatives still count for closedness
    and liveness.
    """
    outputs = [row for row in rows if row.form != 'keyword_unproved']
    observed = set().union(*(row.values for row in outputs))
    expected = set(vocabulary['values'])
    # A composed field domain never widens a canonical decision function's
    # return type, and union arms cannot supply the owner's liveness evidence.
    writes = {'assignment', 'dict', 'keyword', 'keyword_unproved', 'object'}
    unregistered = set().union(*(
        row.values - (field_domain if field_domain is not None and row.form in writes else expected)
        for row in rows
    ))
    if unregistered:
        sites = sorted({f'{r.site}:{r.line}' for r in rows if r.values & unregistered})
        role = 'producer writes' if any(r.values & unregistered for r in outputs) else 'field argument carries'
        raise ValueError(f'{name}: {role} unregistered values {sorted(unregistered)} at {sites}')
    compatibility = set(vocabulary.get('compatibility_only', {}))
    if compatibility - expected:
        raise ValueError(f'{name}: compatibility-only values must be registered')
    if compatibility & observed:
        raise ValueError(f'{name}: compatibility-only values are produced: {sorted(compatibility & observed)}')
    missing = expected - observed - compatibility
    if missing:
        raise ValueError(f'{name}: values have no observed producer: {sorted(missing)}; owner definition is not production')
    producers = vocabulary.get('producers', [])
    declared = set(producers)
    if len(declared) != len(producers):
        raise ValueError(f'{name}: producer sites repeat')
    returns = set(vocabulary.get('return_producers', []))
    if not returns <= declared:
        raise ValueError(f'{name}: return producers must also be registered producers')
    stale = sorted(declared - {row.site for row in outputs})
    if stale:
        raise ValueError(f'{name}: producer sites have no observed write or return: {stale}')
    undeclared = sorted({row.site for row in outputs if row.values and row.site not in declared})
    if undeclared:
        raise ValueError(f'{name}: undeclared producer sites: {undeclared}')
    # The label says why the unknown stayed unknown, so the count is actionable:
    # `argument_name_only` and `annotation_only` can never become evidence.
    return sorted({f'{row.site}:{row.line} [{row.blocker or "other"}]'
                   for row in rows if row.unresolved})


def probe_turn_result_input_domain(vocabulary: dict[str, Any]) -> list[Production]:
    """Witness this real decoder's finite output domain, not the host's traces.

    A successful call is evidence that the production function can emit a value
    for a legal input. Merely enumerating the owner is not such evidence. The
    callable is fixed in code; registry data cannot select arbitrary imports.
    """
    from loopx.control_plane.turn_driver.transaction import LoopXTurnResultKind, _result_kind

    site = 'loopx/control_plane/turn_driver/transaction.py::_result_kind'
    if vocabulary.get('input_producer') != site:
        raise ValueError('turn_result_kind: input_producer must name the anchored decoder')
    rows = []
    for value in vocabulary['values']:
        errors: list[str] = []
        actual = _result_kind(value, errors)
        if errors or not isinstance(actual, LoopXTurnResultKind) or actual.value != value:
            raise ValueError(f'turn_result_kind: decoder does not produce registered input {value}')
        rows.append(Production(site, _result_kind.__code__.co_firstlineno, 'input_witness', frozenset({actual.value}), False))
    for invalid in (None, '', 'unknown_result_kind', 3, [], {}):
        errors = []
        actual = _result_kind(invalid, errors)
        if actual is not None or not errors:
            raise ValueError('turn_result_kind: decoder accepted an invalid input probe')
    return rows


def probe_settlement_binding_production(vocabulary: dict[str, Any]) -> list[Production]:
    """Witness the real settlement identity builder's binding-kind domain.

    This executes the shipped TypeScript builder and reports what it returns,
    then re-checks the same inputs through the Python bridge that reaches it.
    Enumerating the owner would prove nothing about which values a legal input
    can actually produce; a successful call does.

    The module and the function are fixed here rather than named by registry
    data, so the registry cannot select an arbitrary callable. Exercise each
    binding, the unbound input and every conflicting combination.
    """
    from pathlib import Path as _Path

    from ..control_plane.effect_program import SettlementBindingKind, SettlementIdentity

    site = 'loopx/control_plane/effect_program.ts::settlementIdentity'
    if vocabulary.get('input_producer') != site:
        raise ValueError('settlement_binding_kind: input_producer must name the anchored builder')

    expected = {
        'todo': {'todo_id': 't1'},
        'autonomous_replan': {'replan_obligation_id': 'r1'},
        'request': {'request_subject': {
            'request_id': 'a' * 64, 'request_revision': 1,
            'request_digest': 'b' * 64, 'execution_scope_sha256': 'c' * 64,
        }},
        'unbound': {},
    }
    if set(expected) != set(vocabulary['values']):
        raise ValueError('settlement_binding_kind: witness does not cover the registered values')

    root = _Path(__file__).resolve().parents[2]
    probes = [expected[value] for value in vocabulary['values']]
    from itertools import combinations
    bindings = [expected[key] for key in ('todo', 'autonomous_replan', 'request')]
    conflicts = [dict(item for binding in group for item in binding.items())
                 for count in (2, 3) for group in combinations(bindings, count)]
    probes.extend(conflicts)
    completed = subprocess.run(
        ['node', '--no-warnings', '--experimental-strip-types', *node_typescript_flags(),
         str(root / 'scripts/settlement_binding_witness.mts')],
        input=json.dumps({'probes': probes}), capture_output=True, text=True,
        encoding='utf-8', check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f'settlement_binding_kind: witness probe failed: {completed.stderr.strip()[:300]}')
    observed = json.loads(completed.stdout)

    rows: list[Production] = []
    for value, result in zip(vocabulary['values'], observed, strict=False):
        if not result.get('ok') or result.get('binding_kind') != value:
            raise ValueError(
                f'settlement_binding_kind: builder does not produce registered value {value}')
        # The same input through the Python bridge has to agree, so a broken
        # adapter is a failure rather than an unobserved difference.
        bridged = SettlementIdentity(
            goal_id='g', agent_id='a', turn_instance_id='t',
            todo_id=expected[value].get('todo_id'),
            replan_obligation_id=expected[value].get('replan_obligation_id'),
            request_subject=expected[value].get('request_subject'),
        )
        if bridged.binding_kind is not SettlementBindingKind(value):
            raise ValueError(
                f'settlement_binding_kind: the Python bridge disagrees for {value}')
        rows.append(Production(site, SETTLEMENT_IDENTITY_LINE, 'input_witness',
                               frozenset({value}), False))

    if any(result.get('ok') for result in observed[len(expected):]):
        raise ValueError('settlement_binding_kind: builder accepted conflicting bindings')
    return rows


def _probe_controller_domain(vocabulary: dict[str, Any]) -> list[Production]:
    from .turn_contract_witness import probe_controller_production, probe_projection_production
    return probe_controller_production() + probe_projection_production()


def probe_quota_run_decision_production(vocabulary: dict[str, Any]) -> list[Production]:
    """Adapt the fixed TypeScript classifier proof to the existing CI collector."""
    if vocabulary.get('input_producer') != QUOTA_RUN_DECISION_SITE:
        raise ValueError('effective_action: quota witness must name its anchored classifier')
    root = Path(__file__).resolve().parents[2]
    try:
        completed = subprocess.run(
            ['node', '--no-warnings', '--experimental-strip-types', *node_typescript_flags(),
             str(root / 'scripts/quota_run_decision_witness.mts')],
            input=json.dumps({'values': vocabulary['values']}), capture_output=True,
            text=True, encoding='utf-8', check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('effective_action: quota native witness is unavailable') from error
    if completed.returncode:
        raise ValueError(f'effective_action: quota native witness failed: {completed.stderr[:300]}')
    try:
        observed = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError('effective_action: quota witness returned invalid JSON') from error
    if (not isinstance(observed, dict) or observed.get('site') != QUOTA_RUN_DECISION_SITE
            or type(observed.get('line')) is not int or observed['line'] < 1
            or not isinstance(observed.get('actions'), list) or not observed['actions']
            or any(not isinstance(action, str) or action not in vocabulary['values']
                   for action in observed['actions'])):
        raise ValueError('effective_action: quota witness returned invalid production evidence')
    return [Production(QUOTA_RUN_DECISION_SITE, observed['line'], 'input_witness',
                       frozenset({action}), False) for action in observed['actions']]


# Executable input witnesses are fixed in code and selected only by the
# registered ``input_producer`` site; registry data cannot import a callable.
# Line of ``settlementIdentity`` in the tracked builder, so the witness row
# points at the function it executed.
SETTLEMENT_IDENTITY_LINE = 408
QUOTA_RUN_DECISION_SITE = 'loopx/control_plane/quota/run_decision.ts::resolveQuotaRunDecision'

def _probe_turn_routes(vocabulary: dict[str, Any]) -> list[Production]:
    from .turn_contract_witness import probe_route_production
    return probe_route_production()


INPUT_WITNESSES: dict[str, Callable[[dict[str, Any]], list[Production]]] = {
    'loopx/control_plane/turn_driver/driver.py::_typed_route': _probe_turn_routes,
    QUOTA_RUN_DECISION_SITE: probe_quota_run_decision_production,
    'loopx/control_plane/turn_driver/transaction.py::_result_kind': probe_turn_result_input_domain,
    'loopx/control_plane/turn_driver/loop_controller.py::decide_loop_disposition': _probe_controller_domain,
    'loopx/control_plane/effect_program.ts::settlementIdentity': probe_settlement_binding_production,
}
