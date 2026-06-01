---
name: awf-shared-infra-get
description: Provision the shared play Hetzner server and Neon project used across all S3 projects. Writes into ~/.config/awf/shared.json (Shared). Idempotent — re-running is a no-op if resources already exist.
---

# Purpose

Provisions user-scope shared infrastructure:
- A Hetzner Cloud VM (the "play" server) shared across all S3 projects.
- A Neon Postgres project (the "play" project) shared across all S3 projects.

Both are recorded in `~/.config/awf/shared.json`. This is an atomic skill
for user-scope resources: it does **not** read a project anchor (no
`.awf/project.json` required).

Delegates to:
- `lib.hetzner.HetznerClient.servers.get_or_create()` for the server.
- `lib.neon.NeonClient.projects.get_or_create()` for the Neon project.

Both lib calls are themselves idempotent (search-before-create).

# Prerequisites

- `HETZNER_API_TOKEN` in any layered config source.
- `NEON_API_KEY` in any layered config source.

No project anchor required — this skill is user-scope.

# Inputs

| Flag | Default | Description |
|------|---------|-------------|
| `--server-type` | `cx22` | Hetzner server type slug. |
| `--server-location` | `fsn1` | Hetzner location code. |
| `--neon-region` | `aws-eu-central-1` | Neon region ID. |
| `--play-hostname` | `awf-play` | Name given to the Hetzner play server. |
| `--play-neon-name` | `awf-play` | Name given to the Neon play project. |
| `--json` | `false` | Emit machine-readable JSON on stdout. |

# Procedure

1. Run:
   ```
   uv run "$AWF_HOME/skills/awf-shared-infra-get/scripts/shared_infra_get.py" \
       [--server-type cx22] [--play-hostname awf-play] [--json]
   ```
2. Report the output verbatim to the user.
3. The `play_server.ip` field in the JSON output is the value to pass as
   `--content` to `awf-cf-dns-record` for A-record creation.

# Errors handled

| Code | Meaning |
|------|---------|
| `0`  | Success — created, partial, or skipped (no-op) |
| `2`  | Credentials missing — `HETZNER_API_TOKEN` or `NEON_API_KEY` not in any config source |
| `3`  | Remote API error — `HetznerError` or `NeonError`; message surfaces on stderr |
| `4`  | State validation failure — `StateValidationError` from `.save()` (rare; indicates a bug) |

Note: exit code 1 (project not found) is not used by this skill because it
is user-scope and does not require a project anchor.

# Idempotency

- If both `play_server` and `play_neon_project_id` are already set in
  `shared.json`, the skill exits 0 with action `skip` and makes no API calls.
- If only one is set, exactly one provider is called; `shared.json` is
  updated with the new resource and action is `partial`.
- State is written atomically only after **both** lib calls succeed: if
  Hetzner succeeds but Neon fails, nothing is persisted and the next run
  finds the Hetzner server by name (idempotent `get_or_create`) and
  retries Neon.

# Manual gates

None. This skill is fully automated.
