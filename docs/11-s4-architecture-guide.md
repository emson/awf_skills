# 11 — S4 Architecture Guide: Production Prescale

The complete reference for the S4 "prescale" stage: a dedicated
production system with a Hetzner Load Balancer, Cloudflare CDN, a
dedicated Neon database, and a clear runway to S5 multi-server scaling.

> **Scope.** This document covers the S4 architecture in depth —
> every layer, every trade-off, and every step to migrate from S3.
> It also specifies exactly how to add a second server at S5 without
> changing DNS, how to scale the database connection pool, and when
> to move to the next stage.

---

## What changes from S3 to S4

| Dimension | S3 (shared lab) | S4 (prescale) |
|-----------|-----------------|----------------|
| Server | Shared CX22 | Dedicated CX32 |
| DNS routing | Direct to server (grey) | Through LB (orange) |
| CDN | None | Cloudflare edge |
| TLS | kamal-proxy (Let's Encrypt) | CF → LB chain (Origin Cert) |
| Load balancer | None | Hetzner LB11 |
| Private network | None | Hetzner 10.0.0.0/16 |
| Database | Shared Neon project, branch | Dedicated Neon project, main |
| DB auto-suspend | Yes (idle = free) | No (always-on compute) |
| Redundancy | None | LB health checks, auto-failover |
| Cost | ~€4/month shared | ~€33/month per app |
| Path to scale | Add to shared server | Add servers to LB pool |

The critical architectural commitment at S4 is the **load balancer
from day one**, even before a second server is added. The LB is the
pivot that makes S5 scaling trivially additive — new servers join the
pool without any DNS change.

---

## The mental model

S4 is the first architecture that can handle real users, real uptime
requirements, and real data sensitivity.

The key design insight: **traffic never touches your servers directly**.
It flows:
```
User → Cloudflare edge (CDN, WAF, DDoS) → Hetzner LB (TLS, health)
     → private network → app server (kamal-proxy, container) → Neon
```

Every layer adds a capability:
- **Cloudflare**: CDN caching, DDoS absorption, WAF, bot protection
- **Load balancer**: health checking, TLS termination, traffic distribution
- **Private network**: LB→server traffic never on public internet
- **Dedicated server**: no resource contention from other apps
- **Dedicated Neon**: always-on compute, PITR backups, no shared limits

---

## Full network topology

```
                ┌────────────────────────────────────────────────────────┐
                │                Namecheap (registrar)                    │
                │    NS records → ns1.cloudflare.com, ns2.cloudflare.com  │
                │    (set at S1; never changes again)                     │
                └────────────────────────────────────────────────────────┘

                ┌────────────────────────────────────────────────────────┐
                │              Cloudflare Edge (orange cloud)            │
                │                                                         │
                │   app.yourdomain.com  →  A  9.10.11.12  [PROXIED]     │
                │                                                         │
                │   Services active:                                      │
                │   ├── CDN: cache /_app/immutable/* at edge             │
                │   ├── SSL: Full Strict (CF Origin Cert)                │
                │   ├── DDoS: Layer 3/4/7 mitigation                    │
                │   ├── WAF: OWASP rules (free tier)                     │
                │   ├── Bot Fight Mode: on                               │
                │   └── Always Use HTTPS: on                             │
                │                                                         │
                │   Forwards to origin: 9.10.11.12:443 (HTTPS)          │
                └────────────────────────┬───────────────────────────────┘
                                         │ HTTPS (CF Origin Cert)
                                         │ CF adds X-Forwarded-For
                ┌────────────────────────▼───────────────────────────────┐
                │           Hetzner Load Balancer LB11                   │
                │           Public IP: 9.10.11.12  (€5.83/mo)           │
                │                                                         │
                │   Port 443: terminate SSL (CF Origin Cert)             │
                │              forward HTTP → private network :80        │
                │   Port 80:  redirect → HTTPS (via LB rule)            │
                │                                                         │
                │   Health check: GET /up every 30s on port 80          │
                │   Algorithm: least connections                         │
                │   If /up fails 3× → remove server from pool            │
                └───────────────────────┬────────────────────────────────┘
                                        │ HTTP  (private network only)
                       ┌────────────────┴──────────────────┐
                       │    Hetzner Private Network         │
                       │    10.0.0.0/16                     │
                       │    (€0, included with Hetzner)     │
                       └────────────┬──────────────────────┘
                                    │ HTTP :80
                ┌───────────────────▼────────────────────────────────────┐
                │         Hetzner CX32  (dedicated, €8.21/mo)            │
                │         Public IP: 1.2.3.4   Private IP: 10.0.0.2     │
                │                                                         │
                │   Hetzner Firewall:                                     │
                │   ├── :22  allow from [deploy IPs only]                │
                │   ├── :80  allow from private network (10.0.0.0/16)    │
                │   └── all other inbound: deny                          │
                │                                                         │
                │   ┌──────────────────────────────────────────────────┐ │
                │   │          kamal-proxy                               │ │
                │   │  :80 only (ssl: false — LB handles TLS)           │ │
                │   │  routes Host: → app container                      │ │
                │   │  zero-downtime drain/swap on deploy                │ │
                │   └───────────────────────┬──────────────────────────┘ │
                │                           │                             │
                │   ┌───────────────────────▼──────────────────────────┐ │
                │   │             app container  :3000                   │ │
                │   │             SvelteKit (SSR)                        │ │
                │   │             DATABASE_URL from .kamal/secrets       │ │
                │   └───────────────────────┬──────────────────────────┘ │
                └───────────────────────────┼────────────────────────────┘
                                            │ TLS (sslmode=require)
                ┌───────────────────────────▼────────────────────────────┐
                │              Neon Postgres (Launch plan, $19/mo)       │
                │                                                         │
                │   Project: app-prod                                     │
                │   ├── main  (production — always-on compute)           │
                │   └── dev   (optional — for schema testing)             │
                │                                                         │
                │   Connection: ep-xxx.eu-central-1.aws.neon.tech:5432   │
                │   (S5 pooler: ep-xxx-pooler...neon.tech:6432)          │
                └─────────────────────────────────────────────────────────┘
```

---

## Layer by layer: what each component does

### Layer 1 — Namecheap (registrar, unchanged)

Namecheap delegates DNS to Cloudflare via NS records. This was
configured at S1 and never needs to change. The registrar is just
the "ownership" layer; all DNS management happens in Cloudflare.

### Layer 2 — Cloudflare (edge, CDN, security)

At S3 the CF record was grey cloud (direct pass-through). At S4 it
flips to **orange cloud** (CF proxied). This unlocks:

**CDN caching.** Static assets from SvelteKit (`/_app/immutable/*`) are
content-addressed and cache-forever. CF caches them at 300+ edge
locations globally. Repeat visitors load instantly from the nearest
edge; your server never sees those requests.

**DDoS protection.** CF's free tier absorbs volumetric attacks at the
edge (Layers 3, 4, and 7). The origin server is shielded.

**WAF.** Basic Web Application Firewall rules (OWASP CRS) are active on
free tier. They block common injection attacks, scanners, and known
malicious patterns.

**Full Strict SSL.** CF terminates HTTPS from the browser, then
re-establishes HTTPS to the origin (the Hetzner LB). Full Strict mode
requires a valid certificate on the origin — this is the Cloudflare
Origin Certificate (see below).

**Real IP forwarding.** CF adds `X-Forwarded-For` and `CF-Connecting-IP`
headers so your app sees the real client IP, not CF's edge IP. Enable
`proxy.forward_headers: true` in kamal's deploy.yml.

### Layer 3 — The TLS chain and Cloudflare Origin Certificate

This is the most important and most confusing part. There are two TLS
legs:

```
Browser ──HTTPS──→ Cloudflare ──HTTPS──→ Hetzner LB ──HTTP──→ App server
         (Let's     (CF issues  (Origin    (terminates    (private
          Encrypt)   browser     Cert)      TLS, serves    network)
                     cert)                  HTTP to app)
```

**Leg 1 (browser → CF):** CF uses a Cloudflare-issued certificate
(managed automatically, always valid, no cost). Browsers see a valid
cert from Cloudflare.

**Leg 2 (CF → LB):** CF uses the **Cloudflare Origin Certificate** to
verify the LB. This cert is:
- Free
- Valid for 15 years (no renewal)
- Issued by Cloudflare CA (trusted ONLY by CF's edge, not by browsers)
- Covers `*.yourdomain.com` and `yourdomain.com`
- Downloaded from the CF dashboard, installed on the Hetzner LB

Because it's only trusted by CF, it perfectly serves the "CF-to-origin"
leg. No browser can directly validate this cert — which is fine because
browsers connect to CF's edge, not directly to the LB.

**Leg 3 (LB → server):** Plain HTTP on the private Hetzner network
(10.0.0.0/16). No TLS needed — the private network is not accessible
from the internet.

**SSL mode: Full (Strict)** in CF tells CF: "re-encrypt to the origin
AND verify the origin's cert is valid." This is the only mode that
closes the "flexible SSL" security gap (where CF→origin is unencrypted).

**Getting the Origin Certificate:**
1. CF dashboard → SSL/TLS → Origin Server → Create Certificate
2. Choose: Cloudflare generates key, RSA or ECDSA, 15 years
3. Enter hostnames: `yourdomain.com`, `*.yourdomain.com`
4. Download: certificate (`.pem`) and private key (`.key`)
5. Store privately: `~/.config/awf/origin.pem`, `~/.config/awf/origin.key`
6. Upload to Hetzner LB as a Certificate resource (one-time)

### Layer 4 — Hetzner Load Balancer (LB11)

The LB is the most important S4 architectural decision. Even with one
server, the LB provides:

1. **SSL termination** — holds the CF Origin Certificate; decrypts
   HTTPS from CF and forwards plain HTTP to the server pool
2. **Health checking** — polls `/up` every 30 seconds on each server;
   automatically removes unhealthy servers from the pool
3. **Traffic distribution** — when S5 adds a second server, traffic
   spreads across the pool immediately with no DNS change
4. **HTTP→HTTPS redirect** — LB can redirect port 80 to 443 before CF
   even sees the request (belt-and-suspenders with CF's "Always HTTPS")
5. **Sticky sessions** — optional cookie-based stickiness if your app
   needs it (prefer stateless JWT sessions instead)

**LB11 limits** (the cheapest Hetzner LB):
- 5 backend targets (enough for S4 and most S5 configs)
- 1,000,000 monthly requests included
- €0.007/hour = €5.83/month
- Upgrade to LB21 (25 targets, €14.16/mo) when needed

**Health check configuration:**

```
Protocol: HTTP
Port: 80
Path: /up
Interval: 30 seconds
Timeout: 10 seconds
Retries before unhealthy: 3
Retries before healthy: 2
```

The `/up` endpoint at S4 should return 200 only when the app is
genuinely healthy — including a database connectivity check. See the
health check section below.

### Layer 5 — Hetzner Private Network

A Hetzner private network (10.0.0.0/16) connects the LB and all
app servers internally. Benefits:

- LB→server traffic does not traverse the public internet
- Server port 80 is accessible only from within the network (via firewall)
- Adding a new server at S5 is automatic — join the network and you're
  reachable from the LB
- No bandwidth charges for private network traffic

The LB gets one IP in the network (e.g. 10.0.0.1). Each server gets
the next available IP (10.0.0.2, 10.0.0.3, …).

### Layer 6 — Hetzner Firewall

The S4 firewall hardens the server against direct internet access:

| Rule | Direction | Protocol | Port | Source |
|------|-----------|----------|------|--------|
| SSH | inbound | TCP | 22 | Your IPs + CI runner IPs |
| HTTP from LB | inbound | TCP | 80 | 10.0.0.0/16 (private net) |
| All other inbound | inbound | — | — | DENY |
| Outbound HTTPS | outbound | TCP | 443 | anywhere (GHCR, Neon) |
| Outbound DNS | outbound | UDP | 53 | anywhere |

**Why deny port 80 from the public internet?** Without this rule,
someone who discovers your server's public IP can send requests directly
to the app, bypassing CF's WAF and DDoS protection. Restricting port 80
to the private network means all traffic must flow through
CF → LB → private network.

### Layer 7 — App server (CX32)

The CX32 (4 vCPU, 8 GB RAM) is the recommended default for S4. Why not
CX22 (2 vCPU, 4 GB)?

- A production app with real traffic can spike CPU during SSR
- 8 GB gives headroom for the app container + kamal-proxy + Docker overhead
- CX32 is shared vCPU — fine for most apps; upgrade to CCX23 (dedicated
  vCPU, €14.46/mo) if you need consistent CPU performance

What runs on the server:
- Docker daemon (boots at server start)
- kamal-proxy container (always running, :80 only — no TLS at S4)
- App container (restarted automatically on crash or deploy)

**The critical `ssl: false` change:**
At S3, kamal-proxy handled TLS (`ssl: true`). At S4, the LB handles TLS.
The `config/deploy.yml` changes to:

```yaml
proxy:
  ssl: false                # LB handles TLS, not kamal-proxy
  host: app.yourdomain.com
  forward_headers: true     # pass X-Forwarded-For from LB to app
```

Without `forward_headers: true`, your app sees the LB's IP as the
client IP for every request. With it, CF's real client IP flows through.

### Layer 8 — Neon Postgres (dedicated)

At S4, the app has its own Neon project on the Launch plan ($19/month):
- **300 compute hours/month** (no auto-suspend on demand, though compute
  can be manually suspended)
- **7-day point-in-time restore** (PITR) — roll back to any second
  in the past 7 days
- **Dedicated compute** — no sharing with other projects
- **Branching** — create `dev` or `preview` branches from `main` for
  schema testing without touching production data

Connection string:
```
postgres://user:pass@ep-xxx.eu-central-1.aws.neon.tech/neondb?sslmode=require
```

At S5 (multiple servers), switch to the **pooler endpoint** by appending
`-pooler` to the hostname. This routes through PgBouncer and handles
the connection limit problem:

```
# S4 (direct, 1 server):
ep-xxx.eu-central-1.aws.neon.tech:5432

# S5 (pooled, 3+ servers):
ep-xxx-pooler.eu-central-1.aws.neon.tech:6432
```

---

## State files at S4

The changes in `infra.json` from S3 to S4:

```json
{
  "registry": { "host": "ghcr.io", "image": "user/app", "user": "user" },
  "hetzner": {
    "servers": [
      {
        "id":       "dedicated-server-id",
        "ip":       "1.2.3.4",          ← public IP (for SSH)
        "role":     "web",
        "shared":   false,              ← was true at S3
        "cost_eur_month": 8.21
      }
    ],
    "lb_id":      "lb-12345",           ← populated (was null)
    "network_id": "net-67890"           ← populated (was null)
  },
  "neon": {
    "project_id":           "dedicated-proj-id",  ← own project
    "branch_id":            "",                   ← using main
    "branch_name":          "main",
    "mode":                 "dedicated",           ← was "shared-branch"
    "connection_secret_ref": "DATABASE_URL"
  },
  "kamal": {
    "config_path":        "config/deploy.yml",
    "last_deploy_image":  "user/app:latest"
  }
}
```

The `config/deploy.yml` at S4:

```yaml
service: my-app
image: ghcr.io/user/my-app

servers:
  web:
    hosts:
      - 1.2.3.4          # server public IP (SSH target for kamal)

proxy:
  ssl: false             # LB handles SSL
  host: app.yourdomain.com
  forward_headers: true  # real client IP from CF headers

registry:
  server: ghcr.io
  username: user
  password:
    - <%= ENV["GHCR_TOKEN"] %>

env:
  secret:
    - DATABASE_URL
```

---

## The health check at S4

The `/up` endpoint in the SvelteKit template currently returns a plain
`"OK"` string. At S4, it should verify the database is reachable:

```typescript
// src/routes/up/+server.ts
import { json } from '@sveltejs/kit';
import { sql } from '$lib/db';

export async function GET() {
  try {
    await sql`SELECT 1`;
    return json({ status: 'ok', db: 'ok' }, { status: 200 });
  } catch (e) {
    return json({ status: 'error', db: 'unreachable' }, { status: 503 });
  }
}
```

**Why this matters:** The LB health check polls `/up`. If the server
returns 503, the LB stops sending it traffic and alerts. Without a DB
check, a dead database connection causes 500 errors to users while the
LB still considers the server healthy.

At S5 with multiple servers, a DB connectivity failure on one server
(e.g. network blip) causes the LB to redirect all traffic to the
healthy servers automatically — without any human intervention.

---

## Credentials needed for S4

In addition to all S1 credentials, S4 needs:

| Credential | Where | How to get |
|---|---|---|
| `HETZNER_API_TOKEN` | `~/.config/awf/.env` | console.hetzner.cloud → Security → API Tokens (R/W) |
| `NEON_API_KEY` | `~/.config/awf/.env` | console.neon.tech → Account → API Keys |
| `GHCR_TOKEN` | `.kamal/secrets` | github.com → Settings → Dev settings → PAT (`write:packages`) |
| CF Origin Cert (`.pem`) | `~/.config/awf/origin.pem` | CF dashboard → SSL/TLS → Origin Server → Create Certificate |
| CF Origin Key (`.key`) | `~/.config/awf/origin.key` | downloaded with the cert above |

The CF Origin Certificate is the new credential unique to S4. It is:
- Never committed to git
- Stored at `~/.config/awf/origin.pem` and `~/.config/awf/origin.key`
- Referenced as `CF_ORIGIN_CERT_PATH` and `CF_ORIGIN_KEY_PATH` env vars
- Uploaded to Hetzner LB once during `awf-hetzner-lb` setup
- Valid for 15 years — no renewal

---

## The S3 → S4 migration: complete sequence

### Pre-flight

```bash
# Check all S4 credentials are present:
uv run ~/.claude/skills/awf-doctor/scripts/check.py --for-stage prescale

# Verify app is healthy on S3:
curl https://app.yourdomain.com/up
```

### Phase 1 — Provision infrastructure (no traffic impact)

**Step 1: Get the CF Origin Certificate**

In the Cloudflare dashboard:
- SSL/TLS → Origin Server → Create Certificate
- Generate private key: RSA 2048 or ECDSA P-256
- Hostnames: `yourdomain.com`, `*.yourdomain.com`
- Validity: 15 years
- Download both files

```bash
# Store securely:
cp ~/Downloads/cloudflare-origin.pem ~/.config/awf/origin.pem
cp ~/Downloads/cloudflare-origin.key ~/.config/awf/origin.key
chmod 600 ~/.config/awf/origin.pem ~/.config/awf/origin.key

# Set env vars (add to ~/.config/awf/.env):
CF_ORIGIN_CERT_PATH=~/.config/awf/origin.pem
CF_ORIGIN_KEY_PATH=~/.config/awf/origin.key
```

**Step 2: Create the private network**

```bash
uv run ~/.claude/skills/awf-hetzner-network/scripts/hetzner_network.py \
    --name app-net \
    --cidr 10.0.0.0/16 \
    --location nbg1
```

Records `network_id` in `infra.json`.

**Step 3: Create the dedicated Hetzner server**

```bash
uv run ~/.claude/skills/awf-hetzner-server/scripts/hetzner_server.py \
    --name app-prod \
    --type cx32 \
    --location nbg1 \
    --network-id <network_id_from_step_2>
```

Records server ID, public IP, and private IP (10.0.0.2) in `infra.json`.
Server joins the private network at creation time.

**Step 4: Create the Hetzner LB**

```bash
uv run ~/.claude/skills/awf-hetzner-lb/scripts/hetzner_lb.py \
    --name app-lb \
    --type lb11 \
    --location nbg1 \
    --network-id <network_id> \
    --target-private-ip 10.0.0.2 \
    --cert-path ~/.config/awf/origin.pem \
    --cert-key-path ~/.config/awf/origin.key
```

This:
- Creates the LB11 (gets public IP 9.10.11.12)
- Attaches it to the private network
- Uploads the CF Origin Certificate to Hetzner
- Configures HTTPS service: port 443 → target private IP :80
- Configures HTTP redirect: port 80 → 443
- Configures health check: GET /up on :80 every 30s

Records `lb_id` in `infra.json`.

**Step 5: Configure the Hetzner Firewall**

```bash
uv run ~/.claude/skills/awf-hetzner-firewall/scripts/hetzner_firewall.py \
    --name app-fw \
    --server-id <server_id> \
    --allow-ssh-from "YOUR.IP.ADDRESS/32,CI.RUNNER.IP/32" \
    --allow-http-from-private  # 10.0.0.0/16 only
```

**Step 6: Create the dedicated Neon project**

```bash
uv run ~/.claude/skills/awf-neon-project/scripts/neon_project.py \
    --name app-prod
```

Records `project_id` in `infra.json`. The connection string for the
`main` branch is shown in output.

### Phase 2 — Database migration (brief downtime)

This is the one manual gate. Choose a low-traffic window.

```bash
# Get old connection string (S3 Neon branch):
OLD_DB=$(grep DATABASE_URL .kamal/secrets | cut -d= -f2-)

# Get new connection string (S4 dedicated project main branch):
# From the Neon console or the output of awf-neon-project
NEW_DB="postgres://user:pass@ep-new-endpoint.neon.tech/neondb?sslmode=require"

# Dump from S3 branch, restore to S4 project:
# (app is still running on S3 during dump — read traffic continues)
pg_dump "$OLD_DB" --no-owner --no-acl --clean > /tmp/app-dump.sql

# Brief write downtime starts here (~10–30 seconds):
# Option A: put a maintenance page in CF (CF Rules → return 503)
# Option B: just proceed — writes during restore window will be lost

psql "$NEW_DB" < /tmp/app-dump.sql

# Update the secret:
uv run ~/.claude/skills/awf-app-secret-set/scripts/app_secret_set.py \
    --key DATABASE_URL --value "$NEW_DB"
```

For zero-downtime DB migration (advanced): use Neon's logical
replication to stream changes from the S3 branch to the S4 project
continuously, then cut over with a final sync. Practical guide in
Neon's documentation under "Logical Replication."

### Phase 3 — Deploy to S4 server

**Step 7: Re-render deploy.yml**

```bash
uv run ~/.claude/skills/awf-kamal-config/scripts/kamal_config.py
```

The renderer picks up the new server IP and writes:
```yaml
proxy:
  ssl: false          # no Let's Encrypt; LB handles TLS
  host: app.yourdomain.com
  forward_headers: true
```

**Step 8: kamal setup on the new server**

```bash
uv run ~/.claude/skills/awf-kamal-setup/scripts/kamal_setup.py \
    --server-ip 1.2.3.4   # new server's PUBLIC IP
```

This polls DNS, installs Docker and kamal-proxy on the new server.
kamal-proxy will serve on :80 only (no TLS).

**Step 9: Deploy the app**

```bash
uv run ~/.claude/skills/awf-kamal-deploy/scripts/kamal_deploy.py
```

Builds image, pushes to GHCR, deploys to new server.

**Step 10: Smoke test before cutting DNS**

```bash
# Test app on new server directly (bypassing DNS):
curl -H "Host: app.yourdomain.com" http://1.2.3.4/up
# Expected: 200

# Test DB connection (from container perspective):
curl -H "Host: app.yourdomain.com" http://1.2.3.4/api/healthcheck
```

### Phase 4 — Cut traffic to S4

**Step 11: Update CF DNS to point at LB**

```bash
uv run ~/.claude/skills/awf-cf-dns-record/scripts/cf_dns_record.py \
    --domain app.yourdomain.com \
    --ip 9.10.11.12 \
    --proxied          # orange cloud (this is the key change from S3)
```

The record changes from:
- `A app.yourdomain.com → 1.2.3.4` (grey, old server)

To:
- `A app.yourdomain.com → 9.10.11.12` (orange, LB IP)

CF propagation: < 30 seconds.

**Step 12: Set CF SSL mode to Full (Strict)**

In CF dashboard: SSL/TLS → Overview → Full (Strict)

Or via API (skill to be built):
```bash
curl -X PATCH "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/settings/ssl" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -d '{"value":"strict"}'
```

**Step 13: Enable CF performance features**

In CF dashboard:
- Speed → Optimization → Brotli: On
- Speed → Optimization → HTTP/2: On
- SSL/TLS → Edge Certificates → Always Use HTTPS: On
- SSL/TLS → Edge Certificates → Min TLS Version: 1.2
- SSL/TLS → Edge Certificates → HSTS: Enable (max-age=31536000)

Cache rule (via CF dashboard → Caching → Cache Rules):
```
Match: /_app/immutable/*
Cache eligibility: Eligible for cache
Edge TTL: 1 year (or "respect origin" — SvelteKit sets this header)
```

### Phase 5 — Verify and clean up

**Step 14: Verify end-to-end**

```bash
# Full chain test (browser path):
curl -I https://app.yourdomain.com/up
# Expected headers:
# CF-Ray: ...                    ← proves traffic went through CF
# Server: cloudflare             ← CF header
# HTTP/2 200                     ← HTTP/2 from CF edge
# X-Forwarded-Proto: https       ← from LB to app

# SSL chain:
# ssllabs.com/ssltest → should show A or A+

# LB health check (Hetzner console):
# Targets tab: server shows "healthy"
```

**Step 15: Update project stage**

```bash
# Edit .awf/project.json:
# "stage": "prescale"
```

**Step 16: Clean up S3**

```bash
# Stop the old container on the shared play server:
ssh root@<play-server-ip> \
    "docker stop <app-slug>-web-1 && docker rm <app-slug>-web-1"

# Delete the S3 Neon branch (keep 7+ days as backup):
# Via Neon console: Projects → awf-play → Branches → delete app-slug branch
# (safe to do after confirming S4 is running well)
```

---

## S4 → S5: adding a second server

With the LB already in place, adding a second server is additive.
No DNS change. No LB IP change. No CF reconfiguration.

### When to add a second server

Signals that a second server is needed:
- Sustained CPU > 70% on the single server
- Response time P95 > 500ms under normal load
- You want zero-downtime deploys guaranteed (one server can't achieve this alone — during deploy, container swaps cause brief interruption)
- You need maintenance windows without downtime

### Adding the server (step by step)

**Step 1: Create the new server on the same private network**

```bash
uv run ~/.claude/skills/awf-hetzner-server/scripts/hetzner_server.py \
    --name app-prod-2 \
    --type cx32 \
    --location nbg1 \
    --network-id <same-network-id>
```

New server gets private IP 10.0.0.3.

**Step 2: Update infra.json to include both servers**

```json
"servers": [
  { "id": "srv-1", "ip": "1.2.3.4", "role": "web", "shared": false },
  { "id": "srv-2", "ip": "5.6.7.8", "role": "web", "shared": false }
]
```

**Step 3: Apply the same firewall to the new server**

Same rules: SSH from your IPs, port 80 from 10.0.0.0/16 only.

**Step 4: kamal setup on the new server**

```bash
uv run ~/.claude/skills/awf-kamal-setup/scripts/kamal_setup.py \
    --server-ip 5.6.7.8
```

Installs Docker and kamal-proxy on the new server.

**Step 5: Update deploy.yml to include both servers**

```yaml
servers:
  web:
    hosts:
      - 1.2.3.4   # server 1
      - 5.6.7.8   # server 2
```

```bash
uv run ~/.claude/skills/awf-kamal-config/scripts/kamal_config.py
```

**Step 6: Deploy to both servers**

```bash
uv run ~/.claude/skills/awf-kamal-deploy/scripts/kamal_deploy.py
```

Kamal deploys to both hosts sequentially (rolling). Server 2 starts
receiving traffic from the LB as soon as kamal-proxy registers the
container and `/up` returns 200.

**Step 7: Add server 2 to the LB target pool**

```bash
# Via Hetzner console: LB → Targets → Add Target → 10.0.0.3:80
# Or via skill (to be built in Phase D):
uv run ~/.claude/skills/awf-hetzner-lb-add-target/scripts/lb_add_target.py \
    --lb-id <lb_id> \
    --private-ip 10.0.0.3 \
    --port 80
```

The LB immediately starts distributing traffic to both servers.

**Step 8: Switch to Neon pooler endpoint**

With 2+ servers, each server maintains its own connection pool to Neon.
Postgres has a default connection limit of 100. With 2 servers × 20
concurrent requests each = 40 connections. Fine for now.

Switch to the pooler endpoint before you hit the limit (typically at 4+
servers or high concurrency):

```bash
# Change endpoint from direct (5432) to pooler (6432):
POOLER_URL="postgres://user:pass@ep-xxx-pooler.eu-central-1.aws.neon.tech:6432/neondb?sslmode=require"

uv run ~/.claude/skills/awf-app-secret-set/scripts/app_secret_set.py \
    --key DATABASE_URL --value "$POOLER_URL"

# Redeploy to pick up new DATABASE_URL:
uv run ~/.claude/skills/awf-kamal-deploy/scripts/kamal_deploy.py
```

The `-pooler` hostname suffix activates Neon's built-in PgBouncer.
It supports hundreds of connections from the application side while
Neon itself maintains a small pool to Postgres.

---

## Rolling deploys across multiple servers

When `kamal deploy` runs with multiple servers in `deploy.yml`:

```
kamal deploy
├── Build Docker image → push to GHCR
├── Server 1 (1.2.3.4):
│   ├── SSH in
│   ├── docker pull image
│   ├── Start new container
│   ├── Poll /up → 200 ✓
│   ├── kamal-proxy: register new → drain old → stop old
│   └── (Server 1 now serving new version)
│   
│   (During this time, the LB sends some traffic to Server 1 on old
│    version and some to Server 2 on old version — all working fine)
│   
└── Server 2 (5.6.7.8):
    ├── SSH in
    ├── docker pull image
    ├── Start new container
    ├── Poll /up → 200 ✓
    ├── kamal-proxy: register new → drain old → stop old
    └── (Server 2 now serving new version)
```

During deploy, there is a brief window (~30 seconds) where Server 1
serves the new version and Server 2 serves the old version simultaneously.
This is "rolling deploy" semantics. Design implications:

- **API changes must be additive.** Don't remove a field that v1 code
  uses until all servers are on v2.
- **Database schema changes** must be backward compatible with the old
  app version. Use the expand/contract pattern for zero-downtime
  migrations.
- **Sessions/cookies** must be readable by both old and new code during
  the transition window.

For apps that cannot tolerate mixed versions, use **blue/green deploy**:
- Route 100% of LB traffic to Server 1 (primary)
- Deploy to Server 2 (secondary) and test
- Switch LB to 100% Server 2
- Keep Server 1 as rollback target for 30 minutes

Hetzner LB supports target weights for this pattern.

---

## Database operations at S4/S5

### Schema migrations

**Option A — Maintenance window (simplest, recommended for S4):**

```bash
# 1. Put CF in maintenance mode:
#    CF Rules → Route → return 503 with custom HTML
# 2. Wait for in-flight requests to drain
# 3. Run migration:
DATABASE_URL="$NEW_DB" npx prisma migrate deploy
#    or: node -e "require('./lib/db').runMigrations()"
# 4. Deploy new app version
# 5. Remove CF maintenance rule
```

Total downtime: 15–60 seconds depending on migration complexity.

**Option B — Expand/contract (zero-downtime, recommended for S5):**

1. **Expand phase:** Add new columns as nullable; keep old columns.
   Deploy v1.1 which writes to both old and new columns simultaneously.
2. **Backfill:** Update all existing rows to populate new columns.
3. **Contract phase:** Remove old columns in a later deploy.

Example: renaming `username` → `display_name`:
- v1.1: Add `display_name` (nullable). Write both `username` and
  `display_name`. Read `display_name ?? username`.
- Backfill: `UPDATE users SET display_name = username WHERE display_name IS NULL`
- v1.2: Remove `username`. Read `display_name` only.

**Option C — Neon branching for schema testing:**

```bash
# Create a branch from production for testing:
# Neon console → Project → Branches → Create Branch from main

# Test migration against production-like data:
DATABASE_URL_DEV="postgres://...dev-branch.../neondb"
DATABASE_URL_DEV=$DATABASE_URL_DEV npx prisma migrate deploy

# If OK, apply to production main:
DATABASE_URL=$PROD_DB npx prisma migrate deploy
```

### Backups

At S4 with Neon Launch plan, you get 7-day PITR (point-in-time
restore). This covers most scenarios.

For longer retention or export:

```bash
# Manual backup to local file (can automate as a cron job):
pg_dump "$DATABASE_URL" --no-owner --clean \
    | gzip > backup-$(date +%Y%m%d).sql.gz

# Store in Hetzner Object Storage (S3-compatible):
# Or store in R2 (Cloudflare) — €0 egress
```

### Restoring from backup

```bash
# PITR via Neon (within 7 days):
# Neon console → Branch → Restore → pick timestamp

# From pg_dump:
gunzip backup-20260601.sql.gz
psql "$DATABASE_URL" < backup-20260601.sql
```

---

## Session management across multiple servers

S5 requires that session state is not tied to a single server. Options:

**JWT (recommended for SvelteKit apps):**
- Sessions are encoded in a signed cookie (JWT or similar)
- No server-side session store
- Any server can verify and read any session
- No stickiness needed on the LB

**DB-backed sessions:**
- Session state stored in Neon
- Any server can read/write any session
- Slight latency cost per request
- Works fine for S4/S5

**Avoid memory-backed sessions for S5.** If session state lives only in
a server's memory, a request routed to the other server sees an empty
session. This causes random logout bugs under load — one of the most
confusing multi-server failure modes.

---

## Cost analysis

### S4 baseline (one server)

| Component | Monthly cost |
|-----------|-------------|
| Hetzner CX32 | €8.21 |
| Hetzner LB11 | €5.83 |
| Hetzner private network | €0 |
| Neon Launch | $19.00 |
| Cloudflare Free | €0 |
| GHCR | €0 |
| **Total** | **~€33/month** |

### S5 (two servers)

| Component | Monthly cost |
|-----------|-------------|
| Hetzner CX32 × 2 | €16.42 |
| Hetzner LB11 | €5.83 |
| Neon Launch | $19.00 |
| **Total** | **~€41/month** |

### When to upgrade LB (LB21 at €14.16/mo)

The LB11 handles up to 5 targets and ~1M requests/month. Upgrade when:
- You have more than 5 servers
- Traffic exceeds 1M requests/month
- You need more advanced LB features (LB21 supports 25 targets)

### When to upgrade Neon (Scale at $69/mo)

Launch → Scale when:
- You need more than 7-day PITR (Scale: 30 days)
- Database size exceeds 10 GB
- You want a read replica for analytics queries
- You need SOC 2 compliance (Scale tier)

---

## Security hardening checklist

Before going live on S4, verify:

- [ ] CF SSL mode: Full (Strict)
- [ ] CF Always Use HTTPS: on
- [ ] CF HSTS: enabled (max-age ≥ 31536000)
- [ ] CF Min TLS version: 1.2
- [ ] CF Bot Fight Mode: on
- [ ] App server port 80: accessible from private network only (firewall)
- [ ] App server port 22: accessible from known IPs only (firewall)
- [ ] No other inbound ports open
- [ ] DATABASE_URL: `?sslmode=require` in connection string
- [ ] GHCR_TOKEN: scoped to `read:packages` only for server pulls
- [ ] Docker container: runs as non-root user (set in Dockerfile)
- [ ] Secrets: never committed to git (`.kamal/secrets` in `.gitignore`)
- [ ] CF Origin Certificate: stored at `~/.config/awf/`, never in git
- [ ] SSH: key-based auth only (password auth disabled on server)

---

## What awf-skills automates vs. what is manual today

### Automated (or nearly so)

| Task | Skill |
|------|-------|
| Create private network | `awf-hetzner-network` (lib built, skill needed) |
| Create dedicated server | `awf-hetzner-server` ✅ |
| Create dedicated Neon project | `awf-neon-project` ✅ |
| Scaffold Dockerfile + healthcheck | `awf-app-dockerize` ✅ |
| Render deploy.yml (ssl: false) | `awf-kamal-config` (needs ssl: false support) |
| kamal setup + deploy | `awf-kamal-setup`, `awf-kamal-deploy` ✅ |

### Manual today (Phase D work)

| Task | Gap | Skill needed |
|------|-----|--------------|
| CF Origin Certificate creation | CF API limitation (manual in dashboard) | `awf-cf-origin-cert` (partial automation) |
| Upload cert to Hetzner LB | Not built | `awf-hetzner-lb` (cert upload) |
| Create Hetzner LB | Lib built, skill not built | `awf-hetzner-lb` |
| Configure LB targets, health checks | Lib built, skill not built | `awf-hetzner-lb` |
| Configure Hetzner Firewall | Lib built, skill not built | `awf-hetzner-firewall` |
| Switch CF DNS to orange cloud | Not built | `awf-cf-proxy-toggle` |
| Set CF SSL mode | Not built | `awf-cf-ssl-mode` |
| Database migration (pg_dump/restore) | Complex, manual gate | `awf-db-migrate` (Phase D) |
| Add server to LB pool | Not built | `awf-hetzner-lb-add-target` |
| Full S4 sequence | Not built | `awf-stage-prescale` composer |

---

## Design decisions and rationale

### Why put the LB in before the second server?

Without the LB, adding a second server at S5 requires changing the CF
DNS record from the server IP to the LB IP. That means a TTL wait
(even at 60s, some clients cache longer) and a window where some
traffic hits the old server and some hits the LB. With the LB from day
one, S5 is purely additive — new server joins the pool, nothing else
changes.

The LB also enables health checking at the network layer. If the single
server crashes at 3am, the LB detects it within 90 seconds (3 failed
health checks) and returns 502. That's better than DNS-based failover,
which takes minutes to hours.

### Why not CF Tunnel instead of a public LB?

Cloudflare Tunnel routes traffic from CF's edge to your server without
a public IP, bypassing the LB entirely. It's free and simpler for
simple setups.

**Why not use it here:**
- CF Tunnel doesn't support multiple backend servers natively (you'd
  need to manage a load balancing layer yourself)
- The Hetzner LB provides health checking, cert management, and a
  single stable origin IP that's independent of individual servers
- The LB cost (€5.83/mo) is trivial relative to the operational
  simplicity it provides for S5 scaling

CF Tunnel is better for solo developers who never need >1 server.

### Why CF Origin Certificate over Let's Encrypt on the LB?

Hetzner LB supports "Managed Certificates" (automated LE renewal).
Why use the CF Origin Cert instead?

1. **No renewal management.** 15 years vs 90 days.
2. **Only trusted by CF.** The cert is not publicly trusted by browsers.
   This means if someone discovers your LB IP and hits it directly,
   they get an untrusted cert warning — effectively forcing traffic
   through CF. Let's Encrypt certs are publicly trusted, which makes
   origin bypass easier.
3. **Free regardless of domain count.** One cert covers `*` subdomain,
   all your apps on the same domain.

### Why ssl: false in deploy.yml at S4?

At S3, kamal-proxy issued Let's Encrypt certificates and served HTTPS.
At S4, the LB terminates TLS — kamal-proxy only needs to serve HTTP on
the private network.

If you keep `ssl: true` at S4:
- kamal-proxy tries to issue LE certs (fails because port 443 is firewalled from internet)
- Or kamal-proxy issues LE certs but the LB also terminates TLS (double termination, confused routing)

`ssl: false` + LB TLS termination is the clean separation of concerns.

### Why Hetzner private network between LB and server?

Without a private network, the LB forwards requests to the server's
public IP. That means:
- Traffic from LB to server transits the public internet
- You can't lock down port 80 on the server (the LB has no fixed IP
  on the public internet to whitelist)
- It's slightly slower (public routing vs. private switch)

With a private network, the LB uses the server's private IP. You can
firewall port 80 to only allow traffic from 10.0.0.0/16. The traffic
never leaves Hetzner's private switching fabric.

---

## Edge cases and failure scenarios

### The server dies

**With S4 (1 server + LB):**
LB health check fails → after 3 retries (90s) → LB returns 502 for all
requests. Alert fires. Recovery: provision new server, kamal setup +
deploy (~10 min). The LB IP and CF DNS record are unchanged.

**With S5 (2+ servers + LB):**
LB health check fails on Server 1 → LB routes all traffic to Server 2.
Zero-downtime failover. Alert fires to prompt investigation/replacement.

### A deploy breaks the app

kamal-proxy drains old container and registers new container only after
`/up` returns 200. If the new container never passes the health check,
kamal-proxy keeps serving the old container and the deploy fails.

If the new container starts but has a runtime bug (health check passes
but users see errors):
```bash
# Rollback to previous image:
kamal rollback
# kamal-proxy swaps back to the previous container immediately
```

### Neon goes down

The app's `/up` endpoint returns 503. The LB health check fails (after
90s). LB returns 502 to CF. CF returns its own error page to users.

Recovery: automatic when Neon recovers. No manual intervention needed.
Neon's SLA is 99.95% for Launch plan.

### CF goes down

Neon and the server are fine. Users can't reach the site because CF
is the DNS resolver and proxy. The origin (LB) is unreachable directly
(the LB's CF Origin Cert is not trusted by browsers without CF).

This is the one outage scenario that CF-orange-cloud creates vs.
grey-cloud. CF has ~99.99% uptime historically; this is an acceptable
trade-off for the CDN and DDoS benefits.

If CF downtime is unacceptable: keep a grey-cloud A record as a backup,
only switch to orange during incidents. This is operationally complex
and rarely worth it for S4 apps.

### TLS certificate expires

CF Origin Certificates last 15 years. They expire in 2041. By that
point, the infrastructure will have been replaced many times. No action
needed.

### The LB fails (Hetzner outage)

Hetzner LBs are highly available (they have internal redundancy). In
the unlikely event of an LB failure, update the CF DNS A record to
point directly at a server's public IP and switch to grey cloud. This
is a 5-minute manual procedure that bypasses the LB entirely until
Hetzner resolves the issue.

### Connection pool exhaustion (S5)

If many servers hit Neon simultaneously with many concurrent requests,
the connection limit (100 by default for Neon Launch) can be reached.
Symptoms: `FATAL: remaining connection slots are reserved` errors.

Fix: switch DATABASE_URL to the Neon pooler endpoint immediately.
This is a one-line change and requires a redeploy. No migration, no
data loss.

---

## Quick reference

```bash
# --- S4 DEPLOY --------------------------------------------------------

# Full S3→S4 migration (when awf-stage-prescale is built):
uv run ~/.claude/skills/awf-stage-prescale/scripts/stage_prescale.py

# Individual steps (today, manual sequence):
uv run ~/.claude/skills/awf-hetzner-network/scripts/hetzner_network.py ...
uv run ~/.claude/skills/awf-hetzner-server/scripts/hetzner_server.py ...
uv run ~/.claude/skills/awf-hetzner-lb/scripts/hetzner_lb.py ...
uv run ~/.claude/skills/awf-neon-project/scripts/neon_project.py ...
# ... DB migration ...
uv run ~/.claude/skills/awf-kamal-config/scripts/kamal_config.py
uv run ~/.claude/skills/awf-kamal-setup/scripts/kamal_setup.py --server-ip X
uv run ~/.claude/skills/awf-kamal-deploy/scripts/kamal_deploy.py
# ... update DNS to orange cloud ...

# --- S5 SCALE OUT -----------------------------------------------------

# Add server 2 to the pool:
uv run ~/.claude/skills/awf-hetzner-server/scripts/hetzner_server.py --name app-prod-2 ...
uv run ~/.claude/skills/awf-kamal-setup/scripts/kamal_setup.py --server-ip 5.6.7.8
uv run ~/.claude/skills/awf-kamal-deploy/scripts/kamal_deploy.py    # deploys to all hosts
# Add 10.0.0.3 as LB target (Hetzner console or awf-hetzner-lb-add-target)

# Switch to Neon pooler when you have 4+ servers:
# Update DATABASE_URL port 5432 → 6432 and hostname to -pooler

# --- OPERATIONS -------------------------------------------------------

# Rollback bad deploy:
kamal rollback   # from project directory

# Server health:
ssh root@1.2.3.4 "docker stats --no-stream && free -m"

# App logs:
kamal app logs   # or: uv run ~/.claude/skills/awf-log/scripts/log.py tail -n 50

# LB status:
# Hetzner console → Load Balancers → app-lb → Targets tab

# Pre-flight for S4:
uv run ~/.claude/skills/awf-doctor/scripts/check.py --for-stage prescale

# DB backup:
pg_dump "$DATABASE_URL" --no-owner | gzip > backup-$(date +%Y%m%d).sql.gz
```
