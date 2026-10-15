//! Install the already signature-verified updater archive on the App's volume.
//! Download/signature, channel selection and maintenance admission stay with
//! the existing updater. This is only its macOS filesystem effect adapter.
use std::{
    fs,
    io::{self, Cursor},
    path::{Component, Path},
};

use flate2::read::GzDecoder;
use tauri_plugin_updater::Update;

// Provider-local errors, never a new persisted or caller-facing vocabulary.
#[derive(Debug, PartialEq)]
enum InstallError {
    AuthorizationRequired,
    Failed,
}

pub(crate) fn install(update: &Update, verified_bytes: &[u8]) -> Result<(), String> {
    let executable = std::env::current_exe().map_err(|_| "app_install_failed")?;
    match install_verified_archive(&executable, verified_bytes) {
        Ok(()) => Ok(()),
        // An administrator-owned installation retains the existing native
        // authorization path. Never fall back after extraction or a failed
        // exchange: the target must not be removed to work around that error.
        Err(InstallError::AuthorizationRequired) => update
            .install(verified_bytes)
            .map_err(|_| "app_install_failed".into()),
        Err(InstallError::Failed) => Err("app_install_failed".into()),
    }
}

fn install_verified_archive(executable: &Path, verified_bytes: &[u8]) -> Result<(), InstallError> {
    let failed = || InstallError::Failed;
    let target = crate::update_backup::installed_bundle_at(executable).map_err(|_| failed())?;
    let parent = target.parent().ok_or_else(failed)?;
    let staging = tempfile::Builder::new()
        .prefix(".loopx-update-")
        .tempdir_in(parent)
        .map_err(|error| match error.kind() {
            io::ErrorKind::PermissionDenied => InstallError::AuthorizationRequired,
            _ => InstallError::Failed,
        })?;

    // Enforce the shipped single-App archive layout before extracting. Use
    // tar's rooted unpacker, which also rejects writes through escaping links;
    // Entry::unpack with a caller-composed destination would not do so.
    let mut archive = tar::Archive::new(GzDecoder::new(Cursor::new(verified_bytes)));
    for entry in archive.entries().map_err(|_| failed())? {
        let entry = entry.map_err(|_| failed())?;
        let path = entry.path().map_err(|_| failed())?;
        let mut components = path.components().filter(|part| *part != Component::CurDir);
        if components.next() != Some(Component::Normal("LoopX.app".as_ref()))
            || components.any(|part| !matches!(part, Component::Normal(_)))
        {
            return Err(failed());
        }
        let kind = entry.header().entry_type();
        if !(kind.is_file() || kind.is_dir() || kind.is_symlink() || kind.is_hard_link()) {
            return Err(failed());
        }
    }
    let mut archive = tar::Archive::new(GzDecoder::new(Cursor::new(verified_bytes)));
    archive.set_preserve_permissions(true);
    archive.unpack(staging.path()).map_err(|_| failed())?;
    let candidate = staging.path().join("LoopX.app");
    if !fs::symlink_metadata(&candidate)
        .map(|metadata| metadata.file_type().is_dir())
        .unwrap_or(false)
        || !crate::update_backup::installed_bundle_verifies(
            &candidate.join("Contents/MacOS/loopx-control-plane"),
        )
    {
        return Err(failed());
    }

    // Retain both sides before the only commit operation. The same atomic
    // exchange as rollback keeps the install path occupied, even across a
    // process interruption. Unsupported filesystems fail with the old App in
    // place; do not turn that into a sequential delete/move fallback.
    let displaced = staging.path().join("previous.app");
    let _preserved = staging.keep();
    crate::update_backup::replace_bundle(&target, &candidate, &displaced).map_err(|_| failed())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::update_backup::signed_app_test_support::{
        ad_hoc_signed_synthetic_app, synthetic_executable,
    };
    use flate2::{write::GzEncoder, Compression};

    fn archive(app: &Path) -> Vec<u8> {
        let encoder = GzEncoder::new(Vec::new(), Compression::default());
        let mut builder = tar::Builder::new(encoder);
        builder.append_dir_all("LoopX.app", app).unwrap();
        builder.into_inner().unwrap().finish().unwrap()
    }

    #[test]
    fn verified_candidate_replaces_app_and_retains_intact_previous_bundle() {
        let root = tempfile::tempdir().unwrap();
        let target = ad_hoc_signed_synthetic_app(root.path(), "Installed.app");
        let new_app = ad_hoc_signed_synthetic_app(root.path(), "Release.app");
        // A different sealed resource distinguishes the candidate from the
        // original. Re-sign the complete candidate after changing it.
        fs::write(
            new_app.join("Contents/Resources/sealed-resource.txt"),
            "new",
        )
        .unwrap();
        assert!(std::process::Command::new("codesign")
            .args(["--sign", "-", "--force"])
            .arg(&new_app)
            .status()
            .unwrap()
            .success());
        install_verified_archive(&synthetic_executable(&target), &archive(&new_app)).unwrap();
        assert_eq!(
            fs::read_to_string(target.join("Contents/Resources/sealed-resource.txt")).unwrap(),
            "new"
        );
        let preserved = fs::read_dir(root.path())
            .unwrap()
            .map(Result::unwrap)
            .find(|entry| {
                entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".loopx-update-")
            })
            .unwrap()
            .path()
            .join("previous.app");
        assert_eq!(
            fs::read_to_string(preserved.join("Contents/Resources/sealed-resource.txt")).unwrap(),
            "sealed"
        );
        for app in [&target, &preserved] {
            assert!(crate::update_backup::installed_bundle_verifies(
                &synthetic_executable(app)
            ));
        }
    }

    #[test]
    fn unsealed_or_incomplete_candidate_cannot_touch_the_installed_app() {
        for remove in [false, true] {
            let root = tempfile::tempdir().unwrap();
            let target = ad_hoc_signed_synthetic_app(root.path(), "Installed.app");
            let candidate = ad_hoc_signed_synthetic_app(root.path(), "Release.app");
            if remove {
                fs::remove_file(synthetic_executable(&candidate)).unwrap();
            } else {
                fs::write(
                    candidate.join("Contents/Resources/sealed-resource.txt"),
                    "tampered",
                )
                .unwrap();
            }
            assert_eq!(
                install_verified_archive(&synthetic_executable(&target), &archive(&candidate)),
                Err(InstallError::Failed)
            );
            assert!(crate::update_backup::installed_bundle_verifies(
                &synthetic_executable(&target)
            ));
            assert!(!fs::read_dir(root.path()).unwrap().any(|entry| entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".loopx-update-")));
        }
    }

    #[test]
    fn escaping_symlink_cannot_write_outside_staging_or_replace_app() {
        let root = tempfile::tempdir().unwrap();
        let target = ad_hoc_signed_synthetic_app(root.path(), "Installed.app");
        let encoder = GzEncoder::new(Vec::new(), Compression::default());
        let mut builder = tar::Builder::new(encoder);
        let mut link = tar::Header::new_gnu();
        link.set_entry_type(tar::EntryType::Symlink);
        link.set_size(0);
        link.set_mode(0o755);
        builder
            .append_link(&mut link, "LoopX.app", root.path())
            .unwrap();
        let mut file = tar::Header::new_gnu();
        file.set_size(6);
        file.set_mode(0o644);
        file.set_cksum();
        builder
            .append_data(&mut file, "LoopX.app/escaped", &b"escape"[..])
            .unwrap();
        let bytes = builder.into_inner().unwrap().finish().unwrap();
        assert_eq!(
            install_verified_archive(&synthetic_executable(&target), &bytes),
            Err(InstallError::Failed)
        );
        assert!(!root.path().join("escaped").exists());
        assert!(crate::update_backup::installed_bundle_verifies(
            &synthetic_executable(&target)
        ));
    }

    #[test]
    fn invalid_archive_is_rejected_without_changing_the_installed_app() {
        let root = tempfile::tempdir().unwrap();
        let target = ad_hoc_signed_synthetic_app(root.path(), "Installed.app");
        assert_eq!(
            install_verified_archive(&synthetic_executable(&target), b"not an archive"),
            Err(InstallError::Failed)
        );
        assert!(crate::update_backup::installed_bundle_verifies(
            &synthetic_executable(&target)
        ));
    }

    #[test]
    fn only_parent_permission_denial_requests_the_existing_authorization_path() {
        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::tempdir().unwrap();
        let target = ad_hoc_signed_synthetic_app(root.path(), "Installed.app");
        let bytes = archive(&target);
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o500)).unwrap();
        let result = install_verified_archive(&synthetic_executable(&target), &bytes);
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
        assert_eq!(result, Err(InstallError::AuthorizationRequired));
        assert!(crate::update_backup::installed_bundle_verifies(
            &synthetic_executable(&target)
        ));
    }

    #[test]
    #[ignore = "requires LOOPX_TEST_APP, LOOPX_TEST_SIGNED_ARCHIVE and a separate APFS LOOPX_TEST_INSTALL_PARENT"]
    fn real_release_installs_on_another_volume_and_keeps_the_old_app() {
        use std::os::unix::fs::MetadataExt;
        let parent = std::env::var("LOOPX_TEST_INSTALL_PARENT").unwrap();
        let root = tempfile::tempdir_in(parent).unwrap();
        assert_ne!(
            fs::metadata(root.path()).unwrap().dev(),
            fs::metadata(std::env::temp_dir()).unwrap().dev(),
            "the opt-in regression must exercise a different volume"
        );
        let source = std::env::var("LOOPX_TEST_APP").unwrap();
        let target = root.path().join("Installed.app");
        assert!(std::process::Command::new("ditto")
            .arg(&source)
            .arg(&target)
            .status()
            .unwrap()
            .success());
        let bytes = fs::read(std::env::var("LOOPX_TEST_SIGNED_ARCHIVE").unwrap()).unwrap();
        install_verified_archive(&synthetic_executable(&target), &bytes).unwrap();
        assert!(crate::update_backup::installed_bundle_verifies(
            &synthetic_executable(&target)
        ));
        let preserved = fs::read_dir(root.path())
            .unwrap()
            .map(Result::unwrap)
            .find(|entry| {
                entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".loopx-update-")
            })
            .unwrap()
            .path()
            .join("previous.app");
        assert_eq!(
            fs::read(preserved.join("Contents/Info.plist")).unwrap(),
            fs::read(Path::new(&source).join("Contents/Info.plist")).unwrap()
        );
        assert!(crate::update_backup::installed_bundle_verifies(
            &synthetic_executable(&preserved)
        ));
        assert!(crate::update_backup::installed_bundle_verifies(
            &synthetic_executable(Path::new(&source))
        ));
    }

    #[test]
    #[ignore = "requires distinct LOOPX_TEST_APP and LOOPX_TEST_SIGNED_ARCHIVE, plus a writable HFS+ LOOPX_TEST_UNSUPPORTED_INSTALL_PARENT"]
    fn real_release_keeps_old_app_when_volume_rejects_atomic_exchange() {
        let parent = std::env::var("LOOPX_TEST_UNSUPPORTED_INSTALL_PARENT").unwrap();
        let root = tempfile::tempdir_in(parent).unwrap();

        // Qualify the disposable filesystem independently of the installer.
        // A permission failure or an APFS directory is not this regression.
        let left = root.path().join("exchange-left");
        let right = root.path().join("exchange-right");
        fs::create_dir(&left).unwrap();
        fs::create_dir(&right).unwrap();
        let error = rustix::fs::renameat_with(
            rustix::fs::CWD,
            &left,
            rustix::fs::CWD,
            &right,
            rustix::fs::RenameFlags::EXCHANGE,
        )
        .expect_err("the fixture must reject atomic directory exchange");
        assert_eq!(error.raw_os_error(), libc::ENOTSUP);

        let source = std::env::var("LOOPX_TEST_APP").unwrap();
        let target = root.path().join("Installed.app");
        assert!(std::process::Command::new("ditto")
            .arg(&source)
            .arg(&target)
            .status()
            .unwrap()
            .success());
        let original_info = fs::read(target.join("Contents/Info.plist")).unwrap();
        let original_executable = fs::read(synthetic_executable(&target)).unwrap();
        let bytes = fs::read(std::env::var("LOOPX_TEST_SIGNED_ARCHIVE").unwrap()).unwrap();
        assert_eq!(
            install_verified_archive(&synthetic_executable(&target), &bytes),
            Err(InstallError::Failed)
        );
        assert_eq!(
            fs::read(target.join("Contents/Info.plist")).unwrap(),
            original_info
        );
        assert_eq!(
            fs::read(synthetic_executable(&target)).unwrap(),
            original_executable
        );
        for app in [&target, Path::new(&source)] {
            assert!(crate::update_backup::installed_bundle_verifies(
                &synthetic_executable(app)
            ));
        }

        let staged = fs::read_dir(root.path())
            .unwrap()
            .map(Result::unwrap)
            .find(|entry| {
                entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".loopx-update-")
            })
            .unwrap()
            .path()
            .join("LoopX.app");
        assert!(crate::update_backup::installed_bundle_verifies(
            &synthetic_executable(&staged)
        ));
        // Identical inputs would hide an erroneous replacement with the new App.
        assert_ne!(
            fs::read(staged.join("Contents/Info.plist")).unwrap(),
            original_info,
            "use an old source App distinct from the update archive"
        );
    }
}
