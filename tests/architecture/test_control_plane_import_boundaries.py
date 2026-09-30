from __future__ import annotations

import ast
import importlib
from importlib.util import resolve_name
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "loopx"
SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts"
CONTROL_PLANE_ROOT = PACKAGE_ROOT / "control_plane"
EXPERIMENT_ROOT = PACKAGE_ROOT / "experiments"
EXPERIMENT_PREFIX = "loopx.experiments"
STATUS_MODULE = PACKAGE_ROOT / "status.py"
QUOTA_MODULE = PACKAGE_ROOT / "quota.py"
PUBLIC_CONTRACT_ROOTS = (
    REPOSITORY_ROOT / "tests",
    REPOSITORY_ROOT / "examples",
    REPOSITORY_ROOT / "scripts",
    REPOSITORY_ROOT / "regression",
)
PUBLIC_COMPAT_FACADES = {
    "loopx.status": STATUS_MODULE,
    "loopx.quota": QUOTA_MODULE,
}
GOAL_BOUNDARY_MODULE = CONTROL_PLANE_ROOT / "quota" / "goal_boundary.py"
OPERATOR_INBOX_MODULE = CONTROL_PLANE_ROOT / "work_items" / "operator_inbox.py"
QUOTA_CLI_MODULE = PACKAGE_ROOT / "cli_commands" / "quota.py"
TURN_CLI_MODULE = PACKAGE_ROOT / "cli_commands" / "turn.py"
LARK_INBOX_CLI_MODULE = PACKAGE_ROOT / "cli_commands" / "lark_inbox.py"
PERIODIC_REPORT_REQUEST_ACTION_MODULE = (
    PACKAGE_ROOT / "capabilities" / "periodic_report" / "request_action.py"
)
EXTENSION_HOOK_ADAPTERS_MODULE = PACKAGE_ROOT / "extensions" / "hook_adapters.py"
ISSUE_FIX_REVIEWER_CLI_MODULE = (
    PACKAGE_ROOT / "capabilities" / "issue_fix" / "reviewer_cli.py"
)
ISSUE_FIX_REVIEWER_NOTIFICATION_MODULE = (
    PACKAGE_ROOT / "capabilities" / "issue_fix" / "reviewer_notification.py"
)
LARK_EXTENSION_ROOT = PACKAGE_ROOT / "extensions" / "lark"
LEGACY_LARK_CAPABILITY_ROOT = PACKAGE_ROOT / "capabilities" / "lark"
LEGACY_LARK_PRESENTATION_ROOT = PACKAGE_ROOT / "presentation" / "sinks" / "lark"
FORBIDDEN_DEPENDENCY_PREFIXES = (
    "loopx.benchmark_adapters",
    "loopx.capabilities",
    "loopx.cli",
    "loopx.cli_commands",
    "loopx.presentation",
)
STATUS_FORBIDDEN_DEPENDENCY_PREFIXES = (
    "loopx.benchmark_adapters",
    "loopx.presentation",
)


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(("loopx", *parts))


def _resolved_imports(path: Path) -> set[str]:
    module_name = _module_name(path)
    package_name = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package_name)
            imports.add(module)
    return imports


def _resolved_from_imports(path: Path) -> dict[str, set[str]]:
    package_name = ""
    if path.is_relative_to(PACKAGE_ROOT):
        module_name = _module_name(path)
        package_name = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if node.level:
            if not package_name:
                continue
            module = resolve_name("." * node.level + module, package_name)
        imports.setdefault(module, set()).update(alias.name for alias in node.names)
    return imports


def _top_level_imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }


def _top_level_function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _top_level_import_bindings(path: Path) -> dict[str, str]:
    module_name = _module_name(path)
    package_name = module_name.rpartition(".")[0]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bindings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bindings[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package_name)
            for alias in node.names:
                bindings[alias.asname or alias.name] = f"{module}.{alias.name}"
    return bindings


def _public_import_only_bindings(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    loaded_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return {
        name: target
        for name, target in _top_level_import_bindings(path).items()
        if name != "annotations" and not name.startswith("_") and name not in loaded_names
    }


def _public_contract_evidence() -> dict[tuple[str, str], set[str]]:
    evidence: dict[tuple[str, str], set[str]] = {}
    for root in PUBLIC_CONTRACT_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            # Aliases such as ``from loopx import status as status_module`` are a
            # real consumer of a facade export, reached by attribute access
            # rather than a from-import. Without this map the audit would report
            # a live consumer as missing evidence.
            module_aliases: dict[str, str] = {}
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level:
                    continue
                module = node.module or ""
                for alias in node.names:
                    # ``from loopx import status`` binds the facade itself; a
                    # from-import of a facade also binds the facade's exports.
                    candidate = alias.name if not module else f"{module}.{alias.name}"
                    if candidate in PUBLIC_COMPAT_FACADES:
                        module_aliases[alias.asname or alias.name] = candidate
                        continue
                    if module in PUBLIC_COMPAT_FACADES:
                        evidence.setdefault((module, alias.name), set()).add(
                            str(path.relative_to(REPOSITORY_ROOT))
                        )
            if not module_aliases:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load):
                    continue
                facade_name = module_aliases.get(getattr(node.value, "id", ""))
                if facade_name is not None:
                    evidence.setdefault((facade_name, node.attr), set()).add(
                        str(path.relative_to(REPOSITORY_ROOT))
                    )
    for path in (REPOSITORY_ROOT / "docs").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for facade_name in PUBLIC_COMPAT_FACADES:
            facade = importlib.import_module(facade_name)
            for export_name in facade._PUBLIC_COMPAT_REEXPORTS:
                if (
                    f"{facade_name}.{export_name}" in text
                    or f"from {facade_name} import {export_name}" in text
                ):
                    evidence.setdefault((facade_name, export_name), set()).add(
                        str(path.relative_to(REPOSITORY_ROOT))
                    )
    return evidence


def test_control_plane_does_not_gain_outward_dependencies() -> None:
    outward_dependencies = {
        (_module_name(path), dependency)
        for path in CONTROL_PLANE_ROOT.rglob("*.py")
        for dependency in _resolved_imports(path)
        if any(
            dependency == prefix or dependency.startswith(prefix + ".")
            for prefix in FORBIDDEN_DEPENDENCY_PREFIXES
        )
    }

    assert not outward_dependencies, (
        "control-plane code must not depend on presentation, CLI, capability, or "
        f"benchmark-adapter layers; unexpected edges: {sorted(outward_dependencies)}"
    )


def test_periodic_report_hook_does_not_break_cold_imports() -> None:
    periodic_report_facade = (
        PACKAGE_ROOT / "capabilities" / "periodic_report" / "__init__.py"
    )
    facade_imports = _resolved_imports(periodic_report_facade)
    assert not {
        "loopx.capabilities.periodic_report.post_writeback_hook",
        "loopx.capabilities.periodic_report.stage_completion",
    } & facade_imports

    import_statements = (
        "import loopx.quota",
        (
            "from loopx.control_plane.turn_driver.turn_journal_runtime "
            "import interpret_turn_journal_projection"
        ),
    )

    for statement in import_statements:
        completed = subprocess.run(
            [sys.executable, "-c", statement],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr


def test_read_adapters_do_not_load_writeback_facades_for_leaf_helpers() -> None:
    # Run independently: an earlier test importing a writer must not hide an
    # accidental cold-start dependency of a read-only entrypoint.
    for module, unrelated in (
        ("loopx.control_plane.coordination.local_authority", "loopx.history"),
        ("loopx.cli_commands.status", "loopx.state_refresh"),
    ):
        completed = subprocess.run(
            [sys.executable, "-c", (
                f"import sys; import {module}; "
                f"assert {unrelated!r} not in sys.modules, {unrelated!r}"
            )],
            cwd=REPOSITORY_ROOT, capture_output=True, check=False, text=True,
        )
        assert completed.returncode == 0, completed.stderr


def test_core_does_not_import_experiments() -> None:
    forbidden_edges = {
        (_module_name(path), dependency)
        for path in PACKAGE_ROOT.rglob("*.py")
        if not path.is_relative_to(EXPERIMENT_ROOT)
        for dependency in _resolved_imports(path)
        if dependency == EXPERIMENT_PREFIX
        or dependency.startswith(EXPERIMENT_PREFIX + ".")
    }

    assert not forbidden_edges, (
        "stable LoopX code must not depend on opt-in experiments; "
        f"unexpected edges: {sorted(forbidden_edges)}"
    )


def test_quota_markdown_is_owned_by_the_presentation_layer() -> None:
    legacy_renderer = CONTROL_PLANE_ROOT / "quota" / "markdown.py"
    imports = _resolved_imports(QUOTA_MODULE)
    cli_imports = _resolved_from_imports(QUOTA_CLI_MODULE)
    presentation_renderers = cli_imports.get(
        "loopx.presentation.renderers.quota_event_markdown", set()
    )

    assert not legacy_renderer.exists()
    assert "loopx.presentation.renderers.quota_markdown" in imports
    assert {
        "render_quota_monitor_poll_markdown",
        "render_quota_slot_preview_markdown",
    } <= presentation_renderers
    assert "loopx.presentation.renderers.quota_event_markdown" in imports
    assert "render_quota_monitor_poll_markdown" not in _top_level_function_names(
        CONTROL_PLANE_ROOT / "quota" / "monitor_poll.py"
    )
    assert "render_quota_slot_preview_markdown" not in _top_level_function_names(
        CONTROL_PLANE_ROOT / "quota" / "slot_accounting.py"
    )


def test_internal_consumers_bypass_status_and_quota_reexport_routes() -> None:
    facade_reexports = {
        "loopx.status": _top_level_imported_names(STATUS_MODULE),
        "loopx.quota": _top_level_imported_names(QUOTA_MODULE),
    }
    internal_paths = (
        path
        for root in (PACKAGE_ROOT, SCRIPTS_ROOT)
        for path in root.rglob("*.py")
        if path not in {STATUS_MODULE, QUOTA_MODULE}
    )
    indirect_imports = {
        (_module_name(path) if path.is_relative_to(PACKAGE_ROOT) else str(path.relative_to(REPOSITORY_ROOT)), facade, name)
        for path in internal_paths
        for facade, imported_names in _resolved_from_imports(path).items()
        if facade in facade_reexports
        for name in imported_names & facade_reexports[facade]
    }

    assert not indirect_imports, (
        "repository-internal consumers must import extracted symbols from their "
        f"canonical modules instead of status/quota re-export routes: {sorted(indirect_imports)}"
    )


def test_public_facade_import_only_reexports_match_the_audited_allowlist() -> None:
    for facade_name, source_path in PUBLIC_COMPAT_FACADES.items():
        facade = importlib.import_module(facade_name)
        allowlist = getattr(facade, "_PUBLIC_COMPAT_REEXPORTS")
        import_only_bindings = _public_import_only_bindings(source_path)

        assert import_only_bindings == {
            export_name: f"{canonical_module}.{export_name}"
            for export_name, canonical_module in allowlist.items()
        }
        for export_name, canonical_module_name in allowlist.items():
            canonical_module = importlib.import_module(canonical_module_name)
            assert getattr(facade, export_name) is getattr(
                canonical_module, export_name
            )


def test_public_facade_compatibility_entries_have_contract_evidence() -> None:
    evidence = _public_contract_evidence()
    missing_evidence: list[tuple[str, str]] = []
    for facade_name in PUBLIC_COMPAT_FACADES:
        facade = importlib.import_module(facade_name)
        for export_name in facade._PUBLIC_COMPAT_REEXPORTS:
            if not evidence.get((facade_name, export_name)):
                missing_evidence.append((facade_name, export_name))

    assert not missing_evidence, (
        "import-only compatibility exports need a repository consumer or explicit "
        f"public documentation; remove stale entries: {missing_evidence}"
    )


def test_status_has_no_forbidden_outward_dependencies() -> None:
    outward_dependencies = {
        (_module_name(STATUS_MODULE), dependency)
        for dependency in _resolved_imports(STATUS_MODULE)
        if any(
            dependency == prefix or dependency.startswith(prefix + ".")
            for prefix in STATUS_FORBIDDEN_DEPENDENCY_PREFIXES
        )
    }

    assert not outward_dependencies, (
        "loopx.status must not depend on benchmark-adapter or presentation layers; "
        f"unexpected edges: {sorted(outward_dependencies)}"
    )


def test_quota_receives_lark_urgency_only_through_cli_composition() -> None:
    for module in (QUOTA_MODULE, GOAL_BOUNDARY_MODULE):
        imports = _resolved_imports(module)
        assert not any(
            dependency == "loopx.extensions.lark"
            or dependency.startswith("loopx.extensions.lark.")
            for dependency in imports
        )

    assert "loopx.cli_commands.lark_inbox" in _resolved_imports(QUOTA_CLI_MODULE)
    assert "loopx.cli_commands.lark_inbox" in _resolved_imports(TURN_CLI_MODULE)


def test_periodic_report_request_ports_are_manifest_discovered_outside_kernel() -> None:
    generic_modules = (
        PERIODIC_REPORT_REQUEST_ACTION_MODULE,
        EXTENSION_HOOK_ADAPTERS_MODULE,
        QUOTA_CLI_MODULE,
    )
    for module in generic_modules:
        assert not any(
            dependency == "loopx.extensions.lark"
            or dependency.startswith("loopx.extensions.lark.")
            for dependency in _resolved_imports(module)
        )
    assert not any(
        dependency == "loopx.control_plane"
        or dependency.startswith("loopx.control_plane.")
        for dependency in _resolved_imports(EXTENSION_HOOK_ADAPTERS_MODULE)
    )


def test_lark_operator_inbox_contract_is_extension_owned() -> None:
    core_source = OPERATOR_INBOX_MODULE.read_text(encoding="utf-8")
    extension_source = (LARK_EXTENSION_ROOT / "event_inbox.py").read_text(
        encoding="utf-8"
    )

    assert "LARK_OPERATOR_INBOX_SOURCE_CONTRACT" not in core_source
    assert "lark_event_inbox_config_v0" not in core_source
    assert "LARK_OPERATOR_INBOX_SOURCE_CONTRACT" in extension_source
    assert "OperatorInboxSourceContract" in extension_source


def test_lark_inbox_provider_is_owned_by_the_extension_layer() -> None:
    legacy_provider_modules = {
        "event_collector.py",
        "event_collector_runtime.py",
        "event_inbox.py",
        "inbox_reply.py",
    }
    assert not any(
        (LEGACY_LARK_CAPABILITY_ROOT / name).exists()
        for name in legacy_provider_modules
    )
    legacy_imports = {
        f"loopx.capabilities.lark.{Path(name).stem}"
        for name in legacy_provider_modules
    }
    remaining_legacy_imports = {
        (_module_name(path), dependency)
        for path in PACKAGE_ROOT.rglob("*.py")
        for dependency in _resolved_imports(path)
        if dependency in legacy_imports
    }
    assert not remaining_legacy_imports

    imports = _resolved_imports(LARK_INBOX_CLI_MODULE)
    assert {
        "loopx.extensions.lark.event_collector",
        "loopx.extensions.lark.event_collector_runtime",
        "loopx.extensions.lark.event_inbox",
        "loopx.extensions.lark.inbox_reply",
        "loopx.extensions.runtime",
    } <= imports
    assert (LARK_EXTENSION_ROOT / "extension.toml").is_file()
    assert (LARK_EXTENSION_ROOT / "provider.py").is_file()


def test_lark_projection_sinks_are_owned_by_the_extension_layer() -> None:
    assert not list(LEGACY_LARK_CAPABILITY_ROOT.glob("*.py"))
    assert not list(LEGACY_LARK_PRESENTATION_ROOT.glob("*.py"))
    extension_presentation = LARK_EXTENSION_ROOT / "presentation"
    assert (extension_presentation / "kanban.py").is_file()
    assert (extension_presentation / "explore_results.py").is_file()

    core_provider_imports = {
        (_module_name(path), dependency)
        for root in (PACKAGE_ROOT / "capabilities", PACKAGE_ROOT / "presentation")
        for path in root.rglob("*.py")
        for dependency in _resolved_imports(path)
        if dependency == "loopx.extensions.lark"
        or dependency.startswith("loopx.extensions.lark.")
    }
    assert not core_provider_imports


def test_issue_fix_reviewer_uses_provider_neutral_inbox_hooks() -> None:
    imports = _resolved_imports(ISSUE_FIX_REVIEWER_CLI_MODULE)

    assert not any(
        dependency == "loopx.extensions.lark"
        or dependency.startswith("loopx.extensions.lark.")
        for dependency in imports
    )
    assert "loopx.capabilities.issue_fix.provider_hooks" in imports


def test_issue_fix_reviewer_notification_provider_is_extension_owned() -> None:
    imports = _resolved_imports(ISSUE_FIX_REVIEWER_NOTIFICATION_MODULE)
    source = ISSUE_FIX_REVIEWER_NOTIFICATION_MODULE.read_text(encoding="utf-8")
    extension_module = LARK_EXTENSION_ROOT / "reviewer_notification.py"

    assert not any(
        dependency == "loopx.extensions.lark"
        or dependency.startswith("loopx.extensions.lark.")
        for dependency in imports
    )
    assert "lark-cli" not in source
    assert "lark_chat" not in source
    assert "reader_profile" not in source
    assert "_lark_result" not in source
    assert extension_module.is_file()
    assert (
        "loopx.capabilities.issue_fix.reviewer_notification"
        in _resolved_imports(extension_module)
    )
