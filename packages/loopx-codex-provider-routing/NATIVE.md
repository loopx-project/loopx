# Native Codex accounts

The optional `loopx-codex-provider-routing` provider replaces the recommended CPA
path with native slots. It has no new Core capability id. Credentials and lifecycle
stay in this package; Core composes the App transport. Python adapts existing
private file/process owners; `native_policy.ts` owns availability and selection.

## The user task

Previously an exhausted account required finding another account, closing clients,
selecting it by hand and reopening the GPT interface. In
**Settings → Model provider → Native Codex accounts**, LoopX checks allowance and
selects/reopens when the native owner admits it. Enable automatic handoff and
**Allow automatic GPT client quit and restart** for the complete automatic path.
The page reads back accounts,
windows, waiting cause and recovery. Tokens and identity claims stay private.

**An open native Codex client is an occupied owner, even between turns.** With
restart permission, the provider requests a normal AppKit quit of only the
inspected GPT instance, waits for exit and native idle, saves/switches the login,
then reopens that same route. It never addresses a shared bundle id or quits a
different route. With the separate force permission below, a refused or timed-out
normal quit can escalate against that same inspected instance. Otherwise those
failures, or remaining CLI/file owners, leave credentials unchanged and pause
automatic shutdowns until a check. Changed process ownership leaves credentials
unchanged and consumes the bounded retry budget.
Without this additional permission it continues to wait for closed clients.
It does not implement hot login replacement, job replay or
resumption of an interrupted turn. If every
account is unavailable, it waits for the observed reset and checks again.

## Prerequisites and activation

Install in the same Python environment as LoopX, then restart its App/Chat service:

```sh
python -m pip install ./packages/loopx-codex-provider-routing
loopx-codex-accounts status
```

The native adapter requires POSIX, the installed **Personal Apps** GPT slot owner
(`personal-apps` with adjacent `personal_apps.py` and `personal_accounts.py`), its
registered native GPT route, and the official `codex` CLI. Switching is macOS-only
in that owner. This package adapts that installation; it ships no second account
vault or launcher. Other hosts show unavailable. Node 22.18+ or 24+ runs the
TypeScript selector. Enrol accounts through the native owner's official login.

Click **Switch automatically when quota runs out** to authorize the listed slots
as destinations. Installation alone grants no account effect. The App observer
runs with its serving LoopX process and rehydrates the saved opt-in on restart.
Version 0.12.8 uses the registry's declared runtime owner for this policy even when Chat
history uses a separate `--runtime-root`. The settings page and the native CLI
therefore read and update the same policy; no history or credential data is
copied. A registry without `common_runtime_root` retains the explicit Chat
runtime for its policy, so an isolated setup cannot fall back to the user's
global policy. Explicit separate registry/runtime owners remain isolated. For a CLI
watch, pass that registry owner's runtime to `--runtime-root` as usual.

An App observer and CLI `watch` sharing that policy elect one active observer
with a private process lock. Contenders perform no native account observations
or switches; they wait up to five seconds between ownership checks and can take
over when the active owner exits. Stopping a contender releases its thread
without stopping the active owner. The existing transaction lock still serializes
configuration, explicit checks and switches. Turning the policy off affects the
actual observer, while an already dispatched credential transaction retains its
normal completion/recovery contract. Upgrade and restart all existing observers
before relying on this ownership fence; an older process does not acquire it.

Client restart is a separate permission, off by default and absent from older
policies. Enabling it also permits a subsequent explicit manual switch to quit
and reopen the GPT instance. Neither permission is enabled by installation.
**Force quit GPT if normal quit fails** is a further, default-off permission.
When enabled, normal refusal or timeout automatically force-quits only the
observed GPT launch, then continues switching and reopening without another
confirmation. The PID, executable, launch date, selection and quota freshness
must still match. The launch probe must explicitly contain `launch_time`: a
positive finite timestamp retains the launch-date fence; explicit `null` from
Launch Services uses the inspected PID and executable instead. Missing fields,
invalid types and non-finite timestamps cannot authorize force quit. A successful
normal quit never escalates. Unmanaged CLI/file owners remain holds. Force quit can interrupt
work in that instance; it does not authorize replay of submitted jobs.
Version 0.12.4 also records the inspected App's descendants before quitting.
Only those helpers, with unchanged kernel executable and microsecond start
identity, can be retired after the App exits. An older orphan with no observed
parentage, a standalone CLI using the same bundled binary, or a later App launch
remains a hold. Bundle paths alone never authorize termination.
With client restart enabled, version 0.12.6 discovers all loaded current-user
LaunchAgents whose observed Codex process trees consume this home. Service names are
not an allowlist: renamed Chat services, additional providers and plists with
different filenames participate through their existing launchd manager. A
trusted plist, its loaded path, PID, executable and kernel start identity must
match. An unrelated home, ambiguous definition or unverifiable owner cannot
authorize a restart. Discovery combines open home files with the private launch
environment of Codex processes, covering warm app-servers that cached auth and
closed their files. Arguments and environment values are never projected or logged.

Each service's restoration intent is saved privately before unloading. Version
0.12.7 waits up to five seconds for launchd to finish an acknowledged asynchronous
unload; a job that remains loaded retains its recovery intent and fails without
switching accounts. Observed
descendants are retired only while their kernel identity still matches, even if
they have since closed every auth file. The provider waits for their exit and
rechecks native idle under the existing account transaction locks. Up to three
rescans cover services that appear during shutdown. With force permission,
helpers still alive after normal termination can be killed against those same
frozen identities. No arbitrary CLI is relaunched and no submitted job is replayed.

Restoration is checked even after a partial failure. A loaded job alone is not
success: a previously running service must have a fresh live process. A failed
restore is reported as `chat_resume_failed`;
the next native request reconciles that intent without repeating the account
transaction. A changed service definition is preserved and requires local
repair rather than bootstrapping unchecked bytes. Installation does not enable
client restart or start a new Chat service.

`loopx-codex-accounts clients` performs a read-only live inventory with PIDs,
App/service/unmanaged classification and restartability. This inventory also
leaves outstanding service recovery to the existing status/switch owner. It never reads token
contents or changes selection. Unknown file owners (including filesystem/VM
services without a proven restart owner) remain visible holds; installation
does not turn them into termination targets. Discovery is best effort within
the current user's kernel visibility, not a claim to inspect auth cached inside
another user's process or a virtual machine.

Version 0.12.9 also explains unmanaged holders through Darwin's optional kernel
responsibility probe. Reparented XPC processes can retain a different manager
from the current App. A responsible PID/name is diagnostic provenance, never
App ownership or restart permission. For a verified installed Lima hostagent,
the inventory cross-checks its private pidfile and the original Lima/Colima
catalog's running instance; raw arguments, environment and catalog paths stay
private. Missing SPI/permissions, changed launch identities, ambiguous instances
or unavailable manager reads preserve an explicit hold. No guest command runs.
The existing App/LaunchAgent restart path is unchanged; externally managed
workloads require their own idle/recovery qualification. This read-only CLI
diagnostic is a staged prerequisite: the settings page still shows its existing
busy cause, and VM recovery/new-auth adoption remain unqualified.

0.12.9 的只读占用清单会核对脱离 App 进程树的 XPC 责任进程，并对已核验的
Lima/Colima 管理器交叉读取原实例。识别到管理器不等于业务空闲或可以重启；
未知或变化的身份保留等待，不执行 guest 命令，不新增强关目标。设置页的完整
占用诊断与外部工作负载恢复仍待交付，不能把这一步视为真实换号已生效。

An operator-owned service independent of the App can run the same observer:

```sh
loopx-codex-accounts watch
```

Do not run a busy shell loop. A private lock serializes hosts; observations carry
next-due times. This provider is independent of Goal compute quota, heartbeat
settlement and the model data plane. CLI mutations take JSON on stdin with
revisions from `status`:

```sh
loopx-codex-accounts configure <<'JSON'
{"expected_revision":0,"enabled":true,"restart_clients":true,"force_quit_clients":true,"slots":["a","b"]}
JSON
loopx-codex-accounts refresh
```

Explicit `switch` accepts `manual_slot`, `expected_selection` and a unique
`operation_id`; retrying it does not repeat selection. `recover` and `rollback`
require `expected_revision` and `expected_selection`. App buttons use the same
fences. `--runtime-root` selects a custom private runtime; browser requests
cannot supply paths, executables or source files.
`reopen` accepts `expected_selection` and retries only opening a committed
selection. It never repeats the credential switch.
Explicit checking or selection can connect the registered slots with automatic
effects still off. A manual choice may select an exhausted/expired account for
native sign-in or inspection; this never qualifies it for automatic selection.

## Availability and continuation

Usage checks use verified HTTPS without model calls, bound response size and
refuse redirects. Schema drift/network failure means unknown, never exhausted.
If the HTTP transport fails and the host already configures a SOCKS route,
the provider can use its installed curl transport for the same fixed HTTPS
quota endpoint. Proxy and authorization headers travel on stdin, never argv or
logs. It does not change proxy settings or retry HTTP authentication denials.
The active native client owns its login refresh. A distinct inactive account
can refresh through an isolated official App Server; rotated bytes are saved
only to that slot under native locks after identity and selection checks. No
active-home copy is refreshed.

A rejected inactive refresh is cached against that private login-cache revision;
background checks do not repeatedly retry the same rejected credential. A new
native login or an explicit check permits one new attempt. This refusal stays
visible as a sign-in requirement, rather than aging into usable allowance.

The selector prefers App Server `rateLimitsByLimitId.codex` over the legacy
bucket. Review/other product buckets cannot establish Codex availability.
Missing/invalid/future-dated or older-than-two-minute samples stay unknown. A
past reset invalidates an old exhausted sample; it does not grant new allowance.
100% subscription use with positive credits stays available; absent credit facts
remain unknown. Login errors, generic 429, query failure and measured exhaustion
are distinct. The observer never buys credits or uses reset coupons.

Only fresh active-account exhaustion starts automatic selection. Destinations
must be allowlisted and freshly available. Automatic selection prefers the
largest remaining subscription percentage, taking the lower remaining value
across each account's reported short and long windows. For example, 99% short
and 5% long ranks below 80% short and 55% long. Equal values keep the configured
ring order after the active account; a single reported window uses that window.
These observations do not establish absolute remaining tokens or equal plan
capacities. Positive credits retain their existing availability semantics but
do not count as additional subscription percentage. An available current account
is never preempted. Switching retains the five-minute cooldown.
An external native/manual choice wins and
starts a cooldown. Busy clients defer; refused/timed-out clients and remaining
file owners pause further shutdowns. Changed clients and other repeated failures
stop after three attempts until an explicit check/reconnect. Successful quota
reads do not reset that budget, which survives observer restart. A completed
switch, explicit check/reconfiguration or later native choice can reset it.
Quota resets require new observations.
A paused observer still reads native selection metadata: a later native/manual
choice invalidates the old account observations and failure pause, then starts
the usual cooldown without another shutdown attempt.
All-unavailable accounts are checked again at the next reset or within five
minutes; unknown observations retry within one minute. These are bounded usage
reads, not model probes. The managed extension's enable/disable controls its
permissionless qualification protocol; the native host has this separate
explicit opt-in and stop lifecycle.

Version 0.12.3 changes candidate ordering for an already enabled observer; it
does not enable handoff, client restart or force quit. The settings help explains
the percentage comparison on hover, keyboard focus or tap. The six-account
packaged views below use the production HTTP service, private state store and
TypeScript policy with synthetic quota and a simulated busy native transport.
They qualify presentation and selection readback, not a real credential swap:
B has 99% short-window but 5% long-window allowance; C has 80% and 55%, so C is
selected while A stays current until the existing owner admits switching.

![Remaining-first account selection](docs/assets/native-accounts-quota-desktop.png)

[Narrow account settings](docs/assets/native-accounts-quota-narrow.png) ·
[Selection help on demand](docs/assets/native-accounts-quota-help.png)

An intent is durable before the effect. A restart-authorized switch runs in a
separate POSIX session and writes a private completion receipt, so quitting the
calling Codex App does not kill the switch/restart or its result channel. A
restarted observer waits for that receipt instead of dispatching another switch.
An interrupted worker remains visible and recoverable; an unresolved live
worker cannot be overwritten by reconfiguration or recovery. If shutdown succeeds
but switching fails, the unchanged source App is reopened when native preflight
allows it. An incomplete native journal stays closed for recovery.

The native owner checks idle and the
selection revision under its own locks and immediately before writes, saves
outgoing token rotations, commits and reads back. A lost reply reconciles
selection without replaying a switch. Interrupted native journals require
existing recovery, which refuses to overwrite later edits. Model settings and
history stay outside the transaction. Reopening failure is reported after
committed selection, without switching again.
The same checks reject expired automatic quota evidence before shutting down or
writing credentials. A manual/native choice during exit or launch wins.

Private Bot requests that failed with `usageLimitExceeded` can continue through
their original delivery worker after fresh usable allowance is independently
observed. Admission requires the exact original user-only native inventory,
unchanged source and grant, idle canonical queue, bounded backoff and a durable
per-request attempt identity. A tool, answer, correction, unknown inventory or
unavailable allowance prevents continuation. The old failure remains immutable;
the new Turn resumes the same Session, and its idle model client reloads the
current login. This path requires the optional provider's availability policy
in the Bot's Python environment; it never replays the failed Turn or delegates
business work from a repair Agent.

## Disable, upgrade and rollback

Turn the switch off to stop automatic effects. CLI `configure` with
`enabled:false` also works offline. Stop a separate `watch` process through its
service manager. Stop/restart the serving process before uninstalling loaded
code. Uninstall leaves Core and native slots intact.
An already dispatched credential transaction finishes and reopens safely even
when the observer is stopped; disabling never discards that pending intent.

Activation pins the two installed native source files. Updates produce
`adapter_changed`; **Reconnect** reads and pins the installation again. An old
observer never silently adopts new source. Private state contains paths, pins,
policy, content-free observations and an intent, never tokens.

From CPA, first stop its separately owned service and restore a backed-up native
configuration using its existing operator workflow. Keep the selected model;
review removal of active custom provider/catalog/profile overrides. The native
adapter refuses a non-native default and never edits an App bundle or rewrites
a running home for migration. The [native template](templates/codex-app-config.toml)
illustrates the new route; the [legacy template](templates/legacy-cpa-config.toml)
and explicit CPA operator remain for deliberate rollback. No credential or
history conversion occurs.

## Qualification boundary

Tests challenge exhausted/unknown/credit-funded facts, allowlists, manual fences,
duplicates, concurrency, busy deferral, lost replies, stop-before-effect and
recovery with synthetic credentials. Browser evidence must use the packaged
page with the owning backend and a recovery case. Installed native file/process
qualification uses a disposable home and a signed disposable Cocoa App: actual
AppKit normal quit, refusal, native idle/save/switch, source reopen on failure,
restart failure recovery and a detached worker surviving its owned caller exit.
Quota and authentication transport remain synthetic. Read-only live status certifies neither
an actual account swap nor installed adoption or uninterrupted hot switching.

## Packaged example

The packaged settings page below uses synthetic account aliases and allowance,
with the actual native file/transaction owner and a disposable Cocoa App.
Usage/OAuth responses are simulated; a real-account cutover is not qualified by
these images. Desktop and narrow views keep the selected account
visible even when the account list continues below the viewport.

![Packaged desktop account settings](docs/assets/native-accounts-desktop.png)

[Narrow account settings](docs/assets/native-accounts-phone.png)

### Force-quit settings / 自动强关设置

The packaged settings page persists the optional permission and describes the
interruption effect before selection. These screenshots use synthetic A/B
accounts through the shipped HTTP service, native policy and private state
owner; they do not show a live credential transaction. Unknown quota was also
exercised, followed by a fresh successful check without changing the account.

设置页保存可选强关授权，并在选择前说明中断效果。下图通过实际打包网页、HTTP
服务、原生策略和私有状态 owner 验收，账号 A/B 为合成数据，不代表真实凭据切换。
另已验收额度未知及重新检查恢复；未知期间没有换号。

![Desktop force-quit settings / 桌面强关设置](docs/assets/native-accounts-force-desktop.png)

![Narrow settings / 窄屏设置](docs/assets/native-accounts-force-narrow.png)

### Shared policy owner / 共用策略来源

The settings page and CLI use the registry-declared native account owner even
when Chat history has a separate runtime directory. Without a declared owner,
an explicit isolated Chat runtime remains isolated. Version 0.12.8 also holds a
process lock for the observer lifetime: another App or CLI stays passive until
the owner exits. Upgrade and restart every existing observer before relying on
this guarantee; an older observer does not participate in the new lock.

设置页与 CLI 共用 registry 声明的原生账号策略来源，会话历史仍保留在原目录。
未声明公共来源时，显式隔离的 Chat runtime 保持隔离。0.12.8 通过跨进程锁保证同一
策略来源只有一个自动观察器；原观察器退出后，等待者接管。升级时需要重启所有旧
观察器，旧版本不会自动遵守新锁。

The actual packaged page and HTTP service below use synthetic A/B/C accounts.
Turning automatic switching off and back on persisted to the shared owner and
survived reload without changing the separate conversation policy. Unknown
allowance kept account A selected; a fresh successful check recovered. These
images do not qualify a real credential switch.

以下为实际打包网页与 HTTP 服务，账号 A/B/C 为合成数据。关闭、重新开启和刷新后
均读回共用策略，会话目录中的独立旧策略未被改写。额度未知时保留 A，重新检查后
恢复；不代表已完成真实账号切换。

![Shared account policy, desktop / 桌面](docs/assets/native-policy-owner-desktop.png)

![Shared account policy, narrow / 窄屏](docs/assets/native-policy-owner-narrow.png)

![Unknown allowance preserves selection / 额度未知保留当前账号](docs/assets/native-policy-owner-unknown-narrow.png)
