//! Provider keys live in `~/.hydra/.env` (or `$HYDRA_HOME/.env`), the file the agent already reads. This module is the
//! only place the app reads or writes it, so first-run key entry works without a terminal.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// (env variable, label shown in Settings, is a secret)
pub const PROVIDERS: &[(&str, &str, bool)] = &[
    ("OPENROUTER_API_KEY", "OpenRouter key", true),
    ("AI_GATEWAY_API_KEY", "Vercel AI Gateway key", true),
    ("CHEAPERINFERENCE_API_KEY", "CheaperInference key", true),
    ("RUNPOD_API_KEY", "Runpod key", true),
    ("HF_TOKEN", "Hugging Face token", true),
    ("LOCAL_AI_BASE", "Local endpoint URL (e.g. http://127.0.0.1:8000)", false),
];

pub fn env_path() -> PathBuf {
    let home = match std::env::var("HYDRA_HOME") {
        Ok(h) if !h.trim().is_empty() => PathBuf::from(h),
        _ => PathBuf::from(std::env::var("HOME").or_else(|_| std::env::var("USERPROFILE")).unwrap_or_else(|_| ".".into())).join(".hydra"),
    };
    home.join(".env")
}

fn unquote(v: &str) -> String {
    let v = v.trim();
    if v.len() >= 2 && ((v.starts_with('"') && v.ends_with('"')) || (v.starts_with('\'') && v.ends_with('\''))) {
        v[1..v.len() - 1].to_string()
    } else {
        v.to_string()
    }
}

pub fn read_from(path: &Path) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    for line in std::fs::read_to_string(path).unwrap_or_default().lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let line = line.strip_prefix("export ").unwrap_or(line);
        if let Some((k, v)) = line.split_once('=') {
            out.insert(k.trim().to_string(), unquote(v));
        }
    }
    out
}

/// True when the name is set in the process environment or in the env file.
pub fn is_set(name: &str, file: &Path) -> bool {
    std::env::var(name).map(|v| !v.trim().is_empty()).unwrap_or(false) || read_from(file).get(name).map(|v| !v.is_empty()).unwrap_or(false)
}

/// Set or replace one variable, keeping every other line (including comments) as it was. Atomic; owner-only on unix.
pub fn set_in(path: &Path, name: &str, value: &str) -> Result<(), String> {
    if name.is_empty() || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return Err(format!("bad variable name {name}"));
    }
    if value.contains('\n') || value.contains('\r') {
        return Err("the value must be a single line".into());
    }
    let old = std::fs::read_to_string(path).unwrap_or_default();
    let mut lines: Vec<String> = Vec::new();
    let mut done = false;
    for line in old.lines() {
        let probe = line.trim().strip_prefix("export ").unwrap_or(line.trim());
        if probe.split_once('=').map(|(k, _)| k.trim() == name).unwrap_or(false) {
            if !done {
                lines.push(format!("{name}={value}"));
                done = true;
            }
        } else {
            lines.push(line.to_string());
        }
    }
    if !done {
        lines.push(format!("{name}={value}"));
    }
    let mut text = lines.join("\n");
    text.push('\n');
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    }
    let tmp = path.with_extension("env.tmp");
    {
        use std::io::Write;
        let mut f = std::fs::File::create(&tmp).map_err(|e| format!("cannot write {}: {e}", tmp.display()))?;
        f.write_all(text.as_bytes()).and_then(|_| f.sync_all()).map_err(|e| e.to_string())?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
    }
    std::fs::rename(&tmp, path).map_err(|e| format!("cannot replace {}: {e}", path.display()))
}

/// Show that something is stored without revealing it: first 3 and last 2 characters of long values.
/// D22: check a settings value before anything is written. The message names the bad value for non-secret
/// fields (a URL), and only its masked form for keys, so a typo is visible without leaking a secret.
pub fn validate(name: &str, value: &str) -> Result<(), String> {
    let v = value.trim();
    if name == "LOCAL_AI_BASE" {
        let rest = v.strip_prefix("http://").or_else(|| v.strip_prefix("https://")).ok_or_else(|| format!("\"{v}\" is not a URL: it must start with http:// or https://"))?;
        if v.contains(char::is_whitespace) {
            return Err(format!("\"{v}\" has spaces; a URL has none"));
        }
        let host = rest.split(['/', '?', '#']).next().unwrap_or("");
        let (h, port) = match host.rsplit_once(':') {
            Some((h, p)) if !h.ends_with(']') || p.is_empty() => (h, Some(p)),
            _ => (host, None),
        };
        if h.is_empty() || !h.chars().all(|c| c.is_ascii_alphanumeric() || ".-[]:".contains(c)) {
            return Err(format!("\"{v}\" has no valid host name"));
        }
        if let Some(p) = port {
            if p.parse::<u16>().map(|n| n == 0).unwrap_or(true) {
                return Err(format!("\"{v}\": port \"{p}\" is not a number from 1 to 65535"));
            }
        }
        return Ok(());
    }
    if v.contains(char::is_whitespace) {
        return Err(format!("{} has spaces; paste the key alone", mask(v)));
    }
    if v.len() < 8 {
        return Err(format!("{} is too short for a key", mask(v)));
    }
    Ok(())
}

pub fn mask(value: &str) -> String {
    let n = value.chars().count();
    if n <= 8 {
        return "•".repeat(n.max(4));
    }
    let head: String = value.chars().take(3).collect();
    let tail: String = value.chars().skip(n - 2).collect();
    format!("{head}••••••{tail}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settings_values_are_checked_before_saving_and_errors_show_the_url() {
        assert!(validate("LOCAL_AI_BASE", "http://127.0.0.1:8000").is_ok());
        assert!(validate("LOCAL_AI_BASE", "https://my-box.local/v1").is_ok());
        let e = validate("LOCAL_AI_BASE", "not a url").unwrap_err();
        assert!(e.contains("not a url"), "the bad value is shown: {e}");
        assert!(validate("LOCAL_AI_BASE", "ftp://x").unwrap_err().contains("http://"));
        assert!(validate("LOCAL_AI_BASE", "http://").is_err());
        assert!(validate("LOCAL_AI_BASE", "http://host:99999").unwrap_err().contains("99999"));
        assert!(validate("OPENROUTER_API_KEY", "sk-or-v1-abcdefgh").is_ok());
        let e = validate("OPENROUTER_API_KEY", "sk-or v1-secretvalue").unwrap_err();
        assert!(!e.contains("secretvalue"), "a key is never echoed: {e}");
        assert!(validate("HF_TOKEN", "abc").is_err());
    }

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("hydra-secrets-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d.join(".env")
    }

    #[test]
    fn set_adds_replaces_and_keeps_other_lines() {
        let p = tmp("set");
        std::fs::write(&p, "# mine\nFOO=1\nOPENROUTER_API_KEY=old\nexport BAR=\"x y\"\n").unwrap();
        set_in(&p, "OPENROUTER_API_KEY", "sk-new").unwrap();
        set_in(&p, "HF_TOKEN", "hf_abc").unwrap();
        let text = std::fs::read_to_string(&p).unwrap();
        assert!(text.contains("# mine") && text.contains("FOO=1"));
        assert_eq!(text.matches("OPENROUTER_API_KEY").count(), 1);
        let m = read_from(&p);
        assert_eq!(m["OPENROUTER_API_KEY"], "sk-new");
        assert_eq!(m["HF_TOKEN"], "hf_abc");
        assert_eq!(m["BAR"], "x y");
        assert!(is_set("HF_TOKEN", &p));
        assert!(!is_set("RUNPOD_API_KEY_NOT_SET_ANYWHERE", &p));
    }

    #[test]
    fn rejects_multiline_values_and_bad_names() {
        let p = tmp("bad");
        assert!(set_in(&p, "A", "x\nB=evil").is_err(), "a newline would inject a second variable");
        assert!(set_in(&p, "A B", "x").is_err());
        assert!(!p.exists());
    }

    #[cfg(unix)]
    #[test]
    fn file_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let p = tmp("mode");
        set_in(&p, "HF_TOKEN", "hf_secret_value").unwrap();
        assert_eq!(std::fs::metadata(&p).unwrap().permissions().mode() & 0o777, 0o600);
    }

    #[test]
    fn mask_never_returns_the_whole_value() {
        let m = mask("sk-or-v1-abcdefghijkl");
        assert!(!m.contains("abcdefgh"));
        assert!(m.starts_with("sk-") && m.ends_with("kl"));
        assert_eq!(mask("short"), "•••••");
    }
}
