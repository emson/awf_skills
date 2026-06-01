---
name: awf-cf-dns-record
description: Create one Cloudflare DNS record on a zone. Writes the record ID into passport.json (cloudflare["<TYPE>:<name>"]). Idempotent — re-running with the same inputs is a no-op.
---

# Purpose

Creates a single Cloudflare DNS record and records its ID in
`passport.json` under `cloudflare["<TYPE>:<name>"]` for resumability.
This is an atomic skill: it owns exactly one resource (the named record)
and exactly one passport field.

Delegates to `lib.cf.dns.create_dns_record()` which handles the
search-before-create idempotency contract at the API layer.

# Prerequisites

- A project with `.awf/project.json` and `passport.json`.
- A Cloudflare zone for the domain (run `awf-setup-domain` first).
- `CLOUDFLARE_EMAIL`, `CLOUDFLARE_API_KEY`, `CLOUDFLARE_ACCOUNT_ID` in
  any layered config source.

# Inputs

| Flag | Default | Description |
|------|---------|-------------|
| `--type` | (required) | DNS record type: `A`, `AAAA`, `CNAME`, `TXT`. |
| `--name` | (required) | Subdomain (`@`, `www`, or full FQDN). |
| `--content` | (required) | Record value. For A records, typically the play server IP from `awf-shared-infra-get` output (`play_server.ip`). |
| `--proxied` / `--no-proxied` | proxied for A/AAAA/CNAME; no-proxy for TXT | Whether to enable Cloudflare proxy. |
| `--domain` | `ProjectAnchor.domain` | Override domain (default from project anchor). |
| `--json` | `false` | Emit machine-readable JSON on stdout. |

# Procedure

1. Run:
   ```
   uv run "$AWF_HOME/skills/awf-cf-dns-record/scripts/cf_dns_record.py" \
       --type A --name api --content 1.2.3.4 [--proxied] [--domain example.com] [--json]
   ```
2. Report the output verbatim to the user.

# Errors handled

| Code | Meaning |
|------|---------|
| `0`  | Success — created or skipped (no-op) |
| `1`  | Project not found — no `.awf/project.json` walking up |
| `2`  | Credentials missing — Cloudflare credentials not in any layered config source |
| `3`  | Remote API error — `CloudflareError` or zone not found; message surfaces on stderr |
| `4`  | State validation failure — `StateValidationError` from `.save()` (rare; indicates a bug) |

# Idempotency

Second invocation with the same `--type`/`--name`/`--content` finds the
existing record, compares the record ID against the passport cache, and
exits 0 with action `skip`. Zero `state.change` events are emitted.

# Manual gates

None. This skill is fully automated.
