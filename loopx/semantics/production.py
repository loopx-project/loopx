"""Collect and check bounded semantic production evidence for repository CI."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
from typing import Any, Callable

from .inventory import SourceFile
from .python_production import Production, enum_members, scan_python_production


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


def collect_production(root: Path, vocabulary: dict[str, Any], sources: list[SourceFile]) -> list[Production]:
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
    for source in selected:
        if source.suffix == '.py':
            names = frozenset(site.split('::')[1] for site in returns if site.split('::')[0] == source.path)
            paths = {site.split('::')[1]: tuple(path) for site, path in return_paths.items()
                     if site.split('::')[0] == source.path}
            rows.extend(scan_python_production(source, field=field, enums=enums, return_functions=names,
                                              return_paths=paths, call_arguments=calls, modules=by_path))
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


def _typescript_scan(
    root: Path, ts_sources: list[SourceFile], field: str | None,
    returns: list[str], mode: str = 'production',
) -> list[Production]:
    if not ts_sources or not field:
        return []
    rows = run_typescript_scan(root, ts_sources, {'field': field, 'return_functions': returns, 'mode': mode})
    # The parser names the same blocker vocabulary as the Python scanner;
    # ``typescript_dynamic`` stays the fallback for a form it cannot classify.
    return [Production(r['site'], r['line'], r['form'], frozenset(r['values']), r['unresolved'],
                       (r.get('blocker') or 'typescript_dynamic') if r['unresolved'] else None)
            for r in rows]


def run_typescript_scan(
    root: Path, ts_sources: list[SourceFile], request: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run repository semantic AST analysis with one bounded error boundary."""
    if not ts_sources:
        return []
    completed = subprocess.run(
        ['node', str(root / 'scripts/semantic_production_scan.mjs')],
        input=json.dumps({**request, 'sources': [{'path': s.path, 'text': s.text} for s in ts_sources]}),
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    if completed.returncode:
        # Accept only a bounded location from the parser, never echo source
        # text or arbitrary subprocess stderr into public diagnostics.
        try:
            failure = json.loads(completed.stdout)
        except json.JSONDecodeError:
            failure = None
        error = failure.get('error') if isinstance(failure, dict) else None
        if (isinstance(error, dict) and error.get('code') == 'typescript_syntax'
                and error.get('path') in {s.path for s in ts_sources}
                and isinstance(error.get('line'), int) and error['line'] > 0):
            raise ValueError(f"{error['path']}:{error['line']}: invalid TypeScript source; repair syntax before semantic scanning")
        raise ValueError('TypeScript production parser failed; run npm ci --ignore-scripts and check the Node runtime')
    return json.loads(completed.stdout)


def collect_literal_uses(root: Path, field: str, sources: list[SourceFile]) -> dict[str, set[str]]:
    """Observe literal field writes/dispatch, with a deliberately bounded grammar."""
    from .python_production import python_literal_uses

    observed: dict[str, set[str]] = {}
    for source in sources:
        if source.suffix == '.py':
            for value in python_literal_uses(source, field):
                observed.setdefault(value, set()).add(source.path)
    for row in _typescript_scan(root, [s for s in sources if s.suffix == '.ts'], field, [], 'literal_uses'):
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
    and liveness. This function does not claim whole-program closedness.
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
    data, so the registry cannot select an arbitrary callable. The four cases
    are the builder's whole binding domain: a todo binding, a replan binding,
    neither, and the pair it must refuse.
    """
    from pathlib import Path as _Path

    from ..control_plane.effect_program import SettlementBindingKind, SettlementIdentity

    site = 'loopx/control_plane/effect_program.ts::settlementIdentity'
    if vocabulary.get('input_producer') != site:
        raise ValueError('settlement_binding_kind: input_producer must name the anchored builder')

    expected = {
        'todo': {'todo_id': 't1'},
        'autonomous_replan': {'replan_obligation_id': 'r1'},
        'unbound': {},
    }
    if set(expected) != set(vocabulary['values']):
        raise ValueError('settlement_binding_kind: witness does not cover the registered values')

    root = _Path(__file__).resolve().parents[2]
    probes = [expected[value] for value in vocabulary['values']]
    probes.append({'todo_id': 't1', 'replan_obligation_id': 'r1'})
    completed = subprocess.run(
        ['node', '--no-warnings', '--experimental-strip-types',
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
        )
        if bridged.binding_kind is not SettlementBindingKind(value):
            raise ValueError(
                f'settlement_binding_kind: the Python bridge disagrees for {value}')
        rows.append(Production(site, SETTLEMENT_IDENTITY_LINE, 'input_witness',
                               frozenset({value}), False))

    refused = observed[-1]
    if refused.get('ok'):
        raise ValueError('settlement_binding_kind: builder accepted both bindings at once')
    return rows


def _probe_controller_domain(vocabulary: dict[str, Any]) -> list[Production]:
    from .turn_contract_witness import probe_controller_production, probe_projection_production
    return probe_controller_production() + probe_projection_production()


# Executable input witnesses are fixed in code and selected only by the
# registered ``input_producer`` site; registry data cannot import a callable.
# Line of ``settlementIdentity`` in the tracked builder, so the witness row
# points at the function it executed.
SETTLEMENT_IDENTITY_LINE = 408

INPUT_WITNESSES: dict[str, Callable[[dict[str, Any]], list[Production]]] = {
    'loopx/control_plane/turn_driver/transaction.py::_result_kind': probe_turn_result_input_domain,
    'loopx/control_plane/turn_driver/loop_controller.py::decide_loop_disposition': _probe_controller_domain,
    'loopx/control_plane/effect_program.ts::settlementIdentity': probe_settlement_binding_production,
}
