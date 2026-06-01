---
name: awf-kamal-setup
description: Run `kamal setup` after polling DNS propagation. Always invokes kamal (no setup_done gate). May take minutes on first run while waiting for DNS propagation.
---

# Purpose

Wraps `KamalRunner(cwd=anchor.path).setup(domain=..., server_ip=...)`.
The lib polls the A-record for `domain` before shelling out to `kamal setup`
(D-001 op rule #1: DNS-before-TLS). This cannot be bypassed by callers.

**Important:** This skill always invokes `kamal setup` — there is no skip
path. `action` is always `"created"` on success regardless of whether kamal
actually performed work. This is intentional: kamal setup is idempotent
(re-running on a configured server is a no-op for kamal), and we do not
maintain a `setup_done` gate because that gate could drift from reality if
the server is reprovisioned.

On a *first* run this skill may take several minutes waiting for DNS to
propagate. On subsequent runs, DNS is already resolved and the skill
completes in seconds.

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
| `0`  | Success — kamal setup completed |
| `1`  | Project not found — no `.awf/project.json` walking up |
| `2`  | Missing CLI — `kamal` binary not on PATH |
| `3`  | Remote / subprocess error — `KamalDnsTimeout` or `KamalSetupFailed` |

# Idempotency

`kamal setup` is itself idempotent. The skill always invokes it. `action` is
always `"created"` on success. No `state.change` event is emitted because
this skill has no Infra state footprint.

# Manual gates

None. This skill is fully automated (the DNS gate is enforced inside the lib).
