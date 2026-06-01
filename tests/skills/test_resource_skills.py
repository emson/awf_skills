"""Tests for the five atomic resource skills from plan_008.

Covers:
  - awf-hetzner-server
  - awf-neon-project
  - awf-neon-branch
  - awf-cf-dns-record
  - awf-shared-infra-get

Mocking strategy: monkeypatch lib.hetzner.HetznerClient.from_env,
lib.neon.NeonClient.from_env, and lib.cf.client.get_client to return fakes.
Real tmp_path projects + real state-file writes; only external API boundaries mocked.

Tests invoke main() directly (in-process) after setting AWF_HOME and cwd,
plus --help smoke checks via subprocess.

Acceptance criteria from plan_008:
  - happy-path-create: first run creates resource + state, action=created
  - happy-path-skip: second run with same inputs, action=skip, no new state.change
  - no-project-exit-1: skills 2-5 exit 1 when no .awf/project.json
  - missing-creds-exit-2: RuntimeError from from_env() → exit 2
  - --json shape assertion: output parses as JSON with required keys
  - --help exits 0 (smoke check)
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Repo root and shared constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / "skills"
_LIB_DIR = _REPO_ROOT / "lib"

# Minimal passport for the project
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

_MINIMAL_ANCHOR: dict[str, Any] = {
    "awf_version": "0.1.0",
    "domain": "example.com",
    "slug": "example-com",
    "stage": "mvp-play",
    "created": "2026-06-01T00:00:00Z",
    "has": {"passport": True, "infra": False, "kamal": False, "content": False},
}


# ---------------------------------------------------------------------------
# Project setup helpers
# ---------------------------------------------------------------------------


def _make_project(root: Path, *, domain: str = "example.com") -> None:
    """Write passport.json and .awf/project.json."""
    slug = domain.replace(".", "-")
    passport = dict(_MINIMAL_PASSPORT)
    passport["domain"] = domain
    passport["project_name"] = slug
    passport["site_url"] = f"https://{domain}"
    (root / "passport.json").write_text(json.dumps(passport, indent=4) + "\n", encoding="utf-8")

    awf_dir = root / ".awf"
    awf_dir.mkdir(exist_ok=True)
    anchor = dict(_MINIMAL_ANCHOR)
    anchor["domain"] = domain
    anchor["slug"] = slug
    (awf_dir / "project.json").write_text(json.dumps(anchor) + "\n", encoding="utf-8")


def _read_infra(root: Path) -> dict[str, Any]:
    path = root / ".awf" / "infra.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_passport(root: Path) -> dict[str, Any]:
    path = root / "passport.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fake client factories
# ---------------------------------------------------------------------------


def _make_fake_server(server_id: str = "42", ip: str = "1.2.3.4", name: str = "test-server"):
    srv = MagicMock()
    srv.id = int(server_id)
    srv.public_net = MagicMock()
    srv.public_net.ipv4 = MagicMock()
    srv.public_net.ipv4.ip = ip
    return srv


def _make_fake_hetzner_client(server_id: str = "42", ip: str = "1.2.3.4"):
    srv = _make_fake_server(server_id, ip)
    hz = MagicMock()
    hz.servers.get_or_create.return_value = srv
    return hz


def _make_fake_neon_project(project_id: str = "lucky-cloud-111111", name: str = "test-project"):
    proj = MagicMock()
    proj.id = project_id
    proj.name = name
    return proj


def _make_fake_neon_branch(branch_id: str = "br-test-111", name: str = "preview", project_id: str = "lucky-cloud-111111"):
    br = MagicMock()
    br.id = branch_id
    br.name = name
    br.project_id = project_id
    return br


def _make_fake_neon_client(
    project_id: str = "lucky-cloud-111111",
    branch_id: str = "br-test-111",
):
    proj = _make_fake_neon_project(project_id)
    br = _make_fake_neon_branch(branch_id, project_id=project_id)
    nc = MagicMock()
    nc.projects.get_or_create.return_value = proj
    nc.branches.get_or_create.return_value = br
    return nc


def _make_fake_cf_client():
    record = MagicMock()
    record.id = "rec-abc123"
    cf = MagicMock()
    cf.config.account_id = "acct-test"
    cf.client = MagicMock()
    return cf, record


def _inject_cloudflare_stub():
    """Inject a stub 'cloudflare' package into sys.modules so lib.cf.client
    can be imported without the real cloudflare PyPI package installed.

    Called once per test session via a module-level setup or per-test
    before any lib.cf import.
    """
    if "cloudflare" in sys.modules:
        return  # already present (either real or stub)

    stub = MagicMock()
    stub.Cloudflare = MagicMock
    sys.modules["cloudflare"] = stub


# ---------------------------------------------------------------------------
# Script import helper
# ---------------------------------------------------------------------------


def _import_main(skill_name: str, module_name: str):
    """Import the main() function from a skill script.

    Temporarily inserts repo root and lib/ into sys.path so the script
    bootstrap (which sets up its own path) works in-process.
    """
    script_path = _SKILLS_DIR / skill_name / "scripts" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(
        f"awf_skill_{module_name}",
        script_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Ensure repo root and lib are on path before exec
    for p in [str(_REPO_ROOT), str(_LIB_DIR)]:
        if p not in sys.path:
            sys.path.insert(0, p)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.main


# ---------------------------------------------------------------------------
# Shared AWF_HOME patcher
# ---------------------------------------------------------------------------


def _patch_env(monkeypatch, tmp_path: Path, cwd: Path | None = None):
    """Set AWF_HOME and optionally change cwd for the test."""
    monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
    if cwd is not None:
        monkeypatch.chdir(cwd)


# ===========================================================================
# awf-hetzner-server tests
# ===========================================================================


class TestHetznerServer:
    """Tests for awf-hetzner-server."""

    def _main(self):
        return _import_main("awf-hetzner-server", "hetzner_server")

    def test_help_exits_0(self):
        """--help exits 0 (smoke check)."""
        script = _SKILLS_DIR / "awf-hetzner-server" / "scripts" / "hetzner_server.py"
        result = subprocess.run(
            ["uv", "run", str(script), "--help"],
            env={**os.environ, "AWF_HOME": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "awf-hetzner-server" in result.stdout

    def test_happy_path_create(self, tmp_path: Path, monkeypatch) -> None:
        """First run creates server entry in infra.json, action=created."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        fake_hz = _make_fake_hetzner_client()

        with patch("lib.hetzner.client.HetznerClient.from_env", return_value=fake_hz):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["hetzner_server", "--name", "test-server"])
            rc = main()

        assert rc == 0
        infra = _read_infra(tmp_path)
        assert len(infra["hetzner"]["servers"]) == 1
        assert infra["hetzner"]["servers"][0]["id"] == "42"
        assert infra["hetzner"]["servers"][0]["ip"] == "1.2.3.4"

    def test_happy_path_create_json(self, tmp_path: Path, monkeypatch) -> None:
        """--json output has required keys on create."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        fake_hz = _make_fake_hetzner_client()

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        with patch("lib.hetzner.client.HetznerClient.from_env", return_value=fake_hz):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["hetzner_server", "--name", "test-server", "--json"])
            with patch("builtins.print", side_effect=capturing_print):
                rc = main()

        assert rc == 0
        assert len(output_lines) == 1
        data = json.loads(output_lines[0])
        assert set(data.keys()) >= {"action", "server"}
        assert data["action"] == "created"
        assert "id" in data["server"]
        assert "ip" in data["server"]

    def test_happy_path_skip(self, tmp_path: Path, monkeypatch) -> None:
        """Second run with same server ID → action=skip, no additional state.change."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        fake_hz = _make_fake_hetzner_client()

        with patch("lib.hetzner.client.HetznerClient.from_env", return_value=fake_hz):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["hetzner_server", "--name", "test-server"])
            main()  # first run — creates

        # Read infra to confirm written
        infra_before = _read_infra(tmp_path)
        assert len(infra_before["hetzner"]["servers"]) == 1

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        with patch("lib.hetzner.client.HetznerClient.from_env", return_value=fake_hz):
            main2 = self._main()
            monkeypatch.setattr(sys, "argv", ["hetzner_server", "--name", "test-server"])
            with patch("builtins.print", side_effect=capturing_print):
                rc = main2()

        assert rc == 0
        assert any("skip" in line for line in output_lines), f"Expected skip in {output_lines}"

        # Infra unchanged
        infra_after = _read_infra(tmp_path)
        assert infra_after == infra_before

    def test_no_project_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        """No .awf/project.json → exits 1."""
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["hetzner_server", "--name", "test-server"])
        rc = main()
        assert rc == 1

    def test_missing_creds_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        """RuntimeError from HetznerClient.from_env → exits 2."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        with patch("lib.hetzner.client.HetznerClient.from_env", side_effect=RuntimeError("HETZNER_API_TOKEN missing")):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["hetzner_server", "--name", "test-server"])
            rc = main()

        assert rc == 2


# ===========================================================================
# awf-neon-project tests
# ===========================================================================


class TestNeonProject:
    """Tests for awf-neon-project."""

    def _main(self):
        return _import_main("awf-neon-project", "neon_project")

    def test_help_exits_0(self):
        """--help exits 0 (smoke check)."""
        script = _SKILLS_DIR / "awf-neon-project" / "scripts" / "neon_project.py"
        result = subprocess.run(
            ["uv", "run", str(script), "--help"],
            env={**os.environ, "AWF_HOME": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "awf-neon-project" in result.stdout

    def test_happy_path_create(self, tmp_path: Path, monkeypatch) -> None:
        """First run writes neon.project_id to infra.json, action=created."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        fake_nc = _make_fake_neon_client()

        with patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["neon_project", "--name", "my-app"])
            rc = main()

        assert rc == 0
        infra = _read_infra(tmp_path)
        assert infra["neon"]["project_id"] == "lucky-cloud-111111"

    def test_happy_path_create_json(self, tmp_path: Path, monkeypatch) -> None:
        """--json output has required keys."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        fake_nc = _make_fake_neon_client()

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        with patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["neon_project", "--name", "my-app", "--json"])
            with patch("builtins.print", side_effect=capturing_print):
                rc = main()

        assert rc == 0
        data = json.loads(output_lines[0])
        assert set(data.keys()) >= {"action", "neon_project_id"}
        assert data["action"] == "created"
        assert data["neon_project_id"] == "lucky-cloud-111111"

    def test_action_is_created_on_first_run(self, tmp_path: Path, monkeypatch) -> None:
        """First run action=created (before_id was empty, Reviewer T4 fix)."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        fake_nc = _make_fake_neon_client()

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        with patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["neon_project", "--name", "my-app", "--json"])
            with patch("builtins.print", side_effect=capturing_print):
                main()

        data = json.loads(output_lines[0])
        assert data["action"] == "created", f"Expected created, got {data['action']!r}"

    def test_happy_path_skip(self, tmp_path: Path, monkeypatch) -> None:
        """Second run with same project ID → action=skip, infra unchanged."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        fake_nc = _make_fake_neon_client()

        with patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["neon_project", "--name", "my-app"])
            main()

        infra_before = _read_infra(tmp_path)

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        with patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main2 = self._main()
            monkeypatch.setattr(sys, "argv", ["neon_project", "--name", "my-app"])
            with patch("builtins.print", side_effect=capturing_print):
                rc = main2()

        assert rc == 0
        assert any("skip" in line for line in output_lines)
        assert _read_infra(tmp_path) == infra_before

    def test_no_project_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        """No .awf/project.json → exits 1."""
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["neon_project", "--name", "my-app"])
        rc = main()
        assert rc == 1

    def test_missing_creds_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        """RuntimeError from NeonClient.from_env → exits 2."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        with patch("lib.neon.client.NeonClient.from_env", side_effect=RuntimeError("NEON_API_KEY missing")):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["neon_project", "--name", "my-app"])
            rc = main()

        assert rc == 2


# ===========================================================================
# awf-neon-branch tests
# ===========================================================================


class TestNeonBranch:
    """Tests for awf-neon-branch."""

    def _main(self):
        return _import_main("awf-neon-branch", "neon_branch")

    def _make_project_with_neon(self, tmp_path: Path, project_id: str = "lucky-cloud-111111") -> None:
        """Make a project with neon.project_id already in infra.json."""
        _make_project(tmp_path)
        infra_data = {
            "registry": {"host": "ghcr.io", "image": "", "user": ""},
            "hetzner": {"servers": [], "lb_id": None, "network_id": None},
            "neon": {"project_id": project_id, "branch_id": "", "branch_name": "", "mode": "shared-branch", "connection_secret_ref": "DATABASE_URL"},
            "kamal": {"config_path": "config/deploy.yml", "last_deploy_image": None},
        }
        awf_dir = tmp_path / ".awf"
        awf_dir.mkdir(exist_ok=True)
        (awf_dir / "infra.json").write_text(json.dumps(infra_data) + "\n", encoding="utf-8")

    def test_help_exits_0(self):
        """--help exits 0 (smoke check)."""
        script = _SKILLS_DIR / "awf-neon-branch" / "scripts" / "neon_branch.py"
        result = subprocess.run(
            ["uv", "run", str(script), "--help"],
            env={**os.environ, "AWF_HOME": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "awf-neon-branch" in result.stdout

    def test_happy_path_create(self, tmp_path: Path, monkeypatch) -> None:
        """First run writes branch_id and branch_name to infra.json, action=created."""
        self._make_project_with_neon(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        fake_nc = _make_fake_neon_client()

        with patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["neon_branch", "--name", "preview"])
            rc = main()

        assert rc == 0
        infra = _read_infra(tmp_path)
        assert infra["neon"]["branch_id"] == "br-test-111"
        assert infra["neon"]["branch_name"] == "preview"

    def test_happy_path_create_json(self, tmp_path: Path, monkeypatch) -> None:
        """--json output has required keys."""
        self._make_project_with_neon(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        fake_nc = _make_fake_neon_client()

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        with patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["neon_branch", "--name", "preview", "--json"])
            with patch("builtins.print", side_effect=capturing_print):
                rc = main()

        assert rc == 0
        data = json.loads(output_lines[0])
        assert set(data.keys()) >= {"action", "branch_id", "branch_name", "project_id"}
        assert data["action"] == "created"

    def test_happy_path_skip(self, tmp_path: Path, monkeypatch) -> None:
        """Second run with same branch → action=skip."""
        self._make_project_with_neon(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        fake_nc = _make_fake_neon_client()

        with patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["neon_branch", "--name", "preview"])
            main()

        infra_before = _read_infra(tmp_path)

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        with patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main2 = self._main()
            monkeypatch.setattr(sys, "argv", ["neon_branch", "--name", "preview"])
            with patch("builtins.print", side_effect=capturing_print):
                rc = main2()

        assert rc == 0
        assert any("skip" in line for line in output_lines)
        assert _read_infra(tmp_path) == infra_before

    def test_no_project_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        """No .awf/project.json → exits 1."""
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["neon_branch", "--name", "preview"])
        rc = main()
        assert rc == 1

    def test_no_neon_project_id_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        """No neon.project_id in infra.json and no --project-id → exits 1."""
        _make_project(tmp_path)  # project exists but no infra.json / neon project_id
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["neon_branch", "--name", "preview"])
        rc = main()
        assert rc == 1

    def test_project_id_from_flag(self, tmp_path: Path, monkeypatch) -> None:
        """--project-id bypasses infra.json lookup."""
        _make_project(tmp_path)  # no infra neon project_id
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        fake_nc = _make_fake_neon_client()

        with patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main = self._main()
            monkeypatch.setattr(
                sys, "argv",
                ["neon_branch", "--name", "preview", "--project-id", "lucky-cloud-111111"],
            )
            rc = main()

        assert rc == 0
        infra = _read_infra(tmp_path)
        assert infra["neon"]["branch_id"] == "br-test-111"

    def test_missing_creds_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        """RuntimeError from NeonClient.from_env → exits 2."""
        self._make_project_with_neon(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        with patch("lib.neon.client.NeonClient.from_env", side_effect=RuntimeError("NEON_API_KEY missing")):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["neon_branch", "--name", "preview"])
            rc = main()

        assert rc == 2


# ===========================================================================
# awf-cf-dns-record tests
# ===========================================================================


class TestCfDnsRecord:
    """Tests for awf-cf-dns-record."""

    def _main(self):
        _inject_cloudflare_stub()
        return _import_main("awf-cf-dns-record", "cf_dns_record")

    def test_help_exits_0(self):
        """--help exits 0 (smoke check)."""
        script = _SKILLS_DIR / "awf-cf-dns-record" / "scripts" / "cf_dns_record.py"
        result = subprocess.run(
            ["uv", "run", str(script), "--help"],
            env={**os.environ, "AWF_HOME": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "awf-cf-dns-record" in result.stdout

    def _patch_cf_dns(self, monkeypatch, record_id: str = "rec-abc123"):
        """Patch get_client and create_dns_record to return a fake record.

        Must call _inject_cloudflare_stub() first to ensure lib.cf is importable.
        """
        _inject_cloudflare_stub()
        fake_cf, fake_record = _make_fake_cf_client()
        fake_record.id = record_id

        # Patch the script's imported get_client (after the module is loaded).
        # We use sys.modules manipulation since the script imports get_client at
        # module load time; we need to patch the reference the script holds.
        import lib.cf.client as cf_client_module
        monkeypatch.setattr(cf_client_module, "get_client", lambda **kw: fake_cf)

        import lib.cf.dns as dns_module
        monkeypatch.setattr(dns_module, "create_dns_record", lambda *a, **kw: fake_record)

        return fake_cf, fake_record

    def test_happy_path_create(self, tmp_path: Path, monkeypatch) -> None:
        """First run writes record_id to passport.cloudflare, action=created."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        self._patch_cf_dns(monkeypatch)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["cf_dns_record", "--type", "A", "--name", "api", "--content", "1.2.3.4"])
        rc = main()

        assert rc == 0
        passport = _read_passport(tmp_path)
        assert "cloudflare" in passport
        assert passport["cloudflare"]["A:api"] == "rec-abc123"

    def test_happy_path_create_json(self, tmp_path: Path, monkeypatch) -> None:
        """--json output has required keys."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        self._patch_cf_dns(monkeypatch)

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["cf_dns_record", "--type", "A", "--name", "api", "--content", "1.2.3.4", "--json"])
        with patch("builtins.print", side_effect=capturing_print):
            rc = main()

        assert rc == 0
        data = json.loads(output_lines[0])
        assert set(data.keys()) >= {"action", "record_id", "type", "name", "content"}
        assert data["action"] == "created"
        assert data["record_id"] == "rec-abc123"

    def test_happy_path_skip(self, tmp_path: Path, monkeypatch) -> None:
        """Second run with same record ID → action=skip, passport unchanged."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        self._patch_cf_dns(monkeypatch)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["cf_dns_record", "--type", "A", "--name", "api", "--content", "1.2.3.4"])
        main()

        passport_before = _read_passport(tmp_path)

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        main2 = self._main()
        monkeypatch.setattr(sys, "argv", ["cf_dns_record", "--type", "A", "--name", "api", "--content", "1.2.3.4"])
        with patch("builtins.print", side_effect=capturing_print):
            rc = main2()

        assert rc == 0
        assert any("skip" in line for line in output_lines)
        assert _read_passport(tmp_path) == passport_before

    def test_cloudflare_roundtrip(self, tmp_path: Path, monkeypatch) -> None:
        """passport.cloudflare field round-trips: written on save, loaded on reload."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        self._patch_cf_dns(monkeypatch)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["cf_dns_record", "--type", "A", "--name", "test", "--content", "5.6.7.8"])
        rc = main()
        assert rc == 0

        # Reload passport and verify cloudflare field is present and correct
        passport_data = _read_passport(tmp_path)
        assert passport_data["cloudflare"]["A:test"] == "rec-abc123"

        # Import Passport and load to verify round-trip
        from lib.passport import Passport
        passport = Passport.load(tmp_path)
        assert isinstance(passport.cloudflare, dict)
        assert passport.cloudflare["A:test"] == "rec-abc123"

    def test_no_project_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        """No .awf/project.json → exits 1."""
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["cf_dns_record", "--type", "A", "--name", "api", "--content", "1.2.3.4"])
        rc = main()
        assert rc == 1

    def test_missing_creds_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        """RuntimeError from get_client → exits 2."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        _inject_cloudflare_stub()

        import lib.cf.client as cf_client_module

        def _raise(**kw):
            raise RuntimeError("credentials missing")

        monkeypatch.setattr(cf_client_module, "get_client", _raise)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["cf_dns_record", "--type", "A", "--name", "api", "--content", "1.2.3.4"])
        rc = main()
        assert rc == 2


# ===========================================================================
# awf-shared-infra-get tests
# ===========================================================================


class TestSharedInfraGet:
    """Tests for awf-shared-infra-get."""

    def _main(self):
        return _import_main("awf-shared-infra-get", "shared_infra_get")

    def _patch_shared_path(self, monkeypatch, shared_dir: Path):
        """Redirect Shared.load_or_create() to use shared_dir instead of ~/.config/awf."""
        import lib.awf_home as awf_home_mod
        monkeypatch.setattr(awf_home_mod, "user_config_dir", lambda: shared_dir)

    def test_help_exits_0(self):
        """--help exits 0 (smoke check)."""
        script = _SKILLS_DIR / "awf-shared-infra-get" / "scripts" / "shared_infra_get.py"
        result = subprocess.run(
            ["uv", "run", str(script), "--help"],
            env={**os.environ, "AWF_HOME": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "awf-shared-infra-get" in result.stdout

    def test_happy_path_create(self, tmp_path: Path, monkeypatch) -> None:
        """First run creates play_server and play_neon_project_id in shared.json."""
        shared_dir = tmp_path / "shared-config"
        shared_dir.mkdir()
        _patch_env(monkeypatch, tmp_path)
        self._patch_shared_path(monkeypatch, shared_dir)

        fake_hz = _make_fake_hetzner_client()
        fake_nc = _make_fake_neon_client()

        with patch("lib.hetzner.client.HetznerClient.from_env", return_value=fake_hz), \
             patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["shared_infra_get"])
            rc = main()

        assert rc == 0
        shared_path = shared_dir / "shared.json"
        assert shared_path.exists()
        data = json.loads(shared_path.read_text())
        assert data["play_server"]["hetzner_id"] == "42"
        assert data["play_server"]["ip"] == "1.2.3.4"
        assert data["play_neon_project_id"] == "lucky-cloud-111111"

    def test_happy_path_create_json(self, tmp_path: Path, monkeypatch) -> None:
        """--json output has required keys."""
        shared_dir = tmp_path / "shared-config"
        shared_dir.mkdir()
        _patch_env(monkeypatch, tmp_path)
        self._patch_shared_path(monkeypatch, shared_dir)

        fake_hz = _make_fake_hetzner_client()
        fake_nc = _make_fake_neon_client()

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        with patch("lib.hetzner.client.HetznerClient.from_env", return_value=fake_hz), \
             patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["shared_infra_get", "--json"])
            with patch("builtins.print", side_effect=capturing_print):
                rc = main()

        assert rc == 0
        data = json.loads(output_lines[0])
        assert set(data.keys()) >= {"action", "play_server", "play_neon_project_id"}
        assert data["action"] == "created"
        assert "ip" in data["play_server"]

    def test_happy_path_skip(self, tmp_path: Path, monkeypatch) -> None:
        """Second run → action=skip when both resources already exist."""
        shared_dir = tmp_path / "shared-config"
        shared_dir.mkdir()
        _patch_env(monkeypatch, tmp_path)
        self._patch_shared_path(monkeypatch, shared_dir)

        # Pre-populate shared.json
        shared_data = {
            "play_server": {
                "hetzner_id": "42",
                "ip": "1.2.3.4",
                "hostname": "awf-play",
                "registry": "ghcr.io",
                "created": "2026-06-01T00:00:00+00:00",
            },
            "play_neon_project_id": "lucky-cloud-111111",
            "default_registry": {"host": "ghcr.io", "user": ""},
        }
        (shared_dir / "shared.json").write_text(json.dumps(shared_data) + "\n", encoding="utf-8")

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        # No API clients needed — both resources already in shared.json
        with patch("lib.hetzner.client.HetznerClient.from_env", return_value=MagicMock()) as hz_mock, \
             patch("lib.neon.client.NeonClient.from_env", return_value=MagicMock()) as nc_mock:
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["shared_infra_get"])
            with patch("builtins.print", side_effect=capturing_print):
                rc = main()

            # Verify no API calls were made (both resources existed)
            hz_mock.return_value.servers.get_or_create.assert_not_called()
            nc_mock.return_value.projects.get_or_create.assert_not_called()

        assert rc == 0
        assert any("skip" in line for line in output_lines)

    def test_missing_hetzner_creds_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        """RuntimeError from HetznerClient.from_env → exits 2."""
        shared_dir = tmp_path / "shared-config"
        shared_dir.mkdir()
        _patch_env(monkeypatch, tmp_path)
        self._patch_shared_path(monkeypatch, shared_dir)

        with patch("lib.hetzner.client.HetznerClient.from_env", side_effect=RuntimeError("HETZNER_API_TOKEN missing")):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["shared_infra_get"])
            rc = main()

        assert rc == 2

    def test_missing_neon_creds_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        """RuntimeError from NeonClient.from_env → exits 2."""
        shared_dir = tmp_path / "shared-config"
        shared_dir.mkdir()
        _patch_env(monkeypatch, tmp_path)
        self._patch_shared_path(monkeypatch, shared_dir)

        fake_hz = _make_fake_hetzner_client()
        with patch("lib.hetzner.client.HetznerClient.from_env", return_value=fake_hz), \
             patch("lib.neon.client.NeonClient.from_env", side_effect=RuntimeError("NEON_API_KEY missing")):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["shared_infra_get"])
            rc = main()

        assert rc == 2

    def test_partial_action_when_only_neon_missing(self, tmp_path: Path, monkeypatch) -> None:
        """When server exists but Neon missing → action=partial after creation."""
        shared_dir = tmp_path / "shared-config"
        shared_dir.mkdir()
        _patch_env(monkeypatch, tmp_path)
        self._patch_shared_path(monkeypatch, shared_dir)

        # Pre-populate shared.json with server only (no neon project)
        shared_data = {
            "play_server": {
                "hetzner_id": "42",
                "ip": "1.2.3.4",
                "hostname": "awf-play",
                "registry": "ghcr.io",
                "created": "2026-06-01T00:00:00+00:00",
            },
            "play_neon_project_id": None,
            "default_registry": {"host": "ghcr.io", "user": ""},
        }
        (shared_dir / "shared.json").write_text(json.dumps(shared_data) + "\n", encoding="utf-8")

        fake_hz = _make_fake_hetzner_client()
        fake_nc = _make_fake_neon_client()

        output_lines = []
        real_print = print

        def capturing_print(*args, **kwargs):
            if kwargs.get("file") is None or kwargs.get("file") == sys.stdout:
                output_lines.append(" ".join(str(a) for a in args))
            else:
                real_print(*args, **kwargs)

        with patch("lib.hetzner.client.HetznerClient.from_env", return_value=fake_hz), \
             patch("lib.neon.client.NeonClient.from_env", return_value=fake_nc):
            main = self._main()
            monkeypatch.setattr(sys, "argv", ["shared_infra_get"])
            with patch("builtins.print", side_effect=capturing_print):
                rc = main()

        assert rc == 0
        assert any("partial" in line for line in output_lines)


# ===========================================================================
# lib/passport.py cloudflare field tests (plan-008 T3 condition)
# ===========================================================================


class TestPassportCloudflareField:
    """Tests for Passport.cloudflare field round-trip (plan-008 T3 condition)."""

    def test_cloudflare_field_defaults_empty(self) -> None:
        """Passport.cloudflare defaults to empty dict."""
        from lib.passport import Passport
        p = Passport.new(domain="example.com")
        assert p.cloudflare == {}

    def test_cloudflare_field_roundtrip(self, tmp_path: Path) -> None:
        """cloudflare dict round-trips through save/load."""
        from lib.passport import Passport
        _make_project(tmp_path)

        p = Passport.load(tmp_path)
        p.cloudflare = {"A:api": "rec-abc", "TXT:@": "rec-xyz"}
        p.save(tmp_path)

        reloaded = Passport.load(tmp_path)
        assert isinstance(reloaded.cloudflare, dict)
        assert reloaded.cloudflare["A:api"] == "rec-abc"
        assert reloaded.cloudflare["TXT:@"] == "rec-xyz"

    def test_cloudflare_field_survives_other_save(self, tmp_path: Path) -> None:
        """cloudflare data persists when passport is saved again (no data loss)."""
        from lib.passport import Passport
        _make_project(tmp_path)

        # Write cloudflare data
        p = Passport.load(tmp_path)
        p.cloudflare = {"A:www": "rec-111"}
        p.save(tmp_path)

        # Reload, modify something else, save again
        p2 = Passport.load(tmp_path)
        p2.site_name = "My Site"
        p2.save(tmp_path)

        # Verify cloudflare data survives
        p3 = Passport.load(tmp_path)
        assert p3.cloudflare == {"A:www": "rec-111"}
        assert p3.site_name == "My Site"

    def test_cloudflare_field_with_dns_records_list(self, tmp_path: Path) -> None:
        """cloudflare field supports multiple record types and names."""
        from lib.passport import Passport
        _make_project(tmp_path)

        p = Passport.load(tmp_path)
        records = {
            "A:api": "rec-001",
            "A:staging": "rec-002",
            "CNAME:www": "rec-003",
            "TXT:@": "rec-004",
        }
        p.cloudflare = records
        p.save(tmp_path)

        reloaded = Passport.load(tmp_path)
        assert reloaded.cloudflare == records

    def test_old_passport_without_cloudflare_loads_cleanly(self, tmp_path: Path) -> None:
        """Passport JSON without cloudflare key loads with empty dict default."""
        from lib.passport import Passport

        # Write a passport without the cloudflare key (simulates pre-plan-008 passport)
        passport_data = dict(_MINIMAL_PASSPORT)
        # Ensure cloudflare is not in the data
        passport_data.pop("cloudflare", None)
        (tmp_path / "passport.json").write_text(json.dumps(passport_data) + "\n", encoding="utf-8")

        p = Passport.load(tmp_path)
        assert p.cloudflare == {}
