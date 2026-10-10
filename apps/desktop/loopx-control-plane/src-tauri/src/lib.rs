mod bundled_runtime;
mod maintenance;
mod runtime_selection;
mod service_endpoints;
mod services;
mod update_backup;

use services::ServiceSet;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
use std::time::{Duration, Instant};
use tauri::{
    ipc::CapabilityBuilder, webview::PageLoadEvent, AppHandle, Manager, RunEvent, Url, WebviewUrl,
    WebviewWindowBuilder,
};
use tauri_plugin_notification::NotificationExt;

const APP_IDENTIFIER: &str = "io.loopx.control-plane";

// A ready listener does not acknowledge a queued WebKit navigation. On macOS,
// Wry's Started event is WKNavigationDelegate.didCommitNavigation: once the
// workspace commits, let it finish without interrupting slow document loads.
// Do not poll window.url(): WebKit can have no URL after a provisional failure.
// This state belongs only to the native shell, with no persisted protocol.
#[derive(Clone, Copy)]
enum NavigationRetry {
    SingleAttempt,
    UntilNativeCommit,
}

struct WorkspaceHandoff {
    boot_url: Url,
    retry: NavigationRetry,
    pending: bool,
    last_attempt: Option<Instant>,
}

impl WorkspaceHandoff {
    const RETRY_INTERVAL: Duration = Duration::from_secs(2);

    fn new(boot_url: Url, retry: NavigationRetry) -> Self {
        Self {
            boot_url,
            retry,
            pending: false,
            last_attempt: None,
        }
    }

    fn reconnect(&mut self) {
        self.pending = true;
        self.last_attempt = None;
    }

    fn page_reached(&mut self, current: &Url, target: &Url) {
        // A native commit/completion at the workspace ACKs the handoff. An
        // unrelated established page cancels it; boot cannot ACK the workspace.
        if current.origin() == target.origin() || current != &self.boot_url {
            self.pending = false;
        }
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

    fn reconcile(handoff: &Mutex<Self>, app: &AppHandle, target: &Url) {
        // Release the state lock before dispatching a native effect: page-load
        // callbacks run on the UI thread and update that same state.
        if !handoff
            .lock()
            .expect("workspace handoff lock")
            .needs_navigation(Instant::now())
        {
            return;
        }
        if let Some(window) = app.get_webview_window("main") {
            if let Err(error) = window.navigate(target.clone()) {
                eprintln!("LoopX workspace navigation failed: {error}");
            }
        }
    }
}

fn boot_url(app: &AppHandle) -> Url {
    // WebviewUrl::App("index.html") resolves to Tauri's root URL; the Windows
    // WebView2 transport uses its HTTP alias. Development uses the configured
    // frontend. Resolve this from configuration, before any WebKit URL exists.
    if cfg!(dev) {
        if let Some(url) = app.config().build.dev_url.as_ref() {
            return url.clone();
        }
    }
    if cfg!(target_os = "windows") {
        "http://tauri.localhost/".parse().expect("valid boot URL")
    } else {
        "tauri://localhost/".parse().expect("valid boot URL")
    }
}

fn maintenance_origin(url: &Url) -> String {
    // Custom-protocol IPC carries the HTTP Origin header (no /chat/ path).
    // postMessage carries the page URL. Both must match the same exact origin.
    url.origin().ascii_serialization()
}

// Supervisor failures are either stable machine codes emitted by the
// maintenance state machine (runtime_setup_required, runtime_install_exit_2,
// ...) or human-readable service diagnostics. Only a stable code is safe to
// echo verbatim into the boot surface, where it names the recovery panel's
// actionable diagnostics instead of a misleading fixed message.
fn boot_failure_message(error: &str) -> String {
    // The pairing decision is not a failure: the window is waiting for the
    // operator to choose between updating the App and aligning the CLI.
    if error == "runtime_pairing_required" {
        return "请选择继续使用已安装的运行时，或更新 App；选择后同一个窗口会继续打开工作区。"
            .to_string();
    }
    let is_stable_code = !error.is_empty()
        && error.chars().all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '_'
        });
    if is_stable_code {
        format!(
            "本地服务暂时无法启动，请检查安装或端口占用。（错误码 {error}，详见恢复与更新面板）"
        )
    } else {
        "本地服务暂时无法启动，请检查安装或端口占用。".to_string()
    }
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

// A destination request comes from the trusted workspace, not a new privileged
// WebView. Send only web destinations to the system browser; retain the main
// window's origin restriction and never launch custom handlers or local files.
fn open_web_destination(
    url: &Url,
    launch: impl FnOnce(&str) -> std::io::Result<()>,
) -> tauri::webview::NewWindowResponse<tauri::Wry> {
    if matches!(url.scheme(), "http" | "https") {
        if let Err(error) = launch(url.as_str()) {
            eprintln!("LoopX could not open web destination: {error}");
        }
    }
    tauri::webview::NewWindowResponse::Deny
}

fn workspace_navigation(
    url: &Url,
    origin: &Url,
    launch: impl FnOnce(&str) -> std::io::Result<()>,
) -> bool {
    if url.scheme() == "tauri" || url.origin() == origin.origin() {
        return true;
    }
    // WKWebView evaluates navigation policy before its new-window delegate.
    // Hand external web links off here while keeping them out of this WebView.
    open_web_destination(url, launch);
    false
}

pub fn run() {
    // Release builds load the versioned LoopX Chat workspace that ships inside
    // the installed `loopx` release, so `loopx update` refreshes the frontend
    // and backend together instead of reusing a separately built asset bundle.
    let endpoints = service_endpoints::ServiceEndpoints::allocate(!cfg!(dev))
        .expect("could not allocate LoopX loopback endpoints");
    #[cfg(dev)]
    let web_origin = "http://127.0.0.1:5173".to_string();
    #[cfg(not(dev))]
    let web_origin = endpoints.workspace_origin();
    let services = Arc::new(Mutex::new(None::<ServiceSet>));
    let services_for_setup = Arc::clone(&services);
    let navigation_origin: Url = web_origin.parse().expect("valid desktop origin");
    let shutting_down = Arc::new(AtomicBool::new(false));
    let shutting_down_for_setup = Arc::clone(&shutting_down);

    let builder = tauri::Builder::default()
        .manage(maintenance::Maintenance::default())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            maintenance::desktop_update,
            maintenance::desktop_update_status
        ])
        .plugin(
            tauri_plugin_single_instance::Builder::new()
                .dbus_id(APP_IDENTIFIER)
                .callback(|app, _args, _cwd| show_main_window(app))
                .build(),
        )
        .plugin(tauri_plugin_notification::init())
        .setup(move |app| {
            let origin: Url = web_origin.parse()?;
            app.add_capability(
                CapabilityBuilder::new("desktop-loopx-chat")
                    .remote(maintenance_origin(&origin))
                    .permission("allow-desktop-update")
                    .permission("allow-desktop-update-status")
                    .window("main"),
            )?;

            // Other engines report Started before commit. Preserve their
            // existing one-request handoff rather than treating it as WebKit's
            // acknowledgement or introducing an unqualified retry policy.
            let retry = if cfg!(target_os = "macos") {
                NavigationRetry::UntilNativeCommit
            } else {
                NavigationRetry::SingleAttempt
            };
            let handoff = Arc::new(Mutex::new(WorkspaceHandoff::new(
                boot_url(app.handle()),
                retry,
            )));
            let handoff_for_load = Arc::clone(&handoff);
            let origin_for_load = origin.clone();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("LoopX")
                .inner_size(1280.0, 820.0)
                .min_inner_size(960.0, 640.0)
                .on_page_load(move |_window, payload| {
                    match payload.event() {
                        PageLoadEvent::Started if cfg!(target_os = "macos") => {}
                        PageLoadEvent::Finished => {}
                        _ => return,
                    }
                    handoff_for_load
                        .lock()
                        .expect("workspace handoff lock")
                        .page_reached(payload.url(), &origin_for_load);
                })
                .on_navigation(move |url| {
                    workspace_navigation(url, &navigation_origin, |destination| {
                        open::that_detached(destination)
                    })
                })
                .on_new_window(|url, _features| {
                    open_web_destination(&url, |destination| open::that_detached(destination))
                })
                .build()?;

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                // Runtime preparation belongs to the same live supervisor as
                // service startup. A failed install must not strand this window
                // after a later repair, reload, or external runtime correction.
                while !shutting_down_for_setup.load(Ordering::Acquire) {
                    {
                        let mut current = services_for_setup.lock().expect("service state lock");
                        if current.is_some() {
                            if maintenance::reconnect_requested(&handle) {
                                // Runtime repair reuses this supervisor even
                                // after the workspace has already been opened.
                                // Drop only processes owned by this App.
                                *current = None;
                                handoff.lock().expect("workspace handoff lock").reconnect();
                            } else {
                                drop(current);
                                WorkspaceHandoff::reconcile(&handoff, &handle, &origin);
                                std::thread::sleep(std::time::Duration::from_millis(200));
                                continue;
                            }
                        }
                    }
                    match maintenance::start_services(&handle, &endpoints) {
                        Ok(None) => {
                            std::thread::sleep(std::time::Duration::from_millis(200));
                            continue;
                        }
                        Ok(Some(mut started)) => {
                            if shutting_down_for_setup.load(Ordering::Acquire) {
                                started.stop();
                                return;
                            }
                            let healed = started.healed;
                            *services_for_setup.lock().expect("service state lock") = Some(started);
                            handoff.lock().expect("workspace handoff lock").reconnect();
                            WorkspaceHandoff::reconcile(&handoff, &handle, &origin);
                            if healed {
                                let _ = handle
                                    .notification()
                                    .builder()
                                    .title("LoopX")
                                    .body("已自动升级到当前 LoopX 版本，服务已重启。")
                                    .show();
                            }
                        }
                        Err(error) => {
                            let message = boot_failure_message(&error);
                            eprintln!("LoopX service error: {error}");
                            if let Some(window) = handle.get_webview_window("main") {
                                if let Ok(encoded) = serde_json::to_string(&message) {
                                    let _ =
                                        window.eval(format!("window.loopxBootFailed({encoded})"));
                                }
                            }
                            // A blocked runtime choice is not work to repeat
                            // every two seconds. Keep recovery responsive while
                            // bounding Core doctor probes of an unchanged CLI.
                            let rounds = if matches!(
                                error.as_str(),
                                "runtime_identity_unavailable"
                                    | "runtime_pairing_required"
                                    | "runtime_selection_invalid"
                                    | "runtime_selection_unavailable"
                            ) {
                                150
                            } else {
                                10
                            };
                            for _ in 0..rounds {
                                if shutting_down_for_setup.load(Ordering::Acquire) {
                                    return;
                                }
                                if maintenance::reconnect_requested(&handle) {
                                    break;
                                }
                                std::thread::sleep(std::time::Duration::from_millis(200));
                            }
                            if let Some(window) = handle.get_webview_window("main") {
                                let _ = window.eval("window.loopxBootRetrying()");
                            }
                        }
                    }
                }
            });
            Ok(())
        });

    let app = builder
        .build(tauri::generate_context!())
        .expect("failed to build LoopX desktop shell");
    app.run(move |_app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            shutting_down.store(true, Ordering::Release);
            if let Ok(mut guard) = services.lock() {
                if let Some(current) = guard.as_mut() {
                    current.stop();
                }
                *guard = None;
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::{open_web_destination, workspace_navigation, NavigationRetry, WorkspaceHandoff};
    use std::time::Instant;
    use tauri::Url;

    #[test]
    fn requested_web_destinations_open_once_without_creating_a_native_child() {
        for destination in [
            "https://example.org/form?lang=en&view=all",
            "http://127.0.0.1:8767/chat/?reportSessionId=example",
        ] {
            let url: Url = destination.parse().unwrap();
            let mut opened = Vec::new();
            let response = open_web_destination(&url, |value| {
                opened.push(value.to_string());
                Ok(())
            });
            assert_eq!(opened, [url.as_str()]);
            assert!(matches!(response, tauri::webview::NewWindowResponse::Deny));
        }
    }

    #[test]
    fn new_window_rejects_non_web_handlers_and_keeps_launcher_failures_contained() {
        for destination in [
            "file:///tmp/example.txt",
            "javascript:alert(1)",
            "data:text/html,example",
            "mailto:someone@example.org",
            "tauri://localhost/",
        ] {
            let response = open_web_destination(&destination.parse().unwrap(), |_| {
                panic!("non-web destination must never reach a system handler")
            });
            assert!(matches!(response, tauri::webview::NewWindowResponse::Deny));
        }
        let response = open_web_destination(&"https://example.org/".parse().unwrap(), |_| {
            Err(std::io::Error::other("browser unavailable"))
        });
        assert!(matches!(response, tauri::webview::NewWindowResponse::Deny));
    }

    #[test]
    fn navigation_keeps_the_workspace_fence_and_hands_external_web_links_off_once() {
        let origin: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        for destination in [
            "tauri://localhost/",
            "http://127.0.0.1:8767/chat/?goalId=example",
        ] {
            assert!(workspace_navigation(
                &destination.parse().unwrap(),
                &origin,
                |_| { panic!("workspace navigation must not leave the App") }
            ));
        }
        let external: Url = "https://example.org/form?lang=en&view=all".parse().unwrap();
        let mut opened = Vec::new();
        let allowed = workspace_navigation(&external, &origin, |value| {
            opened.push(value.to_string());
            Ok(())
        });
        // macOS never reaches the new-window delegate after this rejection.
        assert!(!allowed);
        assert_eq!(opened, [external.as_str()]);
        for destination in [
            "file:///tmp/example.txt",
            "javascript:alert(1)",
            "mailto:someone@example.org",
        ] {
            assert!(!workspace_navigation(
                &destination.parse().unwrap(),
                &origin,
                |_| { panic!("non-web navigation must not reach a system handler") }
            ));
        }
        assert!(!workspace_navigation(&external, &origin, |_| {
            Err(std::io::Error::other("browser unavailable"))
        }));
    }

    #[test]
    fn failed_workspace_load_retries_until_a_native_commit() {
        let boot: Url = "tauri://localhost/".parse().unwrap();
        let target: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        let mut handoff = WorkspaceHandoff::new(boot.clone(), NavigationRetry::UntilNativeCommit);
        let now = Instant::now();
        assert!(!handoff.needs_navigation(now));
        handoff.reconnect();
        assert!(handoff.needs_navigation(now));
        // Queuing a request, or finishing the boot page, does not acknowledge
        // the workspace. Failed provisional loads emit no finished event.
        handoff.page_reached(&boot, &target);
        assert!(!handoff.needs_navigation(now));
        let retry = now + WorkspaceHandoff::RETRY_INTERVAL;
        assert!(handoff.needs_navigation(retry));
        let opened: Url = "http://127.0.0.1:8767/chat/?goal=example".parse().unwrap();
        handoff.page_reached(&opened, &target);
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
            let mut handoff =
                WorkspaceHandoff::new(boot.clone(), NavigationRetry::UntilNativeCommit);
            handoff.reconnect();
            handoff.page_reached(&page.parse().unwrap(), &target);
            assert!(!handoff.needs_navigation(Instant::now()), "{page}");
        }
    }

    #[test]
    fn repair_reloads_once_and_rearms_a_failed_handoff_without_reading_webkit_url() {
        let boot: Url = "http://tauri.localhost/".parse().unwrap();
        let target: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        let mut handoff = WorkspaceHandoff::new(boot, NavigationRetry::UntilNativeCommit);
        let now = Instant::now();
        handoff.reconnect();
        assert!(handoff.needs_navigation(now));
        handoff.page_reached(&target, &target);
        assert!(!handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL));
        handoff.reconnect();
        assert!(handoff.needs_navigation(now));
        assert!(handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL));
        handoff.page_reached(&target, &target);
        assert!(!handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL * 2));
    }

    #[test]
    fn committed_workspace_load_is_not_interrupted_while_the_document_finishes() {
        let boot: Url = "tauri://localhost/".parse().unwrap();
        let target: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        let mut handoff = WorkspaceHandoff::new(boot, NavigationRetry::UntilNativeCommit);
        let now = Instant::now();
        handoff.reconnect();
        assert!(handoff.needs_navigation(now));
        // The real macOS Started callback is a native commit, not a queued or
        // provisional navigation. A slow response body must get time to finish.
        handoff.page_reached(&target, &target);
        assert!(!handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL));
        assert!(!handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL * 10));
    }

    #[test]
    fn other_engines_keep_one_navigation_attempt_until_explicit_repair() {
        let boot: Url = "http://tauri.localhost/".parse().unwrap();
        let mut handoff = WorkspaceHandoff::new(boot, NavigationRetry::SingleAttempt);
        let now = Instant::now();
        handoff.reconnect();
        assert!(handoff.needs_navigation(now));
        assert!(!handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL * 10));
        handoff.reconnect();
        assert!(handoff.needs_navigation(now + WorkspaceHandoff::RETRY_INTERVAL * 10));
    }

    #[test]
    fn maintenance_acl_accepts_both_transports_only_on_the_app_origin() {
        use tauri::utils::acl::RemoteUrlPattern;
        let page: tauri::Url = "http://127.0.0.1:49123/chat/".parse().unwrap();
        let old: RemoteUrlPattern = page.to_string().parse().unwrap();
        assert!(!old.test(&"http://127.0.0.1:49123".parse().unwrap()));
        let pattern: RemoteUrlPattern = super::maintenance_origin(&page).parse().unwrap();
        for allowed in [
            "http://127.0.0.1:49123",
            "http://127.0.0.1:49123/chat/?goal=x",
        ] {
            assert!(pattern.test(&allowed.parse().unwrap()), "{allowed}");
        }
        for denied in [
            "http://127.0.0.1:8767/chat/",
            "http://localhost:49123/chat/",
            "https://127.0.0.1:49123/chat/",
            "https://example.com/chat/",
        ] {
            assert!(!pattern.test(&denied.parse().unwrap()), "{denied}");
        }
    }

    #[test]
    fn startup_surface_is_visible_and_names_automatic_recovery() {
        let html = include_str!("../../static/index.html");
        let style = include_str!("../../static/boot.css");
        let script = include_str!("../../static/boot.js");

        assert!(html.contains("正在打开 LoopX"));
        assert!(html.contains("aria-busy=\"true\""));
        assert!(html.contains("aria-live=\"polite\""));
        assert!(html.contains("class=\"status-dots\""));
        assert!(style.contains("@keyframes boot-progress"));
        assert!(style.contains("@keyframes mark-breathe"));
        assert!(style.contains("prefers-reduced-motion: reduce"));
        assert!(style.contains("main[data-state=\"error\"] .progress::after"));
        assert!(style.contains("--warning: #f5a623"));
        assert!(script.contains("desktop_update_status"));
        assert!(script.contains("window.loopxBootRetrying"));
        // Services connect concurrently, so the phase names the loopback set
        // until one connection outlives its peer and can be named on its own.
        assert!(script.contains("正在打开工作区"));
        // The boot surface must derive its error projection from the polled
        // snapshot itself and name the known fresh-Mac installer failure.
        assert!(script.contains("runtime_install_exit_2"));
        assert!(script.contains("desktop_recovery_diagnostics_v2"));
    }

    #[test]
    fn boot_failure_message_appends_stable_codes_only() {
        use super::boot_failure_message;
        // The pairing decision names the forward choices instead of the
        // generic startup failure text.
        let pairing = boot_failure_message("runtime_pairing_required");
        assert!(pairing.contains("已安装的运行时"));
        assert!(pairing.contains("更新 App"));
        assert!(!pairing.contains("错误码"));
        assert_eq!(
            boot_failure_message("runtime_install_exit_2"),
            "本地服务暂时无法启动，请检查安装或端口占用。（错误码 runtime_install_exit_2，详见恢复与更新面板）"
        );
        assert_eq!(
            boot_failure_message("runtime_setup_required"),
            "本地服务暂时无法启动，请检查安装或端口占用。（错误码 runtime_setup_required，详见恢复与更新面板）"
        );
        // Human-readable service diagnostics and empty errors keep the fixed
        // message; free-form text is never echoed into the boot surface.
        assert_eq!(
            boot_failure_message("port 8766 is occupied by a service that is not LoopX status"),
            "本地服务暂时无法启动，请检查安装或端口占用。"
        );
        assert_eq!(
            boot_failure_message(""),
            "本地服务暂时无法启动，请检查安装或端口占用。"
        );
    }
}
