//! App-owned update transaction. No browser-supplied commands, paths or URLs.
use crate::bundled_runtime;
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::{Duration, Instant},
};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Default)]
pub struct Maintenance {
    busy: AtomicBool,
    supervision: tauri::async_runtime::Mutex<()>,
    snapshot: Mutex<Value>,
    pending: Mutex<Option<(String, Update)>>,
    last_failure: Mutex<Value>,
    runtime_retry: Mutex<RuntimeRetry>,
    install_journal_discarded: AtomicBool,
    environment_cache: Mutex<Option<(Instant, Value)>>,
    startup_started: std::sync::OnceLock<Instant>,
    phase_started: Mutex<Option<Instant>>,
}

#[derive(Default)]
struct RuntimeRetry {
    attempts: u8,
    last_attempt: Option<Instant>,
}

impl RuntimeRetry {
    fn admit(&mut self, now: Instant) -> bool {
        if self.attempts >= 3
            || self
                .last_attempt
                .is_some_and(|last| now.duration_since(last) < Duration::from_secs(30))
        {
            return false;
        }
        self.attempts += 1;
        self.last_attempt = Some(now);
        true
    }
}
impl Maintenance {
    fn acquire(&self) -> Result<BusyGuard<'_>, String> {
        self.busy
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "update_busy")?;
        Ok(BusyGuard(&self.busy))
    }
    fn publish(&self, phase: &str, details: Value) -> Value {
        let value = json!({"phase": phase, "details": details});
        // The pairing decision belongs with the other blocked states: the
        // recovery panel keeps it for diagnostics even after the operator
        // resolves it in the same App process.
        if matches!(
            phase,
            "error" | "runtime_required" | "runtime_pairing_required" | "service_error"
        ) {
            *self.last_failure.lock().unwrap() = value.clone();
        }
        let mut snapshot = self.snapshot.lock().unwrap();
        if snapshot["phase"] != phase || snapshot["details"] != value["details"] {
            *self.phase_started.lock().unwrap() = Some(Instant::now());
            if let Some(started) = self.startup_started.get() {
                eprintln!(
                    "LoopX startup phase={phase} elapsed_ms={}",
                    started.elapsed().as_millis()
                );
            }
        }
        *snapshot = value.clone();
        value
    }

    fn startup_timing(&self) -> Value {
        let elapsed = self
            .startup_started
            .get()
            .map(|at| at.elapsed().as_millis());
        let phase_elapsed = self
            .phase_started
            .lock()
            .unwrap()
            .map(|at| at.elapsed().as_millis());
        json!({"elapsed_ms": elapsed, "phase_elapsed_ms": phase_elapsed})
    }

    fn prepare_runtime(
        &self,
        step: RuntimeStep,
        explicit_override: bool,
        now: Instant,
        pairing: Value,
        install: impl FnOnce() -> Result<(), String>,
    ) -> Result<(), String> {
        if step == RuntimeStep::AlreadyPaired {
            *self.runtime_retry.lock().unwrap() = RuntimeRetry::default();
            return Ok(());
        }
        if explicit_override {
            self.publish(
                "runtime_required",
                json!({"code":"runtime_identity_mismatch", "revision_matches":false}),
            );
            return Err("runtime_identity_mismatch".into());
        }
        if step == RuntimeStep::AskOperator {
            // Replacing a different installed runtime is the operator's call:
            // the boot surface offers updating the App or aligning the CLI to
            // this App's snapshot. Fail closed without installing anything.
            self.publish("runtime_pairing_required", pairing);
            return Err("runtime_pairing_required".into());
        }
        if !self.runtime_retry.lock().unwrap().admit(now) {
            // Keep the last actionable install error while the live supervisor
            // observes external correction and permits an explicit repair.
            return Err("runtime_setup_required".into());
        }
        self.publish("installing_runtime", json!({}));
        match install() {
            Ok(()) => {
                *self.runtime_retry.lock().unwrap() = RuntimeRetry::default();
                self.publish("connecting", json!({}));
                Ok(())
            }
            Err(error) => {
                self.publish("error", json!({"code":error}));
                Err(error)
            }
        }
    }

    // Service supervision and maintenance must never replace/start different
    // runtime versions concurrently. A failed connection is not an install.
    fn reconcile_services<T>(
        &self,
        start: impl FnOnce() -> Result<T, String>,
    ) -> Result<Option<T>, String> {
        if self.busy.load(Ordering::Acquire) {
            return Ok(None);
        }
        let Ok(_guard) = self.supervision.try_lock() else {
            return Ok(None);
        };
        if self.busy.load(Ordering::Acquire) {
            return Ok(None);
        }
        let phase = self.snapshot.lock().unwrap()["phase"]
            .as_str()
            .unwrap_or("idle")
            .to_string();
        if phase == "restart_required" {
            return Ok(None);
        }
        match start() {
            Ok(services) => {
                self.publish("ready", json!({}));
                Ok(Some(services))
            }
            Err(error) => {
                let runtime_error = matches!(
                    self.snapshot.lock().unwrap()["phase"].as_str(),
                    Some("error" | "runtime_required" | "runtime_pairing_required")
                );
                if !runtime_error {
                    self.publish("service_error", json!({"code":"service_start_failed"}));
                }
                Err(error)
            }
        }
    }
}
struct BusyGuard<'a>(&'a AtomicBool);
impl Drop for BusyGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

fn allow_action(phase: &str, action: &str) -> Result<(), String> {
    if phase == "restart_required" && action != "restart" {
        return Err("restart_required".into());
    }
    Ok(())
}
fn endpoint(channel: &str) -> Result<tauri::Url, String> {
    Ok(match channel {
        "stable" => "https://github.com/loopx-project/loopx/releases/download/desktop-stable/desktop-updater.json",
        "main" => "https://github.com/loopx-project/loopx/releases/download/desktop-main/desktop-updater.json",
        _ => return Err("invalid_update_channel".into()),
    }.parse().unwrap())
}

fn check_error(error: tauri_plugin_updater::Error) -> &'static str {
    use tauri_plugin_updater::Error;
    match error {
        // The plugin discards non-success HTTP status codes, so this cannot
        // distinguish an unpublished feed (404) from an unavailable server.
        Error::ReleaseNotFound => "update_feed_unavailable",
        Error::Serialization(_) => "update_feed_invalid",
        Error::TargetNotFound(_) | Error::TargetsNotFound(_) => "update_platform_unavailable",
        Error::Reqwest(error) if error.is_timeout() => "update_check_timeout",
        Error::Reqwest(error) if error.is_decode() => "update_feed_invalid",
        Error::Reqwest(_) => "update_network_failed",
        _ => "update_check_failed",
    }
}
// Environment telemetry for the recovery diagnostics: coarse, non-PII facts
// that separate "fresh Mac without a usable Python" from OS-specific defects.
// No paths, environment variables or process output beyond the probed version.
const ENVIRONMENT_TTL: Duration = Duration::from_secs(30);

fn compose_environment(
    os_version: Option<String>,
    arch: &str,
    runtime_executable_found: bool,
    python3: (bool, Option<String>),
) -> Value {
    json!({
        "os_version": os_version,
        "arch": arch,
        "runtime_executable_found": runtime_executable_found,
        "python3_found": python3.0,
        "python3_version": python3.1,
    })
}

fn environment_is_fresh(cached: &Option<(Instant, Value)>, now: Instant) -> bool {
    cached
        .as_ref()
        .is_some_and(|(probed_at, _)| now.duration_since(*probed_at) < ENVIRONMENT_TTL)
}

fn detect_environment() -> Value {
    let os_version = (cfg!(target_os = "macos"))
        .then(|| {
            let mut probe = std::process::Command::new("sw_vers");
            probe.arg("-productVersion");
            crate::services::timed_output(probe)
                .filter(|output| output.status.success())
                .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
                .filter(|version| !version.is_empty())
        })
        .flatten();
    let runtime_executable_found =
        std::path::Path::new(&crate::services::loopx_executable()).is_file();
    compose_environment(
        os_version,
        std::env::consts::ARCH,
        runtime_executable_found,
        crate::services::python3_environment(),
    )
}

#[tauri::command]
pub fn desktop_update_status(app: AppHandle, state: State<'_, Maintenance>) -> Value {
    let snapshot = state.snapshot.lock().unwrap().clone();
    let last_failure = state.last_failure.lock().unwrap().clone();
    // Probing spawns bounded sub-processes; the boot page polls every second,
    // so serve the cached block and refresh at most every ENVIRONMENT_TTL.
    let environment = {
        let now = Instant::now();
        let mut cache = state.environment_cache.lock().unwrap();
        if !environment_is_fresh(&cache, now) {
            *cache = Some((now, detect_environment()));
        }
        cache.as_ref().expect("refreshed above").1.clone()
    };
    json!({"state": snapshot, "startup": state.startup_timing(), "last_failure": last_failure, "app_version": app.package_info().version.to_string(), "runtime": bundled_runtime::identity(&app).ok(), "rollback_available": crate::update_backup::available(&app), "environment": environment})
}
#[tauri::command]
pub async fn desktop_update(
    app: AppHandle,
    action: String,
    channel: String,
) -> Result<Value, String> {
    if cfg!(dev) || !cfg!(target_os = "macos") {
        return Err("platform_update_not_supported".into());
    }
    let url = endpoint(&channel)?;
    if !matches!(
        action.as_str(),
        "check" | "apply" | "repair" | "align_runtime" | "restart" | "rollback"
    ) {
        return Err("invalid_update_action".into());
    }
    let state = app.state::<Maintenance>();
    let _guard = state.acquire()?;
    // An explicit action takes priority over the next retry, while awaiting
    // the current bounded service attempt instead of failing with update_busy.
    let _supervision = state.supervision.lock().await;
    allow_action(
        state.snapshot.lock().unwrap()["phase"]
            .as_str()
            .unwrap_or("idle"),
        &action,
    )?;
    if action == "restart" {
        if state.snapshot.lock().unwrap()["phase"] != "restart_required" {
            return Err("restart_not_ready".into());
        }
        app.restart();
    }
    let outcome = perform(&app, &action, &channel, url).await;
    if let Err(error) = &outcome {
        state.publish_failure(error, &channel);
    }
    outcome
}
impl Maintenance {
    // App-install failures discard the stale continuation journal (see
    // perform); surface that in the failure diagnostics exactly once.
    fn publish_failure(&self, code: &str, channel: &str) -> Value {
        let mut details = json!({"code": code, "channel": channel});
        if code == "app_install_failed" {
            details["journal_discarded"] = self
                .install_journal_discarded
                .swap(false, Ordering::AcqRel)
                .into();
        }
        self.publish("error", details)
    }
}
// Bounded reinstall of this App's bundled snapshot, shared by the recovery
// action and the operator's pairing choice. The version-scoped journal keeps an
// interrupted install resumable and can only ever authorize this App's own
// snapshot.
async fn reinstall_bundled_runtime(app: &AppHandle) -> Result<Value, String> {
    let state = app.state::<Maintenance>();
    state.publish("installing_runtime", json!({}));
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        bundled_runtime::record_pending(
            &handle,
            &handle.package_info().version.to_string(),
            "bundled",
        )?;
        bundled_runtime::install(&handle)?;
        bundled_runtime::resume_pending(&handle).map(|_| ())
    })
    .await
    .map_err(|_| "runtime_install_failed".to_string())??;
    *state.runtime_retry.lock().unwrap() = RuntimeRetry::default();
    Ok(state.publish("connecting", json!({})))
}

async fn perform(
    app: &AppHandle,
    action: &str,
    channel: &str,
    url: tauri::Url,
) -> Result<Value, String> {
    let state = app.state::<Maintenance>();
    if action == "check" {
        state.publish("checking", json!({"channel":channel}));
        *state.pending.lock().unwrap() = None;
        let main_channel = channel == "main";
        let update = app
            .updater_builder()
            .version_comparator(move |current, remote| {
                if main_channel && !current.pre.as_str().starts_with("main.") {
                    remote.version.pre.as_str().starts_with("main.")
                } else {
                    remote.version > current
                }
            })
            .endpoints(vec![url])
            .map_err(|_| "update_unavailable")?
            .timeout(Duration::from_secs(30))
            .build()
            .map_err(|_| "update_unavailable")?
            .check()
            .await
            .map_err(check_error)?;
        let details = json!({"channel":channel,"version":update.as_ref().map(|v| &v.version),"current_version":app.package_info().version.to_string()});
        let phase = if update.is_some() {
            "available"
        } else {
            "up_to_date"
        };
        *state.pending.lock().unwrap() = update.map(|u| (channel.to_string(), u));
        return Ok(state.publish(phase, details));
    }
    if action == "rollback" {
        state.publish("installing_app", json!({}));
        let handle = app.clone();
        tauri::async_runtime::spawn_blocking(move || crate::update_backup::restore(&handle))
            .await
            .map_err(|_| "rollback_failed")??;
        return Ok(state.publish("restart_required", json!({})));
    }
    if action == "repair" || action == "align_runtime" {
        // `repair` reinstalls this App's snapshot for recovery and
        // `align_runtime` is the operator's explicit pairing choice; both mean
        // "install what this App carries", and both must install even when a
        // manifest matches: missing or corrupt runtime files are not an
        // identity mismatch. Only replacing the App binary needs a process
        // restart, so the live supervisor connects this same window.
        return reinstall_bundled_runtime(app).await;
    }
    let update = state
        .pending
        .lock()
        .unwrap()
        .as_ref()
        .filter(|(c, _)| c == channel)
        .map(|(_, u)| u.clone())
        .ok_or("update_check_required")?;
    state.publish("downloading", json!({"version":update.version}));
    let mut received = 0u64;
    let bytes = update
        .download(
            |chunk, total| {
                received += chunk as u64;
                state.publish(
                    "downloading",
                    json!({"received":received,"total":total,"version":update.version}),
                );
            },
            || {},
        )
        .await
        .map_err(|_| "update_download_or_signature_failed")?;
    // Archive signature is now verified. Persist continuation before replacement.
    state.publish("installing_app", json!({"version":update.version}));
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || crate::update_backup::prepare(&handle))
        .await
        .map_err(|_| "backup_failed")??;
    bundled_runtime::record_pending(app, &update.version, channel)?;
    let target = update.version.clone();
    // A JoinError (task panic/cancellation) is an unknown-state failure just
    // like an install error: both must clear the same verification below, so
    // neither returns ahead of the recovery decision.
    let installed: Result<(), String> =
        tauri::async_runtime::spawn_blocking(move || update.install(bytes))
            .await
            .map_err(|_| "app_install_failed".to_string())
            .and_then(|result| result.map_err(|_| "app_install_failed".to_string()));
    if installed.is_err() {
        // The pinned macOS installer renames the old App away before moving
        // the new one in, so a failed install does not by itself prove the
        // previously installed App is still in place. Only a verified
        // previous App (bundle present, runtime still pairing with it) may
        // discard the journal and promise a safe restart; anything else keeps
        // the journal and surfaces the distinct recovery state so the
        // verified backup remains the rollback path.
        return Err(finalize_install_failure(
            &state,
            failed_install_left_previous_app_usable(app),
            || bundled_runtime::discard_journal(app),
        )
        .into());
    }
    *state.pending.lock().unwrap() = None;
    Ok(state.publish("restart_required", json!({"version":target})))
}
// A safe-restart promise after a failed app replacement requires the running
// App's bundle to still be present -- the pinned macOS updater's install_inner
// performs rename(old App -> temporary) before rename(new App -> original), so
// the second rename failing leaves the original location empty -- its actual
// installed target to pass the same signature/integrity verification the
// backup boundary uses (a surviving Info.plist and executable do not prove
// sealed resources are intact), and the installed runtime to still pair with
// this App's bundled snapshot. A verified backup copy can never substitute for
// verifying the current installation.
fn failed_install_left_previous_app_usable(app: &AppHandle) -> bool {
    let Ok(executable) = std::env::current_exe() else {
        return false;
    };
    previous_installation_is_usable(
        &executable,
        bundled_runtime::identity(app).ok().as_ref(),
        crate::services::runtime_identity_for_executable(&crate::services::loopx_executable())
            .as_ref(),
    )
}

// Path-level safe-restart predicate shared by the release failure path and
// tests: the actual installed target (located from the executable, never a
// caller path) must verify layout AND codesign integrity, and the installed
// runtime must still pair with the bundled snapshot. Unverified keeps the
// journal and the `app_install_incomplete` recovery state.
fn previous_installation_is_usable(
    executable: &std::path::Path,
    bundled: Option<&Value>,
    installed: Option<&Value>,
) -> bool {
    crate::update_backup::installed_bundle_verifies(executable)
        && runtime_revisions_pair(bundled, installed)
}

// Recovery classification for a failed app replacement: only a verified
// previous App may discard the journal and carry the safe-restart promise;
// an unverified failure keeps the journal under the distinct incomplete code
// so the recovery panel keeps offering the verified backup rollback.
fn install_failure_recovery(previous_app_usable: bool) -> (&'static str, bool) {
    if previous_app_usable {
        ("app_install_failed", true)
    } else {
        ("app_install_incomplete", false)
    }
}

// The final install-failure state must fold in the journal effect: a usable
// previous App earns the safe-restart `app_install_failed` state only when the
// stale continuation journal is absent after the effect (removed or already
// absent). A failed discard keeps the journal, so the journal-preserving
// recovery state applies instead.
fn install_failure_state(
    previous_app_usable: bool,
    journal_discarded: Result<bool, String>,
) -> &'static str {
    let (code, may_discard_journal) = install_failure_recovery(previous_app_usable);
    if !may_discard_journal {
        return code;
    }
    match journal_discarded {
        Ok(_) => code,
        Err(_) => "app_install_incomplete",
    }
}

// Own the complete install-failure decision at one testable boundary. The
// public diagnostic reports whether this call removed a file; safe restart is
// a separate invariant and also accepts an already-absent journal.
fn finalize_install_failure(
    state: &Maintenance,
    previous_app_usable: bool,
    discard_journal: impl FnOnce() -> Result<bool, String>,
) -> &'static str {
    let (code, may_discard_journal) = install_failure_recovery(previous_app_usable);
    if !may_discard_journal {
        return code;
    }
    let journal_result = discard_journal();
    let journal_removed = journal_result.as_ref().is_ok_and(|removed| *removed);
    state
        .install_journal_discarded
        .store(journal_removed, Ordering::Release);
    install_failure_state(previous_app_usable, journal_result)
}

// True when the installed runtime's source_revision equals the bundled
// snapshot's. Any missing identity counts as unpaired: fail closed.
fn runtime_revisions_pair(bundled: Option<&Value>, installed: Option<&Value>) -> bool {
    match (bundled, installed) {
        (Some(bundled), Some(installed)) => {
            bundled["source_revision"] == installed["source_revision"]
        }
        _ => false,
    }
}

// What this start may do about the runtime, decided before the bounded install
// budget and the explicit `LOOPX_BIN` override are applied.
#[derive(Debug, PartialEq, Eq)]
enum RuntimeStep {
    /// The installed runtime already is this App's snapshot.
    AlreadyPaired,
    /// Installing the bundled snapshot replaces nothing the operator chose: no
    /// runtime is installed yet, or an approved journal already carries their
    /// consent for this App's snapshot.
    InstallBundled,
    /// A different runtime is installed and nobody has chosen yet. This is a
    /// decision, not a failure: the CLI may be the newer layer, so the App
    /// asks instead of replacing it.
    AskOperator,
}

fn classify_runtime_step(
    bundled: &Value,
    installed: Option<&Value>,
    approved_journal: bool,
) -> RuntimeStep {
    if runtime_revisions_pair(Some(bundled), installed) {
        RuntimeStep::AlreadyPaired
    } else if installed.is_none() || approved_journal {
        RuntimeStep::InstallBundled
    } else {
        RuntimeStep::AskOperator
    }
}

// Bounded, non-PII evidence for the operator choice: the two revisions, never
// a path, command or environment value.
fn pairing_details(bundled: &Value, installed: Option<&Value>, app_version: &str) -> Value {
    json!({
        "code": "runtime_pairing_required",
        "app_version": app_version,
        "installed_revision": installed.and_then(|value| value["source_revision"].as_str()),
        "bundled_revision": bundled["source_revision"],
        "installed_identity_available": installed.is_some(),
        "revision_matches": false,
    })
}

// Shared App/runtime pairing gate for both release startup entrances: the
// journal-absent path and the start that just discarded a stale journal may
// connect only when the installed runtime pairs with the bundled snapshot.
// A runtime state that already published its own phase must not be relabelled
// by the supervisor's generic error publication: the boot surface renders the
// repair guidance and the operator decision by their own rules.
fn runtime_state_publishes_own_phase(error: &str) -> bool {
    matches!(
        error,
        "runtime_setup_required" | "runtime_pairing_required"
    )
}

fn require_paired_runtime(state: &Maintenance, app: &AppHandle) -> Result<(), String> {
    let bundled = bundled_runtime::identity(app)?;
    let installed =
        crate::services::runtime_identity_for_executable(&crate::services::loopx_executable());
    // A journal that reached this gate was just discarded, so no approval
    // applies to the runtime that is still on disk.
    match classify_runtime_step(&bundled, installed.as_ref(), false) {
        RuntimeStep::AlreadyPaired => Ok(()),
        RuntimeStep::AskOperator => {
            state.publish(
                "runtime_pairing_required",
                pairing_details(
                    &bundled,
                    installed.as_ref(),
                    &app.package_info().version.to_string(),
                ),
            );
            Err("runtime_pairing_required".into())
        }
        RuntimeStep::InstallBundled => {
            state.publish(
                "runtime_required",
                json!({
                    "code":"runtime_setup_required",
                    "installed_identity_available": false,
                    "revision_matches": false
                }),
            );
            Err("runtime_setup_required".into())
        }
    }
}

// Startup decision after the journal has been resolved. The pairing gate is
// injected so headless tests drive the exact release startup branches: a
// discarded stale journal may connect only through the same gate the
// no-journal entrance enforces, and a failed gate leaves services stopped.
fn startup_after_resume(
    state: &Maintenance,
    resolved: Result<bundled_runtime::Resume, String>,
    pairing_gate: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    match resolved {
        Ok(bundled_runtime::Resume::Applied) | Ok(bundled_runtime::Resume::Absent) => {
            state.publish("connecting", json!({}));
            Ok(())
        }
        Ok(bundled_runtime::Resume::StaleDiscarded) => {
            pairing_gate()?;
            state.publish("connecting", json!({}));
            Ok(())
        }
        Err(error) => {
            state.publish("error", json!({"code":error}));
            Err(error)
        }
    }
}
fn resume_runtime(app: &AppHandle) -> Result<(), String> {
    // Development intentionally pairs a live frontend with a developer-selected
    // runtime; it must neither replace itself nor force release installation.
    if cfg!(dev) || !cfg!(target_os = "macos") {
        return Ok(());
    }
    let state = app.state::<Maintenance>();
    let _guard = state.acquire()?;
    let bundled = bundled_runtime::identity(app)?;
    let installed =
        crate::services::runtime_identity_for_executable(&crate::services::loopx_executable());
    let pairing = pairing_details(
        &bundled,
        installed.as_ref(),
        &app.package_info().version.to_string(),
    );
    let explicit_override = std::env::var("LOOPX_BIN").is_ok_and(|v| !v.trim().is_empty());
    // Validate an existing approved target before any automatic installation.
    // A journal for another App must never authorize this App's bundle.
    let journal = bundled_runtime::journal(app)?;
    // A journal naming *this* App version is the operator's standing consent to
    // install the snapshot the App carries -- it is how an App update they
    // approved finishes. Only a journal for another App is discarded; that path
    // must then pass the same gate as a start with no journal at all.
    let mut approved_journal = false;
    if journal.exists() {
        let pending: Value = serde_json::from_slice(
            &std::fs::read(&journal).map_err(|_| "update_state_unavailable")?,
        )
        .map_err(|_| "update_state_invalid")?;
        if pending["version"] != app.package_info().version.to_string() {
            state.publish("installing_runtime", json!({}));
            let resolved = bundled_runtime::resume_pending(app);
            return startup_after_resume(&state, resolved, || require_paired_runtime(&state, app));
        }
        approved_journal = true;
    }
    let step = classify_runtime_step(&bundled, installed.as_ref(), approved_journal);
    state.prepare_runtime(
        step,
        explicit_override,
        Instant::now(),
        pairing,
        || {
            if !journal.exists() {
                bundled_runtime::record_pending(
                    app,
                    &app.package_info().version.to_string(),
                    "bundled",
                )?;
            }
            bundled_runtime::resume_pending(app).map(|_| ())
        },
    )?;
    if journal.exists() {
        // Idempotent completion after a crash between promotion and journal
        // removal: do not reinstall an already matching runtime.
        bundled_runtime::resume_pending(app).map(|_| ())?;
    }
    Ok(())
}
pub fn start_services(app: &AppHandle) -> Result<Option<crate::services::ServiceSet>, String> {
    app.state::<Maintenance>()
        .startup_started
        .get_or_init(Instant::now);
    app.state::<Maintenance>().reconcile_services(|| {
        if let Err(error) = resume_runtime(app) {
            // Runtime states publish the phase that explains them before they
            // return: a missing runtime is the repair guidance, and a different
            // installed runtime is the operator decision. Relabelling either as
            // a generic error would replace the surface that offers the next
            // step with a failure notice.
            if !runtime_state_publishes_own_phase(&error) {
                app.state::<Maintenance>()
                    .publish("error", json!({"code":error}));
            }
            return Err(error);
        }
        crate::services::ServiceSet::start(|kind| {
            app.state::<Maintenance>()
                .publish("connecting", json!({"service":kind.label()}));
        })
        .map_err(|e| e.to_string())
    })
}

pub fn reconnect_requested(app: &AppHandle) -> bool {
    app.state::<Maintenance>().snapshot.lock().unwrap()["phase"] == "connecting"
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn startup_clock_survives_status_reads_and_repeated_phase_publication() {
        let state = Maintenance::default();
        assert!(state.startup_timing()["elapsed_ms"].is_null());
        state
            .startup_started
            .set(Instant::now() - Duration::from_secs(35))
            .unwrap();
        state.publish("installing_runtime", json!({}));
        let first = *state.phase_started.lock().unwrap();
        state.publish("installing_runtime", json!({}));
        assert_eq!(*state.phase_started.lock().unwrap(), first);
        assert!(state.startup_timing()["elapsed_ms"].as_u64().unwrap() >= 35000);
        state.publish("connecting", json!({"service":"chat"}));
        assert!(state.phase_started.lock().unwrap().unwrap() >= first.unwrap());
        assert!(state.startup_timing()["elapsed_ms"].as_u64().unwrap() >= 35000);
    }
    #[test]
    fn runtime_failure_can_recover_without_restarting_the_supervisor() {
        let state = Maintenance::default();
        let now = Instant::now();
        let attempt = |relation, now, install: fn() -> Result<(), String>| {
            state.reconcile_services(|| {
                state.prepare_runtime(relation, false, now, json!({}), install)?;
                Ok(())
            })
        };
        assert!(attempt(
            RuntimeStep::InstallBundled,
            now,
            || Err("runtime_install_exit_1".into())
        )
        .is_err());
        assert!(
            state.acquire().is_ok(),
            "failed automatic install releases maintenance"
        );
        assert!(
            attempt(RuntimeStep::InstallBundled, now + Duration::from_secs(2), || panic!(
                "backoff must not reinstall"
            ))
            .is_err()
        );
        assert_eq!(
            state.snapshot.lock().unwrap()["details"]["code"],
            "runtime_install_exit_1"
        );
        assert_eq!(
            attempt(
                RuntimeStep::InstallBundled,
                now + Duration::from_secs(31),
                || Ok(())
            )
            .unwrap(),
            Some(())
        );
        assert_eq!(state.snapshot.lock().unwrap()["phase"], "ready");
        assert_eq!(
            attempt(
                RuntimeStep::AlreadyPaired,
                now + Duration::from_secs(32),
                || panic!("matching runtime must not reinstall")
            )
            .unwrap(),
            Some(())
        );
        assert_eq!(state.snapshot.lock().unwrap()["phase"], "ready");
    }

    #[test]
    fn automatic_install_is_bounded_but_external_repair_is_still_observed() {
        let state = Maintenance::default();
        let now = Instant::now();
        for seconds in [0, 31, 62] {
            assert!(state
                .prepare_runtime(
                    RuntimeStep::InstallBundled,
                    false,
                    now + Duration::from_secs(seconds),
                    json!({}),
                    || Err("runtime_install_exit_1".into())
                )
                .is_err());
        }
        assert!(state
            .prepare_runtime(
                RuntimeStep::InstallBundled,
                false,
                now + Duration::from_secs(1000),
                json!({}),
                || panic!("retry budget exhausted")
            )
            .is_err());
        assert!(state
            .prepare_runtime(
                RuntimeStep::AlreadyPaired,
                false,
                now + Duration::from_secs(1001),
                json!({}),
                || panic!("external correction needs no install")
            )
            .is_ok());
    }

    #[test]
    fn explicit_runtime_override_is_never_replaced_automatically() {
        let state = Maintenance::default();
        assert_eq!(
            state
                .prepare_runtime(
                    RuntimeStep::InstallBundled,
                    true,
                    Instant::now(),
                    json!({}),
                    || panic!("explicit selection must be respected")
                )
                .unwrap_err(),
            "runtime_identity_mismatch"
        );
        assert!(state
            .prepare_runtime(
                RuntimeStep::AlreadyPaired,
                true,
                Instant::now(),
                json!({}),
                || panic!("already matches")
            )
            .is_ok());
    }

    #[test]
    fn diagnostics_retain_failure_after_successful_update_check() {
        let state = Maintenance::default();
        let failure = state.publish("error", json!({"code":"runtime_install_exit_23"}));
        state.publish("checking", json!({}));
        state.publish("up_to_date", json!({}));
        assert_eq!(*state.last_failure.lock().unwrap(), failure);
        let next = state.publish("runtime_required", json!({"code":"runtime_setup_required"}));
        assert_eq!(*state.last_failure.lock().unwrap(), next);
    }
    #[test]
    fn check_failures_preserve_actionable_categories_without_diagnostics() {
        use tauri_plugin_updater::Error;
        assert_eq!(
            check_error(Error::ReleaseNotFound),
            "update_feed_unavailable"
        );
        assert_eq!(
            check_error(Error::TargetNotFound("private-target".into())),
            "update_platform_unavailable"
        );
        assert_eq!(
            check_error(Error::Network("private-diagnostic".into())),
            "update_check_failed"
        );
        let invalid = serde_json::from_str::<Value>("invalid").unwrap_err();
        assert_eq!(
            check_error(Error::Serialization(invalid)),
            "update_feed_invalid"
        );
    }
    #[test]
    fn transaction_is_singleflight_and_released_on_unwind() {
        let state = Maintenance::default();
        let guard = state.acquire().unwrap();
        assert!(state.acquire().is_err());
        drop(guard);
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _guard = state.acquire().unwrap();
            panic!("simulated task failure");
        }));
        assert!(state.acquire().is_ok());
    }
    #[test]
    fn replaced_app_requires_restart_before_another_transaction() {
        for action in ["check", "apply", "repair", "align_runtime", "rollback"] {
            assert!(allow_action("restart_required", action).is_err());
        }
        assert!(allow_action("restart_required", "restart").is_ok());
    }
    #[test]
    fn endpoints_are_closed_and_https() {
        assert_eq!(endpoint("main").unwrap().scheme(), "https");
        assert_eq!(endpoint("stable").unwrap().host_str(), Some("github.com"));
        assert!(endpoint("https://example.com").is_err());
        assert!(endpoint("main;whoami").is_err());
    }
    #[test]
    fn status_survives_webview_reload() {
        let state = Maintenance::default();
        state.publish("downloading", json!({"received":12,"total":24}));
        assert_eq!(state.snapshot.lock().unwrap()["details"]["received"], 12);
    }

    #[test]
    fn environment_telemetry_is_coarse_and_non_identifying() {
        let environment = compose_environment(
            Some("26.5".to_string()),
            "aarch64",
            false,
            (true, Some("3.9.6".to_string())),
        );
        assert_eq!(environment["os_version"], "26.5");
        assert_eq!(environment["arch"], "aarch64");
        assert_eq!(environment["runtime_executable_found"], false);
        assert_eq!(environment["python3_found"], true);
        assert_eq!(environment["python3_version"], "3.9.6");
        // The fresh-Mac signature: no Homebrew/CLT python3 answers a version
        // probe, and the runtime executable has never been installed.
        let fresh = compose_environment(None, "aarch64", false, (false, None));
        assert_eq!(fresh["os_version"], Value::Null);
        assert_eq!(fresh["python3_found"], false);
        assert_eq!(fresh["python3_version"], Value::Null);
        // The block carries exactly the five diagnostic keys — no paths,
        // environment variables or free-form output can join it.
        let keys: Vec<&str> = environment
            .as_object()
            .expect("environment object")
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(
            keys,
            [
                "arch",
                "os_version",
                "python3_found",
                "python3_version",
                "runtime_executable_found"
            ]
        );
    }

    #[test]
    fn environment_cache_serves_within_ttl_and_refreshes_after_it() {
        let now = Instant::now();
        let cached = Some((now, json!({"os_version":"26.5"})));
        assert!(environment_is_fresh(&cached, now + Duration::from_secs(29)));
        assert!(!environment_is_fresh(
            &cached,
            now + Duration::from_secs(30)
        ));
        assert!(!environment_is_fresh(&None, now));
    }

    #[test]
    fn service_failure_releases_recovery_and_later_success_is_ready() {
        let state = Maintenance::default();
        state.publish("connecting", json!({}));
        assert!(state
            .reconcile_services::<()>(|| Err("occupied port".into()))
            .is_err());
        assert_eq!(state.snapshot.lock().unwrap()["phase"], "service_error");
        assert!(
            state.acquire().is_ok(),
            "recovery transaction must be available"
        );
        assert_eq!(state.reconcile_services(|| Ok(())).unwrap(), Some(()));
        assert_eq!(state.snapshot.lock().unwrap()["phase"], "ready");
    }

    #[test]
    fn service_retry_cannot_race_maintenance_or_restart() {
        let state = Maintenance::default();
        let guard = state.acquire().unwrap();
        assert_eq!(
            state
                .reconcile_services::<()>(|| panic!("must not start"))
                .unwrap(),
            None
        );
        drop(guard);
        state.publish("restart_required", json!({}));
        assert_eq!(
            state
                .reconcile_services::<()>(|| panic!("must not start"))
                .unwrap(),
            None
        );
    }

    #[test]
    fn install_failures_report_journal_discard_exactly_once() {
        let state = Maintenance::default();
        state
            .install_journal_discarded
            .store(true, Ordering::Release);
        let failure = state.publish_failure("app_install_failed", "stable");
        assert_eq!(failure["details"]["journal_discarded"], true);
        // The diagnostics flag is consumed with the failure it describes.
        let repeat = state.publish_failure("app_install_failed", "stable");
        assert_eq!(repeat["details"]["journal_discarded"], false);
    }

    #[test]
    fn unrelated_failures_do_not_report_journal_discard() {
        let state = Maintenance::default();
        state
            .install_journal_discarded
            .store(true, Ordering::Release);
        let failure = state.publish_failure("update_network_failed", "stable");
        assert!(failure["details"].get("journal_discarded").is_none());
        // Unrelated failures leave the flag for the install failure that owns it.
        let install = state.publish_failure("app_install_failed", "stable");
        assert_eq!(install["details"]["journal_discarded"], true);
    }

    #[test]
    fn pairing_requires_both_identities_to_agree() {
        let bundled = |revision: &str| json!({"source_revision": revision});
        assert!(runtime_revisions_pair(
            Some(&bundled("a")),
            Some(&bundled("a"))
        ));
        assert!(!runtime_revisions_pair(
            Some(&bundled("a")),
            Some(&bundled("b"))
        ));
        // A missing installed runtime identity (or a missing bundle identity)
        // is never paired: fail closed.
        assert!(!runtime_revisions_pair(Some(&bundled("a")), None));
        assert!(!runtime_revisions_pair(None, Some(&bundled("a"))));
    }

    #[test]
    fn only_replacing_nothing_is_installed_without_the_operator() {
        let bundled = json!({"source_revision": "b".repeat(40)});
        let installed = |revision: &str| json!({"source_revision": revision});
        assert_eq!(
            classify_runtime_step(&bundled, Some(&installed(&"b".repeat(40))), false),
            RuntimeStep::AlreadyPaired
        );
        assert_eq!(
            classify_runtime_step(&bundled, Some(&installed(&"a".repeat(40))), false),
            RuntimeStep::AskOperator
        );
        // No readable runtime identity at all: nothing is replaced, so the
        // App may install its own snapshot (fresh machine bootstrap).
        assert_eq!(
            classify_runtime_step(&bundled, None, false),
            RuntimeStep::InstallBundled
        );
        // An approved journal for this App version is standing consent: the
        // update the operator started must finish instead of asking again.
        assert_eq!(
            classify_runtime_step(&bundled, Some(&installed(&"a".repeat(40))), true),
            RuntimeStep::InstallBundled
        );
        assert_eq!(
            classify_runtime_step(&bundled, Some(&installed(&"b".repeat(40))), true),
            RuntimeStep::AlreadyPaired
        );
    }

    #[test]
    fn different_installed_runtime_asks_instead_of_replacing_it() {
        let state = Maintenance::default();
        let now = Instant::now();
        let pairing = json!({
            "code": "runtime_pairing_required",
            "installed_revision": "a".repeat(40),
            "bundled_revision": "b".repeat(40),
        });
        // Reinstalling here would silently move the host CLI backwards, so the
        // install closure must never run -- not even after the retry window,
        // which stays untouched because this is a decision, not a failure.
        for seconds in [0, 31, 62, 1000] {
            assert_eq!(
                state
                    .prepare_runtime(
                        RuntimeStep::AskOperator,
                        false,
                        now + Duration::from_secs(seconds),
                        pairing.clone(),
                        || panic!("a different installed runtime must never be replaced"),
                    )
                    .unwrap_err(),
                "runtime_pairing_required"
            );
        }
        let snapshot = state.snapshot.lock().unwrap().clone();
        assert_eq!(snapshot["phase"], "runtime_pairing_required");
        assert_eq!(snapshot["details"], pairing);
        // Diagnostics keep the decision reachable for the recovery panel.
        assert_eq!(state.last_failure.lock().unwrap()["phase"], "runtime_pairing_required");
    }

    #[test]
    fn pairing_evidence_carries_only_the_two_revisions() {
        let bundled = json!({"source_revision": "b".repeat(40), "sha256": "PRIVATE"});
        let installed = json!({"source_revision": "a".repeat(40)});
        let details = pairing_details(&bundled, Some(&installed), "1.0.5");
        assert_eq!(details["app_version"], "1.0.5");
        assert_eq!(details["installed_revision"], "a".repeat(40));
        assert_eq!(details["bundled_revision"], "b".repeat(40));
        assert_eq!(details["revision_matches"], false);
        assert_eq!(details["installed_identity_available"], true);
        assert!(!details.to_string().contains("PRIVATE"));
        let mut keys: Vec<&str> = details
            .as_object()
            .expect("pairing details object")
            .keys()
            .map(String::as_str)
            .collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            [
                "app_version",
                "bundled_revision",
                "code",
                "installed_identity_available",
                "installed_revision",
                "revision_matches"
            ]
        );
        // An unreadable installed identity is reported as absent, never as a
        // fabricated revision.
        let absent = pairing_details(&bundled, None, "1.0.5");
        assert_eq!(absent["installed_revision"], Value::Null);
        assert_eq!(absent["installed_identity_available"], false);
    }

    #[test]
    fn pairing_decision_survives_the_service_failure_classifier() {
        // The gate's Err reaches reconcile_services as a runtime state, not as
        // a service-start failure: the choice must not be relabelled.
        let state = Maintenance::default();
        assert!(state
            .reconcile_services(|| {
                state.prepare_runtime(
                    RuntimeStep::AskOperator,
                    false,
                    Instant::now(),
                    json!({"code":"runtime_pairing_required"}),
                    || panic!("must not install"),
                )
            })
            .is_err());
        assert_eq!(
            state.snapshot.lock().unwrap()["phase"],
            "runtime_pairing_required"
        );
    }

    #[test]
    fn supervisor_keeps_the_phase_that_explains_a_runtime_state() {
        // Both runtime states publish their own phase before resume_runtime
        // returns. A generic error publication here would replace the repair
        // guidance or the operator decision with a failure notice -- the
        // decision would stop rendering its two choices entirely.
        for owned in ["runtime_setup_required", "runtime_pairing_required"] {
            assert!(runtime_state_publishes_own_phase(owned), "{owned}");
        }
        for relabelled in [
            "runtime_identity_mismatch",
            "runtime_install_exit_1",
            "runtime_install_timeout",
            "update_state_invalid",
            "service_start_failed",
        ] {
            assert!(!runtime_state_publishes_own_phase(relabelled), "{relabelled}");
        }
    }

    #[test]
    fn stale_journal_start_connects_only_through_the_pairing_gate() {
        // Stale journal + paired App/runtime: the start may connect.
        let state = Maintenance::default();
        assert!(
            startup_after_resume(&state, Ok(bundled_runtime::Resume::StaleDiscarded), || Ok(
                ()
            ))
            .is_ok()
        );
        assert_eq!(state.snapshot.lock().unwrap()["phase"], "connecting");

        // Stale journal + mismatched or missing runtime identity: no
        // connecting; the gate's runtime_required state stands (an Err from
        // resume keeps the service startup thread on the boot-failure path,
        // so no service starts).
        let state = Maintenance::default();
        assert_eq!(
            startup_after_resume(&state, Ok(bundled_runtime::Resume::StaleDiscarded), || {
                Err("runtime_setup_required".into())
            }),
            Err("runtime_setup_required".into())
        );
        assert_ne!(state.snapshot.lock().unwrap()["phase"], json!("connecting"));
    }

    #[test]
    fn applied_journals_connect_and_resume_errors_surface_without_connecting() {
        for resolved in [
            Ok(bundled_runtime::Resume::Applied),
            Ok(bundled_runtime::Resume::Absent),
        ] {
            let state = Maintenance::default();
            assert_eq!(
                startup_after_resume(&state, resolved, || panic!("gate must not rerun")),
                Ok(())
            );
            assert_eq!(state.snapshot.lock().unwrap()["phase"], "connecting");
        }
        let state = Maintenance::default();
        assert_eq!(
            startup_after_resume(&state, Err("update_state_invalid".into()), || Ok(())),
            Err("update_state_invalid".into())
        );
        assert_eq!(state.snapshot.lock().unwrap()["phase"], json!("error"));
        assert_eq!(
            state.snapshot.lock().unwrap()["details"]["code"],
            json!("update_state_invalid")
        );
    }

    #[test]
    fn only_verified_previous_apps_keep_the_safe_restart_promise() {
        // Verified App bundle + pairing runtime: safe-restart class, journal
        // may be discarded.
        assert_eq!(install_failure_recovery(true), ("app_install_failed", true));
        // Unknown state (second rename failed, original location emptied, or
        // identity unavailable): keep the journal under the recovery code.
        assert_eq!(
            install_failure_recovery(false),
            ("app_install_incomplete", false)
        );
    }

    #[test]
    fn partially_removed_app_keeps_the_journal_as_incomplete() {
        // Review round 3 counterexample, through the same boundary the
        // safe-restart predicate uses: the pinned macOS updater's failed
        // replacement leaves Info.plist (and the runtime identity) behind
        // while the executable is gone. The layout verification
        // failed_install_left_previous_app_usable reuses must reject this
        // bundle, so the failure classifies as app_install_incomplete and
        // the journal is retained for the verified-backup rollback.
        let dir = tempfile::tempdir().unwrap();
        let contents = dir.path().join("Partial.app/Contents");
        std::fs::create_dir_all(contents.join("MacOS")).unwrap();
        std::fs::write(contents.join("Info.plist"), "plist").unwrap();
        let executable = contents.join("MacOS/loopx-control-plane");
        std::fs::write(&executable, "binary").unwrap();
        assert!(crate::update_backup::app_bundle_at(&executable).is_ok());
        // The partial removal: executable deleted, Info.plist survives.
        std::fs::remove_file(&executable).unwrap();
        // The predicate's bundle term fails exactly as it would for the
        // running App's deleted binary, and the recovery classification
        // keeps the journal instead of promising a safe restart.
        let usable = crate::update_backup::app_bundle_at(&executable).is_ok();
        assert!(!usable);
        assert_eq!(
            install_failure_recovery(usable),
            ("app_install_incomplete", false)
        );
    }

    #[test]
    #[cfg(target_os = "macos")]
    fn safe_restart_promise_requires_a_signature_verified_paired_installation() {
        // Review round 4: drive the production safe-restart predicate
        // (previous_installation_is_usable — the same function the failed
        // install path calls) over a real ad-hoc signed synthetic
        // installation. Layout-only evidence accepted a bundle whose sealed
        // resource was deleted; the shared codesign gate must reject it, and
        // only a signature-intact, runtime-paired installation may discard
        // the journal and carry the "可直接重启" (safe restart) promise.
        use crate::update_backup::signed_app_test_support as support;

        let dir = tempfile::tempdir().unwrap();
        let bundled = |revision: &str| json!({"source_revision": revision});

        // Complete signed installation + pairing runtime: safe restart, the
        // journal may be discarded.
        let intact = support::ad_hoc_signed_synthetic_app(dir.path(), "Intact.app");
        let usable = previous_installation_is_usable(
            &support::synthetic_executable(&intact),
            Some(&bundled("a")),
            Some(&bundled("a")),
        );
        assert!(usable);
        assert_eq!(
            install_failure_recovery(usable),
            ("app_install_failed", true)
        );

        // Missing sealed resource (executable + Info.plist intact): layout
        // passes, only the signature gate rejects. The journal is retained:
        // may_discard_journal stays false, so `perform` never reaches
        // discard_journal and the recovery panel keeps the rollback path.
        let sealed_missing = support::ad_hoc_signed_synthetic_app(dir.path(), "SealedMissing.app");
        std::fs::remove_file(sealed_missing.join("Contents/Resources/sealed-resource.txt"))
            .unwrap();
        let damaged = previous_installation_is_usable(
            &support::synthetic_executable(&sealed_missing),
            Some(&bundled("a")),
            Some(&bundled("a")),
        );
        assert!(!damaged);
        assert_eq!(
            install_failure_recovery(damaged),
            ("app_install_incomplete", false)
        );

        // Identity mismatch: the installation's signature is intact but the
        // installed runtime no longer pairs with the bundled snapshot.
        let mismatched = previous_installation_is_usable(
            &support::synthetic_executable(&intact),
            Some(&bundled("a")),
            Some(&bundled("b")),
        );
        assert!(!mismatched);
        assert_eq!(
            install_failure_recovery(mismatched),
            ("app_install_incomplete", false)
        );
    }
}

#[cfg(test)]
mod install_failure_state_tests {
    use super::{finalize_install_failure, Maintenance};

    #[test]
    fn existing_journal_is_removed_before_safe_restart_is_reported() {
        let state = Maintenance::default();
        let dir = tempfile::tempdir().unwrap();
        let journal = dir.path().join("desktop-update.json");
        std::fs::write(&journal, "{\"version\":\"1.2.3\"}").unwrap();

        let code = finalize_install_failure(&state, true, || {
            crate::bundled_runtime::discard_journal_at(&journal)
        });

        assert_eq!(code, "app_install_failed");
        assert!(!journal.exists(), "the journal must be absent on readback");
        let failure = state.publish_failure(code, "stable");
        assert_eq!(failure["details"]["journal_discarded"], true);
    }

    #[test]
    fn already_absent_journal_is_safe_but_not_reported_as_discarded() {
        let state = Maintenance::default();
        let dir = tempfile::tempdir().unwrap();
        let journal = dir.path().join("desktop-update.json");

        let code = finalize_install_failure(&state, true, || {
            crate::bundled_runtime::discard_journal_at(&journal)
        });

        assert_eq!(code, "app_install_failed");
        assert!(!journal.exists(), "absence must remain durable");
        let failure = state.publish_failure(code, "stable");
        assert_eq!(failure["details"]["journal_discarded"], false);
    }

    #[test]
    fn failed_discard_keeps_the_journal_and_incomplete_state() {
        let state = Maintenance::default();
        let dir = tempfile::tempdir().unwrap();
        let journal = dir.path().join("desktop-update.json");
        std::fs::create_dir(&journal).unwrap();

        let code = finalize_install_failure(&state, true, || {
            crate::bundled_runtime::discard_journal_at(&journal)
        });

        assert_eq!(code, "app_install_incomplete");
        assert!(
            journal.exists(),
            "the failed effect must preserve the journal"
        );
        let failure = state.publish_failure(code, "stable");
        assert!(
            failure["details"].get("journal_discarded").is_none(),
            "incomplete recovery must not claim a completed discard"
        );
    }

    #[test]
    fn unusable_app_keeps_recovery_state_without_touching_the_journal() {
        let state = Maintenance::default();
        let code = finalize_install_failure(&state, false, || {
            panic!("an unusable App must not discard recovery state")
        });
        assert_eq!(code, "app_install_incomplete");
    }
}
