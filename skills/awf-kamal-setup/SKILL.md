---
name: awf-kamal-setup
description: Run `kamal setup` on first use of a server; skip if already done for this server ID.
---

# Purpose

Wraps `KamalRunner(cwd=anchor.path).setup(domain=..., server_ip=...)`.
The lib polls the A-record for `domain` before shelling out to `kamal setup`
(D-001 op rule #1: DNS-before-TLS). This cannot be bypassed by callers.

**Important:** This skill skips `kamal setup` if
`Shared.play_server.kamal_setup_done_for_server_id == Shared.play_server.hetzner_id`.
This prevents kamal-proxy from being restarted on a shared server when a new
app is deployed alongside existing ones. If the server is reprovisioned and
gets a new `hetzner_id`, `awf-shared-infra-get` updates `PlayServer.hetzner_id`
accordingly; the mismatch triggers setup automatically — no manual flag reset
required. If no `play_server` record exists in `Shared`, setup always runs.

On a *first* run this skill may take several minutes waiting for DNS to
propagate. On subsequent runs with the same server ID, the skill skips
immediately.

# Prerequisites

- A project with `.awf/project.json` (run `awf-migrate` first).
- `config/deploy.yml` rendered by `awf-kamal-config`.
- `kamal` binary on PATH (`gem install kamal`).
- The A-record for the domain must eventually point to `--server-ip`.

# Inputs

| Flag | Default | Description |
|------|---------|-------------|
| `--server-ip` | (required) | IP address kamal will SSH to. Read from `Shared.play_server.ip` or `Infra.hetzner.servers[].ip` by the composer. |
| `--domain` | anchor.domain | Domain whose A-record is polled before setup. |
| `--dns-timeout` | `600` | Seconds to wait for DNS propagation. |
| `--json` | `false` | Emit machine-readable JSON on stdout. |

# Procedure

1. Run:
   ```
   uv run "$AWF_HOME/skills/awf-kamal-setup/scripts/kamal_setup.py" \
       --server-ip 1.2.3.4 [--domain example.com] [--dns-timeout 600] [--json]
   ```
2. Report the output verbatim to the user.

# Errors handled

| Code | Meaning |
|------|---------|
| `0`  | Success — action is `"skip"` (already set up) or `"created"` (newly set up) |
| `1`  | Project not found — no `.awf/project.json` walking up |
| `2`  | Missing CLI — `kamal` binary not on PATH |
| `3`  | Remote / subprocess error — `KamalDnsTimeout` or `KamalSetupFailed` |

# Idempotency

Skips if `Shared.play_server.kamal_setup_done_for_server_id == Shared.play_server.hetzner_id`.
Automatically re-runs if the server is reprovisioned (new `hetzner_id`).
When setup runs, `kamal_setup_done_for_server_id` is updated to the current
`hetzner_id` in `~/.config/awf/shared.json`. Exit code 0 with `action="skip"`
means setup was already done for this server; no kamal-proxy restart occurred.

# Manual gates

None. This skill is fully automated (the DNS gate is enforced inside the lib).
