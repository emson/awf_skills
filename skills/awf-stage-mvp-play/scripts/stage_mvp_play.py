#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///

"""awf-stage-mvp-play — S3 composer: promote project to mvp-play.

Sequences 8 atomic skills in dependency order:
  1. awf-shared-infra-get    (user-scope; shared server + Neon project)
  2. awf-app-dockerize       (Dockerfile, .dockerignore, healthcheck, db lib)
  3. awf-neon-branch         (branch on shared Neon project)
  4. awf-app-secret-set      (DATABASE_URL → .kamal/secrets)
  5. awf-kamal-config        (render config/deploy.yml)
  6. awf-cf-dns-record       (A record → play server IP, grey cloud)
  7. awf-kamal-setup         (kamal setup; polls DNS internally)
  8. awf-kamal-deploy        (kamal deploy)

On full success: advances anchor to stage="mvp-play", has.infra=True,
has.kamal=True.

On mid-run failure: anchor is NOT advanced; partial state in infra.json /
shared.json reflects what atomic skills created; re-run resumes.

Exit codes:
    0  — all 8 steps succeeded; anchor advanced
    1  — no .awf/project.json found
    2  — atomic skill exited 2 (credentials / CLI missing)
    3  — atomic skill exited 3 (remote / subprocess error)
    4  — missing prereq state (composer fail-fast, T2) or anchor save failure
    5  — gate hit (DNS propagation); wait and re-run
"""

import argparse
import json as jsonlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
# Script lives at: <AWF_HOME>/skills/awf-stage-mvp-play/scripts/stage_mvp_play.py
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from lib.project import ProjectNotFound  # noqa: E402
from lib.state import (  # noqa: E402
    Infra,
    ProjectAnchor,
    Shared,
    StateValidationError,
)


# ---------------------------------------------------------------------------
# Composer context
# ---------------------------------------------------------------------------


@dataclass
class ComposerCtx:
    """Mutable context threaded through all step functions."""

    anchor: ProjectAnchor
    root: Path

    # Mutable state — re-read from disk between steps.
    infra: Infra
    shared: Shared

    # CLI args
    play_hostname: str
    port: int
    node_version: int
    dry_run: bool
    as_json: bool

    # Accumulated per-step summaries for --json output.
    steps: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Subprocess invocation helper
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    """Result of a single atomic-skill invocation."""

    exit_code: int
    payload: dict[str, Any]  # parsed JSON from stdout, or {} on parse failure
    stderr: str


def _invoke(
    ctx: ComposerCtx,
    skill: str,
    args: list[str],
    *,
    step_name: str,
    safe_args: dict[str, Any] | None = None,
) -> StepResult:
    """Invoke one atomic skill via subprocess and return a StepResult.

    Always passes --json so stdout is parseable.
    Sets cwd=ctx.root so the atomic skill's project locator finds the anchor.

    Emits log.note before and after the invocation.
    """
    skill_script = AWF_HOME / "skills" / skill / "scripts" / f"{skill.replace('-', '_')}.py"

    cmd = ["uv", "run", str(skill_script)] + args + ["--json"]

    log.note(f"step={step_name} action=start skill={skill}", by="awf-stage-mvp-play")

    t_start = time.monotonic()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(ctx.root),
        env={**os.environ, "AWF_HOME": str(AWF_HOME)},
    )
    duration_ms = int((time.monotonic() - t_start) * 1000)

    # Log the subprocess invocation (non-secret args only).
    log.process(cmd=cmd, exit_code=proc.returncode, duration_ms=duration_ms, cwd=str(ctx.root))

    # Try to parse JSON from stdout.
    payload: dict[str, Any] = {}
    if proc.stdout.strip():
        try:
            payload = jsonlib.loads(proc.stdout.strip())
        except (jsonlib.JSONDecodeError, ValueError):
            pass

    action = payload.get("action", "unknown") if proc.returncode == 0 else "fail"
    log.note(
        f"step={step_name} action={action} skill={skill} exit={proc.returncode}",
        by="awf-stage-mvp-play",
    )

    return StepResult(
        exit_code=proc.returncode,
        payload=payload,
        stderr=proc.stderr,
    )


def _refresh_state(ctx: ComposerCtx) -> None:
    """Re-read Infra and Shared from disk into ctx."""
    ctx.infra = Infra.load_or_create(start=ctx.root)
    ctx.shared = Shared.load_or_create()


# ---------------------------------------------------------------------------
# Step 1 — awf-shared-infra-get
# ---------------------------------------------------------------------------


def step_shared_infra(ctx: ComposerCtx) -> StepResult:
    """Ensure play server + play Neon project exist in shared.json."""
    args: list[str] = ["--play-hostname", ctx.play_hostname]
    result = _invoke(ctx, "awf-shared-infra-get", args, step_name="shared_infra_get")
    _refresh_state(ctx)
    return result


# ---------------------------------------------------------------------------
# Step 2 — awf-app-dockerize
# ---------------------------------------------------------------------------


def step_dockerize(ctx: ComposerCtx) -> StepResult:
    """Scaffold Dockerfile, .dockerignore, healthcheck route, db lib."""
    args = ["--port", str(ctx.port), "--node-version", str(ctx.node_version)]
    result = _invoke(ctx, "awf-app-dockerize", args, step_name="dockerize")
    return result


# ---------------------------------------------------------------------------
# Step 3 — awf-neon-branch
# ---------------------------------------------------------------------------


def step_neon_branch(ctx: ComposerCtx) -> StepResult:
    """Branch on the shared Neon project (needed: Shared.play_neon_project_id set)."""
    if not ctx.shared.play_neon_project_id:
        msg = (
            "awf-stage-mvp-play: Shared.play_neon_project_id is empty; "
            "step 1 (awf-shared-infra-get) must succeed first"
        )
        print(msg, file=sys.stderr)
        log.error(msg=msg, hint="Re-run after step 1 completes successfully")
        return StepResult(exit_code=4, payload={}, stderr=msg)

    args = [
        "--name", ctx.anchor.slug,
        "--project-id", ctx.shared.play_neon_project_id,
    ]
    result = _invoke(ctx, "awf-neon-branch", args, step_name="neon_branch")
    _refresh_state(ctx)
    return result


# ---------------------------------------------------------------------------
# Step 4 — awf-app-secret-set (DATABASE_URL)
# ---------------------------------------------------------------------------


def step_app_secret_set(ctx: ComposerCtx, connection_string: str) -> StepResult:
    """Write DATABASE_URL into .kamal/secrets.

    The connection string is passed literally. It is never logged (per
    Decision §6 — only key name and source-step are safe to log).
    """
    if not connection_string:
        msg = (
            "awf-stage-mvp-play: no DATABASE_URL connection string available; "
            "step 3 (awf-neon-branch) must provide it"
        )
        print(msg, file=sys.stderr)
        log.error(msg=msg, hint="Re-run after step 3 completes successfully")
        return StepResult(exit_code=4, payload={}, stderr=msg)

    args = ["--key", "DATABASE_URL", "--value", connection_string]
    # safe_args must never include the secret value — only key + source
    result = _invoke(
        ctx,
        "awf-app-secret-set",
        args,
        step_name="secret_database_url",
        safe_args={"key": "DATABASE_URL", "source": "neon_branch"},
    )
    return result


# ---------------------------------------------------------------------------
# Step 5 — awf-kamal-config
# ---------------------------------------------------------------------------


def step_kamal_config(ctx: ComposerCtx) -> StepResult:
    """Render config/deploy.yml.

    Fail-fast (T2): if registry.host is empty the child would fail with an
    opaque error; we surface a clear exit-4 instead so the operator knows
    to run `awf-doctor --for-stage mvp-play`.
    """
    if not ctx.infra.registry.host:
        msg = (
            "awf-stage-mvp-play: Infra.registry.host is empty; "
            "run `awf-doctor --for-stage mvp-play` to configure registry credentials"
        )
        print(msg, file=sys.stderr)
        log.error(msg=msg, hint="Set GHCR / registry credentials then re-run")
        return StepResult(exit_code=4, payload={}, stderr=msg)

    args: list[str] = ["--path", "config/deploy.yml"]
    result = _invoke(ctx, "awf-kamal-config", args, step_name="kamal_config")
    _refresh_state(ctx)
    return result


# ---------------------------------------------------------------------------
# Step 6 — awf-cf-dns-record
# ---------------------------------------------------------------------------


def step_cf_dns_record(ctx: ComposerCtx) -> StepResult:
    """Create A record → play server IP (grey cloud, not proxied)."""
    server_ip = ctx.shared.play_server.ip if ctx.shared.play_server else ""
    if not server_ip:
        msg = (
            "awf-stage-mvp-play: Shared.play_server.ip is empty; "
            "step 1 (awf-shared-infra-get) must succeed first"
        )
        print(msg, file=sys.stderr)
        log.error(msg=msg, hint="Re-run after step 1 populates play_server")
        return StepResult(exit_code=4, payload={}, stderr=msg)

    args = [
        "--type", "A",
        "--name", ctx.anchor.domain,
        "--content", server_ip,
        "--no-proxied",
        "--domain", ctx.anchor.domain,
    ]
    result = _invoke(ctx, "awf-cf-dns-record", args, step_name="cf_dns_record")
    return result


# ---------------------------------------------------------------------------
# Step 7 — awf-kamal-setup  (DNS gate lives inside the atomic skill)
# ---------------------------------------------------------------------------


def step_kamal_setup(ctx: ComposerCtx) -> StepResult:
    """Run `kamal setup` after DNS propagation.

    If the atomic skill exits 3 with gate=dns_propagation in its JSON,
    the composer re-classifies to exit 5 and emits log.gate.
    """
    server_ip = ctx.shared.play_server.ip if ctx.shared.play_server else ""
    if not server_ip:
        msg = (
            "awf-stage-mvp-play: Shared.play_server.ip is empty; "
            "step 1 (awf-shared-infra-get) must succeed first"
        )
        print(msg, file=sys.stderr)
        log.error(msg=msg, hint="Re-run after step 1 populates play_server")
        return StepResult(exit_code=4, payload={}, stderr=msg)

    args = ["--server-ip", server_ip, "--domain", ctx.anchor.domain]
    result = _invoke(ctx, "awf-kamal-setup", args, step_name="kamal_setup")

    # DNS gate detection: exit 3 + "gate":"dns_propagation" in JSON payload
    if result.exit_code == 3 and result.payload.get("gate") == "dns_propagation":
        instructions = (
            f"DNS propagation timeout for {ctx.anchor.domain}. "
            "The A record has been created but not yet resolved by kamal. "
            "Wait 2–5 minutes and re-run `awf-stage-mvp-play`. "
            "The composer will resume from this step."
        )
        log.gate(
            name="dns_propagation",
            reason=result.payload.get("reason", "DNS A-record not yet resolving"),
            instructions=instructions,
        )
        print(
            f"\nawf-stage-mvp-play: DNS propagation gate hit for {ctx.anchor.domain}.\n"
            f"{instructions}\n",
            file=sys.stderr,
        )
        # Return a modified result with exit_code=5 to signal the caller.
        return StepResult(exit_code=5, payload=result.payload, stderr=result.stderr)

    return result


# ---------------------------------------------------------------------------
# Step 8 — awf-kamal-deploy
# ---------------------------------------------------------------------------


def step_kamal_deploy(ctx: ComposerCtx) -> StepResult:
    """Run `kamal deploy`."""
    result = _invoke(ctx, "awf-kamal-deploy", [], step_name="kamal_deploy")
    _refresh_state(ctx)
    return result


# ---------------------------------------------------------------------------
# Anchor advancement
# ---------------------------------------------------------------------------


def advance_anchor(ctx: ComposerCtx) -> int:
    """Advance anchor to stage=mvp-play, has.infra=True, has.kamal=True.

    Returns 0 on success, 4 on StateValidationError.
    """
    ctx.anchor.stage = "mvp-play"
    ctx.anchor.has.infra = True
    ctx.anchor.has.kamal = True
    try:
        ctx.anchor.save()
    except StateValidationError as exc:
        msg = f"awf-stage-mvp-play: anchor save failed: {exc}"
        print(msg, file=sys.stderr)
        log.error(msg=msg, hint="Inspect .awf/project.json for schema violations")
        return 4
    log.note("stage advanced to mvp-play", by="awf-stage-mvp-play")
    return 0


# ---------------------------------------------------------------------------
# --dry-run plan printer
# ---------------------------------------------------------------------------


def print_dry_run_plan(ctx: ComposerCtx) -> None:
    """Print the 8-step execution plan to stdout without running anything."""
    server_ip = ctx.shared.play_server.ip if ctx.shared.play_server else "<not yet known>"
    neon_project_id = ctx.shared.play_neon_project_id or "<not yet known>"

    steps = [
        {
            "step": 1,
            "skill": "awf-shared-infra-get",
            "args": f"--play-hostname {ctx.play_hostname} --json",
        },
        {
            "step": 2,
            "skill": "awf-app-dockerize",
            "args": f"--port {ctx.port} --node-version {ctx.node_version} --json",
        },
        {
            "step": 3,
            "skill": "awf-neon-branch",
            "args": f"--name {ctx.anchor.slug} --project-id {neon_project_id} --json",
        },
        {
            "step": 4,
            "skill": "awf-app-secret-set",
            "args": "--key DATABASE_URL --value <from step 3> --json",
        },
        {
            "step": 5,
            "skill": "awf-kamal-config",
            "args": "--path config/deploy.yml --json",
        },
        {
            "step": 6,
            "skill": "awf-cf-dns-record",
            "args": f"--type A --name {ctx.anchor.domain} --content {server_ip} --no-proxied --json",
        },
        {
            "step": 7,
            "skill": "awf-kamal-setup",
            "args": f"--server-ip {server_ip} --domain {ctx.anchor.domain} --json",
        },
        {
            "step": 8,
            "skill": "awf-kamal-deploy",
            "args": "--json",
        },
    ]

    print(f"awf-stage-mvp-play DRY RUN for project: {ctx.anchor.slug} ({ctx.anchor.domain})")
    print("target stage: mvp-play\n")
    for s in steps:
        log.intent(
            action=f"invoke {s['skill']} {s['args']}",
            impact=f"step {s['step']} of 8",
        )
        log.note(
            f"step={s['skill']} action=dry-run args={s['args']}",
            by="awf-stage-mvp-play",
        )
        print(f"  {s['step']}. {s['skill']}")
        print(f"     {s['args']}\n")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def _map_child_exit(exit_code: int, step_name: str) -> int:
    """Map a child exit code to the composer's exit code.

    Exit codes 0, 2, 3, 4, 5 pass through unchanged.
    Any other non-zero code maps to 3 (remote/subprocess error).
    """
    if exit_code in (0, 2, 3, 4, 5):
        return exit_code
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="awf-stage-mvp-play",
        description="Promote a project to stage=mvp-play by chaining 8 atomic skills.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Print plan; no subprocesses; exits 0.",
    )
    parser.add_argument(
        "--play-hostname", default="play.awfship.dev", dest="play_hostname",
        help="Hostname for shared Hetzner play server (default: play.awfship.dev).",
    )
    parser.add_argument(
        "--port", type=int, default=3000,
        help="Port for Dockerfile EXPOSE (default: 3000).",
    )
    parser.add_argument(
        "--node-version", type=int, default=20, dest="node_version",
        help="Node.js version for Dockerfile (default: 20).",
    )
    parser.add_argument(
        "--json", action="store_true", default=False,
        help="Emit machine-readable JSON summary on stdout.",
    )
    args = parser.parse_args()

    # Enable dry-run logging (so intent() events fire).
    if args.dry_run:
        log.set_dry_run(True)

    # ── Locate project root OUTSIDE session (plan_004 lesson M1) ──────────
    try:
        anchor = ProjectAnchor.load()
    except ProjectNotFound as exc:
        print(f"awf-stage-mvp-play: project not found: {exc}", file=sys.stderr)
        return 1

    root = anchor._path.parent.parent  # type: ignore[union-attr]

    # Load current state snapshots (before session opens).
    infra = Infra.load_or_create(start=root)
    shared = Shared.load_or_create()

    ctx = ComposerCtx(
        anchor=anchor,
        root=root,
        infra=infra,
        shared=shared,
        play_hostname=args.play_hostname,
        port=args.port,
        node_version=args.node_version,
        dry_run=args.dry_run,
        as_json=args.json,
    )

    # ── Dry-run short-circuits before opening a session ───────────────────
    if ctx.dry_run:
        with log.session(composer="awf-stage-mvp-play", target="mvp-play"):
            print_dry_run_plan(ctx)
        return 0

    # ── Main orchestration session ─────────────────────────────────────────
    final_exit = 0

    with log.session(composer="awf-stage-mvp-play", target="mvp-play"):
        log.note("starting step shared_infra_get", by="awf-stage-mvp-play")
        r1 = step_shared_infra(ctx)
        ctx.steps.append({"step": 1, "skill": "awf-shared-infra-get", "exit_code": r1.exit_code, "action": r1.payload.get("action", "fail")})
        if r1.exit_code != 0:
            log.error(msg=f"step shared_infra_get failed (exit {r1.exit_code})", hint=r1.stderr[:200])
            final_exit = _map_child_exit(r1.exit_code, "shared_infra_get")
            _emit_output(ctx, args.json, final_exit)
            return final_exit

        log.note("starting step dockerize", by="awf-stage-mvp-play")
        r2 = step_dockerize(ctx)
        ctx.steps.append({"step": 2, "skill": "awf-app-dockerize", "exit_code": r2.exit_code, "action": r2.payload.get("action", "fail")})
        if r2.exit_code != 0:
            log.error(msg=f"step dockerize failed (exit {r2.exit_code})", hint=r2.stderr[:200])
            final_exit = _map_child_exit(r2.exit_code, "dockerize")
            _emit_output(ctx, args.json, final_exit)
            return final_exit

        log.note("starting step neon_branch", by="awf-stage-mvp-play")
        r3 = step_neon_branch(ctx)
        ctx.steps.append({"step": 3, "skill": "awf-neon-branch", "exit_code": r3.exit_code, "action": r3.payload.get("action", "fail")})
        if r3.exit_code != 0:
            log.error(msg=f"step neon_branch failed (exit {r3.exit_code})", hint=r3.stderr[:200])
            final_exit = _map_child_exit(r3.exit_code, "neon_branch")
            _emit_output(ctx, args.json, final_exit)
            return final_exit

        # Extract connection string from step 3 payload.
        # awf-neon-branch emits connection_string in its JSON when available.
        connection_string: str = r3.payload.get("connection_string", "")

        log.note("starting step secret_database_url", by="awf-stage-mvp-play")
        r4 = step_app_secret_set(ctx, connection_string)
        ctx.steps.append({"step": 4, "skill": "awf-app-secret-set", "exit_code": r4.exit_code, "action": r4.payload.get("action", "fail")})
        if r4.exit_code != 0:
            log.error(msg=f"step secret_database_url failed (exit {r4.exit_code})", hint=r4.stderr[:200])
            final_exit = _map_child_exit(r4.exit_code, "secret_database_url")
            _emit_output(ctx, args.json, final_exit)
            return final_exit

        log.note("starting step kamal_config", by="awf-stage-mvp-play")
        r5 = step_kamal_config(ctx)
        ctx.steps.append({"step": 5, "skill": "awf-kamal-config", "exit_code": r5.exit_code, "action": r5.payload.get("action", "fail")})
        if r5.exit_code != 0:
            log.error(msg=f"step kamal_config failed (exit {r5.exit_code})", hint=r5.stderr[:200])
            final_exit = _map_child_exit(r5.exit_code, "kamal_config")
            _emit_output(ctx, args.json, final_exit)
            return final_exit

        log.note("starting step cf_dns_record", by="awf-stage-mvp-play")
        r6 = step_cf_dns_record(ctx)
        ctx.steps.append({"step": 6, "skill": "awf-cf-dns-record", "exit_code": r6.exit_code, "action": r6.payload.get("action", "fail")})
        if r6.exit_code != 0:
            log.error(msg=f"step cf_dns_record failed (exit {r6.exit_code})", hint=r6.stderr[:200])
            final_exit = _map_child_exit(r6.exit_code, "cf_dns_record")
            _emit_output(ctx, args.json, final_exit)
            return final_exit

        log.note("starting step kamal_setup", by="awf-stage-mvp-play")
        r7 = step_kamal_setup(ctx)
        ctx.steps.append({"step": 7, "skill": "awf-kamal-setup", "exit_code": r7.exit_code, "action": r7.payload.get("action", "fail")})
        if r7.exit_code == 5:
            # DNS gate — propagate as exit 5 without logging as an error
            # (the gate is already logged inside step_kamal_setup).
            final_exit = 5
            _emit_output(ctx, args.json, final_exit)
            return final_exit
        if r7.exit_code != 0:
            log.error(msg=f"step kamal_setup failed (exit {r7.exit_code})", hint=r7.stderr[:200])
            final_exit = _map_child_exit(r7.exit_code, "kamal_setup")
            _emit_output(ctx, args.json, final_exit)
            return final_exit

        log.note("starting step kamal_deploy", by="awf-stage-mvp-play")
        r8 = step_kamal_deploy(ctx)
        ctx.steps.append({"step": 8, "skill": "awf-kamal-deploy", "exit_code": r8.exit_code, "action": r8.payload.get("action", "fail")})
        if r8.exit_code != 0:
            log.error(msg=f"step kamal_deploy failed (exit {r8.exit_code})", hint=r8.stderr[:200])
            final_exit = _map_child_exit(r8.exit_code, "kamal_deploy")
            _emit_output(ctx, args.json, final_exit)
            return final_exit

        # ── All 8 steps succeeded — advance anchor ────────────────────────
        advance_result = advance_anchor(ctx)
        if advance_result != 0:
            final_exit = advance_result
        else:
            final_exit = 0

        _emit_output(ctx, args.json, final_exit)

    return final_exit


def _emit_output(ctx: ComposerCtx, as_json: bool, final_exit: int) -> None:
    """Emit human or JSON summary to stdout."""
    if not as_json:
        status = "success" if final_exit == 0 else f"failed (exit {final_exit})"
        print(f"awf-stage-mvp-play: {status}")
        for s in ctx.steps:
            print(f"  step {s['step']} {s['skill']}: {s['action']}")
        return

    anchored = final_exit == 0
    print(jsonlib.dumps({
        "skill": "awf-stage-mvp-play",
        "result": "ok" if final_exit == 0 else "fail",
        "exit_code": final_exit,
        "anchor_advanced": anchored,
        "steps": ctx.steps,
    }))


if __name__ == "__main__":
    sys.exit(main())
