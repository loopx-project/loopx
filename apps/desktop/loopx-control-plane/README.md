# LoopX Desktop

This directory contains the experimental Tauri shell for the LoopX personal
Agent workspace. It reuses the existing React dashboard and LoopX HTTP
services; it does not introduce another Goal, Todo, Gate, or Chat state owner.

This is currently a preview desktop shell. It is not installed by the LoopX
Python package; use `loopx dashboard` for the supported browser/PWA launch
path. Published LoopX releases attach desktop preview artifacts when the
desktop release workflow succeeds:

- macOS: `.dmg` plus a zipped `.app` bundle;
- Windows: `.msi` plus an NSIS `.exe` installer.

On Apple Silicon macOS, the App checks its current official channel on launch,
verifies a newer App's updater signature, installs it and restarts automatically.
An offline or failed check keeps the installed App usable; it does not certify
that the App is current. Main points to the latest complete signed build,
not arbitrary Git HEAD. Builds without the updater need a one-time replacement.
Windows preview retains manual CLI installation; browser/PWA users continue
with `loopx update`.

## Updates And Recovery

Startup automatically uses the newest qualified local runtime it can establish.
It compares package versions; equal versions with source revisions use ancestry
from the fixed official GitHub repository. Installation time and lexical SHA
order never determine freshness. If the bounded check is offline, rate limited
or revisions diverge, an independently installed CLI keeps working without a
version selection screen or downgrade. An App-owned snapshot with the same
release base can follow the current App's bundled snapshot when ancestry is
unknown; this is maintenance of the App's own installation, not proof that one
source revision is newer. A provably newer runtime remains selected. Both local HTTP services must expose the selected
artifact's identity before the workspace opens.

If the bundle is newer, or no selected CLI qualifies, the App prepares its
bundled snapshot in its own data directory through the existing installer.
This updates the CLI used by the App without replacing uv/pip/pipx's command or
editing the user's shell profile. An optional failed upgrade retains a qualified
previous runtime. Core's installation-only doctor qualifies non-editable wheel
fingerprints: a local reuse fence, not publisher or source attestation. Older
CLIs without that readback can use the automatic App-owned fallback.

A launch-time `LOOPX_BIN` remains the developer's explicit pin. Development mode
also accepts a source runtime without promoting an installation. A previously
saved path is a discovery candidate, not a permanent version pin. Corrupt
preferences fall back to discovery. Runtime selection does not grant Goal,
Todo, capability or account authority.

Terminal failures stop the wait counter and expose recovery immediately.
**Repair this version** prepares the App-owned runtime, saves the qualified
promoted executable and reconnects the same window. Restart uses that completed
promotion rather than an older cached release path; separately managed or
explicitly pinned CLIs remain with their owner.
**Forget runtime choice** removes the saved discovery candidate, not a current
`LOOPX_BIN` value. Channels, rollback and copyable diagnostics remain in Recovery
& updates and the workspace's existing update panel. Browser callers supply only
fixed native actions, never commands, paths or download URLs.

The App binary owns windowing, startup, IPC and update/recovery. Its runtime owns
the CLI, HTTP APIs and workspace assets. A CLI update cannot patch the native
shell, and a shell update does not certify Goal acceptance or provider readiness.
The release workflow packages both layers from one Git revision.

The installer and App-owned services use the same bounded tool search,
including standard Homebrew locations on macOS, without loading interactive
shell profiles. Finder launches therefore do not depend on terminal PATH setup.
Installation uses `loopx doctor --deep --installation-only`: package ownership,
representative imports/commands/files and the TypeScript runtime semantic probe
remain required, but existing Goal projects are not traversed. Native runtime
preparation also leaves host skills/slash commands and provider doctors alone;
it cannot depend on a macOS Documents-folder consent prompt. Ordinary
`loopx doctor` retains its full operator/integration diagnostics. Terminal
installation still revalidates enabled extensions by default; the App sets
`LOOPX_INSTALL_REVALIDATE_EXTENSIONS=0` for its bounded bootstrap.
Failed runtime preparation remains supervised, with at most three automatic
install attempts per App process and at least 30 seconds between attempts.
Existing recovery controls remain available after that budget is exhausted,
and externally corrected installations are still detected. Legacy journals
are cleared only after the actual App installation verifies; they do not pin
the runtime to an older source revision.

A listener that accepts TCP but does not answer HTTP is given a 15-second
startup grace period. The supervisor can then replace it only after verifying
its LoopX command, service kind and port, including a second PID readback before
termination. Unknown listeners are retained and reported as startup errors.

Chat begins serving its workspace and readiness endpoints independently of Lark
binding discovery. Slow or permission-blocked project reads remain in one
background initialization worker; discovery failures retry every five seconds.
Closing the server fences late discovery results before any queue resumption
or event consumer starts. Existing enabled bindings retain their routing and
authorization rules.

If startup cannot proceed, the embedded **Recovery & updates** screen stays
available without a working HTTP service. **Repair this version** reinstalls
the bundled runtime and then reconnects the same window automatically; it no
longer requires a second App restart. Reloading the WebView cannot terminate
the native startup supervisor.

Legacy update/rollback journals are resolved against the running App before
startup. An incomplete App installation retains its recovery state; runtime
freshness must prevent a journal from silently downgrading a newer CLI.
Concurrent transactions and another install before restart remain rejected.
macOS keeps a signature-verified previous App for **Restore previous version**;
Goal data is not deleted or migrated backwards, so data-schema compatibility
still governs rollback suitability. Older backups may consume disk space.

The embedded Recovery & updates section includes selectable, copyable diagnostics
with the App version, last failure category, installer exit code when available,
runtime identity availability/match results, and (v2) coarse environment facts:
OS version, architecture, whether the runtime executable resolved, and whether a
`python3` in the installer's bounded search path reported a version. The last
failure survives a subsequent update check within the same App process. Copying
never includes raw installer output, environment variables, local paths, or Goal
content. Installation failures, missing/corrupt bundles, and runtime mismatch
show distinct recovery instructions. A terminal `loopx doctor` checks the
terminal-selected runtime; it does not prove that Desktop selected the same
installation or matching revision.

When the update snapshot stays in a terminal phase, the boot screen itself stops
presenting an endless loading state: the main status line immediately shows the current error, stops its wait
counter and expands Recovery & updates. Unchanged invalid selections are
rechecked at most every 30 seconds; an explicit recovery action wakes the
supervisor immediately. It returns to the loading shape as soon as the
snapshot leaves the terminal phase (for example while an explicit repair runs).

## Known Issues

### Fresh Mac without a usable Python stays on the boot screen

On a new macOS machine with no developer environment, the App's automatic
runtime installation can fail with `runtime_install_exit_2`: the installer
requires a usable Python 3.11+ (`python3` reachable through the bounded search
path that covers standard Homebrew locations), and a bare macOS does not ship
one. The automatic install budget (three attempts) exhausts itself, and before
this fix the failure was only visible inside the collapsed Recovery & updates
panel, so the window read as a permanent loading state.

Self-service:

1. Check the boot screen error code or Recovery & updates → Diagnostics
   (`error_code: runtime_install_exit_2`, `environment.python3_found` /
   `python3_version` confirm the missing interpreter).
2. Install Python 3.11+ — for example `brew install python@3.12` or via
   official python.org installer (note that macOS Command Line Tools provides
   Python 3.9, which does not satisfy the Python 3.11+ requirement).
3. Press **Repair this version** (or reopen the App); the live supervisor
   reconnects the same window without a second App restart.

`loopx doctor` run on another machine (for example the Linux host that also
serves your terminal runtime) checks that host's installation. It cannot
diagnose this Mac App's local bootstrap; only the App's own diagnostics do.

The updater accepts only fixed official HTTPS channels, not browser-provided
commands, paths or download URLs. Its signing private key is confined to the
release secret; the App embeds the public key. PR validation has no signing
secret. Updater signing does not provide Apple notarization. It also does not
change Goal authority, grant capabilities, or stop running agents on behalf of
the user. Services may briefly disconnect during reconciliation.

Published macOS preview artifacts use ad-hoc code signing to verify bundle
integrity without requiring an Apple Developer account. They are not signed
with a Developer ID and are not notarized, so macOS may require the operator
to approve the first launch in System Settings > Privacy & Security.

## Runtime Model

The shell:

1. immediately renders an embedded startup surface instead of a blank WebView;
2. verifies or starts `loopx serve-status` on `127.0.0.1:8766`;
3. verifies or starts `loopx chat` on `127.0.0.1:8767`;
4. loads the versioned LoopX Chat workspace only after its lightweight
   capabilities endpoint is readable, retrying transient service replacement;
5. opens the existing personal workspace in one native window;
6. terminates only the service process groups it started when the window exits.

On macOS, the shell retries a failed workspace navigation until WebKit commits
the workspace document. A committed slow document can finish without repeated
reloads; explicit runtime repair starts a fresh handoff. Other platforms retain
their existing single navigation attempt because their page-start events do
not provide the same commit acknowledgement. Native startup changes require an
App update; updating the CLI alone does not replace the shell.

An unknown process on either LoopX port is a hard startup error. Existing
services are reused only after a successful response exposes both the exact
top-level JSON fingerprint and the same installed release identity as the
selected `loopx` command. Marker-like text in headers or nested values is not
accepted. A service left running by an older installation is reported as stale
and is replaced automatically only after the shell resolves the listener PID
and confirms that its command is the expected `loopx serve-status` or
`loopx chat` invocation on the matching port. If process ownership cannot be
confirmed, startup fails closed without sending a termination signal. Unknown
services remain a hard error. Windows currently keeps this owner-facing error
path instead of terminating an existing process automatically.

Release launchers publish a stable process fingerprint that is independent of
their internal Python entry module. The shell also recognizes the historical
fixed-CLI and lightweight-entrypoint launcher shapes, so upgrading LoopX can
replace an already-running older service without asking the operator to find
and stop it manually.

On macOS, when the standard `com.loopx.status` or `com.loopx.chat` LaunchAgent
is loaded, Desktop keeps launchd as the single service owner. After replacing a
stale listener it requests a launchd wake and waits through the throttle
interval instead of racing a second Desktop-owned process onto the same port.

The WebView is pinned to the loopback Chat origin served by the installed
LoopX release. Dashboard requests to the status and Chat services remain
restricted to loopback CORS and the existing preview/apply authority boundary.

## Coexistence With `loopx dashboard`

The desktop shell and `loopx dashboard` share the same loopback services on
`8766` and `8767`, and both entry points reuse an already-running matching
LoopX service:

- Start `loopx dashboard` first, then open the desktop shell: the shell keeps
  using the running status and Chat services and only opens the native window.
- Start the desktop shell first, then run `loopx dashboard`: the command
  detects the matching LoopX Chat service, prints its URL, and opens the
  browser/PWA route without starting a second server.

Either order works. Closing the desktop window stops only the service process
groups it started; a Chat service started by `loopx dashboard` keeps running
until that command is stopped.

## Prerequisites

- A working Python interpreter for runtime installation; existing managed
  installations preserve their interpreter. Windows requires a separately
  installed LoopX CLI. Set `LOOPX_BIN` only for deliberate runtime overrides.
- Node.js 20.19+ or 22.12+ for dashboard builds.
- Rust stable and the platform-specific Tauri build dependencies.

Linux requires WebKitGTK 4.1 and GTK 3 development packages. See the
[Tauri prerequisites](https://v2.tauri.app/start/prerequisites/).

## Development

```bash
cd apps/desktop/loopx-control-plane
npm install
python3 ../../../scripts/desktop_runtime_bundle.py
npm run dev
```

## Validation

```bash
cd apps/desktop/loopx-control-plane/src-tauri
cargo fmt --check
cargo test
cargo clippy --all-targets -- -D warnings

cd ..
./scripts/dashboard.sh build
npm run build
```

`npm run build` produces the configured platform bundles under
`src-tauri/target/release/bundle/`. The release workflow builds macOS `.dmg`
and `.app.zip` artifacts on macOS, Windows `.msi` and `.exe` artifacts on
Windows, and uploads them to the GitHub Release that triggered the workflow.
It verifies the ad-hoc macOS app signature and disk-image integrity before
upload. A separate `DESKTOP-SHA256SUMS` manifest covers all desktop artifacts
attached by the workflow, and release builds use the Git tag as the desktop
bundle version. A manual rerun for an existing tag is an explicit full desktop
republish: it rebuilds both macOS and Windows assets, preserves the previous
desktop set as a short-lived workflow artifact, validates the complete new
four-file set, replaces all desktop binaries, and uploads the new checksum
manifest last. Binary hashes may therefore change after a manual rerun.

## Disable Or Remove

Close the desktop window to stop service processes owned by the shell. Services
that were already running before the shell opened are left untouched. Removing
the desktop package does not modify LoopX project or runtime state.
