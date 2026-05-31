# 02 — Architecture

How the suite is laid out on disk, how it gets installed, how a skill
locates the things it needs at runtime, and how shared code is shared.

---

## Repo layout

```
awf_skills/
├── README.md
├── .env.example                  ← documents every credential the suite uses
├── .gitignore
├── install.sh                    ← symlinks skills into ~/.claude/skills/
│
├── docs/
│   ├── 00-plan.md
│   ├── 01-principles.md
│   ├── 02-architecture.md        ← this file
│   ├── 03-passport-contract.md
│   ├── 04-skill-authoring.md
│   └── 05-credentials.md
│
├── lib/                          ← shared Python, imported by uv-scripts
│   ├── __init__.py
│   ├── awf_home.py               ← locate the skills repo at runtime
│   ├── config.py                 ← layered .env loader (A6)
│   ├── project.py                ← walk up from cwd to find passport.json
│   ├── slug.py                   ← domain → slug (A12)
│   └── passport.py               ← Pydantic schema, read/write, migrations
│
├── skills/                       ← one dir per skill
│   ├── awf-doctor/
│   │   ├── SKILL.md
│   │   └── scripts/check.py
│   ├── awf-status/
│   │   ├── SKILL.md
│   │   └── scripts/status.py
│   ├── awf-launch/SKILL.md       ← orchestrator
│   ├── awf-create-project/…
│   ├── awf-setup-domain/…
│   ├── awf-setup-nameservers/…
│   ├── awf-setup-analytics/…
│   ├── awf-generate-content/…    ← Claude-native, no script
│   ├── awf-review-passport/…
│   ├── awf-install/…
│   ├── awf-deploy/…
│   ├── awf-setup-gsc/…
│   ├── awf-verify-gsc/…
│   ├── awf-submit-bing/…
│   └── awf-update-template/…
│
├── templates/                    ← versioned site templates
│   ├── README.md
│   └── landing-page-v1/          ← (added when template lands)
│       ├── template.json         ← declares required passport schema
│       ├── preserve-list.txt     ← paths kept on update
│       └── …                     ← Svelte template files
│
└── tests/
```

### Why this shape

- `docs/` is upstream of code. Anyone reading the repo cold should hit
  `docs/00-plan.md` first, not a file tree.
- `lib/` is flat, not packaged. Modules are small and focused; importing
  `from lib.passport import Passport` is enough. No nested namespaces.
- `skills/` mirrors the install target (`~/.claude/skills/`) one-for-one.
  Each skill is a single dir with everything that skill needs (its
  `SKILL.md`, its scripts). Easy to copy, easy to delete, easy to publish
  individually if we ever do.
- `templates/` is sibling to `skills/`, not nested. Templates and skills
  evolve at different cadences (A10) — co-located is fine, nested would
  imply ownership.

---

## Install model

Cloning the repo is *not* the install. Skills must appear in
`~/.claude/skills/` (or in a path Claude Code looks at) for Claude to
discover them.

`install.sh` does two things:

1. Symlinks each `skills/awf-*/` into `~/.claude/skills/`.
2. Writes a one-line export of `AWF_HOME=<absolute path>` into the user's
   shell rc (with confirmation), and exports it for the current shell.

Symlinks (rather than copies) mean a `git pull` instantly updates skills.
Per-skill symlinks (rather than one big symlink) mean users can disable
individual skills by `rm`-ing the link.

Uninstall is `./install.sh --uninstall`: removes the symlinks, leaves
`AWF_HOME` and the rc export for the user to delete (avoid touching
shell rc on uninstall).

### What about Claude Code plugin packaging?

Plugins are a more polished distribution format with manifest, versioning,
and discoverability. We don't depend on them initially:

- The plugin format may evolve; locking in early couples release cadence.
- Symlink-install is portable to anyone running Claude Code today.
- Wrapping the suite as a plugin later is a packaging change, not an
  architecture change.

When the plugin format stabilises, add a `.claude-plugin/plugin.json`
manifest at repo root. The skill dirs already match the layout plugins
expect.

---

## Runtime resolution

Three pieces of "where am I?" logic, each with one clear strategy.

### `AWF_HOME` — where is the skills repo?

`lib/awf_home.py`:

1. `os.environ["AWF_HOME"]` if set.
2. `~/.claude/awf-skills/` if it exists.
3. Walk up from the running script's `realpath` until we find a directory
   containing `lib/awf_home.py` (i.e. our own marker).
4. Error.

Scripts that need `lib/` do:

```python
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(os.environ.get("AWF_HOME") or
    Path(__file__).resolve().parents[3]) / "lib"))
from passport import Passport
```

Ugly but explicit. Wrapping it in a `bootstrap()` helper is tempting but
that helper itself can't be imported until the path is set, so we eat the
boilerplate at the top of each script.

### Project root — where is the website I'm working on?

`lib/project.py`:

1. Walk up from `cwd` looking for `passport.json`.
2. If found, that dir is the project root.
3. If not found, raise `ProjectNotFound` with: "run `awf-create-project`
   from where you want the project, or `cd` to an existing project."

No env var, no global registry. The cwd is the project, period (A1).

### Credentials — where is the value of `CLOUDFLARE_API_KEY`?

`lib/config.py`, layered per A6:

1. `os.environ`
2. `<project_root>/.env` (only when a project root is found)
3. `$AWF_HOME/.env`
4. `~/.config/awf/.env`

`Config.get(key) → (value, source)`. `awf-doctor` prints the source for
each key. There is no implicit `dotenv.load_dotenv()` of any single file
— we load all four into a layered dict and resolve on access.

---

## Skill format

Each `skills/<skill-name>/SKILL.md` is a Claude Code skill file:

```markdown
---
name: awf-doctor
description: Validate the awf-skills runtime — required CLIs, credentials,
  OAuth tokens, git/npm hygiene. Run before any awf-* skill that mutates
  remote state.
---

# Body

Plain prose telling Claude:
- when this skill is appropriate
- what to do (which command to run)
- how to interpret output
- where to escalate to the user
```

Companion scripts live next to it (`scripts/<name>.py`), invoked with
`uv run`. Skills should NOT inline code that has non-trivial logic — put
it in a script and tell Claude to run it. Markdown is for orchestration,
not implementation.

See [`04-skill-authoring.md`](04-skill-authoring.md) for the full
template.

---

## Shared library boundary

`lib/` modules are *the* contract for cross-skill code. Two rules:

1. **No circular dependencies.** `slug` depends on nothing. `awf_home`
   depends on nothing. `config` depends on `awf_home` + `project`.
   `passport` depends on nothing in `lib/`. `project` depends on nothing.
2. **No skill-specific code.** If `awf-deploy` needs a helper, that
   helper goes in `awf-deploy/scripts/` or, if shared, into `lib/` —
   never into another skill's dir.

Tests for `lib/` live in `tests/lib/`. Tests for skills are
end-to-end-ish: a fixture project dir + a fake env, asserting the script
produces the right `passport.json` mutation or exits with the right
status.
