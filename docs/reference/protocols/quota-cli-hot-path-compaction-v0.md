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

During the C1 migration, exact current-Task title sharing is owned by the
TypeScript decision composer in `turn_envelope.ts`. The legacy Python display
helper shares one coarse transport call with Explore-guidance sharing; each
explicit detail selector is preserved. No source carrier or both detail selectors
means no sharing call. Its label-prefix omission stays display-only.
Complete `loopx_turn_envelope_v1` decisions share before hashing/capture and
validate the same-capture target on recovery. Conflicting inline/reference values,
chains, malformed paths or a no-longer-verified selected Task reject even with
valid integrity hashes. This source sharing does not require differential delivery.
The `--include-detail` behavior above describes the historical display projection;
it does not select an alternative complete contract. Retire its delegate with the
legacy display callers while retaining supported historical capture recovery.

C1 迁移中，当前 Task 标题的精确共享归 `turn_envelope.ts` 的 TypeScript decision
composer 所有；旧 Python 展示 helper 与 Explore 指引共享合用一次粗粒度调用，
分别保留 detail 选择；无源载体或两项 detail 均开启时无需共享调用。按 label 前缀
省略仍仅属于展示。
完整 `loopx_turn_envelope_v1` 在 hash／capture 前共享，恢复时验证同一 capture 的目标。
原文／引用冲突、引用链、非法路径或当前选中 Task 无法继续核验时，即使完整性 hash
有效也拒绝。该来源共享不要求开启差分。上文 `--include-detail` 描述历史展示投影，
不会选择另一套完整契约；委托随旧展示调用者退役，支持的历史 capture 恢复继续保留。

The same composer also owns exact nested vision-audit sharing. The root keeps the
full audit and judge; the existing `projection_ref` carries only matching audit
and judge decision fields. Type-sensitive full-JSON comparison preserves different
channel facts and short audits inline. Complete readers reject unavailable owners,
reference chains and conflicting copies before effects. Historical envelopes keep
the inline-root judge, and the old display adds only its existing metadata. This
delegate retires with the same caller migration; it is not a second rule owner.

同一 composer 也拥有嵌套 vision audit 的精确共享。根保留完整 audit 和 judge；已有
`projection_ref` 仅携带匹配的 audit／judge 判定字段。完整 JSON 的类型敏感比较使
不同 channel 事实和短 audit 保持内联。完整 reader 在产生效果前拒绝不可用 owner、
引用链及冲突副本。历史 envelope 保留内联根 judge，旧展示只添加既有 metadata；
委托随同一调用链迁移退役，不构成第二个规则 owner。

A declined `should-run` decision with an interaction contract uses that same
complete composer before capture. Failure still prevents host handoff and native
context-baseline publication; validation never grants work or settlement. An
error before an interaction contract exists remains the original typed diagnostic
and recovery payload, with either CLI flag spelling. It is neither a fabricated
work contract nor a historical-envelope fallback. Readers must preserve the
original receipt and capture when transport fails after admission.

已有 interaction contract 的拒绝 `should-run` 判定也在 capture 前使用同一完整
composer。失败仍阻止 host handoff 和原生 context baseline 发布，验证不授予工作
或结算权限。interaction contract 形成前的错误在两种 CLI flag 写法下都保留原
typed 诊断及恢复 payload，不补造工作契约、不回退历史 envelope。准入后传输失败
时，reader 必须保留原 receipt 和 capture。

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
