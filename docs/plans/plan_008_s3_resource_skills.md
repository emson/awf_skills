# Plan 008 — S3 atomic resource skills (5 of 10)

**Status:** ready
**Phase:** B
**Spec refs:** [`spec.md` § B4](../spec.md), [`decisions.md` D-001](../decisions.md#d-001--multi-stage-architecture-pattern), [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson), [D-005](../decisions.md#d-005--image-registry-default-ghcr)
**Owner (current):** Reviewer
**Created:** 2026-06-01
**Updated:** 2026-06-01

## Goal

Deliver the first five of the ten atomic resource skills from spec § B4:

1. `awf-shared-infra-get` — the play Hetzner server + the play Neon project,
   stored in user-scope `Shared`.
2. `awf-hetzner-server` — one project-scope Hetzner VM.
3. `awf-neon-project` — one Neon project.
4. `awf-neon-branch` — one Neon branch on a project.
5. `awf-cf-dns-record` — one Cloudflare DNS record.

These are thin imperative wrappers around the Phase B libs
(`lib/hetzner/`, `lib/neon/`, `lib/cf/`) shipped by plans 005–007.
Each one owns exactly one resource (D-001 two-layer skill model:
atomic skills do one thing; composers stitch). The S3 composer
(`awf-stage-mvp-play`, plan_010) calls these in dependency order
without itself knowing how Hetzner or Neon work.

Out of scope (deferred to later plans):
- `awf-app-dockerize`, `awf-app-secret-set` — app-side skills (plan_009).
- `awf-kamal-config`, `awf-kamal-setup`, `awf-kamal-deploy` — kamal-runtime
  skills (plan_009).
- `awf-stage-mvp-play` composer (plan_010).

## Context

- Spec: [`docs/spec.md` § B4](../spec.md) — atomic-skill acceptance criteria.
- ADR [D-001](../decisions.md#d-001--multi-stage-architecture-pattern):
  two-layer model. Atomic skills mutate exactly one external resource and
  exactly one state-file block. They must never invoke each other.
- ADR [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson):
  `Infra.hetzner.servers[]`, `Infra.neon.{project_id,branch_id,branch_name}`,
  and `Shared.{play_server,play_neon_project_id}` are the only blocks we
  write here. `Infra.registry`, `Infra.kamal` are owned by plan_009.
- ADR [D-005](../decisions.md#d-005--image-registry-default-ghcr): the play
  server's `registry` field defaults to `"ghcr.io"`.
- Lessons inherited from plan_004 (`awf-migrate`) — *encoded into every script
  body below*:
  - `find_project_root()` runs **outside** `log.session` so a missing
    project exits before the session opens (avoids a sessionless
    `session.start` + `session.end` pair on the user-scope orphan log).
  - The script accepts `--json` for composer consumption; default human.
  - Exit codes are an explicit table in SKILL.md.
- Lessons from plan_005/006/007 (lib authoring):
  - Every `_call` in the lib already emits `api.call`. The skill script
    must **not** double-emit `api.call`; it only emits `skill.invoke /
    skill.complete` (via `log.invoke`) and indirectly `state.change`
    (via `Infra.save()` / `Shared.save()`).
  - The "skip vs created" determination is made by **pre-loading the
    current state file** and comparing the lib's returned resource ID
    against what's already recorded — never by trusting the lib (the lib
    only knows about the remote provider, not `.awf/infra.json`).

## Per-skill anatomy

All five follow the same shape:

```
skills/<name>/
├── SKILL.md            # frontmatter + 1-page description
└── scripts/<verb>.py   # uv-script, ~40-70 lines
```

Each script:

1. PEP 723 header: `requires-python = ">=3.11"`, `dependencies` matched
   to the lib used (e.g. `hcloud` for Hetzner, `httpx` for Neon, `cloudflare`
   for cf-dns).
2. AWF_HOME bootstrap identical to `awf-migrate/scripts/migrate.py:35-39`
   (resolve `parents[3]`, prepend repo root + `lib/` to `sys.path`).
3. `argparse` for inputs + `--json` flag.
4. `find_project_root()` outside `log.session` (lesson from plan_004).
5. `log.session(composer="<skill>", target="resource")` wraps the body.
6. `with log.invoke(skill="<skill>", args={...}):` inside the session.
7. Pre-read the state file (`Infra.load_or_create()` or `Shared.load_or_create()`),
   capture `before_id = <current value>`.
8. Call the lib `get_or_create` method.
9. If returned resource ID == `before_id` → action `"skip"`, no save.
   Else → mutate the model, `.save()` (emits `state.change`), action `"created"`
   or `"updated"`.
10. Print human or JSON, exit 0.
11. On any uncaught exception inside `log.invoke`, `log.error(...)` is emitted
    by the context manager's `result="fail"` close; main() catches and maps
    to exit code (table below).

### Standard exit-code table (used by all five SKILL.md files)

| Code | Meaning |
|------|---------|
| `0`  | Success — created, updated, or skipped (no-op) |
| `1`  | Project not found — no `.awf/project.json` walking up (n/a for `awf-shared-infra-get`) |
| `2`  | Credentials missing — `HETZNER_API_TOKEN` / `NEON_API_KEY` / `CLOUDFLARE_API_TOKEN` not in any layered config source |
| `3`  | Remote API error — `HetznerError` / `NeonError` / `CloudflareError`; message surfaces on stderr |
| `4`  | State validation failure — `StateValidationError` from `.save()` (rare; indicates a bug) |

Per-skill SKILL.md cites this table verbatim under "Errors handled" so
that the composer (plan_010) can pattern-match exit codes deterministically.

## Skill 1: `awf-shared-infra-get`

**Special.** Owns user-scope resources (Hetzner play server, Neon play project).
Reads/writes `~/.config/awf/shared.json`. Does **not** read project anchor.

**Inputs (CLI flags):**
- `--server-type` (default: `cx22`) — `~€5/month` per plan_005 surface.
- `--server-location` (default: `fsn1`).
- `--neon-region` (default: `aws-eu-central-1`).
- `--play-hostname` (default: `awf-play`) — name the Hetzner server gets.
- `--play-neon-name` (default: `awf-play`) — name the Neon project gets.
- `--json` — stdout JSON.

**Script body (concrete):**

```python
from lib import log
from lib.state import Shared, PlayServer
from lib.hetzner import HetznerClient
from lib.neon import NeonClient

shared = Shared.load_or_create()
before = shared.model_dump(mode="json")

with log.session(composer="awf-shared-infra-get", target="shared-infra"):
    with log.invoke(skill="awf-shared-infra-get", args=safe_args):
        changed = False

        # --- play_server ---
        if shared.play_server is None or not shared.play_server.hetzner_id:
            hz = HetznerClient.from_env()
            srv = hz.servers.get_or_create(
                name=args.play_hostname,
                type=args.server_type,
                location=args.server_location,
                labels={"awf-role": "play", "awf-shared": "true"},
            )
            shared.play_server = PlayServer(
                hetzner_id=str(srv.id),
                ip=srv.public_net.ipv4.ip,
                hostname=args.play_hostname,
                registry="ghcr.io",   # D-005
                created=datetime.now(timezone.utc).isoformat(...),
            )
            changed = True

        # --- play_neon_project_id ---
        if not shared.play_neon_project_id:
            nc = NeonClient.from_env()
            proj = nc.projects.get_or_create(
                name=args.play_neon_name,
                region_id=args.neon_region,
            )
            shared.play_neon_project_id = proj.id
            changed = True

        if changed:
            shared.save()
```

**Idempotency contract:** the lib `get_or_create` calls are themselves
idempotent. If `Shared.play_server.hetzner_id` is already set, we skip
the Hetzner call entirely (no `api.call` event). If only the Neon side
is missing, we make exactly one Neon `api.call` (the list/search) plus
the create only when missing, per plan_006's contract.

**Stdout (human):**
```
play_server: <id> @ <ip> (<hostname>)
play_neon_project_id: <id>
action: created | partial | skip
```

**Stdout (`--json`):**
```json
{"action": "created", "play_server": {"id": "...", "ip": "...", "hostname": "awf-play"}, "play_neon_project_id": "..."}
```

**Failure modes documented in SKILL.md:**
- Hetzner type/location/image not found → `HetznerNotFound` → exit 3.
- Auth failures → `HetznerAuthError` / `NeonAuthError` → exit 2 if the
  underlying `Config.require()` raised (caught before lib call); exit 3
  if the lib raised on a stale token.
- Partial-state recovery: if Hetzner succeeds but Neon fails, the
  Hetzner-side write to `Shared` happens **after** Neon succeeds — see
  Decisions §1 (single-write-at-end). On failure nothing is persisted,
  so re-run finds Hetzner server by name (`get_or_create` returns
  existing) and proceeds to Neon.

## Skill 2: `awf-hetzner-server`

**Inputs:**
- `--name` (required) — server name (unique within Hetzner project).
- `--type` (default: `cx22`).
- `--location` (default: `fsn1`).
- `--role` (default: `app`) — written into `Infra.hetzner.servers[].role`.
- `--shared` flag — written into `Infra.hetzner.servers[].shared`.
- `--ssh-key` (repeatable) — names of SSH keys to attach.
- `--json`.

**Script body:**

```python
from lib.state import ProjectAnchor, Infra, Server
from lib.hetzner import HetznerClient

ProjectAnchor.load()           # raises ProjectNotFound → exit 1
infra = Infra.load_or_create()
existing = next((s for s in infra.hetzner.servers if s.id and s.role == args.role and s.shared == args.shared), None)
# also match by name via labels — see Decisions §4

with log.session(composer="awf-hetzner-server", target="hetzner-server"):
    with log.invoke(skill="awf-hetzner-server", args=safe_args):
        hz = HetznerClient.from_env()
        srv = hz.servers.get_or_create(
            name=args.name, type=args.type, location=args.location,
            ssh_keys=args.ssh_key or None,
            labels={"awf-role": args.role, "awf-shared": str(args.shared).lower()},
        )
        new_entry = Server(
            id=str(srv.id),
            ip=srv.public_net.ipv4.ip,
            role=args.role,
            shared=args.shared,
            cost_eur_month=_cost_lookup(args.type),  # plan_005 helper or hardcoded {cx22: 4.5, cx32: 7.5}
        )
        idx = next((i for i, s in enumerate(infra.hetzner.servers) if s.id == new_entry.id), None)
        if idx is None:
            infra.hetzner.servers.append(new_entry)
            infra.save()
            action = "created"
        elif infra.hetzner.servers[idx] != new_entry:
            infra.hetzner.servers[idx] = new_entry
            infra.save()
            action = "updated"
        else:
            action = "skip"
```

**State write:** appends to `Infra.hetzner.servers[]` (D-003 §hetzner block).
Never touches `lb_id` or `network_id`.

**Failure modes:** auth (exit 2), Hetzner API (exit 3), validation (exit 4).
No partial state: `.save()` only runs after the Hetzner call succeeds.

## Skill 3: `awf-neon-project`

**Inputs:**
- `--name` (required) — Neon project name.
- `--region` (default: `aws-eu-central-1`).
- `--pg-version` (default: `16`).
- `--json`.

**Script body:**

```python
from lib.state import ProjectAnchor, Infra
from lib.neon import NeonClient

ProjectAnchor.load()
infra = Infra.load_or_create()

with log.session(composer="awf-neon-project", target="neon-project"):
    with log.invoke(skill="awf-neon-project", args=safe_args):
        nc = NeonClient.from_env()
        proj = nc.projects.get_or_create(
            name=args.name, region_id=args.region, pg_version=args.pg_version,
        )
        if infra.neon.project_id == proj.id:
            action = "skip"
        else:
            infra.neon.project_id = proj.id
            # Do NOT touch branch_id / branch_name — that's awf-neon-branch's
            # responsibility (D-001 single-resource ownership).
            infra.save()
            action = "created" if not infra.neon.project_id else "updated"  # eval before mutation
```

Note the cross-field invariant from `Infra.validate()`: setting `project_id`
alone is fine (branch fields default to `""`). Setting branch fields without
`project_id` raises. Order of skill invocation in the composer (project → branch)
is therefore mandatory; spec § B5 step 1 already encodes this.

## Skill 4: `awf-neon-branch`

**Inputs:**
- `--name` (required) — branch name.
- `--project-id` (optional) — defaults to `Infra.neon.project_id`. Must be set
  somewhere; if neither is provided, exits 1 with a hint.
- `--parent-id` (optional).
- `--json`.

**Script body:**

```python
from lib.state import ProjectAnchor, Infra
from lib.neon import NeonClient

ProjectAnchor.load()
infra = Infra.load_or_create()
project_id = args.project_id or infra.neon.project_id
if not project_id:
    print("awf-neon-branch: no Neon project_id (set via --project-id or run awf-neon-project first)", file=sys.stderr)
    return 1

with log.session(composer="awf-neon-branch", target="neon-branch"):
    with log.invoke(skill="awf-neon-branch", args=safe_args):
        nc = NeonClient.from_env()
        br = nc.branches.get_or_create(project_id, name=args.name, parent_id=args.parent_id)
        if infra.neon.branch_id == br.id and infra.neon.branch_name == br.name:
            action = "skip"
        else:
            infra.neon.branch_id = br.id
            infra.neon.branch_name = br.name
            infra.neon.project_id = project_id   # ensure invariant holds
            infra.save()
            action = "created"
```

## Skill 5: `awf-cf-dns-record`

Slightly different shape: spec § B4 lists `Reads: infra`, `Writes:
passport.cloudflare`. We do **not** mutate `Infra` here; the source of
truth for DNS records is Cloudflare itself, and the existing
`lib/cf/dns.py:create_dns_record` is already idempotent. We do however
record the record ID in `passport.json` under a new sub-key
`cloudflare.dns_records[]` for resumability (consistent with how
plan_004 added `.awf/project.json` while keeping passport authoritative
for Stage 1 content).

> Cross-plan note for Reviewer: the spec line "Writes: passport.cloudflare"
> implies a passport schema field that does not yet exist on `Passport`.
> See Tensions §3 — this is the one place where this plan has to either
> extend `lib/passport.py` or stash record IDs somewhere else. Recommended:
> add `Passport.cloudflare: dict = {}` (Pydantic field with default factory),
> keyed by `<type>:<name>` → `<record_id>`. No schema-version bump needed
> because passport is `extra="allow"`; the field becomes load-bearing only
> when other skills consume it.

**Inputs:**
- `--type` (required) — `A`, `AAAA`, `CNAME`, `TXT`.
- `--name` (required) — subdomain (`@` or `www` or full FQDN).
- `--content` (required) — record value.
- `--proxied` / `--no-proxied` (default proxied for A/AAAA/CNAME, no-proxy for TXT).
- `--domain` (optional) — defaults to `ProjectAnchor.domain`.
- `--json`.

**Script body:**

```python
from lib.state import ProjectAnchor
from lib.passport import Passport
from lib.cf.client import CloudflareClient
from lib.cf.dns import create_dns_record

anchor = ProjectAnchor.load()
domain = args.domain or anchor.domain
passport = Passport.load()

with log.session(composer="awf-cf-dns-record", target="dns-record"):
    with log.invoke(skill="awf-cf-dns-record", args=safe_args):
        cf = CloudflareClient.from_env()  # existing factory
        record = create_dns_record(
            cf, domain, args.type, args.name, args.content,
            proxied=args.proxied,
        )
        key = f"{args.type}:{args.name}"
        cloudflare = getattr(passport, "cloudflare", None) or {}
        if not isinstance(cloudflare, dict):
            cloudflare = {}
        if cloudflare.get(key) == record.id:
            action = "skip"
        else:
            cloudflare[key] = record.id
            passport.cloudflare = cloudflare   # tolerated by extra="allow"
            passport.save()
            action = "created"
```

**Idempotency note:** `lib/cf/dns.py:create_dns_record` already prints
`- DNS record … already exists` for the search-hit case. We turn that
into a structured log event by re-comparing record IDs against passport.

## Acceptance criteria

### Per-skill (all five)
- [ ] Second invocation with identical inputs logs `skill.complete` with
      `result="ok"` and emits zero new `state.change` events (action `skip`).
- [ ] First invocation emits exactly one `state.change` event per mutated
      file (`Infra.save()` for skills 2–4; `Shared.save()` for skill 1;
      `Passport.save()` for skill 5) plus one or more `api.call` events
      from the underlying lib.
- [ ] Runnable standalone:
      `uv run skills/<name>/scripts/<verb>.py --help` exits 0 with usage.
- [ ] SKILL.md has frontmatter (`name`, `description`), Prerequisites,
      Inputs, Procedure (uv run command), Exit-code table, Errors handled,
      Idempotency, Manual gates sections.
- [ ] Failure with missing credentials exits 2 (not 3), via
      `Config.require()` raising before the lib is touched.

### Plan-wide
- [ ] One consolidated test file `tests/skills/test_resource_skills.py`
      (Decision §1) covering all five skills via subprocess.
- [ ] Each skill has at least: happy-path-create, happy-path-skip,
      no-project-exit-1 (skills 2–5), missing-creds-exit-2, `--json`
      shape assertion. ~6 × 5 = 30 tests minimum.
- [ ] Mocking strategy: monkeypatch `lib.hetzner.HetznerClient.from_env`,
      `lib.neon.NeonClient.from_env`, `lib.cf.client.CloudflareClient.from_env`
      to return fakes with stubbed resource namespaces. Real `tmp_path`
      projects + real state-file writes; only HTTP boundaries mocked.
- [ ] Full suite green: 152 baseline + ~30 new ≥ 182 passing, no regressions.
- [ ] `ruff check skills/` clean on all five script files.
- [ ] Each skill's `--help` output is included in the test as a smoke check.

## Decisions

1. **Test file layout: consolidated.** One `tests/skills/test_resource_skills.py`
   with shared fixtures (`make_project`, `fake_hetzner_client`, `fake_neon_client`,
   `fake_cf_client`). The patterns are identical enough that per-file tests
   would be ~80% boilerplate. Trade-off accepted: the file is large (~600 lines),
   but pytest's `-k` filtering by skill name is sufficient navigation.

2. **Stdout format: `--json` opt-in, human default.** Matches `awf-migrate`
   precedent (plan_004). Composer (plan_010) always passes `--json`; humans
   running atomic skills directly see prose.

3. **Default Hetzner server type: `cx22`.** ~€4.50/month (close enough to
   spec's stated €5). Cheapest x86 type that runs Docker + a small SvelteKit
   app. Override available via `--server-type` on `awf-shared-infra-get` and
   `--type` on `awf-hetzner-server`. Cost-per-month lookup table lives in
   the `awf-hetzner-server` script (small, hardcoded `{cx22: 4.5, cx32: 7.5,
   cpx21: 5.0}`; updates land here, not in the lib).

4. **Server matching for "is this already in Infra?"** Match by Hetzner
   numeric ID (returned by `get_or_create`), not by name or labels. The
   lib is authoritative for "does this resource exist in Hetzner"; the
   state file is authoritative for "have we recorded it". The
   intersection is the ID.

5. **`awf-cf-dns-record` writes passport, not infra.** Spec line item.
   Requires a tolerated extra field `passport.cloudflare: dict`. See
   Tensions §3.

6. **No `--force` flag anywhere in this plan.** A1: search-or-create is
   the only idempotency contract. `--force` recreate is a Phase D affordance
   and out of scope here.

## Tensions for Reviewer

1. **Passport vs Infra for DNS records.** Spec § B4 says `awf-cf-dns-record`
   *Writes: passport.cloudflare*, but DNS is infrastructure. Putting it in
   passport keeps S1-only projects (landing pages) consistent with their
   existing model where `passport.cloudflare_zone_id` already lives, and
   it lets `awf-cf-dns-record` work at S1 *before* `Infra` exists. The
   alternative (route to `Infra.cloudflare`) would force an S3 promotion
   for any DNS work, which is wrong for landing pages. Recommend keeping
   the spec line as written.

2. **Cost-per-month source of truth.** Plan_005 didn't capture a cost
   lookup. Hardcoding three server types in `awf-hetzner-server` is the
   pragmatic short-term move; a real solution is to call Hetzner's
   `/server_types` endpoint at create time and read `prices[].price_monthly.gross`.
   Punt to a Phase D follow-up; document the table inline as `# TODO`.

3. **Adding `Passport.cloudflare` field.** Either (a) just write through
   `extra="allow"` and don't touch the dataclass (current draft), or
   (b) add an explicit `cloudflare: dict[str, str] = field(default_factory=dict)`
   field to `lib/passport.py` for type-checker visibility. (b) is cleaner
   but couples this plan to a passport schema change. Recommend (a) for
   this plan, and a single-line passport.py update under plan_009 once we
   know the full key set.

4. **Where `awf-cf-dns-record` gets the Hetzner IP.** The composer
   (plan_010) is the one that knows "A record content = play server IP".
   This skill takes `--content` as a literal. That's fine — composer
   reads `Shared.play_server.ip` and passes it through. Documented but
   worth flagging because it's the one place where the atomic skill's
   inputs are non-obvious without the composer context.

## Risks

- Hetzner / Neon test doubles must mirror the lib's internal namespace
  layout (`hz.servers.get_or_create`, `nc.projects.get_or_create`).
  Refactors to plan_005/006 internals can break these tests. Mitigation:
  the fakes only implement the methods this plan calls; lib refactors
  that don't change method *names* are insulated.
- `passport.json` writes via `extra="allow"` need a small test that
  `Passport.load(); .save()` round-trips the new `cloudflare` key. Added
  to the test plan.

## Out of scope

- Removal/teardown skills (`awf-hetzner-server-delete`, etc.) — not in spec.
- Drift detection (existing server has wrong type) — plan_005 explicit
  non-goal; surfaces as `action: "skip"` even on drift.
- Multi-record bulk DNS — caller invokes `awf-cf-dns-record` N times.

## Implementation order

1. `awf-hetzner-server` (smallest, exercises Infra writes).
2. `awf-neon-project` (similar shape, different lib).
3. `awf-neon-branch` (depends on project_id being in Infra).
4. `awf-cf-dns-record` (passport write, no Infra).
5. `awf-shared-infra-get` (last; combines Hetzner + Neon + Shared writes;
   tests reuse fakes from 1–3).

Each lands as its own commit. Test file grows incrementally per skill.
PR merges only after all five plus the consolidated test file are green.

---

## Review

### Pass 1 (2026-06-01)

**Reviewer:** Reviewer agent
**Verdict:** APPROVED — T1 and T4 approved unconditionally; T2 approved with a Phase D note; T3 approved with a deferred-field condition.

---

**T1 — DNS records in passport, not Infra: APPROVED.**

The Lead's rationale is correct and S1-compat is the decisive argument. `passport.json` already carries `cloudflare_zone_id` (set by the existing `awf-setup-domain` skill), so `cloudflare.dns_records[]` is a natural extension of an established S1 pattern, not a new kind of entry. The alternative — routing DNS writes to `Infra.cloudflare` — would require an S3-promoted project anchor before any DNS can be written, which breaks the landing-page pipeline where `awf-cf-dns-record` may legitimately be called at S1 (e.g., custom subdomains for Cloudflare Pages). Spec § B4 reflects this intent explicitly ("Writes: passport.cloudflare"). The placement is correct. No change required.

**T2 — Hardcoded cost table `{cx22: 4.5, cx32: 7.5, cpx21: 5.0}`: APPROVED for Phase B; Phase D note attached.**

The hardcoded table is the right pragmatic choice now. The Hetzner `/server_types` endpoint price response is a nested list of per-datacenter, net/gross variants that would require location-aware disambiguation — non-trivial to do correctly. For the three types listed the values are stable and publicly documented. The `# TODO: call /server_types` comment (mentioned in the plan's Decisions §3) must appear inline in the script body at the lookup table definition, not buried in prose. Condition (minor): the plan already says "document the table inline as `# TODO`"; the implementation must honour this — the TODO comment must reference the Hetzner pricing API endpoint and this plan by number so a Phase D author can pick it up without archaeology. No re-review needed; a comment audit in the code review pass will catch omission.

**T3 — `Passport.cloudflare` via `extra="allow"` NOW, explicit field in plan_009: APPROVED.**

The plan's recommendation (a) — write through `extra="allow"` and defer the explicit `Passport.cloudflare: dict[str, str]` field to plan_009 — is structurally sound given the current `lib/passport.py` implementation. Reading the code confirms: `_from_dict` (line 191) filters to `known` fields only — this means a `cloudflare` key written to `passport.json` is **silently dropped on load**. The `extra="allow"` shim in the skill script (`passport.cloudflare = cloudflare`) writes to the dataclass as a dynamic attribute and `_to_dict` calls `asdict(p)` which only serialises `__dataclass_fields__`, so the key will be written to disk on save but **not round-tripped on reload** without the explicit field. This is a latent data-loss bug, not a cosmetic one: any skill that reads `passport.cloudflare` after a reload will get `AttributeError`. The workaround in the script body (`getattr(passport, "cloudflare", None) or {}`) handles the missing-attribute case on the first run but will fail silently on re-load because the persisted value is not restored. Verdict: option (a) as written is insufficient. Two acceptable fixes, in order of preference: (a') add a single-line field `cloudflare: dict = field(default_factory=dict)` to `lib/passport.py` now (trivially small change; no schema-version bump required; this plan owns the skill that needs it); (b) stash record IDs in `Infra` under a new `Infra.cloudflare_records: dict[str, str] = {}` field instead of passport (still consistent with S1 use because `Infra.load_or_create()` already exists). Option (a') is preferred because it matches the spec line exactly ("Writes: passport.cloudflare") and the passport dataclass field is a one-liner with no cascading consequences. The plan should be amended to mandate option (a') before implementation starts. This is a blocking condition on implementation (not on this review passing, because the fix is mechanical), but Dev must not implement skill 5 until `lib/passport.py` has the field. The plan's implementation order (skill 4 before skill 5) gives room to land the passport change as a pre-step commit in the same PR.

**T4 — `awf-cf-dns-record --content` takes literal IP; composer pipes from Shared: APPROVED.**

The design is clean. Keeping the atomic skill interface free of cross-resource knowledge (i.e., not auto-resolving from `Shared.play_server.ip`) is the correct application of D-001: atomic skills own one resource; the composer owns the wiring. The composer (plan_010) reading `Shared.play_server.ip` and passing it as `--content` is explicit and testable. The SKILL.md "Inputs" section must include a one-sentence note explaining that `--content` for A-record use is typically the play server IP obtained from `awf-shared-infra-get` output — not for correctness but for LLM ergonomics (the composer may call this skill directly; the context-hint prevents a wrong value being passed). No change to design; advisory on SKILL.md wording only.

**Additional observation — `awf-neon-project` action determination bug (no tension; implementation note):**

In Skill 3's script body, line: `action = "created" if not infra.neon.project_id else "updated"` is evaluated **after** the mutation `infra.neon.project_id = proj.id`, so `infra.neon.project_id` is always truthy at evaluation time and action is always `"updated"`. The `before_id` pattern recommended in the plan's Per-skill anatomy §7 must be applied here: capture `before_id = infra.neon.project_id` before the lib call, then set `action = "created" if not before_id else "updated"`. This is a bug in the plan's own pseudocode; Dev must fix it during implementation. Not blocking the review because the plan's anatomy section already states the correct pattern — the Skill 3 snippet simply failed to apply it.

**Summary:** T1 and T4 are clean approvals. T2 is approved with a minor comment-quality condition. T3 has a blocking implementation condition: `lib/passport.py` must gain `cloudflare: dict = field(default_factory=dict)` before skill 5 is implemented (one-line change). The neon-project action-determination bug in the plan pseudocode is an implementation note for Dev. Implementation may proceed for skills 1–4 immediately; skill 5 is gated on the passport patch commit.

---

## Status log

| Date | Status | Actor | Note |
|------|--------|-------|------|
| 2026-06-01 | draft | Lead | Initial plan created; encodes plan-004/005/006/007 lessons; defines five atomic resource skills for Phase B. |
| 2026-06-01 | review-approved | Reviewer | Pass 1: T1 (DNS in passport) approved; T2 (hardcoded costs) approved with Phase D TODO-comment condition; T3 (`extra="allow"` approach) approved with blocking condition — `lib/passport.py` must add explicit `cloudflare` field before skill 5 is implemented; T4 (literal --content) approved with SKILL.md wording advisory. Implementation note on neon-project action-determination bug in pseudocode. Skills 1–4 unblocked; skill 5 gated on passport patch. |
