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
| `quota_should_run_json` | quota guard | decide whether the selected goal may spend compute | `status`, `history`, or active state | `json_chars <= 20500` | `nested_keys <= 525` | `top_level_keys <= 54` |
| `dashboard_status_json` | operator dashboard | render first-screen operator state | `history`, run artifacts, or project-local adapter output | `json_chars <= 24000` | `nested_keys <= 350` | `top_level_keys <= 27` |

These four budgets measure compact machine payloads. For
`heartbeat_prompt_json`, the measured payload is the actual
`heartbeat_agent_input_v1` projection emitted by the recurring-host
`heartbeat-prompt --thin --format json` path, not the richer internal
generator payload. Visible one-shot Goal hosts retain their host-specific
activation packet. The other payloads are measured before stdout formatting. JSON
indentation and Markdown wrappers can make emitted output materially larger;
the emitted-output qualification matrix below measures that separate boundary
through the real CLI entry point.

The current quota fixture measures 15,419 characters, 378 nested keys, and 52
top-level keys on `main`. The `quota_should_run` projection envelope raises the
same workload to 18,636 characters, 513 nested keys, and 53 top-level keys. The
new keys carry the inherited status sources, direct live-read facts, freshness,
alerts, and Goal coverage that a consumer must inspect before spending compute.
The character ceiling moves from 16,500 to 20,500, and the nested-key ceiling
moves from 386 to 525. The top-level ceiling stays at 54. The new ceilings leave
1,864 characters, 12 nested keys, and one top-level key on the measured head.
Removing envelope rows would violate the projection contract, so the budget
records the useful contract growth instead of hiding it through compaction.

同一合成 workload 在 `main` 上为 15,419 字符、378 个嵌套键和 52 个顶层键；加入
`quota_should_run` projection envelope 后为 18,636 字符、513 个嵌套键和 53 个
顶层键。新增字段携带继承的 status 来源、实时读取事实、freshness、告警与 Goal
覆盖，消费者必须在花费 compute 前读取这些信息。字符上限从 16,500 调至 20,500，
嵌套键上限从 386 调至 525，顶层键上限仍为 54。精确 head 仍保留 1,864 字符、
12 个嵌套键与一个顶层键余量。删除 envelope 行会破坏投影合同，因此这里记录有用的
合同增长，而不是用压缩隐藏它。

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
the **3,600-character thin task body**, 4,000-character native Goal body,
structural limits, and emitted CLI ceilings. These are presentation regression
budgets, not external host limits, token counts, execution quota, or permission.

All scoped heartbeat envelope fixtures now measure that same shipped Agent
projection, rather than generator diagnostics. On the unchanged two-scope fixture,
the shared stop/readback clause changes Agent JSON from 3,182 to 3,299 characters;
the 5,400 JSON ceiling stays unchanged. Separately, the compact
scoped body grows from 7,086 to 7,213 normalized characters, including the fixed
640-character Reward Memory allowance, exceeding its previous 7,140 limit.
The compact base allowance becomes 6,700 (7,340 with that optional contract),
preserving the exact heartbeat target, single attempt, verified result and
unconfirmed-stop handling instead of deleting those obligations. The real thin CLI
fixture with a supported 320-character scope grows from 2,992 to 3,109; its body
ceiling becomes 3,200. Full/brief/native Goal body limits, key counts and execution
budgets do not change. The broader full-scoped fixture already
exceeded its limit before this repair and remains an overflow diagnostic.

同一 scoped fixture 的 Agent JSON 从 3,182 增至 3,299 字符，仍沿用 5,400 上限；
不再把生成器诊断误算为 Agent 输入。compact 正文从 7,086 增至 7,213（均含可选契约的
640 字符余量），超过旧 7,140；基础展示预算仅从 6,500 调至 6,700，保留停止对象、
单次尝试、结果确认与未确认处理。真实 thin CLI 的 320 字符范围输入从 2,992 增至 3,109，正文上限调至 3,200；
full／brief／native Goal、字段数和执行预算不变；原已超限的 full
大范围 fixture 仍如实报告超限。

Do not abbreviate admission, continuation, scope, safety or settlement instructions
solely to fit a historical character count, or copy dynamic quota decisions into
the static prompt.

The same two-scope Codex App brief fixture measures 4,284 normalized body
characters on v1.4.1 and 4,447 with the shared stop/readback rule: 163 additional
characters preserve the exact heartbeat target, one attempt, verified stopping,
unconfirmed-result handling and prohibition on work/spend/retry fallback. The
brief presentation allowance becomes 4,600, leaving 153 characters of headroom.
The two original scope paragraphs and current-Turn settlement instructions stay
intact. This is a regression-budget adjustment, not a model context limit or an
execution allowance; all other mode limits, field counts, quota and authority
remain unchanged. The original 4,300-character failure is retained in validation.

同一 Codex App brief 双范围 fixture 在 v1.4.1 为 4,284 个归一化正文字符，加入共享停止/
读回规则后为 4,447，增加 163；保留准确停止对象、单次尝试、停止验证、未确认结果处理
以及禁止 work/spend/retry fallback。brief 展示预算调至 4,600，保留 153 字符余量，
不删除两段原范围与当前 Turn 结算规则。其余模式、字段数、执行配额和权限不变；
原 4,300 上限失败保留，不能将新展示预算称为模型上下文或执行额度提升。

The earlier readable-peer qualification raised the thin body ceiling from 2,500
to 3,000; those historical measurements follow.
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

The pause/correction qualification measures the same small/crowded/multi-Agent
thin CLI fixtures at `a59aed703` as 3,070 JSON / 4,998 Markdown characters,
the pause-only branch as 3,187 / 5,115, and the correction refinement as
3,224 / 5,152; all remain 65 Markdown lines. The prior 5,100 Markdown ceiling
already fails on pause-only instructions. Retain verified-stop and Core-owned
correction clauses and raise only that human diagnostic ceiling to 5,250;
the 3,400 JSON ceiling, body, line, key and execution budgets stay unchanged.
The ordinary generated full body shrinks by 215 characters; compact/brief/thin
grow by 88/46/37 on the matched public fixture. No second summary or model call
is added. Replan advice gains 363 text bytes for local scope and outcome checks;
its separate advice allocation becomes 1,800 bytes, preserving the 8 KiB envelope
warning and any already-over-budget fixture instead of truncating requirements.

暂停／纠偏的同一三场景 thin CLI 从 main 的 3,070／4,998 字符，经过暂停补丁的
3,187／5,115，变为 3,224／5,152（JSON／Markdown）；Markdown 均为 65 行。
仅人工诊断输出上限由 5,100 调至 5,250，机器 JSON／正文／行数／执行预算不变。
重规划指导增加 363 字节，独立指导预算为 1,800；8 KiB 总包告警及原有超限仍保留。

The same qualification also covers emitted `--compact` Markdown, independently
of the task-body and thin-output limits. On the unchanged native mode fixture,
base `3e611660a` emits 11,315 characters and the pause/correction head
`84e00bac1` emits 11,530, failing the old 11,500 ceiling; both have 145 lines.
The 215-character growth carries the current-heartbeat single stop attempt,
confirmation and unresolved-stop handling, plus evidence-scoped correction and
original settlement obligations. These clauses guide different decisions and
are not removable duplicate diagnostics. Preserve the complete text and raise
only this Markdown regression ceiling to 11,750, leaving 220 characters of
headroom (the base had 185) for bounded wording variation. JSON grows from
12,658 to 12,873 under the unchanged 13,500 ceiling; the 155-line cap, semantic
anchors, fixture population, growth checks and execution limits remain intact.
This qualifies readable output size, not installed host behavior or model adoption.

The base/head differential independently detects the same instruction rollout:
work-context text adds 208 characters (one receipt character may vary), optional
replan advice adds 363, and thin/brief/compact heartbeat output adds 154/163/215.
The existing probe records complete historical/current clauses and grants a
one-time allowance only on their named carriers: 224 characters for context,
384 for replan advice, or 224 for heartbeat text. Context and replan allowances
combine only when both transitions are observed. JSON bytes/compact characters
follow the same bounded allowance; line limits and absolute ceilings do not
change. Missing or contradictory observations, already-upgraded bases, other
surfaces and growth beyond the allowance retain ordinary failure behavior.
This evidence does not qualify arbitrary future wording or runtime authority.

同一资格化还须独立覆盖 `--compact` 的完整 Markdown 输出：不改原生 mode 夹具时，
基线 `3e611660a` 为 11,315 字符，暂停／纠偏头 `84e00bac1` 为 11,530，
确实超过旧 11,500 上限；两者均为 145 行。新增 215 字符承载当前心跳单次停止、
结果确认、未确认处理、证据限定的局部纠偏及原结算义务，各自支持不同决定，
不能作为重复诊断删除。保留全文，仅将该 Markdown 回归上限调至 11,750，
留 220 字符余量供有界措辞变化（基线余量为 185）。JSON 从 12,658 增至
12,873，仍遵守原 13,500 上限；155 行、语义锚点、夹具规模、增长检查和执行限制
不变。这只资格化可读输出尺寸，不证明安装态行为或模型采纳。

base/head 差异门独立测得工作上下文增加 208 字符（回执可能差 1 字符）、可选
replan 指引增加 363、thin/brief/compact 输出增加 154/163/215。既有 probe
记录完整的新旧条款，仅在对应输出表面首次迁移时分别允许 224、384 或 224 字符；
上下文与 replan 余量只有同时观察到两项迁移才相加。字节及 compact JSON 使用同一
有界余量，行数和绝对上限不变。缺失／矛盾观察、已升级基线、其他表面或超额增长
仍失败；这不资格化未来任意措辞，也不改变运行时权限。

此前 thin 正文回归预算从 2,500 调至 3,000，保留完整的准入、接续与权限说明。同一真实
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

The complete vision-rule qualification preserves the original fixtures and
failures. The same 36-Todo / 12-run default quota stdout measures 45,313
characters with the old lossy projection and 48,418 with the complete audit;
explicit vision detail remains 57,346. Its separate real-vision ceiling changes
from 46,000 to 50,000, leaving 1,582 characters. Differing channel judges remain
inline: a subset is not an identical duplicate. The original 40,000-character
compactor fixture remains unchanged. Crowded Turn JSON already measured 21,238
characters / 477 lines on both formatter versions, above 18,000 / 460; its
qualified ceilings are 22,500 / 500, leaving 1,262 characters / 23 lines.

Raw quota is a different boundary. The unchanged packet measures 15,201 compact
JSON characters with the ordinary temporary root and 15,865 with the original
longer root. Preserve full command routes; its ceiling changes from 15,500 to
16,500, leaving 635 characters on that failing fixture. Both roots retain 368
bounded nested keys and 52 top-level keys, below the unchanged 376 / 54 guards.
That same longer-root run then exposes a previously masked status failure:
23,274 characters against 23,000 (ordinary root: 22,560). Preserve complete
source revisions and paths; 24,000 leaves 726 characters, with 336 nested /
26 top-level keys still below the unchanged 350 / 27 guards.
Characters, UTF-8 bytes and line counts are measured separately. These local
measurements do not qualify native Windows or arbitrary path lengths. Other
surface/mode ceilings, collection-growth guards, production overflow diagnostics,
execution quota and permissions remain unchanged; original failures remain
failed historical evidence. See the [budget decision guide](../../development/testing-and-quality.md#budget-failure-decisions).

完整 vision 规则资格化保留原夹具与失败：同一 36 Todo／12 run 默认 quota 输出从
有损的 45,313 字符变为完整的 48,418，详情仍为 57,346；独立真实 vision 展示上限
46,000→50,000，余量 1,582。不同 channel 的 judge 子集仍内联，不能当相同副本
引用；原 40,000 字符 compactor 夹具不变。Turn 在两个版本均为 21,238 字符／477
行，原 18,000／460 失败保留，新限 22,500／500，余量 1,262 字符／23 行。
raw quota 在普通／原较长临时根目录为 15,201／15,865 字符，完整命令路由保留；
上限 15,500→16,500，失败夹具余量 635。两者结构均为 368 nested／52 top-level
keys，376／54 不变。字符、UTF-8 字节与行分别测量；不宣称原生 Windows 或任意
路径长度已验。其它模式、增长、生产超限诊断、执行配额与权限不变。
同一长根目录随后暴露此前被 quota 失败遮住的 status 原失败：23,274>23,000，
普通根目录为 22,560。完整 source revision／路径保留，新限 24,000、余量 726；
336 nested／26 top-level keys 仍低于不变的 350／27 限制。

The shared CLI printer now emits compact UTF-8 JSON with one terminal newline,
including default quota, detail selectors, and typed error results. This is a
default presentation change across commands using `print_payload`, not a quota
policy or schema change. It removes indentation and separator whitespace only:
all fields, unknown future fields, string whitespace, escaped newlines, ordering,
and in-response references remain. Markdown and paths that print directly
(such as message-only bootstrap output) retain their existing presentation.
For human inspection use `--format markdown` or pipe JSON through `jq .`.

The same 36-Todo / 12-run vision workload compares the pinned two-space printer
with the compact printer on each identical decided payload:

| Quota JSON mode | Previous chars / UTF-8 bytes / lines | Compact chars / UTF-8 bytes / lines |
| --- | --- | --- |
| Default | 48,418 / 48,418 / 1,090 | 39,726 / 39,726 / 1 |
| Vision detail | 57,346 / 57,346 / 1,226 | 47,201 / 47,201 / 1 |
| All detail | 70,850 / 70,850 / 1,607 | 57,186 / 57,186 / 1 |

The default saves 8,692 presentation characters (18.0%) without removing a
rule. Across 336 measurements (three populations, vision off/on, both formats,
default and explicit modes), all 162 shared-printer JSON pairs decode to the
complete original payload; all Markdown and direct-output measurements match.
Raw compact-payload, structural, growth and emitted ceilings remain unchanged,
including the original 40,000-character fixture. These local measurements do
not establish token, model, native Windows or installed-release improvements.

共用 CLI 打印器默认输出紧凑 UTF-8 JSON，以一个换行结尾；默认 quota、详情和类型化
错误都只删除展示缩进与分隔空白。字段、未知未来字段、字符串空白、转义换行、顺序
及引用保持完整，Markdown 与直接输出路径不变；人工查看可用 `--format markdown`
或 `jq .`。同一 36 Todo／12 run 的默认输出从 48,418 降至 39,726 字符，节省
8,692（18.0%），完整规则保留。336 项配对测量中的 162 项共用 JSON 输出解码值
完全相同；原始预算及增长限制不改，不将字符收益宣称为 token 或模型收益。

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

### Current-contract delivery prompt allowance

The instruction-coherence change keeps action-specific authorization, sustained
integration/negative-case/readback work and exact current settlement/recovery in
all heartbeat modes. On the same supported 320-character scope plus capability
fixture, normalized thin body cost is 3,172 → 3,599 characters. The thin body
ceiling is 3,600; CLI JSON/Markdown ceilings are 3,900/5,800 (previously
3,400/5,250), against measured 3,651/5,579 on the same public fixture.
The registered-profile brief body grows from 4,447 to 4,675, with a 4,800
ceiling (previously 4,600). Full/compact remove duplicated static recipes; no host execution quota, authority,
line, key or reward-memory allowance changes. Oversized custom inputs still fail.

The existing differential observer recognizes the complete reviewed old→new
instruction transition only on thin/brief/compact carriers, allowing at most 512
characters once. Missing clauses, unrelated surfaces and later new→new growth
retain ordinary gates. This is cost attribution, not admission logic or proof of
semantic equivalence. The PR's clause review and current-path validation remain
necessary; deleting an obligation cannot earn this allowance.
