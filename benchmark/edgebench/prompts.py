"""EdgeBench's restricted-feedback variants of its iterative task wrapper.

This is provider prompt policy, not a second LoopX goal or continuation owner.
The native wrapper remains owned by SForge; blind and best-only trials use these variants.
"""

from __future__ import annotations


def blind_task_prompt(original_query: str, submit_paths: list[str]) -> str:
    """Retain the native optimization contract without exposing host evaluation.

    Best-score selection is task policy, not access to the scorer. The agent
    chooses its current best version from local evidence; it cannot discover
    which version the hidden evaluator ranks highest.
    """
    return _local_task_prompt(original_query, submit_paths)


def _local_task_prompt(original_query, submit_paths, *, intro=(
        "External evaluation and evaluation feedback are unavailable."), checks=(
        "Official scores and per-test diagnostics are unavailable.")):
    paths = ", ".join(f"`{path}`" for path in submit_paths)
    return (
        "## Iterative Evaluation Mode\n\n"
        f"{intro} "
        "After implementing code, use local tests and validation to identify "
        "issues, then iterate based on the results.\n\n"
        "### How to Test Your Code\n\n"
        "Use local checks regularly to check your progress and identify issues. "
        f"{checks}\n"
        "\n### Submitted Files\n\n"
        f"Only the following paths are submitted for evaluation: {paths}\n\n"
        "**Keep these files in a compilable/runnable state at all times.** "
        "Write changes to disk promptly and ensure the submitted files always "
        "represent your current best solution, judged from available local evidence.\n"
        "\n### Network Environment\n\n"
        "**This environment has NO internet access.** "
        "Only the AI API is reachable. "
        "Do not attempt to download packages, fetch remote resources, "
        "or access external URLs — all dependencies are pre-installed "
        "in the workspace.\n\n"
        "### Strategy\n\n"
        "- **Implement incrementally**: Complete one module/project at a time\n"
        "- **Read local test feedback carefully**: Failed local checks help identify issues\n"
        "- **Iterate**: Fix failures based on local validation, then test again\n\n"
        "### Scoring\n\n"
        "- The task's grading and final-selection rules determine your result\n"
        "- You don't lose points for failed attempts — experimentation is encouraged\n\n"
        "---\n\n"
        f"{original_query}\n"
    )


def best_only_task_prompt(original_query: str, submit_paths: list[str]) -> str:
    """Use the blind local-work contract with one explicit positive feedback channel."""
    # Build the wrapper without rewriting any task-owned query or instructions.
    local = _local_task_prompt("", submit_paths,
        intro="External evaluation is automatic; only new-best notifications are available.",
        checks="Exact scores and per-test diagnostics are unavailable.",
    ).removesuffix("---\n\n\n")
    return local + (
        "### New-best Feedback\n\n"
        "The harness automatically adds new-best notifications to your context after tool calls "
        "or when a session/turn starts. You do not need to poll a file. "
        "Do not wait idle for feedback; continue useful local work. "
        "The host samples submitted files on a fixed schedule; you cannot trigger evaluations. "
        "A notification means that the named snapshot strictly improved the task's native ranking "
        "among valid scored snapshots (using its selection policy and score direction). "
        "The first valid result only establishes "
        "a baseline, without a notification. Ties, regressions and unsuccessful evaluations produce "
        "no notifications. The file retains only the latest improvement.\n\n"
        "Evaluation is asynchronous: the notification refers to the named snapshot, **not necessarily "
        "your current files**. Its `source_archive` contains your exact submitted source and "
        "`source_sha256` identifies that archive. Preserve or compare that checkpoint before changing "
        "direction; do not blindly overwrite newer work. No notification does not mean failure or "
        "regression: evaluation may still be pending or unavailable. These signals are not task "
        "completion evidence, and do not replace local tests or the task's acceptance criteria.\n\n"
        "---\n\n" + original_query + "\n"
    )
