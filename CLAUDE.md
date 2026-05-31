# Claude Code Context: awf_skills

A portable suite of Claude Code skills for end-to-end Svelte website launch. Installable to `~/.claude/skills/`, runnable from any project directory.

## Before starting work

Read in this order:
1. [`docs/00-plan.md`](docs/00-plan.md) — why this exists, what it does, the full pipeline
2. [`docs/01-principles.md`](docs/01-principles.md) — 17 axioms (A1–A17) that govern all design
3. [`docs/02-architecture.md`](docs/02-architecture.md) — repo layout, file resolution, how it all fits together

Then pick the specific doc for the work you're doing:
- **Modifying a skill**: [`docs/04-skill-authoring.md`](docs/04-skill-authoring.md)
- **Adding credentials**: [`docs/05-credentials.md`](docs/05-credentials.md)
- **Testing/debugging**: [`docs/06-experimentation-guide.md`](docs/06-experimentation-guide.md) (four tiers: dry → local build → API-side → full launch)
- **Understanding passport.json**: [`docs/03-passport-contract.md`](docs/03-passport-contract.md)
- **Building templates**: [`templates/README.md`](templates/README.md)

## Core principles to keep in mind

**Idempotency first**: Every mutating operation uses search-or-create (check if exists before creating). APIs that don't natively support idempotency get an explicit read-then-compare-before-write wrapper.

**Layered config**: All credentials resolve through: process env → `./.env` → `$AWF_HOME/.env` → `~/.config/awf/.env`. Use `lib/config.py:Config.layered()` to read them. Never hardcode env var names; always go through the config layer.

**Project locator**: Walk up to find `passport.json`. Use `lib/project.py:find_project_root()`. Never assume cwd.

**AWF_HOME locator**: Find the skills repo at runtime via env var → conventional path → walk-up search. Use `lib/awf_home.py:get_awf_home()`.

**Manual gates as first-class**: Some steps are inherently manual (e.g., SERP screenshot, Bing-Webmaster import). Gates are recorded in `passport.json` under `launch.gates.<name>`. Orchestrator skips completed steps on resume. Tie gate completion to CLI flags (`--mark-reviewed`, `--confirm-imported`) so the human action is explicitly captured.

**uv inline-scripts, no venv**: All Python scripts use [`uv` PEP 723 script metadata](https://docs.astral.sh/uv/guides/scripts/). Dependencies declared in a `# /// script` comment block at the top of the file. No virtualenv to manage.

## Working with skills

**Anatomy of a skill:**
```
skills/awf-<verb>/
├── SKILL.md            # Claude Code skill definition (what it does, args, interaction mode)
└── scripts/<verb>.py   # Optional uv-script that does the work (or body-only if no script)
```

All scripts import from `lib/`. If your script needs to do something not in `lib/`, either add it to `lib/` (if it's reusable across skills) or keep it local (if it's skill-specific).

**Testing a skill locally:**
```bash
cd ~/.claude/awf-skills
python -m skills.awf-<verb>.scripts.<verb> [args]
```

Or run the full experimentation tiers in [`docs/06-experimentation-guide.md`](docs/06-experimentation-guide.md).

## Key files to know

**Shared library (`lib/`)**: All ~1000 lines of API code (Cloudflare, Fathom, Namecheap, Bing, GSC, config, schema) lives here. Before writing API glue, check if `lib/` already has it.

**Passport schema (`lib/passport.py`)**: The `Passport` dataclass is Pydantic v2. Use `Passport.load()` / `.save()` / `.validate()`. Gates go in `.launch.gates`. See [`docs/03-passport-contract.md`](docs/03-passport-contract.md) for the full schema.

**Template engine (`lib/templates.py`)**: `plan_overlay()` computes file changes (respecting preserve-globs), `apply_overlay()` writes them. Templates declare `preserve_globs` in `template.json` to protect user files during re-overlay.

**Experimentation guide (`docs/06-experimentation-guide.md`)**: The canonical reference for testing. Covers four tiers with expected outputs, recovery procedures, and troubleshooting.

## No destructive shortcuts

Always identify and fix root causes. Don't skip validation (use `passport.validate()` before saving). Don't bypass errors with `--no-verify` or similar. If a credential fails, check the source chain via `config.source()` instead of guessing.

## Common patterns

**Search-or-create**:
```python
from lib.cf import get_or_create_zone
zone = get_or_create_zone(slug)  # Returns existing zone if found, creates if not
```

**Layered config lookup**:
```python
from lib.config import Config
config = Config.layered()
api_token = config.require('CLOUDFLARE_API_TOKEN')  # Raises if missing
source = config.source('CLOUDFLARE_API_TOKEN')  # Where it came from
```

**Passport gate tracking**:
```python
passport = Passport.load()
if not passport.launch.gates.get('my_gate'):
    # Do work
    passport.launch.gates['my_gate'] = True
    passport.save()
```

## When to add to lib/ vs stay skill-local

**Add to lib/** if: Multiple skills use it, or it's a reusable abstraction (API client, config resolver, schema). Keep it under 100 lines and focused on one thing.

**Stay skill-local** if: Only this skill uses it, or it's highly specialized business logic.

## Testing credentials without running all skills

Use `awf-doctor` to validate credentials in isolation:
```bash
> /awf-doctor
```

It checks CLIs, OAuth status, and all credential groups. Human + JSON output modes. Use this before attempting a full launch.

## One more time: the axioms

The 17 axioms in [`docs/01-principles.md`](docs/01-principles.md) are load-bearing. A few highlights:
- **A1**: Search-or-create, never duplicate.
- **A6**: Layered config everywhere.
- **A7**: Project locator (walk up to `passport.json`).
- **A11**: Resumability via gates in `passport.json`.
- **A14**: Manual gates are not failures; they're design features.

Read them all before making design decisions.
