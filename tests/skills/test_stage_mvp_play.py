"""Tests for awf-stage-mvp-play composer (plan_010).

Covers acceptance criteria:
  - Happy path: 8 steps invoked in order, anchor advanced.
  - Mid-run failure (step 5 exits 3): anchor NOT advanced, exits 3.
  - DNS gate (step 7 exits 3 with gate=dns_propagation): composer exits 5, log.gate emitted.
  - Re-run after partial: skipped steps recognized via action=skip.
  - --dry-run: prints plan, no subprocesses invoked, anchor untouched.
  - Missing prereq (step 5 exits 4 via fail-fast): composer exits 4.
  - No project (no .awf/project.json): exits 1 before session opens.
  - Integration path: real subprocess.run with fake atomic-skill scripts.

Mocking strategy:
  - Unit tests: monkeypatch subprocess.run to return CompletedProcess objects
    with canned JSON stdout (the subprocess.CompletedProcess mock factory
    takes a step_name→response map).
  - Integration tests: write tiny fake Python scripts into tmp_path that
    print {"action":"created"} and exit 0, then invoke main() for real.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Repo root and path bootstrap
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / "skills"
_LIB_DIR = _REPO_ROOT / "lib"

for _p in [str(_REPO_ROOT), str(_LIB_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Fixtures: minimal project state
# ---------------------------------------------------------------------------

_MINIMAL_ANCHOR: dict[str, Any] = {
    "awf_version": "0.1.0",
    "domain": "example.com",
    "slug": "example-com",
    "stage": "landing",
    "created": "2026-06-01T00:00:00Z",
    "has": {"passport": True, "infra": False, "kamal": False, "content": False},
}

_MINIMAL_INFRA: dict[str, Any] = {
    "registry": {"host": "ghcr.io", "image": "testuser/example-com", "user": "testuser"},
    "hetzner": {
        "servers": [
            {"id": "42", "ip": "1.2.3.4", "role": "web", "shared": False, "cost_eur_month": 4.5}
        ],
        "lb_id": None,
        "network_id": None,
    },
    "neon": {
        "project_id": "neon-proj-111",
        "branch_id": "br-abc-111",
        "branch_name": "example-com",
        "mode": "shared-branch",
        "connection_secret_ref": "DATABASE_URL",
    },
    "kamal": {"config_path": "config/deploy.yml", "last_deploy_image": None},
}

_MINIMAL_SHARED: dict[str, Any] = {
    "play_server": {
        "hetzner_id": "42",
        "ip": "1.2.3.4",
        "hostname": "play.awfship.dev",
        "registry": "ghcr.io",
        "created": "2026-06-01T00:00:00Z",
    },
    "play_neon_project_id": "neon-proj-111",
    "default_registry": {"host": "ghcr.io", "user": "testuser"},
}


def _make_project(
    root: Path,
    *,
    domain: str = "example.com",
    with_infra: bool = False,
    with_shared: bool = False,
    shared_dir: Path | None = None,
) -> None:
    """Write .awf/project.json (and optionally infra.json) into root."""
    slug = domain.replace(".", "-")
    awf_dir = root / ".awf"
    awf_dir.mkdir(exist_ok=True)
    anchor = dict(_MINIMAL_ANCHOR)
    anchor["domain"] = domain
    anchor["slug"] = slug
    (awf_dir / "project.json").write_text(json.dumps(anchor) + "\n", encoding="utf-8")

    if with_infra:
        infra = dict(_MINIMAL_INFRA)
        (awf_dir / "infra.json").write_text(json.dumps(infra, indent=2) + "\n", encoding="utf-8")

    if with_shared and shared_dir is not None:
        shared_dir.mkdir(parents=True, exist_ok=True)
        (shared_dir / "shared.json").write_text(
            json.dumps(_MINIMAL_SHARED, indent=2) + "\n", encoding="utf-8"
        )


def _read_anchor(root: Path) -> dict[str, Any]:
    path = root / ".awf" / "project.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _read_log_events(root: Path) -> list[dict[str, Any]]:
    log_path = root / ".awf" / "log.jsonl"
    if not log_path.exists():
        return []
    return [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _events_of_type(root: Path, type_: str) -> list[dict[str, Any]]:
    return [e for e in _read_log_events(root) if e.get("type") == type_]


# ---------------------------------------------------------------------------
# Module import helper
# ---------------------------------------------------------------------------


def _import_composer():
    """Import and return the stage_mvp_play module (not just main)."""
    script_path = _SKILLS_DIR / "awf-stage-mvp-play" / "scripts" / "stage_mvp_play.py"
    spec = importlib.util.spec_from_file_location("awf_stage_mvp_play", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    for p in [str(_REPO_ROOT), str(_LIB_DIR)]:
        if p not in sys.path:
            sys.path.insert(0, p)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _set_argv(monkeypatch, args: "list[str] | None" = None) -> None:
    """Set sys.argv so argparse doesn't pick up pytest args."""
    monkeypatch.setattr(sys, "argv", ["awf-stage-mvp-play"] + (args or []))


# ---------------------------------------------------------------------------
# Canned subprocess responses
# ---------------------------------------------------------------------------

def _make_proc(stdout: dict[str, Any] | None = None, returncode: int = 0) -> MagicMock:
    """Build a mock CompletedProcess."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = json.dumps(stdout or {"action": "created"})
    proc.stderr = ""
    return proc


def _build_happy_responses() -> list[MagicMock]:
    """Return 8 canned success responses for each step in order."""
    return [
        _make_proc({"action": "created", "play_server": {"ip": "1.2.3.4"}, "play_neon_project_id": "neon-proj-111"}),  # step 1
        _make_proc({"action": "created"}),  # step 2
        _make_proc({"action": "created", "branch_id": "br-abc", "branch_name": "example-com", "connection_string": "postgres://host/db?sslmode=require"}),  # step 3
        _make_proc({"action": "created"}),  # step 4
        _make_proc({"action": "created"}),  # step 5
        _make_proc({"action": "created"}),  # step 6
        _make_proc({"action": "created"}),  # step 7
        _make_proc({"action": "created"}),  # step 8
    ]


# ---------------------------------------------------------------------------
# autouse fixture: reset log dry_run flag
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_log_dry_run():
    """Reset log._dry_run_active and sys.argv after each test."""
    from lib import log
    log.set_dry_run(False)
    original_argv = sys.argv[:]
    sys.argv = ["awf-stage-mvp-play"]
    yield
    log.set_dry_run(False)
    sys.argv = original_argv


# ---------------------------------------------------------------------------
# Helper: patch subprocess.run with a sequence of responses
# ---------------------------------------------------------------------------

class _SubprocessSequence:
    """Returns canned responses in order; raises StopIteration if exhausted."""

    def __init__(self, responses: list[MagicMock]) -> None:
        self._iter = iter(responses)
        self.calls: list[Any] = []

    def __call__(self, cmd, **kwargs) -> MagicMock:
        self.calls.append(cmd)
        return next(self._iter)


# ---------------------------------------------------------------------------
# Shared.load_or_create patcher
# ---------------------------------------------------------------------------

def _patch_shared(monkeypatch, shared_data: dict[str, Any] | None = None) -> None:
    """Patch Shared.load_or_create to return a Shared object from dict."""
    from lib.state import Shared
    data = shared_data or _MINIMAL_SHARED
    shared_obj = Shared.model_validate(data)
    # Assign a dummy _path so save() won't error on None
    from pathlib import Path as P
    shared_obj._path = P("/tmp/fake_shared.json")
    monkeypatch.setattr(Shared, "load_or_create", staticmethod(lambda: shared_obj))


# ---------------------------------------------------------------------------
# Test class: happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    """Full 8-step happy path: all steps succeed, anchor advances."""

    def test_happy_path_all_steps_invoked(self, tmp_path, monkeypatch):
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        mod = _import_composer()
        responses = _build_happy_responses()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 0, f"Expected exit 0, got {rc}"
        assert len(seq.calls) == 8, f"Expected 8 subprocess calls, got {len(seq.calls)}"

    def test_happy_path_anchor_advanced(self, tmp_path, monkeypatch):
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        mod = _import_composer()
        seq = _SubprocessSequence(_build_happy_responses())

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 0
        anchor = _read_anchor(tmp_path)
        assert anchor["stage"] == "mvp-play"
        assert anchor["has"]["infra"] is True
        assert anchor["has"]["kamal"] is True

    def test_happy_path_order_is_correct(self, tmp_path, monkeypatch):
        """Verify skills are invoked in dependency order."""
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        mod = _import_composer()
        seq = _SubprocessSequence(_build_happy_responses())

        with patch("subprocess.run", side_effect=seq):
            mod.main()

        expected_skills = [
            "awf-shared-infra-get",
            "awf-app-dockerize",
            "awf-neon-branch",
            "awf-app-secret-set",
            "awf-kamal-config",
            "awf-cf-dns-record",
            "awf-kamal-setup",
            "awf-kamal-deploy",
        ]
        for i, expected in enumerate(expected_skills):
            # cmd[2] is the script path which contains the skill name
            assert expected.replace("-", "_") in seq.calls[i][2], (
                f"Step {i+1}: expected {expected} in {seq.calls[i][2]}"
            )

    def test_happy_path_json_output(self, tmp_path, monkeypatch, capsys):
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        monkeypatch.setattr(sys, "argv", ["awf-stage-mvp-play", "--json"])
        _patch_shared(monkeypatch)

        mod = _import_composer()
        seq = _SubprocessSequence(_build_happy_responses())

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert output["result"] == "ok"
        assert output["anchor_advanced"] is True
        assert len(output["steps"]) == 8

    def test_skip_actions_on_rerun(self, tmp_path, monkeypatch):
        """Re-run with action=skip from all skills: anchor still advanced."""
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        # Pre-advance anchor to simulate partial state (not yet mvp-play but infra exists)
        # The composer should still advance it after all skips.

        skip_responses = [
            _make_proc({"action": "skip", "play_server": {"ip": "1.2.3.4"}, "play_neon_project_id": "neon-proj-111"}),  # step 1
            _make_proc({"action": "skip"}),  # step 2
            _make_proc({"action": "skip", "branch_id": "br-abc", "branch_name": "example-com", "connection_string": "postgres://host/db?sslmode=require"}),  # step 3
            _make_proc({"action": "skip"}),  # step 4
            _make_proc({"action": "skip"}),  # step 5
            _make_proc({"action": "skip"}),  # step 6
            _make_proc({"action": "skip"}),  # step 7
            _make_proc({"action": "skip"}),  # step 8
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(skip_responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 0
        assert len(seq.calls) == 8, "All 8 steps invoked even for skip actions"
        anchor = _read_anchor(tmp_path)
        assert anchor["stage"] == "mvp-play"


# ---------------------------------------------------------------------------
# Test class: mid-run failure
# ---------------------------------------------------------------------------

class TestMidRunFailure:
    """Mid-run failure: anchor must NOT be advanced."""

    def test_step5_fails_exits_3(self, tmp_path, monkeypatch):
        """Step 5 (kamal_config) exits 3 → composer exits 3, anchor unchanged."""
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        responses = [
            _make_proc({"action": "created", "play_server": {"ip": "1.2.3.4"}, "play_neon_project_id": "neon-proj-111"}),  # step 1
            _make_proc({"action": "created"}),  # step 2
            _make_proc({"action": "created", "branch_id": "br-abc", "branch_name": "example-com", "connection_string": "postgres://host/db?sslmode=require"}),  # step 3
            _make_proc({"action": "created"}),  # step 4
            _make_proc({"action": "fail", "error": "kamal config error"}, returncode=3),  # step 5 fails
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 3
        # Only 5 subprocess calls (steps 1-5); steps 6-8 never invoked
        assert len(seq.calls) == 5, f"Expected 5 calls, got {len(seq.calls)}"
        # Anchor not advanced
        anchor = _read_anchor(tmp_path)
        assert anchor["stage"] == "landing", "Anchor must not be advanced on failure"
        assert anchor["has"]["infra"] is False
        assert anchor["has"]["kamal"] is False

    def test_step3_fails_no_subprocess_after(self, tmp_path, monkeypatch):
        """Step 3 fails → only 3 subprocess invocations, anchor untouched."""
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        responses = [
            _make_proc({"action": "created", "play_server": {"ip": "1.2.3.4"}, "play_neon_project_id": "neon-proj-111"}),  # step 1
            _make_proc({"action": "created"}),  # step 2
            _make_proc({"action": "fail"}, returncode=3),  # step 3 fails
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 3
        assert len(seq.calls) == 3
        anchor = _read_anchor(tmp_path)
        assert anchor["stage"] == "landing"

    def test_child_exit_2_propagates(self, tmp_path, monkeypatch):
        """Child exit 2 (creds missing) → composer exits 2."""
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        responses = [
            _make_proc(returncode=2),  # step 1 exits 2 (creds)
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 2

    def test_child_exit_nonstandard_maps_to_3(self, tmp_path, monkeypatch):
        """Child exits with unexpected code → composer exits 3."""
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        responses = [
            _make_proc(returncode=99),  # step 1 exits 99
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 3


# ---------------------------------------------------------------------------
# Test class: DNS gate (exit 5)
# ---------------------------------------------------------------------------

class TestDnsGate:
    """DNS propagation gate: step 7 exits 3 with gate=dns_propagation → exit 5."""

    def test_dns_gate_exits_5(self, tmp_path, monkeypatch):
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        responses = [
            _make_proc({"action": "created", "play_server": {"ip": "1.2.3.4"}, "play_neon_project_id": "neon-proj-111"}),  # step 1
            _make_proc({"action": "created"}),  # step 2
            _make_proc({"action": "created", "branch_id": "br-abc", "connection_string": "postgres://host/db"}),  # step 3
            _make_proc({"action": "created"}),  # step 4
            _make_proc({"action": "created"}),  # step 5
            _make_proc({"action": "created"}),  # step 6
            _make_proc(  # step 7 — DNS gate
                {"skill": "awf-kamal-setup", "action": "gate", "gate": "dns_propagation",
                 "result": "fail", "reason": "DNS A-record not resolving after 600s",
                 "domain": "example.com", "expected_ip": "1.2.3.4"},
                returncode=3,
            ),
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 5, f"Expected exit 5 for DNS gate, got {rc}"

    def test_dns_gate_emits_log_gate(self, tmp_path, monkeypatch):
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        responses = [
            _make_proc({"action": "created", "play_server": {"ip": "1.2.3.4"}, "play_neon_project_id": "neon-proj-111"}),
            _make_proc({"action": "created"}),
            _make_proc({"action": "created", "branch_id": "br-abc", "connection_string": "postgres://host/db"}),
            _make_proc({"action": "created"}),
            _make_proc({"action": "created"}),
            _make_proc({"action": "created"}),
            _make_proc(
                {"gate": "dns_propagation", "reason": "timeout", "domain": "example.com", "expected_ip": "1.2.3.4"},
                returncode=3,
            ),
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 5
        # gate.hit event should appear in the log
        gate_events = _events_of_type(tmp_path, "gate.hit")
        assert len(gate_events) >= 1, "Expected at least one gate.hit event"
        assert gate_events[0]["data"]["gate_name"] == "dns_propagation"

    def test_dns_gate_anchor_not_advanced(self, tmp_path, monkeypatch):
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        responses = [
            _make_proc({"action": "created", "play_server": {"ip": "1.2.3.4"}, "play_neon_project_id": "neon-proj-111"}),
            _make_proc({"action": "created"}),
            _make_proc({"action": "created", "branch_id": "br-abc", "connection_string": "postgres://host/db"}),
            _make_proc({"action": "created"}),
            _make_proc({"action": "created"}),
            _make_proc({"action": "created"}),
            _make_proc({"gate": "dns_propagation"}, returncode=3),
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 5
        anchor = _read_anchor(tmp_path)
        assert anchor["stage"] == "landing", "Anchor must not advance on DNS gate"

    def test_step7_exit3_without_gate_is_propagated_as_3(self, tmp_path, monkeypatch):
        """Step 7 exit 3 without gate payload → regular error, exits 3 (not 5)."""
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        responses = [
            _make_proc({"action": "created", "play_server": {"ip": "1.2.3.4"}, "play_neon_project_id": "neon-proj-111"}),
            _make_proc({"action": "created"}),
            _make_proc({"action": "created", "branch_id": "br-abc", "connection_string": "postgres://host/db"}),
            _make_proc({"action": "created"}),
            _make_proc({"action": "created"}),
            _make_proc({"action": "created"}),
            _make_proc({"action": "fail", "error": "kamal setup failed"}, returncode=3),  # step 7 generic error
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 3


# ---------------------------------------------------------------------------
# Test class: --dry-run
# ---------------------------------------------------------------------------

class TestDryRun:
    """--dry-run: prints plan, no subprocesses, anchor untouched."""

    def test_dry_run_no_subprocess(self, tmp_path, monkeypatch):
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        monkeypatch.setattr(sys, "argv", ["awf-stage-mvp-play", "--dry-run"])
        _patch_shared(monkeypatch)

        mod = _import_composer()
        call_count = 0

        def fake_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_proc()

        with patch("subprocess.run", side_effect=fake_run):
            rc = mod.main()

        assert rc == 0
        assert call_count == 0, f"--dry-run must not invoke any subprocess, got {call_count}"

    def test_dry_run_anchor_unchanged(self, tmp_path, monkeypatch):
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        monkeypatch.setattr(sys, "argv", ["awf-stage-mvp-play", "--dry-run"])
        _patch_shared(monkeypatch)

        mod = _import_composer()

        with patch("subprocess.run", side_effect=lambda *a, **k: _make_proc()):
            rc = mod.main()

        assert rc == 0
        anchor = _read_anchor(tmp_path)
        assert anchor["stage"] == "landing", "Anchor must not be advanced in dry-run"

    def test_dry_run_prints_steps(self, tmp_path, monkeypatch, capsys):
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        monkeypatch.setattr(sys, "argv", ["awf-stage-mvp-play", "--dry-run"])
        _patch_shared(monkeypatch)

        mod = _import_composer()

        with patch("subprocess.run", side_effect=lambda *a, **k: _make_proc()):
            mod.main()

        captured = capsys.readouterr()
        out = captured.out
        # Plan output should list all 8 skills
        for skill in [
            "awf-shared-infra-get",
            "awf-app-dockerize",
            "awf-neon-branch",
            "awf-app-secret-set",
            "awf-kamal-config",
            "awf-cf-dns-record",
            "awf-kamal-setup",
            "awf-kamal-deploy",
        ]:
            assert skill in out, f"Expected {skill} in dry-run output"


# ---------------------------------------------------------------------------
# Test class: missing prereq (exit 4)
# ---------------------------------------------------------------------------

class TestMissingPrereq:
    """Composer fail-fast on missing prerequisite state → exit 4."""

    def test_missing_shared_neon_project_id_exits_4(self, tmp_path, monkeypatch):
        """Step 3 requires Shared.play_neon_project_id; if missing, exits 4."""
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))

        # Patch Shared to return empty play_neon_project_id
        from lib.state import Shared
        empty_shared = Shared.model_validate(
            {"play_server": None, "play_neon_project_id": None, "default_registry": {"host": "ghcr.io", "user": ""}}
        )
        empty_shared._path = Path("/tmp/fake_shared.json")
        monkeypatch.setattr(Shared, "load_or_create", staticmethod(lambda: empty_shared))

        responses = [
            # step 1 succeeds but does NOT populate shared (we control shared via mock)
            _make_proc({"action": "created", "play_neon_project_id": ""}),
            # step 2 also succeeds (dockerize)
            _make_proc({"action": "created"}),
            # step 3 is not invoked via subprocess — fails fast in composer before _invoke
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 4

    def test_missing_registry_host_exits_4(self, tmp_path, monkeypatch):
        """Step 5 requires infra.registry.host; if missing, exits 4 (T2 fail-fast)."""
        _make_project(tmp_path, with_infra=False)
        # Write infra without registry.host
        infra_no_registry = dict(_MINIMAL_INFRA)
        infra_no_registry["registry"] = {"host": "", "image": "", "user": ""}
        awf_dir = tmp_path / ".awf"
        awf_dir.mkdir(exist_ok=True)
        (awf_dir / "infra.json").write_text(json.dumps(infra_no_registry, indent=2), encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        _patch_shared(monkeypatch)

        responses = [
            _make_proc({"action": "created", "play_server": {"ip": "1.2.3.4"}, "play_neon_project_id": "neon-proj-111"}),  # step 1
            _make_proc({"action": "created"}),  # step 2
            _make_proc({"action": "created", "branch_id": "br-abc", "connection_string": "postgres://host/db"}),  # step 3
            _make_proc({"action": "created"}),  # step 4
            # step 5 never reached — composer fails fast
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 4, f"Expected exit 4 (missing registry.host), got {rc}"
        # Only steps 1-4 invoked
        assert len(seq.calls) == 4


# ---------------------------------------------------------------------------
# Test class: no project
# ---------------------------------------------------------------------------

class TestNoProject:
    """No .awf/project.json → exit 1 before session opens."""

    def test_no_project_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))

        mod = _import_composer()
        call_count = 0

        def fake_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_proc()

        with patch("subprocess.run", side_effect=fake_run):
            rc = mod.main()

        assert rc == 1
        assert call_count == 0, "No subprocesses should be called when project not found"

    def test_no_project_no_session_in_log(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))

        mod = _import_composer()

        with patch("subprocess.run", side_effect=lambda *a, **k: _make_proc()):
            mod.main()

        # No project dir means log goes to orphan log, not project log
        # Verify no .awf/log.jsonl created at tmp_path
        project_log = tmp_path / ".awf" / "log.jsonl"
        assert not project_log.exists(), "No project log should be created when project not found"


# ---------------------------------------------------------------------------
# Test class: integration (real subprocess with fake scripts)
# ---------------------------------------------------------------------------

class TestIntegration:
    """Integration tests: real subprocess.run pointing at fake atomic scripts."""

    def _write_fake_skill(self, skills_dir: Path, skill_name: str, response: dict[str, Any]) -> None:
        """Write a tiny Python fake script that prints JSON and exits 0."""
        script_dir = skills_dir / skill_name / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        module_name = skill_name.replace("-", "_")
        script = textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json, sys
            # Strip --json from args (always passed by composer)
            print(json.dumps({json.dumps(response)}))
            sys.exit(0)
        """)
        (script_dir / f"{module_name}.py").write_text(script, encoding="utf-8")

    def _write_fake_skill_fail(self, skills_dir: Path, skill_name: str, exit_code: int, payload: dict) -> None:
        """Write a fake script that prints JSON and exits with given code."""
        script_dir = skills_dir / skill_name / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        module_name = skill_name.replace("-", "_")
        script = textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json, sys
            print(json.dumps({json.dumps(payload)}))
            sys.exit({exit_code})
        """)
        (script_dir / f"{module_name}.py").write_text(script, encoding="utf-8")

    def test_integration_happy_path(self, tmp_path, monkeypatch):
        """End-to-end: fake scripts on disk, real subprocess.run path."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        fake_skills_dir = tmp_path / "fake_awf" / "skills"

        _make_project(project_dir, with_infra=True)

        # Write fake shared.json
        shared_dir = tmp_path / ".config" / "awf"
        shared_dir.mkdir(parents=True)
        (shared_dir / "shared.json").write_text(json.dumps(_MINIMAL_SHARED), encoding="utf-8")

        # Write fake atomic skill scripts
        skill_responses = {
            "awf-shared-infra-get": {"action": "created", "play_server": {"ip": "1.2.3.4"}, "play_neon_project_id": "neon-proj-111"},
            "awf-app-dockerize": {"action": "created"},
            "awf-neon-branch": {"action": "created", "branch_id": "br-111", "branch_name": "myproject", "connection_string": "postgres://host/db?sslmode=require"},
            "awf-app-secret-set": {"action": "created"},
            "awf-kamal-config": {"action": "created"},
            "awf-cf-dns-record": {"action": "created"},
            "awf-kamal-setup": {"action": "created"},
            "awf-kamal-deploy": {"action": "created"},
        }
        for skill_name, response in skill_responses.items():
            self._write_fake_skill(fake_skills_dir, skill_name, response)

        monkeypatch.chdir(project_dir)
        # Point AWF_HOME to our fake skills dir parent
        fake_awf_home = tmp_path / "fake_awf"
        monkeypatch.setenv("AWF_HOME", str(fake_awf_home))

        # Patch Shared.load_or_create to use our test shared.json
        from lib.state import Shared
        shared_obj = Shared.model_validate(_MINIMAL_SHARED)
        shared_obj._path = shared_dir / "shared.json"
        monkeypatch.setattr(Shared, "load_or_create", staticmethod(lambda: shared_obj))

        # Import the composer with our fake AWF_HOME
        script_path = _SKILLS_DIR / "awf-stage-mvp-play" / "scripts" / "stage_mvp_play.py"
        spec = importlib.util.spec_from_file_location("awf_stage_mvp_play_integ", script_path)
        module = importlib.util.module_from_spec(spec)
        for p in [str(_REPO_ROOT), str(_LIB_DIR)]:
            if p not in sys.path:
                sys.path.insert(0, p)

        # Override AWF_HOME in the module after exec
        old_env = os.environ.get("AWF_HOME")
        os.environ["AWF_HOME"] = str(fake_awf_home)
        try:
            spec.loader.exec_module(module)
            rc = module.main()
        finally:
            if old_env is not None:
                os.environ["AWF_HOME"] = old_env
            else:
                os.environ.pop("AWF_HOME", None)

        assert rc == 0
        anchor = _read_anchor(project_dir)
        assert anchor["stage"] == "mvp-play"

    def test_integration_nonzero_exit(self, tmp_path, monkeypatch):
        """Integration: step 1 script exits non-zero → composer propagates."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        fake_skills_dir = tmp_path / "fake_awf" / "skills"
        fake_awf_home = tmp_path / "fake_awf"

        _make_project(project_dir, with_infra=True)

        # step 1 fails with exit 3
        self._write_fake_skill_fail(
            fake_skills_dir, "awf-shared-infra-get",
            exit_code=3,
            payload={"action": "fail", "error": "API error"},
        )

        monkeypatch.chdir(project_dir)
        monkeypatch.setenv("AWF_HOME", str(fake_awf_home))

        from lib.state import Shared
        empty_shared = Shared.model_validate(
            {"play_server": None, "play_neon_project_id": None, "default_registry": {"host": "ghcr.io", "user": ""}}
        )
        empty_shared._path = Path("/tmp/fake_shared.json")
        monkeypatch.setattr(Shared, "load_or_create", staticmethod(lambda: empty_shared))

        script_path = _SKILLS_DIR / "awf-stage-mvp-play" / "scripts" / "stage_mvp_play.py"
        spec = importlib.util.spec_from_file_location("awf_stage_mvp_play_integ2", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rc = module.main()

        assert rc == 3
        anchor = _read_anchor(project_dir)
        assert anchor["stage"] == "landing"


# ---------------------------------------------------------------------------
# Test class: --json output shape
# ---------------------------------------------------------------------------

class TestJsonOutput:
    """Verify --json output shape on success and failure."""

    def test_json_success_shape(self, tmp_path, monkeypatch, capsys):
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        monkeypatch.setattr(sys, "argv", ["awf-stage-mvp-play", "--json"])
        _patch_shared(monkeypatch)

        mod = _import_composer()
        seq = _SubprocessSequence(_build_happy_responses())

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out["result"] == "ok"
        assert out["exit_code"] == 0
        assert out["anchor_advanced"] is True
        assert len(out["steps"]) == 8
        for step in out["steps"]:
            assert "step" in step
            assert "skill" in step
            assert "exit_code" in step
            assert "action" in step

    def test_json_failure_shape(self, tmp_path, monkeypatch, capsys):
        _make_project(tmp_path, with_infra=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
        monkeypatch.setattr(sys, "argv", ["awf-stage-mvp-play", "--json"])
        _patch_shared(monkeypatch)

        responses = [
            _make_proc(returncode=3),  # step 1 fails immediately
        ]

        mod = _import_composer()
        seq = _SubprocessSequence(responses)

        with patch("subprocess.run", side_effect=seq):
            rc = mod.main()

        assert rc == 3
        out = json.loads(capsys.readouterr().out.strip())
        assert out["result"] == "fail"
        assert out["exit_code"] == 3
        assert out["anchor_advanced"] is False
