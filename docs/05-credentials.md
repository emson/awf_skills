# 05 — Credentials

Every credential the suite touches, where it comes from, where it's
looked up at runtime, and which skill needs it.

The full list is mirrored in [`.env.example`](../.env.example). If the
two ever disagree, `.env.example` wins (it's machine-checkable; this is
prose).

---

## Resolution order (per A6)

For any credential key, `lib/config.py` checks in order; first match
wins:

1. **process environment** (`os.environ`)
2. **project-local `.env`** at `<project_root>/.env` — only when a
   project root is found by walking up from cwd.
3. **skills-repo `.env`** at `$AWF_HOME/.env`
4. **user-global `.env`** at `~/.config/awf/.env`

`awf-doctor` prints the source for each key on every run.

There is no implicit `dotenv.load_dotenv()` of any single file. We load
all four into a layered dict and resolve on access. This means setting a
variable in your shell always wins, no matter what's in the files.

---

## Catalogue

### Cloudflare API
| Key | Used by | Notes |
|---|---|---|
| `CLOUDFLARE_EMAIL` | `awf-setup-domain`, `awf-setup-gsc` | account email |
| `CLOUDFLARE_API_KEY` | as above | global API key (legacy form). Token-form support is a future improvement. |
| `CLOUDFLARE_ACCOUNT_ID` | as above | UUID; find in dashboard |

### Cloudflare Pages deploy
Separate from the API: `wrangler` CLI auth, established by
`wrangler login` (browser-OAuth on first run). `awf-doctor` runs
`wrangler whoami` to verify.

### Namecheap
| Key | Used by | Notes |
|---|---|---|
| `NAMECHEAP_API_USER` | `awf-setup-nameservers` | usually the Namecheap username |
| `NAMECHEAP_API_KEY` | as above | enable API access in account settings |
| `NAMECHEAP_USERNAME` | as above | |
| `NAMECHEAP_CLIENT_IP` | as above | must be allowlisted in Namecheap |

### Fathom
| Key | Used by | Notes |
|---|---|---|
| `FATHOM_API_KEY` | `awf-setup-analytics` | |

### Google (GSC + Site Verification)
| Artifact | Used by | Notes |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` (env var, file path) | `awf-setup-gsc`, `awf-verify-gsc` | path to OAuth desktop client JSON |
| `<project_root>/token.json` *or* `$AWF_HOME/token.json` | as above | cached refresh token; written on first auth |

The OAuth flow is interactive (browser) on first run. `awf-doctor`
detects an absent or expired token and instructs the user to run the
auth helper. Skills that need GSC must not invoke the auth flow
silently.

### Bing IndexNow
| Key | Used by | Notes |
|---|---|---|
| `passport.json#indexnow_key` | `awf-submit-bing` | per-domain (UUID hex). Generated on first Bing setup; stored in passport (A6 trade-off resolved per-domain). |

There is **no** `BING_INDEXNOW_KEY` env var. The key is per-site.

### LLM
The legacy content step used `OPENAI_API_KEY`. In this suite,
`awf-generate-content` is Claude-native (A17). Neither `OPENAI_API_KEY`
nor `ANTHROPIC_API_KEY` is required by any skill — Claude itself is
running them.

---

## File precedence in practice

A common setup:

- `~/.config/awf/.env` — long-lived account creds (Cloudflare,
  Namecheap, Fathom, Google credentials path). Set once.
- `$AWF_HOME/.env` — usually empty. Use it only for things specific to
  this skills checkout (e.g. testing against a sandbox account).
- `<project_root>/.env` — usually absent. Use it only for per-project
  overrides (rare; the project's identity lives in `passport.json`,
  not in env).
- `process env` — the override of last resort, e.g. running a one-off
  with a different Cloudflare account.

`awf-doctor` shows you exactly which file each variable resolved from
on this run.

---

## Security posture

- `.env` files are in `.gitignore`. `.env.example` is the only env file
  ever committed.
- `token.json` (Google) is in `.gitignore`.
- Skills never log credential values. They log *sources* (which file)
  and presence/absence.
- If a credential is missing, the failing skill points the user at
  `awf-doctor` rather than offering to "fix" the .env automatically.

---

## Adding a new credential

See [`04-skill-authoring.md` § Adding a new credential](04-skill-authoring.md).
The four-step ordering (`.env.example` → doctor → `lib/config.py` →
this doc) ensures a missing credential is caught before any skill that
uses it can be run.
