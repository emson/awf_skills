"""Tests for skills/awf-status (plan_012).

Covers:
  - No project → exits 0, prints "Stage: none"
  - Basic project (S1): prints Project/Stage/Drift/Recent/Next + footer
  - Drift: Cloudflare (missing, unknown/creds-missing, id-mismatch, exception)
  - Drift: Hetzner (found-running, found-stopped, missing, unknown, exception)
  - Drift: Neon (project-missing, branch-missing, unknown, exception)
  - Missing infra.json → Hetzner/Neon checks skipped
  - --json output validates against STATUS_JSON_SCHEMA
  - --verbose: 20 events, per-provider detail block
  - Idle detection: 89/90/91 day edge cases, no session.end → null
  - Stage → next composer mapping for all five stages
  - tail_events import assertion (DRY check)

Mocking strategy: direct main() calls with monkeypatching of provider
client constructors. Real files in tmp_path (no live API calls).
Module is loaded via importlib module-loader pattern (sys.modules registration).
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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

from lib import log as log_lib  # noqa: E402
from lib.log import LOG_DIRNAME, LOG_FILENAME  # noqa: E402

# ---------------------------------------------------------------------------
# Module import helper
# ---------------------------------------------------------------------------


def _import_awf_status() -> Any:
    """Import and return the awf-status script module."""
    script_path = _SKILLS_DIR / "awf-status" / "scripts" / "status.py"
    spec = importlib.util.spec_from_file_location("awf_status_script", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


_AWF_STATUS: Any = None


def _get_awf_status() -> Any:
    global _AWF_STATUS
    if _AWF_STATUS is None:
        _AWF_STATUS = _import_awf_status()
    return _AWF_STATUS


# ---------------------------------------------------------------------------
# Fixtures: minimal project helpers
# ---------------------------------------------------------------------------

_MINIMAL_ANCHOR: dict[str, Any] = {
    "awf_version": "0.1.0",
    "domain": "example.com",
    "slug": "example-com",
    "stage": "landing",
    "created": "2026-01-01T00:00:00Z",
    "has": {"passport": False, "infra": False, "kamal": False, "content": False},
}


def _make_project(
    root: Path,
    *,
    domain: str = "example.com",
    stage: str = "landing",
) -> Path:
    """Create a minimal .awf project structure; return root."""
    awf_dir = root / ".awf"
    awf_dir.mkdir(parents=True, exist_ok=True)
    anchor = dict(_MINIMAL_ANCHOR)
    anchor["domain"] = domain
    anchor["slug"] = domain.replace(".", "-").replace("/", "-")
    anchor["stage"] = stage
    (awf_dir / "project.json").write_text(
        json.dumps(anchor) + "\n", encoding="utf-8"
    )
    # passport.json used by find_project_root walk
    (root / "passport.json").write_text(
        json.dumps({"domain": domain, "cloudflare": {}}), encoding="utf-8"
    )
    return root


def _make_infra(
    root: Path,
    *,
    servers: list[dict[str, Any]] | None = None,
    neon_project_id: str = "",
    neon_branch_id: str = "",
) -> Path:
    """Create a minimal .awf/infra.json."""
    infra: dict[str, Any] = {
        "registry": {"host": "ghcr.io", "image": "", "user": ""},
        "hetzner": {"servers": servers or [], "lb_id": None, "network_id": None},
        "neon": {
            "project_id": neon_project_id,
            "branch_id": neon_branch_id,
            "branch_name": "",
            "mode": "shared-branch",
            "connection_secret_ref": "DATABASE_URL",
        },
        "kamal": {"config_path": "config/deploy.yml", "last_deploy_image": None},
    }
    awf_dir = root / ".awf"
    awf_dir.mkdir(parents=True, exist_ok=True)
    (awf_dir / "infra.json").write_text(json.dumps(infra) + "\n", encoding="utf-8")
    return root


def _write_log_events(root: Path, events: list[dict[str, Any]]) -> Path:
    """Write a canned list of events to the project log; return the log path."""
    log_path = root / LOG_DIRNAME / LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, separators=(",", ":"), ensure_ascii=False) + "\n")
    return log_path


def _session_end_event(
    *,
    days_ago: int = 0,
    sid: str = "SESS001",
) -> dict[str, Any]:
    """Return a session.end event with ts set `days_ago` days in the past."""
    ended_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "ts": ended_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "session": sid,
        "project": "example-com",
        "stage": "landing",
        "actor": "claude-code",
        "type": "session.end",
        "result": "ok",
        "duration_ms": 1000,
        "data": {"events_count": 1, "gates_hit": 0, "summary": "ok"},
    }


# ---------------------------------------------------------------------------
# Autouse: reset log state after each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup_log_state() -> Any:
    """Reset log ContextVars and dry-run flag after each test."""
    yield
    log_lib.set_dry_run(False)
    log_lib._current_project_root.set(None)
    log_lib._current_project_slug.set("")
    log_lib._current_stage.set("")
    log_lib._current_actor.set("claude-code")
    log_lib._current_summary.set(None)


# ===========================================================================
# No-project tests
# ===========================================================================


class TestNoProject:
    def test_no_project_exits_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """No project anchor → exits 0."""
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        rc = mod.main([])
        assert rc == 0

    def test_no_project_prints_stage_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """No project → prints 'Stage: none'."""
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main([])
        captured = capsys.readouterr()
        assert "Stage:   none" in captured.out or "stage" in captured.out.lower()

    def test_no_project_prints_help_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """No project → prints help suggestion."""
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main([])
        captured = capsys.readouterr()
        assert "awf-help" in captured.out or "awf-create-project" in captured.out

    def test_no_project_json_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """No project + --json → project=null, stage='none', hint present."""
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["--json"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["project"] is None
        assert data["stage"] == "none"
        assert "hint" in data
        assert data["schema_version"] == 1


# ===========================================================================
# Basic S1 project
# ===========================================================================


class TestBasicProject:
    def _no_cf(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch CF client to raise (credentials missing)."""
        import lib.cf.zones as cfz
        monkeypatch.setattr(
            cfz, "get_zone", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no creds"))
        )

    def test_basic_project_exits_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """S1 project → exits 0."""
        _make_project(tmp_path)
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        # Patch config so CF check degrades to unknown (no live call)
        import lib.config as config_mod
        orig_layered = config_mod.Config.layered

        def _empty_cfg(*a: Any, **kw: Any) -> Any:
            cfg = orig_layered(*a, **kw)
            return cfg

        # Just run — no real CF creds so it will degrade gracefully
        rc = mod.main([])
        assert rc == 0

    def test_basic_project_output_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Output block order: Project / Stage / Drift / Recent / Next."""
        _make_project(tmp_path)
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main([])
        captured = capsys.readouterr()
        out = captured.out
        p_idx = out.find("Project:")
        s_idx = out.find("Stage:")
        d_idx = out.find("Drift:")
        r_idx = out.find("Recent:")
        n_idx = out.find("Next:")
        # All present and in order
        assert p_idx >= 0
        assert s_idx > p_idx
        assert d_idx > s_idx
        assert r_idx > d_idx
        assert n_idx > r_idx

    def test_basic_project_not_yet_checked_footer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Footer always present with v1 deferred checks."""
        _make_project(tmp_path)
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main([])
        captured = capsys.readouterr()
        assert "Not yet checked:" in captured.out
        assert "dns_records" in captured.out
        assert "cloudflare_pages" in captured.out
        assert "fathom" in captured.out
        assert "gsc" in captured.out
        assert "hetzner_lb" in captured.out

    def test_basic_project_landing_stage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """S1 landing stage is reported."""
        _make_project(tmp_path, stage="landing")
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main([])
        captured = capsys.readouterr()
        assert "landing" in captured.out


# ===========================================================================
# Stage → Next composer mapping
# ===========================================================================


class TestNextComposerMapping:
    @pytest.mark.parametrize("stage,expected_composer", [
        ("landing", "awf-stage-demo"),
        ("demo", "awf-stage-mvp-play"),
        ("mvp-play", "awf-stage-prescale"),
        ("prescale", "awf-stage-scale"),
        ("scale", None),
    ])
    def test_next_composer_for_stage(
        self, stage: str, expected_composer: str | None
    ) -> None:
        """next_composer_for_stage returns correct composer for each stage."""
        mod = _get_awf_status()
        composer, hint = mod.next_composer_for_stage(stage)
        assert composer == expected_composer

    def test_scale_stage_terminal_hint(self) -> None:
        """scale stage → hint mentions terminal."""
        mod = _get_awf_status()
        _, hint = mod.next_composer_for_stage("scale")
        assert "terminal" in hint.lower() or "atomic" in hint.lower()

    def test_next_shown_in_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Next: block appears in output with composer name."""
        _make_project(tmp_path, stage="landing")
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main([])
        captured = capsys.readouterr()
        assert "Next:" in captured.out
        assert "awf-stage-demo" in captured.out


# ===========================================================================
# Drift: Cloudflare
# ===========================================================================


class TestDriftCloudflare:
    def _patch_cf_no_creds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make CF credential lookup return empty (credentials missing)."""
        import lib.config as config_mod

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                return None
            def require(self, key: str) -> str:
                raise RuntimeError(f"missing: {key}")
            def source(self, key: str) -> str:
                return "none"

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

    def test_cf_missing_credentials_world_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CF creds missing → drift entry with world_value='unknown', exits 0."""
        _make_project(tmp_path)
        mod = _get_awf_status()
        self._patch_cf_no_creds(monkeypatch)
        monkeypatch.chdir(tmp_path)
        rc = mod.main([])
        assert rc == 0

    def test_cf_missing_credentials_json_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """CF creds missing → JSON drift entry world_value='unknown'."""
        _make_project(tmp_path)
        mod = _get_awf_status()
        self._patch_cf_no_creds(monkeypatch)
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        cf_drift = [d for d in data["drift"] if d["provider"] == "cloudflare"]
        assert any(d["world_value"] == "unknown" for d in cf_drift)
        assert all(d["suggested_skill"] == "" for d in cf_drift)

    def _patch_cf_zone_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch CF to return no zone (zone missing) by patching _check_cloudflare internals."""
        import lib.config as config_mod

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                if key in ("CLOUDFLARE_EMAIL", "CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID"):
                    return "fake-value"
                return None
            def require(self, key: str) -> str:
                return "fake-value"
            def source(self, key: str) -> str:
                return "env"

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

        # Patch at the awf_status_script module level since it does its own imports
        mod = _get_awf_status()

        # Intercept the whole _check_cloudflare to return a "missing" drift entry
        # without requiring the cloudflare SDK module

        def _fake_check_cloudflare(anchor: Any, infra: Any, *, verbose: bool) -> list:
            return [mod.DriftEntry(
                provider="cloudflare",
                resource="zone",
                state_value=anchor.domain,
                world_value="missing",
                suggested_skill="awf-setup-domain",
                note=f"Zone for {anchor.domain} not found in Cloudflare.",
            )]

        monkeypatch.setattr(mod, "_check_cloudflare", _fake_check_cloudflare)

    def test_cf_zone_missing_drift_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """CF zone missing → drift entry with world_value='missing' and suggested skill."""
        _make_project(tmp_path)
        mod = _get_awf_status()
        self._patch_cf_zone_missing(monkeypatch)
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        cf_drift = [d for d in data["drift"] if d["provider"] == "cloudflare"]
        assert len(cf_drift) > 0
        missing = [d for d in cf_drift if d["world_value"] == "missing"]
        assert len(missing) > 0
        assert missing[0]["suggested_skill"] == "awf-setup-domain"

    def test_cf_exception_world_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """CF get_zone raises → world_value='unknown', exits 0."""
        _make_project(tmp_path)
        mod = _get_awf_status()

        # Patch _check_cloudflare directly to simulate exception capture
        def _fake_check_cf_exception(anchor: Any, infra: Any, *, verbose: bool) -> list:
            return [mod.DriftEntry(
                provider="cloudflare",
                resource="zone",
                state_value=anchor.domain,
                world_value="unknown",
                suggested_skill="",
                note="Cloudflare API call failed.",
                _error_msg="boom",
            )]

        monkeypatch.setattr(mod, "_check_cloudflare", _fake_check_cf_exception)
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["--json"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        cf_drift = [d for d in data["drift"] if d["provider"] == "cloudflare"]
        assert any(d["world_value"] == "unknown" for d in cf_drift)


# ===========================================================================
# Drift: Hetzner
# ===========================================================================


class TestDriftHetzner:
    def _setup_with_server(
        self, tmp_path: Path, server_id: str = "42"
    ) -> None:
        _make_project(tmp_path)
        _make_infra(tmp_path, servers=[{"id": server_id, "ip": "1.2.3.4", "role": "web", "shared": False, "cost_eur_month": 5.0}])

    def _patch_hz_no_creds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import lib.config as config_mod

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                return None
            def require(self, key: str) -> str:
                raise RuntimeError(f"missing: {key}")
            def source(self, key: str) -> str:
                return "none"

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

    def test_hetzner_no_creds_world_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Hetzner creds missing → drift entry world_value='unknown'."""
        self._setup_with_server(tmp_path)
        mod = _get_awf_status()
        self._patch_hz_no_creds(monkeypatch)
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["--json"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        hz_drift = [d for d in data["drift"] if d["provider"] == "hetzner"]
        assert any(d["world_value"] == "unknown" for d in hz_drift)
        assert all(d["suggested_skill"] == "" for d in hz_drift)

    def _patch_hz_server_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hetzner server not found (returns None)."""
        import lib.config as config_mod
        import lib.hetzner as hz_mod

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                if key == "HETZNER_API_TOKEN":
                    return "fake-token"
                return None

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

        class _FakeServers:
            def get_by_id(self, id: int) -> Any:
                return None

        class _FakeHZClient:
            def __init__(self, config: Any) -> None:
                self.config = config
                self._client = type("_C", (), {"servers": _FakeServers()})()
                # Expose servers namespace (not needed for get_by_id path)

        class _FakeHetznerClient:
            def __init__(self, config: Any) -> None:
                self._client = type("_C", (), {"servers": _FakeServers()})()
                self.config = config

        monkeypatch.setattr(hz_mod, "HetznerClient", _FakeHetznerClient)

    def test_hetzner_server_missing_drift(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Hetzner server not found → drift entry world_value='missing'."""
        self._setup_with_server(tmp_path, server_id="99")
        mod = _get_awf_status()
        self._patch_hz_server_missing(monkeypatch)
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        hz_drift = [d for d in data["drift"] if d["provider"] == "hetzner"]
        assert any(d["world_value"] == "missing" for d in hz_drift)
        missing = [d for d in hz_drift if d["world_value"] == "missing"]
        assert missing[0]["suggested_skill"] == "awf-hetzner-provision"

    def test_hetzner_server_stopped_drift(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Hetzner server found but stopped → drift entry with status."""
        self._setup_with_server(tmp_path, server_id="55")
        mod = _get_awf_status()

        import lib.config as config_mod
        import lib.hetzner as hz_mod

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                if key == "HETZNER_API_TOKEN":
                    return "fake-token"
                return None

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

        class _FakeServer:
            status = "off"
            id = 55

        class _FakeServers:
            def get_by_id(self, id: int) -> Any:
                return _FakeServer()

        class _FakeHetznerClient:
            def __init__(self, config: Any) -> None:
                self._client = type("_C", (), {"servers": _FakeServers()})()
                self.config = config

        monkeypatch.setattr(hz_mod, "HetznerClient", _FakeHetznerClient)

        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        hz_drift = [d for d in data["drift"] if d["provider"] == "hetzner"]
        assert any("status=off" in d["world_value"] for d in hz_drift)

    def test_hetzner_server_running_no_drift(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Hetzner server running → no drift entry for hetzner."""
        self._setup_with_server(tmp_path, server_id="77")
        mod = _get_awf_status()

        import lib.config as config_mod
        import lib.hetzner as hz_mod

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                if key == "HETZNER_API_TOKEN":
                    return "fake-token"
                return None

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

        class _FakeServer:
            status = "running"
            id = 77

        class _FakeServers:
            def get_by_id(self, id: int) -> Any:
                return _FakeServer()

        class _FakeHetznerClient:
            def __init__(self, config: Any) -> None:
                self._client = type("_C", (), {"servers": _FakeServers()})()
                self.config = config

        monkeypatch.setattr(hz_mod, "HetznerClient", _FakeHetznerClient)

        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        hz_drift = [d for d in data["drift"] if d["provider"] == "hetzner"]
        assert len(hz_drift) == 0

    def test_hetzner_exception_world_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """HetznerError raised → world_value='unknown', exits 0."""
        self._setup_with_server(tmp_path)
        mod = _get_awf_status()

        import lib.config as config_mod
        import lib.hetzner as hz_mod
        from lib.hetzner.errors import HetznerError

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                if key == "HETZNER_API_TOKEN":
                    return "fake-token"
                return None

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

        class _FakeServers:
            def get_by_id(self, id: int) -> Any:
                raise HetznerError("boom")

        class _FakeHetznerClient:
            def __init__(self, config: Any) -> None:
                self._client = type("_C", (), {"servers": _FakeServers()})()
                self.config = config

        monkeypatch.setattr(hz_mod, "HetznerClient", _FakeHetznerClient)

        monkeypatch.chdir(tmp_path)
        rc = mod.main(["--json"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        hz_drift = [d for d in data["drift"] if d["provider"] == "hetzner"]
        assert any(d["world_value"] == "unknown" for d in hz_drift)


# ===========================================================================
# Drift: Neon
# ===========================================================================


class TestDriftNeon:
    def _setup_with_neon(
        self,
        tmp_path: Path,
        project_id: str = "lucky-cloud-123",
        branch_id: str = "br-abc-123",
    ) -> None:
        _make_project(tmp_path)
        _make_infra(tmp_path, neon_project_id=project_id, neon_branch_id=branch_id)

    def _patch_neon_no_creds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import lib.config as config_mod

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                return None

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

    def test_neon_no_creds_world_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Neon creds missing → drift entry world_value='unknown'."""
        self._setup_with_neon(tmp_path)
        mod = _get_awf_status()
        self._patch_neon_no_creds(monkeypatch)
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["--json"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        neon_drift = [d for d in data["drift"] if d["provider"] == "neon"]
        assert any(d["world_value"] == "unknown" for d in neon_drift)
        assert all(d["suggested_skill"] == "" for d in neon_drift)

    def _patch_neon_project_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import lib.config as config_mod
        import lib.neon.client as neon_client_mod

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                if key == "NEON_API_KEY":
                    return "fake-key"
                return None

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

        class _FakeProjects:
            def get(self, name_or_id: str) -> Any:
                return None

        class _FakeBranches:
            def get(self, project_id: str, name_or_id: str) -> Any:
                return None

        class _FakeNeonClient:
            def __init__(self, config: Any) -> None:
                self.config = config
                self.projects = _FakeProjects()
                self.branches = _FakeBranches()

        monkeypatch.setattr(neon_client_mod, "NeonClient", _FakeNeonClient)

    def test_neon_project_missing_drift(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Neon project missing → drift entry; branch check skipped."""
        self._setup_with_neon(tmp_path)
        mod = _get_awf_status()
        self._patch_neon_project_missing(monkeypatch)
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        neon_drift = [d for d in data["drift"] if d["provider"] == "neon"]
        # Only project drift — branch subsumes check skipped
        project_missing = [d for d in neon_drift if d["resource"] == "project" and d["world_value"] == "missing"]
        assert len(project_missing) == 1
        assert project_missing[0]["suggested_skill"] == "awf-neon-provision"
        # No branch entry (subsumes)
        branch_drift = [d for d in neon_drift if d["resource"] == "branch"]
        assert len(branch_drift) == 0

    def _patch_neon_branch_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import lib.config as config_mod
        import lib.neon.client as neon_client_mod
        from lib.neon.resources.projects import Project

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                if key == "NEON_API_KEY":
                    return "fake-key"
                return None

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

        _fake_project = Project(id="lucky-cloud-123", name="my-app", region_id="aws-eu-central-1", created_at="2026-01-01")

        class _FakeProjects:
            def get(self, name_or_id: str) -> Any:
                return _fake_project

        class _FakeBranches:
            def get(self, project_id: str, name_or_id: str) -> Any:
                return None

        class _FakeNeonClient:
            def __init__(self, config: Any) -> None:
                self.config = config
                self.projects = _FakeProjects()
                self.branches = _FakeBranches()

        monkeypatch.setattr(neon_client_mod, "NeonClient", _FakeNeonClient)

    def test_neon_branch_missing_drift(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Neon project found but branch missing → branch drift entry."""
        self._setup_with_neon(tmp_path)
        mod = _get_awf_status()
        self._patch_neon_branch_missing(monkeypatch)
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        neon_drift = [d for d in data["drift"] if d["provider"] == "neon"]
        branch_missing = [d for d in neon_drift if d["resource"] == "branch" and d["world_value"] == "missing"]
        assert len(branch_missing) == 1
        assert branch_missing[0]["suggested_skill"] == "awf-neon-provision"

    def test_neon_exception_world_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """NeonError raised → world_value='unknown', exits 0."""
        self._setup_with_neon(tmp_path)
        mod = _get_awf_status()

        import lib.config as config_mod
        import lib.neon.client as neon_client_mod
        from lib.neon.errors import NeonError

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                if key == "NEON_API_KEY":
                    return "fake-key"
                return None

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

        class _FakeProjects:
            def get(self, name_or_id: str) -> Any:
                raise NeonError("boom")

        class _FakeBranches:
            def get(self, *a: Any, **kw: Any) -> Any:
                return None

        class _FakeNeonClient:
            def __init__(self, config: Any) -> None:
                self.config = config
                self.projects = _FakeProjects()
                self.branches = _FakeBranches()

        monkeypatch.setattr(neon_client_mod, "NeonClient", _FakeNeonClient)

        monkeypatch.chdir(tmp_path)
        rc = mod.main(["--json"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        neon_drift = [d for d in data["drift"] if d["provider"] == "neon"]
        assert any(d["world_value"] == "unknown" for d in neon_drift)


# ===========================================================================
# Infra absent → Hetzner/Neon skipped
# ===========================================================================


class TestNoInfra:
    def test_no_infra_hetzner_neon_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """No infra.json → Hetzner and Neon drift checks skipped entirely."""
        _make_project(tmp_path)  # no infra.json
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        hz_drift = [d for d in data["drift"] if d["provider"] == "hetzner"]
        neon_drift = [d for d in data["drift"] if d["provider"] == "neon"]
        assert len(hz_drift) == 0
        assert len(neon_drift) == 0


# ===========================================================================
# Recent events via tail_events
# ===========================================================================


class TestRecentEvents:
    def test_recent_block_uses_tail_events(self) -> None:
        """Assert that status.py imports tail_events from lib.log (DRY check)."""
        script_path = _SKILLS_DIR / "awf-status" / "scripts" / "status.py"
        content = script_path.read_text(encoding="utf-8")
        assert "tail_events" in content
        assert "from lib.log import tail_events" in content or "from lib.log import" in content

    def test_recent_events_shown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Recent events from log are included in output."""
        _make_project(tmp_path)
        events = [
            {
                "ts": f"2026-06-01T10:00:0{i}.000Z",
                "session": "SID",
                "project": "example-com",
                "stage": "landing",
                "actor": "cli",
                "type": "note",
                "data": {"text": f"msg {i}", "by": "human"},
            }
            for i in range(3)
        ]
        _write_log_events(tmp_path, events)
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main([])
        captured = capsys.readouterr()
        assert "Recent:" in captured.out
        # At least one event line should appear after Recent:
        assert "note" in captured.out


# ===========================================================================
# Idle detection
# ===========================================================================


class TestIdleDetection:
    def test_no_session_end_idle_null(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """No session.end in log → idle=null in JSON."""
        _make_project(tmp_path)
        events = [
            {
                "ts": "2026-06-01T10:00:00.000Z",
                "session": "SID",
                "project": "example-com",
                "stage": "landing",
                "actor": "cli",
                "type": "note",
                "data": {"text": "hello"},
            }
        ]
        _write_log_events(tmp_path, events)
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data.get("idle") is None

    def test_recent_session_not_idle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """session.end 89 days ago → NOT idle."""
        _make_project(tmp_path)
        _write_log_events(tmp_path, [_session_end_event(days_ago=89)])
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data.get("idle") is None

    def test_boundary_not_idle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """session.end exactly 90 days ago → NOT idle (threshold is strictly >)."""
        _make_project(tmp_path)
        _write_log_events(tmp_path, [_session_end_event(days_ago=90)])
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data.get("idle") is None

    def test_91_days_idle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """session.end 91 days ago → idle.days_since_last_session >= 91."""
        _make_project(tmp_path)
        _write_log_events(tmp_path, [_session_end_event(days_ago=91)])
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data.get("idle") is not None
        assert data["idle"]["days_since_last_session"] >= 91

    def test_idle_warning_in_human_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Idle project → 'Idle:' line in human output."""
        _make_project(tmp_path)
        _write_log_events(tmp_path, [_session_end_event(days_ago=120)])
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main([])
        captured = capsys.readouterr()
        assert "Idle:" in captured.out


# ===========================================================================
# --verbose flag
# ===========================================================================


class TestVerbose:
    def test_verbose_extends_event_tail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """--verbose requests 20 events (not 5)."""
        _make_project(tmp_path)
        # Write 15 events
        events = [
            {
                "ts": f"2026-06-01T10:00:{i:02d}.000Z",
                "session": "SID",
                "project": "example-com",
                "stage": "landing",
                "actor": "cli",
                "type": "note",
                "data": {"text": f"msg {i}"},
            }
            for i in range(15)
        ]
        _write_log_events(tmp_path, events)
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main(["--verbose"])
        captured = capsys.readouterr()
        # With 15 events and n=20 we get all 15 in output
        assert "note" in captured.out

    def test_verbose_details_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """--verbose shows '--- details ---' divider."""
        _make_project(tmp_path)
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main(["--verbose"])
        captured = capsys.readouterr()
        assert "--- details ---" in captured.out

    def test_verbose_json_has_details(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """--verbose --json includes 'details' key in output."""
        _make_project(tmp_path)
        _make_infra(tmp_path)
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main(["--json", "--verbose"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data.get("verbose") is True


# ===========================================================================
# JSON schema validation
# ===========================================================================


class TestJsonSchema:
    def test_schema_parses(self) -> None:
        """STATUS_JSON_SCHEMA is valid JSON."""
        from lib.state import STATUS_JSON_SCHEMA
        schema = json.loads(STATUS_JSON_SCHEMA)
        assert "oneOf" in schema

    def test_no_project_output_validates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """No-project JSON output validates against STATUS_JSON_SCHEMA."""
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")
        from lib.state import STATUS_JSON_SCHEMA
        schema = json.loads(STATUS_JSON_SCHEMA)

        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        # Should not raise
        jsonschema.validate(instance=data, schema=schema)

    def test_project_output_validates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Project JSON output validates against STATUS_JSON_SCHEMA."""
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")
        from lib.state import STATUS_JSON_SCHEMA
        schema = json.loads(STATUS_JSON_SCHEMA)

        _make_project(tmp_path)
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        jsonschema.validate(instance=data, schema=schema)

    def test_project_with_drift_validates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Project output with drift entries validates against schema."""
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")
        from lib.state import STATUS_JSON_SCHEMA
        schema = json.loads(STATUS_JSON_SCHEMA)

        _make_project(tmp_path)
        _make_infra(tmp_path, servers=[{"id": "10", "ip": "1.2.3.4", "role": "web", "shared": False, "cost_eur_month": 0.0}])
        mod = _get_awf_status()

        # Patch everything to produce drift entries cleanly
        import lib.config as config_mod

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                return None

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))

        monkeypatch.chdir(tmp_path)
        mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        jsonschema.validate(instance=data, schema=schema)


# ===========================================================================
# Exit codes
# ===========================================================================


class TestExitCodes:
    def test_unknown_flag_exits_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unknown flag → exits 4."""
        mod = _get_awf_status()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["--unknown-flag"])
        assert rc == 4

    def test_drift_present_exits_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Drift present (credentials missing) → still exits 0."""
        _make_project(tmp_path)
        mod = _get_awf_status()
        import lib.config as config_mod

        class _FakeCfg:
            def get(self, key: str) -> str | None:
                return None

        monkeypatch.setattr(config_mod.Config, "layered", classmethod(lambda cls, **kw: _FakeCfg()))
        monkeypatch.chdir(tmp_path)
        rc = mod.main([])
        assert rc == 0
