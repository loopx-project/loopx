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
| `quota_should_run_json` | quota guard | decide whether the selected goal may spend compute | `status`, `history`, or active state | `json_chars <= 15500` | `nested_keys <= 376` | `top_level_keys <= 54` |
| `dashboard_status_json` | operator dashboard | render first-screen operator state | `history`, run artifacts, or project-local adapter output | `json_chars <= 23000` | `nested_keys <= 350` | `top_level_keys <= 27` |

These four budgets measure compact machine payloads. For
`heartbeat_prompt_json`, the measured payload is the actual
`heartbeat_agent_input_v1` projection emitted by the recurring-host
`heartbeat-prompt --thin --format json` path, not the richer internal
generator payload. Visible one-shot Goal hosts retain their host-specific
activation packet. The other payloads are measured before stdout formatting. JSON
indentation and Markdown wrappers can make emitted output materially larger;
the emitted-output qualification matrix below measures that separate boundary
through the real CLI entry point.

The M3 automatic-cadence readback raises the quota fixture from 357 to 368
nested keys. The 11-key increase is one always-present, validated readback:
Goal, Agent and automation scope, configuration revision, effective minimum and
the owner's four-field eligibility union. The 376-key ceiling leaves eight keys
of headroom. The same envelope moves the fixture from 51 to 52 top-level keys;
the 54-key ceiling leaves two keys of headroom. The JSON-size ceiling does not
change.

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
Codex App thin fixtures. Its 5,400-character JSON ceiling remains independent of
the **3,000-character thin task body**, 4,000-character native Goal body,
structural limits, and emitted CLI ceilings. These are presentation regression
budgets, not external host limits, token counts, execution quota, or permission.
Do not abbreviate admission, continuation, scope, safety or settlement instructions
solely to fit a historical character count, or copy dynamic quota decisions into
the static prompt.

The thin body ceiling rises from 2,500 to 3,000 to retain readable peer guidance.
On the same real CLI fixture with a host scope and three runtime capabilities,
latest main measured 2,515 normalized body characters, the earlier compressed
PR measured 2,495, and readable guidance measures 2,632. With the supported
320-character scope, the same measurements are 2,805, 2,785 and 2,922; the new
ceiling leaves 78 characters on that bounded case. Single- and two-peer fixtures
give the same measurements. The normalized metric substitutes Goal/state paths,
not caller identity, scope or capabilities. This accepts useful text growth and
keeps overflow reporting and boundary tests; it does not promise arbitrary caller
paths/scopes fit every JSON or emitted-output envelope. Peer requirements defer
to current quota admission plus repository rules, without granting cross-agent
authority. Other mode, envelope and execution budgets remain unchanged.

The emitted CLI differential measures +117 characters on all three thin fixtures
in JSON and Markdown. The complete readable peer instruction block attributes a
one-time 160-character/byte/compact-character allowance to thin rows only;
missing or altered authority wording, other surfaces, and subsequent edits after
this block is in the base use the ordinary growth budget. Absolute emitted
ceilings, line limits and semantic anchors remain enforced.

thin 正文回归预算从 2,500 调至 3,000，保留完整的准入、接续与权限说明。同一真实
CLI、host scope 和三项 runtime 能力下，最新 main／此前压缩版本／清晰版本分别为
2,515／2,495／2,632 字符；320 字符 scope 时为 2,805／2,785／2,922，新预算余量
78 字符。单 peer 与双 peer 结果相同。计量只替换 Goal 与 state 路径，不缩短身份、
范围或能力。仍检查超限并保留独立 JSON 和其它模式预算；字符余量不授予执行权限。

The brief body allowance rises
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

The work-count projection retains scope and completeness facts. The current
quota ceiling rises from 14,500 to 15,500 characters to include full Goal and
selected-work required-read commands, carried once in the Agent channel. On the
same fixture, main measured 14,065 and the unified projection measured 14,916
characters; a preceding longer-reason draft measured 15,084. The 584-character
headroom accommodates useful source coordinates without truncating requirements.
Nested and top-level ceilings remain 360 and 52 (observed 357 and 51). This is a
presentation regression budget, not a token, permission or compute allowance.
Exact Todo detail removes redundant bodies through an explicit caller migration;
other quota lane projections remain separately owned.

The dashboard/status character ceiling rises from 22,500 to 23,000. On the
unchanged hot-path fixture, commit `8bb21ac5d40f6db679475495800157e20fc11c2f`
added selected-work content revisions: its parent measured 21,895 characters,
and that commit, current main and the handoff acknowledgment candidate all
measure 22,560. Seven digest fields account for the full 665-character growth.
The separate item lanes and Todo index carry those revisions into quota's
selected-work freshness checks; removing them from internal status would change
admission semantics. Existing CLI compaction still omits this metadata from the
quota Agent input. Retain the source fields and unchanged fixture, leaving 440
characters of headroom; nested/top-level ceilings remain 350/27 (observed 336/26).
This adjusts a measured regression budget, not an external limit, execution
quota, permission, transport SLO or unrestricted payload allowance.

dashboard/status 字符预算从 22,500 调至 23,000。同一 fixture 在所选任务正文
revision 引入前为 21,895 字符，引入后、当前 main 与交接回复候选均为 22,560；
七处 digest 字段解释全部 665 字符增长。各任务 lane 和 Todo 索引向 quota 传递
准入新鲜度依据，不能从内部 status 删除；现有 quota CLI 投影仍隐藏这些元数据。
保留原 fixture 和字段，余量 440 字符；嵌套键/顶层键上限仍为 350/27，实测
336/26。此调整仅为有测量依据的回归预算，不扩大权限、计算额度或传输 SLO。

quota 字符预算从 14,500 调至 15,500，容纳完整 Goal 与所选工作项的必读命令，
只在 Agent channel 携带一份。同一 fixture 的 main／统一后分别为 14,065／14,916
字符；此前较长说明草稿为 15,084。当前余量 584 字符；嵌套键与顶层键上限仍为
360／52，实测 357／51。该预算不授予权限或计算额度。精确 Todo 通过调用方迁移
移除重复正文，完整要求不截断，其它 quota lane 保留各自用途。

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

`quota should-run` uses one repeatable cold-path selector:
`--include-detail scheduler`, `agent-todos`, `user-todos`, `vision`, or
`goal-boundary`. `quota status` and `quota plan` accept `agent-todos` and
`user-todos`; `quota monitor-poll` accepts `decisions`.
`--include-detail all` expands the selected command's sections. Public docs,
emitted `detail_ref` commands, and internal callers use only this selector.
Unknown or unsupported sections fail before status collection, including when
combined with `all`. Status/plan summaries preserve counts and decisions and
declare omitted lists; explicit detail preserves the full Todo metadata. These
are CLI display projections after full planning, not truncated provider inputs.

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
closed. The intentional evidence-command retirement is recognized only with a
qualified replacement replan-context projection and remains a review signal.
Coverage-only to dense replan context has a measured, one-time allowance on its
three affected JSON surfaces; dense-to-dense changes retain ordinary limits.

Both budget layers are intentionally about projections, not the full archival
facts. When a surface needs more detail, put that detail behind a queryable
cold-path command or a linked run-history artifact instead of making the
recurring heartbeat prompt carry it. `nested_keys` counts dictionary keys
through three payload levels and samples at most 20 list items per level; it is
a hot-path structure budget, not an archival record-size budget.

Required replan is a decision phase with a separate information need. Its
`replan_context` carries the core Goal and up to 24 distinct observations from
the full compact index, reducing repetitions before selection. On the unchanged
crowded public CLI fixture, emitted JSON grows from 26,844 to 33,523 characters
and 718 to 806 lines. The replan scenario ceiling moves from 30,000/750 to
34,000/830, with 6,000 fixed semantic growth characters; ordinary and multi-Agent
non-replan guards retain their previous output and ceilings. Handoff forwarding
keeps a brief evidence pointer and the full review packet owns the structured
context, removing duplicate JSON. The explicit cold-path diagnosis retains its
existing selected-packet plus Goal-array contract; its replan fixture measures
43,132 characters / 804 lines and uses 44,000 / 850 ceilings with 7,000 fixed
semantic growth characters. These are output measurements, not model-token,
latency or long-horizon quality qualifications. Settlement checks use complete
available history even when the readable context omits older observations.

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
