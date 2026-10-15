//! Native window navigation only. No persisted state or task execution effects.
#[cfg(target_os = "macos")]
use std::sync::Arc;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager, Url};

// On macOS Wry's Started is didCommitNavigation; stop retries at commit so a
// slow document can finish. Other engines retain the established single try.
#[derive(Clone, Copy)]
pub(crate) enum NavigationRetry {
    SingleAttempt,
    UntilNativeCommit,
}

pub(crate) struct WorkspaceHandoff {
    boot_url: Url,
    workspace_url: Url,
    last_workspace_url: Option<Url>,
    retry: NavigationRetry,
    pending: bool,
    last_attempt: Option<Instant>,
}

impl WorkspaceHandoff {
    const RETRY_INTERVAL: Duration = Duration::from_secs(2);
    pub(crate) fn new(boot_url: Url, workspace_url: Url, retry: NavigationRetry) -> Self {
        Self {
            boot_url,
            workspace_url,
            last_workspace_url: None,
            retry,
            pending: false,
            last_attempt: None,
        }
    }
    pub(crate) fn reconnect(&mut self) {
        // Explicit runtime repair retains its established workspace-root handoff.
        self.last_workspace_url = None;
        self.pending = true;
        self.last_attempt = None;
    }
    pub(crate) fn page_reached(&mut self, current: &Url) {
        if current.origin() == self.workspace_url.origin() {
            self.last_workspace_url = Some(current.clone());
            self.pending = false;
        } else if current != &self.boot_url {
            self.pending = false;
            self.last_workspace_url = None;
        }
    }
    fn content_lost(&mut self, current: Option<&Url>) -> bool {
        // Capture the last URL once in the termination callback, including SPA
        // history updates. This is not URL polling or a navigation ACK.
        if let Some(current) = current {
            if current.origin() == self.workspace_url.origin() {
                self.last_workspace_url = Some(current.clone());
            } else if current.as_str() != "about:blank" {
                return false;
            }
        }
        if self.last_workspace_url.is_none() {
            return false;
        }
        self.pending = true;
        self.last_attempt = None;
        true
    }
    fn needs_navigation(&mut self, now: Instant) -> bool {
        if !self.pending
            || self.last_attempt.is_some_and(|last| match self.retry {
                NavigationRetry::SingleAttempt => true,
                NavigationRetry::UntilNativeCommit => {
                    now.duration_since(last) < Self::RETRY_INTERVAL
                }
            })
        {
            return false;
        }
        self.last_attempt = Some(now);
        true
    }
    fn destination(&self) -> &Url {
        self.last_workspace_url
            .as_ref()
            .unwrap_or(&self.workspace_url)
    }
    pub(crate) fn reconcile(handoff: &Mutex<Self>, app: &AppHandle) {
        let destination = {
            let mut state = handoff.lock().expect("workspace handoff lock");
            if !state.needs_navigation(Instant::now()) {
                return;
            }
            state.destination().clone()
        };
        // Dispatch after releasing the lock: commit callbacks update this owner.
        if let Some(window) = app.get_webview_window("main") {
            if let Err(error) = window.navigate(destination) {
                eprintln!("LoopX workspace navigation failed: {error}");
            }
        }
    }
}

#[cfg(target_os = "macos")]
pub(crate) fn content_lost(webview: &tauri::Webview) {
    let current = webview.url().ok();
    if webview.label() == "main" {
        if let Some(handoff) = webview.try_state::<Arc<Mutex<WorkspaceHandoff>>>() {
            if handoff
                .lock()
                .expect("workspace handoff lock")
                .content_lost(current.as_ref())
            {
                // Replace Tauri's one-shot reload with the existing supervisor's
                // retry-until-commit. A failed reload must not orphan a blank view.
                return;
            }
        }
    }
    // Preserve framework behavior for boot and any other native window.
    if let Err(error) = webview.reload() {
        eprintln!("LoopX WebContent reload failed: {error}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn failed_workspace_load_retries_until_a_native_commit() {
        let boot: Url = "tauri://localhost/".parse().unwrap();
        let target: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        let mut handoff = WorkspaceHandoff::new(
            boot.clone(),
            target.clone(),
            NavigationRetry::UntilNativeCommit,
        );
        let now = Instant::now();
        assert!(!handoff.needs_navigation(now));
        handoff.reconnect();
        assert!(handoff.needs_navigation(now));
        // Queuing a request, or finishing the boot page, does not acknowledge
        // the workspace. Failed provisional loads emit no finished event.
        handoff.page_reached(&boot);
        assert!(!handoff.needs_navigation(now));
        let retry = now + WorkspaceHandoff::RETRY_INTERVAL;
        assert!(handoff.needs_navigation(retry));
        let opened: Url = "http://127.0.0.1:8767/chat/?goal=example".parse().unwrap();
        handoff.page_reached(&opened);
        assert!(!handoff.needs_navigation(retry + WorkspaceHandoff::RETRY_INTERVAL));
    }

    #[test]
    fn unrelated_completed_pages_cancel_the_handoff() {
        let boot: Url = "tauri://localhost/".parse().unwrap();
        let target: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        for page in [
            "https://example.com/",
            "http://127.0.0.1:8766/chat/",
            "tauri://another-app/",
            "tauri://localhost/another-page.html",
        ] {
            let mut handoff = WorkspaceHandoff::new(
                boot.clone(),
                target.clone(),
                NavigationRetry::UntilNativeCommit,
            );
            handoff.reconnect();
            handoff.page_reached(&page.parse().unwrap());
            assert!(!handoff.needs_navigation(Instant::now()), "{page}");
        }
    }

    #[test]
    fn repair_reloads_once_and_rearms_a_failed_handoff_without_reading_webkit_url() {
        let boot: Url = "http://tauri.localhost/".parse().unwrap();
        let target: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        let mut handoff =
            WorkspaceHandoff::new(boot, target.clone(), NavigationRetry::UntilNativeCommit);
        let now = Instant::now();
        handoff.reconnect();
        assert!(handoff.needs_navigation(now));
        handoff.page_reached(&target);
        assert!(!handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL));
        handoff.reconnect();
        assert!(handoff.needs_navigation(now));
        assert!(handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL));
        handoff.page_reached(&target);
        assert!(!handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL * 2));
    }

    #[test]
    fn committed_workspace_load_is_not_interrupted_while_the_document_finishes() {
        let boot: Url = "tauri://localhost/".parse().unwrap();
        let target: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        let mut handoff =
            WorkspaceHandoff::new(boot, target.clone(), NavigationRetry::UntilNativeCommit);
        let now = Instant::now();
        handoff.reconnect();
        assert!(handoff.needs_navigation(now));
        // The real macOS Started callback is a native commit, not a queued or
        // provisional navigation. A slow response body must get time to finish.
        handoff.page_reached(&target);
        assert!(!handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL));
        assert!(!handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL * 10));
    }

    #[test]
    fn other_engines_keep_one_navigation_attempt_until_explicit_repair() {
        let boot: Url = "http://tauri.localhost/".parse().unwrap();
        let target: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        let mut handoff = WorkspaceHandoff::new(boot, target, NavigationRetry::SingleAttempt);
        let now = Instant::now();
        handoff.reconnect();
        assert!(handoff.needs_navigation(now));
        assert!(!handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL * 10));
        handoff.reconnect();
        assert!(handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL * 10));
    }

    #[test]
    fn content_loss_restores_spa_route_and_waits_for_real_commit() {
        let target: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        let mut h = WorkspaceHandoff::new(
            "tauri://localhost/".parse().unwrap(),
            target.clone(),
            NavigationRetry::UntilNativeCommit,
        );
        h.page_reached(&target);
        let route: Url = "http://127.0.0.1:8767/chat/?goalId=example&view=tasks#selected"
            .parse()
            .unwrap();
        assert!(h.content_lost(Some(&route)));
        assert_eq!(h.destination(), &route);
        let now = Instant::now();
        assert!(h.needs_navigation(now));
        assert!(!h.needs_navigation(now + Duration::from_millis(200)));
        // A provisional failure has no commit callback. Keep the same route.
        assert!(h.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL));
        assert_eq!(h.destination(), &route);
        h.page_reached(&route);
        assert!(!h.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL * 10));
        // A second independent loss gets its own recovery, without service work.
        assert!(h.content_lost(None));
        assert!(h.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL * 11));
    }
    #[test]
    fn content_loss_never_adopts_foreign_or_boot_destinations() {
        let boot: Url = "tauri://localhost/".parse().unwrap();
        let target: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        let mut h = WorkspaceHandoff::new(
            boot.clone(),
            target.clone(),
            NavigationRetry::UntilNativeCommit,
        );
        assert!(!h.content_lost(None));
        assert!(!h.content_lost(Some(&boot)));
        h.page_reached(&target);
        for foreign in [
            "https://example.com/",
            "http://127.0.0.1:8768/chat/",
            "file:///tmp/example",
            "codex://threads/example",
        ] {
            assert!(!h.content_lost(Some(&foreign.parse().unwrap())));
            assert!(!h.needs_navigation(Instant::now()));
            assert_eq!(h.destination(), &target);
        }
        assert!(h.content_lost(Some(&"about:blank".parse().unwrap())));
        assert_eq!(h.destination(), &target);
    }
}
