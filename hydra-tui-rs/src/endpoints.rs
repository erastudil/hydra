//! Local reachability and provider budget probes. Secret values stay out of the returned rows.

use serde_json::Value;
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct Endpoint {
    pub name: String,
    pub target: String,
    pub open: bool,
}

#[derive(Debug, Clone)]
pub struct Budget {
    pub provider: String,
    pub detail: String,
}

pub fn local_endpoints() -> Vec<Endpoint> {
    let specs = [
        ("ollama", env_host("OLLAMA_HOST", "127.0.0.1:11434"), 11434u16),
        ("llama.cpp", env_host("LLAMACPP_HOST", "127.0.0.1:8080"), 8080),
        ("local openai", env_host("LOCAL_AI_BASE", "127.0.0.1:8000"), 8000),
    ];
    specs
        .into_iter()
        .map(|(name, target, default_port)| {
            let open = tcp_open(&target, default_port);
            Endpoint { name: name.into(), target, open }
        })
        .collect()
}

pub fn budgets() -> Vec<Budget> {
    let mut rows = vec![Budget {
        provider: "context window".into(),
        detail: format!("{} tokens", env_u64("HYDRA_CONTEXT_WINDOW", 1_048_576)),
    }];
    rows.push(cheaper_budget());
    rows.push(openrouter_budget());
    rows.push(env_flag("anthropic", "ANTHROPIC_API_KEY"));
    rows.push(env_flag("openai", "OPENAI_API_KEY"));
    rows.push(env_flag("cloudflare", "CLOUDFLARE_API_TOKEN"));
    rows.push(env_flag("gemini", "GEMINI_API_KEY"));
    rows.push(env_flag("xai", "XAI_API_KEY"));
    rows
}

pub fn free_usage() -> Vec<Budget> {
    let mut rows = Vec::new();
    let local = local_endpoints();
    let open_locals: Vec<String> = local.iter().filter(|e| e.open).map(|e| e.name.clone()).collect();
    if !open_locals.is_empty() {
        rows.push(Budget {
            provider: "local inference".into(),
            detail: format!("active; unlimited (0 cost); models: {}", open_locals.join(", ")),
        });
    } else {
        rows.push(Budget {
            provider: "local inference".into(),
            detail: "offline; unlimited upon starting ollama or llama.cpp".into(),
        });
    }

    let cf = std::env::var("CLOUDFLARE_API_TOKEN").ok().filter(|s| !s.trim().is_empty());
    if cf.is_some() {
        rows.push(Budget {
            provider: "cloudflare workers ai".into(),
            detail: "ready; 10,000 neurons/day standard allocation".into(),
        });
    } else {
        rows.push(Budget {
            provider: "cloudflare workers ai".into(),
            detail: "unconfigured; 10,000 neurons/day upon setting CLOUDFLARE_API_TOKEN".into(),
        });
    }

    let or = std::env::var("OPENROUTER_API_KEY").ok().filter(|s| !s.trim().is_empty());
    if or.is_some() {
        rows.push(Budget {
            provider: "openrouter free tier (:free)".into(),
            detail: "ready; 20 req/min free pool quota active".into(),
        });
    } else {
        rows.push(Budget {
            provider: "openrouter free tier (:free)".into(),
            detail: "unconfigured; free models available upon setting OPENROUTER_API_KEY".into(),
        });
    }

    rows
}

fn cheaper_budget() -> Budget {
    if let Ok(raw) = std::env::var("CHEAPERINFERENCE_CREDIT_BALANCE") {
        if let Ok(n) = raw.trim().parse::<f64>() {
            return Budget { provider: "cheaperinference".into(), detail: format!("${n:.2} from CHEAPERINFERENCE_CREDIT_BALANCE") };
        }
    }
    let Some(key) = std::env::var("CHEAPERINFERENCE_API_KEY").ok().filter(|s| !s.trim().is_empty()) else {
        return Budget { provider: "cheaperinference".into(), detail: "key absent".into() };
    };
    let base = std::env::var("CHEAPERINFERENCE_API_BASE").unwrap_or_else(|_| "https://api.cheaperinference.com/v1".into());
    let root = base.trim().trim_end_matches('/');
    for path in ["/user/balance", "/balance", "/dashboard/billing/credit_grants"] {
        let url = format!("{root}{path}");
        if let Some(n) = http_balance(&url, &key) {
            return Budget { provider: "cheaperinference".into(), detail: format!("${n:.2}") };
        }
    }
    Budget { provider: "cheaperinference".into(), detail: "key present, balance unread".into() }
}

fn openrouter_budget() -> Budget {
    let Some(key) = std::env::var("OPENROUTER_API_KEY").ok().filter(|s| !s.trim().is_empty()) else {
        return Budget { provider: "openrouter".into(), detail: "key absent".into() };
    };
    match http_json("https://openrouter.ai/api/v1/key", &key) {
        Some(v) => {
            let data = v.get("data").cloned().unwrap_or(v);
            let usage = data.get("usage").and_then(|x| x.as_f64());
            let limit = data.get("limit").and_then(|x| x.as_f64());
            let remaining = data.get("limit_remaining").and_then(|x| x.as_f64());
            let detail = match (usage, limit, remaining) {
                (Some(u), Some(l), _) => format!("usage {u:.4} / limit {l:.4}"),
                (_, _, Some(r)) => format!("remaining {r:.4}"),
                _ => "key present, balance fields absent".into(),
            };
            Budget { provider: "openrouter".into(), detail }
        }
        None => Budget { provider: "openrouter".into(), detail: "key present, balance unread".into() },
    }
}

fn env_flag(provider: &str, key: &str) -> Budget {
    let present = std::env::var(key).ok().filter(|s| !s.trim().is_empty()).is_some();
    Budget {
        provider: provider.into(),
        detail: if present { format!("{key} present") } else { format!("{key} absent") },
    }
}

fn env_host(key: &str, default: &str) -> String {
    std::env::var(key).ok().filter(|s| !s.trim().is_empty()).unwrap_or_else(|| default.into())
}

fn env_u64(key: &str, default: u64) -> u64 {
    std::env::var(key).ok().and_then(|s| s.trim().parse().ok()).unwrap_or(default)
}

/// Host and port from a Hydra endpoint string.
///
/// Accepts `127.0.0.1:11434`, `http://localhost:11434`, and `http://localhost:8080/v1`.
/// A missing port uses `default_port` (the service default), not port 80.
fn host_port(target: &str, default_port: u16) -> Option<(String, u16)> {
    let trimmed = target.trim();
    if trimmed.is_empty() {
        return None;
    }
    let rest = trimmed
        .strip_prefix("https://")
        .or_else(|| trimmed.strip_prefix("http://"))
        .unwrap_or(trimmed);
    let rest = rest.split(['/', '?', '#']).next().unwrap_or(rest).trim();
    if rest.is_empty() {
        return None;
    }
    if let Some(body) = rest.strip_prefix('[') {
        let (addr, after) = body.split_once(']')?;
        if addr.is_empty() {
            return None;
        }
        let port = match after.strip_prefix(':') {
            Some(raw) if !raw.is_empty() => raw.parse().ok()?,
            _ => default_port,
        };
        return Some((format!("[{addr}]"), port));
    }
    if let Some((host, raw_port)) = rest.rsplit_once(':') {
        if !host.is_empty() {
            let port = raw_port.parse::<u16>().unwrap_or(default_port);
            return Some((host.to_string(), port));
        }
    }
    Some((rest.to_string(), default_port))
}

fn tcp_open(target: &str, default_port: u16) -> bool {
    let Some((host, port)) = host_port(target, default_port) else {
        return false;
    };
    let lookup = host.trim_start_matches('[').trim_end_matches(']');
    let Ok(addrs) = (lookup, port).to_socket_addrs() else {
        return false;
    };
    addrs.take(8).any(|sockaddr| TcpStream::connect_timeout(&sockaddr, Duration::from_millis(250)).is_ok())
}

fn http_balance(url: &str, key: &str) -> Option<f64> {
    let v = http_json(url, key)?;
    find_balance(&v)
}

fn find_balance(v: &Value) -> Option<f64> {
    for field in ["balance", "credit", "credits", "total_available"] {
        if let Some(n) = v.get(field).and_then(|x| x.as_f64()) {
            return Some(n);
        }
    }
    v.get("data").and_then(find_balance)
}

fn http_json(url: &str, key: &str) -> Option<Value> {
    let resp = ureq::get(url)
        .timeout(Duration::from_secs(4))
        .set("Authorization", &format!("Bearer {key}"))
        .set("User-Agent", "Hydra-Tui")
        .call()
        .ok()?;
    serde_json::from_reader(resp.into_reader()).ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn host_port_reads_url_forms_the_rest_of_hydra_stores() {
        assert_eq!(host_port("http://localhost:8080/v1", 8000), Some(("localhost".into(), 8080)));
        assert_eq!(host_port("https://127.0.0.1:11434", 80), Some(("127.0.0.1".into(), 11434)));
        assert_eq!(host_port("127.0.0.1:8080", 80), Some(("127.0.0.1".into(), 8080)));
        assert_eq!(host_port("http://127.0.0.1", 11434), Some(("127.0.0.1".into(), 11434)));
        assert_eq!(host_port("[::1]:11434", 80), Some(("[::1]".into(), 11434)));
        assert_eq!(host_port("   ", 11434), None);
    }

    #[test]
    fn tcp_open_reaches_localhost_urls() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let url = format!("http://localhost:{port}/v1");
        assert!(tcp_open(&url, 9), "{url} should be open");
        assert!(tcp_open(&format!("http://127.0.0.1:{port}"), 9));
        drop(listener);
        assert!(!tcp_open(&format!("http://127.0.0.1:{port}"), 9));
    }
}
