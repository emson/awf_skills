# Plan: First S3 deployment — `astralphenology.com` (SvelteKit 5 + FastAPI, no DNS change)

**Target:** stand up the canonical two-container architecture (SvelteKit 5 frontend + FastAPI backend) on the shared Hetzner play server. Accessible via a sslip.io hostname. Existing CF Pages site at `https://astralphenology.com` stays live and untouched. This is the first end-to-end S3 promotion AND the first validation of the long-term architecture.

**Date:** 2026-06-02
**Project path:** `/Users/emson/Dropbox/devel/projects/astralphenology-com`
**Current stage:** `landing` (S1 deployed via CF Pages on 2026-05-18)
**Target stage:** `mvp-play`

---

## 1. Goal & constraints

**Goal:** A working two-container deployment proving the SvelteKit + FastAPI + Neon + private Docker network architecture, with the FastAPI container fully private and the frontend publicly reachable. Minimum code, maximum architectural fidelity.

**Hard constraints:**
- No DNS changes — `astralphenology.com` keeps pointing at CF Pages
- Existing static build (`build.mjs` → `dist/` → CF Pages) untouched
- Two-container architecture from day one (SvelteKit + FastAPI)
- FastAPI container has zero public exposure (only reachable from frontend via Docker bridge network)
- Migration-friendly DB design so we can move to a dedicated Neon project at S4 with one line change
- Idempotent — every step safe to re-run
- Fully reversible — teardown leaves no trace except the shared server itself

**Soft constraints:**
- Minimum code per container (this is the architectural skeleton; features come later)
- SvelteKit homepage shows "Astral Phenology" and a DB-connectivity readout fetched from the FastAPI backend (proves both containers + the internal proxy)
- Use the established skill set; document gaps that need manual workaround

**Why straight to the two-container shape, not a phased single-then-two:**
- The two-container deploy is the irreducible architecture we'll use forever. Validating it once now beats refactoring through a single-container intermediate that gets thrown away.
- Failure modes specific to the two-container shape (Docker network, internal hostnames, two-app deploy ordering, backend privacy) only surface when we actually try them.
- Total minimum code: ~150 LOC across both containers — small enough that shipping both isn't materially harder than shipping one.

---

## 2. Project facts (verified)

`astralphenology-com` is:
- Static HTML built by a custom `build.mjs` script
- Not SvelteKit, not Node-server-based, has no Python anywhere
- Currently has `passport.json` (legacy schema), no `.awf/` directory
- Has a Cloudflare zone (`zone_id: 33dda86e1590882200a3661553bb3425`), Fathom analytics, GSC registered
- Live URL: `https://astralphenology.com` (CF Pages)

The existing site stays untouched. We add `frontend/` and `backend/` subdirectories on a new `s3-test` branch. Nothing about the current S1 deployment changes.

---

## 3. The hard design question — no DNS, how does anyone reach the app?

kamal-proxy routes by `Host:` header. On a multi-tenant server every app must have a distinct hostname or there is no routing key. We cannot:

- Use `astralphenology.com` (would require DNS change to point at Hetzner)
- Use `<server-ip>` directly (collides as soon as a second tenant exists)
- Use a Hetzner-provided hostname (Hetzner doesn't provide one)
- Use raw port-based routing (defeats kamal-proxy multi-tenancy)

### Alternatives considered

| Option | How it works | Verdict |
|---|---|---|
| `.local` hostname | One developer's `/etc/hosts` | Rejected — not internet-reachable |
| Buy throwaway domain | Spend €1, point at server | Rejected — costs money and lifetime management |
| Subdomain of unrelated project's domain | Real DNS, real cert | Rejected — pollutes another project's zone |
| **`sslip.io` wildcard DNS** | `app.<ip>.sslip.io` resolves to `<ip>` for free, forever | **Chosen** |
| Tailscale / WireGuard | Private network only | Rejected — not publicly accessible |

### Why sslip.io is the right answer here

sslip.io is a public wildcard-DNS service: `anything.135.181.X.Y.sslip.io` resolves to `135.181.X.Y` over public DNS. Free, zero configuration, the hostname embeds the IP so it's self-documenting. Multi-tenant works naturally because each tenant gets its own subdomain prefix.

### The TLS trade-off (deliberately accepted)

Let's Encrypt cannot issue certs for `*.sslip.io` (it's on the Public Suffix List with rate limits shared across the world). Options:
- HTTP only (`proxy.ssl: false`)
- Self-signed cert (browser warnings; no real security anyway)
- Cloudflare proxied — requires the DNS change we ruled out

**Decision: HTTP-only for this test.** The endpoint shows a DB version readout — nothing sensitive on the wire. When we promote to real DNS, kamal-proxy auto-acquires a Let's Encrypt cert.

### Consequence: a known gap in the skill suite

`KamalConfig.render()` hardcodes `proxy.ssl: True` and uses the project anchor's `domain` as `proxy.host`. For this test we hand-patch the rendered `config/deploy.yml`. This is non-idempotent — re-running `awf-kamal-config` re-renders and overwrites our edits. TODO #1 captures the permanent fix.

---

## 4. The Neon DB design (migration-friendly)

S3 uses a **shared Neon project** with one **branch per tenant**. The FastAPI container is the only thing that talks to the database.

### What makes migration easy

1. **All DB access through a single env var** (`DATABASE_URL`). Migration to a dedicated Neon project = change one line in `.kamal/secrets`. Backend code reads only `os.getenv("DATABASE_URL")`.
2. **No Neon-specific extensions.** Standard PostgreSQL only (`pg_trgm`, `uuid-ossp` are fine; anything starting with `neon_` is forbidden).
3. **`sslmode=require` always in the URL.** Neon enforces it; explicit avoids driver-default surprises.
4. **Connection pool sized per container** (`pool_size=5`, `max_overflow=10`). At S5 (multi-server) we switch to the Neon `-pooler` endpoint via a one-line URL change.

### Why FastAPI owns the DB (not SvelteKit)

- Single source of schema truth in one language.
- Frontend container has zero DB credentials → blast radius of a frontend compromise stops at the API surface.
- Schema migrations (when they exist) run in one place at backend container startup.
- Future Stripe webhook handlers, background workers, batch jobs all live in Python alongside the schema.

### Phase A — no schema yet

For this deploy: the only DB call is `SELECT version()` to prove connectivity. No tables, no Alembic, no migrations directory. Add Alembic the moment the first table appears (D-002 of this project later).

---

## 5. Final architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         public internet                              │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  │  HTTP (port 80)
                                  │  Host: astralphenology-com.<ip>.sslip.io
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│              Hetzner CX22  (€4.35/mo, shared, EU)                    │
│                                                                      │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │              kamal-proxy (port 80)                           │  │
│   │  Routes by Host header → frontend containers only            │  │
│   └──────────────────────────────────────────────────────────────┘  │
│                          │                                           │
│                          ▼                                           │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │   Docker network: astralphenology-net  (bridge, private)     │  │
│   │                                                              │  │
│   │   ┌────────────────────────┐    ┌─────────────────────────┐ │  │
│   │   │ Frontend container     │───▶│ Backend container       │ │  │
│   │   │ SvelteKit 5 +          │    │ FastAPI + asyncpg       │ │  │
│   │   │ adapter-node           │    │ (NO public exposure;    │ │  │
│   │   │ :3000  (public)        │    │  proxy.enabled: false)  │ │  │
│   │   │ alias:                 │    │ :8000                   │ │  │
│   │   │   astralphenology-com  │    │ alias:                  │ │  │
│   │   │                        │    │   astralphenology-api   │ │  │
│   │   └────────────────────────┘    └────────────┬────────────┘ │  │
│   └──────────────────────────────────────────────┼──────────────┘  │
└──────────────────────────────────────────────────┼─────────────────┘
                                                   │ TLS, sslmode=require
                                                   ▼
                ┌────────────────────────────────────────────────────┐
                │ Neon shared project: awf-play-shared                │
                │ Branch: astralphenology-com                         │
                │ Free tier; auto-suspends after 5 min idle           │
                └────────────────────────────────────────────────────┘
```

### Files added to the repo (on `s3-test` branch)

```
astralphenology-com/                  (existing repo)
├── .awf/                             (NEW — created by awf-migrate)
│   ├── project.json
│   └── infra.json
├── .kamal/                           (NEW — secrets gitignored)
│   └── secrets
├── config/                           (Kamal configs)
│   ├── deploy.yml                    (frontend, public)
│   └── deploy.api.yml                (backend, private)
├── frontend/                         (NEW — SvelteKit 5)
│   ├── package.json
│   ├── svelte.config.js
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── src/
│   │   ├── app.html
│   │   ├── hooks.server.ts           (proxies /api/* internally)
│   │   └── routes/
│   │       ├── +page.server.ts       (fetches /api/db-version)
│   │       ├── +page.svelte          (renders the placeholder)
│   │       └── up/+server.ts         (kamal healthcheck)
│   └── Dockerfile
├── backend/                          (NEW — FastAPI)
│   ├── pyproject.toml
│   ├── app/
│   │   ├── __init__.py
│   │   └── main.py                   (one endpoint: GET /db-version)
│   └── Dockerfile
├── scripts/
│   └── deploy.sh                     (sequences backend → frontend)
├── .gitignore                        (updated)
└── (everything else unchanged: dist/, build.mjs, static/, ...)
```

**Public test URL:** `http://astralphenology-com.<play-ip>.sslip.io/`

---

## 6. Scenario simulation

Walked the runbook against three scenarios.

### Scenario 1 — Cold start, no shared infra exists yet

1. `awf-doctor --for-stage mvp-play` passes (Hetzner / Neon / GHCR creds set)
2. `awf-migrate` creates `.awf/project.json` from legacy `passport.json`
3. `awf-shared-infra-get` provisions CX22 server + Neon project (~3 min)
4. `awf-neon-branch` creates branch `astralphenology-com` (~10 s)
5. **One-time SSH:** create `astralphenology-net` Docker network on the play server
6. Scaffold `frontend/` and `backend/` (manual; awf-app-dockerize doesn't fit either)
7. `awf-app-secret-set --key DATABASE_URL` (backend secrets)
8. Render frontend `config/deploy.yml`; render backend `config/deploy.api.yml`; hand-patch both
9. `awf-kamal-setup` (first time on this server; sets up kamal-proxy)
10. `kamal deploy -c config/deploy.api.yml` — backend first
11. `kamal deploy -c config/deploy.yml` — frontend second
12. `curl http://astralphenology-com.<ip>.sslip.io/up` → `OK`
13. `curl http://astralphenology-com.<ip>.sslip.io/` → HTML with DB version from FastAPI

**Concern surfaced — deploy order:** if frontend deploys before backend, SSR `load()` fails because `astralphenology-api` doesn't resolve. Mitigation: hard-code deploy order in `scripts/deploy.sh` (backend first). Also: frontend `+page.server.ts` catches the error and renders "API unavailable" rather than 500.

**Concern surfaced — Docker network:** if the network doesn't exist when kamal tries to deploy the backend, the `docker run --network=astralphenology-net` fails. Mitigation: manual `docker network create` step before first deploy, idempotent (`|| true`). TODO #5 wraps this into a skill.

### Scenario 2 — Re-run after partial failure

Each skill / step is idempotent:
- `awf-shared-infra-get` reuses cached `~/.config/awf/shared.json`
- `awf-neon-branch` search-or-create by name
- `docker network create astralphenology-net` returns "already exists" — exit 1 (catch with `|| true`)
- `awf-app-secret-set` upserts by key
- `awf-kamal-config` re-renders (hand-patch must be re-applied each time)
- `awf-kamal-setup` skipped if `play_server.kamal_setup_done_for_server_id == hetzner_id` (D-010)
- `kamal deploy` rolling-deploys with new image tag

**Concern:** the hand-patches to both deploy.yml files are non-idempotent under awf-kamal-config re-runs. **Mitigation:** the runbook tells you which steps to skip on re-run. Long-term: TODO #1.

### Scenario 3 — Future DNS flip to production

When the real site is ready:
1. Add CF DNS A record at `@` pointing to `<play-ip>` (proxied or not)
2. Patch `config/deploy.yml`: `proxy.host: astralphenology.com`, `proxy.ssl: true`
3. `kamal deploy -c config/deploy.yml` → kamal-proxy auto-acquires Let's Encrypt cert
4. Verify `https://astralphenology.com` serves the new app
5. Backend config and Docker network unchanged
6. Optional: delete CF Pages project

Backend never sees the public DNS — it stays internal regardless. That's the elegance.

---

## 7. Runbook — exact commands and code

Read everything before starting. Mark off each step.

### Step 0 — Pre-flight

```bash
cd /Users/emson/Dropbox/devel/projects/astralphenology-com
git status                                                  # must be clean
git checkout -b s3-test
git push -u origin s3-test                                  # optional, recovery aid

uv run ~/.claude/skills/awf-doctor/scripts/check.py --for-stage mvp-play
# Required green: HETZNER_API_TOKEN, NEON_API_KEY, GHCR_TOKEN, SSH key, gh auth
```

### Step 1 — Project anchor

```bash
uv run ~/.claude/skills/awf-migrate/scripts/migrate.py
jq '.' .awf/project.json
# expect: {"awf_version":"0.1.0","domain":"astralphenology.com","slug":"astralphenology-com","stage":"landing",...}
```

### Step 2 — Shared infrastructure

```bash
uv run ~/.claude/skills/awf-shared-infra-get/scripts/shared_infra_get.py
PLAY_IP=$(jq -r '.play_server.ip' ~/.config/awf/shared.json)
echo "$PLAY_IP"
```

### Step 3 — Neon branch

```bash
uv run ~/.claude/skills/awf-neon-branch/scripts/neon_branch.py
jq '.neon' .awf/infra.json     # branch_id, connection_string
```

### Step 4 — Create the Docker bridge network on the play server

```bash
ssh "root@${PLAY_IP}" 'docker network create astralphenology-net 2>/dev/null || true'
ssh "root@${PLAY_IP}" 'docker network inspect astralphenology-net --format "{{.Name}} ok"'
# expect: astralphenology-net ok
```

Idempotent: subsequent runs see the network exists, the `|| true` swallows the error.

### Step 5 — Scaffold the FastAPI backend

```bash
mkdir -p backend/app
touch backend/app/__init__.py
```

**`backend/pyproject.toml`**

```toml
[project]
name = "astralphenology-backend"
version = "0.0.1"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "asyncpg>=0.29",
]

[tool.uv]
package = false
```

**`backend/app/main.py`**

```python
import os
import asyncpg
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException

DATABASE_URL = os.environ["DATABASE_URL"]

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    yield
    await app.state.pool.close()

app = FastAPI(lifespan=lifespan)

@app.get("/up")
async def up():
    return {"ok": True}

@app.get("/db-version")
async def db_version():
    try:
        async with app.state.pool.acquire() as conn:
            v = await conn.fetchval("SELECT version()")
        return {"ok": True, "version": v}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db error: {e}")
```

**`backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Container size: ~140 MB. Memory at idle: ~50 MB.

### Step 6 — Scaffold the SvelteKit 5 frontend

```bash
mkdir -p frontend/src/routes/up
```

**`frontend/package.json`**

```json
{
  "name": "astralphenology-frontend",
  "version": "0.0.1",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite dev",
    "build": "vite build",
    "preview": "vite preview"
  },
  "devDependencies": {
    "@sveltejs/adapter-node": "^5.2.0",
    "@sveltejs/kit": "^2.7.0",
    "@sveltejs/vite-plugin-svelte": "^4.0.0",
    "svelte": "^5.0.0",
    "typescript": "^5.6.0",
    "vite": "^5.4.0"
  },
  "engines": { "node": ">=22" }
}
```

**`frontend/svelte.config.js`**

```js
import adapter from "@sveltejs/adapter-node";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

export default {
  preprocess: vitePreprocess(),
  kit: { adapter: adapter() },
};
```

**`frontend/vite.config.ts`**

```ts
import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";
export default defineConfig({ plugins: [sveltekit()] });
```

**`frontend/tsconfig.json`**

```json
{
  "extends": "./.svelte-kit/tsconfig.json",
  "compilerOptions": {
    "allowJs": true, "checkJs": true, "esModuleInterop": true,
    "forceConsistentCasingInFileNames": true, "resolveJsonModule": true,
    "skipLibCheck": true, "sourceMap": true, "strict": true,
    "moduleResolution": "bundler"
  }
}
```

**`frontend/src/app.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Astral Phenology</title>
  %sveltekit.head%
</head>
<body data-sveltekit-preload-data="hover">
  <div style="display:contents">%sveltekit.body%</div>
</body>
</html>
```

**`frontend/src/hooks.server.ts`**

```ts
import type { Handle } from "@sveltejs/kit";

const API_INTERNAL =
  process.env.API_INTERNAL_URL ?? "http://astralphenology-api:8000";

export const handle: Handle = async ({ event, resolve }) => {
  if (event.url.pathname.startsWith("/api/")) {
    const upstream = new URL(
      event.url.pathname.replace(/^\/api/, "") + event.url.search,
      API_INTERNAL
    );
    const init: RequestInit = {
      method: event.request.method,
      headers: event.request.headers,
    };
    if (event.request.method !== "GET" && event.request.method !== "HEAD") {
      init.body = await event.request.arrayBuffer();
    }
    return fetch(upstream, init);
  }
  return resolve(event);
};
```

**`frontend/src/routes/+page.server.ts`**

```ts
import type { PageServerLoad } from "./$types";

const API_INTERNAL =
  process.env.API_INTERNAL_URL ?? "http://astralphenology-api:8000";

export const load: PageServerLoad = async () => {
  try {
    const r = await fetch(`${API_INTERNAL}/db-version`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!r.ok) return { ok: false, error: `api ${r.status}` };
    const data = (await r.json()) as { version: string };
    return { ok: true, version: data.version };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
};
```

**`frontend/src/routes/+page.svelte`**

```svelte
<script lang="ts">
  let { data } = $props();
</script>

<main>
  <h1>Astral Phenology</h1>
  <p>S3 test deployment — placeholder page.</p>
  <div class="status">
    {#if data.ok}
      <span class="ok">DB connected — {data.version.split(" ").slice(0,2).join(" ")}</span>
    {:else}
      <span class="err">API/DB error: {data.error}</span>
    {/if}
  </div>
  <p style="margin-top:2rem">
    <a href="https://console.neon.tech/" target="_blank" rel="noopener">Neon console →</a>
  </p>
</main>

<style>
  main { font-family: ui-sans-serif, system-ui, sans-serif; max-width: 40rem; margin: 6rem auto; padding: 0 1rem; color: #1a1a1a; }
  h1 { font-weight: 300; letter-spacing: 0.02em; }
  .status { margin-top: 2rem; padding: 1rem; background: #f5f5f5; border-radius: 6px; font-family: ui-monospace, monospace; font-size: 0.875rem; }
  .ok { color: #0a7d23; }
  .err { color: #b91c1c; }
  a { color: #1a1a1a; }
</style>
```

**`frontend/src/routes/up/+server.ts`**

```ts
import { text } from "@sveltejs/kit";
export const GET = () => text("OK");
```

**`frontend/Dockerfile`**

```dockerfile
FROM node:22-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/build ./build
COPY --from=build /app/package*.json ./
RUN npm install --omit=dev --no-audit --no-fund
EXPOSE 3000
CMD ["node", "build"]
```

Container size: ~200 MB. Memory at idle: ~80 MB.

### Step 7 — gitignore + commit

**`.gitignore`** — append:

```
.kamal/secrets
.awf/log.jsonl
frontend/node_modules
frontend/.svelte-kit
frontend/build
backend/__pycache__
backend/.venv
**/*.pyc
```

```bash
git add frontend backend .gitignore .awf/project.json
git commit -m "s3-test: scaffold minimum SvelteKit 5 frontend + FastAPI backend"
```

### Step 8 — Inject DATABASE_URL into backend secrets

```bash
uv run ~/.claude/skills/awf-app-secret-set/scripts/secret_set.py \
    --key DATABASE_URL \
    --from-file <(jq -r '.neon.connection_string' .awf/infra.json)

grep -c '^DATABASE_URL=' .kamal/secrets
# expect: 1
```

### Step 9 — Render the two Kamal configs and hand-patch

```bash
uv run ~/.claude/skills/awf-kamal-config/scripts/kamal_config.py --path config/deploy.yml
# Default render uses domain=astralphenology.com, ssl=true. We'll patch.
```

The skill renders only one config. The backend config doesn't exist yet from the skill — write it by hand alongside, then patch the frontend render.

**Patch `config/deploy.yml` (frontend, public):**

```bash
PLAY_IP=$(jq -r '.play_server.ip' ~/.config/awf/shared.json)
SLIP_HOST="astralphenology-com.${PLAY_IP}.sslip.io"

python3 <<EOF
import yaml
from pathlib import Path
p = Path("config/deploy.yml")
d = yaml.safe_load(p.read_text())
d["service"] = "astralphenology-com"
d["proxy"]["host"] = "${SLIP_HOST}"
d["proxy"]["ssl"] = False
d["proxy"]["app_port"] = 3000
d.setdefault("builder", {})["context"] = "frontend"
d.setdefault("servers", {}).setdefault("web", {}).setdefault("options", {})
d["servers"]["web"]["options"]["network"] = "astralphenology-net"
d["servers"]["web"]["options"]["network-alias"] = "astralphenology-com"
d.setdefault("env", {}).setdefault("clear", {})["API_INTERNAL_URL"] = "http://astralphenology-api:8000"
d["env"]["clear"]["NODE_ENV"] = "production"
# frontend has no secrets in Phase A
d["env"].pop("secret", None)
p.write_text(yaml.safe_dump(d, sort_keys=False))
print("frontend deploy.yml patched")
EOF
```

**Write `config/deploy.api.yml` (backend, private) by hand:**

```bash
PLAY_IP=$(jq -r '.play_server.ip' ~/.config/awf/shared.json)
GHCR_USER=$(gh api user --jq .login 2>/dev/null || echo "REPLACE-ME")

cat > config/deploy.api.yml <<EOF
service: astralphenology-api
image: ghcr.io/${GHCR_USER}/astralphenology-api
servers:
  web:
    hosts:
      - ${PLAY_IP}
    options:
      network: astralphenology-net
      network-alias: astralphenology-api
proxy:
  enabled: false
builder:
  arch: amd64
  context: backend
registry:
  server: ghcr.io
  username: ${GHCR_USER}
  password:
    - KAMAL_REGISTRY_PASSWORD
env:
  clear:
    PYTHONUNBUFFERED: "1"
  secret:
    - DATABASE_URL
healthcheck:
  cmd: curl -fsS http://localhost:8000/up || exit 1
  interval: 10s
  timeout: 3s
  retries: 5
EOF
echo "backend deploy.api.yml written"
```

Verify both files:

```bash
grep -E "host:|ssl:|network|context:|enabled:" config/deploy.yml
grep -E "service:|network|enabled:|secret" config/deploy.api.yml
```

Commit:

```bash
git add config/deploy.yml config/deploy.api.yml
git commit -m "s3-test: kamal configs for SvelteKit (public, sslip.io) and FastAPI (private, network-only)"
```

### Step 10 — First-time kamal setup on play server

```bash
uv run ~/.claude/skills/awf-kamal-setup/scripts/kamal_setup.py
```

Sets up kamal-proxy + GHCR login. Idempotent per server (D-010).

### Step 11 — Deploy backend FIRST, then frontend

The order matters: frontend SSR will hit the backend on render. Backend must be up first.

```bash
# Backend (private)
cd config && kamal deploy -c deploy.api.yml -d backend && cd ..
# OR if kamal -d isn't desired, use destination-less:
# kamal deploy -c config/deploy.api.yml

# Sanity check: backend container running and healthy
ssh "root@${PLAY_IP}" 'docker ps --filter name=astralphenology-api --format "{{.Names}}  {{.Status}}"'
# expect: astralphenology-api-web-XXXX  Up X seconds (healthy)

# Frontend (public via kamal-proxy)
cd config && kamal deploy -c deploy.yml && cd ..
```

First-time build: ~3-5 min for the frontend (npm install + svelte-kit build dominate), ~1 min for the backend.

### Step 12 — Verify end-to-end

```bash
PLAY_IP=$(jq -r '.play_server.ip' ~/.config/awf/shared.json)
SLIP_HOST="astralphenology-com.${PLAY_IP}.sslip.io"

# Public healthcheck (SvelteKit /up route)
curl -s -o /dev/null -w "frontend /up = %{http_code}\n" "http://${SLIP_HOST}/up"
# expect: frontend /up = 200

# Public homepage — proves SvelteKit SSR → FastAPI → Neon end-to-end
curl -s "http://${SLIP_HOST}/" | grep -E "Astral Phenology|DB connected|API/DB error"
# expect: <h1>Astral Phenology</h1>
#         <span class="ok">DB connected — PostgreSQL 16.x</span>

# Backend NOT reachable from outside (security check)
curl -sS -o /dev/null -w "backend direct = %{http_code}\n" --max-time 3 "http://${PLAY_IP}:8000/up" || echo "backend direct = blocked (expected)"
# expect: blocked / refused / timeout — backend is NOT publicly exposed

# Backend reachable only from inside Docker network
ssh "root@${PLAY_IP}" 'docker exec $(docker ps -q -f name=astralphenology-com-web) wget -qO- http://astralphenology-api:8000/up'
# expect: {"ok":true}
```

If frontend `/` shows "API/DB error" instead of the version: check `.awf/log.jsonl`. Most likely causes:
- Backend container not running → `docker ps` on play server
- Docker network not joined → check `docker inspect <container> | grep NetworkMode`
- DATABASE_URL malformed → check `kamal secrets -c deploy.api.yml` (won't print value)
- Neon branch not active yet (cold start) → first request takes 5-10s; retry

### Step 13 — Stage transition

`awf-kamal-deploy` should advance `.awf/project.json:stage` to `mvp-play` on success. If we used raw `kamal deploy` directly (as above), set it manually:

```bash
python3 <<'EOF'
import json
from pathlib import Path
p = Path(".awf/project.json")
d = json.loads(p.read_text())
d["stage"] = "mvp-play"
p.write_text(json.dumps(d, indent=2) + "\n")
EOF
git add .awf/project.json
git commit -m "s3-test: stage → mvp-play after successful two-container deploy"
```

### Step 14 — Convenience deploy script for future iterations

**`scripts/deploy.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
kamal deploy -c config/deploy.api.yml    # backend first
kamal deploy -c config/deploy.yml         # then frontend
```

```bash
chmod +x scripts/deploy.sh
git add scripts/deploy.sh
git commit -m "s3-test: deploy script enforcing backend-first ordering"
```

For all future deploys: `./scripts/deploy.sh`.

---

## 8. Future migrations — what we're set up for

### Migration A — flip prod DNS to S3

When the real site is ready:

```bash
# 1. CF DNS A record at @ → play server IP
uv run ~/.claude/skills/awf-cf-dns-record/scripts/cf_dns_record.py \
    --type A --name @ --content "$PLAY_IP" --proxied

# 2. Patch frontend config/deploy.yml back to production
python3 <<EOF
import yaml; from pathlib import Path
p = Path("config/deploy.yml")
d = yaml.safe_load(p.read_text())
d["proxy"]["host"] = "astralphenology.com"
d["proxy"]["ssl"] = True
p.write_text(yaml.safe_dump(d, sort_keys=False))
EOF

# 3. Redeploy frontend only (backend unchanged)
kamal deploy -c config/deploy.yml
```

Backend config and Docker network are unchanged. Only the frontend learns its new public hostname.

### Migration B — promote to S4 (dedicated Neon project)

```bash
# 1. Create dedicated Neon project (Neon dashboard or awf-neon-project)
# 2. Restore data from current shared branch (Neon "Restore to new project" or pg_dump/restore)
# 3. Get new connection string
NEW_URL="postgresql://<new>?sslmode=require"

# 4. Swap the secret (backend only — frontend doesn't know about DB)
uv run ~/.claude/skills/awf-app-secret-set/scripts/secret_set.py \
    --key DATABASE_URL --value "$NEW_URL"

# 5. Redeploy backend only
kamal deploy -c config/deploy.api.yml
```

App code knows nothing about Neon project IDs. One-line swap.

### Migration C — first real schema

When the first table appears:

```bash
# 1. Add Alembic to backend
cd backend
uv add alembic sqlalchemy
uv run alembic init alembic
# edit alembic/env.py to read DATABASE_URL and target SQLAlchemy metadata

# 2. Write the first model in backend/app/db/models.py
# 3. Generate first migration
uv run alembic revision --autogenerate -m "initial schema"

# 4. Add to Dockerfile entrypoint: alembic upgrade head before uvicorn
# 5. Redeploy backend
```

Frontend never changes for schema work.

### Migration D — add Stripe

```bash
# 1. Add stripe to backend deps
# 2. Add /api/billing/checkout + /api/webhooks/stripe routes in FastAPI
# 3. Store STRIPE_API_KEY + STRIPE_WEBHOOK_SECRET in .kamal/secrets
# 4. Add billing pages in frontend; they call /api/billing/* (already proxied via hooks.server.ts)
```

The /api/* proxy already works. New endpoints come online with zero plumbing changes.

---

## 9. Teardown procedure

```bash
cd /Users/emson/Dropbox/devel/projects/astralphenology-com

# 1. Remove both kamal apps from the play server
cd config
kamal app remove -c deploy.yml      || true
kamal app remove -c deploy.api.yml  || true
cd ..

# 2. Remove the Docker network
ssh "root@${PLAY_IP}" 'docker network rm astralphenology-net || true'

# 3. Delete the Neon branch
NEON_PROJECT_ID=$(jq -r '.play_neon_project.id' ~/.config/awf/shared.json)
BRANCH_ID=$(jq -r '.neon.branch_id' .awf/infra.json)
curl -sX DELETE \
    -H "Authorization: Bearer ${NEON_API_KEY}" \
    "https://console.neon.tech/api/v2/projects/${NEON_PROJECT_ID}/branches/${BRANCH_ID}"

# 4. Switch back to main, delete the s3-test branch
git checkout main
git branch -D s3-test
# git push origin --delete s3-test    # if you pushed it
```

Leaves: the shared play server, the shared Neon project. Both persistent across tenants. Marginal cost: zero.

---

## 10. Cost summary

| Item | Cost | Notes |
|---|---|---|
| Hetzner CX22 (shared) | €4.35/mo | All tenants |
| Neon free tier | €0 | 191 compute-hours/month; auto-suspends idle |
| Cloudflare (existing) | €0 | Untouched |
| GHCR (2 images) | €0 | ~350 MB combined, well under free quota |
| sslip.io | €0 | |
| **Total marginal cost** | **€0** if shared infra existed; **€4.35** if first-ever shared infra |

CX22 memory budget: 4 GB. Per-tenant footprint at idle: ~130 MB (frontend ~80 + backend ~50). Capacity: ~15-20 idle tenants, ~5-8 active. Plenty for the lab.

---

## 11. Known issues / TODOs surfaced

| # | Issue | Workaround used | Permanent fix |
|---|---|---|---|
| 1 | `awf-kamal-config` has no `--proxy-host` / `--no-ssl` / `--network` flags | Hand-patch YAML | Add flags OR `deploy_targets` field in project anchor (`{name, host, ssl, network}`); `awf-kamal-config --target <name>` |
| 2 | `awf-app-dockerize` assumes single SvelteKit at repo root | Manual Dockerfiles in `frontend/` and `backend/` | Make skill multi-service-aware OR drop the scaffold assumption and rely on user Dockerfiles |
| 3 | `awf-kamal-config` can't write the backend (private, proxy.enabled:false) config | Hand-written `deploy.api.yml` | Add `--mode api` or a sibling `awf-kamal-config-api` skill that emits a no-proxy backend config |
| 4 | Docker network creation is manual SSH | `ssh ... docker network create ... || true` | Wrap as `awf-docker-network` skill; idempotent; tracked in `infra.json` |
| 5 | Two-app deploy order is manual | `scripts/deploy.sh` enforces order | Add `awf-stage-mvp-play` composer with `--variant two-tier-svelte-fastapi` that knows the order |
| 6 | No `awf-teardown` skill | Manual `kamal app remove` x2 + manual Neon delete + manual network rm | Build `awf-teardown` that handles multi-container projects |
| 7 | Stage transition is implicit / missing | Hand-edit `.awf/project.json:stage` after deploy | Wire stage advance into the composer skill (TODO #5) |
| 8 | `awf-app-secret-set --from-infra` not available | `--from-file <(jq ...)` shell trick | Add `--from-infra neon.connection_string` semantic flag |
| 9 | No drift detection for backend secrets / network membership | Manual `docker inspect` checks | Extend `awf-status` to surface kamal-app health + network alias correctness |

These don't block this test — but they're the real shape of the work needed to make this deployment one-command in the future.

---

## 12. Definition of done

- [ ] `git branch --show-current` returns `s3-test`
- [ ] `.awf/project.json` exists with `stage: "mvp-play"` after deploy
- [ ] `~/.config/awf/shared.json` lists this slug
- [ ] `docker network inspect astralphenology-net` succeeds on play server
- [ ] Backend container running and healthy: `docker ps` shows `astralphenology-api-web-...` with `(healthy)` status
- [ ] Frontend container running and registered with kamal-proxy
- [ ] `curl http://astralphenology-com.<ip>.sslip.io/up` returns `200 OK`
- [ ] `curl http://astralphenology-com.<ip>.sslip.io/` returns HTML containing "DB connected" and a PostgreSQL version string
- [ ] `curl --max-time 3 http://<ip>:8000/up` fails (backend NOT publicly exposed)
- [ ] `docker exec <frontend-container> wget -qO- http://astralphenology-api:8000/up` succeeds (internal reach works)
- [ ] `https://astralphenology.com` still returns the original CF Pages site (no regression)
- [ ] `awf-status` shows project at `mvp-play` with no drift
- [ ] `awf-log tail` shows the full sequence as structured events
- [ ] Teardown procedure validated mentally

When every box is checked: this is the suite's first proven multi-tenant S3 deployment AND the first running instance of the SvelteKit 5 + FastAPI canonical architecture. Capture lessons in `docs/decisions.md` as D-012 ("Two-container S3 baseline: SvelteKit 5 frontend + FastAPI backend on private Docker bridge").
