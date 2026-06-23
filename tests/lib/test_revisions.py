"""Tests for applied-revisions (lib/revisions.py, D-012 part D)."""

from __future__ import annotations

import json
from pathlib import Path

from lib import revisions


def _write_log(root: Path, events: list[dict]) -> None:
    (root / ".awf").mkdir(parents=True, exist_ok=True)
    (root / ".awf" / "log.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )


def _sc(ts, skill, session, file="passport.json", added=None, removed=None, changed=None):
    return {
        "type": "state.change", "ts": ts, "skill": skill, "session": session,
        "data": {"file": file, "key": "", "diff": {
            "added": added or [], "removed": removed or [], "changed": changed or []}},
    }


def test_revisions_numbered_in_log_order(tmp_path):
    root = tmp_path / "proj"
    _write_log(root, [
        _sc("2026-06-01T10:00:00Z", "awf-create-project", "S1", added=["domain", "project_name"]),
        {"type": "skill.invoke", "ts": "2026-06-01T10:00:01Z"},  # ignored
        _sc("2026-06-02T10:00:00Z", "awf-setup-domain", "S2", changed=["launch"]),
        _sc("2026-06-03T10:00:00Z", "awf-setup-analytics", "S3", changed=["fathom_site_id"]),
    ])
    revs = revisions.project_revisions(root)
    assert [r.n for r in revs] == [1, 2, 3]
    assert revs[0].skill == "awf-create-project"
    assert revs[0].added == ["domain", "project_name"]
    assert revs[2].n == 3 and revs[2].skill == "awf-setup-analytics"
    # newest is the last / highest n
    assert revs[-1].n == 3


def test_summary_and_touched(tmp_path):
    root = tmp_path / "proj"
    _write_log(root, [
        _sc("2026-06-01T10:00:00Z", "awf-x", "S1", added=["a"], removed=["b"], changed=["c", "d"]),
    ])
    rev = revisions.project_revisions(root)[0]
    assert rev.summary() == "+1 -1 ~2"
    assert rev.touched() == ["a", "b", "c", "d"]


def test_empty_diff_still_counts_as_revision(tmp_path):
    root = tmp_path / "proj"
    _write_log(root, [_sc("2026-06-01T10:00:00Z", "awf-x", "S1")])
    rev = revisions.project_revisions(root)[0]
    assert rev.n == 1
    assert rev.summary() == "(no field change)"


def test_get_revision_and_missing(tmp_path):
    root = tmp_path / "proj"
    _write_log(root, [
        _sc("2026-06-01T10:00:00Z", "awf-a", "S1"),
        _sc("2026-06-02T10:00:00Z", "awf-b", "S2"),
    ])
    assert revisions.get_revision(root, 2).skill == "awf-b"
    assert revisions.get_revision(root, 99) is None


def test_no_log_yields_no_revisions(tmp_path):
    assert revisions.project_revisions(tmp_path / "nothing") == []


def test_resolve_project_root_by_path_slug_and_current(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    proj = tmp_path / "example-com"
    (proj / ".awf").mkdir(parents=True)
    (proj / "passport.json").write_text(json.dumps({"project_name": "example-com"}), encoding="utf-8")
    (config_dir / "sessions.jsonl").write_text(
        json.dumps({"project_slug": "example-com", "project_path": str(proj)}) + "\n",
        encoding="utf-8",
    )
    # explicit path
    assert revisions.resolve_project_root(config_dir, str(proj)) == proj
    # by slug, via the sessions index
    assert revisions.resolve_project_root(config_dir, "example-com") == proj
    # unknown slug
    assert revisions.resolve_project_root(config_dir, "nope") is None
    # current project (cwd inside it)
    assert revisions.resolve_project_root(config_dir, None, cwd=proj) == proj
