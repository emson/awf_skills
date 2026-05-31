---
name: awf-status
description: Report the live state of a website project — does the Cloudflare zone exist, is the Pages project deployed, is the Fathom site created, is GSC verified. Run to figure out where a partial launch left off, or before re-running awf-launch.
---

# Purpose

Resumability without local state (A7). Queries Cloudflare, Fathom, and
GSC for ground truth and prints a checklist of done/missing for the
current project.

# Prerequisites

- A project root (passport.json present in cwd or above), OR a `--domain`
  flag.
- Cloudflare, Fathom, and (optionally) Google credentials. `awf-doctor`
  validates them.

# Inputs

- `--domain <domain>` (optional) — when not run from inside a project.
- `--json` — machine-readable output.

# Procedure

1. Load `passport.json` (or build a minimal one from `--domain`).
2. Run `uv run "$AWF_HOME/skills/awf-status/scripts/status.py"`.
3. Report the table to the user, with each line marked done / missing /
   error. The orchestrator (`awf-launch`) consumes the JSON form.

# Idempotency

Pure read.

# Manual gates

None.

# Implementation status

✓ Fully functional. Cloudflare (zone + NS, apex CNAME, www A record,
always_use_https, Pages project + domain, bulk-redirect), Fathom
(passport `fathom_site_id` confirmed live, drift detection), GSC
(property exists + verified, sitemap submitted). Skips GSC when no
cached token rather than triggering interactive OAuth from a status
check.
