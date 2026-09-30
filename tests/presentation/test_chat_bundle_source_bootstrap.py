"""A source checkout has to be able to build its own frontend without LoopX installed.

`loopx/presentation/chat_bundle.py` asks the digest owner what a stored SHA-256 looks
like, and three places load that module by file path: `scripts/chat_bundle.py`,
`scripts/desktop_runtime_bundle.py` and `setup.py` (whose `build_py`/`sdist` hooks verify
the bundle for a non-editable install). A file-path load gives the module no package
context, so its import of the owner only resolves if the loader made the checkout
importable first - which is exactly what a checkout that has never been `pip install`ed
does not otherwise have.

Two halves, both needed:

* the real entry point is run under an interpreter with `site` disabled, so an installed
  LoopX cannot rescue it, and the reported failure has to be the documented "bundle not
  built yet" state rather than an import error;
* a de-bootstrapped copy of the same two files is run the same way and has to fail with
  the import error. Without that control this file cannot tell a guard from a tautology.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = "loopx/presentation/chat_bundle.py"
IMPORT_ERROR = "No module named"


def _loads_contract(node: ast.AST) -> bool:
    """Is this `spec_from_file_location(...)` call pointing at the chat bundle contract?"""

    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "spec_from_file_location"
        and CONTRACT in ast.dump(node)
    )


def _loader_files() -> list[Path]:
    """Every script or packaging file that execs the contract by file path."""

    candidates = [ROOT / "setup.py", ROOT / "scripts"]
    found: list[Path] = []
    for candidate in candidates:
        paths = [candidate] if candidate.is_file() else sorted(candidate.rglob("*.py"))
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _loads_contract(node):
                    found.append(path)
                    break
    return found


def test_every_loader_of_the_contract_makes_the_checkout_importable() -> None:
    """A new file-path load of the contract has to bring the root onto sys.path too.

    Checked structurally: the module-level `sys.path` insertion must be present in the
    loader, because the contract no longer carries its own copy of the digest shape.
    """

    loaders = _loader_files()
    assert len(loaders) >= 3, (
        f"expected the three known loaders of {CONTRACT}, found {[str(p) for p in loaders]}"
    )
    missing = []
    for path in loaders:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        assigns = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "insert"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "path"
        ]
        if not assigns:
            missing.append(str(path.relative_to(ROOT)))
    assert not missing, (
        f"loader(s) of the contract that never touch sys.path: {missing}"
    )


def _run_uninstalled(
    script: Path, *arguments: str, cwd: Path
) -> subprocess.CompletedProcess:
    """Run a script with `site` disabled, so an installed LoopX cannot answer for the tree."""

    # `-I` ignores PYTHONPATH and user site, `-S` hides site-packages; together they are
    # what stops an installed LoopX from rescuing a checkout-local loader. The environment
    # is inherited minus PYTHONPATH, because on Windows a stripped environment loses
    # SystemRoot and the interpreter then fails for a reason this test is not about.
    environment = {
        key: value for key, value in os.environ.items() if key != "PYTHONPATH"
    }
    return subprocess.run(
        [sys.executable, "-I", "-S", str(script), *arguments],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_the_real_entry_point_starts_without_an_installed_loopx(tmp_path: Path) -> None:
    """`--help` must succeed, and `verify` must fail for the bundle, not for the import.

    On a checkout that has never built the frontend there is no bundle to verify, so the
    expected outcome for `verify` is the documented missing-bundle error. The point of this
    case is the *absence* of an import error, which is the regression this branch fixed.
    """

    help_result = _run_uninstalled(
        ROOT / "scripts" / "chat_bundle.py", "--help", cwd=tmp_path
    )
    assert help_result.returncode == 0, help_result.stderr
    assert IMPORT_ERROR not in help_result.stderr + help_result.stdout
    assert "build" in help_result.stdout

    verify_result = _run_uninstalled(
        ROOT / "scripts" / "chat_bundle.py", "verify", cwd=tmp_path
    )
    combined = verify_result.stdout + verify_result.stderr
    assert IMPORT_ERROR not in combined, combined
    assert "bundle-manifest.json" in combined or verify_result.returncode == 0, combined


def test_the_bootstrap_is_load_bearing(tmp_path: Path) -> None:
    """Remove those two lines and the same command must die at the import instead.

    Without this control the tests above would pass in any environment that happens to have
    LoopX installed, and would prove nothing about a bare source checkout.
    """

    sandbox = tmp_path / "checkout"
    (sandbox / "scripts").mkdir(parents=True, exist_ok=True)
    for relative in (
        "loopx/__init__.py",
        "loopx/presentation/__init__.py",
        "loopx/control_plane/__init__.py",
    ):
        target = sandbox / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    for relative in (
        "loopx/presentation/chat_bundle.py",
        "loopx/control_plane/content_digest.py",
    ):
        source = ROOT / relative
        destination = sandbox / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    script = (ROOT / "scripts" / "chat_bundle.py").read_text(encoding="utf-8")
    with_bootstrap = sandbox / "scripts" / "chat_bundle_with_bootstrap.py"
    without_bootstrap = sandbox / "scripts" / "chat_bundle_without_bootstrap.py"
    with_bootstrap.write_text(script, encoding="utf-8")

    dropped = (
        "if str(ROOT) not in sys.path:",
        "sys.path.insert(0, str(ROOT))",
    )
    stripped = "".join(
        line for line in script.splitlines(keepends=True) if line.strip() not in dropped
    )
    assert "sys.path.insert" not in stripped
    assert len(stripped.splitlines()) == len(script.splitlines()) - 2, stripped
    without_bootstrap.write_text(stripped, encoding="utf-8")

    kept = _run_uninstalled(with_bootstrap, "--help", cwd=tmp_path)
    assert kept.returncode == 0, kept.stderr
    removed = _run_uninstalled(without_bootstrap, "--help", cwd=tmp_path)
    assert removed.returncode != 0
    assert IMPORT_ERROR in removed.stderr, removed.stderr
    assert "loopx" in removed.stderr
