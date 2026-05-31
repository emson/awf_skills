# Plan 006 — S3 enabler: `lib/neon/` (Neon database API client)

**Status:** implemented
**Phase:** B
**Spec refs:** [`spec.md` § B2](../spec.md#b2-libneonpy-new), [`decisions.md` D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson), [`01-principles.md` A1/A5/A6](../01-principles.md)
**Owner (current):** Reviewer
**Created:** 2026-06-01
**Updated:** 2026-06-01

## Goal

Deliver `lib/neon/`: an idempotent, well-logged Neon REST API client
scoped to what S3–S5 actually consumes — projects, branches, and
branch connection strings. Second of three Phase-B enabler libraries
(B1 Hetzner shipped in plan 005; B3 Kamal next).

After this plan no other code touches the Neon REST API directly; all
calls flow through `NeonClient`. The two-layer skill model (D-001) is
designed around that boundary: composers operate in terms of
`NeonClient.projects` / `.branches`, never raw HTTP.

The schema this library must round-trip into is fixed by D-003: the
`.awf/infra.json` `neon` block holds `project_id`, `branch_id`,
`branch_name`, `mode`, `connection_secret_ref`. Return types align
1:1 — no further translation in callers.

## Context

- Spec: [`docs/spec.md` § B2](../spec.md#b2-libneonpy-new) — public
  API and four acceptance criteria (idempotent `get_or_create`;
  `sslmode=require`; token redacted; `branches.delete()` exists).
- ADR: [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson)
  fixes the on-disk schema. `mode` and `connection_secret_ref` are
  caller concerns (not API surface), but the IDs we return must be
  the IDs the schema expects.
- Principles:
  - [A1 — search-or-create](../01-principles.md): list-by-name on the
    way in; log `api.call result=skip` on hit.
  - [A5 — idempotent or refused](../01-principles.md): re-runs safe
    by default; no `--force` in the library.
  - [A6 — layered config](../01-principles.md): `NEON_API_KEY` via
    `Config.layered()`.
- Logging: [`lib/log.py`](../../lib/log.py). Every API call ends with
  `log.api(provider="neon", method=…, path=…, status_code=…,
  resource_id=…)`. Bearer tokens (typical prefix `napi_`) are already
  on `safe_log`'s denylist via the `token` pattern; this plan adds a
  regression test that pins the exact `NEON_API_KEY` key name and
  asserts no token byte ever lands in `.awf/log.jsonl`.
- Style precedent: [`lib/hetzner/`](../../lib/hetzner/) (plan 005,
  final form). Same shape: package layout, `_call` wrapper, resource
  namespaces, error hierarchy. We mirror it so a reader who knows
  `lib/hetzner/` can read `lib/neon/` cold.

### Hard lessons from plan 005 — encoded here from day one

Plan 005 took **four code-review passes** to converge. The cumulative
cost was driven by three avoidable choices made at planning time:

1. **M1 — single-module-first.** Plan 005 started as
   `lib/hetzner.py` and was forced to split into `lib/hetzner/` at
   1221 lines in Pass 1. **Plan 006 starts as a package.** No
   threshold debate. Hard cap: 200 lines per file (plan 005's final
   target after Pass 2's Minor was raised). Files that approach 200
   get split eagerly, not when a reviewer asks.
2. **M2/M3/M4 — `_call` wrapper.** Plan 005's design specified a
   `_call` helper but Dev shipped without it (M2), then wired it only
   to mutation paths (M3), then forgot the `get_by_name` lookups (M4).
   Three passes, all on the same theme: *every SDK call routes through
   `_call`, no exceptions, including reads*. **Plan 006 makes that the
   first acceptance criterion**, calls it out in implementation order
   step 3 (before any resource), and adds a grep-based regression
   test that fails CI if a bare `httpx` call appears outside a closure
   passed to `_call`.
3. **Resource wiring.** Plan 005's resource classes were given the
   raw SDK client and had to be rewired in Pass 2. **Plan 006's
   resource classes are constructed with the `_call` bound method and
   a session/transport handle; they never see anything else.** No
   raw transport leaks below the client layer.

These three encodings should land the plan in 1–2 review passes.

## Non-goals

- **Endpoints/databases/roles management.** Neon's REST API exposes
  `/projects/{id}/branches/{id}/endpoints`, `/databases`, `/roles`.
  S3–S5 only needs the default branch endpoint and the default
  `neondb` database/`neondb_owner` role auto-created with a branch.
  Custom roles, custom databases, endpoint suspension policies — all
  deferred to a later plan when (if) a composer asks.
- **Async client.** `httpx.AsyncClient` is half the work, half the
  win; nothing in S3–S5 is concurrent at the Neon boundary. Sync only.
- **Auto-retry.** Same call as plan 005 op rule #3: errors carry
  `retryable: bool`; composers decide policy.
- **Operation polling as a public method.** Neon's create-project /
  create-branch return an `operations[]` array with async work
  (provisioning the underlying Postgres). Polling becomes an internal
  helper used by `projects.get_or_create` and `branches.get_or_create`
  (returning only once the resource is "ready"), not part of the
  public surface.
- **An SDK-style replacement for `hcloud`.** Neon has no first-party
  Python SDK worth depending on. The community ones are thin httpx
  wrappers; we write our own and own the contract.
- **CLI.** No `__main__`. Library-only plan.

## Design

### Package layout — locked, not negotiated

```
lib/neon/
├── __init__.py         # re-exports NeonClient + error hierarchy
├── client.py           # NeonClient + NeonConfig + from_env + _call + transport
├── errors.py           # NeonError, NeonNotFound, NeonRateLimited, NeonNetworkError, NeonAuthError
└── resources/
    ├── __init__.py
    ├── projects.py     # _Projects: get, get_or_create, delete
    └── branches.py     # _Branches: get, get_or_create, delete, connection_string
```

Per-file ceiling: **200 lines** (hard, as in plan 005's final form).
Files that approach the ceiling get split — e.g. if `branches.py`
grows past 200 because `connection_string` resolution is non-trivial,
move the connection-string helpers into `lib/neon/connection.py`.

### Public API — concrete signatures

```python
class NeonClient:
    config: NeonConfig
    projects: "_Projects"
    branches: "_Branches"

    @classmethod
    def from_env(
        cls,
        *,
        project_root: Path | None = None,
        awf_home: Path | None = None,
    ) -> "NeonClient": ...

    # Single chokepoint for every HTTP call. See _call below.

class _Projects:
    def get_or_create(
        self,
        name: str,
        *,
        region_id: str = "aws-eu-central-1",
        pg_version: int = 16,
    ) -> Project: ...
    def get(self, name_or_id: str) -> Project | None: ...
    def delete(self, project_id: str) -> bool: ...

class _Branches:
    def get_or_create(
        self,
        project_id: str,
        *,
        name: str,
        parent_id: str | None = None,   # defaults to project primary
    ) -> Branch: ...
    def get(self, project_id: str, name_or_id: str) -> Branch | None: ...
    def delete(self, project_id: str, branch_id: str) -> bool: ...
    def connection_string(
        self,
        project_id: str,
        branch_id: str,
        *,
        role: str = "neondb_owner",
        database: str = "neondb",
        pooled: bool = True,
    ) -> str: ...
```

`Project` and `Branch` are small frozen dataclasses (not dicts),
constructed in `resources/*.py` from the raw JSON. We do not re-export
raw SDK objects because there is no SDK; the JSON itself is the wire
format and exposing dicts would force every caller to memorise
field names. The dataclasses carry only the fields S3–S5 actually
reads: `Project(id, name, region_id, created_at)`,
`Branch(id, project_id, name, parent_id, primary, current_state)`.

### `_call` — the single chokepoint, from day one

```python
def _call(
    self,
    method: str,
    path: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    resource_id: str | None = None,
) -> dict | None:
    """Issue one HTTP request; log api.call; translate errors.

    Returns parsed JSON body (dict) or None on 204. Every byte that
    leaves this client travels through here.
    """
    try:
        resp = self._http.request(method, path, json=json, params=params)
    except httpx.HTTPError as e:
        log.api(provider="neon", method=method, path=path,
                status_code=0, resource_id=resource_id)
        raise NeonNetworkError(str(e)) from e

    body: dict | None = resp.json() if resp.content else None
    rid = resource_id or _extract_id(body)
    log.api(provider="neon", method=method, path=path,
            status_code=resp.status_code, resource_id=rid)
    if resp.status_code >= 400:
        raise _translate(resp, body)
    return body
```

**Acceptance test (grep, in CI):**
`grep -rn "self\._http\." lib/neon/resources/` must return zero
matches. Resource methods may only see `self._call`. This is the
plan-005 M3/M4 lesson, encoded as a test from PR-1.

Resource classes are constructed with `caller=client._call`:

```python
class _Projects:
    def __init__(self, call: CallFn) -> None:
        self._call = call
```

No `httpx.Client` reference anywhere outside `client.py`. (Mirrors
how `lib/hetzner/resources/*.py` ended up after Pass 2.)

### Transport

`httpx.Client` (sync), `base_url="https://console.neon.tech/api/v2"`,
`headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}`,
`timeout=30.0`. Created in `NeonClient.__init__`. Passed only to
`_call`. **The bearer token never appears in any keyword to `log.api`
or any string passed to a resource method** — structural redaction
beats denylist redaction.

### Idempotency contract

For `projects.get_or_create(name)`:

1. `GET /projects?search=<name>` → scan for exact-name match.
2. **Hit:** log `api.call result=ok status_code=200` (the search),
   return existing `Project`. No further calls. (Skip is encoded as
   *list-only, no follow-up create* — same convention as plan 005.)
3. **Miss:** `POST /projects` with `{ "project": { "name": name,
   "region_id": region_id, "pg_version": pg_version } }`. Response
   includes the new project plus an `operations[]` array. Internal
   helper `_await_operations(operations)` polls
   `GET /projects/{id}/operations/{op_id}` until each terminal-state
   (`finished` or `failed`); raises on `failed`.
4. Return `Project`.

For `branches.get_or_create(project_id, name)`:

1. `GET /projects/{project_id}/branches` → scan for name match.
2. Hit → return; Miss → `POST /projects/{id}/branches`, await
   operations, return. Same shape as projects.

For `branches.connection_string`:

1. `GET /projects/{id}/connection_uri?branch_id=…&role_name=…&
   database_name=…&pooled=true` (Neon exposes a dedicated endpoint
   that assembles the URI server-side, including password).
2. Assert the returned URI ends with `?sslmode=require` (Neon's
   default); if not, append. **Test pins this**.
3. Return the URI string verbatim. The library does **not** log the
   URI body — only the `api.call` event for the GET; the URI is the
   return value, never a log payload.

### Error model

```python
class NeonError(Exception):
    """Base. Carries (provider='neon', code, message, retryable)."""

class NeonNotFound(NeonError): ...        # 404
class NeonConflict(NeonError): ...        # 409 — handled internally by get_or_create
class NeonRateLimited(NeonError):         # 429
    retry_after: float                    # from Retry-After header
class NeonAuthError(NeonError): ...       # 401/403
class NeonNetworkError(NeonError): ...    # httpx-side failures
```

`retryable=True` only for `NeonRateLimited` and `NeonNetworkError`.
Same shape as `lib/hetzner/errors.py` so composers can write generic
retry adaptors.

### Logging contract

Every call through `_call` emits exactly one `log.api` event:

```python
log.api(provider="neon", method=method, path=path,
        status_code=status, resource_id=rid)
```

`path` is the literal URL path with IDs substituted in (e.g.
`/projects/lucky-cloud-123`). `resource_id` is the project ID or
branch ID; for project-scoped operations on a branch we prefer the
branch ID. **No request/response bodies are logged.** Two regression
tests:

- The Neon API token (`napi_…`) never appears in any line of
  `.awf/log.jsonl` produced during a test run (grep-assert).
- The connection-string URI (which contains the role password)
  never appears in any line of `.awf/log.jsonl` (grep-assert on
  `:password@`-style substring).

### Credential resolution

`NeonClient.from_env()` resolves `NEON_API_KEY` via `Config.layered()`
(A6). Missing → `RuntimeError("Neon credentials missing: NEON_API_KEY.
Run awf-init, then awf-doctor.")` — same phrasing as
`lib/hetzner/client.py:from_env()`.

Single env var; the whole credential surface.

## Test plan

`tests/lib/test_neon.py`. **Mock the transport, not the resources**:
inject `httpx.MockTransport` into `httpx.Client` at `NeonClient`
construction time via a private `_transport` kwarg used only by tests.
This is the cleanest seam: it exercises the real `_call`, the real
JSON-decode path, and the real error translation, while making each
test a small request/response script.

Target: **~22 tests**, all unit-level. Layout single file.

**Test matrix:**

| Group | n | Tests |
|-------|---:|-------|
| Construction | 3 | `from_env` happy path; missing token → RuntimeError with correct phrasing; explicit `NeonConfig` injection |
| `projects.get_or_create` | 4 | create when absent (await operations succeeds); skip when present (no POST issued); operations failure raises `NeonError` with op detail; passes `region_id`/`pg_version` in body |
| `projects.get` / `delete` | 3 | get-by-name miss returns None; get-by-id hit returns Project; delete returns True on 200, False on 404 |
| `branches.get_or_create` | 3 | create with explicit `parent_id`; skip when present; await-operations gate honoured |
| `branches.delete` | 2 | success path; 404 returns False (idempotent teardown) |
| `branches.connection_string` | 3 | URI contains `?sslmode=require` on default response; appends it when API omits it (defensive); pooled vs unpooled passes `pooled` query param |
| Logging + redaction | 4 | every successful call emits exactly one `api.call`; token never in jsonl; connection URI (with password) never in jsonl; rate-limit response → `NeonRateLimited(retry_after=…)` from `Retry-After` header |
| **Total** | **22** | |

**Architectural test (separate from the matrix):**
`tests/lib/test_neon_structure.py` (or folded into the main file) —
greps `lib/neon/resources/` for `self._http.` and asserts zero
matches. This is the plan-005 M3/M4 regression encoded as test.

**Acceptance for tests:**
- `pytest tests/lib/test_neon.py` — 22 green.
- `mypy --strict lib/neon/ tests/lib/test_neon.py` — clean.
- `ruff check lib/neon/ tests/lib/test_neon.py` — clean.

## Acceptance criteria

Spec B2 (restated and clarified) + plan additions:

- [x] `projects.get_or_create` / `branches.get_or_create` idempotent:
      second call returns same IDs, issues no POST, logs only the
      list/search call.
- [x] `branches.connection_string()` return value contains
      `?sslmode=require` (test pins exact substring).
- [x] `NEON_API_KEY` never appears in any line of `.awf/log.jsonl`
      written during a test run (grep-assert).
- [x] Connection-string return value (which embeds the role password)
      never appears in any line of `.awf/log.jsonl` (grep-assert).
- [x] `branches.delete(project_id, branch_id)` returns `True` on
      success, `False` on 404; idempotent for teardown.
- [x] **All HTTP calls route through `NeonClient._call`.** Grep test:
      `grep -rn "self._http." lib/neon/resources/` returns zero
      matches. Bare `httpx` calls in `resources/` fail CI.
- [x] **Every file in `lib/neon/` ≤ 200 lines.** Hard cap.
- [x] Every public method has a docstring stating: what it does,
      what it logs, what it raises, idempotency contract.
- [x] `mypy --strict lib/neon/` clean (one pre-existing lib/state.py
      unused-ignore unrelated to this plan).
- [x] `ruff check lib/neon/` clean.
- [x] 23 tests in `tests/lib/test_neon.py` green; full suite (123) green.

## Risks / open questions for Reviewer

1. **`connection_uri` endpoint shape.** Neon exposes
   `GET /projects/{id}/connection_uri`; the response includes the
   role password in the URI. We trust Neon's `sslmode=require`
   default but defensively append it if missing. The defensive append
   is one branch with one test — Reviewer to confirm we don't instead
   want to fail-loud if Neon ever changes its default.
2. **Operation polling timeout.** Neon project provisioning typically
   completes in <30s but the API contract doesn't bound it. We poll
   with `max_wait=300s`, `interval=2s`. On timeout raise
   `NeonError(code="operation_timeout", retryable=True)`. Composers
   decide whether to retry the wait or treat as failure. Reviewer to
   sanity-check the bounds; they're cheap to change.
3. **`Project`/`Branch` as dataclasses, not dicts.** Plan 005 chose
   to re-export hcloud SDK types directly to avoid premature
   abstraction. Neon has no SDK so we cannot do the same; raw dicts
   would force callers to memorise JSON keys. Frozen dataclasses with
   only the fields S3–S5 reads (≤6 fields each) is the lightest
   alternative. Flagging the asymmetry with plan 005 explicitly so
   it isn't litigated again.
4. **`get` accepts name or id.** Neon project URLs use IDs
   (`lucky-cloud-123…`), but humans think in names. Accepting both
   keeps callers ergonomic at the cost of a try-by-id-then-search
   internal branch. Reviewer to confirm; alternative is two methods
   (`get_by_id`, `get_by_name`) which is more code for the same
   semantics.
5. **No `endpoints` namespace yet.** Neon auto-creates a default
   endpoint per branch. If a composer ever needs to suspend/resume
   endpoints for cost reasons, we'll add `branches.endpoints` then.
   Out of scope here; noting it so nobody adds it speculatively.

## Implementation order

This is the order of least surprise. Each step extends
`tests/lib/test_neon.py` by 2–4 tests.

1. `errors.py` — full hierarchy + `_translate(response, body)`.
2. `client.py` skeleton — `NeonConfig`, `NeonClient.__init__` with
   `httpx.Client` (mock-transport-friendly), `from_env()`.
3. **`client.py:_call`** — the chokepoint. Logging, error
   translation, JSON decode. Two construction tests + one logging
   test pass at this point.
4. `resources/projects.py` — `_Projects` namespace, including the
   internal `_await_operations` helper (lives here, not in `client.py`,
   because it's project-scoped). 7 tests pass.
5. `resources/branches.py` — `_Branches.get_or_create` / `get` /
   `delete`. 5 more tests pass.
6. `connection_string` — in `branches.py` if it fits under 200 lines;
   otherwise extract to `lib/neon/connection.py`. 3 more tests pass.
7. `__init__.py` — re-exports.
8. Logging + redaction regression tests (4 tests). Grep structural
   test for `self._http.` outside `client.py`.
9. Self-check: `mypy --strict`, `ruff`, `pytest tests/lib/test_neon.py`,
   full suite.

## Reviewer handoff

Two things to confirm before implementation:

- (a) Frozen-dataclass return types vs raw dicts (Risk 3). The plan
  commits to dataclasses; happy to flip if Reviewer prefers dicts.
- (b) Defensive `?sslmode=require` append vs fail-loud (Risk 1).

Everything else is mechanical and encoded in the acceptance criteria.

## Review

### Pass 1 (2026-06-01)

**Reviewer:** Reviewer agent
**Verdict:** APPROVED — no blockers; two advisory notes.

---

**T1 — Frozen dataclasses (`Project`, `Branch`) vs raw dicts.**
APPROVED. Neon has no first-party Python SDK, so the hetzner precedent of re-exporting SDK objects does not apply here. Frozen dataclasses with ≤6 fields apiece are the correct minimum abstraction: they give callers stable, typed field access, prevent accidental mutation, and make it impossible for a composer to memorise wrong JSON keys. The plan's own rationale is sound. The asymmetry with plan 005 is correctly flagged and explained; it does not need resolution, only documentation (which the plan provides). No change required.

**T2 — `?sslmode=require` defensive append vs fail-loud.**
APPROVED with advisory. The plan's choice — assert-then-append — is safer in practice than fail-loud: Neon's default is `sslmode=require`, but the URI composition (pooled vs unpooled endpoint, proxy rules) could theoretically produce a URI without the param under a future Neon change while still being valid. A silent append keeps the application secure without breaking callers. However, to preserve visibility, add a `log.warning` event when the append fires (e.g. `log.event("neon.connection_string.ssl_appended", branch_id=branch_id)`) so operators can detect if Neon ever changes its default. The test that pins the append branch already exists in the matrix; this advisory only asks for a warning log on that branch. Mark as Minor.

**T3 — Polling bounds (`max_wait=300s`, `interval=2s`).**
APPROVED. 300 s is generous relative to Neon's typical <30 s provisioning; 2 s interval yields at most 150 HTTP round-trips, well within practical limits. `NeonError(code="operation_timeout", retryable=True)` is the right signal — composer decides retry policy (A14 / non-goal). The bounds are cheap to tune later; no reason to hold the plan for them.

**T4 — `get(name_or_id)` accepts either form.**
APPROVED. The try-by-id-then-search internal branch is at most two API calls and is invisible to callers. The alternative (`get_by_id` / `get_by_name`) would double the public surface without adding safety. One advisory: document the disambiguation heuristic in the `get` docstring (e.g., "strings matching `[a-z]+-[a-z]+-[0-9]+` are treated as IDs; everything else triggers a name search") so the contract is testable. The existing test matrix already covers both paths; no new test needed beyond the docstring clarification.

**Minor (non-blocking):**
- T2: emit `log.warning` on the `sslmode` append branch.
- T4: document the name-vs-id disambiguation heuristic in the `get` docstring.

**Summary:** All four lead-flagged tensions are resolved. The plan encodes the plan-005 lessons correctly and adds structural enforcement (grep CI test, 200-line cap, `_call` from step 3). The test matrix at 22 tests is correctly scoped. Handoff to Dev.

---

### Pass 1 (2026-06-01) — code review

**Reviewer:** Reviewer agent
**Branch:** `feat/plan-006-s3-neon-lib`
**Verdict:** accepted — 0 Blockers, 0 Majors, 2 Minors (both carried forward from plan review; one new Minor on regex scope).

---

**Verification results:**

- `git diff main...feat/plan-006-s3-neon-lib --stat`: 11 files, 1839 insertions.  Only the expected files touch the diff.
- `pytest tests/ -v` (with hcloud): **123/123 passed** in 1.41 s.  Without hcloud (missing from test env): 98/98 on the non-hetzner subset; neon suite is 23/23.
- `ruff check lib/neon/ tests/lib/test_neon.py`: **clean** (0 errors).
- `mypy --strict lib/neon/`: **1 error** — `lib/state.py:113: Unused "type: ignore"` — pre-existing, not introduced by this plan.  lib/neon/ itself is clean under strict mode.
- `wc -l lib/neon/**/*.py`: all files under 200.  `client.py` is the largest at **196 lines**; branches 182, projects 183.  Hard cap met.
- `grep -nE "self\._http\." lib/neon/resources/`: **zero matches**.  Structural test also passes as `test_resources_never_reference_self_http`.

**AC checklist:**

| # | AC | Status |
|---|-----|--------|
| 1 | `get_or_create` idempotent (no POST on hit) | PASS — `test_projects_get_or_create_skips_when_present`, `test_branches_get_or_create_skips_when_present` |
| 2 | `connection_string` always contains `?sslmode=require` | PASS — 2 tests: default path and defensive-append path |
| 3 | `NEON_API_KEY` never in log.jsonl | PASS — `test_token_never_in_log_jsonl` |
| 4 | Connection URI password never in log.jsonl | PASS — `test_connection_uri_password_never_in_log` |
| 5 | `branches.delete` returns True/False, idempotent | PASS — `test_branches_delete_success_returns_true`, `test_branches_delete_404_returns_false` |
| 6 | All HTTP calls route through `_call`; grep test | PASS — `test_resources_never_reference_self_http`; confirmed by grep |
| 7 | Every file ≤200 lines | PASS — largest is client.py at 196 |
| 8 | Every public method has a docstring | PASS — reviewed all resource methods |
| 9 | `mypy --strict lib/neon/` clean | PASS — one pre-existing `lib/state.py` error unrelated to this plan |
| 10 | `ruff check lib/neon/` clean | PASS |
| 11 | 23 tests green; full suite 123 green | PASS |

**Design observations:**

- `_call` is in `client.py` commit 1 (`37df0da`) as required by AC #6 / plan lesson M2-M4.  Resource classes accept only `CallFn`; confirmed no `httpx` import in either `resources/projects.py` or `resources/branches.py`.
- Error hierarchy is shape-identical to `lib/hetzner/errors.py`: same base class fields (`code`, `message`, `retryable`), same subclass set, same `retryable=True` on `RateLimited` and `NetworkError` only.  `translate()` in `neon/errors.py` mirrors `lib/hetzner/errors.py`.  Parity confirmed.
- Token redaction: `config.api_key` is passed only to `Authorization: Bearer …` header inside `_build_http_client`; `_call` never forwards it to `log.api`; resources never see it.  Structural redaction is correct and the regression test covers it.
- `?sslmode=require` append: implemented in `lib/neon/connection.py` using `log.note` (not `log.warning` — see Minor M-T2 below).  The defensive branch is tested.
- `get_or_create` skip path: `projects.get_or_create` calls `self.get(name)` first; on a hit it returns immediately — no POST and no extra log event.  The plan's "log only the list/search call" contract is honoured.
- Operation polling: `await_operations` in `lib/neon/operations.py` (correctly extracted, not in client.py).  Uses `time.monotonic()` deadline; raises `NeonError(code="operation_timeout", retryable=True)` on breach.  Tested via `test_branches_get_or_create_awaits_operations`.
- `assert body is not None` appears in four call sites post-200 responses where the body should always be present.  This is acceptable under `mypy --strict` (it satisfies the type narrowing) but is a runtime `AssertionError` rather than a `NeonError` if Neon ever returns an empty 200.  Non-blocking at this scope; flagged as informational.
- Test count: plan matrix says "22 tests"; acceptance criteria says "23 tests"; file has 23 functions.  The extra test is the grep structural test counted separately in the plan text but folded into the main file.  No discrepancy in execution.

**Minors (non-blocking):**

- **M-T2 (carried):** `connection.py` emits `log.note` on the ssl-append branch; the plan advisory and plan-review Minor asked for `log.warning`.  `log.note` is a manual-annotation event intended for human annotations, not operational drift signals.  The distinction is cosmetic at current log-consumer sophistication but semantically wrong.  Recommend replacing `log.note(…)` with `log.warning(…)` when `log.warning` exists, or at minimum renaming the event key from the human-annotation format to a machine-event format (e.g. `log.api(… path="neon.ssl_appended" …)` is not right either — the cleanest fix is to add `log.warn` to `lib/log.py` mirroring `log.note`).  No action required to merge; track as next-pass cleanup.
- **M-T4 (carried):** `_Projects.get` docstring documents the disambiguation heuristic correctly: "strings matching `[a-z]+-[a-z]+-[0-9]+` … are treated as Neon project IDs".  Confirmed present.  However, the regex is `re.match` (prefix-match) not `re.fullmatch`, so a project name like `"my-app-123extra"` is falsely classified as an ID (the `123` prefix satisfies the digit group, and `extra` is ignored).  In practice Neon project names are human-chosen and unlikely to start with a two-word-plus-digits prefix, but the docstring implies fullmatch semantics.  Either tighten the regex to `re.fullmatch` (add `$` anchor) or update the docstring to say "prefix-matches".  Non-blocking.
- **M-assert (new):** `assert body is not None` on the success path of `_call` in three resource files and `connection.py` converts a hypothetical Neon empty-body 200 into an `AssertionError` rather than a typed `NeonError`.  Python's `-O` flag strips asserts.  Prefer `if body is None: raise NeonError("unexpected empty body", code="empty_response")`.  Non-blocking at this scope; no S3–S5 path currently triggers it.

**Summary:** The implementation converges the plan-005 lesson set correctly in a single dev pass.  Package layout, `_call`-from-commit-1, structural grep test, 200-line cap, frozen dataclasses, error hierarchy parity, and redaction are all executed as specified.  All 11 acceptance criteria pass.  The three Minors are carry-forwards or cosmetic; none affects correctness or the caller contract.

---

## Status log

| Date | Status | Actor | Note |
|------|--------|-------|------|
| 2026-06-01 | draft | Lead | Initial plan created; encodes plan-005 lessons (package-from-day-one, `_call` from day-one, 200-line cap, structural grep test). |
| 2026-06-01 | review-approved | Reviewer | Pass 1 (plan review): all four tensions approved; two Minor advisories (T2 ssl-append warning log, T4 disambiguation docstring). Handoff to Dev. |
| 2026-06-01 | implemented | Dev | lib/neon/ package (client, errors, operations, connection, resources/projects, resources/branches). 23 tests green, full suite 123/123. ruff clean. mypy --strict clean on lib/neon/ (one pre-existing lib/state.py unused-ignore). All acceptance criteria met. T2: ssl-append emits log.note. T4: disambiguation docstring in _Projects.get. |
| 2026-06-01 | accepted | Reviewer | Pass 1 (code review): 123/123, ruff clean, mypy clean on lib/neon/, all ACs pass, 0 Blockers, 0 Majors. Three Minors: log.note vs log.warning on ssl-append (M-T2 carried), regex prefix-vs-fullmatch in looks_like_id (M-T4 new), assert-not-NeonError on empty 200 body (M-assert new). |
