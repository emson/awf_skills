---
name: awf-hetzner-server
description: Provision one Hetzner Cloud server for a project. Writes the server ID/IP into .awf/infra.json (Infra.hetzner.servers[]). Idempotent — re-running with the same inputs is a no-op.
---

# Purpose

Provisions a single Hetzner Cloud VM and records it in the project's
`.awf/infra.json` under `hetzner.servers[]`. This is an atomic skill: it
owns exactly one resource (the named server) and exactly one state-file
block (the servers list entry).

Delegates to `lib.hetzner.HetznerClient.servers.get_or_create()` which
handles the search-before-create idempotency contract at the API layer.

# Prerequisites

- A project with `.awf/project.json` (run `awf-migrate` first).
- `HETZNER_API_TOKEN` in any layered config source.

# Inputs

| Flag | Default | Description |
|------|---------|-------------|
| `--name` | (required) | Server name — unique within your Hetzner project. |
| `--type` | `cx22` | Hetzner server type slug (e.g. `cx22`, `cx32`, `cpx21`). |
| `--location` | `fsn1` | Hetzner location code. |
| `--role` | `app` | Role label written into `Infra.hetzner.servers[].role`. |
| `--shared` | `false` | Boolean flag — marks server as shared across projects. |
| `--ssh-key` | (none) | SSH key name(s) to attach; repeat for multiple. |
| `--json` | `false` | Emit machine-readable JSON on stdout. |

# Procedure

1. Run:
   ```
   uv run "$AWF_HOME/skills/awf-hetzner-server/scripts/hetzner_server.py" \
       --name my-server [--type cx22] [--location fsn1] [--role app] \
       [--ssh-key mykey] [--json]
   ```
2. Report the output verbatim to the user.

# Errors handled

| Code | Meaning |
|------|---------|
| `0`  | Success — created, updated, or skipped (no-op) |
| `1`  | Project not found — no `.awf/project.json` walking up |
| `2`  | Credentials missing — `HETZNER_API_TOKEN` not in any layered config source |
| `3`  | Remote API error — `HetznerError`; message surfaces on stderr |
| `4`  | State validation failure — `StateValidationError` from `.save()` (rare; indicates a bug) |

# Idempotency

Second invocation with the same `--name` returns the existing server, writes
zero `state.change` events (action `skip`), and exits 0. The Hetzner API is
only called once (GET to check existence).

# Manual gates

None. This skill is fully automated.
