"""Inventory project-registry I/O without importing production modules."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import subprocess
from typing import Any

from .inventory import SourceFile
from .production import run_typescript_scan


PROJECT_REGISTRY_IO_MANIFEST_SCHEMA = "loopx_project_registry_io_manifest_v1"
DIRECT_IO_CLASSIFICATIONS_PATH = (
    Path("loopx") / "semantics" / "project_registry_io_classifications_v1.json"
)
SOURCE_ROOT = "loopx"
PROJECT_REGISTRY_SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".mjs"})
EXCLUDED_PREFIXES = (
    "loopx/control_plane/testing/",
    "loopx/web/chat/assets/",
)
EXCLUDED_PARTS = frozenset({"__pycache__", "node_modules", "tests"})

APPROVED_READ_APIS = frozenset(
    {
        "decode_project_registry",
        "decode_registry_snapshot",
        "load_project_registry",
        "load_registry",
    }
)
APPROVED_WRITE_APIS = frozenset({"mutate_project_registry"})
APPROVED_TRANSACTION_APIS = frozenset(
    {
        "project_registry_transaction",
        "source_session_registry_transaction",
    }
)
DIRECT_READ_APIS = frozenset(
    {"read_json", "read_json_object", "_read_json", "parse_json_object"}
)
DIRECT_WRITE_APIS = frozenset(
    {"atomic_write_json", "write_json", "_atomic_write_json"}
)
DIRECT_WRITE_METHODS = frozenset({"write_bytes", "write_text"})
ALLOWED_DIRECT_CLASSIFICATIONS = frozenset(
    {"codec_internal", "global_registry_io", "legacy_registry_source"}
)


class _PathRole(str, Enum):
    UNKNOWN = "unknown"
    PROJECT = "project"
    GLOBAL = "global"


@dataclass(frozen=True, order=True, slots=True)
class RegistryIOObservation:
    path: str
    scope: str
    line: int
    column: int
    kind: str
    api: str


def _excluded(relative: str) -> bool:
    path = Path(relative)
    return any(relative.startswith(prefix) for prefix in EXCLUDED_PREFIXES) or bool(
        EXCLUDED_PARTS.intersection(path.parts)
    )


def load_registry_io_sources(repo_root: Path) -> list[SourceFile]:
    """Load the tracked importable product tree used by the census."""

    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "-z", "--", SOURCE_ROOT],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.decode("utf-8").split("\0")
    result: list[SourceFile] = []
    for relative in sorted(set(tracked) - {""}):
        path = repo_root / relative
        if (
            _excluded(relative)
            or path.is_symlink()
            or not path.is_file()
            or path.suffix not in PROJECT_REGISTRY_SOURCE_SUFFIXES
        ):
            continue
        result.append(
            SourceFile(
                path=relative,
                suffix=path.suffix,
                text=path.read_text(encoding="utf-8", errors="replace"),
            )
        )
    return result


def _terminal_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _identifier_role(name: str) -> _PathRole:
    normalized = name.lower()
    if "global" in normalized and "registr" in normalized:
        return _PathRole.GLOBAL
    if normalized in {"global_path", "global_registry"}:
        return _PathRole.GLOBAL
    if "registr" in normalized and not normalized.endswith(
        ("payload", "record", "records", "goals", "data")
    ):
        return _PathRole.PROJECT
    return _PathRole.UNKNOWN


def _merge_roles(roles: list[_PathRole]) -> _PathRole:
    if _PathRole.GLOBAL in roles:
        return _PathRole.GLOBAL
    if _PathRole.PROJECT in roles:
        return _PathRole.PROJECT
    return _PathRole.UNKNOWN


class _PythonRegistryIOScanner(ast.NodeVisitor):
    def __init__(self, source: SourceFile) -> None:
        self.source = source
        self.observations: list[RegistryIOObservation] = []
        self.scopes = ["<module>"]
        self.roles: list[dict[str, _PathRole]] = [{}]
        self.handles: list[dict[str, _PathRole]] = [{}]
        self.contents: list[dict[str, _PathRole]] = [{}]

    @property
    def scope(self) -> str:
        return ".".join(self.scopes)

    def _lookup(
        self, environments: list[dict[str, _PathRole]], name: str
    ) -> _PathRole:
        for environment in reversed(environments):
            if name in environment:
                return environment[name]
        return _PathRole.UNKNOWN

    def _role(self, node: ast.AST | None) -> _PathRole:
        if node is None:
            return _PathRole.UNKNOWN
        if isinstance(node, ast.Name):
            assigned = self._lookup(self.roles, node.id)
            return assigned if assigned is not _PathRole.UNKNOWN else _identifier_role(node.id)
        if isinstance(node, ast.Attribute):
            own = _identifier_role(node.attr)
            return own if own is not _PathRole.UNKNOWN else self._role(node.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            normalized = node.value.replace("\\", "/").lower()
            if "registry.global.json" in normalized:
                return _PathRole.GLOBAL
            if normalized.endswith("/.loopx/registry.json") or normalized in {
                ".loopx/registry.json",
                "registry.json",
            }:
                return _PathRole.PROJECT
            return _PathRole.UNKNOWN
        if isinstance(node, ast.Call):
            name = _terminal_name(node.func) or ""
            if name == "global_registry_path":
                return _PathRole.GLOBAL
            if name in {"default_registry_path", "find_registry", "_registry_path"}:
                return _PathRole.PROJECT
            if name == "Path" and node.args:
                return self._role(node.args[0])
            if (
                isinstance(node.func, ast.Attribute)
                and name in {"absolute", "expanduser", "resolve"}
            ):
                return self._role(node.func.value)
            return _PathRole.UNKNOWN
        if isinstance(node, ast.BinOp):
            return _merge_roles([self._role(node.left), self._role(node.right)])
        if isinstance(node, ast.IfExp):
            return _merge_roles([self._role(node.body), self._role(node.orelse)])
        return _PathRole.UNKNOWN

    def _content_role(self, node: ast.AST | None) -> _PathRole:
        if isinstance(node, ast.Name):
            return self._lookup(self.contents, node.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"read_bytes", "read_text"}:
                return self._role(node.func.value)
        return _PathRole.UNKNOWN

    def _handle_role(self, node: ast.AST | None) -> _PathRole:
        if isinstance(node, ast.Name):
            return self._lookup(self.handles, node.id)
        return _PathRole.UNKNOWN

    def _record(self, node: ast.AST, *, kind: str, api: str) -> None:
        self.observations.append(
            RegistryIOObservation(
                path=self.source.path,
                scope=self.scope,
                line=node.lineno,
                column=node.col_offset + 1,
                kind=kind,
                api=api,
            )
        )

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        self.scopes.append(node.name)
        local_roles: dict[str, _PathRole] = {}
        if "registry" in node.name.lower() and "global" not in node.name.lower():
            arguments = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            for argument in arguments:
                if argument.arg in {"path", "source", "target"}:
                    local_roles[argument.arg] = _PathRole.PROJECT
        self.roles.append(local_roles)
        self.handles.append({})
        self.contents.append({})
        for statement in node.body:
            self.visit(statement)
        self.contents.pop()
        self.handles.pop()
        self.roles.pop()
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scopes.append(node.name)
        self.roles.append({})
        self.handles.append({})
        self.contents.append({})
        for statement in node.body:
            self.visit(statement)
        self.contents.pop()
        self.handles.pop()
        self.roles.pop()
        self.scopes.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        role = self._role(node.value)
        content_role = self._content_role(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if role is not _PathRole.UNKNOWN:
                    self.roles[-1][target.id] = role
                if content_role is not _PathRole.UNKNOWN:
                    self.contents[-1][target.id] = content_role
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            role = self._role(node.value)
            content_role = self._content_role(node.value)
            if role is not _PathRole.UNKNOWN:
                self.roles[-1][node.target.id] = role
            if content_role is not _PathRole.UNKNOWN:
                self.contents[-1][node.target.id] = content_role
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        handle_roles: dict[str, _PathRole] = {}
        for item in node.items:
            context = item.context_expr
            if (
                isinstance(context, ast.Call)
                and isinstance(context.func, ast.Attribute)
                and context.func.attr == "open"
                and isinstance(item.optional_vars, ast.Name)
            ):
                role = self._role(context.func.value)
                handle_roles[item.optional_vars.id] = role
                mode = (
                    context.args[0].value
                    if context.args
                    and isinstance(context.args[0], ast.Constant)
                    and isinstance(context.args[0].value, str)
                    else ""
                )
                if role is _PathRole.PROJECT and any(flag in mode for flag in "wax+"):
                    self._record(context, kind="direct_json_write", api="Path.open")
            self.visit(context)
        self.handles.append(handle_roles)
        for statement in node.body:
            self.visit(statement)
        self.handles.pop()

    visit_AsyncWith = visit_With

    def visit_Call(self, node: ast.Call) -> None:
        name = _terminal_name(node.func)
        if name in APPROVED_READ_APIS:
            self._record(node, kind="codec_read", api=name)
        elif name in APPROVED_WRITE_APIS:
            self._record(node, kind="codec_write", api=name)
        elif name in APPROVED_TRANSACTION_APIS:
            self._record(node, kind="codec_transaction", api=name)
        elif name in DIRECT_READ_APIS and node.args:
            if self._role(node.args[0]) is _PathRole.PROJECT:
                self._record(node, kind="direct_json_read", api=name)
        elif name in DIRECT_WRITE_APIS and node.args:
            if self._role(node.args[0]) is _PathRole.PROJECT:
                self._record(node, kind="direct_json_write", api=name)
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in DIRECT_WRITE_METHODS
            and self._role(node.func.value) is _PathRole.PROJECT
        ):
            self._record(node, kind="direct_json_write", api=f"Path.{node.func.attr}")
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "json"
            and node.func.attr in {"load", "loads"}
            and node.args
        ):
            role = (
                self._handle_role(node.args[0])
                if node.func.attr == "load"
                else self._content_role(node.args[0])
            )
            if role is _PathRole.PROJECT:
                self._record(node, kind="direct_json_read", api=f"json.{node.func.attr}")
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "json"
            and node.func.attr == "dump"
            and len(node.args) >= 2
            and self._handle_role(node.args[1]) is _PathRole.PROJECT
        ):
            self._record(node, kind="direct_json_write", api="json.dump")
        self.generic_visit(node)


def scan_python_registry_io(source: SourceFile) -> list[RegistryIOObservation]:
    try:
        tree = ast.parse(source.text, filename=source.path)
    except SyntaxError as error:
        raise ValueError(
            f"cannot scan invalid Python source: {source.path}:{error.lineno}"
        ) from None
    scanner = _PythonRegistryIOScanner(source)
    scanner.visit(tree)
    return scanner.observations


def _scan_javascript_registry_io(
    repo_root: Path, sources: list[SourceFile]
) -> list[RegistryIOObservation]:
    rows = run_typescript_scan(
        repo_root,
        sources,
        {
            "mode": "registry_io",
            "approved_reads": sorted(APPROVED_READ_APIS),
            "approved_writes": sorted(APPROVED_WRITE_APIS),
            "approved_transactions": sorted(APPROVED_TRANSACTION_APIS),
        },
    )
    return [
        RegistryIOObservation(
            path=row["path"],
            scope=row["scope"],
            line=row["line"],
            column=row["column"],
            kind=row["kind"],
            api=row["api"],
        )
        for row in rows
    ]


def collect_project_registry_io(
    repo_root: Path, sources: list[SourceFile] | None = None
) -> list[RegistryIOObservation]:
    selected = sources if sources is not None else load_registry_io_sources(repo_root)
    observations: list[RegistryIOObservation] = []
    for source in selected:
        if source.suffix == ".py":
            observations.extend(scan_python_registry_io(source))
    javascript = [
        source
        for source in selected
        if source.suffix in {".ts", ".tsx", ".js", ".mjs"}
    ]
    observations.extend(_scan_javascript_registry_io(repo_root, javascript))
    return sorted(observations)


def _site_rows(
    observations: list[RegistryIOObservation],
    *,
    classifications: dict[str, str],
) -> list[dict[str, Any]]:
    counters: Counter[tuple[str, str, str, str]] = Counter()
    rows: list[dict[str, Any]] = []
    for observation in observations:
        identity = (
            observation.path,
            observation.scope,
            observation.kind,
            observation.api,
        )
        counters[identity] += 1
        site = (
            f"{observation.path}::{observation.scope}::"
            f"{observation.kind}:{observation.api}#{counters[identity]}"
        )
        classification = (
            "codec_api"
            if observation.kind.startswith("codec_")
            else classifications.get(site, "unclassified")
        )
        rows.append(
            {
                "site": site,
                "line": observation.line,
                "column": observation.column,
                "kind": observation.kind,
                "api": observation.api,
                "classification": classification,
            }
        )
    return rows


def build_project_registry_io_manifest(
    repo_root: Path,
    *,
    classifications: dict[str, str] | None = None,
    sources: list[SourceFile] | None = None,
) -> dict[str, Any]:
    """Compute the census; only reviewed direct-I/O classifications are tracked."""
    if classifications is None:
        classifications = load_project_registry_io_classifications(repo_root)
    observations = collect_project_registry_io(repo_root, sources)
    return {
        "schema_version": PROJECT_REGISTRY_IO_MANIFEST_SCHEMA,
        "source_policy": {
            "root": SOURCE_ROOT,
            "suffixes": sorted(PROJECT_REGISTRY_SOURCE_SUFFIXES),
            "excluded_prefixes": list(EXCLUDED_PREFIXES),
            "excluded_parts": sorted(EXCLUDED_PARTS),
            "scripts": "excluded_non_importable_tooling",
        },
        "sites": _site_rows(observations, classifications=classifications),
    }


def load_project_registry_io_classifications(repo_root: Path) -> dict[str, str]:
    path = repo_root / DIRECT_IO_CLASSIFICATIONS_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or any(
        not isinstance(site, str) or not isinstance(classification, str)
        for site, classification in payload.items()
    ):
        raise ValueError("project registry I/O classifications must map sites to strings")
    return payload


def render_project_registry_io_manifest(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def validate_project_registry_io_manifest(
    repo_root: Path,
    manifest: dict[str, Any] | None = None,
    *,
    classifications: dict[str, str] | None = None,
    sources: list[SourceFile] | None = None,
) -> list[str]:
    """Check current source policy, or an explicit export against current source."""
    if manifest is not None and manifest.get("schema_version") != PROJECT_REGISTRY_IO_MANIFEST_SCHEMA:
        return ["project registry I/O manifest schema is unsupported"]
    if classifications is None:
        classifications = load_project_registry_io_classifications(repo_root)
    expected = build_project_registry_io_manifest(
        repo_root,
        classifications=classifications,
        sources=sources,
    )
    if manifest is None:
        manifest = expected
    actual_rows = manifest.get("sites")
    if not isinstance(actual_rows, list):
        return ["project registry I/O manifest sites must be a list"]
    errors: list[str] = []
    direct_sites = {
        row["site"] for row in expected["sites"]
        if row["kind"] in {"direct_json_read", "direct_json_write"}
    }
    stale_classifications = sorted(set(classifications) - direct_sites)
    if stale_classifications:
        errors.append(f"stale project registry I/O classifications: {stale_classifications}")
    for classified_site, label in sorted(classifications.items()):
        if label not in ALLOWED_DIRECT_CLASSIFICATIONS:
            errors.append(f"invalid project registry I/O classification: {classified_site}: {label}")
    site_names = [
        row.get("site")
        for row in actual_rows
        if isinstance(row, dict) and isinstance(row.get("site"), str)
    ]
    duplicate_sites = sorted(
        site for site, count in Counter(site_names).items() if count > 1
    )
    if duplicate_sites:
        errors.append(f"duplicate project registry I/O sites: {duplicate_sites}")
    actual_by_site = {
        row.get("site"): row
        for row in actual_rows
        if isinstance(row, dict) and isinstance(row.get("site"), str)
    }
    expected_by_site = {row["site"]: row for row in expected["sites"]}
    stale = sorted(set(actual_by_site) - set(expected_by_site))
    missing = sorted(set(expected_by_site) - set(actual_by_site))
    if stale:
        errors.append(f"stale project registry I/O sites: {stale}")
    if missing:
        errors.append(f"unregistered project registry I/O sites: {missing}")
    for site in sorted(set(actual_by_site) & set(expected_by_site)):
        if actual_by_site[site] != expected_by_site[site]:
            errors.append(f"project registry I/O site metadata changed: {site}")
    if manifest.get("source_policy") != expected["source_policy"]:
        errors.append("project registry I/O source policy changed")
    for row in actual_rows:
        if not isinstance(row, dict):
            errors.append("project registry I/O site must be an object")
            continue
        kind = row.get("kind")
        classification = row.get("classification")
        site = row.get("site")
        if kind in {"codec_read", "codec_write", "codec_transaction"}:
            if classification != "codec_api":
                errors.append(f"codec site has invalid classification: {site}")
        elif kind in {"direct_json_read", "direct_json_write"}:
            if classification not in ALLOWED_DIRECT_CLASSIFICATIONS:
                errors.append(f"unclassified direct project registry I/O: {site}")
        else:
            errors.append(f"project registry I/O site has unknown kind: {site}")
    return errors
