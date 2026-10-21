# TurnEnvelope v0

`loopx_turn_envelope_v0` is an additive, bounded read model over an already
computed `quota should-run` decision. It gives an agent the next action and its
safety contract without replaying every diagnostic lane in the full quota
payload.

Preview it explicitly:

```bash
loopx --format json quota should-run --goal-id <goal-id> --agent-id <agent-id> --turn-envelope
```

For a tool with bounded output, optionally capture the full decided payload before
its display projection. Choose a **new directory for each invocation**, even when
reentering the same Turn:

```bash
loopx --format json quota should-run --goal-id <goal-id> --agent-id <agent-id> \
  --turn-instance-id <turn-id> --turn-envelope --decision-output-dir ./guard-001
cat ./guard-001/decision.json
```

`--decision-output-dir` is valid only with `quota should-run` and an explicit
`--turn-instance-id`. Its parent must exist. LoopX creates the directory privately
(0700 on POSIX) before running the guard, rejects existing paths including
symlinks, and atomically publishes `decision.json` (0600 on POSIX) before printing
the result. The file contains the unprojected decision and its original receipt;
it excludes display-only host poll metadata. It can contain private Goal context:
keep it out of public artifacts. No capture is created without a capture option.

If a tool truncates the displayed JSON, read this file instead of rerunning the
guard merely to recover the observation. The file is **not fresh authority**:
Todo selection, lease changes, cancellation, quota changes or other required
revalidation still use a fresh guard invocation and a new capture directory.
Check the saved Goal/Agent/Turn identity before use; a cache filename does not
prove identity. Nothing here bypasses the owning mutation-time validation.
Capture also preserves decided rejection/diagnostic payloads; inspect `ok` before
acting. A late disk failure can happen after the guard has committed its receipt;
the CLI reports that boundary rather than claiming rollback. An empty capture
directory is not a usable decision. Omit the option to disable capture; remove
unneeded local captures through ordinary file management.

When both capture and `--turn-envelope` are requested, `detail_ref.full_decision`
points to the saved file using a quoted POSIX `cat --` command. A file-capable host
can read `detail_ref.captured_decision.path` directly instead. The typed projector
includes the expected Goal/Agent/Turn and a reference to the existing canonical
source-decision hash (a hash of the JSON value, not the formatted file bytes).
Verify those against the read observation, including `ok`; the link itself does
not verify file integrity or make saved admission current. Missing, malformed,
or mismatched files require recovery, never automatic guard replay. Consumers already referring to `full_decision`, such as compacted capability
context and peer diagnostics, now read this same observation. Scheduler-specific
legacy detail requests are unchanged. Replan obligations and fresh selection/lease/quota checks retain their existing owners.
Without capture, the historical full-decision route remains available. Enabling
capture does not enable TurnEnvelope or change admission/settlement defaults.
This opt-in detail link does not change action-signature coverage.

中文：同时开启短包和完整 decision 保存时，详情入口读取本次保存的文件，不再
为补读上下文重跑 guard。读取前核对身份和源哈希；旧观察不提供新的执行权限。
文件损坏或丢失不能自动重跑，选 Todo、lease 变化等仍需按原契约重新准入。

The default ordinary quota CLI view may replace an exactly identical nested
`heartbeat_recommendation.blocked_priority_fallback` diagnostic with
`blocked_priority_fallback_ref="$.blocked_priority_fallback"`. Read the complete
root field in that same packet; resolving this reference needs no new guard or
detail command. Different mirrors remain inline. Raw decisions, saved captures
and the existing `--include-detail agent-todos` view retain their original shape.
Action dimensions remain equivalent; the raw source hash still identifies the
specific original rendering and must not be substituted across views.
The existing packet owner supplies this compatibility projection, keeping the
CLI from becoming another reader of the legacy guidance field.

中文：普通 quota CLI 热路径将完全相同的 fallback 副本改为本包根字段引用，完整
事实仍在当前包内，不需再次执行 guard。不同副本和既有详细视图保留；原始决定与
capture 不变。动作维度等价不意味着不同渲染可以共用原始 source hash。

### Native Turn Host capture

Executing `turn run-once --execute` now saves the original full decision before
Host invocation and binds the received envelope's detail route to that private
observation. This is the native Turn execution default; ordinary quota capture
remains opt-in. `turn plan`, `managed-step` inspection and `run-once` previews
remain read-only. No new capability or configuration switch is introduced.

The Turn journal owns the native Turn identity. Its capture reference includes
Goal, Agent, Turn instance, Turn key and the existing source-decision hash; it
does not invent a heartbeat admission receipt inside the saved decision. The
TypeScript envelope owner validates the observation and binding. Python only
publishes and reads the private file under the Turn journal lock. Captures live
in the journal directory's `decisions` child, outside the journal file inventory,
and are published atomically with owner-only file permissions without replacing
an existing observation. They can contain private Goal context and must not be
included in public logs or artifacts.

Host retry validates and reuses the original journal's capture even if current
source text changes. Reading detail does not invoke quota or spend a slot.
Current source admission, cancellation, workspace/lease and mutation checks
retain their existing owners; the observation grants no fresh authority.
Missing, corrupt, wrong-identity or source-hash-mismatched captures refuse Host
reinvocation. Restore the original evidence rather than reconstructing it from
a new guard. A capture published before a journal write failure is retained;
a different observation cannot overwrite it on retry.

Compatibility: terminal journal readback and recovery using an already cached
typed Host result retain the original effect receipts and do not need to launch
a Host. An older journal without capture cannot safely reconstruct the omitted
original context for another Host attempt. Preserve that journal, resolve its
pending effects using the existing recovery path, then start a fresh Turn when
permitted. Downgrading code must preserve journals and captures; deleting them
is not rollback. This transport qualification does not establish live model,
packaged App/Lark or overall convergence acceptance.

Repair and replan diagnostics remain in the journal's typed `host_result`,
including its summary and follow-up action. They do not implicitly replace Todo
notes or evidence, even when settlement succeeds. Semantic refusal and cached
result recovery must preserve that task context; explicit Todo lifecycle
operations retain their existing authority and validation.

中文：原生 `turn run-once --execute` 在启动 Host 前保存完整原始决策，实际短包详情
与失败重试均读取该快照，预览不写入。Turn 身份来自原 journal，不伪造 heartbeat
准入记录。旧观察不授予新权限；缺失、损坏或身份／哈希不符时拒绝重新启动 Host。
无快照的旧 journal 保留终态回读和已缓存结果的结算恢复，但不能重新构造当时的完整
上下文后继续调用 Host；先处置原效果，再按当前准入创建新 Turn。文件含私有上下文，
保留在运行时目录，禁止放入公开产物。

For repeated host-owned guards, `--decision-output-root ./private-decisions`
allocates a unique child directory per invocation below an **existing** directory.
It is mutually exclusive with `--decision-output-dir`; both require an explicit
Turn ID. The returned Todo selection command retains the root and requested
`--turn-envelope` projection, so choosing work never overwrites the first read.
Other repair/replan guard commands still require the caller to retain those
transport flags. A saved decision is not permission to skip revalidation.

The heartbeat renderer can capture decisions while retaining its ordinary
dispatcher and quota display:

```bash
mkdir -m 700 ./private-decisions
loopx --format json heartbeat-prompt --thin --runtime-profile generic_cli \
  --goal-id <goal-id> --agent-id <agent-id> --turn-instance-id <turn-id> \
  --decision-output-root ./private-decisions
```

Capture and display are independent. `--decision-output-root` saves complete
decisions; `--decision-handoff-socket` optionally holds their output for a
caller-owned checkpoint. Neither enables TurnEnvelope. This corrects the old
renderer behavior where requesting capture also selected the short dispatcher.
Existing capture callers that intentionally want the short view must now add
`--turn-envelope` to `heartbeat-prompt`; the generated guard and thin regeneration
command retain that explicit choice. Quota's existing explicit flag is unchanged.

With that display opt-in, execute its guard, read the short action/contract/writeback fields, and use
`detail_ref.full_decision` to read the **same invocation** when selection, replan,
an `agent_context.detail_ref` or truncation requires detail. Replan history remains with the
existing owner; this option neither invents another history store nor chooses a
strategy. The renderer rejects full/compact/brief modes, missing Turn identity,
native Goal-owned Turns and invalid roots. Stable bootstrap prompts cannot pin a
Turn. Omit `--turn-envelope` to use the ordinary dispatcher, including when
checkpoint capture remains enabled. Captured files retain the full decision in
both display modes.

Capability planning context stays inline under the existing context owner's
3 KiB bound, including when the envelope exceeds its diagnostic budget. Read it
before planning; it is guidance, not permission. `delivery=projected` also labels
complete context and does not itself require a detail read. The shared DSH/cloud
Host adapter transports this signed context. For historical context with a
`detail_ref`, it also retains the original capture route and source signature;
only that saved observation may satisfy the missing context, never a fresh quota
guard. Missing or invalid evidence holds dependent planning while independent
work retains its authority. Capture validation and later mutation checks remain
with their existing owners; prompt delivery does not prove model adoption.

For admitted replans, the shared DSH/cloud adapter also carries the signed
`replan_action_packet` and the original capture route/signature. Read that saved
decision's `autonomous_replan_obligation.replan_context` before choosing the
semantic outcome, preserving the existing full Goal read. Missing or invalid
original evidence holds dependent replanning; never rerun admission to replace
it. Return the typed candidate rather than executing the packet's writeback
commands: the outer Turn executor owns state, quota and scheduler effects.
This repairs an adapter omission without adding an evidence-read receipt or
changing non-replan requests.

中文：已准入的重规划通过共享 DSH/cloud adapter 传递签名 action packet，以及原捕获
路由和签名。选择语义结果前从原决策读取既有 replan context，保留完整 Goal 必读；
来源缺失或无效时暂停依赖它的重规划，不能重跑准入替代。返回 typed candidate，
不执行 packet 内写回命令；状态、quota 和 scheduler 效果仍归外层 Turn executor。
本修复不增加补读回执，也不改变非重规划请求。

The unchanged public multi-subagent CLI fixture grows by 1,147 JSON characters
and UTF-8 bytes when this context replaces its reference. Its differential
allows 1,280 only for that reference-to-inline transition (133 headroom);
absolute ceilings, line limits, later inline growth and the 8 KiB production
diagnostic remain unchanged. This is an output qualification, not a full-cost SLO.

中文：同一公开 multi-subagent CLI 夹具恢复完整内联后增加 1,147 个 JSON 字符／UTF-8
字节；差分仅对该引用转内联的一次变化给予 1,280（余量 133），绝对上限、行数、后续
内联增长检查及生产 8 KiB 诊断不改；这不是完整成本 SLO 验收。

中文：能力规划上下文由既有 owner 限制在 3 KiB 内，短包超出诊断预算时仍完整内联。
规划前读取它，但它不授予权限。`delivery=projected` 也用于完整上下文，不能仅据该值
要求补读。共享 DSH/cloud Host 传递签名上下文；历史 `detail_ref` 保留原捕获路由与
源签名，只能补读原观察，不能重跑 quota。证据缺失或失效时暂停依赖它的规划，独立
工作保留原权限。捕获及后续写入校验仍归原 owner；输入到达不证明模型实际采用。

中文：捕获和模型输入投影独立。`--decision-output-root` 仅保存完整决策，
`--decision-handoff-socket` 仅保留检查点交接；二者均不隐式开启 TurnEnvelope。
此前捕获目录同时启用短包的行为已修正。需要短包的调用者须对
`heartbeat-prompt` 显式传 `--turn-envelope`；省略该开关时仍使用普通输入，
选择、结算、身份与完整私有捕获不变。旧运行的实际输入与设置不得追溯改写。

This is Python filesystem/command transport under the existing quota/context
owner, not a new capability or Python decision rule. The shared benchmark
heartbeat worker has a separate default-off `turn_envelope` option documented in
[its runtime guide](../../../benchmark/runtime/RUNTIME.md). Native `/goal`, Turn
driver, installed App automations, frontend and Lark are not switched by this
research-runner setting. Real local worker/renderer/selection/settlement tests
qualify transport; model token/IO costs, decision quality and long-run adoption
still require a separately pinned experiment. Capture grants no additional
read/write/lease authority. Keep captures private; disable the option in the
next run and use ordinary file management to remove unneeded records.

Whole-Goal requirements and selected work have different sources. After final
delivery admission, the shared typed interaction owner adds these pre-work reads
after any existing hook reads, without duplicating identical commands. The
Agent channel is the single carrier; CLI actions do not repeat the list. See
[required work context](../required-work-context.md) for the changed exact-read
default and caller migration:

1. `cat -- <registered-state-file>` restores the whole Goal intent, acceptance
   and stop conditions. The pointer is resolved from the registered Goal, not a
   selected Todo or run-history source label.
2. When canonical acceptance is enabled, `goal-acceptance inspect --goal-id …`
   restores its full objective, non-goals, criteria, scope and revision. It does
   not execute validators or expose their commands. A scoped acceptance contract
   does not replace original Goal intent or prove global completion.
3. For selected work, `todo list --goal-id … --todo-id …` restores full current
   task text and status/claim once at `todo.text` (exact reads remove list/role
   aliases and reject `--thin`), including when canonical
   Todos supersede a stale Markdown display. Adaptive work uses its primary ID.

Ordinary product heartbeat consumes current task/acceptance/User sources from
`interaction_contract.agent_channel.work_context`, then any remaining
`agent_channel.required_reads`. The Agent channel is the single content/read
carrier; the CLI channel carries transitions. TurnEnvelope consumes and signs
both as `work_context` and `required_reads`; it never independently synthesizes
another policy. Already delivered content requires no second CLI invocation.
The selected-Todo display body is display-only and may be bounded; the typed
owner validates the exact read against selected identity, lifecycle, claim and
source revision. A supplied full-body snapshot must match before fulfilling the read. If the full body is already
in the selected action, `work_context.selected_todo_ref` reuses that body while
the source retains unique continuation, relation and authority metadata. If the
selected view is bounded, the full canonical detail stays in
`work_context.sources`. TurnEnvelope must preserve those unique fields and may
omit only an actually duplicate body; if there are no other fields to retain,
`work_context.selected_todo_authority` carries the source and revision. All exact
commands remain in `required_reads`.
The mixed Goal document remains an exact progressive full read; quota checks
source availability rather than automatically copying unrelated tasks/history.
No heading heuristic replaces Goal intent. `work_context.complete` does not
settle pending read obligations. Full delivered sources and related User
obligations survive budget overflow. Missing,
ambiguous, failed or changed sources require recovery and a fresh guard before
dependent work; content grants no claim, lease or amendment authority.

This is a default decision/heartbeat behavior change, not an envelope opt-in.
Admitted replans acquire Goal reads, without an old selected-Todo read.
Non-delivery, selection-only, settlement-only and governed-capability lanes
acquire no extra core work reads. Related User obligations remain scoped; full quota summaries stay compact. Shared adapters retain
selected Todo context; text aliases require exact equality, never a case-folded
or truncated-prefix match. Supplied selected text survives envelope serialization
intact. Hosts must honor the reads; signing does not prove execution or model
adherence. Full Goal requirement coverage and global closeout remain open.

The continuation-audit capsule preserves the source's complete ordered
`required_before_closeout`, continuation guidance, current `vision_gap_judge`,
accepted evidence kinds and insufficient-proof conditions. These are decision
obligations, so display truncation must not remove their clauses or list tails.
They enter the existing action signature; editing a step, its order or the judge
changes the signed dimensions. Detailed acceptance-gap records stay in the same
captured full decision. Historical saved envelopes retain their original
signatures; this projection change grants no closure or execution authority.
The 8 KiB warning remains diagnostic when required meaning exceeds it.

Shared DSH/cloud Host input forwards this audit from the signed capsule, with
the original capture route and hash for additional acceptance/coverage evidence.
Hosts read its ordered obligations and current judge before proposing closure;
they return a semantic result while the outer Turn executor owns state, quota
and scheduler effects. The same input carries the complete existing
`execution_profile`, including outcome-floor and degradation rules, to size and
qualify work. Display filtering must not discard those clauses or ordered
requirements before signing. A new material result carrying `execution_profile`
must return `work_evidence` and `validation_evidence` as separate arrays of
1-8 non-empty public-safe strings, at most 240 characters per item and 1,200
characters per field. The Host must preserve its actual
`delivery_batch_scale` and `delivery_outcome` rather than replacing them with a
fixed progress class. The outer Turn executor validates and retains this evidence
in the typed host result; `turn run-once` projects it as `host_evidence`.
Legacy journal replay may retain an older material result without these fields.
Stop results omit both arrays. The profile does not transfer writeback or quota
ownership. Historical profiles keep their original signature, and inputs without
either contract retain their prompt.

中文：验收 capsule 完整保留原来源的有序步骤、继续执行说明、当前结束／继续判断、
认可的证据种类和不能作为证明的条件；展示裁剪不能丢掉条款或列表尾部。新投影使用
既有签名维度，历史 envelope 的签名不重写。详细验收记录仍从同次捕获读取，体积
超过 8 KiB 时保留 warning，不以缩短义务或放大预算替代语义验收。
共享 DSH／云端 Host 输入也从签名 capsule 传递这份 audit，附原捕获路由与哈希，供
补读验收／覆盖证据；提议收尾前须读取有序义务及当前 judge。Host 返回语义结果，
状态、quota 和 scheduler 效果仍由外层 Turn executor 执行。同一输入也传递完整的既有
`execution_profile`，包括成果底线、退化规则和有序要求，供确定工作规模及验收；不能
在签名前按展示字段丢弃。携带 `execution_profile` 的新 material result 必须分别返回
`work_evidence` 与 `validation_evidence` 字符串数组：各 1-8 项、每项不超过 240 字符、
整字段不超过 1,200 字符，且公开安全。Host 必须保留实际
`delivery_batch_scale` 与 `delivery_outcome`，不能改写成固定的 progress 分类。外层
Turn executor 验证 typed result 并保留证据，`turn run-once` 以 `host_evidence`
投影回读；旧 journal replay 可保留缺少这些字段的历史 material result，stop result
不得返回两组证据。Host 不取得写回或扣额权。历史 profile 保留原签名；两种契约都不
存在时保持原 prompt。

No new frontend/Lark operation or configuration is introduced. Regenerate saved
expanded heartbeat prompts to consume the new contract; bootstrap prompts reload
installed rules on their next wake. This source-checkout change does not upgrade
an installed runtime or modify an existing automation.

中文：共享 typed interaction owner 在准入后生成 Goal 原文、已启用的结构化验收、
当前 Todo 全文三层读取。普通产品 heartbeat 消费 agent channel 清单，TurnEnvelope
只传输并签名同一清单；不能仅由短包临时推导义务。Goal 原文恢复完整意图和停止条件，
局部验收不能替代完整目标，任务状态/claim 继续由精确 Todo reader 提供。读取失败、
缺失、歧义或变化须重新 guard；当前内容不是 selection 快照，签名也不证明模型已读。
这是默认 guard/heartbeat 行为变化：准入的 replan 读取 Goal，等待、待选择、仅结算及
受治理 capability 分支不追加读取。完整义务覆盖与全局收口仍未实现。旧的展开式
heartbeat prompt 需重新生成；bootstrap 会在下次唤醒重载已安装规则。本源码改动不会
升级已安装 runtime 或修改现有 automation，也不新增前端/Lark 操作。

The envelope flag selects a projection of the full decision. The original v0
contract left the default `quota should-run` output unchanged; the
[PR-05 migration](protocol-action-packet-decision-v0.md) omits
`protocol_action_packet` from new full decisions, including live, paused and
recovery output, from the first release containing #4794. Historical v0 reads
remain supported for the v0 reader lifetime. The v0 envelope keeps:

- the selected todo, claim, and effective action;
- the bounded action portfolio when the agent must choose among multiple
  admitted actions before delivery;
- the bounded read-only planning horizon when selected work has strategic
  Todo, relation, or goal-acceptance context;
- concrete user actions and gate reasons;
- required reads;
- write scope, approvals, guards, workspace/capability gates, and stop rule;
- delivery, repair, safe-bypass, and blocked-action policy;
- validation/writeback and quota-spend policy;
- the current scheduler action and cadence acknowledgement command.

The envelope also carries a bounded `contract_capsule` for interaction mode,
work-lane and execution obligations, successor/replan duties, automation
liveness, vision/handoff state, and actionable warning references. A canonical
`action_signature` is independently built from the full decision and from the
envelope; matching hashes prove the covered action dimensions agree for that
projection. They do not prove that every possible quota state has test
coverage.

Required reads use the authoritative `interaction_contract.agent_channel` list,
including an explicit empty list. Historical interaction-root and payload lists
remain fallbacks when that carrier is absent. Commands retain their exact bytes;
existing `ordering`, `hook_id` and `capability_id` coordinates survive both the
quota transport and the signed envelope projection. Provider diagnostics remain
outside this whitelist. A matching signature cannot prove that omitted reads
were complete or that a host executed them. Newly projected decisions retain
these corrected facts in the existing required-read dimension; saved signatures
are not rewritten, and no new execution permission is granted.

中文：必读列表优先使用 agent_channel 的权威列表，包括明确的空列表；缺失时
才兼容旧位置。命令原样保留，已有读取顺序、hook 和 capability 身份贯穿 quota
传输与签名短包，私有诊断不进入该列表。修正新投影不重写历史签名；签名相等
不能证明读取完整或宿主已经执行，也不提供新的权限。

Action-signature coverage is versioned independently from the envelope schema.
`turn_envelope_action_dimensions_v0` covers the original action projection;
`turn_envelope_action_dimensions_v1` additionally covers a blocking user
gate's `response_plan`; `turn_envelope_action_dimensions_v2` additionally signs
`action.action_portfolio`; `turn_envelope_action_dimensions_v3` additionally
signs `action.planning_horizon`; v4 additionally signs capability `agent_context`;
`turn_envelope_action_dimensions_v5` additionally preserves the canonical
`writeback.settlement_plan`. Base/head qualification accepts a declared
coverage migration as a review signal. The bounded, JSON-only v2 and v3
migration budgets apply only to their named schema transitions; ordinary
growth limits resume once the new version is the baseline. A digest change
without a supported coverage migration, or a projection above its one-version
budget, still fails closed.

For a decision carrying a settlement plan, the opt-in envelope now transports
that plan intact: effect identity, ordered steps, command conditions, expected
receipts and the host-owned handoff. It also keeps the corresponding next CLI
commands untruncated. This repairs the earlier preview that could report matching
action hashes while omitting the settlement plan. The plan remains owned by the
shared settlement algebra; the envelope neither rebuilds it nor grants authority
to execute an unadmitted step. Packets without a plan keep their previous coverage
and do not acquire one from a historical heartbeat receipt. Stored v0–v4 signatures
are not rewritten. The v5 migration is an explicit semantic review signal and
has **no additional size allowance**; overflow still requires the existing budget
analysis. Default full quota output and settlement rules are unchanged.

Newly generated turn-scoped settlement commands include global `--format json`
before the subcommand. This changes the generated command output default for App
heartbeat, generic CLI, and visible Goal lanes, in both full decisions and
TurnEnvelope. It also applies to `settlement_owed.command` after writeback.
Execute the returned command after filling its declared placeholders; do not
append `--format json` after `quota spend-slot`, where it is not a subcommand
option. Inspect both the exit code and JSON `ok`. Direct CLI invocations retain
their existing output defaults; stored commands and receipts are not rewritten.
This is command rendering over the existing settlement owner: validation,
identity, step conditions, authority and one-spend semantics are unchanged.

中文：新生成的结算命令自带位置正确的全局 JSON 参数，完整包、短包及写回后的
补结算命令保持一致。填充占位项后直接执行，检查退出码和 JSON `ok`；不再需要
手工在子命令后追加格式参数。直接 CLI 的默认格式、历史回执与结算权限均不变。

When an admitted Turn's verified durable writeback is followed by a Todo
dependency wait, its replay now projects only the original `quota_spend` step.
The later wait cannot replace the committed outcome with a blocked writeback.
This default guard recovery grants no independent work and preserves the Todo
wait and successor. The envelope retains the same complete executable command,
including identity arguments after long registry/runtime paths. The spend owner
still validates the original receipts, workspace and checkpoint; a missing spend
receipt is repaired on that identity without another debit. Before verified
writeback, registered causal waits retain the blocked no-spend closeout.
This is shared CLI/heartbeat decision transport; it adds no frontend/Lark
operation, changes no host cadence and does not update installed runtimes.

中文：原 Turn 已有验证通过的持久写回，之后 Todo 才进入依赖等待时，同一 Turn
重入只补原 `quota_spend` 步骤，不再要求用 blocked 覆盖已有结果。等待和后继任务
保持原样，不准入独立工作；短包保留完整命令和原身份。额度 owner 仍校验原回执、
工作区和 checkpoint，补回执不重复扣额。尚无验证写回的因果等待仍走不扣额收口。
此项修复不改变宿主调度、已安装版本或前端/Lark 操作。

The shared CLI plan also explains delivery classification before writeback.
Validated evidence that excludes a route and informs the next decision can be
`outcome_progress` even when the attempted candidate does not improve the target
metric. A failed attempt, an unchanged metric, or a new Todo alone does not prove
progress. Record the validated evidence and its consequence. `outcome_gap` is the
existing blocked-settlement path: it requires `--progress-result-class blocked`,
blocker/evidence IDs, and the existing continuation checks. These authoring hints
neither judge evidence nor relax validation, completion, lease, or spend rules;
an invalid `outcome_gap` plus `advanced` still fails before writeback. The same
plan reaches full CLI output and the opt-in envelope. This changes agent-facing
guidance in both views. Full, compact, brief and thin heartbeat prompts scope
no-refresh/spend closeout to exact monitor settlement; auxiliary polling leaves
the original work settlement due. Admitted work follows its settlement plan;
an unchanged artifact alone does not imply no progress. The shared heartbeat
renderer supplies this wording, while the TypeScript settlement owner retains
classification semantics. This adds no capability, automatic classification, or
frontend/Lark operation. Model error-rate reduction remains unqualified.

中文：短包现在完整保留已有结算计划及执行命令，签名 v5 覆盖结算身份、步骤顺序、
条件和宿主边界；没有计划的输入不会凭空获得结算权限。旧签名保留，默认完整输出
不变，大小预算不放宽。此修复是短上下文实验的前置条件，尚不证明模型收益。

`quota_planning_horizon_v0` remains advisory even when carried by the envelope.
Its `selection_contract` points back to `selected_todo` and `action_portfolio`,
and `horizon_changes_selection=false`. Effect Program transports this
observation; the TypeScript work-item reducer owns its ordering and bounds.
The quota projection keeps the horizon's typed `detail_refs`. TurnEnvelope does
not copy those commands a second time: it emits
`action.planning_horizon.detail_refs_ref="$.detail_ref"`, and the existing
top-level cold path owns the full-decision, Todo, and status reads. This
transport compaction is covered by the same action signature and does not
change horizon completeness or selection authority.
See [`quota_planning_horizon_v0`](quota-planning-horizon-v0.md).

For `quota_action_portfolio_v2`, the envelope carries the recommendation and
bounded, non-exhaustive `suggested_actions`, but neither is a settlement
identity or permission list. When the full interaction contract says
`selection_required=true`, the agent must rerun quota in the same turn with any
currently authoritative, same-agent, capability-ready Todo. The full decision's
`selection_command.command_args_template` is a rendering template, not a
permission list. It and `candidate_discovery_args` share one bound
`route_prefix`; the discovery route exposes the current open agent queue
when the bounded suggestions are insufficient. The requested Todo remains
pending until the second guard re-runs current lane arbitration and eligibility;
only a qualified request upgrades the identity-less receipt. A newly due hard
lane leaves the receipt unbound, and only the resulting receipt-bound envelope
is a delivery contract.

An unbound selection that no longer qualifies is a preflight outcome, not a
settlement-identity conflict. The full quota response preserves the TypeScript
`action_selection_qualification_v0` result and returns
`quota_action_selection_deferred` or `quota_action_selection_rejected`, including
the exact current preemption or eligibility reason. An existing identity-less
receipt appends an identity-less `pending_action_selection` revision when an
otherwise eligible explicit choice is deferred. That revision is not delivery
or settlement authority: it preserves the explicit choice only so a no-argument
same-Turn reentry cannot replace it with the current recommendation. An
ineligible/rejected choice still replays the receipt without mutation; a
first-call rejection reports
`heartbeat_receipt.status=not_committed` and writes no receipt event.

A receipt-bound `refresh-state --next-action` must refine that original Turn's
Goal, Agent and Todo. If waiting or completion makes the current selector choose
another Todo, the existing typed recommendation owner rejects the step before
appending a run; even the other Todo's fresh read basis cannot retarget the
original writeback. Finish the original writeback without `--next-action`, then
select subsequent work through a fresh guard. A matching bound step records
`settlement_alignment=exact`; ordinary unbound steps retain `not_applicable`.
This changes the previous acceptance of a mismatched task-step write, without
clearing dependencies, altering Todo authority or granting another Turn.

中文：绑定回执的 `refresh-state --next-action` 必须属于原 Turn 的 Goal、Agent
和 Todo。原任务等待或完成后，即使当前选择器已选中另一个 Todo，且提供其最新
read basis，同一 TS recommendation owner 仍在追加 run 前拒绝跨任务步骤。
先去掉 `--next-action` 完成原写回，再通过 fresh guard 选择后续工作。匹配步骤保留
`settlement_alignment=exact`，无 Turn 绑定的普通步骤仍为 `not_applicable`。
本项修复此前错误接受的跨任务写入，不清依赖、不改变 Todo 权威或授予新 Turn。

For an eligible explicit choice deferred solely by runnable autonomous replan,
the typed qualifier adds `inline_reentry_allowed=true`. After durably retaining
that choice, the CLI now executes one same-Turn guard reentry and returns its
fresh result directly, including in TurnEnvelope mode. This changes the previous
default of returning a failed selection before asking the caller to reenter.
It grants no delivery or spend authority: the fresh guard and receipt owner
still decide the Todo/replan binding, and may return a newly changed gate. It
does not retry other hard lanes, missing candidates, or a first-call refusal
without a durable choice receipt. The reentry has no explicit selection, so it
cannot recursively retry. Hosts that do not execute this CLI path can continue
to use the existing recovery command.

For other refusals, the agent receives
`recovery_action=reenter_guard_without_selection` and one executable
same-Turn guard in the full decision's `cli_channel.next_cli_actions`; the compact
envelope preserves the recovery in its action and writeback preview. The failed
selection exposes no settlement plan, spend command, or unadmitted replan action
packet. Execute that guard without
a Todo/replan argument before following the resulting binding or portfolio. A
receipt already bound to a different Todo or autonomous replan obligation
remains a hard `heartbeat_receipt_identity_conflict`. On reentry, an identical
projected Todo may bind normally. If a hard autonomous replan owns the current
lane, the replan receives the Turn's settlement identity and the retained Todo
is reported as `deferred_to_fresh_turn`; a different recommended Todo never
inherits the retained choice or its authority.
When a due monitor is visible only as auxiliary context for an advancement lane,
the typed reason is
`auxiliary_monitor_not_selectable_in_advancement_lane`. The agent selects a
current advancement Todo or retries after the monitor becomes the hard lane;
this state is never reported as a receipt write failure.

A selection may also qualify while repository delivery is temporarily blocked
by the peer workspace guard. In that case the response and bound receipt keep
the selected Todo, `effective_action=agent_workspace_repair`, and the typed
worktree recovery instruction. Moving to an independent worktree and rerunning
the guard with the same Turn id resumes the selected Todo; the wrapper must not
rewrite this recoverable state as a settlement-identity conflict.

For admitted local delivery, the workspace hint points to the registered Goal
workspace and defers isolation requirements to the current workspace guard and
repository rules. A peer identity alone does not require moving to another
worktree. This corrects the previous unconditional Git-peer hint in both full
quota output and TurnEnvelope; admission and settlement enforcement are unchanged.

An executed, turn-scoped `quota monitor-poll` is a no-spend closeout only when
its observed Todo exactly matches the Turn's `settlement_todo_id`. That response
includes `turn_continuation.next_turn_required=true` and requires a fresh Turn
before unrelated work. An admitted auxiliary monitor uses its own observed Todo
while retaining the advancement Todo as `settlement_todo_id`; its continuation
keeps `current_turn_settled=false` and `next_turn_required=false` so the original
writeback and spend can finish. A missing exact or typed auxiliary binding never
claims settlement.

Portfolio v2 preserves v1's selection policy, candidate ordering, and
settlement rules, and adds an optional `continuation_hint` to each suggested
action. The default quota producer and Turn controller now require v2. The
compact quota CLI view uses the independently versioned
`quota_cli_action_portfolio_compaction_v1` detail marker and inlines candidate
`text`, `priority`, `action_kind`, and `continuation_hint` alongside the v1
identity fields. TurnEnvelope keeps the same `loopx_turn_envelope_v0` outer
schema and v2 action-signature coverage; only its nested action portfolio
version changes. Hosts that strictly accept v1 must update before consuming
the new default. LoopX does not dual-emit or negotiate a v1 downgrade, so an
unknown nested portfolio version must fail closed. Ignoring an absent
`continuation_hint` remains valid when reading stored v1 evidence, but it does
not make a v1-only live decoder compatible with the v2 producer.

`loopx turn plan` and `loopx turn run-once` have no agent selection phase before
they build the host transaction. When such a Turn sees a v2 portfolio, its
outer controller binds the advisory primary by rerunning the same current
eligibility qualification, retains the portfolio in the envelope for audit,
and marks the selected Todo with
`selected_by=turn_controller_advisory_primary`. This deterministic compatibility
path does not apply to heartbeat/model turns: their first response remains
identity-less and delivery-blocked until the agent explicitly chooses.

The compact envelope does not truncate those executable commands into unusable
strings. It carries non-exhaustive `writeback.suggested_todo_ids` plus
`selection_command_ref`; the full decision remains the authority for exact argv.

Historical full decisions may carry `protocol_action_packet`. For those inputs,
the envelope reconstructs its ordered semantic fields from `action`, `user`,
work-lane, automation, and scheduler contracts, while carrying the explicit
`llm_policy=no_api` invariant. Exact reconstruction retains the source summary
hash and derivation status; a differing compact action retains field-level
`residue`; an opaque summary follows `unverified_retain_summary`. These remain
historical read paths and do not rewrite stored packets or envelopes.

Under PR-05, a new source without a packet produces no
`contract_capsule.protocol_action_packet` witness. The ordered semantic
projection `protocol_action_packet_fields` and historical summary renderer
remain; packet absence does not remove typed obligations or their signature
coverage. Source and envelope signature documents must match for that input.
Compared with a packet-bearing source, the document may lack the capsule's
packet witness and have a different hash. This is not a cross-version hash
compatibility promise; existing signature checks and historical signatures
remain intact. The v1.1.0 reader/host accepts the tested new and stored v0 examples.
The [migration contract](protocol-action-packet-decision-v0.md) defines the
release boundary, v0 reader lifetime, consumer set and rollback steps; unknown
external readers and complete private archives are not implicitly qualified.

Large todo summaries, frontier diagnostics, readiness history, compatibility
fields, and warning collections stay on the referenced full-decision/status
cold paths. The envelope has an **8 KiB compact UTF-8 JSON performance target**,
not an execution-admission limit. `compaction.envelope_utf8_bytes` measures the
final packet, including diagnostics. The historical `source_json_bytes` and
`envelope_json_bytes` fields still count Unicode code points for v0 compatibility;
do not use them as wire-byte measurements.

### Execution boundary retention

The envelope preserves complete `write_scope`, `available_capabilities`,
`requires_parent_approval`, `guards` and `stop_condition` values, including
literal path whitespace and explicit empty lists. Resolved checkpointed write
approval retains every active scope; private approval entries stay on the
authorized full-decision path. Display limits must not remove restrictions or
change path patterns. The 8 KiB warning remains diagnostic.

The shared DSH/cloud candidate prompt carries the remaining signed boundary
under `boundary`. Its existing `write_scope` and `workspace_guard` fields keep
their original locations and are not duplicated in that remainder. These are
instructions for the Host, not a new enforcement mechanism or permission grant;
owning admission, lease and mutation checks still apply. Boundary facts already
belong to the action signature: new projections can change hashes, while saved
signatures and receipts are not rewritten. Signature equality alone cannot
prove source completeness when both sides use the same projection.

中文：envelope 完整保留范围、能力、审批要求、守卫和停止条件，路径空白、空数组及
已批准的全部范围也不裁剪。共享 DSH／云端提示传递其余签名边界，原范围和 workspace
字段继续兼容且只传一份。它们是宿主指令，不替代准入、lease 或写入校验；8 KiB 仍只
警告，不改写历史签名和回执。两边共用投影时，签名相等本身不能证明来源条款完整。

### Capability refusal facts

`boundary.capability_gate` carries the existing quota gate's `required` and
`missing` arrays intact, including an empty `missing` array after admission.
Earlier compact projection used only `required_capabilities` and
`missing_capabilities`, which could report matching projection signatures while
omitting the current refusal facts. Historical names are still carried when
present in the supplied source; they are not synthesized or rewritten.

These facts participate in the existing boundary signature. Newly built
projections over current sources therefore have different hashes; saved
signatures are not rewritten and this is not a cross-version hash-equivalence
promise. Full quota output, admission, optional capability activation, claim,
lease and mutation checks are unchanged. No capability is granted by reading
these arrays. The default full heartbeat packet remains the source decision;
compact/Turn hosts retain its reason for refusal without recomputing readiness.

中文：短包原样保留现有能力门禁的 required/missing（包括合法空数组），历史字段
只在源中存在时保留。新投影签名覆盖这些事实，不改写历史签名；完整包、准入、
可选能力开关和 claim/lease 权限不变。读取缺失项不授予能力。

### Optional memory participation

The compact boundary retains the verified Goal/Agent Reward Memory automation
facts in `boundary.capabilities.reward_memory`: `automatic_recall` and
`automatic_ingest`. Unconfigured, disabled or unavailable bindings omit this
projection. It carries neither private provider configuration nor memory content
and grants no action authority. Existing action-signature coverage includes it.

Codex CLI host requests use these facts together with the fresh runtime binding
readback. Recall guidance is added only with enabled recall and actual context;
the reflection instruction and output-schema field appear only with enabled
ingest. A disabled or stale recall packet cannot activate either operation.
Older/custom hosts may still return a reflection field: while ingest is off the
adapter ignores it before validation and journaling, preserving ordinary work.
When enabled, bounded reflection validation, exact independent attestation and
post-settlement ingest retain their existing rules. Provider failure remains
fail-open. The host adapter is Python IO transport over the existing capability
resolver and TypeScript envelope owner, not a new enablement policy.

The shared DSH/cloud conversion also preserves the runtime recall packet only
when the signed participation and fresh Goal/Agent binding permit it. Its local
`reward_memory_instructions` projection reuses the same Codex CLI clauses;
ingest-only input omits recalled context. Enabled reflections pass unchanged to
the outer validator, which retains the existing bounds and material-result rule.
Native Turn settlement runs the existing ingest observer for verified enabled
ingest on other hosts too; ordinary validation is still insufficient to write
memory. Unconfigured, disabled and stale inputs add no memory guidance, result
field or new observer on those hosts. Codex CLI's existing disabled behavior is
unchanged. File/SQLite CLI and hermetic host/provider tests qualify this IO
boundary, not live memory service, model adherence or installed activation.

中文：共享 DSH/cloud 转换只在签名参与事实及新鲜 Goal/Agent 绑定允许时保留运行时
recall 包；本地指导投影复用 Codex CLI 原条款，仅 ingest 不携带召回内容。已开启的
反思原样交给外层 validator，原大小和 material 规则保留。其他宿主已核验开启 ingest
时也调用既有结算后 observer，普通验证仍不能授权写记忆。关闭、未配置或失效时不
新增指导、反思字段或 observer，Codex CLI 原关闭行为不变。隔离 File/SQLite CLI
及宿主／provider fixture 仅证明 IO 边界，不代表真实服务、模型采用或安装已生效。

This is a bounded shared-facts step. Heartbeat still has its existing full and
compact rendering paths; it is not yet the same installed host journey. The
remaining convergence work is tracked in the existing
[effect-interpreter RFC](../../architecture/rfcs/agent-loop-effect-interpreter-v0.md#heartbeat-and-turn-envelope-convergence).

中文：短包只携带已核验的 recall/ingest 参与事实；关闭、未配置或不可用时不加入。
Codex CLI 据此及新鲜绑定读回装配指令和 schema，关闭 ingest 时忽略旧宿主返回的
反思字段。权限、独立验证与结算规则不变。heartbeat 与 TurnEnvelope 的安装态统一
仍待验证，不能把这一步当作完成。

### Budget warnings and allocation

Oversize valid envelopes keep their normal Turn plan/controller route. They
report `compaction.within_budget=false` and a structured
`warning.code=turn_envelope_budget_exceeded`, with `excess_bytes`, additive
`section_bytes` and `over_target_sections`. JSON carries this through the Turn
plan and host request; Markdown plan/envelope output calls out the warning.
Schema, signatures, identity, permissions, receipt validation and execution
quota are still hard gates. This changes previous behavior for **all Turn hosts**:
packet growth alone no longer produces `contract_error` or stops a Turn loop.

The TypeScript owner keeps review allocations totaling 8,192 bytes. These are
diagnostic targets, not permission to truncate fields or hard per-section caps:

| Section | Target bytes | Included fields |
| --- | ---: | --- |
| action | 800 | action, user, required reads, replan packet, response plan |
| boundary | 2,000 | boundary and execution policy |
| writeback | 600 | validation/settlement commands and policy |
| scheduler | 600 | scheduler action and acknowledgement |
| contracts | 1,800 | contract capsule |
| context | 1,400 | capability context and task orchestration |
| transport | 992 | identity/metadata, signatures, cold-read commands, diagnostics |

Counts include JSON property names, delimiters and UTF-8 text. Their sum equals
the measured final packet; dividing each by `envelope_utf8_bytes` gives its
share. Diagnostic detail is emitted only on overflow, not every normal Turn.
Use the existing `quota should-run --turn-envelope` or `turn plan` JSON output
to inspect the breakdown. Record a public-safe reproduction and compare each
section with the same fixture on the baseline before changing its owner.
First remove repeated presentation or move non-actionable detail to an existing
cold read. Never trim write scope, executable arguments, signatures or required
reads to silence a warning, and do not simply raise the target. The cold-read
commands remain; their redundant human-readable `contains` inventory is retired.

Repository size/parity canaries remain blocking **delivery-time regression
checks**, independent of runtime warning semantics. Representative fixtures must
still fit the target. A warning is a performance investigation signal, not an
automatic Todo, new authority, or permission to spend an extra Turn.

中文：TurnEnvelope 超出 8 KiB 后产生可分析的 warning，不再仅因大小中断合法
Turn。按最终 UTF-8 字节数统计各部分占比，先压缩重复展示内容，再检查对应规则
所属模块；不得截断权限、签名或执行指令，也不应单纯提高预算掩盖增长。
身份、权限、签名和执行配额仍是硬门禁；仓库的体积与语义回归检查仍阻止交付。

Hot-path fields may use explicit references when the inline value would only
repeat another authoritative field. In particular,
`action.selected_todo.text_ref = action.recommended_action` means the selected
todo text is already present as the recommended action. Scheduler reset plans
keep the exact acknowledgement argv inline when it satisfies the executable
argv limits; the failure argv stays behind `failure_cli_args_detail_ref` until
the host update actually fails. Both omitted-argv references now carry a
`detail_ref` into `full_decision.scheduler_hint.<source carrier>` instead of an
unbound `quota should-run --include-detail scheduler` request. Resolve that path
through this envelope's captured `detail_ref.full_decision`, check its
Goal/Agent/Turn and source hash, and retain the original argv verbatim. The
carrier is `app_automation` when supplied, otherwise the historical `codex_app`;
an alias must not redirect the reference to another source. Missing capture
requires recovery, never another admission disguised as a detail read. Actual
host readback and current scheduler mutation validation still apply.

中文：被省略的 ACK 和失败上报参数通过同一 Turn 保存的完整决策读取，不再
提示执行未绑定的新 quota 命令。先核对 Goal/Agent/Turn 和来源哈希，再按
`detail_ref` 读取原始参数；历史 Codex 投影仍使用其原始来源。捕获丢失须恢复，
不得将重新准入当作细节读取；此引用不代替实际 host 读回或调度写入时的验证。

This contract is a projection only. It does not change quota selection, todo
routing, scheduler state, history writes, or state transitions. Promoting it to
the default agent view requires separate parity evidence across delivery,
monitor, user-gate, capability-gate, workspace-guard, and blocked states.

## Multi-State Parity Evidence

`tests/fixtures/turn_envelope_state_matrix.json` is the durable synthetic
promotion fixture. It covers delivery, monitor quiet-skip, user gate,
capability gate, workspace guard, autonomous replan, successor replan,
blocked, and throttled decisions. Every case must preserve the canonical action
signature and remain within the 8 KiB budget. Historical packet-bearing inputs
must retain their reconstruction, residue, or opaque-summary witness; inputs
without a packet must preserve typed obligations without inventing a witness.

The matrix records exact measurements in validation rather than treating a
dated size range as the contract. This keeps the projection available as an
opt-in host view. It is not sufficient to change
the default CLI response: default promotion still requires shadow parity from a
real host integration, no consumer regression with the full decision available
as a cold path, and explicit compatibility acceptance for the default-view
change.
