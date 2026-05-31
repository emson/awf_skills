"""Tests for lib/kamal — KamalConfig, KamalRunner, DNS gate, logging, errors.

20-test matrix per plan_007 § "Test plan":
  KamalConfig.render            5
  _run_kamal chokepoint         3
  KamalRunner.setup             4
  deploy / rollback / app_logs  4
  Logging + structural          3
  Error hint heuristics         1 (parametrised, counts as 1 group)
  Total                        20 (parametrised cases expand the run count)

All tests are unit-level:
  - KamalConfig tests have no subprocess calls (pure render).
  - KamalRunner tests monkeypatch subprocess.run at the lib.kamal.runner module
    boundary, exercising the real _run_kamal, error translation, and DNS gate.
  - DNS tests use FakeResolver — no network I/O.

References:
  - docs/plans/plan_007_s3_kamal_lib.md § "Test plan"
  - docs/spec.md § B3
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lib.kamal import (
    KamalConfig,
    KamalDeployFailed,
    KamalDnsTimeout,
    KamalError,
    KamalNotInstalled,
    KamalRunner,
    KamalSetupFailed,
)
from lib.kamal.dns import FakeResolver
from lib.kamal.errors import _hint_from_stderr
from lib.state import Hetzner, Infra, Kamal, Neon, ProjectAnchor, Registry, Server
from lib.state import HasFlags


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "kamal"


def _make_anchor(
    *,
    domain: str = "example.com",
    slug: str = "example",
    stage: str = "mvp-play",
) -> ProjectAnchor:
    """Build a minimal ProjectAnchor for tests."""
    return ProjectAnchor(
        domain=domain,
        slug=slug,
        stage=stage,  # type: ignore[arg-type]
        created="2026-06-01T00:00:00Z",
        has=HasFlags(passport=True, infra=True, kamal=True),
    )


def _make_infra(
    *,
    registry_host: str = "ghcr.io",
    registry_user: str = "user",
    server_ip: str = "1.2.3.4",
    server_role: str = "web",
    secret_ref: str = "DATABASE_URL",
) -> Infra:
    """Build a minimal Infra for tests."""
    return Infra(
        registry=Registry(
            host=registry_host,
            image=f"{registry_user}/example",
            user=registry_user,
        ),
        hetzner=Hetzner(
            servers=[
                Server(id="123", ip=server_ip, role=server_role, shared=True, cost_eur_month=0)
            ]
        ),
        neon=Neon(
            project_id="neon_proj_xxx",
            branch_id="br_xxx",
            branch_name="example",
            mode="shared-branch",
            connection_secret_ref=secret_ref,
        ),
        kamal=Kamal(config_path="config/deploy.yml"),
    )


def _make_cp(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> "subprocess.CompletedProcess[str]":
    """Build a fake CompletedProcess."""
    cp: subprocess.CompletedProcess[str] = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def _make_runner(
    *,
    cwd: Path | None = None,
    resolver: Any = None,
    dns_timeout_s: float = 1.0,
    dns_interval_s: float = 0.0,
) -> KamalRunner:
    """Build a KamalRunner with a FakeResolver (no real DNS)."""
    if resolver is None:
        resolver = FakeResolver(sequence=[True])
    return KamalRunner(
        cwd=cwd or Path("/tmp/test-project"),
        dns_resolver=resolver,
        dns_timeout_s=dns_timeout_s,
        dns_interval_s=dns_interval_s,
    )


# ---------------------------------------------------------------------------
# Group 1 — KamalConfig.render (5 tests)
# ---------------------------------------------------------------------------


class TestKamalConfigRender:
    def test_render_matches_golden_fixture(self) -> None:
        """render() output must be byte-equal to the golden fixture."""
        anchor = _make_anchor()
        infra = _make_infra()
        cfg = KamalConfig(anchor, infra)
        result = cfg.render()
        expected = (FIXTURES_DIR / "deploy_v1.yml").read_text(encoding="utf-8")
        assert result == expected, (
            "render() output does not match golden fixture deploy_v1.yml.\n"
            "If the YAML schema changed legitimately, bump to deploy_v2.yml "
            "and update FIXTURE_VERSION in config.py; never edit v1 in place."
        )

    def test_render_is_diff_stable(self) -> None:
        """Two calls with the same (anchor, infra) produce byte-identical output."""
        anchor = _make_anchor()
        infra = _make_infra()
        cfg = KamalConfig(anchor, infra)
        first = cfg.render()
        second = cfg.render()
        assert first == second

    def test_render_ghcr_host_uses_ghcr_token(self) -> None:
        """GHCR host produces GHCR_TOKEN password reference."""
        anchor = _make_anchor()
        infra = _make_infra(registry_host="ghcr.io")
        result = KamalConfig(anchor, infra).render()
        assert 'ENV["GHCR_TOKEN"]' in result
        assert 'ENV["DOCKER_PASSWORD"]' not in result

    def test_render_non_ghcr_host_uses_docker_password(self) -> None:
        """Non-GHCR host produces DOCKER_PASSWORD password reference."""
        anchor = _make_anchor()
        infra = _make_infra(registry_host="registry.hub.docker.com")
        result = KamalConfig(anchor, infra).render()
        assert 'ENV["DOCKER_PASSWORD"]' in result
        assert 'ENV["GHCR_TOKEN"]' not in result

    def test_render_missing_web_server_raises(self) -> None:
        """Missing web server raises KamalError with descriptive message."""
        anchor = _make_anchor()
        infra = _make_infra(server_role="worker")  # no "web" role
        with pytest.raises(KamalError, match="no web server in infra"):
            KamalConfig(anchor, infra).render()


# ---------------------------------------------------------------------------
# Group 2 — _run_kamal chokepoint (3 tests)
# ---------------------------------------------------------------------------


class TestRunKamalChokepoint:
    def test_success_emits_one_process_event(self, tmp_path: Path, capfd: Any) -> None:
        """Success path: exactly one process.invoke event emitted, no error event."""
        runner = _make_runner(cwd=tmp_path)
        events: list[dict[str, Any]] = []

        def capture(record: dict[str, Any]) -> None:
            events.append(record)

        with patch("lib.log._write_event", side_effect=capture):
            with patch("subprocess.run", return_value=_make_cp(returncode=0, stdout="ok")) as mock_run:
                runner._run_kamal(["deploy"])
                mock_run.assert_called_once()

        process_events = [e for e in events if e.get("type") == "process.invoke"]
        assert len(process_events) == 1
        data = process_events[0]["data"]
        assert data["cmd"] == ["kamal", "deploy"]
        assert data["exit_code"] == 0
        assert data["cwd"] == str(tmp_path)

    def test_failure_emits_process_then_error_event(self, tmp_path: Path) -> None:
        """Failure path: process.invoke then error event; raises KamalError."""
        runner = _make_runner(cwd=tmp_path)
        events: list[dict[str, Any]] = []

        with patch("lib.log._write_event", side_effect=lambda r: events.append(r)):
            with patch(
                "subprocess.run",
                return_value=_make_cp(returncode=1, stderr="missing secret FOO"),
            ):
                with pytest.raises(KamalDeployFailed):
                    runner._run_kamal(["deploy"])

        types = [e.get("type") for e in events]
        assert "process.invoke" in types
        assert "error" in types
        # process.invoke must come before error
        assert types.index("process.invoke") < types.index("error")

    def test_file_not_found_raises_not_installed(self, tmp_path: Path) -> None:
        """FileNotFoundError from subprocess translates to KamalNotInstalled."""
        runner = _make_runner(cwd=tmp_path)
        with patch("subprocess.run", side_effect=FileNotFoundError("kamal: not found")):
            with pytest.raises(KamalNotInstalled):
                runner._run_kamal(["deploy"])


# ---------------------------------------------------------------------------
# Group 3 — KamalRunner.setup (4 tests)
# ---------------------------------------------------------------------------


class TestKamalRunnerSetup:
    def test_setup_resolves_immediately(self, tmp_path: Path) -> None:
        """DNS resolves on first poll: kamal setup is invoked once."""
        runner = _make_runner(cwd=tmp_path, resolver=FakeResolver(sequence=[True]))
        with patch("subprocess.run", return_value=_make_cp(returncode=0)) as mock_run:
            runner.setup(domain="example.com", server_ip="1.2.3.4")
        # Only one subprocess call: kamal setup
        assert mock_run.call_count == 1
        call_args = mock_run.call_args[0][0]
        assert call_args == ["kamal", "setup"]

    def test_setup_resolves_after_polls(self, tmp_path: Path) -> None:
        """DNS resolves after multiple FakeResolver ticks: kamal setup still called once."""
        # FakeResolver sequence: False, True — second call resolves
        # But FakeResolver.wait_for is called once (it handles the whole poll loop).
        # We need a resolver that returns False on first wait_for then True on second.
        # Since FakeResolver.wait_for pops one item per call, we give it [False, True]
        # but KamalRunner.setup() calls wait_for only once.
        # To test multi-poll behaviour we subclass FakeResolver.
        class TwoTickResolver:
            def __init__(self) -> None:
                self.calls = 0

            def wait_for(self, *, domain: str, expected_ip: str, timeout_s: float, interval_s: float) -> bool:
                self.calls += 1
                # Simulate resolving on the second overall wait_for call
                return self.calls >= 2

        resolver = TwoTickResolver()
        runner = _make_runner(cwd=tmp_path, resolver=resolver)
        with patch("subprocess.run", return_value=_make_cp(returncode=0)) as mock_run:
            # First setup call: DNS not ready yet → KamalDnsTimeout
            with pytest.raises(KamalDnsTimeout):
                runner.setup(domain="example.com", server_ip="1.2.3.4")
            # Second setup call: DNS ready → invokes kamal setup
            runner.setup(domain="example.com", server_ip="1.2.3.4")
        assert mock_run.call_count == 1

    def test_setup_dns_timeout_emits_gate_and_raises(self, tmp_path: Path) -> None:
        """DNS timeout: gate.hit emitted with name=dns_propagation; kamal never called."""
        runner = _make_runner(cwd=tmp_path, resolver=FakeResolver(sequence=[False]))
        events: list[dict[str, Any]] = []

        with patch("lib.log._write_event", side_effect=lambda r: events.append(r)):
            with patch("subprocess.run") as mock_run:
                with pytest.raises(KamalDnsTimeout) as exc_info:
                    runner.setup(domain="example.com", server_ip="1.2.3.4")
                mock_run.assert_not_called()

        gate_events = [e for e in events if e.get("type") == "gate.hit"]
        assert len(gate_events) == 1
        assert gate_events[0]["data"]["gate_name"] == "dns_propagation"
        assert exc_info.value.domain == "example.com"
        assert exc_info.value.expected_ip == "1.2.3.4"

    def test_setup_kamal_fails_raises_setup_failed(self, tmp_path: Path) -> None:
        """kamal setup exits non-zero → KamalSetupFailed with stderr attached."""
        runner = _make_runner(cwd=tmp_path, resolver=FakeResolver(sequence=[True]))
        with patch(
            "subprocess.run",
            return_value=_make_cp(returncode=1, stderr="connection refused port 22"),
        ):
            with pytest.raises(KamalSetupFailed) as exc_info:
                runner.setup(domain="example.com", server_ip="1.2.3.4")
        assert "connection refused" in exc_info.value.stderr


# ---------------------------------------------------------------------------
# Group 4 — deploy / rollback / app_logs (4 tests)
# ---------------------------------------------------------------------------


class TestDeployRollbackAppLogs:
    def test_deploy_success(self, tmp_path: Path) -> None:
        """deploy() success: subprocess called with ['kamal', 'deploy']."""
        runner = _make_runner(cwd=tmp_path)
        with patch("subprocess.run", return_value=_make_cp(returncode=0)) as mock_run:
            runner.deploy()
        args = mock_run.call_args[0][0]
        assert args == ["kamal", "deploy"]

    def test_deploy_failure_with_dockerfile_hint(self, tmp_path: Path) -> None:
        """deploy() failure: KamalDeployFailed with Dockerfile hint heuristic."""
        runner = _make_runner(cwd=tmp_path)
        with patch(
            "subprocess.run",
            return_value=_make_cp(returncode=1, stderr="no such file or directory: Dockerfile"),
        ):
            with pytest.raises(KamalDeployFailed) as exc_info:
                runner.deploy()
        assert "awf-app-dockerize" in exc_info.value.hint
        assert exc_info.value.stderr != ""

    def test_rollback_with_explicit_tag(self, tmp_path: Path) -> None:
        """rollback(to='sha256:abc') passes the tag as a positional argument."""
        runner = _make_runner(cwd=tmp_path)
        with patch("subprocess.run", return_value=_make_cp(returncode=0)) as mock_run:
            runner.rollback(to="sha256:abc")
        args = mock_run.call_args[0][0]
        assert args == ["kamal", "rollback", "sha256:abc"]

    def test_app_logs_returns_stdout(self, tmp_path: Path) -> None:
        """app_logs() returns captured stdout from kamal app logs."""
        runner = _make_runner(cwd=tmp_path)
        expected_output = "line1\nline2\nline3\n"
        with patch(
            "subprocess.run",
            return_value=_make_cp(returncode=0, stdout=expected_output),
        ) as mock_run:
            result = runner.app_logs(tail=50)
        assert result == expected_output
        args = mock_run.call_args[0][0]
        assert args == ["kamal", "app", "logs", "--lines", "50"]


# ---------------------------------------------------------------------------
# Group 5 — Logging + structural (3 tests)
# ---------------------------------------------------------------------------


class TestLoggingAndStructural:
    def test_golden_fixture_byte_equal(self) -> None:
        """Golden fixture is byte-equal to render() for the canonical fixture input."""
        anchor = _make_anchor()
        infra = _make_infra()
        result = KamalConfig(anchor, infra).render()
        expected = (FIXTURES_DIR / "deploy_v1.yml").read_text(encoding="utf-8")
        assert result == expected

    def test_subprocess_only_in_runner_and_dns(self) -> None:
        """Grep test: subprocess.run/Popen/call/check_output only in runner.py and dns.py."""
        lib_kamal = Path(__file__).parents[2] / "lib" / "kamal"
        pattern = re.compile(r"subprocess\.(run|Popen|call|check_output)")
        violations: list[str] = []
        for py_file in lib_kamal.glob("*.py"):
            if py_file.name in ("runner.py", "dns.py"):
                continue
            text = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    violations.append(f"{py_file.name}:{lineno}: {line.strip()}")
        assert violations == [], (
            "subprocess.* found outside runner.py and dns.py:\n"
            + "\n".join(violations)
        )

    def test_yaml_only_in_config(self) -> None:
        """Grep test: import yaml only in config.py."""
        lib_kamal = Path(__file__).parents[2] / "lib" / "kamal"
        pattern = re.compile(r"\bimport yaml\b")
        violations: list[str] = []
        for py_file in lib_kamal.glob("*.py"):
            if py_file.name == "config.py":
                continue
            text = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    violations.append(f"{py_file.name}:{lineno}: {line.strip()}")
        assert violations == [], (
            "import yaml found outside config.py:\n" + "\n".join(violations)
        )

    def test_file_size_cap(self) -> None:
        """Every .py file in lib/kamal/ must be <= 200 lines."""
        lib_kamal = Path(__file__).parents[2] / "lib" / "kamal"
        violations: list[str] = []
        for py_file in sorted(lib_kamal.glob("*.py")):
            lines = py_file.read_text(encoding="utf-8").splitlines()
            if len(lines) > 200:
                violations.append(f"{py_file.name}: {len(lines)} lines (max 200)")
        assert violations == [], "File(s) exceed 200-line cap:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# Group 6 — Error hint heuristics (1 parametrised test)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr,expected_substr",
    [
        ("no such file or directory: Dockerfile", "awf-app-dockerize"),
        ("Error: missing secret DATABASE_URL", "awf-app-secret-set"),
        ("Error: secret not set: RAILS_MASTER_KEY", "awf-app-secret-set"),
        ("denied: permission_denied to push image", "GHCR_TOKEN"),
        ("unauthorized: authentication required", "GHCR_TOKEN"),
        ("connection refused (port 22)", "re-run in 60s"),
        ("some totally unknown error occurred", "log.jsonl"),
    ],
)
def test_hint_heuristics(stderr: str, expected_substr: str) -> None:
    """_hint_from_stderr() matches the correct hint for each known error pattern."""
    hint = _hint_from_stderr(stderr)
    assert expected_substr in hint, (
        f"Expected hint containing {expected_substr!r} for stderr={stderr!r}, "
        f"but got: {hint!r}"
    )
