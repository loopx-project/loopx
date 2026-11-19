fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["desktop_update", "desktop_update_status"]),
    ))
    .expect("desktop build");

    link_windows_resource_into_tests();
}

/// `tauri_build` hands its Windows resource file to `[[bin]]` targets only
/// (`cargo:rustc-link-arg-bins`), so the libtest binary of this crate links
/// without a manifest. Its `comctl32` imports are Common Controls v6 entry
/// points, which the loader then resolves against the System32 5.82 copy, and
/// the test process dies with `STATUS_ENTRYPOINT_NOT_FOUND` before `main`.
/// Attach the same resource to every linked target so `cargo test` can run on
/// Windows.
fn link_windows_resource_into_tests() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let Ok(out_dir) = std::env::var("OUT_DIR") else {
        return;
    };
    let resource = std::path::Path::new(&out_dir).join("resource.lib");
    if resource.exists() {
        println!("cargo:rustc-link-search=native={out_dir}");
    }
}
