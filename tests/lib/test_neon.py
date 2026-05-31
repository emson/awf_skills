"""Tests for lib/neon — NeonClient, resources, logging, redaction.

22-test matrix per plan_006 § "Test plan":
  Construction            3
  projects.get_or_create  4
  projects.get / delete   3
  branches.get_or_create  3
  branches.delete         2
  branches.connection_string 3
  Logging + redaction     4
  Total                  22

Plus one structural / grep test asserting that resources/ never reference
self._http directly.

All tests are unit-level with httpx.MockTransport — no live API calls.

References:
  - docs/plans/plan_006_s3_neon_lib.md § "Test plan"
  - docs/spec.md § B2
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import httpx

from lib.neon import (
    NeonClient,
    NeonConfig,
    NeonRateLimited,
    NeonError,
    Project,
)


# ---------------------------------------------------------------------------
# MockTransport helpers
# ---------------------------------------------------------------------------


class _Response:
    """Simple response spec for MockTransport handlers."""

    def __init__(
        self, status_code: int, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self._body = body
        self._headers = headers or {}

    def to_httpx(self) -> httpx.Response:
        content = json.dumps(self._body).encode() if self._body is not None else b""
        return httpx.Response(
            self.status_code,
            content=content,
            headers={"Content-Type": "application/json", **self._headers},
        )


def _make_transport(
    responses: list[_Response],
) -> httpx.MockTransport:
    """Return a transport that serves responses from the queue in order."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if not queue:
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")
        return queue.pop(0).to_httpx()

    return httpx.MockTransport(handler)


def _client(*responses: _Response) -> NeonClient:
    """Build a NeonClient with a canned-response transport."""
    config = NeonConfig(api_key="napi_test_token_abc123")
    transport = _make_transport(list(responses))
    return NeonClient(config, _transport=transport)


# Canned response bodies
def _project_body(
    id: str = "lucky-cloud-123456",
    name: str = "my-app",
    region_id: str = "aws-eu-central-1",
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict[str, Any]:
    return {
        "project": {"id": id, "name": name, "region_id": region_id, "created_at": created_at}
    }


def _projects_list_body(projects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"projects": projects or []}


def _branch_body(
    id: str = "br-lucky-cloud-001",
    project_id: str = "lucky-cloud-123456",
    name: str = "main",
    parent_id: str | None = None,
    primary: bool = True,
) -> dict[str, Any]:
    b: dict[str, Any] = {
        "id": id,
        "project_id": project_id,
        "name": name,
        "primary": primary,
        "current_state": "ready",
    }
    if parent_id is not None:
        b["parent_id"] = parent_id
    return {"branch": b}


def _branches_list_body(branches: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"branches": branches or []}


def _op_body(op_id: str, status: str = "finished") -> dict[str, Any]:
    return {"operation": {"id": op_id, "status": status}}


# ---------------------------------------------------------------------------
# Construction (3 tests)
# ---------------------------------------------------------------------------


def test_construction_from_env_happy_path(tmp_path: Path) -> None:
    """from_env resolves NEON_API_KEY from layered config."""
    env_file = tmp_path / ".env"
    env_file.write_text("NEON_API_KEY=napi_from_env_file\n")
    nc = NeonClient.from_env(project_root=tmp_path)
    assert nc.config.api_key == "napi_from_env_file"


def test_construction_missing_token_raises() -> None:
    """from_env raises RuntimeError with the canonical message when key is absent."""
    with patch.dict("os.environ", {}, clear=True):
        # Patch Config.layered to return empty config
        from lib.config import Config

        def _empty_layered(**kwargs: Any) -> Config:
            from lib.config import Layer
            return Config(layers=[Layer(name="env", path=None, values={})])

        with patch.object(Config, "layered", staticmethod(_empty_layered)):
            with pytest.raises(RuntimeError, match="NEON_API_KEY"):
                NeonClient.from_env()


def test_construction_explicit_config() -> None:
    """NeonClient accepts an explicit NeonConfig without calling Config.layered."""
    config = NeonConfig(api_key="napi_explicit")
    # Use a handler that raises immediately if called — construction must not
    # issue any HTTP requests.
    def _fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Should not make HTTP calls during construction")

    nc = NeonClient(config, _transport=httpx.MockTransport(_fail))
    assert nc.config.api_key == "napi_explicit"
    assert nc.projects is not None
    assert nc.branches is not None


# ---------------------------------------------------------------------------
# projects.get_or_create (4 tests)
# ---------------------------------------------------------------------------


def test_projects_get_or_create_creates_when_absent() -> None:
    """get_or_create issues GET (empty list) then POST, awaits operation, returns Project."""
    nc = _client(
        _Response(200, _projects_list_body()),  # search: miss
        _Response(200, {
            "project": {"id": "lucky-cloud-123456", "name": "my-app", "region_id": "aws-eu-central-1", "created_at": "2026-01-01T00:00:00Z"},
            "operations": [{"id": "op-001"}],
        }),  # POST create
        _Response(200, _op_body("op-001", "finished")),  # poll op
    )
    p = nc.projects.get_or_create("my-app")
    assert p.id == "lucky-cloud-123456"
    assert p.name == "my-app"
    assert isinstance(p, Project)


def test_projects_get_or_create_skips_when_present() -> None:
    """get_or_create returns existing project without issuing POST."""
    existing = _project_body()["project"]
    nc = _client(
        _Response(200, _projects_list_body([existing])),  # search: hit
    )
    p = nc.projects.get_or_create("my-app")
    assert p.id == "lucky-cloud-123456"


def test_projects_get_or_create_raises_on_failed_operation() -> None:
    """get_or_create raises NeonError when provisioning operation fails."""
    nc = _client(
        _Response(200, _projects_list_body()),  # search: miss
        _Response(200, {
            "project": {"id": "x-123", "name": "bad-app", "region_id": "aws-eu-central-1", "created_at": "2026-01-01T00:00:00Z"},
            "operations": [{"id": "op-fail"}],
        }),
        _Response(200, _op_body("op-fail", "failed")),
    )
    with pytest.raises(NeonError) as exc_info:
        nc.projects.get_or_create("bad-app")
    assert exc_info.value.code == "operation_failed"


def test_projects_get_or_create_passes_region_and_pg_version() -> None:
    """get_or_create sends region_id and pg_version in POST body."""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured.append(json.loads(request.content))
            body = {
                "project": {"id": "eu-123", "name": "eu-app", "region_id": "aws-eu-west-1", "created_at": ""},
                "operations": [],
            }
            return httpx.Response(200, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        # GET search: empty
        body = {"projects": []}
        return httpx.Response(200, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"})

    nc = NeonClient(NeonConfig(api_key="napi_x"), _transport=httpx.MockTransport(handler))
    nc.projects.get_or_create("eu-app", region_id="aws-eu-west-1", pg_version=15)

    assert len(captured) == 1
    proj = captured[0]["project"]
    assert proj["region_id"] == "aws-eu-west-1"
    assert proj["pg_version"] == 15


# ---------------------------------------------------------------------------
# projects.get / projects.delete (3 tests)
# ---------------------------------------------------------------------------


def test_projects_get_by_name_miss_returns_none() -> None:
    """get returns None when name is not in the search results."""
    nc = _client(_Response(200, _projects_list_body()))
    assert nc.projects.get("nonexistent") is None


def test_projects_get_by_id_hit_returns_project() -> None:
    """get by ID (matching _ID_RE) returns the Project on 200."""
    nc = _client(_Response(200, _project_body()))
    p = nc.projects.get("lucky-cloud-123456")
    assert p is not None
    assert p.id == "lucky-cloud-123456"


def test_projects_delete_returns_true_on_success() -> None:
    """delete returns True when the project is deleted."""
    nc = _client(_Response(200, {"project": {"id": "lucky-cloud-123456", "name": "x", "region_id": "", "created_at": ""}}))
    result = nc.projects.delete("lucky-cloud-123456")
    assert result is True


# ---------------------------------------------------------------------------
# branches.get_or_create (3 tests)
# ---------------------------------------------------------------------------


def test_branches_get_or_create_creates_with_parent_id() -> None:
    """get_or_create sends parent_id when provided."""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured.append(json.loads(request.content))
            data = {"branch": {"id": "br-new-001", "name": "preview", "project_id": "proj-1", "primary": False, "current_state": "ready", "parent_id": "br-main"}, "operations": []}
            return httpx.Response(200, content=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
        # GET list: empty
        data = {"branches": []}
        return httpx.Response(200, content=json.dumps(data).encode(), headers={"Content-Type": "application/json"})

    nc = NeonClient(NeonConfig(api_key="napi_x"), _transport=httpx.MockTransport(handler))
    b = nc.branches.get_or_create("proj-1", name="preview", parent_id="br-main")
    assert b.id == "br-new-001"
    assert captured[0]["branch"]["parent_id"] == "br-main"


def test_branches_get_or_create_skips_when_present() -> None:
    """get_or_create returns existing branch without POST when name matches."""
    existing = {"id": "br-existing", "name": "preview", "project_id": "proj-1", "primary": False, "current_state": "ready"}
    nc = _client(_Response(200, {"branches": [existing]}))
    b = nc.branches.get_or_create("proj-1", name="preview")
    assert b.id == "br-existing"


def test_branches_get_or_create_awaits_operations() -> None:
    """get_or_create polls operations before returning."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if "/operations/" in path:
            data = {"operation": {"id": "op-br", "status": "finished"}}
            return httpx.Response(200, content=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
        if request.method == "POST":
            post_data: dict[str, Any] = {
                "branch": {"id": "br-new", "name": "staging", "project_id": "proj-1", "primary": False, "current_state": "ready"},
                "operations": [{"id": "op-br"}],
            }
            return httpx.Response(200, content=json.dumps(post_data).encode(), headers={"Content-Type": "application/json"})
        # GET list branches: empty
        get_data: dict[str, Any] = {"branches": []}
        return httpx.Response(200, content=json.dumps(get_data).encode(), headers={"Content-Type": "application/json"})

    nc = NeonClient(NeonConfig(api_key="napi_x"), _transport=httpx.MockTransport(handler))
    b = nc.branches.get_or_create("proj-1", name="staging")
    assert b.name == "staging"


# ---------------------------------------------------------------------------
# branches.delete (2 tests)
# ---------------------------------------------------------------------------


def test_branches_delete_success_returns_true() -> None:
    """delete returns True on 200."""
    nc = _client(_Response(200, {"branch": {"id": "br-x", "name": "x", "project_id": "proj-1", "primary": False, "current_state": "ready"}}))
    assert nc.branches.delete("proj-1", "br-x") is True


def test_branches_delete_404_returns_false() -> None:
    """delete returns False on 404 (idempotent teardown)."""
    nc = _client(_Response(404, {"message": "not found"}))
    assert nc.branches.delete("proj-1", "br-gone") is False


# ---------------------------------------------------------------------------
# branches.connection_string (3 tests)
# ---------------------------------------------------------------------------


def test_connection_string_contains_sslmode_require() -> None:
    """connection_string returns URI with sslmode=require from Neon's default response."""
    uri_body = {"uri": "postgres://neondb_owner:secret@host.neon.tech/neondb?sslmode=require"}
    nc = _client(_Response(200, uri_body))
    uri = nc.branches.connection_string("proj-1", "br-1")
    assert "sslmode=require" in uri


def test_connection_string_appends_sslmode_when_missing() -> None:
    """connection_string appends sslmode=require when Neon omits it."""
    uri_body = {"uri": "postgres://neondb_owner:secret@host.neon.tech/neondb"}
    nc = _client(_Response(200, uri_body))
    uri = nc.branches.connection_string("proj-1", "br-1")
    assert "sslmode=require" in uri


def test_connection_string_passes_pooled_param() -> None:
    """connection_string sends the pooled query parameter correctly."""
    captured: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(dict(request.url.params))
        body = {"uri": "postgres://neondb_owner:secret@host.neon.tech/neondb?sslmode=require"}
        return httpx.Response(200, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"})

    nc = NeonClient(NeonConfig(api_key="napi_x"), _transport=httpx.MockTransport(handler))
    nc.branches.connection_string("proj-1", "br-1", pooled=False)
    assert captured[0]["pooled"] == "false"


# ---------------------------------------------------------------------------
# Logging + redaction (4 tests)
# ---------------------------------------------------------------------------


def test_every_successful_call_emits_api_call_event() -> None:
    """Each _call invocation emits exactly one log.api event."""
    events: list[tuple[str, ...]] = []

    original_api = __import__("lib.log", fromlist=["api"]).api

    def capturing_api(provider: str, method: str, path: str, status_code: int, resource_id: str | None = None) -> None:
        events.append((provider, method, path))
        original_api(provider=provider, method=method, path=path, status_code=status_code, resource_id=resource_id)

    with patch("lib.log.api", side_effect=capturing_api):
        nc = _client(
            _Response(200, _projects_list_body()),
        )
        nc.projects.get("missing")

    assert len(events) == 1
    assert events[0][0] == "neon"


def test_token_never_in_log_jsonl(tmp_path: Path) -> None:
    """NEON_API_KEY value never appears in log.jsonl during test execution."""
    log_dir = tmp_path / ".awf"
    log_dir.mkdir()
    log_file = log_dir / "log.jsonl"

    token = "napi_super_secret_test_token_xyz"
    config = NeonConfig(api_key=token)
    nc = NeonClient(config, _transport=_make_transport([_Response(200, _projects_list_body())]))

    with patch("lib.log._resolve_log_path", return_value=log_file):
        nc.projects.get("test-project")

    if log_file.exists():
        content = log_file.read_text()
        assert token not in content, "Bearer token found in log.jsonl"


def test_connection_uri_password_never_in_log(tmp_path: Path) -> None:
    """Connection URI (containing the role password) never appears in log.jsonl."""
    log_dir = tmp_path / ".awf"
    log_dir.mkdir()
    log_file = log_dir / "log.jsonl"

    uri_with_password = "postgres://neondb_owner:s3cr3t_password@host.neon.tech/neondb?sslmode=require"
    nc = _client(_Response(200, {"uri": uri_with_password}))

    with patch("lib.log._resolve_log_path", return_value=log_file):
        result = nc.branches.connection_string("proj-1", "br-1")

    assert result == uri_with_password
    if log_file.exists():
        content = log_file.read_text()
        assert "s3cr3t_password" not in content, "Connection URI password found in log"


def test_rate_limit_raises_neon_rate_limited_with_retry_after() -> None:
    """429 response with Retry-After header raises NeonRateLimited with correct retry_after."""
    nc = _client(_Response(429, {"message": "rate limited"}, headers={"Retry-After": "42"}))
    with pytest.raises(NeonRateLimited) as exc_info:
        nc.projects.get("some-project")
    assert exc_info.value.retry_after == 42.0
    assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# Structural / grep test
# ---------------------------------------------------------------------------


def test_resources_never_reference_self_http() -> None:
    """No file in lib/neon/resources/ contains a bare ``self._http.`` reference.

    This is the plan-005 M3/M4 lesson encoded as a regression test.
    Any self._http. call in resources/ would bypass the _call chokepoint
    and skip log.api emission + error translation.
    """
    resources_dir = Path(__file__).parent.parent.parent / "lib" / "neon" / "resources"
    assert resources_dir.is_dir(), f"resources dir not found: {resources_dir}"

    violations: list[str] = []
    for py_file in sorted(resources_dir.glob("*.py")):
        content = py_file.read_text()
        for lineno, line in enumerate(content.splitlines(), 1):
            if "self._http." in line:
                violations.append(f"{py_file.name}:{lineno}: {line.strip()}")

    assert violations == [], (
        "Found bare self._http. in resources/ — all HTTP calls must route through _call:\n"
        + "\n".join(violations)
    )
