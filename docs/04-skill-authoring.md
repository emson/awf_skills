# 04 — Skill Authoring

How to write or modify a skill in this suite. The conventions encode the
axioms — follow them and the skill will compose; ignore them and it
won't.

---

## Anatomy

A skill is a directory under `skills/`:

```
skills/awf-<verb>/
├── SKILL.md           ← required. YAML frontmatter + body.
├── scripts/           ← optional. uv-scripts invoked by the body.
│   └── <name>.py
└── README.md          ← optional. Notes for skill maintainers, not Claude.
```

`SKILL.md` is what Claude Code loads. Everything else is referenced from
it.

---

## SKILL.md template

```markdown
---
name: awf-<verb>
description: <one sentence — when this skill is appropriate, ending with
  the trigger conditions. Claude uses this to decide whether to invoke.>
---

# Purpose

One paragraph: what this skill does, what it does *not* do, and which
other skills it composes with.

# Prerequisites

- A project root (passport.json present in cwd or above), unless this
  skill creates one.
- Specific credentials: `CLOUDFLARE_API_KEY`, etc. Defer detail to
  `awf-doctor`.
- Specific CLIs: `wrangler`, `gh`, etc.

# Inputs

- `domain` (required, derived) — the canonical site domain.
- `<other args>` — describe each.

# Procedure

Numbered, imperative, addressed to Claude.

1. Verify prerequisites (or call out to `awf-doctor`).
2. Run the script: `uv run scripts/<name>.py "$DOMAIN"`.
3. Interpret exit codes / output.
4. Report to the user.

# Idempotency

Describe what happens on a second run with the same inputs:
- Already done → no-op + report.
- Partially done → repair what's missing.
- Conflicting state → error, do not auto-resolve.

# Failure modes

The errors the user is most likely to see, and what each means.

# Manual gates

If this skill includes an irreducibly human step, name it explicitly:
*"Open https://… in a browser, do X, return here and confirm."*
```

---

## Script template (uv inline metadata)

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "cloudflare>=3",
#   "python-dotenv",
# ]
# ///

"""One-line description for `awf-<verb>`."""

import os
import sys
from pathlib import Path

# Bootstrap: locate AWF_HOME and put lib/ on sys.path
AWF_HOME = Path(
    os.environ.get("AWF_HOME")
    or Path(__file__).resolve().parents[3]
)
sys.path.insert(0, str(AWF_HOME / "lib"))

from config import Config       # noqa: E402
from passport import Passport   # noqa: E402
from project import find_project_root  # noqa: E402
from slug import domain_to_project_name  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: <script> <domain>", file=sys.stderr)
        return 2

    domain = argv[1]
    cfg = Config.layered(project_root=find_project_root(optional=True))

    # … do the thing …
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

Rules:

1. **Single file.** No multi-file Python packages inside `scripts/`. If
   you reach for one, the logic belongs in `lib/`.
2. **Declare deps in the header.** Don't import what you didn't declare.
3. **Bootstrap is uniform.** Copy the bootstrap block verbatim. It is
   the single point where path resolution can fail.
4. **Return integer exit codes.** `0` success, `1` runtime failure,
   `2` usage error, `3` manual gate required.
5. **Print human output to stdout, errors to stderr.** Claude reads
   both; the user reads stdout.
6. **No `argparse` for one-or-two-arg scripts.** Keep it tight.
   `argparse` is fine when there are flags.

---

## When to extend `lib/` vs keep it in the skill

In the skill's `scripts/` if:
- The logic is single-skill-specific.
- It is < ~80 LOC.

In `lib/` if:
- Two skills already use it.
- It is contract-shaped (passport schema, slug derivation, credential
  resolution, project location).

Don't pre-extract. Wait for the second use.

---

## What goes in the SKILL.md *body* vs in scripts

Body (Markdown, read by Claude):
- *When* this skill is appropriate.
- *Which* command to run.
- *How* to interpret success/failure.
- *What* manual steps are required and how to wait for them.

Scripts (Python, executed by the runtime):
- API calls.
- File mutations.
- Validation.
- Anything with branches that need to be tested.

If you find yourself writing branchy logic in Markdown ("if Cloudflare
returned 409, then …"), move it into the script and have the script exit
with a code Claude can dispatch on.

---

## Naming

- Skill names: `awf-<verb>` or `awf-<verb>-<noun>`.
  - `awf-deploy`, `awf-setup-domain`, `awf-verify-gsc`. Not
    `awf-deployer`, not `awf-domain-setup`.
- Script names: match the skill verb. `scripts/setup_domain.py` for
  `awf-setup-domain`. Not `main.py`, not `run.sh`.

---

## Testing a skill

Two layers:

1. **Library tests** (`tests/lib/`) — pytest, fast, no network. Cover
   `passport`, `slug`, `config`, `project`.
2. **Skill tests** (`tests/skills/<name>/`) — fixture-based. Each
   fixture is a temp project dir + a fake env. Asserts the script
   produces the right `passport.json` mutation, the right exit code,
   and the right stdout shape. Network calls are stubbed via `respx`
   (httpx) or `responses` (requests).

Skills that *only* talk to the network (e.g. `awf-setup-domain`) are
tested with stubs at the HTTP layer, not at the Cloudflare SDK layer —
the SDK is the part most likely to change.

---

## Adding a new credential

1. Add the variable to `.env.example` with a comment.
2. Add it to `awf-doctor/scripts/check.py`'s required-vars table, in
   the right group.
3. Add a `Config.<name>` property (or document it as a free key) in
   `lib/config.py`.
4. Update [`05-credentials.md`](05-credentials.md).

That ordering means a missing credential is caught by `awf-doctor`
before any skill that uses it tries to run.

---

## Adding a new skill — checklist

- [ ] Decide it's one verb (A4). If not, split it.
- [ ] Decide whether it mutates remote state. If yes, it must be
  idempotent (A5) and surface upstream errors unchanged (A13).
- [ ] Decide whether it requires a project root, and document
  (Prerequisites).
- [ ] Decide whether it has manual gates, and document them (A8).
- [ ] Write `SKILL.md` from the template above.
- [ ] If it needs a script, write it from the script template above.
- [ ] Add to `install.sh`'s symlink list (or rely on the glob if you
  used `awf-*` naming).
- [ ] Update the catalogue in [`00-plan.md` §5](00-plan.md).
- [ ] Add tests.
