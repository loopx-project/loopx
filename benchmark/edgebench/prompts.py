"""EdgeBench's no-judge-feedback variant of its iterative task wrapper.

This is provider prompt policy, not a second LoopX goal or continuation owner.
The native wrapper remains owned by SForge; only blind trials use this variant.
"""

from __future__ import annotations


def blind_task_prompt(original_query: str, submit_paths: list[str]) -> str:
    """Retain the native optimization contract without exposing host evaluation.

    Best-score selection is task policy, not access to the scorer. The agent
    chooses its current best version from local evidence; it cannot discover
    which version the hidden evaluator ranks highest.
    """
    paths = ", ".join(f"`{path}`" for path in submit_paths)
    return (
        "## Iterative Evaluation Mode\n\n"
        "External evaluation and evaluation feedback are unavailable. "
        "After implementing code, use local tests and validation to identify "
        "issues, then iterate based on the results.\n\n"
        "### How to Test Your Code\n\n"
        "Use local checks regularly to check your progress and identify issues. "
        "Official scores and per-test diagnostics are unavailable.\n"
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
        "- Your **best score** across solution versions is your final score\n"
        "- You don't lose points for failed attempts — experimentation is encouraged\n\n"
        "---\n\n"
        f"{original_query}\n"
    )
