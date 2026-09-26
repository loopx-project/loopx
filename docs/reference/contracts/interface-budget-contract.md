# Interface Budget Contract

LoopX keeps hot-path worker surfaces small enough that a short heartbeat
can route work without reading raw run history or long chat context. This is a
restraint contract, not an encouragement to add more state surfaces. Each
surface below has a single owner, a named consumer action, a cold-path fallback,
and size/count budgets.

| Surface | Owner | Consumer Action | Cold Path | Size Budget | Nested Budget | Count Budget |
| --- | --- | --- | --- | --- | --- | --- |
| `heartbeat_prompt_json` | heartbeat automation | wake and route one bounded turn | `quota should-run`, `status`, or `review-packet --handoff-only` | `json_chars <= 5400` plus `interface_budget.within_budget=true` | `nested_keys <= 40` | `top_level_keys <= 30` |
| `review_packet_handoff_only_json` | project-agent handoff | forward the smallest sufficient task packet | full `review-packet` or run-history artifact | `json_chars <= 3000` plus `handoff_interface_budget.within_budget=true` | `nested_keys <= 40` | `top_level_keys <= 18` |
| `quota_should_run_json` | quota guard | decide whether the selected goal may spend compute | `status`, `history`, or active state | `json_chars <= 14500` | `nested_keys <= 360` | `top_level_keys <= 52` |
| `dashboard_status_json` | operator dashboard | render first-screen operator state | `history`, run artifacts, or project-local adapter output | `json_chars <= 22500` | `nested_keys <= 350` | `top_level_keys <= 27` |

These four budgets measure compact machine payloads. For
`heartbeat_prompt_json`, the measured payload is the actual
`heartbeat_agent_input_v1` projection emitted by the recurring-host
`heartbeat-prompt --thin --format json` path, not the richer internal
generator payload. Visible one-shot Goal hosts retain their host-specific
activation packet. The other payloads are measured before stdout formatting. JSON
indentation and Markdown wrappers can make emitted output materially larger;
the emitted-output qualification matrix below measures that separate boundary
through the real CLI entry point.

The successful thin heartbeat projection contains only `schema_version`,
`ok`, `goal_id`, optional `agent_id`, `task_body`, and the compact
`interface_budget`; exact Turn identity and `bootstrap=true` are included only
when requested. Generator provenance, resolved paths, mode booleans, policy
source strings, runtime diagnostics, and command copies already embedded in
`task_body` stay out of the Agent input. A failed projection contains the
schema, `ok=false`, Goal/optional Agent identity, and the actionable `error`.
Use Markdown output for human generator diagnostics or a non-thin JSON mode
for the richer generator packet; neither is the recurring Agent hot path.

The heartbeat envelope ceiling covers the unbound and representative agent/scope-bound
Codex App thin fixtures. It includes generator metadata and repeated bound commands,
not only the execution prompt. On the same scoped fixture, current main uses
4,791 JSON characters and the language-aware body uses 5,167 while retaining
static safety, repair routing, work obligation, and settlement instructions.
The 5,400-character ceiling leaves 233 characters of fixture headroom, without
relaxing the independent **2,500-character thin task body**,
4,000-character native Goal body, structural limits, or emitted CLI ceilings.
It is not a token count, execution quota, or allowance to append more instructions.
Arbitrary-length caller paths/scopes are not promised to fit this fixed fixture
envelope; their emitted output is qualified separately by the CLI matrix.
Do not remove safety or settlement semantics to fit the envelope, and do not copy
dynamic quota decisions into the static prompt. The brief body allowance rises
from 3,500 to 4,300 characters. Translating its fixed Chinese instructions to
English grows the Codex App brief body from 3,282 to 3,980 characters (3,494 to
4,192 with two agent-profile scopes) while its `o200k_base` token count falls
from 962 to 927 (994 to 959) and UTF-8 bytes grow about 2%. The character
ceiling therefore moves with the script, not with prompt cost, and keeps about
8% headroom for the unscoped fixture, close to the previous 7%. Saved
automation, scheduler cadence, and spending policy do not change with these
budget adjustments.

The quota budget includes the typed action portfolio, one shared bound CLI
route, pending-selection qualification, and hard-lane preemption evidence. The
budget retains modest headroom for those enforceable semantics; repeated action
details and command prefixes still belong in compact references or cold paths.

The work-count projection adds scope and completeness facts that a bounded Todo
list cannot supply. Its observed-row count is derived from `open - hidden`,
rather than repeated in the wire object. The quota ceiling moves from 14,000
to 14,500 characters and from 350 to 360 nested keys to retain modest headroom
for this useful semantic growth; the top-level ceiling stays 52. Existing
repeated Todo bodies across named lanes have distinct consumers and cannot be
removed without a separately validated caller migration.

工作计数增加了展示列表无法提供的完整性与作用域信息；已观察行数由 `open - hidden`
推导，不重复传输。quota 字符预算从 14,000 调至 14,500，嵌套键从 350 调至 360，
保留适量余量；顶层键上限仍为 52。不同 lane 重复携带的 Todo 有既有消费者，后续
去重应配合调用方迁移，不能仅为通过尺寸测试而删除。

| Emitted Surface | Default Qualification | Scale / Limit Contract | Cold Path |
| --- | --- | --- | --- |
| `start-goal --guided` | baseline and growth | small, crowded, and multi-agent goals; objective/command duplication | `packet_summary.detail_refs` and `bootstrap-command-pack` |
| `bootstrap-command-pack` | baseline and growth | small, crowded, and multi-agent goals; objective/command duplication | `--message-only` and `packet_summary.detail_refs` |
| `quota should-run` | absolute hot path | todo-count growth plus semantic anchors | `status`, `history`, active state, repeatable `--include-detail <section>` |
| `status --goal-id` | absolute hot path | todo-count growth; task graph excluded by default | `--include-task-graph`, `history`, run artifacts |
| `diagnose --goal-id` | explicit-limit cold path | `--limit 5` fixture matrix | status plus goal-specific quota/todo reads |
| `review-packet --handoff-only` | absolute hot path | todo-count growth plus handoff semantic anchors | full `review-packet`, run artifacts |
| `heartbeat-prompt --thin` | absolute hot path | agent scope, multi-agent fixture matrix, and exact Agent-input field allowlist | Markdown diagnostics, `--compact`, `--full` |
| `todo list` | baseline and growth | todo-count growth and agent filtering semantics | `--thin`, `--limit N`, role/status filters, direct todo-id lifecycle commands |
| `history --limit 5` | explicit-limit cold path | returned-run bound | individual run JSON/Markdown artifacts |
| `evidence-log --thin --limit 5` | explicit-limit cold path | returned-evidence bound | referenced run-history and rollout-event artifacts |

`quota should-run` uses one repeatable cold-path selector:
`--include-detail scheduler`, `agent-todos`, `user-todos`, or
`goal-boundary`; `--include-detail all` expands every section. Public docs,
emitted `detail_ref` commands, and internal callers use only this selector.
Unknown sections and selectors attached to another quota command fail before
status collection.

The canonical emitted-output inventory and current characterization ceilings
live in `loopx.control_plane.testing.cli_output_budget`. Those ceilings are
regression baselines, not target sizes: unexplained growth fails, while measured
consumer value can justify compaction or a reviewed increase. Tests
also record UTF-8 bytes, line count, JSON parseability, pretty-print overhead,
semantic anchors, collection-growth slope, and bootstrap duplication. Every
declared agent-facing surface must name an owner, consumer action, and cold-path
fallback.

The same matrix characterizes explicit mode switches instead of assuming that
the default command represents them. Covered variants are
`bootstrap-command-pack --message-only`, quota per-section and all-detail
selectors, TurnEnvelope output, status task-graph detail, the full review
packet, and the brief/compact/full heartbeat prompt modes. These remain opt-in
cold paths, but their exact stdout size and semantic anchors are regression
contracts too.

The user-language prompt transition is one measured exception to ordinary
base/head growth, scoped to heartbeat rows and only when the base lacks the
rendered language-policy revision. On the same small CLI fixture, `origin/main`
to this branch grew by 376 characters for thin, 695 JSON / 690 Markdown
characters and five Markdown lines for brief, 264 / 262 characters for
compact, and 258 / 260 for full. Replacing fixed Chinese instructions and
restoring blocker/next-action continuation gives the worker usable language
and work guidance; removing those clauses solely to fit the old delta would
lose that consumer value. The one-time per-mode allowances are 400, 720, 288,
and 288 characters respectively, plus six lines for brief. The absolute
surface ceilings, UTF-8 byte limits, quota/status budgets, and normal growth
limits after this revision becomes the baseline remain unchanged.

`todo list --thin` is an explicit bounded projection, not a new filtering or
ordering mode. After the normal role, status, Todo-id, and agent filters run,
it keeps at most two matched items per role in one top-level `todos` container.
The hot item shape keeps `text` and omits its redundant derived `title` field so
the maximum retained field shape stays inside the registered fixed budget.
The payload preserves the existing `todo_count` meaning, adds full-match
`matched_todo_count` plus `returned_todo_count` and `omitted_todo_count`, and
repeats per-role overflow readback in `payload_compaction`. Retained strings
and nested scope collections are bounded as declared by
`todo_list_thin_projection_v0`, while actionable identity and gate/monitor
relationship fields stay allowlisted. Local paths, note/evidence detail, and
duplicate summary lanes remain omitted.

`--limit N` composes by lowering the intrinsic per-role cap to `min(N, 2)`;
it never expands the thin projection. Use `todo list` without `--thin`, the
direct Todo-id cold path, or active state when omitted items or full fields are
required. Omitting `--thin` restores the existing full list shape without
changing Todo selection, ordering, quota, lifecycle, or write behavior.

The start and daily command groups in the public help surface, plus
`heartbeat-prompt`, are fail-closed inventory inputs. Each command must map to
a default qualified surface or carry an explicit cold-path exception with a
rationale. The canary planner selects the output-budget profile for CLI command,
help, implementation, fixture, workflow, or budget-contract changes, and PR CI
runs the matrix as a named step.

The qualification also runs the same public fixture against `origin/main` and
the candidate checkout. It allows only policy-sized growth for each surface and
format, rather than treating one global percentage as safe. A smaller candidate
still fails when it removes a declared semantic key or changes an existing
`action_signature` semantic contract. Removed observed nested JSON paths or
Markdown headings are reported as review signals: sensitive output reductions
must account for them in human review, while an intentional presentation or
structural refactor does not become a permanent CI red light.
The receipts contain counts, shape paths, headings, and digests only; they do
not persist raw CLI output. Candidate-only surfaces are allowed after their
absolute characterization passes, while removing a qualified base row fails
closed.

Both budget layers are intentionally about projections, not the full archival
facts. When a surface needs more detail, put that detail behind a queryable
cold-path command or a linked run-history artifact instead of making the
recurring heartbeat prompt carry it. `nested_keys` counts dictionary keys
through three payload levels and samples at most 20 list items per level; it is
a hot-path structure budget, not an archival record-size budget.

Restraint rules for new fields:

1. Prefer adding evidence to run history, then projecting only the smallest
   decision summary into a hot-path surface.
2. A hot-path field must answer a current consumer action. If the consumer only
   says "nice to inspect", keep the field in the cold path.
3. For a new nested object or a budget failure, compare compaction, retaining the
   limit, and an evidence-backed increase using the
   [budget decision guide](../../development/testing-and-quality.md#budget-failure-decisions).
   Update the owning contract and tests together when the budget changes;
   preserve semantic checks and the original measurement scope. Similar
   objects with different consumers are not automatically redundant.
4. Do not add prompt branches to compensate for an unclear payload. Clarify the
   status/quota/review-packet contract instead.
5. If a short worker would need to read more than one hot-path payload before it
   can choose the next action, demote the extra detail to a cold-path command.

The quota guard keeps top-level `action_required` and `open_count` as compact
compatibility aliases for older heartbeat/host prompts; the authoritative
structured fields remain `interaction_contract.user_channel` and
`user_todo_summary`.

Regression entrypoints:

```bash
pytest -q tests/control_plane/test_cli_output_budget.py
pytest -q tests/control_plane/test_cli_output_differential.py
python3 examples/control_plane/cli-output-base-head-differential-smoke.py
python3 examples/control_plane/cli-output-budget-regression-smoke.py
python3 examples/control_plane/hot-path-interface-budget-smoke.py
python3 examples/control_plane/status-quota-perf-budget-smoke.py
```

Cadence contract:

The same smoke also emits and validates an `interface_budget_cadence` summary
for clean drift checks. A drift-check run may record that summary in run
history; `loopx status` projects it under
`attention_queue.items[].project_asset.interface_budget_cadence`, and
`quota should-run` mirrors the selected goal summary at top level. This lets a
short heartbeat quiet-skip a still-fresh clean check without losing the ongoing
guard todo.

Stable cadence fields:

- `checked_at`: when the hot-path budget check was run.
- `freshness_hours`: how long the clean check remains fresh.
- `next_check_due_at`: when the next check is due.
- `overdue`: whether the current summary is past `next_check_due_at`.
- `within_budget`: whether all measured hot-path surfaces fit their budgets.
- `minimum_headroom_ratio`, `tightest_surface`, `tightest_metric`, and
  `headroom_remaining`: compact headroom evidence for the tightest observed
  surface.
- `recommendation`: either `quiet_skip_until_next_check_due` or
  `rerun_hot_path_interface_budget_smoke`. Fresh checks only quiet-skip when
  every surface is within budget and the tightest metric still has positive
  headroom; a zero-headroom surface is already at the compatibility edge and
  should rerun the smoke before more hot-path growth is accepted.

Do not add a heartbeat prompt branch for this cadence. Store exact measurements
in run history, project only this compact decision summary, and rerun the smoke
when `overdue=true`, when `headroom_remaining <= 0`, or when a
prompt/status/quota/review-packet/dashboard contract changes.

Scheduler reset policy budget:

`quota should-run.scheduler_hint.reset_policy` is a host-action summary, not a
debug snapshot. It carries the reset token, host state key, initial Codex App
RRULE, unchanged-state clear flag, and short identity/profile signatures needed
to detect reset transitions. Full identity/profile snapshots stay off the hot
path; use status, history, active state, or a focused regression fixture when
debugging why a reset token changed.

### Status projection envelope budget decision

The unchanged dashboard fixture measured 19,455 compact JSON characters, 244
nested keys and 25 top-level keys before the projection envelope; the initial
envelope measured 21,518 / 332 / 26. The old 19,500 / 260 / 25 ceilings were
regression budgets, not transport limits. The operator needs source read times,
read failures and scope coverage to distinguish a cached or partial observation
from a current, complete view. Per-source rows support diagnosis and replay;
removing them would lose that contract. The bounded five-source envelope is
retained rather than shortening names or shrinking the fixture. Ceilings become
22,500 / 350 / 27, leaving 982 characters, 18 nested keys and one top-level key
above the measured head for variation. Other hot surfaces retain their budgets.
This adds a read contract to default status; it grants no execution authority.

同一 dashboard 负载在新增 envelope 前为 19,455 字符／244 个嵌套键／25 个顶层键，
初始 head 为 21,518／332／26。旧上限属于回归预算而非传输硬限制。操作员需要
来源读取时间、错误与范围覆盖来识别缓存和部分观察；逐来源数据还支撑诊断和重放，
不能为过线删除。保留五个有界来源，不缩小负载或改短字段名，将上限同步调整为
22,500／350／27，较实测 head 保留 982 字符、18 个嵌套键和一个顶层键的余量。
其他热表面预算保持原值。默认 status 新增读合同，不授予执行权限。

The emitted CLI matrix separately measured +2,801 pretty JSON characters,
+102 lines and +1,910 compact characters on small, crowded and multi-agent
status fixtures. Markdown added 127 characters before the explicit schema
marker. The existing schema-transition mechanism grants **only status and its
explicit task-graph variant**, and only `none -> loopx_projection_envelope_v0`,
3,000 JSON chars/bytes, 110 lines and 2,048 compact chars; Markdown receives
192 chars/224 bytes and three lines. The marker makes this transition visible
and review-required. Unknown schemas, reverse transitions, unrelated surfaces
and subsequent v0 growth retain ordinary budgets. Absolute ceilings stay intact.

CLI 同负载差分另测得 JSON 增加 2,801 字符、102 行、1,910 个紧凑字符；Markdown
在显式 schema 标识前增加 127 字符。沿用既有 schema 迁移预算机制，仅 status 及
其 task-graph 显式变体的 none → v0 获得一次 3,000 JSON 字符／字节、110 行、
2,048 紧凑字符余量；Markdown 余量为 192 字符／224 字节和三行。该迁移必须评审。
未知 schema、反向迁移、其他表面和后续 v0 增长使用普通预算，绝对上限保持不变。
