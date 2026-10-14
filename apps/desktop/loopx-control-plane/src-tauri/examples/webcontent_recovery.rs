//! macOS-only, opt-in native regression. Uses no LoopX runtime, data or services.
#[cfg(not(target_os = "macos"))]
fn main() {
    eprintln!("webcontent_recovery requires macOS");
    std::process::exit(2);
}

// The example embeds the complete production owner; startup and other-engine
// branches stay exercised by its library tests rather than this macOS fault path.
#[cfg(target_os = "macos")]
#[allow(dead_code)]
#[path = "../src/workspace_handoff.rs"]
mod workspace_handoff;

#[cfg(target_os = "macos")]
fn main() {
    use std::{
        io::{Read, Write},
        net::{Shutdown, TcpListener},
        sync::{
            atomic::{AtomicBool, AtomicUsize, Ordering},
            Arc, Mutex,
        },
        time::{Duration, Instant},
    };
    use tauri::{webview::PageLoadEvent, Manager, WebviewUrl, WebviewWindowBuilder};
    use workspace_handoff::{NavigationRetry, WorkspaceHandoff};
    let baseline = std::env::args().skip(1).any(|a| a == "--baseline");
    let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
    listener.set_nonblocking(true).unwrap();
    let target: tauri::Url = format!(
        "http://{}/chat/?goalId=example&view=conversation",
        listener.local_addr().unwrap()
    )
    .parse()
    .unwrap();
    let expected = format!(
        "{}#selected",
        target.as_str().replace("view=conversation", "view=tasks")
    );
    let unavailable = Arc::new(AtomicBool::new(false));
    let stopped = Arc::new(AtomicBool::new(false));
    let writes = Arc::new(AtomicUsize::new(0));
    let server_down = unavailable.clone();
    let server_stop = stopped.clone();
    let server_writes = writes.clone();
    std::thread::spawn(move || {
        while !server_stop.load(Ordering::Acquire) {
            let Ok((mut stream, _)) = listener.accept() else {
                std::thread::sleep(Duration::from_millis(10));
                continue;
            };
            let server_down = server_down.clone();
            let server_writes = server_writes.clone();
            std::thread::spawn(move || {
                if stream
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .is_err()
                {
                    return;
                }
                let mut request = [0; 4096];
                let n = stream.read(&mut request).unwrap_or(0);
                // WebKit preconnects can close without sending a request.
                if n == 0 {
                    return;
                }
                if !request[..n].starts_with(b"GET ") {
                    server_writes.fetch_add(1, Ordering::SeqCst);
                    return;
                }
                if server_down.load(Ordering::Acquire) {
                    let _ = stream.shutdown(Shutdown::Both);
                    return;
                }
                let prefix = format!("<html><body><!--{}-->", "x".repeat(2048));
                let body = b"synthetic-native-workspace</body></html>";
                let headers = format!("HTTP/1.0 200 OK\r\nContent-Type: text/html\r\nContent-Length: {}\r\nCache-Control: no-store\r\n\r\n", prefix.len() + body.len());
                // Deliver enough HTML to commit before the slow remaining body.
                if stream
                    .write_all(headers.as_bytes())
                    .and_then(|_| stream.write_all(prefix.as_bytes()))
                    .is_ok()
                {
                    std::thread::sleep(Duration::from_secs(3));
                    let _ = stream.write_all(body);
                }
            });
        }
    });
    let faulted = Arc::new(AtomicBool::new(false));
    let mut builder = tauri::Builder::default();
    if !baseline {
        builder = builder.on_web_content_process_terminate(workspace_handoff::content_lost);
    }
    let result = Arc::new(AtomicUsize::new(3));
    let exit_result = result.clone();
    let app = builder.setup(move |app| {
        app.set_activation_policy(tauri::ActivationPolicy::Prohibited);
        let handoff = Arc::new(Mutex::new(WorkspaceHandoff::new("tauri://localhost/".parse().unwrap(), target.clone(), NavigationRetry::UntilNativeCommit)));
        app.manage(handoff.clone());
        let load_state = handoff.clone();
        let down = unavailable.clone(); let done = stopped.clone(); let effects = writes.clone();
        let original = expected.clone();
        let load_result = exit_result.clone();
        WebviewWindowBuilder::new(app, "main", WebviewUrl::External(target.clone())).incognito(true).visible(false)
        .on_page_load(move |webview, payload| {
            load_state.lock().unwrap().page_reached(payload.url());
            if !matches!(payload.event(), PageLoadEvent::Finished) || faulted.swap(true, Ordering::SeqCst) { return; }
            let view = webview.clone(); let down = down.clone(); let done = done.clone(); let effects = effects.clone(); let original = original.clone();
            let result = load_result.clone();
            // Replace history before killing only this incognito view's process.
            webview.eval_with_callback("history.replaceState(null,'','?goalId=example&view=tasks#selected')", move |_| {
                let view = view.clone(); let down = down.clone(); let done = done.clone(); let effects = effects.clone(); let original = original.clone();
                let result = result.clone();
                std::thread::spawn(move || {
                    down.store(true, Ordering::Release);
                    view.with_webview(|native| unsafe {
                        let object = native.inner() as *mut objc2::runtime::AnyObject;
                        let pid: i32 = objc2::msg_send![object, _webProcessIdentifier];
                        assert!(pid > 1 && pid != libc::getpid());
                        assert_eq!(libc::kill(pid, libc::SIGKILL), 0);
                    }).unwrap();
                    std::thread::sleep(Duration::from_secs(3)); down.store(false, Ordering::Release);
                    std::thread::sleep(Duration::from_secs(6));
                    let app = view.app_handle().clone();
                    view.eval_with_callback("JSON.stringify({body:document.body.textContent,url:location.href})", move |value| {
                        let outer: serde_json::Value = serde_json::from_str(&value).unwrap();
                        let dom: serde_json::Value = serde_json::from_str(outer.as_str().unwrap()).unwrap();
                        let accepted = dom["body"] == "synthetic-native-workspace" && dom["url"] == original && effects.load(Ordering::SeqCst) == 0;
                        println!("{}", serde_json::json!({"automatic_recovery":accepted,"dom":dom,"non_get_requests":effects.load(Ordering::SeqCst),"baseline":baseline}));
                        result.store(if accepted {0} else {1}, Ordering::Release);
                        done.store(true, Ordering::Release); app.exit(0);
                    }).unwrap();
                });
            }).unwrap();
        }).build()?;
        let handle = app.handle().clone(); let end = stopped.clone();
        std::thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(20);
            while !end.load(Ordering::Acquire) && Instant::now() < deadline {
                if !baseline { WorkspaceHandoff::reconcile(&handoff, &handle); }
                std::thread::sleep(Duration::from_millis(200));
            }
            if !end.load(Ordering::Acquire) { eprintln!("native recovery timed out"); handle.exit(3); }
        });
        Ok(())
    }).build(tauri::generate_context!("webcontent-recovery.tauri.conf.json")).expect("isolated recovery smoke");
    app.run_return(|_, _| {});
    std::process::exit(result.load(Ordering::Acquire) as i32);
}
