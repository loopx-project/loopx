"""Prepare standalone Core and its matching built-in capability distributions.

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
EXCLUDED_TREES = ("loopx/chat/", "loopx/web/", "loopx/canary/")
EXCLUDED_MODULES = {"loopx/cli.py", "loopx/chat.py"}
SOURCE_SUFFIXES = {".py", ".ts", ".json", ".mjs", ".js", ".md", ".toml"}
RECALL_DISTRIBUTION = "loopx-capability-agent-turn-recall"
PR_REVIEW_DISTRIBUTION = "loopx-capability-pull-request-review"
ISSUE_FIX_DISTRIBUTION = "loopx-capability-issue-fix"
STANDARD_CAPABILITY_DISTRIBUTIONS = (
    RECALL_DISTRIBUTION,
    PR_REVIEW_DISTRIBUTION,
    ISSUE_FIX_DISTRIBUTION,
)
PR_REVIEW_DIRECTORY = Path("loopx/capabilities/pr_review_queue")
PR_REVIEW_CORE_FILES = {
    "__init__.py", "catalog_entry.py", "goal_configuration.py", "machine_defaults.py",
    "order.py", "scheduling.py", "README.md",
}
ISSUE_FIX_DIRECTORY = Path("loopx/capabilities/issue_fix")
# Generic PR dependencies and existing acknowledgements retain their readback
# and continuation even when the authoring workflow is absent.
ISSUE_FIX_CORE_FILES = {
    "__init__.py", "catalog_entry.py", "command_templates.py", "README.md", "README.zh-CN.md",
    "github_public.py", "metadata_preview.py", "pr_gate_reconcile.py", "pr_lifecycle.py",
    "pr_lifecycle_rollout.py", "pr_review_ack.py", "pr_wait_hook.py", "pr_wait_reconcile.py",
}
CAPABILITY_ENTRY_MODULES = {
    RECALL_DISTRIBUTION: {
        Path("loopx/capabilities/agent_turn_recall/cli.py"),
        Path("loopx/capabilities/agent_turn_recall/core.py"),
    },
    PR_REVIEW_DISTRIBUTION: {
        Path("loopx/cli_commands/pr_review.py"),
        Path("loopx/pr_review.py"),
        Path("loopx/pr_review_merge_readiness.py"),
        PR_REVIEW_DIRECTORY / "core.py",
    },
    ISSUE_FIX_DISTRIBUTION: {
        ISSUE_FIX_DIRECTORY / name for name in ("cli.py", "candidate_preflight.py", "workflow_plan.py", "acceptance_loop.py")
    },
}


def _implementation_paths(sources: list[Path], directory: Path, core_files: set[str]) -> set[Path]:
    return {
        path for path in sources
        if path.is_relative_to(directory)
        and path.relative_to(directory).as_posix() not in core_files
        and path.suffix in SOURCE_SUFFIXES
    }


def _optional_capability_sources(sources: list[Path]) -> dict[str, set[Path]]:
    tracked = set(sources)
    return {
        RECALL_DISTRIBUTION: tracked & CAPABILITY_ENTRY_MODULES[RECALL_DISTRIBUTION],
        PR_REVIEW_DISTRIBUTION: (
            _implementation_paths(sources, PR_REVIEW_DIRECTORY, PR_REVIEW_CORE_FILES)
            | (tracked & CAPABILITY_ENTRY_MODULES[PR_REVIEW_DISTRIBUTION])
        ),
        ISSUE_FIX_DISTRIBUTION: _implementation_paths(sources, ISSUE_FIX_DIRECTORY, ISSUE_FIX_CORE_FILES),
    }


def prepare_core_source(output: Path, *, repository: Path = REPOSITORY) -> Path:
    repository = repository.resolve()
    sources = _tracked_loopx_sources(repository)
    optional = set().union(*_optional_capability_sources(sources).values())
    sources = [path for path in sources if path.suffix in SOURCE_SUFFIXES
               and path not in optional
               and path.as_posix() not in EXCLUDED_MODULES
               and not (path.parent == Path("loopx") and path.name.startswith("chat_"))
               and not path.as_posix().startswith(EXCLUDED_TREES)]
    if not sources or not (repository / "loopx/entrypoint.py").is_file():
        raise ValueError("a complete LoopX source checkout is required")
    output = _stage_source(output, repository, "loopx-core", sources)
    version = run_path(str(repository / "loopx/__init__.py"))["__version__"]
    (output / "requires-standard.txt").write_text(
        "".join(f"{distribution}=={version}\n" for distribution in STANDARD_CAPABILITY_DISTRIBUTIONS),
        encoding="utf-8",
    )
    return output


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


def prepare_capability_source(
    output: Path, distribution: str, *, repository: Path = REPOSITORY,
) -> Path:
    repository = repository.resolve()
    sources = _optional_capability_sources(_tracked_loopx_sources(repository))[distribution]
    if not CAPABILITY_ENTRY_MODULES[distribution].issubset(sources):
        raise ValueError(f"the complete {distribution} source is required")
    version = run_path(str(repository / "loopx/__init__.py"))["__version__"]
    output = _stage_source(output, repository, distribution, sorted(sources))
    (output / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (output / "requires-core.txt").write_text(f"loopx-core=={version}\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--distribution", choices=["loopx-core", *CAPABILITY_ENTRY_MODULES], default="loopx-core")
    args = parser.parse_args()
    output = (
        prepare_core_source(args.output) if args.distribution == "loopx-core"
        else prepare_capability_source(args.output, args.distribution)
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
