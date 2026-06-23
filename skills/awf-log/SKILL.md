---
name: awf-log
description: CLI window onto the awf event log and the cross-project applied-state inventory. Query, tail, search, annotate, and replay per-project event history; or list/locate applied resources across all projects (inventory, where, rebuild-index). Use to inspect what happened in a session, find an event, add a note, or answer "what has been applied, how, and where."
---

# Purpose

`awf-log` gives humans and the LLM structured read access (and one write: `note`) to
the append-only event log created by `lib/log.py`. It surfaces per-project history
that would otherwise require raw `cat .awf/log.jsonl | jq` commands.

The per-project sub-commands map to the operations documented in
`docs/08-logging.md`. Three further sub-commands (`rebuild-index`,
`inventory`, `where`) expose the **cross-project applied-state inventory**
(`lib/inventory.py`, D-012): a projection that joins each project's state
files (`passport.json` / `.awf/infra.json`, the source of truth) with its
event log (for provenance) into one view of every applied resource.

---

# Sub-commands

## `tail [-n N]`

Print the last N events from `.awf/log.jsonl`, oldest-of-tail first (Unix `tail` style).

```
uv run "$AWF_HOME/skills/awf-log/scripts/log.py" tail [-n 50] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `-n N` | `50` | Number of events to return |
| `--json` | `false` | Emit raw JSONL (one event per line); no banner |

Human mode banner: `# last N events (oldest first):`

---

## `session [<id>|last]`

Print all events belonging to a session (from `session.start` through `session.end`).

```
uv run "$AWF_HOME/skills/awf-log/scripts/log.py" session [<id>|last] [--json]
```

| Arg | Default | Description |
|-----|---------|-------------|
| `<id>` | `last` | Session ULID or the literal `last` |
| `--json` | `false` | Emit raw JSONL |

---

## `find <pattern>`

Regex search across all events; print matching events as JSONL.

```
uv run "$AWF_HOME/skills/awf-log/scripts/log.py" find <pattern> [--type <event_type>] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--type` | (all) | Filter to a specific `type` value before applying regex |
| `--json` | `false` | (already JSONL output; flag kept for consistency) |

---

## `diff`

Stub — drift detection lives in `awf-status` (plan_012).

```
uv run "$AWF_HOME/skills/awf-log/scripts/log.py" diff
```

Prints a redirect message and exits 0.

---

## `note "<text>"`

Append a manual `note` event to the project log.

```
uv run "$AWF_HOME/skills/awf-log/scripts/log.py" note "<text>"
```

Requires `.awf/project.json` in an ancestor directory. Exits 1 if no project is found.

---

## `replay <session>`

Render a narrative summary of a session: timeline, step list, result.

```
uv run "$AWF_HOME/skills/awf-log/scripts/log.py" replay <session>
```

For in-progress sessions (no `session.end`), ends the narrative with
"no session.end recorded".

---

## `sessions [--days 30]`

Read `~/.config/awf/sessions.jsonl` (cross-project index) and print a summary table.

```
uv run "$AWF_HOME/skills/awf-log/scripts/log.py" sessions [--days 30] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--days N` | `30` | Limit to sessions started within the last N days |
| `--json` | `false` | Emit raw JSONL |

---

## `rebuild-index`

Re-scan every known project (discovered from `sessions.jsonl`, plus the current
project) and rewrite the applied-state inventory cache at
`~/.config/awf/inventory.jsonl`. The per-project state files are always the
source of truth; this only refreshes the cache, so it is safe to run any time
(Terraform `refresh` analogue). Run after a launch, or whenever the inventory
looks stale.

```
uv run "$AWF_HOME/skills/awf-log/scripts/log.py" rebuild-index [--json]
```

## `inventory [--provider NAME] [--project SLUG]`

List every applied resource across all projects — provider, type, resource id,
the skill that applied it, and when. Answers "what has been applied, and where."
Auto-builds the cache on first use if absent.

```
uv run "$AWF_HOME/skills/awf-log/scripts/log.py" inventory [--provider cloudflare] [--project my-site] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--provider NAME` | (all) | Filter to one provider (`cloudflare`, `neon`, `hetzner`, `fathom`, `kamal`, …) |
| `--project SLUG` | (all) | Filter to one project (slug or path) |
| `--json` | `false` | Emit raw JSONL |

## `where <resource_id>`

Reverse lookup: which project a resource id belongs to (full or prefix match),
with its provenance. Answers "where does `srv_123` live, and who applied it?"

```
uv run "$AWF_HOME/skills/awf-log/scripts/log.py" where <resource_id> [--json]
```

---

# Inputs / invocation

Run from any directory inside the project (project is located by walking up to find
`.awf/project.json`). The `note` sub-command is the only one that requires a project.

```
uv run "$AWF_HOME/skills/awf-log/scripts/log.py" <subcommand> [args]
```

Or let Claude Code invoke it directly via the skill runner.

---

# Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success (including empty results) |
| `1` | No project found when required (`note` only) |
| `4` | Invalid input: bad regex, unknown session ID, bad argument value |

---

# LLM directive

Use `awf-log tail` as your first move when diagnosing a failed or unexpected run.
Use `awf-log session last` to inspect what the most recent session did.
Use `awf-log note` to record a manual observation before handing off.
Use `awf-status` (not `awf-log diff`) for live drift detection.
