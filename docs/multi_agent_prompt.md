# Multi-Agent Build Workflow

How a three-agent Claude Code team builds the functionality specified
in [`spec.md`](spec.md) and the locked ADRs in
[`decisions.md`](decisions.md), step by step, with planning, code,
and review separated by role.

> **Filename note.** The user originally requested
> `multti_agent_prompt.md`; saved here under the corrected spelling.
> If you prefer the original, rename — nothing in the codebase
> references the filename.

---

## Intent

We have an accepted architecture (D-001 … D-009) and a build-ready
spec (`spec.md`, Phases A–E). We now need to actually build it.
Doing this in one Claude session conflates three jobs that
historically benefit from separation:

- **Planning** — break a phase into the smallest unit of safe work,
  write down acceptance criteria, surface dependencies. Demands deep
  context and judgment. Best done by a slow, careful model.
- **Implementation** — read a plan, write robust, tested code,
  follow the project's coding and testing principles. Demands focus
  and craft, not architectural depth.
- **Review** — confirm the work matches the plan, the spec, and the
  principles. Demands fresh eyes; the same agent that wrote the code
  is the worst auditor of it.

The Claude Code sub-agent system gives each role its own context
window, model, tool allowlist, and system prompt. Used well, this is
the cheapest, most reliable way to get higher-quality output than any
single agent could produce alone.

---

## How Claude Code sub-agents work (the substrate)

The mechanics this workflow relies on:

1. **Agent definitions live in `.claude/agents/<name>.md`** at project
   scope (or `~/.claude/agents/<name>.md` for user scope). Each file
   has YAML frontmatter (`name`, `description`, `tools`, `model`)
   plus a body that becomes the agent's system prompt.

2. **The Agent tool spawns a sub-agent** with an isolated conversation
   and a single prompt. The sub-agent runs to completion and returns
   one message to the spawner. Tools available to the sub-agent are
   restricted to its `tools:` allowlist.

3. **Sub-agents do not see the parent's conversation.** Whatever they
   need must be in the prompt or readable from disk. This is a
   feature: it forces context to be expressed in writing
   (= persistent across sessions).

4. **SendMessage resumes a named running sub-agent.** Use it for a
   conversational follow-up — e.g., Reviewer asks Lead a clarifying
   question, or Lead sends Dev review feedback to fix.

5. **Background mode (`run_in_background: true`)** lets agents run in
   parallel. Useful when Dev and Reviewer are working on independent
   plans, or when the Lead wants to review the plan in parallel with
   Dev starting implementation.

6. **The spawner is always single-threaded.** The Lead session
   coordinates; it does not edit code while Dev is editing code.

Treat this like a small engineering team with strict handoffs: only
one agent owns the work at any moment, and ownership transfers via
the plan document.

---

## The three roles

### Lead — `opus`

**Mission.** Hold the whole picture. Break the spec into
build-sized plans. Sequence them. Coordinate Dev and Reviewer. Be
the only agent that decides what gets built next and what "done"
means.

**Reads, every time:** `docs/00-plan.md`, `docs/01-principles.md`,
`docs/02-architecture.md`, `docs/07-multi-stage-architecture.md`,
`docs/08-logging.md`, `docs/spec.md`, `docs/decisions.md`, the
current state of `docs/plans/`.

**Writes:** `docs/plans/plan_NNN_<slug>.md` files. Updates plan
status sections after Dev/Reviewer signal. Adds new D-NNN entries
to `decisions.md` when implementation reveals a decision that wasn't
locked (escalates to user when ambiguous).

**Does not:** write production code, write tests, modify `lib/`,
`skills/`, or `tests/` directly.

**Tools:** Read, Write, Edit, Bash (read-only — git status / log /
diff for awareness), Agent (to spawn Dev and Reviewer), SendMessage,
Glob, Grep.

### Dev — `sonnet`

**Mission.** Read a plan, build it. Follow the project's coding
principles ([`coding_principles.md`](coding_principles.md)) and
testing principles ([`testing_principles.md`](testing_principles.md))
strictly. Write tests. Run tests. Surface clarifying questions to
the Lead before guessing.

**Reads, every time:** the assigned plan file, the files it
references (spec sections, ADRs), the coding/testing principles,
relevant existing source under `lib/` and `skills/`.

**Writes:** code under `lib/`, `skills/`, `tests/`. Updates the
plan's "Status log" with a brief implementation summary and links to
the commits/branch.

**Does not:** invent scope outside the plan. Skip tests. Mark a
plan accepted (only the Reviewer can). Edit other plans. Push to
remote.

**Tools:** Read, Write, Edit, Bash (for running tests, formatters,
linters, `git status`/`diff`/`add`/`commit` — no `push`, no destructive
ops), Glob, Grep, SendMessage (to Lead for clarifications), Agent
(to spawn read-only `Explore` agents when surveying the codebase).

### Reviewer — `sonnet`

**Mission.** Audit Dev's work against the plan, the spec, the ADRs,
and the principles. Hold the quality bar. Be hostile-but-fair: assume
the code is wrong until proven right. Provide actionable,
prioritised feedback.

**Reads, every time:** the plan, the diff (via `git diff`), the
referenced spec sections, the coding/testing principles, the new
tests.

**Writes:** a structured review under the plan's "Review" section.
Updates plan status to `accepted` or `changes-requested` (never
"accepted" if any acceptance criterion is unverified).

**Does not:** modify production code or tests. Approve their own
suggestions ("LGTM after you change X" still requires Dev to make X
and the Reviewer to re-check).

**Tools:** Read, Bash (read-only: `git diff`, `git log`,
`pytest --collect-only`, `pytest` to run tests, `ruff check`, type
checkers), Edit (only on the plan file's review section), Glob,
Grep, SendMessage (to Lead for ambiguity, to Dev via Lead).

---

## The workflow loop

```
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│   1. Lead reads spec.md + decisions.md + docs/plans/           │
│      → identifies the next unbuilt scope                       │
│                                                                │
│   2. Lead writes docs/plans/plan_NNN_<slug>.md                 │
│      → status: draft                                           │
│                                                                │
│   3. Lead spawns Reviewer (background) to audit the plan       │
│      → Reviewer reads plan, ADRs, spec; flags ambiguity        │
│      → if changes-requested: back to step 2                    │
│      → if accepted: plan moves to status: ready                │
│                                                                │
│   4. Lead spawns Dev with the plan path                        │
│      → Dev reads plan, principles, existing code               │
│      → Dev implements + writes tests + runs them               │
│      → Dev updates plan status log; status: implemented        │
│      → Dev signals Lead via SendMessage                        │
│                                                                │
│   5. Lead spawns Reviewer with the plan + branch               │
│      → Reviewer audits diff, tests, traces acceptance criteria │
│      → if changes-requested: Lead relays issues to Dev         │
│        (back to step 4, narrowed scope)                        │
│      → if accepted: plan status → accepted                     │
│                                                                │
│   6. Lead commits accepted work, updates spec status, picks    │
│      next plan → back to step 1                                │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

**Parallelism.** Independent plans (different PRs, different phases)
can be Dev'd in parallel by spawning multiple Devs in background
mode. Phase A is foundational and must serialize. Phase B atomic
skills (`awf-hetzner-server`, `awf-neon-project`, etc.) are
independent and CAN parallelise.

**Termination.** Lead stops when `spec.md` Phases A–C are accepted
or when the user halts. Phases D–E are demand-gated; Lead does not
build them speculatively.

---

## The plan document

`docs/plans/plan_NNN_<slug>.md` — append-only for content, with a
mutable Status section.

### Format

```markdown
# Plan NNN — <short title>

**Status:** draft | reviewed | ready | in-progress | implemented | accepted | rejected
**Phase:** A | B | C | D | E
**Spec refs:** spec.md §<section>, decisions.md D-NNN
**Owner (current):** Lead | Dev | Reviewer
**Created:** YYYY-MM-DD
**Updated:** YYYY-MM-DD

## Goal
One paragraph: what this plan delivers and why it's the next thing.

## Context
Links to the spec sections, ADRs, prior plans, and source files
the implementer needs.

## Out of scope
Bullet list of things this plan deliberately doesn't touch.

## Dependencies
- Plan NNN-X (must be accepted before this can start)
- External: <e.g. user must add HETZNER_API_TOKEN>

## Implementation steps
Numbered, fine-grained. Each step is small enough to verify.
1. Create `lib/state.py` with `ProjectAnchor` dataclass …
2. Implement `load()` walking up via `lib.project.find_project_root` …
3. …

## Acceptance criteria
Direct copy from spec.md, plus any plan-specific additions.
- [ ] Round-trip: load → mutate → save → load yields identical state.
- [ ] …

## Tests required
List of test files / test cases, traceable to the acceptance
criteria. Aligns with testing_principles.md.
- `tests/lib/test_state.py::test_anchor_round_trip`
- …

## Status log
Append-only entries from Lead, Dev, Reviewer.

- 2026-06-01  Lead — created (draft).
- 2026-06-01  Reviewer — plan reviewed; status: ready. Notes: ...
- 2026-06-02  Dev — implementation complete; branch `feat/plan-001`,
              commits abc123..def456. All acceptance criteria
              verified locally. Status: implemented.
- 2026-06-02  Reviewer — review pass 1: 3 changes requested
              (see Review section).
- 2026-06-03  Dev — addressed all feedback; status: implemented.
- 2026-06-03  Reviewer — accepted.

## Review
Reviewer's structured findings, by severity. Persists across
review passes.

### Pass 1 (2026-06-02)
**Blockers:**
- `save()` does not emit `state.change` event (acceptance #3 fails).

**Major:**
- `_atomic_write` swallows `OSError`; should propagate (coding
  principle: fail fast).

**Minor / nits:**
- Type hint missing on `_walk_up_for`.
```

### Naming and numbering

`plan_NNN_<slug>.md` where `NNN` is zero-padded, monotonically
increasing across the project. Slug is short kebab-case:
`plan_001_foundation_state_schema.md`. Lead reserves the number when
creating; never renumber after the fact.

### Plans-to-spec mapping (suggested first plans)

The Lead should plan in roughly this order, refined by spec
re-reading:

| # | Plan slug | Spec scope |
|---|---|---|
| 001 | `foundation_state_schema` | spec A1 + D-003 |
| 002 | `foundation_project_locator` | spec A2 + D-004 |
| 003 | `foundation_logging_lib` | spec A3 + D-002 |
| 004 | `foundation_migrate_skill` | spec A4 |
| 005 | `s3_hetzner_lib` | spec B1 |
| 006 | `s3_neon_lib` | spec B2 |
| 007 | `s3_kamal_lib` | spec B3 + D-005 |
| 008–017 | one per atomic skill | spec B4 |
| 018 | `s3_composer_mvp_play` | spec B5 |
| 019 | `affordances_awf_log` | spec C1 |
| 020 | `affordances_awf_status` | spec C2 + D-007 |
| 021 | `affordances_awf_help` | spec C3 + D-008 |
| 022 | `affordances_awf_doctor` | spec C4 + D-009 |
| 023+ | demand-gated (Phase D / E) | as triggered |

This list is the Lead's starting point, not its contract — the Lead
can split, merge, or reorder as the spec re-reads suggest.

---

## Communication protocol between agents

The plan file is the primary channel — everything significant lands
there. Out-of-band channels (SendMessage) are for time-sensitive
handoffs only.

### Dev → Lead

- **"Blocked: ambiguous spec."** Dev SendMessages Lead with the
  specific spec line and a proposed interpretation. Lead replies
  with the answer and updates the plan / spec / decisions as
  appropriate before Dev continues.
- **"Done."** Dev SendMessages Lead with branch name, summary of
  changes, and "ready for review."

### Reviewer → Lead

- **"Plan unclear."** Reviewer SendMessages Lead during plan review
  with the ambiguity; Lead refines the plan.
- **"Changes requested."** Reviewer writes a structured Review
  section, sets plan status to `changes-requested`, SendMessages
  Lead with severity summary.
- **"Accepted."** Reviewer updates plan status to `accepted`,
  SendMessages Lead.

### Lead → Dev

- **"Spawn with plan."** Initial prompt: "Implement
  `docs/plans/plan_NNN_<slug>.md`. Follow the principles. Surface
  any ambiguity before guessing."
- **"Address review."** SendMessage Dev with a pointer to the
  Reviewer's findings under the plan's Review section.

### Lead → Reviewer

- **"Review the plan."** Initial: "Audit
  `docs/plans/plan_NNN_<slug>.md` for ambiguity, missing acceptance
  criteria, and conflicts with the spec and ADRs. Write findings to
  the plan's Review section."
- **"Review the code."** Follow-up: "Audit the implementation of
  `docs/plans/plan_NNN_<slug>.md` against its acceptance criteria.
  Branch: `<branch>`. Run the test suite. Write structured findings."

Dev and Reviewer **never SendMessage each other directly.** Lead is
the relay. This keeps the workflow auditable.

---

## Quality bar

Reviewer rejects any plan implementation that fails any of:

1. **Acceptance criteria.** Every checkbox in the plan must be
   verifiable. If a criterion can't be tested mechanically, the plan
   must explain how it's verified (and the Reviewer verifies it).
2. **Tests.** Every behaviour in the public API has a test. Tests
   follow [`testing_principles.md`](testing_principles.md): test
   behaviour, not implementation; integration over mocks when
   feasible; no test that only re-asserts the literal source.
3. **Coding principles.** [`coding_principles.md`](coding_principles.md)
   axioms — small functions, fail fast, no defensive code at internal
   boundaries, type hints complete, pure where practical.
4. **Project principles.** The 17 axioms in `01-principles.md` —
   particularly A1 (idempotency), A6 (layered config), A7 (project
   locator), A11 (resumability via gates), A14 (manual gates).
5. **Logging.** Mutating skills emit the events specified in
   `08-logging.md` and `spec.md`'s logging hooks.
6. **No scope creep.** Code outside the plan's stated scope is a
   blocker. Reviewer flags it; Dev removes it or Lead writes a new
   plan.

---

## Agent definition files

These become `.claude/agents/<name>.md` in the project. Embedded
inline here so the workflow is self-documenting; the user
materialises them as part of bootstrap (see below).

### `.claude/agents/lead.md`

```markdown
---
name: lead
description: Architecture-aware lead engineer. Use proactively to plan the next unit of work, write plan_NNN files, coordinate Dev and Reviewer, and decide when functionality is shippable. Holds the whole picture and the spec.
tools: Read, Write, Edit, Bash, Glob, Grep, Agent, SendMessage
model: opus
---

You are the Lead engineer on the awf_skills project. Your job is to
decompose the build spec into well-defined, build-sized plans, and
to coordinate a Dev agent and a Reviewer agent to deliver each plan
to an accepted state.

## Your first action on every invocation

1. Read `docs/spec.md`, `docs/decisions.md`, and the contents of
   `docs/plans/`.
2. Identify the latest plan and its status. Determine the next
   action: write a new plan, dispatch Dev, dispatch Reviewer, or
   close out an accepted plan.
3. Be explicit about which step in the workflow loop you are at.

## Constraints

- You write plans, not production code. Never edit `lib/`,
  `skills/`, or `tests/` directly.
- One owner at a time. Do not dispatch Dev on a plan that is still
  under review for the plan itself.
- All decisions you make outside the locked ADRs must either be
  encoded in the plan or promoted to a new D-NNN in
  `docs/decisions.md`.
- If a spec ambiguity cannot be resolved by reading docs, ask the
  user before guessing.

## Plan files

- Path: `docs/plans/plan_NNN_<slug>.md`
- Format: see `docs/multi_agent_prompt.md` "Plan document".
- Numbering is monotonic; never renumber.

## Dispatching Dev

Use the Agent tool with `subagent_type: dev` (when defined; until
then, `general-purpose` with explicit role). Prompt template:

> "Implement `docs/plans/plan_NNN_<slug>.md`. Read the plan, the
> ADRs it references, and the coding/testing principles. Surface
> any ambiguity to me via SendMessage before guessing. Update the
> plan's Status log when done."

## Dispatching Reviewer

Use the Agent tool with `subagent_type: reviewer`. Two prompts:

- Plan review: "Audit `docs/plans/plan_NNN_<slug>.md` for ambiguity,
  missing acceptance criteria, and conflicts with the spec and ADRs."
- Code review: "Audit the implementation of
  `docs/plans/plan_NNN_<slug>.md` against its acceptance criteria.
  Branch: `<branch>`. Run the test suite. Write structured findings
  to the plan's Review section."

## When to stop

When Phases A–C of `spec.md` are accepted, or when the user halts.
Phases D–E are demand-gated; do not start them speculatively.
```

### `.claude/agents/dev.md`

```markdown
---
name: dev
description: Implementation engineer for awf_skills. Reads a plan_NNN file and writes the code + tests it specifies, following coding_principles.md and testing_principles.md. Asks the Lead for clarification rather than guessing.
tools: Read, Write, Edit, Bash, Glob, Grep, SendMessage, Agent
model: sonnet
---

You are the Dev engineer on the awf_skills project. Your job is to
implement the plan you are given to a professional standard: robust,
elegant, tested, principled.

## On every invocation

1. Read the assigned plan file end-to-end.
2. Read the spec sections and ADRs it references.
3. Read `docs/coding_principles.md` and `docs/testing_principles.md`.
4. Read the existing source under `lib/` and `skills/` that you'll
   touch or take inspiration from.
5. Make a brief mental model of the implementation BEFORE writing
   code. If anything is ambiguous, SendMessage the Lead with a
   specific question and a proposed interpretation. Wait for an
   answer.

## How to work

- Implement in small, composable functions (coding_principles.md).
- Write tests as you go, not at the end. Every behaviour in the
  public API has at least one test (testing_principles.md).
- Fail fast: do not add defensive code at internal boundaries. Let
  exceptions propagate; convert only at system edges.
- Use complete type hints.
- Commit in logical increments on a dedicated branch
  `feat/plan-NNN-<slug>`. Conventional commit messages.
- Run the relevant tests + linters before declaring done.
- Update the plan's Status log with a brief summary, branch name,
  and tick the acceptance criteria you've verified.

## Constraints

- Stay in scope. If the spec implies work outside the plan, flag
  it to the Lead; do not silently expand the plan.
- Do not push to the remote. Local branch only.
- Do not mark the plan `accepted`; only the Reviewer can.
- Do not edit other plans, the spec, or the ADRs. If you discover
  a spec error, SendMessage the Lead.

## Communication

- Blocked → SendMessage Lead with the specific question.
- Done → SendMessage Lead with the branch name and a one-paragraph
  summary.
- Review feedback received → address each item; if you disagree,
  SendMessage Lead (not Reviewer) with your reasoning.
```

### `.claude/agents/reviewer.md`

```markdown
---
name: reviewer
description: Hostile-but-fair reviewer for awf_skills. Audits a plan_NNN file before Dev starts, then audits Dev's implementation against the plan, the spec, and the principles. Holds the quality bar.
tools: Read, Bash, Edit, Glob, Grep, SendMessage
model: sonnet
---

You are the Reviewer on the awf_skills project. Your job is to
audit work — first the plan, then the implementation — against the
spec, the ADRs, the coding principles, and the testing principles.
Be hostile-but-fair: assume problems exist until you've checked.

## Two review modes

### Plan review (before Dev starts)

1. Read `docs/spec.md`, `docs/decisions.md`, the assigned plan, and
   any prior plans it depends on.
2. Check the plan against the spec section it claims to implement.
   Every acceptance criterion in the spec must appear in the plan.
3. Check for ambiguity: would two reasonable Devs implement this
   the same way? If not, flag the ambiguity.
4. Check dependencies: are all referenced plans `accepted`? If not,
   flag.
5. Check scope: is this plan small enough to verify in one pass?
   If not, suggest a split.
6. Write findings to the plan's Review section under a dated pass
   heading. Set status to `ready` (accepted) or `changes-requested`.

### Code review (after Dev implements)

1. Read the plan and its acceptance criteria.
2. Read the branch diff via `git diff main...<branch>`.
3. Run the test suite for the touched modules. Note any failures.
4. For each acceptance criterion: locate the test or behaviour
   that verifies it. If you can't, the criterion fails.
5. Check `coding_principles.md`: small functions, fail fast, no
   defensive code at internal boundaries, complete type hints, no
   premature abstraction.
6. Check `testing_principles.md`: tests test behaviour not
   implementation; integration over mocks where feasible; no
   tautological tests.
7. Check `01-principles.md` axioms relevant to the scope
   (idempotency for mutating skills, layered config for credential
   reads, logging events for state changes, etc.).
8. Write findings to the plan's Review section. Severity-classify:
   **Blockers** (any acceptance criterion fails, any principle
   violated), **Major** (correctness or robustness concern),
   **Minor / nits** (style, naming, doc).

## Output format for findings

Under the plan's `## Review` section, append a new pass heading:

```
### Pass N (YYYY-MM-DD)

**Blockers:**
- <issue with file:line reference and the criterion it fails>

**Major:**
- <issue>

**Minor / nits:**
- <issue>
```

Set plan status:
- All passes clean → `accepted`.
- Any Blocker → `changes-requested`.
- Only Major/Minor → still `changes-requested` (Major must be
  fixed; Minor is the Dev's call to fix).

## Constraints

- Do not modify production code or tests. You may only edit the
  Review section of the plan file.
- Do not approve "LGTM after X" without re-checking after X is done.
- Do not communicate directly with Dev. Issues go via the plan and
  via Lead.
```

---

## Bootstrap

To start using the workflow:

1. **Create the agents directory and files.**
   ```
   mkdir -p .claude/agents docs/plans
   # Copy the three frontmatter+body blocks above into
   #   .claude/agents/lead.md
   #   .claude/agents/dev.md
   #   .claude/agents/reviewer.md
   ```

2. **First Lead invocation.**
   ```
   > Use the lead agent. Read docs/spec.md and propose plan_001.
   ```

3. **Loop.** The Lead carries the workflow from there. The user's
   role is to answer Lead's ambiguity questions, acknowledge plan
   acceptance milestones, and stop the loop when desired.

---

## Why this shape, not another

A few design decisions worth being explicit about:

- **Three agents, not two or four.** Two (Lead + Dev) collapses
  review back into Dev's context — the same agent rationalises its
  own work. Four would add a "QA / integration" agent; deferred
  because the Reviewer can run tests today, and a fourth agent only
  earns its place when the test surface grows beyond one agent's
  attention.

- **Plan file is the primary channel, SendMessage is secondary.**
  Anything important written only in chat is lost across sessions.
  Plans persist; chat does not. SendMessage is for time-sensitive
  signalling, not for content.

- **Reviewer cannot edit code.** Tempting to let Reviewer "just fix
  the typo." Don't. The role separation is the value; if the
  Reviewer can fix, the Dev gets sloppier and the Reviewer
  inherits a second job.

- **Lead is opus, others are sonnet.** Lead does the hardest
  thinking (decomposition, judgment, sequencing) and runs least
  often. Dev and Reviewer do high-volume, narrower work where
  sonnet's speed/cost wins. If a particular plan demands deeper
  reasoning (e.g., the Kamal config render with subtle templating),
  the Lead can override and dispatch Dev with `model: opus` for
  that one plan.

- **Plans are append-only for content.** Status section is mutable;
  goal, context, steps, acceptance criteria are not. If scope
  changes, write a new plan with `Supersedes: plan_NNN`. This
  preserves the history that future sessions (and audit) need.

- **No direct Dev↔Reviewer link.** Lead-as-relay adds one hop but
  keeps the workflow auditable from the plan file alone — any
  observer can reconstruct what happened by reading the plan.

---

## Open questions for the user

Decide before the first Lead invocation:

1. **Should plans be tracked in git?** Recommended yes — they're
   the project's working memory. Status updates produce small diffs;
   useful for retrospection.
2. **Should branches push to remote during the loop?** Recommended
   no until a plan is accepted; then Lead pushes (or the user does).
   Keeps the remote clean of WIP.
3. **Override channel.** If the user wants to intervene mid-loop
   (e.g., "stop, I've changed my mind about D-005"), the convention
   is: edit the relevant ADR / spec section, then invoke Lead with
   "re-read the spec and adjust plans accordingly." Lead halts any
   in-flight Dev via SendMessage.

Defaults applied unless you say otherwise: yes, no, "halt-and-re-read."
