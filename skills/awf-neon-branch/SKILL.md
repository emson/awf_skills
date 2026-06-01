---
name: awf-neon-branch
description: Provision one Neon Postgres branch on an existing project. Writes branch_id and branch_name into .awf/infra.json (Infra.neon). Idempotent — re-running with the same name is a no-op.
---

# Purpose

Provisions a single Neon branch on a Neon project and records its ID and
name in the project's `.awf/infra.json` under `neon.branch_id` and
`neon.branch_name`. This is an atomic skill: it owns exactly one resource
(the named branch) and exactly two state-file fields.

Delegates to `lib.neon.NeonClient.branches.get_or_create()` which handles
the search-before-create idempotency contract at the API layer.

**Dependency:** `awf-neon-project` must have run first so `neon.project_id`
is available in `infra.json`, or `--project-id` must be passed explicitly.

# Prerequisites

- A project with `.awf/project.json` (run `awf-migrate` first).
- A Neon project ID in `Infra.neon.project_id` (run `awf-neon-project` first),
  or pass `--project-id` explicitly.
- `NEON_API_KEY` in any layered config source.

# Inputs

| Flag | Default | Description |
|------|---------|-------------|
| `--name` | (required) | Branch name (unique within the Neon project). |
| `--project-id` | `Infra.neon.project_id` | Neon project ID; defaults to value in infra.json. |
| `--parent-id` | (none) | Parent branch ID to fork from; defaults to primary branch. |
| `--json` | `false` | Emit machine-readable JSON on stdout. |

# Procedure

1. Run:
   ```
   uv run "$AWF_HOME/skills/awf-neon-branch/scripts/neon_branch.py" \
       --name preview [--project-id <id>] [--parent-id <id>] [--json]
   ```
2. Report the output verbatim to the user.

# Errors handled

| Code | Meaning |
|------|---------|
| `0`  | Success — created or skipped (no-op) |
| `1`  | Project not found — no `.awf/project.json` walking up, or no Neon project_id available |
| `2`  | Credentials missing — `NEON_API_KEY` not in any layered config source |
| `3`  | Remote API error — `NeonError`; message surfaces on stderr |
| `4`  | State validation failure — `StateValidationError` from `.save()` (rare; indicates a bug) |

# Idempotency

Second invocation with the same `--name` on the same project returns the
existing branch, writes zero `state.change` events (action `skip`), and exits 0.

# Manual gates

None. This skill is fully automated.
