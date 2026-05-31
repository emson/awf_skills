---
name: awf-update-template
description: Re-overlay a newer template version onto an existing project, preserving content per the template's preserve-list. Use when a new templates/landing-page-vN/ ships and you want existing sites to inherit improvements.
---

# Purpose

Templates evolve (A10). This skill brings an existing project up to a
newer template without losing the content stored under the template's
`preserve-list.txt`.

# Prerequisites

- A project root.
- A target template under `$AWF_HOME/templates/<name>/` newer than the
  project's current `template_version`.

# Inputs

- `--to <template-name>` (optional, default: latest).
- `--dry-run` — show the diff without writing.

# Procedure

1. Read project's current `template_version` from `passport.json`.
2. Resolve target template; refuse if target ≤ current (use `--force`
   to allow downgrade).
3. Compute the file set: target template's files MINUS the
   preserve-list.
4. Show a unified diff of what would change. Stop here on
   `--dry-run`.
5. Apply, bump `passport.template_version`, commit (if a git repo)
   with a message naming both versions.

# Idempotency

Re-running with the same target is a no-op (no diff). Re-running with
a newer target advances.

# Failure modes

- Target template's `template.json` declares a passport schema newer
  than the project's: refuse and tell the user to update awf-skills
  first.

# Implementation status

✓ Functional. `scripts/update.py` uses `lib/templates.py` for the
overlay, with `--dry-run`, semver comparison, schema-compatibility
checking against `Passport.SCHEMA_VERSION_CURRENT`, and a best-effort
git commit on success.
