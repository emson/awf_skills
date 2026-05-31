# 01 — Principles

The axioms the suite is built on. Concise reference. Rationale lives in
[`00-plan.md`](00-plan.md). Architecture consequences live in
[`02-architecture.md`](02-architecture.md).

These are opinionated by design. Reject any of them and the system stops
making sense as a whole.

---

## A1. The project is the cwd; the contract is `passport.json`

A skill that acts on "this site" expects `./passport.json` to exist (walk
up to find it). No registry. No monorepo. The marker file *is* the
project. Skills run from inside the project dir like `git` does.

## A2. Portable by default

Required runtime: `bash`, `git`, `node`/`npm`, `uv`, `wrangler`, `gh`.
Plus declared credentials. Nothing else. No clone of the legacy
`agent_web_factory` repo. No virtualenv setup.

## A3. Companion code is Python-via-uv-script

Every script that needs API logic is a single file with PEP 723 inline
metadata declaring its dependencies:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["cloudflare>=3", "python-dotenv"]
# ///
```

`uv run` resolves and caches deps on first invocation. No venv, no install
step. This preserves the existing tested API logic from
`agent_web_factory/agent_factory/tools/` without dragging the monorepo or
asking users to manage a Python environment.

## A4. One verb per atomic skill; one orchestrator above

Atomic skills do one thing (`awf-deploy`, `awf-setup-domain`,
`awf-verify-gsc`). The orchestrator (`awf-launch`) sequences them with
checkpoints. No deep hierarchies. No utility skills that "almost" do two
things.

## A5. Idempotent or refused

Every mutating skill checks current state via the upstream API and either
no-ops, repairs, or errors with an explicit `--force`. Mirror the
search-or-create pattern already in `cloudflare/zones.py` and
`cloudflare/pages.py`. Re-running `awf-launch` on a half-finished site
must be safe.

## A6. Credentials are layered and discoverable

Resolution order (first match wins, per-key):

1. process environment
2. `./.env` (project-local, walking up to project root)
3. `$AWF_HOME/.env` (skills-repo-local)
4. `~/.config/awf/.env` (user-global)

`awf-doctor` prints which file each var came from. No hidden
`__file__`-relative loads.

## A7. State lives where the API stores it

`awf-status` queries Cloudflare, Fathom, GSC for the current truth. The
only local state is `passport.json`, which is config not cache.
`fathom_site_id` is stored because the API requires it on subsequent
calls; it is not "remembering" Fathom's state on Fathom's behalf.

## A8. Manual steps are first-class

Domain pick, SERP screenshot, passport review, Bing webmaster import.
Each is named as a gate, with what-the-human-must-do printed and an
explicit wait. Not bugs to be hidden. Not retries to be added. Not silent
assumptions.

## A9. Doctor before doing

Pre-flight is its own skill (`awf-doctor`). Other skills assume the
environment is healthy and fail loudly if it isn't, rather than
re-implementing checks.

## A10. The template is a separately-versioned artifact

Templates live at `templates/landing-page-v<N>/` (semver tags on the
repo). `awf-create-project` writes `template_version` into
`passport.json`. `awf-update-template` performs a 3-way merge to
re-overlay a newer version while preserving content (per the template's
own `preserve-list`).

## A11. The passport has a schema version

`schema_version: "1.0"` field. Skills refuse to operate on schemas they
don't understand. Migrations are explicit code paths. No silent breakage
when a future template adds fields.

## A12. Derive the slug, never accept it

`domain` is the only input. Slug is computed via the canonical function
in `lib/slug.py` (`devroast.com → devroast-com`). The legacy
dotted-vs-hyphenated ambiguity dies here.

## A13. Surface upstream errors unchanged

On API failure, dump the response body. Don't pretty-print over real
errors. The legacy workflows already do this; preserve the discipline.

## A14. Skills compose via file artifacts, not bash chaining

Skill A writes to `passport.json`; skill B reads it. Loosely coupled.
Lets users run individual skills out of order while recovering from a
partial launch. The orchestrator is convenience, not the only entry
point.

## A15. No destructive git from skills

Skills suggest `git` commands; they never run `merge`, `branch -d`,
`reset`, `checkout --`, or `push --force`. `awf-create-project` may
`git init` a fresh repo (creation, not destruction). Everything else is
the human's call.

## A16. Hardcode the stack; document the swap point

Cloudflare + Namecheap + Fathom + GSC + Bing. No adapter pattern. No
`HostingProvider` interface with one implementation. If a second user
needs Vercel + Plausible later, they fork. Adapters before the second
user are over-engineering.

## A17. Content generation is Claude-native, not an SDK call

`awf-generate-content` reads the SERP screenshot directly with Claude's
multimodal capabilities and writes structured output to `passport.json`
and vault notes. No `ell`, no OpenAI key, no response-format gymnastics.
The Pydantic shapes survive as the contract documented in the skill body.

---

## When axioms collide

A few collisions to be explicit about:

- **A5 (idempotent) vs A8 (manual gates).** Re-running `awf-launch`
  should not re-prompt the user for screenshots they already supplied.
  The orchestrator records gate completion in `passport.json`
  (`launch.gates.<name>.completed_at`) so resumed runs skip what's done.

- **A7 (no local state) vs A11 (schema version).** The schema version is
  not state — it is contract metadata. Same for `template_version`,
  `fathom_site_id`, `indexnow_key`. These are identifiers and contract
  pins, not cached API results.

- **A15 (no destructive git) vs A4 (one verb).** A skill named
  `awf-launch-and-merge` violates A4 *and* A15. The orchestrator must not
  cross the deploy boundary into git mutations on `main`.
