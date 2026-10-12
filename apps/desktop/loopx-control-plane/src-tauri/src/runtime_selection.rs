//! Native launch preference only; installation identity remains owned by Core.
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
};
use tauri::{AppHandle, Manager};

pub(crate) struct Selection {
    pub executable: String,
    pub explicit: bool,
    pub environment_override: bool,
}

fn preference_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_local_data_dir()
        .map_err(|_| "runtime_selection_unavailable")?
        .join("runtime-selection.json"))
}

fn read_preference(path: &Path) -> Result<Option<String>, String> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("runtime_selection_unavailable".into()),
    };
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| "runtime_selection_invalid")?;
    let executable = value["executable"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or("runtime_selection_invalid")?;
    if value["schema_version"] != "desktop_runtime_selection_v1"
        || !Path::new(executable).is_absolute()
    {
        return Err("runtime_selection_invalid".into());
    }
    Ok(Some(executable.to_owned()))
}

pub(crate) fn selected(app: &AppHandle) -> Result<Selection, String> {
    if let Ok(executable) = std::env::var("LOOPX_BIN") {
        if !executable.trim().is_empty() {
            let executable = crate::services::loopx_executable();
            let executable = fs::canonicalize(&executable)
                .map(|path| path.to_string_lossy().into_owned())
                .unwrap_or(executable);
            return Ok(Selection {
                executable,
                explicit: true,
                environment_override: true,
            });
        }
    }
    // A corrupt preference is not an installation failure. Automatic discovery
    // can still find a usable CLI or prepare the App-owned runtime.
    let remembered = match read_preference(&preference_path(app)?) {
        Err(error) => {
            eprintln!("LoopX launch preference ignored: {error}");
            None
        }
        Ok(value) => value,
    };
    if let Some(executable) = remembered {
        return Ok(Selection {
            executable,
            explicit: true,
            environment_override: false,
        });
    }
    Ok(Selection {
        executable: crate::services::loopx_executable(),
        explicit: false,
        environment_override: false,
    })
}

fn write_preference(path: &Path, executable: &str) -> Result<(), String> {
    let executable = fs::canonicalize(executable).map_err(|_| "runtime_selection_unavailable")?;
    let value = json!({"schema_version":"desktop_runtime_selection_v1",
        "executable":executable.to_string_lossy()});
    let directory = path.parent().ok_or("runtime_selection_unavailable")?;
    fs::create_dir_all(directory).map_err(|_| "runtime_selection_unavailable")?;
    let mut file =
        tempfile::NamedTempFile::new_in(directory).map_err(|_| "runtime_selection_unavailable")?;
    use std::io::Write;
    file.write_all(value.to_string().as_bytes())
        .map_err(|_| "runtime_selection_unavailable")?;
    file.as_file()
        .sync_all()
        .map_err(|_| "runtime_selection_unavailable")?;
    file.persist(path)
        .map_err(|_| "runtime_selection_unavailable")?;
    Ok(())
}

pub(crate) fn remember(app: &AppHandle, selection: &Selection) -> Result<(), String> {
    write_preference(&preference_path(app)?, &selection.executable)
}

pub(crate) fn forget(app: &AppHandle) -> Result<(), String> {
    match fs::remove_file(preference_path(app)?) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err("runtime_selection_unavailable".into()),
    }
}

fn compare_package_versions(
    package: &tauri::utils::PackageInfo,
    installed: &Value,
    candidate: &Value,
) -> Option<std::cmp::Ordering> {
    fn parse_like<T: std::str::FromStr>(_: &T, text: &str) -> Option<T> {
        text.parse().ok()
    }
    let mut left = parse_like(&package.version, installed["package_version"].as_str()?)?;
    let mut right = parse_like(&package.version, candidate["package_version"].as_str()?)?;
    // Core wheels carry the release base; main-channel shell prereleases do
    // not make that same Core version older than the stable package string.
    left.pre = Default::default();
    left.build = Default::default();
    right.pre = Default::default();
    right.build = Default::default();
    Some(left.cmp(&right))
}

// Native launch coordination only. Core continues to qualify installation
// identity. Unknown ancestry is not a claim about which runtime is newer.
pub(crate) fn compare_runtimes(
    package: &tauri::utils::PackageInfo,
    installed: &Value,
    candidate: &Value,
    compare_commits: impl FnOnce(&str, &str) -> Option<std::cmp::Ordering>,
) -> Option<std::cmp::Ordering> {
    use std::cmp::Ordering;
    match compare_package_versions(package, installed, candidate)? {
        Ordering::Equal => {
            let left = installed["source_revision"].as_str()?;
            let right = candidate["source_revision"].as_str()?;
            if left == right {
                Some(Ordering::Equal)
            } else {
                compare_commits(left, right)
            }
        }
        order => Some(order),
    }
}

pub(crate) fn is_private_runtime(executable: &str, private_executable: &Path) -> bool {
    let releases = private_executable
        .parent()
        .and_then(Path::parent)
        .and_then(|root| fs::canonicalize(root.join("releases")).ok());
    fs::canonicalize(executable)
        .ok()
        .zip(releases)
        .is_some_and(|(executable, releases)| executable.starts_with(releases))
}

// Discovery follows the installer's promoted default, not the time or name of
// a release. Only canonical sibling snapshots of the same installation qualify
// for this fallback; a checkout, another installer or an escaping symlink does
// not acquire the installation owner's authority by resembling its path.
pub(crate) fn same_release_installation(
    installed_executable: &str,
    installed: &Value,
    default_executable: &str,
    default: &Value,
) -> bool {
    fn installation(executable: &str, identity: &Value) -> Option<PathBuf> {
        let executable = fs::canonicalize(executable).ok()?;
        if executable.file_name()? != "loopx" {
            return None;
        }
        let scripts = executable.parent()?;
        if scripts.file_name()? != "scripts" {
            return None;
        }
        let release = scripts.parent()?;
        if release.file_name()?.to_str()? != identity["release_id"].as_str()? {
            return None;
        }
        let releases = release.parent()?;
        (releases.file_name()? == "releases").then(|| releases.to_path_buf())
    }
    installation(installed_executable, installed)
        .zip(installation(default_executable, default))
        .is_some_and(|(installed, default)| installed == default)
}

pub(crate) fn prefer_discovered_runtime(
    package: &tauri::utils::PackageInfo,
    installed: &Value,
    candidate: &Value,
    installer_default: bool,
    compare_commits: impl FnOnce(&str, &str) -> Option<std::cmp::Ordering>,
) -> bool {
    use std::cmp::Ordering;
    match compare_runtimes(package, installed, candidate, compare_commits) {
        Some(Ordering::Less) => true,
        Some(Ordering::Greater) => false,
        // Equal/unknown source ancestry cannot turn a cached immutable path
        // into a pin against its own installation's currently promoted CLI.
        // This follows the owner; it does not claim that source is newer.
        _ => {
            installer_default
                && compare_package_versions(package, installed, candidate) == Some(Ordering::Equal)
        }
    }
}

// A saved App-owned release path is a discovery cache, not a developer pin.
// For the same release base, the current App can maintain its own snapshot
// even when rebased sources or offline ancestry cannot be ordered. Preserve
// provably newer runtimes and independently installed CLIs. Callers still
// qualify the current bundle/installed identity before connecting services.
pub(crate) fn prefer_current_bundle(
    package: &tauri::utils::PackageInfo,
    installed: &Value,
    bundle: &Value,
    app_owned: bool,
    compare_commits: impl FnOnce(&str, &str) -> Option<std::cmp::Ordering>,
) -> bool {
    use std::cmp::Ordering;
    match compare_runtimes(package, installed, bundle, compare_commits) {
        Some(Ordering::Less) => true,
        None => {
            app_owned
                && compare_package_versions(package, installed, bundle) == Some(Ordering::Equal)
        }
        _ => false,
    }
}

pub(crate) fn compare_official_commits(left: &str, right: &str) -> Option<std::cmp::Ordering> {
    if ![left, right]
        .iter()
        .all(|revision| revision.len() == 40 && revision.bytes().all(|c| c.is_ascii_hexdigit()))
    {
        return None;
    }
    // GitHub includes file patches only on page one. Page two retains the
    // comparison relation without downloading a potentially huge source diff.
    // https://docs.github.com/en/rest/commits/commits#compare-two-commits
    let url = format!(
        "https://api.github.com/repos/loopx-project/loopx/compare/{left}...{right}?per_page=1&page=2"
    );
    let mut command = std::process::Command::new("curl");
    crate::services::configure_runtime_environment(&mut command);
    command.args([
        "--fail",
        "--silent",
        "--show-error",
        "--proto",
        "=https",
        "--max-time",
        "2",
        "--header",
        "Accept: application/vnd.github+json",
        &url,
    ]);
    let output =
        crate::services::timed_output_with_timeout(command, std::time::Duration::from_secs(3))?;
    if !output.status.success() {
        return None;
    }
    let payload: Value = serde_json::from_slice(&output.stdout).ok()?;
    match payload["status"].as_str()? {
        "ahead" => Some(std::cmp::Ordering::Less),
        "behind" => Some(std::cmp::Ordering::Greater),
        "identical" => Some(std::cmp::Ordering::Equal),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn newer_runtime_uses_versions_then_ancestry_and_never_install_time() {
        use std::cmp::Ordering;
        let package = tauri::utils::PackageInfo {
            name: "LoopX".into(),
            version: "1.2.4-main.20261004".parse().unwrap(),
            authors: "contributors",
            description: "desktop",
            crate_name: "desktop",
        };
        let identity = |version: &str, revision: Option<&str>| json!({"package_version":version,"source_revision":revision});
        for (left, right, expected) in [
            ("1.2.10", "1.2.9", Ordering::Greater),
            ("1.2.3", "1.2.4", Ordering::Less),
        ] {
            assert_eq!(
                compare_runtimes(
                    &package,
                    &identity(left, None),
                    &identity(right, None),
                    |_, _| panic!("different versions do not need a network")
                ),
                Some(expected)
            );
        }
        let old = identity("1.2.4", Some("older"));
        let main = identity("1.2.4-main.20261004", Some("newer"));
        assert_eq!(
            compare_runtimes(&package, &old, &main, |left, right| {
                assert_eq!((left, right), ("older", "newer"));
                Some(Ordering::Less)
            }),
            Some(Ordering::Less)
        );
        assert_eq!(
            compare_runtimes(&package, &old, &main, |_, _| None),
            None,
            "offline or diverged is not permission to replace"
        );
        assert_eq!(
            compare_runtimes(&package, &old, &old, |_, _| panic!(
                "same revision needs no lookup"
            )),
            Some(Ordering::Equal)
        );
        assert_eq!(
            compare_runtimes(&package, &identity("1.2.4", None), &main, |_, _| panic!(
                "wheel has no source attestation"
            )),
            None
        );
        assert_eq!(
            compare_runtimes(&package, &identity("invalid", None), &main, |_, _| panic!(
                "invalid version"
            )),
            None
        );
    }
    #[test]
    fn preference_survives_restart_but_does_not_store_an_identity_or_authority() {
        let root = tempfile::tempdir().unwrap();
        let executable = root.path().join("loopx");
        fs::write(&executable, b"installed CLI").unwrap();
        let path = root.path().join("runtime-selection.json");
        write_preference(&path, executable.to_str().unwrap()).unwrap();
        assert_eq!(
            read_preference(&path).unwrap(),
            Some(
                fs::canonicalize(&executable)
                    .unwrap()
                    .to_string_lossy()
                    .into_owned()
            )
        );
        let value: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
        assert_eq!(value.as_object().unwrap().len(), 2);
    }
    #[test]
    fn installer_default_replaces_a_cache_without_inventing_source_freshness() {
        use std::cmp::Ordering;
        let package = tauri::utils::PackageInfo {
            name: "LoopX".into(),
            version: "1.2.4".parse().unwrap(),
            authors: "contributors",
            description: "desktop",
            crate_name: "desktop",
        };
        let current = json!({"package_version":"1.2.4", "source_revision":"cached"});
        let candidate = json!({"package_version":"1.2.4", "source_revision":"default"});
        for (installer_default, relation, expected) in [
            (true, None, true),
            (false, None, false),
            (true, Some(Ordering::Greater), false),
            (false, Some(Ordering::Greater), false),
            (true, Some(Ordering::Less), true),
            (false, Some(Ordering::Less), true),
            (true, Some(Ordering::Equal), true),
            (false, Some(Ordering::Equal), false),
        ] {
            assert_eq!(
                prefer_discovered_runtime(
                    &package,
                    &current,
                    &candidate,
                    installer_default,
                    |_, _| relation
                ),
                expected,
                "installer_default={installer_default} relation={relation:?}"
            );
        }
        for (version, expected) in [("1.2.3", false), ("1.2.5", true), ("invalid", false)] {
            assert_eq!(
                prefer_discovered_runtime(
                    &package,
                    &current,
                    &json!({"package_version":version, "source_revision":"default"}),
                    true,
                    |_, _| panic!("versions precede owner fallback")
                ),
                expected
            );
        }
        assert!(prefer_discovered_runtime(
            &package,
            &current,
            &json!({"package_version":"1.2.4", "source_revision":null}),
            true,
            |_, _| panic!("following the installer needs no source attestation")
        ));
    }
    #[cfg(unix)]
    #[test]
    fn cached_release_follows_only_the_same_canonical_installation_default() {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir().unwrap();
        let old = root.path().join("owner/releases/old/scripts/loopx");
        let new = root.path().join("owner/releases/new/scripts/loopx");
        let independent = root.path().join("another/releases/new/scripts/loopx");
        let checkout = root.path().join("checkout/scripts/loopx");
        for path in [&old, &new, &independent, &checkout] {
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, "qualified by Core elsewhere").unwrap();
        }
        let launcher = root.path().join("default-loopx");
        symlink(&new, &launcher).unwrap();
        let old_identity = json!({"release_id":"old"});
        let new_identity = json!({"release_id":"new"});
        assert!(same_release_installation(
            old.to_str().unwrap(),
            &old_identity,
            launcher.to_str().unwrap(),
            &new_identity
        ));
        for path in [&independent, &checkout, &root.path().join("missing")] {
            assert!(!same_release_installation(
                old.to_str().unwrap(),
                &old_identity,
                path.to_str().unwrap(),
                &new_identity
            ));
        }
        assert!(!same_release_installation(
            old.to_str().unwrap(),
            &old_identity,
            new.to_str().unwrap(),
            &json!({"release_id":"unrelated"})
        ));
        // Text beneath the first owner's releases cannot authorize another
        // installation after a symlink escapes the canonical ownership root.
        let escape = root.path().join("owner/releases/escape");
        symlink(independent.parent().unwrap().parent().unwrap(), &escape).unwrap();
        assert!(!same_release_installation(
            old.to_str().unwrap(),
            &old_identity,
            escape.join("scripts/loopx").to_str().unwrap(),
            &new_identity
        ));
    }
    #[test]
    fn current_app_maintains_only_its_own_unordered_same_base_snapshot() {
        use std::cmp::Ordering;
        let package = tauri::utils::PackageInfo {
            name: "LoopX".into(),
            version: "1.2.4-main.20261005".parse().unwrap(),
            authors: "contributors",
            description: "desktop",
            crate_name: "desktop",
        };
        let bundle = json!({"package_version":"1.2.4-main.20261005", "source_revision":"bundle"});
        for (version, app_owned, relation, expected) in [
            ("1.2.4", true, None, true),
            ("1.2.4", false, None, false),
            ("1.2.4", true, Some(Ordering::Greater), false),
            ("1.2.4", false, Some(Ordering::Less), true),
            ("1.2.4", true, Some(Ordering::Equal), false),
        ] {
            let installed = json!({"package_version":version, "source_revision":"installed"});
            assert_eq!(
                prefer_current_bundle(&package, &installed, &bundle, app_owned, |_, _| relation),
                expected,
                "ownership={app_owned} relation={relation:?}"
            );
        }
        for (version, expected) in [("1.2.5", false), ("1.2.3", true), ("invalid", false)] {
            let installed = json!({"package_version":version, "source_revision":"installed"});
            assert_eq!(
                prefer_current_bundle(&package, &installed, &bundle, true, |_, _| panic!(
                    "different or invalid versions do not need ancestry"
                )),
                expected
            );
        }
        assert!(!prefer_current_bundle(
            &package,
            &bundle,
            &bundle,
            true,
            |_, _| panic!("the already current snapshot needs no reinstall or network")
        ));
    }
    #[cfg(unix)]
    #[test]
    fn private_release_ownership_follows_canonical_paths_not_path_text() {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir().unwrap();
        let private = root.path().join("runtime/bin/loopx");
        let release = root.path().join("runtime/releases/release/scripts/loopx");
        fs::create_dir_all(release.parent().unwrap()).unwrap();
        fs::create_dir_all(private.parent().unwrap()).unwrap();
        fs::write(&release, "qualified elsewhere").unwrap();
        symlink(&release, &private).unwrap();
        assert!(is_private_runtime(private.to_str().unwrap(), &private));
        assert!(is_private_runtime(release.to_str().unwrap(), &private));
        let external = root.path().join("runtime/releases-other-loopx");
        fs::write(&external, "independent CLI").unwrap();
        let escape = root.path().join("runtime/releases/escape");
        symlink(&external, &escape).unwrap();
        for candidate in [external, escape, root.path().join("missing")] {
            assert!(!is_private_runtime(candidate.to_str().unwrap(), &private));
        }
    }
    #[cfg(unix)]
    #[test]
    fn remembering_a_completed_promotion_replaces_the_old_release_on_restart() {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir().unwrap();
        let directory = fs::canonicalize(root.path()).unwrap();
        let old = directory.join("old-loopx");
        let new = directory.join("new-loopx");
        fs::write(&old, "old installed snapshot").unwrap();
        fs::write(&new, "qualified new snapshot").unwrap();
        let launcher = root.path().join("loopx");
        symlink(&old, &launcher).unwrap();
        let preference = root.path().join("runtime-selection.json");
        write_preference(&preference, launcher.to_str().unwrap()).unwrap();
        assert_eq!(
            read_preference(&preference).unwrap(),
            Some(old.to_string_lossy().into_owned())
        );
        fs::remove_file(&launcher).unwrap();
        symlink(&new, &launcher).unwrap();
        // This is the install result used by both startup and explicit Repair.
        write_preference(&preference, launcher.to_str().unwrap()).unwrap();
        assert_eq!(
            read_preference(&preference).unwrap(),
            Some(new.to_string_lossy().into_owned())
        );
    }
    #[test]
    fn broken_selection_is_not_treated_as_a_missing_installation() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("runtime-selection.json");
        assert!(read_preference(&path).unwrap().is_none());
        for bytes in [
            "broken",
            "{}",
            r#"{"schema_version":"desktop_runtime_selection_v1","executable":"relative/loopx","use_installed":true}"#,
        ] {
            fs::write(&path, bytes).unwrap();
            assert_eq!(
                read_preference(&path).unwrap_err(),
                "runtime_selection_invalid"
            );
        }
    }
}
