# Plan 005 — S3 enabler: `lib/hetzner.py` (port from `hetzner_deploy`)

**Status:** ready
**Phase:** B
**Spec refs:** [`spec.md` § B1](../spec.md#b1-libhetznerpy-port-from-hetzner_deploy), [`decisions.md` D-001](../decisions.md#d-001--multi-stage-architecture-pattern), [`decisions.md` D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson), [`01-principles.md` A1/A5](../01-principles.md)
**Owner (current):** Lead
**Created:** 2026-05-31
**Updated:** 2026-05-31

## Goal

Deliver `lib/hetzner.py`: an idempotent, well-logged Hetzner Cloud API
client scoped to what S3–S5 (`mvp-play` → `scale`) actually consumes.
This is the first of three Phase-B enabler libraries (B1 here, B2
Neon, B3 Kamal). When this ships, `awf-hetzner-server` / `…-firewall`
/ `…-lb` atomic skills (Phase B, later plans) can be authored against
a stable surface, and the first S3 composer can call them.

The library is the seam between awf-skills and Hetzner. After this
plan no other code may touch `hcloud.Client` directly — all calls
flow through `HetznerClient`. The two-layer skill model (D-001) is
designed around that constraint: composers operate in terms of
`HetznerClient` resources, never raw SDK objects.

This plan ports **only the public-API slice** required by spec B1
plus the smallest set of supporting calls. Over-porting (volumes,
action polling helpers used only by deploy code, rate-limit-aware
batch helpers) is explicitly deferred — see *Non-goals*.

## Context

- Spec: [`docs/spec.md` § B1](../spec.md#b1-libhetznerpy-port-from-hetzner_deploy)
  — public API, operating rules, acceptance criteria.
- ADR: [D-001](../decisions.md#d-001--multi-stage-architecture-pattern)
  rejected reusing `hetzner_deploy` as a subprocess/workspace
  dependency on portability grounds (A2). The port is mandatory; the
  external repo is a reference implementation.
- ADR: [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson)
  locks `.awf/infra.json`'s `hetzner` block to `servers[]` (id, ip,
  role, shared, cost_eur_month), `lb_id`, `network_id`. Our return
  types must round-trip into that shape without further translation.
- Principles:
  - [A1 — search-or-create](../01-principles.md): every mutating call
    looks up by name first and returns the existing resource on hit,
    logging an `api.call` with `result=skip` to make idempotency
    visible in `.awf/log.jsonl`.
  - [A5 — idempotent or refused](../01-principles.md): re-runs are
    safe by default; `--force` belongs to skills, not this library.
- Logging: [`lib/log.py`](../../lib/log.py) (plan 003 shipped).
  Every API call ends with `log.api(provider="hetzner", method=…,
  path=…, status_code=…, resource_id=…)`. The bearer token is
  redacted by `safe_log` because `Authorization` / `token` are on
  the denylist; this plan adds a regression test that exercises the
  redaction path against a real `log.api` call.
- Style precedent: [`lib/cf/client.py`](../../lib/cf/client.py).
  Same shape: a tiny config dataclass + `from_env()` factory + a
  client object that exposes resource namespaces. We deliberately
  mirror this so a future reader who knows `lib/cf/` can read
  `lib/hetzner.py` cold.

### Source survey — what's actually in `hetzner_deploy`

Concrete read of the upstream tree (so future plans don't repeat the
work):

- `packages/common/src/hetzner_common/` — ~4 files: `state.py`
  (`ProvisioningState` dataclass — *do not port*, that's their
  passport equivalent), `exceptions.py` (two classes — *port,
  rename*), `logging_setup.py` (*do not port*, we have `lib/log.py`),
  `__init__.py`.
- `packages/provision/src/hetzner_provision/provisioning/hetzner_client.py`
  — 488 lines, the actual API wrapper. This is the **only file we
  port wholesale**, and we cut it down (no volumes; no
  `wait_for_server_ready` since composers, not the lib, decide
  readiness policy in our model).
- `packages/provision/.../stages/stage_0{2,3,4,6}*.py` — 4 files,
  ~525 lines, illustrate the search-or-create pattern *callers* use
  on top of the lower-level client. We do **not** port these; we
  *fold* the search-or-create into the library's `.get_or_create` /
  `.ensure` methods, which is exactly what spec B1's public API
  asks for. This is the single biggest shape change vs the upstream.
- `packages/provision/tests/` — see *Test reality check* below.

### Test reality check — the spec's "47 tests" criterion is mis-scoped

The spec acceptance criterion says *"All 47 tests from
`hetzner_deploy/packages/provision/tests` pass against the ported
client (adjust import paths only)."* Direct inspection of the test
tree:

| File | Test count | What it tests |
|------|-----------:|---------------|
| `tests/unit/test_state.py` | 12 | `ProvisioningState` (their passport) |
| `tests/unit/test_config.py` | 12 | YAML config loader |
| `tests/unit/test_cli.py` | 16 | Click CLI argv parsing |
| `tests/unit/test_validators.py` | 7 | Config validators |
| `tests/integration/test_provisioning_e2e.py` | 4 | Live API e2e |
| **Total** | **51** | (47 unit + 4 integration; spec rounds down) |

**Zero of these directly test `hetzner_client.py`.** The 47 unit
tests exercise `ProvisioningState`, the YAML config schema, and the
Click CLI — none of which we're porting. The 4 integration tests do
exercise the client but require live Hetzner credentials and create
real billable resources.

Therefore the spec's "47 tests pass" criterion **cannot be met as
written** without porting the entire surrounding harness (state +
config + CLI) that D-001 explicitly told us not to port. We resolve
this by writing **new tests for the public API surface only**,
sized to the slice we ship. Concrete target in *Test plan*.

This deviation needs Reviewer ack. It is the single biggest tension
in this plan.

## Non-goals

- **Volumes.** `create_volume` / `attach_volume` are in the upstream
  client. S3–S5 store DB state in Neon, not block storage. Defer to
  a later plan when (if) we add stateful workloads outside Neon.
- **Action polling as a public method.** Upstream exposes
  `wait_for_action` / `wait_for_server_ready` as client methods.
  Composers, not the library, own deploy-readiness policy in our
  model. Polling becomes an internal helper, not part of the public
  surface.
- **Auto-retry.** Spec B1 op rule #3 is explicit: surface network
  errors with retry hints; composers decide policy. We drop the
  upstream `_retry_with_backoff` wrapper. Errors carry a
  `retryable: bool` attribute the composer can inspect.
- **Rate-limit-aware throttling.** The upstream `RATE_LIMIT_THRESHOLD`
  constant is dead code (never read). Don't port the noise.
- **Locations / server-type / image lookup as public methods.**
  These are pre-create resolution steps. Keep them as private helpers
  used by `servers.get_or_create`.
- **CLI.** No `__main__`. Skills wrap the library; this is a
  library-only plan.
- **`hcloud-python` replacement.** We keep the `hcloud` SDK as the
  transport. Replacing it with a hand-rolled `httpx` client is
  ~600 lines of churn for no gain. The "port" is of the *wrapper
  semantics* (search-or-create, logging, error shape), not of the
  HTTP layer.

## Design

### Module layout — single module

`lib/hetzner.py`, one file. Estimated size after the port-and-trim:
~550–700 lines. Threshold for splitting into a package is 800 lines
(per Lead's call in the task brief). If we exceed it during
implementation, split at that point into:

```
lib/hetzner/
├── __init__.py        # re-exports HetznerClient
├── client.py          # HetznerClient + HetznerConfig + from_env()
├── errors.py          # HetznerError, NotFoundError, RateLimitedError
└── resources.py       # _Servers, _Firewalls, _LBs, _SSHKeys, _Networks
```

But we start with one file. Splitting on speculation is the kind of
churn plan 004 reviewers called out.

### Public API

The shape spec B1 calls for, written in concrete signatures:

```python
class HetznerClient:
    config: HetznerConfig
    servers: "_Servers"
    firewalls: "_Firewalls"
    lb: "_LoadBalancers"
    ssh_keys: "_SSHKeys"
    networks: "_Networks"

    @classmethod
    def from_env(
        cls,
        *,
        project_root: Path | None = None,
        awf_home: Path | None = None,
    ) -> "HetznerClient": ...

# Resource namespaces — each is an attribute on HetznerClient.

class _Servers:
    def get_or_create(
        self,
        name: str,
        *,
        type: str = "cx22",              # cx22, cx32, …
        image: str = "ubuntu-24.04",     # OS image; Docker installs via cloud-init
        location: str = "fsn1",
        ssh_keys: list[str] | None = None,   # SSH key names
        network: str | None = None,          # network name
        labels: dict[str, str] | None = None,
        user_data: str | None = None,
    ) -> Server: ...
    def get(self, name: str) -> Server | None: ...
    def delete(self, name: str) -> bool: ...   # for teardown skill

class _Firewalls:
    def ensure(
        self,
        name: str,
        *,
        rules: list[FirewallRule],
        apply_to: list[str] | None = None,  # server names
    ) -> Firewall: ...
    def get(self, name: str) -> Firewall | None: ...
    def delete(self, name: str) -> bool: ...

class _LoadBalancers:
    def get_or_create(
        self,
        name: str,
        *,
        type: str = "lb11",
        location: str = "fsn1",
        targets: list[str] | None = None,    # server names
        health_check: dict[str, Any] | None = None,
        services: list[LBService] | None = None,
    ) -> LoadBalancer: ...
    def get(self, name: str) -> LoadBalancer | None: ...
    def delete(self, name: str) -> bool: ...

class _SSHKeys:
    def get_or_create(self, name: str, *, public_key: str) -> SSHKey: ...
    def get(self, name: str) -> SSHKey | None: ...

class _Networks:
    def get_or_create(
        self,
        name: str,
        *,
        ip_range: str = "10.0.0.0/16",
        subnet_zone: str = "eu-central",
        subnet_range: str = "10.0.0.0/24",
    ) -> Network: ...
    def get(self, name: str) -> Network | None: ...
    def delete(self, name: str) -> bool: ...
```

`Server`, `Firewall`, etc. are re-exports of the hcloud SDK objects.
We do *not* wrap them in our own dataclasses — that's churn that
yields nothing (composers only need `.id` / `.public_net.ipv4.ip` /
`.name` from the SDK objects, which they have). Wrapping is the kind
of "premature abstraction" plan 002 reviewers warned about.

### Idempotency contract — what `get_or_create` does

For every `get_or_create(name, **spec)`:

1. `get_by_name(name)` via the SDK list-with-filter.
2. **Hit:** log `api.call result=skip resource_id=<id>`, return
   existing object. **No drift detection in this plan.** A server
   that exists with the wrong type/image is returned as-is; the
   composer is responsible for noticing (this matches `lib/cf/`
   behaviour). Drift-check is a separate later concern; spec B1
   does not require it.
3. **Miss:** log the read as `api.call result=ok status_code=404`
   (or `200` with empty list — the SDK normalises), then issue the
   create, log the create as `api.call result=ok status_code=201
   resource_id=<id>`, poll any returned action to terminal state,
   return.

For `firewalls.ensure(name, rules=[...])`:

- Read-modify-write semantics: get-or-create the firewall shell,
  then **diff** the rules against current. If equal, log `skip`. If
  different, replace (Hetzner has no rule-level patch). This is the
  only place we do a content-level diff because firewall rules
  *must* converge to the declared spec — A1 isn't satisfied by "the
  firewall exists, ship it" when its rules are wrong. This is the
  one structural deviation from the upstream which never diffs.

### Error model

```python
class HetznerError(Exception):
    """Base. Carries (provider="hetzner", code, message, retryable)."""

class HetznerNotFound(HetznerError): ...        # 404
class HetznerConflict(HetznerError): ...        # 409 — handled internally
class HetznerRateLimited(HetznerError):         # 429
    retry_after: float                          # seconds, from header
class HetznerAuthError(HetznerError): ...       # 401/403
class HetznerNetworkError(HetznerError): ...    # timeouts, connection reset
```

`retryable` is True for `HetznerRateLimited` and `HetznerNetworkError`,
False otherwise. Composers read this attribute to decide retry policy
(spec B1 op rule #3).

### Logging contract

Every SDK call goes through a single private helper:

```python
def _call(self, method: str, path: str, fn: Callable[[], T], *,
          resource_id: str | None = None) -> T:
    try:
        result = fn()
        log.api(provider="hetzner", method=method, path=path,
                status_code=200, resource_id=resource_id or _extract_id(result))
        return result
    except hcloud.APIException as e:
        log.api(provider="hetzner", method=method, path=path,
                status_code=e.code, resource_id=resource_id)
        raise _translate(e) from e
```

`path` is synthetic (e.g. `"/servers"`, `"/firewalls/{id}/actions/set_rules"`)
because the SDK hides the URL. A small constant map per resource is
fine; it's documentation as much as logging.

For `get_or_create` hits, we emit a separate skip event:

```python
log.api(provider="hetzner", method="GET", path="/servers",
        status_code=200, resource_id=str(existing.id))
# Then in the same code path, no create call; nothing more logged.
```

The `result=skip` semantics live in `log.api` already (status 200 +
no follow-up POST is implicitly a skip). No new log primitive needed.

### Credential resolution

`HetznerClient.from_env()` resolves `HETZNER_API_TOKEN` via
`Config.layered()` (A6). Missing → `RuntimeError` with the same
phrasing as `lib/cf/client.py:get_client()` ("Hetzner credentials
missing: HETZNER_API_TOKEN. Run awf-init, then awf-doctor.").
This single env var is the entire credential surface — Hetzner
Cloud is single-token by design.

The token is added to `lib/log.py`'s denylist explicitly (it's
already covered by the `token` / `*_TOKEN` denylist patterns, but a
regression test pinning the exact key name is part of this plan).

## Test plan

We replace the spec's "47 tests pass" criterion with a **scoped
test matrix** sized to the surface we ship. Target: ~25 tests, all
unit-level with a mocked `hcloud.Client`, no live API calls.

Layout: `tests/lib/test_hetzner.py` (single file, mirroring how
`tests/lib/test_cf_*.py` are organised today).

**Mocking strategy.** Replace `hcloud.Client` with a `unittest.mock.MagicMock`
whose `.servers.get_list` / `.servers.create` / etc. return canned
SDK objects built from `hcloud.servers.Server(...)` constructors.
No VCR.py — overkill for a surface we control. The upstream
integration tests stay where they are (in `hetzner_deploy`); we
don't replay them.

**Test matrix:**

| Group | n | Tests |
|-------|---:|-------|
| Construction | 3 | `from_env` happy path; missing token error; explicit `HetznerConfig` injection |
| `servers.get_or_create` | 5 | create when absent; skip when present (returns same id); skip emits `api.call`; passes `user_data` through; resolves `ssh_keys` by name |
| `servers.get` / `delete` | 2 | get-miss returns None; delete idempotent (None for absent) |
| `firewalls.ensure` | 4 | create + set_rules; skip when rules match; replace when rules differ; apply_to wiring |
| `lb.get_or_create` | 4 | create with services; skip when present; targets resolved by server name; health_check pass-through |
| `ssh_keys.get_or_create` | 2 | create; skip |
| `networks.get_or_create` | 2 | create + subnet; skip |
| Logging + redaction | 3 | every successful call emits one `api.call`; token never in jsonl; `HetznerRateLimited` carries `retry_after` from header |
| **Total** | **25** | |

That's the **realistic test target**. It exceeds the *meaningful*
test coverage of the upstream (which has 4 e2e tests touching the
client) by a wide margin, while honestly admitting we are not
running their CLI/config/state tests because we aren't porting that
surface.

**Acceptance for tests:** `pytest tests/lib/test_hetzner.py` — 25
green. `mypy --strict lib/hetzner.py tests/lib/test_hetzner.py` —
clean. `ruff check lib/hetzner.py tests/lib/test_hetzner.py` —
clean.

## Acceptance criteria

Spec B1 (restated and clarified):

- [ ] `HetznerClient.from_env()` builds a working client from
      `HETZNER_API_TOKEN` resolved through the layered config (A6).
- [ ] `servers.get_or_create` / `firewalls.ensure` / `lb.get_or_create`
      / `ssh_keys.get_or_create` / `networks.get_or_create` are
      idempotent: second call with same args returns the same
      resource, makes no create call, logs `api.call` with no
      follow-up create.
- [ ] `firewalls.ensure` converges rules: second call with changed
      rules replaces them; second call with identical rules skips.
- [ ] Every API call (read or write) emits exactly one `api.call`
      event with `provider="hetzner"`, a method, a synthetic path,
      a status code, and (for resource-bearing calls) a `resource_id`.
- [ ] The bearer token never appears in any line of `.awf/log.jsonl`
      written during a test run. (Verified by reading the jsonl back
      and grep-asserting absence.)
- [ ] Network/rate-limit errors raise `HetznerNetworkError` /
      `HetznerRateLimited` with `retryable=True`; auth/4xx errors
      raise non-retryable variants. No automatic retry inside the
      library.
- [ ] Every public method has a docstring stating: what it does,
      what it logs, what it raises, and the idempotency contract.
- [ ] `mypy --strict lib/hetzner.py` clean.
- [ ] `ruff check lib/hetzner.py` clean.
- [ ] 25 tests in `tests/lib/test_hetzner.py` green.

**Spec criterion explicitly renegotiated:** "All 47 tests from
`hetzner_deploy/packages/provision/tests` pass" is replaced with the
25-test matrix above. Reasoning in *Test reality check*. Reviewer to
confirm before implementation starts.

## Risks / open questions for Reviewer

1. **Spec deviation on the test count.** The 47-tests criterion in
   spec B1 cannot be met as written. Resolution: 25 new tests
   scoped to the public surface. **Reviewer ack required.**
2. **`image="docker-ce"` default in spec B1 public-API example.**
   `docker-ce` is not a stock Hetzner OS image — it's an app image
   that requires a different lookup path (`client.images.get_by_name`
   resolves OS images by default; app images need `type="app"`
   filtering). Two options: (a) default to `ubuntu-24.04` and let
   `awf-hetzner-server` install Docker via cloud-init `user_data`;
   (b) keep `docker-ce` and add app-image resolution in the lib.
   **Recommendation: (a)**, because the user_data approach lets
   Kamal's own bootstrap own Docker installation (it does anyway).
   Reviewer to confirm; trivial to flip later if wrong.
3. **No drift detection on `servers.get_or_create`.** A server with
   wrong type/image is returned, not rebuilt. This matches `lib/cf/`
   but a future S5 may want it. Out of scope for B1; noting it so
   nobody is surprised.
4. **`hcloud-python` is a new dependency.** Adds ~1MB and a
   transitive `requests` dependency to anyone running awf-skills.
   Justified by the alternative (hand-rolled httpx client) being
   500+ lines of avoidable code. Will be added to the repo's
   `pyproject.toml` extras / `requirements`.
5. **Single module vs package.** Starting single-file. Will split
   at 800 lines if reached. No strong opinion either way; flagging
   so the splitting decision isn't relitigated mid-implementation.

## Implementation order

1. `HetznerConfig` dataclass + `HetznerClient.__init__` + `from_env()`.
2. Error hierarchy + `_translate(e)` from `hcloud.APIException`.
3. Private `_call(method, path, fn, resource_id=…)` wrapper that
   handles logging + error translation.
4. `_SSHKeys` (simplest namespace — exercises the search-or-create
   shape with no action polling).
5. `_Networks` (adds subnet creation with action polling).
6. `_Servers` (the bulk — pre-resolve ssh_keys/network/image/type,
   wait on `next_actions`).
7. `_Firewalls` (adds the rules-diff branch).
8. `_LoadBalancers` (built fresh — no upstream reference).
9. Tests written alongside each namespace; final pass for the
   logging / redaction regression tests after all namespaces land.
10. Self-check: `mypy --strict`, `ruff`, `pytest tests/lib/test_hetzner.py`.

This is the order of least surprise per namespace; each step
extends the test file by 2–5 tests and grows `lib/hetzner.py` by
50–100 lines.

## Reviewer handoff

Three things to confirm before implementation:

- (a) Test-count renegotiation (47 → 25). This is load-bearing — if
  the spec criterion is read strictly, the plan is rejected before
  it starts.
- (b) `image` default — `ubuntu-24.04` (recommended) vs `docker-ce`
  (spec literal).
- (c) Single-module-first with 800-line split threshold.

Everything else is mechanical.

---

## Review

### Pass 1 (2026-05-31)

**Reviewer:** Reviewer agent
**Verdict:** Approved with conditions (T1 approved, T2 approved, T3 confirmed — one minor condition on T2 resolution)

---

**T1 — Test-count renegotiation (47 → 25): APPROVED.**

The Lead's analysis is correct and the deviation is well-founded. Direct inspection of
`hetzner_deploy/packages/provision/tests/` confirms the breakdown: `test_state.py`
(12), `test_config.py` (12), `test_cli.py` (16), `test_validators.py` (7) test
`ProvisioningState`, the YAML config loader, the Click CLI, and field validators
respectively — none of which we are porting, per D-001's explicit rejection of the
subprocess-dependency model. The 4 integration tests in
`test_provisioning_e2e.py` do exercise `hetzner_client.py` but require live credentials
and create billable resources; they are not appropriate as a CI gate. Zero of the 51
upstream tests exercise `hetzner_client.py` in unit-level isolation. The spec
criterion "All 47 tests pass (adjust import paths only)" was written before the
test-tree was read; it assumed a 1:1 mapping between upstream test files and ported
code that does not exist. Replacing it with a 25-test matrix scoped entirely to the
new public API surface is the correct response. The matrix as drafted (Construction 3,
servers 5+2, firewalls 4, lb 4, ssh_keys 2, networks 2, logging/redaction 3) is
complete and the total is honest. **Condition:** the spec's acceptance criterion in
`spec.md § B1` must be updated to reference the 25-test matrix before the PR merges,
so the spec and the plan agree. Lead to amend `spec.md § B1` acceptance criteria in
the same PR.

**T2 — `image="docker-ce"` default: APPROVED, option (a) confirmed.**

`docker-ce` is a Hetzner app image, not a stock OS image. The hcloud SDK's
`images.get_by_name()` resolves OS images by default; retrieving an app image requires
explicit `image_type="app"` filtering — a non-obvious API surface and a second
lookup path that adds complexity for no benefit at B1. More importantly, Kamal's own
`setup` step installs Docker on a fresh OS image via its own bootstrap; providing
`docker-ce` as a pre-baked image would bypass that bootstrap and create a version-drift
risk between what Kamal expects and what the image provides. The Lead's recommendation
(a) — default `ubuntu-24.04`, let `awf-hetzner-server` pass Docker cloud-init via
`user_data`, Kamal owns Docker — is architecturally consistent with the two-layer
model (D-001) and eliminates the dual lookup path entirely. **Condition:** the
`_Servers.get_or_create` signature in the plan's Public API section already shows
`image: str = "docker-ce"` — this must be updated to `image: str = "ubuntu-24.04"`
in the plan before implementation starts, so the plan's code is not misleading. Trivial
one-line fix; noting it so it isn't carried into the implementation as a stale default.

**T3 — Keep `hcloud` SDK as transport: CONFIRMED.**

Not in tension. The source file confirms `hetzner_client.py` is itself a thin wrapper
over `hcloud.Client`; the upstream never hand-rolls HTTP. The Non-goals section
correctly states this and the plan's design section makes the dependency explicit.
No action required.

**Additional observation (no blocking action):** The upstream `_retry_with_backoff`
uses `time.sleep` inside the library, which the Non-goals section correctly defers.
The `wait_for_action` polling loop in the upstream (`time.sleep(2)`) is used internally
by `create_server` and `create_subnet`; the plan designates this an internal helper
rather than a public method. That call is correct and consistent with the stated
design that composers own readiness policy. No deviation.

**Summary.** Two conditions, both minor and mechanical: (1) update `spec.md § B1`
acceptance criteria to reference the 25-test matrix; (2) update the `image` default
in the plan's Public API block from `docker-ce` to `ubuntu-24.04`. Neither requires
a re-review pass. Implementation may start after both edits are made.

---

## Status log

| Date | Status | Actor | Note |
|------|--------|-------|------|
| 2026-05-31 | draft | Lead | Initial plan created |
| 2026-05-31 | reviewed — approved with conditions | Reviewer | Pass 1: T1 approved, T2 approved (option a), T3 confirmed; two mechanical conditions before implementation |
| 2026-05-31 | ready | Orchestrator | Applied both Pass-1 conditions: image default `docker-ce` → `ubuntu-24.04` (plan public API + spec § B1); test-count AC in spec § B1 updated to reference 25-test scope and plan's "Test reality check" |
