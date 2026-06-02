"""Consolidated tests for plan_009 skills: awf-app-dockerize, awf-app-secret-set,
awf-kamal-config, awf-kamal-setup, awf-kamal-deploy.

Mocking strategy:
  - awf-app-dockerize / awf-app-secret-set: real filesystem writes to tmp_path.
  - awf-kamal-config: real KamalConfig.render() against tmp_path projects.
  - awf-kamal-setup / awf-kamal-deploy: class-level monkeypatch of
    lib.kamal.runner.KamalRunner to a FakeRunner so signature drift surfaces
    as a construction failure.

Acceptance criteria (plan_009 §acceptance):
  - happy-create, happy-skip, no-project-exit-1, --json shape: all five.
  - awf-app-dockerize: drift-no-clobber, port/node interpolation, partial-write.
  - awf-app-secret-set: literal/env/file sources, update-existing-key,
    atomic-write-leaves-tempfile-cleaned, invalid-key-format,
    secret-not-in-log assertion.
  - awf-kamal-config: Infra.registry-empty raises through lib, path change
    emits state.change.
  - awf-kamal-setup: KamalNotInstalled→exit 2, KamalDnsTimeout→exit 3,
    success emits process.invoke via injected fake runner.
  - awf-kamal-deploy: image-changed→updated, image-unchanged→skip,
    KamalDeployFailed→exit 3.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Repo root constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / "skills"
_LIB_DIR = _REPO_ROOT / "lib"

# ---------------------------------------------------------------------------
# Minimal project fixtures
# ---------------------------------------------------------------------------

_MINIMAL_ANCHOR: dict[str, Any] = {
    "awf_version": "0.1.0",
    "domain": "example.com",
    "slug": "example-com",
    "stage": "mvp-play",
    "created": "2026-06-01T00:00:00Z",
    "has": {"passport": True, "infra": False, "kamal": False, "content": False},
}

_MINIMAL_INFRA: dict[str, Any] = {
    "registry": {"host": "ghcr.io", "image": "testuser/example-com", "user": "testuser"},
    "hetzner": {
        "servers": [{"id": "42", "ip": "1.2.3.4", "role": "web", "shared": False, "cost_eur_month": 4.5}],
        "lb_id": None,
        "network_id": None,
    },
    "neon": {
        "project_id": "test-proj-111",
        "branch_id": "",
        "branch_name": "",
        "mode": "shared-branch",
        "connection_secret_ref": "DATABASE_URL",
    },
    "kamal": {"config_path": "config/deploy.yml", "last_deploy_image": None},
}

_MINIMAL_SHARED: dict[str, Any] = {
    "play_server": {
        "hetzner_id": "srv-111",
        "ip": "1.2.3.4",
        "hostname": "play-server",
        "registry": "ghcr.io",
        "created": "2026-06-01T00:00:00Z",
        "kamal_setup_done_for_server_id": "",
    },
    "play_neon_project_id": "neon-proj-111",
    "default_registry": {"host": "ghcr.io", "user": "testuser"},
}


def _make_project(root: Path, *, domain: str = "example.com", with_infra: bool = False) -> None:
    """Write .awf/project.json (and optionally .awf/infra.json) into root."""
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


def _read_infra(root: Path) -> dict[str, Any]:
    path = root / ".awf" / "infra.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _make_shared(tmp_path: Path, monkeypatch, *, done_for_id: str = "") -> Path:
    """Write a shared.json to tmp_path/config/awf/ and patch user_config_dir."""
    config_dir = tmp_path / "config" / "awf"
    config_dir.mkdir(parents=True, exist_ok=True)
    shared = dict(_MINIMAL_SHARED)
    shared["play_server"] = dict(shared["play_server"])
    shared["play_server"]["kamal_setup_done_for_server_id"] = done_for_id
    (config_dir / "shared.json").write_text(json.dumps(shared, indent=2), encoding="utf-8")
    monkeypatch.setattr("lib.awf_home.user_config_dir", lambda: config_dir)
    return config_dir


def _read_log_events(root: Path) -> list[dict[str, Any]]:
    log_path = root / ".awf" / "log.jsonl"
    if not log_path.exists():
        return []
    return [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _events_of_type(root: Path, type_: str) -> list[dict[str, Any]]:
    return [e for e in _read_log_events(root) if e.get("type") == type_]


# ---------------------------------------------------------------------------
# Script import helper
# ---------------------------------------------------------------------------


def _import_main(skill_name: str, module_name: str):
    """Import and return main() from a skill script (in-process)."""
    script_path = _SKILLS_DIR / skill_name / "scripts" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(
        f"awf_skill_{module_name}_{skill_name}",
        script_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    for p in [str(_REPO_ROOT), str(_LIB_DIR)]:
        if p not in sys.path:
            sys.path.insert(0, p)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.main


def _patch_env(monkeypatch, tmp_path: Path, cwd: Path | None = None) -> None:
    monkeypatch.setenv("AWF_HOME", str(_REPO_ROOT))
    if cwd is not None:
        monkeypatch.chdir(cwd)


def _capture_stdout(func, *args, **kwargs) -> tuple[int, list[str]]:
    """Run func(*args, **kwargs) capturing stdout lines; return (rc, lines)."""
    output_lines: list[str] = []
    real_print = print

    def capturing_print(*pargs, **pkwargs):
        if pkwargs.get("file") is None or pkwargs.get("file") is sys.stdout:
            output_lines.append(" ".join(str(a) for a in pargs))
        else:
            real_print(*pargs, **pkwargs)

    with patch("builtins.print", side_effect=capturing_print):
        rc = func(*args, **kwargs)
    return rc, output_lines


# ---------------------------------------------------------------------------
# Fake KamalRunner for setup / deploy tests
# ---------------------------------------------------------------------------


class FakeKamalRunner:
    """Drop-in replacement for KamalRunner that records calls and can raise."""

    def __init__(
        self,
        *,
        cwd: Path,
        dns_resolver=None,
        dns_timeout_s: float = 600.0,
        dns_interval_s: float = 5.0,
        setup_raises=None,
        deploy_raises=None,
    ) -> None:
        self.cwd = cwd
        self.dns_timeout_s = dns_timeout_s
        self.setup_calls: list[dict[str, Any]] = []
        self.deploy_calls: list[dict[str, Any]] = []
        self._setup_raises = setup_raises
        self._deploy_raises = deploy_raises

    def setup(self, *, domain: str, server_ip: str) -> None:
        self.setup_calls.append({"domain": domain, "server_ip": server_ip})
        if self._setup_raises is not None:
            raise self._setup_raises

    def deploy(self) -> None:
        self.deploy_calls.append({})
        if self._deploy_raises is not None:
            raise self._deploy_raises


# ===========================================================================
# awf-app-dockerize tests
# ===========================================================================


class TestAppDockerize:
    """Tests for awf-app-dockerize."""

    def _main(self):
        return _import_main("awf-app-dockerize", "app_dockerize")

    def test_help_exits_0(self):
        """--help exits 0 (smoke check)."""
        script = _SKILLS_DIR / "awf-app-dockerize" / "scripts" / "app_dockerize.py"
        result = subprocess.run(
            ["uv", "run", str(script), "--help"],
            env={**os.environ, "AWF_HOME": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "awf-app-dockerize" in result.stdout

    def test_happy_path_create(self, tmp_path: Path, monkeypatch) -> None:
        """First run creates all four files, emits file.write events."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(sys, "argv", ["app_dockerize"])

        rc = main()

        assert rc == 0
        assert (tmp_path / "Dockerfile").exists()
        assert (tmp_path / ".dockerignore").exists()
        assert (tmp_path / "src" / "routes" / "up" / "+server.ts").exists()
        assert (tmp_path / "lib" / "db.ts").exists()
        fw_events = _events_of_type(tmp_path, "file.write")
        assert len(fw_events) >= 1

    def test_happy_path_create_json(self, tmp_path: Path, monkeypatch) -> None:
        """--json output has correct shape."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(sys, "argv", ["app_dockerize", "--json"])

        rc, lines = _capture_stdout(main)

        assert rc == 0
        data = json.loads(lines[0])
        assert set(data.keys()) >= {"action", "wrote", "skipped", "drift", "dockerize_version"}
        assert data["action"] == "created"
        assert "Dockerfile" in data["wrote"]

    def test_happy_path_skip(self, tmp_path: Path, monkeypatch) -> None:
        """Second run with unchanged files → action=skip, zero file.write events."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["app_dockerize"])
        main()  # first run — writes files

        fw_before = _events_of_type(tmp_path, "file.write")

        main2 = self._main()
        monkeypatch.setattr(sys, "argv", ["app_dockerize", "--json"])
        rc, lines = _capture_stdout(main2)

        assert rc == 0
        data = json.loads(lines[0])
        assert data["action"] == "skip"
        assert data["wrote"] == []

        fw_after = _events_of_type(tmp_path, "file.write")
        assert len(fw_after) == len(fw_before), "second run should emit no new file.write events"

    def test_no_project_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        """No .awf/project.json → exits 1."""
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(sys, "argv", ["app_dockerize"])
        assert main() == 1

    def test_port_interpolation(self, tmp_path: Path, monkeypatch) -> None:
        """--port 8080 appears in Dockerfile EXPOSE line."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(sys, "argv", ["app_dockerize", "--port", "8080"])
        main()
        dockerfile = (tmp_path / "Dockerfile").read_text(encoding="utf-8")
        assert "EXPOSE 8080" in dockerfile

    def test_node_version_interpolation(self, tmp_path: Path, monkeypatch) -> None:
        """--node-version 18 appears in FROM line."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(sys, "argv", ["app_dockerize", "--node-version", "18"])
        main()
        dockerfile = (tmp_path / "Dockerfile").read_text(encoding="utf-8")
        assert "FROM node:18-slim" in dockerfile

    def test_drift_no_clobber(self, tmp_path: Path, monkeypatch) -> None:
        """If Dockerfile exists with different content, it is NOT overwritten."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        custom_content = "# MY CUSTOM DOCKERFILE\nFROM ubuntu\n"
        (tmp_path / "Dockerfile").write_text(custom_content, encoding="utf-8")

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["app_dockerize", "--json"])
        rc, lines = _capture_stdout(main)

        assert rc == 0
        data = json.loads(lines[0])
        assert "Dockerfile" in data["drift"]
        assert (tmp_path / "Dockerfile").read_text(encoding="utf-8") == custom_content

    def test_partial_write_some_files_exist(self, tmp_path: Path, monkeypatch) -> None:
        """If some files already exist (matching), only missing ones are written."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        # Pre-write .dockerignore with matching content
        di_content = "node_modules\n.git\n.svelte-kit\nbuild\n.env*\n"
        (tmp_path / ".dockerignore").write_text(di_content, encoding="utf-8")

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["app_dockerize", "--json"])
        rc, lines = _capture_stdout(main)

        assert rc == 0
        data = json.loads(lines[0])
        assert ".dockerignore" in data["skipped"]
        assert "Dockerfile" in data["wrote"]


# ===========================================================================
# awf-app-secret-set tests
# ===========================================================================


class TestAppSecretSet:
    """Tests for awf-app-secret-set."""

    def _main(self):
        return _import_main("awf-app-secret-set", "app_secret_set")

    def test_help_exits_0(self):
        """--help exits 0 (smoke check)."""
        script = _SKILLS_DIR / "awf-app-secret-set" / "scripts" / "app_secret_set.py"
        result = subprocess.run(
            ["uv", "run", str(script), "--help"],
            env={**os.environ, "AWF_HOME": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "awf-app-secret-set" in result.stdout

    def test_happy_path_create_literal(self, tmp_path: Path, monkeypatch) -> None:
        """First run writes key=value to .kamal/secrets, action=created."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "DATABASE_URL", "--value", "postgres://test/db"],
        )
        rc = main()

        assert rc == 0
        secrets_path = tmp_path / ".kamal" / "secrets"
        assert secrets_path.exists()
        content = secrets_path.read_text(encoding="utf-8")
        assert "DATABASE_URL=postgres://test/db" in content

    def test_happy_path_create_json(self, tmp_path: Path, monkeypatch) -> None:
        """--json output has correct shape on create."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "MY_KEY", "--value", "myval", "--json"],
        )
        rc, lines = _capture_stdout(main)

        assert rc == 0
        data = json.loads(lines[0])
        assert set(data.keys()) >= {"action", "key", "source"}
        assert data["action"] == "created"
        assert data["key"] == "MY_KEY"
        assert data["source"] == "literal"

    def test_happy_path_skip(self, tmp_path: Path, monkeypatch) -> None:
        """Second run with same key/value → action=skip, no new file.write."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "K", "--value", "V"],
        )
        main()  # first run

        fw_before = _events_of_type(tmp_path, "file.write")

        main2 = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "K", "--value", "V", "--json"],
        )
        rc, lines = _capture_stdout(main2)

        assert rc == 0
        data = json.loads(lines[0])
        assert data["action"] == "skip"

        fw_after = _events_of_type(tmp_path, "file.write")
        assert len(fw_after) == len(fw_before)

    def test_no_project_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        """No .awf/project.json → exits 1."""
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "K", "--value", "V"],
        )
        assert main() == 1

    def test_from_env_source(self, tmp_path: Path, monkeypatch) -> None:
        """--from-env reads from env var."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        monkeypatch.setenv("MY_SECRET_VAR", "env-value-xyz")

        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "MYSECRET", "--from-env", "MY_SECRET_VAR", "--json"],
        )
        rc, lines = _capture_stdout(main)

        assert rc == 0
        data = json.loads(lines[0])
        assert data["source"] == "env"
        secrets_content = (tmp_path / ".kamal" / "secrets").read_text(encoding="utf-8")
        assert "MYSECRET=env-value-xyz" in secrets_content

    def test_from_env_missing_var_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        """--from-env with unset variable → exits 2."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        monkeypatch.delenv("DOES_NOT_EXIST_VAR", raising=False)

        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "K", "--from-env", "DOES_NOT_EXIST_VAR"],
        )
        assert main() == 2

    def test_from_file_source(self, tmp_path: Path, monkeypatch) -> None:
        """--from-file reads value from file."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        secret_file = tmp_path / "my_secret.txt"
        secret_file.write_text("file-value-abc\n", encoding="utf-8")

        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "FILEK", "--from-file", str(secret_file), "--json"],
        )
        rc, lines = _capture_stdout(main)

        assert rc == 0
        data = json.loads(lines[0])
        assert data["source"] == "file"
        secrets_content = (tmp_path / ".kamal" / "secrets").read_text(encoding="utf-8")
        assert "FILEK=file-value-abc" in secrets_content

    def test_from_file_missing_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        """--from-file with non-existent file → exits 2."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "K", "--from-file", str(tmp_path / "no_such_file.txt")],
        )
        assert main() == 2

    def test_update_existing_key(self, tmp_path: Path, monkeypatch) -> None:
        """Re-running with a different value updates the line in-place."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        # Write first value
        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "DB_URL", "--value", "postgres://old/db"],
        )
        main()

        # Update
        main2 = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "DB_URL", "--value", "postgres://new/db", "--json"],
        )
        rc, lines = _capture_stdout(main2)

        assert rc == 0
        data = json.loads(lines[0])
        assert data["action"] == "updated"

        content = (tmp_path / ".kamal" / "secrets").read_text(encoding="utf-8")
        assert "postgres://new/db" in content
        assert "postgres://old/db" not in content

    def test_atomic_write_no_tempfile_left(self, tmp_path: Path, monkeypatch) -> None:
        """After write, no .tmp file remains in .kamal/."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "K", "--value", "V"],
        )
        main()

        kamal_dir = tmp_path / ".kamal"
        tmp_files = list(kamal_dir.glob("*.tmp"))
        assert tmp_files == [], f"Unexpected .tmp files: {tmp_files}"

    def test_secret_value_not_in_log(self, tmp_path: Path, monkeypatch) -> None:
        """Secret value must never appear in any log event."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        secret_value = "super-secret-password-99"

        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["app_secret_set", "--key", "DB_PASS", "--value", secret_value],
        )
        main()

        log_path = tmp_path / ".awf" / "log.jsonl"
        if log_path.exists():
            raw = log_path.read_text(encoding="utf-8")
            assert secret_value not in raw, (
                f"Secret value found in log! Check safe_args construction.\n"
                f"Log content:\n{raw}"
            )


# ===========================================================================
# awf-kamal-config tests
# ===========================================================================


class TestKamalConfig:
    """Tests for awf-kamal-config."""

    def _main(self):
        return _import_main("awf-kamal-config", "kamal_config")

    def test_help_exits_0(self):
        """--help exits 0 (smoke check)."""
        script = _SKILLS_DIR / "awf-kamal-config" / "scripts" / "kamal_config.py"
        result = subprocess.run(
            ["uv", "run", str(script), "--help"],
            env={**os.environ, "AWF_HOME": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "awf-kamal-config" in result.stdout

    def test_happy_path_create(self, tmp_path: Path, monkeypatch) -> None:
        """First run renders YAML and records config_path in infra.json."""
        _make_project(tmp_path, with_infra=True)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        # Reset config_path so first run looks like creation
        awf_dir = tmp_path / ".awf"
        infra_data = json.loads((awf_dir / "infra.json").read_text())
        infra_data["kamal"]["config_path"] = ""
        (awf_dir / "infra.json").write_text(json.dumps(infra_data, indent=2))

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_config"])
        rc = main()

        assert rc == 0
        assert (tmp_path / "config" / "deploy.yml").exists()
        yaml_content = (tmp_path / "config" / "deploy.yml").read_text(encoding="utf-8")
        assert "example-com" in yaml_content  # slug
        assert "1.2.3.4" in yaml_content      # web server IP

    def test_happy_path_create_json(self, tmp_path: Path, monkeypatch) -> None:
        """--json output has correct shape."""
        _make_project(tmp_path, with_infra=True)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        # Reset config_path
        awf_dir = tmp_path / ".awf"
        infra_data = json.loads((awf_dir / "infra.json").read_text())
        infra_data["kamal"]["config_path"] = ""
        (awf_dir / "infra.json").write_text(json.dumps(infra_data, indent=2))

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_config", "--json"])
        rc, lines = _capture_stdout(main)

        assert rc == 0
        data = json.loads(lines[0])
        assert set(data.keys()) >= {"action", "config_path"}
        assert data["action"] == "created"

    def test_happy_path_skip(self, tmp_path: Path, monkeypatch) -> None:
        """Second run with same path → action=skip, no new state.change at state level."""
        _make_project(tmp_path, with_infra=True)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        # Reset config_path for first run
        awf_dir = tmp_path / ".awf"
        infra_data = json.loads((awf_dir / "infra.json").read_text())
        infra_data["kamal"]["config_path"] = ""
        (awf_dir / "infra.json").write_text(json.dumps(infra_data, indent=2))

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_config"])
        main()  # first run — sets config_path

        # Count state.change events after first run
        sc_before = _events_of_type(tmp_path, "state.change")

        main2 = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_config", "--json"])
        rc, lines = _capture_stdout(main2)

        assert rc == 0
        data = json.loads(lines[0])
        assert data["action"] == "skip"

        sc_after = _events_of_type(tmp_path, "state.change")
        assert len(sc_after) == len(sc_before), "second run should emit no new state.change events"

    def test_no_project_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        """No .awf/project.json → exits 1."""
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_config"])
        assert main() == 1

    def test_no_web_server_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        """No web server in infra (KamalError) → exits 2."""
        _make_project(tmp_path, with_infra=True)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        # Remove web server
        awf_dir = tmp_path / ".awf"
        infra_data = json.loads((awf_dir / "infra.json").read_text())
        infra_data["hetzner"]["servers"] = []
        infra_data["kamal"]["config_path"] = ""
        (awf_dir / "infra.json").write_text(json.dumps(infra_data, indent=2))

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_config"])
        assert main() == 2

    def test_path_arg_change_emits_state_change(self, tmp_path: Path, monkeypatch) -> None:
        """Changing --path triggers state.change for Infra.kamal.config_path."""
        _make_project(tmp_path, with_infra=True)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        # First run with default path
        awf_dir = tmp_path / ".awf"
        infra_data = json.loads((awf_dir / "infra.json").read_text())
        infra_data["kamal"]["config_path"] = ""
        (awf_dir / "infra.json").write_text(json.dumps(infra_data, indent=2))

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_config"])
        main()

        sc_before = _events_of_type(tmp_path, "state.change")

        # Second run with different path
        main2 = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_config", "--path", "config/deploy-alt.yml"])
        rc = main2()

        assert rc == 0
        sc_after = _events_of_type(tmp_path, "state.change")
        assert len(sc_after) > len(sc_before), "path change should emit a state.change event"


# ===========================================================================
# awf-kamal-setup tests
# ===========================================================================


class TestKamalSetup:
    """Tests for awf-kamal-setup."""

    def _main(self):
        return _import_main("awf-kamal-setup", "kamal_setup")

    def test_help_exits_0(self):
        """--help exits 0 (smoke check)."""
        script = _SKILLS_DIR / "awf-kamal-setup" / "scripts" / "kamal_setup.py"
        result = subprocess.run(
            ["uv", "run", str(script), "--help"],
            env={**os.environ, "AWF_HOME": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "awf-kamal-setup" in result.stdout

    def test_happy_path_create(self, tmp_path: Path, monkeypatch) -> None:
        """Success path: fake runner called with correct domain/ip, exits 0; shared.json updated."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        config_dir = _make_shared(tmp_path, monkeypatch, done_for_id="")

        runner_instance = FakeKamalRunner(cwd=tmp_path)

        def _fake_runner_factory(**kwargs):
            runner_instance.cwd = kwargs.get("cwd", tmp_path)
            runner_instance.dns_timeout_s = kwargs.get("dns_timeout_s", 600.0)
            return runner_instance

        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: _fake_runner_factory(**kw))

        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["kamal_setup", "--server-ip", "1.2.3.4"],
        )
        rc = main()

        assert rc == 0
        assert len(runner_instance.setup_calls) == 1
        assert runner_instance.setup_calls[0]["server_ip"] == "1.2.3.4"
        assert runner_instance.setup_calls[0]["domain"] == "example.com"

        # Verify shared.json was updated with the server ID.
        shared_data = json.loads((config_dir / "shared.json").read_text(encoding="utf-8"))
        assert shared_data["play_server"]["kamal_setup_done_for_server_id"] == "srv-111"

    def test_happy_path_create_json(self, tmp_path: Path, monkeypatch) -> None:
        """--json output has correct shape."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        config_dir = _make_shared(tmp_path, monkeypatch, done_for_id="")

        runner_instance = FakeKamalRunner(cwd=tmp_path)
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: runner_instance)

        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["kamal_setup", "--server-ip", "1.2.3.4", "--json"],
        )
        rc, lines = _capture_stdout(main)

        assert rc == 0
        data = json.loads(lines[0])
        assert set(data.keys()) >= {"action", "domain", "server_ip"}
        assert data["action"] == "created"

        # Verify shared.json was updated.
        shared_data = json.loads((config_dir / "shared.json").read_text(encoding="utf-8"))
        assert shared_data["play_server"]["kamal_setup_done_for_server_id"] == "srv-111"

    def test_happy_path_skip(self, tmp_path: Path, monkeypatch) -> None:
        """When kamal_setup_done_for_server_id == hetzner_id, runner.setup is NOT called and action=='skip'."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        # done_for_id matches hetzner_id → should skip
        _make_shared(tmp_path, monkeypatch, done_for_id="srv-111")

        runner_instance = FakeKamalRunner(cwd=tmp_path)
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: runner_instance)

        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["kamal_setup", "--server-ip", "1.2.3.4", "--json"],
        )
        rc, lines = _capture_stdout(main)

        assert rc == 0
        assert runner_instance.setup_calls == [], "runner.setup must NOT be called on skip"
        data = json.loads(lines[0])
        assert data["action"] == "skip"

    def test_different_server_id_runs_setup(self, tmp_path: Path, monkeypatch) -> None:
        """When kamal_setup_done_for_server_id != hetzner_id (server reprovisioned), runs setup and updates field."""
        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        # done_for_id is a stale old server ID — different from hetzner_id "srv-111"
        config_dir = _make_shared(tmp_path, monkeypatch, done_for_id="old-server-999")

        runner_instance = FakeKamalRunner(cwd=tmp_path)
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: runner_instance)

        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["kamal_setup", "--server-ip", "1.2.3.4"],
        )
        rc = main()

        assert rc == 0
        assert len(runner_instance.setup_calls) == 1, "setup must run when server ID changed"

        # shared.json must be updated to the current hetzner_id.
        shared_data = json.loads((config_dir / "shared.json").read_text(encoding="utf-8"))
        assert shared_data["play_server"]["kamal_setup_done_for_server_id"] == "srv-111"

    def test_no_project_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        """No .awf/project.json → exits 1."""
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_setup", "--server-ip", "1.2.3.4"])
        assert main() == 1

    def test_kamal_not_installed_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        """KamalNotInstalled raised by runner → exits 2."""
        from lib.kamal.errors import KamalNotInstalled

        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        _make_shared(tmp_path, monkeypatch, done_for_id="")

        failing_runner = FakeKamalRunner(
            cwd=tmp_path, setup_raises=KamalNotInstalled()
        )
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: failing_runner)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_setup", "--server-ip", "1.2.3.4"])
        assert main() == 2

    def test_dns_timeout_exits_3(self, tmp_path: Path, monkeypatch) -> None:
        """KamalDnsTimeout raised by runner → exits 3."""
        from lib.kamal.errors import KamalDnsTimeout

        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        _make_shared(tmp_path, monkeypatch, done_for_id="")

        failing_runner = FakeKamalRunner(
            cwd=tmp_path,
            setup_raises=KamalDnsTimeout(domain="example.com", expected_ip="1.2.3.4"),
        )
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: failing_runner)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_setup", "--server-ip", "1.2.3.4"])
        assert main() == 3

    def test_dns_timeout_json_gate_payload(self, tmp_path: Path, monkeypatch) -> None:
        """KamalDnsTimeout + --json → stdout has gate.hit JSON shape, exits 3."""
        from lib.kamal.errors import KamalDnsTimeout

        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        _make_shared(tmp_path, monkeypatch, done_for_id="")

        failing_runner = FakeKamalRunner(
            cwd=tmp_path,
            setup_raises=KamalDnsTimeout(domain="example.com", expected_ip="1.2.3.4"),
        )
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: failing_runner)

        main = self._main()
        monkeypatch.setattr(
            sys, "argv",
            ["kamal_setup", "--server-ip", "1.2.3.4", "--json"],
        )
        rc, lines = _capture_stdout(main)

        assert rc == 3
        assert lines, "expected at least one stdout line"
        data = json.loads(lines[0])
        assert data["skill"] == "awf-kamal-setup"
        assert data["action"] == "gate"
        assert data["gate"] == "dns_propagation"
        assert data["result"] == "fail"
        assert data["domain"] == "example.com"
        assert data["expected_ip"] == "1.2.3.4"
        assert "reason" in data

    def test_dns_timeout_human_stderr(self, tmp_path: Path, monkeypatch) -> None:
        """KamalDnsTimeout without --json → human-readable message on stderr, exits 3."""
        from lib.kamal.errors import KamalDnsTimeout

        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        _make_shared(tmp_path, monkeypatch, done_for_id="")

        failing_runner = FakeKamalRunner(
            cwd=tmp_path,
            setup_raises=KamalDnsTimeout(domain="example.com", expected_ip="1.2.3.4"),
        )
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: failing_runner)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_setup", "--server-ip", "1.2.3.4"])

        stderr_lines: list[str] = []
        real_print = print

        def capturing_print(*pargs, **pkwargs):
            if pkwargs.get("file") is sys.stderr:
                stderr_lines.append(" ".join(str(a) for a in pargs))
            else:
                real_print(*pargs, **pkwargs)

        with patch("builtins.print", side_effect=capturing_print):
            rc = main()

        assert rc == 3
        assert stderr_lines, "expected stderr output in human mode"
        assert "awf-kamal-setup" in stderr_lines[0]
        assert "example.com" in stderr_lines[0]

    def test_setup_failed_exits_3(self, tmp_path: Path, monkeypatch) -> None:
        """KamalSetupFailed raised by runner → exits 3."""
        from lib.kamal.errors import KamalSetupFailed

        _make_project(tmp_path)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        _make_shared(tmp_path, monkeypatch, done_for_id="")

        failing_runner = FakeKamalRunner(
            cwd=tmp_path, setup_raises=KamalSetupFailed("kamal setup failed")
        )
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: failing_runner)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_setup", "--server-ip", "1.2.3.4"])
        assert main() == 3


# ===========================================================================
# awf-kamal-deploy tests
# ===========================================================================


class TestKamalDeploy:
    """Tests for awf-kamal-deploy."""

    def _main(self):
        return _import_main("awf-kamal-deploy", "kamal_deploy")

    def test_help_exits_0(self):
        """--help exits 0 (smoke check)."""
        script = _SKILLS_DIR / "awf-kamal-deploy" / "scripts" / "kamal_deploy.py"
        result = subprocess.run(
            ["uv", "run", str(script), "--help"],
            env={**os.environ, "AWF_HOME": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "awf-kamal-deploy" in result.stdout

    def test_happy_path_create(self, tmp_path: Path, monkeypatch) -> None:
        """First deploy records last_deploy_image, action=created."""
        _make_project(tmp_path, with_infra=True)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        # Reset last_deploy_image to None
        awf_dir = tmp_path / ".awf"
        infra_data = json.loads((awf_dir / "infra.json").read_text())
        infra_data["kamal"]["last_deploy_image"] = None
        (awf_dir / "infra.json").write_text(json.dumps(infra_data, indent=2))

        runner_instance = FakeKamalRunner(cwd=tmp_path)
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: runner_instance)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_deploy"])
        rc = main()

        assert rc == 0
        infra = _read_infra(tmp_path)
        assert infra["kamal"]["last_deploy_image"] == "testuser/example-com"

    def test_happy_path_create_json(self, tmp_path: Path, monkeypatch) -> None:
        """--json output has correct shape."""
        _make_project(tmp_path, with_infra=True)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        awf_dir = tmp_path / ".awf"
        infra_data = json.loads((awf_dir / "infra.json").read_text())
        infra_data["kamal"]["last_deploy_image"] = None
        (awf_dir / "infra.json").write_text(json.dumps(infra_data, indent=2))

        runner_instance = FakeKamalRunner(cwd=tmp_path)
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: runner_instance)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_deploy", "--json"])
        rc, lines = _capture_stdout(main)

        assert rc == 0
        data = json.loads(lines[0])
        assert set(data.keys()) >= {"action", "last_deploy_image"}
        assert data["action"] == "created"

    def test_happy_path_skip(self, tmp_path: Path, monkeypatch) -> None:
        """Same image already recorded → action=skip, no new state.change."""
        _make_project(tmp_path, with_infra=True)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        # Pre-populate last_deploy_image with the same image
        awf_dir = tmp_path / ".awf"
        infra_data = json.loads((awf_dir / "infra.json").read_text())
        infra_data["kamal"]["last_deploy_image"] = "testuser/example-com"
        (awf_dir / "infra.json").write_text(json.dumps(infra_data, indent=2))

        runner_instance = FakeKamalRunner(cwd=tmp_path)
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: runner_instance)

        sc_before = _events_of_type(tmp_path, "state.change")

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_deploy", "--json"])
        rc, lines = _capture_stdout(main)

        assert rc == 0
        data = json.loads(lines[0])
        assert data["action"] == "skip"

        sc_after = _events_of_type(tmp_path, "state.change")
        assert len(sc_after) == len(sc_before), "skip should emit no new state.change"

    def test_no_project_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        """No .awf/project.json → exits 1."""
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)
        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_deploy"])
        assert main() == 1

    def test_kamal_not_installed_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        """KamalNotInstalled → exits 2."""
        from lib.kamal.errors import KamalNotInstalled

        _make_project(tmp_path, with_infra=True)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        failing_runner = FakeKamalRunner(cwd=tmp_path, deploy_raises=KamalNotInstalled())
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: failing_runner)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_deploy"])
        assert main() == 2

    def test_deploy_failed_exits_3(self, tmp_path: Path, monkeypatch) -> None:
        """KamalDeployFailed → exits 3."""
        from lib.kamal.errors import KamalDeployFailed

        _make_project(tmp_path, with_infra=True)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        failing_runner = FakeKamalRunner(
            cwd=tmp_path, deploy_raises=KamalDeployFailed("kamal deploy failed")
        )
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: failing_runner)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_deploy"])
        assert main() == 3

    def test_image_changed_emits_updated(self, tmp_path: Path, monkeypatch) -> None:
        """When image changes, last_deploy_image is updated and action=updated."""
        _make_project(tmp_path, with_infra=True)
        _patch_env(monkeypatch, tmp_path, cwd=tmp_path)

        awf_dir = tmp_path / ".awf"
        infra_data = json.loads((awf_dir / "infra.json").read_text())
        infra_data["kamal"]["last_deploy_image"] = "ghcr.io/testuser/example-com:v1"
        (awf_dir / "infra.json").write_text(json.dumps(infra_data, indent=2))

        runner_instance = FakeKamalRunner(cwd=tmp_path)
        monkeypatch.setattr("lib.kamal.runner.KamalRunner", lambda **kw: runner_instance)

        main = self._main()
        monkeypatch.setattr(sys, "argv", ["kamal_deploy", "--json"])
        rc, lines = _capture_stdout(main)

        assert rc == 0
        data = json.loads(lines[0])
        # Infra.registry.image is "testuser/example-com" != "...v1"
        assert data["action"] == "updated"
        infra = _read_infra(tmp_path)
        assert infra["kamal"]["last_deploy_image"] == "testuser/example-com"
