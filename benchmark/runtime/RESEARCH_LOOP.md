# Coordinated research-loop treatment

For a **fresh** EdgeBench `heartbeat-explore` attempt with native public feedback
and a custom Responses-compatible endpoint, `--research-loop` enables one
coordinated treatment. Omission preserves the previous treatment. The runner
rejects adding it to an older checkpoint. Runtime, worker and export receipts
retain the flag; do not compare mixed treatments as identical configurations.
Roll back by omitting the flag on a new attempt. Retained findings containing
the new optional observation require a supporting reader; do not rewrite old
history or claim that an older strict event reader understands the new field.
The treatment selects the existing `evidence_conditioned_v1` replan strategy;
an explicitly conflicting strategy is rejected. This is a combined treatment,
not an isolated estimate of the contribution of any one component.

The entry creates an initial-candidate Todo and a separate deferred research
Todo using existing `todo_done`/successor semantics. Finishing the candidate
releases real optimization work; it cannot by itself close that work. The
original task remains the Goal objective. Research retains the best candidate,
meaningful negative results and reasoned closeout against the original objective.
User Stop, the original budget and genuine blockers remain authoritative. There
is no requirement to fill time or change the native scoring/submission rules.
Preparation records this scope through the ordinary Agent Vision writer as
`surface_only`, without claiming research progress or spending a work Turn.
Existing prepared research work is not reopened or its later Vision overwritten.

The worker reuses the live Host launch owner before **each** provider request,
including requests inside one long native wake. Current wait/Stop/refusal means
no upstream send and interruption of the owned native process; the original
scheduler handles wait/recovery. Checkpoint continuations retain the held Turn.
Unreadable authority fails closed. Already-forwarded requests cannot be
retroactively cancelled, and absent canonical waits are not inferred from prose.
Connection establishment (including a forward proxy's CONNECT and TLS) happens
outside the authority lock. The original owner/process is pinned before that
connection; its binding, liveness, current control and phase deadline are checked
again immediately before sending. A delayed old request cannot borrow a newer
process or Turn, and a slow connection does not prevent a concurrent Stop write.
For guarded requests, headers and body share a one-second send deadline (or
the caller's shorter timeout); response reads retain their original timeout.
An incomplete send releases the authority lock and remains ambiguous. Refusing
an obsolete binding interrupts only its captured process; it cannot mark a
valid replacement binding refused. A current binding's refusal still requires
the existing recovery path.

Private `provider-requests.jsonl` correlates wake, LoopX Turn, native
session/thread/Turn when supplied, client request, gateway request and returned
upstream request identities. A dispatch without a finished record is unknown;
`forwarded=null` preserves ambiguity during a failed send. Missing identifiers
remain null. The journal excludes bodies, credentials and upstream endpoints.
Qualify the pinned client at its request boundary. Admission adds control cost;
include it in subsequent matched comparisons instead of promising token savings.
The adapter retains the declared HTTP(S) forward-proxy route and `NO_PROXY`
exclusions; it does not bypass an isolated task's model egress boundary.

The initial work context requests a local probe before first submission. The
installed `loopx-research-probe` uses the staged runner/Python:

```sh
loopx-research-probe --candidate-file candidate.py --output probe-001.json \
  --input-revision public-case-v1 --planned-method-id scheduler \
  --executed-method-id mask-selection --change-reason 'The control isolated mask selection first.' \
  --evidence-ref probe:local-001 --limit-seconds 30 --diagnostics probe-diagnostics.json \
  -- python3 candidate.py
```

The solver may write `{"search_iterations":2,"stop_reason":"search_cap"}` to
`LOOPX_RESEARCH_DIAGNOSTICS`. The host measures elapsed time, exit/timeout and
the selected candidate-file digest before/after. A changed candidate rejects
attribution. Solver counters/reasons remain reports, missing counters remain
null, and low resource use is a diagnostic rather than a mandatory time floor.
Pins cover only declared files at those boundaries, not transitive imports or
independent writers. Evidence paths must be fresh; raw output stays local.
The probe neither submits nor accesses hidden evaluation.
Malformed optional solver diagnostics leave host elapsed/exit evidence intact;
their counters stay unknown and a bounded diagnostic-error receipt explains why.

The adjacent `.candidate.json` preserves the selected relative paths and file
hashes. `candidate_sha256` hashes that sorted JSON file manifest (sorted keys,
compact separators), not a native archive's container bytes. Compare the same
manifest against the submitted/released snapshot before attaching feedback;
an archive SHA-256 is not interchangeable. The native adapter performs this
comparison automatically for registered probes, including selected files within
an archive containing more than 32 files. A missing match remains unknown.

Attach the emitted object as `execution_observation` in the existing Explore
result writeback for the model's own local findings. Preserve its input revision
and evidence refs. On native EdgeBench, the installed probe also registers its
manifest with the native result producer. Released native feedback is ingested
automatically with `result_ref` and, only when an accepted snapshot matches,
`result_candidate_sha256`. Late feedback belongs to that older candidate.
Automatic binding covers the registered probes available when that native
observation completes. Later probes use ordinary local Explore writeback; a
completed observation is not reopened to ingest revised results or new probes.
Different planned/executed method ids require a change reason. Replan and the
research-history page read the same Explore source; neither certifies method
benefit or Goal completion.

## Native acceptance and recovery

The research adapter instruments the installed native `sforge-submit` at its
actual packaging and HTTP-send boundaries. It preserves the native tar command,
submission limits, evaluation and scoring. An unsupported native script layout
or a pinned product lacking the matching Explore observer capability fails
installation before model entry. If product and runner revisions differ, pin
both compatible versions; the runner overlay does not replace product owners.
The host observes only public token-visible
history/results and the native accepted source archive. It does not initiate an
evaluation or read hidden judge artifacts.

Each submit invocation journals an operation ID and immutable packaged bytes
before its single POST. A successful native response binds the returned
submission ID and round; the host then verifies their accepted archive digest.
The archive SHA-256, selected-file manifest and result digest remain distinct
linked facts. A completed/error native result is usable only after its public
identity, task, status and history agree. Duplicate identities, replaced archives
or ambiguous recovery retain unknown attribution without blocking unrelated
verified submissions. The first submitted candidate is ordered by native
acceptance (`agent-N`), not by when evaluations finish.

If the submission response disappears, rerunning `sforge-submit` first recovers
the unresolved operation from its journal and native history. Once it uniquely
matches the accepted archive, the helper reads the original submission ID even
if the workspace has changed. It never posts that unresolved operation again.
An unresolved acceptance stops that retry with an explicit unknown reason;
independent research may continue. Receiving external feedback does not pretend
that the interrupted native client observed its result or completed its operation.
The supported native provider's two exact capacity-full 503 responses are
pre-admission refusals: they consume neither a round nor a submission. Those
operations retain their snapshots but are marked rejected, allowing a later
explicit submit invocation. Other 5xx, unrecognized 503 responses and lost
responses remain unknown; the response hook never automatically resubmits.
If the retained archive and result identities are verified but the archive
cannot be parsed, its native error remains a generic finding without a bound
probe manifest. That record does not prevent unrelated verified results from
being ingested.

## Explicit feedback dependencies and wakeup

When the current work truly needs the external result, use its canonical Todo ID:

```sh
sforge-submit --wait-todo TODO_ID
# Or attach a dependency after an already accepted submission:
loopx-research-feedback wait --todo-id TODO_ID --submission-id SUBMISSION_ID
```

Pending evaluation by itself does not defer a model Todo. The explicit option
registers exactly that work's dependency as soon as the native accepted receipt
is available. Existing different waits, completed work, claims and original
operation identities are preserved. The finite observation Todo belongs to the
runner's `edgebench-feedback-observer`; model agents cannot claim it as work.
This is real low-cost external observation through the existing Todo owner.
An independent model Todo remains eligible while one evaluation is pending.

The runner polls public native history without invoking a model. Released facts
go through the existing Explore attachment codec, append-only event log and
canonical Todo linking API. This external observer entry does not create a model
Turn, spend a slot, acknowledge replan or claim research progress. Only durable
finding/link readback permits completion of that observation dependency. Partial
write/copy failures retry with the same source identity. Negative scores and
native error results remain evidence; neither automatically marks research blocked.
Consumption runs under a 60-second process-group timeout with bounded cleanup;
durable results can release independent work even if a later item needs retry.

Consumers serialize ingestion with a nonblocking local lock; a busy consumer
leaves retry to the existing runner. The revision signal is derived from the
current canonical completed observation Todos, rather than the caller's possibly
older feedback packet. Each successful release publishes it immediately, and
replay reconstructs it if completion persisted before notification failed.
An old packet cannot replace a newer completion signal, and unchanged completed
observations do not create fresh wakeups.

The opt-in scheduler wrapper
observes that small file during its ordinary sleep and promptly returns to the
same scheduler's quota check. Repeated or invalid signals do not busy-loop, and a
signal alone grants no model admission. When all model work is waiting, the
per-request guard prevents upstream sends. Resumption uses current evidence and
the existing owner of the original operation and budget. The wrapper does not
change Turn IDs, extend phase deadlines or resubmit candidates. Ordinary newly
admitted successor work can still have its own normal work Turn.

## Measurement and checkpoint boundary

The passive `research-cost.json` receipt records runner wall time, observed
provider requests and native evaluation counts, plus captured Codex token
coverage. Token accounting uses the existing complete-session counter owner;
identical/prefix copies of one session are deduplicated and incompatible,
forked, incomplete or missing captures remain unknown. Cached input is included
in input, and token counts do not imply an invoice. Request and session coverage
are separate: a missing journal or unfinished request cannot be interpreted as
zero cost. Known subtotals and unknown coverage are explicit.

Runner elapsed time includes its preparation, planning, control checks, waiting,
probes and checkpoint activity inside that measured interval. These overlapping
durations are not added together. Detailed phase allocation and costs incurred
before runner entry are unknown. Compare candidate quality against complete
observed costs; do not infer benefit from more Turns, Todos or reports.

This change does not qualify checkpoint restoration of the new native journals.
Cold runs remain the supported entry; applying `--research-loop` to an older
checkpoint is rejected. Before restoring or forking a research attempt, capture
the native submission/probe/ingestion journals with the existing checkpoint
writer barrier and verify original submission identity, available evidence,
budget and the first resumed experiment. Decisions or feedback created after
capture must not be indexed as capture-time knowledge; preserve the existing
`research_point_qualified=false` until that qualification is actually performed.

This treatment does not automatically instrument arbitrary solvers or guarantee
a stronger first score. The model may omit a probe or misreport a method; actual
adoption and benefit still require qualification. Stage separation does not
replace the default whole-Goal acceptance owner. Do not extend budget or suppress
legal closeout to manufacture a successful long run.

## 中文交付边界

这一显式处理将初始候选、持续研究、等待准入、执行证据与复盘接在既有
Goal/Todo/Host/Explore 上。候选完成只释放后续研究；本次没有包含默认全 Goal
验收工作线，也没有重新启动历史运行或长测。

原生真正打包后保存操作 ID 和不可变快照；接受提交后读取真实提交 ID、轮次和
原生归档。归档哈希与所选文件清单哈希分别保存并验证关系。结果晚到时始终指向
原候选，不改写新工作区或自动采用、回滚。提交响应丢失时恢复原操作，无法唯一
匹配时保留未知，不重复消耗评测机会。首次候选按实际接受顺序评价。
支持的原生 provider 在受理前返回的两种明确容量 503 会记为拒绝，保留快照并允许
稍后显式提交；其他 5xx、无法识别的 503 与丢失响应仍为未知，不自动重发。
原始归档与结果身份可靠但归档无法解析时，保留未绑定探针的通用错误 finding，
不阻断独立正常结果，也不声称坏归档具有已验证的候选文件清单。

只有显式 `--wait-todo` 或反馈依赖命令才延后指定 Todo。运行器负责真实外部观察，
先写入 Explore 并读回，再完成观察依赖；其自身不占模型 Turn 或预算。
全部可执行工作等待时停止模型请求；有独立工作时保持准入。新反馈信号打断
调度休眠后仍需通过当前准入，不通过换 Turn、重发操作或重置预算逃出等待。
准入锁前后的原进程、绑定和截止时间都校验，慢连接也不会占住 Stop 的写锁。
带准入保护的请求头与请求体共用一秒发送期限（调用者设置更短时沿用更短值），
响应读取仍用原超时；发送不完整会释放锁并保留未知转发状态。旧绑定的拒绝只影响
旧进程，不能污染合法替换进程；当前绑定的真实拒绝仍须走原恢复路径。
反馈消费者非阻塞互斥，繁忙时由原运行器稍后重试。唤醒版本来自当前 canonical
已完成观察 Todo，独立结果完成后立即发布；完成已落盘但通知失败时可重建补发，
旧包不能覆盖新完成信号，重复消费也不会持续制造唤醒。

探针保留实际耗时、退出状态及可选 solver 报告；坏诊断不能抹掉可靠测量。
完整成本先提供已知总时长、去重计数和明确的未知覆盖，阶段分摊不伪造为零。
检查点恢复仍需在准备恢复／分叉时补齐并验收，不能把保存成功当作恢复资格。
有界 CLI、HTTP、真实本地进程验证只证明工程链路；模型是否采用诊断、首次质量
是否不低于 native、能否超越平台期，仍需冻结新版本后的真实实验与合适对照。
