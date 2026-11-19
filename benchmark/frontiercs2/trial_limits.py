"""FrontierCS 2 task limits shared by the solver inputs and local controller."""

from dataclasses import dataclass
import math
from pathlib import Path
import re
import tomllib


def _integer(name: str, value: object, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _merged_table(defaults: dict, task: dict, name: str) -> dict:
    inherited, overrides = defaults.get(name, {}), task.get(name, {})
    if not isinstance(inherited, dict) or not isinstance(overrides, dict):
        raise ValueError(f"{name} must be a table")
    return inherited | overrides


@dataclass(frozen=True)
class TrialLimits:
    answering_seconds: int | None
    max_train_calls: int
    max_train_wall_seconds: int
    eval_timeout_seconds: int
    max_payload_bytes: int

    def __post_init__(self) -> None:
        if self.answering_seconds is not None:
            _integer("answering_seconds", self.answering_seconds, 1)
        _integer("max_train_calls", self.max_train_calls, 0)
        _integer("max_train_wall_seconds", self.max_train_wall_seconds, 0)
        _integer("eval_timeout_seconds", self.eval_timeout_seconds, 1)
        _integer("max_payload_bytes", self.max_payload_bytes, 1)

    @classmethod
    def from_task(
        cls,
        task_path: Path,
        defaults_path: Path,
        *,
        natural: bool = False,
        answering_seconds: int | None = None,
    ) -> "TrialLimits":
        with task_path.open("rb") as stream:
            task = tomllib.load(stream)
        with defaults_path.open("rb") as stream:
            defaults = tomllib.load(stream)
        if task.get("schema") != "frontier.task/v1":
            raise ValueError("Expected a frontier.task/v1 definition")
        quota = _merged_table(defaults, task, "quota")
        submission = _merged_table(defaults, task, "submission")
        native_answering = _integer("wall_time_seconds", quota.get("wall_time_seconds"), 1)
        if natural and answering_seconds is not None:
            raise ValueError("A natural trial cannot declare an answering limit")
        effective_answering = answering_seconds if answering_seconds is not None else native_answering
        return cls(
            None if natural else effective_answering,
            _integer("max_train_calls", quota.get("max_train_calls"), 0),
            _integer("max_train_wall_seconds", quota.get("max_train_wall_seconds"), 0),
            _integer("eval_timeout_seconds", quota.get("eval_timeout_seconds"), 1),
            _integer("max_bytes", submission.get("max_bytes"), 1),
        )

    def answering_deadline(self, started_at: float) -> float | None:
        if not math.isfinite(started_at):
            raise ValueError("Answering time requires a finite start timestamp")
        return None if self.answering_seconds is None else started_at + self.answering_seconds

    def answering_remaining(self, deadline: float | None, now: float) -> float | None:
        if (deadline is None) != (self.answering_seconds is None):
            raise ValueError("Answering deadline disagrees with the trial policy")
        if not math.isfinite(now) or (deadline is not None and not math.isfinite(deadline)):
            raise ValueError("Trial timestamps must be finite")
        return None if deadline is None else max(0.0, deadline - now)

    def train_timeout(self, *, spent: float, deadline: float | None, now: float) -> int:
        if not math.isfinite(spent) or spent < 0:
            raise ValueError("Spent Train time must be finite and nonnegative")
        remaining = self.max_train_wall_seconds - spent
        answering = self.answering_remaining(deadline, now)
        if answering is not None:
            remaining = min(remaining, answering)
        return max(0, int(remaining))

    def final_payload(self, *, explicit: str | None, saved: str | None, cancelled: bool) -> str | None:
        if cancelled:
            return None
        return explicit or (saved if self.answering_seconds is not None else None)

    def render_limits(self) -> str:
        if self.answering_seconds is None:
            answering = "- This trial has no overall answering deadline."
        else:
            answering = (
                f"- The answering phase allows **{self.answering_seconds} seconds**,"
                " including planning, solver work, and any overlapping Train."
            )
        return "\n".join((
            answering,
            f"- At most **{self.max_train_calls} Train calls** and "
            f"**{self.max_train_wall_seconds} seconds** of cumulative Train verifier time."
            " These are ceilings. Each accepted request consumes one call even if verification fails."
            " Only one Train may be queued or running. A concurrent request is rejected without a charge."
            " A Train uses the remaining verifier allowance, bounded by any answering deadline.",
            f"- Final evaluation has a separate **{self.eval_timeout_seconds}-second** allowance."
            " It runs after answering closes and does not consume Train time or calls."
            " The final submission command returns acceptance rather than the evaluation score.",
            f"- Each submitted archive may contain up to **{self.max_payload_bytes} bytes**.",
        ))

    def render_submission_policy(self) -> str:
        if self.answering_seconds is None:
            return (
                "Saving keeps a candidate snapshot and returns `null` for `remaining_runtime_s`."
                " Finish by submitting an explicit final and exiting."
                " An interrupted attempt without an explicit final retains its evidence for analysis."
            )
        return (
            "Saving keeps a candidate snapshot and returns the remaining answering seconds."
            " An accepted explicit final takes precedence."
            " On normal exit, a deadline, or a crash, the controller may use the last accepted save."
            " Operator cancellation stops automatic final evaluation."
        )


LIMITS_SLOT = "<!-- frontiercs2:trial-limits -->"
SUBMISSION_POLICY_SLOT = "<!-- frontiercs2:submission-policy -->"
_LIMITS_SECTION = re.compile(r"^## Limits[ \t]*\n(?P<body>.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
_STOCK_QUOTA_LABELS = (
    re.compile(r"- \*\*\d+ train calls\*\*;"),
    re.compile(r"- Cumulative train wall time:"),
    re.compile(r"- Only one train runs at a time:"),
    re.compile(r"- These limits are ceilings,"),
    re.compile(r"- The overall answering window is "),
    re.compile(r"- Final evaluation has a separate "),
)
_STOCK_STORAGE_RUNTIME = re.compile(
    r"^The JSON returns `remaining_runtime_s` \(Agent time remaining\) and\n"
    r"`remaining_checkpoint_submissions`.*?Agent-time limits still apply\.$",
    re.MULTILINE | re.DOTALL,
)
_STOCK_FINAL_POLICY = re.compile(
    r"^If no explicit final submission was accepted .*?(?=\n\nIterate only while useful\.)",
    re.MULTILINE | re.DOTALL,
)


def render_task_instructions(source: str, limits: TrialLimits) -> str:
    """Render a task template or the generated Limits block in existing task images."""
    if LIMITS_SLOT not in source:
        sections = list(_LIMITS_SECTION.finditer(source))
        if len(sections) != 1:
            raise ValueError("Expected one native Limits section or an explicit trial-limits slot")
        section = sections[0]
        body = section["body"]
        bullets = list(re.finditer(r"^- .+", body, re.MULTILINE))
        matches = [
            idx for idx in range(len(bullets) - len(_STOCK_QUOTA_LABELS) + 1)
            if all(label.match(bullets[idx + offset].group())
                   for offset, label in enumerate(_STOCK_QUOTA_LABELS))
        ]
        if len(matches) != 1:
            raise ValueError("Unexpected native quota block; provide an explicit trial-limits slot")
        first = matches[0]
        last = first + len(_STOCK_QUOTA_LABELS) - 1
        start = section.start("body") + bullets[first].start()
        # The generated quota bullets are contiguous with indented wrapped lines.
        end = section.start("body") + bullets[last].end()
        continuation = re.match(r"(?:\n[ \t]+[^ \t\n][^\n]*)*", source[end:])
        end += continuation.end()
        between = source[start:end]
        if any(line and not line.startswith(("- ", " ", "\t")) for line in between.splitlines()):
            raise ValueError("Native quota block contains additional text; provide an explicit trial-limits slot")
        source = source[:start] + LIMITS_SLOT + source[end:]
    if source.count(LIMITS_SLOT) != 1:
        raise ValueError("Expected exactly one Frontier trial-limits slot")
    return source.replace(LIMITS_SLOT, limits.render_limits())


def render_submission_skill(source: str, limits: TrialLimits) -> str:
    """Apply the same policy to a template or the standard native submission skill."""
    if SUBMISSION_POLICY_SLOT not in source:
        source, policy_blocks = _STOCK_FINAL_POLICY.subn(SUBMISSION_POLICY_SLOT, source)
        source, storage_blocks = _STOCK_STORAGE_RUNTIME.subn(
            "The JSON reports `remaining_checkpoint_submissions` for the remaining save allowance."
            " A null value means there is no separate save-count quota.",
            source,
        )
        if policy_blocks != 1 or storage_blocks != 1:
            raise ValueError("Unexpected native skill; provide an explicit submission-policy slot")
        source = source.replace("save a fallback final answer", "save a candidate snapshot")
    if source.count(SUBMISSION_POLICY_SLOT) != 1:
        raise ValueError("Expected exactly one Frontier submission-policy slot")
    return source.replace(SUBMISSION_POLICY_SLOT, limits.render_submission_policy())
