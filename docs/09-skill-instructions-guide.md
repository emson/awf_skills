# AWF Skills Testing Guide

Complete step-by-step instructions for testing each phase of the awf-skills system. Includes exactly which API keys you need, where to get them, and how to set them up.

---

## Before anything: one-time setup

### 1. Install the skills

```bash
cd ~/.claude/awf-skills   # or wherever you cloned the repo
./install.sh              # symlinks skills into ~/.claude/skills/
```

### 2. Create your credentials file

```bash
mkdir -p ~/.config/awf
cp .env.example ~/.config/awf/.env 2>/dev/null || touch ~/.config/awf/.env
```

Then open `~/.config/awf/.env` in your editor and fill in values as described below for each phase.

### 3. Verify your runtime

```bash
uv run --script ~/.claude/skills/awf-doctor/scripts/check.py
```

Everything should show `[OK]` (or `[WARN]` for optional items). Fix anything marked `[FAIL]` before moving on.

---

## Phase A — Foundation (no API keys needed)

Phase A is the core library layer. You're testing that the state schemas, project locator, logging, and migration tooling work correctly.

### What it tests
- `.awf/project.json` and `.awf/infra.json` schemas (Pydantic v2)
- Walking up a directory tree to find a project anchor
- Writing structured events to `.awf/log.jsonl`
- Migrating legacy `passport.json` projects to the new anchor format

### API keys needed
**None.** Phase A is entirely local — no network calls.

### How to test

Run the Phase A test suite:

```bash
cd /path/to/awf_skills
uv run --with pytest --with pydantic pytest tests/lib/test_state.py tests/lib/test_project.py tests/lib/test_log.py tests/skills/test_awf_migrate.py -v
```

Expected: ~75 tests, all passing.

**Test the migrate skill manually:**

```bash
# Create a fake legacy project
mkdir /tmp/test-site
echo '{"domain":"example.com","slug":"example-com","stage":"landing"}' > /tmp/test-site/passport.json

# Run migration
cd /tmp/test-site
uv run --script ~/.claude/skills/awf-migrate/scripts/migrate.py

# Verify the anchor was created
cat .awf/project.json
# Output should show: {"awf_version": "0.1.0", "domain": "example.com", ...}
```

---

## Phase S1 — Landing page (existing pipeline)

Tests the full static site launch: Cloudflare zone, DNS, nameserver swap at Namecheap, Fathom analytics, and Google Search Console registration.

### API keys needed

#### Cloudflare

1. Go to https://dash.cloudflare.com/
2. Click your profile icon (bottom left) → **My Profile**
3. Go to **API Tokens** tab
4. Click **Create Token → Custom Token**
5. Grant these scopes:
   - Zone:Edit
   - DNS:Edit
   - Page Rules:Edit
   - Cloudflare Pages:Edit
6. Copy the token
7. On the main dashboard, note your **Account ID** (right sidebar under "Accounts")

Add to `~/.config/awf/.env`:
```bash
CLOUDFLARE_EMAIL=you@example.com
CLOUDFLARE_API_KEY=token-you-just-created
CLOUDFLARE_ACCOUNT_ID=account-id-from-dashboard
```

#### Namecheap

1. Go to https://www.namecheap.com/
2. Click your username → **Account**
3. Left sidebar → **API Interface**
4. Click **Enable API Access**
5. Add your public IP to the whitelist (your IP is shown on this page)
6. Copy your API key

Add to `~/.config/awf/.env`:
```bash
NAMECHEAP_API_USER=your-namecheap-username
NAMECHEAP_API_KEY=api-key-from-above
NAMECHEAP_USERNAME=your-namecheap-username
NAMECHEAP_CLIENT_IP=your.public.ip.address
```

**How to find your public IP:**
```bash
curl ifconfig.me
```

#### Fathom Analytics

1. Go to https://app.usefathom.com/
2. Log in
3. Top right → **Settings**
4. Left sidebar → **API Tokens**
5. Click **Create New Token**
6. Copy the token

Add to `~/.config/awf/.env`:
```bash
FATHOM_API_KEY=token-you-just-created
```

#### Google Search Console (OAuth)

1. Go to https://console.cloud.google.com/
2. Create a new project (or select an existing one)
3. Search for "Google Search Console API" and enable it
4. Go to **Credentials** (left sidebar)
5. Click **Create Credentials → OAuth Client ID**
6. Choose **Desktop Application**
7. Download the JSON file

Add to `~/.config/awf/.env`:
```bash
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/your/downloaded/oauth-creds.json
```

### How to test

**From an empty directory, create a fresh project:**

```bash
mkdir /tmp/my-landing-site && cd /tmp/my-landing-site

# Create the project (scaffolds from template)
/awf-create-project
```

**Or run the full S1 pipeline in one orchestrator call:**

```bash
/awf-launch
```

**What happens:**
1. Creates a Cloudflare zone for your domain
2. Creates a Cloudflare Pages project
3. Sets up DNS records
4. Pauses and tells you to swap nameservers at Namecheap
5. Waits for DNS propagation
6. Creates a Fathom analytics site
7. Registers your domain in Google Search Console
8. Submits your sitemap

The orchestrator will pause at manual gates and tell you exactly what to do at each step.

---

## Phase S3 — MVP-play on Hetzner + Neon

Tests a full production-ready deployment: Docker container running on a Hetzner VM, Neon serverless Postgres, Kamal deployment orchestration, and Cloudflare DNS routing.

### API keys needed (in addition to all S1 keys above)

#### Hetzner Cloud

1. Go to https://console.hetzner.cloud/
2. Select a project from the sidebar
3. Go to **Security → API Tokens**
4. Click **Generate API Token**
5. Name it (e.g., "awf-deploy")
6. Select **Read & Write** permissions
7. Copy the token

Add to `~/.config/awf/.env`:
```bash
HETZNER_API_TOKEN=token-starting-with-hcloud
```

#### Neon (serverless Postgres)

1. Go to https://console.neon.tech/
2. Sign up or log in
3. Go to **Account Settings** (bottom left)
4. Click **API Keys**
5. Click **Create New API Key**
6. Copy the token (starts with `napi_`)

Add to `~/.config/awf/.env`:
```bash
NEON_API_KEY=napi_your-token-here
```

#### GitHub Container Registry (GHCR)

This is where Docker images get pushed.

1. Go to https://github.com/settings/tokens
2. Click **Generate new token (classic)**
3. Give it these scopes:
   - `write:packages`
   - `read:packages`
4. Copy the token

Add to `~/.config/awf/.env`:
```bash
GHCR_TOKEN=ghp_your-token-here
```

Also make sure you're logged in to GitHub via the CLI:
```bash
gh auth login
# Follow the prompts, choose HTTPS
```

#### SSH Key for Kamal deployment

Kamal needs to SSH into your Hetzner server to deploy.

If you don't have an SSH key:
```bash
ssh-keygen -t ed25519 -C "awf-deploy"
# Just press Enter for all prompts to use defaults
```

The system will automatically upload your public key (`~/.ssh/id_ed25519.pub`) to Hetzner.

### How to test S3

**Step 1: Start from an S1 project or create a minimal anchor**

```bash
mkdir /tmp/my-app && cd /tmp/my-app

# Create the anchor directory
mkdir .awf

# Create a minimal project anchor
cat > .awf/project.json << 'EOF'
{
  "awf_version": "0.1.0",
  "domain": "myapp.example.com",
  "slug": "myapp-example-com",
  "stage": "landing",
  "created": "2026-06-01T00:00:00Z",
  "has": {"passport": false, "infra": false, "kamal": false, "content": false}
}
EOF
```

**Step 2: Check all S3 credentials are set**

```bash
uv run --script ~/.claude/skills/awf-doctor/scripts/check.py --for-stage mvp-play
```

Everything should be `[OK]`. Fix any `[FAIL]` before moving on.

**Step 3: Run the S3 composer (all-in-one)**

```bash
/awf-stage-mvp-play
```

Or from command line:
```bash
uv run --script ~/.claude/skills/awf-stage-mvp-play/scripts/stage_mvp_play.py
```

**What happens:**
1. Creates/reuses your shared Hetzner play server
2. Creates/reuses your shared Neon project
3. Scaffolds `Dockerfile`, `/up` healthcheck route, and database connection code
4. Creates a Neon database branch for your app
5. Writes `DATABASE_URL` into `.kamal/secrets`
6. Renders the Kamal deployment config (`config/deploy.yml`)
7. Creates a Cloudflare DNS A record pointing to the Hetzner server
8. Waits for DNS propagation
9. Runs `kamal setup` (first-time setup on the server)
10. Runs `kamal deploy` (pushes your Docker image and starts the container)

**Step 4: Verify success**

```bash
# Check your current status
uv run --script ~/.claude/skills/awf-status/scripts/status.py

# Should show Stage: mvp-play, with recent events

# Test the healthcheck endpoint
curl https://myapp.example.com/up
# Should return: OK
```

### Dry-run first (recommended)

Before spending money on a real server, do a dry-run:

```bash
uv run --script ~/.claude/skills/awf-stage-mvp-play/scripts/stage_mvp_play.py --dry-run
```

This prints every step it *would* take without making any API calls.

---

## Phase C — Affordances (Status, Help, Doctor, Log)

These skills provide context awareness and operational visibility. They work immediately once Phase A is set up. No extra credentials needed.

### How to test

**From inside any awf project directory:**

```bash
# See where you are in the pipeline
uv run --script ~/.claude/skills/awf-status/scripts/status.py

# Get context-aware help
uv run --script ~/.claude/skills/awf-help/scripts/help.py

# Read the event log (last 10 events)
uv run --script ~/.claude/skills/awf-log/scripts/log.py tail -n 10

# Replay the last session in detail
uv run --script ~/.claude/skills/awf-log/scripts/log.py session last

# Check doctor scoped to S3 phase
uv run --script ~/.claude/skills/awf-doctor/scripts/check.py --for-stage mvp-play
```

**From a directory with no project:**

```bash
cd /tmp
uv run --script ~/.claude/skills/awf-help/scripts/help.py
# Shows: "You're not in an awf project..."

uv run --script ~/.claude/skills/awf-status/scripts/status.py
# Shows: "Stage: none" + suggestion to start a project
```

---

## Quick credential reference

| Credential | Phase | Where to get |
|---|---|---|
| `CLOUDFLARE_EMAIL` | S1+ | Your Cloudflare account email |
| `CLOUDFLARE_API_KEY` | S1+ | cloudflare.com → Profile → API Tokens |
| `CLOUDFLARE_ACCOUNT_ID` | S1+ | Dashboard right sidebar under "Accounts" |
| `NAMECHEAP_API_USER` | S1+ | Your Namecheap username |
| `NAMECHEAP_API_KEY` | S1+ | namecheap.com → Account → API Interface |
| `NAMECHEAP_USERNAME` | S1+ | Your Namecheap username |
| `NAMECHEAP_CLIENT_IP` | S1+ | Your public IP (run `curl ifconfig.me`) |
| `FATHOM_API_KEY` | S1+ | app.usefathom.com → Settings → API Tokens |
| `GOOGLE_APPLICATION_CREDENTIALS` | S1+ (GSC) | console.cloud.google.com → Credentials (Desktop OAuth Client) |
| `HETZNER_API_TOKEN` | S3+ | console.hetzner.cloud → Security → API Tokens |
| `NEON_API_KEY` | S3+ | console.neon.tech → Account Settings → API Keys |
| `GHCR_TOKEN` | S3+ | github.com → Settings → Developer settings → Personal access tokens |

All credentials go in `~/.config/awf/.env`. After adding each one, verify it's picked up:

```bash
uv run --script ~/.claude/skills/awf-doctor/scripts/check.py
```

---

## Recovery: if something fails

Most failures are recoverable — the system is idempotent. Fix the problem and re-run the same command. Completed steps are automatically skipped.

```bash
# See what happened in the last session
uv run --script ~/.claude/skills/awf-log/scripts/log.py replay last

# Check current state vs reality
uv run --script ~/.claude/skills/awf-status/scripts/status.py

# Doctor for a specific failing skill
uv run --script ~/.claude/skills/awf-doctor/scripts/check.py --for-skill awf-kamal-deploy

# Check logs for recent errors
uv run --script ~/.claude/skills/awf-log/scripts/log.py find error
```

Common issues:

- **Credential not found**: Run `uv run --script ~/.claude/skills/awf-doctor/scripts/check.py` to see which layer it's being read from. Check `~/.config/awf/.env` and also `./.env` if you have a project-local one.
- **DNS not propagating**: The system waits up to 10 minutes. If it times out, the Kamal setup is skipped but logged. You can manually re-run `uv run --script ~/.claude/skills/awf-kamal-setup/scripts/kamal_setup.py` after DNS settles.
- **Docker build fails**: Check the build logs in `.awf/log.jsonl` and fix your app code. Re-run the same deployment command.

---

## Testing progression

**Recommended order:**

1. **Phase A** — Test locally to make sure state/log/project locator work
2. **Phase C** — Test affordances with a dummy Phase A project
3. **Phase S1** — Test landing page in dry-run, then do one full launch
4. **Phase S3** — Test with `--dry-run` first, then do one full deploy to the play server

This builds confidence incrementally and lets you catch credential/environment issues early.

