mod bundled_runtime;
mod maintenance;
mod services;
mod update_backup;

use services::ServiceSet;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
use tauri::{
    ipc::CapabilityBuilder, AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_notification::NotificationExt;

const APP_IDENTIFIER: &str = "io.loopx.control-plane";

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
        return "本机 LoopX 运行时与 App 自带的运行时不一致，请在上方选择「更新 App 与运行时」或「回退 CLI 到本 App 版本」后继续。"
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

pub fn run() {
    // Release builds load the versioned LoopX Chat workspace that ships inside
    // the installed `loopx` release, so `loopx update` refreshes the frontend
    // and backend together instead of reusing a separately built asset bundle.
    #[cfg(dev)]
    let web_origin = "http://127.0.0.1:5173".to_string();
    #[cfg(not(dev))]
    let web_origin = "http://127.0.0.1:8767/chat/".to_string();
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

            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("LoopX")
                .inner_size(1280.0, 820.0)
                .min_inner_size(960.0, 640.0)
                .on_navigation(move |url| {
                    url.scheme() == "tauri" || url.origin() == navigation_origin.origin()
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
                            } else {
                                drop(current);
                                std::thread::sleep(std::time::Duration::from_millis(200));
                                continue;
                            }
                        }
                    }
                    match maintenance::start_services(&handle) {
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
                            if healed {
                                let _ = handle
                                    .notification()
                                    .builder()
                                    .title("LoopX")
                                    .body("已自动升级到当前 LoopX 版本，服务已重启。")
                                    .show();
                            }
                            if let Some(window) = handle.get_webview_window("main") {
                                let _ = window.navigate(origin.clone());
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
                            for _ in 0..10 {
                                if shutting_down_for_setup.load(Ordering::Acquire) {
                                    return;
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
    #[test]
    fn maintenance_acl_accepts_both_transports_only_on_the_app_origin() {
        use tauri::utils::acl::RemoteUrlPattern;
        let page: tauri::Url = "http://127.0.0.1:8767/chat/".parse().unwrap();
        let old: RemoteUrlPattern = page.to_string().parse().unwrap();
        assert!(!old.test(&"http://127.0.0.1:8767".parse().unwrap()));
        let pattern: RemoteUrlPattern = super::maintenance_origin(&page).parse().unwrap();
        for allowed in [
            "http://127.0.0.1:8767",
            "http://127.0.0.1:8767/chat/?goal=x",
        ] {
            assert!(pattern.test(&allowed.parse().unwrap()), "{allowed}");
        }
        for denied in [
            "http://127.0.0.1:8766/chat/",
            "http://localhost:8767/chat/",
            "https://127.0.0.1:8767/chat/",
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

        assert!(html.contains("正在启动本地控制面"));
        assert!(html.contains("aria-busy=\"true\""));
        assert!(html.contains("aria-live=\"polite\""));
        assert!(html.contains("class=\"status-dots\""));
        assert!(style.contains("@keyframes boot-progress"));
        assert!(style.contains("@keyframes mark-breathe"));
        assert!(style.contains("prefers-reduced-motion: reduce"));
        assert!(style.contains("main[data-state=\"error\"] .progress::after"));
        assert!(style.contains("main[data-state=\"decision\"] .progress"));
        assert!(style.contains("--warning: #f5a623"));
        assert!(script.contains("desktop_update_status"));
        assert!(script.contains("window.loopxBootRetrying"));
        // Services connect concurrently, so the phase names the loopback set
        // until one connection outlives its peer and can be named on its own.
        assert!(script.contains("正在连接本地服务"));
        assert!(script.contains("正在连接状态服务"));
        assert!(script.contains("正在连接管家对话服务"));
        // The first screen must offer both operator choices, not a repair path
        // that silently replaces the CLI runtime.
        assert!(html.contains("id=\"pairing-align\""));
        assert!(html.contains("回退 CLI"));
        assert!(script.contains("runtime_pairing_required"));
        assert!(script.contains("\"align_runtime\""));
        // The boot surface must derive its error projection from the polled
        // snapshot itself and name the known fresh-Mac installer failure.
        assert!(script.contains("runtime_install_exit_2"));
        assert!(script.contains("desktop_recovery_diagnostics_v2"));
    }

    #[test]
    fn boot_failure_message_appends_stable_codes_only() {
        use super::boot_failure_message;
        // The pairing decision names both operator choices instead of the
        // generic startup failure text.
        let pairing = boot_failure_message("runtime_pairing_required");
        assert!(pairing.contains("更新 App 与运行时"));
        assert!(pairing.contains("回退 CLI 到本 App 版本"));
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
