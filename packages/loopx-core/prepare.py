"""Prepare standalone Core and its matching benchmark wheel from one source tree.

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
# These are distribution exclusions, not an authority or activation policy.
EXCLUDED_PREFIXES = (
    "loopx/chat/",
    "loopx/web/",
    "loopx/canary/",
)
BENCHMARK_DISTRIBUTION = "loopx-capability-benchmark-toolkit"
BENCHMARK_EXECUTION_PREFIXES = (
    "loopx/capabilities/benchmark_toolkit/",
    "loopx/cli_commands/benchmark_",
)
EXCLUDED_MODULES = {
    "loopx/cli.py",
    "loopx/chat.py",
    "loopx/presentation/research_analysis_api.py",
    "loopx/presentation/runtime_workspace.py",
}
# Keep the catalog in a namespace package so discovery skips the execution facade.
BENCHMARK_CATALOG_FILES = {
    "loopx/capabilities/benchmark_toolkit/README.md",
    "loopx/capabilities/benchmark_toolkit/catalog_entry.py",
    "loopx/capabilities/benchmark_toolkit/four_arm_contract.py",
}
SOURCE_SUFFIXES = {".py", ".ts", ".json", ".mjs", ".js", ".md", ".toml"}


def _benchmark_execution_source(path: Path) -> bool:
    name = path.as_posix()
    return name.startswith(BENCHMARK_EXECUTION_PREFIXES) and name not in BENCHMARK_CATALOG_FILES


def prepare_core_source(output: Path, *, repository: Path = REPOSITORY) -> Path:
    repository = repository.resolve()
    sources = [path for path in _tracked_loopx_sources(repository)
               if path.suffix in SOURCE_SUFFIXES
               and path.as_posix() not in EXCLUDED_MODULES
               and not (path.parent == Path("loopx") and path.name.startswith("chat_"))
               and not path.as_posix().startswith(EXCLUDED_PREFIXES)
               and not _benchmark_execution_source(path)]
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


def prepare_benchmark_source(output: Path, *, repository: Path = REPOSITORY) -> Path:
    repository = repository.resolve()
    sources = [path for path in _tracked_loopx_sources(repository)
               if path.suffix in SOURCE_SUFFIXES and _benchmark_execution_source(path)]
    if Path("loopx/cli_commands/benchmark_dispatch.py") not in sources:
        raise ValueError("the complete benchmark toolkit source is required")
    output = _stage_source(output, repository, BENCHMARK_DISTRIBUTION, sources)
    version = run_path(str(repository / "loopx/__init__.py"))["__version__"]
    (output / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (output / "requires-core.txt").write_text(f"loopx-core=={version}\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--distribution", choices=["loopx-core", BENCHMARK_DISTRIBUTION], default="loopx-core")
    args = parser.parse_args()
    prepare = prepare_benchmark_source if args.distribution == BENCHMARK_DISTRIBUTION else prepare_core_source
    print(prepare(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
