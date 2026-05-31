---
name: awf-create-project
description: Scaffold a new website project — create the directory, copy the current template, write passport.json, optionally git-init. Run this first when launching a new site.
---

# Purpose

Bootstrap a fresh project on disk. Single entry point for "I have a
domain; I want a Svelte site I can deploy."

# Prerequisites

- `awf-doctor` passes (or at least its non-Cloudflare/Namecheap parts —
  scaffolding doesn't touch APIs).
- `uv`, `git`.

# Inputs

- `domain` (required) — e.g. `devroast.com`. The slug is derived (A12).
- `--template <name>` (optional, default: latest under
  `$AWF_HOME/templates/`).
- `--no-git` (optional) — skip `git init`.
- `--force` (optional) — overwrite an existing project dir (per the
  template's `preserve-list.txt`).

# Procedure

1. Run `uv run "$AWF_HOME/skills/awf-create-project/scripts/create.py" <domain> [--in <dir>] [--template <name>] [--force] [--no-git]`
2. Report the output verbatim to the user.
3. If the exit code is non-zero, surface the error and suggest fixes per the failure modes below.

# Idempotency

If the target dir already has a `passport.json`, refuse without
`--force`. With `--force`, run the equivalent of the legacy
`overwrite_project_website` (PRESERVE_LIST honoured).

# Manual gates

None — but the output reminds the user that copy is empty until
`awf-generate-content` runs.

# Implementation status

✓ Functional. `scripts/create.py` uses `lib/templates.py` for template
discovery and overlay (preserve-list-aware). When no template is yet
present under `$AWF_HOME/templates/`, falls back to writing only
`passport.json` with a clear warning — useful for dev-time use of the
rest of the pipeline before a template lands.
