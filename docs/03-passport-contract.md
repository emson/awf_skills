# 03 — Passport Contract

`passport.json` is the single config artifact for a website project. It
is the contract between the skills, the template, and the human. It is
the marker file that defines "this directory is a project" (A1). It is
also the only place skills write durable state (A7).

The schema is versioned (A11). Skills refuse to operate on schemas they
don't understand, and migrate explicitly when the version is older than
their target.

The canonical Pydantic model lives in [`lib/passport.py`](../lib/passport.py).
This document is the human-readable mirror.

> **Note:** Multi-stage state (`.awf/project.json`, `.awf/infra.json`,
> `~/.config/awf/shared.json`) is a separate contract defined in
> [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson)
> and implemented in `lib/state.py`. The two contracts coexist per D-001 §5.

---

## Schema v1.0

```jsonc
{
  // Contract metadata
  "schema_version": "1.0",          // (A11) — bumped on breaking changes
  "template_version": "1.0",        // (A10) — pinned by awf-create-project

  // Identity
  "project_name": "devroast-com",   // derived; do not edit
  "domain":       "devroast.com",   // canonical input
  "site_url":     "https://devroast.com",

  // Copy (populated by awf-generate-content; reviewed by human)
  "site_name":        "Roast My Website",
  "site_hero":        "The Most Brutal Feedback on the Internet!",
  "site_subtitle":    "Want free backlinks? Then be prepared to be roasted.",
  "site_description": "…",
  "category":         "Website Optimization",
  "tags":             "Website Roast, Feedback, Conversion Rates",
  "cta_button":       "Open Link",
  "email":            "hello@agentwebfactory.com",

  // Stack pins (A16)
  "nameserver_service": "namecheap",
  "hosting_service":    "cloudflare",

  // External identifiers stored locally because the API requires them on
  // subsequent calls (not state caching — see A7).
  "fathom_site_id": "OFGTDHCD",
  "indexnow_key":   "69f37f88931747a88951c70c3603c397",

  // Content
  "features": [""],
  "faqs": [
    {
      "question": "What are the benefits of getting a website roast?",
      "answer":   "…"
    }
  ],

  // Launch progress (A8 + A5 reconciliation)
  "launch": {
    "gates": {
      "domain_picked":     { "completed_at": "2025-05-01T10:14:00Z" },
      "serp_screenshot":   { "completed_at": "2025-05-01T10:22:00Z",
                             "path": "./data/search-devroast.jpeg" },
      "passport_reviewed": { "completed_at": null },
      "bing_imported":     { "completed_at": null }
    }
  }
}
```

### Field rules

| Field | Type | Writer | Mutable by user? | Notes |
|---|---|---|---|---|
| `schema_version` | string (semver) | skills | no | version of *this* schema |
| `template_version` | string (semver) | `awf-create-project`, `awf-update-template` | no | which template the project was scaffolded from |
| `project_name` | string | `awf-create-project` | no | derived from `domain` via `lib/slug.py` |
| `domain` | string | `awf-create-project` | no | canonical input; never accepted elsewhere |
| `site_url` | string (URL) | `awf-create-project` | no | always `https://{domain}` |
| `site_name`, `site_hero`, `site_subtitle`, `site_description`, `category`, `tags` | string | `awf-generate-content` | **yes** | reviewed in `awf-review-passport` |
| `cta_button`, `email` | string | template defaults | yes | rarely changed |
| `nameserver_service` | enum: `namecheap` | template defaults | no (A16) | |
| `hosting_service` | enum: `cloudflare` | template defaults | no (A16) | |
| `fathom_site_id` | string | `awf-setup-analytics` | no | Fathom-issued |
| `indexnow_key` | string (uuid hex) | `awf-submit-bing` (first run) | no | per-domain (A6 trade-off resolved) |
| `features` | string[] | template + manual | yes | Svelte template reads this directly |
| `faqs` | `{question, answer}[]` | `awf-generate-content` | yes | reviewed in `awf-review-passport` |
| `launch.gates.<name>` | object | orchestrator | no | per-gate completion record; `awf-launch` resumes by skipping completed gates |

### Validation rules

Encoded in `lib/passport.py`:

- `domain` matches `^[a-z0-9.-]+\.[a-z]{2,}$` (loose; we don't re-implement RFC 1035).
- `project_name == domain_to_project_name(domain)`. Mismatch is a hard error.
- `site_url == f"https://{domain}"`. Mismatch is a hard error.
- `faqs` entries non-empty strings; warn if `<3` (template expects a few).
- `features[0]` non-empty after `awf-review-passport`; warn before then.

---

## Versioning policy

`schema_version` follows semver:

- **Patch** (`1.0 → 1.0.1`): documentation/wording only. Skills accept all
  patches at or below their compiled version.
- **Minor** (`1.0 → 1.1`): added optional fields. Older skills ignore
  unknown fields; newer skills tolerate older minor versions.
- **Major** (`1.0 → 2.0`): breaking. Renamed/removed/repurposed fields.
  Requires an explicit migration in `lib/passport.py` (`migrate_v1_to_v2`).

`Passport.load(path)`:

1. Read JSON.
2. If `schema_version > our_max`: error "this passport was written by a
   newer awf-skills; please update."
3. If `schema_version < our_min`: run migrations in order to lift it to
   `our_min`, write back, log the upgrade.
4. Validate.

`schema_version` lives at the top of the file because it's the first
thing readers should see.

---

## Migration policy

Migrations are explicit code, never magic:

```python
def migrate_v1_to_v2(data: dict) -> dict:
    """1.0 → 2.0: rename `tags` (csv string) to `tags_list` (string[])."""
    csv = data.pop("tags", "")
    data["tags_list"] = [t.strip() for t in csv.split(",") if t.strip()]
    data["schema_version"] = "2.0"
    return data
```

Rules:

- One migration per version step. No "skip-grade" migrations.
- Migrations are pure: dict in, dict out. No file I/O.
- Migrations write the upgraded passport back to disk *and* leave a
  backup at `passport.json.v<old>.bak`.
- A `--dry-run` flag on `awf-update-template` (and on the migration
  helper) prints the diff without writing.

---

## Why store *anything* locally?

Only when the API requires the local value to call back to the service:

- `fathom_site_id`: Fathom's API needs the site ID; we store what they
  return.
- `indexnow_key`: IndexNow expects the key to live at
  `https://{domain}/{key}.txt`; the build needs it.
- `template_version`: pin needed for `awf-update-template` to know what
  to diff from.

Everything else (Cloudflare zone state, GSC verification status, Fathom
existence) is queried live by `awf-status` per A7. We do not cache it.
