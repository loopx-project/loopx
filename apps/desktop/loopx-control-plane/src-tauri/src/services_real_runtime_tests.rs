//! Opt-in production supervisor + installed Core. Never use an active registry.
use super::*;
use std::os::unix::fs::PermissionsExt;

fn response_body(port: u16, path: &str) -> String {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    write!(
        stream,
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    )
    .unwrap();
    let mut raw = String::new();
    stream
        .take(MAX_PROBE_RESPONSE_BYTES + 1)
        .read_to_string(&mut raw)
        .unwrap();
    assert!(raw.len() as u64 <= MAX_PROBE_RESPONSE_BYTES);
    let (headers, body) = raw.split_once("\r\n\r\n").unwrap();
    assert!(headers.starts_with("HTTP/1.0 200 ") || headers.starts_with("HTTP/1.1 200 "));
    body.to_owned()
}

fn assert_stopped(endpoints: ServiceEndpoints) {
    for kind in SERVICE_KINDS {
        assert_eq!(
            probe_on_port(kind, endpoints.port(kind), None),
            Probe::Unavailable
        );
    }
}

#[test]
#[ignore = "requires a disposable Core install and empty isolated registry; see Desktop README"]
fn real_private_runtime_serves_and_rejects_stale_identity() {
    assert_eq!(
        env::var("LOOPX_USAGE_PING").as_deref(),
        Ok("0"),
        "synthetic Core qualification requires LOOPX_USAGE_PING=0"
    );
    let root = PathBuf::from(env::var("LOOPX_TEST_PRIVATE_RUNTIME").unwrap());
    // A caller's empty registry can still share a runtime with bound private
    // conversations. Own a fresh data root rather than trusting that shortcut.
    let data = tempfile::tempdir().unwrap();
    let state = fs::canonicalize(data.path()).unwrap();
    let registry = state.join("registry.global.json");
    fs::write(&registry, b"{\"goals\":[]}").unwrap();
    let executable = fs::canonicalize(root.join("bin/loopx")).unwrap();
    let identity = runtime_identity_for_executable(executable.to_str().unwrap()).unwrap();

    // The production service commands use --global-registry. An explicit local
    // launch pin supplies their real CLI route, not a second identity/provider.
    // Use shell quoting for paths, including spaces, quotes and dollar signs.
    let quoted = |path: &Path| format!("'{}'", path.to_str().unwrap().replace('\'', "'\"'\"'"));
    let wrapper = tempfile::tempdir().unwrap();
    let pin = wrapper.path().join("isolated-core");
    fs::write(
        &pin,
        format!(
            "#!/bin/sh\nexec {} --registry {} --runtime-root {} \"$@\"\n",
            quoted(&executable),
            quoted(&registry),
            quoted(&state)
        ),
    )
    .unwrap();
    fs::set_permissions(&pin, fs::Permissions::from_mode(0o700)).unwrap();
    let runtime = SelectedRuntime {
        executable: pin.to_string_lossy().into_owned(),
        identity: Some(identity.clone()),
    };
    let mut origins = Vec::new();
    for _ in 0..2 {
        let endpoints = ServiceEndpoints::allocate(true).unwrap();
        let mut services = ServiceSet::start(&runtime, &endpoints, |_| {}).unwrap();
        for kind in SERVICE_KINDS {
            assert_eq!(
                probe_on_port(kind, endpoints.port(kind), Some(&identity)),
                Probe::Matching
            );
        }
        let status: serde_json::Value = serde_json::from_str(&response_body(
            endpoints.port(ServiceKind::Status),
            "/status.json",
        ))
        .unwrap();
        assert_eq!(status["goal_count"], 0);
        assert_eq!(
            status["registry"].as_str(),
            registry.to_str(),
            "the real backend must use only this disposable registry"
        );
        let sessions: serde_json::Value = serde_json::from_str(&response_body(
            endpoints.port(ServiceKind::Chat),
            "/api/chat/sessions",
        ))
        .unwrap();
        assert!(sessions["sessions"].as_array().is_some_and(Vec::is_empty));
        assert!(response_body(endpoints.port(ServiceKind::Chat), "/chat/")
            .to_ascii_lowercase()
            .contains("<!doctype html>"));
        origins.push(endpoints.workspace_origin());
        services.stop();
        assert_stopped(endpoints);
    }

    // The production owner must reject an actual backend from the wrong frozen
    // artifact and clean up both children, including a successful peer start.
    let mut mismatched = identity.clone();
    mismatched["source_revision"] = serde_json::json!("0000000000000000000000000000000000000000");
    let invalid = SelectedRuntime {
        executable: runtime.executable.clone(),
        identity: Some(mismatched),
    };
    let endpoints = ServiceEndpoints::allocate(true).unwrap();
    let error = ServiceSet::start(&invalid, &endpoints, |_| {})
        .err()
        .expect("a stale artifact must fail readiness");
    assert!(error.to_string().contains("unexpected listener"));
    assert_stopped(endpoints);
    println!(
        "{}",
        serde_json::json!({"private_service_reopens":origins.len(), "workspace_origins":origins, "runtime_identity":identity, "goals":0, "sessions":0, "stale_identity_rejected":true, "owned_children_stopped":true})
    );
}
