//! D19 end to end: the real Hydra binary started with a window library hidden. A private mount namespace
//! (`unshare -rm`) binds /dev/null over the library for that process only, so nothing on the machine changes.
#![cfg(target_os = "linux")]

use std::process::{Command, Output};

/// The file the dynamic loader really opens for `soname`: load it here, then read it back from /proc/self/maps.
fn real_path(soname: &str) -> String {
    let lib = unsafe { libloading::Library::new(soname) };
    assert!(lib.is_ok(), "{soname} must be installed for this test");
    let stem = soname.split(".so").next().unwrap();
    let maps = std::fs::read_to_string("/proc/self/maps").unwrap();
    let path = maps.lines().filter_map(|l| l.split_whitespace().nth(5)).find(|p| p.rsplit('/').next().is_some_and(|f| f.starts_with(&format!("{stem}.so")))).expect("loaded library is mapped");
    std::fs::canonicalize(path).unwrap().display().to_string()
}

fn run_hydra(hide: Option<&str>, home: &std::path::Path) -> Output {
    let exe = env!("CARGO_BIN_EXE_Hydra");
    let mask = hide.map(|s| format!("mount --bind /dev/null '{}' && ", real_path(s))).unwrap_or_default();
    let out = Command::new("timeout")
        .args(["20", "unshare", "-rm", "sh", "-c", &format!("{mask}exec '{exe}'")])
        .env("HOME", home)
        .env_remove("HYDRA_STATE_DIR")
        .env("DISPLAY", ":97")
        .env_remove("WAYLAND_DISPLAY")
        .output()
        .expect("unshare must be available: this test hides a library from one process only");
    out
}

fn home(name: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("hydra-syslibs-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

#[test]
fn d19_missing_xkbcommon_x11_names_the_package_instead_of_panicking() {
    let h = home("xkb");
    let out = run_hydra(Some("libxkbcommon-x11.so.0"), &h);
    let err = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(1), "stderr: {err}");
    assert!(!err.contains("panicked"), "{err}");
    assert!(err.contains("libxkbcommon-x11.so.0 (keyboard input on X11)"), "{err}");
    assert!(err.contains("sudo apt install libxkbcommon-x11-0"), "{err}");
    let logs = h.join(".hydra/desktop/logs");
    let saved = std::fs::read_to_string(logs.join("startup-error.txt")).unwrap_or_default();
    assert_eq!(saved, err, "the same message is left in the log folder");
    let crashes = std::fs::read_dir(&logs).unwrap().flatten().filter(|e| e.file_name().to_string_lossy().starts_with("crash-")).count();
    assert_eq!(crashes, 0, "a missing library is not a crash");
}

#[test]
fn d19_nothing_hidden_passes_the_check_and_clears_an_old_report() {
    let h = home("ok");
    let logs = h.join(".hydra/desktop/logs");
    std::fs::create_dir_all(&logs).unwrap();
    std::fs::write(logs.join("startup-error.txt"), "old").unwrap();
    let out = run_hydra(None, &h);
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(!err.contains("system libraries are missing"), "{err}");
    assert!(!logs.join("startup-error.txt").exists(), "a clean check removes the old report");
}
