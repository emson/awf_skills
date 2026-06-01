---
name: awf-app-dockerize
description: Scaffold Dockerfile, .dockerignore, /up healthcheck route, and lib/db.ts into the project tree. Idempotent — existing files with matching content are skipped; user edits are never clobbered.
---

# Purpose

Writes four files into a SvelteKit project to make it deployable via Kamal:

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage node image (node:<version>-slim, EXPOSE <port>, `CMD ["node", "build"]`) |
| `.dockerignore` | Exclude node_modules, .git, .svelte-kit, build, .env* |
| `src/routes/up/+server.ts` | SvelteKit healthcheck route returning `200 OK` |
| `lib/db.ts` | Exports a `pg.Pool` reading `DATABASE_URL` |

Template content is versioned as `DOCKERIZE_VERSION = "1"`. Idempotency
compares on-disk content to the template constant — no remote calls, no
state-file writes.

# Prerequisites

- A project with `.awf/project.json` (run `awf-migrate` first).
- SvelteKit project with `src/routes/` directory convention.

# Inputs

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `3000` | Port interpolated into `EXPOSE` and CMD listen address. |
| `--node-version` | `20` | Node.js version for `FROM node:<version>-slim`. |
| `--json` | `false` | Emit machine-readable JSON on stdout. |

# Procedure

1. Run:
   ```
   uv run "$AWF_HOME/skills/awf-app-dockerize/scripts/app_dockerize.py" \
       [--port 3000] [--node-version 20] [--json]
   ```
2. Report the output verbatim to the user.

# Errors handled

| Code | Meaning |
|------|---------|
| `0`  | Success — created, or skipped (no-op) |
| `1`  | Project not found — no `.awf/project.json` walking up |

# Idempotency

Second invocation with identical args and unchanged files → zero `file.write`
events emitted, exit 0, `action="skip"`. If any of the four files exists
with content that differs from the template (user edit), the file is left
untouched and its path appears in the `drift` list; `action` is still `"skip"`.

# Manual gates

None. This skill is fully automated.
