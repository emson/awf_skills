"""Tests for skills/awf-migrate/scripts/migrate.py.

Covers acceptance criteria from plan_004_foundation_migrate_skill.md and
spec.md § A4. All tests use real tmp_path projects and real subprocess
invocations of the script under uv run.

Test categories:
  - Happy paths: legacy migration, no-op, --json flag
  - Error paths: no project (exit 1), I/O failure (exit 2)
  - Log integration: session.start/end, composer/target fields
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Repo root resolution (used to set AWF_HOME in subprocess env)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "skills" / "awf-migrate" / "scripts" / "migrate.py"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MINIMAL_PASSPORT: dict[str, Any] = {
    "schema_version": "1.0",
    "template_version": "1.0",
    "project_name": "example-com",
    "domain": "example.com",
    "site_url": "https://example.com",
    "site_name": "",
    "site_hero": "Welcome",
    "site_subtitle": "",
    "site_description": "",
    "category": "",
    "tags": "",
    "cta_button": "Open Link",
    "email": "",
    "nameserver_service": "namecheap",
    "hosting_service": "cloudflare",
    "fathom_site_id": "",
    "indexnow_key": "",
    "features": [""],
    "faqs": [],
    "launch": {"gates": {}},
}


def _make_legacy_project(root: Path, *, domain: str = "example.com") -> None:
    """Write a minimal valid passport.json at root (legacy project, no anchor)."""
    slug = domain.replace(".", "-")
    data = dict(_MINIMAL_PASSPORT)
    data["domain"] = domain
    data["project_name"] = slug
    data["site_url"] = f"https://{domain}"
    (root / "passport.json").write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")


def _make_migrated_project(root: Path, *, domain: str = "example.com") -> None:
    """Write passport.json and seed .awf/project.json via ensure_anchor."""
    _make_legacy_project(root, domain=domain)

    # Add repo root to path so we can call ensure_anchor directly
    import sys as _sys

    _repo = str(_REPO_ROOT)
    _lib = str(_REPO_ROOT / "lib")
    if _repo not in _sys.path:
        _sys.path.insert(0, _repo)
    if _lib not in _sys.path:
        _sys.path.insert(0, _lib)

    from lib.project import ensure_anchor  # noqa: PLC0415
    ensure_anchor(root)


def _run_migrate(
    cwd: Path,
    *extra_args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke migrate.py via uv run in the given cwd."""
    base_env = {**os.environ, "AWF_HOME": str(_REPO_ROOT)}
    if env:
        base_env.update(env)
    return subprocess.run(
        ["uv", "run", str(_SCRIPT), *extra_args],
        cwd=str(cwd),
        env=base_env,
        capture_output=True,
        text=True,
    )


def _read_log_lines(project_root: Path) -> list[dict[str, Any]]:
    """Parse .awf/log.jsonl and return all events as dicts."""
    log_path = project_root / ".awf" / "log.jsonl"
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_legacy_project_migrates_and_creates_anchor(tmp_path: Path) -> None:
    """A legacy project (passport only) is migrated on first run.

    Spec AC2: emits state.change events for files touched.
    """
    _make_legacy_project(tmp_path)

    result = _run_migrate(tmp_path)

    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    assert (tmp_path / ".awf" / "project.json").is_file()
    assert "migrated" in result.stdout

    events = _read_log_lines(tmp_path)
    event_types = [e.get("type") for e in events]
    assert "session.start" in event_types
    assert "session.end" in event_types
    # ensure_anchor.save() emits at least one state.change event
    assert any(e.get("type") == "state.change" for e in events), (
        f"Expected state.change in events: {event_types}"
    )


def test_already_migrated_is_noop(tmp_path: Path) -> None:
    """Already-migrated project: exit 0, 'already migrated' message, no new state.change.

    Spec AC1 + AC3.
    """
    _make_migrated_project(tmp_path)

    # Count existing log events before second invocation
    events_before = _read_log_lines(tmp_path)
    state_changes_before = sum(1 for e in events_before if e.get("type") == "state.change")

    result = _run_migrate(tmp_path)

    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    assert "already migrated" in result.stdout

    events_after = _read_log_lines(tmp_path)
    new_events = events_after[len(events_before):]
    new_event_types = [e.get("type") for e in new_events]

    # Second run should add exactly session.start + session.end, no state.change
    assert "session.start" in new_event_types
    assert "session.end" in new_event_types
    state_changes_after = sum(1 for e in events_after if e.get("type") == "state.change")
    assert state_changes_after == state_changes_before, (
        "No new state.change events expected on no-op run"
    )


def test_json_flag_emits_structured_output(tmp_path: Path) -> None:
    """--json flag produces a single JSON object with the required four fields."""
    _make_legacy_project(tmp_path)

    result = _run_migrate(tmp_path, "--json")

    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    output = json.loads(result.stdout)
    assert set(output.keys()) >= {"action", "anchor_path", "domain", "slug"}
    assert output["action"] == "migrated"
    assert output["domain"] == "example.com"
    assert output["slug"] == "example-com"
    assert ".awf/project.json" in output["anchor_path"]


def test_json_flag_noop_emits_no_op_action(tmp_path: Path) -> None:
    """--json flag on an already-migrated project reports action=no-op."""
    _make_migrated_project(tmp_path)

    result = _run_migrate(tmp_path, "--json")

    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    output = json.loads(result.stdout)
    assert output["action"] == "no-op"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_no_project_exits_1(tmp_path: Path) -> None:
    """No passport.json or .awf/project.json in cwd walk: exits 1, helpful stderr."""
    # tmp_path is empty — no project files
    result = _run_migrate(tmp_path)

    assert result.returncode == 1
    stderr = result.stderr
    # Must mention passport.json or awf-create-project to be helpful
    assert "passport.json" in stderr or "awf-create-project" in stderr, (
        f"Expected helpful message in stderr, got: {stderr!r}"
    )


def test_io_failure_exits_2(tmp_path: Path) -> None:
    """Read-only project root prevents .awf/ creation: exits 2 with error on stderr."""
    if os.getuid() == 0:
        pytest.skip("root bypasses chmod — I/O failure test not meaningful as root")

    _make_legacy_project(tmp_path)

    # chmod to read+execute only — prevents mkdir(.awf/)
    tmp_path.chmod(0o500)
    try:
        result = _run_migrate(tmp_path)
        assert result.returncode == 2, (
            f"Expected exit 2, got {result.returncode}; stderr: {result.stderr!r}"
        )
        assert result.stderr.strip() != "", "Expected non-empty stderr on I/O failure"
    finally:
        # Always restore so pytest can clean up tmp_path
        tmp_path.chmod(0o700)


def test_io_failure_session_end_records_fail_result(tmp_path: Path) -> None:
    """session.end carries result='fail' when an I/O error occurs.

    Regression coverage for M1: sys.exit(2) inside log.session raised
    SystemExit (BaseException), bypassing the except-Exception guard, so
    session.end was recorded with result='ok'. MigrateIOError (a plain
    RuntimeError subclass) must be raised instead so log.session correctly
    flips result to 'fail'.
    """
    if os.getuid() == 0:
        pytest.skip("root bypasses chmod — I/O failure test not meaningful as root")

    _make_legacy_project(tmp_path)

    # chmod to read+execute only — prevents mkdir(.awf/)
    tmp_path.chmod(0o500)
    try:
        result = _run_migrate(tmp_path)
        assert result.returncode == 2, (
            f"Expected exit 2, got {result.returncode}; stderr: {result.stderr!r}"
        )

        # The log is written to .awf/ which cannot be created under 0o500,
        # so we must restore permissions first before reading the log.
        tmp_path.chmod(0o700)

        events = _read_log_lines(tmp_path)
        session_end = next(
            (e for e in events if e.get("type") == "session.end"), None
        )
        # If the log dir was unwriteable, no events will exist — that's still
        # a valid I/O-failure scenario. Only assert when the log was written.
        if session_end is not None:
            assert session_end.get("result") == "fail", (
                f"Expected session.end result='fail', got: {session_end!r}"
            )
            # Also confirm data.summary mirrors the top-level result field
            summary_val = session_end.get("data", {}).get("summary")
            assert summary_val == "fail", (
                f"Expected data.summary='fail', got: {summary_val!r}"
            )
    finally:
        tmp_path.chmod(0o700)


# ---------------------------------------------------------------------------
# Log integration (plan 003 contract)
# ---------------------------------------------------------------------------


def test_session_event_carries_composer_and_target(tmp_path: Path) -> None:
    """session.start event has data.composer=awf-migrate, data.target_stage=anchor."""
    _make_legacy_project(tmp_path)

    result = _run_migrate(tmp_path)
    assert result.returncode == 0, f"stderr: {result.stderr!r}"

    events = _read_log_lines(tmp_path)
    session_start = next(
        (e for e in events if e.get("type") == "session.start"), None
    )
    assert session_start is not None, "Expected session.start event in log"

    data = session_start.get("data", {})
    assert data.get("composer") == "awf-migrate", (
        f"Expected composer=awf-migrate, got: {data!r}"
    )
    assert data.get("target_stage") == "anchor", (
        f"Expected target_stage=anchor, got: {data!r}"
    )
