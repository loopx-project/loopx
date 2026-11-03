# FrontierCS-2 single-host compatibility repair v2

This opt-in patch repairs the native-client, HTTP bridge and Docker transport
published in [the frozen v1 source](https://github.com/loopx-project/loopx/tree/f3aee4586d87b678581a22d23b61fba7e49d4672/benchmark/frontiercs2-single-host-v1).
Apply it to a new copy. The original archive and its historical scores retain
their identities. This research helper is not installed into LoopX, registered
as a capability, or run by active product CI.

## Evidence and repaired behavior

The [published pilot](https://huggingface.co/datasets/songwells/loopxbench/tree/7adf7b7e6828895dee4393506af80afecc843d37/public/frontiercs2-batch-local-counting-20261102-v1)
retains 63 train evaluations and four final evaluations. Its acceptance-time
fields are empty, and final workspace captures are empty or partial. Those are
observed evidence gaps. The export does not establish that a bridge restart,
receipt-write failure or blocked event loop occurred during the pilot; the
defects below are demonstrated separately with synthetic fault injection.

| Boundary | v2 behavior |
| --- | --- |
| Repeated final submission | Replays the original remote selection and atomically replaces the local receipt; an existing receipt no longer makes a successful retry fail. |
| Local receipt IO failure | Returns `state=accepted` with `receipt_pending_recovery=true` and exit zero. Repeat `final` with the same tar and original trial capability to repair the receipt. Remote acceptance is authoritative. |
| Acceptance observation | The bridge's original Unix timestamp appears as `accepted_at` in final responses/receipts and `final_at` in the inspection projection. It is preserved on replay and restart, not reconstructed from upload/grader timestamps. |
| Bridge restart | A private v2 recovery record retains the original capability, deadline, payload identities, feedback, consumed opportunities, checkpoint/final selections and idempotent responses. Payload bytes and trial identity are checked before recovery. |
| Durable admission | Admission and its replay response are committed before train queue handoff. Payload bytes are flushed to a private temporary file before atomic publication; an interrupted write cannot poison the immutable target. A failed commit rolls back in-memory admission and consumes no opportunity; a completed orphan upload can be adopted on retry. |
| Interrupted native worker | Recovery reports `controller_recovery_required`, blocks new train admission and automatic queue replay, and preserves measured cost and consumed opportunities. It neither repeats the evaluation nor invents unmeasured cost. |
| Asynchronous Docker transport | Exec and both upload routes await subprocess IO. Timeout/cancellation reaps the Docker client. Error logging no longer prints exec command/stderr fragments. |
| Uploads into writable tmpfs | Streams file bytes or tar through unprivileged `docker exec` in the container's mount namespace. File uploads replace a temporary sibling atomically, allowing repeated read-only artifact uploads while retaining mode and ownership. It checks actual content in the Docker fixture; trusted installation retains its root-filesystem copy fallback. |

The existing HTTP capability, train/final purpose separation, train-only status,
100-call/180,000-second train limits, 512-KiB request limit, final-selection rule
and answering deadline are retained. A checkpoint does not override a later
explicit final choice, and this patch does not select a maximum train score.
Client result schema v1 remains in use with additive local observation fields;
the private recovery record uses version 2. These fields belong only to this
experiment compatibility implementation, not a shared LoopX state vocabulary.

The tmpfs issue is independently reproduced in the synthetic Docker fixture.
[Docker documents this copy limitation](https://docs.docker.com/reference/cli/docker/container/cp/#corner-cases)
and recommends tar through `docker exec` for mounted paths. This also explains
why a successful copy command alone cannot qualify the historical workspace
captures; it does not prove which files existed before the old capture.

## Prepare and verify

From the LoopX repository root, obtain the pinned source commit and materialize
a separate disposable directory:

```sh
git fetch origin f3aee4586d87b678581a22d23b61fba7e49d4672
FRONTIER_V2_ROOT=$(mktemp -d)
git archive f3aee4586d87b678581a22d23b61fba7e49d4672 benchmark/frontiercs2-single-host-v1 |
  tar -x -C "$FRONTIER_V2_ROOT" --strip-components=2
git -C "$FRONTIER_V2_ROOT" apply --check "$PWD/benchmark/frontiercs2/compatibility/v2.patch"
git -C "$FRONTIER_V2_ROOT" apply "$PWD/benchmark/frontiercs2/compatibility/v2.patch"
uv run --extra test python benchmark/frontiercs2/compatibility/verify_v2.py \
  --source "$FRONTIER_V2_ROOT" --docker-image python:3.12-slim
```

Use the exact source identity above; reject a mismatched base instead of forcing
patch application. The original v1 hash checker still applies only to v1.
Review [v2.patch](compatibility/v2.patch) as the implementation delta.

The opt-in [verification script](compatibility/verify_v2.py) uses disposable
owner-only directories, loopback HTTP, real client subprocesses and synthetic
archives. `--docker-image` uses an already-local Python image with `/bin/bash`;
it never pulls an image. It creates and removes only its uniquely named
container, with network disabled and no host mounts. The fixture normalizes
the Python interpreter path and checks concurrent exec plus sealed/streamed
file and directory uploads, including repeated `0444`/`0555` files. Separate
process/filesystem checks interrupt a payload write at the OS file-size limit
and execute the emitted streaming upload program locally; these do not qualify
container namespace or UID behavior. Omitting the image explicitly skips real Docker
coverage. This is transport validation, not campaign sandbox qualification.
No model, task, native evaluator, reference solution or historical candidate
is executed by these checks. Experiment-specific tests remain opt-in here.

## Controller integration and remaining limits

Use a new trial directory for a new experiment. A v1 inspection-only state file
is deliberately rejected as a recovery source. The controller must own a
single-writer process lock and an owner-only trial directory. The new
`trial-recovery.json` contains a live capability and local payload metadata:
keep it private, including in backups; never upload it with public results.
`submission-state.json` is an inspection projection, not recovery authority.
The recovery commit precedes projection publication; an observation-write
failure logs a warning and cannot undo a durably accepted submission. A later
persist refreshes the projection.
Atomic file publication covers process restart; power-loss durability and
multi-process concurrent ownership are not qualified here.

For a recovered trial, construct `Trial` with its original directory, arm and a
new queue, register the recovered `trial.token`, and preserve its original
deadline. Do not call `start()` again. After stopping/reconciling the old scorer,
call `trial.requeue_pending()` once to hand off queued train work. Repeated calls
on that recovered instance do not enqueue it twice. The controller still owns
phase closure, queued-work cancellation and final-evaluation dispatch.

An interrupted running worker requires reconciliation against the original
process and native result. Preserve the consumed call; record actual elapsed
cost and its native outcome before clearing the recovery failure and persisting.
Do not clear it to obtain another opportunity or restart the original budget.
The patch cannot determine whether an external native scorer survived a
controller crash, so it fails closed at this boundary.

The external campaign controller, isolated model execution, mounts/firewall,
workspace capture, native grader and its task-specific prerequisites remain
outside this repair. In particular, the missing historical workspace files
cannot be recovered from submitted tar files, and the new timestamps do not
backfill missing historical acceptance times. Killing a Docker client does not
prove the container command stopped; the controller retains descendant cleanup
and hard-stop ownership. No model/scorer rerun or score benefit is claimed.

To roll back, stop the disposable controller/containers and discard the patched
source copy. Preserve private v2 recovery data for diagnosis; do not feed it to
v1 or reinterpret the original study as a repaired run.

## 中文说明

这是对冻结 v1 源码的独立修复补丁：保留原始实验、分数和归档字节，修复重复 final、
回执失败恢复、提交接受时间缺失、桥接重启恢复及异步 Docker 阻塞。文件先完整写入临时
文件再原子发布，避免中断写入阻塞重试，并允许保留原权限地重复上传只读文件。以上命令在新目录
应用补丁并执行合成 HTTP、客户端和真实 Docker 验证，不调用模型或裁判。

恢复必须保留原 token、deadline、已消耗次数和实测成本；遇到中断的运行中评测，先核对
原进程和原生结果，不能重复评分、补发预算或虚构成本。恢复文件含凭据，只能私有保存。
原始 controller、工作区完整留存和沙箱资格仍未交付；本修复不能补回历史缺失文件和
时间，也不证明 LoopX 的分数或长程效率提升。
