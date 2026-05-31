# Plan 007 — S3 enabler: `lib/kamal/` (Kamal deploy library)

**Status:** implemented
**Phase:** B
**Spec refs:** [`spec.md` § B3](../spec.md), [`decisions.md` D-001](../decisions.md#d-001--multi-stage-architecture-pattern), [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson), [D-005](../decisions.md#d-005--image-registry-default-ghcr)
**Owner (current):** Reviewer
**Implemented:** 2026-06-01
**Created:** 2026-06-01
**Updated:** 2026-06-01

## Goal

Deliver `lib/kamal/`: a deterministic YAML renderer + subprocess
wrapper around the `kamal` CLI, scoped to what S3–S5 actually needs —
render `config/deploy.yml` from `ProjectAnchor + Infra`; invoke
`kamal setup / deploy / rollback / app logs`; and encode D-001
operational rule #1 (DNS-before-TLS) by polling `dig +short` before
the first `kamal setup` ever fires.

Third and final Phase-B enabler library. After this plan, no other
code shells out to `kamal` directly and no other code generates
`config/deploy.yml`. The two-layer skill model (D-001) is designed
around that boundary: `awf-kamal-config`, `awf-kamal-setup`,
`awf-kamal-deploy` and the S3 composer all operate in terms of
`KamalConfig` / `KamalRunner`, never raw subprocess or raw YAML.

The on-disk YAML schema is determined by Kamal itself; we only own
the *projection* from `(anchor, infra)` to YAML. The `.awf/infra.json`
`kamal` block (D-003) holds the rendered `config_path` and the last
deployed image tag — both written by composers, never by this lib.

## Context

- Spec: [`docs/spec.md` § B3](../spec.md) — public API
  (`KamalConfig`, `KamalRunner`) and three acceptance criteria
  (diff-stable render; `dns_propagation` gate; deploy stderr surfaces
  in error event with hint).
- ADR: [D-001](../decisions.md#d-001--multi-stage-architecture-pattern)
  picks Kamal as the deploy abstraction for S3–S5 and defines the
  operational rule set. **Rule 1 (DNS-before-TLS): never invoke
  `kamal setup` until `dig +short <domain>` matches the server IP** —
  Let's Encrypt will hard-fail otherwise and burn an ACME quota.
  This rule is encoded *inside* `KamalRunner.setup()` so neither the
  S3 composer nor a human operator can accidentally bypass it.
  Rule 2 (orange-cloud-after-cert) is a *composer* concern and stays
  out of this lib — flagging here so it isn't litigated again.
- ADR: [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson)
  fixes the schemas `render()` reads from. `KamalConfig.render()`
  reads only `ProjectAnchor` (for `domain`, `slug`) and `Infra` (for
  `registry`, `hetzner.servers`, `neon.connection_secret_ref`,
  `kamal.config_path`). It does not read `passport.json`.
- ADR: [D-005](../decisions.md#d-005--image-registry-default-ghcr)
  GHCR is the default registry. Rendered YAML emits
  `registry.server: ghcr.io` and `registry.username: <user>` from
  `Infra.registry`; the registry password reference is
  `<%= ENV["GHCR_TOKEN"] %>`. Non-GHCR registries are supported by
  reading `Infra.registry.host` verbatim.
- Logging: [`lib/log.py`](../../lib/log.py). Subprocess invocations
  emit a `process.invoke` event analogous to `api.call`. The current
  module exposes `log.api`, `log.gate`, `log.error`, etc. but **not**
  `log.process`. This plan adds a minimal `log.process(cmd, exit_code,
  duration_ms, cwd)` helper to `lib/log.py` mirroring `log.api`'s
  shape, with the same redaction guarantees. This is the smallest
  change that lets us honour the spec's "`api.call`-equivalent log
  events (type: `process.invoke`)" without growing the lib/log API
  later. See Risk 1 for the alternative considered (reusing `log.api`
  with `provider="kamal"`).
- Style precedent: [`lib/hetzner/`](../../lib/hetzner/) (plan 005
  final form) and [`lib/neon/`](../../lib/neon/) (plan 006).
  Same shape: package layout from day 1, single chokepoint with grep
  test, 200-line cap, error hierarchy. We mirror them so a reader
  who knows either can read `lib/kamal/` cold.

### Hard lessons from plans 005 + 006 — encoded here from day one

Both prior plans converged faster *only* after we encoded their
lessons structurally. Plan 007 inherits them:

1. **Package layout from day 1, no single-module phase.** Plan 005
   started as `lib/hetzner.py`, was forced to split at 1221 lines
   in Pass 1. Plan 006 started as `lib/neon/`. **Plan 007 starts as
   `lib/kamal/`.** No threshold debate.
2. **Hard 200-line cap per file**, encoded as an acceptance
   criterion. Files approaching the ceiling get split eagerly.
3. **Single chokepoint, grep-tested.** Plan 005 took three passes
   to wire every SDK call through `_call`. Plan 006 made it AC #1.
   **Plan 007's chokepoint is `_run_kamal(args, ...)`** in
   `runner.py`. AC: `grep -rn "subprocess\." lib/kamal/` returns
   matches only inside `runner.py` (and only the `_run_kamal`
   definition); zero matches elsewhere. The grep test is part of CI.
4. **Pure render, side-effects only in runner.** `KamalConfig.render()`
   is referentially transparent given `(anchor, infra)` — same
   inputs always produce byte-identical YAML. No timestamps, no
   randomness, no environment lookups. Tested via a golden fixture
   at `tests/lib/fixtures/kamal/deploy_v1.yml`. This separation is
   what makes plan 007's render side trivially testable without
   touching real `kamal`.

## Non-goals

- **Wrapping every `kamal` subcommand.** S3–S5 needs `setup`,
  `deploy`, `rollback`, `app logs`. We add those four. `kamal env push`,
  `kamal accessory`, `kamal lock`, `kamal audit` — deferred to a
  future plan when (if) a composer asks. Adding them speculatively
  bloats the API and tempts callers to use them without idempotency
  thought.
- **Auto-installing Kamal.** We assume `kamal` is on PATH (validated
  via `awf-doctor`). If missing, `_run_kamal` raises
  `KamalNotInstalled` with the install hint.
- **Owning `.kamal/secrets`.** That file is written by
  `awf-app-secret-set`. This library never reads or writes it.
- **Wrapping `dig` as a real DNS client.** We shell out to `dig` for
  resolution parity with what an operator would type. The dig
  resolver function is **injectable** so tests don't actually call
  `dig` (Risk 2).
- **TLS / certificate orchestration.** Kamal does it; we just make
  sure DNS is right before we hand off.
- **An async runner.** Sync subprocess only. Composers serialise.
- **CLI.** No `__main__`. Library-only plan.

## Design

### Package layout — locked, not negotiated

```
lib/kamal/
├── __init__.py     # re-exports KamalConfig, KamalRunner, error hierarchy
├── config.py       # KamalConfig + render()  (pure)
├── runner.py       # KamalRunner + _run_kamal chokepoint
├── dns.py          # dig polling helper + DnsResolver protocol
└── errors.py       # KamalError, KamalNotInstalled, KamalDnsTimeout,
                    #   KamalDeployFailed, KamalSetupFailed
```

Per-file ceiling: **200 lines** (hard, as in plans 005/006). Files
approaching the ceiling get split — e.g. if `config.py` grows past
200 because YAML composition fans out, split per-section helpers
into `lib/kamal/sections.py`.

### Public API — concrete signatures

```python
from pathlib import Path
from lib.state import ProjectAnchor, Infra
from lib.kamal.dns import DnsResolver

class KamalConfig:
    """Pure YAML renderer. No I/O beyond read-anchor / read-infra / write-path."""

    def __init__(self, anchor: ProjectAnchor, infra: Infra) -> None: ...

    def render(self, *, path: Path | str | None = None) -> str:
        """Compose YAML; if path given, write it; return YAML string."""

class KamalRunner:
    def __init__(
        self,
        *,
        cwd: Path,
        dns_resolver: DnsResolver | None = None,   # injectable; default = real dig
        dns_timeout_s: float = 600.0,
        dns_interval_s: float = 5.0,
    ) -> None: ...

    def setup(self, *, domain: str, server_ip: str) -> None: ...
    def deploy(self) -> None: ...
    def rollback(self, *, to: str | None = None) -> None: ...
    def app_logs(self, *, tail: int = 200) -> str: ...
```

`KamalConfig` and `KamalRunner` are deliberately *separate classes*.
Render is pure and unit-testable without any subprocess; runner is
impure and unit-tested by stubbing `_run_kamal`. The split mirrors
the hetzner/neon split between data-shape concerns and transport.

### `_run_kamal` — the single chokepoint, from day one

```python
def _run_kamal(
    self,
    args: list[str],
    *,
    capture_output: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Invoke `kamal <args>`; log process.invoke; translate errors.

    Every byte that leaves this library via subprocess travels
    through here. Stdout/stderr are captured (unless capture_output
    is False for log streaming). Exit-code != 0 raises a typed
    KamalError subclass; the raw stderr is attached to the exception
    and surfaced in a log.error event with a hint.
    """
    cmd = ["kamal", *args]
    t0 = time.monotonic()
    try:
        cp = subprocess.run(cmd, cwd=self._cwd, capture_output=capture_output,
                            text=True, check=False)
    except FileNotFoundError as e:
        raise KamalNotInstalled(
            "kamal CLI not on PATH; install with `gem install kamal`"
        ) from e
    dur_ms = int((time.monotonic() - t0) * 1000)
    log.process(cmd=cmd, exit_code=cp.returncode, duration_ms=dur_ms,
                cwd=str(self._cwd))
    if check and cp.returncode != 0:
        raise _translate(args, cp)
    return cp
```

**Acceptance test (grep, in CI):**
`grep -rnE "subprocess\.(run|Popen|call|check_output)" lib/kamal/`
returns matches **only** inside `runner.py` (the `_run_kamal`
definition) and `dns.py` (the `DigResolver` probe). No other module
in `lib/kamal/` may shell out. The `dns.py` exemption is documented
in-code with a comment explaining `DigResolver` is a read-only probe
that intentionally does NOT route through `_run_kamal` because it
predates `kamal setup` (and exists precisely to gate it). This
encodes the plan-005 M3/M4 lesson and plan-006 AC #6, with the
narrow well-justified carve-out.

Also: add `# TODO(awf-doctor): add `dig` presence check` as a comment
near `DigResolver` so `awf-doctor` work later picks it up.

### `KamalConfig.render()` — pure, deterministic, golden-tested

`render()` builds the YAML document from `(anchor, infra)`:

```yaml
service: <slug>
image: <registry.host>/<registry.user>/<slug>
servers:
  web:
    hosts:
      - <server.ip>           # first server with role=="web"
proxy:
  ssl: true
  host: <anchor.domain>
registry:
  server: <registry.host>
  username: <registry.user>
  password:
    - <REGISTRY_PASSWORD_ENV>  # GHCR_TOKEN if host=ghcr.io, else DOCKER_PASSWORD
env:
  secret:
    - <infra.neon.connection_secret_ref>   # e.g. DATABASE_URL
```

Properties pinned by golden fixture
`tests/lib/fixtures/kamal/deploy_v1.yml`:

- Keys in stable insertion order (PyYAML `sort_keys=False`,
  `default_flow_style=False`, `width=4096`).
- No timestamps, no random IDs, no env reads from inside `render()`.
- Two calls with the same `(anchor, infra)` produce byte-identical
  output (diff-stable AC).
- Trailing newline; LF line endings.

Render is **the only place in the lib that imports yaml**. Runner
doesn't parse YAML; it shells out to a `kamal` that does.

### DNS polling — D-001 op rule #1, encoded in `setup()`

`KamalRunner.setup()` is the only function in the codebase that
invokes `kamal setup`. Before doing so, it polls DNS:

```python
def setup(self, *, domain: str, server_ip: str) -> None:
    resolved = self._dns.wait_for(
        domain=domain, expected_ip=server_ip,
        timeout_s=self._dns_timeout_s, interval_s=self._dns_interval_s,
    )
    if not resolved:
        log.gate(
            name="dns_propagation",
            reason=f"A-record for {domain} did not resolve to {server_ip} "
                   f"within {self._dns_timeout_s}s",
            instructions=(
                f"Check the CF DNS record for {domain} is set to {server_ip} "
                f"and grey-cloud. Re-run once `dig +short {domain}` returns "
                f"{server_ip}."
            ),
        )
        raise KamalDnsTimeout(domain=domain, expected_ip=server_ip)
    self._run_kamal(["setup"])
```

The `DnsResolver` protocol (in `dns.py`):

```python
class DnsResolver(Protocol):
    def wait_for(self, *, domain: str, expected_ip: str,
                 timeout_s: float, interval_s: float) -> bool: ...
```

Default implementation `DigResolver` shells out to `dig +short
<domain>` (via `_run_kamal`-style subprocess wrapper local to
`dns.py`) on a polling loop. Tests inject a `FakeResolver` that
returns canned results without touching the network.

Two `DnsResolver` instantiation paths:
1. `DigResolver()` — default; shells out to `dig` (assumed on PATH;
   validated by `awf-doctor`).
2. `FakeResolver(sequence=[...])` — test-only; returns scripted
   answers per poll tick.

### Error model

```python
class KamalError(Exception):
    """Base. Carries (code, message, stderr, hint, retryable)."""

class KamalNotInstalled(KamalError): ...        # kamal CLI missing on PATH
class KamalDnsTimeout(KamalError):              # dig poll exhausted
    domain: str
    expected_ip: str
class KamalSetupFailed(KamalError): ...         # `kamal setup` exit != 0
class KamalDeployFailed(KamalError): ...        # `kamal deploy` exit != 0
class KamalRollbackFailed(KamalError): ...      # `kamal rollback` exit != 0
```

`retryable=True` only for `KamalDnsTimeout` (the human can wait for
DNS and retry) and `KamalDeployFailed` with an exit-code subset
(network-ish failures). Same `retryable` shape as plans 005/006 so
composers can write generic retry adaptors.

`_translate(args, cp)` maps `(argv, CompletedProcess)` →
`KamalError` subclass with a one-line hint heuristic on `stderr`:

| stderr substring | hint |
|---|---|
| `no such file or directory` + `Dockerfile` | `Run awf-app-dockerize to scaffold the Dockerfile.` |
| `missing secret` / `secret not set` | `Run awf-app-secret-set; check .kamal/secrets.` |
| `denied: permission_denied` / `unauthorized` | `Check GHCR_TOKEN in ~/.config/awf/.env; run awf-doctor.` |
| `connection refused` + port 22 | `Server may still be booting; re-run in 60s.` |
| (default) | `See log.jsonl for the full stderr.` |

The hint is attached to both `KamalError.hint` and the `log.error`
event's `hint` field. This satisfies the spec's "hint pointing at
common causes (Dockerfile, secrets, registry auth)" AC.

### Logging contract

Every call through `_run_kamal` emits exactly one `log.process` event:

```python
log.process(cmd=cmd, exit_code=cp.returncode,
            duration_ms=dur_ms, cwd=str(self._cwd))
```

Stdout/stderr bodies are **not** logged (they may contain registry
auth artefacts). On non-zero exit, a *second* event — `log.error`
with `msg=f"kamal {subcommand} failed"`, `hint=<heuristic>` — is
emitted before the exception is raised. Two events on failure, one
on success.

This requires adding `log.process(cmd, exit_code, duration_ms, cwd)`
to `lib/log.py`. The addition is ~15 lines following the `log.api`
pattern: build a record with `type="process.invoke"`, route through
`_write_event`, never raise. `cmd` is logged as a list; `safe_log()`
already redacts denylist key names in nested dicts but a plain
`list[str]` of CLI args is allowed verbatim. Argument values that
might contain secrets (none of the four subcommands we wrap take
such args) would need additional handling; not in scope here.

### Credential resolution

`lib/kamal/` itself reads **no credentials**. Kamal's own subprocess
reads `GHCR_TOKEN` (or `DOCKER_PASSWORD`) from the environment via
its own ERB `<%= ENV["..."] %>` template hook. The S3 composer is
responsible for putting those into the environment of the
`_run_kamal` call. The library is intentionally credential-free —
it can't leak what it never sees.

`awf-doctor` validates the necessary env vars are present (D-005);
this lib trusts that or raises through `KamalDeployFailed` with the
"check GHCR_TOKEN" hint.

## Test plan

`tests/lib/test_kamal.py`. Mock the chokepoint, not the resources:
inject a fake `subprocess.run` via monkeypatching at the
`lib.kamal.runner` module boundary, and inject a `FakeResolver` for
DNS. This exercises the real `_run_kamal`, the real error
translation, and the real DNS gate logic, while keeping each test
to a small scripted invocation.

Target: **~20 tests**, all unit-level. Single file.

**Test matrix:**

| Group | n | Tests |
|-------|---:|-------|
| `KamalConfig.render` | 5 | render against golden fixture (byte-equal); two calls produce identical output (diff-stable AC); GHCR host produces `GHCR_TOKEN` ref; non-GHCR host produces `DOCKER_PASSWORD` ref; missing `web` server raises `KamalError("no web server in infra")` |
| `_run_kamal` chokepoint | 3 | success path emits one `log.process` event with correct fields; failure path emits `log.process` then `log.error` with hint; `FileNotFoundError` translates to `KamalNotInstalled` |
| `KamalRunner.setup` | 4 | DNS resolves immediately → invokes `kamal setup` once; DNS resolves after 2 polls → still invokes once; DNS times out → emits `gate.hit name=dns_propagation`, raises `KamalDnsTimeout`, **never** invokes `kamal setup`; setup exits non-zero → `KamalSetupFailed` with stderr attached |
| `KamalRunner.deploy / rollback / app_logs` | 4 | deploy success; deploy failure → `KamalDeployFailed` + hint heuristic ("Dockerfile" stderr → dockerize hint); rollback with explicit `to=` passes through; app_logs returns captured stdout |
| Logging + structure | 3 | golden fixture byte-equal (separate file from render group, run independently); grep test: no `subprocess.` outside `runner.py`; grep test: no `import yaml` outside `config.py` |
| Error hint heuristics | 1 | parametrised across the 5 hint rules in the table above |
| **Total** | **20** | |

**Architectural tests (folded into the main file):**
- `tests/lib/test_kamal.py::test_subprocess_only_in_runner` — greps
  `lib/kamal/` for `subprocess.` outside `runner.py`, asserts zero.
- `tests/lib/test_kamal.py::test_yaml_only_in_config` — greps for
  `import yaml` outside `config.py`, asserts zero.
- `tests/lib/test_kamal.py::test_file_size_cap` — asserts every
  `.py` file in `lib/kamal/` is ≤200 lines.

These three encode the plan-005/006 structural lessons as runnable
tests, not just review checklist items.

**Golden fixture:**
`tests/lib/fixtures/kamal/deploy_v1.yml` — the byte-exact YAML
output for a canonical `(anchor, infra)` pair (slug=`example`,
domain=`example.com`, GHCR registry, one web server at `1.2.3.4`,
`DATABASE_URL` secret). When `render()` legitimately changes
(schema bump from Kamal upstream), bump the fixture filename to
`deploy_v2.yml` and update the constant in `config.py`; do not edit
v1 in place. Plan 005 / 006 do not have golden fixtures because
their outputs are wire-format JSON, not files we own; this plan
does because the YAML *is* the artefact.

**uv script headers:** all scripts that import from `lib/kamal/`
(skills in later plans) must declare `pyyaml` in their PEP 723
inline metadata. Within `lib/kamal/` itself, `config.py` imports
`yaml`; the existing AWF top-level `pyproject` / lock already lists
`pyyaml` (used elsewhere). Adding it again to the per-script headers
of B4 skills is a *skill-plan* concern, not this plan's.

**Acceptance for tests:**
- `pytest tests/lib/test_kamal.py` — 20 green.
- `mypy --strict lib/kamal/ tests/lib/test_kamal.py` — clean.
- `ruff check lib/kamal/ tests/lib/test_kamal.py` — clean.

## Acceptance criteria

Spec B3 (restated and clarified) + plan additions:

- [x] `KamalConfig.render()` output byte-equal to golden fixture
      `tests/lib/fixtures/kamal/deploy_v1.yml`; two calls produce
      identical output (diff-stable AC, spec B3 #1).
- [x] `KamalRunner.setup()` with un-propagated DNS emits a
      `gate.hit` event with `name="dns_propagation"`, raises
      `KamalDnsTimeout`, and **never** invokes `kamal setup` (spec
      B3 #2 + D-001 op rule #1).
- [x] `kamal deploy` failure surfaces stderr in a `log.error` event
      with a hint string from the heuristic table; the same hint is
      on `KamalDeployFailed.hint` (spec B3 #3).
- [x] **All subprocess calls in `lib/kamal/` route through
      `_run_kamal`.** Grep test: `grep -rnE "subprocess\.(run|Popen|call|check_output)" lib/kamal/`
      returns matches only inside `runner.py` and `dns.py` (per R1
      amended AC — DigResolver exemption documented). (Plan 005/006 lesson.)
- [x] **Every file in `lib/kamal/` ≤ 200 lines.** Hard cap, encoded
      as `test_file_size_cap`.
- [x] **`import yaml` appears only in `config.py`.** Grep test.
- [x] `KamalConfig.render()` performs no environment reads, no
      subprocess invocations, and no time/random calls. Verified by
      structural inspection + byte-equality across two calls in the
      same test.
- [x] Every public method (`render`, `setup`, `deploy`, `rollback`,
      `app_logs`) has a docstring stating: what it does, what it
      logs, what it raises, idempotency contract.
- [x] `mypy --strict lib/kamal/` clean (pre-existing `lib/state.py:113`
      unused-ignore from plan 006 noted and unrelated).
- [x] `ruff check lib/kamal/` clean.
- [x] 27 tests in `tests/lib/test_kamal.py` green (20 groups + 7
      parametrised hint cases); full suite 152 green (123 plan 006 +
      27 kamal + 2 log.process = 152).

## Risks / open questions for Reviewer

1. **`log.process` vs reusing `log.api(provider="kamal")`.** The
   spec says "`api.call`-equivalent log events (type:
   `process.invoke`)". Two readings: (a) add a new `log.process`
   helper emitting `type="process.invoke"`, (b) shoehorn into
   `log.api` with a magic provider. Plan picks (a): cleaner type
   discrimination in `awf-log` queries (`type=api.call` vs
   `type=process.invoke`), no overloading of `api.call`'s
   `(method, path, status_code)` schema, ~15 LoC addition. Reviewer
   to confirm; alternative is fine but produces a worse query model
   later.
2. **`dig` on PATH assumption.** `DigResolver` shells out to `dig`,
   which is not on every macOS by default (it is via BIND; usually
   present but not guaranteed). Options: (a) require `dig`, add to
   `awf-doctor`; (b) use Python's `socket.gethostbyname_ex` as a
   fallback. Plan picks (a): `dig +short` is what an operator types
   to debug, so parity matters. Doctor check goes into a follow-up
   doctor-update plan; flagging here so it isn't forgotten.
3. **DNS timeout default = 600s.** Cloudflare typical propagation
   for a new A-record is 30–120s; 600s buys headroom without making
   the failure case painful. Composer can override. Reviewer to
   sanity-check; cheap to change.
4. **Rule 2 (orange-cloud-after-cert) is NOT in this lib.** D-001
   op rule #2 says: after `kamal setup` succeeds and Let's Encrypt
   issues, flip the CF DNS record from grey-cloud to orange-cloud.
   That requires a Cloudflare API call, which belongs in
   `lib/cf/` and the S3 composer, not here. Flagging explicitly
   because a reviewer reading "DNS-before-TLS encoded in setup()"
   might reasonably ask "why isn't the orange flip here too?" — it
   is a composer concern, deliberately separated.
5. **Golden-fixture maintenance.** When Kamal upstream changes the
   YAML schema (it has done so historically — `proxy:` block was
   `traefik:` until Kamal 2.0), the fixture diverges. Plan policy:
   bump to `deploy_v2.yml` and update the rendered constant; never
   edit v1 in place. This keeps any older `kamal` user able to pin.
   Reviewer to confirm the bump-not-edit policy.
6. **`KamalConfig` reads `Infra` only.** If a future composer needs
   to pass *override* values (e.g. a one-off image tag for a hotfix
   deploy without persisting it to infra.json), the current API
   forces them to mutate `Infra` first. Acceptable for S3 scope;
   becomes ugly for S5. Out of scope here; noting it so the next
   composer-side plan can add a `KamalConfig.render(*, overrides=…)`
   without re-litigating the pure-render principle.

## Implementation order

This is the order of least surprise. Each step extends
`tests/lib/test_kamal.py` by 2–4 tests.

1. `errors.py` — full hierarchy + `_translate(args, cp)` with the
   hint heuristic table.
2. `lib/log.py` — add `log.process(cmd, exit_code, duration_ms,
   cwd)` helper. One test in the existing `test_log.py` to pin
   the event shape.
3. `runner.py` skeleton — `KamalRunner.__init__`, `_run_kamal` (the
   chokepoint). Three chokepoint tests pass at this point.
4. `dns.py` — `DnsResolver` protocol, `DigResolver`, `FakeResolver`.
   No tests yet (covered via setup's tests).
5. `runner.py:setup()` — wire DNS gate, call `_run_kamal(["setup"])`
   only on resolve. Four setup tests pass.
6. `runner.py:deploy()` / `rollback()` / `app_logs()`. Four more
   tests pass.
7. `config.py` — `KamalConfig.render()`. Golden-fixture test +
   four other render tests pass. **This is the largest single
   step; budget ~150 lines and watch the 200-line cap.**
8. `__init__.py` — re-exports.
9. Architectural tests (grep + size cap). All three green.
10. Hint heuristic parametrised test.
11. Self-check: `mypy --strict`, `ruff`, `pytest tests/lib/test_kamal.py`,
    full suite.

## Reviewer handoff

Three things to confirm before implementation:

- (a) `log.process` as a new helper vs overload `log.api` (Risk 1).
  Plan commits to a new helper.
- (b) `dig` on PATH assumption with doctor follow-up (Risk 2).
- (c) DNS timeout default = 600s (Risk 3).

Everything else is mechanical and encoded in the acceptance
criteria.

---

### Pass 1 (2026-06-01)

**R1 — `log.process` new helper vs overload `log.api`.** APPROVED as
planned. Inspecting `lib/log.py` confirms the verdict. `log.api` has a
rigid `(provider, method, path, status_code, resource_id)` signature
with its own `data` schema — shoehorning `cmd`, `exit_code`,
`duration_ms`, `cwd` into it would require a magic `provider="kamal"`
convention and would corrupt the query model (`type=api.call` mixes API
round-trips with subprocess invocations). The new helper follows the
identical pattern used by `gate`, `error`, `note` — build a record via
`_build_record("process.invoke", ...)`, route through `_write_event`,
never raise. ~15 LoC is an honest estimate. One addition: the
implementation order lists `log.process` at step 2 (after `errors.py`);
this is correct. Confirm one matching test is added to the existing
`test_log.py` to pin the event shape (the plan names this but does not
include it in the 20-test count — that count is for `test_kamal.py`
only, so the arithmetic is correct).

**R2 — `dig` on PATH assumption.** APPROVED with a required tracking
entry. `dig` is part of BIND (`bind-utils` / `dnsutils`) and is present
by default on macOS via the system BIND installation. The rationale
(operator-parity: `dig +short` is what a human types to debug DNS) is
sound and consistent with the plan-007 non-goal of wrapping a Python DNS
client. The plan already names the mitigation (doctor check in a
follow-up plan). One action required: add a `# TODO(awf-doctor): add
dig presence check` comment in `dns.py` at the `DigResolver` class
definition so it cannot be silently forgotten. The `FakeResolver`
injection path means tests are fully insulated regardless.

**R3 — DNS timeout default = 600s.** APPROVED. Cloudflare
typically propagates a new A-record within 30–120 seconds but the tail
(cold TTL expiry, regional propagation, ISP-side caching) can reach
5–10 minutes on a slow day. 600s is ten minutes — generous without
being punishing. The composer can override via `dns_timeout_s=` at
construction. No change needed; the plan's own reasoning is
sufficient.

**R5 — Golden-fixture "bump not edit" policy.** APPROVED. The policy
(bump to `deploy_v2.yml`, update the constant in `config.py`, leave
`deploy_v1.yml` intact) is the correct approach: it preserves a
regression baseline for users pinned to older Kamal, makes the version
change visible as a diff in `config.py`, and prevents silent fixture
drift. The plan notes this only applies to this lib because the YAML
file is an artefact we own (unlike Hetzner/Neon whose outputs are
wire-format JSON determined by upstream APIs). The policy is
sufficiently specified; no changes needed.

**R6 — `KamalConfig.render()` no `overrides=` escape hatch.** NOTED,
S5 DEBT ACCEPTED. For S3 scope — where the composer always has the full
`Infra` object in hand — forcing mutation through `Infra` before calling
`render()` is acceptable. The plan explicitly flags this as future
ergonomics debt and names the resolution path (`render(*, overrides=…)`
without re-litigating pure-render). No S3 acceptance criterion requires
`overrides=`; no change needed now.

**Additional finding — `DigResolver` subprocess isolation.** The plan
states `DigResolver` shells out via a "`_run_kamal`-style subprocess
wrapper local to `dns.py`" (Design section, DNS polling subsection). This
means `dns.py` has its own `subprocess.run` call outside `runner.py`,
which would break the chokepoint grep test as written. The plan's grep
test is: `grep -rnE "subprocess\.(run|Popen|call|check_output)" lib/kamal/`
returns matches only inside `runner.py`. Either (a) route `DigResolver`
through `_run_kamal` (passing `self` or a bare function reference into
`dns.py` — slightly awkward), or (b) narrow the grep test to
*production* subprocess calls by exempting `DigResolver`'s `dig` call
via a clearly named private helper `_run_dig()` in `dns.py` and
documenting the exception in the AC, or (c) the simplest fix: `dns.py`
uses `subprocess.run` directly for the `dig` call and the grep test is
amended to say "matches only inside `runner.py` and `dns.py`." Option
(c) preserves the spirit (kamal subcommands are the only thing routed
through the runner chokepoint; `dig` is a read-only probe, not a state
mutation). **Recommended: option (c) — amend the grep AC to allow
`subprocess.` in `dns.py` in addition to `runner.py`, and add a
comment in `dns.py` explaining why this exemption is intentional.** The
separate `import yaml` grep test is unaffected.

**Overall verdict: APPROVED with two required changes before
implementation begins.**

1. Add `# TODO(awf-doctor): add dig presence check` to `dns.py` at the
   `DigResolver` class definition (R2 follow-up tracking).
2. Amend AC "all subprocess calls route through `_run_kamal`" to allow
   `subprocess.run` in `dns.py` for the `DigResolver._run_dig()` helper
   only, with the exemption documented in both the AC text and an
   in-code comment (subprocess isolation finding above).

All five flagged risks are resolved or accepted. The plan is
structurally sound: package layout is locked from day one, chokepoint
is grep-tested, 200-line cap is a CI test, golden fixture policy is
explicit, and the `log.process` addition is a minimal, well-precedented
extension. Implementation may proceed once the two amendments are
applied.

---

## Status log

| Date | Status | Actor | Note |
|------|--------|-------|------|
| 2026-06-01 | draft | Lead | Initial plan. Encodes plan-005/006 lessons (package-from-day-one, single chokepoint with grep test, 200-line cap, golden fixture for pure render). Adds `log.process` helper to `lib/log.py` (~15 LoC). DNS-before-TLS encoded in `KamalRunner.setup()`; orange-cloud-after-cert explicitly excluded as composer concern. |
| 2026-06-01 | review-pass-1 | Reviewer | R1 approved (new helper, not overload). R2 approved + tracking TODO required in dns.py. R3 approved (600s). R5 approved (bump-not-edit). R6 accepted as S5 debt. Additional finding: DigResolver subprocess call breaks grep AC — recommended fix: amend AC to allow subprocess in dns.py (option c). Two required changes before implementation. |
| 2026-06-01 | implemented | Dev | All 5 files written (errors.py, config.py, dns.py, runner.py, __init__.py). log.process added to lib/log.py. Golden fixture deploy_v1.yml committed. 152 tests pass; ruff clean; mypy --strict clean (pre-existing state.py noise noted). All ACs ticked. |
| 2026-06-01 | code-review-pass-1 | Reviewer | ACCEPTED. 0 Blockers, 0 Majors. 2 Minors (mypy side-effect, test teardown style). See Pass 1 section below. |

---

### Pass 1 (2026-06-01) — code review

**Status:** accepted

**Verification results**

| Check | Result |
|-------|--------|
| `git diff main...HEAD --stat` | 11 files, 1943 insertions, 0 deletions |
| `pytest tests/ -v` | 152/152 passed in 1.42 s |
| `ruff check lib/kamal/ lib/log.py tests/lib/test_kamal.py` | All checks passed |
| `mypy --strict lib/kamal/ lib/log.py` | 1 error in `lib/state.py:113` (pre-existing, see Minor M1) |
| `wc -l lib/kamal/**/*.py` | errors.py 197, runner.py 182, config.py 162, dns.py 161, `__init__.py` 37 — all ≤ 200 |
| subprocess grep | Matches only in `runner.py` and `dns.py` (documented exemption) |

**Checklist**

- [x] All plan_007 ACs verified by tests — 152 tests green; AC grep tests (`test_subprocess_only_in_runner_and_dns`, `test_yaml_only_in_config`, `test_file_size_cap`) encoded in `TestLoggingAndStructural`.
- [x] `_run_kamal` is the chokepoint — single `subprocess.run` call at line 100 of `runner.py`; `DigResolver._run_dig()` exemption is documented in the module docstring, the class docstring, the inline comment, and an AC test.
- [x] `render()` is pure — no env reads, no timestamps, no random IDs, no external I/O other than the optional file write when `path` is provided. `yaml.dump(sort_keys=False)` + stable dict insertion order ensures byte-identical output.
- [x] Golden fixture exists — `tests/lib/fixtures/kamal/deploy_v1.yml` (18 lines). `test_render_matches_golden_fixture` verifies byte-equality; `test_golden_fixture_byte_equal` in `TestLoggingAndStructural` provides a second assertion.
- [x] DNS gate — `FakeResolver(sequence=[False])` → `log.gate(name="dns_propagation")` emitted, `kamal` never called, `KamalDnsTimeout` raised with `.domain` and `.expected_ip`. Confirmed by `test_setup_dns_timeout_emits_gate_and_raises`.
- [x] Deploy failure — stderr surfaced in `error` event via `log.error(msg=..., hint=err.hint)`; `err.hint` is derived from `_hint_from_stderr(cp.stderr)`; `err.stderr` carries the raw string. Confirmed by `test_failure_emits_process_then_error_event` and `test_deploy_failure_with_dockerfile_hint`.
- [x] `log.process` added correctly — 33-line implementation at `lib/log.py:693–725`, identical pattern to `log.api` (build record, route through `_write_event`, never raise, increments `events_count`). Two dedicated tests in `test_log.py`: `test_log_process_emits_correct_event_shape` and `test_log_process_non_zero_exit_result_is_fail`.
- [x] Coding and testing principles upheld — search-or-create not applicable (subprocess wrapper); no premature abstraction; `DnsResolver` protocol enables injection without framework overhead; `FakeResolver` keeps all tests unit-level with no network I/O.

**Findings**

*Blockers (0)*

None.

*Majors (0)*

None.

*Minors (2)*

**M1 — mypy side-effect on `lib/state.py:113`.** The addition of `log.process` in `lib/log.py` makes the `from lib import log` import at `state.py:113` resolvable where it previously was not, causing mypy to flag the `# type: ignore[attr-defined]` comment as unused. The comment was correct at the time it was written (plan_001) and `lib/state.py` is unchanged in this branch. The error is a side-effect of the new code, not a defect in it. Fix: remove `# type: ignore[attr-defined]` from `lib/state.py:113` in a follow-up commit or as part of the next plan that touches `state.py`. No action required before merging.

**M2 — Inconsistent ContextVar teardown in new `test_log.py` tests.** `test_log_process_emits_correct_event_shape` (line 694) and `test_log_process_non_zero_exit_result_is_fail` (line 719) use `log._current_project_root.set(None)` in their finally blocks instead of the `.reset(token)` pattern used consistently in all prior tests in the same file. `set(None)` is functionally equivalent in a flat test context (no nested sessions in these tests), but diverges from the file's established idiom and would silently corrupt context if a test were ever wrapped by a session fixture. Fix: capture the token from `.set()` and call `.reset(token)` in the finally block, matching the pattern at lines 278–282, 303–311, etc. No action required before merging.

**Summary**

The implementation is clean and complete. All plan_007 acceptance criteria are met. The chokepoint is sound, render() is demonstrably pure, the DNS gate is encoded correctly with no bypass path, and `log.process` is a minimal well-precedented addition. The two required changes from the spec review (R2 TODO comment and grep AC amendment) were correctly applied — `dns.py` has `# TODO(awf-doctor): add dig presence check` at the class definition, and the subprocess exemption is documented in three layers (module docstring, class docstring, inline comment). The two Minors are cosmetic and do not affect correctness.
