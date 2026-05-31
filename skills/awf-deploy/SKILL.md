---
name: awf-deploy
description: Build the SvelteKit project and deploy it to Cloudflare Pages via wrangler. Run after awf-install and after awf-setup-domain (the Pages project must exist).
---

# Purpose

`npm run build` then `npx wrangler pages deploy`. Single concern.

# Prerequisites

- A project root.
- `wrangler` installed and `wrangler whoami` returns an account.
- The Cloudflare Pages project for this domain exists (created by
  `awf-setup-domain`).

# Procedure

1. Verify `wrangler whoami` succeeds.
2. Warn if the working tree is dirty (don't block — deploys before
   commit are sometimes intentional).
3. `npm run build`.
4. `npx wrangler pages deploy` (the project name is read from
   `wrangler.toml` in the project; the template ships with it pinned).
5. Print the deployment URL.

# Idempotency

Each deploy is a new immutable Pages deployment. Re-running creates a
fresh one; not a problem.

# Failure modes

- `wrangler whoami` fails → tell the user to `wrangler login`.
- Build error → surface `npm run build` output unchanged.
- Pages project not found → surface the wrangler error and tell the
  user to run `awf-setup-domain` first.

# Implementation status

✓ Functional. `scripts/deploy.py` runs `wrangler whoami` first, warns on
dirty git tree (doesn't block), then `npm run build` + `wrangler pages
deploy`. Marks `passport.launch.gates.deploy` on success.
