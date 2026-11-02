mod bundled_runtime;
#[cfg(target_os = "macos")]
mod macos_update;
mod maintenance;
mod prepared_update;
mod runtime_ancestry;
mod runtime_selection;
mod service_endpoints;
mod service_lifecycle;
#[cfg(target_os = "macos")]
mod service_lifetime;
mod services;
mod update_backup;
mod upgrade_request;
mod workspace_handoff;
mod workspace_location;

use service_lifecycle::ServiceLifecycle;
use std::sync::{Arc, Mutex};
use tauri::{
    ipc::CapabilityBuilder, webview::PageLoadEvent, AppHandle, Manager, RunEvent, Url, WebviewUrl,
    WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_notification::NotificationExt;
use workspace_handoff::{NavigationRetry, WorkspaceHandoff};

const APP_IDENTIFIER: &str = "io.loopx.control-plane";

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
    if error == "chat_work_pending" {
        return "管家正在处理请求，完成后会自动连接工作区。".to_string();
    }
    if matches!(error, "chat_state_unverified" | "chat_service_unverified") {
        return "正在等待安全连接管家服务；请展开「恢复与更新」查看诊断。".to_string();
    }
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
    workspace_origin: &Url,
    boot_origin: &Url,
    launch: impl FnOnce(&str) -> std::io::Result<()>,
) -> bool {
    // macOS serves the App's bundled boot surface over `tauri://localhost`, and
    // the scheme check below covers it. Windows serves the same bundled page
    // over the WebView2 transport alias `http://tauri.localhost`, so that boot
    // origin stays inside the fence too. Opaque origins never compare equal,
    // which keeps `file://` and other custom protocols outside the fence.
    if url.scheme() == "tauri"
        || url.origin() == workspace_origin.origin()
        || url.origin() == boot_origin.origin()
    {
        return true;
    }
    // WKWebView evaluates navigation policy before its new-window delegate.
    // Hand external web links off here while keeping them out of this WebView.
    open_web_destination(url, launch);
    false
}

pub fn run() {
    #[cfg(target_os = "macos")]
    service_lifetime::run_if_requested();

    // Release builds load the versioned LoopX Chat workspace that ships inside
    // the installed `loopx` release, so `loopx update` refreshes the frontend
    // and backend together instead of reusing a separately built asset bundle.
    // App and browser are clients of the same machine services. Private
    // endpoints are an explicit developer/QA choice, never a second default
    // Chat store created merely by opening a release window.
    let private_endpoints = std::env::var_os("LOOPX_DESKTOP_PRIVATE_ENDPOINTS");
    let endpoints = service_endpoints::ServiceEndpoints::for_window(private_endpoints.as_deref())
        .expect("could not allocate LoopX loopback endpoints");
    #[cfg(dev)]
    let web_origin = "http://127.0.0.1:5173".to_string();
    #[cfg(not(dev))]
    let web_origin = endpoints.workspace_origin();
    let navigation_origin: Url = web_origin.parse().expect("valid desktop origin");
    let lifecycle = ServiceLifecycle::new();
    let lifecycle_for_setup = lifecycle.clone();

    let builder = tauri::Builder::default()
        .manage(maintenance::Maintenance::default())
        .manage(lifecycle.clone())
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
        .on_window_event(|window, event| {
            if window.label() == "main" && matches!(event, WindowEvent::CloseRequested { .. }) {
                workspace_location::remember_current(window.app_handle());
            }
        })
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
            let boot_origin = boot_url(app.handle());
            let handoff = Arc::new(Mutex::new(WorkspaceHandoff::new(
                boot_origin.clone(),
                workspace_location::launch_destination(app.handle(), &origin),
                retry,
            )));
            app.manage(Arc::clone(&handoff));
            let handoff_for_load = Arc::clone(&handoff);
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
                        .page_reached(payload.url());
                })
                .on_navigation(move |url| {
                    workspace_navigation(url, &navigation_origin, &boot_origin, |destination| {
                        open::that_detached(destination)
                    })
                })
                .on_new_window(|url, _features| {
                    open_web_destination(&url, |destination| open::that_detached(destination))
                })
                .build()?;

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let lifecycle = lifecycle_for_setup;
                // Runtime preparation belongs to the same live supervisor as
                // service startup. A failed install must not strand this window
                // after a later repair, reload, or external runtime correction.
                while !lifecycle.should_stop() {
                    // The App-update handoff owns the runtime until it either
                    // replaces this process or returns; while it is active the
                    // supervisor neither starts nor recovers services.
                    if lifecycle.handoff_active() {
                        std::thread::sleep(std::time::Duration::from_millis(200));
                        continue;
                    }
                    {
                        let mut current = lifecycle.services();
                        if current.is_some() {
                            if maintenance::reconnect_requested(&handle) {
                                // Runtime repair reuses this supervisor even
                                // after the workspace has already been opened.
                                // Drop only processes owned by this App.
                                *current = None;
                                handoff.lock().expect("workspace handoff lock").reconnect();
                            } else {
                                maintenance::recover_services(
                                    &handle,
                                    current.as_mut().expect("connected services"),
                                    &endpoints,
                                );
                                drop(current);
                                WorkspaceHandoff::reconcile(&handoff, &handle);
                                std::thread::sleep(std::time::Duration::from_millis(200));
                                continue;
                            }
                        }
                    }
                    let restoring = lifecycle.restore_pending();
                    let started = if restoring {
                        // A released handoff (the installer never launched)
                        // already stopped this window's own services. Bring the
                        // same runtime back without re-announcing phases: the
                        // failure snapshot on screen is the state the operator
                        // is retrying from, and it must not be overwritten.
                        maintenance::restore_services_after_update_failure(&handle, &endpoints)
                    } else {
                        maintenance::start_services(&handle, &endpoints)
                    };
                    match started {
                        Ok(None) => {
                            std::thread::sleep(std::time::Duration::from_millis(200));
                            continue;
                        }
                        Ok(Some(started)) => {
                            let healed = started.healed;
                            if let Err(mut returned) = lifecycle.adopt(started) {
                                // The window closed or a new handoff claimed
                                // the runtime while this start was in flight:
                                // stop the group this thread started instead
                                // of stranding it, and keep owing whatever the
                                // owner recorded.
                                returned.stop();
                                continue;
                            }
                            if restoring {
                                // The failure snapshot owns this transition;
                                // the workspace handoff already settled and
                                // must not replay an old selection.
                                continue;
                            }
                            handoff
                                .lock()
                                .expect("workspace handoff lock")
                                .services_ready();
                            WorkspaceHandoff::reconcile(&handoff, &handle);
                            maintenance::start_background_update(&handle);
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
                            if restoring {
                                // The failure diagnostics stay on screen;
                                // retry the reclaimed runtime at the
                                // supervisor's pace without re-announcing a
                                // phase.
                                eprintln!(
                                    "LoopX service restore after failed App update deferred: {error}"
                                );
                                for _ in 0..10 {
                                    if lifecycle.should_stop() || lifecycle.handoff_active() {
                                        break;
                                    }
                                    std::thread::sleep(std::time::Duration::from_millis(200));
                                }
                                continue;
                            }
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
                                if lifecycle.should_stop() {
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

    #[cfg(target_os = "macos")]
    let builder = builder.on_web_content_process_terminate(workspace_handoff::content_lost);

    let app = builder
        .build(tauri::generate_context!())
        .expect("failed to build LoopX desktop shell");
    app.run(move |app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            // Save the route and reclaim this App's own services through the
            // same owner the App-update handoff uses.
            lifecycle.shutdown(app);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::{open_web_destination, workspace_navigation};
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
        let workspace: Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        // macOS reaches the bundled boot surface over the custom protocol;
        // Windows serves the same page over the WebView2 transport alias.
        for (boot, boot_page) in [
            ("tauri://localhost/", "tauri://localhost/index.html"),
            (
                "http://tauri.localhost/",
                "http://tauri.localhost/index.html",
            ),
        ] {
            let boot: Url = boot.parse().unwrap();
            for destination in [boot_page, "http://127.0.0.1:8767/chat/?goalId=example"] {
                assert!(workspace_navigation(
                    &destination.parse().unwrap(),
                    &workspace,
                    &boot,
                    |_| { panic!("workspace navigation must not leave the App") }
                ));
            }
        }
        let boot: Url = "http://tauri.localhost/".parse().unwrap();
        let external: Url = "https://example.org/form?lang=en&view=all".parse().unwrap();
        let mut opened = Vec::new();
        let allowed = workspace_navigation(&external, &workspace, &boot, |value| {
            opened.push(value.to_string());
            Ok(())
        });
        // macOS never reaches the new-window delegate after this rejection.
        assert!(!allowed);
        assert_eq!(opened, [external.as_str()]);
        // The transport alias is accepted only as an exact origin: a lookalike
        // host, port, or scheme stays an external web destination and is
        // handed off once more.
        for destination in [
            "http://tauri.localhost.evil.example/index.html",
            "http://elsewhere.localhost/index.html",
            "http://tauri.localhost:8080/index.html",
            "https://tauri.localhost/index.html",
        ] {
            let destination: Url = destination.parse().unwrap();
            let mut opened = Vec::new();
            assert!(!workspace_navigation(
                &destination,
                &workspace,
                &boot,
                |value| {
                    opened.push(value.to_string());
                    Ok(())
                }
            ));
            assert_eq!(opened, [destination.as_str()]);
        }
        for destination in [
            "file:///tmp/example.txt",
            "javascript:alert(1)",
            "mailto:someone@example.org",
        ] {
            assert!(!workspace_navigation(
                &destination.parse().unwrap(),
                &workspace,
                &boot,
                |_| { panic!("non-web navigation must not reach a system handler") }
            ));
        }
        assert!(!workspace_navigation(&external, &workspace, &boot, |_| {
            Err(std::io::Error::other("browser unavailable"))
        }));
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
        // The Windows release ships no bundled-runtime repair and no rollback,
        // so fallback copy for states without a dedicated code must switch to
        // wording that avoids those controls when repair is unavailable.
        assert!(
            script.contains("!bundledRepairAvailable && Object.hasOwn(unrepairedFallback, phase)")
        );
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
