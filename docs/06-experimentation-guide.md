# 06 — Experimentation Guide

A hands-on walkthrough for trying the skills end-to-end. Four
progressive tiers, each requiring a bit more setup than the last:

| Tier | Needs | Validates |
|------|-------|-----------|
| 1. **Dry** | repo cloned | bootstrap, doctor, init, create-project, review-passport |
| 2. **Local build** | + Node + npm | template overlay, `npm run build`, passport substitution |
| 3. **API-side** | + Cloudflare / Namecheap / Fathom creds | setup-domain, setup-nameservers, setup-analytics, status |
| 4. **Full launch** | + a *real* domain you own | install, deploy, GSC, IndexNow, end-to-end |

You can stop at any tier. Tiers 1 and 2 cost nothing and exercise the
bulk of the suite's logic; tiers 3 and 4 spend real money / claim real
DNS and should use a throwaway domain the first time.

This guide assumes a fresh checkout. Adjust paths as needed.

---

## 0. One-time setup

```bash
# clone wherever you like; the conventional path is ~/.claude/awf-skills/
git clone <repo> ~/.claude/awf-skills
cd ~/.claude/awf-skills

# symlink each skill into ~/.claude/skills/ so Claude Code discovers them
./install.sh

# tell scripts where the repo lives, in this shell and persistently
export AWF_HOME=~/.claude/awf-skills
echo "export AWF_HOME=$AWF_HOME" >> ~/.zshrc   # or ~/.bashrc
```

`install.sh` is idempotent — re-running is fine. To uninstall:
`./install.sh --uninstall`.

### Required tools (Tier 1 minimum)

- `bash`, `git`, `uv` (https://docs.astral.sh/uv/getting-started/installation/)

### Required tools (Tier 2)

- `node` / `npm` (for `npm run build`)

### Required tools (Tier 4)

- `wrangler` (`npm i -g wrangler`) + `wrangler login`
- `gh` (optional but recommended)

`awf-doctor` validates all of the above and reports anything missing.

---

## Tier 1 — dry: no credentials, no network

### 1.1 Validate the runtime

In any Claude Code session, in any directory:

```
> /awf-doctor
```

Expected output (excerpt):

```
awf-doctor — runtime check
────────────────────────────────────────────────────────────
AWF_HOME     : /Users/you/.claude/awf-skills
project_root : (none — not inside a project)

auth
  [FAIL]  wrangler whoami  — wrangler not installed
          ↳ run: wrangler login
  [OK]    gh auth status (optional)  — github.com

cli
  [OK]    git   — /usr/bin/git
  [OK]    uv    — ...
  [OK]    node  — ...
  [OK]    npm   — ...
  [FAIL]  wrangler  — not on PATH
          ↳ npm i -g wrangler

creds:cloudflare
  [FAIL]  CLOUDFLARE_EMAIL — not set
  ...
```

Read the report. Every `[FAIL]` is something you'll need before
later tiers; **do not try to "fix" them all now** — many are only
needed at Tier 4.

### 1.2 Onboard

```
> /awf-init
```

Walks the four steps interactively:

```
[1/4] Resolve AWF_HOME
  ✓ AWF_HOME = /Users/you/.claude/awf-skills

[2/4] User credentials (~/.config/awf/.env)
  ✓ created /Users/you/.config/awf/.env from template

  9 credential(s) to fill. Press enter to skip any.

  # ── Cloudflare API ─────────────────────────────────────
  CLOUDFLARE_EMAIL=                # ← press Enter to skip for now

  # ...etc
```

For Tier 1, **press Enter through every prompt** to leave credentials
blank. The empty file is fine; Tier 3+ skills will refuse to run until
keys are populated.

### 1.3 Scaffold a throwaway project

```bash
cd ~/tmp                            # any empty directory
```

```
> /awf-create-project example.test --no-git
```

Output:

```
- template: landing-page v0.1 (/Users/you/.claude/awf-skills/templates/landing-page-v0)
- target: /Users/you/tmp/example-test
- overlay: 6 created, 0 modified, 0 preserved, 0 unchanged
- wrote passport.json

Project scaffolded at: /Users/you/tmp/example-test

Next:
  cd /Users/you/tmp/example-test
  awf-doctor                 # confirm credentials are set
  awf-setup-domain           # establish Cloudflare zone + Pages
  ...
```

The slug `example-test` is derived from the domain (`example.test`
isn't a real TLD, which is why we use it for testing).

Inspect the result:

```bash
$ cd example-test && tree -a
.
├── .gitignore
├── build.mjs
├── package.json
├── passport.json
├── static
│   ├── index.html
│   ├── robots.txt
│   └── sitemap.xml
└── template_version: 0.1  (in passport.json)
```

### 1.4 Edit the passport by hand

The passport's text fields (`site_name`, `site_hero`, etc.) are empty
on a fresh scaffold — normally `awf-generate-content` fills them from
a Google-SERP screenshot. For dry-run testing, edit them manually:

```bash
$ $EDITOR passport.json
```

Set:

```jsonc
{
  "site_name":        "Example Test",
  "site_hero":        "A site for testing awf-skills",
  "site_subtitle":    "Ignore me — I'm a throwaway",
  "site_description": "Validates the awf-skills pipeline end-to-end.",
  "category":         "Testing",
  "tags":             "test, demo",
  "email":            "you@example.com",
  ...
}
```

### 1.5 Lint the passport

```
> /awf-review-passport
```

Expected:

```
awf-review-passport — /Users/you/tmp/example-test/passport.json
────────────────────────────────────────────────────────────
  domain          : example.test
  project_name    : example-test
  schema_version  : 1.0
  template_version: 0.1
  fathom_site_id  : (unset)
  faqs            : 0 entries

  [WARN] only 0 FAQs (template expects ≥ 3)

Pass --mark-reviewed once you've inspected the file to advance the launch gate.
```

Empty FAQs are a warning, not an error — Tier 1 ignores them. To
satisfy the warning, add a couple of entries:

```json
"faqs": [
  { "question": "What is this?", "answer": "A test." },
  { "question": "Why?",          "answer": "Because." },
  { "question": "When?",         "answer": "Now." }
]
```

Then mark the gate complete:

```
> /awf-review-passport --mark-reviewed
```

### 1.6 Verify status (no credentials)

```
> /awf-status
```

You'll see graceful per-section credential errors — each section
reports independently so you can see what's missing without one
failure cascading into the rest:

```
awf-status — example.test
────────────────────────────────────────────────────────────
cloudflare
  [ERR]   credentials  — Cloudflare credentials missing: ...

fathom
  [ERR]   credentials  — Fathom credentials missing: ...

gsc
  [MISS]  auth  — no cached token — run awf-setup-gsc to authenticate
  ...
```

**Tier 1 complete.** You've exercised: `init`, `doctor`,
`create-project`, `review-passport`, `status`. None of these touched
a network API.

---

## Tier 2 — local build: validate the template substitution

Still no credentials needed; just `node` + `npm`.

### 2.1 Install npm deps (none, but it validates `package.json`)

```
> /awf-install
```

The v0 template has no runtime dependencies — `npm install` does
almost nothing but ensures `package.json` is well-formed:

```
- npm install in /Users/you/tmp/example-test

up to date, audited 1 package in 200ms
```

### 2.2 Build

The `awf-deploy` skill does build + deploy in one step. For Tier 2
we want to inspect just the build output, so run `npm run build`
directly:

```bash
$ npm run build
> awf-site@0.0.0 build
> node build.mjs

build: 3 substituted, 0 copied → dist/
```

### 2.3 Inspect the substituted output

```bash
$ grep -E '<title>|<h1>|<meta name="description"' dist/index.html
  <title>Example Test</title>
  <meta name="description" content="Validates the awf-skills pipeline end-to-end.">
  <h1>A site for testing awf-skills</h1>

$ cat dist/sitemap.xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.test/</loc>
    ...
```

All the `{{var}}` placeholders are gone, replaced by passport values.

### 2.4 Try a template update (dry-run)

There's only one template (`landing-page-v0`), so this is a no-op,
but the *dry-run* mechanic itself is worth exercising:

```
> /awf-update-template --dry-run
```

```
- current template_version: 0.1
- target template:          landing-page v0.1

- nothing to do: target v0.1 ≤ current v0.1. Pass --force to re-apply or downgrade.
```

When a `landing-page-v1` ships later, this same command will show a
diff list. To force re-application of the current template (useful if
you've damaged a file and want to restore it from the template,
preserving passport / IndexNow keys):

```
> /awf-update-template --force --dry-run
```

**Tier 2 complete.** You've validated the template overlay logic,
the substitution, the `npm` plumbing, and the dry-run safety net.

---

## Tier 3 — API-side: with credentials, without a real domain

Populate `~/.config/awf/.env` with at least Cloudflare, Namecheap, and
Fathom credentials. You can populate via `awf-init` interactively or
edit the file directly:

```bash
$ $EDITOR ~/.config/awf/.env
```

Required for Tier 3:

```ini
CLOUDFLARE_EMAIL=you@example.com
CLOUDFLARE_API_KEY=...
CLOUDFLARE_ACCOUNT_ID=...

NAMECHEAP_API_USER=yourusername
NAMECHEAP_API_KEY=...
NAMECHEAP_USERNAME=yourusername
NAMECHEAP_CLIENT_IP=your.public.ip   # must be allowlisted in Namecheap

FATHOM_API_KEY=...
```

Re-run `awf-doctor`:

```
> /awf-doctor
```

Everything in `creds:cloudflare`, `creds:namecheap`, `creds:fathom`
should now be `[OK]` with the layer it resolved from.

### 3.1 Test the Fathom path against a real account

`awf-setup-analytics` is the safest mutation to try first — it creates
a Fathom site (no DNS, no domain registration, no cost). Use a
distinctive site name so you can find it in the Fathom dashboard:

```
> /awf-setup-analytics
```

Output:

```
- creating Fathom site for example.test
- passport.fathom_site_id = ABCDEF12
```

Open https://app.usefathom.com/ — your throwaway site should be
listed. Delete it manually when you're done (no `awf-delete-analytics`
yet).

### 3.2 Test idempotency

Run the same command again:

```
> /awf-setup-analytics

- Fathom site_id=ABCDEF12 already in passport and confirmed live; no-op
```

### 3.3 Re-run status — Fathom should now light up green

```
> /awf-status

fathom
  [OK]    site  — id=ABCDEF12 (from passport, live)
```

### 3.4 (Optional) `awf-setup-domain` against a *throwaway test domain*

⚠ This step creates a real Cloudflare zone and Pages project. If you
use a domain you don't own, Cloudflare will detect the conflict and
fail loudly — that's fine; it's a clean way to test the API plumbing
without claiming anything. If you do own a test domain (e.g. a cheap
`.xyz` you bought specifically for this), you can run the full thing.

Either way:

```
> /awf-setup-domain
```

Idempotent — re-running surfaces "already exists" for each resource.
On success, the zone's nameservers are stashed in
`passport.launch.gates.domain_setup.meta.nameservers` for the next
skill to consume.

**Tier 3 complete.** You've exercised real API mutations and verified
idempotency without going through the irreducibly manual steps (NS
swap at the registrar, DNS propagation wait).

---

## Tier 4 — full launch: real domain, end-to-end

This is the real pipeline. Have a domain you own (registered at
Namecheap, since that's the only registrar adapter we ship — A16) and
expect ~30 minutes of wall time (DNS propagation, GSC verification).

### 4.1 The full flow

In a fresh shell, from anywhere:

```
> /awf-launch  example.com  --keywords "your keywords here"
```

The orchestrator skill walks the pipeline, pausing at the
irreducibly manual gates. You don't need to remember the order — but
if you want to do it step-by-step, here is the canonical sequence:

```bash
> /awf-create-project example.com
> cd example-com
> /awf-doctor                                    # last chance to fix env
> /awf-setup-domain                              # Cloudflare zone + Pages + DNS + redirects
> /awf-setup-nameservers                         # Namecheap NS swap (uses passport gate)
> /awf-setup-analytics                           # Fathom site
# --- manual gate: take a Google SERP screenshot, save it somewhere ---
> /awf-generate-content --screenshot ./serp.jpg --keywords "your keywords here"
# --- manual gate: review the generated copy ---
> /awf-review-passport --mark-reviewed
> /awf-install
> /awf-deploy                                    # first deploy — site goes live on CF Pages
> /awf-setup-gsc                                 # adds GSC property + TXT record
# --- wait 2–5 minutes for DNS propagation ---
> /awf-verify-gsc                                # verify + submit sitemap
# --- manual gate: open Bing Webmaster, import from GSC ---
> /awf-submit-bing --confirm-imported            # generates key file, deploys, submits URLs
# the key file step requires *another* deploy — awf-submit-bing
# will exit 3 the first time and tell you to redeploy:
> /awf-deploy
> /awf-submit-bing --confirm-imported
```

### 4.2 Recovery from a partial launch

Re-running `/awf-status` at any point shows you what's done and what's
not — gates are queried *live* from the upstream APIs (per A7), not
read from a local cache:

```
> /awf-status

awf-status — example.com
────────────────────────────────────────────────────────────
cloudflare
  [OK]    zone               — id=abc... ns=clay.ns.cloudflare.com, connie.ns.cloudflare.com
  [OK]    apex_cname         — -> example-com.pages.dev
  [OK]    www_record         — www -> 192.0.2.1
  [OK]    always_use_https   — value=on
  [OK]    pages_project      — name=example-com
  [OK]    pages_domain       — example.com attached
  [OK]    bulk_redirect_www  — www.example.com -> https://example.com

fathom
  [OK]    site               — id=ABCDEF12 (from passport, live)

gsc
  [OK]    verified           — sc-domain:example.com (siteOwner)
  [MISS]  sitemap            — no sitemaps submitted

All checked steps complete.
```

Re-run only the parts that still report `[MISS]`:

```
> /awf-verify-gsc                                # picks up where we left off
```

The orchestrator (`awf-launch`) uses this same logic internally to
skip completed gates on resume.

---

## Reference: what each gate means

| Gate (`passport.launch.gates.X`) | Set by | Means |
|---|---|---|
| `domain_setup` | `awf-setup-domain` | CF zone + Pages + DNS established; NS list cached |
| `nameservers_setup` | `awf-setup-nameservers` | Namecheap NS now points at CF |
| `analytics_setup` | `awf-setup-analytics` | Fathom site exists; ID in passport |
| `content_generated` | `awf-generate-content` | Site copy + FAQs written to passport |
| `passport_reviewed` | `awf-review-passport --mark-reviewed` | Human has eyeballed the copy |
| `deploy` | `awf-deploy` | Successful Cloudflare Pages deploy |
| `gsc_setup` | `awf-setup-gsc` | GSC property added, TXT record on CF |
| `gsc_verify` | `awf-verify-gsc` | Property verified, sitemap submitted |
| `bing_imported` | `awf-submit-bing --confirm-imported` | Manual Bing-Webmaster import confirmed |
| `submit_bing` | `awf-submit-bing` | URLs pushed to IndexNow |

Gates are advisory metadata — re-running a skill always re-checks
upstream state. They're used for two purposes only:

1. **Orchestrator skip-logic** — `awf-launch` consults gates to skip
   steps whose human-side is done.
2. **Audit** — a quick history of when a launch's stages were
   completed.

---

## Troubleshooting

### "Cloudflare credentials missing"
You haven't populated `~/.config/awf/.env`. Run `awf-init` interactively
or edit it directly; verify with `awf-doctor`.

### "namecheap: Invalid request IP"
Namecheap requires your public IP to be whitelisted in their API
allowlist (account settings → API). The IP you set in
`NAMECHEAP_CLIENT_IP` must match what Namecheap sees as your source IP
when the request arrives. If you're on a dynamic IP, you'll need to
update both whenever it changes.

### "TXT record not propagated yet" from `awf-verify-gsc`
DNS propagation typically takes 1–5 minutes after `awf-setup-gsc`
writes the record. Wait and re-run. You can confirm propagation with:

```bash
dig TXT example.com
```

### `awf-submit-bing` repeatedly says "key file not reachable"
The key file lives at `static/<key>.txt` in your project; it must be
served at `https://<domain>/<key>.txt` for Bing to verify ownership.
After `awf-submit-bing` generates the key:

1. The file is written into `static/`.
2. The IndexNow API will reject submissions until that file is live.
3. You must `awf-deploy` *again* to make the file reachable.
4. Then re-run `awf-submit-bing --confirm-imported`.

The skill catches the "200 with SPA-fallback HTML body" case
explicitly — if you see that in the error message, it means your
build is treating unknown paths as 404 → index.html, which is the SPA
default. The v0 template doesn't do this (`static/<key>.txt` is a
real file served by Cloudflare directly).

### Google OAuth keeps re-prompting
Delete the cached token:

```bash
rm ~/.config/awf/token.json     # or wherever awf-doctor reports it
```

And re-run the next GSC skill. A fresh browser flow will run, and the
new token writes to the same path.

### "schema_version is newer than this awf-skills understands"
You're working with a passport written by a newer version of the
skills. Update awf-skills:

```bash
cd ~/.claude/awf-skills && git pull
```

---

## §why-no-wrangler-toml

The v0 template doesn't include a `wrangler.toml`. Instead,
`awf-deploy` reads `passport.project_name` (which is derived from
`passport.domain` via `lib/slug.py` — the single source of truth per
A12) and passes it explicitly to wrangler:

```
wrangler pages deploy dist --project-name=<slug>
```

Why:

- **Substitution timing.** `wrangler.toml` is read by `wrangler` at
  the start of deploy, *before* our `npm run build` substitutes
  `{{var}}` placeholders. Embedding the project name in
  `wrangler.toml` would require either (a) running substitution
  before wrangler, or (b) having `awf-create-project` substitute at
  scaffold time. Both add coupling between the template format and
  the skills.
- **One source of truth.** With the `--project-name` flag, the only
  place the project's identity is stored is `passport.json`. The
  template never needs to know.

A richer template later may bring `wrangler.toml` back if it needs
additional wrangler config (env vars, KV bindings, etc.); the
project-name flag will still take precedence.

---

## What this guide does NOT cover

- **Building a richer template** — that's its own piece of work; see
  [`templates/README.md`](../templates/README.md) for the contract a
  new template must honour.
- **`awf-generate-content`** — Claude-native (A17); the SKILL.md is
  the contract, no script. Used inside a Claude Code session, the
  model reads your SERP screenshot and writes structured output to
  `passport.json`.
- **`awf-launch`** — orchestrator, body-only. Best tested *after* you
  have a real end-to-end working manually so you can compare.
- **Mass launches** — the suite is designed for single sites. Batch
  workflows are out of scope (a non-goal in
  [`docs/00-plan.md` §10](00-plan.md)).
