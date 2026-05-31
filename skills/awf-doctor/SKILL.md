---
name: awf-doctor
description: Validate the awf-skills runtime — required CLIs, credentials, OAuth tokens, git/npm hygiene. Run before any awf-* skill that mutates remote state, or whenever an awf-* skill fails with a credential or environment error.
---

# Purpose

Pre-flight check for the entire awf-skills suite. Reports which CLIs are
present, which credentials are set (and from which `.env` layer), and
whether the OAuth-bound services (Google, `wrangler`, `gh`) are
authenticated. Reads only; mutates nothing.

This is the source of truth for "what does awf-skills need to run". If
a check is missing here, no other skill should re-invent it.

# Prerequisites

None. `awf-doctor` runs anywhere, including before any project exists.

# Inputs

Optional flag:

- `--json` — emit machine-readable JSON instead of the human table.

# Procedure

1. Run `uv run "$AWF_HOME/skills/awf-doctor/scripts/check.py"`
   (or `uv run scripts/check.py` if invoked from the skill's directory).
2. Report the output verbatim to the user. The script formats a
   green/red table per category.
3. If any **required** check fails, tell the user exactly which env var
   to set or which CLI to install. The script's stderr already includes
   actionable hints.
4. Exit codes:
   - `0` — all required checks pass (warnings allowed).
   - `1` — at least one required check failed.
   - `2` — usage error.

# Idempotency

Pure read; running it ten times in a row is identical to running once.

# Failure modes

- **`AwfHomeNotFound`** — `$AWF_HOME` not set and the script can't
  realpath itself to a recognisable repo. Tell the user to either set
  `AWF_HOME=/path/to/awf_skills` or to run `./install.sh` from the
  repo root.
- **Missing CLI** — print the install hint (`brew install`, `npm i -g`,
  etc.) the script suggests.
- **Expired Google `token.json`** — instruct the user to delete it and
  re-run `awf-setup-gsc` (which kicks off the OAuth flow).
- **`wrangler whoami` fails** — instruct `wrangler login`.

# Manual gates

None.
