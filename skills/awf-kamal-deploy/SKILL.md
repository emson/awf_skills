---
name: awf-kamal-deploy
description: Run `kamal deploy` and record Infra.kamal.last_deploy_image on success. Always invokes kamal; action reflects state-file delta only.
---

# Purpose

Wraps `KamalRunner(cwd=anchor.path).deploy()`. On success, records
`Infra.registry.image` as `Infra.kamal.last_deploy_image` so the composer
(plan_010) can detect whether the image changed between runs.

Kamal re-rolls containers on every `deploy` — the skill always invokes it.
`action="skip"` means `Infra.registry.image` was already recorded as the
last-deployed image (i.e. the state-file was not changed), not that kamal
did nothing.

# Prerequisites

- A project with `.awf/project.json` (run `awf-migrate` first).
- `config/deploy.yml` rendered by `awf-kamal-config` (run immediately before
  this skill). **Hard pre-condition:** `Infra.registry.image` must match
  what is currently in `config/deploy.yml`. If the composer has bumped
  `Infra.registry.image` without re-running `awf-kamal-config`, the recorded
  `last_deploy_image` will not reflect the actually-deployed image.
- `kamal` binary on PATH (`gem install kamal`).

# Inputs

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | `false` | Emit machine-readable JSON on stdout. |

The deploy is fully driven by `config/deploy.yml`. To change the image tag,
bump `Infra.registry.image` and re-run `awf-kamal-config` first.

# Procedure

1. Run:
   ```
   uv run "$AWF_HOME/skills/awf-kamal-deploy/scripts/kamal_deploy.py" [--json]
   ```
2. Report the output verbatim to the user.

# Errors handled

| Code | Meaning |
|------|---------|
| `0`  | Success — deployed (created, updated, or skipped) |
| `1`  | Project not found — no `.awf/project.json` walking up |
| `2`  | Missing CLI — `kamal` binary not on PATH |
| `3`  | Remote / subprocess error — `KamalDeployFailed` |
| `4`  | State validation failure — `StateValidationError` from `.save()` |

# Idempotency

The comparison is at `Infra.registry.image` level. Kamal always re-deploys;
`action="skip"` only means the same image was already recorded as last-deployed.

# Manual gates

None. This skill is fully automated.
