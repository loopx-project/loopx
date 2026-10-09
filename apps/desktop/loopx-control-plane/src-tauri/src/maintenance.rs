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
    separately_managed_runtime: AtomicBool,
    automatic_update_checked: AtomicBool,
    incomplete_app_installation: AtomicBool,
    manual_failure_pending: AtomicBool,
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
    fn status_snapshot(&self) -> Value {
        if self.incomplete_app_installation.load(Ordering::Acquire) {
            json!({"phase":"error", "details":{"code":"app_install_incomplete"}})
        } else {
            self.snapshot.lock().unwrap().clone()
        }
    }

    // Only a completed native replacement/verified restore can retire this
    // failure latch. Keep the restart readback coherent with the supervisor.
    fn complete_app_replacement(&self, details: Value) -> Value {
        self.incomplete_app_installation
            .store(false, Ordering::Release);
        self.publish("restart_required", details)
    }

    fn acquire(&self) -> Result<BusyGuard<'_>, String> {
        self.busy
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "update_busy")?;
        Ok(BusyGuard(&self.busy))
    }

    async fn run_automatic_update<T, Check, Apply, ApplyFuture, Restart>(
        &self,
        channel: &str,
        check: Check,
        apply: Apply,
        restart: Restart,
    ) -> Result<Option<()>, String>
    where
        Check: std::future::Future<Output = Result<Option<T>, String>>,
        Apply: FnOnce(T) -> ApplyFuture,
        ApplyFuture: std::future::Future<Output = Result<(), String>>,
        Restart: FnOnce(),
    {
        if self.busy.load(Ordering::Acquire) {
            return Ok(None);
        }
        // Automatic startup work owns supervision, while an explicit action
        // reserves `busy` and waits here instead of failing with update_busy.
        let Ok(_supervision) = self.supervision.try_lock() else {
            return Ok(None);
        };
        if self.busy.load(Ordering::Acquire) {
            return Ok(None);
        }
        let result: Result<(), String> = async {
            if let Some(update) = check.await? {
                if self.busy.load(Ordering::Acquire) {
                    return Ok(());
                }
                apply(update).await?;
                // Installation cannot be interrupted. Once it finishes, either
                // an admitted manual request reads restart_required or this
                // transaction reserves the non-returning restart atomically.
                let Ok(_restart_reservation) = self.acquire() else {
                    return Ok(());
                };
                restart();
            }
            Ok(())
        }
        .await;
        // A queued explicit action must become the final snapshot writer.
        if let Err(error) = &result {
            self.publish_failure(error, channel);
        }
        result.map(Some)
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
        now: Instant,
        install: impl FnOnce() -> Result<(), String>,
    ) -> Result<(), String> {
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
        if self.busy.load(Ordering::Acquire)
            || self.incomplete_app_installation.load(Ordering::Acquire)
            || self.manual_failure_pending.load(Ordering::Acquire)
        {
            return Ok(None);
        }
        let Ok(_guard) = self.supervision.try_lock() else {
            return Ok(None);
        };
        if self.busy.load(Ordering::Acquire) || self.manual_failure_pending.load(Ordering::Acquire)
        {
            return Ok(None);
        }
        let phase = self.snapshot.lock().unwrap()["phase"]
            .as_str()
            .unwrap_or("idle")
            .to_string();
        // A completed Check owns the available update until an explicit
        // Apply/Repair/Forget action changes the phase. Another failed runtime
        // probe must not erase its Install button while the user is acting.
        if matches!(phase.as_str(), "available" | "restart_required") {
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
    let selection = crate::runtime_selection::selected(&app).ok();
    let runtime = bundled_runtime::identity(&app).ok();
    let runtime_selection = json!({
        "explicit": selection.as_ref().is_some_and(|selected| selected.environment_override),
        "remembered": selection.as_ref().is_some_and(|selected| selected.explicit),
        "bundled_repair_available": selection.is_some()
            && !state.separately_managed_runtime.load(Ordering::Acquire),
    });
    let rollback_available = crate::update_backup::available(&app);
    let app_version = app.package_info().version.to_string();
    let startup = state.startup_timing();
    // Read mutable transaction state after the potentially blocking
    // diagnostics so this response cannot revive a superseded phase.
    let snapshot = state.status_snapshot();
    let last_failure = state.last_failure.lock().unwrap().clone();
    json!({"state": snapshot, "startup": startup, "last_failure": last_failure, "app_version": app_version, "runtime": runtime, "runtime_selection": runtime_selection, "rollback_available": rollback_available, "environment": environment})
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
        "check"
            | "apply"
            | "repair"
            | "align_runtime"
            | "forget_runtime_selection"
            | "restart"
            | "rollback"
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
    state.manual_failure_pending.store(false, Ordering::Release);
    let outcome = perform(&app, &action, &channel, url).await;
    if let Err(error) = &outcome {
        state.publish_manual_failure(error, &channel);
    }
    outcome
}
impl Maintenance {
    // Keep a failed user operation and its retry controls until the next
    // explicit action. An automatic feed failure still permits startup.
    fn publish_manual_failure(&self, code: &str, channel: &str) -> Value {
        self.manual_failure_pending.store(true, Ordering::Release);
        self.publish_failure(code, channel)
    }

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
// Bounded reinstall of this App's bundled snapshot. Persist the qualified
// promotion before reconnecting; the old discovery cache must not undo it.
async fn reinstall_bundled_runtime(app: &AppHandle) -> Result<Value, String> {
    // A separate CLI selection belongs to its installation owner. Never
    // promote a snapshot that this same window cannot select afterwards.
    let selection = crate::runtime_selection::selected(app)?;
    let state = app.state::<Maintenance>();
    if selection.environment_override || state.separately_managed_runtime.load(Ordering::Acquire) {
        return Err("runtime_selection_explicit".into());
    }
    state.publish("installing_runtime", json!({}));
    let handle = app.clone();
    let executable =
        tauri::async_runtime::spawn_blocking(move || bundled_runtime::install_private(&handle))
            .await
            .map_err(|_| "runtime_install_failed".to_string())??;
    crate::runtime_selection::remember(
        app,
        &crate::runtime_selection::Selection {
            executable,
            explicit: false,
            environment_override: false,
        },
    )?;
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
    if action == "forget_runtime_selection" {
        crate::runtime_selection::forget(app)?;
        return Ok(state.publish("connecting", json!({})));
    }
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
            .timeout(Duration::from_secs(3))
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
        *state.pending.lock().unwrap() = update.map(|mut update| {
            // A quick availability check must not become the download budget.
            update.timeout = Some(Duration::from_secs(30));
            (channel.to_string(), update)
        });
        return Ok(state.publish(phase, details));
    }
    if action == "rollback" {
        state.publish("installing_app", json!({}));
        let handle = app.clone();
        tauri::async_runtime::spawn_blocking(move || crate::update_backup::restore(&handle))
            .await
            .map_err(|_| "rollback_failed")??;
        return Ok(state.complete_app_replacement(json!({})));
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
    // After restart, freshness coordination prepares the App-owned runtime
    // only when it is newer; it cannot reassign the system CLI's owner.
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
        // previous App (bundle intact, a qualified runtime available) may
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
    Ok(state.complete_app_replacement(json!({"version":target})))
}
// A safe-restart promise after a failed app replacement requires the running
// App's bundle to still be present -- the pinned macOS updater's install_inner
// performs rename(old App -> temporary) before rename(new App -> original), so
// the second rename failing leaves the original location empty -- its actual
// installed target to pass the same signature/integrity verification the
// backup boundary uses (a surviving Info.plist and executable do not prove
// sealed resources are intact), and a qualified runtime must be available.
// A newer runtime need not share this App's source revision. A verified backup copy can never substitute for
// verifying the current installation.
fn failed_install_left_previous_app_usable(app: &AppHandle) -> bool {
    let Ok(executable) = std::env::current_exe() else {
        return false;
    };
    let selected = crate::runtime_selection::selected(app)
        .ok()
        .and_then(|selected| crate::services::runtime_identity_for_executable(&selected.executable))
        .or_else(|| {
            bundled_runtime::private_executable(app)
                .ok()
                .and_then(|path| {
                    crate::services::runtime_identity_for_executable(&path.to_string_lossy())
                })
        });
    previous_installation_is_usable(
        &executable,
        bundled_runtime::identity(app).ok().as_ref(),
        selected.as_ref(),
    )
}

// Path-level safe-restart predicate shared by the release failure path and
// tests: the actual installed target (located from the executable, never a
// caller path) must verify layout AND codesign integrity, and the installed
// runtime must have a qualified identity. Unverified keeps the
// journal and the `app_install_incomplete` recovery state.
fn previous_installation_is_usable(
    executable: &std::path::Path,
    bundled: Option<&Value>,
    installed: Option<&Value>,
) -> bool {
    crate::update_backup::installed_bundle_verifies(executable)
        && bundled.is_some()
        && installed.is_some()
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
        state
            .incomplete_app_installation
            .store(true, Ordering::Release);
        return code;
    }
    let journal_result = discard_journal();
    let journal_removed = journal_result.as_ref().is_ok_and(|removed| *removed);
    state
        .install_journal_discarded
        .store(journal_removed, Ordering::Release);
    let code = install_failure_state(previous_app_usable, journal_result);
    state
        .incomplete_app_installation
        .store(code == "app_install_incomplete", Ordering::Release);
    code
}

// A runtime state that already published its own phase must not be relabelled
// by the supervisor's generic error publication: the boot surface renders the
// repair guidance and the operator decision by their own rules.
fn runtime_state_publishes_own_phase(error: &str) -> bool {
    matches!(
        error,
        "runtime_setup_required" | "runtime_pairing_required" | "runtime_identity_unavailable"
    )
}

fn resume_runtime(app: &AppHandle) -> Result<crate::services::SelectedRuntime, String> {
    use crate::runtime_selection::{
        compare_official_commits, is_private_runtime, prefer_current_bundle,
        prefer_discovered_runtime, same_release_installation, Selection,
    };
    let mut selection = crate::runtime_selection::selected(app)?;
    let mut installed = crate::services::runtime_identity_for_executable(&selection.executable);
    if cfg!(dev) || !cfg!(target_os = "macos") {
        return Ok(crate::services::SelectedRuntime {
            executable: selection.executable,
            identity: installed,
        });
    }
    let state = app.state::<Maintenance>();
    let _guard = state.acquire()?;
    let bundled = bundled_runtime::identity(app)?;
    let candidate = json!({"package_version":app.package_info().version.to_string(), "source_revision":bundled["source_revision"]});
    let private_executable = bundled_runtime::private_executable(app)?;
    // Only a launch-time developer override pins a runtime. Ordinary launches
    // reconcile discovery, a previous choice, and the App-owned installation.
    if !selection.environment_override {
        let mut candidates = crate::services::discovered_loopx_executables();
        let default_executable = candidates.first().cloned();
        candidates.push(private_executable.to_string_lossy().into_owned());
        for executable in candidates {
            if executable == selection.executable {
                continue;
            }
            if let Some(identity) = crate::services::runtime_identity_for_executable(&executable) {
                let replace = installed.as_ref().is_none_or(|current| {
                    if is_private_runtime(&executable, &private_executable)
                        && identity["source_revision"] == bundled["source_revision"]
                    {
                        prefer_current_bundle(
                            app.package_info(),
                            current,
                            &identity,
                            is_private_runtime(&selection.executable, &private_executable),
                            compare_official_commits,
                        )
                    } else {
                        prefer_discovered_runtime(
                            app.package_info(),
                            current,
                            &identity,
                            default_executable.as_ref() == Some(&executable)
                                && same_release_installation(
                                    &selection.executable,
                                    current,
                                    &executable,
                                    &identity,
                                ),
                            compare_official_commits,
                        )
                    }
                });
                if replace {
                    selection = Selection {
                        executable,
                        explicit: false,
                        environment_override: false,
                    };
                    installed = Some(identity);
                }
            }
        }
    }
    if selection.environment_override && installed.is_none() {
        state.publish(
            "runtime_required",
            json!({"code":"runtime_identity_unavailable", "bundled_repair_available":false}),
        );
        return Err("runtime_identity_unavailable".into());
    }
    let newer_bundle = installed.as_ref().is_none_or(|current| {
        prefer_current_bundle(
            app.package_info(),
            current,
            &candidate,
            is_private_runtime(&selection.executable, &private_executable),
            compare_official_commits,
        )
    });
    if newer_bundle && !selection.environment_override {
        let mut prepared = None;
        let result = state.prepare_runtime(Instant::now(), || {
            prepared = Some(bundled_runtime::install_private(app)?);
            Ok(())
        });
        match result {
            Ok(()) => {
                selection = Selection {
                    executable: prepared.ok_or("runtime_install_failed")?,
                    explicit: false,
                    environment_override: false,
                };
                installed = crate::services::runtime_identity_for_executable(&selection.executable);
                if installed.is_none() {
                    return Err("runtime_identity_unavailable".into());
                }
            }
            Err(error) if installed.is_none() => return Err(error),
            // An optional upgrade must not strand an already usable runtime.
            Err(_) => {}
        }
    }
    if installed.is_none() {
        return Err("runtime_identity_unavailable".into());
    }
    // A previous bundled-install approval cannot silently downgrade a runtime
    // chosen by the automatic freshness rule. Private preparation promotes
    // only after qualification; interruption is retried normally.
    bundled_runtime::finish_legacy_journal(app)?;
    if let Err(error) = crate::runtime_selection::remember(app, &selection) {
        // A preference is an optimization, not a requirement to use a
        // qualified runtime. Re-discovery remains available on next launch.
        eprintln!("LoopX runtime preference was not saved: {error}");
    }
    state.separately_managed_runtime.store(
        selection.environment_override
            || !is_private_runtime(&selection.executable, &private_executable),
        Ordering::Release,
    );
    Ok(crate::services::SelectedRuntime {
        executable: selection.executable,
        identity: installed,
    })
}

fn automatic_app_update(app: &AppHandle) {
    let state = app.state::<Maintenance>();
    if cfg!(dev)
        || !cfg!(target_os = "macos")
        || app.config().identifier != "io.loopx.control-plane"
        || state.automatic_update_checked.swap(true, Ordering::AcqRel)
    {
        return;
    }
    let channel = if app.package_info().version.pre.as_str().starts_with("main.") {
        "main"
    } else {
        "stable"
    };
    let result: Result<Option<()>, String> =
        tauri::async_runtime::block_on(state.run_automatic_update(
            channel,
            async {
                let url = endpoint(channel)?;
                let checked = perform(app, "check", channel, url.clone()).await?;
                Ok((checked["phase"] == "available").then_some(url))
            },
            |url| async move {
                perform(app, "apply", channel, url).await?;
                Ok(())
            },
            || app.restart(),
        ));
    if let Err(error) = result {
        eprintln!("LoopX automatic App update deferred: {error}");
    }
    // An unavailable feed/signature never certifies freshness or prevents use
    // of the installed App. Diagnostics retain the failed check.
}

pub fn start_services(
    app: &AppHandle,
    endpoints: &crate::service_endpoints::ServiceEndpoints,
) -> Result<Option<crate::services::ServiceSet>, String> {
    app.state::<Maintenance>()
        .startup_started
        .get_or_init(Instant::now);
    automatic_app_update(app);
    app.state::<Maintenance>().reconcile_services(|| {
        let runtime = match resume_runtime(app) {
            Ok(runtime) => runtime,
            Err(error) => {
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
        };
        crate::services::ServiceSet::start(&runtime, endpoints, |pending| {
            let service = crate::services::ServiceKind::pending_label(pending);
            app.state::<Maintenance>()
                .publish("connecting", json!({"service":service}));
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

    fn finish_queued_manual_after_automatic_failure(
        manual: impl FnOnce(&Maintenance) + Send + 'static,
    ) -> std::sync::Arc<Maintenance> {
        let state = std::sync::Arc::new(Maintenance::default());
        let (automatic_started_tx, automatic_started_rx) = std::sync::mpsc::channel();
        let (manual_admitted_tx, manual_admitted_rx) = std::sync::mpsc::channel();
        let (release_automatic_tx, release_automatic_rx) = std::sync::mpsc::channel();
        let automatic_state = std::sync::Arc::clone(&state);
        let automatic = std::thread::spawn(move || {
            tauri::async_runtime::block_on(automatic_state.run_automatic_update(
                "stable",
                async move {
                    automatic_started_tx.send(()).unwrap();
                    release_automatic_rx
                        .recv_timeout(Duration::from_secs(3))
                        .unwrap();
                    Err::<Option<()>, String>("update_check_failed".into())
                },
                |_| async { Ok(()) },
                || {},
            ))
            .unwrap_err()
        });
        automatic_started_rx
            .recv_timeout(Duration::from_secs(3))
            .unwrap();

        let manual_state = std::sync::Arc::clone(&state);
        let explicit = std::thread::spawn(move || {
            let _manual = manual_state.acquire().unwrap();
            manual_admitted_tx.send(()).unwrap();
            tauri::async_runtime::block_on(async {
                let _supervision = manual_state.supervision.lock().await;
                manual(&manual_state);
            });
        });
        manual_admitted_rx
            .recv_timeout(Duration::from_secs(3))
            .unwrap();
        release_automatic_tx.send(()).unwrap();

        assert_eq!(automatic.join().unwrap(), "update_check_failed");
        explicit.join().unwrap();
        state
    }

    #[test]
    fn automatic_preparation_is_bounded_and_recovery_reuses_the_supervisor() {
        let state = Maintenance::default();
        let now = Instant::now();
        assert!(state
            .reconcile_services(
                || state.prepare_runtime(now, || Err("runtime_install_exit_1".into()))
            )
            .is_err());
        assert!(state.acquire().is_ok());
        assert!(state
            .prepare_runtime(now + Duration::from_secs(2), || panic!("backoff"))
            .is_err());
        assert_eq!(
            state.snapshot.lock().unwrap()["details"]["code"],
            "runtime_install_exit_1"
        );
        assert_eq!(
            state
                .reconcile_services(
                    || state.prepare_runtime(now + Duration::from_secs(31), || Ok(()))
                )
                .unwrap(),
            Some(())
        );
        assert_eq!(state.snapshot.lock().unwrap()["phase"], "ready");
        for seconds in [62, 93, 124] {
            assert!(state
                .prepare_runtime(now + Duration::from_secs(seconds), || Err(
                    "runtime_install_exit_1".into()
                ))
                .is_err());
        }
        assert!(state
            .prepare_runtime(now + Duration::from_secs(1000), || panic!(
                "budget exhausted"
            ))
            .is_err());
        assert_eq!(
            state.reconcile_services(|| Ok(())).unwrap(),
            Some(()),
            "an externally repaired usable runtime needs no new install"
        );
    }
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
    fn queued_manual_success_supersedes_automatic_failure() {
        let state = finish_queued_manual_after_automatic_failure(|state| {
            state.manual_failure_pending.store(false, Ordering::Release);
            state.publish("available", json!({"version":"1.3.1"}));
        });

        assert_eq!(state.snapshot.lock().unwrap()["phase"], "available");
        assert_eq!(
            state.last_failure.lock().unwrap()["details"]["code"],
            "update_check_failed"
        );
        assert!(!state.manual_failure_pending.load(Ordering::Acquire));
    }

    #[test]
    fn queued_manual_failure_supersedes_automatic_failure() {
        let state = finish_queued_manual_after_automatic_failure(|state| {
            state.manual_failure_pending.store(false, Ordering::Release);
            state.publish_manual_failure("runtime_install_exit_23", "stable");
        });

        assert_eq!(
            state.snapshot.lock().unwrap()["details"]["code"],
            "runtime_install_exit_23"
        );
        assert_eq!(
            state.last_failure.lock().unwrap()["details"]["code"],
            "runtime_install_exit_23"
        );
        assert!(state.manual_failure_pending.load(Ordering::Acquire));
    }

    #[test]
    fn automatic_failure_is_published_before_supervision_is_released() {
        let state = Maintenance::default();
        let error = tauri::async_runtime::block_on(state.run_automatic_update(
            "stable",
            async { Err::<Option<()>, String>("update_check_failed".into()) },
            |_| async { Ok(()) },
            || {},
        ))
        .unwrap_err();

        assert_eq!(error, "update_check_failed");
        assert_eq!(
            state.snapshot.lock().unwrap()["details"]["code"],
            "update_check_failed"
        );
    }

    #[test]
    fn automatic_update_yields_to_an_admitted_manual_action() {
        let state = Maintenance::default();
        let _manual = state.acquire().unwrap();
        let ran = std::cell::Cell::new(false);
        let result = tauri::async_runtime::block_on(state.run_automatic_update(
            "stable",
            async {
                ran.set(true);
                Ok(None::<()>)
            },
            |_| async { Ok(()) },
            || {},
        ))
        .unwrap();
        assert!(result.is_none());
        assert!(!ran.get(), "automatic update must yield to explicit work");
    }

    #[test]
    fn queued_manual_before_available_update_preempts_automatic_install() {
        let state = std::sync::Arc::new(Maintenance::default());
        let applied = std::sync::Arc::new(AtomicBool::new(false));
        let restarted = std::sync::Arc::new(AtomicBool::new(false));
        let (check_started_tx, check_started_rx) = std::sync::mpsc::channel();
        let (release_check_tx, release_check_rx) = std::sync::mpsc::channel();
        let automatic_state = std::sync::Arc::clone(&state);
        let automatic_applied = std::sync::Arc::clone(&applied);
        let automatic_restarted = std::sync::Arc::clone(&restarted);
        let automatic = std::thread::spawn(move || {
            tauri::async_runtime::block_on(automatic_state.run_automatic_update(
                "stable",
                async move {
                    check_started_tx.send(()).unwrap();
                    release_check_rx
                        .recv_timeout(Duration::from_secs(3))
                        .unwrap();
                    Ok(Some(()))
                },
                move |_| async move {
                    automatic_applied.store(true, Ordering::Release);
                    Ok(())
                },
                move || automatic_restarted.store(true, Ordering::Release),
            ))
            .unwrap()
        });
        check_started_rx
            .recv_timeout(Duration::from_secs(3))
            .unwrap();

        let _manual = state.acquire().expect("manual request admitted");
        release_check_tx.send(()).unwrap();
        assert_eq!(automatic.join().unwrap(), Some(()));
        assert!(
            !applied.load(Ordering::Acquire),
            "an admitted manual request must run before automatic installation"
        );
        assert!(!restarted.load(Ordering::Acquire));
    }

    #[test]
    fn queued_manual_during_install_reads_restart_required_before_restart() {
        let state = std::sync::Arc::new(Maintenance::default());
        let restarted = std::sync::Arc::new(AtomicBool::new(false));
        let (install_started_tx, install_started_rx) = std::sync::mpsc::channel();
        let (release_install_tx, release_install_rx) = std::sync::mpsc::channel();
        let automatic_state = std::sync::Arc::clone(&state);
        let install_state = std::sync::Arc::clone(&state);
        let automatic_restarted = std::sync::Arc::clone(&restarted);
        let automatic = std::thread::spawn(move || {
            tauri::async_runtime::block_on(automatic_state.run_automatic_update(
                "stable",
                async { Ok(Some(())) },
                move |_| async move {
                    install_started_tx.send(()).unwrap();
                    release_install_rx
                        .recv_timeout(Duration::from_secs(3))
                        .unwrap();
                    install_state.complete_app_replacement(json!({"version":"1.3.1"}));
                    Ok(())
                },
                move || automatic_restarted.store(true, Ordering::Release),
            ))
            .unwrap()
        });
        install_started_rx
            .recv_timeout(Duration::from_secs(3))
            .unwrap();

        let _manual = state.acquire().expect("manual request admitted");
        release_install_tx.send(()).unwrap();
        assert_eq!(automatic.join().unwrap(), Some(()));
        assert_eq!(state.status_snapshot()["phase"], "restart_required");
        tauri::async_runtime::block_on(async {
            let _supervision = state.supervision.lock().await;
            assert_eq!(
                allow_action(
                    state.snapshot.lock().unwrap()["phase"]
                        .as_str()
                        .unwrap_or("idle"),
                    "repair",
                ),
                Err("restart_required".into())
            );
        });
        assert!(
            !restarted.load(Ordering::Acquire),
            "automatic restart must not discard an admitted manual response"
        );
    }

    #[test]
    fn queued_manual_runs_after_automatic_check_finds_no_update() {
        let state = std::sync::Arc::new(Maintenance::default());
        let (check_started_tx, check_started_rx) = std::sync::mpsc::channel();
        let (release_check_tx, release_check_rx) = std::sync::mpsc::channel();
        let automatic_state = std::sync::Arc::clone(&state);
        let check_state = std::sync::Arc::clone(&state);
        let automatic = std::thread::spawn(move || {
            tauri::async_runtime::block_on(automatic_state.run_automatic_update(
                "stable",
                async move {
                    check_started_tx.send(()).unwrap();
                    release_check_rx
                        .recv_timeout(Duration::from_secs(3))
                        .unwrap();
                    check_state.publish("up_to_date", json!({"channel":"stable"}));
                    Ok(None::<()>)
                },
                |_| async { panic!("no update must not install") },
                || panic!("no update must not restart"),
            ))
            .unwrap()
        });
        check_started_rx
            .recv_timeout(Duration::from_secs(3))
            .unwrap();

        let _manual = state.acquire().expect("manual request admitted");
        release_check_tx.send(()).unwrap();
        assert_eq!(automatic.join().unwrap(), Some(()));
        let manual_result = tauri::async_runtime::block_on(async {
            let _supervision = state.supervision.lock().await;
            allow_action(
                state.snapshot.lock().unwrap()["phase"]
                    .as_str()
                    .unwrap_or("idle"),
                "check",
            )?;
            Ok::<Value, String>(state.publish("available", json!({"version":"1.3.1"})))
        })
        .unwrap();
        assert_eq!(manual_result["phase"], "available");
        assert_eq!(state.status_snapshot(), manual_result);
    }

    #[test]
    fn queued_manual_result_supersedes_automatic_install_failure() {
        let state = std::sync::Arc::new(Maintenance::default());
        let (install_started_tx, install_started_rx) = std::sync::mpsc::channel();
        let (release_install_tx, release_install_rx) = std::sync::mpsc::channel();
        let automatic_state = std::sync::Arc::clone(&state);
        let automatic = std::thread::spawn(move || {
            tauri::async_runtime::block_on(automatic_state.run_automatic_update(
                "stable",
                async { Ok(Some(())) },
                move |_| async move {
                    install_started_tx.send(()).unwrap();
                    release_install_rx
                        .recv_timeout(Duration::from_secs(3))
                        .unwrap();
                    Err("app_install_failed".into())
                },
                || panic!("a failed install must not restart"),
            ))
            .unwrap_err()
        });
        install_started_rx
            .recv_timeout(Duration::from_secs(3))
            .unwrap();

        let _manual = state.acquire().expect("manual request admitted");
        release_install_tx.send(()).unwrap();
        assert_eq!(automatic.join().unwrap(), "app_install_failed");
        assert_eq!(
            state.last_failure.lock().unwrap()["details"]["code"],
            "app_install_failed"
        );
        let manual_result = tauri::async_runtime::block_on(async {
            let _supervision = state.supervision.lock().await;
            state.manual_failure_pending.store(false, Ordering::Release);
            state.publish("connecting", json!({}))
        });
        assert_eq!(manual_result["phase"], "connecting");
        assert_eq!(state.status_snapshot(), manual_result);
    }

    #[test]
    fn automatic_install_restarts_when_no_manual_request_is_pending() {
        let state = Maintenance::default();
        let restarted = AtomicBool::new(false);
        let result = tauri::async_runtime::block_on(state.run_automatic_update(
            "stable",
            async { Ok(Some(())) },
            |_| async {
                state.complete_app_replacement(json!({"version":"1.3.1"}));
                Ok(())
            },
            || {
                assert_eq!(
                    state.acquire().err().as_deref(),
                    Some("update_busy"),
                    "automatic restart reservation rejects a late manual request"
                );
                restarted.store(true, Ordering::Release);
            },
        ))
        .unwrap();

        assert_eq!(result, Some(()));
        assert!(restarted.load(Ordering::Acquire));
        assert_eq!(state.status_snapshot()["phase"], "restart_required");
        assert!(
            state.acquire().is_ok(),
            "test restart returned and released"
        );
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
    fn an_available_update_stays_actionable_until_explicit_recovery() {
        let state = Maintenance::default();
        let available = state.publish("available", json!({"version":"1.2.4"}));
        // A failed runtime must not erase the update the user just checked.
        // Repeated supervisor ticks are observations, not a new user action.
        for _ in 0..3 {
            assert_eq!(
                state
                    .reconcile_services::<()>(|| panic!("update choice is pending"))
                    .unwrap(),
                None
            );
            assert_eq!(*state.snapshot.lock().unwrap(), available);
        }
        assert!(
            state.acquire().is_ok(),
            "the Apply action remains available"
        );

        // Forget/Repair publishes connecting, so a deliberate recovery can
        // still resume the owning service supervisor in this same process.
        state.publish("connecting", json!({}));
        assert_eq!(state.reconcile_services(|| Ok(())).unwrap(), Some(()));
        assert_eq!(state.snapshot.lock().unwrap()["phase"], "ready");
    }

    #[test]
    fn manual_recovery_failure_retains_its_diagnostics_until_retry() {
        let state = Maintenance::default();
        let failure = state.publish_manual_failure("backup_failed", "stable");
        for _ in 0..3 {
            assert_eq!(
                state
                    .reconcile_services::<()>(|| panic!("a failed manual action owns recovery"))
                    .unwrap(),
                None
            );
            assert_eq!(*state.snapshot.lock().unwrap(), failure);
            assert_eq!(*state.last_failure.lock().unwrap(), failure);
        }
        assert!(state.acquire().is_ok(), "same-window retry is available");

        // The accepted next user action releases the failure; a completed
        // rollback must keep Restart instead of resuming service probes.
        state.manual_failure_pending.store(false, Ordering::Release);
        state.publish("restart_required", json!({}));
        assert_eq!(
            state
                .reconcile_services::<()>(|| panic!("rollback awaits restart"))
                .unwrap(),
            None
        );
        assert_eq!(state.snapshot.lock().unwrap()["phase"], "restart_required");
    }

    #[test]
    fn completed_restore_retires_incomplete_installation_and_exposes_restart() {
        let state = Maintenance::default();
        finalize_install_failure(&state, false, || panic!("unverified journal retained"));
        assert_eq!(
            state.status_snapshot()["details"]["code"],
            "app_install_incomplete"
        );
        // The successful verified restore uses this same transition. A stale
        // failure latch must not hide its Restart action from status polling.
        let restored = state.complete_app_replacement(json!({}));
        assert_eq!(restored["phase"], "restart_required");
        assert_eq!(state.status_snapshot(), restored);
        assert_eq!(
            state
                .reconcile_services::<()>(|| panic!("restart owns next action"))
                .unwrap(),
            None
        );
    }

    #[test]
    fn automatic_update_failure_does_not_prevent_installed_app_startup() {
        let state = Maintenance::default();
        state.publish_failure("update_feed_unavailable", "stable");
        assert_eq!(state.reconcile_services(|| Ok(())).unwrap(), Some(()));
        assert_eq!(state.snapshot.lock().unwrap()["phase"], "ready");
        assert_eq!(
            state.last_failure.lock().unwrap()["details"]["code"],
            "update_feed_unavailable"
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
            assert!(
                !runtime_state_publishes_own_phase(relabelled),
                "{relabelled}"
            );
        }
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
    fn safe_restart_requires_an_intact_app_and_a_qualified_runtime() {
        // Review round 4: drive the production safe-restart predicate
        // (previous_installation_is_usable — the same function the failed
        // install path calls) over a real ad-hoc signed synthetic
        // installation. Layout-only evidence accepted a bundle whose sealed
        // resource was deleted; the shared codesign gate must reject it, and
        // only a signature-intact installation with a qualified runtime may discard
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

        // A different qualified runtime is usable: automatic selection may
        // legitimately have selected a newer CLI than this App's snapshot.
        let different = previous_installation_is_usable(
            &support::synthetic_executable(&intact),
            Some(&bundled("a")),
            Some(&bundled("b")),
        );
        assert!(different);
        assert_eq!(
            install_failure_recovery(different),
            ("app_install_failed", true)
        );
        assert!(!previous_installation_is_usable(
            &support::synthetic_executable(&intact),
            Some(&bundled("a")),
            None,
        ));
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
        state.publish("checking", serde_json::json!({}));
        assert_eq!(
            state
                .reconcile_services::<()>(|| panic!("an incomplete App cannot resume"))
                .unwrap(),
            None
        );
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
