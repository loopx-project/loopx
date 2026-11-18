from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any


PRESET_PICKER_SCHEMA_VERSION = "loopx_beginner_preset_picker_v0"


@dataclass(frozen=True)
class BeginnerPreset:
    preset_id: str
    title: str
    tier: str
    maturity: str
    cadence: str
    default_mode: str
    summary: str
    goal_text: str
    capability_path: tuple[str, ...]
    safety_defaults: tuple[str, ...]
    useful_when: tuple[str, ...]


BEGINNER_PRESETS: tuple[BeginnerPreset, ...] = (
    BeginnerPreset(
        preset_id="daily-triage",
        title="Daily Triage L1",
        tier="beginner_default",
        maturity="L1 report-only",
        cadence="daily or weekday morning",
        default_mode="report_only",
        summary=(
            "Inspect LoopX status, active todos, open gates, stale signals, and the "
            "single next action so the owner gets a useful project digest."
        ),
        goal_text=(
            "Run Daily Triage L1 for this repository: inspect LoopX status, active "
            "todos, open gates, stale signals, and next actions; write a compact "
            "report and ask before code edits or external writes."
        ),
        capability_path=(
            "status",
            "todo projection",
            "quota should-run",
            "thin evidence log",
        ),
        safety_defaults=(
            "No code edits by default.",
            "No external writes by default.",
            "Human review before changing project files, publishing, or closing gates.",
        ),
        useful_when=(
            "You want LoopX to keep a long-running repo goal warm.",
            "You need a low-risk first demo that still uses real LoopX state.",
        ),
    ),
    BeginnerPreset(
        preset_id="changelog-draft",
        title="Changelog Draft L1",
        tier="beginner_default",
        maturity="L1 draft-only",
        cadence="per release, or daily during release week",
        default_mode="draft_only",
        summary=(
            "Turn recent merged work and LoopX run history into a release-note draft "
            "that answers the upgrade decision and preserves PR-backed product groups."
        ),
        goal_text=(
            "Run Changelog Draft L1 for this repository: summarize recent merged "
            "work and LoopX run history into a human-reviewed release-note draft "
            "that states who should upgrade, what the release solves, whether it is "
            "breaking, how to verify it, and who contributed; preserve PR-backed "
            "product groups and optional-capability activation/rollback guidance; "
            "do not publish."
        ),
        capability_path=(
            "history",
            "release notes draft",
            "PR link evidence",
            "human review gate",
        ),
        safety_defaults=(
            "Draft only; no release creation or publish action.",
            "Prefer PR links and compact public evidence over raw logs.",
            "Human review before tagging, announcing, or editing canonical docs.",
        ),
        useful_when=(
            "You want visible maintainer value without granting write authority.",
            "You need release copy that is grounded in recent work instead of memory.",
        ),
    ),
    BeginnerPreset(
        preset_id="pr-watch",
        title="PR Watch L1",
        tier="beginner_default",
        maturity="L1 watch-only",
        cadence="every 10-30 minutes while the PR is active",
        default_mode="watch_only",
        summary=(
            "Watch review, CI, and merge blockers for a PR, then report what changed "
            "and what needs a human decision."
        ),
        goal_text=(
            "Run PR Watch L1 for this repository: watch review, CI, and merge "
            "blockers for the target PR; report material changes, blockers, and "
            "next human decisions; do not auto-merge."
        ),
        capability_path=(
            "continuous monitor todo",
            "quota monitor-poll",
            "CI/review blocker summary",
            "human merge gate",
        ),
        safety_defaults=(
            "No auto-merge.",
            "No code edits unless the owner upgrades the preset to an explicit L2 fix lane.",
            "Quiet unchanged polls should not spend quota.",
        ),
        useful_when=(
            "You want fewer manual PR refreshes while keeping merge authority human.",
            "You need an automation-friendly monitor that still produces concrete blockers.",
        ),
    ),
    BeginnerPreset(
        preset_id="ci-sweeper",
        title="CI Sweeper L2",
        tier="advanced_opt_in",
        maturity="L2 opt-in patch lane",
        cadence="on failing checks, or every 30-60 minutes while a PR is active",
        default_mode="dry_run_report_first",
        summary=(
            "Classify failing checks, propose the smallest bounded fix, and only draft "
            "a worktree patch after explicit owner opt-in."
        ),
        goal_text=(
            "Run CI Sweeper L2 for this repository: inspect failing checks and recent "
            "CI context, classify the likely fix, then produce a dry-run report first. "
            "Only after explicit owner opt-in, draft a bounded patch in an isolated "
            "codex/ worktree, run focused verification, and stop for human review before "
            "push, merge, rerun-cost expansion, or external writes."
        ),
        capability_path=(
            "failing-check intake",
            "bounded codex/ worktree patch",
            "focused verifier or smoke",
            "human review gate",
        ),
        safety_defaults=(
            "Dry-run report first; no patch until owner opt-in.",
            "Patch only in an isolated codex/ worktree with a narrow allowed scope.",
            "Verifier or focused smoke must pass before marking a patch ready.",
            "Human review before push, merge, rerun-cost expansion, or external writes.",
            "Escalate after repeated failures or unchanged error signatures.",
        ),
        useful_when=(
            "A PR or main branch has boring, well-scoped check failures.",
            "You want LoopX to reduce maintainer toil without granting merge authority.",
        ),
    ),
    BeginnerPreset(
        preset_id="dependency-sweeper",
        title="Dependency Sweeper L2",
        tier="advanced_opt_in",
        maturity="L2 opt-in dependency lane",
        cadence="weekly, per security notice, or during release hardening",
        default_mode="policy_report_first",
        summary=(
            "Review dependency update candidates against a patch/minor policy, denylist, "
            "and verifier plan before drafting any update patch."
        ),
        goal_text=(
            "Run Dependency Sweeper L2 for this repository: inspect dependency update "
            "candidates, apply a patch/minor policy and denylist, then produce a policy "
            "report first. Only after explicit owner opt-in, draft a bounded dependency "
            "patch in an isolated codex/ worktree, run focused verification, and stop "
            "for human review before push, merge, publish, or rollout."
        ),
        capability_path=(
            "dependency candidate intake",
            "patch/minor policy and denylist",
            "bounded codex/ worktree patch",
            "focused verifier or smoke",
            "human review gate",
        ),
        safety_defaults=(
            "Policy report first; no dependency edit until owner opt-in.",
            "Default to patch and minor updates; deny major, runtime, lockfile-wide, "
            "and toolchain jumps unless explicitly allowed.",
            "Verifier or focused smoke must pass before marking an update ready.",
            "Human review before push, merge, publish, rollout, or dependency source changes.",
            "Escalate when updates fail twice, widen scope, or hit a denied package.",
        ),
        useful_when=(
            "A repo has recurring safe dependency bumps or security patch noise.",
            "You want update toil reduced while keeping rollout and publish authority human.",
        ),
    ),
)

PRESET_IDS = tuple(preset.preset_id for preset in BEGINNER_PRESETS)
DEFAULT_PRESET_IDS = tuple(
    preset.preset_id for preset in BEGINNER_PRESETS if preset.tier == "beginner_default"
)
ADVANCED_PRESET_IDS = tuple(
    preset.preset_id for preset in BEGINNER_PRESETS if preset.tier == "advanced_opt_in"
)


def _shell(value: str) -> str:
    return shlex.quote(value)


def _preset_commands(
    preset: BeginnerPreset,
    *,
    cli_bin: str,
    project: str,
    goal_id: str,
    agent_id: str,
    agent_scope: str,
) -> dict[str, str]:
    quoted_cli = _shell(cli_bin)
    return {
        "slash_start": f"/loopx {preset.goal_text}",
        "cli_start": (
            f"{quoted_cli} start-goal --guided --project {_shell(project)} "
            f"--goal-text {_shell(preset.goal_text)}"
        ),
        "quota_guard": (
            f"{quoted_cli} --format json quota should-run "
            f"--goal-id {_shell(goal_id)} --agent-id {_shell(agent_id)} "
            "--runtime-profile codex_app_heartbeat"
        ),
        "heartbeat_prompt": (
            f"{quoted_cli} heartbeat-prompt --thin --goal-id {_shell(goal_id)} "
            f"--agent-id {_shell(agent_id)} --agent-scope {_shell(agent_scope)} "
            "--runtime-profile codex_app_heartbeat"
        ),
    }


def _preset_to_dict(
    preset: BeginnerPreset,
    *,
    cli_bin: str,
    project: str,
    goal_id: str,
    agent_id: str,
    agent_scope: str,
) -> dict[str, Any]:
    return {
        "id": preset.preset_id,
        "title": preset.title,
        "tier": preset.tier,
        "maturity": preset.maturity,
        "cadence": preset.cadence,
        "default_mode": preset.default_mode,
        "summary": preset.summary,
        "goal_text": preset.goal_text,
        "capability_path": list(preset.capability_path),
        "safety_defaults": list(preset.safety_defaults),
        "useful_when": list(preset.useful_when),
        "commands": _preset_commands(
            preset,
            cli_bin=cli_bin,
            project=project,
            goal_id=goal_id,
            agent_id=agent_id,
            agent_scope=agent_scope,
        ),
    }


def build_beginner_preset_packet(
    *,
    preset_id: str | None = None,
    cli_bin: str = "loopx",
    project: str = ".",
    goal_id: str = "<goal-id>",
    agent_id: str = "<agent-id>",
    agent_scope: str = "<agent-scope>",
) -> dict[str, Any]:
    presets = BEGINNER_PRESETS
    if preset_id:
        presets = tuple(preset for preset in BEGINNER_PRESETS if preset.preset_id == preset_id)
        if not presets:
            raise ValueError(f"unknown beginner preset: {preset_id}")
    return {
        "ok": True,
        "schema_version": PRESET_PICKER_SCHEMA_VERSION,
        "summary": (
            "Beginner LoopX presets compile common loop-engineering use cases into "
            "existing LoopX start, quota, heartbeat, and evidence flows."
        ),
        "mutation_policy": "read_only; renders commands only; does not write registry, state, automation, PRs, or docs",
        "recommended_order": list(PRESET_IDS),
        "default_preset_ids": list(DEFAULT_PRESET_IDS),
        "advanced_preset_ids": list(ADVANCED_PRESET_IDS),
        "placeholders": {
            "goal_id": goal_id,
            "agent_id": agent_id,
            "agent_scope": agent_scope,
            "project": project,
        },
        "common_setup": [
            f"{_shell(cli_bin)} doctor",
            f"{_shell(cli_bin)} slash-commands --install",
            f"{_shell(cli_bin)} preset list",
        ],
        "presets": [
            _preset_to_dict(
                preset,
                cli_bin=cli_bin,
                project=project,
                goal_id=goal_id,
                agent_id=agent_id,
                agent_scope=agent_scope,
            )
            for preset in presets
        ],
    }


def render_beginner_preset_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX beginner preset picker",
        "",
        str(payload.get("summary") or ""),
        "",
        f"Mutation policy: {payload.get('mutation_policy')}",
        "",
        "Common setup:",
    ]
    raw_setup = payload.get("common_setup")
    common_setup = raw_setup if isinstance(raw_setup, list) else []
    for command in common_setup:
        lines.append(f"- `{command}`")
    lines.append("")
    cli_prefix = (
        str(common_setup[0]).rsplit(" ", 1)[0]
        if common_setup and isinstance(common_setup[0], str)
        else "loopx"
    )
    raw_presets = payload.get("presets")
    presets = raw_presets if isinstance(raw_presets, list) else []
    for preset in presets:
        if not isinstance(preset, dict):
            continue
        lines.extend(
            [
                f"## {preset.get('title')}",
                "",
                f"- id: `{preset.get('id')}`",
                f"- tier: `{preset.get('tier')}`",
                f"- maturity: {preset.get('maturity')}",
                f"- cadence: {preset.get('cadence')}",
                f"- default mode: `{preset.get('default_mode')}`",
                f"- summary: {preset.get('summary')}",
                "",
                "Useful when:",
            ]
        )
        raw_useful = preset.get("useful_when")
        for item in raw_useful if isinstance(raw_useful, list) else []:
            lines.append(f"- {item}")
        lines.extend(["", "Safety defaults:"])
        raw_safety = preset.get("safety_defaults")
        for item in raw_safety if isinstance(raw_safety, list) else []:
            lines.append(f"- {item}")
        lines.extend(["", "Commands:", ""])
        raw_commands = preset.get("commands")
        commands = raw_commands if isinstance(raw_commands, dict) else {}
        for label in ("slash_start", "cli_start", "quota_guard", "heartbeat_prompt"):
            command = commands.get(label)
            if command:
                fence = "text" if label == "slash_start" else "bash"
                lines.extend([f"{label}:", f"```{fence}", str(command), "```", ""])
        lines.append(f"Show only this preset: `{cli_prefix} preset show {preset.get('id')}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
