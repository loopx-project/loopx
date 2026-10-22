//! Real HTTP and the shipped updater/signature verifier; no App installation,
//! Core service, credentials or external network. Only the window runtime is fake.
use super::*;
use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    sync::{
        atomic::{AtomicBool, AtomicUsize},
        Arc,
    },
};

const BODY: &[u8] = b"LoopX synthetic signed update transport fixture. No install or runtime.\n";
// Disposable fixture public key and prehashed minisign signature. The signing
// key is not retained. These bytes are deliberately not an installable App.
const KEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IExvb3BYIHRlc3QgcHVibGljIGtleQpSV1JzYjI5d2VIUnpkRWJGVGJ5dExTQ0VKK2RpdGw1NGgwcnBBTTFLa2xjdW5OYy9PaE80KzYwZwo=";
const SIGNATURE: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IExvb3BYIHN5bnRoZXRpYyB0cmFuc3BvcnQgZml4dHVyZQpSVVJzYjI5d2VIUnpkQVJhZGtJKy9nSTdQRzVibndOY3VDbUhyQmVzamNZOTgxaFg4KzhOYXZkRFJhTi91SDNheGNmNnBmOUNyaXRvL2UzTitHMklwMWM0dGVxNHNVTWNld0E9CnRydXN0ZWQgY29tbWVudDogTG9vcFggdHJhbnNwb3J0IGZpeHR1cmUKQUdNUDBuTnhqN0NITHdONURZY2hrTm5Rc0RxZklSZUxVMWM5WEpKcm1WTHpvUWR0bkJJb2F5TGlOY29LZlNHV00zVXdLbTB3c1N1WEVhSStQZFFOQWc9PQo=";

struct Server {
    base: String,
    stop: Arc<AtomicBool>,
    feed_release: Arc<AtomicBool>,
    requests: Arc<AtomicUsize>,
    thread: Option<std::thread::JoinHandle<()>>,
}

fn pause(stop: &AtomicBool, duration: Duration) {
    let until = Instant::now() + duration;
    while Instant::now() < until && !stop.load(Ordering::Acquire) {
        std::thread::sleep(Duration::from_millis(10));
    }
}

impl Server {
    fn new() -> Self {
        Self::with_archive(BODY.to_vec(), SIGNATURE.into())
    }

    fn with_archive(bytes: Vec<u8>, signature: String) -> Self {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let stop = Arc::new(AtomicBool::new(false));
        let feed_release = Arc::new(AtomicBool::new(false));
        let requests = Arc::new(AtomicUsize::new(0));
        let release = feed_release.clone();
        let observed = requests.clone();
        let done = stop.clone();
        let url = base.clone();
        let bytes = Arc::new(bytes);
        let thread = std::thread::spawn(move || {
            while !done.load(Ordering::Acquire) {
                let Ok((mut stream, _)) = listener.accept() else {
                    pause(&done, Duration::from_millis(10));
                    continue;
                };
                let done = done.clone();
                let url = url.clone();
                let bytes = bytes.clone();
                let signature = signature.clone();
                let release = release.clone();
                let observed = observed.clone();
                std::thread::spawn(move || {
                    // macOS accepted sockets inherit listener nonblocking mode.
                    stream.set_nonblocking(false).unwrap();
                    stream
                        .set_read_timeout(Some(Duration::from_secs(2)))
                        .unwrap();
                    stream
                        .set_write_timeout(Some(Duration::from_secs(2)))
                        .unwrap();
                    let mut request = [0; 8192];
                    let size = stream.read(&mut request).unwrap_or(0);
                    let text = String::from_utf8_lossy(&request[..size]);
                    let path = text.split_whitespace().nth(1).unwrap_or("");
                    let mode = path.trim_start_matches('/').trim_start_matches("feed/");
                    observed.fetch_add(1, Ordering::Release);
                    if mode == "manual-pending" {
                        while !release.load(Ordering::Acquire) && !done.load(Ordering::Acquire) {
                            pause(&done, Duration::from_millis(10));
                        }
                    }
                    if !path.starts_with("/feed/") && mode == "not-found" {
                        let _ = stream.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                        return;
                    }
                    if (!path.starts_with("/feed/") && mode == "headers") || mode == "feed-stall" {
                        pause(&done, Duration::from_secs(40));
                        return;
                    }
                    let feed = json!({"version":"1.1.0", "url":format!("{url}/{mode}"), "signature":signature}).to_string();
                    let body = if path.starts_with("/feed/") {
                        feed.as_bytes()
                    } else {
                        &bytes
                    };
                    let headers = format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    );
                    if path.starts_with("/feed/") && mode == "manual-headers" {
                        pause(&done, Duration::from_secs(34));
                    }
                    if stream.write_all(headers.as_bytes()).is_err() {
                        return;
                    }
                    if path.starts_with("/feed/") {
                        if mode == "manual-body" {
                            pause(&done, Duration::from_secs(34));
                        }
                        let _ = stream.write_all(body);
                    } else if mode == "body-stall" {
                        let _ = stream.write_all(&body[..2]);
                        pause(&done, Duration::from_secs(40));
                    } else if mode == "truncated" {
                        let _ = stream.write_all(&body[..2]);
                    } else if mode == "tampered" {
                        let _ = stream.write_all(&vec![b'!'; body.len()]);
                    } else if mode == "slow" {
                        for chunk in body.chunks(body.len().div_ceil(36)) {
                            if stream.write_all(chunk).is_err() || done.load(Ordering::Acquire) {
                                break;
                            }
                            pause(&done, Duration::from_secs(1));
                        }
                    } else {
                        let _ = stream.write_all(body);
                    }
                });
            }
        });
        Self {
            base,
            stop,
            feed_release,
            requests,
            thread: Some(thread),
        }
    }

    fn feed(&self, mode: &str) -> tauri::Url {
        format!("{}/feed/{mode}", self.base).parse().unwrap()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        let _ = TcpStream::connect(self.base.trim_start_matches("http://"));
        self.thread.take().unwrap().join().unwrap();
    }
}

fn app(key: &str) -> tauri::App<tauri::test::MockRuntime> {
    let mut context = tauri::test::mock_context(tauri::test::noop_assets());
    context.config_mut().plugins.0.insert(
        "updater".into(),
        json!({
        "pubkey":key, "dangerousInsecureTransportProtocol":true
        }),
    );
    tauri::test::mock_builder()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .build(context)
        .unwrap()
}

#[test]
fn manual_native_feed_releases_startup_before_http_finishes() {
    let app = app(KEY);
    let server = Server::new();
    let state = Arc::new(Maintenance::default());
    let read_state = state.clone();
    let handle = app.handle().clone();
    let url = server.feed("manual-pending");
    let request = tauri::async_runtime::spawn(async move {
        read_state
            .run_manual_check(
                "stable",
                find_update(&handle, "stable", url, Duration::from_secs(5)),
                |update| read_state.accept_checked_update("stable", "0.1.0", update),
            )
            .await
    });
    // Synchronize on the real HTTP request, not a lucky elapsed-time sample.
    let deadline = Instant::now() + Duration::from_secs(3);
    while server.requests.load(Ordering::Acquire) == 0 && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(server.requests.load(Ordering::Acquire), 1);
    assert_eq!(state.reconcile_services(|| Ok(1)).unwrap(), Some(1));
    assert_eq!(state.recover_connected(|| 2), Some(2));
    assert_eq!(state.status_snapshot()["phase"], "checking");
    server.feed_release.store(true, Ordering::Release);
    let result = tauri::async_runtime::block_on(request).unwrap().unwrap();
    assert_eq!(result["phase"], "available");
    assert_eq!(state.status_snapshot(), result);
    assert!(state.pending.lock().unwrap().is_some());
    assert_eq!(
        server.requests.load(Ordering::Acquire),
        1,
        "checking must not download the archive"
    );
    assert_eq!(state.reconcile_services(|| Ok(3)).unwrap(), Some(3));
    assert_eq!(
        state.status_snapshot(),
        result,
        "the Install choice remains available in the workspace"
    );
}

#[test]
fn manual_native_feed_timeout_retains_failure_and_allows_service_start() {
    let app = app(KEY);
    let server = Server::new();
    let state = Maintenance::default();
    let result = tauri::async_runtime::block_on(state.run_manual_check(
        "stable",
        find_update(
            app.handle(),
            "stable",
            server.feed("feed-stall"),
            Duration::from_millis(100),
        ),
        |_| panic!("a timed-out feed cannot supply an update candidate"),
    ));
    assert_eq!(result.unwrap_err(), "update_check_timeout");
    assert_eq!(state.reconcile_services(|| Ok(())).unwrap(), Some(()));
    assert_eq!(
        state.status_snapshot()["details"]["code"],
        "update_check_timeout"
    );
    assert_eq!(*state.last_failure.lock().unwrap(), state.status_snapshot());
    assert!(state.pending.lock().unwrap().is_none());
}

#[test]
fn native_update_transport_checks_signatures_truncation_and_cache_readback() {
    let app = app(KEY);
    let server = Server::new();
    tauri::async_runtime::block_on(async {
        for (mode, expected_error) in [
            ("fast", None),
            ("tampered", Some("update_signature_invalid")),
            ("truncated", Some("update_network_failed")),
            ("not-found", Some("update_download_failed")),
        ] {
            let update = find_update(
                app.handle(),
                "stable",
                server.feed(mode),
                Duration::from_secs(2),
            )
            .await
            .unwrap()
            .unwrap();
            let result = download_update(&update, |_, _| {}).await;
            if let Some(expected) = expected_error {
                assert_eq!(result.unwrap_err(), expected, "archive mode: {mode}");
                continue;
            }
            let bytes = result.unwrap();
            assert_eq!(bytes, BODY);
            let root = tempfile::tempdir().unwrap();
            crate::prepared_update::save(
                root.path(),
                "stable",
                &update.version,
                &update.signature,
                &bytes,
            )
            .unwrap();
            let prepared = crate::prepared_update::load(root.path(), "0.1.0", "stable", KEY)
                .unwrap()
                .unwrap();
            assert_eq!(prepared.bytes, BODY);
        }
    });
}

#[test]
fn background_download_failures_keep_their_cause_without_blocking_local_services() {
    let app = app(KEY);
    let server = Server::new();
    tauri::async_runtime::block_on(async {
        for (mode, expected) in [
            ("tampered", "update_signature_invalid"),
            ("truncated", "update_network_failed"),
            ("not-found", "update_download_failed"),
        ] {
            let state = Maintenance::default();
            state.publish("ready", json!({}));
            let generation = state.admit_background_update().unwrap();
            let result = state
                .run_background_update(
                    generation,
                    "stable",
                    find_update(
                        app.handle(),
                        "stable",
                        server.feed(mode),
                        Duration::from_secs(2),
                    ),
                    |update| async move {
                        download_update(&update, |_, _| {}).await?;
                        panic!("a rejected archive cannot be published to the cache");
                    },
                )
                .await;
            assert_eq!(result.unwrap_err(), expected);
            assert_eq!(state.status_snapshot()["details"]["code"], expected);
            assert_eq!(state.status_snapshot()["details"]["background"], true);
            assert_eq!(state.recover_connected(|| 1), Some(1));
            assert!(state.pending.lock().unwrap().is_none());
        }
    });
}

#[test]
fn native_feed_keeps_its_short_deadline() {
    let app = app(KEY);
    let server = Server::new();
    let start = Instant::now();
    let error = tauri::async_runtime::block_on(find_update(
        app.handle(),
        "stable",
        server.feed("feed-stall"),
        Duration::from_millis(150),
    ))
    .err()
    .unwrap();
    assert_eq!(error, "update_check_timeout");
    assert!(start.elapsed() < Duration::from_secs(3));
}

fn assert_manual_feed_window(mode: &str) {
    let app = app(KEY);
    let server = Server::new();
    let start = Instant::now();
    let update = tauri::async_runtime::block_on(find_update(
        app.handle(),
        "stable",
        server.feed(mode),
        Duration::from_secs(45),
    ))
    .unwrap()
    .unwrap();
    let elapsed = start.elapsed();
    assert!(elapsed > Duration::from_secs(30));
    assert!(elapsed < Duration::from_secs(48));
    let bytes = tauri::async_runtime::block_on(download_update(&update, |_, _| {})).unwrap();
    assert_eq!(bytes, BODY);
    eprintln!(
        "manual {mode} feed: {:.3}s; signed archive still downloads",
        elapsed.as_secs_f64()
    );
}

#[test]
#[ignore = "real manual feed response headers arrive after the archive idle deadline"]
fn manual_feed_headers_keep_the_original_forty_five_second_window() {
    assert_manual_feed_window("manual-headers");
}

#[test]
#[ignore = "real manual feed body arrives after the archive idle deadline"]
fn manual_feed_body_keeps_the_original_forty_five_second_window() {
    assert_manual_feed_window("manual-body");
}

#[test]
#[ignore = "real transport spans the historical thirty-second download deadline"]
fn progressing_signed_download_survives_the_old_total_deadline() {
    let app = app(KEY);
    let server = Server::new();
    let state = Maintenance::default();
    state.reconcile_services(|| Ok(())).unwrap();
    let start = Instant::now();
    let mut received = 0;
    tauri::async_runtime::block_on(state.run_background_update(
        0,
        "stable",
        find_update(
            app.handle(),
            "stable",
            server.feed("slow"),
            Duration::from_secs(2),
        ),
        |update| {
            let state = &state;
            let received = &mut received;
            async move {
                let bytes = download_update(&update, |size, total| {
                    *received += size;
                    assert_eq!(state.recover_connected(|| 1), Some(1));
                    assert_eq!(total, Some(BODY.len() as u64));
                })
                .await?;
                assert_eq!(bytes, BODY);
                Ok(json!({"version":update.version, "prepared":true}))
            }
        },
    ))
    .unwrap();
    assert_eq!(received, BODY.len());
    assert!(start.elapsed() > Duration::from_secs(30));
    assert_eq!(state.status_snapshot()["details"]["prepared"], true);
    eprintln!(
        "progressing signed download: {} bytes, {:.3}s; service recovery stayed available",
        received,
        start.elapsed().as_secs_f64()
    );
}

#[test]
#[ignore = "real thirty-second idle deadlines for headers and mid-body stalls"]
fn stalled_update_transfer_remains_bounded() {
    let app = app(KEY);
    let server = Server::new();
    for mode in ["headers", "body-stall"] {
        let update = tauri::async_runtime::block_on(find_update(
            app.handle(),
            "stable",
            server.feed(mode),
            Duration::from_secs(2),
        ))
        .unwrap()
        .unwrap();
        let start = Instant::now();
        assert_eq!(
            tauri::async_runtime::block_on(download_update(&update, |_, _| {})).unwrap_err(),
            "update_download_timeout"
        );
        assert!(
            start.elapsed() < Duration::from_secs(35),
            "stalled {mode} transfer was not bounded"
        );
        eprintln!("{mode} stopped in {:.3}s", start.elapsed().as_secs_f64());
    }
}

#[test]
#[ignore = "requires LOOPX_TEST_SIGNED_ARCHIVE; real released bytes over slow loopback HTTP"]
fn released_archive_survives_slow_native_transport_and_cache_reopen() {
    let path = std::env::var("LOOPX_TEST_SIGNED_ARCHIVE").unwrap();
    let bytes = std::fs::read(&path).unwrap();
    let signature = std::fs::read_to_string(format!("{path}.sig")).unwrap();
    let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
    let key = config["plugins"]["updater"]["pubkey"].as_str().unwrap();
    let app = app(key);
    let server = Server::with_archive(bytes.clone(), signature.trim().into());
    let start = Instant::now();
    let update = tauri::async_runtime::block_on(find_update(
        app.handle(),
        "stable",
        server.feed("slow"),
        Duration::from_secs(2),
    ))
    .unwrap()
    .unwrap();
    let downloaded = tauri::async_runtime::block_on(download_update(&update, |_, _| {})).unwrap();
    assert!(start.elapsed() > Duration::from_secs(30));
    assert_eq!(downloaded, bytes);
    let root = tempfile::tempdir().unwrap();
    crate::prepared_update::save(
        root.path(),
        "stable",
        &update.version,
        &update.signature,
        &downloaded,
    )
    .unwrap();
    assert_eq!(
        crate::prepared_update::load(root.path(), "0.1.0", "stable", key)
            .unwrap()
            .unwrap()
            .bytes,
        bytes
    );
    eprintln!("released signed archive: {} bytes, {:.3}s including verified cache reopen; no installation", bytes.len(), start.elapsed().as_secs_f64());
}
