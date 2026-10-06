//! macOS rollback is scoped to this exact App bundle, never a caller path.
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};
use tauri::{AppHandle, Manager};

pub(crate) fn app_bundle() -> Result<PathBuf, String> {
    app_bundle_at(&std::env::current_exe().map_err(|_| "app_bundle_required")?)
}

// Locate the trusted install target for the running executable: the nearest
// `.app` ancestor directory. This is the target boundary rollback swaps into,
// derived from the running executable the same way `app_bundle` derives the
// verified bundle — never from a caller-supplied path.
//
// Boundary only: it must NOT require the installed App to be intact. Rollback
// exists precisely to repair a damaged installation (missing executable,
// missing sealed resource), so requiring the broken target to pass integrity
// checks here would make the recovery button unable to fix the state it is
// offered for.
pub(crate) fn installed_bundle_at(executable: &Path) -> Result<PathBuf, String> {
    let bundle = executable
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .ok_or("app_bundle_required")?;
    if bundle.extension().and_then(|s| s.to_str()) != Some("app") {
        return Err("app_bundle_required".into());
    }
    Ok(bundle.to_path_buf())
}

// The pinned macOS updater moves the old App bundle away before moving the new
// one in, so callers that must verify the installed App is still in place
// (failed replacement recovery) resolve it from the running executable.
// Path-level so synthetic App layouts are testable without a signed build.
//
// This is a layout-and-runnability check, not a signature check: the bundle
// must keep its Info.plist AND its executable. A partially removed bundle —
// executable deleted while Info.plist (and the runtime identity) survives a
// failed replacement — must not verify, or the safe-restart predicate would
// discard the recovery journal over an unbootable App. Signature integrity is
// layered on top by `installed_bundle_verifies`, which shares the exact
// `codesign --verify` check the backup `copy` boundary uses.
pub(crate) fn app_bundle_at(executable: &Path) -> Result<PathBuf, String> {
    let bundle = installed_bundle_at(executable)?;
    if !bundle.join("Contents/Info.plist").is_file()
        || !bundle_executable_is_present(executable, &bundle)
    {
        return Err("app_bundle_required".into());
    }
    Ok(bundle)
}

// Actual-installed-target integrity for the safe-restart predicate: layout
// verification is necessary but not sufficient — a bundle whose Info.plist and
// executable survive while a sealed resource was deleted still passes the
// layout check, yet `codesign --verify --deep --strict` rejects it. This is
// the same signature verification `copy` applies to a fresh backup, applied to
// the installed App itself; a previous backup copy's verification can never
// substitute for verifying the current installation.
pub(crate) fn installed_bundle_verifies(executable: &Path) -> bool {
    app_bundle_at(executable)
        .map(|bundle| signature_verifies(&bundle))
        .unwrap_or(false)
}

// Shared signature/integrity gate for both bundle consumers: the verified
// backup `copy` writes, and the safe-restart predicate's verification of the
// actually installed App.
fn signature_verifies(bundle: &Path) -> bool {
    Command::new("codesign")
        .args(["--verify", "--deep", "--strict"])
        .arg(bundle)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

#[cfg(all(test, target_os = "macos"))]
pub(crate) fn signature_verifies_for_test(bundle: &Path) -> bool {
    signature_verifies(bundle)
}

// The running binary keeps executing after the updater deletes it, so a
// missing file at the executable's own path is exactly the "old bundle was
// moved away / partially removed" state layout verification must reject.
// When Info.plist declares CFBundleExecutable, that declared binary must
// exist too: the bundle launches what Info.plist names, not any surviving
// neighbor. An unreadable (e.g. binary) plist skips the declared-name check
// without weakening the executable-presence check above.
fn bundle_executable_is_present(executable: &Path, bundle: &Path) -> bool {
    if !executable.is_file() {
        return false;
    }
    match declared_bundle_executable(bundle) {
        Some(declared) => {
            !declared.is_empty() && bundle.join("Contents/MacOS").join(declared).is_file()
        }
        None => true,
    }
}

fn declared_bundle_executable(bundle: &Path) -> Option<String> {
    let plist = fs::read_to_string(bundle.join("Contents/Info.plist")).ok()?;
    let key = "<key>CFBundleExecutable</key>";
    let rest = &plist[plist.find(key)? + key.len()..];
    let open = rest.find("<string>")? + "<string>".len();
    let value = &rest[open..];
    let close = value.find("</string>")?;
    Some(value[..close].trim().to_string())
}

fn root(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_local_data_dir()
        .map_err(|_| "backup_unavailable")?
        .join("update-backup"))
}
fn copy(source: &Path, destination: &Path) -> Result<(), String> {
    let status = Command::new("ditto")
        .arg(source)
        .arg(destination)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|_| "backup_failed")?;
    if !status.success() {
        return Err("backup_failed".into());
    }
    if !signature_verifies(destination) {
        return Err("backup_failed".into());
    }
    Ok(())
}

fn recoverable_backup(root: &Path) -> Option<PathBuf> {
    let previous = root.join("previous");
    if previous.join("LoopX.app/Contents/Info.plist").is_file() {
        return Some(previous);
    }
    fs::read_dir(root)
        .ok()?
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let sequence = entry
                .file_name()
                .to_str()?
                .strip_prefix("older-")?
                .parse::<u128>()
                .ok()?;
            let path = entry.path();
            path.join("LoopX.app/Contents/Info.plist")
                .is_file()
                .then_some((sequence, path))
        })
        .max_by_key(|(sequence, _)| *sequence)
        .map(|(_, path)| path)
}

pub fn available(app: &AppHandle) -> bool {
    cfg!(target_os = "macos") && root(app).is_ok_and(|root| recoverable_backup(&root).is_some())
}

pub fn prepare(app: &AppHandle) -> Result<(), String> {
    if !cfg!(target_os = "macos") {
        return Ok(());
    }
    let bundle = app_bundle()?;
    let root = root(app)?;
    fs::create_dir_all(&root).map_err(|_| "backup_failed")?;
    let temporary = tempfile::tempdir_in(&root).map_err(|_| "backup_failed")?;
    copy(&bundle, &temporary.path().join("LoopX.app"))?;
    fs::write(
        temporary.path().join("version"),
        app.package_info().version.to_string(),
    )
    .map_err(|_| "backup_failed")?;
    let previous = root.join("previous");
    // Retain the old backup until the new verified backup is durable. This
    // directory contains only backups generated by this updater.
    if previous.exists() {
        let older = root.join(format!(
            "older-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis()
        ));
        fs::rename(&previous, older).map_err(|_| "backup_failed")?;
    }
    fs::rename(temporary.path(), previous).map_err(|_| "backup_failed")?;
    Ok(())
}

pub fn restore(app: &AppHandle) -> Result<(), String> {
    if !available(app) {
        return Err("backup_unavailable".into());
    }
    // The trusted target boundary comes from the running executable — the
    // same derivation the seam performs — never from a caller path. The
    // damaged installed App is exactly what rollback repairs, so locating it
    // must not require it to be intact; the verified backup source is what
    // must pass verification (`copy` re-checks its codesign signature).
    let executable = std::env::current_exe().map_err(|_| "app_bundle_required")?;
    let backup = recoverable_backup(&root(app)?).ok_or("backup_unavailable")?;
    let version = fs::read_to_string(backup.join("version")).map_err(|_| "backup_unavailable")?;
    let handle = app.clone();
    restore_verified_backup(&executable, &backup, move || {
        crate::bundled_runtime::record_pending(&handle, version.trim(), "rollback")
    })
}

// The restore seam, entered from the running executable exactly like the
// production `restore` so the locator decision itself is under test: locate
// the trusted install target boundary (never requiring the damaged target to
// be intact), copy the verified backup beside it, let the caller record its
// continuation journal (`before_swap`), then swap. The backup source is
// verified through `copy`'s shared signature gate before anything is swapped.
pub(crate) fn restore_verified_backup(
    executable: &Path,
    previous: &Path,
    before_swap: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    if !previous.join("LoopX.app/Contents/Info.plist").is_file() {
        return Err("backup_unavailable".into());
    }
    let target = installed_bundle_at(executable)?;
    let staging = tempfile::tempdir_in(target.parent().ok_or("app_bundle_required")?)
        .map_err(|_| "rollback_failed")?;
    let candidate = staging.path().join("LoopX.app");
    copy(&previous.join("LoopX.app"), &candidate)?;
    let failed = staging.path().join("failed.app");
    // Preserve staging before the swap. After a successful exchange it owns
    // the displaced App; after an exchange failure it owns the verified copy.
    let _preserved = staging.keep();
    before_swap()?;
    replace_bundle(&target, &candidate, &failed)?;
    // Keep the displaced App recoverable too. Never delete an installed App.
    Ok(())
}

fn replace_bundle(bundle: &Path, candidate: &Path, failed: &Path) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        // The staging directory is created beside the installed App, so both
        // paths are on one volume. RENAME_SWAP keeps the install path occupied
        // before, during, and after the only commit operation.
        rustix::fs::renameat_with(
            rustix::fs::CWD,
            bundle,
            rustix::fs::CWD,
            candidate,
            rustix::fs::RenameFlags::EXCHANGE,
        )
        .map_err(|_| "rollback_failed")?;
        #[cfg(test)]
        wait_for_bundle_swap_kill();
        // The old App is already safe at `candidate`; this rename only gives
        // it the expected recovery name inside the preserved staging directory.
        let _ = fs::rename(candidate, failed);
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    {
        fs::rename(bundle, failed).map_err(|_| "rollback_failed")?;
        if fs::rename(candidate, bundle).is_err() {
            let _ = fs::rename(failed, bundle);
            return Err("rollback_failed".into());
        }
        Ok(())
    }
}

#[cfg(all(test, target_os = "macos"))]
fn wait_for_bundle_swap_kill() {
    let Some(root) = std::env::var_os("LOOPX_TEST_KILL_DURING_BUNDLE_SWAP") else {
        return;
    };
    fs::write(PathBuf::from(root).join("swap-committed"), []).unwrap();
    loop {
        std::thread::park();
    }
}

// Test-only synthetic App factory shared by the backup and maintenance test
// modules: builds a real ad-hoc-signed bundle so the shared
// `codesign --verify --deep --strict` gate is exercised exactly as it is on
// the installed App.
#[cfg(all(test, target_os = "macos"))]
pub(crate) mod signed_app_test_support {
    use std::{
        fs,
        path::{Path, PathBuf},
        process::{Command, Stdio},
    };

    // A real Mach-O executable (copied from the system shell), a declaring
    // Info.plist, and one sealed resource, signed with `codesign --sign -`:
    // deleting any sealed content must fail verification exactly as it would
    // for the installed App.
    pub(crate) fn ad_hoc_signed_synthetic_app(dir: &Path, name: &str) -> PathBuf {
        let app = dir.join(name);
        fs::create_dir_all(app.join("Contents/MacOS")).unwrap();
        fs::create_dir_all(app.join("Contents/Resources")).unwrap();
        fs::copy("/bin/sh", app.join("Contents/MacOS/loopx-control-plane")).unwrap();
        fs::write(
            app.join("Contents/Info.plist"),
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\
             <plist version=\"1.0\"><dict>\
             <key>CFBundleIdentifier</key><string>com.loopx.test.synthetic</string>\
             <key>CFBundleExecutable</key><string>loopx-control-plane</string>\
             </dict></plist>",
        )
        .unwrap();
        fs::write(app.join("Contents/Resources/sealed-resource.txt"), "sealed").unwrap();
        let signed = Command::new("codesign")
            .args(["--sign", "-", "--force"])
            .arg(&app)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .unwrap();
        assert!(
            signed.success(),
            "ad-hoc codesign must succeed on the synthetic App"
        );
        assert!(
            crate::update_backup::signature_verifies_for_test(&app),
            "the freshly signed synthetic App must verify before damage is applied"
        );
        app
    }

    pub(crate) fn synthetic_executable(app: &Path) -> PathBuf {
        app.join("Contents/MacOS/loopx-control-plane")
    }
}

#[cfg(test)]
mod tests {
    #[cfg(target_os = "macos")]
    use super::signed_app_test_support::{ad_hoc_signed_synthetic_app, synthetic_executable};
    use super::*;

    #[test]
    fn interrupted_rotation_keeps_the_latest_old_backup_recoverable() {
        let root = tempfile::tempdir().unwrap();
        for sequence in [1000, 2000] {
            let backup = root.path().join(format!("older-{sequence}"));
            fs::create_dir_all(backup.join("LoopX.app/Contents")).unwrap();
            fs::write(backup.join("LoopX.app/Contents/Info.plist"), "plist").unwrap();
            fs::write(backup.join("version"), format!("1.0.{sequence}")).unwrap();
        }
        let unrelated = root.path().join("older-not-a-sequence");
        fs::create_dir_all(unrelated.join("LoopX.app/Contents")).unwrap();
        fs::write(unrelated.join("LoopX.app/Contents/Info.plist"), "plist").unwrap();

        assert_eq!(
            recoverable_backup(root.path()),
            Some(root.path().join("older-2000"))
        );

        let previous = root.path().join("previous");
        fs::create_dir_all(previous.join("LoopX.app/Contents")).unwrap();
        fs::write(previous.join("LoopX.app/Contents/Info.plist"), "plist").unwrap();
        assert_eq!(recoverable_backup(root.path()), Some(previous));
    }

    #[test]
    fn failed_replacement_restores_original() {
        let dir = tempfile::tempdir().unwrap();
        let app = dir.path().join("installed.app");
        fs::create_dir(&app).unwrap();
        fs::write(app.join("original"), "keep").unwrap();
        assert!(replace_bundle(
            &app,
            &dir.path().join("missing.app"),
            &dir.path().join("failed.app")
        )
        .is_err());
        assert_eq!(fs::read_to_string(app.join("original")).unwrap(), "keep");
    }

    #[test]
    fn successful_replacement_preserves_the_original() {
        let dir = tempfile::tempdir().unwrap();
        let installed = dir.path().join("installed.app");
        let candidate = dir.path().join("candidate.app");
        let failed = dir.path().join("failed.app");
        fs::create_dir(&installed).unwrap();
        fs::write(installed.join("original"), "original").unwrap();
        fs::create_dir(&candidate).unwrap();
        fs::write(candidate.join("replacement"), "replacement").unwrap();

        replace_bundle(&installed, &candidate, &failed).unwrap();

        assert_eq!(
            fs::read_to_string(installed.join("replacement")).unwrap(),
            "replacement"
        );
        assert_eq!(
            fs::read_to_string(failed.join("original")).unwrap(),
            "original"
        );
        assert!(!candidate.exists());
    }

    #[test]
    #[cfg(target_os = "macos")]
    fn bundle_swap_keeps_install_path_after_process_kill() {
        if let Some(root) = std::env::var_os("LOOPX_TEST_KILL_DURING_BUNDLE_SWAP") {
            let root = PathBuf::from(root);
            replace_bundle(
                &root.join("installed.app"),
                &root.join("candidate.app"),
                &root.join("failed.app"),
            )
            .expect("the test kill point must stop the child first");
            panic!("bundle swap did not stop at the test kill point");
        }

        let dir = tempfile::tempdir().unwrap();
        let installed = dir.path().join("installed.app");
        let candidate = dir.path().join("candidate.app");
        fs::create_dir(&installed).unwrap();
        fs::write(installed.join("original"), "original").unwrap();
        fs::create_dir(&candidate).unwrap();
        fs::write(candidate.join("replacement"), "replacement").unwrap();

        let mut child = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "update_backup::tests::bundle_swap_keeps_install_path_after_process_kill",
                "--nocapture",
            ])
            .env("LOOPX_TEST_KILL_DURING_BUNDLE_SWAP", dir.path())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let committed = dir.path().join("swap-committed");
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while !committed.is_file() {
            if let Some(status) = child.try_wait().unwrap() {
                panic!("swap child exited before reaching the kill point: {status}");
            }
            if std::time::Instant::now() >= deadline {
                child.kill().unwrap();
                child.wait().unwrap();
                panic!("swap child did not reach the kill point");
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        child.kill().unwrap();
        assert!(!child.wait().unwrap().success());

        assert_eq!(
            fs::read_to_string(installed.join("replacement")).unwrap(),
            "replacement"
        );
        assert_eq!(
            fs::read_to_string(candidate.join("original")).unwrap(),
            "original"
        );
    }

    #[test]
    fn synthetic_app_layout_verifies_only_when_the_bundle_survives() {
        // A synthetic App layout stands in for the second-rename failure of
        // the pinned macOS installer: when the original location is emptied,
        // the running executable's ancestor bundle must stop verifying, which
        // is what blocks the safe-restart promise after a failed replacement.
        let dir = tempfile::tempdir().unwrap();
        let app = dir.path().join("Synthetic.app/Contents/MacOS");
        fs::create_dir_all(&app).unwrap();
        let executable = app.join("loopx-control-plane");
        fs::write(&executable, "binary").unwrap();
        fs::write(app.join("../Info.plist"), "plist").unwrap();
        assert_eq!(
            app_bundle_at(&executable),
            Ok(dir.path().join("Synthetic.app"))
        );
        // The updater's first rename moved the original away: no bundle with
        // an Info.plist remains at the executable's ancestors.
        fs::remove_file(app.join("../Info.plist")).unwrap();
        assert_eq!(
            app_bundle_at(&executable),
            Err("app_bundle_required".into())
        );
        // A bare executable outside any .app bundle never verifies.
        let loose = dir.path().join("bin/loopx-control-plane");
        fs::create_dir_all(loose.parent().unwrap()).unwrap();
        fs::write(&loose, "binary").unwrap();
        assert_eq!(app_bundle_at(&loose), Err("app_bundle_required".into()));
    }

    #[test]
    fn executable_removal_while_info_plist_remains_stops_verifying() {
        // Review round 3 counterexample: the pinned macOS updater's failed
        // replacement can leave a partially removed bundle — Info.plist and
        // the runtime identity survive while the executable is gone (a
        // running process keeps executing its deleted binary, so the path
        // simply stops existing). Layout verification must fail here so the
        // safe-restart predicate cannot discard the journal and promise a
        // safe restart over an unbootable App.
        let dir = tempfile::tempdir().unwrap();
        let contents = dir.path().join("Partial.app/Contents");
        fs::create_dir_all(contents.join("MacOS")).unwrap();
        fs::write(contents.join("Info.plist"), "plist").unwrap();
        let executable = contents.join("MacOS/loopx-control-plane");
        fs::write(&executable, "binary").unwrap();
        assert!(app_bundle_at(&executable).is_ok());
        fs::remove_file(&executable).unwrap();
        assert_eq!(
            app_bundle_at(&executable),
            Err("app_bundle_required".into())
        );
    }

    #[test]
    fn declared_bundle_executable_must_be_present_when_plist_names_it() {
        // When Info.plist declares CFBundleExecutable, that exact binary
        // must exist in Contents/MacOS: a bundle whose declared executable
        // was removed — even with another file left behind — cannot run the
        // binary the bundle claims to launch.
        let dir = tempfile::tempdir().unwrap();
        let contents = dir.path().join("Declared.app/Contents");
        fs::create_dir_all(contents.join("MacOS")).unwrap();
        fs::write(
            contents.join("Info.plist"),
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\
             <plist version=\"1.0\"><dict>\
             <key>CFBundleExecutable</key><string>loopx-control-plane</string>\
             </dict></plist>",
        )
        .unwrap();
        let survivor = contents.join("MacOS/unrelated-helper");
        fs::write(&survivor, "binary").unwrap();
        assert_eq!(app_bundle_at(&survivor), Err("app_bundle_required".into()));
        // With the declared executable restored beside the survivor the
        // bundle is a runnable layout again.
        let declared = contents.join("MacOS/loopx-control-plane");
        fs::write(&declared, "binary").unwrap();
        assert!(app_bundle_at(&survivor).is_ok());
        assert!(app_bundle_at(&declared).is_ok());
    }

    #[test]
    #[ignore = "requires LOOPX_TEST_APP from a signed macOS build"]
    fn real_backup_copy_retains_signature() {
        let source = std::env::var("LOOPX_TEST_APP").unwrap();
        let dir = tempfile::tempdir().unwrap();
        let copied = dir.path().join("LoopX.app");
        copy(Path::new(&source), &copied).unwrap();
        assert!(copied
            .join("Contents/Resources/runtime/identity.json")
            .is_file());
    }

    #[test]
    fn locating_the_rollback_target_boundary_does_not_require_an_intact_bundle() {
        // Rollback exists to repair a damaged installation, so target
        // location must stay boundary-only: the nearest `.app` ancestor.
        // Every state the safe-restart verification rejects below must still
        // be locatable here, or the recovery button could not reach it.
        let dir = tempfile::tempdir().unwrap();
        let contents = dir.path().join("Damaged.app/Contents");
        fs::create_dir_all(contents.join("MacOS")).unwrap();
        fs::write(contents.join("Info.plist"), "plist").unwrap();
        let executable = contents.join("MacOS/loopx-control-plane");
        fs::write(&executable, "binary").unwrap();
        assert!(installed_bundle_at(&executable).is_ok());
        // Executable deleted, Info.plist survives: not intact, still locatable.
        fs::remove_file(&executable).unwrap();
        assert!(installed_bundle_at(&executable).is_ok());
        assert_eq!(
            app_bundle_at(&executable),
            Err("app_bundle_required".into())
        );
        // Bundle moved away entirely: no `.app` ancestor, no boundary.
        let loose = dir.path().join("bin/loopx-control-plane");
        fs::create_dir_all(loose.parent().unwrap()).unwrap();
        fs::write(&loose, "binary").unwrap();
        assert_eq!(
            installed_bundle_at(&loose),
            Err("app_bundle_required".into())
        );
    }

    #[test]
    #[cfg(target_os = "macos")]
    fn installed_target_integrity_rejects_every_damage_mode() {
        // Review round 4 counterexample: a bundle that keeps Info.plist and
        // its executable while one sealed resource was deleted still passes
        // the layout check, yet codesign --verify --deep --strict rejects it.
        // The safe-restart evidence must run the shared signature gate on the
        // actual installed target, not just the layout rules.
        let dir = tempfile::tempdir().unwrap();

        // Complete signed App: verifies.
        let complete = ad_hoc_signed_synthetic_app(dir.path(), "Complete.app");
        assert!(installed_bundle_verifies(&synthetic_executable(&complete)));

        // Missing executable: layout alone rejects.
        let no_binary = ad_hoc_signed_synthetic_app(dir.path(), "NoBinary.app");
        fs::remove_file(synthetic_executable(&no_binary)).unwrap();
        assert!(!installed_bundle_verifies(&synthetic_executable(
            &no_binary
        )));

        // Missing sealed resource with executable and plist intact: layout
        // still passes, only the shared signature gate rejects it.
        let sealed_missing = ad_hoc_signed_synthetic_app(dir.path(), "SealedMissing.app");
        assert!(app_bundle_at(&synthetic_executable(&sealed_missing)).is_ok());
        fs::remove_file(sealed_missing.join("Contents/Resources/sealed-resource.txt")).unwrap();
        assert!(app_bundle_at(&synthetic_executable(&sealed_missing)).is_ok());
        assert!(!installed_bundle_verifies(&synthetic_executable(
            &sealed_missing
        )));

        // Unparseable (replaced, binary) Info.plist: layout's plist read
        // degrades, the signature gate rejects the mutated bundle.
        let plist_mutated = ad_hoc_signed_synthetic_app(dir.path(), "PlistMutated.app");
        fs::write(plist_mutated.join("Contents/Info.plist"), [0u8, 1, 2, 3]).unwrap();
        assert!(!installed_bundle_verifies(&synthetic_executable(
            &plist_mutated
        )));

        // Unsigned bundle (the pre-signing synthetic layouts): signature
        // verification fails closed even though the layout is complete.
        let unsigned_contents = dir.path().join("Unsigned.app/Contents");
        fs::create_dir_all(unsigned_contents.join("MacOS")).unwrap();
        fs::write(unsigned_contents.join("Info.plist"), "plist").unwrap();
        let unsigned_exe = unsigned_contents.join("MacOS/loopx-control-plane");
        fs::write(&unsigned_exe, "binary").unwrap();
        assert!(app_bundle_at(&unsigned_exe).is_ok());
        assert!(!installed_bundle_verifies(&unsigned_exe));
    }

    #[test]
    #[cfg(target_os = "macos")]
    fn restore_verified_backup_repairs_damaged_targets_but_not_a_moved_target() {
        // The actual recovery seam: a verified (ad-hoc signed) backup must be
        // able to replace a damaged installation — the exact state the
        // recovery panel offers rollback for — while a target that was moved
        // away entirely fails closed without deleting the backup.
        let dir = tempfile::tempdir().unwrap();
        let previous = dir.path().join("previous");
        fs::create_dir_all(&previous).unwrap();
        let backup_app = ad_hoc_signed_synthetic_app(dir.path(), "Backup.app");
        fs::rename(&backup_app, previous.join("LoopX.app")).unwrap();

        // Damaged target A: executable deleted, Info.plist survives (this
        // round's restore counterexample — rollback must still repair it).
        // The seam enters from the running executable exactly like the
        // production restore, so the locator decision is under test: a
        // layout-verifying locator would reject this target up front.
        let target_a = ad_hoc_signed_synthetic_app(dir.path(), "Damaged.app");
        fs::remove_file(synthetic_executable(&target_a)).unwrap();
        assert_eq!(
            app_bundle_at(&synthetic_executable(&target_a)),
            Err("app_bundle_required".into())
        );
        assert!(
            restore_verified_backup(&synthetic_executable(&target_a), &previous, || Ok(())).is_ok()
        );
        assert!(target_a
            .join("Contents/Resources/sealed-resource.txt")
            .is_file());
        assert!(installed_bundle_verifies(&synthetic_executable(&target_a)));

        // Damaged target B: a sealed resource deleted; rollback repairs it too.
        let target_b = ad_hoc_signed_synthetic_app(dir.path(), "SealedDamaged.app");
        fs::remove_file(target_b.join("Contents/Resources/sealed-resource.txt")).unwrap();
        assert!(!installed_bundle_verifies(&synthetic_executable(&target_b)));
        assert!(
            restore_verified_backup(&synthetic_executable(&target_b), &previous, || Ok(())).is_ok()
        );
        assert!(installed_bundle_verifies(&synthetic_executable(&target_b)));

        // Moved-away target: the boundary is derived from the executable's
        // path (the updater moved the bundle away; the running process keeps
        // its old path), so location succeeds but the swap's first rename
        // finds nothing there. The seam fails closed without deleting the
        // verified backup, which stays in place for manual recovery.
        let target_c = ad_hoc_signed_synthetic_app(dir.path(), "MovedAway.app");
        fs::remove_dir_all(&target_c).unwrap();
        assert_eq!(
            restore_verified_backup(&synthetic_executable(&target_c), &previous, || Ok(())),
            Err("rollback_failed".into())
        );
        assert!(previous.join("LoopX.app/Contents/Info.plist").is_file());

        // Corrupted backup source: copy's signature gate rejects it before
        // any swap, so a damaged backup can never replace an installation.
        let target_d = ad_hoc_signed_synthetic_app(dir.path(), "Intact.app");
        fs::write(
            previous.join("LoopX.app/Contents/Resources/sealed-resource.txt"),
            "tampered",
        )
        .unwrap();
        assert_eq!(
            restore_verified_backup(&synthetic_executable(&target_d), &previous, || Ok(())),
            Err("backup_failed".into())
        );
        assert!(installed_bundle_verifies(&synthetic_executable(&target_d)));
    }

    #[test]
    #[cfg(target_os = "macos")]
    fn restore_verified_backup_keeps_target_when_atomic_exchange_fails() {
        // Between the verified copy and the swap, the candidate can vanish
        // (disk pressure, cleanup race). The atomic exchange must fail before
        // changing the installed App.
        let dir = tempfile::tempdir().unwrap();
        let previous = dir.path().join("previous");
        fs::create_dir_all(&previous).unwrap();
        let backup_app = ad_hoc_signed_synthetic_app(dir.path(), "Backup.app");
        fs::rename(&backup_app, previous.join("LoopX.app")).unwrap();
        let target = ad_hoc_signed_synthetic_app(dir.path(), "Rollback.app");
        let executable = synthetic_executable(&target);
        let staging_root = target.parent().unwrap().to_path_buf();
        let result = restore_verified_backup(&executable, &previous, || {
            // Simulate the candidate disappearing before the exchange.
            let candidate = fs::read_dir(&staging_root)
                .into_iter()
                .flatten()
                .filter_map(|entry| entry.ok())
                .map(|entry| entry.path())
                .find(|path| {
                    path.file_name()
                        .is_some_and(|name| name.to_string_lossy().starts_with(".tmp"))
                        && path.join("LoopX.app").is_dir()
                })
                .expect("staged candidate must exist before the swap")
                .join("LoopX.app");
            fs::remove_dir_all(&candidate).unwrap();
            Ok(())
        });
        assert_eq!(result, Err("rollback_failed".into()));
        // The target itself survived the failed swap, still intact.
        assert!(installed_bundle_verifies(&executable));
    }
}
