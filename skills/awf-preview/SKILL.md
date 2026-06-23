---
name: awf-preview
description: Deploy an artifact to a Cloudflare preview URL. Auth-aware — unauthenticated sessions use `wrangler deploy --temporary` (60-min, zero credentials, claim-to-keep); authenticated sessions use `wrangler deploy` (permanent until deleted, prints the delete command). One concern: get a live *.workers.dev URL fast.
---

# Purpose

The scratchpad rung of the AWF factory. Where `awf-deploy` ships to a
permanent Cloudflare Pages project and `awf-stage-mvp-play` stands up a
full Hetzner + Neon + Kamal app, `awf-preview` does the opposite: get
a live URL on the real Cloudflare edge as fast as possible, no project
state required.

**Mode is automatic, chosen by auth state:**

| Session | Command used | URL lifetime | Cleanup |
|---------|-------------|--------------|---------|
| Logged out | `wrangler deploy --temporary` | ~60 min (claim to extend) | Auto-expires |
| Logged in | `wrangler deploy` | Permanent | `wrangler delete --name <name>` (printed in output) |

# When to use

- You just built a Worker or static artifact and want it live on the
  edge before wiring up a real project.
- A disposable "click to try this" link for a review session.
- Prototyping a landing page, trainer, or experiment before committing
  to `awf-create-project`.
- Agent build→deploy→verify loops (unauthenticated mode, CI context).

# When NOT to use

- Production hosting — not the right rung.
- Anything with KV/D1/Durable Objects state you care about keeping
  (unauthenticated mode: state dies with the temporary account unless
  claimed).
- Tight loops creating many previews — temporary-account creation is
  rate-limited.
- Deploying secrets or keys — the URL is briefly public.

# Prerequisites

- **Wrangler** on PATH (any recent version for authenticated mode;
  **≥ 4.102.0** required for unauthenticated / `--temporary` mode).
- Either a directory containing a `wrangler.{jsonc,toml,json}` config,
  or a directory of static files (pass via `--assets`, or point `path`
  at it — a config is generated automatically).

# Inputs

| Flag | Default | Description |
|------|---------|-------------|
| `path` (positional) | `.` | Directory to deploy from. |
| `--assets DIR` | — | Deploy `DIR` as a static-assets-only site. A minimal wrangler config is generated for the run and discarded. |
| `--name NAME` | derived from dir | Worker name (slugified). Ignored when an existing wrangler config is present. |
| `--keep-config` | `false` | Keep a generated config instead of deleting it after deployment. |
| `--json` | `false` | Emit machine-readable JSON (mode, live_url, claim_url or delete_cmd, raw). |

# Procedure

```
uv run "$AWF_HOME/skills/awf-preview/scripts/preview.py" [path] \
    [--assets public] [--name my-preview] [--keep-config] [--json]
```

1. Resolve the wrangler command (prefer global `wrangler`, else `npx wrangler`).
2. Check `wrangler whoami` to determine auth state (drives mode selection).
3. Unauthenticated: verify wrangler ≥ 4.102.0; abort with upgrade hint if lower.
4. If `--assets` given (or no wrangler config in dir), generate a minimal
   assets-only config; discard after deployment unless `--keep-config`.
5. Run `wrangler deploy [--temporary]` and stream output.
6. Parse output for the live `*.workers.dev` URL.
   - Unauthenticated: also surfaces the claim URL.
   - Authenticated: prints `wrangler delete --name <name>` for cleanup.

# Idempotency

Stateless — nothing written to `passport.json` or `.awf/`. Re-running
is always safe. In authenticated mode, re-running the same name
re-deploys in place (same URL). In unauthenticated mode, each run
creates a fresh temporary account and a new URL.

# Failure modes

- Wrangler not found → install Wrangler.
- Wrangler < 4.102.0 (unauthenticated path) → `npm i -g wrangler@latest`.
- **Limbo session** (whoami exits 1 but Cloudflare still sees the local token): the
  skill tries unauthenticated (`--temporary`); Cloudflare blocks it with "already
  authenticated". Fix: `wrangler login` to refresh, or `wrangler logout` to clear.
  The script prints a targeted hint for this case (error code 10000 or
  "already authenticated" in output).
- Rate-limited temporary-account creation → wait and retry, or log in
  (skill switches to authenticated mode automatically on next run).
- Build/deploy error → wrangler output streamed unchanged; exit code
  propagated.

# Deleting an authenticated preview

The skill prints the exact command at the end of a successful deploy:

```
  delete: wrangler delete --name <worker-name>
```

Run that whenever you are done. No separate skill needed.

# Where this skill lives

`awf_skills` (not `skill_forge`). AWF skills are deployment/infrastructure;
`skill_forge` is cognitive meta-skills (scout, simulate, specforge). They
stay separate. `awf_skills/skills/` may be added to `SKILLLOOP_READ_ROOTS`
so skill_loop can observe routing gaps, but skill_loop never writes here.
