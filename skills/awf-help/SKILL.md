---
name: awf-help
description: Context-aware orientation for the awf-skills suite. Auto-detects fresh-start, in-project, or overview mode. Use this as the entry point from any blank project directory; reads only, runs anywhere.
---

# LLM directive

Use this as the entry point from any blank project directory; reads only,
runs anywhere. Never mutates state; never calls external APIs.

# Purpose

Context-aware orientation skill for the awf-skills suite. Auto-detects
which of three modes to render:

1. **Fresh-start mode** — no `.awf/project.json` found by walking up from
   cwd. One screen of orientation pointing at `/awf-create-project` or
   `/awf-launch`, with a hint that `/awf-help --overview` shows the full
   system.
2. **In-project mode** — `.awf/project.json` found. Prints the named
   composer for `stage+1`, the atomic skills relevant to the current
   stage, and a "common operations" footer.
3. **`--overview` mode** — explicit flag. Full catalogue grouped by stage,
   with links to `docs/07-multi-stage-architecture.md` and
   `docs/08-logging.md`.

# Prerequisites

None. Works before `awf-doctor`, before a project exists, from any directory.

# Flags

| Flag | Description |
|------|-------------|
| `--overview` | Full catalogue grouped by stage. |
| `--pipeline` | Deprecated alias for `--overview`; removed in plan_015. |
| `--json` | Machine-readable JSON output; includes `"schema_version": 1`. |

# Procedure

Run the script directly:

```
uv run "$AWF_HOME/skills/awf-help/scripts/help.py" [flags]
```

Or from the skill directory:

```
uv run scripts/help.py [flags]
```

Report the output verbatim to the user.

# Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Always (for any non-usage invocation). |
| `2`  | Argument parsing error (invalid flag combination). |

# Usage examples

```
# Auto-detect mode (fresh-start or in-project):
uv run scripts/help.py

# Full catalogue:
uv run scripts/help.py --overview

# Machine-readable in-project status:
uv run scripts/help.py --json

# Overview as JSON:
uv run scripts/help.py --overview --json
```

# Idempotency

Read-only. Safe to run any number of times.

# Failure modes

None — `--overview` works from any directory. In-project mode degrades to
fresh-start if no `.awf/project.json` is found.

# Manual gates

None.
