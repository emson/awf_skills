# Plan 013 — `awf-help` redesign + `awf-doctor` scoping

**Status:** ready
**Phase:** C
**Spec refs:** [`spec.md` § C3](../spec.md), [`spec.md` § C4](../spec.md), [`decisions.md` D-008](../decisions.md#d-008), [`decisions.md` D-009](../decisions.md#d-009), [`07-multi-stage-architecture.md`](../07-multi-stage-architecture.md), [`08-logging.md`](../08-logging.md)
**Owner (current):** Implementer
**Created:** 2026-06-01
**Updated:** 2026-06-01

## Status log

| Date | Status | Actor | Note |
|------|--------|-------|------|
| 2026-06-01 | draft | Lead | Initial plan. Batches C3 (`awf-help` redesign per D-008) + C4 (`awf-doctor` scoping per D-009) under one plan; both are small, both touch a stage→X mapping, and both want a single home for that mapping (`lib/stages.py`, new). C3 fully replaces the body-only `awf-help`; C4 extends the existing `awf-doctor` with two flags + recent-error surfacing without changing default behaviour. Promotes `NEXT_COMPOSERS` out of `skills/awf-status/scripts/status.py` into `lib/stages.py` (plan_012 explicitly flagged this — see plan_012 § Decisions item 9 and `status.py:50` TODO). Encodes plan_011 / plan_012 lessons: subprocess-only for end-to-end shape; direct `main()` for unit logic; `lib.log.tail_events` is the DRY source; per-provider degrade-to-unknown posture; LLM-directive line in each SKILL.md. |
| 2026-06-01 | review-approved | Reviewer | Pass 1 complete. All five tensions and D2 verdict below. No blocking issues; two minor notes to carry into implementation. |

## Goal

Two thin C-phase skills, one shared mapping module, one plan.

**`awf-help` (C3 / D-008).** Replace the current body-only "printed
catalogue" `awf-help` with a context-aware skill that auto-detects
which of three modes to render:

1. **Fresh-start mode** (no `.awf/project.json` found by walking up).
   One screen of orientation pointing the operator at
   `/awf-create-project` or `/awf-launch`, with a hint that
   `/awf-help --overview` shows the full system.
2. **In-project mode** (anchor found). Print the named composer for
   `stage+1`, the atomic skills relevant to the current stage, and a
   "common operations" footer (`status`, `log tail`, `doctor`,
   `teardown`).
3. **`--overview` mode** (explicit flag). Full catalogue grouped by
   stage, with links to `docs/07-multi-stage-architecture.md` and
   `docs/08-logging.md`. The shape the old `awf-help` had — but
   structured by stage instead of a flat pipeline table.

Read-only. Never mutates state. Never calls external APIs.

**`awf-doctor` (C4 / D-009).** Add two scoping flags and a
recent-error pre-step to the existing `awf-doctor`, without changing
default behaviour.

1. `--for-stage <name>` — check only the subsystems (credentials +
   CLIs + OAuth) needed at that stage.
2. `--for-skill <skill>` — narrowest scope: the subsystems a single
   atomic skill needs.
3. **Recent-error surfacing.** Tail the last 50 events of the project
   log via `lib.log.tail_events`. If a credential-shaped error
   (`401`, `403`, `"auth"` substring, case-insensitive) appears,
   doctor leads with that specific subsystem's check before doing
   the broader sweep. The lead-with is visual only — the full
   selected sweep still runs after it.

Default invocation (`uv run check.py` with no scope flag, no project
context) keeps the current behaviour byte-for-byte; backwards-compat
is an explicit AC.

Out of scope (this plan):

- Composer-side calls into `awf-doctor --for-stage X` as a pre-step
  (a separate hook, plan_014+).
- A `--for-composer` flag (composers are sequences of skills; we
  already have `--for-skill` and `--for-stage`).
- Auto-running `awf-doctor` from `awf-help` (the help skill stays
  pure-info).
- Repairing detected issues (`awf-doctor --fix`); doctor remains a
  reporter.
- Translating `awf-help`'s output for non-CLI surfaces (web UI etc.).

## Context

- [`spec.md § C3`](../spec.md) (lines 387–400) fixes the three-mode
  contract for `awf-help`, locks "never mutates / never calls
  external APIs", and names the docs to link from `--overview`.
- [`spec.md § C4`](../spec.md) (lines 402–416) fixes the two scope
  flags, the credential-shaped-error definition, and the default-
  unchanged guarantee. Names `Hetzner, Neon, GHCR, SSH` for
  `--for-stage mvp-play` and `registry auth, kamal CLI, ssh to
  target server` for `--for-skill awf-kamal-deploy` — these strings
  are AC text.
- [`decisions.md` D-008](../decisions.md#d-008) closes D-OPEN-H.
  Notes the "revisit if skill count > 30" sub-categorisation hook.
- [`decisions.md` D-009](../decisions.md#d-009) closes D-OPEN-I.
  Names `lib/doctor.py` as the home for the stage→check mapping.
  We instead consolidate **all** stage-keyed mappings under
  `lib/stages.py` (see Decisions §2 below); D-009's `lib/doctor.py`
  is folded into that.
- [`skills/awf-help/SKILL.md`](../../skills/awf-help/SKILL.md) is
  the current body-only skill: ~115 lines of Markdown, no script.
  D-008 reshapes the contract entirely; the new skill needs a
  script (`help.py`) because mode selection requires filesystem
  detection. The body becomes a thin instruction wrapper around
  `uv run ".../help.py"`.
- [`skills/awf-doctor/SKILL.md`](../../skills/awf-doctor/SKILL.md)
  and [`scripts/check.py`](../../skills/awf-doctor/scripts/check.py)
  are the current skill: 315 lines, argparse with `--json` only,
  exit codes 0 / 1 / 2. Functions `check_cli`, `check_credential`,
  `check_google_token`, `build_report`. We extend, not replace.
- [`skills/awf-status/scripts/status.py`](../../skills/awf-status/scripts/status.py)
  lines 50–66 define `NEXT_COMPOSERS` and `NEXT_HINTS` with a TODO
  comment ("Local for now; plan_013 (`awf-help`) will promote to
  `lib/stages.py`"). plan_012's Decisions §9 promised this
  promotion. This plan delivers it.
- [`lib/log.py`](../../lib/log.py): `tail_events(path, n)` and
  `latest_session_id(path)` from plan_011 are the public read API.
  Doctor's recent-error surfacing reuses `tail_events`; no
  reverse-block re-implementation.
- [`lib/state.py`](../../lib/state.py): `ProjectAnchor.load(start,
  optional=True)` (locked by plan_012's T5 / spec A1) is the
  locator. `optional=True` returns `None` instead of raising
  `ProjectNotFound`; both new scripts use this form.
- [`lib/project.py`](../../lib/project.py): `find_project_root` is
  the legacy locator (`passport.json`-based). plan_012 uses
  `ProjectAnchor.load`; we follow that pattern.
- [`lib/config.py`](../../lib/config.py): `Config.layered()` and
  `config.source(key)` are the credential-check primitives.
  `awf-doctor` already uses them; the scoping flags just gate
  *which* calls fire.

## Architecture overview

```
lib/stages.py                                # NEW
  NEXT_COMPOSERS:      dict[str, str | None]
  NEXT_HINTS:          dict[str, str]
  RELEVANT_SKILLS:     dict[str, list[str]]
  STAGE_REQUIREMENTS:  dict[str, list[str]]      # subsystem ids
  SKILL_REQUIREMENTS:  dict[str, list[str]]      # subsystem ids
  STAGE_ORDER:         tuple[str, ...]           # ("landing", "demo", "mvp-play", "prescale", "scale")
  SUBSYSTEMS:          frozenset[str]            # closed enum of valid ids
  next_composer(stage) -> str | None
  relevant_skills(stage) -> list[str]
  stage_subsystems(stage) -> list[str]
  skill_subsystems(skill) -> list[str]

skills/awf-help/
  SKILL.md                                       # rewritten — body becomes thin
  scripts/help.py                                # NEW — argparse, mode detection, render

skills/awf-doctor/
  SKILL.md                                       # updated — flag table + LLM directive
  scripts/check.py                               # extended — flags + recent-error pre-step
```

### `lib/stages.py` — single source of truth

One module, five constants, four helper functions. The constants
are the canonical answer to "what does stage X involve":

```python
STAGE_ORDER: Final = ("landing", "demo", "mvp-play", "prescale", "scale")

NEXT_COMPOSERS: Final[dict[str, str | None]] = {
    "landing":   "awf-stage-demo",
    "demo":      "awf-stage-mvp-play",
    "mvp-play":  "awf-stage-prescale",
    "prescale":  "awf-stage-scale",
    "scale":     None,
}

NEXT_HINTS: Final[dict[str, str]] = { ... }  # moved verbatim from status.py

# Atomic skills that make sense to invoke at a given stage.
# Composer skills (awf-stage-*) intentionally excluded — they're the
# next-composer, not "relevant at this stage".
RELEVANT_SKILLS: Final[dict[str, list[str]]] = {
    "landing": [
        "awf-init", "awf-create-project", "awf-setup-domain",
        "awf-setup-analytics", "awf-setup-nameservers",
        "awf-generate-content", "awf-review-passport",
        "awf-install", "awf-deploy",
        "awf-setup-gsc", "awf-verify-gsc", "awf-submit-bing",
    ],
    "demo": [
        # Superset of landing + template refresh
        # (… same as landing …),
        "awf-update-template",
    ],
    "mvp-play": [
        "awf-shared-infra-get", "awf-app-dockerize",
        "awf-neon-branch", "awf-app-secret-set",
        "awf-kamal-config", "awf-cf-dns-record",
        "awf-kamal-setup", "awf-kamal-deploy",
    ],
    "prescale": [],   # TBD; plan_014+ populates
    "scale":    [],   # TBD; plan_015+ populates
}

# Subsystem ids are stable strings used by doctor's check functions.
# Closed set; mypy enforces via SUBSYSTEMS check at test time.
SUBSYSTEMS: Final[frozenset[str]] = frozenset({
    "cloudflare", "namecheap", "fathom", "gsc", "bing",
    "hetzner", "neon", "ghcr", "ssh", "kamal_cli",
    "google_oauth", "git", "node", "wrangler",
})

STAGE_REQUIREMENTS: Final[dict[str, list[str]]] = {
    "landing":  ["cloudflare", "namecheap", "fathom", "gsc", "bing", "git", "node", "wrangler"],
    "demo":     ["cloudflare", "namecheap", "fathom", "gsc", "bing", "git", "node", "wrangler"],
    "mvp-play": ["hetzner", "neon", "ghcr", "ssh", "kamal_cli", "git"],
    "prescale": ["hetzner", "neon", "ghcr", "ssh", "kamal_cli", "git"],
    "scale":    ["hetzner", "neon", "ghcr", "ssh", "kamal_cli", "git"],  # upstash deferred
}

# Per-skill subsystem map; only skills with non-trivial preflights need an entry.
# Skills not listed fall through to "no specific preflight required".
SKILL_REQUIREMENTS: Final[dict[str, list[str]]] = {
    "awf-setup-domain":     ["cloudflare"],
    "awf-setup-nameservers":["namecheap"],
    "awf-setup-analytics":  ["fathom"],
    "awf-setup-gsc":        ["google_oauth", "cloudflare"],
    "awf-verify-gsc":       ["google_oauth"],
    "awf-submit-bing":      ["bing"],
    "awf-deploy":           ["wrangler", "node"],
    "awf-install":          ["node"],
    "awf-hetzner-provision":["hetzner", "ssh"],
    "awf-neon-provision":   ["neon"],
    "awf-neon-branch":      ["neon"],
    "awf-kamal-config":     ["kamal_cli"],
    "awf-kamal-setup":      ["kamal_cli", "ssh", "ghcr"],
    "awf-kamal-deploy":     ["kamal_cli", "ssh", "ghcr"],
    "awf-cf-dns-record":    ["cloudflare"],
    "awf-app-secret-set":   [],   # local; no preflight
    "awf-app-dockerize":    [],   # local; no preflight
}
```

A unit test in `tests/lib/test_stages.py` asserts:

- Every value in `STAGE_REQUIREMENTS` and `SKILL_REQUIREMENTS` is in
  `SUBSYSTEMS` (closed-set integrity).
- Every key in `NEXT_COMPOSERS` is in `STAGE_ORDER`.
- `RELEVANT_SKILLS` keys are exactly `STAGE_ORDER`.
- `next_composer("scale") is None` (terminal).

Migration: `skills/awf-status/scripts/status.py` lines 50–66 (the
`NEXT_COMPOSERS` / `NEXT_HINTS` constants) are deleted and replaced
with `from lib.stages import NEXT_COMPOSERS, NEXT_HINTS`. The TODO
comment is removed. `test_awf_status.py::test_next_composer_for_stage`
continues to pass — the values are identical.

### `awf-help` — mode dispatch

```python
# skills/awf-help/scripts/help.py
def main(argv: list[str]) -> int:
    args = parse(argv)              # --overview, --json, --pipeline (alias for --overview)
    if args.overview:
        render_overview(args.json)
        return 0
    anchor = ProjectAnchor.load(start=Path.cwd(), optional=True)
    if anchor is None:
        render_fresh_start(args.json)
        return 0
    render_in_project(anchor, args.json)
    return 0
```

Three render functions, plus a `--json` mode for each that mirrors the
human shape. No I/O outside `Path.cwd()` and `ProjectAnchor.load`.

**Fresh-start mode (human).**

```
You're not in an awf project.

Start a project:
  /awf-create-project <domain>        Scaffold a project at the current stage.
  /awf-launch <domain> --keywords "…" Run the full landing pipeline end-to-end.

Learn the system:
  /awf-help --overview                Full catalogue grouped by stage.
```

Exactly four bullets, one recommended next command. D-008 names
`/awf-create-project` first.

**In-project mode (human).**

```
You're at stage: <stage>.

To advance:
  /<next-composer>                    <next-hint>

Atomic skills relevant here:
  /<skill-1>
  /<skill-2>
  …

Common operations:
  /awf-status                         Show where this project is.
  /awf-log tail                       Tail the recent event log.
  /awf-doctor                         Validate environment + credentials.
  /awf-teardown                       (deferred — D2 plan)

Full catalogue: /awf-help --overview
```

The "common operations" block names `awf-teardown` even though it
ships in plan D2 — D-008's text lists it explicitly. The line is
marked `(deferred)` so the LLM does not call a missing skill.

**`--overview` mode (human).**

Sections in `STAGE_ORDER` order. For each stage:

```
=== Stage: <stage> ===
Next composer: /<composer> (<hint>)
Atomic skills:
  /<skill> — <one-line description from SKILL.md frontmatter>
  …
```

Followed by:

```
Common operations: /awf-status, /awf-log, /awf-doctor
Reference: docs/07-multi-stage-architecture.md, docs/08-logging.md
```

The one-line skill descriptions are pulled from each skill's
`SKILL.md` frontmatter `description:` field at script run time
(scan `$AWF_HOME/skills/*/SKILL.md`). If the file is missing or
unparseable, the line falls back to the skill name only — never
fail the help command for a malformed peer skill.

**`--json` shape.**

```json
{
  "schema_version": 1,
  "mode": "fresh_start | in_project | overview",
  "stage": "<stage>" | null,
  "project": {"slug": "...", "domain": "...", "root": "/abs/path"} | null,
  "next_composer": "<name>" | null,
  "next_hint": "..." | null,
  "relevant_skills": ["..."],
  "common_operations": ["awf-status", "awf-log", "awf-doctor", "awf-teardown"],
  "stages": [           // present only when mode == "overview"
    {
      "name": "landing",
      "next_composer": "awf-stage-demo",
      "next_hint": "...",
      "atomic_skills": [
        {"name": "awf-init", "description": "First-time onboarding ..."},
        ...
      ]
    },
    ...
  ],
  "doc_links": ["docs/07-multi-stage-architecture.md", "docs/08-logging.md"]
}
```

No `STATUS_JSON_SCHEMA`-style strict-schema constant for `awf-help`
(unlike plan_012). The schema is documented in this plan and in
SKILL.md; the AC list pins the shape. A schema-validation constant
adds maintenance cost the C-phase doesn't need yet — `awf-help` is
not consumed by composers programmatically. (T1 below.)

### `awf-doctor` — flag extension

The existing `check.py` is **extended, not rewritten**. The diff
adds: argparse flags, a subsystem dispatch table, a recent-error
pre-step, and a guard around the existing `build_report()` flow.

```python
def main(argv: list[str]) -> int:
    args = parse(argv)                # --json, --for-stage, --for-skill
    cfg = Config.layered()

    # Resolve scope.
    if args.for_skill:
        wanted = skill_subsystems(args.for_skill)
        scope = ScopeSkill(args.for_skill, wanted)
    elif args.for_stage:
        wanted = stage_subsystems(args.for_stage)
        scope = ScopeStage(args.for_stage, wanted)
    else:
        wanted = None                 # default: full sweep
        scope = ScopeDefault()

    # Recent-error pre-step (always runs; cheap; project-anchor-optional).
    anchor = ProjectAnchor.load(start=Path.cwd(), optional=True)
    leading_subsystem: str | None = None
    if anchor is not None:
        leading_subsystem = detect_credential_error_subsystem(anchor)
        # Returns "cloudflare", "hetzner", etc., or None.

    report = build_report(cfg, scope=scope, lead_with=leading_subsystem)
    print(render(report, json=args.json))
    return 0 if report.ok else 1
```

`build_report` already exists; we add a `scope` parameter (default
`ScopeDefault()` preserves byte-for-byte backwards-compat) and a
`lead_with` parameter (default `None`).

**Subsystem mapping (in `check.py`, since `check.py` owns the actual
check calls).**

```python
SUBSYSTEM_CHECKS: dict[str, Callable[[Report, Config], None]] = {
    "cloudflare":  _check_cloudflare,    # CLOUDFLARE_API_TOKEN
    "namecheap":   _check_namecheap,     # NAMECHEAP_API_USER/KEY/IP
    "fathom":      _check_fathom,
    "gsc":         _check_gsc,           # google_oauth subset
    "bing":        _check_bing,
    "hetzner":     _check_hetzner,
    "neon":        _check_neon,
    "ghcr":        _check_ghcr,
    "ssh":         _check_ssh,
    "kamal_cli":   _check_kamal_cli,
    "google_oauth":_check_google_oauth,
    "git":         _check_git,
    "node":        _check_node,
    "wrangler":    _check_wrangler,
}
```

The current `check.py` already runs every one of these inline inside
`build_report()`; this plan **refactors** those into named per-
subsystem functions matching the keys above. The refactor is a
straight extraction — no behavioural change in the default path —
and is covered by the existing `test_doctor*` test (if absent, we
add a baseline test that captures the default output before
refactoring).

**Scope semantics.**

- `ScopeDefault` → run every entry in `SUBSYSTEM_CHECKS`.
- `ScopeStage(name, wanted)` → run only `wanted` keys, in the order
  given by `stage_subsystems(name)` (which preserves the
  `STAGE_REQUIREMENTS[name]` list order, which is curated for
  human-readable output).
- `ScopeSkill(name, wanted)` → run only `wanted` keys.

If `lead_with` is set **and** the subsystem is in the resolved
scope, that subsystem is moved to the front of the run order and
a one-line header is printed:

```
Recent error suggests credential issue — checking <subsystem> first.
```

If `lead_with` is set but the subsystem is **not** in the resolved
scope (e.g. `--for-stage landing` with a recent `401` from Neon),
print an advisory header anyway:

```
Recent error in <subsystem> noted but outside --for-stage <name>
scope. Run /awf-doctor (no flag) to investigate.
```

**`detect_credential_error_subsystem`.**

```python
def detect_credential_error_subsystem(anchor: ProjectAnchor) -> str | None:
    log_path = anchor.root / ".awf" / "log.jsonl"
    if not log_path.exists():
        return None
    events = tail_events(log_path, n=50)
    for ev in reversed(events):                # most-recent first
        if ev.get("type") != "error":
            continue
        msg = (ev.get("note") or "").lower()
        if not any(token in msg for token in ("401", "403", "auth")):
            continue
        subsystem = ev.get("subsystem") or _infer_subsystem(ev.get("skill", ""))
        if subsystem in SUBSYSTEMS:
            return subsystem
    return None
```

`_infer_subsystem(skill_name)` is a thin lookup: if the skill name
is in `SKILL_REQUIREMENTS` and the first subsystem in its list is
credentialed (i.e. not `git`/`node`/`ssh`), return that. The
detector is conservative — it only triggers on events that
explicitly carry `type: "error"` and contain a credential-shaped
token. False positives are an accepted cost; false negatives just
mean doctor runs the full sweep, which is fine. (T2 below.)

**Exit codes** (unchanged from current):

| Code | Meaning |
|------|---------|
| `0`  | All checks in scope pass (warnings allowed). |
| `1`  | At least one required check in scope failed. |
| `2`  | Usage error (bad flag combo, unknown stage name, unknown skill name). |

`--for-stage UNKNOWN` and `--for-skill UNKNOWN` exit `2` with a
stderr line listing the valid options. **Crucially**: `--for-skill
awf-app-secret-set` (a known skill with `SKILL_REQUIREMENTS[name] ==
[]`) is **not** a usage error — it exits `0` with a one-line note
"No preflight checks required for this skill." (T3 below.)

### Per-flag semantic table — `awf-doctor`

| Flag | Behaviour | Exit |
|------|-----------|------|
| *none* | Full sweep, byte-for-byte same as today. | 0 / 1 / 2 |
| `--json` | Same scope as above, JSON output. | 0 / 1 / 2 |
| `--for-stage <name>` | Run only `STAGE_REQUIREMENTS[name]`. | 0 / 1 / 2 |
| `--for-skill <name>` | Run only `SKILL_REQUIREMENTS[name]`. Empty list → exit 0 with note. | 0 / 1 / 2 |
| `--for-stage X --for-skill Y` | Usage error: mutually exclusive. | 2 |
| `--for-stage X --json` | Scoped sweep, JSON output. | 0 / 1 / 2 |

Recent-error surfacing is **always-on** regardless of flag (it
costs one `tail_events` call); behaviour described above.

## Acceptance criteria

### `awf-help` (spec § C3, verbatim)

- [ ] No `.awf/project.json` upward → fresh-start mode with one
      recommended next command (`/awf-create-project`).
- [ ] In-project mode lists the named composer for `stage+1` and
      the atomic skills relevant to the current stage.
- [ ] `--overview` is the full catalogue grouped by stage; links
      `docs/07-multi-stage-architecture.md` and
      `docs/08-logging.md`.
- [ ] Never mutates state; never calls external APIs.

### `awf-help` (plan-specific)

- [ ] `skills/awf-help/scripts/help.py` exists (new file); SKILL.md
      reduced to a thin instruction wrapper around `uv run …/help.py`
      with the LLM-directive line: "Use this as the entry point from
      any blank project directory; reads only, runs anywhere."
- [ ] Exit code always `0` for any non-usage invocation
      (`--overview` from any directory, in-project, fresh-start).
      Exit `2` only on `--json --overview` argparse errors.
- [ ] `--json` emits the documented shape for each of the three
      modes. Test asserts `mode in {fresh_start, in_project,
      overview}` and that mode-specific keys are present
      (`stage`/`relevant_skills` in `in_project`; `stages` array in
      `overview`).
- [ ] No-project mode does not import any provider client
      (`lib.cf`, `lib.hetzner`, `lib.neon`); a grep test asserts
      the imports in `help.py` are restricted to `lib.state`,
      `lib.stages`, `lib.project` (optional), and stdlib.
- [ ] In-project mode reads `.awf/project.json` only; no
      `.awf/infra.json` access (D-008: help is about names, not
      drift).
- [ ] `--overview` enumerates exactly the stages in `STAGE_ORDER`,
      in order, including stages with empty `RELEVANT_SKILLS`
      (`prescale`, `scale`) which print `(TBD — atomic skills land
      in plan_014+)` under "Atomic skills".
- [ ] Skill description scraping degrades to "name only" if a peer
      `SKILL.md` is missing or malformed; test injects a
      half-written `SKILL.md` into a fixture `$AWF_HOME` and
      confirms no exception.

### `awf-doctor` (spec § C4, verbatim)

- [ ] `--for-stage mvp-play` checks Hetzner, Neon, GHCR, SSH key
      reachability; skips Bing IndexNow, GSC.
- [ ] `--for-skill awf-kamal-deploy` checks registry auth (GHCR),
      kamal CLI presence, ssh to target server.
- [ ] If last session's log contains a credential-shaped error
      (`401`, `403`, `"auth"` in error message), doctor leads with
      that specific check.
- [ ] Default (no flag) behaviour unchanged for backwards-compat.

### `awf-doctor` (plan-specific)

- [ ] `--for-stage <unknown>` and `--for-skill <unknown>` exit `2`
      with a stderr line listing valid names. Test asserts the
      exact text and exit code.
- [ ] `--for-stage X --for-skill Y` exits `2` (mutually exclusive).
- [ ] `--for-skill awf-app-secret-set` (no preflight required) exits
      `0` with a "No preflight checks required" line. **Not** an
      error.
- [ ] `SUBSYSTEM_CHECKS` extraction is a behaviour-preserving
      refactor: a "golden" test captures the default-flag JSON
      output **before** the refactor (in a fixture file) and asserts
      it after (with credentials all monkeypatched-present).
- [ ] Recent-error surfacing only triggers on events with
      `type == "error"` and a `note` matching the token set
      (`401`, `403`, `auth`, case-insensitive). Test covers: no
      log file, log file with no errors, log file with non-credential
      error (`500`), log file with credential error (`403` against
      Neon).
- [ ] When `lead_with` is set but outside scope, doctor prints the
      "outside scope" advisory and proceeds with the requested
      scope; exit code is determined by the requested scope only.
- [ ] `tail_events` is the only mechanism used to read the log;
      grep test asserts the import in `check.py`.

### `lib/stages.py` (this plan)

- [ ] Module created with `STAGE_ORDER`, `NEXT_COMPOSERS`,
      `NEXT_HINTS`, `RELEVANT_SKILLS`, `SUBSYSTEMS`,
      `STAGE_REQUIREMENTS`, `SKILL_REQUIREMENTS` and helpers
      `next_composer`, `relevant_skills`, `stage_subsystems`,
      `skill_subsystems`.
- [ ] Helpers raise `KeyError` on unknown stage/skill (not
      `ValueError`) — matches dict semantics; doctor catches and
      re-raises as exit-`2`.
- [ ] `skills/awf-status/scripts/status.py` is updated to import
      `NEXT_COMPOSERS` and `NEXT_HINTS` from `lib.stages`; the
      local definitions and the TODO comment are removed.
- [ ] `tests/lib/test_stages.py` covers: closed-set integrity for
      `STAGE_REQUIREMENTS` and `SKILL_REQUIREMENTS`; key parity
      between `NEXT_COMPOSERS` / `RELEVANT_SKILLS` / `STAGE_ORDER`;
      `next_composer("scale") is None`; helper `KeyError` on
      unknown input.
- [ ] `test_awf_status.py::test_next_composer_for_stage` continues
      to pass without modification.

### Cross-cutting

- [ ] Both skills idempotent + safe (read-only; no mutation; no
      provider mutation).
- [ ] `ruff check skills/awf-help/ skills/awf-doctor/ lib/stages.py`
      clean.
- [ ] `mypy --strict lib/stages.py skills/awf-help/scripts/help.py
      skills/awf-doctor/scripts/check.py` clean modulo pre-existing
      `lib/state.py:113` advisory.
- [ ] Test coverage: new file `tests/skills/test_awf_help.py`
      (~20 tests); `tests/skills/test_awf_doctor.py` created or
      extended (~15 tests); `tests/lib/test_stages.py` (~6 tests).
- [ ] Full suite green: 362 baseline → ~403 total, no regressions
      in plans 001–012.
- [ ] Both SKILL.md files carry the LLM-directive line, the flag
      table, the exit-code table, and one usage example per
      non-trivial mode/flag combo.

## Decisions

1. **Replace `awf-help` body fully; extend `awf-doctor` in place.**
   D-008 reshapes the help contract entirely (the old skill is a
   flat catalogue; the new one is a three-mode context-aware tool),
   so a full rewrite is cheaper than retrofitting. D-009 keeps
   default doctor behaviour unchanged, so we extend `check.py`
   instead of rewriting. This matches the two skills' D-decisions
   literally and avoids a "doctor rewrite" risk that isn't asked for.

2. **One mapping module: `lib/stages.py`, not `lib/doctor.py`
   (which D-009 suggested) + `lib/help.py`.** D-009 names
   `lib/doctor.py` as the home for the stage→check mapping. But
   `awf-help` needs the *same* stage→relevant-skills concept, and
   `awf-status` already needs the stage→next-composer mapping
   (plan_012 left a TODO). Three modules with overlapping stage
   keys would drift. One `lib/stages.py` with all stage-keyed
   constants is the right size — five constants, four helpers,
   ~120 lines. The cross-skill coupling D-009 imagines is real but
   it's *stage* coupling, not *doctor* coupling.

3. **Promote `NEXT_COMPOSERS` from `status.py` in this plan.**
   plan_012 § Decisions item 9 deferred this with an explicit
   "awf-help (plan_013) will inherit the move cost". This is that
   cost. The promotion is a five-line diff in `status.py` plus the
   `lib/stages.py` addition; the existing `test_awf_status.py`
   tests pin the values so the move is safe.

4. **`awf-help` gets a script (`help.py`); it is no longer
   body-only.** D-008 requires mode auto-detection based on
   filesystem state. Body-only skills cannot conditionally branch
   on `Path.cwd()`. The body becomes a thin instruction wrapper
   pointing at `uv run ".../help.py"`, matching `awf-status` and
   `awf-log` (plan_011/012).

5. **`--overview` accepts the old `--pipeline` flag as a deprecated
   alias.** The current `awf-help` has `--pipeline`. We add
   `--overview` as the new canonical flag (matches D-008's wording),
   and accept `--pipeline` for one cycle as a deprecation-warning
   alias (`stderr: --pipeline is renamed --overview; please update`).
   Removed in plan_015 or whenever next we touch this skill.

6. **No strict JSON-schema constant for `awf-help`.** Unlike
   `awf-status` (plan_012), `awf-help` is not consumed by
   composers; its JSON output is for humans / the LLM. Adding a
   `HELP_JSON_SCHEMA` would buy nothing and pin a shape we may
   want to evolve as the suite grows. AC-list pins the shape;
   tests assert key presence; that is enough.

7. **Recent-error surfacing is always-on, not gated behind a
   `--with-recent-errors` flag.** The cost is one `tail_events(50)`
   call (~milliseconds). The benefit is that the LLM's default
   doctor invocation surfaces credential issues without needing to
   know about an extra flag. D-009 names this as part of the
   default behaviour-change envelope — it's not a "scope flag";
   it's a behaviour upgrade orthogonal to scope.

8. **`SUBSYSTEM_CHECKS` lives in `check.py`, not in `lib/stages.py`.**
   `lib/stages.py` is the data layer (which subsystem ids belong to
   which stage); `check.py` is the behaviour layer (how to check
   one subsystem). Keeping the behaviour layer with the doctor skill
   means the doctor refactor is bounded to one file. The data layer
   in `lib/` is consumed by both doctor and help.

9. **Doctor refactor (extracting per-subsystem check functions) is
   done **before** the flag work, and is itself a separate commit
   with a "golden" output test.** This isolates the refactor risk
   (the refactor must be byte-for-byte identical in the default
   path) from the new-feature risk (scoping flags, recent-error
   surfacing). plan_011 followed the same split (refactor commit,
   then feature commit).

10. **In-project mode does not run drift checks or read
    `.awf/infra.json`.** That is `awf-status`'s job. D-008's
    "atomic skills relevant here" is purely a stage-keyed lookup;
    it does not depend on infra state. Help stays cheap and
    side-effect-free.

11. **Tests follow plan_011/012's split.** Subprocess-driven for
    end-to-end shape and exit codes (`subprocess.run([..., 'help'])`);
    direct `main()` calls with monkeypatching for unit logic
    (mode detection, scope resolution, recent-error tokeniser).
    Provider clients are not invoked; doctor's per-subsystem
    functions are monkeypatched.

## Tensions for Reviewer

1. **T1 — JSON-schema constant for `awf-help`?**
   - (a) **No `HELP_JSON_SCHEMA`** (recommended): AC-list pins
         shape; tests assert presence. Pro: no schema-string
         maintenance overhead, can evolve freely. Con: contract
         is implicit; future consumers (composer? web UI?) have
         no machine-readable spec.
   - (b) **Yes**, mirror plan_012's `STATUS_JSON_SCHEMA` pattern.
         Pro: symmetry; documented contract. Con: nobody consumes
         `awf-help` JSON programmatically today, so we'd be
         locking a shape that wants to evolve.
   - (c) **TypedDict** instead of JSON-Schema. Pro: type-safety
         from inside Python; less verbose. Con: doesn't help any
         non-Python consumer.
   Recommend (a). plan_012's schema constant was justified by
   composers consuming the JSON; `awf-help` has no such consumer.
   If one emerges, we add `HELP_JSON_SCHEMA` in the same shape.

2. **T2 — Credential-error token set: `{401, 403, auth}` only, or
   broader?**
   - (a) **Exact set named by spec** (recommended): the three
         tokens spec § C4 names. Pro: faithful to the spec; small
         false-positive surface. Con: misses `unauthorized`,
         `forbidden`, `expired token`, `invalid_grant`, etc.
   - (b) **Extended set** including `unauthorized`, `forbidden`,
         `expired`, `invalid_grant`, `permission denied`. Pro:
         catches more real cases. Con: spec drift; harder to
         reason about; we'd want to update the spec text first.
   - (c) **Regex-based** with a configurable list in `lib/stages.py`.
         Pro: extensible. Con: over-engineered for one feature.
   Recommend (a). The spec text is the contract; if practice
   shows we miss real credential errors, we propose a spec edit
   and add tokens in a later plan. Configurable extension is
   YAGNI for v1.

3. **T3 — Empty-`SKILL_REQUIREMENTS` skill: exit `0` with note, or
   refuse?**
   - (a) **Exit `0` with note** (recommended): "No preflight
         checks required for this skill." Pro: gives the LLM a
         clear signal it can proceed. Con: invites callers to use
         doctor as a generic "is this skill known?" check, which
         it isn't.
   - (b) **Exit `2` "no checks for this skill"**. Pro: forces the
         caller to know whether a preflight is meaningful. Con:
         the LLM has to special-case the exit code; in practice
         it just skips doctor on those skills.
   - (c) **Auto-fall-back to `--for-stage <current>`**. Pro:
         always returns something useful. Con: silently changes
         scope; surprising.
   Recommend (a). Exit `0` is the honest answer ("nothing to
   check, you're good"); the note tells humans what happened.
   Exit `2` for a known skill name is confusing.

4. **T4 — `--for-stage` ordering: preserve the curated
   `STAGE_REQUIREMENTS` list order, or sort alphabetically?**
   - (a) **Curated order** (recommended): the order in
         `STAGE_REQUIREMENTS[name]` is hand-tuned for human-readable
         output (e.g. for `mvp-play` we want Hetzner before Neon
         because provisioning order is host-first). Pro: matches
         operator mental model. Con: a future maintainer might
         re-sort the list without realising they're changing
         doctor output.
   - (b) **Alphabetical** in `check.py`. Pro: deterministic;
         decoupled from `lib/stages.py` list order. Con: loses
         the human-tuned semantics.
   - (c) **Sort by "fastest to check first"**. Pro: fail-fast.
         Con: requires runtime cost annotations.
   Recommend (a). A comment on `STAGE_REQUIREMENTS` ("order is
   semantic — doctor uses it as-is") guards against the
   re-sort risk.

5. **T5 — Recent-error surfacing: lead-with header always, or only
   when the subsystem is in scope?**
   - (a) **Always show the header, advisory if out of scope**
         (recommended): "Recent error in `neon` noted but outside
         `--for-stage landing` scope. Run `/awf-doctor` to
         investigate." Pro: the LLM still learns about the issue
         even when running a narrow check. Con: an extra line in
         narrow-scope output.
   - (b) **Suppress the header when out of scope**. Pro: keeps
         narrow-scope output clean. Con: silently hides
         information the operator needs.
   - (c) **Auto-widen scope to include the recent-error
         subsystem.** Pro: doctor does the right thing
         automatically. Con: undermines the `--for-stage X`
         contract ("I want to check stage X only"); surprising.
   Recommend (a). Doctor's job is to surface signal; the operator's
   `--for-stage X` is a *scope* hint, not a "shut up about
   anything else" hint. The advisory line is the minimal cost.

## Risks

- **`lib/stages.py` cross-skill coupling.** Three skills will
  import from it (`awf-help`, `awf-doctor`, `awf-status`); a wrong
  edit breaks all three. Mitigated by: (i) closed-set integrity
  test in `tests/lib/test_stages.py` (any new subsystem id
  triggers a test failure unless added to `SUBSYSTEMS`); (ii)
  helper functions raise `KeyError` on unknown input so typos
  fail loudly; (iii) the three consumer skills each have a unit
  test that imports from `lib.stages` and asserts a specific
  expected value (defence in depth).

- **Skill description scraping for `--overview` is fragile.**
  Reading every peer skill's `SKILL.md` frontmatter at runtime
  couples `awf-help` to file shape conventions. Mitigated by:
  (i) wrapping the scrape in `try/except Exception` and falling
  back to "name only"; (ii) a test injecting a malformed
  `SKILL.md` into a fixture `$AWF_HOME` and asserting graceful
  degrade; (iii) the scrape is `$AWF_HOME`-relative so it works
  the same on dev and on installed skill paths.

- **`detect_credential_error_subsystem` false positives.**
  A debug log that happens to contain the string `"401"` (e.g. a
  timestamp like `2026-06-01T04:01:..."` matching nothing
  intended) could fire the lead-with. Mitigated by: (i) the
  detector requires `type == "error"`; (ii) it inspects only the
  `note` field, not the whole event; (iii) the match is case-
  insensitive but token-bounded. If false positives prove
  noisy, T2 (b) tightens the match.

- **Refactor risk in `check.py`.** Extracting per-subsystem
  functions from the current monolithic `build_report` could
  silently change ordering or formatting. Mitigated by: (i)
  the "golden" output test captured **before** the refactor;
  (ii) ruff and mypy on the refactor commit; (iii) splitting
  the refactor into its own commit makes a regression easy to
  bisect.

- **Spec drift between `awf-help` `--overview` and reality.** As
  new skills are added by later plans, `RELEVANT_SKILLS` must be
  kept in sync. Mitigated by: (i) the integrity test asserts
  every key in `RELEVANT_SKILLS` resolves to an actually-existing
  skill directory under `$AWF_HOME/skills/`; (ii) a stale entry
  produces a test failure when the skill is removed.

- **`--for-skill` knowledge gap.** A new skill landing without a
  `SKILL_REQUIREMENTS` entry will be treated as "no preflight"
  (T3 (a)), which can mislead callers if the skill actually does
  need credentials. Mitigated by: (i) a CI-level integrity check
  that every skill named in `RELEVANT_SKILLS` has either an
  explicit `SKILL_REQUIREMENTS[name]` entry or is in an
  allowlist `SKILLS_WITHOUT_PREFLIGHT`; (ii) plan-doc note that
  adding a new skill requires touching `lib/stages.py`.

- **`--pipeline` deprecation alias drift.** The deprecation
  warning has to stay through one release; if forgotten, callers
  break. Mitigated by: (i) explicit `TODO(plan_015)` in
  `help.py` next to the alias; (ii) the warning text names the
  removal plan.

## Out of scope

- A `--for-composer <name>` flag (composers are sequences of
  skills; we already have `--for-skill` and `--for-stage`).
- Auto-running `awf-doctor` from `awf-help` (help stays pure-info).
- Help mode that consults `.awf/infra.json` (drift is `awf-status`'s
  job).
- `awf-teardown` skill (D2 plan); `awf-help` references it as a
  named-but-not-yet-shipped operation.
- Strict JSON-Schema for `awf-help` (T1).
- Configurable credential-error token set (T2).
- `awf-doctor --fix` (doctor stays a reporter).
- Per-subsystem cost annotation for `--for-stage` ordering (T4 (c)).
- Auto-widen scope on recent-error detection (T5 (c)).
- Removing `--pipeline` deprecation alias (plan_015 or later).
- Populating `RELEVANT_SKILLS["prescale"]` / `["scale"]` — those
  stages' atomic skills don't exist yet.

## Implementation order

1. **Create `lib/stages.py` with constants + helpers.** Add
   `tests/lib/test_stages.py` with closed-set integrity, key
   parity, and helper-`KeyError` tests. Run pytest: 362 + ~6 new.

2. **Promote `NEXT_COMPOSERS` / `NEXT_HINTS` from
   `skills/awf-status/scripts/status.py`** to imports from
   `lib.stages`. Remove the local constants and the TODO comment.
   Run pytest: `test_awf_status.py` must stay green. No new tests
   needed (existing tests pin the values).

3. **Refactor `check.py` — extract per-subsystem check functions.**
   Pull the inline credential/CLI checks out of `build_report`
   into the `SUBSYSTEM_CHECKS` dispatch table. **Behaviour must
   be byte-for-byte identical** in the default path. Add a
   "golden" snapshot test in `tests/skills/test_awf_doctor.py`
   (created if absent): monkeypatch every credential present,
   capture default-flag JSON output, store the expected shape in
   a fixture file, assert equality.

4. **Add `awf-doctor` flag plumbing.** argparse: `--for-stage`,
   `--for-skill`, with mutual-exclusion check. `ScopeDefault`,
   `ScopeStage`, `ScopeSkill` dispatch. Tests: known stage, known
   skill, unknown stage exits 2, unknown skill exits 2, both flags
   exits 2, empty-requirements skill exits 0 with note. ~7 tests.

5. **Add `detect_credential_error_subsystem` + lead-with rendering
   to `awf-doctor`.** Tests: no log, no errors, non-credential
   error, credential error in scope (lead-with), credential error
   out of scope (advisory). ~5 tests.

6. **Update `awf-doctor` SKILL.md.** Add flag table, exit-code
   table, LLM-directive line, one usage example per flag combo.

7. **Create `skills/awf-help/scripts/help.py` skeleton.** argparse
   (`--overview`, `--pipeline` deprecated alias, `--json`),
   dispatch on mode, three render-function stubs. Run pytest:
   stays green; no new tests yet.

8. **Implement fresh-start mode.** Render function + tests:
   subprocess from a `tmp_path` with no `.awf/`; assert exit 0 and
   the expected human / JSON output. ~3 tests.

9. **Implement in-project mode.** Render function + tests:
   fixture `.awf/project.json` at each stage; assert
   `next_composer`, `relevant_skills`, `common_operations` blocks.
   ~6 tests.

10. **Implement `--overview` mode + peer SKILL.md scraping.**
    Tests: full `$AWF_HOME` fixture with three peer skills; assert
    grouped-by-stage output; injection of a malformed `SKILL.md`
    asserts graceful degrade. ~5 tests.

11. **Add `--json` output for all three modes.** Tests assert key
    presence per mode, no provider imports (grep test). ~3 tests.

12. **Replace `skills/awf-help/SKILL.md`** with a thin wrapper:
    instruction to `uv run scripts/help.py`, LLM-directive line,
    flag table, exit-code table, examples for each mode.
    Delete the old body-only catalogue content (which `--overview`
    now generates dynamically).

13. **Polish:** mypy --strict on all three changed/new files,
    ruff, AC checkbox sweep, full-suite run (target ~403/403),
    PR description.

---

**Reviewer paragraph:** This plan ships C3 + C4 together because
they share a stage→X mapping that wants one home. New module
`lib/stages.py` carries `STAGE_ORDER`, `NEXT_COMPOSERS`,
`NEXT_HINTS`, `RELEVANT_SKILLS`, `SUBSYSTEMS`,
`STAGE_REQUIREMENTS`, `SKILL_REQUIREMENTS` and four helpers;
`skills/awf-status/scripts/status.py` migrates its local
`NEXT_COMPOSERS` / `NEXT_HINTS` to imports (fulfilling plan_012's
TODO). `awf-help` is replaced wholesale: a new `help.py` script
auto-detects fresh-start / in-project / `--overview` modes from
`ProjectAnchor.load(optional=True)`, renders in human or `--json`,
exits `0` always (except argparse usage errors), and never calls
external APIs or touches `.awf/infra.json`. The old body-only
catalogue is dropped; `--pipeline` survives as a one-cycle
deprecated alias for `--overview`. `awf-doctor` is extended in
place: a refactor commit first extracts per-subsystem check
functions into a `SUBSYSTEM_CHECKS` dispatch table (with a golden-
output test pinning byte-for-byte default behaviour), then a
feature commit adds `--for-stage <name>`, `--for-skill <skill>`
(mutually exclusive), and always-on recent-error surfacing via
`tail_events(50)` looking for `type == "error"` events with notes
matching `{401, 403, auth}`. Lead-with header always renders;
if the implicated subsystem is outside the requested scope, an
advisory header still names it. Empty-requirements skills exit `0`
with a note (T3 (a)); unknown stage/skill names exit `2`. Key
decisions: (D1) replace help, extend doctor, matching each
D-decision's posture literally; (D2) one `lib/stages.py` instead of
D-009's suggested `lib/doctor.py` + an implicit `lib/help.py`,
because the cross-coupling is stage-coupling not doctor-coupling;
(D3) deliver plan_012's promised `NEXT_COMPOSERS` promotion here;
(D7) recent-error surfacing always-on, not flag-gated, since the
cost is one log tail. Tensions: (T1) no strict JSON schema for
help (no programmatic consumer yet); (T2) credential-error token
set = exactly `{401, 403, auth}` per spec; (T3) empty-requirements
skill exits 0 with note; (T4) `STAGE_REQUIREMENTS` curated order
preserved by doctor; (T5) lead-with header advisory when out of
scope. Main risks are `lib/stages.py` becoming a coupling point
(mitigated by closed-set integrity tests + KeyError-on-unknown),
peer-SKILL.md scraping fragility in `--overview` (mitigated by
try/except + graceful name-only fallback), and the `check.py`
refactor silently changing default output (mitigated by the
golden-snapshot test taken before the refactor commit). Both
skills remain read-only; doctor's exit codes 0 / 1 / 2 are
unchanged in the default path.

---

### Pass 1 (2026-06-01)

**Reviewer:** Reviewer | **Status at entry:** draft | **Status at exit:** review-approved

**T1 — No strict JSON-schema constant for `awf-help`.** Approved. The plan's argument is sound: `awf-status` earned a `STATUS_JSON_SCHEMA` constant because composers consume its JSON programmatically; `awf-help` has no current programmatic consumer and the shape is likely to evolve as stages are populated. AC-list key-presence assertions in `test_awf_help.py` are sufficient for v1. Carry-note for implementer: add a `# schema_version: 1` comment inside the emitted JSON (not a constant, just a field) so any future consumer can gate on it without a schema migration.

**T2 — Exact token set `{401, 403, auth}`.** Approved. The spec text at § C4 names exactly these three tokens; implementing a broader set would drift from the spec without a spec amendment. The conservative posture is correct: false negatives degrade to a full sweep (acceptable), while false positives from an extended set risk noisier lead-with headers. The `_infer_subsystem` fall-through to the full sweep is an adequate safety net. Note: the `type == "error"` gate plus `note`-field-only match already substantially limit false positives from numeric strings in other fields (e.g. timestamps); the risk section correctly identifies this.

**T3 — Empty-requirements skill exits 0 with note.** Approved. Exit 2 for a known skill name with an empty requirements list is semantically wrong — the skill *is* known, it simply has no preflight. Exit 0 with a clear "No preflight checks required" note is the honest answer and avoids LLM special-casing. One sharpening: the AC should require the note to appear on stdout (not stderr) so it is visible when `--json` is not used, and the JSON equivalent should carry `"preflight_required": false` as a top-level field alongside the check list (currently the plan does not specify this). Implementer should add this to the `--for-skill` JSON output.

**T4 — Curated `STAGE_REQUIREMENTS` order preserved by doctor.** Approved. The plan's mitigation is correct: a comment on `STAGE_REQUIREMENTS` marking the order as semantic is sufficient guard. One implementation note: the `stage_subsystems()` helper must return a `list[str]` preserving insertion order (not a `set`); the AC for `test_stages.py` should add an assertion that `stage_subsystems("landing")` returns the exact sequence `["cloudflare", "namecheap", "fathom", "gsc", "bing", "git", "node", "wrangler"]` — order matters because doctor's human output groups checks in that order.

**T5 — Lead-with header always renders, advisory if out of scope.** Approved. Option (b) — suppressing the header when out of scope — would silently hide information the operator sent doctor to investigate. Option (c) — auto-widening scope — would subvert the `--for-stage X` contract. Option (a) is the minimal information-preserving choice. The advisory line is a single line and will not crowd narrow-scope output. No changes needed.

**D2 — `lib/stages.py` vs D-009's suggested `lib/doctor.py`.** Verdict: `lib/stages.py` is the right call and the divergence from D-009's naming is justified. D-009 names `lib/doctor.py` as the home for the stage→check mapping, but that was written before `awf-help`'s redesign clarified that *three* separate skills (`awf-status`, `awf-help`, `awf-doctor`) all need stage-keyed data. Three modules with overlapping stage constants would inevitably drift; a single `lib/stages.py` that is the data layer for all stage-keyed lookups is a tighter design. The plan correctly notes this is stage-coupling not doctor-coupling. `decisions.md` D-009 should receive an implementation note recording the `lib/stages.py` substitution (suggested wording: "Implementation note (plan_013): stage→check mapping consolidated into `lib/stages.py` alongside `NEXT_COMPOSERS`, `RELEVANT_SKILLS`, etc. `lib/doctor.py` not created; `check.py` owns the `SUBSYSTEM_CHECKS` dispatch table as the behaviour layer."). This is the only documentation gap; the plan itself explains the rationale in § Decisions item 2.

**Overall verdict.** No blocking issues. Two carry-notes for the implementer: (1) add `"preflight_required": false` to the `--for-skill` JSON output for skills with empty requirements (T3 sharpening); (2) add an order-asserting test for `stage_subsystems()` in `test_stages.py` (T4 sharpening). Both are additive and do not affect the plan's architecture or AC list count. Record the `lib/stages.py` substitution in D-009. Plan is approved to move to implementation.
