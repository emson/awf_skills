# 12 — LLM Workflow Guide

> **This document is written for the LLM, not for humans.** It answers
> the question "given what the user just said and what state the project
> is in, which skill should I invoke next, with what parameters, and
> what should I check first?" Read it when you are working with
> awf-skills and need to make a routing decision.

---

## Protocol: establish ground truth first

Before touching anything, run these three commands. Do not skip them.
Do not assume you know the state from context.

```bash
# 1. Where are we and what stage?
uv run ~/.claude/skills/awf-status/scripts/status.py --json

# 2. Are credentials and CLIs present?
uv run ~/.claude/skills/awf-doctor/scripts/check.py --json

# 3. What happened recently?
uv run ~/.claude/skills/awf-log/scripts/log.py tail -n 10
```

Interpret ALL three outputs before taking action. Attempting a composer
without knowing the credential state is the single most common source
of mid-pipeline failures that are hard to recover from.

**If `awf-status` fails to find a project (`"stage": "none"`):**
Check `pwd`. Walk up looking for `.awf/project.json` or `passport.json`.
If the user is in a fresh directory, they need to create a project first.
If they're in the wrong directory, navigate to the project root.

**If `awf-doctor` shows `[FAIL]` items:**
Fix them before proceeding. A skill that fails mid-way through because
of a missing credential is harder to recover from than failing before
it starts. Use `awf-doctor --for-stage <name>` to scope the check to
only what the next operation needs.

---

## The lifecycle state machine

A project exists at exactly one stage. Advancement is explicit and
human-approved. The stage is the primary routing signal.

```
NO_PROJECT (no .awf/project.json found by walking up)
     │
     │  awf-init → awf-create-project
     ▼
landing   ──────────────────────────────────────────────┐
     │  CF Pages, no DB, static SvelteKit               │
     │  deploy with: awf-install → awf-deploy           │
     │  advance with: awf-stage-mvp-play (skips demo)   │
     ▼                                                   │
demo      (currently same tooling as landing)           │ atomic
     │  CF Pages, client-side mocks, no DB              │ skills
     │  advance with: awf-stage-mvp-play                │ operate
     ▼                                                   │ across
mvp-play  ──────────────────────────────────────────────┤ all
     │  Shared Hetzner server, Neon branch, Kamal       │ stages
     │  deploy with: awf-kamal-deploy                   │
     │  advance with: awf-stage-prescale (Phase D)      │
     ▼                                                   │
prescale  ──────────────────────────────────────────────┤
     │  Dedicated server + LB, dedicated Neon           │
     │  deploy with: awf-kamal-deploy                   │
     │  advance with: awf-stage-scale (Phase D)         │
     ▼                                                   │
scale     ──────────────────────────────────────────────┘
          Multi-server + LB, Neon pooler; terminal stage
```

**Key rule:** the deploy skill changes at S3. `awf-deploy` is for
CF Pages (landing/demo only). `awf-kamal-deploy` is for all server
stages. Never use `awf-deploy` for an mvp-play+ project.

**What `has:` flags mean:**

| Flag | Meaning | Set by |
|------|---------|--------|
| `has.passport` | passport.json exists and is valid | awf-create-project |
| `has.infra` | `.awf/infra.json` exists with server/DB resources | awf-shared-infra-get, awf-hetzner-server |
| `has.kamal` | `config/deploy.yml` exists and kamal setup has run | awf-kamal-config + awf-kamal-setup |
| `has.content` | Content has been generated for the site | awf-generate-content |

---

## Intent routing: what did the user ask for?

Match the user's intent to one of these categories, then follow the
relevant section.

| User says something like… | Category |
|---------------------------|----------|
| "launch a new site", "I want to start a new project" | → [A] Fresh launch |
| "deploy my changes", "push the latest code" | → [B] Redeploy |
| "add a database", "I need server-side routes", "use a real server" | → [C] Promote to S3 |
| "go to production", "I want a proper production setup" | → [D] Promote to S4 |
| "add another server", "scale out", "more capacity" | → [E] Scale to S5 |
| "something is broken", "my site is down", "deploy failed" | → [F] Diagnose and fix |
| "what's going on?", "show me the status", "where are we?" | → [G] Inspect |
| "update the content", "change the copy" | → [H] Content update |
| "update the template", "newer version" | → [I] Template update |
| "teardown", "delete this", "clean up" | → [J] Teardown |
| "set up for the first time", "configure credentials" | → [K] First-run setup |
| "migrate old project", "passport.json exists" | → [L] Legacy migration |

---

## [A] Fresh launch (no project exists)

**State:** `stage = none` — no `.awf/project.json` found

**Sequence:**
```
1. awf-init          (creates ~/.config/awf/.env, sets AWF_HOME)
2. awf-create-project (scaffold from template, write passport.json)
3. awf-setup-domain  (CF zone + Pages project + DNS)
4. awf-setup-nameservers (point Namecheap NS at Cloudflare)
   ── manual gate: verify NS propagation ──
5. awf-setup-analytics (Fathom site creation)
6. awf-generate-content (Claude-native: SERP screenshot → copy)
7. awf-review-passport (validate passport.json)
8. awf-install        (npm install)
9. awf-deploy         (build + wrangler pages deploy)
10. awf-setup-gsc    (register Google Search Console property)
11. awf-verify-gsc   (verify + submit sitemap)
12. awf-submit-bing  (IndexNow key + push URLs)
```

Or use the composer which sequences steps 2–12 automatically:
```bash
/awf-launch
```

**Guard conditions before starting:**
- `awf-doctor` passes for stage=landing (cloudflare, namecheap, fathom, gsc, bing, git, node, wrangler)
- Domain is registered at Namecheap and NOT yet pointing at Cloudflare
- User has chosen a domain and slug

**Hard ordering rules:**
- `awf-setup-domain` BEFORE `awf-setup-nameservers` — CF zone must exist before NS delegation
- `awf-setup-nameservers` and NS propagation BEFORE `awf-deploy` — Pages project must be active
- `awf-review-passport` BEFORE `awf-deploy` — catches invalid passport before a failed build
- `awf-install` BEFORE `awf-deploy` — node_modules must exist
- `awf-setup-gsc` BEFORE `awf-verify-gsc` — property must be registered before verification

**Manual gates in this flow:**
- After `awf-setup-nameservers`: user must confirm nameserver propagation at Namecheap. The LLM cannot automate this. Tell the user to check `dig NS yourdomain.com` returns Cloudflare nameservers. This typically takes 5–30 minutes.
- `awf-generate-content`: requires a SERP screenshot from the user. The LLM can prompt for it but cannot take the screenshot automatically.

---

## [B] Redeploy (code changes, same stage)

**State:** project exists, stage known

**Choose the right deploy skill based on stage:**

| Stage | Deploy skill | What it does |
|-------|-------------|--------------|
| landing | `awf-install` → `awf-deploy` | npm install + wrangler pages deploy to CF Pages |
| demo | `awf-install` → `awf-deploy` | same |
| mvp-play | `awf-kamal-deploy` | Docker build → push to GHCR → kamal rolling deploy |
| prescale | `awf-kamal-deploy` | same |
| scale | `awf-kamal-deploy` | same; deploys to all servers in deploy.yml |

**Never use `awf-deploy` for mvp-play+ projects.** It targets Cloudflare
Pages, not the Kamal/Hetzner stack. The project will appear to deploy
but nothing changes on the server.

**Guard conditions before redeploy:**
- For landing/demo: wrangler authenticated (`wrangler whoami`)
- For mvp-play+: `GHCR_TOKEN` has `write:packages` scope; server is up; `config/deploy.yml` exists
- For all: check `awf-status` for drift before deploying — deploying onto drifted infrastructure can leave inconsistent state

**If deploy fails mid-way:**
Kamal is safe to re-run. If the container didn't start, kamal keeps
the old container running — users are not affected. Fix the issue and
re-run `awf-kamal-deploy`. Do not attempt manual Docker operations on
the server before trying the re-run.

---

## [C] Promote landing → mvp-play (add server + database)

**State:** `stage = landing` or `stage = demo`

**Use the composer:**
```bash
cd /path/to/project   # MUST be in the project directory
/awf-stage-mvp-play
```

Or directly:
```bash
uv run ~/.claude/skills/awf-stage-mvp-play/scripts/stage_mvp_play.py
```

**This composer sequences 8 atomic skills automatically:**
1. `awf-shared-infra-get` — mint/reuse shared Hetzner server + Neon project
2. `awf-app-dockerize` — scaffold Dockerfile, `/up` healthcheck, `lib/db.ts`
3. `awf-neon-branch` — create branch on shared Neon project
4. `awf-app-secret-set` — write DATABASE_URL to `.kamal/secrets`
5. `awf-kamal-config` — render `config/deploy.yml`
6. `awf-cf-dns-record` — create A record → play server IP (grey cloud)
7. `awf-kamal-setup` — wait for DNS, install Docker + kamal-proxy (idempotent)
8. `awf-kamal-deploy` — build → push → deploy

**The composer is fully idempotent.** If it fails mid-way, fix the issue
and re-run. Completed steps are automatically skipped.

**Guard conditions before running:**
```bash
uv run ~/.claude/skills/awf-doctor/scripts/check.py --for-stage mvp-play
```
Must show OK for: `hetzner`, `neon`, `ghcr`, `ssh`, `kamal_cli`, `git`.

**Exit codes from the composer:**
| Code | Meaning | What to do |
|------|---------|------------|
| 0 | Success | Verify with curl /up |
| 1 | No project found | Check pwd, navigate to project root |
| 2 | Credentials/CLI missing | Run awf-doctor, fix, re-run |
| 3 | Remote error | Check awf-log, fix, re-run |
| 4 | State validation failure | Check .awf/project.json and infra.json |
| 5 | DNS gate timeout | Wait for DNS, re-run; it's idempotent |

**Exit code 5 is not a failure.** It means DNS hasn't propagated yet.
Re-run once `dig +short <domain>` returns the play server IP. Do not
attempt any other intervention.

---

## [D] Promote mvp-play → prescale (S4 production)

**State:** `stage = mvp-play`

**Status:** `awf-stage-prescale` composer not yet built (Phase D).
Until it is, follow the manual sequence in `docs/11-s4-architecture-guide.md`.

**The LLM's role today:**
- Explain the steps from that doc
- Run the atomic skills in sequence
- Flag the manual gates (CF Origin Certificate, database migration)
- Verify each step before proceeding

**Manual gate:** database migration is unavoidable. Walk the user through
the pg_dump/psql procedure. This is the ONE step in the system that
requires a brief write downtime.

**Critical ordering:** DNS must switch to orange cloud (CF proxied) ONLY
after the LB is up and its health check is passing. Switching DNS before
the LB is ready causes an outage.

---

## [E] Scale to S5 (add second server)

**State:** `stage = prescale`, LB exists, private network exists

**This is purely additive.** Nothing changes for existing users.

```
1. awf-hetzner-server (new server, same private network)
2. awf-kamal-setup --server-ip <new-public-ip>
3. Update deploy.yml to add new server to hosts[]
4. awf-kamal-deploy (rolling deploy to both servers)
5. Add new server private IP as LB target (Hetzner console or awf-hetzner-lb-add-target)
6. If 4+ servers: switch DATABASE_URL to Neon pooler endpoint
```

**No DNS change. No LB IP change. No CF reconfiguration.**

The LB IP is stable from the day it was created. This is the entire
point of putting the LB in at S4 before it was needed.

---

## [F] Diagnose and fix (something is broken)

**Never jump straight to a fix.** Always diagnose first.

**Diagnosis protocol:**
```bash
# Step 1: What does the project think its state is?
uv run ~/.claude/skills/awf-status/scripts/status.py --json

# Step 2: Are credentials and CLIs OK?
uv run ~/.claude/skills/awf-doctor/scripts/check.py --json

# Step 3: What happened recently?
uv run ~/.claude/skills/awf-log/scripts/log.py tail -n 20

# Step 4: Are there recent errors?
uv run ~/.claude/skills/awf-log/scripts/log.py find error
```

**Then match the diagnosis to a resolution:**

| Symptom | Likely cause | Resolution |
|---------|-------------|------------|
| Site returns 502 | Container not running | `ssh root@<ip> "docker ps"` → `awf-kamal-deploy` |
| Site returns 404 | CF Pages deploy failed | Check awf-log, re-run `awf-deploy` |
| Site returns 500 | App code error or DB unreachable | Check app logs: `kamal app logs` |
| awf-doctor shows [FAIL] for credential | Credential missing/expired | Update ~/.config/awf/.env, re-run |
| awf-status shows drift: server_missing | Hetzner server was deleted | Re-provision with `awf-hetzner-server` |
| awf-status shows drift: neon_branch_missing | Neon branch deleted | Re-create with `awf-neon-branch` |
| awf-status shows drift: zone_missing | CF zone deleted | Re-run `awf-setup-domain` |
| DNS not resolving | NS not propagated or record missing | Check `dig +short <domain>`, re-run `awf-cf-dns-record` |
| kamal deploy hangs | Docker build failure | Check build output, fix Dockerfile |
| awf-kamal-setup returns exit 5 | DNS not propagated | Wait, check `dig`, re-run |
| All S3 apps returning DB errors | Neon compute hours exhausted | Upgrade Neon plan |
| Google OAuth failing | token.json expired | Delete `~/.config/awf/token.json`, re-run awf-setup-gsc |

**Never delete and recreate** a Hetzner server to fix a deploy issue.
The server holds running containers and shared state. Fix the specific
problem. Only re-provision if the server is genuinely gone (awf-status
shows drift: server_missing).

---

## [G] Inspect (what's going on)

```bash
# Current state:
/awf-status

# Machine-readable (for further processing):
/awf-status --json

# Last N events:
/awf-log tail -n 20

# Last session:
/awf-log session last

# Find errors:
/awf-log find error

# Context-aware help:
/awf-help

# Full skill catalogue:
/awf-help --overview
```

Run `awf-status` first. Its output tells you the stage, any drift, the
most recent events, and the recommended next action. If something looks
wrong, `awf-log session last` shows the complete event trace for the
last session, which usually identifies the root cause.

---

## [H] Content update

**State:** landing or demo; passport.json exists

```
1. Edit passport.json directly (or run awf-generate-content)
2. awf-review-passport  (validate before deploying)
3. awf-install          (if node_modules stale)
4. awf-deploy           (rebuild + push to CF Pages)
```

`awf-generate-content` is a Claude-native skill — it processes a SERP
screenshot and writes site copy. It requires a screenshot input from the
user. It cannot generate content without one.

---

## [I] Template update

**State:** landing or demo; existing project

```bash
/awf-update-template
```

This re-overlays the latest template version onto the project, preserving
files listed in the template's `preserve_globs` (passport.json, custom
components, etc.). Safe to run on any landing/demo project. Always review
the changes before deploying.

---

## [J] Teardown

**Status:** `awf-teardown` skill not yet built (Phase D).

For now, teardown is manual:

**S3 shared server teardown (one app):**
```bash
# Stop and remove the container:
ssh root@<play-server-ip> "docker stop <slug>-web-1 && docker rm <slug>-web-1"
# Delete the Neon branch (Neon console or API)
# Delete the CF DNS record (CF dashboard or awf-cf-dns-record with --delete)
```

**S3 server teardown (all apps, destroy server):**
- Delete server from Hetzner console
- Delete Neon project (Neon console)
- Remove `play_server` from `~/.config/awf/shared.json`

**S4 teardown:**
- Delete LB (Hetzner console)
- Delete server (Hetzner console)
- Delete private network (Hetzner console)
- Delete Neon project (Neon console)
- Update/delete CF DNS record

---

## [K] First-run setup

**State:** fresh machine, no credentials configured

```bash
cd ~/.claude/awf-skills   # or wherever AWF_HOME is
./install.sh              # symlink skills into ~/.claude/skills/

# In a Claude Code session:
/awf-init                 # creates ~/.config/awf/.env, prompts for creds
/awf-doctor               # verify everything is wired up
```

`awf-init` is idempotent. Re-running it after adding new credentials is
safe — it only prompts for missing values.

After `awf-init`, confirm `AWF_HOME` is set in the shell:
```bash
echo $AWF_HOME   # should be the path to the awf-skills repo
```

If not set, add `export AWF_HOME=/path/to/awf-skills` to `~/.zshrc`
or `~/.bashrc` and restart the shell.

---

## [L] Legacy migration (passport.json exists, no .awf/)

**State:** project has `passport.json` in the root but no `.awf/` directory

```bash
cd /path/to/legacy-project
/awf-migrate
```

This creates `.awf/project.json` from the passport.json data and
initialises the anchor. After migration, all modern skills work
correctly. `passport.json` is left untouched.

---

## Guard conditions reference

Before invoking any skill that touches a remote API, verify these guards.
Running `awf-doctor --for-skill <name>` automates this check.

### Before any S1 operation

```bash
uv run ~/.claude/skills/awf-doctor/scripts/check.py --for-stage landing
```
Required: cloudflare, namecheap, fathom, gsc, google_oauth, bing, git, node, wrangler

### Before awf-stage-mvp-play (S3)

```bash
uv run ~/.claude/skills/awf-doctor/scripts/check.py --for-stage mvp-play
```
Required: hetzner, neon, ghcr, ssh, kamal_cli, git

Also verify manually:
- `~/.ssh/id_ed25519` (or equivalent) exists — `ls -la ~/.ssh/id_ed25519.pub`
- `kamal` gem is installed — `kamal version`
- The project stage is `landing` or `demo` — check `awf-status`

### Before awf-kamal-deploy (any S3+ redeploy)

- `config/deploy.yml` exists in project root
- `.kamal/secrets` contains `DATABASE_URL` and `GHCR_TOKEN`
- `infra.json` has `hetzner.servers[0].ip` populated
- Server is accessible — `ssh root@<server-ip> "echo ok"`

### Before any DNS-changing operation

- `CLOUDFLARE_API_TOKEN` is present and valid
- CF zone exists for the domain — check with `awf-status` (drift would show if zone missing)
- Inform user that DNS changes take 30–120 seconds (CF) and up to several hours globally

---

## Skill selection guide: atomic vs. composer

### When to use the composer

- **First-time setup** at a new stage — the composer handles ordering automatically
- **Resuming a partial run** — composers are idempotent and skip completed steps
- **Standard promotion** — user wants to go from stage X to stage X+1

### When to use atomic skills directly

- **Fixing a specific drift** — e.g., DNS record is wrong → `awf-cf-dns-record`
- **Targeted operation** — e.g., add a new secret → `awf-app-secret-set`
- **The composer for that stage doesn't exist yet** — S4, S5 composers are Phase D
- **Debugging** — test one component in isolation
- **After the composer failed at a specific step** — fix the underlying issue, then re-run just that atomic skill to verify, then re-run the composer

### The composer ≠ the only path

Composers are convenient, not mandatory. An experienced operator can
run the atomic skills in sequence and achieve the same result. The
composer adds: correct ordering, idempotency across steps, structured
logging, and exit code discipline.

---

## Idempotency: what is safe to re-run

**Always safe (read-only):**
`awf-status`, `awf-doctor`, `awf-log`, `awf-help`, `awf-review-passport`

**Safe (search-or-create, skips if already done):**
`awf-setup-domain`, `awf-setup-nameservers`, `awf-setup-analytics`,
`awf-setup-gsc`, `awf-shared-infra-get`, `awf-hetzner-server`,
`awf-neon-project`, `awf-neon-branch`, `awf-cf-dns-record`,
`awf-kamal-setup`, `awf-app-secret-set`, `awf-app-dockerize`,
`awf-kamal-config`, `awf-stage-mvp-play`

**Safe but causes disruption (use during low-traffic periods):**
`awf-kamal-deploy` (brief container drain/swap), `awf-deploy`
(CF Pages rebuild), `awf-install` (npm install, slow)

**NOT safe to re-run without understanding the effect:**
None currently — all skills in the suite are designed to be re-runnable.
The skill's exit code and `action` field tell you whether it did work
(`"created"` / `"updated"`) or skipped (`"skip"`).

---

## Edge case catalog

These are the situations most likely to cause confusion. Each entry
describes how to detect the case and exactly what to do.

### E-01: LLM is in the wrong directory

**Detect:** `awf-status` returns `Stage: none` and the project should
exist. Or `awf-status` finds a different project than expected.

**Fix:** `pwd` → navigate to the correct directory. The project root
is the directory containing `.awf/project.json`. Skills walk up from
cwd to find it, so being in a subdirectory is fine.

### E-02: Skills not symlinked

**Detect:** `uv run ~/.claude/skills/awf-status/...` returns "No such
file or directory."

**Fix:**
```bash
cd $AWF_HOME && ./install.sh
```

### E-03: AWF_HOME not set

**Detect:** `echo $AWF_HOME` is empty; skills fail to locate lib/.

**Fix:**
```bash
export AWF_HOME=/path/to/awf-skills
# Add to ~/.zshrc or ~/.bashrc for persistence
```

### E-04: Partial S3 deploy (composer failed mid-way)

**Detect:** `awf-status` shows `stage = landing` but `infra.json`
exists with partial data (some resources created, others missing). Or
the composer previously exited with code 2, 3, or 4.

**Fix:** Run the composer again. It is fully idempotent.
```bash
uv run ~/.claude/skills/awf-stage-mvp-play/scripts/stage_mvp_play.py
```
Completed steps are skipped. The failed step reruns.

### E-05: DNS gate timeout (exit code 5 from awf-kamal-setup)

**Detect:** `awf-stage-mvp-play` or `awf-kamal-setup` exits with code 5.
Log shows `gate=dns_propagation`.

**Fix:** This is expected, not an error. Wait for DNS to propagate.
```bash
dig +short <domain>   # must return the play server IP
# Once it does, re-run the composer — it will skip the steps that completed
```
Typical wait: 30–120 seconds for Cloudflare grey-cloud records.

### E-06: Credential expired mid-pipeline

**Detect:** A skill exits with code 2 (credentials) after previous
skills succeeded.

**Fix:** Update the specific credential in `~/.config/awf/.env`. Then:
```bash
uv run ~/.claude/skills/awf-doctor/scripts/check.py --for-stage mvp-play
```
Verify the specific subsystem is now OK, then re-run the composer.

### E-07: Hetzner server exists in infra.json but is deleted from Hetzner

**Detect:** `awf-status` shows drift: `server_missing`. Or `kamal deploy`
fails with SSH connection refused.

**Fix:** The server is gone. Re-provision:
```bash
uv run ~/.claude/skills/awf-hetzner-server/scripts/hetzner_server.py ...
uv run ~/.claude/skills/awf-kamal-setup/scripts/kamal_setup.py --server-ip <new-ip>
uv run ~/.claude/skills/awf-kamal-deploy/scripts/kamal_deploy.py
uv run ~/.claude/skills/awf-cf-dns-record/scripts/cf_dns_record.py --ip <new-ip>
```
Note: the new server gets a new IP — update DNS accordingly.

### E-08: kamal_setup_done but server was reprovisioned

**Detect:** `awf-kamal-setup` reports `action: skip` but the server
is a fresh machine (just created).

**How it works:** The skip logic compares `kamal_setup_done_for_server_id`
against `play_server.hetzner_id` in `shared.json`. If the server was
reprovisioned, `hetzner_id` changed and the comparison fails —
setup WILL run. This is handled automatically.

**If it incorrectly skips:** The old server ID is still in `shared.json`
because `awf-shared-infra-get` was not re-run. Re-run it first to update
`hetzner_id`, then `awf-kamal-setup` will detect the mismatch and run.

### E-09: Neon compute hours exhausted

**Detect:** ALL S3 apps return database connection errors simultaneously.
`awf-log find error` shows DB connection failures across multiple projects.

**Fix:** Upgrade the Neon project to Launch plan ($19/month). This is
a billing change in the Neon console. No code changes needed. Compute
hours reset at the next billing cycle.

**This affects all apps sharing the shared Neon project.** It is a
cross-project failure — check `~/.config/awf/shared.json` to see which
apps share the project.

### E-10: GHCR push fails (authentication)

**Detect:** `awf-kamal-deploy` fails during image push. Log shows
`unauthorized` or `denied: requested access to the resource is denied`.

**Fix:** The `GHCR_TOKEN` in `.kamal/secrets` needs `write:packages`
scope. Regenerate the token at github.com → Settings → Developer settings
→ Personal access tokens → new token with `write:packages` scope. Update
`.kamal/secrets`:
```bash
uv run ~/.claude/skills/awf-app-secret-set/scripts/app_secret_set.py \
    --key GHCR_TOKEN --value ghp_new_token
```

### E-11: Container starts but /up health check fails

**Detect:** `awf-kamal-deploy` succeeds (exits 0) but `curl https://<domain>/up`
returns 500 or hangs.

**Fix:** This is an app code issue, not infrastructure. Check:
```bash
ssh root@<server-ip> "docker logs <slug>-web-1"
```
Common causes: missing env var, DB migration not run, port mismatch.
Fix the code, re-run `awf-kamal-deploy`.

### E-12: Slug collision on shared server

**Detect:** Two projects have the same slug. When one deploys, the
other's container is replaced. Both projects' `config/deploy.yml`
have `service: same-slug`.

**Fix:** Change the slug in one project's `.awf/project.json` and
`config/deploy.yml`. Slugs must be unique across all apps on the same
server. Convention: prefix with a short namespace (`alice-myapp`).

### E-13: Stage/infra state mismatch (inconsistent state)

**Detect:** `awf-status --json` shows `stage = mvp-play` but `has.infra = false`.
Or `infra.json` exists but `project.json` says `stage = landing`.

**Fix:** This usually means a partial migration was recorded incorrectly.
1. Read both `project.json` and `infra.json` carefully
2. Determine what's actually true (what resources actually exist)
3. Edit the state files to match reality
4. Re-run `awf-status` to confirm the state makes sense
5. Re-run the composer — it will execute any steps that are genuinely missing

### E-14: Google OAuth token expired (awf-setup-gsc / awf-verify-gsc fails)

**Detect:** `awf-doctor` shows `[FAIL]` for `google_token_json` (cached
OAuth token). Or the skill fails with a 401/403 from the Google API.

**Fix:**
```bash
rm ~/.config/awf/token.json   # delete the expired token
/awf-setup-gsc                # re-run — triggers OAuth flow in browser
```

### E-15: Namecheap API client IP not whitelisted

**Detect:** `awf-setup-nameservers` fails with "IP not in whitelist" error.

**Fix:**
1. Get your current public IP: `curl ifconfig.me`
2. Go to Namecheap → Profile → API access → Add IP
3. Re-run `awf-setup-nameservers`

Note: if working from a dynamic IP (home broadband), update this whenever
your IP changes. Add your CI runner IP too if using automated deploys.

### E-16: Multiple `.awf/project.json` in the path (nested projects)

**Detect:** The wrong project is found by `awf-status`. Skills operate
on a project you didn't intend.

**Fix:** The project locator (`lib/project.py`) returns the FIRST
`project.json` found walking UP from cwd. If you're inside project A
but want to operate on project B, navigate to project B's directory:
```bash
cd /path/to/project-b
```

### E-17: Play server capacity reached

**Detect:** New container on the play server fails to start due to OOM.
`ssh root@<ip> "free -m"` shows < 200 MB free. `docker stats` shows
memory pressure.

**Options:**
1. Stop unused app containers: `ssh root@<ip> "docker stop <slug>-web-1"`
2. Upgrade the server type (Hetzner resize, ~5 min downtime for all apps)
3. Migrate busy apps to S4 (dedicated server)

### E-18: DATABASE_URL missing ?sslmode=require

**Detect:** DB connection errors in logs: "SSL connection is required"
or "SSL off" from Neon.

**Fix:**
```bash
uv run ~/.claude/skills/awf-app-secret-set/scripts/app_secret_set.py \
    --key DATABASE_URL \
    --value "postgres://...?sslmode=require"   # append the parameter
uv run ~/.claude/skills/awf-kamal-deploy/scripts/kamal_deploy.py
```
All Neon connection strings must end with `?sslmode=require`.

---

## Anti-patterns: what NOT to do

**Do not skip awf-doctor before running a composer.**
Credential failures mid-composer are harder to recover from than
a preflight failure. The 10 seconds `awf-doctor` takes is always
worth it.

**Do not delete and recreate a server to fix a deploy issue.**
The shared play server hosts multiple apps. Deleting it destroys all
of them. Fix the specific problem. Only provision a new server when
`awf-status` shows the server is genuinely gone.

**Do not run `awf-deploy` for an S3+ project.**
`awf-deploy` targets Cloudflare Pages. It will appear to succeed but
the Hetzner container is unchanged. Use `awf-kamal-deploy` for all
mvp-play+ stages.

**Do not manually edit `.awf/infra.json` to advance the stage.**
The stage is recorded in `.awf/project.json`. Editing `infra.json`
directly to fix state is sometimes necessary for recovery but must be
followed by `awf-status` to verify consistency. Never set `stage` in
`infra.json` — it lives only in `project.json`.

**Do not promote `project.json` stage manually.**
Let the composer set the stage after verifying all steps succeeded.
A manually-promoted stage with incomplete infrastructure confuses
subsequent skills and awf-status drift detection.

**Do not put secrets in project.json, infra.json, or deploy.yml.**
These files may end up in git. Secrets belong only in `.kamal/secrets`
(gitignored by the template) and `~/.config/awf/.env` (outside the
project tree).

**Do not attempt manual Docker operations on a live server to fix a
deploy.**
Running `docker stop`, `docker rm`, or `docker run` directly bypasses
kamal-proxy's drain logic and can cause a traffic outage. Always use
`kamal deploy` or `kamal rollback` for container lifecycle management.

**Do not ignore exit code 5 from awf-stage-mvp-play.**
Exit 5 means DNS gate timeout — not failure. The partial state is
tracked. Re-run after DNS propagates. Do not attempt to debug further
until DNS resolves.

**Do not run any composer from the awf-skills repo directory.**
Composers locate the project by walking up from `cwd`. Running from
`/path/to/awf-skills` will either find no project or find the wrong one.
Always `cd` into the project directory first.

---

## Sequence dependency map

Operations with hard ordering requirements. Violating these causes
failures that are confusing to diagnose.

### S1 hard dependencies

```
awf-setup-domain
     ↓ (zone must exist)
awf-setup-nameservers
     ↓ (NS must propagate — 5–30 min manual wait)
awf-deploy  (CF Pages requires zone to be active)

awf-generate-content
     ↓
awf-review-passport
     ↓
awf-install
     ↓
awf-deploy

awf-setup-gsc → awf-verify-gsc → awf-submit-bing
(must be in order; each step requires the previous)
```

### S3 hard dependencies

```
awf-shared-infra-get
     ↓ (provides server IP and neon project_id)
awf-app-dockerize  (parallel with neon-branch OK)
awf-neon-branch
     ↓ (provides connection string)
awf-app-secret-set (DATABASE_URL)
     ↓
awf-kamal-config
     ↓ (deploy.yml must exist)
awf-cf-dns-record  (creates A record)
     ↓ (DNS must propagate — polled internally by setup)
awf-kamal-setup
     ↓ (Docker + kamal-proxy must be installed)
awf-kamal-deploy
```

The `awf-stage-mvp-play` composer enforces this ordering automatically.
When running atomic skills manually, follow this sequence.

---

## Quick reference: commands by situation

```bash
# ORIENT ─────────────────────────────────────────────────────────────
uv run ~/.claude/skills/awf-status/scripts/status.py --json
uv run ~/.claude/skills/awf-doctor/scripts/check.py --json
uv run ~/.claude/skills/awf-log/scripts/log.py tail -n 10

# FIRST-TIME SETUP ────────────────────────────────────────────────────
./install.sh                                      # from AWF_HOME
/awf-init                                         # in Claude Code session
/awf-doctor                                       # verify

# S1 LAUNCH ───────────────────────────────────────────────────────────
/awf-create-project
/awf-launch                                       # full pipeline

# S1 REDEPLOY ─────────────────────────────────────────────────────────
/awf-install && /awf-deploy

# S3 PROMOTE ──────────────────────────────────────────────────────────
uv run ~/.claude/skills/awf-stage-mvp-play/scripts/stage_mvp_play.py

# S3 REDEPLOY ─────────────────────────────────────────────────────────
uv run ~/.claude/skills/awf-kamal-deploy/scripts/kamal_deploy.py

# DIAGNOSE ────────────────────────────────────────────────────────────
uv run ~/.claude/skills/awf-doctor/scripts/check.py --for-stage mvp-play
uv run ~/.claude/skills/awf-log/scripts/log.py find error
uv run ~/.claude/skills/awf-log/scripts/log.py session last

# ROLLBACK ────────────────────────────────────────────────────────────
kamal rollback                                    # from project directory

# SERVER OPS ──────────────────────────────────────────────────────────
ssh root@<ip> "docker stats --no-stream"          # resource usage
ssh root@<ip> "docker logs <slug>-web-1"          # app logs
ssh root@<ip> "docker system prune -f"            # clean old images

# DNS CHECK ───────────────────────────────────────────────────────────
dig +short <domain>                               # what does DNS resolve to?
```
