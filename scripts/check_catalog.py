#!/usr/bin/env python3
"""
Check that every model in hydra_cli/catalog.json is served by a provider it may route to.

Uses the public model lists of OpenRouter and the Vercel AI Gateway. CheaperInference and
Cloudflare lists are checked only when their credentials are in the environment.
Nothing secret is printed. Exit code 1 means at least one model is unserved.

    python scripts/check_catalog.py
"""

import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hydra_cli.config import CATALOG  # noqa: E402
from hydra_cli.providers import adapt_model_for_url  # noqa: E402

URLS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "vercel": "https://ai-gateway.vercel.sh/v1/chat/completions",
    "cheaperinference": "https://api.cheaperinference.com/v1/chat/completions",
}


def fetch_ids(url, token=None, field="id"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
        data = json.loads(resp.read())
    items = data.get("data", data.get("result", data))
    return {item.get(field) for item in items}


def main():
    lists = {
        "openrouter": fetch_ids("https://openrouter.ai/api/v1/models"),
        "vercel": fetch_ids("https://ai-gateway.vercel.sh/v1/models"),
    }
    ci_key = os.environ.get("CHEAPERINFERENCE_API_KEY", "").strip()
    if ci_key:
        lists["cheaperinference"] = fetch_ids("https://api.cheaperinference.com/v1/models", ci_key)

    models = {spec["model"] for spec in CATALOG["aliases"].values()}
    models |= {head["model"] for head in CATALOG["swarm"].values()}
    restricted = CATALOG.get("model_providers", {})
    problems = 0
    for model in sorted(models):
        allowed = restricted.get(model) or ["openrouter", "vercel", "cheaperinference"]
        found = [p for p in allowed if p in lists and adapt_model_for_url(URLS[p], model) in lists[p]]
        unchecked = [p for p in allowed if p not in lists]
        if found:
            status = "ok   " + ",".join(found)
        elif unchecked:
            status = "skip (needs " + ",".join(unchecked) + " credentials to check)"
        else:
            status = "MISSING"
            problems += 1
        print(f"{model:45} {status}")
    for model in CATALOG["free_models"]:
        ok = model in lists["openrouter"]
        problems += 0 if ok else 1
        print(f"{model:45} {'ok   openrouter' if ok else 'MISSING'} (free)")

    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    cf_account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    cf_model = CATALOG["default_cloudflare_model"]
    if cf_token and cf_account:
        url = (f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/models/search"
               "?task=Text%20Generation&per_page=200")
        ok = cf_model in fetch_ids(url, cf_token, field="name")
        problems += 0 if ok else 1
        print(f"{cf_model:45} {'ok   cloudflare' if ok else 'MISSING'}")
    else:
        print(f"{cf_model:45} skip (needs Cloudflare credentials to check)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
