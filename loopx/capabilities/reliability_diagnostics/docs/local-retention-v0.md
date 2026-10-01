# Local diagnostic ledger retention rehearsal (v0)

[English](#english) · [中文](#中文) · [Capability](../README.md) ·
[Owning RFC](../../../../docs/architecture/rfcs/long-running-agent-reliability-diagnostics-governed-delivery-v0.md)

## English

This is an **operator-run, offline rehearsal profile**, using the existing
diagnostic ledger and CLI. It makes export, deletion readback and recovery
reproducible before a pilot. It introduces no automatic expiry, retention
scheduler, new command, managed backup or production qualification. The P0
exit gate still needs C0/C1 and measured observer overhead. A deployment's data
owner must accept its retention period and deletion scope separately.

### Declare the boundary before collecting events

Keep a private operator note with the exact goal/session and run identity,
data owner, permitted storage location, retention deadline (timezone-aware),
deletion scope and recovery requirement. Choose a finite period before the
pilot; this reference does not invent a universal period. Apply it to positive,
null, degraded, quarantined and invalid runs equally. An unresolved integrity
failure remains a failure; expiration must not improve a reported denominator.

The envelope/stats allowlist remains the collection boundary. A byte-preserving
archive can include corrupt bytes or invalid records already in the source;
copying them does **not** certify public safety. Keep the ledger, readbacks,
hash and operator note private. Share only reviewed public-safe aggregates.

| Lifecycle | Required operator evidence |
| --- | --- |
| Collect | Declared identity and finite retention deadline; default-off observer explicitly enabled for that session |
| Freeze | Observer hooks detached, pending flush settled, and no observer/ingest writer remains for this ledger |
| Export | Whole ledger, SHA-256, fixed-time receipt/projection and pinned installed revision; no filtering of negative records |
| Verify | Same bytes/hash and same receipt/projection from an isolated copy; source still matches the export |
| Delete | Scoped authorization, deadline/hold decision and verified export when recovery is required; missing-ledger readback is `invalid` |
| Restore | Authorization to retain the data again; same goal, no active writer, absent destination, exclusive creation and identical readback |
| Expire all copies | Account for source, exports, copy runtimes, readback files and external backups; never claim secure erasure from unlink alone |

The deadline and hold decision are manual obligations in this reference,
not machine-enforced settings. If automatic TTL, per-session pruning, concurrent
rotation, cross-host/BYOC backup or tenancy is required, hold that deployment
until its lifecycle owner supplies and qualifies those behaviors.

### Stop collection without changing worker authority

Unset the three required variables **before the next harness launch**:

```sh
unset LOOPX_DSH_SHADOW_OBSERVER_GOAL_ID
unset LOOPX_DSH_SHADOW_OBSERVER_SESSION_ID
unset LOOPX_DSH_SHADOW_OBSERVER_RUN_IDENTITY_JSON
```

This leaves the observer row on its feature-off path at the next launch; it
does not detach hooks in an already running process. Use the harness owner's
approved lifecycle to dispose/detach that observer and wait for its final flush.
If this cannot be proven without disrupting an active worker, postpone the
offline operation. Do not let a retention operation stop, resume or retry the
worker. Unsetting optional variables alone is insufficient.

### Export, verify, delete and restore

The POSIX recipe below operates on **one frozen, owner-authorized ledger**.
Set `diagnostic_runtime` to its explicit runtime root, `diagnostic_goal` to its
exact goal id and `diagnostic_as_of` to one timezone-aware replay timestamp.
Set `diagnostic_provider_ledger_dir` to the resolved ledger directory recorded
in the frozen provider configuration; the current shell environment is not
evidence of what an already running observer used.
Use the same installed `loopx` revision throughout and record `loopx --version`.
This v0 recipe supports the canonical layout only:
`<runtime-root>/reliability_diagnostics/<goal-file>.ndjson`. The provider's
resolved directory must match `<runtime-root>/reliability_diagnostics`, including
when `LOOPX_DSH_SHADOW_OBSERVER_LEDGER_DIR` was explicitly set. An arbitrary
custom directory's parent does not supply this mapping: the CLI always inserts
`reliability_diagnostics`. The preflight below rejects that mismatch before
CLI readback or export. Do not move, re-ingest or delete an unsupported ledger
to make this recipe pass; retain it privately for an owner-approved recovery
path. Do not infer a ledger from the current project or default runtime.

Filename normalization currently replaces `:` with `_`, and some filesystems
ignore case. Distinct goal ids can therefore share a filename. The ownership
check below refuses mixed, foreign or unparseable records before deletion;
it does not qualify multi-tenant isolation. Reserve a unique filename for the
rehearsal and investigate ambiguous ownership separately.

```sh
set -eu
: "${diagnostic_runtime:?set the frozen ledger runtime root}"
: "${diagnostic_goal:?set the exact goal id}"
: "${diagnostic_as_of:?set a fixed timezone-aware replay timestamp}"
: "${diagnostic_provider_ledger_dir:?set the frozen provider ledger directory}"
python3 - "$diagnostic_runtime" "$diagnostic_provider_ledger_dir" <<'PY'
import sys
from pathlib import Path
expected = Path(sys.argv[1]).expanduser() / "reliability_diagnostics"
provider = Path(sys.argv[2]).expanduser()
if provider.resolve() != expected.resolve():
    raise SystemExit("unsupported provider ledger directory: canonical runtime layout required")
if provider.is_symlink():
    raise SystemExit("symlink ledger directory: hold offline operations")
PY
umask 077
diagnostic_archive=$(mktemp -d "${TMPDIR:-/tmp}/loopx-diagnostics.XXXXXX")
loopx --version > "$diagnostic_archive/version.txt"
loopx --runtime-root "$diagnostic_runtime" --format json reliability-diagnostics \
  status --goal-id "$diagnostic_goal" --with-receipt --as-of "$diagnostic_as_of" \
  > "$diagnostic_archive/before.json"
diagnostic_ref=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["ledger_ref"])' \
  "$diagnostic_archive/before.json")
diagnostic_ledger="$diagnostic_runtime/$diagnostic_ref"
test ! -L "$diagnostic_ledger" || { printf '%s\n' 'symlink ledger: hold offline operations' >&2; exit 1; }
test -f "$diagnostic_ledger" || { printf '%s\n' 'regular ledger required: hold offline operations' >&2; exit 1; }
python3 - "$diagnostic_ledger" "$diagnostic_goal" <<'PY'
import json, sys
from pathlib import Path
rows = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines() if line.strip()]
if not rows or any(not isinstance(row, dict) or row.get("goal_id") != sys.argv[2] for row in rows):
    raise SystemExit("ownership uncertain: hold deletion and investigate privately")
PY
diagnostic_copy_runtime="$diagnostic_archive/readback-runtime"
mkdir -p "$(dirname "$diagnostic_copy_runtime/$diagnostic_ref")"
cp "$diagnostic_ledger" "$diagnostic_copy_runtime/$diagnostic_ref"
shasum -a 256 < "$diagnostic_ledger" > "$diagnostic_archive/ledger.sha256"
cmp "$diagnostic_ledger" "$diagnostic_copy_runtime/$diagnostic_ref"
loopx --runtime-root "$diagnostic_copy_runtime" --format json reliability-diagnostics \
  status --goal-id "$diagnostic_goal" --with-receipt --as-of "$diagnostic_as_of" \
  > "$diagnostic_archive/export.json"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1])) == json.load(open(sys.argv[2])), "export readback changed"' \
  "$diagnostic_archive/before.json" "$diagnostic_archive/export.json"

# Continue only after the owner's deletion/hold decision and retention deadline check.
shasum -a 256 < "$diagnostic_ledger" > "$diagnostic_archive/pre-delete.sha256"
cmp "$diagnostic_archive/ledger.sha256" "$diagnostic_archive/pre-delete.sha256"
rm -- "$diagnostic_ledger"
loopx --runtime-root "$diagnostic_runtime" --format json reliability-diagnostics \
  receipt --goal-id "$diagnostic_goal" > "$diagnostic_archive/after-delete.json"
python3 - "$diagnostic_archive/after-delete.json" <<'PY'
import json, sys
receipt = json.load(open(sys.argv[1]))["receipt"]
assert receipt["status"] == "invalid" and "no_observations" in receipt["reason_codes"]
assert receipt["persisted_event_count"] == 0
PY

# Optional recovery rehearsal: requires permission to retain the data again.
shasum -a 256 < "$diagnostic_copy_runtime/$diagnostic_ref" > "$diagnostic_archive/pre-restore.sha256"
cmp "$diagnostic_archive/ledger.sha256" "$diagnostic_archive/pre-restore.sha256"
python3 - "$diagnostic_copy_runtime/$diagnostic_ref" "$diagnostic_ledger" <<'PY'
import sys
from pathlib import Path
data = Path(sys.argv[1]).read_bytes()
with Path(sys.argv[2]).open("xb") as destination:
    destination.write(data)  # refuse any existing destination, including a restarted writer's file
PY
cmp "$diagnostic_ledger" "$diagnostic_copy_runtime/$diagnostic_ref"
loopx --runtime-root "$diagnostic_runtime" --format json reliability-diagnostics \
  status --goal-id "$diagnostic_goal" --with-receipt --as-of "$diagnostic_as_of" \
  > "$diagnostic_archive/restored.json"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1])) == json.load(open(sys.argv[2])), "restored readback changed"' \
  "$diagnostic_archive/before.json" "$diagnostic_archive/restored.json"
```

For permanent deletion, omit recovery and expire the private archive and every
other retained copy under the owner's policy. A retained export makes this a
source-file deletion rehearsal, not complete data deletion. These commands do
not touch Goal, Todo, quota, gate or worker-session state and do not enable the
observer. Keep it disabled until a separately authorized collection resumes.

Do not restore by appending accepted envelopes, resetting sequence numbers,
merging sessions or re-ingesting selected rows. `ingest` is validation, not a
byte-preserving restore API: it can replace invalid input with a new violation
marker. Copy the entire frozen ledger to an absent destination so stats, gaps,
failure markers and malformed bytes retain their original meaning.

### Reproducible validation and remaining evidence

```sh
# From the source checkout; creates and deletes only its own temporary synthetic state.
uv run --extra test python examples/reliability_diagnostics/ledger-retention-smoke.py

# With a non-editable wheel installed into a disposable environment:
# use that environment's Python; --installed rejects checkout imports.
python examples/reliability_diagnostics/ledger-retention-smoke.py --installed
```

The smoke executes this literal shell block with the selected interpreter's
real CLI against disposable state. It restores degraded and refused-control
input ledgers with identical bytes and receipt/projection, including invalid
missing-ledger readback after deletion. Symlinks, foreign/mixed ownership,
malformed input and normalized filename collisions stop before ledger export;
source/copy tampering and occupied restore destinations are rejected. Synthetic
sibling state stays unchanged. The existing DSH producer's real resolver and
file appender also write both canonical and arbitrary custom layouts: the
canonical ledger round-trips through the CLI, while an unsupported directory
is held before this recipe's operations. This proves offline recovery mechanics;
it measures neither observer CPU/RSS/bytes/latency nor actual harness lifecycle
or live C0/C1 non-interference. Record the revision, commands, failures/skips,
operator stop evidence and policy acceptance in the owning issue before a pilot.

## 中文

这是复用现有 ledger 和 CLI 的**人工执行、离线演练方案**，让 pilot 之前的导出、删除读回与
恢复可复现。它没有新增自动过期、retention scheduler、命令或托管备份，也不构成生产验收。
P0 exit 仍需 C0/C1 与 observer 开销实测；部署的数据 owner 须另行接受保留期限和删除范围。

收集前在私有操作记录里固定 goal/session/run identity、数据 owner、存储位置、带时区的有限
保留截止时间、删除范围与恢复要求。正面、空结果、degraded、quarantined 和 invalid 运行采用
同一规则；到期删除不能美化已报告的分母。这里的截止时间和 hold 决策是人工义务，并非已实现
的机器配置。自动 TTL、按 session 裁剪、并发轮转、跨 host/BYOC 备份与多租户须由其 lifecycle
owner 实现并独立验收，不能从此演练推导。

envelope/stats allowlist 仍是采集边界。完整字节副本可能保留源文件已有的损坏或非法记录，
不能因此宣称 public-safe；ledger、读回、hash 和操作记录均保留在私有存储，只分享审核后的
public-safe 聚合。保留全部失败标记，不筛选“成功”行。

离线步骤和证据与上面的 lifecycle 表及 POSIX recipe 相同：

1. 在下一次 harness 启动前 unset 三个必需变量；这不会卸载已运行进程的 hooks。由 harness
   owner 按已授权生命周期 detach/dispose observer，等待最后 flush，并确认无 observer/ingest
   writer。若会干扰活跃 worker，则延期；retention 不能取得 stop/resume/retry worker 的权限。
2. 明确 `diagnostic_runtime`、准确的 `diagnostic_goal` 和固定带时区 `diagnostic_as_of`，并把
   冻结 provider 配置中记录的真实目录填入 `diagnostic_provider_ledger_dir`，记录同一安装版本。
   本 v0 只支持 canonical 布局：该目录须与 `<runtime-root>/reliability_diagnostics` 对应。
   任意 custom directory 的 parent 无法建立映射，因为 CLI 会固定添加 `reliability_diagnostics`；
   preflight 会在 CLI 读回和导出前拒绝不匹配或 symlink directory。保留原件供另行授权的恢复
   路径使用，不要移动、重新 ingest 或删除文件来绕过此 hold，也不能依赖当前 shell 的 env、
   当前项目或默认路径猜测实际目录。
3. 使用 recipe 的归属检查；目前 `:` 会被映射为 `_`，部分文件系统忽略大小写，不同 goal
   可能共用文件名。混合、外来或不可解析行须暂停删除，另行调查；此方案没有证明租户隔离。
4. 完整导出、记录 SHA-256 和固定时间 receipt/projection，在隔离副本里读回并比较；删除前
   再比较源 hash。只有 owner 已授权、期限/hold 决策已核实才执行单文件删除。
5. 删除后 receipt 必须为 `invalid`、包含 `no_observations` 且 persisted count 为 0。
6. 如需恢复演练，先取得再次保留数据的许可，验证副本 hash，并用 exclusive creation 写入
   不存在的目标，拒绝覆盖重新启动的 writer 文件；完整字节和 receipt/projection 必须一致。
7. 永久删除应跳过恢复，并按 owner policy 处理原件、导出、副本 runtime、读回及外部备份。
   留有导出只能称源文件删除演练，unlink 不能证明安全擦除。整个过程不修改 Goal/Todo/quota/
   gate/worker session，也不启用 observer；继续采集须另行授权。

不要用追加合法 envelope、重置 sequence、合并 session 或筛选行后 ingest 来恢复。`ingest`
不是字节保真恢复 API：它可能用新的 violation marker 替代非法输入。恢复整个冻结文件到
不存在的目标，才能保留 stats、缺口、失败标记及损坏字节的原始意义。

上面的两条验证命令在临时合成状态中执行实际 shell block 和所选解释器的真实 CLI。
`--installed` 要求非 editable 的 wheel 安装并拒绝 checkout import；演练 degraded 和拒绝
控制输入的 invalid ledger，验证导出副本、丢失后的 invalid 证据和恢复前后完全一致。
symlink、foreign/mixed ownership、损坏行和文件名碰撞在导出 ledger 前拒绝；源／副本篡改、
已占用恢复目标也被拒绝，其它合成状态不变。真实 DSH producer 的 resolver 与 file appender
分别写入 canonical 和任意 custom 布局：前者经 CLI 完整读回，后者在 recipe 操作前 hold。它只证明
离线恢复机制，没有测 observer CPU/RSS/bytes/latency，也未验真实 harness 停机或 live C0/C1。
pilot 前须在所属 issue 记录 revision、命令、通过/失败/跳过、停写证据与 policy 接受决定。
