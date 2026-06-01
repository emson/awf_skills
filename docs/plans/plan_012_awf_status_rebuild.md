# Plan 012 — `awf-status` rebuild (canonical "where am I")

**Status:** ready
**Phase:** C
**Spec refs:** [`spec.md` § C2](../spec.md), [`decisions.md` D-007](../decisions.md#d-007), [`07-multi-stage-architecture.md`](../07-multi-stage-architecture.md), [`08-logging.md`](../08-logging.md)
**Owner (current):** Reviewer
**Created:** 2026-06-01
**Updated:** 2026-06-01

## Status log

| Date | Status | Actor | Note |
|------|--------|-------|------|
| 2026-06-01 | draft | Lead | Initial plan. Replaces existing `skills/awf-status/`. Ships C-phase canonical "where am I" surface with fixed-order output (Project/Stage/Drift/Recent/Next), drift detection v1 across CF zone + Hetzner servers + Neon project+branch, `--json` and `--verbose`. Encodes plan_005–011 lessons (subprocess tests, atomic skill anatomy, JSON parity, hard-reject outside project where appropriate, lib I/O ownership, DRY tail via `lib.log.tail_events`). |
| 2026-06-01 | reviewed | Reviewer | Pass 1 complete. All five tensions resolved. No blocks. Ready for implementation. |

## Goal

Rebuild `skills/awf-status/` as the canonical "where am I"
surface for the post-state-model awf-skills runtime. The current
implementation reports per-provider state in ad-hoc form, predates
the `.awf/project.json` / `.awf/infra.json` split, and does not
know about stages or the event log. D-007 makes `awf-status` the
single answer to "what stage is this project at and what should
happen next" — for humans, for the LLM, and for composers that
need a resumability hint.

Output is **fixed order**:

```
Project: <slug> (<domain>)
Stage:   <stage>
Drift:   <none|description>
Recent:  <last 5 log events, one per line>
Next:    <composer skills for stage+1>
```

`--json` emits the machine-readable equivalent; `--verbose`
extends the event tail (5 → 20) and prints per-provider state
detail (zone_id, server ip/role, neon project_id, etc.);
`--brief` is **deferred** to a follow-up (D-007 "revisit if"
clause).

Out of scope (this plan):
- DNS-record-level drift (each A/CNAME compared to live CF).
- Cloudflare Pages drift (project existence + deployed commit).
- Fathom site drift.
- GSC verification / sitemap drift.
- Hetzner load balancer drift (S4+ resource; field exists in
  `infra.hetzner.lb_id` but check ships in the S4 plan).
- `--brief` mode (D-007 revisit-if; defer until usage demands it).
- Cost rollup / spend snapshot in the status view.
- Cross-project status (e.g. `awf-status --all`).

These are listed in the "not yet checked" footer of the human
output so callers know they are intentional gaps, not bugs.

## Context

- [`spec.md § C2`](../spec.md) (lines 370–385) fixes the output
  order, the `--json` requirement, the `--verbose` semantics, the
  no-project behaviour ("Stage: none" + help suggestion, exit 0),
  and the LLM-directive line ("Run this first when location is
  uncertain"). Drift detection is mandated: "compares state files
  against world via per-provider clients; lists divergences and
  names the atomic skill that would re-converge."
- [`decisions.md` D-007](../decisions.md#d-007) closes
  D-OPEN-G. It also pins the alternatives rejected (per-provider
  only; separate `awf-where`) and the revisit-if clause for a
  future `--brief`.
- [`lib/state.py`](../../lib/state.py):
  `ProjectAnchor.load()` (walks up to `.awf/project.json`),
  `Infra.load()` (walks up to `.awf/infra.json`, present from
  S3 onward), `Shared.load()` (registry-level). The status skill
  must use `ProjectAnchor.load()` for the locator and degrade
  cleanly if no anchor is found. `ProjectAnchor.stage` is one of
  `landing | demo | mvp-play | prescale | scale`.
- [`lib/log.py`](../../lib/log.py): plan_011 added the public
  read API. `tail_events(path, n)` is the **single source of
  truth** for the `Recent:` block — the skill must reuse it (DRY)
  rather than re-implement reverse-block seek. `latest_session_id`
  and `read_events` are used for the idle-detection check
  (D-OPEN-J Tier 1).
- [`lib/cf/zones.py`](../../lib/cf/zones.py): `get_zone(client,
  domain_name)` returns the live zone or `None`. The drift
  comparator looks up by domain (not by id — CF's API supports
  both, and domain is the more stable handle when the local
  state has a stale id).
- [`lib/hetzner/resources/servers.py`](../../lib/hetzner/resources/servers.py)
  exposes a `_Servers` collection. The drift comparator iterates
  `infra.hetzner.servers` and asks Hetzner whether each `id`
  exists and what its status is (we treat anything other than
  `running` as drift).
- [`lib/neon/resources/projects.py`](../../lib/neon/resources/projects.py)
  and [`branches.py`](../../lib/neon/resources/branches.py): the
  drift comparator looks up `infra.neon.project_id` and
  `infra.neon.branch_id`. A missing project subsumes a missing
  branch (don't double-report).
- [`skills/awf-log/scripts/log.py`](../../skills/awf-log/scripts/log.py)
  is the style template: argparse, `--json` flag pattern,
  exit-code table, autouse log-state cleanup in tests, fixture
  trees in `tmp_path`. We follow the same shape.
- Plan_005 / plan_006 establish that the Hetzner and Neon
  clients return typed objects and raise typed errors
  (`HetznerError`, `NeonError`). The status skill treats *any*
  client-side exception as `unknown` for that provider (not as a
  drift entry), and records the underlying error message under
  `--verbose`.

## Architecture overview

```
skills/awf-status/
├── SKILL.md                 # frontmatter + LLM directive + flags + exit codes
└── scripts/status.py        # uv-script, argparse, ~350-450 lines
```

The existing `skills/awf-status/scripts/check.py` (and any other
files in that directory) is **deleted**. The new script is named
`status.py` for consistency with plan_011 (`log.py`) — the file
name matches the verb, not "check".

### Top-level flow

```
main() →
  1. anchor = ProjectAnchor.load(start=cwd, optional=True)
  2. if anchor is None:
       print no-project block (`Stage: none` + /awf-help hint); exit 0
  3. infra  = Infra.load(start=anchor.path, optional=True)   # may be None pre-S3
  4. drift  = run_drift_checks(anchor, infra)                # list[DriftEntry]
  5. recent = lib.log.tail_events(anchor.root/".awf/log.jsonl", n)
  6. next_  = next_composer_for_stage(anchor.stage)
  7. idle   = check_idle(anchor.root/".awf/log.jsonl")       # bool/None
  8. render(anchor, infra, drift, recent, next_, idle, mode)
```

Two render functions: `render_human(...)` and `render_json(...)`.
Both consume the same intermediate `StatusReport` dataclass so
the only difference is presentation. This is the same pattern
plan_011 used for `replay`.

### Drift detection (v1 scope)

A `DriftEntry` is:

```python
@dataclass
class DriftEntry:
    provider: str           # "cloudflare" | "hetzner" | "neon"
    resource: str           # "zone" | "server:<id>" | "project" | "branch"
    state_value: str        # what passport/infra says exists
    world_value: str        # what the provider says (or "missing" / "unknown")
    suggested_skill: str    # atomic skill that would re-converge
    note: str = ""          # one-line human explanation
```

The three v1 checks:

1. **Cloudflare zone.** Read `passport.cloudflare.zone_id` and
   `passport.domain`. Call `lib.cf.zones.get_zone(client, domain)`.
   - Zone exists and id matches → no drift.
   - Zone exists but id differs → drift `world_value="zone_id=<live>"`,
     suggest `awf-setup-domain` (it will reconcile the id).
   - Zone does not exist → drift `world_value="missing"`, suggest
     `awf-setup-domain`.
   - Credentials missing → `world_value="unknown"`, no
     suggestion, note `"CLOUDFLARE_API_TOKEN not configured"`.
2. **Hetzner servers.** For each `server` in
   `infra.hetzner.servers`: call `hetzner.servers.get(server.id)`.
   - Found and `status == "running"` → no drift.
   - Found and status is `off`/`stopping`/`unknown` → drift
     `world_value="status=<s>"`, suggest `awf-hetzner-provision`
     (which is idempotent and will reconverge).
   - Not found (404) → drift `world_value="missing"`, suggest
     `awf-hetzner-provision`.
   - Credentials missing → `world_value="unknown"`.
3. **Neon project + branch.** Read `infra.neon.project_id` and
   `infra.neon.branch_id`.
   - Look up the project. Missing → drift `world_value="missing"`
     for the project; **skip the branch check** (a missing
     project subsumes a missing branch). Suggest
     `awf-neon-provision`.
   - Project found, branch missing → drift on the branch, suggest
     `awf-neon-provision`.
   - Credentials missing → both report `world_value="unknown"`.

Each check is wrapped in `try/except Exception` at the provider
level — a network error or unexpected API shape gets reported as
`world_value="unknown"` with the exception message captured for
`--verbose`. **No exception bubbles past the check function.**

If `infra` is `None` (project is at S1/S2 and has no
`.awf/infra.json` yet), Hetzner and Neon checks are skipped
entirely (the resources don't exist by design, so they can't
have drifted). The CF zone check still runs (the zone exists
from S1).

### Idle detection (D-OPEN-J Tier 1)

After reading the recent events, also scan backward for the most
recent `session.end` event (any session) and parse its
`ended_at` timestamp. If `now - ended_at > 90 days`, set
`report.idle = True` and the human render adds a one-line
notice:

```
Idle:    last session ended 142 days ago — project may be stale.
```

The JSON form includes `"idle": {"days_since_last_session": 142}`
(or `null` if no `session.end` exists yet — fresh project).

This is a **soft warning, not a drift entry.** Idle does not
contribute to the exit code; it is informational.

### "Next" mapping (composer for stage+1)

| Current stage | Next composer |
|---------------|---------------|
| `landing` | `awf-stage-demo` |
| `demo` | `awf-stage-mvp-play` |
| `mvp-play` | `awf-stage-prescale` |
| `prescale` | `awf-stage-scale` |
| `scale` | `(none — terminal)` |

For `scale`, the `Next:` line reads `"none — at terminal stage;
operate via atomic skills"`. The JSON form sets
`"next_composer": null`.

The mapping lives in a module-level dict in `status.py`. It is
**not** in `lib/` because no other skill needs it yet (YAGNI).
If `awf-help` (plan_013, C3) needs the same mapping, we move it
to `lib/stages.py` then; this plan does not pre-promote.

### Human render — exact shape

```
Project: <slug> (<domain>)
Stage:   <stage>
Drift:   <none | first line of first drift entry>
         <one indented line per additional drift entry>
Recent:  <event 1 short form>
         <event 2 short form>
         <event 3 short form>
         <event 4 short form>
         <event 5 short form>
Next:    <next composer> — <one-line hint>

Not yet checked: dns_records, cloudflare_pages, fathom, gsc, hetzner_lb
```

- "Short form" of an event is `<ts> <type> <skill?> <note?>` —
  ISO timestamp truncated to seconds, event type, the `skill`
  field if present, and a truncated note. Single line, no JSON.
- The `Not yet checked:` footer is **always present** and lists
  the v1-deferred check names. It tells callers (especially the
  LLM) that these checks are not "passing"; they are "not yet
  implemented". Without this footer, "Drift: none" is misleading.
- If `--verbose`, after the main block emit a `--- details ---`
  divider then:
  - 20 recent events instead of 5;
  - Per-provider state block (`zone_id`, `pages_project`,
    `fathom_site_id`, `hetzner.servers`, `neon.project_id` /
    `branch_id`, `kamal.last_deploy_image`);
  - Captured error messages for any `world_value="unknown"`
    entries.

### JSON render — schema

The schema is documented in `lib/state.py` as a docstring on a
new `STATUS_JSON_SCHEMA: Final[str]` constant (path required by
spec § C2 AC: "`--json` output validates against a documented
schema in `lib/state.py`"). The schema is JSON-Schema draft-07,
embedded as a string literal; the comment above it explains the
field semantics. A small unit test parses the schema and
validates a real status output against it (using `jsonschema`
which is already a dev dep — confirm in Implementation step 1).

Shape:

```json
{
  "schema_version": 1,
  "project": {"slug": "...", "domain": "...", "root": "/abs/path"},
  "stage": "landing",
  "drift": [
    {
      "provider": "cloudflare",
      "resource": "zone",
      "state_value": "abc123",
      "world_value": "missing",
      "suggested_skill": "awf-setup-domain",
      "note": "Zone for example.com not found in Cloudflare."
    }
  ],
  "recent": [ { /* raw event object */ } ],
  "next_composer": "awf-stage-demo",
  "idle": {"days_since_last_session": 142} | null,
  "not_yet_checked": ["dns_records", "cloudflare_pages", "fathom", "gsc", "hetzner_lb"],
  "verbose": false,
  "details": { /* present only under --verbose */ }
}
```

The no-project case has its own shape:

```json
{
  "schema_version": 1,
  "project": null,
  "stage": "none",
  "hint": "No .awf/project.json found. Run /awf-help or /awf-create-project."
}
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Status produced successfully (drift present **or** none — drift is information, not a failure). Also the no-project case. |
| `4`  | Argument validation failure (unknown flag combo, malformed `--verbose`). |

Codes `1`, `2`, `3` are **unused**. Specifically:
- No project does **not** exit 1 (spec § C2 says exit 0 with a
  help suggestion).
- Missing credentials do **not** exit 2 (we degrade to `unknown`
  per the plan brief).
- Provider network errors do **not** exit 3 (same — captured as
  `unknown`).

This is intentionally narrower than the plan_005–010 exit-code
table. The status skill is a read-only reporter; the **whole
point** is that it never fails on partial information. If the
operator wants strict checking, they call `awf-doctor`.

### Per sub-command detail

There are no sub-commands. `awf-status` is a single command with
flags (`--json`, `--verbose`). This is deliberate — D-007's
output contract is fixed. Sub-commands would mean two CLIs
masquerading as one.

## Acceptance criteria

Spec § C2 (verbatim):

- [ ] No project anchor → `Stage: none` + help suggestion. Exit 0.
- [ ] Drift detection: compares state files against world via
      per-provider clients; lists divergences and names the
      atomic skill that would re-converge.
- [ ] `--json` output validates against a documented schema in
      `lib/state.py`.
- [ ] LLM-directive line in SKILL.md: "Run this first when
      location is uncertain."

Plan-specific:

- [ ] Output order is exactly `Project / Stage / Drift / Recent
      / Next` followed by a `Not yet checked:` footer. The
      footer enumerates `dns_records`, `cloudflare_pages`,
      `fathom`, `gsc`, `hetzner_lb`.
- [ ] Drift check covers Cloudflare zone, Hetzner servers,
      Neon project+branch. Each check has at least one
      "missing" test and one "unknown" (credentials missing)
      test.
- [ ] Missing credentials produce a drift entry with
      `world_value="unknown"` and no `suggested_skill`, **not**
      an error. The skill exits 0 in this case.
- [ ] Any unexpected provider exception is caught and converted
      to `world_value="unknown"` with the captured message
      surfaced only under `--verbose`. Tests assert that a
      raised `HetznerError("boom")` does not break the run.
- [ ] `Recent:` is rendered from `lib.log.tail_events(...)` —
      the script does **not** re-implement reverse-block read.
      A grep-level test asserts the import.
- [ ] `Next:` maps stages per the table above, and reads
      `"none — at terminal stage"` for `scale`.
- [ ] `--verbose` extends the event tail to 20 and prints
      per-provider state detail.
- [ ] `--json` emits the schema above and a unit test validates
      a real status output against `lib.state.STATUS_JSON_SCHEMA`
      with `jsonschema.validate`.
- [ ] No-project JSON form has `"stage": "none"`, `"project":
      null`, and the help `"hint"` field. Schema accepts both
      shapes via `oneOf`.
- [ ] Idle detection: when the most recent `session.end` is >90
      days old, the human output adds an `Idle:` line and the
      JSON output sets `idle.days_since_last_session`. Pure
      informational; does not affect exit code.
- [ ] Existing `skills/awf-status/scripts/check.py` and any
      other obsolete files are **deleted** in the same commit
      that adds `scripts/status.py`. SKILL.md is replaced.
- [ ] `mypy --strict skills/awf-status/scripts/status.py` clean.
- [ ] `ruff check skills/awf-status/ lib/state.py` clean.
- [ ] `tests/skills/test_awf_status.py` covers each render
      mode, each drift provider (× found / missing / unknown /
      exception), the idle path, the no-project path, the
      `--verbose` path, and the JSON-schema validation path.
      Target ≥ 30 tests, subprocess-driven for top-level paths
      and direct-`main()` for unit-level paths (mirroring
      plan_011's split).
- [ ] Full suite green: 317 baseline + ~30 new = ~347 tests, no
      regressions in plans 001–011.
- [ ] SKILL.md has the LLM-directive line, the flag table, the
      exit-code table, and a usage example for each flag combo.

## Decisions

1. **Replace the existing `skills/awf-status/` outright.** The
   current implementation predates the state-model split and
   reports per-provider state without stage awareness. There is
   no useful subset to preserve. The new `status.py` is a fresh
   file; `check.py` is deleted in the same commit. Coexistence
   would mean two skills with the same name doing different
   things — exactly the ambiguity D-007 rejects.

2. **Drift checks are sequential, not parallel.** Three quick
   API lookups, ~3–5 seconds in the worst case. `asyncio`
   gymnastics add code and obscure stack traces for trivial
   wall-clock savings. If the v2 drift surface (DNS records,
   Pages, Fathom, GSC) pushes wall time past ~15 seconds,
   revisit. Premature optimisation otherwise.

3. **`--brief` is deferred.** D-007's "revisit if" clause names
   `--brief` as the response if the output gets too tall. With
   five fixed lines + a footer, the output is already brief.
   Adding `--brief` now would commit us to a contract we don't
   need; defer until either an LLM complains or the verbose
   output noticeably swamps a chat window.

4. **`--verbose` extends event tail 5 → 20 and prints
   per-provider state.** Two effects in one flag because both
   are "I want more detail." Splitting them (`--events 20` and
   `--show-state`) would multiply flag combinations without
   buying anyone anything; D-007 explicitly lumps them together.

5. **Drift comparator catches all provider exceptions and
   reports `unknown`.** A `HetznerError`, a `NeonError`, an
   `httpx.ConnectError`, or a stray `KeyError` from an
   unexpected API shape all collapse to `world_value="unknown"`.
   This is the same posture as "credentials missing": the status
   skill is a reporter, not an enforcer. The verbose output
   keeps the captured message so an operator can debug.

6. **JSON schema lives in `lib/state.py`, not in a new file.**
   Spec § C2 names `lib/state.py` explicitly. We add a
   `STATUS_JSON_SCHEMA: Final[str]` constant with a docstring
   that explains each field. Co-locating with the state models
   matches the schema's purpose (it describes a view over the
   state model). A separate `lib/status_schema.py` would split
   the knowledge across two files for no benefit — the same
   logic that placed the read API in `lib/log.py` for plan_011.

7. **No `--project <path>` flag (yet).** plan_011 deferred the
   same flag; we defer it too for consistency. `cd` to the
   project root is the canonical entry. If multi-project tooling
   (e.g. `awf-status --all` in a later plan) demands it, we add
   it in one place rather than piecemeal.

8. **Idle threshold: 90 days.** D-OPEN-J Tier 1 doesn't pin a
   number. 90 days is the longest a project can plausibly be
   "actively worked on but quiet" — credential rotations,
   provider drift, and stale npm deps all become likely past
   that mark. Soft warning only; if 90 days proves too tight in
   practice we widen to 180.

9. **The stage→composer mapping lives in the skill, not in
   `lib/`.** It's a five-row table. Promoting it to `lib/` now
   would create a dependency that `awf-help` (plan_013) can
   adopt with a one-line move when the time comes. YAGNI;
   plan_013 will inherit the move cost.

10. **Tests follow plan_011's split.** Subprocess-driven for
    end-to-end (output shape, exit codes, JSON schema validity);
    direct `main()` calls for unit-level (drift comparator,
    idle detector, render functions). Same autouse fixtures as
    `test_awf_log.py` for log-state cleanup; provider clients
    are monkeypatched (no live calls).

## Tensions for Reviewer

1. **T1 — "Not yet checked" footer: always present, or only
   when relevant?**
   - (a) **Always present** (recommended; what this plan ships):
         the line `Not yet checked: dns_records, cloudflare_pages,
         fathom, gsc, hetzner_lb` is printed every time. Pro: the
         operator and the LLM cannot mistake "Drift: none" for
         "everything is checked and fine." Con: adds visual
         clutter to the otherwise-clean five-line output.
   - (b) **Suppress when all listed checks would be no-ops** —
         e.g. at S1/S2 the Pages and Hetzner-LB checks don't
         apply because those resources don't exist yet. Pro:
         less noise. Con: now the footer is conditional on
         stage and the operator has to remember which checks
         apply when.
   - (c) **Suppress unless `--verbose`.** Pro: keeps the default
         tight. Con: the LLM's default invocation is *not*
         `--verbose`, and "Drift: none" silently overclaims.
   Recommend (a). The footer's job is to manage caller
   expectations about scope; suppressing it defeats the purpose.
   The visual cost is small (one line).

2. **T2 — Drift entry for "credentials missing": one entry per
   provider or one global entry?**
   - (a) **One entry per provider** (recommended): if CF, HZ,
         and Neon all lack credentials, three drift entries with
         `world_value="unknown"`. Pro: each tells the operator
         which env var to set. Con: noisy on a freshly-cloned
         repo before `awf-init`.
   - (b) **One global "credentials not configured" entry**.
         Pro: less noise. Con: collapses three independent
         problems into one; the operator can't tell whether one
         provider works and two don't.
   - (c) **Suppress entirely** when credentials are missing,
         show only a single info line at the bottom.
         Pro: cleanest output. Con: hides drift status; an
         operator who knows their CF token is set will see
         "Drift: none" and assume CF is fine when actually we
         didn't check.
   Recommend (a). Per-provider granularity matches the rest of
   the drift surface; (b) and (c) are forms of pretending we
   know more than we do. If noise on a fresh clone is real,
   `awf-doctor` is the right tool for the credentials story.

3. **T3 — Should the suggested re-converge skill be a hard
   string or a structured reference?**
   - (a) **Hard string** (recommended): `"awf-setup-domain"` is
         a string field. Pro: simple JSON. Con: typo-resistance
         relies on tests.
   - (b) **Enum / Literal type** in the dataclass with a
         documented list in `lib/state.py`. Pro: mypy enforces
         the name set. Con: every new skill needs a one-line
         enum update.
   - (c) **Skill object** with name + a brief help string.
         Pro: rich. Con: schema grows; over-engineered for one
         field.
   Recommend (a). Skill names are CLI surface and don't change
   often; the type-safety benefit of (b) is real but the
   maintenance tax is wrong for the first ship. If a skill is
   renamed, tests catch it via the rendered output.

4. **T4 — Idle detection: based on `session.end` only, or also
   on the file mtime of `.awf/log.jsonl`?**
   - (a) **`session.end` only** (recommended): rigorous; a
         project with `session.start` events but no `session.end`
         events (every session crashed) is treated as "no
         sessions yet" for idle purposes. Pro: well-defined.
         Con: pathological case under-reports idleness.
   - (b) **Last event of any type.** Pro: catches the crashing
         project. Con: muddies the "session ended" semantics
         that plan_011 just locked.
   - (c) **mtime fallback** if no `session.end` exists. Pro:
         robust. Con: filesystem mtime is fragile (touch,
         rsync, archive restoration all change it).
   Recommend (a). The crashing-project case is rare and the
   replay narrative already exposes it. Idle detection is a
   soft warning; getting it slightly wrong on a pathological
   project isn't a bug.

5. **T5 — Exit code on unhandled exception in `main()`.**
   The drift comparators catch their own exceptions and degrade.
   But the **outer** `main()` could still throw — e.g. a
   corrupted `.awf/project.json` that `ProjectAnchor.load()`
   rejects with a Pydantic ValidationError. Options:
   - (a) **Let it propagate** (recommended): the project anchor
         being unparseable is a genuine "this project is broken"
         signal that the operator should see, not paper over.
         The traceback is informative.
   - (b) **Catch and report exit 4** with a one-line stderr.
         Pro: consistent with the "status never fails" posture.
         Con: hides the actual cause; the operator has to re-run
         with PYTHONFAULTHANDLER to see what's wrong.
   - (c) **Exit 0 with a human-readable "project anchor is
         corrupt" line** in the regular output. Pro: keeps the
         "always 0" promise. Con: same hiding problem.
   Recommend (a). The "never fail" posture applies to
   provider-side uncertainty (which the skill cannot resolve);
   it does not apply to local-state corruption (which the
   operator must resolve and which a clean traceback helps
   debug). plan_011's `awf-log` follows the same split.

## Risks

- **JSON-schema embedding in `lib/state.py`.** A multi-hundred-line
  JSON-Schema document inside a Python string is awkward to
  maintain and hard to diff. Mitigated by keeping the schema
  itself ≤ ~120 lines (the status shape is small), parsing it
  with `json.loads(STATUS_JSON_SCHEMA)` at test time to catch
  syntax errors, and pairing it with a hand-written unit test
  that validates a representative status object. If the schema
  grows past ~250 lines we move it to a `schemas/` JSON file
  and load it via `importlib.resources` — defer that until it
  bites.

- **Provider client signatures drift.** plan_005/006 set the
  client APIs; if a later plan changes (say)
  `lib.cf.zones.get_zone` to require an extra arg, this skill
  breaks. Mitigated by: (i) calling through the `__init__.py`
  re-exports (`lib.cf.zones.get_zone`) which are the documented
  surface; (ii) integration tests that monkeypatch the clients
  rather than mock the HTTP layer (so a signature change shows
  up as a TypeError immediately).

- **`tail_events` performance on huge logs.** plan_011 ships
  reverse-block seek which is O(n) in the number of returned
  events. Under `--verbose` we request 20 events; on a log with
  multi-KB `state.change` events that's still well under one
  block read. No new risk vs. plan_011.

- **Stage / next-composer mapping divergence with `awf-help`.**
  When plan_013 (C3, `awf-help`) implements its in-project mode,
  it will name the same `stage+1` composer. If the two skills
  disagree, the LLM gets contradictory guidance. Mitigated by:
  (i) tests in `test_awf_status.py` assert the exact mapping;
  (ii) plan_013 must reuse the same mapping (and at that point
  promotes it to `lib/stages.py`).

- **Idle threshold is a guess.** 90 days might be wrong. The
  risk is small (the warning is soft, advisory only) and
  reversible (one constant in `status.py`).

- **Deleting `check.py` orphans any caller scripts that invoke
  it directly.** Mitigated by: (i) `grep -r check.py
  skills/` to confirm no internal callers; (ii) SKILL.md
  documents that the script is `status.py` now; (iii)
  composers (S1–S3 in plan_010 etc.) call the skill by name
  (`awf-status`), not the script path, so they are unaffected.

- **`Not yet checked:` footer becomes stale.** As later plans
  add the deferred checks, the footer must shrink. Mitigated
  by: (i) the list lives as one module-level constant
  `NOT_YET_CHECKED` in `status.py`; (ii) each deferred check
  has a corresponding TODO comment in the same module pointing
  at the plan that will implement it.

## Out of scope

- DNS-record-level drift (planned for the post-MVP drift pass).
- Cloudflare Pages drift (project + deployed commit).
- Fathom site drift.
- GSC verification / sitemap drift.
- Hetzner LB drift (`infra.hetzner.lb_id`; S4+).
- `--brief` mode (D-007 revisit-if).
- `--project <path>` override flag.
- Cross-project status (`awf-status --all`).
- Cost / spend rollup in the status view.
- Re-converge actions (`awf-status --fix` or similar). The skill
  *names* the re-converge skill; running it is the operator's
  call.
- Schema migration for `STATUS_JSON_SCHEMA` (a `schema_version`
  field is reserved but no migration tooling ships).

## Implementation order

1. **`lib/state.py`: `STATUS_JSON_SCHEMA` constant.** Add the
   schema string under a `# Status JSON schema` section header,
   plus a small docstring on each top-level field. Add
   `jsonschema` to dev deps (verify it isn't already in
   `pyproject.toml`). Direct unit test in
   `tests/lib/test_state_schema.py` parses the schema and
   validates a hand-built example. Run pytest; baseline green
   (317 + ~3 new).
2. **`skills/awf-status/scripts/status.py` skeleton.** argparse
   (`--json`, `--verbose`), dispatch table, exit-code
   constants, `StatusReport` dataclass, `DriftEntry` dataclass,
   the stage→composer mapping, the `NOT_YET_CHECKED` constant.
   No drift logic yet — `run_drift_checks` returns `[]`.
   Render functions stubbed. Each subcommand stubbed.
3. **No-project path + render functions.** Implement
   `render_human` and `render_json` for both the regular and
   no-project shapes. Tests for both. Pytest stays green.
4. **`Recent:` block via `tail_events`.** Wire up
   `lib.log.tail_events` and the short-form event line. Test
   with a fixture log file. Verify import via grep test.
5. **`Next:` mapping.** Implement the table; tests for all five
   stages and the terminal case.
6. **Drift comparator: CF zone.** Read passport, call
   `lib.cf.zones.get_zone` via a monkeypatchable factory.
   Cover: found, missing, id-mismatch, credentials missing,
   exception. ~6 tests.
7. **Drift comparator: Hetzner servers.** Same pattern.
   Cover: found-running, found-stopped, missing, credentials
   missing, exception. ~5 tests.
8. **Drift comparator: Neon project + branch.** Same pattern.
   Cover: project missing (branch skipped), branch missing,
   credentials missing, exception. ~4 tests.
9. **`--verbose` extension.** 20 events, per-provider state
   block, captured error messages. ~3 tests.
10. **Idle detection.** Walk events backward to most recent
    `session.end`, compute delta. Tests with fixtures at the
    edge (89, 90, 91 days). ~3 tests.
11. **JSON-schema validation test.** Real `--json` output
    through `jsonschema.validate(..., STATUS_JSON_SCHEMA_DICT)`.
12. **Delete `skills/awf-status/scripts/check.py`** and any
    other obsolete files; replace SKILL.md.
13. **Polish:** mypy --strict, ruff, SKILL.md examples, PR
    description, AC checkbox sweep.

---

**Reviewer paragraph:** This plan replaces `skills/awf-status/`
with a fresh implementation matching the D-007 contract:
fixed-order output (Project / Stage / Drift / Recent / Next), a
`Not yet checked:` footer that pins v1 scope, `--json` validated
against a `STATUS_JSON_SCHEMA` constant in `lib/state.py`,
`--verbose` that extends the event tail 5 → 20 and adds
per-provider detail, and the LLM-directive line in SKILL.md.
Drift detection v1 covers Cloudflare zone, Hetzner servers, and
Neon project+branch via the existing `lib/cf`, `lib/hetzner`, and
`lib/neon` clients; every provider exception or missing
credential degrades to `world_value="unknown"` rather than
erroring (exit 0 always for legitimate runs). `Recent:` is
sourced from `lib.log.tail_events` (DRY with plan_011). Idle
detection (D-OPEN-J Tier 1) emits a soft warning when the most
recent `session.end` is >90 days old. Key decisions: (D1)
replace the existing skill outright, (D2) sequential drift
checks (no asyncio for v1), (D3) defer `--brief`, (D5) catch
all provider exceptions, (D6) schema in `lib/state.py`, (D8)
90-day idle threshold, (D9) stage-mapping local to the skill
until `awf-help` (plan_013) needs to share it. Tensions: (T1)
"Not yet checked" footer always vs conditional — recommend
always; (T2) credentials-missing one entry per provider vs
global — recommend per-provider; (T3) suggested skill as a
plain string vs typed enum — recommend string; (T4) idle
based on `session.end` vs last-event-of-any-type vs mtime —
recommend `session.end` only; (T5) `main()` exception on
corrupt project anchor propagate vs catch — recommend
propagate (local-state corruption is not the same kind of
uncertainty as provider-side unknowns). Main risks are
schema-string maintenance in `lib/state.py` (mitigated by
size cap and dict-parsing test), drift-mapping divergence
with `awf-help` (mitigated by tests and a plan_013 promotion
hook), and the 90-day idle threshold being a guess (cheap to
tune). The skill stays read-only, never mutates state,
exits 0 / 4 only.

---

### Pass 1 (2026-06-01)

**Reviewer:** Reviewer | **Status after pass:** approved for implementation

**T1 — "Not yet checked" footer: always-on vs conditional.**
Verdict: **accept Lead recommendation (always-on).** The footer's
purpose is epistemic hygiene — it prevents "Drift: none" from
being read as "all checks passed." Suppressing it at S1/S2 (where
Pages and Hetzner-LB don't yet exist) would require stage-aware
filter logic with its own edge cases, and the one-line cost is
trivially small. The JSON form gains symmetrically: `not_yet_checked`
is always a non-empty array until a later plan removes a name.
The constant `NOT_YET_CHECKED` in `status.py` ensures a single
edit site when checks are promoted. **No change to plan.**

**T2 — Credentials-missing: one entry per provider vs global.**
Verdict: **accept Lead recommendation (per-provider).** Collapsing
three independent unknowns into one hides diagnostic signal that the
operator needs to act. The "fresh-clone noise" objection is real but
bounded: a first-run user will see at most three `world_value="unknown"`
entries with explicit env-var names, which is exactly the nudge they
need to run `awf-init`. If noise proves excessive in practice, the
right mitigation is a leading line ("credentials not configured for
N provider(s)") that does not suppress the per-provider detail —
an implementation choice, not a plan-level one. **No change to plan.**

**T3 — Suggested re-converge skill: plain string vs typed enum.**
Verdict: **accept Lead recommendation (plain string).** Skill names
are stable CLI identifiers; renames are caught immediately by output
assertions in `test_awf_status.py`. A Literal enum would require a
`lib/state.py` edit every time a new S3+ skill is named as a
re-converge target, coupling two modules for no runtime benefit at
this stage. One refinement worth noting for the implementer: the
acceptance criterion "grep-level test asserts the import" pattern
used for `tail_events` should be mirrored for `suggested_skill`
values — a module-level constant `SUGGEST: dict[str, str]` mapping
`(provider, resource)` pairs to skill names makes the coupling
explicit without committing to a full enum. This is an implementation
detail below plan resolution. **No change to plan.**

**T4 — Idle detection: `session.end` only vs any event vs mtime.**
Verdict: **accept Lead recommendation (`session.end` only).** The
`session.end`-only anchor is the most coherent definition: it measures
when real work *concluded*, not when a crash happened or when rsync
touched the file. The pathological case (every session crashed, so no
`session.end` exists) resolves cleanly: `idle = null` in JSON and no
`Idle:` line in human output, which is correct — the log is ambiguous
about that project's activity and the operator should use `awf-log
sessions` to investigate. This is meaningfully better than a false
`Idle: 0 days` from mtime or a misleading `Idle: N days` from the last
crash event. The 90-day constant and the `null` path both need explicit
test coverage (edge cases at 89, 90, 91 days are already listed in
implementation step 10). **No change to plan.**

**T5 — Corrupt project anchor: propagate vs catch.**
Verdict: **accept Lead recommendation (propagate).** The "status
never fails" posture is scoped to provider-side uncertainty — network
timeouts, missing credentials, unexpected API shapes — not to local
filesystem corruption that only the operator can repair. A
`Pydantic ValidationError` from a malformed `.awf/project.json` gives
a precise byte-level error message that points directly at the bad
field; wrapping it in a generic exit-4 message hides that. The analogy
to `awf-log`'s split (provider unknowns degraded, local-state errors
propagated) is correct and should be called out explicitly in SKILL.md
under "error handling" so the LLM caller knows when to expect a
non-zero exit outside of the documented codes. **No change to plan.**

**Additional observations (non-blocking).**

1. The current `skills/awf-status/scripts/status.py` exits `1` when
   any check has `ERROR` status (`return 1 if any(r.status == ERROR)
   else 0`). The rebuild correctly drops this; the acceptance criteria
   and the exit-code table in the plan are unambiguous. The
   implementer should grep for callers of `awf-status` that inspect
   the exit code and confirm none rely on the old non-zero-on-error
   behaviour (the SKILL.md says "The orchestrator (`awf-launch`)
   consumes the JSON form" — worth confirming `awf-launch` is not
   exit-code-sensitive to the old contract).

2. The `STATUS_JSON_SCHEMA` `oneOf` for the no-project shape is
   noted in the acceptance criteria but not shown in the schema
   block. The implementer should draft both branches before writing
   the JSON-schema unit test; the `oneOf` discriminator (`"project":
   null` vs `"project": {object}`) is straightforward but easy to
   get wrong with `required` arrays. Worth adding a brief schema
   snippet to implementation step 1.

3. Plan says `ProjectAnchor.load(start=cwd, optional=True)`. Verify
   that the `optional=True` parameter is part of the `lib/state.py`
   public API as locked in D-003 / spec A1. The spec's `load()`
   signature shows `load()` raising `ProjectNotFound` by default;
   confirm `optional=True` is the agreed keyword (vs `strict=False`
   or similar) before implementation step 2 reaches that call site.
