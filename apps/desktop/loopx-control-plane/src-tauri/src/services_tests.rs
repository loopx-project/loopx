use super::*;

#[test]
fn status_readiness_is_explicit_and_fails_closed() {
    assert_eq!(ServiceKind::Status.probe_path(), "/?readiness=1");
    for (readiness, expected) in [
        (
            serde_json::json!({"schema_version":"loopx_status_readiness_v0","state":"ready","reason":"registry_readable"}),
            Probe::Matching,
        ),
        (
            serde_json::json!({"schema_version":"loopx_status_readiness_v0","state":"failed","reason":"registry_invalid"}),
            Probe::NotReady,
        ),
        (
            serde_json::json!({"schema_version":"loopx_status_readiness_v0","state":"failed","reason":"registry_unavailable"}),
            Probe::NotReady,
        ),
        (
            serde_json::json!({"schema_version":"loopx_status_readiness_v0","state":"ready","reason":"registry_invalid"}),
            Probe::Foreign,
        ),
        (
            serde_json::json!({"schema_version":"future","state":"ready","reason":"registry_readable"}),
            Probe::Foreign,
        ),
        (serde_json::Value::Null, Probe::Foreign),
    ] {
        let body = serde_json::json!({"source":"serve-status","readiness":readiness});
        let response = format!("HTTP/1.1 200 OK\r\n\r\n{body}");
        assert_eq!(
            classify_response(ServiceKind::Status, &response, None),
            expected
        );
    }
    assert_eq!(classify_response(
        ServiceKind::Status,
        "HTTP/1.1 200 OK\r\n\r\n{\"source\":\"serve-status\",\"readiness_url\":\"/?readiness=1\"}",
        None,
    ), Probe::Foreign);
}

#[test]
#[cfg(target_os = "macos")]
fn finder_runtime_path_includes_tools_without_loading_shell_profiles() {
    let path = runtime_search_path(
        Some("/fixture/user".into()),
        Some("/usr/bin:/bin:/usr/sbin:/sbin".into()),
    );
    let paths: Vec<_> = env::split_paths(&path).collect();
    assert_eq!(paths[0], PathBuf::from("/fixture/user/.local/bin"));
    assert!(
        paths
            .iter()
            .position(|p| p == Path::new("/opt/homebrew/bin"))
            .unwrap()
            < paths
                .iter()
                .position(|p| p == Path::new("/usr/bin"))
                .unwrap()
    );
    assert!(paths.contains(&PathBuf::from("/usr/local/bin")));
    assert!(paths.contains(&PathBuf::from("/sbin")));
    let again = runtime_search_path(Some("/fixture/user".into()), Some(path.clone()));
    assert_eq!(again, path, "tool search must be idempotent");
}

#[cfg(not(windows))]
fn spawn_listener_fixture(
    executable: &Path,
    command: &str,
    port: u16,
    payload: &str,
) -> std::process::Child {
    use std::os::unix::fs::PermissionsExt;

    let source = format!(
        r#"#!/usr/bin/env python3
import socket
import time

payload = {payload:?}.encode("utf-8")
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", {port}))
server.listen(8)
while True:
    connection, _ = server.accept()
    connection.recv(65536)
    if not payload:
        time.sleep(60)
        continue
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {{len(payload)}}\r\n".encode("ascii")
        + b"Connection: close\r\n\r\n"
        + payload
    )
    connection.sendall(response)
    connection.close()
"#
    );
    fs::write(executable, source).expect("write listener fixture");
    let mut permissions = fs::metadata(executable)
        .expect("listener fixture metadata")
        .permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(executable, permissions).expect("make listener fixture executable");
    Command::new(executable)
        .args([command, "--host", "127.0.0.1", "--port", &port.to_string()])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn listener fixture")
}

#[cfg(not(windows))]
fn reserve_loopback_port() -> u16 {
    std::net::TcpListener::bind(("127.0.0.1", 0))
        .expect("reserve loopback port")
        .local_addr()
        .expect("reserved loopback address")
        .port()
}

#[cfg(not(windows))]
fn wait_for_probe(
    kind: ServiceKind,
    port: u16,
    expected_runtime_identity: Option<&serde_json::Value>,
    expected_probe: Probe,
) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if probe_on_port(kind, port, expected_runtime_identity) == expected_probe {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
    panic!("fixture on port {port} did not reach {expected_probe:?}");
}

#[test]
fn stale_listener_process_must_match_loopx_command_and_port() {
    let executable = "/fixture/bin/loopx";
    assert!(is_expected_loopx_listener_command(
        ServiceKind::Status,
        executable,
        8766,
        "/usr/bin/python3 /fixture/bin/loopx serve-status --global-registry --host 127.0.0.1 --port 8766",
    ));
    assert!(is_expected_loopx_listener_command(
        ServiceKind::Chat,
        executable,
        8767,
        "/usr/bin/python3 -m loopx.cli chat --global-registry --host 127.0.0.1 --port=8767 --no-open",
    ));
    assert!(is_expected_loopx_listener_command(
        ServiceKind::Status,
        executable,
        8766,
        r#"/opt/loopx/bin/python -c import os\012import runpy\012release_root = os.environ["LOOPX_RELEASE_ROOT"]\012runpy.run_module("loopx.cli", run_name="__main__")\012 --registry /tmp/registry.json serve-status --global-registry --host 127.0.0.1 --port 8766 --limit 80"#,
    ));
    assert!(is_expected_loopx_listener_command(
        ServiceKind::Status,
        executable,
        8766,
        r#"/opt/loopx/bin/python -c import os\012import runpy\012release_root = os.environ["LOOPX_RELEASE_ROOT"]\012sys.argv[0] = os.path.join(release_root, "scripts", "loopx")\012module = (\012    "loopx.entrypoint"\012    if os.path.isfile(os.path.join(release_root, "loopx", "entrypoint.py"))\012    else "loopx.cli"\012)\012runpy.run_module(module, run_name="__main__")\012 --registry /tmp/registry.json serve-status --global-registry --host 127.0.0.1 --port 8766 --limit 80"#,
    ));
    assert!(is_expected_loopx_listener_command(
        ServiceKind::Chat,
        executable,
        8767,
        r#"/opt/loopx/bin/python -c LOOPX_MANAGED_RELEASE_LAUNCHER_V1 = True\012release_root = os.environ["LOOPX_RELEASE_ROOT"]\012runpy.run_module(next_module, run_name="__main__")\012 --registry /tmp/registry.json chat --global-registry --host 127.0.0.1 --port 8767 --no-open"#,
    ));
    assert!(!is_expected_loopx_listener_command(
        ServiceKind::Status,
        executable,
        8766,
        "/tmp/not-loopx serve-status --port 8766",
    ));
    assert!(!is_expected_loopx_listener_command(
        ServiceKind::Status,
        executable,
        8766,
        "/tmp/not-loopx --config /fixture/bin/loopx serve-status --port 8766",
    ));
    assert!(!is_expected_loopx_listener_command(
        ServiceKind::Status,
        executable,
        8766,
        "/bin/sh -m loopx.cli serve-status --port 8766",
    ));
    assert!(!is_expected_loopx_listener_command(
        ServiceKind::Status,
        executable,
        8766,
        "/usr/bin/python3 /fixture/bin/loopx chat --port 8766",
    ));
    assert!(!is_expected_loopx_listener_command(
        ServiceKind::Status,
        executable,
        8766,
        "/usr/bin/python3 /fixture/bin/loopx serve-status --port 8767",
    ));
    assert!(!is_expected_loopx_listener_command(
        ServiceKind::Status,
        executable,
        8766,
        r#"/opt/loopx/bin/python -c runpy.run_module("other.cli", run_name="__main__") serve-status --port 8766"#,
    ));
    assert!(!is_expected_loopx_listener_command(
        ServiceKind::Status,
        executable,
        8766,
        r#"/opt/loopx/bin/python -c release_root = os.environ["LOOPX_RELEASE_ROOT"]\012print("loopx.entrypoint")\012runpy.run_module(module, run_name="__main__") serve-status --port 8766"#,
    ));
}

#[test]
fn release_launchers_publish_the_stable_process_fingerprint() {
    let posix_launcher = include_str!("../../../../../scripts/loopx");
    let portable_entry = include_str!("../../../../../scripts/loopx_entry.py");

    assert!(posix_launcher.contains(MANAGED_RELEASE_LAUNCHER_MARKER));
    assert!(portable_entry.contains(MANAGED_RELEASE_LAUNCHER_MARKER));
}

#[test]
fn platform_managed_services_have_stable_launchd_labels() {
    assert_eq!(
        platform_managed_service_label(ServiceKind::Status),
        "com.loopx.status"
    );
    assert_eq!(
        platform_managed_service_label(ServiceKind::Chat),
        "com.loopx.chat"
    );
}

#[test]
fn stale_listener_verification_rejects_the_entire_pid_set_on_mismatch() {
    let executable = "/fixture/bin/loopx";
    let confirmed = ListenerProcess {
        pid: 11,
        command_line: format!("{executable} serve-status --port 8766"),
    };
    assert_eq!(
        verified_loopx_listener_pids(ServiceKind::Status, executable, 8766, &[confirmed])
            .expect("confirmed LoopX listener"),
        vec![11]
    );

    let mixed = [
        ListenerProcess {
            pid: 11,
            command_line: format!("{executable} serve-status --port 8766"),
        },
        ListenerProcess {
            pid: 12,
            command_line: "foreign-server --port 8766".to_string(),
        },
    ];
    let error = verified_loopx_listener_pids(ServiceKind::Status, executable, 8766, &mixed)
        .expect_err("mixed ownership must fail closed");
    assert!(error.to_string().contains("refusing to stop"));
}

#[cfg(not(windows))]
#[test]
fn service_supervisor_reuses_matching_replaces_stale_and_rejects_foreign() {
    let unique = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock must follow the Unix epoch")
        .as_nanos();
    let fixture_root = env::temp_dir().join(format!(
        "loopx-service-supervisor-{}-{unique}",
        std::process::id()
    ));
    fs::create_dir_all(&fixture_root).expect("create service supervisor fixture");
    let current_identity = serde_json::json!({
        "schema_version": "loopx_runtime_identity_v1",
        "package_version": "0.5.1",
        "release_id": "current-release",
        "source_revision": "current-revision",
    });

    let matching_port = reserve_loopback_port();
    let matching_executable = fixture_root.join("matching-loopx");
    let matching_payload = serde_json::json!({
        "source": "serve-status",
        "runtime_identity": current_identity,
    })
    .to_string();
    let mut matching = spawn_listener_fixture(
        &matching_executable,
        "serve-status",
        matching_port,
        &matching_payload,
    );
    wait_for_probe(
        ServiceKind::Status,
        matching_port,
        Some(&current_identity),
        Probe::Matching,
    );
    assert!(matching
        .try_wait()
        .expect("matching fixture status")
        .is_none());
    matching.kill().expect("stop matching fixture");
    matching.wait().expect("reap matching fixture");

    let stale_port = reserve_loopback_port();
    let stale_executable = fixture_root.join("loopx");
    let stale_payload = serde_json::json!({
        "source": "serve-status",
        "runtime_identity": {
            "schema_version": "loopx_runtime_identity_v1",
            "package_version": "0.5.0",
            "release_id": "stale-release",
            "source_revision": "stale-revision",
        },
    })
    .to_string();
    let mut stale = spawn_listener_fixture(
        &stale_executable,
        "serve-status",
        stale_port,
        &stale_payload,
    );
    wait_for_probe(
        ServiceKind::Status,
        stale_port,
        Some(&current_identity),
        Probe::Stale,
    );
    let stale_processes = listener_processes(stale_port).expect("inspect stale listener fixture");
    assert!(
        stale_processes
            .iter()
            .all(|process| is_expected_loopx_listener_command(
                ServiceKind::Status,
                stale_executable.to_string_lossy().as_ref(),
                stale_port,
                &process.command_line,
            )),
        "fixture process must classify as LoopX: {stale_processes:?}"
    );
    terminate_verified_listener(
        ServiceKind::Status,
        stale_executable.to_string_lossy().as_ref(),
        stale_port,
    )
    .expect("replace confirmed stale LoopX listener");
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline && stale.try_wait().expect("stale fixture status").is_none() {
        thread::sleep(Duration::from_millis(50));
    }
    assert!(stale
        .try_wait()
        .expect("stale fixture final status")
        .is_some());

    let foreign_port = reserve_loopback_port();
    let foreign_executable = fixture_root.join("foreign-server");
    let mut foreign = spawn_listener_fixture(
        &foreign_executable,
        "serve-status",
        foreign_port,
        r#"{"source":"other"}"#,
    );
    wait_for_probe(
        ServiceKind::Status,
        foreign_port,
        Some(&current_identity),
        Probe::Foreign,
    );
    let error = terminate_verified_listener(
        ServiceKind::Status,
        stale_executable.to_string_lossy().as_ref(),
        foreign_port,
    )
    .expect_err("foreign listener must be rejected");
    assert!(error.to_string().contains("refusing to stop"));
    assert!(foreign
        .try_wait()
        .expect("foreign fixture status")
        .is_none());
    foreign.kill().expect("stop foreign fixture");
    foreign.wait().expect("reap foreign fixture");

    // A silent listener must be distinct from a foreign HTTP response. Only
    // the expected LoopX process may be terminated after the startup grace.
    for confirmed in [true, false] {
        let port = reserve_loopback_port();
        let executable = fixture_root.join(if confirmed { "loopx" } else { "silent-foreign" });
        let mut silent = spawn_listener_fixture(&executable, "chat", port, "");
        wait_for_probe(
            ServiceKind::Chat,
            port,
            Some(&current_identity),
            Probe::Unresponsive,
        );
        let result = terminate_verified_listener(
            ServiceKind::Chat,
            stale_executable.to_string_lossy().as_ref(),
            port,
        );
        if confirmed {
            result.expect("replace a confirmed but unresponsive LoopX service");
        } else {
            assert!(result.is_err());
            assert!(silent
                .try_wait()
                .expect("foreign listener status")
                .is_none());
            silent.kill().expect("clean up foreign fixture");
        }
        silent.wait().expect("reap silent fixture");
    }

    fs::remove_dir_all(&fixture_root).expect("remove service supervisor fixture");
}

#[test]
fn pending_service_label_names_one_service_and_the_concurrent_set() {
    // A single pending service keeps its own name so a stalled connection is
    // still diagnosable; a set that is connecting together has no single name.
    assert_eq!(ServiceKind::pending_label(&[ServiceKind::Status]), "status");
    assert_eq!(ServiceKind::pending_label(&[ServiceKind::Chat]), "chat");
    assert_eq!(ServiceKind::pending_label(&SERVICE_KINDS), "local");
    assert_eq!(ServiceKind::pending_label(&[]), "local");
}

#[test]
fn service_connections_run_concurrently_and_name_the_remaining_service() {
    // Concurrency is the contract, not a timing coincidence: each connection
    // must be able to observe its peer in flight. A sequential implementation
    // can never satisfy the peer wait, and fails on the bounded timeout
    // instead of hanging the suite.
    let in_flight = std::sync::Mutex::new(0usize);
    let peer_arrived = std::sync::Condvar::new();
    let saw_peer = Mutex::new(Vec::new());
    let published = Mutex::new(Vec::new());

    let outcomes = connect_all(
        SERVICE_KINDS,
        |kind| {
            let mut count = in_flight.lock().expect("in-flight lock");
            *count += 1;
            peer_arrived.notify_all();
            let mut timed_out = false;
            while *count < SERVICE_KINDS.len() && !timed_out {
                let (waited, timeout) = peer_arrived
                    .wait_timeout(count, Duration::from_secs(10))
                    .expect("peer wait");
                count = waited;
                timed_out = timeout.timed_out();
            }
            let observed = *count >= SERVICE_KINDS.len();
            drop(count);
            saw_peer
                .lock()
                .expect("observation lock")
                .push((kind, observed));
            ServiceOutcome {
                owned: None,
                healed: false,
                result: Ok(()),
            }
        },
        |pending| {
            published
                .lock()
                .expect("published lock")
                .push(pending.to_vec())
        },
    );

    assert!(outcomes.iter().all(|outcome| outcome.result.is_ok()));
    let saw_peer = saw_peer.into_inner().expect("observation lock");
    assert_eq!(saw_peer.len(), SERVICE_KINDS.len());
    assert!(
        saw_peer.iter().all(|(_, observed)| *observed),
        "every service must connect while its peer is still in flight: {saw_peer:?}"
    );

    // The boot page first sees the set connecting together, then the single
    // service whose connection outlived its peer. A finished set publishes
    // nothing, because there is no remaining service to name.
    let published = published.into_inner().expect("published lock");
    assert_eq!(published.len(), 2, "{published:?}");
    assert_eq!(published[0], SERVICE_KINDS.to_vec());
    assert_eq!(published[1].len(), 1, "{published:?}");
}

#[cfg(unix)]
#[test]
fn a_failed_service_set_stops_the_child_its_peer_started() {
    // Ownership must travel with every outcome: a peer that failed still
    // leaves this App responsible for the process it already spawned.
    let mut command = Command::new("sh");
    command
        .args(["-c", "exec sleep 30"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let child = command.group_spawn().expect("spawn owned service fixture");
    let pid = child.id();

    let Err(error) = ServiceSet::collect([
        ServiceOutcome {
            owned: Some(OwnedService { child }),
            healed: false,
            result: Ok(()),
        },
        ServiceOutcome {
            owned: None,
            healed: false,
            result: Err(ServiceError("LoopX chat did not become ready".into())),
        },
    ]) else {
        panic!("a failed peer must fail the whole set");
    };
    assert!(error.to_string().contains("did not become ready"));

    let mut alive = true;
    for _ in 0..50 {
        if !Command::new("kill")
            .args(["-0", &pid.to_string()])
            .stderr(Stdio::null())
            .status()
            .expect("kill -0")
            .success()
        {
            alive = false;
            break;
        }
        thread::sleep(Duration::from_millis(20));
    }
    assert!(!alive, "owned service {pid} outlived the failed set");
}
