"""Compose research scope with ordinary Todo/wait owners, not a second planner."""
from __future__ import annotations


def research_stride_strategy(enabled, strategy):
    """One treatment definition shared by native CLI and programmatic entries."""
    if not enabled:
        return strategy
    if strategy not in (None, "evidence_conditioned_v1"):
        raise ValueError("research_loop requires evidence_conditioned_v1, not a disabled or different review strategy")
    return "evidence_conditioned_v1"


async def prepare_research_continuation(runtime, environment, *, cwd):
    """A candidate stage cannot close a separately retained research outcome.

    The deferred successor contains actual optimization work, not a recurring
    instruction to replan. Its existing resume owner releases it when the
    initial stage is done; budget, Stop and evidence-backed closeout stay native.
    """
    initial = runtime._seeded_todo_id
    if not initial:
        raise ValueError("research continuation requires its initial candidate stage")
    listed = await runtime._loopx(environment, ["todo", "list", "--goal-id", runtime.goal_id,
        "--role", "agent"], cwd=cwd)
    matches = [todo for todo in listed["todos"] if todo.get("target_key") == "research-continuation"]
    if len(matches) > 1:
        raise ValueError("research continuation identity is ambiguous")
    if matches:
        current = matches[0]
        if current.get("claimed_by") != runtime.agent_id:
            raise ValueError("research continuation belongs to another Agent")
        return current  # Never reopen completed work or clear a real wait on preparation.
    research = await runtime._loopx(environment, [
        "todo", "add", "--goal-id", runtime.goal_id, "--role", "agent",
        "--claimed-by", runtime.agent_id, "--task-class", "advancement_task",
        "--action-kind", "benchmark_task", "--target-key", "research-continuation",
        "--status", "deferred", "--resume-when", "todo_done:" + initial,
        "--text", f"[P1] Improve the original research objective in {runtime._task_document} through "
        "complete, evidence-driven experiments. Preserve the best validated candidate. Use released "
        "results and actual resource diagnostics to choose between a local repair and a different "
        "mechanism; retain negative results. Candidate correctness and a successful submission settle "
        "only that candidate stage. Close this research work only with evidence against the original "
        "objective and remaining useful authorized work. Explicit user Stop, the original budget limit "
        "and genuine blockers remain valid stops; do not manufacture work or run merely to fill time.",
        "--execute"], cwd=cwd)
    await runtime._loopx(environment, ["todo", "update", "--goal-id", runtime.goal_id,
        "--agent-id", runtime.agent_id, "--todo-id", initial,
        "--successor-todo-id", research["todo_id"], "--execute"], cwd=cwd)
    await runtime._loopx(environment, ["refresh-state", "--goal-id", runtime.goal_id,
        "--agent-id", runtime.agent_id, "--classification", "research_scope_initialized",
        "--delivery-batch-scale", "single_surface", "--delivery-outcome", "surface_only",
        "--vision-state", "active", "--vision-advancement-policy", "as_needed",
        "--vision-summary", "Build a strong initial candidate, then execute evidence-driven improvements while retaining the best validated artifact.",
        "--vision-acceptance", "Candidate validity settles its stage, not the original research objective. Respect user Stop, original budget and genuine blockers; justify research closeout from remaining useful authorized work.",
        "--no-global-sync", "--suppress-external-sinks"], cwd=cwd)
    return research


def research_execution_guidance() -> str:
    return (
        "\nResearch-loop execution evidence:\n"
        "The initial candidate and ongoing research are separate canonical Todos. Do not close the "
        "research successor merely because the candidate passes tests. Preserve the original objective "
        "and existing Stop/budget/blocked conditions. Before the first native submission, run a relevant "
        "local candidate check and retain actual resource observations, including unknown measurements. "
        "Use `loopx-research-probe --help` for the local probe wrapper: it pins "
        "explicit candidate files, measures runtime and enforces the selected local limit. A solver can "
        "write search_iterations and stop_reason as JSON to LOOPX_RESEARCH_DIAGNOSTICS; absent values "
        "remain unknown, and solver reports are not independently verified. This is not another "
        "evaluation or a requirement to use every available second. Attach the emitted "
        "execution_observation in the normal Explore result writeback. Preserve its input revision "
        "and evidence refs. On the native EdgeBench adapter, probes register automatically and released "
        "results are matched to the actual accepted archive; do not fill result attribution from the "
        "current workspace. An unknown match cannot justify adoption or rollback. "
        "Record changed method ids and the reason when "
        "actual implementation differs from the proposal. Resolve this evidence at the next replan. "
        "If the current Todo requires that external result, use `sforge-submit --wait-todo TODO_ID` "
        "or `loopx-research-feedback wait --todo-id TODO_ID --submission-id SUBMISSION_ID`; "
        "the native accepted receipt creates the specific dependency and the runner observes it. "
        "Independent useful work remains runnable. When all useful work is waiting, return to the "
        "existing scheduler; do not repeatedly call a model to inspect unchanged feedback. "
        "If a submission response is lost, repeat the same submit entrypoint to recover its original "
        "operation and native ID; unresolved acceptance stays unknown without another evaluation.\n"
    )
