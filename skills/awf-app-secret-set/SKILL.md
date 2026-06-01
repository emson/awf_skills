---
name: awf-app-secret-set
description: Upsert one KEY=value line in .kamal/secrets. Never logs the secret value. Idempotent — same key/value is a no-op.
---

# Purpose

Writes (or updates) one `KEY=value` entry in the project's `.kamal/secrets`
file. This file is read by Kamal at deploy time to inject runtime secrets.

The secret value is never written to the event log (defence-in-depth on top
of the logger's autoredact). The log event records only `key` and `source`
(`literal`, `env`, or `file`).

# Prerequisites

- A project with `.awf/project.json` (run `awf-migrate` first).
- The key must match `[A-Z][A-Z0-9_]*` (uppercase letters, digits, underscores).

# Inputs

Exactly one of `--value`, `--from-env`, or `--from-file` is required.

| Flag | Description |
|------|-------------|
| `--key KEY` | (required) Secret key name, e.g. `DATABASE_URL`. |
| `--value VALUE` | Literal secret value. |
| `--from-env VAR` | Read value from the named environment variable. |
| `--from-file PATH` | Read value from a file (stripped of trailing whitespace). |
| `--json` | Emit machine-readable JSON on stdout. |

# Procedure

1. Run:
   ```
   uv run "$AWF_HOME/skills/awf-app-secret-set/scripts/app_secret_set.py" \
       --key DATABASE_URL --value "postgres://..." [--json]
   ```
   Or via env var:
   ```
   uv run ... --key DATABASE_URL --from-env DATABASE_URL
   ```
2. Report the output verbatim to the user.

# Errors handled

| Code | Meaning |
|------|---------|
| `0`  | Success — created, updated, or skipped (no-op) |
| `1`  | Project not found — no `.awf/project.json` walking up |
| `2`  | Credentials / input error — `--from-env VAR` not set; `--from-file PATH` not found |

# Idempotency

Second invocation with the same key and value → `action="skip"`, zero
`file.write` events emitted, exit 0. If the value changes, the line is
replaced in-place and `action="updated"`.

# Manual gates

None. This skill is fully automated.
