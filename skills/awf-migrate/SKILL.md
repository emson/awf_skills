---
name: awf-migrate
description: Explicit one-shot upgrade for legacy projects (passport-only) to the dual-file layout (passport.json + .awf/project.json). Run once per project after pulling awf-skills v0.1+. Idempotent — safe to run on already-migrated projects.
---

# Purpose

Explicit one-shot upgrade for legacy projects (passport-only) to the dual-file
layout (`passport.json` + `.awf/project.json`) introduced in awf-skills v0.1.
A legacy project has only `passport.json`; a migrated project has both files.
Running this skill creates `.awf/project.json` (the ProjectAnchor) from the
existing `passport.json`. The anchor is the canonical source of truth for the
project stage, domain, and slug from S1 onward.

Delegates entirely to `lib.project.ensure_anchor()` — all schema validation,
ULID timestamps, and `state.change` log events happen inside that library call.

# Prerequisites

A directory containing `passport.json` (legacy) or `.awf/project.json`
(already migrated). The skill walks up from cwd to find the project root;
you do not need to be in the exact project directory.

No credentials required. No network calls.

# Inputs

Optional flag:

- `--json` — emit machine-readable JSON on stdout instead of the human message.
  Output fields: `action` (`"migrated"` or `"no-op"`), `anchor_path`,
  `domain`, `slug`.

# Procedure

1. Run:
   ```
   uv run "$AWF_HOME/skills/awf-migrate/scripts/migrate.py"
   ```
   (or with `--json` for structured output).
2. Report the output verbatim to the user.
3. Exit-code table:

   | Code | Meaning |
   |------|---------|
   | `0`  | Success — migrated or already migrated (no-op) |
   | `1`  | Project not found — no `passport.json` or `.awf/project.json` walking up from cwd |
   | `2`  | I/O failure during migration (e.g., read-only filesystem, malformed `passport.json`) |

# Idempotency

Running on an already-migrated project is a no-op: prints
`already migrated: <path>/.awf/project.json`, exits 0, and emits no
`state.change` events. Only one `session.start` + `session.end` pair is
written to `.awf/log.jsonl`.

# Failure modes

- **Project not found** (exit 1): no `passport.json` or `.awf/project.json`
  found walking up from cwd. Tell the user to `cd` into their project
  directory or run `awf-create-project` first.
- **I/O failure** (exit 2): filesystem is read-only, `passport.json` fails
  schema validation, or another unexpected error during `ensure_anchor`.
  The underlying exception message is printed to stderr.

# Manual gates

None. This skill is fully automated.
