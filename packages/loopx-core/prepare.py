"""Prepare the standalone Core or its matching built-in recall distribution.

Build the returned directory with a PEP 517 frontend. No runtime code is copied
back into the repository and the ordinary distribution remains unchanged.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from runpy import run_path

REPOSITORY = Path(__file__).resolve().parents[2]
# These are distribution exclusions.
EXCLUDED_TREES = ("loopx/chat/", "loopx/web/", "loopx/canary/")
EXCLUDED_MODULES = {"loopx/cli.py", "loopx/chat.py"}
SOURCE_SUFFIXES = {".py", ".ts", ".json", ".mjs", ".js", ".md", ".toml"}
# Keep recall's declaration and admission readback in Core.
RECALL_IMPLEMENTATION_MODULES = {
    "loopx/capabilities/agent_turn_recall/cli.py",
    "loopx/capabilities/agent_turn_recall/core.py",
}
RECALL_DISTRIBUTION = "loopx-capability-agent-turn-recall"


def prepare_core_source(output: Path, *, repository: Path = REPOSITORY) -> Path:
    repository = repository.resolve()
    sources = _tracked_loopx_sources(repository)
    sources = [path for path in sources if path.suffix in SOURCE_SUFFIXES
               and path.as_posix() not in RECALL_IMPLEMENTATION_MODULES
               and path.as_posix() not in EXCLUDED_MODULES
               and not (path.parent == Path("loopx") and path.name.startswith("chat_"))
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
    (output / "MANIFEST.in").write_text(
        "recursive-include loopx *.py *.ts *.json *.mjs *.js *.md *.toml\n", encoding="utf-8",
    )
    return output


def _tracked_loopx_sources(repository: Path) -> list[Path]:
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "-z", "--", "loopx"], cwd=repository,
    ).split(b"\0")
    return [Path(name.decode("utf-8")) for name in names if name]


def prepare_turn_recall_source(output: Path, *, repository: Path = REPOSITORY) -> Path:
    repository = repository.resolve()
    sources = [path for path in _tracked_loopx_sources(repository)
               if path.as_posix() in RECALL_IMPLEMENTATION_MODULES]
    if {path.as_posix() for path in sources} != RECALL_IMPLEMENTATION_MODULES:
        raise ValueError("the complete built-in Turn recall source is required")
    version = run_path(str(repository / "loopx/__init__.py"))["__version__"]
    output = _stage_source(output, repository, RECALL_DISTRIBUTION, sources)
    (output / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (output / "requires-core.txt").write_text(f"loopx-core=={version}\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--distribution", choices=["loopx-core", RECALL_DISTRIBUTION], default="loopx-core")
    args = parser.parse_args()
    prepare = prepare_turn_recall_source if args.distribution == RECALL_DISTRIBUTION else prepare_core_source
    print(prepare(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
