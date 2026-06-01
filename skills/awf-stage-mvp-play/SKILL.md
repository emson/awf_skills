---
name: awf-stage-mvp-play
description: Promote a project from any prior stage to `stage="mvp-play"` by chaining the ten S3 atomic skills in dependency order. This is the first composer skill and the proof of architecture for Phase B. Idempotent — re-running resumes where a partial run left off.
---

# Purpose

Composer skill that promotes a project to `mvp-play` by sequencing:
1. `awf-shared-infra-get` — shared play server + Neon project
2. `awf-app-dockerize` — Dockerfile, .dockerignore, healthcheck, db lib
3. `awf-neon-branch` — branch on shared Neon project
4. `awf-app-secret-set` — DATABASE_URL into .kamal/secrets
5. `awf-kamal-config` — render config/deploy.yml
6. `awf-cf-dns-record` — A record → play server IP (grey cloud)
7. `awf-kamal-setup` — kamal setup (polls DNS internally)
8. `awf-kamal-deploy` — kamal deploy

On success, advances the project anchor: `stage="mvp-play"`,
`has.infra=true`, `has.kamal=true`.

On any mid-run failure, the anchor is **not** advanced. Partial state
in `.awf/infra.json` and `~/.config/awf/shared.json` reflects exactly
what was created. Re-run after fixing the cause resumes idempotently
(atomic skills return `action="skip"` for completed work).

The whole run is wrapped in one `log.session(composer="awf-stage-mvp-play",
target="mvp-play")`. Atomic skills open their own peer sessions; sessions
are flat (no nesting), per D-002 op rule 3.

# Prerequisites

- `.awf/project.json` exists in any ancestor of the working directory.
- A Cloudflare zone for the domain exists (run `awf-setup-domain` first).
- All credentials present: `HETZNER_API_TOKEN`, `NEON_API_KEY`,
  `CLOUDFLARE_EMAIL`, `CLOUDFLARE_API_KEY`, `CLOUDFLARE_ACCOUNT_ID`.
- `kamal` binary on PATH (`gem install kamal`).
- Run `awf-doctor` to validate before the first launch.

# Inputs

| Flag | Default | Description |
|------|---------|-------------|
| `--dry-run` | `false` | Print the 8-step plan + resolved args; no subprocesses; exits 0. |
| `--play-hostname` | `play.awfship.dev` | Passed to `awf-shared-infra-get`. |
| `--port` | `3000` | Passed to `awf-app-dockerize`. |
| `--node-version` | `20` | Passed to `awf-app-dockerize`. |
| `--json` | `false` | Emit machine-readable JSON summary on stdout (all step results). |

# Procedure

Run from any directory inside the project:
```
uv run "$AWF_HOME/skills/awf-stage-mvp-play/scripts/stage_mvp_play.py" \
    [--dry-run] [--play-hostname play.awfship.dev] \
    [--port 3000] [--node-version 20] [--json]
```

Or let Claude Code invoke it directly.

# Exit codes

| Code | Meaning |
|------|---------|
| `0` | All 8 steps succeeded; anchor advanced to `mvp-play` |
| `1` | No `.awf/project.json` found walking up |
| `2` | An atomic skill exited 2 (credentials / CLI missing) |
| `3` | An atomic skill exited 3 (remote / subprocess error) |
| `4` | Missing prerequisite state (e.g., `registry.host` not set — run `awf-doctor --for-stage mvp-play`) or anchor `.save()` failed |
| `5` | **Gate hit** — DNS propagation timeout; wait and re-run |

Exit 5 is new in this composer. `awf-kamal-setup` emits exit 3 with
`"gate":"dns_propagation"` in its JSON payload; the composer re-classifies
this as exit 5 so the operator knows to wait (not fix) before re-running.

# Idempotency

Each atomic skill is called with `--json` so its `action` field is
parseable. `needed()` predicates in the composer skip only steps that
cannot be attempted (e.g., `neon_branch` requires `Shared.play_neon_project_id`).
All other steps are always invoked and rely on the atomic skill's own
idempotency contract to return `action="skip"` when nothing changed.

State files are re-read from disk between steps so each `needed()` sees
the freshest view without requiring the composer to model cross-skill
data flow.

# Manual gates

One: DNS propagation (exit 5). Wait for the A record to propagate
(typically < 5 minutes when using Cloudflare grey cloud), then re-run.

# LLM directive

Run this first when the project has a Cloudflare zone but no deployed
application. Check `awf-status` if you are unsure whether this
composer has already been run successfully.
