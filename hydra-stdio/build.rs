use std::path::Path;
use std::process::Command;

fn main() {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap_or_else(|_| ".".into());
    let repo_root = Path::new(&manifest_dir).join("..");
    let git_dir = repo_root.join(".git");
    if git_dir.exists() {
        println!("cargo:rerun-if-changed={}", git_dir.join("HEAD").display());
        println!("cargo:rerun-if-changed={}", git_dir.join("refs/heads/main").display());
        println!("cargo:rerun-if-changed={}", git_dir.join("packed-refs").display());
    }
    let sha = Command::new("git")
        .arg("-C")
        .arg(&repo_root)
        .args(["rev-parse", "--short=12", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "unknown".into());
    println!("cargo:rustc-env=HYDRA_BUILD_SHA={sha}");
}
