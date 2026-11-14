//! Disposable native updater cache. It grants no installation authority:
//! every read verifies the archive again with the running App's shipped key.
use base64::{engine::general_purpose::STANDARD, Engine};
use minisign_verify::{PublicKey, Signature};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::Path};

const INVALID: &str = "update_download_or_signature_failed";

pub(crate) struct PreparedUpdate {
    pub version: String,
    pub signature: String,
    pub bytes: Vec<u8>,
}

pub(crate) fn is_newer(current: &str, remote: &str, channel: &str) -> bool {
    let (Ok(current), Ok(remote)) = (
        semver::Version::parse(current),
        semver::Version::parse(remote),
    ) else {
        return false;
    };
    if channel == "main" && !current.pre.as_str().starts_with("main.") {
        remote.pre.as_str().starts_with("main.")
    } else {
        remote > current
    }
}

fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn archive_name(metadata: &Value) -> Result<String, String> {
    let hash = metadata["sha256"].as_str().ok_or(INVALID)?;
    if hash.len() != 64
        || !hash
            .bytes()
            .all(|v| v.is_ascii_hexdigit() && !v.is_ascii_uppercase())
    {
        return Err(INVALID.into());
    }
    Ok(format!("{hash}.tar.gz"))
}

fn metadata(root: &Path) -> Result<Option<Value>, String> {
    let path = root.join("prepared.json");
    let file = match fs::File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(INVALID.into()),
    };
    use std::io::Read;
    let value: Value = serde_json::from_reader(file.take(32 * 1024)).map_err(|_| INVALID)?;
    if value["schema_version"] != "desktop_prepared_update_v1"
        || !matches!(value["channel"].as_str(), Some("main" | "stable"))
    {
        return Err(INVALID.into());
    }
    archive_name(&value)?;
    Ok(Some(value))
}

pub(crate) fn save(
    root: &Path,
    channel: &str,
    version: &str,
    signature: &str,
    bytes: &[u8],
) -> Result<(), String> {
    if !matches!(channel, "main" | "stable") || semver::Version::parse(version).is_err() {
        return Err(INVALID.into());
    }
    fs::create_dir_all(root).map_err(|_| INVALID)?;
    let old = metadata(root).ok().flatten();
    let value = json!({"schema_version":"desktop_prepared_update_v1", "channel":channel,
        "version":version, "signature":signature, "sha256":digest(bytes)});
    let name = archive_name(&value)?;
    // Publish the pointer only after all archive bytes are durable. An
    // interrupted download/write cannot replace a previous complete cache.
    let mut archive = tempfile::NamedTempFile::new_in(root).map_err(|_| INVALID)?;
    archive.write_all(bytes).map_err(|_| INVALID)?;
    archive.as_file().sync_all().map_err(|_| INVALID)?;
    archive.persist(root.join(&name)).map_err(|_| INVALID)?;
    let mut pointer = tempfile::NamedTempFile::new_in(root).map_err(|_| INVALID)?;
    serde_json::to_writer(&mut pointer, &value).map_err(|_| INVALID)?;
    pointer.as_file().sync_all().map_err(|_| INVALID)?;
    pointer
        .persist(root.join("prepared.json"))
        .map_err(|_| INVALID)?;
    if let Some(old_name) = old
        .and_then(|v| archive_name(&v).ok())
        .filter(|v| v != &name)
    {
        let _ = fs::remove_file(root.join(old_name));
    }
    Ok(())
}

pub(crate) fn load(
    root: &Path,
    current: &str,
    channel: &str,
    public_key: &str,
) -> Result<Option<PreparedUpdate>, String> {
    read(root, current, channel, public_key, CacheRead::Consume)
}

/// Preparation may reuse a verified archive, but cannot retire another
/// channel's cache while its original workspace is still running.
pub(crate) fn peek(
    root: &Path,
    current: &str,
    channel: &str,
    public_key: &str,
) -> Result<Option<PreparedUpdate>, String> {
    read(root, current, channel, public_key, CacheRead::Reuse)
}

enum CacheRead {
    Consume,
    Reuse,
}

fn read(
    root: &Path,
    current: &str,
    channel: &str,
    public_key: &str,
    mode: CacheRead,
) -> Result<Option<PreparedUpdate>, String> {
    let Some(value) = metadata(root)? else {
        return Ok(None);
    };
    let version = value["version"].as_str().ok_or(INVALID)?;
    if value["channel"] != channel || !is_newer(current, version, channel) {
        if matches!(mode, CacheRead::Consume) {
            clear(root)?;
        }
        return Ok(None);
    }
    let bytes = fs::read(root.join(archive_name(&value)?)).map_err(|_| INVALID)?;
    if digest(&bytes) != value["sha256"] {
        return Err(INVALID.into());
    }
    let key = STANDARD.decode(public_key).map_err(|_| INVALID)?;
    let key =
        PublicKey::decode(std::str::from_utf8(&key).map_err(|_| INVALID)?).map_err(|_| INVALID)?;
    let signature = STANDARD
        .decode(value["signature"].as_str().ok_or(INVALID)?)
        .map_err(|_| INVALID)?;
    let signature = Signature::decode(std::str::from_utf8(&signature).map_err(|_| INVALID)?)
        .map_err(|_| INVALID)?;
    key.verify(&bytes, &signature, true).map_err(|_| INVALID)?;
    Ok(Some(PreparedUpdate {
        version: version.into(),
        signature: value["signature"].as_str().ok_or(INVALID)?.into(),
        bytes,
    }))
}

pub(crate) fn clear(root: &Path) -> Result<(), String> {
    let previous = metadata(root)
        .ok()
        .flatten()
        .and_then(|v| archive_name(&v).ok());
    match fs::remove_file(root.join("prepared.json")) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err(INVALID.into()),
    }
    if let Some(name) = previous {
        let _ = fs::remove_file(root.join(name));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn update_order_matches_explicit_channel_selection() {
        assert!(is_newer("1.0.0", "1.1.0", "stable"));
        assert!(!is_newer("1.1.0", "1.0.0", "stable"));
        assert!(!is_newer("1.1.0", "1.1.0", "stable"));
        assert!(is_newer("1.1.0", "0.0.0-main.20261019", "main"));
        assert!(!is_newer(
            "0.0.0-main.20261019",
            "0.0.0-main.20261018",
            "main"
        ));
        assert!(!is_newer("1.1.0", "invalid", "stable"));
    }

    #[test]
    fn missing_cache_is_ordinary_and_obsolete_cache_is_retired() {
        let root = tempfile::tempdir().unwrap();
        assert!(load(root.path(), "1.0.0", "stable", "").unwrap().is_none());
        save(root.path(), "stable", "1.1.0", "unused", b"old").unwrap();
        assert!(load(root.path(), "1.2.0", "stable", "").unwrap().is_none());
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[test]
    fn cached_data_without_a_valid_shipped_signature_is_never_activated() {
        let root = tempfile::tempdir().unwrap();
        save(root.path(), "stable", "1.1.0", "invalid", b"untrusted").unwrap();
        assert!(load(root.path(), "1.0.0", "stable", "invalid").is_err());
        fs::write(
            root.path().join(format!("{}.tar.gz", digest(b"untrusted"))),
            b"changed",
        )
        .unwrap();
        assert!(load(root.path(), "1.0.0", "stable", "invalid").is_err());
    }

    #[test]
    fn invalid_metadata_cannot_select_an_arbitrary_file() {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("prepared.json"),
            json!({
            "schema_version":"desktop_prepared_update_v1", "channel":"stable",
            "version":"1.1.0", "sha256":"../../outside"})
            .to_string(),
        )
        .unwrap();
        assert!(load(root.path(), "1.0.0", "stable", "").is_err());
        clear(root.path()).unwrap();
    }

    #[test]
    fn replacing_a_prepared_download_preserves_only_the_new_complete_archive() {
        let root = tempfile::tempdir().unwrap();
        save(root.path(), "stable", "1.1.0", "signature", b"first").unwrap();
        save(root.path(), "stable", "1.2.0", "signature", b"second").unwrap();
        let value = metadata(root.path()).unwrap().unwrap();
        assert_eq!(value["version"], "1.2.0");
        assert_eq!(
            fs::read(root.path().join(archive_name(&value).unwrap())).unwrap(),
            b"second"
        );
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 2);
        clear(root.path()).unwrap();
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[test]
    #[ignore = "requires LOOPX_TEST_SIGNED_ARCHIVE from an actual signed release"]
    fn signed_release_cache_is_reverified_after_reopen_and_rejects_tampering() {
        let archive = std::env::var("LOOPX_TEST_SIGNED_ARCHIVE").unwrap();
        let bytes = fs::read(&archive).unwrap();
        let signature = fs::read_to_string(format!("{archive}.sig")).unwrap();
        let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let key = config["plugins"]["updater"]["pubkey"].as_str().unwrap();
        let root = tempfile::tempdir().unwrap();
        save(root.path(), "stable", "1.1.0", signature.trim(), &bytes).unwrap();
        assert_eq!(
            load(root.path(), "1.0.0", "stable", key)
                .unwrap()
                .unwrap()
                .bytes,
            bytes
        );
        // Even relabelling a modified archive with its new digest cannot pass
        // the retained publisher signature on the next process's read.
        let mut changed = bytes;
        changed[0] ^= 1;
        save(root.path(), "stable", "1.1.0", signature.trim(), &changed).unwrap();
        assert!(load(root.path(), "1.0.0", "stable", key).is_err());
    }
}
