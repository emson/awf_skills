---
name: awf-init
description: First-time onboarding for awf-skills — create ~/.config/awf/.env from the template, prompt for missing credentials, ensure AWF_HOME is exported in the shell rc. Run once after install.sh, or any time after pulling new credentials. Idempotent.
---

# Purpose

Second-step onboarding (after `install.sh` has linked the skills).
Brings a fresh machine to "all green in `awf-doctor`" with the minimum
interaction. Re-running on a healthy environment is a no-op + report.

`install.sh` cannot do this work itself: it has to run before any
skill is discoverable, so it stays plain shell, no Claude. `awf-init`
is the *Claude-native* second step — it uses the same skill machinery
as everything else in the suite.

# Prerequisites

- `install.sh` has run (skills symlinked into `~/.claude/skills/`).
- `uv` available.

# Inputs

- `--non-interactive` — print what would be done, don't prompt.
- `--no-rc` — skip the shell-rc edit step.

# Procedure

1. Run `uv run "$AWF_HOME/skills/awf-init/scripts/init.py"`.
2. The script:
   - resolves `AWF_HOME`
   - creates `~/.config/awf/` if missing
   - if `~/.config/awf/.env` is absent, copies `.env.example` into it
   - if present, compares keys with the template and appends any new
     ones (with their comment header)
   - for each empty key, prompts the user; writes the answer back to
     the file. Empty answer = skip (left blank for `awf-doctor` to
     flag).
   - detects the user's shell from `$SHELL`, identifies the rc file
     (`~/.zshrc` / `~/.bashrc` / `~/.bash_profile`), checks whether
     `export AWF_HOME=...` is present; if not, offers to append it
     (one y/n confirmation, no silent rc edits).
   - warns if skills aren't symlinked into `~/.claude/skills/` (i.e.
     user skipped `install.sh`).
3. On success, suggest `awf-doctor` next.

# Idempotency

- `.env` already populated → no prompts, exits cleanly.
- New keys added to `.env.example` since last run → only the new keys
  are prompted for.
- AWF_HOME already exported in rc → skipped.
- Re-running is a green no-op.

# Failure modes

- `AWF_HOME` cannot be resolved (no env var, not in
  `~/.claude/awf-skills`, script can't realpath itself sensibly):
  print the install hint and exit 1.
- Shell rc file doesn't exist: warn and skip (we don't create rc
  files; that's the user's territory).
- `~/.config/awf/.env` exists but is unreadable: surface the OS
  error.

# Manual gates

The user types credential values at the prompt. Each prompt shows the
key name and any leading `#` comment from `.env.example` for context.
Values are echoed back as `***` (length only).
