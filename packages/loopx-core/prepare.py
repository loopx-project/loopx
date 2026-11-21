"""Prepare split LoopX source distributions from the single source tree.

Build the returned directory with a PEP 517 frontend. No runtime code is copied
back into the repository and the ordinary distribution remains unchanged.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from runpy import run_path

REPOSITORY = Path(__file__).resolve().parents[2]
# These are distribution exclusions, not an authority or activation policy.
EXCLUDED_TREES = ("loopx/web/", "loopx/canary/")
WORKSPACE_DISTRIBUTION = "loopx-workspace"
WORKSPACE_MODULES = {
    "loopx/chat.py",
    # Native session adapters use the Workspace store and original conversation.
    "loopx/capabilities/native_chat/session_calendar_tasks.py",
    "loopx/capabilities/native_chat/steward_managed_mode.py",
    "loopx/capabilities/native_chat/steward_monitor.py",
    "loopx/capabilities/native_chat/steward_reports.py",
    "loopx/capabilities/native_chat/steward_turn.py",
    "loopx/dashboard_launcher.py",
    "loopx/cli_commands/support_control_chat.py",
    "loopx/cli_commands/support_control_chat_endpoint.py",
    "loopx/control_plane/collaboration/steward_reports.ts",
    "loopx/extensions/lark/steward_reports.py",
    "loopx/presentation/research_analysis_api.py",
    "loopx/presentation/runtime_workspace.py",
}
WORKSPACE_ASSETS = Path("loopx/web/chat")
SOURCE_SUFFIXES = {".py", ".ts", ".json", ".mjs", ".js", ".md", ".toml"}


def _workspace_source(path: Path) -> bool:
    # Core owns typed action receipts, including operation handoffs read by its Inbox.
    if path.as_posix() == "loopx/chat_action_store.py":
        return False
    return (
        path.as_posix() in WORKSPACE_MODULES
        or path.is_relative_to("loopx/chat")
        or (path.parent == Path("loopx") and path.name.startswith("chat_"))
    )


def prepare_core_source(output: Path, *, repository: Path = REPOSITORY) -> Path:
    repository = repository.resolve()
    sources = [path for path in _tracked_loopx_sources(repository)
               if path.suffix in SOURCE_SUFFIXES
               and path.as_posix() != "loopx/cli.py"
               and not _workspace_source(path)
               and not path.as_posix().startswith(EXCLUDED_TREES)]
    if not sources or not (repository / "loopx/entrypoint.py").is_file():
        raise ValueError("a complete LoopX source checkout is required")
    return _stage_source(output, repository, "loopx-core", sources)


def _stage_source(output: Path, repository: Path, distribution: str, sources: list[Path]) -> Path:
    output = output.resolve()
    if output == repository or output.is_relative_to(repository):
        raise ValueError("prepare the distribution outside the source checkout")
    if output.exists() and any(output.iterdir()):
        raise ValueError("the output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    for relative in sources:
        source = repository / relative
        if source.is_symlink() or not source.resolve().is_relative_to(repository):
            raise ValueError("distribution source must stay inside the checkout")
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    package = repository / "packages" / distribution
    for name in ("pyproject.toml", "README.md"):
        shutil.copyfile(package / name, output / name)
    for name in ("LICENSE", "LICENSE-MIT", "NOTICE"):
        shutil.copyfile(repository / name, output / name)
    manifest = "recursive-include loopx *.py *.ts *.json *.mjs *.js *.md *.toml\n"
    if distribution == WORKSPACE_DISTRIBUTION:
        manifest += "graft loopx/web/chat\n"
    (output / "MANIFEST.in").write_text(manifest, encoding="utf-8")
    return output


def _tracked_loopx_sources(repository: Path) -> list[Path]:
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "-z", "--", "loopx"], cwd=repository,
    ).split(b"\0")
    return [Path(name.decode("utf-8")) for name in names if name]


def prepare_workspace_source(output: Path, *, repository: Path = REPOSITORY) -> Path:
    repository = repository.resolve()
    sources = [path for path in _tracked_loopx_sources(repository)
               if path.suffix in SOURCE_SUFFIXES and _workspace_source(path)]
    if not WORKSPACE_MODULES <= {path.as_posix() for path in sources}:
        raise ValueError("the complete LoopX Workspace source is required")
    bundle = repository / WORKSPACE_ASSETS
    if bundle.is_symlink() or not bundle.resolve().is_relative_to(repository):
        raise ValueError("Workspace assets must stay inside the checkout")
    verify = [sys.executable, str(repository / "scripts/chat_bundle.py"), "verify", "--source"]
    subprocess.run([*verify, "--bundle", str(bundle)], cwd=repository, check=True)
    manifest_path = WORKSPACE_ASSETS / "bundle-manifest.json"
    manifest = json.loads((repository / manifest_path).read_text(encoding="utf-8"))
    assets = [WORKSPACE_ASSETS / name for name in sorted(manifest["files"])]
    output = _stage_source(output, repository, WORKSPACE_DISTRIBUTION, [*sources, manifest_path, *assets])
    subprocess.run([*verify, "--bundle", str(output / WORKSPACE_ASSETS)], cwd=repository, check=True)
    version = run_path(str(repository / "loopx/__init__.py"))["__version__"]
    (output / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (output / "requires-core.txt").write_text(f"loopx-core=={version}\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--distribution", choices=["loopx-core", WORKSPACE_DISTRIBUTION], default="loopx-core")
    args = parser.parse_args()
    prepare = prepare_workspace_source if args.distribution == WORKSPACE_DISTRIBUTION else prepare_core_source
    print(prepare(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
