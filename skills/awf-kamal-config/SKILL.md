---
name: awf-kamal-config
description: Render config/deploy.yml from Infra state via KamalConfig.render() — run before awf-kamal-deploy, which deploys the rendered file. Records Infra.kamal.config_path. Idempotent — re-running with the same path is a no-op at the state-file level.
---

# Purpose

Renders `config/deploy.yml` (or a custom path) for the current project by
calling `KamalConfig(anchor, infra).render(path=...)`. The rendered YAML is
deterministic — same inputs produce byte-identical output.

Writes `Infra.kamal.config_path` on first creation or when the path changes.
Does not write state if the path is already recorded.

# Prerequisites

- A project with `.awf/project.json` (run `awf-migrate` first).
- `.awf/infra.json` with a populated `hetzner.servers[]` entry having
  `role="web"` (required by `KamalConfig`).
- `infra.registry.user` and `infra.registry.image` populated by
  `awf-hetzner-server` / `awf-neon-project` / `awf-shared-infra-get`.

# Inputs

| Flag | Default | Description |
|------|---------|-------------|
| `--path` | `config/deploy.yml` | Output path relative to project root. |
| `--json` | `false` | Emit machine-readable JSON on stdout. |

# Procedure

1. Run:
   ```
   uv run "$AWF_HOME/skills/awf-kamal-config/scripts/kamal_config.py" \
       [--path config/deploy.yml] [--json]
   ```
2. Report the output verbatim to the user.

# Errors handled

| Code | Meaning |
|------|---------|
| `0`  | Success — created, updated, or skipped (no-op) |
| `1`  | Project not found — no `.awf/project.json` walking up |
| `2`  | Credentials / config missing — no web server in infra |
| `4`  | State validation failure — `StateValidationError` from `.save()` |

# Idempotency

`KamalConfig.render()` is deterministic. The skill's idempotency is on
`Infra.kamal.config_path` only — the YAML file is always rewritten by the
lib when the path is given. `action="skip"` means the path key in infra.json
was unchanged; the YAML may still have been rewritten on disk.

# Manual gates

None. This skill is fully automated.
