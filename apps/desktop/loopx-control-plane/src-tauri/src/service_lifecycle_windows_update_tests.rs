//! Windows regression for the verified-but-unlaunchable App update.
//!
//! The pinned updater verifies a real minisign signature, unpacks the real
//! NSIS archive and only then reaches `ShellExecuteW`. When that launch fails
//! the process keeps running with the pre-exit hook already executed, so this
//! window must stay usable and the services the hook reclaimed must return to
//! the live supervisor.
//!
//! Nothing here substitutes for the plugin's own machinery: the feed is real
//! HTTP on loopback, the signature is checked by the plugin's own
//! `minisign-verify` call over the downloaded bytes, and the archive is
//! unpacked by the plugin's own ZIP handling. Only the signing key is
//! throwaway, and it is generated per run so no key material can reach the
//! repository.

// `tauri_build` attaches the application resource to `[[bin]]` targets only, so
// this libtest binary would link without an application manifest. The
// `comctl32` entry points reachable from the mocked App are Common Controls v6
// APIs; without the manifest the loader binds the System32 5.82 copy and the
// process dies with `STATUS_ENTRYPOINT_NOT_FOUND` before `main`. Linking the
// same resource here keeps the fix inside the test binary: the build script
// only publishes the search path, and `rustc-link-arg` was not an option
// because it would duplicate the resource for `[[bin]]` targets.
#[link(name = "resource", kind = "static")]
extern "C" {}

use super::tests::windows_reclaim::{
    hold_process_independent, hold_process_owned, process_is_alive, wait_until,
};
use super::{app_update_pre_exit_hook, release_app_update_handoff, ServiceLifecycle};
use crate::services::owned_set_for_test;
use base64::{engine::general_purpose::STANDARD, Engine};
use blake2::{Blake2b512, Digest};
use ring::signature::{Ed25519KeyPair, KeyPair};
use serde_json::json;
use std::collections::HashMap;
use std::io::{Cursor, Read, Write};
use std::net::TcpListener;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tauri::test::{mock_builder, mock_context, noop_assets, MockRuntime};
use tauri::{AppHandle, Manager, Resource, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_updater::{Update, Updater, UpdaterExt};

/// The announced main-channel version. The production comparator accepts it
/// for a window whose own version is not a `main.` prerelease.
const UPDATE_VERSION: &str = "0.0.2-main.1";
/// A test binary is not a bundled App, so `tauri::utils::platform::bundle_type`
/// cannot report `nsis`; the real NSIS window resolves this key first.
const UPDATE_TARGET: &str = "windows-x86_64-nsis";
const INSTALLER: &str = "loopx-control-plane_0.0.2-main.1_x64-setup.exe";

/// A resource whose survival proves `cleanup_before_exit` did not run: the
/// pinned default hook clears exactly this table.
struct WindowSentinel;
impl Resource for WindowSentinel {}

/// A throwaway minisign-format Ed25519 key. The key id is derived from the
/// public key instead of being random so a run cannot reuse a shipped id.
struct TestSigner {
    key_id: [u8; 8],
    key: Ed25519KeyPair,
}

impl TestSigner {
    fn generate() -> Self {
        let rng = ring::rand::SystemRandom::new();
        let pkcs8 = Ed25519KeyPair::generate_pkcs8(&rng).expect("test signing key");
        let key = Ed25519KeyPair::from_pkcs8(pkcs8.as_ref()).expect("test signing key");
        let digest = Blake2b512::digest(key.public_key().as_ref());
        let mut key_id = [0u8; 8];
        key_id.copy_from_slice(&digest[..8]);
        Self { key_id, key }
    }

    /// The plugin expects the base64 of a two-line `minisign.pub` file.
    fn public_key(&self) -> String {
        let mut key = Vec::with_capacity(42);
        key.extend_from_slice(b"Ed");
        key.extend_from_slice(&self.key_id);
        key.extend_from_slice(self.key.public_key().as_ref());
        STANDARD.encode(format!(
            "untrusted comment: minisign public key\n{}\n",
            STANDARD.encode(key)
        ))
    }

    /// The base64 signature the release feed announces, in the prehashed
    /// ("ED") mode `tauri signer sign` produces.
    fn sign(&self, data: &[u8]) -> String {
        let prehash = Blake2b512::digest(data);
        let signature = self.key.sign(&prehash[..]);
        let mut blob = Vec::with_capacity(74);
        blob.extend_from_slice(b"ED");
        blob.extend_from_slice(&self.key_id);
        blob.extend_from_slice(signature.as_ref());
        let trusted_comment = format!("timestamp:0\tfile:{INSTALLER}\tprehashed");
        let mut global = Vec::with_capacity(64 + trusted_comment.len());
        global.extend_from_slice(signature.as_ref());
        global.extend_from_slice(trusted_comment.as_bytes());
        let global_signature = self.key.sign(&global);
        STANDARD.encode(format!(
            "untrusted comment: signature from minisign secret key\n{}\ntrusted comment: {}\n{}\n",
            STANDARD.encode(&blob),
            trusted_comment,
            STANDARD.encode(global_signature.as_ref()),
        ))
    }
}

/// The ZIP an NSIS App update ships: the plugin extracts its `-setup.exe` and
/// launches that file. The payload is deliberately not a portable executable,
/// so Windows refuses the launch instead of starting a replacement process --
/// the failure this regression owns.
fn nsis_artifact() -> Vec<u8> {
    let mut writer = zip::ZipWriter::new(Cursor::new(Vec::new()));
    writer
        .start_file(
            INSTALLER,
            zip::write::SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Stored),
        )
        .expect("artifact entry");
    writer
        .write_all(b"this payload is not a portable executable")
        .expect("artifact payload");
    writer.finish().expect("artifact archive").into_inner()
}

fn feed_body(artifact_url: &str, signature: &str) -> Vec<u8> {
    let platforms = HashMap::from([(
        UPDATE_TARGET.to_string(),
        json!({"url": artifact_url, "signature": signature}),
    )]);
    json!({
        "version": UPDATE_VERSION,
        "notes": "regression artifact",
        "pub_date": null,
        "platforms": platforms,
    })
    .to_string()
    .into_bytes()
}

/// A loopback feed the pinned updater fetches over real HTTP: the signature
/// verification this regression crosses must run on bytes that travelled
/// through the download path, not on an in-memory substitute.
struct FeedServer {
    origin: String,
    stop: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl FeedServer {
    fn start(routes: impl FnOnce(&str) -> Vec<(&'static str, &'static str, Vec<u8>)>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind the loopback feed");
        let origin = format!(
            "http://127.0.0.1:{}",
            listener.local_addr().expect("feed address").port()
        );
        listener
            .set_nonblocking(true)
            .expect("non-blocking feed listener");
        let routes: HashMap<String, (&'static str, Vec<u8>)> = routes(&origin)
            .into_iter()
            .map(|(path, content_type, body)| (path.to_string(), (content_type, body)))
            .collect();
        let stop = Arc::new(AtomicBool::new(false));
        let thread = std::thread::spawn({
            let stop = Arc::clone(&stop);
            move || {
                while !stop.load(Ordering::Acquire) {
                    let Ok((mut stream, _)) = listener.accept() else {
                        std::thread::sleep(Duration::from_millis(10));
                        continue;
                    };
                    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
                    let mut request = Vec::new();
                    let mut buffer = [0u8; 512];
                    while !request.windows(4).any(|window| window == b"\r\n\r\n") {
                        match stream.read(&mut buffer) {
                            Ok(0) | Err(_) => break,
                            Ok(read) => request.extend_from_slice(&buffer[..read]),
                        }
                    }
                    let path = String::from_utf8_lossy(&request)
                        .lines()
                        .next()
                        .and_then(|line| line.split_whitespace().nth(1))
                        .map(str::to_string);
                    let response = match path.as_deref().and_then(|path| routes.get(path)) {
                        Some((content_type, body)) => {
                            let mut response = format!(
                                "HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\n\
                                 Content-Length: {}\r\nConnection: close\r\n\r\n",
                                body.len()
                            )
                            .into_bytes();
                            response.extend_from_slice(body);
                            response
                        }
                        None => {
                            b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                                .to_vec()
                        }
                    };
                    let _ = stream.write_all(&response);
                    let _ = stream.flush();
                }
            }
        });
        Self {
            origin,
            stop,
            thread: Some(thread),
        }
    }
}

impl Drop for FeedServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// The updater the production window builds, with the pre-exit hook injected
/// as an argument so the plugin's default hook can be contrasted with ours.
fn updater_with<F: Fn() + Send + Sync + 'static>(
    app: &AppHandle<MockRuntime>,
    feed: &str,
    on_before_exit: F,
) -> Updater {
    app.updater_builder()
        .on_before_exit(on_before_exit)
        .version_comparator(|current, remote| {
            // The production main-channel rule: a window that is not itself a
            // `main.` prerelease moves to the announced main build.
            if !current.pre.as_str().starts_with("main.") {
                remote.version.pre.as_str().starts_with("main.")
            } else {
                remote.version > current
            }
        })
        // A test binary is not a bundled App, so the plugin cannot derive
        // the NSIS target; the real window requests this exact key.
        .target(UPDATE_TARGET)
        .endpoints(vec![feed.parse().expect("loopback feed url")])
        .expect("the loopback feed is an allowed endpoint")
        .no_proxy()
        .timeout(Duration::from_secs(30))
        .build()
        .expect("updater for the loopback feed")
}

fn announce(updater: &Updater) -> Update {
    let update = tauri::async_runtime::block_on(updater.check())
        .expect("the loopback feed answers")
        .expect("the feed announces a main-channel update");
    assert_eq!(update.version, UPDATE_VERSION);
    update
}

fn download(update: &mut Update) -> Vec<u8> {
    update.timeout = Some(Duration::from_secs(30));
    tauri::async_runtime::block_on(update.download(|_, _| {}, || {}))
        .expect("the announced artifact crosses the plugin's signature verification")
}

/// The pinned updater keeps its extraction directory; a regression run must
/// not leave it behind.
fn plugin_temp_dirs() -> Vec<std::path::PathBuf> {
    let prefix = format!("test-{UPDATE_VERSION}-updater-");
    std::fs::read_dir(std::env::temp_dir())
        .map(|entries| {
            entries
                .filter_map(|entry| entry.ok())
                .map(|entry| entry.path())
                .filter(|path| {
                    path.file_name()
                        .and_then(|name| name.to_str())
                        .is_some_and(|name| name.starts_with(&prefix))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn kill_with_descendants(child: &mut std::process::Child) {
    let _ = std::process::Command::new("taskkill")
        .args(["/T", "/F", "/PID", &child.id().to_string()])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    let _ = child.wait();
}

#[test]
fn a_verified_artifact_whose_launch_fails_keeps_the_window_usable_and_returns_the_runtime() {
    let artifact = nsis_artifact();
    let signer = TestSigner::generate();
    let signature = signer.sign(&artifact);
    let mut tampered = artifact.clone();
    tampered[64] ^= 0x01;
    let server = FeedServer::start(|origin| {
        vec![
            (
                "/feed.json",
                "application/json",
                feed_body(&format!("{origin}/artifact.zip"), &signature),
            ),
            (
                "/tampered-feed.json",
                "application/json",
                feed_body(&format!("{origin}/tampered.zip"), &signature),
            ),
            (
                "/artifact.zip",
                "application/octet-stream",
                artifact.clone(),
            ),
            (
                "/tampered.zip",
                "application/octet-stream",
                tampered.clone(),
            ),
        ]
    });

    let lifecycle = ServiceLifecycle::new();
    let mut context = mock_context(noop_assets());
    // The pinned plugin reads its configuration from the App config; the
    // signing key is the throwaway test key, and the plain-HTTP loopback feed
    // is the explicit development allowance the plugin documents.
    context.config_mut().plugins.0.insert(
        "updater".to_string(),
        json!({
            "pubkey": signer.public_key(),
            "dangerousInsecureTransportProtocol": true,
        }),
    );
    let app = mock_builder()
        .plugin(
            tauri_plugin_updater::Builder::new()
                .pubkey(signer.public_key())
                .build(),
        )
        .manage(lifecycle.clone())
        .build(context)
        .expect("desktop shell for the update regression");
    let window = WebviewWindowBuilder::new(&app, "main", WebviewUrl::default())
        .visible(true)
        .build()
        .expect("this window");
    let handle = app.handle().clone();

    let owned = hold_process_owned();
    let owned_pid = owned.id();
    let mut independent = hold_process_independent();
    let independent_pid = independent.id();
    assert!(lifecycle.adopt(owned_set_for_test(vec![owned])).is_ok());
    assert!(process_is_alive(owned_pid));
    let app_resource = handle.resources_table().add(WindowSentinel);
    let window_resource = window.resources_table().add(WindowSentinel);
    let temp_before = plugin_temp_dirs();

    let feed = format!("{}/feed.json", server.origin);
    // The production hook, verbatim: the same constructor the desktop window
    // installs, so the regression cannot drift from the shipped hook.
    let handoff = || app_update_pre_exit_hook(&handle);

    // A tampered artifact announced with the same signature must not cross
    // verification; without it the "verified" claim below would be vacuous.
    let tampered_updater = updater_with(
        &handle,
        &format!("{}/tampered-feed.json", server.origin),
        handoff(),
    );
    let tampered_update = announce(&tampered_updater);
    assert!(
        tauri::async_runtime::block_on(tampered_update.download(|_, _| {}, || {})).is_err(),
        "a tampered artifact must not cross the plugin's signature verification"
    );

    let updater = updater_with(&handle, &feed, handoff());
    let mut update = announce(&updater);
    let bytes = download(&mut update);
    let launched = update.install(bytes);
    assert!(
        launched.is_err(),
        "an unlaunchable installer returns an error instead of replacing this process"
    );

    // The plugin ran this window's own hook, and that hook reclaimed exactly
    // one service group -- while a service this App never started survives.
    assert!(
        lifecycle.handoff_active(),
        "the pinned plugin must run the hook the production window installs"
    );
    wait_until(
        || !process_is_alive(owned_pid),
        "the reclaimed service group to exit",
    );
    assert!(
        process_is_alive(independent_pid),
        "a CLI-owned service on its own port is not this App's to stop"
    );

    // The failure keeps the window usable: Tauri's own `cleanup_before_exit`
    // would have cleared exactly these tables and hidden this window.
    assert!(
        handle
            .resources_table()
            .get::<WindowSentinel>(app_resource)
            .is_ok(),
        "the App resources must survive a failed installer launch"
    );
    assert!(
        window
            .resources_table()
            .get::<WindowSentinel>(window_resource)
            .is_ok(),
        "the window resources must survive a failed installer launch"
    );
    assert!(
        handle.get_webview_window("main").is_some(),
        "the window the operator retries from must still be addressable"
    );

    // The released handoff owes the reclaimed runtime back to the supervisor.
    assert!(
        release_app_update_handoff(&handle),
        "the hook reclaimed a runtime, so the release owes it back"
    );
    assert!(lifecycle.restore_pending());
    let replacement = hold_process_owned();
    let replacement_pid = replacement.id();
    assert!(lifecycle
        .adopt(owned_set_for_test(vec![replacement]))
        .is_ok());
    assert!(
        !lifecycle.restore_pending(),
        "a live set settles the restore the released handoff owed"
    );

    // Contrast run: the pinned plugin's *default* hook is `cleanup_before_exit`,
    // exactly what this window replaces. Running it proves the assertions
    // above detect the pre-fix hazard instead of passing vacuously.
    let default_resource = handle.resources_table().add(WindowSentinel);
    let default_updater = updater_with(&handle, &feed, {
        let app = handle.clone();
        move || app.cleanup_before_exit()
    });
    let mut default_update = announce(&default_updater);
    let default_bytes = download(&mut default_update);
    assert!(default_update.install(default_bytes).is_err());
    assert!(
        handle
            .resources_table()
            .get::<WindowSentinel>(default_resource)
            .is_err(),
        "the default hook clears the resource table, so the survival assertions above detect that difference"
    );

    lifecycle.shutdown(&handle);
    assert!(
        !process_is_alive(replacement_pid),
        "closing the window reclaims the replacement group"
    );
    kill_with_descendants(&mut independent);
    for path in plugin_temp_dirs() {
        if !temp_before.contains(&path) {
            let _ = std::fs::remove_dir_all(path);
        }
    }
}
