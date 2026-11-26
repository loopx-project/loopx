"""Prepare split LoopX Core, Workspace, Research, and built-in capability distributions.

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
BENCHMARK_DISTRIBUTION = "loopx-capability-benchmark-toolkit"
BENCHMARK_EXECUTION_PREFIXES = (
    "loopx/capabilities/benchmark_toolkit/",
    "loopx/cli_commands/benchmark_",
)
# Keep the catalog in a namespace package so discovery skips the execution facade.
BENCHMARK_CATALOG_FILES = {
    "loopx/capabilities/benchmark_toolkit/README.md",
    "loopx/capabilities/benchmark_toolkit/catalog_entry.py",
    "loopx/capabilities/benchmark_toolkit/four_arm_contract.py",
}
SOURCE_SUFFIXES = {".py", ".ts", ".json", ".mjs", ".js", ".md", ".toml"}
RECALL_DISTRIBUTION = "loopx-capability-agent-turn-recall"
PR_REVIEW_DISTRIBUTION = "loopx-capability-pull-request-review"
ISSUE_FIX_DISTRIBUTION = "loopx-capability-issue-fix"
RESEARCH_DISTRIBUTION = "loopx-capability-research"
STANDARD_CAPABILITY_DISTRIBUTIONS = (
    RECALL_DISTRIBUTION,
    PR_REVIEW_DISTRIBUTION,
    ISSUE_FIX_DISTRIBUTION,
)
PR_REVIEW_DIRECTORY = Path("loopx/capabilities/pr_review_queue")
PR_REVIEW_CORE_FILES = {
    "__init__.py", "catalog_entry.py", "goal_configuration.py", "machine_defaults.py",
    "order.py", "review_body.py", "scheduling.py", "README.md",
}
ISSUE_FIX_DIRECTORY = Path("loopx/capabilities/issue_fix")
# Generic PR dependencies and existing acknowledgements retain their readback
# and continuation even when the authoring workflow is absent.
ISSUE_FIX_CORE_FILES = {
    "__init__.py", "catalog_entry.py", "command_templates.py", "correction_settlement.py",
    "correction_settlement_gates.py", "correction_settlement_input.py",
    "README.md", "README.zh-CN.md", "github_public.py", "metadata_preview.py",
    "pr_gate_reconcile.py", "pr_lifecycle.py", "pr_lifecycle_rollout.py",
    "pr_review_ack.py", "pr_wait_hook.py", "pr_wait_reconcile.py",
}
EXPLORE_DIRECTORY = Path("loopx/capabilities/explore")
# Core retains Explore's result event schema, lightweight projection, machine/goal
# configuration adapters, and Goal permission/settlement interfaces.
EXPLORE_CORE_FILES = {
    "__init__.py", "activation.py", "candidate_content.py", "catalog_entry.py",
    "composition_frontier.py", "execution_observation.py", "harness_gate.py",
    "machine_defaults.py", "measurements.py", "research_evidence.py",
    "research_frontier.py", "result_log.py", "result_writeback.py",
    "turn_context.py", "README.md", "README.zh-CN.md",
}
RESEARCH_FILES = {
    Path("loopx/cli_commands/explore.py"),
    Path("loopx/cli_commands/explore_planning_commands.py"),
    Path("loopx/cli_commands/explore_feishu_commands.py"),
    Path("loopx/capabilities/deep_research/runtime.py"),
    Path("loopx/capabilities/external_research/cli.py"),
    Path("loopx/capabilities/external_research/projection.py"),
    Path("loopx/capabilities/external_research/provider_probe.py"),
    Path("loopx/cli_commands/deepresearch.py"),
    Path("loopx/control_plane/capabilities/external_evidence.ts"),
    Path("loopx/extensions/public_github_research.py"),
    EXPLORE_DIRECTORY / "harness_runtime.py",
    EXPLORE_DIRECTORY / "harness_frontier.py",
    EXPLORE_DIRECTORY / "replay_runtime.py",
    EXPLORE_DIRECTORY / "research_bridge.py",
    EXPLORE_DIRECTORY / "todo_branch_plan.py",
    EXPLORE_DIRECTORY / "worker_branch_plan.py",
}
CAPABILITY_ENTRY_MODULES = {
    BENCHMARK_DISTRIBUTION: {
        Path("loopx/cli_commands/benchmark_dispatch.py"),
        Path("loopx/capabilities/benchmark_toolkit/trajectory_assessment_store.py"),
    },
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
    RESEARCH_DISTRIBUTION: RESEARCH_FILES,
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
        BENCHMARK_DISTRIBUTION: {
            path for path in sources
            if path.suffix in SOURCE_SUFFIXES and _benchmark_execution_source(path)
        },
        RECALL_DISTRIBUTION: tracked & CAPABILITY_ENTRY_MODULES[RECALL_DISTRIBUTION],
        PR_REVIEW_DISTRIBUTION: (
            _implementation_paths(sources, PR_REVIEW_DIRECTORY, PR_REVIEW_CORE_FILES)
            | (tracked & CAPABILITY_ENTRY_MODULES[PR_REVIEW_DISTRIBUTION])
        ),
        ISSUE_FIX_DISTRIBUTION: _implementation_paths(sources, ISSUE_FIX_DIRECTORY, ISSUE_FIX_CORE_FILES),
        RESEARCH_DISTRIBUTION: (
            _implementation_paths(sources, EXPLORE_DIRECTORY, EXPLORE_CORE_FILES)
            | (tracked & RESEARCH_FILES)
        ),
    }


def _workspace_source(path: Path) -> bool:
    # Core owns typed action receipts, including operation handoffs read by its Inbox.
    if path.as_posix() == "loopx/chat_action_store.py":
        return False
    return (
        path.as_posix() in WORKSPACE_MODULES
        or path.is_relative_to("loopx/chat")
        or (path.parent == Path("loopx") and path.name.startswith("chat_"))
    )


def _benchmark_execution_source(path: Path) -> bool:
    name = path.as_posix()
    return name.startswith(BENCHMARK_EXECUTION_PREFIXES) and name not in BENCHMARK_CATALOG_FILES


def prepare_core_source(output: Path, *, repository: Path = REPOSITORY) -> Path:
    repository = repository.resolve()
    sources = _tracked_loopx_sources(repository)
    optional = set().union(*_optional_capability_sources(sources).values())
    sources = [path for path in sources if path.suffix in SOURCE_SUFFIXES
               and path not in optional
               and path.as_posix() != "loopx/cli.py"
               and not _workspace_source(path)
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


def prepare_research_source(output: Path, *, repository: Path = REPOSITORY) -> Path:
    return prepare_capability_source(output, RESEARCH_DISTRIBUTION, repository=repository)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--distribution", choices=["loopx-core", WORKSPACE_DISTRIBUTION, *CAPABILITY_ENTRY_MODULES], default="loopx-core")
    args = parser.parse_args()
    if args.distribution == "loopx-core":
        output = prepare_core_source(args.output)
    elif args.distribution == WORKSPACE_DISTRIBUTION:
        output = prepare_workspace_source(args.output)
    else:
        output = prepare_capability_source(args.output, args.distribution)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
