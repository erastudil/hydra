//! Local reachability and provider budget probes. Secret values stay out of the returned rows.

use serde_json::Value;
use std::net::{SocketAddr, TcpStream};
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
        ("ollama", env_host("OLLAMA_HOST", "127.0.0.1:11434")),
        ("llama.cpp", env_host("LLAMACPP_HOST", "127.0.0.1:8080")),
        ("local openai", env_host("LOCAL_AI_BASE", "127.0.0.1:8000")),
    ];
    specs
        .into_iter()
        .map(|(name, target)| {
            let open = tcp_open(&target);
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

fn tcp_open(target: &str) -> bool {
    let hostport = target.trim().trim_start_matches("http://").trim_start_matches("https://");
    let hostport = hostport.split('/').next().unwrap_or(hostport);
    let (host, port) = match hostport.rsplit_once(':') {
        Some((h, p)) => (h, p.parse::<u16>().unwrap_or(80)),
        None => (hostport, 80),
    };
    let addr = format!("{host}:{port}");
    let Ok(sockaddr) = addr.parse::<SocketAddr>() else {
        return false;
    };
    TcpStream::connect_timeout(&sockaddr, Duration::from_millis(250)).is_ok()
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
