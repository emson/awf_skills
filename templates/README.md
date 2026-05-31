# templates/

Versioned site templates. One subdirectory per template version
(`landing-page-v1/`, `landing-page-v2/`, …). Each directory contains:

- A SvelteKit project tree (the actual template).
- `template.json` — declares the template's name, version, and the
  passport `schema_version` it requires.
- `preserve-list.txt` — paths under a project that should NOT be
  overwritten when `awf-update-template` overlays a newer version.

This directory is empty in the initial scaffold. The first concrete
template will land as `landing-page-v1/` and is tracked separately
from the skills' build order — see [docs/00-plan.md §9](../docs/00-plan.md).

## `template.json` (shape)

```json
{
  "name": "landing-page",
  "version": "1.0",
  "passport_schema": "1.0",
  "description": "SvelteKit landing page for a single-domain site, deployed to Cloudflare Pages."
}
```

## `preserve-list.txt` (shape)

One glob per line, relative to the project root. The legacy default
list (from `agent_factory/workflows/template_overwrite_workflow.py`)
is the sensible starting point:

```
passport.json
src/passport.css
src/lib/posts/**
src/lib/config/categories.json
static/images/posts/**
static/images/listings/**
static/data/db.json
static/_headers
static/_redirects
```

`awf-update-template` reads this when overlaying.

## Versioning policy

- Patch (`1.0 → 1.0.1`): bug-fix to template files; safe to overlay.
- Minor (`1.0 → 1.1`): added optional features; safe to overlay.
- Major (`1.0 → 2.0`): breaking; usually paired with a passport
  schema bump. `awf-update-template` will refuse without `--force`.
