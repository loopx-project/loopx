"""AST-backed control-plane debt reporting with reviewed exception lifecycle."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from importlib.util import resolve_name
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from ..paths import display_path_text

_SubmoduleCache = dict[tuple[Path, str, str], str | None]


@dataclass(frozen=True, slots=True)
class _ModuleScan:
    metrics: dict[str, int]
    from_imports: dict[str, set[str]] | None
    top_level_package_imports: dict[str, str] | None
    oversized_findings: list[dict[str, Any]] | None
    syntax_error: SyntaxError | None


MAINTAINABILITY_REPORT_SCHEMA_VERSION = "control_plane_maintainability_report_v0"
MODULE_METRIC_BASELINE_SCHEMA_VERSION = "control_plane_module_metric_baseline_v0"
DECISION_STATEMENT_LIMIT = 90
DECISION_POINT_LIMIT = 60
MODULE_LINE_LIMIT = 2000
MODULE_ANY_LIMIT = 300
MODULE_DICT_ANY_LIMIT = 300
MODULE_METRIC_BASELINE_PATH = Path(__file__).with_name("module_metric_baseline.json")

CONTROL_PLANE_FORBIDDEN_DEPENDENCY_PREFIXES = (
    "loopx.capabilities",
    "loopx.cli",
    "loopx.cli_commands",
    "loopx.presentation",
)
STATUS_FORBIDDEN_DEPENDENCY_PREFIXES = (
    "loopx.presentation",
)
COMPATIBILITY_FACADE_PATHS = {
    "loopx.quota": "loopx/quota.py",
    "loopx.status": "loopx/status.py",
}


def _exception(
    reason: str,
    retirement_plan: str,
    *,
    metric_ceilings: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "reason": reason,
        "retirement_plan": retirement_plan,
        **({"metric_ceilings": dict(metric_ceilings)} if metric_ceilings else {}),
    }


REVIEWED_MAINTAINABILITY_EXCEPTIONS: dict[str, dict[str, Any]] = {
    "compatibility_facade:loopx.quota": _exception(
        "The public loopx.quota import surface remains a supported compatibility contract, "
        "including presentation-owned quota event renderers.",
        "Keep internal consumers on canonical modules and shrink exports as callers migrate.",
        metric_ceilings={"package_reexport_count": 101, "source_module_count": 37},
    ),
    "compatibility_facade:loopx.status": _exception(
        "The public loopx.status import surface remains a supported compatibility contract. "
        "Fifteen existing carriers now import their single owners instead of restating "
        "local values; the import count changes without adding public names.",
        "Keep internal consumers on canonical modules and shrink exports as callers migrate.",
        metric_ceilings={"package_reexport_count": 119, "source_module_count": 50},
    ),
}


def _module_name(path: Path, package_root: Path) -> str:
    relative = path.relative_to(package_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join((package_root.name, *parts))


def _package_name(path: Path, package_root: Path) -> str:
    module_name = _module_name(path, package_root)
    return module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]


def _resolved_module(node: ast.ImportFrom, *, package_name: str) -> str:
    module = node.module or ""
    if not node.level:
        return module
    if not package_name:
        return ""
    return resolve_name("." * node.level + module, package_name)


def _iter_import_nodes(tree: ast.Module) -> Iterator[ast.Import | ast.ImportFrom]:
    stack: list[Sequence[ast.stmt]] = [tree.body]
    while stack:
        for stmt in stack.pop():
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                yield stmt
            elif isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While)):
                if stmt.orelse:
                    stack.append(stmt.orelse)
                if stmt.body:
                    stack.append(stmt.body)
            elif isinstance(
                stmt,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.With, ast.AsyncWith),
            ):
                if stmt.body:
                    stack.append(stmt.body)
            elif isinstance(stmt, (ast.Try, ast.TryStar)):
                if stmt.finalbody:
                    stack.append(stmt.finalbody)
                if stmt.orelse:
                    stack.append(stmt.orelse)
                for handler in stmt.handlers:
                    if handler.body:
                        stack.append(handler.body)
                if stmt.body:
                    stack.append(stmt.body)
            elif isinstance(stmt, ast.Match):
                for case in stmt.cases:
                    if case.body:
                        stack.append(case.body)


def _python_paths_under(
    root: Path,
    *,
    tracked_paths: set[Path] | None,
) -> list[Path]:
    if tracked_paths is not None:
        return sorted(
            path
            for path in tracked_paths
            if path.name.endswith(".py")
            and path.is_relative_to(root)
            and path.is_file()
        )
    return sorted(root.rglob("*.py")) if root.is_dir() else []


def _existing_submodule(
    module: str,
    imported_name: str,
    *,
    package_root: Path,
    _submodule_cache: _SubmoduleCache | None = None,
) -> str | None:
    if not module or imported_name == "*":
        return None
    cache_key = (package_root, module, imported_name)
    if _submodule_cache is not None and cache_key in _submodule_cache:
        return _submodule_cache[cache_key]
    candidate = f"{module}.{imported_name}"
    package_prefix = package_root.name + "."
    resolved: str | None = None
    if candidate.startswith(package_prefix):
        relative_parts = candidate[len(package_prefix) :].split(".")
        candidate_path = package_root.joinpath(*relative_parts)
        if candidate_path.with_suffix(".py").is_file() or candidate_path.is_dir():
            resolved = candidate
    if _submodule_cache is not None:
        _submodule_cache[cache_key] = resolved
    return resolved


def _extract_from_imports(
    tree: ast.Module,
    *,
    package_name: str,
    package_root: Path,
    submodule_cache: _SubmoduleCache | None = None,
) -> dict[str, set[str]]:
    imports: dict[str, set[str]] = {}
    for node in _iter_import_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.setdefault(alias.name, set()).add("*")
        else:
            module = _resolved_module(node, package_name=package_name)
            if not module:
                continue
            imports.setdefault(module, set()).update(alias.name for alias in node.names)
            for alias in node.names:
                candidate = _existing_submodule(
                    module,
                    alias.name,
                    package_root=package_root,
                    _submodule_cache=submodule_cache,
                )
                if candidate:
                    imports.setdefault(candidate, set()).add("*")
    return imports


def _extract_top_level_package_imports(
    tree: ast.Module,
    *,
    package_name: str,
    package_root: Path,
) -> dict[str, str]:
    imports: dict[str, str] = {}
    package_prefix = package_root.name + "."
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = _resolved_module(node, package_name=package_name)
            if not module.startswith(package_prefix):
                continue
            for alias in node.names:
                imports[alias.asname or alias.name] = module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(package_prefix):
                    imports[alias.asname or alias.name.split(".")[0]] = alias.name
    return imports


def resolved_imports(
    path: Path,
    *,
    package_root: Path,
    _submodule_cache: _SubmoduleCache | None = None,
) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return set(
        _extract_from_imports(
            tree,
            package_name=_package_name(path, package_root),
            package_root=package_root,
            submodule_cache=_submodule_cache,
        )
    )


def _resolved_from_imports(
    path: Path,
    *,
    package_root: Path,
    _submodule_cache: _SubmoduleCache | None = None,
) -> dict[str, set[str]]:
    package_name = (
        _package_name(path, package_root) if path.is_relative_to(package_root) else ""
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _extract_from_imports(
        tree,
        package_name=package_name,
        package_root=package_root,
        submodule_cache=_submodule_cache,
    )


def _top_level_package_imports(
    path: Path,
    *,
    package_root: Path,
) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _extract_top_level_package_imports(
        tree,
        package_name=_package_name(path, package_root),
        package_root=package_root,
    )


def _matches_prefix(value: str, prefixes: Sequence[str]) -> bool:
    return any(value == prefix or value.startswith(prefix + ".") for prefix in prefixes)


def _outermost_matching_imports(
    imports: Sequence[str] | set[str],
    prefixes: Sequence[str],
) -> list[str]:
    matches = sorted(value for value in imports if _matches_prefix(value, prefixes))
    return [
        value
        for value in matches
        if not any(
            value.startswith(parent + ".") for parent in matches if parent != value
        )
    ]


def _finding_id(category: str, identity: str) -> str:
    return f"{category}:{identity}"


def tracked_python_paths(repository_root: Path) -> set[Path]:
    # `-z` frames each pathname on NUL, so a path carrying U+0085/U+2028/U+2029
    # stays one record even in a repository that sets `core.quotePath=false`,
    # where `git ls-files` emits it raw and `str.splitlines()` would tear it.
    # The read stays in bytes so the record's own identity survives: a text
    # read turns a CR inside a legal name into LF and an undecodable record
    # into U+FFFD, and the rebuilt path would not name the tracked file, so
    # that module would silently leave the ratchet.
    completed = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        cwd=repository_root,
        check=True,
        stdout=subprocess.PIPE,
    )
    paths: set[Path] = set()
    for record in completed.stdout.split(b"\0"):
        if not record:
            continue
        path = repository_root / os.fsdecode(record)
        if path.is_file():
            paths.add(path)
    return paths


def _dependency_finding(
    *,
    source: str,
    target: str,
    path: Path,
    repository_root: Path,
    rule: str,
    imported_name: str | None = None,
) -> dict[str, Any]:
    identity = f"{source}->{target}"
    if imported_name:
        identity += f":{imported_name}"
    return {
        "id": _finding_id("dependency_debt", display_path_text(identity)),
        "category": "dependency_debt",
        "rule": rule,
        # A name Git can hold but UTF-8 cannot encode would otherwise write
        # bytes no JSON reader can parse; the label keeps the record locatable.
        "path": display_path_text(path.relative_to(repository_root).as_posix()),
        "source": display_path_text(source),
        "target": display_path_text(target),
        **({"imported_name": imported_name} if imported_name else {}),
    }


def collect_dependency_debt(
    repository_root: Path,
    *,
    tracked_paths: set[Path] | None = None,
    _scan_cache: Mapping[Path, _ModuleScan] | None = None,
) -> list[dict[str, Any]]:
    package_root = repository_root / "loopx"
    control_plane_root = package_root / "control_plane"
    submodule_cache: _SubmoduleCache = {}

    def path_from_imports(path: Path) -> dict[str, set[str]]:
        if _scan_cache is not None and path in _scan_cache:
            scan = _scan_cache[path]
            if scan.syntax_error is not None:
                raise scan.syntax_error
            if scan.from_imports is not None:
                return scan.from_imports
        return _resolved_from_imports(
            path,
            package_root=package_root,
            _submodule_cache=submodule_cache,
        )

    findings: list[dict[str, Any]] = []
    control_plane_paths = _python_paths_under(
        control_plane_root,
        tracked_paths=tracked_paths,
    )
    for path in control_plane_paths:
        source = _module_name(path, package_root)
        for target in _outermost_matching_imports(
            set(path_from_imports(path)),
            CONTROL_PLANE_FORBIDDEN_DEPENDENCY_PREFIXES,
        ):
            findings.append(
                _dependency_finding(
                    source=source,
                    target=target,
                    path=path,
                    repository_root=repository_root,
                    rule="control_plane_outward_dependency",
                )
            )

    status_path = package_root / "status.py"
    for target in _outermost_matching_imports(
        set(path_from_imports(status_path)),
        STATUS_FORBIDDEN_DEPENDENCY_PREFIXES,
    ):
        findings.append(
            _dependency_finding(
                source="loopx.status",
                target=target,
                path=status_path,
                repository_root=repository_root,
                rule="status_outward_dependency",
            )
        )

    facade_exports: dict[str, set[str]] = {}
    for module, relative_path in COMPATIBILITY_FACADE_PATHS.items():
        facade_path = repository_root / relative_path
        scan = _scan_cache.get(facade_path) if _scan_cache is not None else None
        if scan is not None:
            if scan.syntax_error is not None:
                raise scan.syntax_error
            if scan.top_level_package_imports is not None:
                facade_exports[module] = set(scan.top_level_package_imports)
                continue
        facade_exports[module] = set(
            _top_level_package_imports(facade_path, package_root=package_root)
        )

    facade_paths = {
        repository_root / path for path in COMPATIBILITY_FACADE_PATHS.values()
    }
    internal_paths = [
        path
        for root in (package_root, repository_root / "scripts")
        for path in _python_paths_under(root, tracked_paths=tracked_paths)
        if path not in facade_paths
    ]
    for path in sorted(internal_paths):
        source = (
            _module_name(path, package_root)
            if path.is_relative_to(package_root)
            else path.relative_to(repository_root).as_posix()
        )
        for target, imported_names in sorted(path_from_imports(path).items()):
            if target not in facade_exports:
                continue
            facade_consumers = (
                {"*"}
                if "*" in imported_names
                else imported_names & facade_exports[target]
            )
            for imported_name in sorted(facade_consumers):
                findings.append(
                    _dependency_finding(
                        source=source,
                        target=target,
                        path=path,
                        repository_root=repository_root,
                        rule="internal_facade_consumer",
                        imported_name=imported_name,
                    )
                )
    return sorted(findings, key=lambda item: str(item["id"]))


class _FunctionMetrics(ast.NodeVisitor):
    def __init__(self, root: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.root = root
        self.statement_count = 0
        self.decision_points = 0

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, ast.stmt) and not isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            self.statement_count += 1
        super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.root:
            for child in node.body:
                self.visit(child)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.root:
            for child in node.body:
                self.visit(child)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_If(self, node: ast.If) -> None:
        self.decision_points += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.decision_points += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.decision_points += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.decision_points += 1
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self.decision_points += max(1, len(node.handlers))
        self.generic_visit(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self.decision_points += max(1, len(node.handlers))
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.decision_points += len(node.cases)
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.decision_points += max(1, len(node.values) - 1)
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.decision_points += 1
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.decision_points += 1 + len(node.ifs)
        self.generic_visit(node)


def _iter_functions(
    body: Sequence[ast.stmt],
    *,
    prefix: str = "",
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified_name = f"{prefix}.{node.name}" if prefix else node.name
            functions.append((qualified_name, node))
            functions.extend(_iter_functions(node.body, prefix=qualified_name))
        elif isinstance(node, ast.ClassDef):
            qualified_name = f"{prefix}.{node.name}" if prefix else node.name
            functions.extend(_iter_functions(node.body, prefix=qualified_name))
    return functions


def _decision_scan_paths(
    repository_root: Path,
    *,
    tracked_paths: set[Path] | None,
) -> list[Path]:
    package_root = repository_root / "loopx"
    return [
        *_python_paths_under(package_root / "control_plane", tracked_paths=tracked_paths),
        *(
            path
            for path in (
                package_root / "quota.py",
                package_root / "status.py",
                package_root / "cli_commands" / "quota.py",
            )
            if path.is_file() and (tracked_paths is None or path in tracked_paths)
        ),
    ]


def _extract_oversized_decision_functions(
    tree: ast.Module,
    *,
    path: Path,
    module: str,
    repository_root: Path,
    statement_limit: int,
    decision_point_limit: int,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    relative_path = display_path_text(path.relative_to(repository_root).as_posix())
    display_module = display_path_text(module)
    for qualified_name, function in _iter_functions(tree.body):
        metrics = _FunctionMetrics(function)
        metrics.visit(function)
        if (
            metrics.statement_count <= statement_limit
            and metrics.decision_points <= decision_point_limit
        ):
            continue
        identity = f"{module}:{qualified_name}"
        findings.append(
            {
                "id": _finding_id(
                    "oversized_decision_function", display_path_text(identity)
                ),
                "category": "oversized_decision_function",
                "path": relative_path,
                "module": display_module,
                "symbol": qualified_name,
                "line": function.lineno,
                "metrics": {
                    "statements": metrics.statement_count,
                    "decision_points": metrics.decision_points,
                },
                "thresholds": {
                    "max_statements": statement_limit,
                    "max_decision_points": decision_point_limit,
                },
            }
        )
    return findings


def collect_oversized_decision_functions(
    repository_root: Path,
    *,
    statement_limit: int = DECISION_STATEMENT_LIMIT,
    decision_point_limit: int = DECISION_POINT_LIMIT,
    tracked_paths: set[Path] | None = None,
    _scan_cache: Mapping[Path, _ModuleScan] | None = None,
) -> list[dict[str, Any]]:
    package_root = repository_root / "loopx"
    paths = _decision_scan_paths(repository_root, tracked_paths=tracked_paths)
    use_cached_findings = (
        _scan_cache is not None
        and statement_limit == DECISION_STATEMENT_LIMIT
        and decision_point_limit == DECISION_POINT_LIMIT
    )
    findings: list[dict[str, Any]] = []
    for path in paths:
        if use_cached_findings and _scan_cache is not None and path in _scan_cache:
            scan = _scan_cache[path]
            if scan.syntax_error is not None:
                raise scan.syntax_error
            if scan.oversized_findings is not None:
                findings.extend(scan.oversized_findings)
                continue
        module = _module_name(path, package_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        findings.extend(
            _extract_oversized_decision_functions(
                tree,
                path=path,
                module=module,
                repository_root=repository_root,
                statement_limit=statement_limit,
                decision_point_limit=decision_point_limit,
            )
        )
    return sorted(findings, key=lambda item: str(item["id"]))


def collect_compatibility_facades(
    repository_root: Path,
    *,
    _scan_cache: Mapping[Path, _ModuleScan] | None = None,
) -> list[dict[str, Any]]:
    package_root = repository_root / "loopx"
    findings: list[dict[str, Any]] = []
    for module, relative_path in sorted(COMPATIBILITY_FACADE_PATHS.items()):
        path = repository_root / relative_path
        scan = _scan_cache.get(path) if _scan_cache is not None else None
        if scan is not None:
            if scan.syntax_error is not None:
                raise scan.syntax_error
            imports = (
                scan.top_level_package_imports
                if scan.top_level_package_imports is not None
                else _top_level_package_imports(path, package_root=package_root)
            )
        else:
            imports = _top_level_package_imports(path, package_root=package_root)
        public_exports = sorted(name for name in imports if not name.startswith("_"))
        if not public_exports:
            continue
        findings.append(
            {
                "id": _finding_id("compatibility_facade", module),
                "category": "compatibility_facade",
                "path": relative_path,
                "module": module,
                "metrics": {
                    "package_reexport_count": len(public_exports),
                    "source_module_count": len(
                        {imports[name] for name in public_exports}
                    ),
                },
                "source_modules": sorted({imports[name] for name in public_exports}),
                "package_reexports": public_exports,
            }
        )
    return findings


def _is_benchmark_module_path(path: Path) -> bool:
    normalized = path.as_posix()
    parts = normalized.split("/")
    return (
        normalized.startswith("benchmark/")
        or normalized.startswith("deprecate/benchmark-legacy/")
        or "benchmark_toolkit" in parts
    )


def module_metric_baseline(baseline_path: Path) -> dict[str, dict[str, int]]:
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != MODULE_METRIC_BASELINE_SCHEMA_VERSION:
        raise ValueError(
            f"unexpected module metric baseline schema: "
            f"{payload.get('schema_version')!r}"
        )
    ceilings = payload.get("module_metric_ceilings")
    if not isinstance(ceilings, dict):
        raise ValueError("module metric baseline must contain module_metric_ceilings")
    expected_defaults = {
        "lines": MODULE_LINE_LIMIT,
        "any_count": MODULE_ANY_LIMIT,
        "dict_any_count": MODULE_DICT_ANY_LIMIT,
    }
    if payload.get("default_limits") != expected_defaults:
        raise ValueError(
            "module metric baseline default_limits must match runtime defaults"
        )
    normalized: dict[str, dict[str, int]] = {}
    for path, metrics in ceilings.items():
        if not isinstance(metrics, dict):
            raise ValueError(f"module metric baseline entry must be an object: {path}")
        normalized[str(path)] = {
            key: int(metrics[key])
            for key in ("lines", "any_count", "dict_any_count")
            if key in metrics
        }
    return normalized


def _fast_ast_any_counts(tree: ast.Module) -> tuple[int, int]:
    any_count = 0
    dict_any_count = 0
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Name):
            if node.id == "Any":
                any_count += 1
            continue
        if isinstance(node, ast.Constant):
            continue
        if isinstance(node, ast.Subscript):
            value = node.value
            slice_node = node.slice
            if (
                isinstance(value, ast.Name)
                and value.id == "dict"
                and isinstance(slice_node, ast.Tuple)
                and len(slice_node.elts) == 2
            ):
                key, val = slice_node.elts
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "str"
                    and isinstance(val, ast.Name)
                    and val.id == "Any"
                ):
                    dict_any_count += 1
        for child in node.__dict__.values():
            if type(child) is list:
                for item in child:
                    if isinstance(item, ast.AST):
                        stack.append(item)
            elif isinstance(child, ast.AST):
                stack.append(child)
    return any_count, dict_any_count


def _metrics_from_source_and_tree(
    source: str,
    tree: ast.Module | None,
) -> dict[str, int]:
    lines = len(source.splitlines())
    if tree is None or ("Any" not in source and source.isascii()):
        return {"lines": lines, "any_count": 0, "dict_any_count": 0}
    any_count, dict_any_count = _fast_ast_any_counts(tree)
    return {
        "lines": lines,
        "any_count": any_count,
        "dict_any_count": dict_any_count,
    }


def module_metrics(path: Path) -> dict[str, int]:
    source = path.read_text(encoding="utf-8")
    lines = len(source.splitlines())
    if "Any" not in source and source.isascii():
        return {"lines": lines, "any_count": 0, "dict_any_count": 0}
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return {"lines": lines, "any_count": 0, "dict_any_count": 0}
    return _metrics_from_source_and_tree(source, tree)


def _scan_repository_modules(
    repository_root: Path,
    *,
    tracked_paths: set[Path],
    statement_limit: int = DECISION_STATEMENT_LIMIT,
    decision_point_limit: int = DECISION_POINT_LIMIT,
) -> dict[Path, _ModuleScan]:
    package_root = repository_root / "loopx"
    facade_paths = {
        repository_root / relative_path
        for relative_path in COMPATIBILITY_FACADE_PATHS.values()
    }
    decision_paths = set(_decision_scan_paths(repository_root, tracked_paths=tracked_paths))
    dependency_paths = {
        *(_python_paths_under(package_root, tracked_paths=tracked_paths)),
        *(_python_paths_under(repository_root / "scripts", tracked_paths=tracked_paths)),
        package_root / "status.py",
    } - facade_paths
    metric_paths = {
        path
        for path in _python_paths_under(package_root, tracked_paths=tracked_paths)
        if not _is_benchmark_module_path(path)
    }
    all_paths = sorted(
        path
        for path in (dependency_paths | decision_paths | metric_paths | facade_paths)
        if path.is_file()
    )
    submodule_cache: _SubmoduleCache = {}
    scans: dict[Path, _ModuleScan] = {}
    for path in all_paths:
        source = path.read_text(encoding="utf-8")
        tree: ast.Module | None = None
        syntax_error: SyntaxError | None = None
        need_ast = (
            path in dependency_paths
            or path in decision_paths
            or path in facade_paths
            or "Any" in source
            or not source.isascii()
        )
        if need_ast:
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                syntax_error = exc
        metrics = _metrics_from_source_and_tree(source, tree)
        from_imports: dict[str, set[str]] | None = None
        top_level_imports: dict[str, str] | None = None
        oversized_findings: list[dict[str, Any]] | None = None
        if tree is not None:
            in_package = path.is_relative_to(package_root)
            package_name = _package_name(path, package_root) if in_package else ""
            if path in dependency_paths or path in facade_paths:
                if path in facade_paths:
                    top_level_imports = _extract_top_level_package_imports(
                        tree,
                        package_name=package_name,
                        package_root=package_root,
                    )
                if path in dependency_paths:
                    from_imports = _extract_from_imports(
                        tree,
                        package_name=package_name,
                        package_root=package_root,
                        submodule_cache=submodule_cache,
                    )
            if path in decision_paths:
                oversized_findings = _extract_oversized_decision_functions(
                    tree,
                    path=path,
                    module=_module_name(path, package_root),
                    repository_root=repository_root,
                    statement_limit=statement_limit,
                    decision_point_limit=decision_point_limit,
                )
        scans[path] = _ModuleScan(
            metrics=metrics,
            from_imports=from_imports,
            top_level_package_imports=top_level_imports,
            oversized_findings=oversized_findings,
            syntax_error=syntax_error,
        )
    return scans


def collect_module_metric_findings(
    repository_root: Path,
    *,
    tracked_paths: set[Path] | None = None,
    baseline_path: Path | None = None,
    _scan_cache: Mapping[Path, _ModuleScan] | None = None,
) -> list[dict[str, Any]]:
    package_root = repository_root / "loopx"
    baseline = module_metric_baseline(
        baseline_path or MODULE_METRIC_BASELINE_PATH
    )
    paths = _python_paths_under(package_root, tracked_paths=tracked_paths)
    findings: list[dict[str, Any]] = []
    for path in paths:
        if _is_benchmark_module_path(path):
            continue
        relative = path.relative_to(repository_root).as_posix()
        metrics = (
            dict(_scan_cache[path].metrics)
            if _scan_cache is not None and path in _scan_cache
            else module_metrics(path)
        )
        ceilings = {
            "lines": MODULE_LINE_LIMIT,
            "any_count": MODULE_ANY_LIMIT,
            "dict_any_count": MODULE_DICT_ANY_LIMIT,
        }
        ceilings.update(baseline.get(relative) or {})
        regressions = {
            metric: actual
            for metric, actual in metrics.items()
            if actual > ceilings.get(metric, 0)
        }
        if not regressions:
            continue
        findings.append(
            {
                "id": _finding_id("module_metric_budget", display_path_text(relative)),
                "category": "module_metric_budget",
                "path": display_path_text(relative),
                "module": display_path_text(_module_name(path, package_root)),
                "metrics": metrics,
                "metric_ceilings": ceilings,
                "regressions": regressions,
            }
        )
    return sorted(findings, key=lambda item: str(item["id"]))


def _git_show_text(repository_root: Path, rev: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=repository_root, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _rev_baseline_ceilings(repository_root: Path, rev: str) -> dict[str, dict[str, int]]:
    """Mirror ``module_metric_baseline`` for a committed revision."""
    text = _git_show_text(repository_root, rev, "loopx/canary/module_metric_baseline.json")
    if text is None:
        return {}
    payload = json.loads(text)
    ceilings = payload.get("module_metric_ceilings")
    if not isinstance(ceilings, dict):
        return {}
    return {
        str(path): {key: int(metrics[key]) for key in ("lines", "any_count", "dict_any_count") if key in metrics}
        for path, metrics in ceilings.items()
        if isinstance(metrics, dict)
    }


def diff_scoped_module_ceiling_violations(
    repository_root: Path,
    changed_files: Sequence[str],
    *,
    base_ref: str,
) -> list[dict[str, Any]]:
    """Module growth that crossed a reviewed ceiling must settle in the same diff.

    A module may exceed its pre-diff ceiling without this check firing, as long
    as this diff did not cause the crossing; and a module whose ceiling this diff
    raises can stay silent. The single case this flags is the one that previously
    merged and turned ``main`` red until a separate reconciliation PR refreshed
    the ledger: this diff grew a module past the ceiling it inherited, without
    settling that ceiling here.
    """
    head_ceilings = module_metric_baseline(
        repository_root / "loopx" / "canary" / "module_metric_baseline.json"
    )
    base_ceilings = _rev_baseline_ceilings(repository_root, (base_ref or "origin/main").strip() or "origin/main")
    violations: list[dict[str, Any]] = []
    for changed in changed_files:
        relative = str(changed)
        if not relative.startswith("loopx/") or not relative.endswith(".py"):
            continue
        checkout_path = repository_root / relative
        if not checkout_path.is_file():
            continue
        head_lines = len(checkout_path.read_text(encoding="utf-8").splitlines())
        base_lines = _git_show_text(repository_root, (base_ref or "origin/main").strip() or "origin/main", relative)
        if base_lines is None:
            base_ceiling = MODULE_LINE_LIMIT
            was_within_budget = True
        else:
            base_ceiling = base_ceilings.get(relative, {}).get("lines", MODULE_LINE_LIMIT)
            was_within_budget = len(base_lines.splitlines()) <= base_ceiling
        head_ceiling = head_ceilings.get(relative, {}).get("lines", MODULE_LINE_LIMIT)
        crossed_inherited_ceiling = was_within_budget and head_lines > base_ceiling
        if crossed_inherited_ceiling and head_ceiling < head_lines:
            violations.append(
                {
                    "id": _finding_id(
                        "module_metric_budget", display_path_text(relative)
                    ),
                    "category": "module_metric_budget",
                    "path": display_path_text(relative),
                    "base_lines": 0 if base_lines is None else len(base_lines.splitlines()),
                    "base_ceiling": base_ceiling,
                    "head_lines": head_lines,
                    "head_ceiling": head_ceiling,
                }
            )
    return sorted(violations, key=lambda item: str(item["id"]))


def evaluate_maintainability_findings(
    findings: Sequence[Mapping[str, Any]],
    *,
    reviewed_exceptions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    finding_ids = {str(item.get("id") or "") for item in findings}
    invalid_exceptions = {
        exception_id
        for exception_id, exception in reviewed_exceptions.items()
        if not exception_id
        or not str(exception.get("reason") or "").strip()
        or not str(exception.get("retirement_plan") or "").strip()
    }
    stale_exception_ids = sorted(set(reviewed_exceptions) - finding_ids)
    evaluated: list[dict[str, Any]] = []
    magnitude_regressions: list[dict[str, Any]] = []
    for finding in findings:
        finding_id = str(finding.get("id") or "")
        exception = reviewed_exceptions.get(finding_id)
        metrics = finding.get("metrics")
        metric_ceilings = exception.get("metric_ceilings") if exception else None
        metric_regressions: list[dict[str, Any]] = []
        if exception and isinstance(metrics, Mapping):
            if not isinstance(metric_ceilings, Mapping) or set(metric_ceilings) != set(
                metrics
            ):
                invalid_exceptions.add(finding_id)
            elif any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in metric_ceilings.values()
            ):
                invalid_exceptions.add(finding_id)
            else:
                metric_regressions = [
                    {
                        "metric": metric,
                        "actual": metrics[metric],
                        "ceiling": metric_ceilings[metric],
                    }
                    for metric in sorted(metrics)
                    if metrics[metric] > metric_ceilings[metric]
                ]
        review_state = "unreviewed"
        if exception:
            review_state = (
                "reviewed_exception_exceeded"
                if metric_regressions
                else "reviewed_exception"
            )
        evaluated_finding = {
            **dict(finding),
            "review_state": review_state,
            **({"reviewed_exception": dict(exception)} if exception else {}),
            **(
                {"metric_regressions": metric_regressions} if metric_regressions else {}
            ),
        }
        evaluated.append(evaluated_finding)
        if metric_regressions:
            magnitude_regressions.append(evaluated_finding)
    unreviewed = [item for item in evaluated if item["review_state"] == "unreviewed"]
    return {
        "ok": (
            not unreviewed
            and not stale_exception_ids
            and not invalid_exceptions
            and not magnitude_regressions
        ),
        "finding_count": len(evaluated),
        "category_counts": dict(
            sorted(
                Counter(
                    str(item.get("category") or "unknown") for item in evaluated
                ).items()
            )
        ),
        "reviewed_exception_count": len(evaluated) - len(unreviewed),
        "unreviewed_count": len(unreviewed),
        "stale_exception_count": len(stale_exception_ids),
        "invalid_exception_count": len(invalid_exceptions),
        "magnitude_regression_count": len(magnitude_regressions),
        "findings": evaluated,
        "unreviewed_findings": unreviewed,
        "magnitude_regressions": magnitude_regressions,
        "stale_exceptions": [
            {
                "id": exception_id,
                **dict(reviewed_exceptions[exception_id]),
            }
            for exception_id in stale_exception_ids
        ],
        "invalid_exceptions": sorted(invalid_exceptions),
    }


def build_control_plane_maintainability_report(
    repository_root: Path,
    *,
    reviewed_exceptions: Mapping[
        str, Mapping[str, Any]
    ] = REVIEWED_MAINTAINABILITY_EXCEPTIONS,
) -> dict[str, Any]:
    tracked_paths = tracked_python_paths(repository_root)
    scan_cache = _scan_repository_modules(
        repository_root,
        tracked_paths=tracked_paths,
    )
    findings = [
        *collect_dependency_debt(
            repository_root,
            tracked_paths=tracked_paths,
            _scan_cache=scan_cache,
        ),
        *collect_oversized_decision_functions(
            repository_root,
            tracked_paths=tracked_paths,
            _scan_cache=scan_cache,
        ),
        *collect_compatibility_facades(
            repository_root,
            _scan_cache=scan_cache,
        ),
        *collect_module_metric_findings(
            repository_root,
            tracked_paths=tracked_paths,
            _scan_cache=scan_cache,
        ),
    ]
    evaluation = evaluate_maintainability_findings(
        sorted(findings, key=lambda item: str(item["id"])),
        reviewed_exceptions=reviewed_exceptions,
    )
    return {
        "schema_version": MAINTAINABILITY_REPORT_SCHEMA_VERSION,
        "repository_root": display_path_text(repository_root),
        "policy": {
            "decision_statement_limit": DECISION_STATEMENT_LIMIT,
            "decision_point_limit": DECISION_POINT_LIMIT,
            "module_line_limit": MODULE_LINE_LIMIT,
            "module_any_limit": MODULE_ANY_LIMIT,
            "module_dict_any_limit": MODULE_DICT_ANY_LIMIT,
            "module_metric_baseline_path": MODULE_METRIC_BASELINE_PATH.name,
            "exception_contract": (
                "stable finding id plus non-empty reason and retirement_plan; "
                "metric-bearing debt also requires non-increasing metric ceilings; "
                "new debt, magnitude growth, and stale exceptions fail"
            ),
            "freezes_exact_line_counts": False,
            "repository_scope_decision": (
                "This profile combines semantic control-plane checks with a coarse 2000-line "
                "default hotspot limit and checked-in per-module overrides for intentional "
                "stricter contracts or grandfathered larger modules. Type-density metrics "
                "continue to ratchet per module."
            ),
        },
        **evaluation,
    }


def completed_maintainability_failure_paths(report: Mapping[str, Any]) -> list[str]:
    """Return attributable failed findings from a completed structured report."""

    if report.get("schema_version") != MAINTAINABILITY_REPORT_SCHEMA_VERSION or report.get("ok") is not False:
        return []
    # Invalid or stale policy entries do not supply attributable finding paths.
    if report.get("invalid_exceptions") != [] or report.get("stale_exceptions") != []:
        return []

    paths: set[str] = set()
    for field, count_field in (
        ("unreviewed_findings", "unreviewed_count"),
        ("magnitude_regressions", "magnitude_regression_count"),
    ):
        findings = report.get(field)
        if not isinstance(findings, list) or len(findings) != report.get(count_field):
            return []
        for finding in findings:
            path = finding.get("path") if isinstance(finding, dict) else None
            if not isinstance(path, str) or not path.strip():
                return []
            paths.add(path)
            if finding.get("category") == "module_metric_budget":
                paths.add(f"loopx/canary/{MODULE_METRIC_BASELINE_PATH.name}")
    return sorted(paths)


def render_control_plane_maintainability_report(payload: Mapping[str, Any]) -> str:
    status = "ok" if payload.get("ok") else "failed"
    category_counts = payload.get("category_counts") or {}
    lines = [
        f"control-plane-maintainability-ratchet: {status}",
        "- debt: "
        + ", ".join(
            f"{category}={count}" for category, count in sorted(category_counts.items())
        ),
        f"- reviewed_exceptions: {payload.get('reviewed_exception_count', 0)}",
        f"- unreviewed: {payload.get('unreviewed_count', 0)}",
        f"- stale_exceptions: {payload.get('stale_exception_count', 0)}",
        f"- magnitude_regressions: {payload.get('magnitude_regression_count', 0)}",
    ]
    for finding in payload.get("unreviewed_findings") or []:
        lines.append(
            f"- unreviewed finding: {finding.get('id')} path={finding.get('path')}"
        )
    for exception in payload.get("stale_exceptions") or []:
        lines.append(f"- remove stale exception: {exception.get('id')}")
    for finding in payload.get("magnitude_regressions") or []:
        for regression in finding.get("metric_regressions") or []:
            lines.append(
                "- reviewed debt worsened: "
                f"{finding.get('id')} {regression.get('metric')}="
                f"{regression.get('actual')} ceiling={regression.get('ceiling')}"
            )
    for exception_id in payload.get("invalid_exceptions") or []:
        lines.append(f"- invalid exception metadata: {exception_id}")
    # A module metric budget is settled in the checked-in ledger rather than by
    # a reviewed exception, so name that file next to the finding: it is the
    # reviewer-visible edit that decides whether the growth is accepted.
    if any(
        str(finding.get("category") or "") == "module_metric_budget"
        for finding in payload.get("findings") or []
    ):
        lines.append(
            "- module metric debt: refresh the reviewed ceiling in "
            f"{_module_metric_baseline_name(payload)} for the growth this review accepts"
        )
    return "\n".join(lines) + "\n"


def _module_metric_baseline_name(payload: Mapping[str, Any]) -> str:
    policy = payload.get("policy")
    if isinstance(policy, Mapping):
        declared = str(policy.get("module_metric_baseline_path") or "").strip()
        if declared:
            return declared
    return MODULE_METRIC_BASELINE_PATH.name
