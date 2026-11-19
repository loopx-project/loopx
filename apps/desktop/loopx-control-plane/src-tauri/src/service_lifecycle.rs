//! One owner for this window's App-owned service runtime. The live supervisor
//! adopts, recovers and surrenders it; the window exit and the App-update
//! handoff reclaim it before the process can be replaced. No task execution,
//! and no second place that decides which service processes this App owns.

use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex, MutexGuard,
};

use tauri::{AppHandle, Manager, Runtime};

use crate::services::ServiceSet;

#[derive(Clone)]
pub(crate) struct ServiceLifecycle(Arc<Inner>);

struct Inner {
    shutting_down: AtomicBool,
    handoff: AtomicBool,
    reclaimed: AtomicBool,
    restore_pending: AtomicBool,
    services: Mutex<Option<ServiceSet>>,
}

impl ServiceLifecycle {
    pub(crate) fn new() -> Self {
        Self(Arc::new(Inner {
            shutting_down: AtomicBool::new(false),
            handoff: AtomicBool::new(false),
            reclaimed: AtomicBool::new(false),
            restore_pending: AtomicBool::new(false),
            services: Mutex::new(None),
        }))
    }

    /// The window is closing: the supervisor stops and never starts again.
    pub(crate) fn should_stop(&self) -> bool {
        self.0.shutting_down.load(Ordering::Acquire)
    }

    /// An App-update handoff owns the runtime. The supervisor neither starts
    /// nor recovers services while it is parked, and a start that was already
    /// in flight surrenders the set it built instead of adopting it.
    pub(crate) fn handoff_active(&self) -> bool {
        self.0.handoff.load(Ordering::Acquire)
    }

    /// A released handoff that reclaimed this window's services still owes
    /// them back: the pinned Windows installer only exits the process after a
    /// successful launch, so a failure returns here with the runtime already
    /// stopped. Cleared when a live set is adopted again.
    pub(crate) fn restore_pending(&self) -> bool {
        self.0.restore_pending.load(Ordering::Acquire)
    }

    /// The live services of this window. The lock covers ownership changes
    /// only: callers never hold it across an eager service wait.
    pub(crate) fn services(&self) -> MutexGuard<'_, Option<ServiceSet>> {
        self.0.services.lock().expect("service state lock")
    }

    /// Adopt a freshly started set. A start that raced the closing window or
    /// the App-update handoff is handed back so its caller stops the processes
    /// it started instead of leaving a group this App no longer owns. A set
    /// rejected by an active update handoff records the restore debt for the
    /// stopped runtime; a live set settles the restore a released handoff
    /// still owed.
    pub(crate) fn adopt(&self, services: ServiceSet) -> Result<(), ServiceSet> {
        let mut current = self.services();
        if self.should_stop() {
            return Err(services);
        }
        if self.handoff_active() {
            // The caller stops this late set instead of adopting it. Record
            // that the active handoff reclaimed the runtime, so a failed
            // installer launch still leaves the same restore debt as a set
            // reclaimed through the ownership slot.
            self.0.reclaimed.store(true, Ordering::Release);
            return Err(services);
        }
        self.0.reclaimed.store(false, Ordering::Release);
        self.0.restore_pending.store(false, Ordering::Release);
        *current = Some(services);
        Ok(())
    }

    /// Reclaim this App's own services before the process can be replaced:
    /// save the workspace route, park the supervisor, and stop the owned set.
    /// A set stops through its own job objects, so its descendants leave with
    /// it, while a service this App never started (a CLI-owned listener on
    /// another port) is not in the set and keeps running.
    pub(crate) fn begin_update_handoff<R: Runtime>(&self, app: &AppHandle<R>) -> bool {
        self.begin_update_handoff_with(|| crate::workspace_location::remember_current(app))
    }

    /// Release a handoff that returned in-process, which for the pinned
    /// Windows installer means the installer never launched. Returns whether
    /// this window's runtime was reclaimed, and records that the supervisor
    /// owes the same runtime back while the failure stays on screen.
    pub(crate) fn release_update_handoff(&self) -> bool {
        // Serialize the release with `adopt`: a late set that saw the active
        // handoff must either be counted by this release or adopt cleanly
        // after the handoff ended, never be stopped after the debt check.
        let _current = self.services();
        self.0.handoff.store(false, Ordering::Release);
        let reclaimed = self.0.reclaimed.swap(false, Ordering::AcqRel);
        if reclaimed {
            self.0.restore_pending.store(true, Ordering::Release);
        }
        reclaimed
    }

    /// The window is exiting: save the route and reclaim the owned services
    /// before the process ends.
    pub(crate) fn shutdown<R: Runtime>(&self, app: &AppHandle<R>) {
        self.shutdown_with(|| crate::workspace_location::remember_current(app));
    }

    /// The route-save effect is injected so the ownership rule stays
    /// exercisable without a live window, exactly as `begin_update_handoff`
    /// runs it for the real one.
    pub(crate) fn begin_update_handoff_with(&self, remember_route: impl FnOnce()) -> bool {
        self.reclaim(true, remember_route)
    }

    fn shutdown_with(&self, remember_route: impl FnOnce()) {
        self.reclaim(false, remember_route);
    }

    fn reclaim(&self, update_handoff: bool, remember_route: impl FnOnce()) -> bool {
        // Park the supervisor before touching ownership: a start that is
        // already in flight re-checks this park under the same lock and stops
        // its own set instead of adopting it.
        if update_handoff {
            self.0.handoff.store(true, Ordering::Release);
        } else {
            self.0.shutting_down.store(true, Ordering::Release);
        }
        remember_route();
        let reclaimed = self.services().take();
        match reclaimed {
            Some(mut services) => {
                services.stop();
                if update_handoff {
                    self.0.reclaimed.store(true, Ordering::Release);
                }
                true
            }
            None => false,
        }
    }
}

/// The pinned updater's pre-exit hook. On Windows it runs after the verified
/// archive was unpacked and immediately before the installer is launched, and
/// the process exits as soon as that launch succeeds -- so this is the only
/// place that can reclaim this App's services before an exit that never
/// reaches `RunEvent::Exit`. Tauri's `cleanup_before_exit` is deliberately not
/// part of the hook: it hides the window and clears resource tables that a
/// failed launch cannot restore.
pub(crate) fn begin_app_update_handoff<R: Runtime>(app: &AppHandle<R>) -> bool {
    app.state::<ServiceLifecycle>().begin_update_handoff(app)
}

/// The exact pre-exit hook the desktop window installs. Production and the
/// Windows update regression both build the hook here, so the hook the
/// regression exercises cannot drift from the one the window runs.
pub(crate) fn app_update_pre_exit_hook<R: Runtime>(
    app: &AppHandle<R>,
) -> impl Fn() + Send + Sync + 'static {
    let app = app.clone();
    move || {
        begin_app_update_handoff(&app);
    }
}

/// Release a hook that returned without a replacement. Nothing needs to run
/// on success: the process exits through the plugin right after the launch.
pub(crate) fn release_app_update_handoff<R: Runtime>(app: &AppHandle<R>) -> bool {
    app.state::<ServiceLifecycle>().release_update_handoff()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::services::owned_set_for_test;

    #[test]
    fn an_update_handoff_reclaims_the_runtime_and_owes_exactly_one_restore() {
        let lifecycle = ServiceLifecycle::new();
        assert!(lifecycle.adopt(owned_set_for_test(Vec::new())).is_ok());
        let saved = std::cell::Cell::new(0u32);

        assert!(lifecycle.begin_update_handoff_with(|| saved.set(saved.get() + 1)));
        assert_eq!(saved.get(), 1, "the handoff saves the route once");
        assert!(lifecycle.handoff_active());
        assert!(!lifecycle.should_stop());
        assert!(!lifecycle.restore_pending(), "no release happened yet");
        assert!(lifecycle.services().is_none());

        // Until the handoff returns, no start may claim the runtime.
        assert!(lifecycle.adopt(owned_set_for_test(Vec::new())).is_err());
        assert!(lifecycle.release_update_handoff());
        assert!(!lifecycle.handoff_active());
        assert!(
            lifecycle.restore_pending(),
            "a released handoff owes the reclaimed runtime back"
        );

        // The supervisor settles the debt by adopting a live set again, and a
        // second release has nothing left to owe.
        assert!(!lifecycle.release_update_handoff());
        assert!(lifecycle.adopt(owned_set_for_test(Vec::new())).is_ok());
        assert!(!lifecycle.restore_pending());
        assert!(!lifecycle.release_update_handoff(), "one restore only");
    }

    #[test]
    fn a_handoff_without_a_live_runtime_owes_no_restore() {
        let lifecycle = ServiceLifecycle::new();
        assert!(!lifecycle.begin_update_handoff_with(|| {}));
        assert!(lifecycle.handoff_active());
        assert!(!lifecycle.release_update_handoff());
        assert!(!lifecycle.restore_pending());
        assert!(lifecycle.services().is_none());
    }

    #[test]
    fn a_late_start_rejected_by_the_handoff_still_owes_a_restore() {
        let lifecycle = ServiceLifecycle::new();
        assert!(!lifecycle.begin_update_handoff_with(|| {}));

        // The supervisor started a set before the handoff parked it. `adopt`
        // rejects it; the caller stops that set, and the release that follows
        // must still record the runtime it reclaimed.
        match lifecycle.adopt(owned_set_for_test(Vec::new())) {
            Err(mut late) => late.stop(),
            Ok(()) => panic!("an active handoff must not accept a late set"),
        }
        assert!(lifecycle.release_update_handoff());
        assert!(lifecycle.restore_pending());
    }

    #[test]
    fn a_late_start_after_the_handoff_released_adopts_and_settles() {
        let lifecycle = ServiceLifecycle::new();
        assert!(lifecycle.adopt(owned_set_for_test(Vec::new())).is_ok());
        assert!(lifecycle.begin_update_handoff_with(|| {}));
        assert!(lifecycle.release_update_handoff());
        assert!(lifecycle.restore_pending());

        // Once the handoff is released, a late set is no longer rejected; it
        // settles the outstanding restore instead of creating a second one.
        assert!(lifecycle.adopt(owned_set_for_test(Vec::new())).is_ok());
        assert!(!lifecycle.restore_pending());
        assert!(!lifecycle.release_update_handoff(), "one restore only");
    }

    #[test]
    fn a_closing_window_stops_adopting_and_never_owes_a_restore() {
        let lifecycle = ServiceLifecycle::new();
        assert!(lifecycle.adopt(owned_set_for_test(Vec::new())).is_ok());
        lifecycle.shutdown_with(|| {});
        assert!(lifecycle.should_stop());
        assert!(lifecycle.services().is_none());
        assert!(lifecycle.adopt(owned_set_for_test(Vec::new())).is_err());
        assert!(!lifecycle.release_update_handoff());
        assert!(!lifecycle.restore_pending());
    }

    #[test]
    fn repeated_handoffs_keep_service_ownership_single_valued() {
        let lifecycle = ServiceLifecycle::new();
        for _ in 0..3 {
            assert!(lifecycle.adopt(owned_set_for_test(Vec::new())).is_ok());
            assert!(lifecycle.begin_update_handoff_with(|| {}));
            assert!(lifecycle.release_update_handoff());
            assert!(
                lifecycle.services().is_none(),
                "the caller restores the runtime, the owner never starts one"
            );
            assert!(lifecycle.restore_pending());
        }
    }

    #[cfg(windows)]
    pub(crate) mod windows_reclaim {
        use super::*;
        use command_group::CommandGroup;
        use std::process::{Command, Stdio};
        use std::time::Duration;

        // A real child process that stays alive after its `cmd /c` parent
        // exits, so the reclaim has to reach the job's descendants too.
        pub(crate) fn hold_process_owned() -> command_group::GroupChild {
            let mut command = Command::new("cmd");
            command
                .args(["/c", "ping", "-n", "60", "127.0.0.1"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            command.group_spawn().expect("spawn owned service fixture")
        }

        pub(crate) fn hold_process_independent() -> std::process::Child {
            let mut command = Command::new("cmd");
            command
                .args(["/c", "ping", "-n", "60", "127.0.0.1"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            command.spawn().expect("spawn independent fixture")
        }

        pub(crate) fn process_is_alive(pid: u32) -> bool {
            let output = Command::new("tasklist")
                .args(["/FI", &format!("PID eq {pid}"), "/NH", "/FO", "CSV"])
                .output()
                .expect("tasklist runs");
            String::from_utf8_lossy(&output.stdout).contains(&pid.to_string())
        }

        pub(crate) fn wait_until(condition: impl Fn() -> bool, what: &str) {
            for _ in 0..200 {
                if condition() {
                    return;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            panic!("timed out waiting for {what}");
        }

        #[test]
        fn an_update_handoff_reclaims_the_owned_group_and_its_descendants() {
            let lifecycle = ServiceLifecycle::new();
            let owned = hold_process_owned();
            let owned_pid = owned.id();
            let mut independent = hold_process_independent();
            let independent_pid = independent.id();

            assert!(lifecycle.adopt(owned_set_for_test(vec![owned])).is_ok());
            assert!(process_is_alive(owned_pid));
            assert!(lifecycle.begin_update_handoff_with(|| {}));
            wait_until(
                || !process_is_alive(owned_pid),
                "the owned service group to exit",
            );

            // A process this App never started is not in the owned set: a
            // CLI-owned listener on its own port keeps running.
            assert!(
                process_is_alive(independent_pid),
                "an independent process must survive the handoff"
            );

            // The replacement never launched: the released handoff owes the
            // runtime back, and the replacement group is the only new owner.
            assert!(lifecycle.release_update_handoff());
            assert!(lifecycle.restore_pending());
            let replacement = hold_process_owned();
            let replacement_pid = replacement.id();
            assert!(lifecycle
                .adopt(owned_set_for_test(vec![replacement]))
                .is_ok());
            assert!(!lifecycle.restore_pending());

            // A repeated update reclaims exactly the replacement group and
            // still spares the independent process.
            assert!(lifecycle.begin_update_handoff_with(|| {}));
            wait_until(
                || !process_is_alive(replacement_pid),
                "the replacement service group to exit",
            );
            assert!(process_is_alive(independent_pid));

            let _ = independent.kill();
            let _ = independent.wait();
        }
    }
}

// The verified-artifact launch failure is a full-journey regression: it drives
// the pinned updater's own HTTP, signature verification and Windows launch
// against a real window plus the ownership rules above. It lives in its own
// file to keep those rules readable.
#[cfg(all(test, windows))]
#[path = "service_lifecycle_windows_update_tests.rs"]
mod windows_update_tests;
