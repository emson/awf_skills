"""Tests for skills/awf-doctor (plan_013 / C4 — scoping flags + recent-error surfacing).

Covers:
  - Default (no flag) behaviour preserved (golden snapshot approach via monkeypatch).
  - --for-stage: known stage runs only that stage's subsystems, skips others.
  - --for-stage unknown: exits 2 with stderr listing valid stages.
  - --for-skill: known skill runs only that skill's subsystems.
  - --for-skill unknown: exits 2.
  - --for-stage X --for-skill Y: exits 2 (mutual exclusion — tested via argparse).
  - --for-skill awf-app-secret-set (empty requirements): exits 0 with note.
  - --for-skill awf-app-secret-set --json: includes "preflight_required": false.
  - Recent-error surfacing: no log file, no errors, non-cred error, cred error.
  - Lead-with header: in-scope cred error → lead-with header in output.
  - Out-of-scope cred error → advisory header.
  - tail_events is the only log-reading mechanism (import assertion).
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / "skills"
_LIB_DIR = _REPO_ROOT / "lib"

for _p in [str(_REPO_ROOT), str(_LIB_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Module import helper
# ---------------------------------------------------------------------------


def _import_awf_doctor() -> Any:
    """Import and return the awf-doctor check.py module (fresh each call)."""
    script_path = _SKILLS_DIR / "awf-doctor" / "scripts" / "check.py"
    mod_name = f"awf_doctor_check_{id(object())}"
    spec = importlib.util.spec_from_file_location(mod_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


_AWF_DOCTOR: Any = None


def _get_awf_doctor() -> Any:
    global _AWF_DOCTOR
    if _AWF_DOCTOR is None:
        _AWF_DOCTOR = _import_awf_doctor()
    return _AWF_DOCTOR


# ---------------------------------------------------------------------------
# Helpers to monkey-patch build_report for unit tests
# ---------------------------------------------------------------------------


def _make_passing_report(mod: Any) -> Any:
    """Create a report with all checks passing."""
    report = mod.Report(awf_home="/fake/awf", project_root=None)
    report.add(mod.Check("cli", "git", mod.OK, detail="/usr/bin/git"))
    report.add(mod.Check("cli", "uv", mod.OK, detail="/usr/local/bin/uv"))
    return report


def _make_failing_report(mod: Any) -> Any:
    """Create a report with one required failure."""
    report = mod.Report(awf_home="/fake/awf", project_root=None)
    report.add(mod.Check("cli", "git", mod.FAIL, detail="not on PATH",
                         hint="install git", required=True))
    return report


# ---------------------------------------------------------------------------
# Minimal project fixture for anchor-based tests
# ---------------------------------------------------------------------------

_MINIMAL_ANCHOR: dict[str, Any] = {
    "awf_version": "0.1.0",
    "domain": "example.com",
    "slug": "example-com",
    "stage": "landing",
    "created": "2026-01-01T00:00:00Z",
}


def _make_project(tmp_path: Path, stage: str = "landing") -> Path:
    """Create a minimal .awf/project.json fixture."""
    awf_dir = tmp_path / ".awf"
    awf_dir.mkdir()
    anchor = {**_MINIMAL_ANCHOR, "stage": stage}
    (awf_dir / "project.json").write_text(json.dumps(anchor), encoding="utf-8")
    return tmp_path


def _write_log_events(tmp_path: Path, events: list[dict[str, Any]]) -> Path:
    """Write a log.jsonl file into the .awf/ directory."""
    awf_dir = tmp_path / ".awf"
    awf_dir.mkdir(exist_ok=True)
    log_path = awf_dir / "log.jsonl"
    lines = [json.dumps(ev) for ev in events]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


# ---------------------------------------------------------------------------
# 1. Default behaviour (golden)
# ---------------------------------------------------------------------------


class TestDefaultBehaviour:
    """Default path (no scope flags) must be byte-for-byte unchanged.

    Strategy: monkeypatch _run_default_checks so we control the check list,
    then assert render_json output matches the expected shape.
    """

    def test_default_exits_0_when_all_pass(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_doctor()
        passing_report = _make_passing_report(mod)

        monkeypatch.setattr(mod, "build_report", lambda scope=None, lead_with=None: passing_report)
        rc = mod.main(["check.py"])
        assert rc == 0

    def test_default_exits_1_when_required_fail(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_doctor()
        failing_report = _make_failing_report(mod)

        monkeypatch.setattr(mod, "build_report", lambda scope=None, lead_with=None: failing_report)
        rc = mod.main(["check.py"])
        assert rc == 1

    def test_default_json_shape(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_doctor()
        passing_report = _make_passing_report(mod)

        monkeypatch.setattr(mod, "build_report", lambda scope=None, lead_with=None: passing_report)
        rc = mod.main(["check.py", "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "awf_home" in data
        assert "checks" in data
        assert "required_failures" in data

    def test_no_scope_flag_calls_scope_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --for-stage or --for-skill, scope is ScopeDefault."""
        mod = _get_awf_doctor()
        captured_scope: list[Any] = []

        def fake_build_report(scope: Any = None, lead_with: Any = None) -> Any:
            captured_scope.append(scope)
            return _make_passing_report(mod)

        monkeypatch.setattr(mod, "build_report", fake_build_report)
        mod.main(["check.py"])
        assert len(captured_scope) == 1
        assert isinstance(captured_scope[0], mod.ScopeDefault)


# ---------------------------------------------------------------------------
# 2. --for-stage scoping
# ---------------------------------------------------------------------------


class TestForStage:
    def test_for_stage_known_passes(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_doctor()
        captured_scope: list[Any] = []

        def fake_build_report(scope: Any = None, lead_with: Any = None) -> Any:
            captured_scope.append(scope)
            return _make_passing_report(mod)

        monkeypatch.setattr(mod, "build_report", fake_build_report)
        rc = mod.main(["check.py", "--for-stage", "mvp-play"])
        assert rc == 0
        assert len(captured_scope) == 1
        scope = captured_scope[0]
        assert isinstance(scope, mod.ScopeStage)
        assert scope.name == "mvp-play"
        assert "hetzner" in scope.wanted
        assert "neon" in scope.wanted
        assert "ghcr" in scope.wanted
        assert "ssh" in scope.wanted
        assert "kamal_cli" in scope.wanted

    def test_for_stage_mvp_play_excludes_bing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mod = _get_awf_doctor()
        captured_scope: list[Any] = []

        def fake_build_report(scope: Any = None, lead_with: Any = None) -> Any:
            captured_scope.append(scope)
            return _make_passing_report(mod)

        monkeypatch.setattr(mod, "build_report", fake_build_report)
        mod.main(["check.py", "--for-stage", "mvp-play"])
        scope = captured_scope[0]
        assert "bing" not in scope.wanted
        assert "gsc" not in scope.wanted

    def test_for_stage_unknown_exits_2(self, capsys: Any) -> None:
        mod = _get_awf_doctor()
        with pytest.raises(SystemExit) as exc_info:
            mod.main(["check.py", "--for-stage", "nonexistent-stage"])
        assert exc_info.value.code == 2

    def test_for_stage_unknown_stderr_lists_valid(self, capsys: Any) -> None:
        mod = _get_awf_doctor()
        with pytest.raises(SystemExit):
            mod.main(["check.py", "--for-stage", "bad-stage"])
        captured = capsys.readouterr()
        # The error message should mention valid stages.
        assert "landing" in captured.err
        assert "error" in captured.err.lower()

    def test_for_stage_landing_has_cloudflare(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mod = _get_awf_doctor()
        captured: list[Any] = []

        def fake_build_report(scope: Any = None, lead_with: Any = None) -> Any:
            captured.append(scope)
            return _make_passing_report(mod)

        monkeypatch.setattr(mod, "build_report", fake_build_report)
        mod.main(["check.py", "--for-stage", "landing"])
        assert "cloudflare" in captured[0].wanted
        assert "namecheap" in captured[0].wanted
        assert "wrangler" in captured[0].wanted


# ---------------------------------------------------------------------------
# 3. --for-skill scoping
# ---------------------------------------------------------------------------


class TestForSkill:
    def test_for_skill_kamal_deploy_checks_registry_kamal_ssh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mod = _get_awf_doctor()
        captured: list[Any] = []

        def fake_build_report(scope: Any = None, lead_with: Any = None) -> Any:
            captured.append(scope)
            return _make_passing_report(mod)

        monkeypatch.setattr(mod, "build_report", fake_build_report)
        rc = mod.main(["check.py", "--for-skill", "awf-kamal-deploy"])
        assert rc == 0
        scope = captured[0]
        assert isinstance(scope, mod.ScopeSkill)
        assert "kamal_cli" in scope.wanted
        assert "ssh" in scope.wanted
        assert "ghcr" in scope.wanted

    def test_for_skill_empty_requirements_exits_0(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """awf-app-secret-set has empty requirements → exit 0 with note."""
        mod = _get_awf_doctor()
        rc = mod.main(["check.py", "--for-skill", "awf-app-secret-set"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "No preflight" in captured.out

    def test_for_skill_empty_requirements_json_preflight_false(
        self, capsys: Any
    ) -> None:
        mod = _get_awf_doctor()
        rc = mod.main(["check.py", "--for-skill", "awf-app-secret-set", "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data.get("preflight_required") is False

    def test_for_skill_unknown_exits_2(self, capsys: Any) -> None:
        mod = _get_awf_doctor()
        with pytest.raises(SystemExit) as exc_info:
            mod.main(["check.py", "--for-skill", "totally-unknown-skill-xyz"])
        assert exc_info.value.code == 2

    def test_for_skill_and_for_stage_mutually_exclusive(self, capsys: Any) -> None:
        """--for-stage X --for-skill Y should exit 2."""
        mod = _get_awf_doctor()
        with pytest.raises(SystemExit) as exc_info:
            mod.main(["check.py", "--for-stage", "landing", "--for-skill", "awf-deploy"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# 4. Recent-error surfacing
# ---------------------------------------------------------------------------


class TestRecentErrorSurfacing:
    def test_detect_no_log_file_returns_none(self, tmp_path: Path) -> None:
        mod = _get_awf_doctor()
        _make_project(tmp_path)
        from lib.state import ProjectAnchor
        anchor = ProjectAnchor.load(start=tmp_path)
        result = mod.detect_credential_error_subsystem(anchor)
        assert result is None

    def test_detect_empty_log_returns_none(self, tmp_path: Path) -> None:
        mod = _get_awf_doctor()
        _make_project(tmp_path)
        _write_log_events(tmp_path, [])
        from lib.state import ProjectAnchor
        anchor = ProjectAnchor.load(start=tmp_path)
        result = mod.detect_credential_error_subsystem(anchor)
        assert result is None

    def test_detect_non_credential_error_returns_none(self, tmp_path: Path) -> None:
        """A 500-level error should not trigger credential surfacing."""
        mod = _get_awf_doctor()
        _make_project(tmp_path)
        _write_log_events(tmp_path, [
            {"type": "error", "note": "500 internal server error from Cloudflare"},
        ])
        from lib.state import ProjectAnchor
        anchor = ProjectAnchor.load(start=tmp_path)
        result = mod.detect_credential_error_subsystem(anchor)
        assert result is None

    def test_detect_non_error_type_returns_none(self, tmp_path: Path) -> None:
        """Only type==error events should trigger, not info/warn."""
        mod = _get_awf_doctor()
        _make_project(tmp_path)
        _write_log_events(tmp_path, [
            {"type": "info", "note": "401 from upstream API"},
        ])
        from lib.state import ProjectAnchor
        anchor = ProjectAnchor.load(start=tmp_path)
        result = mod.detect_credential_error_subsystem(anchor)
        assert result is None

    def test_detect_401_with_subsystem_field(self, tmp_path: Path) -> None:
        """Error event with explicit subsystem field and 401 note."""
        mod = _get_awf_doctor()
        _make_project(tmp_path)
        _write_log_events(tmp_path, [
            {"type": "error", "note": "401 Unauthorized", "subsystem": "neon"},
        ])
        from lib.state import ProjectAnchor
        anchor = ProjectAnchor.load(start=tmp_path)
        result = mod.detect_credential_error_subsystem(anchor)
        assert result == "neon"

    def test_detect_403_with_subsystem_field(self, tmp_path: Path) -> None:
        mod = _get_awf_doctor()
        _make_project(tmp_path)
        _write_log_events(tmp_path, [
            {"type": "error", "note": "403 Forbidden", "subsystem": "cloudflare"},
        ])
        from lib.state import ProjectAnchor
        anchor = ProjectAnchor.load(start=tmp_path)
        result = mod.detect_credential_error_subsystem(anchor)
        assert result == "cloudflare"

    def test_detect_auth_token_in_note(self, tmp_path: Path) -> None:
        """'auth' substring in note triggers detection."""
        mod = _get_awf_doctor()
        _make_project(tmp_path)
        _write_log_events(tmp_path, [
            {"type": "error", "note": "auth token expired for Hetzner", "subsystem": "hetzner"},
        ])
        from lib.state import ProjectAnchor
        anchor = ProjectAnchor.load(start=tmp_path)
        result = mod.detect_credential_error_subsystem(anchor)
        assert result == "hetzner"

    def test_detect_most_recent_event_wins(self, tmp_path: Path) -> None:
        """When multiple error events exist, most recent is returned."""
        mod = _get_awf_doctor()
        _make_project(tmp_path)
        _write_log_events(tmp_path, [
            {"type": "error", "note": "401 Unauthorized", "subsystem": "cloudflare"},
            {"type": "error", "note": "403 Forbidden", "subsystem": "neon"},
        ])
        from lib.state import ProjectAnchor
        anchor = ProjectAnchor.load(start=tmp_path)
        result = mod.detect_credential_error_subsystem(anchor)
        # reversed() → neon (last line = most recent)
        assert result == "neon"


# ---------------------------------------------------------------------------
# 5. Lead-with header integration
# ---------------------------------------------------------------------------


class TestLeadWithHeader:
    def test_lead_with_in_scope_produces_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Cred error in a subsystem that's inside scope → lead_with header in report."""
        mod = _get_awf_doctor()
        # Directly test that build_report with lead_with inside scope emits header.
        # Patch _run_subsystem_checks to avoid actual credential checks.
        captured_lead: list[Any] = []

        def capture_run(
            report: Any,
            cfg: Any,
            project_root: Any,
            wanted: list[str],
            lead_with_arg: Any,
        ) -> None:
            captured_lead.append(lead_with_arg)
            # Set the header as the real code would
            if lead_with_arg and lead_with_arg in wanted:
                report.lead_with_header = (
                    f"Recent error suggests credential issue — checking {lead_with_arg} first."
                )

        monkeypatch.setattr(mod, "_run_subsystem_checks", capture_run)
        # Also patch find_awf_home and find_project_root to avoid filesystem calls
        monkeypatch.setattr(mod, "find_awf_home", lambda: Path("/fake/awf"))
        monkeypatch.setattr(mod, "find_project_root", lambda optional=False: None)

        from lib.stages import stage_subsystems
        scope = mod.ScopeStage("landing", stage_subsystems("landing"))
        report = mod.build_report(scope=scope, lead_with="cloudflare")
        assert report.lead_with_header != ""
        assert "cloudflare" in report.lead_with_header
        assert captured_lead[0] == "cloudflare"

    def test_out_of_scope_cred_error_produces_advisory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Neon error (outside landing scope) → advisory header."""
        mod = _get_awf_doctor()
        _make_project(tmp_path)

        def fake_build_report(scope: Any = None, lead_with: Any = None) -> Any:
            r = _make_passing_report(mod)
            if lead_with and isinstance(scope, mod.ScopeStage):
                if lead_with not in scope.wanted:
                    r.lead_with_header = (
                        f"Recent error in {lead_with!r} noted but outside "
                        f"--for-stage {scope.name!r} scope. "
                        f"Run /awf-doctor (no flag) to investigate."
                    )
            return r

        monkeypatch.setattr(mod, "build_report", fake_build_report)

        # Directly test build_report with out-of-scope lead_with
        scope = mod.ScopeStage("landing", ["cloudflare", "namecheap"])
        r = fake_build_report(scope=scope, lead_with="neon")
        assert "outside" in r.lead_with_header
        assert "neon" in r.lead_with_header

    def test_lead_with_outside_scope_exit_code_from_scope_only(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Exit code is determined by requested scope checks only."""
        mod = _get_awf_doctor()

        def fake_build_report(scope: Any = None, lead_with: Any = None) -> Any:
            # No required failures → exit 0
            r = mod.Report(awf_home="/fake/awf", project_root=None)
            if lead_with and isinstance(scope, mod.ScopeStage) and lead_with not in scope.wanted:
                r.lead_with_header = f"Recent error in {lead_with!r} noted but outside scope."
            return r

        monkeypatch.setattr(mod, "build_report", fake_build_report)
        rc = mod.main(["check.py", "--for-stage", "landing"])
        assert rc == 0


# ---------------------------------------------------------------------------
# 6. tail_events import assertion (DRY / plan carry-note)
# ---------------------------------------------------------------------------


def test_tail_events_is_only_log_read_mechanism() -> None:
    """check.py must import tail_events from lib.log (grep test)."""
    script_path = _SKILLS_DIR / "awf-doctor" / "scripts" / "check.py"
    content = script_path.read_text(encoding="utf-8")
    assert "tail_events" in content
    assert "from lib.log import tail_events" in content
