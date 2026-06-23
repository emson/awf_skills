# 08 — Logging

Every skill records what it did. The log is the project's history; the
project anchor and passport are its current state. Together they
support debugging, LLM session handover, drift detection, resumption,
rollback, and audit — without external infrastructure.

> Status: design accepted (D-002); adoption enforced (D-011). `lib/log.py`
> and `awf-log` are built. `Passport.save()` now auto-emits `state.change`
> (mirroring `lib/state.py`), and every state-mutating skill wraps its work
> in `log.session(...)` — enforced by `tools/loglint.py` +
> `tests/test_log_coverage.py`. A central applied-state inventory and
> applied-revisions are reasoned but deferred (D-012).

---

## What's stored, where

```
project/.awf/
├── project.json           # state (identity + stage + pointers)
├── infra.json             # state (S3+ resource IDs)
└── log.jsonl              # history (this file) — gitignored

~/.config/awf/
└── sessions.jsonl         # tiny cross-project index — one line per completed session
```

**`.awf/log.jsonl`** is the source of truth for history. Append-only
JSON Lines, one event per line, per-project, **gitignored by default**
(it's runtime state, not source). A user who wants git as audit can
opt in by `!.awf/log.jsonl` in their own `.gitignore`.

**`~/.config/awf/sessions.jsonl`** is a thin index — one summary line
per completed session — that enables cross-project queries
("what did I launch this month") without scanning every project.
Path references in the index are hints, not authoritative; if a
project moved, the project-local log is still findable from that dir.

State vs history (per A11, restated): **passport / project.json win
for "what is true now"; log wins for "what happened."** They don't
try to be each other. Drift detection compares world to passport;
forensics replays from log; both are first-class.

---

## Event schema

JSON Lines, UTF-8, one event per line. Newlines inside string values
are escaped (`\n`). Required fields are present on every event:

```json
{
  "ts": "2026-05-31T20:04:13.142Z",
  "session": "sess_01HZK7M3...",
  "project": "myapp",
  "stage": "landing",
  "actor": "claude-code",
  "type": "skill.invoke",
  "skill": "awf-setup-domain",
  "result": "ok",
  "duration_ms": 1240,
  "data": { "...event-specific..." }
}
```

| Field | Type | Notes |
|---|---|---|
| `ts` | string | UTC, ISO 8601 with milliseconds |
| `session` | string | ULID; one per composer invocation, threaded through atomic skills |
| `project` | string | slug from `.awf/project.json` |
| `stage` | string | stage at time of event |
| `actor` | string | `claude-code` \| `cli` \| `human` |
| `type` | string | see table below |
| `skill` | string | omitted for `session.*`, `note`, `error` not raised from a skill |
| `result` | string | `ok` \| `fail` \| `skip` \| `gate` \| `pending` (intent only) |
| `duration_ms` | number | wall time; omitted for instantaneous events |
| `data` | object | event-type-specific payload, redacted |

### Event types

| `type` | When | `data` carries |
|---|---|---|
| `session.start` | composer entry | composer, target_stage, source_stage |
| `session.end` | composer exit | events_count, gates_hit, summary |
| `skill.invoke` | atomic skill entry | args (redacted) |
| `skill.complete` | atomic skill exit | (mirrors invoke; carries result) |
| `api.call` | external HTTP call | provider, method, path, status_code, resource_id |
| `state.change` | passport / project / infra mutation | file, key, before, after |
| `gate.hit` | manual gate reached | gate_name, reason, instructions |
| `error` | exception captured | message, hint (no stack traces by default) |
| `intent` | dry-run preview | action, impact |
| `note` | manual annotation | text, by |

Schemas tolerate extra keys — old events stay readable as the schema
grows.

---

## Redaction policy

**Default-deny for secrets, default-allow for resource identifiers.**
Skill writers never log raw HTTP headers, raw `.env` values, or
provider credentials. The `safe_log()` helper enforces this with a
regex denylist applied to every value before write:

- key name matches `Authorization`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `*_KEY` (except `*_KEY_ID`) → value replaced with `***`
- bearer tokens or `sk-`/`pat-`/`hk-`/`tok-` prefixed strings detected anywhere → replaced with `***`

Resource IDs (CF zone ID, Hetzner server ID, Neon project ID) **are
safe to log** — they identify what was created, not how to access it.

The denylist is documented in `lib/log.py`; adding to it is a
one-line change reviewed in PR.

---

## Concurrency and durability

- **Atomic append** is guaranteed by POSIX `O_APPEND` for writes
  under `PIPE_BUF` (4 KB). The schema is designed so a single event
  fits well within this — typical events are 200–400 bytes.
- For events that exceed 4 KB (e.g., a `state.change` carrying a
  large before/after diff), `lib/log.py` switches to `fcntl.flock`.
  Diff size in `state.change` is capped at 2 KB per side; larger
  diffs record a hash + pointer instead.
- **Best-effort.** A failure to write the log never breaks a skill.
  The helper catches `OSError`, prints a one-line warning to stderr,
  and continues. Logging is observability, not the critical path.

---

## The `lib/log.py` API

Public surface (deliberately small):

```python
from lib.log import log

with log.session(composer="awf-stage-mvp-play", target="mvp-play"):
    with log.invoke("awf-hetzner-server", args={"region": "fsn1"}):
        ...
        log.api(provider="hetzner", method="POST",
                path="/servers", status=201, resource_id="srv_123")
        log.state_change(file=".awf/infra.json",
                         key="hetzner.servers[0]", before=None, after={...})

log.gate(name="ns_swap_required", reason="manual NS swap at Namecheap",
         instructions="Visit namecheap.com → ...")

log.error(msg="kamal deploy failed", hint="check Dockerfile syntax")

log.intent(action="provision hetzner server", impact="€5/mo")  # dry-run

log.note("manual: re-ran kamal deploy directly")
```

`session` and `invoke` are context managers — they emit start/end
events with timing automatically, and they thread `session_id` and
`skill` through nested events without the caller passing them.

---

## The `awf-log` skill

Surface for humans and LLMs:

```
awf-log tail [-n 50]              # last N events, formatted for reading
awf-log session [<id>|last]       # full event list for one session
awf-log find <pattern>            # structured grep
awf-log diff                      # log-vs-world drift (delegates into awf-status)
awf-log note "<text>"             # manual annotation
awf-log replay <session>          # narrative summary of a session
awf-log sessions [--days 30]      # central-index summary across projects
```

`awf-status` is extended to print the last 5 events at the top of its
output, so the LLM's first instinct ("check status") naturally
surfaces history.

---

## Operational rules

1. **Logging never raises.** A logging error is a stderr warning, not
   a skill failure.
2. **Skills emit `state.change` whenever they write to `passport.json`
   or `.awf/*.json`.** This is the contract that makes drift
   detection work.
3. **Composers thread `session_id` to every atomic skill they call.**
   Atomic skills called directly start their own one-event session.
4. **Skills never log raw secrets, headers, or `.env` values.** The
   helper enforces this; reviewers double-check in PR.
5. **The LLM treats log entries as history, not state.** "Where am I
   now" is `awf-status`. "What happened" is `awf-log`.
6. **Logs are bounded reads.** Skills and the LLM read by tail or
   session — never the whole file at once.

---

## Locked-in defaults (the open questions, resolved)

- **Gitignored by default.** Opt-in to tracking is a one-line user
  change. (Q1)
- **`awf-status` prints last 5 events.** Promotes "check the log
  first" as LLM default behaviour. (Q2)
- **`intent` events fire only with `--dry-run`.** Avoids double-log
  noise on real runs. Opt-in via `AWF_LOG_INTENTS=1`. (Q3)
- **Session IDs are ULIDs**, implemented inline in `lib/log.py`
  (~20 lines), no new dependency. (Q4)

---

## What is deferred

- Log rotation / archival (not needed until >10 MB; months/years
  away).
- Remote log shipping / sinks.
- Trace-tree visualization (the `replay` narrative is enough).
- Cost/metric aggregation (separate concern; see open ideas in
  [`decisions.md`](decisions.md)).
- Schema migrations beyond "tolerate extra keys."

These will revisit when concrete need appears, not before.
