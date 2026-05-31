"""Tests for lib/state.py.

Covers all 13 acceptance criteria from plan_001_foundation_state_schema.md.
Uses real temp directories (tmp_path fixture), real files, real JSON.
Log hook tests inject a fake lib.log via monkeypatch.setitem(sys.modules, ...).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import lib.awf_home as awf_home_module
from lib.project import ProjectNotFound
from lib.state import (
    AWF_DIRNAME,
    INFRA_FILENAME,
    PROJECT_FILENAME,
    SHARED_FILENAME,
    Infra,
    ProjectAnchor,
    Shared,
    StateCorruptError,
    StateValidationError,
    _atomic_write_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_passport(tmp_path: Path) -> None:
    """Write a minimal passport.json so find_project_root() succeeds."""
    (tmp_path / "passport.json").write_text('{"domain": "example.com"}\n', encoding="utf-8")


def _make_anchor_dir(tmp_path: Path) -> Path:
    """Create .awf/ directory under tmp_path and return its path."""
    awf_dir = tmp_path / AWF_DIRNAME
    awf_dir.mkdir()
    return awf_dir


def _write_anchor(tmp_path: Path, data: dict) -> Path:  # type: ignore[type-arg]
    """Write a project.json into tmp_path/.awf/ and return the file path."""
    _make_passport(tmp_path)
    awf_dir = _make_anchor_dir(tmp_path)
    path = awf_dir / PROJECT_FILENAME
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    return path


def _minimal_anchor_data() -> dict:  # type: ignore[type-arg]
    """Return a minimal valid ProjectAnchor JSON dict."""
    return {
        "awf_version": "0.1.0",
        "domain": "example.com",
        "slug": "example",
        "stage": "landing",
        "created": "2026-05-31T20:00:00Z",
        "has": {"passport": True, "infra": False, "kamal": False, "content": False},
    }


def _full_infra_data() -> dict:  # type: ignore[type-arg]
    """Return a fully-populated valid Infra JSON dict (mirrors D-003)."""
    return {
        "registry": {"host": "ghcr.io", "image": "user/example", "user": "user"},
        "hetzner": {
            "servers": [
                {"id": "123", "ip": "1.2.3.4", "role": "web", "shared": True, "cost_eur_month": 5.0}
            ],
            "lb_id": None,
            "network_id": None,
        },
        "neon": {
            "project_id": "neon_proj_xxx",
            "branch_id": "br_xxx",
            "branch_name": "example",
            "mode": "shared-branch",
            "connection_secret_ref": "DATABASE_URL",
        },
        "kamal": {"config_path": "config/deploy.yml", "last_deploy_image": None},
    }


def _write_infra(tmp_path: Path, data: dict) -> Path:  # type: ignore[type-arg]
    """Write infra.json into tmp_path/.awf/ and return the file path."""
    _make_passport(tmp_path)
    awf_dir = _make_anchor_dir(tmp_path)
    path = awf_dir / INFRA_FILENAME
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    return path


class FakeLog:
    """Minimal fake lib.log module that records state_change calls."""

    def __init__(self) -> None:
        self.calls: list[dict] = []  # type: ignore[type-arg]

    def state_change(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


class RaisingLog:
    """Fake lib.log module whose state_change always raises."""

    def state_change(self, **kwargs: object) -> None:
        raise RuntimeError("simulated log failure")


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


def test_project_anchor_round_trip(tmp_path: Path) -> None:
    """load → mutate → save → load yields identical state."""
    data = _minimal_anchor_data()
    _write_anchor(tmp_path, data)

    anchor = ProjectAnchor.load(start=tmp_path)
    anchor.has.content = True
    anchor.save()

    reloaded = ProjectAnchor.load(start=tmp_path)
    assert reloaded.model_dump() == anchor.model_dump()


def test_infra_round_trip(tmp_path: Path) -> None:
    """Infra: load → mutate → save → load yields identical state."""
    _write_infra(tmp_path, _full_infra_data())

    infra = Infra.load(start=tmp_path)
    infra.kamal.last_deploy_image = "ghcr.io/user/example:v2"
    infra.save()

    reloaded = Infra.load(start=tmp_path)
    assert reloaded.model_dump() == infra.model_dump()


def test_shared_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shared: load_or_create → mutate → save → load_or_create yields identical state."""
    monkeypatch.setattr(awf_home_module, "user_config_dir", lambda: tmp_path / "user-awf")

    shared = Shared.load_or_create()
    shared.play_server = None
    shared.play_neon_project_id = "proj_abc"
    shared.save()

    reloaded = Shared.load_or_create()
    assert reloaded.model_dump() == shared.model_dump()


# ---------------------------------------------------------------------------
# Forward compatibility
# ---------------------------------------------------------------------------


def test_anchor_tolerates_unknown_top_level_keys(tmp_path: Path) -> None:
    """A file with extra top-level keys loads without error and round-trips the key."""
    data = _minimal_anchor_data()
    data["future_field"] = {"x": 1}
    _write_anchor(tmp_path, data)

    anchor = ProjectAnchor.load(start=tmp_path)
    assert anchor.model_dump()["future_field"] == {"x": 1}

    anchor.save()
    reloaded = ProjectAnchor.load(start=tmp_path)
    assert reloaded.model_dump()["future_field"] == {"x": 1}


def test_infra_tolerates_unknown_nested_keys(tmp_path: Path) -> None:
    """Extra key inside hetzner survives a load-save round trip."""
    data = _full_infra_data()
    data["hetzner"]["future_hetzner_key"] = "some-value"
    _write_infra(tmp_path, data)

    infra = Infra.load(start=tmp_path)
    infra.save()

    reloaded = Infra.load(start=tmp_path)
    assert reloaded.hetzner.model_dump()["future_hetzner_key"] == "some-value"


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


def test_anchor_rejects_landing_with_infra(tmp_path: Path) -> None:
    """stage='landing' + has.infra=True raises StateValidationError."""
    data = _minimal_anchor_data()
    data["has"]["infra"] = True
    _write_anchor(tmp_path, data)

    with pytest.raises(StateValidationError, match="infra"):
        ProjectAnchor.load(start=tmp_path)


def test_anchor_rejects_bad_slug(tmp_path: Path) -> None:
    """slug with spaces or uppercase raises StateValidationError."""
    data = _minimal_anchor_data()
    data["slug"] = "Has Spaces"
    _write_anchor(tmp_path, data)

    with pytest.raises(StateValidationError, match="slug"):
        ProjectAnchor.load(start=tmp_path)


def test_infra_rejects_lb_without_servers(tmp_path: Path) -> None:
    """lb_id set with empty servers raises StateValidationError."""
    data = _full_infra_data()
    data["hetzner"]["lb_id"] = "lb-123"
    data["hetzner"]["servers"] = []
    _write_infra(tmp_path, data)

    with pytest.raises(StateValidationError, match="lb_id"):
        Infra.load(start=tmp_path)


def test_save_refuses_to_write_invalid_state(tmp_path: Path) -> None:
    """Mutating to invalid state and calling save raises and leaves disk unchanged."""
    _write_infra(tmp_path, _full_infra_data())
    infra = Infra.load(start=tmp_path)

    # Read on-disk content before the attempted save
    assert infra._path is not None
    original_content = infra._path.read_text(encoding="utf-8")

    # Make state invalid: lb_id with no servers
    infra.hetzner.lb_id = "lb-999"
    infra.hetzner.servers = []

    with pytest.raises(StateValidationError):
        infra.save()

    # Disk must be unchanged
    assert infra._path.read_text(encoding="utf-8") == original_content


# ---------------------------------------------------------------------------
# Error tests
# ---------------------------------------------------------------------------


def test_load_missing_anchor_raises_project_not_found(tmp_path: Path) -> None:
    """Empty tmp_path: ProjectAnchor.load raises ProjectNotFound with path context."""
    with pytest.raises(ProjectNotFound) as exc_info:
        ProjectAnchor.load(start=tmp_path)

    # Message should contain the start path
    assert str(tmp_path) in str(exc_info.value)
    # __cause__ should be the original ProjectNotFound from find_project_root
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, ProjectNotFound)


def test_infra_load_missing_project_raises_project_not_found(tmp_path: Path) -> None:
    """Empty tmp_path: Infra.load raises ProjectNotFound with path context."""
    with pytest.raises(ProjectNotFound) as exc_info:
        Infra.load(start=tmp_path)

    assert str(tmp_path) in str(exc_info.value)
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, ProjectNotFound)


def test_load_malformed_json_raises_corrupt(tmp_path: Path) -> None:
    """Malformed JSON raises StateCorruptError with byte offset and path."""
    _make_passport(tmp_path)
    awf_dir = _make_anchor_dir(tmp_path)
    path = awf_dir / PROJECT_FILENAME
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(StateCorruptError) as exc_info:
        ProjectAnchor.load(start=tmp_path)

    error_msg = str(exc_info.value)
    # Must contain byte offset (some non-negative integer)
    assert any(char.isdigit() for char in error_msg), "Expected byte offset in error message"
    # Must contain the file path
    assert str(path) in error_msg


# ---------------------------------------------------------------------------
# Atomic write tests
# ---------------------------------------------------------------------------


def test_atomic_write_leaves_prior_file_intact_on_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If os.replace raises mid-save, the original file is unchanged."""
    data = _full_infra_data()
    _write_infra(tmp_path, data)
    infra = Infra.load(start=tmp_path)

    assert infra._path is not None
    original_content = infra._path.read_text(encoding="utf-8")

    # Patch os.replace to simulate a crash mid-rename
    monkeypatch.setattr(os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("simulated crash")))

    with pytest.raises(OSError, match="simulated crash"):
        infra.save()

    # Original file must be intact
    assert infra._path.read_text(encoding="utf-8") == original_content


def test_atomic_write_creates_parent_dir(tmp_path: Path) -> None:
    """_atomic_write_json creates the parent directory if absent."""
    target = tmp_path / AWF_DIRNAME / PROJECT_FILENAME
    assert not target.parent.exists()

    _atomic_write_json(target, {"hello": "world"})

    assert target.exists()
    assert json.loads(target.read_text()) == {"hello": "world"}


# ---------------------------------------------------------------------------
# Log hook tests
# ---------------------------------------------------------------------------


def test_save_emits_state_change_via_log_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save() calls log.state_change with canonical signature and correct before/after."""
    _write_anchor(tmp_path, _minimal_anchor_data())
    anchor = ProjectAnchor.load(start=tmp_path)

    fake = FakeLog()
    monkeypatch.setitem(sys.modules, "lib.log", fake)  # type: ignore[arg-type]

    # First save: before should be the on-disk state at load time
    assert anchor._path is not None
    on_disk_before = json.loads(anchor._path.read_text(encoding="utf-8"))
    anchor.save()

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["file"] == str(anchor._path)
    assert call["key"] == ""
    assert call["before"] == on_disk_before
    assert "diff" not in call

    # Second save: before should now be the just-written content
    anchor.has.content = True
    second_before = json.loads(anchor._path.read_text(encoding="utf-8"))
    anchor.save()

    assert len(fake.calls) == 2
    second_call = fake.calls[1]
    assert second_call["before"] == second_before
    assert second_call["after"]["has"]["content"] is True


def test_save_when_log_unavailable_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If lib.log is absent, save() still succeeds and writes the file."""
    _write_anchor(tmp_path, _minimal_anchor_data())
    anchor = ProjectAnchor.load(start=tmp_path)

    # Remove lib.log from sys.modules (simulate it not existing)
    monkeypatch.delitem(sys.modules, "lib.log", raising=False)

    # Should not raise
    anchor.save()

    assert anchor._path is not None
    assert anchor._path.exists()


def test_log_hook_failures_become_stderr_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """If log.state_change raises, save() still succeeds and stderr has 'warn:'."""
    _write_anchor(tmp_path, _minimal_anchor_data())
    anchor = ProjectAnchor.load(start=tmp_path)

    raising_log = RaisingLog()
    monkeypatch.setitem(sys.modules, "lib.log", raising_log)  # type: ignore[arg-type]

    anchor.save()  # Must not raise

    captured = capsys.readouterr()
    assert "warn:" in captured.err


# ---------------------------------------------------------------------------
# Load-or-create semantics
# ---------------------------------------------------------------------------


def test_infra_load_or_create_returns_empty_when_file_absent(tmp_path: Path) -> None:
    """load_or_create on a missing infra.json returns an empty default model."""
    _make_passport(tmp_path)

    infra = Infra.load_or_create(start=tmp_path)

    assert infra.registry.host == "ghcr.io"
    assert infra.hetzner.servers == []
    assert infra.hetzner.lb_id is None
    assert infra.neon.project_id == ""
    assert infra.kamal.last_deploy_image is None


def test_infra_load_or_create_does_not_create_file_until_save(tmp_path: Path) -> None:
    """load_or_create does not write the file; .save() writes it."""
    _make_passport(tmp_path)
    path = tmp_path / AWF_DIRNAME / INFRA_FILENAME

    infra = Infra.load_or_create(start=tmp_path)
    assert not path.exists(), "File must not exist after load_or_create"

    infra.save()
    assert path.exists(), "File must exist after .save()"


def test_shared_load_or_create_uses_awf_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shared reads/writes under the monkeypatched user_config_dir, not ~/.config/awf/."""
    user_dir = tmp_path / "user-awf"
    monkeypatch.setattr(awf_home_module, "user_config_dir", lambda: user_dir)

    shared = Shared.load_or_create()
    shared.play_neon_project_id = "proj_test"
    shared.save()

    expected_path = user_dir / SHARED_FILENAME
    assert expected_path.exists(), "shared.json must be written under monkeypatched dir"

    # Assert the monkeypatched path was used (not ~/.config/awf/)
    assert shared._path == expected_path


def test_shared_load_or_create_returns_default_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With user_config_dir pointing to empty dir, load_or_create returns defaults."""
    user_dir = tmp_path / "user-awf"
    monkeypatch.setattr(awf_home_module, "user_config_dir", lambda: user_dir)

    shared = Shared.load_or_create()

    assert shared.play_server is None
    assert shared.play_neon_project_id is None
    assert shared.default_registry.host == "ghcr.io"
    assert shared.default_registry.user == ""

    expected_path = user_dir / SHARED_FILENAME
    assert not expected_path.exists(), "File must not exist after load_or_create on empty dir"
