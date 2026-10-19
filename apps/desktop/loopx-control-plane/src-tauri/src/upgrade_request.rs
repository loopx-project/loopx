//! Private readback for an explicit terminal upgrade; native maintenance owns effects.
use serde_json::{json, Value};
use std::{
    io::Write,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};

pub(crate) struct UpgradeRequest {
    pub channel: String,
    id: String,
    path: PathBuf,
    version: String,
    revision: Value,
    checked: AtomicBool,
    failure: Mutex<Option<Value>>,
    runtime: Mutex<Option<Value>>,
}

impl UpgradeRequest {
    pub fn from_args(
        args: &[String],
        directory: PathBuf,
        version: String,
        revision: Value,
    ) -> Option<Self> {
        let id = args
            .iter()
            .find_map(|s| s.strip_prefix("--loopx-upgrade-request="))?;
        if id.len() != 32 || !id.bytes().all(|c| c.is_ascii_hexdigit()) {
            return None;
        }
        let channel = args
            .iter()
            .find_map(|s| s.strip_prefix("--loopx-upgrade-channel="))?;
        if !matches!(channel, "main" | "stable") {
            return None;
        }
        Some(Self {
            channel: channel.to_string(),
            id: id.to_string(),
            path: directory.join("upgrades").join(format!("{id}.json")),
            version,
            revision,
            checked: AtomicBool::new(false),
            failure: Mutex::new(None),
            runtime: Mutex::new(None),
        })
    }

    pub fn services_ready(&self, identity: Option<&Value>) {
        *self.runtime.lock().unwrap() = identity.cloned();
        self.publish("ready", &json!({}));
    }

    pub fn publish(&self, phase: &str, details: &Value) {
        if phase == "up_to_date" {
            self.checked.store(true, Ordering::Release);
        }
        let mut failure = self.failure.lock().unwrap();
        if matches!(
            phase,
            "error" | "service_error" | "runtime_required" | "runtime_pairing_required"
        ) {
            *failure = Some(json!({"phase":phase, "details":details}));
        }
        // A healthy HTTP service cannot erase a failed update/signature check.
        let mut snapshot = failure
            .as_ref()
            .cloned()
            .unwrap_or_else(|| json!({"phase":phase, "details":details}));
        if snapshot["phase"] == "up_to_date" && self.runtime.lock().unwrap().is_some() {
            snapshot["phase"] = json!("ready");
        }
        let value = json!({
            "request_id":self.id, "channel":self.channel, "app_version":self.version,
            "bundled_source_revision":self.revision, "pid":std::process::id(),
            "update_checked":self.checked.load(Ordering::Acquire),
            "runtime_identity":*self.runtime.lock().unwrap(),
            "phase":snapshot["phase"], "details":snapshot["details"],
        });
        let result = (|| -> std::io::Result<()> {
            let directory = self.path.parent().unwrap();
            std::fs::create_dir_all(directory)?;
            let mut file = tempfile::NamedTempFile::new_in(directory)?;
            file.write_all(value.to_string().as_bytes())?;
            file.persist(&self.path).map_err(|error| error.error)?;
            Ok(())
        })();
        if result.is_err() {
            eprintln!("LoopX terminal upgrade readback unavailable");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_explicit_bounded_requests_can_select_a_channel_or_write_readback() {
        let root = tempfile::tempdir().unwrap();
        for id in ["", "../outside", "a/b", "z12345678901234567890123456789012"] {
            assert!(UpgradeRequest::from_args(
                &[
                    format!("--loopx-upgrade-request={id}"),
                    "--loopx-upgrade-channel=main".into(),
                ],
                root.path().into(),
                "1.0.0".into(),
                Value::Null
            )
            .is_none());
        }
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[test]
    fn readiness_cannot_hide_an_offline_or_signature_failure() {
        let root = tempfile::tempdir().unwrap();
        let request = UpgradeRequest::from_args(
            &[
                format!("--loopx-upgrade-request={}", "a".repeat(32)),
                "--loopx-upgrade-channel=main".into(),
            ],
            root.path().into(),
            "0.0.0-main.2.1".into(),
            json!("b".repeat(40)),
        )
        .unwrap();
        request.publish(
            "error",
            &json!({"code":"update_download_or_signature_failed"}),
        );
        request.services_ready(Some(&json!({"source_revision":"c".repeat(40)})));
        let receipt: Value =
            serde_json::from_slice(&std::fs::read(&request.path).unwrap()).unwrap();
        assert_eq!(receipt["phase"], "error");
        assert_eq!(receipt["update_checked"], false);
        assert!(!receipt
            .to_string()
            .contains(&root.path().to_string_lossy().to_string()));
    }

    #[test]
    fn success_requires_channel_check_and_qualified_service_identity() {
        let root = tempfile::tempdir().unwrap();
        let request = UpgradeRequest::from_args(
            &[
                format!("--loopx-upgrade-request={}", "a".repeat(32)),
                "--loopx-upgrade-channel=stable".into(),
            ],
            root.path().into(),
            "1.0.0".into(),
            json!("b".repeat(40)),
        )
        .unwrap();
        request.publish("up_to_date", &json!({}));
        request.services_ready(Some(&json!({"source_revision":"c".repeat(40)})));
        let receipt: Value =
            serde_json::from_slice(&std::fs::read(&request.path).unwrap()).unwrap();
        assert_eq!(receipt["phase"], "ready");
        assert_eq!(receipt["update_checked"], true);
        assert_eq!(
            receipt["runtime_identity"]["source_revision"],
            "c".repeat(40)
        );
    }
}
