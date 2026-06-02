# 10 — S3 Architecture Guide: Multi-Tenant Lab

The complete reference for the S3 "MVP-play" stage: what runs where,
why it's designed the way it is, how to share it with colleagues, how
to reason about limits, and exactly how to migrate each app to S4
(dedicated) when the time comes.

> **Who this is for.** Someone who wants to understand the S3 shared
> lab deeply — not just how to run it, but what every component does,
> what the failure modes are, and what a clean migration path looks
> like.

---

## The mental model

S3 is a **cheap communal workbench**. One Hetzner server. One Neon
project. Multiple Docker containers, one per app, each on its own
subdomain. Everything shares the same hardware and the same database
service. You pay ~€4/month total regardless of whether you have one app
or ten.

This works because the apps are small, mostly idle, and built for
development/demo use. When an app graduates to real users and real
stakes, you promote it to S4: it gets its own server, its own database,
and its own billing. The code doesn't change; only the infrastructure
does.

```
S1 Landing       S2 Demo         S3 MVP-play      S4 Prescale
──────────────   ────────────    ─────────────    ─────────────
CF Pages         CF Pages        Shared Hetzner   Own Hetzner
No DB            No DB           Shared Neon      Own Neon
€0               €0              €4.35/month      €23+/month
                                 shared           per app
```

---

## Network topology

```
                   ┌────────────────────────────────────────────────┐
                   │              Cloudflare (free tier)             │
                   │                                                  │
                   │  app1.yourdomain.com  →  A  1.2.3.4  (grey) ──┤
                   │  app2.yourdomain.com  →  A  1.2.3.4  (grey) ──┤
                   │  app3.yourdomain.com  →  A  1.2.3.4  (grey) ──┤
                   └─────────────────────────────────────────┬───────┘
                                                             │ TCP 80/443
                                                             │
                   ┌─────────────────────────────────────────▼───────┐
                   │           Hetzner CX22  1.2.3.4  (€4.35/mo)     │
                   │                                                   │
                   │  ┌────────────────────────────────────────────┐  │
                   │  │               kamal-proxy                   │  │
                   │  │  :80  →  redirect to HTTPS                  │  │
                   │  │  :443 →  TLS termination (Let's Encrypt)    │  │
                   │  │         routes by Host: header              │  │
                   │  └──────────┬──────────────────┬──────────────┘  │
                   │             │                  │                  │
                   │   ┌─────────▼───────┐  ┌──────▼──────────┐      │
                   │   │  app1 container │  │  app2 container  │      │
                   │   │  service: app1  │  │  service: app2   │      │
                   │   │  :3000 (intern) │  │  :3000 (intern)  │      │
                   │   │  SvelteKit      │  │  SvelteKit       │      │
                   │   └────────┬────────┘  └───────┬──────────┘      │
                   │            │                   │                  │
                   └────────────┼───────────────────┼──────────────────┘
                                │ DATABASE_URL       │ DATABASE_URL
                                │                   │
                   ┌────────────▼───────────────────▼──────────────────┐
                   │               Neon (serverless Postgres)           │
                   │                                                     │
                   │   Project: "awf-play"  (free tier)                 │
                   │   ├── Branch: main          (empty seed)           │
                   │   ├── Branch: app1-slug     ← app1 connects here  │
                   │   └── Branch: app2-slug     ← app2 connects here  │
                   │                                                     │
                   │   Each branch: own compute endpoint, own data,     │
                   │   auto-suspends after 5 min idle                   │
                   └─────────────────────────────────────────────────────┘
```

### Why grey cloud (not orange)?

The DNS records point directly at the Hetzner IP, bypassing Cloudflare's
proxy/CDN. This is intentional for S3:

- kamal-proxy handles TLS itself via Let's Encrypt. Putting Cloudflare's
  proxy in front creates double-TLS complexity (CF terminates SSL, then
  re-encrypts to the server).
- For a lab, direct connection is simpler to debug — no CF cache
  interference, no IP masking.
- Grey cloud still gives CF DNS management and fast propagation.

At S4, you switch to orange cloud: CF terminates TLS, the server serves
plain HTTP on port 80, and you gain CDN caching and DDoS protection.

---

## Component by component

### kamal-proxy

kamal-proxy is a lightweight Go binary (part of Kamal 2.x) that runs as
a Docker container on the server. It is the only process that binds to
ports 80 and 443.

**What it does:**
- Listens on :80 and :443
- Redirects all HTTP to HTTPS automatically
- Issues and auto-renews Let's Encrypt certificates per hostname
- Routes HTTPS requests by the `Host:` header to the correct app
  container
- Handles zero-downtime deploys (drain, swap, re-register)

**How it routes:**
Each app's `config/deploy.yml` declares a `proxy.host` value. When you
run `kamal deploy`, kamal registers the new container with kamal-proxy
under that hostname. kamal-proxy creates a routing rule:
`app1.yourdomain.com → container-id:3000`.

Multiple apps on the same server have multiple routing rules. There is no
conflict as long as hostnames are unique. Each container runs on an
internal Docker bridge network; kamal-proxy is the only thing that speaks
to the outside world.

**Port allocation:**
You never manually assign ports. kamal-proxy assigns internal ports
dynamically and manages the mapping. Your app just listens on its
configured port (default 3000).

### The Hetzner CX22 server

The smallest sensible server for running real apps. Specs:
- 2 AMD vCPU (shared)
- 4 GB RAM
- 40 GB NVMe disk
- 20 TB bandwidth
- Ubuntu 24.04

**What lives on it:**
- Docker daemon
- kamal-proxy container (always running)
- One container per app (running unless crashed)
- kamal manages rolling deploys via SSH

**Capacity estimates (idle apps):**

| Resource   | Per app (idle) | Per app (active) | CX22 total |
|------------|----------------|------------------|------------|
| RAM        | 50–100 MB      | 150–300 MB       | 4,096 MB   |
| CPU        | ~0%            | 5–20%            | 200%       |
| Disk (img) | 100–300 MB     | same             | 40 GB      |

A CX22 comfortably handles 10–15 small SvelteKit apps. If any app gets
real traffic (hundreds of concurrent users), upgrade the server to CX32
(8 GB RAM, €8.21/month) before adding more apps.

**Signs you're approaching the limit:**
- `free -m` shows less than 500 MB free
- `docker stats` shows CPU throttling
- Response times increasing on all apps simultaneously

### Neon serverless Postgres

Neon is the only Postgres provider that makes a shared-but-isolated
database model economical. The key feature is **auto-suspend**: a
database compute that is idle for 5 minutes shuts itself off and
costs nothing until the next query wakes it.

**The branching model for S3:**

A Neon **project** is the top-level unit — roughly equivalent to "a
Postgres server." A **branch** is a copy-on-write fork of the project's
data at a point in time. Each branch has its own **compute endpoint**
and its own connection string.

For the S3 lab:
- One Neon project lives in `~/.config/awf/shared.json` as
  `play_neon_project_id`. It is created once and reused.
- Each app gets one branch of that project. The branch is created by
  `awf-neon-branch` and its connection string becomes `DATABASE_URL`.
- The `main` branch of the project is empty — it exists as the
  parent for all app branches but is never directly used.

```
Neon Project: awf-play
 │
 ├── main (empty, €0 — seed branch)
 │    │
 │    ├── branch: app1-example-com    endpoint-A.neon.tech
 │    │   └── database: neondb
 │    │
 │    └── branch: app2-example-com    endpoint-B.neon.tech
 │        └── database: neondb
 │
 └── (each branch auto-suspends independently after 5 min)
```

**Connection strings:**
```
app1: postgres://user:pass@ep-cold-smoke-12345.eu-central-1.aws.neon.tech/neondb?sslmode=require
app2: postgres://user:pass@ep-wild-river-67890.eu-central-1.aws.neon.tech/neondb?sslmode=require
```

Different hostnames (`ep-*`) mean fully isolated compute. One app's
slow query cannot stall another's.

**Free tier limits (as of 2026):**
- 1 project maximum
- Up to 10 branches (more than enough for a lab)
- 190 compute hours/month shared across all branches
- 3 GB total storage
- Auto-suspend: 5 minutes idle → compute off

For a lab where most apps are idle, 190 hours covers roughly 25 apps
each being active for 7–8 hours/month. For a demo app you check once a
week, you'll use under 1 compute hour/month.

### Kamal (the deploy tool)

Kamal is a Ruby gem that wraps Docker and SSH to achieve rolling
zero-downtime deploys without Kubernetes. It:

1. Builds your Docker image locally (or on a builder host)
2. Pushes the image to GHCR
3. SSHs into the Hetzner server as root
4. Pulls the image
5. Starts a new container
6. Registers it with kamal-proxy
7. Waits for the healthcheck (`/up`) to pass
8. Removes the old container

Each app has its own `config/deploy.yml`. Running `kamal deploy` from
app1's project directory only touches app1's container.

**Important:** `kamal setup` (which installs Docker and kamal-proxy on
the server) is now idempotent in awf-skills — it tracks
`kamal_setup_done_for_server_id` in `shared.json` and skips re-runs.
The first app deployed runs setup; all subsequent apps skip it.

---

## State files and where they live

Understanding what state lives where is critical for debugging and for
reasoning about what a migration needs to change.

```
~/.config/awf/shared.json          ← user-scope, shared across all projects
  play_server.hetzner_id           ← Hetzner server ID
  play_server.ip                   ← Server IP
  play_server.kamal_setup_done_for_server_id  ← idempotency key
  play_neon_project_id             ← Neon project ID

<project-root>/.awf/project.json   ← project identity
  domain, slug, stage, has:{}

<project-root>/.awf/infra.json     ← project infrastructure
  hetzner.servers[0].id            ← server ID (same as shared.play_server.hetzner_id)
  hetzner.servers[0].ip            ← server IP
  hetzner.servers[0].shared=true   ← marks this as the shared play server
  neon.project_id                  ← Neon project ID (from shared.json)
  neon.branch_id                   ← this app's branch ID
  neon.branch_name                 ← human-readable branch name
  kamal.config_path                ← path to deploy.yml
  kamal.last_deploy_image          ← image tag of last successful deploy

<project-root>/config/deploy.yml   ← Kamal config (generated by awf-kamal-config)
  service: <slug>
  image: ghcr.io/<user>/<slug>
  servers.web.hosts: [<server-ip>]
  proxy.ssl: true
  proxy.host: <domain>

<project-root>/.kamal/secrets      ← runtime secrets (never committed to git)
  DATABASE_URL=postgres://...
  GHCR_TOKEN=ghp_...
```

At S4, the critical changes in `infra.json` are:
- `hetzner.servers[0].shared` becomes `false`
- `hetzner.servers[0].id` and `.ip` point to the new dedicated server
- `neon.project_id` points to the new dedicated Neon project
- `neon.branch_id` and `neon.branch_name` become empty (using `main`)

---

## Setting up a shared lab (multi-person)

The S3 server is designed to be shared. Here's what "sharing" actually
means technically, and the minimum coordination required.

### What one person needs to deploy to your lab server

1. **An SSH public key** on the server (added by you, the server owner)
2. **The server IP** (so they can configure `--server-ip` in their commands)
3. **A Neon API key** (either your key for the shared project, or their own key if they create their own Neon project)
4. **A GHCR token** with `write:packages` scope (their own GitHub account)
5. **A subdomain on your domain** (Cloudflare DNS record pointing to the server IP)

### Adding a colleague's SSH key

```bash
# On your machine, SSH to the server and append their key:
ssh root@1.2.3.4 "echo 'ssh-ed25519 AAAA... colleague@laptop' >> ~/.ssh/authorized_keys"
```

Or use `awf-shared-infra-get` once a multi-key option is added (Phase D).

### Sharing the Neon project

The simplest approach: share your `NEON_API_KEY`. Your colleague puts it
in their `~/.config/awf/.env`. Both of you create branches on the same
project. Their app's data is isolated in its own branch.

If you want more separation, your colleague creates their own Neon
project (`awf-neon-project`) and uses their own `NEON_API_KEY`. The
apps still share the Hetzner server but have completely independent
databases.

### DNS for colleagues

Each colleague's app needs an A record pointing at the shared server IP.
Since the Cloudflare zone is on your account, you add the record:

```bash
# In the colleague's project directory, with your CF credentials:
uv run ~/.claude/skills/awf-cf-dns-record/scripts/cf_dns_record.py \
    --domain colleague-app.yourdomain.com \
    --ip 1.2.3.4 \
    --type A
```

Or the colleague does it from their own domain on their own CF account
— there's no requirement that all apps use the same root domain.

### Slug uniqueness on the shared server

Kamal identifies services by the `service:` field in `deploy.yml`,
which equals the project slug. **Two apps with the same slug on the same
server will collide** — the second deploy overwrites the first.

Enforce uniqueness by namespacing slugs: `alice-my-app` and
`bob-my-app` rather than just `my-app`. The slug is set in
`.awf/project.json` when the project is created.

---

## Capacity planning

### When S3 is enough

S3 works well when:
- Apps are mostly idle (demos, internal tools, side projects)
- Peak traffic is < 50 concurrent users per app
- No single app has strict uptime requirements (one server = no redundancy)
- Total active apps stay under 10–12

### When to upgrade the server (before S4)

Before migrating any individual app to S4, you can simply upgrade the
shared server to a larger Hetzner type. This is done via the Hetzner
console (resize) or by creating a new larger server and re-deploying.

| Server | vCPU | RAM   | Cost/mo  | Fits ~N apps |
|--------|------|-------|----------|--------------|
| CX22   | 2    | 4 GB  | €4.35    | 10–12        |
| CX32   | 4    | 8 GB  | €8.21    | 20–25        |
| CX42   | 8    | 16 GB | €17.06   | 40–50        |

A server resize takes < 5 minutes and all containers restart
automatically. No app migration required.

### When to migrate an app to S4 (dedicated)

An individual app needs S4 when:
- It has paying users with uptime expectations
- It needs > 100 concurrent users sustained
- You need SLA guarantees (the shared server has no redundancy)
- Data compliance requires isolation (GDPR, SOC2, etc.)
- Database size exceeds Neon free tier (3 GB)
- You want to run background workers, cron jobs, or Redis

S4 per-app cost estimate:
- Hetzner CX22 (dedicated): €4.35/month
- Neon Launch plan: $19/month (300 compute hours, no auto-suspend)
- Optional Cloudflare Pro: $20/month (not required)
- **Total: ~€23–25/month per app**

---

## Edge cases and failure modes

### A container crashes

kamal-proxy returns a 502. The other apps continue unaffected. Docker's
`restart: unless-stopped` policy (set by Kamal) restarts the container
within seconds. No intervention needed.

Check status: `ssh root@1.2.3.4 "docker ps -a | grep <slug>"`

### The server reboots

Docker daemon starts automatically. All containers have
`restart: unless-stopped` — they restart after the daemon. kamal-proxy
also restarts. All apps come back within ~30 seconds of the server
booting. No data loss (apps are stateless; data is in Neon).

### One app under heavy load degrades others

The shared CX22 is a real machine with finite CPU and memory. One app
getting hammered will cause resource contention for the others.

Short-term fix: stop the overloaded app (`kamal app stop`) until you
can promote it to S4.

Long-term fix: resource limits per container (memory/CPU in deploy.yml).
This is not currently implemented in awf-skills but is a straightforward
Kamal config addition.

### TLS certificate fails to renew

Let's Encrypt certs expire after 90 days. kamal-proxy renews them
proactively ~30 days before expiry. Renewal requires the domain to
be reachable over HTTP on port 80 (ACME http-01 challenge).

This fails if: the server is down, DNS has changed, or the app has
removed the port 80 listener. The grey-cloud setup means Cloudflare is
not in the way, so the ACME challenge goes directly to kamal-proxy.

The `/up` healthcheck endpoint ensures the app is reachable. As long
as kamal-proxy is running and DNS points to the server, renewal is
automatic.

### Two people deploy the same slug

The second `kamal deploy` wins. The first app's container is replaced.
The Neon branch for the first app still exists and is unaffected —
only the running container changes.

Mitigation: agree on slug conventions before sharing a server.

### Neon free tier compute hours exhausted

If all branches collectively burn through 190 hours in a month, Neon
suspends all computes until the next billing cycle. Every query returns
a connection error.

Signs: all apps return 500 or database connection errors simultaneously.
Fix: upgrade to Neon Launch ($19/month for the project) or reduce
activity. For a lab, this is unlikely unless apps have automated traffic.

### The shared server runs out of disk

Docker images accumulate. 40 GB fills up with ~80–100 images of 500 MB
each. After several deploys of the same app, old image layers pile up.

Fix: `ssh root@1.2.3.4 "docker system prune -f"` removes dangling
images and stopped containers. Safe to run on a live server.

Long-term: add a cron job to prune weekly, or a `kamal prune` step
post-deploy (not currently in awf-skills).

### DNS change takes too long

`awf-kamal-setup` polls for DNS propagation before running setup.
Default timeout: 600 seconds (10 minutes). Cloudflare grey-cloud
records typically propagate in < 60 seconds. If it times out, the
skill exits with code 3 and emits a `gate=dns_propagation` event in
the log.

Re-run once `dig +short app.yourdomain.com` returns the expected IP.
The skill is idempotent — re-running is always safe.

---

## The S3 → S4 migration: every step

This is the precise sequence for promoting one app from the shared lab
to its own dedicated infrastructure. No data loss. No DNS downtime
beyond one TTL period.

### Preconditions

- App is running on S3 (container live, `/up` returning 200)
- You have `HETZNER_API_TOKEN` with read/write access
- You have `NEON_API_KEY` (your own account, not the shared one)
- You can SSH to the play server

### Step 1 — Create the dedicated Hetzner server

```bash
# From the project directory:
uv run ~/.claude/skills/awf-hetzner-server/scripts/hetzner_server.py \
    --type cx22 \
    --location nbg1   # same DC as play server to minimise latency
```

This creates a new CX22, uploads your SSH key, and writes the server
details to `.awf/infra.json` (replacing the shared server reference).

State change: `infra.hetzner.servers[0].shared` flips to `false`.

### Step 2 — Create the dedicated Neon project

```bash
uv run ~/.claude/skills/awf-neon-project/scripts/neon_project.py \
    --name "<app-slug>-prod"
```

This creates a new Neon project with a `main` branch. Writes
`neon.project_id` to `infra.json`. The old branch ID (`neon.branch_id`)
is retained for the migration.

### Step 3 — Migrate the database

This is the one manual step that awf-skills does not yet automate.
You need `psql` and `pg_dump`/`pg_restore` available locally.

```bash
# Get old and new connection strings:
OLD_DB=$(grep DATABASE_URL .kamal/secrets | cut -d= -f2-)

# Get the new project's connection string from Neon console,
# or from infra.json after awf-neon-project runs.
NEW_DB="postgres://user:pass@ep-new-endpoint.neon.tech/neondb?sslmode=require"

# Dump from old branch, restore to new project:
pg_dump "$OLD_DB" --no-owner --no-acl | psql "$NEW_DB"
```

For zero-downtime migration (S4+ standard): use Neon's logical
replication to keep the new project in sync before cutting over.
For a lab app, a simple dump+restore with a few seconds of write
downtime is acceptable.

### Step 4 — Update the DATABASE_URL secret

```bash
uv run ~/.claude/skills/awf-app-secret-set/scripts/app_secret_set.py \
    --key DATABASE_URL \
    --value "$NEW_DB"
```

This overwrites `.kamal/secrets`. The running container still uses
the old value — it won't see the change until redeployed.

### Step 5 — Re-render deploy.yml

```bash
uv run ~/.claude/skills/awf-kamal-config/scripts/kamal_config.py
```

The new `infra.json` has the dedicated server IP. The renderer picks
it up and writes a new `config/deploy.yml` pointing at the new server.

### Step 6 — Update DNS

```bash
uv run ~/.claude/skills/awf-cf-dns-record/scripts/cf_dns_record.py \
    --domain <app.yourdomain.com> \
    --ip <new-server-ip> \
    --type A
```

This updates the Cloudflare A record. With grey cloud, changes take
effect in < 60 seconds. During propagation, some requests still hit
the old server.

Consider switching to orange cloud at this point (CF as CDN):
- Set CF record to proxied (orange)
- Change `proxy.ssl: false` in deploy.yml (CF terminates TLS)
- Add `proxy.forward_headers: true` so the app sees real client IPs

### Step 7 — Run kamal setup on the new server

```bash
uv run ~/.claude/skills/awf-kamal-setup/scripts/kamal_setup.py \
    --server-ip <new-server-ip>
```

This polls DNS first (ensuring the A record resolved), then runs
`kamal setup` on the new server (installs Docker, starts kamal-proxy).
The `kamal_setup_done_for_server_id` in `shared.json` is NOT updated
here — the dedicated server is tracked in `infra.json`, not `shared.json`.

### Step 8 — Deploy to the new server

```bash
uv run ~/.claude/skills/awf-kamal-deploy/scripts/kamal_deploy.py
```

Builds the Docker image, pushes to GHCR, pulls on the new server,
starts the container. kamal-proxy on the new server handles TLS.

### Step 9 — Verify

```bash
# Healthcheck:
curl https://<app.yourdomain.com>/up
# Expected: 200 OK

# Database connectivity (from within the container):
ssh root@<new-server-ip> "docker exec <slug>-web-1 node -e \
    \"const pg = require('pg'); const c = new pg.Client(); c.connect().then(() => console.log('OK'))\""

# Check logs:
uv run ~/.claude/skills/awf-log/scripts/log.py tail -n 20
```

### Step 10 — Update project stage

```bash
# Manually update .awf/project.json:
# "stage": "prescale"
# "has": { "infra": true, "kamal": true, ... }
```

Or run `awf-stage-prescale` when it exists (Phase D).

### Step 11 — Clean up S3 resources

Remove the old container from the play server (optional — it's using
RAM):

```bash
ssh root@<play-server-ip> "kamal app stop --service <slug>"
```

Or from the project's old working state:
```bash
kamal app stop
```

Delete the old Neon branch (optional — keep for 30 days as a backup):
```bash
# Via Neon console, or:
curl -X DELETE \
    -H "Authorization: Bearer $NEON_API_KEY" \
    "https://console.neon.tech/api/v2/projects/$OLD_PROJECT_ID/branches/$OLD_BRANCH_ID"
```

If this was the last app on the play server, you can destroy it:
- Delete from Hetzner console
- Remove `play_server` entry from `~/.config/awf/shared.json`

---

## What awf-skills currently automates vs. what's manual

### Fully automated (S3 → live)

| Step | Skill |
|------|-------|
| Create shared Hetzner server | `awf-shared-infra-get` |
| Create shared Neon project | `awf-shared-infra-get` |
| Scaffold Dockerfile + healthcheck | `awf-app-dockerize` |
| Create Neon branch per app | `awf-neon-branch` |
| Write DATABASE_URL to secrets | `awf-app-secret-set` |
| Render deploy.yml | `awf-kamal-config` |
| Create DNS A record | `awf-cf-dns-record` |
| kamal setup (idempotent) | `awf-kamal-setup` |
| kamal deploy | `awf-kamal-deploy` |
| All of the above sequenced | `awf-stage-mvp-play` |

### Manual today (not yet automated)

| Step | Reason | Phase |
|------|--------|-------|
| Database migration (S3 branch → S4 project) | Requires `pg_dump`/`pg_restore` and downtime coordination | Phase D |
| Adding colleague SSH keys to server | Multi-user key management not implemented | Phase D |
| Fleet view ("what's running on my server") | `awf-fleet` not built | Phase D |
| Resource limits per container | Kamal config extension needed | Phase D |
| Teardown (destroy resources, clean up) | `awf-teardown` not built | Phase D |
| `awf-stage-prescale` composer | Sequences the S3→S4 migration | Phase D |
| S4 promotion flow (end to end) | Depends on `awf-stage-prescale` | Phase D |
| CF orange cloud toggle | `awf-cf-proxy-toggle` not built | Phase D |
| Server disk prune (old images) | No automated cleanup | Phase E |
| Uptime monitoring / alerting | No monitoring skill | Phase E |

---

## Cost comparison: full picture

### S3 lab (shared, any number of apps)

| Component | Cost/month |
|-----------|-----------|
| Hetzner CX22 | €4.35 |
| Neon free tier | €0 |
| Cloudflare free | €0 |
| GHCR (public repos) | €0 |
| **Total** | **€4.35/month** |
| Per app (10 apps) | **€0.44/app/month** |

### S4 dedicated (per app)

| Component | Cost/month |
|-----------|-----------|
| Hetzner CX22 (dedicated) | €4.35 |
| Neon Launch (300 hrs, no suspend) | $19.00 |
| Cloudflare free (or Pro $20) | €0–$20 |
| **Total** | **~€23–45/month per app** |

### Break-even analysis

Staying on S3 makes sense until an app earns enough to justify S4
costs, has enough users to need guaranteed uptime, or has data
isolation requirements. For a lab with no revenue, S3 is optimal
indefinitely.

---

## Design decisions and trade-offs

### Why Kamal, not Kubernetes or Fly.io?

- Kubernetes: massive operational overhead for 1 server with 5 apps.
  Overkill until you have a team and > 20 services.
- Fly.io/Railway/Render: managed platforms cost 5–10× more at small
  scale than Hetzner + Kamal. Fine for teams who value managed
  infrastructure over cost control.
- Kamal: ~200 lines of Ruby that SSH into a server. Understandable,
  debuggable, no proprietary API, trivially self-hosted.

### Why one deploy.yml per app, not one deploy.yml for all?

Each app is an independent project with an independent lifecycle.
Sharing a deploy.yml would couple their deploys. Kamal is designed
for per-app configs; the multi-service capability comes from running
multiple projects against the same server IP.

### Why not Docker Compose?

Compose lacks kamal-proxy (zero-downtime deploys, TLS, health-gating).
You'd have to add Traefik and write restart logic yourself. Kamal gives
you a better deploy primitive with less configuration.

### Why Neon branches over separate Postgres instances?

A separate Postgres Docker container per app would eat 200–400 MB RAM
each and require backup management. Neon's auto-suspend means an idle
database costs literally nothing and you never manage backups.

### Why not just one shared Postgres with schemas?

One database, multiple schemas is fine for a single product with
multiple tenants (row-level isolation). For completely independent apps
(different schemas, different migrations, different owners), branch
isolation is cleaner — each app is unaffected by the others' migrations
and data.

---

## Quick reference: key commands

```bash
# Deploy a new app to the shared lab:
cd /path/to/my-app
uv run ~/.claude/skills/awf-stage-mvp-play/scripts/stage_mvp_play.py

# Check what's running:
uv run ~/.claude/skills/awf-status/scripts/status.py

# View recent events:
uv run ~/.claude/skills/awf-log/scripts/log.py tail -n 20

# Pre-flight check for S3:
uv run ~/.claude/skills/awf-doctor/scripts/check.py --for-stage mvp-play

# Check server resource usage:
ssh root@<server-ip> "docker stats --no-stream"

# Prune old Docker images on server:
ssh root@<server-ip> "docker system prune -f"

# Stop a specific app (frees RAM):
ssh root@<server-ip> "docker stop <slug>-web-1"

# Check kamal-proxy routing table:
ssh root@<server-ip> "docker exec kamal-proxy kamal-proxy list"
```
