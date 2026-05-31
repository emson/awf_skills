"""Tests for lib/project.py dual-walk and ensure_anchor.

Covers acceptance criteria from plan_002_foundation_project_locator.md.
Uses real temp directories (tmp_path fixture), real files, real JSON.
Log hook tests inject a fake lib.log via monkeypatch.setitem(sys.modules, ...).
None of these tests mutate cwd.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lib.project import (
    AWF_DIRNAME,
    PASSPORT_FILENAME,
    PROJECT_FILENAME,
    ProjectNotFound,
    ensure_anchor,
    find_anchor_state,
    find_project_root,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_passport(root: Path, *, domain: str = "example.com") -> None:
    """Write a minimal passport.json that Passport.load() can parse."""
    # project_name must match domain_to_project_name(domain)
    # example.com → example-com
    slug = domain.replace(".", "-")
    data = {
        "schema_version": "1.0",
        "template_version": "1.0",
        "project_name": slug,
        "domain": domain,
        "site_url": f"https://{domain}",
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
    (root / PASSPORT_FILENAME).write_text(
        json.dumps(data, indent=4) + "\n", encoding="utf-8"
    )


def _make_anchor(root: Path, *, domain: str = "example.com") -> None:
    """Write a minimal valid .awf/project.json at root."""
    slug = domain.replace(".", "-")
    awf_dir = root / AWF_DIRNAME
    awf_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "awf_version": "0.1.0",
        "domain": domain,
        "slug": slug,
        "stage": "landing",
        "created": "2026-05-31T20:00:00Z",
        "has": {"passport": True, "infra": False, "kamal": False, "content": False},
    }
    (awf_dir / PROJECT_FILENAME).write_text(
        json.dumps(data) + "\n", encoding="utf-8"
    )


class FakeLog:
    """Minimal fake lib.log module that records state_change calls."""

    def __init__(self) -> None:
        self.calls: list[dict] = []  # type: ignore[type-arg]

    def state_change(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


# ---------------------------------------------------------------------------
# Dual-walk discovery tests
# ---------------------------------------------------------------------------


def test_find_project_root_anchor_only(tmp_path: Path) -> None:
    """Anchor only: find_project_root returns root; find_anchor_state returns (root, False)."""
    _make_anchor(tmp_path)

    assert find_project_root(tmp_path) == tmp_path
    root, anchor_missing = find_anchor_state(tmp_path)
    assert root == tmp_path
    assert anchor_missing is False


def test_find_project_root_passport_only(tmp_path: Path) -> None:
    """Passport only: find_project_root returns root; find_anchor_state returns (root, True)."""
    _make_passport(tmp_path)

    assert find_project_root(tmp_path) == tmp_path
    root, anchor_missing = find_anchor_state(tmp_path)
    assert root == tmp_path
    assert anchor_missing is True


def test_find_project_root_both_present(tmp_path: Path) -> None:
    """Both files at tmp_path: find_anchor_state returns anchor_missing=False."""
    _make_passport(tmp_path)
    _make_anchor(tmp_path)

    root, anchor_missing = find_anchor_state(tmp_path)
    assert root == tmp_path
    assert anchor_missing is False


def test_find_project_root_neither(tmp_path: Path) -> None:
    """Empty tmp_path: raises ProjectNotFound with cwd and parent path in message."""
    with pytest.raises(ProjectNotFound) as exc_info:
        find_project_root(tmp_path)

    msg = str(exc_info.value)
    assert str(tmp_path) in msg
    # At least one parent path must appear in the message
    parent_strs = [str(p) for p in tmp_path.parents]
    assert any(p in msg for p in parent_strs), (
        f"Expected a parent path in message, got: {msg!r}"
    )


def test_find_project_root_neither_optional_returns_none(tmp_path: Path) -> None:
    """optional=True: returns None rather than raising when no project found."""
    result = find_project_root(tmp_path, optional=True)
    assert result is None


def test_find_project_root_passport_wins_when_closer(tmp_path: Path) -> None:
    """First-match-wins: passport at sub/ beats anchor at tmp_path/ when walk starts deeper.

    Fixture: tmp_path/.awf/project.json exists; tmp_path/sub/passport.json exists.
    Start from tmp_path/sub/deep/ — walk hits tmp_path/sub first (passport only).
    Result: root=tmp_path/sub, anchor_missing=True.
    """
    # Anchor at top level
    _make_anchor(tmp_path)
    # Passport at sub level (no anchor)
    sub = tmp_path / "sub"
    sub.mkdir()
    _make_passport(sub)
    deep = sub / "deep"
    deep.mkdir()

    result_root = find_project_root(deep)
    assert result_root == sub

    root, anchor_missing = find_anchor_state(deep)
    assert root == sub
    assert anchor_missing is True


def test_find_project_root_anchor_wins_when_closer(tmp_path: Path) -> None:
    """First-match-wins: anchor at sub/ beats passport at tmp_path/ when walk starts deeper.

    Fixture: tmp_path/passport.json exists; tmp_path/sub/.awf/project.json exists.
    Start from tmp_path/sub/deep/ — walk hits tmp_path/sub first (anchor present).
    Result: root=tmp_path/sub, anchor_missing=False.
    """
    # Passport at top level
    _make_passport(tmp_path)
    # Anchor at sub level
    sub = tmp_path / "sub"
    sub.mkdir()
    _make_anchor(sub)
    deep = sub / "deep"
    deep.mkdir()

    result_root = find_project_root(deep)
    assert result_root == sub

    root, anchor_missing = find_anchor_state(deep)
    assert root == sub
    assert anchor_missing is False


def test_find_project_root_both_files_same_dir_prefer_anchor(tmp_path: Path) -> None:
    """Per-directory preference: both files in same dir → anchor_missing=False."""
    _make_passport(tmp_path)
    _make_anchor(tmp_path)

    root, anchor_missing = find_anchor_state(tmp_path)
    assert root == tmp_path
    assert anchor_missing is False


# ---------------------------------------------------------------------------
# ensure_anchor tests
# ---------------------------------------------------------------------------


def test_ensure_anchor_idempotent_when_anchor_exists(tmp_path: Path) -> None:
    """Pre-existing anchor: ensure_anchor is a no-op read; returns loaded anchor."""
    _make_passport(tmp_path)
    _make_anchor(tmp_path)

    anchor_path = tmp_path / AWF_DIRNAME / PROJECT_FILENAME
    original_content = anchor_path.read_bytes()

    from lib.state import ProjectAnchor
    result = ensure_anchor(tmp_path)

    assert isinstance(result, ProjectAnchor)
    # File content must be byte-identical (no re-creation occurred)
    assert anchor_path.read_bytes() == original_content


def test_ensure_anchor_migrates_from_passport(tmp_path: Path) -> None:
    """Passport-only project: ensure_anchor creates .awf/project.json."""
    _make_passport(tmp_path, domain="mysite.com")

    anchor_path = tmp_path / AWF_DIRNAME / PROJECT_FILENAME
    assert not anchor_path.exists()

    from lib.state import ProjectAnchor
    result = ensure_anchor(tmp_path)

    assert isinstance(result, ProjectAnchor)
    assert anchor_path.exists()

    data = json.loads(anchor_path.read_text(encoding="utf-8"))
    assert data["stage"] == "landing"
    assert data["has"]["passport"] is True
    assert data["domain"] == "mysite.com"
    assert data["slug"] == "mysite-com"


def test_ensure_anchor_idempotent_after_migration(tmp_path: Path) -> None:
    """Calling ensure_anchor twice preserves the created timestamp verbatim."""
    _make_passport(tmp_path)

    anchor_path = tmp_path / AWF_DIRNAME / PROJECT_FILENAME

    first = ensure_anchor(tmp_path)
    first_content = anchor_path.read_bytes()
    first_created = first.created

    second = ensure_anchor(tmp_path)
    second_content = anchor_path.read_bytes()

    # File content byte-identical between calls
    assert first_content == second_content
    # created timestamp preserved
    assert second.created == first_created


def test_ensure_anchor_emits_state_change_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ensure_anchor calls log.state_change exactly once on first call, not on second."""
    _make_passport(tmp_path)

    fake = FakeLog()
    monkeypatch.setitem(sys.modules, "lib.log", fake)  # type: ignore[arg-type]

    ensure_anchor(tmp_path)
    assert len(fake.calls) == 1

    ensure_anchor(tmp_path)
    # Second call is a no-op read; no additional state.change emission
    assert len(fake.calls) == 1


def test_ensure_anchor_raises_on_empty_directory(tmp_path: Path) -> None:
    """Empty tmp_path (neither anchor nor passport): raises ProjectNotFound."""
    with pytest.raises(ProjectNotFound):
        ensure_anchor(tmp_path)
