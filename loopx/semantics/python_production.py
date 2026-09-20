"""Bounded Python production evidence; never execute inspected source.

Known values are syntactic result possibilities, not proof of reachable traces.
Unresolved expressions retain their source locations. Owner definitions alone,
comparison operands, comments and quoted examples are not production evidence.
"""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Mapping, TypeVar

from .inventory import SourceFile


@dataclass(frozen=True)
class Production:
    site: str
    line: int
    form: str
    values: frozenset[str]
    unresolved: bool
    blocker: str | None = None
    """Why the unknown portion stayed unknown; ``None`` when fully resolved.

    ``argument_name_only`` a field-named keyword argument, which never proves an
    output role. ``unstable_local`` a parameter, an unordered rebinding or a
    shadowed name. ``call_result`` the value comes back from a call this scan
    cannot bind. ``dynamic_key`` a computed or non-literal subscript.
    ``serialized_value`` a string where an enum object was required.
    ``annotation_only`` a bare annotation that declares the field without a
    value. ``attribute_read`` an attribute of an unresolved object.
    ``typescript_dynamic`` a TypeScript form the parser cannot classify further;
    every other label is shared by both runtimes, so one residue taxonomy
    covers them. ``other`` anything else; it keeps the site visible.
    """


def _module(path: str) -> str:
    name = path.removesuffix('.py').replace('/', '.')
    return name.removesuffix('.__init__')


def _import_module(path: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ''
    package = _module(path) if path.endswith('/__init__.py') else _module(path).rpartition('.')[0]
    parts = package.split('.')
    return '.'.join(parts[:len(parts) - node.level + 1] + ([node.module] if node.module else []))


_TREES: dict[tuple[str, int], ast.Module] = {}


def _parsed(source: SourceFile) -> ast.Module:
    key = (source.path, hash(source.text))
    tree = _TREES.get(key)
    if tree is None:
        tree = _TREES[key] = ast.parse(source.text, filename=source.path)
    return tree


def _tracked_module(module: str, modules: Mapping[str, SourceFile]) -> SourceFile | None:
    base = module.replace('.', '/')
    return modules.get(f'{base}.py') or modules.get(f'{base}/__init__.py')


def _reexported(source: SourceFile, symbol: str, imports: Mapping[tuple[str, str], _Binding]) -> _Binding | None:
    """One hop only: ``source`` imports ``symbol`` unrenamed from its owner and never rebinds it.

    ``import X as X`` counts as unrenamed. A renamed import, a second hop through
    another module, a local class or assignment of the same name, or a later
    ``import`` of that name leaves the symbol unbound, so the consumer stays unknown.
    """
    value = None
    for node in _parsed(source).body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == symbol:
                    unrenamed = alias.asname in (None, alias.name)
                    value = imports.get((_import_module(source.path, node), symbol)) if unrenamed else None
        elif isinstance(node, ast.Import):
            if any((alias.asname or alias.name.split('.')[0]) == symbol for alias in node.names):
                value = None
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == symbol for t in targets):
                value = None
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            value = None
    return value


def enum_members(source: SourceFile, symbol: str, *, strict: bool = False) -> dict[str, str]:
    """Extract literal members, with fail-closed generation as an explicit mode.

    Inventory/observation may inspect a bounded subset. Generation must account
    for every declaration without executing source or inferring enum aliases
    from iteration (which omits aliases present in ``__members__``).
    """
    tree = ast.parse(source.text, filename=source.path)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == symbol]
    if len(classes) != 1:
        raise ValueError(f'{source.path}::{symbol}: expected one owner class')
    result: dict[str, str] = {}
    for node in classes[0].body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        if strict:
            if isinstance(node, ast.Pass) or (isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                continue
            names = [n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
            name = target.id if isinstance(target, ast.Name) else ','.join(names) or getattr(node, 'name', symbol)
            prefix = f'{source.path}:{node.lineno}: member {name}'
            if (not isinstance(target, ast.Name) or target.id.startswith('__')
                    or (target.id.startswith('_') and target.id.endswith('_'))):
                raise ValueError(f'{prefix}: unsupported owner declaration')
            if target.id in result:
                raise ValueError(f'{prefix}: duplicate member declaration')
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                raise ValueError(f'{prefix}: expected a literal string; computed members and aliases are unsupported')
            if value.value in result.values():
                original = next(k for k, v in result.items() if v == value.value)
                raise ValueError(f'{prefix}: aliases member {original}; owner and registry differ (aliases unsupported)')
        if (isinstance(target, ast.Name) and isinstance(value, ast.Constant)
                and isinstance(value.value, str)):
            result[target.id] = value.value
    return result


def python_literal_uses(source: SourceFile, field: str) -> set[str]:
    """Literal writes and direct dispatch operands; not alias/data-flow proof."""
    tree = ast.parse(source.text, filename=source.path)

    def reads(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id == field
        if isinstance(node, ast.Attribute):
            return node.attr == field
        if isinstance(node, ast.Subscript):
            return isinstance(node.slice, ast.Constant) and node.slice.value == field
        if isinstance(node, ast.BoolOp):
            # Preserve the common neutral fallback without attributing a
            # different field selected by and/or to this action slot.
            return (isinstance(node.op, ast.Or) and reads(node.values[0])
                    and all(isinstance(value, ast.Constant) and value.value in ('', None)
                            for value in node.values[1:]))
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == 'str' and len(node.args) == 1:
                return reads(node.args[0])
            return (isinstance(node.func, ast.Attribute) and node.func.attr == 'get'
                    and bool(node.args) and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == field)
        return False

    def literals(node: ast.AST) -> set[str]:
        if isinstance(node, ast.Constant):
            return {node.value} if isinstance(node.value, str) and node.value else set()
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return set().union(*(literals(item) for item in node.elts))
        if isinstance(node, ast.MatchValue):
            return literals(node.value)
        if isinstance(node, ast.MatchOr):
            return set().union(*(literals(pattern) for pattern in node.patterns))
        return set()

    # Production collection already separates conditional results from their
    # conditions and does not inspect strings containing sample source text.
    rows = scan_python_production(source, field=field, enums={})
    result = set().union(*(row.values for row in rows))
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for left, operator, right in zip(
                [node.left, *node.comparators[:-1]], node.ops, node.comparators, strict=True,
            ):
                if isinstance(operator, (ast.Eq, ast.NotEq, ast.Is, ast.IsNot, ast.In, ast.NotIn)) and reads(left):
                    result.update(literals(right))
                if isinstance(operator, (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)) and reads(right):
                    result.update(literals(left))
        elif isinstance(node, ast.Match) and reads(node.subject):
            for case in node.cases:
                result.update(literals(case.pattern))
    return result


_Binding = TypeVar('_Binding')


def _qualified_bindings(source: SourceFile, tree: ast.Module, owners: Mapping[str, _Binding],
                        modules: Mapping[str, SourceFile] | None = None) -> dict[str, _Binding]:
    bindings = {owner.split('::')[1]: value for owner, value in owners.items()
                if owner.split('::')[0] == source.path}
    imports = {(_module(owner.split('::')[0]), owner.split('::')[1]): value
               for owner, value in owners.items()}
    symbols = {symbol for _, symbol in imports}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = _import_module(source.path, node)
            for alias in node.names:
                name = alias.asname or alias.name
                value = imports.get((module, alias.name))
                if value is None and modules is not None and alias.name in symbols:
                    # One unrenamed re-export hop through a tracked module binds the
                    # same owner; only names that are owner symbols are followed.
                    target = _tracked_module(module, modules)
                    if target is not None and target.path != source.path:
                        value = _reexported(target, alias.name, imports)
                if value is not None:
                    bindings[name] = value
                else:
                    bindings.pop(name, None)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bindings.pop(alias.asname or alias.name.split('.')[0], None)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings.pop(target.id, None)
    return bindings


def _is_generator(node: ast.FunctionDef) -> bool:
    """Whether this ``def`` itself yields, not whether it contains one that does.

    ``ast.walk`` descends into nested functions and lambdas, so a plain function
    that merely defines a generator inside itself read as a generator and lost
    its binding. The direction was safe -- the call kept ``call_result`` -- but
    it withheld evidence this slice exists to make actionable, and it did not
    match what the docstring says is excluded. A nested scope owns its own
    yields, so the walk stops at one.
    """
    pending: list[ast.AST] = list(node.body)
    while pending:
        current = pending.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(current, (ast.Yield, ast.YieldFrom)):
            return True
        pending.extend(ast.iter_child_nodes(current))
    return False


# Keyed the same way ``_TREES`` is, by path and text hash, because the table is
# derived from the module body alone and is the same for every vocabulary
# scanned over that file. Keying it on ``id(tree)`` made its correctness depend
# on ``_TREES`` never evicting: give that cache a bound and a reused id would
# hand back another file's functions, binding a call to the wrong callee with
# no symptom.
_MODULE_FUNCTIONS: dict[tuple[str, int], dict[str, ast.FunctionDef]] = {}


def _enclosing_scope(tree: ast.Module, target: ast.Global) -> ast.AST:
    """The function body that owns this ``global`` statement, else the module."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            for child in ast.walk(node):
                if child is target:
                    return node
    return tree


def _stores_name(scope: ast.AST, name: str) -> bool:
    """True when ``scope`` assigns, augments or deletes ``name``."""
    for child in ast.walk(scope):
        if isinstance(child, ast.Name) and child.id == name and isinstance(child.ctx, (ast.Store, ast.Del)):
            return True
    return False


def _module_functions(source: SourceFile, tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """Top-level plain ``def``s a same-module call may be bound to.

    A decorator can replace the returned object, ``async def`` hands back a
    coroutine rather than the value, and a generator yields instead of
    returning, so none of those is a recognized producer. Any second top-level
    binding of the name -- a redefinition, class, import, assignment or
    ``del`` -- leaves the name unproven and the call keeps ``call_result``.
    """
    key = (source.path, hash(source.text))
    known = _MODULE_FUNCTIONS.get(key)
    if known is not None:
        return known
    bound: Counter[str] = Counter()
    defined: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound[node.name] += 1
            if isinstance(node, ast.FunctionDef):
                defined[node.name] = node
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                bound[child.id] += 1
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    bound[alias.asname or alias.name.split('.')[0]] += 1
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound[child.name] += 1
    # A ``global`` rebinding lives inside a function body, which the loop above
    # never enters, so the module-level name it replaces looked untouched. Any
    # name some scope declares ``global`` and then stores is no longer reliably
    # the ``def`` above; binding a call to that ``def`` would report the original
    # body's returns for a function the module can swap at runtime.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Global):
            continue
        scope = _enclosing_scope(tree, node)
        for name in node.names:
            if name in defined and _stores_name(scope, name):
                bound[name] += 1
    functions = {name: node for name, node in defined.items()
                 if bound[name] == 1 and not node.decorator_list and not _is_generator(node)}
    _MODULE_FUNCTIONS[key] = functions
    return functions


def _index_value(node: ast.AST) -> str | int | None:
    if isinstance(node, ast.Constant) and type(node.value) in (str, int):
        return node.value
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant) and type(node.operand.value) is int):
        return -node.operand.value
    return None


def _union(values: list[ast.AST]) -> ast.AST:
    """Fold several definitions of one local into a finite selection node.

    The scan reports syntactic result possibilities, so a name written more than
    once carries the union of the writes that precede the read. The synthetic
    test is never inspected; only the arms are resolved.
    """
    node = values[0]
    for other in values[1:]:
        test = ast.copy_location(ast.Constant(value=True), other)
        node = ast.copy_location(ast.IfExp(test=test, body=node, orelse=other), other)
    return node


def scan_python_production(
    source: SourceFile,
    *,
    field: str | None,
    enums: Mapping[str, Mapping[str, str]],
    return_functions: frozenset[str] = frozenset(),
    return_paths: Mapping[str, tuple[str | int, ...]] | None = None,
    call_arguments: Mapping[str, Mapping[str, int | None]] | None = None,
    modules: Mapping[str, SourceFile] | None = None,
) -> list[Production]:
    """Observe writes and owner-member results with bounded local resolution.

    ``enums`` maps module::Class to literal member values from tracked owners.
    Only imported owner classes (including aliases) or the local owner qualify;
    ``modules`` additionally lets one unrenamed re-export hop through a tracked
    module bind the owner. Longer chains and renamed re-exports stay unknown.
    Local aliases and complete branch selections resolve only at output sites.
    Explicit call metadata names only reviewed builder arguments; arbitrary calls
    are consumers. Nested function returns belong to that function, not a
    registered enclosure.

    Three bounded local forms are recognized beyond a single straight-line
    binding. A local written more than once resolves to the union of the writes
    that textually precede the read, provided every store of that name is a
    plain ``name = expression`` (loop, ``with``, ``except``, walrus, augmented,
    unpacking, ``global`` and ``del`` rebindings are not ordered by this scan and
    stay unknown) **and** no write shares an enclosing loop with the read.
    Textual position is execution order only where no back edge crosses it: a
    write later in a loop body reaches the read at the top of the next
    iteration, so such a name is not a finite selection and stays unknown. A
    local container mutated only through direct literal-key subscript writes
    keeps its untouched keys, and a written key carries the union of its
    initializer and every write; an alias, a method call, a deeper or computed
    store, a negative index (which names a slot whose number depends on the
    container's length), or passing the container to any call still discards it.
    A call to an undecorated, non-generator, plainly-defined top-level function
    of the same module resolves to the union of that function's own returns.
    Arguments are never bound to parameters, so a returned parameter stays
    unknown and the result is independent of the call site; recursion, imported
    and attribute calls keep the ``call_result`` blocker.
    """
    # The scan never mutates the tree, so one parse per file serves every
    # vocabulary; synthetic selection nodes are built fresh, never spliced in.
    tree = _parsed(source)
    bindings = _qualified_bindings(source, tree, enums, modules)
    call_arguments = call_arguments or {}
    calls = _qualified_bindings(source, tree, call_arguments, modules)
    return_paths = return_paths or {}
    module_functions = _module_functions(source, tree)
    call_memo: dict[tuple[str, str], tuple[frozenset[str], tuple[str, ...]]] = {}
    environments: dict[tuple[int, str], SimpleNamespace] = {}
    resolving: set[str] = set()

    result: list[Production] = []

    def matches(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id == field
        if isinstance(node, ast.Attribute):
            return node.attr == field
        return (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                and node.slice.value == field)

    def environment(body: list[ast.stmt], scope: str, parameters: set[str],
                    call_shadows: frozenset[str] = frozenset(),
                    own: frozenset[str] = frozenset()) -> SimpleNamespace:
        """Build one scope's bounded local view and its resolvers, once."""
        cached = environments.get((id(body), scope))
        if cached is not None:
            return cached
        nodes: list[ast.AST] = []
        nested: list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = []
        # Which loops enclose each node. A write inside a loop reaches a read in
        # the same loop through the back edge, so their textual order says
        # nothing about which value the read sees.
        enclosing_loops: dict[int, frozenset[int]] = {}

        def collect(node: ast.AST, loops: frozenset[int] = frozenset()) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                nested.append(node)
                return
            if isinstance(node, ast.Lambda):
                return
            nodes.append(node)
            enclosing_loops[id(node)] = loops
            if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                loops = loops | {id(node)}
            for child in ast.iter_child_nodes(node):
                collect(child, loops)
        for statement in body:
            collect(statement)
        assigned = Counter(n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        # Not every binding reaches the tree as ``Name(Store)``. A ``match``
        # pattern keeps its captured name as a string on the pattern node, and a
        # nested scope can rebind an enclosing name through ``nonlocal``. Both
        # write the name without adding a store the plain-assignment scan can
        # order, so they are counted here: ``definitions`` below keeps a name
        # only while every store of it is one of those recorded writes, and an
        # extra count is exactly what drops an unorderable name out of it.
        for node in nodes:
            if isinstance(node, ast.MatchAs) and node.name:
                assigned[node.name] += 1
            elif isinstance(node, ast.MatchStar) and node.name:
                assigned[node.name] += 1
            elif isinstance(node, ast.MatchMapping) and node.rest:
                assigned[node.rest] += 1
        for child in nested:
            declares: set[str] = set()
            for inner in ast.walk(child):
                if isinstance(inner, (ast.Global, ast.Nonlocal)):
                    declares.update(inner.names)
            if not declares:
                continue
            for inner in ast.walk(child):
                if isinstance(inner, ast.Name) and isinstance(inner.ctx, (ast.Store, ast.Del)):
                    if inner.id in declares:
                        assigned[inner.id] += 1
        local_owner_names = {owner.split('::')[1] for owner in enums if owner.split('::')[0] == source.path}
        nested_names = {n.name for n in nested}
        if scope == '<module>':
            nested_names -= local_owner_names | {owner.split('::')[1] for owner in call_arguments
                                                   if owner.split('::')[0] == source.path}
        import_bound = set()
        for node in nodes:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                import_bound.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
        # Two different questions are asked about the same import. At module
        # scope an import is how a producer names the owner module it qualifies
        # against, so it must not shadow that qualified binding -- which is why
        # ``imported`` stays empty here. Whether it takes the name away from a
        # plain assignment to that same name is asked separately, through
        # ``rebound_by_other_forms``, and the answer there is yes in every
        # scope: ``action = "run"`` followed by ``import os as action`` leaves
        # the module object bound, not the literal.
        imported = set() if scope == '<module>' else import_bound
        exception_targets = {n.name for n in nodes if isinstance(n, ast.ExceptHandler) and n.name}
        deleted = {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Del)}
        shadows = set(assigned) | parameters | nested_names | imported | exception_targets | deleted
        # A same-module call binds to a top-level ``def``, so that definition is
        # not itself a shadow; only a rebinding inside this scope or an
        # enclosing one takes the name away from the module function.
        # ``parameters`` also carries every enclosing owner shadow, including the
        # module's own top-level definitions, so only this scope's real bindings
        # may take a name away from the module function it would otherwise name.
        rebinds = set(assigned) | set(own) | imported | exception_targets | deleted
        if scope != '<module>':
            rebinds |= nested_names
        calls_shadowed = frozenset(call_shadows) | rebinds
        local_bindings = {k: v for k, v in bindings.items() if k not in shadows}
        local_calls = {k: v for k, v in calls.items() if k not in shadows}

        plain: dict[str, list[ast.AST]] = {}
        for node in nodes:
            target = value = None
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                target, value = node.targets[0].id, node.value
            elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                    and node.value is not None):
                target, value = node.target.id, node.value
            if target is not None:
                plain.setdefault(target, []).append(value)
        declared = {name for node in nodes if isinstance(node, (ast.Global, ast.Nonlocal))
                    for name in node.names}
        # Every store of the name must be one of those plain writes, so a value
        # this scan cannot order never masquerades as a finite selection.
        # A later ``import as``, ``except as`` or nested ``def``/``class`` takes
        # the name away from the value the plain assignment gave it, so the
        # initializer is no longer the whole story for this scope.
        rebound_by_other_forms = import_bound | exception_targets | nested_names
        definitions = {name: values for name, values in plain.items()
                       if assigned[name] == len(values) and name not in parameters
                       and name not in declared and name not in deleted
                       and name not in rebound_by_other_forms}

        # Resolve only local containers that have not been mutated through an
        # unrecognized path or escaped. A direct literal-key subscript write is
        # recorded against that key; anything else invalidates every alias,
        # rather than turning a stale initializer into false scalar evidence.
        written: dict[str, dict[str | int, list[ast.AST]]] = {}
        recorded: set[int] = set()
        for node in nodes:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                store = node.targets[0]
                # A negative index names the same slot as a non-negative one
                # whose number depends on the container's length, so it cannot
                # be recorded against a key. Leaving it unrecorded sends the
                # container down the existing invalidation path below.
                if (isinstance(store, ast.Subscript) and isinstance(store.value, ast.Name)
                        and not isinstance(store.slice, ast.Slice)
                        and (key := _index_value(store.slice)) is not None
                        and not (type(key) is int and key < 0)):
                    recorded.add(id(store))
                    written.setdefault(store.value.id, {}).setdefault(key, []).append(node.value)
        containers = {name for name, values in definitions.items()
                      if any(isinstance(value, (ast.List, ast.Dict, ast.Set)) for value in values)}
        aliases = [(name, value.id) for name, values in definitions.items()
                   for value in values if isinstance(value, ast.Name)]
        unsafe: set[str] = set()

        def root_name(node: ast.AST) -> str | None:
            while isinstance(node, (ast.Attribute, ast.Subscript)):
                node = node.value
            return node.id if isinstance(node, ast.Name) else None

        for node in nodes:
            if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
                if id(node) in recorded:
                    continue
                if name := root_name(node):
                    unsafe.add(name)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and (name := root_name(node.func)):
                    unsafe.add(name)
                for argument in [*node.args, *(kw.value for kw in node.keywords)]:
                    if isinstance(argument, ast.Name):
                        unsafe.add(argument.id)
        # A second name for the same container would let a write land outside
        # this key map, so an aliased container keeps no key-precise evidence.
        unsafe.update(name for name in written if name in {n for pair in aliases for n in pair})
        for group in (containers, unsafe):
            changed = True
            while changed:
                before = len(group)
                for left, right in aliases:
                    if left in group or right in group:
                        group.update((left, right))
                changed = len(group) != before
        for name in containers & unsafe:
            definitions.pop(name, None)
        for name in unsafe:
            written.pop(name, None)

        blockers: list[str] = []

        def blocked(label: str) -> bool:
            blockers.append(label)
            return True

        def bound(node: ast.AST | None, seen: frozenset[str]) -> tuple[ast.AST | None, frozenset[str]]:
            while isinstance(node, ast.Name) and node.id in definitions and node.id not in seen:
                writes = definitions[node.id]
                # Textual position is execution order only where no back edge
                # crosses it. When a second write shares a loop with the read,
                # the next iteration sees that write and the preceding-writes
                # filter would drop a live value, so the name is not a finite
                # selection and stays unknown.
                if len(writes) > 1 and any(
                    enclosing_loops.get(id(value), frozenset())
                    & enclosing_loops.get(id(node), frozenset())
                    for value in writes
                ):
                    break
                values = [value for value in writes
                          if (value.lineno, value.col_offset) < (node.lineno, node.col_offset)]
                if not values:
                    break
                seen = seen | {node.id}
                node = values[0] if len(values) == 1 else _union(values)
            return node, seen

        def flatten(container: ast.Dict, seen: frozenset[str],
                    depth: int = 0) -> tuple[list[tuple[str | int, ast.AST]], bool] | None:
            """Expand ``**`` spreads of statically known dict literals, in write order.

            A spread whose operand is not a finite selection of dict literals
            with literal keys could overwrite any key, so the whole lookup falls
            back to the unknown-key answer instead of trusting a literal entry.
            """
            if depth > 4:
                return None
            pairs: list[tuple[str | int, ast.AST]] = []
            spread = False
            for key, value in zip(container.keys, container.values, strict=True):
                if key is not None:
                    index = _index_value(key)
                    if index is None:
                        return None
                    pairs.append((index, value))
                    continue
                spread = True
                arms = [value]
                while arms:
                    candidate = arms.pop()
                    # ``bound`` follows plain ``name = expression`` writes only,
                    # so a container mutated afterwards through a subscript still
                    # resolves to its initializer. Spreading it would replay the
                    # stale literal and report a key's original value as the one
                    # produced. The per-key union that ``lookup`` applies is not
                    # reachable here, because a spread contributes every key at
                    # once, so the honest answer is the unknown-key fallback.
                    if isinstance(candidate, ast.Name) and candidate.id in written:
                        return None
                    arm, visited = bound(candidate, seen)
                    if isinstance(arm, ast.IfExp):
                        arms.extend((arm.body, arm.orelse))
                        continue
                    if not isinstance(arm, ast.Dict):
                        return None
                    inner = flatten(arm, visited, depth + 1)
                    if inner is None:
                        return None
                    pairs.extend(inner[0])
            return pairs, spread

        def lookup(container: ast.AST | None, key: str | int | None,
                   seen: frozenset[str] = frozenset(), absent_ok: bool = False) -> tuple[list[ast.AST], bool]:
            # ``absent_ok`` says a recorded write already supplies this key, so an
            # initializer that does not carry it is not an unknown boundary.
            if isinstance(container, (ast.Tuple, ast.List)):
                if type(key) is int:
                    if -len(container.elts) <= key < len(container.elts):
                        return [container.elts[key]], False
                    return [], (False if absent_ok else blocked('dynamic_key'))
                if key is not None:
                    return [], blocked('dynamic_key')
                return list(container.elts), blocked('dynamic_key')
            if isinstance(container, ast.Dict):
                expanded = flatten(container, seen)
                if expanded is None:
                    return list(container.values), blocked('dynamic_key')
                pairs, spread = expanded
                if key is None:
                    return [value for _, value in pairs], blocked('dynamic_key')
                found = [value for index, value in pairs if index == key]
                if not found:
                    return [], (False if absent_ok else blocked('dynamic_key'))
                # Python dict construction keeps the last duplicate key; an
                # optional spread makes each contributor a live possibility.
                return (found if spread else [found[-1]]), False
            return [], blocked('unstable_local' if isinstance(container, ast.Name) else 'other')

        def element(root: str | None, container: ast.AST | None, key: str | int | None,
                    seen: frozenset[str] = frozenset()) -> tuple[list[ast.AST], bool]:
            updates = written.get(root or '')
            extra = [] if not updates else (updates.get(key, []) if key is not None
                                            else [v for values in updates.values() for v in values])
            choices, unknown = lookup(container, key, seen, absent_ok=bool(extra))
            return [*choices, *extra], unknown

        def resolve(node: ast.AST | None, seen: frozenset[str] = frozenset(), *, enum_only: bool = False) -> tuple[set[str], bool]:
            node, seen = bound(node, seen)
            if isinstance(node, ast.Constant):
                if isinstance(node.value, str):
                    return ({node.value} if node.value and not enum_only else set()), False
                return set(), node.value is not None and blocked('other')
            if isinstance(node, ast.Subscript):
                if isinstance(node.slice, ast.Slice) or (isinstance(node.slice, ast.Constant)
                        and type(node.slice.value) not in (str, int)):
                    return set(), blocked('dynamic_key')
                root = node.value.id if isinstance(node.value, ast.Name) else None
                container, visited = bound(node.value, seen)
                choices, unknown = element(root, container, _index_value(node.slice), visited)
                known: set[str] = set()
                for value in choices:
                    part, unresolved = resolve(value, visited, enum_only=enum_only)
                    known.update(part)
                    unknown |= unresolved
                return known, unknown
            if isinstance(node, (ast.IfExp, ast.BoolOp)):
                operands = [node.body, node.orelse] if isinstance(node, ast.IfExp) else node.values
                parts = [resolve(value, seen, enum_only=enum_only) for value in operands]
                return set().union(*(values for values, _ in parts)), any(unknown for _, unknown in parts)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'str' and node.func.id not in shadows
                    and len(node.args) == 1 and not node.keywords):
                # ``str`` is not a transparent pass-through. ``str(None)`` is
                # the text ``"None"``, not "no value", and ``str`` of a
                # ``str``-mixin enum *member* is its qualified name rather than
                # its value, so inheriting the argument's resolution there
                # would report a registered value where the field actually
                # carries an unregistered one. A ``.value`` read, a literal or
                # anything else already carrying text converts to itself.
                argument, _ = bound(node.args[0], seen)
                if isinstance(argument, ast.Constant):
                    text = str(argument.value)
                    return ({text} if text and not enum_only else set()), False
                if (isinstance(argument, ast.Attribute) and argument.attr != 'value'
                        and isinstance(argument.value, ast.Name)
                        and argument.value.id in local_bindings):
                    return set(), blocked('string_conversion')
                return resolve(node.args[0], seen, enum_only=enum_only)
            if isinstance(node, ast.Attribute):
                member = node.value if node.attr == 'value' else node
                if isinstance(member, ast.Attribute) and isinstance(member.value, ast.Name):
                    members = local_bindings.get(member.value.id)
                    if members is not None:
                        if member.attr not in members:
                            raise ValueError(f'{source.path}:{node.lineno}: unknown owner member {member.attr}')
                        return {members[member.attr]}, False
                if node.attr == 'value' and isinstance(node.value, ast.Name):
                    return enum_object_value(node.value, seen)
                return set(), blocked('attribute_read')
            if isinstance(node, ast.Call):
                hit = same_module_call(node, 'enum' if enum_only else 'value')
                return hit if hit is not None else (set(), blocked('call_result'))
            if isinstance(node, ast.Name):
                return set(), blocked('unstable_local')
            if node is None:
                return set(), blocked('other')
            return set(), blocked('other')

        def same_module_call(node: ast.Call, mode: str) -> tuple[set[str], bool] | None:
            if not isinstance(node.func, ast.Name) or node.func.id in calls_shadowed:
                return None
            hit = call_values(node.func.id, mode)
            if hit is None:
                return None
            values, reasons = hit
            for reason in reasons:
                blocked(reason)
            return set(values), bool(reasons)

        def enum_object_value(node: ast.AST, seen: frozenset[str]) -> tuple[set[str], bool]:
            node, seen = bound(node, seen)
            if isinstance(node, ast.IfExp):
                parts = [enum_object_value(value, seen) for value in (node.body, node.orelse)]
                return set().union(*(v for v, _ in parts)), any(u for _, u in parts)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in local_bindings:
                return resolve(node, seen, enum_only=True)
            if isinstance(node, ast.Call):
                # Only a same-module function that returns owner members as enum
                # objects has another ``.value``; a function handing back
                # ``Action.RUN.value`` already returns the serialized string.
                hit = same_module_call(node, 'object')
                if hit is not None and hit[0]:
                    return hit
            # A serialized string (including Action.RUN.value) is not an enum
            # object with another .value attribute.
            return set(), blocked('serialized_value')

        def returned(node: ast.AST | None, path: tuple[str | int, ...], seen: frozenset[str] = frozenset()) -> tuple[set[str], bool]:
            if not path:
                return resolve(node, seen)
            root = node.id if isinstance(node, ast.Name) else None
            node, seen = bound(node, seen)
            choices, unknown = element(root, node, path[0], seen)
            parts = [returned(value, path[1:], seen) for value in choices]
            return set().union(*(v for v, _ in parts)), unknown or any(u for _, u in parts)

        built = SimpleNamespace(nodes=nodes, nested=nested, shadows=shadows, blockers=blockers,
                                calls_shadowed=calls_shadowed, local_calls=local_calls,
                                resolve=resolve, returned=returned, enum_object=enum_object_value)
        environments[(id(body), scope)] = built
        return built

    def call_values(name: str, mode: str) -> tuple[frozenset[str], tuple[str, ...]] | None:
        """Union of one same-module function's own returns, or ``None``.

        ``mode`` asks what the caller needs of each return: its written value,
        only owner members (``enum``), or the member behind an enum object
        (``object``). The last is not the same question as the first: a function
        returning ``Action.RUN.value`` hands back a string that has no further
        ``.value``, so it answers ``object`` with nothing.

        Call arguments are never bound to parameters, so a returned parameter
        stays unknown and the answer does not depend on the call site; it is
        memoised per module scan. A call reached from inside its own callee
        chain keeps the ``call_result`` blocker instead of unrolling recursion.
        A bare ``return`` or a fall-through yields ``None``, which is not a
        vocabulary value: it contributes nothing and blocks nothing.
        """
        target = module_functions.get(name)
        if target is None or name in resolving:
            return None
        key = (name, mode)
        if key not in call_memo:
            resolving.add(name)
            try:
                args = target.args
                params = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
                params.update(a.arg for a in (args.vararg, args.kwarg) if a)
                env = environment(target.body, name, params | module_env.shadows,
                                  module_env.calls_shadowed, frozenset(params))
                values: set[str] = set()
                unknown = False
                start = len(env.blockers)
                for node in env.nodes:
                    if isinstance(node, ast.Return) and node.value is not None:
                        part, missing = (env.enum_object(node.value, frozenset()) if mode == 'object'
                                         else env.resolve(node.value, enum_only=mode == 'enum'))
                        values |= part
                        unknown |= missing
                first = env.blockers[start] if len(env.blockers) > start else 'call_result'
                del env.blockers[start:]
                call_memo[key] = (frozenset(values), (first,) if unknown else ())
            finally:
                resolving.discard(name)
        return call_memo[key]

    def scan_scope(body: list[ast.stmt], scope: str, parameters: set[str],
                   call_shadows: frozenset[str] = frozenset(), own: frozenset[str] = frozenset(),
                   env: SimpleNamespace | None = None) -> None:
        env = env if env is not None else environment(body, scope, parameters, call_shadows, own)
        blockers = env.blockers

        def record(node: ast.AST | None, form: str, location: ast.AST) -> None:
            # Reasons are read back by position: one scope's environment is
            # shared with same-module call resolution, which may nest inside.
            start = len(blockers)
            values, unknown = (env.returned(node, return_paths.get(scope, ()))
                               if form == 'return' else env.resolve(node))
            blocker = blockers[start] if len(blockers) > start else None
            del blockers[start:]
            if node is None and form == 'assignment':
                # A bare annotation declares the field; there is no value to resolve.
                blocker = 'annotation_only'
            if form == 'keyword_unproved':
                # Argument name alone does not prove an output role.
                unknown, blocker = True, 'argument_name_only'
            result.append(Production(f'{source.path}::{scope}', location.lineno, form,
                                     frozenset(values), unknown, blocker if unknown else None))

        for node in env.nodes:
            if isinstance(node, ast.Assign):
                if field and any(matches(t) for t in node.targets):
                    record(node.value, 'assignment', node)
            elif isinstance(node, ast.AnnAssign) and field and matches(node.target):
                record(node.value, 'assignment', node)
            elif isinstance(node, ast.Dict) and field:
                for key, value in zip(node.keys, node.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value == field:
                        record(value, 'dict', node)
            elif isinstance(node, ast.Call):
                output_arguments = env.local_calls.get(node.func.id, {}) if isinstance(node.func, ast.Name) else {}
                for kw in node.keywords:
                    if kw.arg in output_arguments:
                        record(kw.value, 'call_argument', node)
                    elif field and kw.arg == field:
                        record(kw.value, 'keyword_unproved', node)
                for position in output_arguments.values():
                    if position is not None and position < len(node.args):
                        record(node.args[position], 'call_argument', node)
            elif isinstance(node, ast.Return) and node.value is not None:
                if scope in return_functions:
                    record(node.value, 'return', node)
                else:
                    start = len(blockers)
                    values, unknown = env.resolve(node.value, enum_only=True)
                    # An owner-member result keeps its reason too; an unlabelled
                    # unknown would be invisible in the report breakdown.
                    reason = blockers[start] if len(blockers) > start else None
                    del blockers[start:]
                    if values:
                        result.append(Production(f'{source.path}::{scope}', node.lineno, 'enum_result',
                                                 frozenset(values), unknown, reason if unknown else None))
        for child in env.nested:
            name = child.name if scope == '<module>' else f'{scope}.{child.name}'
            params: set[str] = set()
            if not isinstance(child, ast.ClassDef):
                args = child.args
                params = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
                params.update(a.arg for a in (args.vararg, args.kwarg) if a)
            # A nested closure might shadow an owner in any enclosing scope.
            scan_scope(child.body, name, params | env.shadows, env.calls_shadowed, frozenset(params))

    module_env = environment(tree.body, '<module>', set())
    scan_scope(tree.body, '<module>', set(), frozenset(), frozenset(), module_env)
    return sorted(set(result), key=lambda row: (row.site, row.line, row.form, sorted(row.values)))
