# Plan 009 — S3 atomic app + kamal skills (5 of 10)

**Status:** ready
**Phase:** B
**Spec refs:** [`spec.md` § B4](../spec.md), [`decisions.md` D-001](../decisions.md#d-001--multi-stage-architecture-pattern), [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson), [D-005](../decisions.md#d-005--image-registry-default-ghcr)
**Owner (current):** Reviewer
**Created:** 2026-06-01
**Updated:** 2026-06-01

## Goal

Deliver the remaining five of the ten atomic resource skills from spec § B4:

1. `awf-app-dockerize` — scaffold `Dockerfile`, `.dockerignore`, `/up`
   healthcheck route, `lib/db.ts` into the target project tree.
2. `awf-app-secret-set` — upsert one `KEY=value` line in `.kamal/secrets`.
3. `awf-kamal-config` — render `config/deploy.yml` via `KamalConfig.render()`;
   record `Infra.kamal.config_path`.
4. `awf-kamal-setup` — `KamalRunner.setup()` (DNS gate built into lib per
   plan_007).
5. `awf-kamal-deploy` — `KamalRunner.deploy()`; update
   `Infra.kamal.last_deploy_image` on success.

These skills round out the S3 atomic layer. The S3 composer (`awf-stage-mvp-play`,
plan_010) chains all ten atomic skills in dependency order. Each skill
mutates exactly one external surface (filesystem files / `.kamal/secrets` /
remote kamal state) and exactly one state-file block (D-001).

Out of scope (deferred to later plans):
- `awf-stage-mvp-play` composer (plan_010).
- `awf-kamal-rollback`, `awf-app-logs` — operational skills, post-Phase B.
- App template versioning system beyond a hardcoded `DOCKERIZE_VERSION`
  constant (see T1).

## Context

- Spec: [`docs/spec.md` § B4](../spec.md) — atomic-skill acceptance criteria.
- ADR [D-001](../decisions.md#d-001--multi-stage-architecture-pattern):
  two-layer model. Atomic skills mutate exactly one resource and never
  invoke each other. Op rule #1 (DNS-before-TLS) lives in `lib/kamal/runner.py`,
  not in skill scripts.
- ADR [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson):
  `Infra.kamal.{config_path, last_deploy_image}` is the only state block
  owned here. `Infra.hetzner`, `Infra.neon`, `Infra.registry`,
  `Shared.play_*` are owned by plan_008.
- ADR [D-005](../decisions.md#d-005--image-registry-default-ghcr): the
  registry default is `ghcr.io`. `awf-kamal-config` reads
  `Infra.registry.{host,user,image}`; the composer (plan_010) is
  responsible for populating those fields before invoking us.
- Plan_007 (`lib/kamal/`) delivered the heavy lifting: `KamalConfig.render()`
  is deterministic and idempotent, `KamalRunner.setup()` polls DNS before
  shelling out, `KamalRunner.deploy()` raises `KamalDeployFailed` on
  non-zero exit. All subprocess calls already emit `process.invoke` events
  inside the lib — skill scripts must not double-emit.
- Lessons inherited from plan_004 / plan_008:
  - `find_project_root()` (or `ProjectAnchor.load()`) runs **outside**
    `log.session` so a missing-project exit doesn't open a sessionless
    `session.start`/`session.end` pair on the user-scope orphan log.
  - `--json` opt-in; human default. Composer (plan_010) passes `--json`.
  - Exit-code table cited verbatim in each SKILL.md.
  - Pre-load the relevant state file; capture a `before_*` snapshot;
    determine `action` (`created` / `updated` / `skip`) against the
    snapshot, **not** against the post-mutation field.

## Per-skill anatomy

All five follow the same shape:

```
skills/<name>/
├── SKILL.md            # frontmatter + 1-page description
└── scripts/<verb>.py   # uv-script, ~50-90 lines
```

Each script:

1. PEP 723 header: `requires-python = ">=3.11"`, `dependencies` matched
   to the lib used (`pydantic`, `pyyaml` for kamal-config; none beyond
   stdlib for dockerize / secret-set). Kamal-setup/-deploy declare no
   extras — they shell out to the system `kamal` binary.
2. AWF_HOME bootstrap identical to plan_004/008 (resolve `parents[3]`,
   prepend repo root + `lib/` to `sys.path`).
3. `argparse` for inputs + `--json` flag.
4. `ProjectAnchor.load()` (or equivalent) **outside** `log.session`.
5. `log.session(composer="<skill>", target="<resource>")` wraps the body.
6. `with log.invoke(skill="<skill>", args=safe_args):` inside the session.
7. Pre-read the state file. Capture `before_*` snapshots
   (`before_path = infra.kamal.config_path`, `before_image =
   infra.kamal.last_deploy_image`, file digests for dockerize, secrets
   dict snapshot for secret-set).
8. Call the lib method **or** perform the local-file mutation (skills 1–2
   have no lib API — they touch the project tree directly; their
   `api.call` analogue is a `file.write` event emitted inline once per
   changed path, see Decision §2).
9. Compare new vs `before_*`; if equal → `action="skip"`, no `.save()`.
   Else mutate `Infra`, `.save()` (emits `state.change`), action
   `"created"` or `"updated"`.
10. Print human or JSON, exit 0.
11. Uncaught exceptions inside `log.invoke` are closed by the context
    manager with `result="fail"`; `main()` catches and maps to the
    exit-code table below.

### Standard exit-code table (used by all five SKILL.md files)

| Code | Meaning |
|------|---------|
| `0`  | Success — created, updated, or skipped (no-op) |
| `1`  | Project not found — no `.awf/project.json` walking up |
| `2`  | Credentials / required CLI missing — env var not in any layered config source, or `kamal` binary not on PATH (`KamalNotInstalled` → exit 2; treated as a credentials-class error) |
| `3`  | Remote / subprocess error — `KamalDnsTimeout`, `KamalSetupFailed`, `KamalDeployFailed`; message surfaces on stderr |
| `4`  | State validation failure — `StateValidationError` from `.save()` |

Per-skill SKILL.md cites this table verbatim under "Errors handled" so
plan_010's composer can pattern-match deterministically.

## Skill 1: `awf-app-dockerize`

Scaffolds four files into the project tree. Pure local IO; no remote
calls. The template content lives **inside the script** as string
constants alongside a `DOCKERIZE_VERSION = "1"` marker. Idempotency
compares the on-disk content to the constant. If the file exists with
matching content → skip that file. If it exists with non-matching
content → leave it untouched and report `action="skip"` with a
`drift=true` flag in JSON output (do not overwrite user edits; T1).

**Inputs:**
- `--port` (default: `3000`) — interpolated into Dockerfile `EXPOSE`
  and the `/up` route's expected listen port.
- `--node-version` (default: `20`) — `FROM node:<version>-slim`.
- `--json`.

**Files written (relative to `find_project_root()`):**
- `Dockerfile`
- `.dockerignore`
- `src/routes/up/+server.ts` (SvelteKit healthcheck; returns `200 OK`)
- `lib/db.ts` (one-liner that exports a `pg` pool reading `DATABASE_URL`)

**Script body sketch:**

```python
from lib import log
from lib.state import ProjectAnchor

DOCKERIZE_VERSION = "1"
DOCKERFILE = """# awf-dockerize v{ver}
FROM node:{node}-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY . .
EXPOSE {port}
CMD ["node", "build"]
"""
DOCKERIGNORE = "node_modules\n.git\n.svelte-kit\nbuild\n.env*\n"
UP_ROUTE = "export const GET = () => new Response('OK');\n"
DB_TS = "import pg from 'pg';\nexport const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });\n"

anchor = ProjectAnchor.load()           # exit 1 if missing
root = anchor.path                       # project root

with log.session(composer="awf-app-dockerize", target="app-files"):
    with log.invoke(skill="awf-app-dockerize", args=safe_args):
        plan = [
            ("Dockerfile",                  DOCKERFILE.format(ver=DOCKERIZE_VERSION, node=args.node_version, port=args.port)),
            (".dockerignore",               DOCKERIGNORE),
            ("src/routes/up/+server.ts",    UP_ROUTE),
            ("lib/db.ts",                   DB_TS),
        ]
        wrote, skipped, drift = [], [], []
        for rel, content in plan:
            p = root / rel
            if p.exists():
                if p.read_text() == content:
                    skipped.append(rel)
                else:
                    drift.append(rel)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
                log.file_write(path=str(rel), bytes=len(content))   # see Decision §2
                wrote.append(rel)
        action = "created" if wrote else "skip"
```

No `Infra.save()` — this skill doesn't touch state files. The
`state.change` axiom (§1 acceptance) is therefore replaced by `file.write`
events for this skill (Decision §2); the unified-test asserts at least
one `file.write` on a fresh-project run and zero on a second run.

**Idempotency contract:** running twice with identical args on an
unchanged tree → second run emits zero `file.write` events, exit 0,
`action="skip"`. If the user has edited any of the four files,
`drift=true` is reported but the user's edit is **not** clobbered.

**JSON shape:**
```json
{"action": "created", "wrote": ["Dockerfile", "..."], "skipped": [], "drift": [], "dockerize_version": "1"}
```

## Skill 2: `awf-app-secret-set`

Reads `.kamal/secrets`, upserts a single `KEY=value` pair, writes back
atomically (temp file + `os.rename` in the same directory). Other lines
are preserved byte-for-byte (comments, ordering, blank lines, even
malformed entries). The upsert respects shell-style `KEY=value` semantics:
no quoting normalisation, no value-escaping (composer/user is
responsible for shell-safe values).

**Inputs (exactly one of `--value` / `--from-env` / `--from-file` is required):**
- `--key KEY` (required) — must match `^[A-Z_][A-Z0-9_]*$`.
- `--value VALUE` — literal value (shell-safe).
- `--from-env VAR` — read the value from environment variable `VAR`
  (resolved through layered config so `~/.config/awf/.env` works).
- `--from-file PATH` — read the entire file as the value (trailing
  newline stripped).
- `--json`.

**Script body sketch:**

```python
import os, re, tempfile
from lib import log
from lib.state import ProjectAnchor
from lib.config import Config

anchor = ProjectAnchor.load()
secrets_path = anchor.path / ".kamal" / "secrets"

# resolve value source (mutually exclusive)
if args.value is not None:
    value = args.value
elif args.from_env:
    value = Config.layered().require(args.from_env)
elif args.from_file:
    value = Path(args.from_file).read_text().rstrip("\n")
else:
    raise SystemExit("awf-app-secret-set: one of --value/--from-env/--from-file required")

if not re.match(r"^[A-Z_][A-Z0-9_]*$", args.key):
    raise SystemExit(f"awf-app-secret-set: invalid key {args.key!r}")

with log.session(composer="awf-app-secret-set", target="kamal-secret"):
    with log.invoke(skill="awf-app-secret-set", args={"key": args.key, "source": <one of literal|env|file>}):
        # NEVER include `value` in args; it's a secret. Logger redacts but be explicit.
        existing = secrets_path.read_text() if secrets_path.exists() else ""
        lines = existing.splitlines(keepends=False)
        new_line = f"{args.key}={value}"
        idx = next((i for i, ln in enumerate(lines) if ln.split("=", 1)[:1] == [args.key]), None)
        if idx is not None and lines[idx] == new_line:
            action = "skip"
        else:
            if idx is None:
                lines.append(new_line)
                action = "created"
            else:
                lines[idx] = new_line
                action = "updated"
            secrets_path.parent.mkdir(parents=True, exist_ok=True)
            # atomic write
            with tempfile.NamedTemporaryFile("w", delete=False, dir=secrets_path.parent) as tf:
                tf.write("\n".join(lines) + "\n")
                tmp = tf.name
            os.replace(tmp, secrets_path)
            log.file_write(path=".kamal/secrets", bytes=os.path.getsize(secrets_path), redacted_key=args.key)
```

**Logging redaction:** `args` passed to `log.invoke` carries `key` and
`source` (`literal`/`env`/`file`) but **never** `value`. This is enforced
by constructing `safe_args` explicitly rather than relying on the
logger's autoredact (defence in depth; see plan_003 §redaction).

**Idempotency contract:** second invocation with the same key/value
hits the `lines[idx] == new_line` branch → `action="skip"`, no
`file.write`. If the same key is run with a different value, the line is
replaced and `action="updated"`.

**JSON shape:**
```json
{"action": "updated", "key": "DATABASE_URL", "source": "env"}
```

## Skill 3: `awf-kamal-config`

Thin wrapper around `KamalConfig(anchor, infra).render(path=...)`. The
lib already enforces all invariants (registry must be populated, web
server present, etc.); the skill just owns the `Infra.kamal.config_path`
write.

**Inputs:**
- `--path` (default: `config/deploy.yml`) — written relative to project root.
- `--json`.

**Script body sketch:**

```python
from lib import log
from lib.state import ProjectAnchor, Infra
from lib.kamal.config import KamalConfig

anchor = ProjectAnchor.load()
infra = Infra.load_or_create()
before_path = infra.kamal.config_path

abs_path = anchor.path / args.path

with log.session(composer="awf-kamal-config", target="kamal-config"):
    with log.invoke(skill="awf-kamal-config", args=safe_args):
        new_yaml = KamalConfig(anchor, infra).render(path=abs_path)
        # KamalConfig.render() already writes the file when path is given.
        if before_path == args.path:
            action = "skip"
        else:
            infra.kamal.config_path = args.path
            infra.save()
            action = "created" if not before_path else "updated"
```

**Idempotency contract:** `KamalConfig.render()` is deterministic — same
inputs produce byte-identical YAML (plan_007 contract). The lib's render
itself is responsible for skipping the on-disk write if the file already
matches; the skill's idempotency is on `Infra.kamal.config_path` only.
A run where the YAML content has changed but the path key is the same
will rewrite the YAML (via lib) and emit `action="skip"` at the
state-file level — this is intentional: the source of truth for YAML
content is the `Infra` block that produced it, not a separate digest.

## Skill 4: `awf-kamal-setup`

Pure wrap of `KamalRunner(cwd=anchor.path).setup(domain=..., server_ip=...)`.
The lib polls DNS to `server_ip` for up to `dns_timeout_s` (default 600s)
before shelling out to `kamal setup` (D-001 op rule #1, enforced inside
the lib — the skill cannot bypass it).

**Inputs:**
- `--domain` (optional) — defaults to `ProjectAnchor.domain`.
- `--server-ip` (required) — composer reads `Shared.play_server.ip` or
  the appropriate `Infra.hetzner.servers[].ip` and passes it through.
  Not auto-resolved here (D-001: atomic skills don't cross-resolve).
- `--dns-timeout` (default: `600`) — seconds.
- `--json`.

**Script body sketch:**

```python
from lib import log
from lib.state import ProjectAnchor, Infra
from lib.kamal.runner import KamalRunner
from lib.kamal.errors import KamalNotInstalled, KamalDnsTimeout, KamalSetupFailed

anchor = ProjectAnchor.load()
infra  = Infra.load_or_create()
domain = args.domain or anchor.domain

with log.session(composer="awf-kamal-setup", target="kamal-setup"):
    with log.invoke(skill="awf-kamal-setup", args=safe_args):
        runner = KamalRunner(cwd=anchor.path, dns_timeout_s=args.dns_timeout)
        runner.setup(domain=domain, server_ip=args.server_ip)
        action = "created"   # see Decision §3 — no skip-detection
```

**Idempotency contract:** `kamal setup` is itself idempotent (re-running
on a configured server is a no-op for kamal). The skill **always invokes**
`runner.setup()` — we do not maintain a `setup_done` flag in `Infra`
(Decision §3 / T3). The cost is one DNS poll (immediate hit) plus one
kamal SSH probe; acceptable for the simplicity gain. `action` is always
`"created"` on success because we have no reliable way to know whether
kamal did real work.

**Failure modes:**
- `KamalNotInstalled` → exit 2.
- `KamalDnsTimeout` → exit 3 (stderr: `DNS for <domain> never resolved to <ip>; waited <n>s`).
- `KamalSetupFailed` → exit 3 (stderr: `stderr` from kamal + lib's hint heuristic).

No `Infra.save()` on this skill — kamal-setup has no persistent
state-file footprint. The acceptance criterion §1 (one `state.change`
per mutation) is replaced by "one `process.invoke` event from the lib"
which the lib already emits.

## Skill 5: `awf-kamal-deploy`

Pure wrap of `KamalRunner(cwd=anchor.path).deploy()`. Updates
`Infra.kamal.last_deploy_image` on success to the value of
`Infra.registry.image` (the image that kamal just deployed). Failure
exits 3 and leaves `last_deploy_image` untouched.

**Inputs:**
- `--json`.

(No other args. The deploy is fully driven by `config/deploy.yml`, which
itself was rendered from `Infra` by `awf-kamal-config`. If the composer
needs to override the image tag, it should bump `Infra.registry.image`
and re-run `awf-kamal-config` first.)

**Script body sketch:**

```python
from lib import log
from lib.state import ProjectAnchor, Infra
from lib.kamal.runner import KamalRunner
from lib.kamal.errors import KamalNotInstalled, KamalDeployFailed

anchor = ProjectAnchor.load()
infra  = Infra.load_or_create()
before_image = infra.kamal.last_deploy_image
deployed_image = infra.registry.image    # the image kamal will deploy

with log.session(composer="awf-kamal-deploy", target="kamal-deploy"):
    with log.invoke(skill="awf-kamal-deploy", args={}):
        runner = KamalRunner(cwd=anchor.path)
        runner.deploy()
        if before_image == deployed_image:
            action = "skip"          # registry image didn't change since last deploy
        else:
            infra.kamal.last_deploy_image = deployed_image
            infra.save()
            action = "created" if not before_image else "updated"
```

**Idempotency contract:** the comparison is at the `Infra.registry.image`
level. Kamal itself will re-pull and re-roll containers on every
`kamal deploy`, so the skill **always** invokes `runner.deploy()`; the
`action` field reflects state-file delta only. This is consistent with
T3's stance: trust kamal's own idempotency for the side effect; track
"did we record this image as last-deployed" in state.

**Failure modes:**
- `KamalNotInstalled` → exit 2.
- `KamalDeployFailed` → exit 3.
- `StateValidationError` from `.save()` → exit 4 (shouldn't happen;
  `last_deploy_image` is a free-form `str | None`).

## Acceptance criteria

### Per-skill (all five)
- [ ] Second invocation with identical inputs and unchanged source state
      logs `skill.complete` with `result="ok"` and emits zero new
      `state.change` events (action `skip`).
      Exception: skill 4 always invokes kamal (no skip path); the
      acceptance is "zero `state.change` on the second run", which holds
      trivially because skill 4 never calls `.save()`.
      Exception: skill 5 emits zero `state.change` iff
      `Infra.registry.image` is unchanged since the last successful run.
- [ ] First mutation emits the right events: skills 1–2 emit one or more
      `file.write` events; skills 3 and 5 emit one `state.change` (via
      `Infra.save()`); skill 4 emits one or more `process.invoke` events
      from the lib (no `state.change`).
- [ ] Runnable standalone:
      `uv run skills/<name>/scripts/<verb>.py --help` exits 0 with usage.
- [ ] SKILL.md has frontmatter (`name`, `description`), Prerequisites,
      Inputs, Procedure (uv run command), Exit-code table, Errors
      handled, Idempotency, Manual gates sections.
- [ ] Missing-cred / missing-CLI failure exits 2; remote failure exits 3.
- [ ] `awf-app-secret-set` never logs the secret `value`, verified by a
      dedicated test that greps the captured log events for the value
      string and asserts absence.

### Plan-wide
- [ ] One consolidated test file
      `tests/skills/test_app_kamal_skills.py` covering all five skills
      (plan_008 precedent). Target ≥ 35 tests:
      - Each skill: happy-create, happy-skip, no-project-exit-1,
        `--json` shape (5 × 4 = 20).
      - `awf-app-dockerize`: drift-no-clobber, port/node-version
        interpolation, partial-write (some files exist, some don't).
      - `awf-app-secret-set`: literal/env/file value sources,
        update-existing-key, atomic-write-leaves-tempfile-cleaned,
        invalid-key-format, secret-not-in-log assertion.
      - `awf-kamal-config`: Infra.registry-empty raises through lib,
        path-arg-change emits state.change.
      - `awf-kamal-setup`: `KamalNotInstalled` → exit 2,
        `KamalDnsTimeout` → exit 3, success path emits process.invoke via
        injected fake runner.
      - `awf-kamal-deploy`: image-changed → updated, image-unchanged →
        skip, `KamalDeployFailed` → exit 3.
- [ ] Mocking strategy: monkeypatch
      `lib.kamal.runner.KamalRunner.setup` and `.deploy` to fakes that
      record calls and optionally raise. `lib.kamal.config.KamalConfig`
      is exercised through the real implementation against tmp_path
      projects (no mocking — it's pure rendering). Skills 1 and 2
      perform real filesystem writes against `tmp_path`.
- [ ] Full suite green: 192 baseline + ≥ 35 new = ≥ 227 passing, no
      regressions.
- [ ] `ruff check skills/awf-app-dockerize skills/awf-app-secret-set
      skills/awf-kamal-config skills/awf-kamal-setup skills/awf-kamal-deploy
      tests/skills/test_app_kamal_skills.py` clean.
- [ ] Each skill's `--help` smoke-tested in the consolidated file.

## Decisions

1. **Test file layout: consolidated** — one
   `tests/skills/test_app_kamal_skills.py` with shared fixtures
   (`fake_kamal_runner`, `make_project_with_infra`,
   `secrets_path`). Matches plan_008 precedent. Trade-off: file size
   ~500 lines; pytest `-k` covers navigation.

2. **`file.write` event for skills 1–2.** Skills 1 and 2 do not write
   state files (`Infra` / `Shared` / `Passport`), so the plan_008
   `state.change`-per-mutation invariant doesn't apply. Instead they
   emit a `file.write` event per touched path (path + byte count;
   never content) directly from the skill body. This event type is
   new to `lib/log.py` — needs a one-line addition to the event-type
   enum / helper. Single small lib change, scoped to this plan; no
   schema-version bump (event log is append-only JSONL with `extra`-tolerant
   readers per plan_003).

3. **`awf-kamal-setup` does not maintain a `setup_done` gate; always
   invokes.** Rationale: `kamal setup` is itself idempotent; the DNS
   poll on a hot system is sub-second; the cost of always invoking is a
   single SSH probe; the cost of tracking a gate is one more state field
   that can drift from reality (T3). Trust the lib's idempotency. The
   `action` field on success is always `"created"`. This is the only
   skill in the plan where `action` is a constant on the success path,
   and SKILL.md documents this explicitly.

4. **`awf-kamal-deploy` `action` reflects state-file delta only.** Kamal
   re-rolls containers on every `deploy`; we cannot cheaply know whether
   anything actually changed remotely. The skill's contract is "I
   updated `Infra.kamal.last_deploy_image`", and `action="skip"` means
   "the same image was already recorded as last-deployed" — not "kamal
   did nothing".

5. **`awf-app-dockerize` does not clobber user edits.** If any of the
   four files exists with content that doesn't match the
   `DOCKERIZE_VERSION` template, the file is left untouched and the
   path is added to `drift[]` in JSON output. The skill exits 0. This
   is the conservative choice — the alternative (overwrite) destroys
   user customisation; the riskier failure mode (silently letting drift
   sit) is mitigated by the drift list being surfaced both in human
   output and JSON for the composer (plan_010) to act on.

6. **Template versioning lives in the script.** `DOCKERIZE_VERSION = "1"`
   is a module constant; the version string is embedded in the
   `Dockerfile` first-line comment so the next iteration of this skill
   can detect "this is a v1 file, safe to upgrade to v2". This deliberately
   ships **no** template-versioning machinery (no `.awf/app.json`, no
   passport entry) — the constant is sufficient for the first one, and
   the upgrade-path skill is a Phase D concern (T1).

7. **No `--force` flag anywhere in this plan.** A1: search-or-create is
   the only idempotency contract. Same stance as plan_008.

## Tensions for Reviewer

1. **T1 — `awf-app-dockerize` template versioning.** Three plausible
   designs:
   (a) **Hardcoded `DOCKERIZE_VERSION` constant in the script**, version
       embedded in file first-line comment, no extra state file
       (recommended; what this plan ships).
   (b) `.awf/app.json` carrying `{"dockerize_version": "1"}`, separate
       from `project.json` because app-scaffold is conceptually distinct
       from project anchor.
   (c) Add `Passport.dockerize_version` / `Infra.dockerize_version`.
   (a) is simplest and ships now; (b)/(c) only pay off once we have a
   second template-versioned skill (Caddy config? nginx?). Recommend (a)
   with a Phase D upgrade story when the second such skill arrives.
   Note: (a) makes the "skip" determination content-equality based,
   which means changing `--port` from `3000` to `8080` correctly invalidates
   the skip on the Dockerfile (content differs) but leaves
   `.dockerignore` skipped (content unchanged). Correct behaviour.

2. **T2 — `awf-app-secret-set` value source.** Three flag designs:
   (a) `--value LITERAL` only.
   (b) `--value` / `--from-env VAR` / `--from-file PATH` mutually
       exclusive (recommended; what this plan ships).
   (c) Single `--value` that prefixes `@/path` for file and `$VAR` for
       env (kamal-secrets-CLI style).
   (b) is verbose but unambiguous and trivially scriptable from the
   composer. (c) is concise but ambiguous when a literal value
   legitimately starts with `@` or `$`. Recommend (b). The plan also
   mandates that the literal value never appears in the log event
   `args` payload (defence-in-depth on top of the logger's autoredact);
   a dedicated test asserts this.

3. **T3 — `awf-kamal-setup` re-run safety.** Two designs:
   (a) **Always invoke `runner.setup()`** (recommended; what this plan
       ships). Trust kamal's own idempotency.
   (b) Track `Infra.kamal.setup_done: bool` and skip on the second run.
   (b) is "cheaper" by one SSH probe but introduces a stale-state hazard
   (gate true, server actually wiped). Recommend (a). The cost is sub-
   second on a configured system and the lib's DNS gate fast-paths an
   already-resolved A-record. SKILL.md must document that this skill
   may take minutes on a *first* run (DNS propagation + remote setup) so
   the composer (plan_010) and any human caller knows what to expect.

4. **T4 — `awf-kamal-deploy` retrieving the image tag.** The skill reads
   `Infra.registry.image` to populate `last_deploy_image`. If the
   composer mutates `Infra.registry.image` between `awf-kamal-config`
   and `awf-kamal-deploy`, the YAML and the recorded image will drift.
   Mitigation: the skill could re-render YAML defensively before deploy;
   rejected as a D-001 violation (single-resource ownership — kamal-config
   owns the YAML; kamal-deploy owns the deploy). The composer must order
   `awf-kamal-config` immediately before `awf-kamal-deploy`. Documented
   in SKILL.md "Prerequisites".

## Risks

- The new `log.file_write` event helper is a small lib touch (one
  function + one event-type literal). If the event log schema becomes
  load-bearing for downstream consumers (it isn't today), this would
  need versioning. Mitigation: ship as an `extra`-tolerant addition;
  no schema bump.
- `awf-kamal-setup` and `-deploy` tests inject a fake `KamalRunner` via
  monkeypatching; the lib's real `KamalRunner.__init__` signature
  (`cwd`, `dns_resolver`, `dns_timeout_s`, `dns_interval_s`) must not
  drift. Mitigation: fakes are class-level replacements
  (`monkeypatch.setattr("lib.kamal.runner.KamalRunner", FakeRunner)`),
  not instance-level patches, so signature drift surfaces as a test
  failure during construction.
- `awf-app-dockerize` writes a SvelteKit `+server.ts` healthcheck into
  `src/routes/up/` — this assumes the project is SvelteKit. Plan_010's
  composer is the gate that ensures we only run this skill on SvelteKit
  projects; the skill itself doesn't detect framework. SKILL.md
  Prerequisites must state "SvelteKit project with `src/routes/`".

## Out of scope

- Removal / teardown skills (`awf-app-secret-unset`, etc.).
- Multi-secret bulk operations — caller invokes `awf-app-secret-set` N
  times.
- Rendering kamal accessory configs (postgres, redis) — `KamalConfig`
  doesn't emit them today; landing-page MVP doesn't need them.
- `awf-kamal-rollback` — operational, post-Phase-B.
- Live verification that `kamal deploy` actually rolled containers — we
  trust the exit code.

## Implementation order

1. `lib/log.py` — add `file_write` event helper (smallest, used by
   skills 1–2; lands as a pre-step in the same PR or a prior commit).
2. `awf-app-dockerize` (no remote deps; exercises `file.write`).
3. `awf-app-secret-set` (no remote deps; secret-redaction test surface).
4. `awf-kamal-config` (real `KamalConfig.render` against tmp_path).
5. `awf-kamal-setup` (fake `KamalRunner.setup`; error-path matrix).
6. `awf-kamal-deploy` (fake `KamalRunner.deploy`; `last_deploy_image`
   write).

Each lands as its own commit. Test file grows incrementally per skill.
PR merges only after all five plus the consolidated test file are green.

### Pass 1 (2026-06-01)

**Reviewer:** Sonnet 4.6
**Verdict: APPROVED — all four tensions resolved as recommended; one implementation note added.**

**T1 — `DOCKERIZE_VERSION` constant for template versioning.**
APPROVED. Option (a) — hardcoded `DOCKERIZE_VERSION = "1"` constant with the version embedded in the Dockerfile first-line comment — is the correct choice for this phase. The plan's own analysis is sound: the upgrade story (detect "v1 file, safe to upgrade to v2") is coherent, and a separate `.awf/app.json` or passport field only pays off once a second template-versioned skill arrives. The content-equality idempotency model is well-suited to this pattern. One note: the `spec.md § B4` acceptance criterion "each mutation emits both `api.call` (or `process.invoke`) and `state.change` events" is not satisfied for skills 1–2, which emit `file.write` instead. The plan correctly identifies this in Decision §2 and substitutes `file.write` for `state.change`. The reviewer confirms this substitution is valid given the stated rationale (no `Infra` block owned by these skills), and the consolidated test's assertion of "at least one `file.write` on first run, zero on second" is a sufficient acceptance proxy. No change required.

**T2 — `--value` / `--from-env` / `--from-file` mutually-exclusive flags.**
APPROVED. Option (b) is unambiguously correct. The security-critical constraint — that the literal value must never appear in `log.invoke` args — is well-handled by constructing `safe_args` explicitly with `{"key": args.key, "source": <literal|env|file>}`, rather than relying solely on `safe_log` autoredact. The defence-in-depth framing is right. `argparse` mutually exclusive groups enforce the flag constraint at parse time cleanly; no issues. One small implementation note: the sketch uses `raise SystemExit(...)` for the missing-source guard after argparse, but argparse's `add_mutually_exclusive_group(required=True)` would catch this at parse time and produce a consistent exit-2 message — prefer that over the manual guard.

**T3 — `awf-kamal-setup` always-invoke (no `setup_done` gate).**
APPROVED. The stale-gate hazard (gate=True, server wiped) is a real failure mode that option (b) cannot defend against without a health-check roundtrip — at which point the cost difference vanishes. Trusting kamal's own idempotency is the correct choice. The DNS poll fast-path on a hot system is sub-second as stated. The plan correctly documents that `action="created"` is a constant on the success path and mandates that SKILL.md explains this. The reviewer confirms that this is the only skill across the ten-skill atomic layer where `action` is invariant, and that documenting it explicitly in SKILL.md is sufficient. No change required.

**T4 — Image tag drift between `awf-kamal-config` and `awf-kamal-deploy`.**
APPROVED WITH NOTE. The plan's chosen mitigation (composer ordering: always run `awf-kamal-config` immediately before `awf-kamal-deploy`) is correct, and the rejection of re-rendering YAML inside `awf-kamal-deploy` as a D-001 violation is well-reasoned. However, the current `awf-kamal-deploy` script sketch reads `deployed_image = infra.registry.image` from a stale `Infra` load taken before `runner.deploy()` runs. If the composer has already updated `Infra.registry.image` in the same process run and not yet called `awf-kamal-config`, the recorded `last_deploy_image` will reflect the updated image even though the deployed YAML still references the old tag. The fix is narrow and non-invasive: add a note in SKILL.md "Prerequisites" that `Infra.registry.image` must match what is currently in `config/deploy.yml` at the point this skill is invoked, and document the consequence of drift (stale `last_deploy_image`) without changing the code. This is already implicit in "re-run `awf-kamal-config` first" but should be stated as a hard pre-condition so plan_010's composer can assert it before calling. No implementation change required; documentation note is sufficient.

**`log.file_write` event helper.**
APPROVED. The plan correctly identifies this as a one-line lib touch (one function + one event-type literal) and notes no schema-version bump is needed given the `extra`-tolerant JSONL readers. Reviewing `lib/log.py` confirms the pattern is consistent with `log.process()` (lines 693–725): a module-level function that calls `_write_event(_build_record(...))` inside a try/except that never raises. The new helper should follow exactly that pattern with `type_="file.write"` and `data={"path": path, "bytes": bytes, **kwargs}`. The plan's sketch passes `redacted_key=args.key` as a kwarg for the secrets case — this is safe since `redacted_key` is a key name, not a value, and the `safe_log` call in `_build_record` does not redact `redacted_key` by name (it's not in the denylist). Correct.

---

## Status log

| Date | Status | Actor | Note |
|------|--------|-------|------|
| 2026-06-01 | draft | Lead | Initial plan; encodes plan_004/005/006/007/008 lessons; defines the five S3 app + kamal atomic skills, completing the ten-skill atomic layer for Phase B. |
| 2026-06-01 | review-passed | Reviewer | Pass 1: all four tensions approved. Two implementation notes: prefer `argparse` mutually-exclusive-group over manual guard for T2; add hard pre-condition statement for T4 image-drift in SKILL.md Prerequisites. No blocking issues. |
