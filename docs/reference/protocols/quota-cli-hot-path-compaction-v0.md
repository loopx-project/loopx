# Quota CLI Hot-Path Compaction v0

`quota_cli_hot_path_compaction_v0` bounds the default agent-facing
`quota should-run` projection without changing the decision computed by the
quota control plane. The full decision is built first. CLI-only projection then
retains action authority on the hot path and moves repeated diagnostic detail
behind explicit `--include-detail` selectors.

## Ownership Boundary

The quota control plane owns decision, precedence, scheduler, interaction,
selected-todo, and user-action semantics. `cli_projection.py` owns only the
serialized view consumed by agents. A compactor must not become a second
decision owner or recompute any route.

The default projection retains:

- `decision`, `should_run`, `effective_action`, and `recommended_action`;
- selected todo, bounded `action_portfolio`, read-only `planning_horizon`, and
  execution obligation;
- interaction mode, user channel, and executable agent/CLI actions;
- scheduler action and autonomous-replan authority;
- the compact vision decision, trigger kinds, required reads, and judge result;
- warning kinds, counts, stable identities, and cold-path references.

`action_portfolio` is not diagnostic candidate noise. It is retained in the
default packet because it carries the executable fallback rule when the
selected primary becomes unavailable at its real call site. Compaction may
remove the larger todo/capability candidate lists only after preserving this
bounded portfolio unchanged.

The `turn_envelope_action_dimensions_v2` base/head migration has a JSON-only,
bounded growth allowance for this additive portfolio. The allowance applies
only while a v0/v1 baseline migrates to v2, remains a review signal, and still
fails above 1,280 characters/bytes, 36 lines, or 896 compact characters. Once
v2 is the baseline, the ordinary hot-path growth limits apply again.

`quota_planning_horizon_v0` is likewise action-bearing context rather than
diagnostic noise. The compact path preserves its bounded Todo chain, typed
relations, attention ids, completeness counters, and cold-path refs unchanged.
Its `turn_envelope_action_dimensions_v3` migration receives one JSON-only
allowance of 3,200 characters/bytes, 84 lines, or 2,800 compact characters.
That allowance applies only to `none -> quota_planning_horizon_v0` together
with v0/v1/v2 action coverage moving to v3. Once v3 is the baseline, ordinary
growth limits resume. The horizon remains read-only and never replaces
`selected_todo` or explicit action-portfolio selection.

The hot-path horizon and `--include-detail agent-todos` share the same
TypeScript-owned `todo_planning_inventory_v0`; they are not aliases. The former
actively discloses at most five strategic items. The latter adds the larger
`todo_planning_inventory_detail_v0` lens, including planning state, claim state,
typed relations, and completeness, while referring to the existing Todo
summary for repeated item details. A concrete `todo list --goal-id ... --role
agent --status open --agent-id ...` command remains the complete source read.
Inventory overflow must become explicit incompleteness, not a quota failure or
an unbounded default packet.

The additive `none -> todo_planning_inventory_detail_v0` migration has a
JSON-only allowance of 1,280 characters/bytes, 36 lines, and 1,024 compact
characters. It applies only when the probe observes that exact schema change on
the explicit detail variants. Unknown schemas and larger growth fail closed;
once v0 is in the base, ordinary cold-path limits resume.

Repeated vision audits use `$.vision_continuation_audit` as the canonical
projection. Candidate lists and peer action lists retain counts and point to
`--include-detail agent-todos`. The complete vision audit is available through
`--include-detail vision`; `--include-detail all` restores every supported
detail section.

When a bounded next-action label still carries the complete Task again in
`agent_lane_next_action.title`, the default view may replace that exact duplicate
with `title_ref`. The reference resolves to `text` or `title` in the single
current selected-Todo source under this response's `work_context.sources`.
The typed work-context owner must have verified the full current requirements;
incomplete, failed, ambiguous, mismatched or differing sources retain the title.
The full requirements, source metadata, controls and original capture remain
intact. `--include-detail agent-todos` retains the title inline. This ordinary
CLI serialization applies with differential delivery off as well as on; it
neither enables native tracing nor changes the default-off transport switch.

下一动作的有界摘要可能在 `agent_lane_next_action.title` 中重复携带完整任务。
默认视图仅将与当前包内唯一、完整、已核验 Todo 源精确相同的副本替换为
`title_ref`，指向 `work_context.sources` 中的 `text` 或 `title`。
来源缺失、不完整、失败、歧义、身份不符或内容不同时保留原标题；完整要求、来源、
控制字段和原始捕获均保留。`--include-detail agent-todos` 保留内联标题。
这是普通 CLI 序列化去重，差分开关关闭和开启时均适用，不会开启原生追踪或改变默认关闭。

Exact Task-title and Explore-guidance sharing now use the existing TypeScript
context owner; the Python quota projection delegates both operations in one
coarse transport call, preserving each explicit detail selector. With both
selectors enabled, or no context source carrier, it needs no sharing call. Current
quota and Turn entrypoints retain their schemas. Explore guidance shares only
identical inline durable-writeback instructions within the same response, and
stays inline when the reference would be larger. Before relocating context,
Turn and staged replan readers restore that guidance from its settlement owner.
Dangling, chained, conflicting or incorrectly typed references reject rather
than silently losing instructions. Settlement commands, identities and original
source content remain unchanged. This shared-owner prerequisite does not qualify
the proposed complete-contract cutover or claim an end-to-end efficiency gain.

精确任务标题与 Explore 指引共享复用既有 TypeScript 上下文 owner，Python quota
投影通过一次粗粒度调用委托这两项操作，分别保留显式 detail 选择；两项 detail
均启用或不存在上下文源载体时无需共享调用。当前 quota 与 Turn 入口保持各自
schema。Explore 仅共享本响应
内与 durable-writeback 指引完全相同的正文；引用更大时保留内联。Turn 与分阶段
replan reader 在移动上下文前，从原结算 owner 恢复指引。悬空、链式、冲突或类型
错误的引用会被拒绝，不静默丢失指令。结算命令、身份与原始源内容保持不变。
这一共享 owner 前置不代表完整契约切换已通过验收，也不声称端到端效率提升。

When a replan action carries a complete `vision_authoring` schema, the default
`quota should-run` packet now retains it, alongside its executable writeback
summary (`required_fields`, accepted path outcomes, and rule). Only when the
durable-writeback step already contains an identical schema does the replan action
replace its duplicate with `vision_authoring_ref`, an exact path within the same
response. Missing or different authoring stays inline. This replaces the former
`vision_authoring_detail_ref` cold read; diagnostic vision audits remain optional
detail. Progress-only replan does not gain a Vision input requirement.
`--include-detail vision` retains full explicit detail. `turn plan` preserves the
complete schema because it must author the exact input its validator accepts.
The crowded quota and Turn budgets account for that fixed contract without
relaxing Todo-count growth or the small and multi-Agent ceilings.

## Qualification Contract

Deterministic tests own exact full-versus-compact parity, cold-path restoration,
schema shape, and the character budget. The real-scale regression must exceed
the default budget before compaction and remain within it afterward.

Model qualification is one-arm and actual-default. The shipped
`actual_default_model_behavior_portfolio_v0` sends the CLI hot-path projection,
not the unprojected in-memory decision, to the Doubao actor. Its independent
source oracle must still observe the expected selected todo, user gate,
execution obligation, scheduler route, and vision/replan behavior on every
repeat. The planning-horizon scenario additionally starts from fixed typed
facts, validates the complete strategic relation chain independently of the
producer, and requires bounded model readback of the horizon before selected
work. Removing the horizon, breaking a middle relation, or drifting both the
producer and compact packet fails before provider spend. A dedicated
compaction-regression scenario must exceed the JSON hot-path
budget before projection, fit within the budget afterward, preserve the exact
source-derived semantic contract, and preserve the model's route. Two additional
over-budget scenarios repeat clean selected-work and blocking-gate contracts
under omitted diagnostic noise. Bounded contrast results require those pairs to
remain invariant, while blocking versus non-blocking user action and selected
work versus required vision replan remain distinguishable. Exact helper
traversal, omitted counts, warning references, deduplication, and peer-route
shape remain deterministic projection-test responsibilities. The old full
packet is not retained as a permanent second product contract; paired mode is
reserved for explicit differential diagnosis.

The portfolio also includes a future-primary scenario: a typed P0 monitor whose
window is not due remains visible as unavailable higher-priority work while the
actual-default model must execute the selected ready fallback. This qualifies
model obedience to the projection; deterministic tests separately cover the
legacy case where a sticky primary survives and the packet must still expose
fallback actions.

Live receipts may retain only bounded scenario outcomes and digests. Packets,
prompts, raw model responses, credentials, and conversations remain outside the
repository.
