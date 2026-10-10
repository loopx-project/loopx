"""Advisory consumer evidence for vocabularies that declare a slot (RFC B5).

``consumer_ranking`` counts modules that *mention* a symbol: a prompt string, a
comment and a dispatch table all count the same. This module answers the next
question with bounded AST evidence -- for a vocabulary that declares where its
value travels, which sites **read** it, which only **pass it through**, which
**interpret** it by branching, and which stay **unknown** with a reason.

**Coverage is the registry's declaration, never a guess.** A vocabulary is
analysed only when it declares ``literal_scan.field``, the slot its value
travels under. Without one there is no slot identity to anchor a read, and
assuming the vocabulary id doubles as a field name would invent the anchor every
row rests on. Those vocabularies are reported by name under
``missing_slot_identity`` and are not analysed at all -- not partially, not by
owner class alone. The report therefore covers whatever subset has done the B2
slot work, and states how large the rest is.

**What this measures is syntactic use, not data flow.** A row says "this
location performs a recognized read of this slot, and the syntax around it
branches on the value / relocates it / does neither". It does not say the value
came from a registered producer or that the branch is reachable. The
``limitation`` column on every row names the gap that applies to that row.

Interpreter and pass-through are consumer subroles, not a partition of modules:
rows are per site and per vocabulary. Anchors are identities the registry
already carries, so no module registers itself as a consumer:

* **slot read** -- a literal-key read of the declared slot:
  ``payload["example_slot"]``, ``payload.get("example_slot")``,
  ``record.example_slot``, ``"example_slot" in payload``.
* **owner member** -- a comparison or ``match`` case against a member of the
  registered owner class, bound through the same one-unrenamed-hop import
  discipline the producer scanner uses, and only where no nearer binding has
  taken that name over.

A role is claimed only from syntax that establishes it. The climb from a read to
its use passes through constructs that relocate or select the value without
applying anything to it, and stops at the first construct it cannot resolve. A
call, an f-string and a further attribute all stop it: none of them show whether
the value is forwarded, converted or branched on inside. Those sites are
``unknown`` with the construct named, because "it went into ``f()``" is not
evidence that it came out unchanged.

Five limits are part of the metric rather than caveats beside it:

* **Only declared slots are covered**, as above.
* **The slot is keyed by name.** The same vocabulary under a different field
  name is not found; an unrelated field of the same name is misattributed.
* **A computed mapping key is unknown, not absent.** ``payload.get(name)`` may
  read any slot, so no name-keyed scan can prove a vocabulary has no reader.
  Those sites are ``unknown`` against every covered vocabulary at once. Computed
  *subscripts* are excluded: ``rows[index]`` and ``payload[key]`` are the same
  syntax, and counting sequence indexing would inflate the unknown until it
  stopped carrying information.
* **Following a local is one hop.** A parameter, a reassigned name, a name that
  escapes into a nested scope, or a second alias hop is ``unknown`` with the
  reason recorded, never a silent drop.
* **TypeScript is not walked.** A tracked ``.ts`` file carrying the slot token
  is one ``unknown`` row rather than an omission from the reach.

Nothing here gates anything. It is advisory evidence printed on demand by
``scripts/generate_semantic_inventory.py --report --consumer-evidence``,
matching F3's ``advisory`` lane, and it must not become a merge gate without the
RFC decision that would license one.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any, Iterable, Mapping

from .inventory import SourceFile
from .python_production import _qualified_bindings, enum_members

# Module-private names on purpose: a module-level ``NAME = (...)`` of string
# literals is itself an inventory carrier, and this module must not add a
# closed-set carrier to the tree it measures.

# The scan reach is code owned, exactly as PRODUCER_ROOTS is. Registry data
# cannot widen it, so a vocabulary cannot buy coverage with a data-only edit.
# These are the representative paths: the control plane where kernel values are
# decided and the CLI surface that renders them.
CONSUMER_SCAN_ROOTS = ('loopx/control_plane', 'loopx/cli_commands')

_ROLES = ('read', 'interpret', 'pass_through', 'unknown')
# Precedence when one anchor supports more than one claim. Interpreting is the
# stronger statement about the value, so a site that branches and also forwards
# is an interpreter; a site that only forwards is a pass-through.
_ROLE_RANK = {'interpret': 3, 'pass_through': 2, 'read': 1}

# Mapping accessors whose first argument names a slot. Unlike a subscript,
# these are unambiguously mapping reads, so a computed argument is real
# evidence of an unresolved read rather than an indexing false positive.
_MAPPING_READS = frozenset({'get', 'pop'})

# Wildcard vocabulary id for a site that may read any covered slot.
ANY_VOCABULARY = '*'

_LIMITATIONS = {
    'read': 'slot_name_keyed: a recognized read of this slot name; no proof the value came from a registered producer',
    'interpret': 'branch_operands_only: the compared literals are syntactic; unmatched values take default paths this scan does not enumerate',
    'pass_through': 'relocated_unchanged: the AST shows the value moved, not what the receiving site does with it',
    'unknown': 'no_classification: recorded so the site stays visible; it is not evidence of absence',
}


@dataclass(frozen=True)
class ConsumerSite:
    """One classified site. ``blocker`` is set only when ``role`` is unknown."""

    vocabulary: str
    site: str
    line: int
    role: str
    anchor: str
    domain: tuple[str, ...]
    observed: tuple[str, ...]
    limitation: str
    blocker: str | None = None


@dataclass(frozen=True)
class ConsumerEvidence:
    """A whole scan: the tree it ran against, the reach and the coverage.

    ``missing_slot_identity`` is the registered vocabularies this scan refused
    to analyse because they declare no slot. It is part of the result, not a
    footnote: the rows below describe ``covered`` only.
    """

    source_sha: str
    worktree_dirty: bool
    scanned_files: int
    tracked_files: int
    skipped_typescript: int
    covered: tuple[str, ...]
    missing_slot_identity: tuple[str, ...]
    rows: tuple[ConsumerSite, ...]

    def counts(self) -> dict[str, int]:
        tally = Counter(row.role for row in self.rows)
        return {role: tally.get(role, 0) for role in _ROLES}

    def unknown_share(self) -> float:
        return (self.counts()['unknown'] / len(self.rows)) if self.rows else 0.0

    def blockers(self) -> dict[str, int]:
        return dict(sorted(Counter(row.blocker for row in self.rows if row.blocker).items()))


def source_revision(repo_root: Path) -> tuple[str, bool]:
    """The commit the scan ran against, and whether the scanned tree differs.

    ``load_sources`` lists paths from the git index but reads their text from
    the working tree, so a row's location is only reproducible at this commit
    when the tree is clean. A dirty tree is reported, never hidden.
    """
    def git(*arguments: str) -> str:
        return subprocess.run(
            ['git', *arguments],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            timeout=30,
        ).stdout.decode('utf-8').strip()

    try:
        sha = git('rev-parse', 'HEAD')
        dirty = bool(git('status', '--porcelain', '--', *CONSUMER_SCAN_ROOTS))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return 'unknown', True
    return sha, dirty


def declared_slot(vocabulary: Mapping[str, Any]) -> str | None:
    """The payload key the registry says this vocabulary travels under.

    ``None`` when the registry declares none. There is deliberately no fallback
    to the vocabulary id: an id is a name for a concept, not evidence that a
    field of that name carries it, and every row this module emits for a slot
    would inherit that assumption.
    """
    return (vocabulary.get('literal_scan') or {}).get('field') or None


def _in_reach(path: str) -> bool:
    return any(path == root or path.startswith(root + '/') for root in CONSUMER_SCAN_ROOTS)


def _owner_members(vocabulary: Mapping[str, Any], by_path: Mapping[str, SourceFile]) -> dict[str, dict[str, str]]:
    owner = (vocabulary.get('owners') or {}).get('python')
    if not owner or '::' not in owner:
        return {}
    module, symbol = owner.split('::')
    if module not in by_path or by_path[module].suffix != '.py':
        return {}
    try:
        return {owner: enum_members(by_path[module], symbol)}
    except ValueError:
        # An owner this scan cannot read is not an excuse to claim coverage.
        return {}


def _parents(nodes: Iterable[ast.AST]) -> dict[int, ast.AST]:
    table: dict[int, ast.AST] = {}
    for node in nodes:
        for child in ast.iter_child_nodes(node):
            table[id(child)] = node
    return table


@dataclass
class _Scope:
    name: str
    nodes: list[ast.AST]
    nested: list[ast.AST]
    parents: dict[int, ast.AST]
    assigned: Counter
    parameters: set[str]
    shadows: set[str]


def _scopes(tree: ast.Module) -> list[_Scope]:
    """Split a module into scopes, each holding only its own statements.

    Nested functions, classes and lambdas are separate scopes; their bodies are
    kept so a name that escapes into one can be detected rather than mistaken
    for an unused local. Each scope also records the names bound closer than the
    module's imports -- parameters, assignments, local imports, nested
    definitions, ``except`` targets, deletions -- so a module-level owner
    binding is not credited to a site where a nearer name has taken it over.
    Shadows accumulate outwards-in, because a nested scope also sees whatever
    its enclosing scope rebound.
    """
    scopes: list[_Scope] = []

    def walk(body: list[ast.stmt], name: str, parameters: set[str], inherited: set[str]) -> None:
        nodes: list[ast.AST] = []
        nested: list[ast.AST] = []

        def collect(node: ast.AST) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                nested.append(node)
                return
            nodes.append(node)
            for child in ast.iter_child_nodes(node):
                collect(child)

        for statement in body:
            collect(statement)
        assigned = Counter(n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        shadows = set(inherited) | set(assigned) | set(parameters)
        shadows |= {n.name for n in nodes if isinstance(n, ast.ExceptHandler) and n.name}
        shadows |= {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Del)}
        if name != '<module>':
            # At module scope an import and a definition *are* the binding the
            # owner resolution already reads; deeper down they displace it.
            shadows |= {alias.asname or alias.name.split('.')[0]
                        for n in nodes if isinstance(n, (ast.Import, ast.ImportFrom)) for alias in n.names}
            shadows |= {child.name for child in nested if not isinstance(child, ast.Lambda)}
        scopes.append(_Scope(name, nodes, nested, _parents(nodes), assigned, parameters, shadows))
        for child in nested:
            if isinstance(child, ast.Lambda):
                continue
            inner = name + '.' + child.name if name != '<module>' else child.name
            params: set[str] = set()
            if not isinstance(child, ast.ClassDef):
                arguments = child.args
                params = {a.arg for a in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)}
                params.update(a.arg for a in (arguments.vararg, arguments.kwarg) if a)
            walk(child.body, inner, params, shadows)

    walk(tree.body, '<module>', set(), set())
    return scopes


def _literal_strings(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant):
        return {node.value} if isinstance(node.value, str) else set()
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return set().union(*(_literal_strings(item) for item in node.elts), set())
    if isinstance(node, ast.MatchValue):
        return _literal_strings(node.value)
    if isinstance(node, ast.MatchOr):
        return set().union(*(_literal_strings(p) for p in node.patterns), set())
    return set()


# Constructs the climb passes through: they move or select the value without
# applying anything to it, so what happens above them still describes this read.
_RELOCATING = (ast.Tuple, ast.List, ast.Set, ast.Starred, ast.keyword, ast.BoolOp, ast.IfExp, ast.Await)


def _classify_use(node: ast.AST, scope: _Scope) -> tuple[str | None, set[str], str | None, str | None]:
    """Walk syntactic parents of one use; return role, literals, local, blocker.

    A ``None`` role means the climb reached something this scan cannot resolve,
    and ``blocker`` names it. The third element is the plain local name the
    climb ended at, if any, so the caller can spend its single hop on that
    name's own uses; a local hop is available, so it carries no blocker.

    Every role returned here is claimed from syntax that establishes it.
    ``pass_through`` in particular means the AST shows the value itself placed
    somewhere else, with nothing applied to it on the way.
    """
    observed: set[str] = set()
    current = node
    for _ in range(12):  # A finite climb; a deeper nesting stays unclassified.
        parent = scope.parents.get(id(current))
        if parent is None:
            return None, observed, None, 'unclassified_context'
        if isinstance(parent, ast.Compare):
            for operand in (parent.left, *parent.comparators):
                if operand is not current:
                    observed |= _literal_strings(operand)
            return 'interpret', observed, None, None
        if isinstance(parent, ast.Match) and parent.subject is current:
            for case in parent.cases:
                observed |= _literal_strings(case.pattern)
            return 'interpret', observed, None, None
        if isinstance(parent, (ast.If, ast.While, ast.Assert)) and getattr(parent, 'test', None) is current:
            return 'interpret', observed, None, None
        if isinstance(parent, ast.IfExp) and parent.test is current:
            return 'interpret', observed, None, None
        if isinstance(parent, ast.Subscript) and parent.slice is current:
            # The value selects an entry of another mapping: one vocabulary
            # read as the key into another, which is interpretation.
            return 'interpret', observed, None, None
        if isinstance(parent, ast.Return):
            return 'pass_through', observed, None, None
        if isinstance(parent, ast.Dict) and current in parent.values:
            return 'pass_through', observed, None, None
        if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is current:
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            if any(isinstance(t, (ast.Subscript, ast.Attribute)) for t in targets):
                # A store into a payload or an object relocates the value.
                return 'pass_through', observed, None, None
            if len(targets) == 1 and isinstance(targets[0], ast.Name):
                return None, observed, targets[0].id, None
            return None, observed, None, 'unclassified_context'
        if isinstance(parent, ast.Call) and current is not parent.func:
            # The callee is not followed, so nothing here separates a forward
            # from a conversion or a branch taken inside it. ``Kind(value)``
            # and ``sink(value)`` are the same syntax and must not be read as
            # the same claim about the value.
            return None, observed, None, 'call_target_untraced'
        if isinstance(parent, (ast.FormattedValue, ast.JoinedStr)):
            return None, observed, None, 'conversion_untraced'
        if isinstance(parent, ast.Attribute):
            # A further attribute of the read is a different access this scan
            # cannot resolve. ``.value`` is only an enum unwrap when the object
            # is an enum member, which nothing here establishes.
            return None, observed, None, 'attribute_untraced'
        if isinstance(parent, ast.Expr):
            return 'read', observed, None, None
        if not isinstance(parent, _RELOCATING):
            return None, observed, None, 'unclassified_context'
        current = parent
    return None, observed, None, 'climb_depth_exceeded'


def _escapes(name: str, scope: _Scope) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)
               for child in scope.nested for n in ast.walk(child))


def _classify_anchor(node: ast.AST, scope: _Scope) -> tuple[str, set[str], str | None]:
    """Classify one anchor expression, spending at most one local hop.

    A read bound to a plain local produces no role of its own, so that name's
    own uses decide the row. Across them an observed branch wins outright: it
    is the top of the rank, so no unresolved sibling use could be hiding
    anything stronger. Any weaker summary is withdrawn to unknown once a
    sibling use is unresolved, because ``pass_through`` and ``read`` carry an
    implicit *only* that an unwalked use could falsify.
    """
    role, observed, name, blocker = _classify_use(node, scope)
    if name is None:
        if role is not None:
            return role, observed, None
        return 'unknown', observed, blocker or 'unclassified_context'
    if name in scope.parameters or scope.assigned[name] != 1:
        return 'unknown', observed, 'unstable_local'
    if _escapes(name, scope):
        return 'unknown', observed, 'nested_scope_escape'
    uses = [n for n in scope.nodes
            if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)]
    if not uses:
        # Bound and never read again: a real read, and nothing more than that.
        return 'read', observed, None
    resolved: list[str] = []
    unresolved: str | None = None
    for use in uses:
        used_role, named, next_name, used_blocker = _classify_use(use, scope)
        observed |= named
        if used_role is None:
            # A second alias hop would be needed; one hop is the stated bound.
            unresolved = unresolved or ('alias_chain' if next_name else used_blocker)
            continue
        resolved.append(used_role)
    if 'interpret' in resolved:
        return 'interpret', observed, None
    if unresolved is not None:
        return 'unknown', observed, unresolved
    return max(resolved, key=_ROLE_RANK.__getitem__), observed, None


def _slot_anchors(scope: _Scope, slot: str) -> list[ast.AST]:
    anchors: list[ast.AST] = []
    for node in scope.nodes:
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            if isinstance(node.slice, ast.Constant) and node.slice.value == slot:
                anchors.append(node)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and node.attr == slot:
            anchors.append(node)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in _MAPPING_READS and node.args
              and isinstance(node.args[0], ast.Constant) and node.args[0].value == slot):
            anchors.append(node)
    return anchors


def _membership_anchors(scope: _Scope, slot: str) -> list[ast.AST]:
    """``"slot" in payload`` proves the module checks for the slot's presence."""
    return [node for node in scope.nodes
            if isinstance(node, ast.Compare)
            and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
            and isinstance(node.left, ast.Constant) and node.left.value == slot]


def _owner_anchors(scope: _Scope, members: Mapping[str, str],
                   bound: set[str]) -> list[tuple[ast.AST, set[str], str | None]]:
    """Comparisons and match cases naming a member of the bound owner class.

    A site qualifies only while the owner's name still means the owner *here*.
    Where a parameter, a local assignment, a local import, a nested definition
    or an ``except`` target has taken the name over, the operand belongs to
    whatever that closer binding is, so the site is reported unknown rather than
    credited to the registered owner it merely resembles.
    """
    live = bound - scope.shadows
    taken = bound & scope.shadows

    def member_values(node: ast.AST, names: set[str]) -> set[str]:
        if isinstance(node, ast.Attribute) and node.attr == 'value':
            return member_values(node.value, names)
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in names and node.attr in members):
            return {members[node.attr]}
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return set().union(*(member_values(item, names) for item in node.elts), set())
        if isinstance(node, ast.MatchValue):
            return member_values(node.value, names)
        if isinstance(node, ast.MatchOr):
            return set().union(*(member_values(p, names) for p in node.patterns), set())
        return set()

    anchors: list[tuple[ast.AST, set[str], str | None]] = []
    for node in scope.nodes:
        if isinstance(node, ast.Compare):
            operands = (node.left, *node.comparators)
            named: set[str] = set()
            subject: ast.AST | None = None
            for operand in operands:
                values = member_values(operand, live)
                named |= values
                if not values and subject is None:
                    subject = operand
            if named and subject is not None:
                anchors.append((node, named, None))
            elif taken and any(member_values(operand, taken) for operand in operands):
                anchors.append((node, set(), 'shadowed_owner_binding'))
        elif isinstance(node, ast.Match):
            named = set().union(*(member_values(case.pattern, live) for case in node.cases), set())
            if named:
                anchors.append((node, named, None))
            elif taken and any(member_values(case.pattern, taken) for case in node.cases):
                anchors.append((node, set(), 'shadowed_owner_binding'))
    return anchors


def _dynamic_anchors(scope: _Scope) -> list[ast.AST]:
    """Mapping reads with a computed key: possible readers of any slot."""
    return [node for node in scope.nodes
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in _MAPPING_READS and node.args
            and not isinstance(node.args[0], ast.Constant)]


def scan_python_consumers(
    source: SourceFile, *, vocabulary: str, slot: str, values: Iterable[str],
    owners: Mapping[str, Mapping[str, str]] | None = None,
    modules: Mapping[str, SourceFile] | None = None,
) -> list[ConsumerSite]:
    """Classify every recognized consuming site of one vocabulary in one file."""
    tree = ast.parse(source.text, filename=source.path)
    domain = tuple(sorted(values))
    owners = owners or {}
    members: dict[str, str] = {}
    bound: set[str] = set()
    if owners:
        bindings = _qualified_bindings(source, tree, owners, modules)
        bound = set(bindings)
        for member_map in bindings.values():
            members.update(member_map)
    rows: set[ConsumerSite] = set()
    for scope in _scopes(tree):
        site = f'{source.path}::{scope.name}'

        def add(node: ast.AST, role: str, anchor: str, observed: Iterable[str], blocker: str | None) -> None:
            named = tuple(sorted(set(observed) & set(domain))) if domain else tuple(sorted(set(observed)))
            rows.add(ConsumerSite(vocabulary, site, node.lineno, role, anchor, domain, named,
                                  _LIMITATIONS[role] if blocker is None else f'{blocker}: unresolved, not absent',
                                  blocker))

        for node in _slot_anchors(scope, slot):
            role, observed, blocker = _classify_anchor(node, scope)
            add(node, role, 'slot_read', observed, blocker)
        for node in _membership_anchors(scope, slot):
            add(node, 'read', 'slot_presence', set(), None)
        for node, named, blocker in _owner_anchors(scope, members, bound):
            add(node, 'unknown' if blocker else 'interpret', 'owner_member', named, blocker)
    return sorted(rows, key=lambda row: (row.site, row.line, row.role, row.anchor))


def scan_dynamic_reads(source: SourceFile) -> list[ConsumerSite]:
    """Sites that read a mapping under a computed key, unattributed by design.

    Such a site may read any covered slot, so it is recorded once against
    ``ANY_VOCABULARY`` rather than duplicated into every vocabulary's table.
    It is the reason a vocabulary measured at zero readers is measured against
    a stated unknown instead of declared dead.
    """
    rows: list[ConsumerSite] = []
    for scope in _scopes(ast.parse(source.text, filename=source.path)):
        for node in _dynamic_anchors(scope):
            rows.append(ConsumerSite(
                ANY_VOCABULARY, f'{source.path}::{scope.name}', node.lineno, 'unknown',
                'dynamic_mapping_read', (), (),
                'dynamic_key: unresolved, not absent', 'dynamic_key'))
    return rows


def _first_line(text: str, token: str) -> int:
    """The first line carrying a token, so an unclassified mention has a location."""
    for number, line in enumerate(text.splitlines(), start=1):
        if token in line:
            return number
    return 1


def collect_consumer_evidence(
    repo_root: Path, registry: Mapping[str, Any], sources: list[SourceFile],
) -> ConsumerEvidence:
    """Scan the code-owned reach for every vocabulary that declares a slot."""
    by_path = {s.path: s for s in sources}
    selected = [s for s in sources if _in_reach(s.path)]
    python = [s for s in selected if s.suffix == '.py']
    sha, dirty = source_revision(repo_root)
    slots: list[tuple[str, str]] = []
    missing: list[str] = []
    for name, vocabulary in registry['vocabularies'].items():
        slot = declared_slot(vocabulary)
        if slot is None:
            missing.append(name)
        else:
            slots.append((name, slot))
    rows: list[ConsumerSite] = []
    skipped = 0
    for name, slot in slots:
        vocabulary = registry['vocabularies'][name]
        owners = _owner_members(vocabulary, by_path)
        domain = tuple(sorted(vocabulary['values']))
        symbols = [owner.split('::')[1] for owner in owners]
        for source in python:
            # Cheap text prefilter: a file that never spells the slot and never
            # imports the owner cannot yield a slot or owner anchor.
            mentions_slot = slot in source.text
            if not mentions_slot and not any(symbol in source.text for symbol in symbols):
                continue
            found = scan_python_consumers(source, vocabulary=name, slot=slot,
                                          values=vocabulary['values'], owners=owners, modules=by_path)
            rows.extend(found)
            if not found and mentions_slot:
                # The token is here and the grammar recognized nothing: prose, a
                # field list, a local named after the slot, a carrier this scan
                # does not model. Reported as unknown because the alternative is
                # to drop the module and let the tables read as complete.
                rows.append(ConsumerSite(
                    name, f'{source.path}::<module>', _first_line(source.text, slot), 'unknown',
                    'slot_mention', domain, (),
                    'mention_without_recognized_anchor: unresolved, not absent', 'mention_without_recognized_anchor'))
        for source in selected:
            if source.suffix == '.ts' and slot in source.text:
                skipped += 1
                rows.append(ConsumerSite(
                    name, f'{source.path}::<module>', 1, 'unknown', 'typescript_source', domain, (),
                    'typescript_not_walked: unresolved, not absent', 'typescript_not_walked'))
    if slots:
        for source in python:
            rows.extend(scan_dynamic_reads(source))
    rows.sort(key=lambda row: (row.vocabulary, row.site, row.line, row.role, row.anchor))
    return ConsumerEvidence(sha, dirty, len(python), len(sources), skipped,
                            tuple(name for name, _ in slots), tuple(missing), tuple(rows))


def render_consumer_evidence(evidence: ConsumerEvidence, *, top: int = 25) -> list[str]:
    """Advisory lines for the report surface; never a pass/fail verdict.

    Coverage is printed before the counts, because the counts describe only the
    covered vocabularies and a reader who does not know how many were skipped
    cannot size what the table leaves out.

    Two unknown shares are printed because they answer different questions. The
    overall share includes the computed-key sites, which belong to no single
    vocabulary and dominate the count; the attributed share is what is unknown
    once a row has a vocabulary. Printing only one of them would flatter or
    inflate the result depending on which.

    Nothing is filtered out of the row listing. Computed-key and otherwise
    unattributable sites are printed too, because a table that shows only the
    rows the grammar resolved reads as a complete census of the slot's readers
    and is not one. They get their own block under the same ``--top`` budget
    rather than sharing one, since they outnumber the classified rows several
    times over and a single location-ordered list would bury them under it --
    which is the same concealment as the filter, spelled differently.
    """
    counts = evidence.counts()
    total = len(evidence.rows)
    registered = len(evidence.covered) + len(evidence.missing_slot_identity)
    attributed = [row for row in evidence.rows if row.vocabulary != ANY_VOCABULARY]
    attributed_unknown = sum(1 for row in attributed if row.role == 'unknown')
    lines = [
        'consumer evidence (advisory; syntactic use, not proved data flow, never a gate):',
        f'  source_sha={evidence.source_sha[:12]}'
        f'{" +dirty-worktree" if evidence.worktree_dirty else ""}'
        f'  reach={evidence.scanned_files} scanned Python sources of {evidence.tracked_files} tracked'
        f'  roots={", ".join(CONSUMER_SCAN_ROOTS)}',
        f'  covered={len(evidence.covered)} of {registered} registered vocabularies'
        f'  (only those declaring literal_scan.field): ' + (', '.join(evidence.covered) or 'none'),
        f'  missing_slot_identity={len(evidence.missing_slot_identity)}'
        ' not analysed, no declared slot to anchor a read: '
        + (', '.join(evidence.missing_slot_identity) or 'none'),
        f'  rows={total}  ' + '  '.join(f'{role}={counts[role]}' for role in _ROLES)
        + f'  unknown_share={evidence.unknown_share():.1%}',
        f'  attributed_rows={len(attributed)}  unknown={attributed_unknown}'
        f'  attributed_unknown_share={attributed_unknown / len(attributed):.1%}'
        if attributed else '  attributed_rows=0',
        '  unknown_reasons: ' + (', '.join(f'{k}={v}' for k, v in evidence.blockers().items()) or 'none'),
        f'  {ANY_VOCABULARY} rows read a mapping under a computed key, so they are unresolved '
        'for every covered vocabulary at once and are never counted as a reader of one',
    ]
    per_vocabulary = Counter((row.vocabulary, row.role) for row in evidence.rows)
    for name in sorted({row.vocabulary for row in evidence.rows}):
        tally = '  '.join(f'{role}={per_vocabulary.get((name, role), 0)}' for role in _ROLES)
        lines.append(f'  {name}: {tally}')
    unattributed = [row for row in evidence.rows if row.vocabulary == ANY_VOCABULARY]
    for header, population in (
        ('  rows (location | vocabulary | role | anchor | observed values | limitation):', attributed),
        ('  unattributed rows, same budget so neither population crowds the other out '
         '(a computed key may read any covered slot):', unattributed),
    ):
        lines.append(header)
        shown = sorted(population, key=lambda row: (row.site, row.line, row.vocabulary, row.role))[:top]
        for row in shown:
            observed = ','.join(row.observed) or '-'
            lines.append(f'    {row.site}:{row.line} | {row.vocabulary} | {row.role} | {row.anchor} '
                         f'| {observed} | {row.limitation}')
        if not shown:
            lines.append('    none')
        elif len(population) > len(shown):
            lines.append(f'    ... {len(population) - len(shown)} further rows in the same order; '
                         'raise --top to print more')
    return lines
