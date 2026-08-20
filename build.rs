//! Embed the git commit in the binary so `monerosim --version` identifies
//! exactly what a `git pull` user is running (GitHub issue #5: version
//! numbers alone can't distinguish builds between releases).

use std::process::Command;

fn main() {
    let hash = Command::new("git")
        .args(["rev-parse", "--short=8", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "unknown".to_string());
    let dirty = Command::new("git")
        .args(["status", "--porcelain"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| !o.stdout.is_empty())
        .unwrap_or(false);
    println!(
        "cargo:rustc-env=MONEROSIM_GIT_HASH={}{}",
        hash,
        if dirty { "-dirty" } else { "" }
    );
    // Rebuild when HEAD moves so the embedded hash stays truthful.
    println!("cargo:rerun-if-changed=.git/HEAD");
    println!("cargo:rerun-if-changed=.git/index");
}
