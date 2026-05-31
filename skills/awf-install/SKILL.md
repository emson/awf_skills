---
name: awf-install
description: Install npm dependencies for the current project. Run after awf-review-passport, before awf-deploy.
---

# Purpose

Thin wrapper around `npm install` in the project root. Exists as its
own skill so `awf-launch` can sequence it explicitly and so the user
gets a consistent error shape on failure.

# Prerequisites

- A project root.
- `node`, `npm`.

# Procedure

1. `cd` to project root.
2. `npm install` (stream output to the user — installs are slow).
3. Report the install size and any deprecation warnings.

# Idempotency

`npm install` is idempotent against `package-lock.json`.

# Failure modes

- Missing `package.json` — project wasn't scaffolded; tell the user.
- Node version too old — print the version constraint from
  `package.json#engines` if present.

# Implementation status

✓ Functional. `scripts/install.py` runs `npm install` in the project
root with output streamed live (no capture).
