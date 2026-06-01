---
name: awf-status
description: >
  Report the current stage, drift, and next actions for this project.
  Run this first when location is uncertain.
---

# Purpose

Canonical "where am I" surface (D-007 / spec § C2). Emits a fixed-order
status block: `Project / Stage / Drift / Recent / Next`, followed by a
`Not yet checked:` footer that documents v1 scope gaps. Drift detection
compares local `.awf/` state files against live provider APIs
(Cloudflare zone, Hetzner servers, Neon project+branch) and names the
atomic skill that would re-converge each divergence.

**LLM directive:** Run this first when location is uncertain — before any
composer, when resuming a partially-completed pipeline, or any time you
need to know the current stage and what happened last.

# Prerequisites

- `.awf/project.json` present in cwd or an ancestor directory (S1+).
  If absent, emits `Stage: none` with a help suggestion; exits 0.
- Provider credentials in the layered config for live drift checks.
  Missing credentials degrade to `world_value="unknown"` in the drift
  output — the skill never errors on partial information.

# Flags

| Flag | Effect |
|------|--------|
| `--json` | Machine-readable JSON (schema in `lib/state.py:STATUS_JSON_SCHEMA`). |
| `--verbose` | Event tail 5→20 + per-provider state detail block. |

# Exit codes

| Code | Meaning |
|------|---------|
| `0` | Status produced (drift present or none; also the no-project case). |
| `4` | Argument validation failure (unknown flag). |

Note: a corrupt `.awf/project.json` (Pydantic ValidationError) propagates
as an unhandled exception with a traceback — local-state corruption is not
provider-side uncertainty and the traceback helps the operator debug it.

# Procedure

1. Run `uv run "$AWF_HOME/skills/awf-status/scripts/status.py" [flags]`.
2. Output is written to stdout. Parse `--json` output for programmatic use.

# Usage examples

```
# Human output (default)
> /awf-status

# Machine-readable JSON
> /awf-status --json

# Verbose: 20 events + per-provider state
> /awf-status --verbose

# Full detail in JSON form
> /awf-status --json --verbose
```

# Output format

```
Project: <slug> (<domain>)
Stage:   <stage>
Drift:   <none | first drift entry>
         <additional drift entries, one per line indented>
Recent:  <event 1 short form>
         <event 2..5 short form>
Next:    <composer> — <hint>
[Idle:   <warning if last session.end > 90 days ago>]

Not yet checked: dns_records, cloudflare_pages, fathom, gsc, hetzner_lb
```

The `Not yet checked:` footer is always present and tells callers which
drift checks are intentionally out of scope for v1 (not "passing" — just
not yet implemented).

# Drift checks (v1 scope)

| Provider | Resource | Drift detected | Re-converge skill |
|----------|----------|----------------|-------------------|
| Cloudflare | zone | Zone missing or zone_id mismatch | `awf-setup-domain` |
| Hetzner | servers | Server missing or not running | `awf-hetzner-provision` |
| Neon | project + branch | Project or branch missing | `awf-neon-provision` |

Hetzner and Neon checks are skipped when `.awf/infra.json` is absent
(S1/S2 — the resources don't exist yet by design).

# Idempotency

Pure read. Never mutates state files or provider resources.

# Manual gates

None.
