"""Integration tests for skills/awf-log (plan_011).

Covers all seven sub-commands:
  - tail: last N events, missing log, empty log, --json mode
  - session: by id, by 'last', missing session, in-progress (no session.end)
  - find: pattern match, --type filter, bad regex
  - diff: stub exits 0 with redirect message
  - note: appends event, no project exits 1
  - replay: narrative output, in-progress session, missing session
  - sessions: table output, --days filter, --json mode, empty index

Mocking strategy: tests work with real files in tmp_path. The module is
imported via the module-loader pattern (sys.modules registration before
exec_module) to match other skill tests in this suite.
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

import lib.awf_home as awf_home_module  # noqa: E402
from lib import log as log_lib  # noqa: E402
from lib.log import LOG_DIRNAME, LOG_FILENAME, SESSIONS_INDEX_FILENAME  # noqa: E402

# ---------------------------------------------------------------------------
# Module import helper
# ---------------------------------------------------------------------------


def _import_awf_log():
    """Import and return the awf-log script module."""
    script_path = _SKILLS_DIR / "awf-log" / "scripts" / "log.py"
    spec = importlib.util.spec_from_file_location("awf_log_script", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so any relative-import-like patterns work
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# Cache the import so tests share the loaded module
_AWF_LOG = None


def _get_awf_log():
    global _AWF_LOG
    if _AWF_LOG is None:
        _AWF_LOG = _import_awf_log()
    return _AWF_LOG


# ---------------------------------------------------------------------------
# Fixtures: minimal project + log helpers
# ---------------------------------------------------------------------------

_MINIMAL_ANCHOR: dict[str, Any] = {
    "awf_version": "0.1.0",
    "domain": "example.com",
    "slug": "example-com",
    "stage": "landing",
    "created": "2026-06-01T00:00:00Z",
    "has": {"passport": False, "infra": False, "kamal": False, "content": False},
}


def _make_project(root: Path, *, domain: str = "example.com") -> Path:
    """Create a minimal .awf project structure; return root."""
    awf_dir = root / ".awf"
    awf_dir.mkdir(parents=True, exist_ok=True)
    anchor = dict(_MINIMAL_ANCHOR)
    anchor["domain"] = domain
    anchor["slug"] = domain.replace(".", "-")
    (awf_dir / "project.json").write_text(json.dumps(anchor) + "\n", encoding="utf-8")
    # passport.json expected by find_project_root walk
    (root / "passport.json").write_text('{"domain": "' + domain + '"}', encoding="utf-8")
    return root


def _write_log_events(root: Path, events: list[dict[str, Any]]) -> Path:
    """Write a canned list of events to the project log; return the log path."""
    log_path = root / LOG_DIRNAME / LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, separators=(",", ":"), ensure_ascii=False) + "\n")
    return log_path


def _canned_session(
    sid: str,
    *,
    composer: str = "awf-test",
    target: str = "landing",
    with_end: bool = True,
    extra_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return a minimal list of events representing one session."""
    ts_start = "2026-06-01T10:00:00.000Z"
    ts_end = "2026-06-01T10:00:05.000Z"
    events: list[dict[str, Any]] = [
        {
            "ts": ts_start,
            "session": sid,
            "project": "example-com",
            "stage": "landing",
            "actor": "claude-code",
            "type": "session.start",
            "data": {"composer": composer, "target_stage": target, "source_stage": ""},
        }
    ]
    if extra_events:
        events.extend(extra_events)
    if with_end:
        events.append(
            {
                "ts": ts_end,
                "session": sid,
                "project": "example-com",
                "stage": "landing",
                "actor": "claude-code",
                "type": "session.end",
                "result": "ok",
                "duration_ms": 5000,
                "data": {"events_count": 0, "gates_hit": 0, "summary": "ok"},
            }
        )
    return events


def _canned_sessions_index(
    entries: list[dict[str, Any]],
) -> bytes:
    """Serialise a list of session summary dicts to JSONL bytes."""
    lines = [json.dumps(e, separators=(",", ":"), ensure_ascii=False) for e in entries]
    return "\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# Autouse: reset dry-run flag + ContextVars
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup_log_state():
    """Reset log ContextVars and dry-run flag after each test."""
    yield
    log_lib.set_dry_run(False)
    # Reset ContextVars to defaults
    log_lib._current_project_root.set(None)
    log_lib._current_project_slug.set("")
    log_lib._current_stage.set("")
    log_lib._current_actor.set("claude-code")
    log_lib._current_summary.set(None)


# ===========================================================================
# tail tests
# ===========================================================================


class TestTail:
    def test_tail_no_project_exits_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """tail exits 1 when no project is found."""
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["tail"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "error" in captured.err

    def test_tail_empty_log_exits_0(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """tail on an empty log file exits 0 with an informational message."""
        _make_project(tmp_path)
        log_path = tmp_path / LOG_DIRNAME / LOG_FILENAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["tail"])
        assert rc == 0

    def test_tail_missing_log_exits_0(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """tail on a project with no log file yet exits 0 gracefully."""
        _make_project(tmp_path)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["tail"])
        assert rc == 0

    def test_tail_returns_last_n_events(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """tail -n 3 returns exactly 3 events, oldest first."""
        _make_project(tmp_path)
        events = [
            {"ts": f"2026-06-01T10:00:0{i}.000Z", "session": "A", "type": "note",
             "project": "x", "stage": "landing", "actor": "cli", "data": {"text": f"note {i}", "by": "human"}}
            for i in range(10)
        ]
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["tail", "-n", "3"])
        assert rc == 0
        captured = capsys.readouterr()
        # Banner should mention 3 events
        assert "3" in captured.out

    def test_tail_json_mode_emits_jsonl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """tail --json emits valid JSONL with no banner."""
        _make_project(tmp_path)
        events = _canned_session("SID001")
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["tail", "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.splitlines() if ln.strip()]
        assert len(lines) > 0
        for line in lines:
            json.loads(line)  # must be valid JSON
        # No banner in json mode
        assert "#" not in captured.out

    def test_tail_banner_in_human_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """tail without --json shows a banner line."""
        _make_project(tmp_path)
        events = _canned_session("SID001")
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["tail"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "# last" in captured.out
        assert "oldest first" in captured.out


# ===========================================================================
# session tests
# ===========================================================================


class TestSession:
    def test_session_last_finds_most_recent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """session last returns events from the most recently started session."""
        _make_project(tmp_path)
        sid1 = "01HZK7M3AAAAAAAAAAAAAAAAAA"
        sid2 = "01HZK7M3BBBBBBBBBBBBBBBBBB"
        events = _canned_session(sid1) + _canned_session(sid2)
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["session", "last", "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.splitlines() if ln.strip()]
        assert len(lines) > 0
        # All returned events must be from sid2
        for line in lines:
            ev = json.loads(line)
            assert ev["session"] == sid2

    def test_session_by_explicit_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """session <id> returns only events for that specific session."""
        _make_project(tmp_path)
        sid1 = "01HZK7M3AAAAAAAAAAAAAAAAAA"
        sid2 = "01HZK7M3BBBBBBBBBBBBBBBBBB"
        events = _canned_session(sid1) + _canned_session(sid2)
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["session", sid1, "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.splitlines() if ln.strip()]
        for line in lines:
            ev = json.loads(line)
            assert ev["session"] == sid1

    def test_session_unknown_id_exits_4(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """session <unknown-id> exits 4."""
        _make_project(tmp_path)
        events = _canned_session("REAL0000000000000000000000")
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["session", "DOESNOTEXIST0000000000000"])
        assert rc == 4

    def test_session_empty_log_exits_4_for_last(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """session last on an empty log exits 4."""
        _make_project(tmp_path)
        log_path = tmp_path / LOG_DIRNAME / LOG_FILENAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["session", "last"])
        assert rc == 4

    def test_session_in_progress_no_end(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """session with no session.end returns all events up to end of log."""
        _make_project(tmp_path)
        sid = "INPROG000000000000000000AA"
        events = _canned_session(sid, with_end=False)
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["session", sid, "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.splitlines() if ln.strip()]
        assert len(lines) >= 1  # at least session.start


# ===========================================================================
# find tests
# ===========================================================================


class TestFind:
    def test_find_matches_events(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """find returns events matching the regex pattern."""
        _make_project(tmp_path)
        sid = "FINDTEST00000000000000000A"
        extra = [
            {"ts": "2026-06-01T10:00:01.000Z", "session": sid, "type": "note",
             "project": "x", "stage": "landing", "actor": "cli",
             "data": {"text": "hello world", "by": "human"}},
            {"ts": "2026-06-01T10:00:02.000Z", "session": sid, "type": "note",
             "project": "x", "stage": "landing", "actor": "cli",
             "data": {"text": "goodbye", "by": "human"}},
        ]
        events = _canned_session(sid, extra_events=extra)
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["find", "hello"])
        assert rc == 0
        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.splitlines() if ln.strip()]
        assert len(lines) == 1
        ev = json.loads(lines[0])
        assert "hello" in ev["data"]["text"]

    def test_find_type_filter(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """find --type filters events to the given type before regex match."""
        _make_project(tmp_path)
        sid = "FINDTEST00000000000000000B"
        extra = [
            {"ts": "2026-06-01T10:00:01.000Z", "session": sid, "type": "note",
             "project": "x", "stage": "landing", "actor": "cli",
             "data": {"text": "match this", "by": "human"}},
            {"ts": "2026-06-01T10:00:02.000Z", "session": sid, "type": "error",
             "project": "x", "stage": "landing", "actor": "cli",
             "data": {"message": "match this too"}},
        ]
        events = _canned_session(sid, extra_events=extra)
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["find", "match", "--type", "note"])
        assert rc == 0
        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.splitlines() if ln.strip()]
        assert len(lines) == 1
        ev = json.loads(lines[0])
        assert ev["type"] == "note"

    def test_find_bad_regex_exits_4(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """find with an invalid regex exits 4."""
        _make_project(tmp_path)
        _write_log_events(tmp_path, _canned_session("SID"))
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["find", "[invalid("])
        assert rc == 4

    def test_find_no_match_exits_0(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """find with no matches exits 0 (not an error)."""
        _make_project(tmp_path)
        _write_log_events(tmp_path, _canned_session("SID"))
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["find", "NEVERINTHELOG12345"])
        assert rc == 0


# ===========================================================================
# diff tests
# ===========================================================================


class TestDiff:
    def test_diff_exits_0_with_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """diff is a stub that exits 0 and prints a redirect message."""
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["diff"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "awf-status" in captured.out
        assert "plan_012" in captured.out


# ===========================================================================
# note tests
# ===========================================================================


class TestNote:
    def test_note_appends_event(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """note appends a note event to the project log and exits 0."""
        _make_project(tmp_path)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["note", "manual checkpoint reached"])
        assert rc == 0
        # Read the log and confirm event was written
        log_path = tmp_path / LOG_DIRNAME / LOG_FILENAME
        assert log_path.exists()
        lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) >= 1
        note_events = [json.loads(ln) for ln in lines if json.loads(ln).get("type") == "note"]
        assert len(note_events) == 1
        assert note_events[0]["data"]["text"] == "manual checkpoint reached"
        assert note_events[0]["data"]["by"] == "human"

    def test_note_no_project_exits_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """note exits 1 when no project is found."""
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["note", "orphan note"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "error" in captured.err


# ===========================================================================
# replay tests
# ===========================================================================


class TestReplay:
    def test_replay_last_outputs_narrative(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """replay last outputs a narrative including session ID, composer, result."""
        _make_project(tmp_path)
        sid = "REPLAY00000000000000000000"
        events = _canned_session(sid, composer="awf-test", target="landing")
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["replay", "last"])
        assert rc == 0
        captured = capsys.readouterr()
        assert sid in captured.out
        assert "awf-test" in captured.out
        assert "landing" in captured.out

    def test_replay_in_progress_session(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """replay on an in-progress session (no session.end) notes it in output."""
        _make_project(tmp_path)
        sid = "INPROG000000000000000000AA"
        events = _canned_session(sid, with_end=False)
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["replay", sid])
        assert rc == 0
        captured = capsys.readouterr()
        assert "no session.end recorded" in captured.out

    def test_replay_unknown_session_exits_4(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """replay on unknown session exits 4."""
        _make_project(tmp_path)
        _write_log_events(tmp_path, _canned_session("REAL"))
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["replay", "DOESNOTEXIST"])
        assert rc == 4

    def test_replay_shows_skills_and_result(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """replay renders skill steps and their results."""
        _make_project(tmp_path)
        sid = "REPLAYSTEPS000000000000000"
        extra = [
            {"ts": "2026-06-01T10:00:01.000Z", "session": sid, "type": "skill.invoke",
             "skill": "awf-hetzner-server", "result": "pending",
             "project": "x", "stage": "landing", "actor": "claude-code",
             "data": {"args": {}}},
            {"ts": "2026-06-01T10:00:02.000Z", "session": sid, "type": "skill.complete",
             "skill": "awf-hetzner-server", "result": "ok",
             "project": "x", "stage": "landing", "actor": "claude-code",
             "data": {"args": {}}},
        ]
        events = _canned_session(sid, extra_events=extra)
        _write_log_events(tmp_path, events)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["replay", sid])
        assert rc == 0
        captured = capsys.readouterr()
        assert "awf-hetzner-server" in captured.out
        assert "ok" in captured.out


# ===========================================================================
# sessions tests
# ===========================================================================


class TestSessions:
    """Tests for the sessions sub-command.

    The sessions sub-command calls user_config_dir() at runtime via the
    awf_home module imported inside skills/awf-log/scripts/log.py. We
    monkeypatch the awf_home module that the skill imports (not lib.awf_home
    directly) to redirect index reads to a tmp directory.
    """

    def _patch_user_config_dir(self, monkeypatch, user_dir: Path):
        """Patch user_config_dir in the skill module and awf_home module."""
        monkeypatch.setattr(awf_home_module, "user_config_dir", lambda: user_dir)
        mod = _get_awf_log()
        # Also patch the reference cached inside the skill module
        monkeypatch.setattr(mod, "user_config_dir", lambda: user_dir)

    def test_sessions_empty_index_exits_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """sessions with empty index exits 0 with informational message."""
        user_dir = tmp_path / "user-awf"
        user_dir.mkdir(parents=True, exist_ok=True)
        self._patch_user_config_dir(monkeypatch, user_dir)
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["sessions"])
        assert rc == 0

    def test_sessions_table_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """sessions prints a table with headers."""
        user_dir = tmp_path / "user-awf"
        user_dir.mkdir(parents=True, exist_ok=True)
        self._patch_user_config_dir(monkeypatch, user_dir)
        # Write two session index entries
        now = datetime.now(timezone.utc)
        entries = [
            {
                "session_id": "SID001",
                "project_slug": "example-com",
                "project_path": "/tmp/example",
                "composer": "awf-stage-mvp-play",
                "target": "mvp-play",
                "started_at": now.isoformat().replace("+00:00", "Z"),
                "ended_at": now.isoformat().replace("+00:00", "Z"),
                "events_count": 12,
                "gates_hit": 0,
                "result": "ok",
            },
            {
                "session_id": "SID002",
                "project_slug": "another-site",
                "project_path": "/tmp/another",
                "composer": "awf-test",
                "target": "landing",
                "started_at": now.isoformat().replace("+00:00", "Z"),
                "ended_at": now.isoformat().replace("+00:00", "Z"),
                "events_count": 3,
                "gates_hit": 1,
                "result": "fail",
            },
        ]
        index_path = user_dir / SESSIONS_INDEX_FILENAME
        index_path.write_bytes(_canned_sessions_index(entries))
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["sessions"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "example-com" in captured.out
        assert "another-site" in captured.out
        assert "ok" in captured.out
        assert "fail" in captured.out

    def test_sessions_json_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """sessions --json emits valid JSONL."""
        user_dir = tmp_path / "user-awf"
        user_dir.mkdir(parents=True, exist_ok=True)
        self._patch_user_config_dir(monkeypatch, user_dir)
        now = datetime.now(timezone.utc)
        entries = [
            {
                "session_id": "JSONTEST",
                "project_slug": "foo",
                "project_path": "/tmp/foo",
                "composer": "awf-test",
                "target": "landing",
                "started_at": now.isoformat().replace("+00:00", "Z"),
                "ended_at": now.isoformat().replace("+00:00", "Z"),
                "events_count": 5,
                "gates_hit": 0,
                "result": "ok",
            }
        ]
        (user_dir / SESSIONS_INDEX_FILENAME).write_bytes(_canned_sessions_index(entries))
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["sessions", "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.splitlines() if ln.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["session_id"] == "JSONTEST"

    def test_sessions_days_filter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """sessions --days filters out old entries."""
        user_dir = tmp_path / "user-awf"
        user_dir.mkdir(parents=True, exist_ok=True)
        self._patch_user_config_dir(monkeypatch, user_dir)
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=60)
        entries = [
            {
                "session_id": "RECENT",
                "project_slug": "recent-proj",
                "project_path": "/tmp/r",
                "composer": "awf-test",
                "target": "landing",
                "started_at": now.isoformat().replace("+00:00", "Z"),
                "ended_at": now.isoformat().replace("+00:00", "Z"),
                "events_count": 1,
                "gates_hit": 0,
                "result": "ok",
            },
            {
                "session_id": "OLD",
                "project_slug": "old-proj",
                "project_path": "/tmp/o",
                "composer": "awf-test",
                "target": "landing",
                "started_at": old.isoformat().replace("+00:00", "Z"),
                "ended_at": old.isoformat().replace("+00:00", "Z"),
                "events_count": 1,
                "gates_hit": 0,
                "result": "ok",
            },
        ]
        (user_dir / SESSIONS_INDEX_FILENAME).write_bytes(_canned_sessions_index(entries))
        mod = _get_awf_log()
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["sessions", "--days", "30"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "recent-proj" in captured.out
        assert "old-proj" not in captured.out


# ===========================================================================
# Read API unit tests (lib/log.py read helpers)
# ===========================================================================


class TestReadHelpers:
    """Direct unit tests for the lib/log.py Read API added in plan_011."""

    def test_read_events_yields_all_valid_lines(self, tmp_path: Path) -> None:
        """read_events yields all valid JSON lines from a file."""
        from lib.log import read_events

        log_path = tmp_path / "test.jsonl"
        events = _canned_session("SID_READ_001")
        log_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
        )
        result = list(read_events(log_path))
        assert len(result) == len(events)

    def test_read_events_skips_malformed_lines(self, tmp_path: Path, capsys) -> None:
        """read_events skips malformed lines with stderr warning."""
        from lib.log import read_events

        log_path = tmp_path / "test.jsonl"
        log_path.write_text(
            '{"type":"note"}\n{bad json}\n{"type":"error"}\n', encoding="utf-8"
        )
        result = list(read_events(log_path))
        assert len(result) == 2
        captured = capsys.readouterr()
        assert "warn" in captured.err

    def test_read_events_missing_file_yields_nothing(self, tmp_path: Path) -> None:
        """read_events on a missing file yields nothing without raising."""
        from lib.log import read_events

        result = list(read_events(tmp_path / "nonexistent.jsonl"))
        assert result == []

    def test_tail_events_returns_last_n(self, tmp_path: Path) -> None:
        """tail_events returns exactly the last n events, oldest first."""
        from lib.log import tail_events

        log_path = tmp_path / "test.jsonl"
        events = [{"type": "note", "n": i} for i in range(20)]
        log_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
        )
        result = tail_events(log_path, 5)
        assert len(result) == 5
        # Oldest of tail first: events[15..19]
        assert result[0]["n"] == 15
        assert result[-1]["n"] == 19

    def test_tail_events_n_larger_than_file(self, tmp_path: Path) -> None:
        """tail_events with n > file size returns all events."""
        from lib.log import tail_events

        log_path = tmp_path / "test.jsonl"
        events = [{"type": "note", "n": i} for i in range(3)]
        log_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
        )
        result = tail_events(log_path, 100)
        assert len(result) == 3

    def test_tail_events_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """tail_events on missing file returns empty list."""
        from lib.log import tail_events

        result = tail_events(tmp_path / "nope.jsonl", 10)
        assert result == []

    def test_iter_sessions_yields_entries(self, tmp_path: Path) -> None:
        """iter_sessions yields all session summary dicts."""
        from lib.log import iter_sessions

        index_path = tmp_path / "sessions.jsonl"
        entries = [{"session_id": "A", "result": "ok"}, {"session_id": "B", "result": "fail"}]
        index_path.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
        )
        result = list(iter_sessions(index_path))
        assert len(result) == 2
        assert result[0]["session_id"] == "A"

    def test_find_session_bounds_found(self, tmp_path: Path) -> None:
        """find_session_bounds returns (0, n-1) for a complete session at start."""
        from lib.log import find_session_bounds

        sid = "BOUNDS_TEST0000000000000AA"
        events = _canned_session(sid)
        bounds = find_session_bounds(events, sid)
        assert bounds is not None
        start, end = bounds
        assert events[start]["type"] == "session.start"
        assert events[end]["type"] == "session.end"

    def test_find_session_bounds_not_found_returns_none(self) -> None:
        """find_session_bounds returns None for an unknown session."""
        from lib.log import find_session_bounds

        events = _canned_session("REAL_SESSION00000000000000")
        result = find_session_bounds(events, "UNKNOWN000000000000000000")
        assert result is None

    def test_find_session_bounds_in_progress_returns_last_index(self) -> None:
        """find_session_bounds for in-progress session returns last event as end."""
        from lib.log import find_session_bounds

        sid = "INPROG_BOUNDS0000000000000"
        events = _canned_session(sid, with_end=False)
        bounds = find_session_bounds(events, sid)
        assert bounds is not None
        start, end = bounds
        assert end == len(events) - 1

    def test_latest_session_id_returns_most_recent(self, tmp_path: Path) -> None:
        """latest_session_id returns the most recently started session's ID."""
        from lib.log import latest_session_id

        sid1 = "01HZK7M3AAAAAAAAAAAAAAAAAA"
        sid2 = "01HZK7M3BBBBBBBBBBBBBBBBBB"
        events = _canned_session(sid1) + _canned_session(sid2)
        log_path = tmp_path / "test.jsonl"
        log_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
        )
        result = latest_session_id(log_path)
        assert result == sid2

    def test_latest_session_id_missing_file_returns_none(self, tmp_path: Path) -> None:
        """latest_session_id on missing file returns None."""
        from lib.log import latest_session_id

        result = latest_session_id(tmp_path / "nope.jsonl")
        assert result is None
