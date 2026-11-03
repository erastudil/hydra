# Free Services Map — adjacent claims for the Hydra roster

Compiled Oct 16, 2026 from primary-source-checked research (see
`~/workspace/your_files/hydra-next-steps-2026-10-16.md` §2). Each entry is a
*claim* in Hydra's map — an account, a quota, an expiry — not a code path.
Inference providers live in `hydra_cli/providers.py` and
`hydra_cli/auth.py`; this map covers everything around them, so a developer
working through Hydra has no reason to leave it. Numbers below match the
research snapshot exactly; re-verify against the provider's own pricing page
before relying on a figure, because free tiers die without notice.

## Storage

| Service | Free terms | Limits | Claim notes |
| --- | --- | --- | --- |
| Backblaze B2 | First 10 GB free, recurring, no card | Egress free to 3x stored data | Storj free tier discontinued; Wasabi and Tigris have no free tier. Cloudflare R2 already in-roster via Cloudflare. |

## Databases & backend

| Service | Free terms | Limits | Claim notes |
| --- | --- | --- | --- |
| Supabase | Free tier | 500 MB x 2 projects, 50K MAU auth; projects pause after 7 idle days | Supabase Auth rides this tier (see Auth). |
| Neon | Free tier | 100 projects, 100 CU-hours; storage figure conflicts across sources (0.5 vs 1 GB) | Verify the storage figure at signup. |
| Turso | Free tier | 100 DBs, 5 GB, 500M reads/mo | Supabase acquired Turso (~Oct 5, 2026); free-tier continuity unpromised. |
| Upstash Redis | Free tier | 256 MB, 500K commands/mo | — |
| Appwrite Cloud | Free tier | 750K executions, 75K MAU | Generous; self-hosting is also free. |
| PlanetScale | **No free tier** | Starts at $5/mo | Do not map as a claim. |

## Hosting

| Service | Free terms | Limits | Claim notes |
| --- | --- | --- | --- |
| Netlify | Free tier | 300 credits/mo hard cap | — |
| Render | Free tier | 750 instance-hours; spins down after 15 min idle | — |
| Deno Deploy | Free tier | 100K requests/day | — |
| Koyeb | Free tier | One free nano instance | — |
| Fly.io | **No standing free plan** for new accounts | — | Do not map as a claim. |
| Railway | **No standing free plan** for new accounts | — | Do not map as a claim. |

## Domains & DNS

| Service | Terms | Limits | Claim notes |
| --- | --- | --- | --- |
| Cloudflare Registrar | At-cost (~$10.50 .com) | Cloudflare nameserver lock-in | Already in-roster via Cloudflare. |
| Porkbun | ~$11.08 .com | — | Free WHOIS privacy included. |
| FreeDNS / DuckDNS / No-IP | Free subdomains | — | Verified free subdomain sources. |

## Email

| Service | Free terms | Limits | Claim notes |
| --- | --- | --- | --- |
| Resend | 3,000 emails/mo free, no card | — | First claim in this category. |
| Postmark | 100 emails/mo forever | — | — |
| SendGrid | **Permanent free plan removed in 2025** | 60-day trial only | Do not map as a claim. |

## Auth

| Service | Free terms | Limits | Claim notes |
| --- | --- | --- | --- |
| Clerk | Free tier | 50K MRU | — |
| Auth0 | Free tier | 25K MAU; custom domain needs a card | — |
| Supabase Auth | Rides the Supabase free tier | 50K MAU | See Databases. |

## Vector databases

| Service | Free terms | Limits | Claim notes |
| --- | --- | --- | --- |
| Pinecone Starter | Free tier | 2 GB, 2M write + 1M read units/mo | — |
| Qdrant Cloud | Free tier, 1 GB RAM forever | Suspends after 1 week idle, deletes at 4 weeks | Claim the account, then keep it warm or accept the suspend cycle. |

## Observability

| Service | Free terms | Limits | Claim notes |
| --- | --- | --- | --- |
| Sentry | Free tier | 5K errors/mo | — |
| PostHog | Free tier, no card | 1M events/mo | — |
| Grafana Cloud | Free tier | 10K series, 50 GB logs | — |
| Langfuse | Free tier | 50K observations/mo | LLM observability; directly relevant to a router's usage ledger. |

## CI/CD beyond GitHub

| Service | Free terms | Limits | Claim notes |
| --- | --- | --- | --- |
| GitLab | Free tier | 400 CI minutes/mo | — |
| CircleCI | Free tier | Figures conflict across sources; OSS boosted to 400K credits/mo | Verify the personal-tier figure at signup. |

## Payments

No true free tier exists anywhere in payments. Stripe runs 2.9% + $0.30 with
free test mode; Lemon Squeezy, Paddle, and Polar run 5% + $0.50. Hydra's
value here is key custody plus test mode, not a free claim.

## Dead tiers — do not chase (inference, from the same research)

- **Cerebras free tier is gone** — now a $5 one-time credit, verified payment
  method required, expires in 30 days.
- **Together AI free tier is gone** — $5 minimum credit purchase.
- **GitHub Models closed to new customers 2026-06-16** (existing accounts
  keep access).
- DeepInfra: no confirmed free tier.
- Baseten: none; access is a gated startup program only.
- Perplexity API: no free tier.
- Kluster.ai sunset 2026-06-09.
