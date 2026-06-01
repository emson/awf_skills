---
name: awf-neon-project
description: Provision one Neon Postgres project for a project. Writes the Neon project ID into .awf/infra.json (Infra.neon.project_id). Idempotent — re-running with the same name is a no-op.
---

# Purpose

Provisions a single Neon Postgres project and records its ID in the
project's `.awf/infra.json` under `neon.project_id`. This is an atomic
skill: it owns exactly one resource (the named Neon project) and exactly
one state-file field.

Delegates to `lib.neon.NeonClient.projects.get_or_create()` which handles
the search-before-create idempotency contract at the API layer.

# Prerequisites

- A project with `.awf/project.json` (run `awf-migrate` first).
- `NEON_API_KEY` in any layered config source.

# Inputs

| Flag | Default | Description |
|------|---------|-------------|
| `--name` | (required) | Neon project name (unique within your Neon account). |
| `--region` | `aws-eu-central-1` | Neon region ID. |
| `--pg-version` | `16` | Postgres major version. |
| `--json` | `false` | Emit machine-readable JSON on stdout. |

# Procedure

1. Run:
   ```
   uv run "$AWF_HOME/skills/awf-neon-project/scripts/neon_project.py" \
       --name my-project [--region aws-eu-central-1] [--pg-version 16] [--json]
   ```
2. Report the output verbatim to the user.

# Errors handled

| Code | Meaning |
|------|---------|
| `0`  | Success — created, updated, or skipped (no-op) |
| `1`  | Project not found — no `.awf/project.json` walking up |
| `2`  | Credentials missing — `NEON_API_KEY` not in any layered config source |
| `3`  | Remote API error — `NeonError`; message surfaces on stderr |
| `4`  | State validation failure — `StateValidationError` from `.save()` (rare; indicates a bug) |

# Idempotency

Second invocation with the same `--name` returns the existing Neon project,
writes zero `state.change` events (action `skip`), and exits 0.

# Manual gates

None. This skill is fully automated.
