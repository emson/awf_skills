"""Tests for lib/hetzner.py.

25-test matrix per plan_005 § "Test plan":
  Construction         3
  servers.get_or_create 5
  servers.get/delete    2
  firewalls.ensure      4
  lb.get_or_create      4
  ssh_keys.get_or_create 2
  networks.get_or_create 2
  Logging + redaction   3
  Total                25

All tests are unit-level with a mocked hcloud.Client — no live API calls.
Mocking strategy: replace hcloud.Client with a MagicMock whose sub-clients
(.servers, .firewalls, etc.) are further MagicMocks returning canned values.

References:
  - docs/plans/plan_005_s3_hetzner_lib.md § "Test plan"
  - docs/spec.md § B1
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lib.hetzner import (
    HetznerClient,
    HetznerConfig,
    HetznerRateLimited,
)

try:
    from hcloud import APIException
    from hcloud.firewalls import Firewall, FirewallRule
    from hcloud.load_balancers import LoadBalancerService, LoadBalancerTarget
    from hcloud.networks import Network
    from hcloud.servers import Server
    from hcloud.ssh_keys import SSHKey
except ImportError:
    pytest.skip("hcloud not installed", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_client(mock_hcloud: MagicMock) -> HetznerClient:
    """Return a HetznerClient whose internal _client is the given mock."""
    config = HetznerConfig(api_token="test-token-abc123")
    client = HetznerClient(config)
    client._client = mock_hcloud
    # Rebuild namespaces to use the injected mock, routing SDK calls through
    # client._call so the single log-emit + exception-translate path is used.
    from lib.hetzner import _Firewalls, _LoadBalancers, _Networks, _SSHKeys, _Servers

    client.ssh_keys = _SSHKeys(mock_hcloud, client._call)
    client.networks = _Networks(mock_hcloud, client._call)
    client.servers = _Servers(mock_hcloud, client._call, client.ssh_keys, client.networks)
    client.firewalls = _Firewalls(mock_hcloud, client._call, client.servers.get)
    client.lb = _LoadBalancers(mock_hcloud, client._call, client.servers.get)
    return client


def _mock_server(id: int = 1, name: str = "test-server") -> MagicMock:
    s = MagicMock(spec=Server)
    s.id = id
    s.name = name
    return s


def _mock_key(id: int = 10, name: str = "my-key") -> MagicMock:
    k = MagicMock(spec=SSHKey)
    k.id = id
    k.name = name
    return k


def _mock_network(id: int = 20, name: str = "my-net") -> MagicMock:
    n = MagicMock(spec=Network)
    n.id = id
    n.name = name
    return n


def _mock_firewall(id: int = 30, name: str = "my-fw", rules: list[Any] | None = None) -> MagicMock:
    fw = MagicMock(spec=Firewall)
    fw.id = id
    fw.name = name
    fw.rules = rules or []
    return fw


def _make_mock_hcloud() -> MagicMock:
    """Build a permissive MagicMock tree for hcloud.Client."""
    mock = MagicMock()
    # Default: resources don't exist (get_by_name returns None)
    mock.servers.get_by_name.return_value = None
    mock.ssh_keys.get_by_name.return_value = None
    mock.networks.get_by_name.return_value = None
    mock.firewalls.get_by_name.return_value = None
    mock.load_balancers.get_by_name.return_value = None
    # Resolve lookups succeed
    mock.server_types.get_by_name.return_value = MagicMock(id=1, name="cx22")
    mock.images.get_by_name.return_value = MagicMock(id=1, name="ubuntu-24.04")
    mock.locations.get_by_name.return_value = MagicMock(id=1, name="fsn1")
    mock.load_balancer_types.get_by_name.return_value = MagicMock(id=1, name="lb11")
    # Actions succeed immediately
    mock.actions.get_by_id.return_value = MagicMock(status="success", id=99)
    return mock


def _read_log_lines(project_root: Path) -> list[dict]:  # type: ignore[type-arg]
    log_path = project_root / ".awf" / "log.jsonl"
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# Group 1: Construction (3 tests)
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_from_env_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() builds a working client when HETZNER_API_TOKEN is set."""
        monkeypatch.setenv("HETZNER_API_TOKEN", "tok-secret-abc")
        with patch("hcloud.Client.__init__", return_value=None):
            hz = HetznerClient.from_env()
        assert hz.config.api_token == "tok-secret-abc"
        assert hasattr(hz, "servers")
        assert hasattr(hz, "firewalls")
        assert hasattr(hz, "lb")
        assert hasattr(hz, "ssh_keys")
        assert hasattr(hz, "networks")

    def test_from_env_missing_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() raises RuntimeError when HETZNER_API_TOKEN is absent."""
        monkeypatch.delenv("HETZNER_API_TOKEN", raising=False)
        # Ensure none of the .env files on this machine provide it by
        # patching Config.layered to return an empty config
        with patch("lib.hetzner.HetznerClient.from_env.__func__" if False else "lib.config.Config.layered") as mock_cfg:
            mock_cfg.return_value = MagicMock()
            mock_cfg.return_value.get.return_value = None
            with pytest.raises(RuntimeError, match="HETZNER_API_TOKEN"):
                HetznerClient.from_env()

    def test_explicit_config_injection(self) -> None:
        """HetznerClient can be constructed directly with an explicit HetznerConfig."""
        config = HetznerConfig(api_token="explicit-token-xyz", app_name="test-app")
        with patch("hcloud.Client.__init__", return_value=None):
            hz = HetznerClient(config)
        assert hz.config.api_token == "explicit-token-xyz"
        assert hz.config.app_name == "test-app"


# ---------------------------------------------------------------------------
# Group 2: servers.get_or_create (5 tests)
# ---------------------------------------------------------------------------


class TestServersGetOrCreate:
    def test_create_when_absent(self) -> None:
        """servers.get_or_create creates a server when none exists by that name."""
        mock = _make_mock_hcloud()
        server = _mock_server(id=42, name="new-server")
        create_resp = MagicMock()
        create_resp.server = server
        create_resp.root_password = None
        create_resp.next_actions = []
        mock.servers.create.return_value = create_resp

        hz = _make_client(mock)
        result = hz.servers.get_or_create("new-server")

        mock.servers.create.assert_called_once()
        assert result.id == 42

    def test_skip_when_present_returns_same_id(self) -> None:
        """servers.get_or_create returns the existing server without creating."""
        mock = _make_mock_hcloud()
        existing = _mock_server(id=7, name="existing-server")
        mock.servers.get_by_name.return_value = existing

        hz = _make_client(mock)
        result = hz.servers.get_or_create("existing-server")

        mock.servers.create.assert_not_called()
        assert result.id == 7

    def test_skip_emits_api_call(self, tmp_path: Path) -> None:
        """get_or_create skip path emits exactly one api.call log event."""
        import lib.log as log_mod

        tok_root = log_mod._current_project_root.set(tmp_path)
        (tmp_path / ".awf").mkdir()
        try:
            mock = _make_mock_hcloud()
            existing = _mock_server(id=7, name="existing-server")
            mock.servers.get_by_name.return_value = existing

            hz = _make_client(mock)
            hz.servers.get_or_create("existing-server")

            lines = _read_log_lines(tmp_path)
            api_lines = [ev for ev in lines if ev.get("type") == "api.call"]
            assert len(api_lines) == 1
            assert api_lines[0]["data"]["provider"] == "hetzner"
            assert api_lines[0]["data"]["method"] == "GET"
        finally:
            log_mod._current_project_root.reset(tok_root)

    def test_passes_user_data_through(self) -> None:
        """get_or_create forwards user_data to the hcloud create call."""
        mock = _make_mock_hcloud()
        server = _mock_server(id=1, name="cloud-init-server")
        create_resp = MagicMock()
        create_resp.server = server
        create_resp.root_password = None
        create_resp.next_actions = []
        mock.servers.create.return_value = create_resp

        hz = _make_client(mock)
        hz.servers.get_or_create("cloud-init-server", user_data="#cloud-config\n")

        call_kwargs = mock.servers.create.call_args.kwargs
        assert call_kwargs.get("user_data") == "#cloud-config\n"

    def test_resolves_ssh_keys_by_name(self) -> None:
        """get_or_create looks up SSH keys by name and passes them to create."""
        mock = _make_mock_hcloud()
        key = _mock_key(id=10, name="my-key")
        mock.ssh_keys.get_by_name.return_value = key

        server = _mock_server(id=2, name="keyed-server")
        create_resp = MagicMock()
        create_resp.server = server
        create_resp.root_password = None
        create_resp.next_actions = []
        mock.servers.create.return_value = create_resp

        hz = _make_client(mock)
        hz.servers.get_or_create("keyed-server", ssh_keys=["my-key"])

        call_kwargs = mock.servers.create.call_args.kwargs
        assert call_kwargs.get("ssh_keys") == [key]


# ---------------------------------------------------------------------------
# Group 3: servers.get / delete (2 tests)
# ---------------------------------------------------------------------------


class TestServersMisc:
    def test_get_miss_returns_none(self) -> None:
        """servers.get returns None when the server does not exist."""
        mock = _make_mock_hcloud()
        mock.servers.get_by_name.return_value = None

        hz = _make_client(mock)
        result = hz.servers.get("nonexistent")
        assert result is None

    def test_delete_idempotent_absent(self) -> None:
        """servers.delete returns False when the server does not exist."""
        mock = _make_mock_hcloud()
        mock.servers.get_by_name.return_value = None

        hz = _make_client(mock)
        result = hz.servers.delete("ghost-server")
        assert result is False
        mock.servers.delete.assert_not_called()


# ---------------------------------------------------------------------------
# Group 4: firewalls.ensure (4 tests)
# ---------------------------------------------------------------------------


class TestFirewallsEnsure:
    def _rule(self, direction: str = "in", protocol: str = "tcp", port: str = "80") -> FirewallRule:
        return FirewallRule(direction=direction, protocol=protocol, port=port, source_ips=["0.0.0.0/0"])

    def test_create_and_set_rules(self) -> None:
        """ensure creates firewall and sets rules when none exists."""
        mock = _make_mock_hcloud()
        fw = _mock_firewall(id=30, name="new-fw", rules=[])
        fw_create_resp = MagicMock()
        fw_create_resp.firewall = fw
        fw_create_resp.actions = []
        mock.firewalls.create.return_value = fw_create_resp
        mock.firewalls.set_rules.return_value = []

        rules = [self._rule()]
        hz = _make_client(mock)
        result = hz.firewalls.ensure("new-fw", rules=rules)

        mock.firewalls.create.assert_called_once()
        mock.firewalls.set_rules.assert_called_once()
        assert result.id == 30

    def test_skip_when_rules_match(self) -> None:
        """ensure skips set_rules when rules are already equal."""
        rule = self._rule()
        mock = _make_mock_hcloud()
        fw = _mock_firewall(id=30, name="my-fw", rules=[rule])
        mock.firewalls.get_by_name.return_value = fw

        hz = _make_client(mock)
        hz.firewalls.ensure("my-fw", rules=[rule])

        mock.firewalls.set_rules.assert_not_called()

    def test_replace_when_rules_differ(self) -> None:
        """ensure calls set_rules when the rule set has changed."""
        old_rule = self._rule(port="80")
        new_rule = self._rule(port="443")
        mock = _make_mock_hcloud()
        fw = _mock_firewall(id=30, name="my-fw", rules=[old_rule])
        mock.firewalls.get_by_name.return_value = fw
        mock.firewalls.set_rules.return_value = []

        hz = _make_client(mock)
        hz.firewalls.ensure("my-fw", rules=[new_rule])

        mock.firewalls.set_rules.assert_called_once()

    def test_apply_to_wiring(self) -> None:
        """ensure calls apply_to_resources when apply_to is given."""
        rule = self._rule()
        mock = _make_mock_hcloud()
        fw = _mock_firewall(id=30, name="my-fw", rules=[rule])
        mock.firewalls.get_by_name.return_value = fw
        server = _mock_server(id=1, name="web-01")
        mock.servers.get_by_name.return_value = server
        mock.firewalls.apply_to_resources.return_value = []

        hz = _make_client(mock)
        hz.firewalls.ensure("my-fw", rules=[rule], apply_to=["web-01"])

        mock.firewalls.apply_to_resources.assert_called_once()


# ---------------------------------------------------------------------------
# Group 5: lb.get_or_create (4 tests)
# ---------------------------------------------------------------------------


class TestLBGetOrCreate:
    def test_create_with_services(self) -> None:
        """lb.get_or_create creates the LB with supplied services."""
        mock = _make_mock_hcloud()
        lb_obj = MagicMock(id=50, name="my-lb")
        lb_resp = MagicMock()
        lb_resp.load_balancer = lb_obj
        lb_resp.action = None
        mock.load_balancers.create.return_value = lb_resp

        service = LoadBalancerService(protocol="http", listen_port=80, destination_port=8080, proxyprotocol=False)
        hz = _make_client(mock)
        result = hz.lb.get_or_create("my-lb", services=[service])

        mock.load_balancers.create.assert_called_once()
        call_kwargs = mock.load_balancers.create.call_args.kwargs
        assert call_kwargs.get("services") == [service]
        assert result.id == 50

    def test_skip_when_present(self) -> None:
        """lb.get_or_create returns the existing LB without creating."""
        mock = _make_mock_hcloud()
        lb_obj = MagicMock(id=50, name="my-lb")
        mock.load_balancers.get_by_name.return_value = lb_obj

        hz = _make_client(mock)
        result = hz.lb.get_or_create("my-lb")

        mock.load_balancers.create.assert_not_called()
        assert result.id == 50

    def test_targets_resolved_by_server_name(self) -> None:
        """lb.get_or_create resolves server names to LoadBalancerTarget objects."""
        mock = _make_mock_hcloud()
        server = _mock_server(id=3, name="app-01")
        mock.servers.get_by_name.return_value = server
        lb_obj = MagicMock(id=51, name="my-lb")
        lb_resp = MagicMock()
        lb_resp.load_balancer = lb_obj
        lb_resp.action = None
        mock.load_balancers.create.return_value = lb_resp

        hz = _make_client(mock)
        hz.lb.get_or_create("my-lb", targets=["app-01"])

        call_kwargs = mock.load_balancers.create.call_args.kwargs
        targets_arg = call_kwargs.get("targets")
        assert targets_arg is not None
        assert len(targets_arg) == 1
        assert isinstance(targets_arg[0], LoadBalancerTarget)
        assert targets_arg[0].server == server

    def test_health_check_passthrough(self) -> None:
        """lb.get_or_create accepts and stores health_check as a plain dict."""
        mock = _make_mock_hcloud()
        lb_obj = MagicMock(id=52, name="my-lb")
        lb_resp = MagicMock()
        lb_resp.load_balancer = lb_obj
        lb_resp.action = None
        mock.load_balancers.create.return_value = lb_resp

        hz = _make_client(mock)
        # health_check is stored on the HetznerClient LB namespace but not
        # forwarded to the SDK directly in this plan (services carry health_check).
        # We verify the call completes without error.
        result = hz.lb.get_or_create("my-lb", health_check={"protocol": "http", "port": 80})
        assert result.id == 52


# ---------------------------------------------------------------------------
# Group 6: ssh_keys.get_or_create (2 tests)
# ---------------------------------------------------------------------------


class TestSSHKeysGetOrCreate:
    def test_create(self) -> None:
        """ssh_keys.get_or_create creates the key when absent."""
        mock = _make_mock_hcloud()
        key = _mock_key(id=10, name="new-key")
        mock.ssh_keys.get_by_name.return_value = None
        mock.ssh_keys.create.return_value = key

        hz = _make_client(mock)
        result = hz.ssh_keys.get_or_create("new-key", public_key="ssh-rsa AAAA...")

        mock.ssh_keys.create.assert_called_once_with(name="new-key", public_key="ssh-rsa AAAA...")
        assert result.id == 10

    def test_skip(self) -> None:
        """ssh_keys.get_or_create returns the existing key without creating."""
        mock = _make_mock_hcloud()
        key = _mock_key(id=10, name="existing-key")
        mock.ssh_keys.get_by_name.return_value = key

        hz = _make_client(mock)
        result = hz.ssh_keys.get_or_create("existing-key", public_key="ssh-rsa AAAA...")

        mock.ssh_keys.create.assert_not_called()
        assert result.id == 10


# ---------------------------------------------------------------------------
# Group 7: networks.get_or_create (2 tests)
# ---------------------------------------------------------------------------


class TestNetworksGetOrCreate:
    def test_create_with_subnet(self) -> None:
        """networks.get_or_create creates the network with a subnet when absent."""
        mock = _make_mock_hcloud()
        net = _mock_network(id=20, name="new-net")
        mock.networks.get_by_name.return_value = None
        mock.networks.create.return_value = net

        hz = _make_client(mock)
        result = hz.networks.get_or_create("new-net")

        mock.networks.create.assert_called_once()
        call_kwargs = mock.networks.create.call_args.kwargs
        assert "subnets" in call_kwargs
        assert len(call_kwargs["subnets"]) == 1
        assert result.id == 20

    def test_skip(self) -> None:
        """networks.get_or_create returns the existing network without creating."""
        mock = _make_mock_hcloud()
        net = _mock_network(id=20, name="existing-net")
        mock.networks.get_by_name.return_value = net

        hz = _make_client(mock)
        result = hz.networks.get_or_create("existing-net")

        mock.networks.create.assert_not_called()
        assert result.id == 20


# ---------------------------------------------------------------------------
# Group 8: Logging + redaction (3 tests)
# ---------------------------------------------------------------------------


class TestLoggingAndRedaction:
    def test_every_successful_call_emits_one_api_call(self, tmp_path: Path) -> None:
        """A successful get_or_create emits exactly one api.call event."""
        import lib.log as log_mod

        tok_root = log_mod._current_project_root.set(tmp_path)
        (tmp_path / ".awf").mkdir()
        try:
            mock = _make_mock_hcloud()
            key = _mock_key(id=10, name="my-key")
            mock.ssh_keys.get_by_name.return_value = None
            mock.ssh_keys.create.return_value = key

            hz = _make_client(mock)
            hz.ssh_keys.get_or_create("my-key", public_key="ssh-rsa AAAA...")

            lines = _read_log_lines(tmp_path)
            api_events = [line for line in lines if line.get("type") == "api.call"]
            assert len(api_events) == 1
            ev = api_events[0]
            assert ev["data"]["provider"] == "hetzner"
            assert ev["data"]["method"] == "POST"
            # sdk_call uses 200 for all successes (status_code is not propagated
            # from the hcloud response object; 2xx semantics are preserved in
            # the resource_id being present vs absent).
            assert ev["data"]["status_code"] == 200
            assert ev["data"]["resource_id"] == "10"
        finally:
            log_mod._current_project_root.reset(tok_root)

    def test_token_never_in_log(self, tmp_path: Path) -> None:
        """The API token must never appear in any log line written during a test run."""
        import lib.log as log_mod

        secret = "SUPER-SECRET-TOKEN-12345"
        tok_root = log_mod._current_project_root.set(tmp_path)
        (tmp_path / ".awf").mkdir()
        try:
            mock = _make_mock_hcloud()
            key = _mock_key(id=11, name="tok-key")
            mock.ssh_keys.get_by_name.return_value = None
            mock.ssh_keys.create.return_value = key

            config = HetznerConfig(api_token=secret)
            hz = HetznerClient(config)
            hz._client = mock
            from lib.hetzner import _SSHKeys

            hz.ssh_keys = _SSHKeys(mock, hz._call)
            hz.ssh_keys.get_or_create("tok-key", public_key="ssh-rsa BBBB...")

            log_path = tmp_path / ".awf" / "log.jsonl"
            if log_path.exists():
                content = log_path.read_text(encoding="utf-8")
                assert secret not in content, "API token leaked into log!"
        finally:
            log_mod._current_project_root.reset(tok_root)

    def test_rate_limited_carries_retry_after(self) -> None:
        """HetznerRateLimited carries retry_after from the API response details."""
        mock = _make_mock_hcloud()
        exc = APIException(
            code="rate_limit_exceeded",
            message="Too many requests",
            details={"retry_after": 30.0},
        )
        mock.ssh_keys.get_by_name.side_effect = exc

        hz = _make_client(mock)
        with pytest.raises(HetznerRateLimited) as exc_info:
            hz.ssh_keys.get("any-key")

        assert exc_info.value.retryable is True
        assert exc_info.value.retry_after == 30.0
