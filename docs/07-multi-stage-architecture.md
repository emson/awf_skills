# 07 — Multi-Stage Architecture

How awf-skills grows from a static landing page (S1) to a horizontally-
scaled web app (S5) without re-architecting the project. This document
locks in the pattern; the per-stage implementations are tracked in
[`decisions.md`](decisions.md) and built incrementally.

> Status: pattern accepted, implementation deferred. Today the suite
> implements S1 only (landing page on Cloudflare Pages). S2–S5 are
> specified here so atomic skills can be built against the same model.

---

## The stage ladder

A project lives at exactly one stage. Promotion is deliberate and
human-approved; there is no auto-scaling.

| Stage | Code shape | Compute | Database | Edge |
|---|---|---|---|---|
| **S1** Landing | Static SvelteKit | Cloudflare Pages | — | CF DNS |
| **S2** Demo | SvelteKit + client mocks | Cloudflare Pages | — | CF DNS |
| **S3** MVP-play | SvelteKit + server routes, dockerized | Shared Hetzner VM (multi-tenant via Kamal) | Shared Neon project, one branch per app | CF DNS, grey cloud |
| **S4** Prescale | Same code | Hetzner LB + dedicated VM(s) | Dedicated Neon project | CF proxy (orange), CDN |
| **S5** Scale | Same code | LB + N VMs + cache + workers | Neon + read replica | CF + R2 |

The phase change is **S2 → S3**: serverless static hosting becomes a
real server running a container. Everything before is HTML/JS on a CDN;
everything after is the same Docker image moving across more or
better-shaped infrastructure. The whole point of the design is that
S3 → S4 → S5 is configuration evolution, not re-architecture.

---

## Two layers of skills

**Atomic resource skills** own exactly one kind of thing in the world
and are idempotent (per A1). They are runnable directly by humans or
LLMs for surgical fixes.

```
awf-cf-zone           awf-hetzner-server         awf-neon-project
awf-cf-pages          awf-hetzner-lb             awf-neon-branch
awf-cf-dns-record     awf-hetzner-network        awf-neon-promote
awf-cf-proxy-toggle   awf-hetzner-firewall       awf-r2-bucket
awf-cf-cache-rules                                awf-upstash-redis

awf-kamal-config      awf-app-dockerize          awf-app-healthcheck
awf-kamal-setup       awf-app-add-db-client      awf-app-secret-set
awf-kamal-deploy      awf-app-add-worker         awf-shared-infra-get
```

**Composer skills** drive a project toward a named stage. They diff
current state against the target and call atomic skills to close the
gap. They are declarative: "ensure this project is at stage X."

```
awf-stage-landing       (S1 — equivalent to today's awf-launch)
awf-stage-demo          (S2)
awf-stage-mvp-play      (S3)
awf-stage-prescale      (S4)
awf-stage-scale         (S5)
```

Plus a few cross-cutting verbs that do not fit the stage model:

```
awf-status              (extend: reports current stage + drift)
awf-domain-change       (re-point DNS + kamal + redeploy)
awf-teardown            (destroy dedicated infra; --scorched-earth includes shared)
awf-rollback            (kamal rollback wrapper)
```

The LLM-facing surface is **"promote to stage X."** The LLM does not
plan the sequence; the composer does, from passport state.

---

## File layout: where state lives

The original `passport.json` was scoped to landing-page concerns
(content, feature flags, SEO/analytics IDs, launch gates). It stays
exactly as it is. A separate, minimal **project anchor** owns
identity and stage; infra concerns get their own file when they
appear; Kamal's native files are left alone.

```
project/
├── .awf/
│   ├── project.json          # identity + stage + what-exists pointers
│   └── infra.json            # S3+: hetzner/neon/registry resource IDs
├── passport.json             # UNCHANGED: landing-page contract (S1/S2)
├── content.json              # optional split-out of content (deferred)
├── config/deploy.yml         # S3+: kamal native
└── .kamal/secrets            # S3+: kamal native
```

### `.awf/project.json` — the new project anchor

Minimal. Always present from S1.

```json
{
  "domain": "example.com",
  "slug": "example",
  "stage": "landing",
  "has": { "passport": true, "infra": false, "kamal": false }
}
```

Skills walk up from `cwd` to find this file (replaces today's
"walk up for `passport.json`" rule in `lib/project.py`). Existing
landing-page skills get a one-line shim to load `passport.json`
alongside.

### `passport.json` — landing-page contract, unchanged

Owned by S1/S2. Holds site content, feature flags, Fathom site ID,
GSC verification, Bing IndexNow key, launch gates. Lingers at S3+
**only if** the project still has a marketing page; an app-only
project never grows one.

Schema is the existing v1.0; see [`03-passport-contract.md`](03-passport-contract.md).

### `.awf/infra.json` — appears at S3

Records the resources Kamal does not track. Tiny; mostly pointers.

```json
{
  "hetzner": { "servers": [...], "lb_id": "...", "network_id": "..." },
  "neon":    { "project_id": "...", "branch_id": "...", "mode": "shared-branch" },
  "registry": { "host": "ghcr.io", "image": "user/example" }
}
```

### Kamal native files — `config/deploy.yml`, `.kamal/secrets`

Generated by `awf-kamal-config` from `project.json` + `infra.json`,
but the truth-of-record for the deploy lives in Kamal's own files.
`kamal deploy` must work without our skills present.

### `~/.config/awf/shared.json` — user-scope shared infra

Resources that belong to the user, not to one project: the play
Hetzner server, the play Neon project, default registry credentials
reference. Created lazily on first S3 promotion; reused across all
play apps.

```json
{
  "play_server":     { "ip": "...", "hostname": "play.example", "registry": "ghcr.io/user" },
  "play_neon_project_id": "..."
}
```

---

## Why Kamal is the right abstraction for S3–S5

Kamal collapses the difference between "1 server" and "N servers"
into a YAML list. It also handles image build + push, SSH-driven
rolling deploy, Traefik reverse proxy with automatic Let's Encrypt,
multi-app hosting on one box, accessories, and rollbacks. S3 and S4
are the same Kamal config with a different `servers:` list; the
skill never has to know whether the box is shared or dedicated.

What Kamal does not solve and we own separately:

- the load-balancer layer (Hetzner LB, not Kamal's Traefik spread)
- the database (Neon, via Kamal secrets)
- the CDN (Cloudflare, via DNS + proxy toggle)

---

## Operational rules

The constraints below have been worked through in scenario analysis
(see [`decisions.md`](decisions.md) D-001). Skills must enforce them.

1. **DNS-before-TLS.** Kamal will fail Let's Encrypt issuance if the
   A record has not propagated. The promotion skill must poll
   `dig +short` against the server IP before calling `kamal setup`,
   with a hard timeout that escalates to a manual gate.

2. **Orange-cloud after cert.** Cloudflare proxy must be off when
   Traefik first obtains its cert via HTTP-01; flipped on only after
   a successful deploy.

3. **Stage promotion is forward-only.** Demotion uses `awf-teardown`
   then re-promotes, not a reverse path. Avoids implicit state loss.

4. **Composers are the canonical sequencers.** Atomic skills must
   not assume an order; composers encode it.

5. **Manual gates terminate with a known exit code.** Any skill that
   hits a gate (e.g., "approve Bing import", "DNS not propagated")
   exits non-zero with a JSON gate descriptor on stdout. The LLM is
   instructed never to silently proceed past a gate.

---

## What is deferred

Locked in by this document:

- The stage model and the names of the five stages.
- The two-layer skill split (atomic + composer).
- The file layout (`.awf/project.json` as anchor; `passport.json`
  untouched; `.awf/infra.json` for S3+; Kamal native files).
- Kamal as the S3–S5 deploy abstraction.
- Shared vs dedicated resource model, with shared state in
  `~/.config/awf/shared.json`.

Not yet decided; explicitly out of scope for this turn:

- Concrete schema for `.awf/project.json` and `.awf/infra.json`
  beyond the sketches above.
- Image registry choice (GHCR vs Docker Hub vs self-hosted).
- Cache provider at S5 (Upstash vs self-hosted Redis via Kamal
  accessory).
- Background worker model.
- Object storage choice (R2 is the strong default).
- The actual implementation of any S2–S5 skill.

These are tracked as open decisions in [`decisions.md`](decisions.md)
and will be resolved as each composer is built.
