---
name: awf-doctor
description: Validate (read-only) an existing awf-skills runtime — required CLIs, credentials, OAuth tokens, git/npm hygiene. Diagnoses; does not create config — awf-init does first-time setup. Run before any awf-* skill that mutates remote state, or whenever an awf-* skill fails with a credential or environment error.
---

# LLM directive

Run this before any skill that touches a remote API, or whenever an awf-*
skill fails with a credential or environment error. Reads only; never mutates.

# Purpose

Pre-flight check for the entire awf-skills suite. Reports which CLIs are
present, which credentials are set (and from which `.env` layer), and
whether the OAuth-bound services (Google, `wrangler`, `gh`) are
authenticated.

Scoping flags let you check only the subsystems relevant to a particular
stage or skill. Recent-error surfacing automatically checks the subsystem
that last generated a credential-shaped error first.

# Prerequisites

None. `awf-doctor` runs anywhere, including before any project exists.

# Flags

| Flag | Description |
|------|-------------|
| `--json` | Emit machine-readable JSON instead of the human table. |
| `--for-stage NAME` | Check only subsystems needed at the named stage. |
| `--for-skill NAME` | Check only subsystems needed by the named skill. |

`--for-stage` and `--for-skill` are mutually exclusive.

# Procedure

1. Run `uv run "$AWF_HOME/skills/awf-doctor/scripts/check.py"` (or
   `uv run scripts/check.py` from the skill directory), passing any flags.
2. Report the output verbatim to the user.
3. If any **required** check fails, tell the user exactly which env var
   to set or which CLI to install. The script's stderr already includes
   actionable hints.

# Exit codes

| Code | Meaning |
|------|---------|
| `0`  | All required checks in scope pass (warnings allowed). |
| `1`  | At least one required check in scope failed. |
| `2`  | Usage error (unknown flag, unknown stage/skill name, mutual exclusion). |

# Usage examples

```
# Default: full sweep.
uv run scripts/check.py

# Machine-readable full sweep.
uv run scripts/check.py --json

# Check only stage landing subsystems.
uv run scripts/check.py --for-stage landing

# Check only awf-kamal-deploy subsystems.
uv run scripts/check.py --for-skill awf-kamal-deploy

# Scoped check with JSON output.
uv run scripts/check.py --for-stage mvp-play --json

# Skill with no preflight (exits 0 immediately with a note):
uv run scripts/check.py --for-skill awf-app-secret-set
```

# Idempotency

Pure read; running it ten times in a row is identical to running once.

# Failure modes

- **`AwfHomeNotFound`** — `$AWF_HOME` not set and the script can't
  realpath itself to a recognisable repo. Tell the user to either set
  `AWF_HOME=/path/to/awf_skills` or to run `./install.sh` from the repo root.
- **Missing CLI** — print the install hint the script suggests.
- **Expired Google `token.json`** — instruct the user to delete it and
  re-run `awf-setup-gsc` (which kicks off the OAuth flow).
- **`wrangler whoami` fails** — instruct `wrangler login`.
- **Unknown stage/skill name** — exits 2 with a list of valid names.

# Manual gates

None.
