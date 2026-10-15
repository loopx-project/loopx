"""Co-located packages do not grow new imports of `loopx` internals.

Anchored on `docs/architecture/rfcs/monorepo-distribution-split-v0.md`
Section 5: a capability package "may only register providers, capability
contracts and CLI subcommands through `CapabilityRegistry`; it never imports
kernel-private modules." Three packages already do. This guard pins those
edges exactly so a new one needs a recorded decision and a removed one
lowers the pin in the same PR, following `test_top_level_module_budget`.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_ROOT = REPO_ROOT / "packages"
PINS_FIXTURE = Path(__file__).with_name("package_kernel_import_pins.json")
PINS_SCHEMA = "loopx_package_kernel_import_pins_v0"
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _loopx_imports(source: str) -> set[str]:
    """Every `loopx.*` module a source file imports, at any nesting depth."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.split(".")[0] == "loopx":
                found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(
                alias.name for alias in node.names if alias.name.split(".")[0] == "loopx"
            )
    return found


def _observed(packages_root: Path | None = None) -> dict[str, list[str]]:
    root = packages_root or PACKAGES_ROOT
    observed: dict[str, set[str]] = {}
    for path in sorted(root.glob("*/src/**/*.py")):
        modules = _loopx_imports(path.read_text(encoding="utf-8"))
        if modules:
            observed.setdefault(path.relative_to(root).parts[0], set()).update(modules)
    return {package: sorted(modules) for package, modules in sorted(observed.items())}


def _pins() -> dict[str, Any]:
    payload = json.loads(PINS_FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema"] == PINS_SCHEMA, (
        f"{PINS_FIXTURE.name} schema is {payload['schema']!r}, expected {PINS_SCHEMA!r}"
    )
    assert _COMMIT_SHA.match(str(payload["baseline_commit"])), (
        "baseline_commit must be the full 40-character commit the pins were measured at"
    )
    imports = payload["imports"]
    assert isinstance(imports, dict) and all(
        isinstance(modules, list) and modules == sorted(set(modules))
        for modules in imports.values()
    ), "imports must map package directory -> sorted, duplicate-free module list"
    return payload


def test_packages_import_exactly_the_pinned_loopx_modules() -> None:
    pinned = {package: set(modules) for package, modules in _pins()["imports"].items()}
    observed = {package: set(modules) for package, modules in _observed().items()}

    added = {
        package: sorted(observed.get(package, set()) - pinned.get(package, set()))
        for package in observed
        if observed.get(package, set()) - pinned.get(package, set())
    }
    assert not added, (
        f"co-located packages gained imports of loopx internals: {added}. The "
        "monorepo-distribution-split RFC routes packages through CapabilityRegistry; "
        "a new kernel import needs a recorded design decision, not a pin update."
    )

    removed = {
        package: sorted(pinned.get(package, set()) - observed.get(package, set()))
        for package in pinned
        if pinned.get(package, set()) - observed.get(package, set())
    }
    assert not removed, (
        f"pinned package imports no longer exist: {removed}. Lower the pin in "
        f"{PINS_FIXTURE.name} in the same PR so the ratchet records the retirement."
    )


def test_import_scan_sees_nested_and_aliased_imports() -> None:
    source = (
        "import loopx.file_lock as fl\n"
        "def f():\n"
        "    from loopx.control_plane import effect_runtime\n"
        "    from .local import helper\n"
        "    import json\n"
    )
    assert _loopx_imports(source) == {"loopx.file_lock", "loopx.control_plane"}
