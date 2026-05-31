"""Tests for lib/log.py.

Covers all acceptance criteria from plan_003_foundation_logging_lib.md.
Uses real temp directories (tmp_path fixture), real files, real JSON,
and real fcntl.flock.

Design notes:
- Tests access _current_session_id / _current_summary directly where
  needed to verify ContextVar reset semantics. This is equivalent to
  plan_001's direct _path access — test-internal instrumentation, not
  a test of implementation details.
- The _reset_dry_run autouse fixture guards every test against _dry_run_active
  leaking. Any test that sets the flag must not rely on teardown only —
  the fixture ensures reset even on assertion failure (N2 from plan_003 Pass 1).
"""

from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path
from typing import Iterator

import pytest

import lib.awf_home as awf_home_module
from lib import log
from lib.log import (
    LOG_DIRNAME,
    LOG_FILENAME,
    SESSIONS_INDEX_FILENAME,
    _CROCKFORD_ALPHABET,
    _current_session_id,
    _ulid,
)

# ---------------------------------------------------------------------------
# Module-wide autouse fixture: always reset _dry_run_active
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_dry_run() -> Iterator[None]:
    """Ensure _dry_run_active is always False after each test.

    A bare log.set_dry_run(False) at the end of a test body is NOT acceptable:
    if the assertion fails, the flag stays True and contaminates later tests.
    This autouse fixture guarantees reset regardless of test outcome.
    """
    yield
    log.set_dry_run(False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal awf project structure and return the project root."""
    awf_dir = tmp_path / ".awf"
    awf_dir.mkdir()
    anchor = {
        "awf_version": "0.1.0",
        "domain": "example.com",
        "slug": "example",
        "stage": "landing",
        "created": "2026-05-31T20:00:00Z",
        "has": {"passport": False, "infra": False, "kamal": False, "content": False},
    }
    (awf_dir / "project.json").write_text(json.dumps(anchor), encoding="utf-8")
    return tmp_path


def _read_log_lines(project_root: Path) -> list[dict]:  # type: ignore[type-arg]
    """Read all events from the project log as parsed dicts."""
    log_path = project_root / LOG_DIRNAME / LOG_FILENAME
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_raw_lines(path: Path) -> list[str]:
    """Read non-empty lines from a jsonl file."""
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# ULID tests
# ---------------------------------------------------------------------------


def test_ulid_format_is_26_crockford_base32() -> None:
    """_ulid() returns a 26-character string using only Crockford base32 chars."""
    ulid = _ulid()
    assert len(ulid) == 26
    assert all(c in _CROCKFORD_ALPHABET for c in ulid)


def test_ulid_generated_1ms_apart_sorts_earlier_first() -> None:
    """Two ULIDs generated ≥5 ms apart sort with the earlier one first.

    Using a single pair with sleep(0.005) — comfortably above the 1ms
    timestamp resolution on any reasonable host (N1 from plan_003 Pass 1).
    Do NOT test bulk concurrent ordering — that is luck-dependent on loaded CI.
    """
    ulid_a = _ulid()
    time.sleep(0.005)
    ulid_b = _ulid()
    assert ulid_a < ulid_b


# ---------------------------------------------------------------------------
# Redaction tests
# ---------------------------------------------------------------------------


def test_safe_log_redacts_authorization_header() -> None:
    """Authorization key value is replaced with ***."""
    result = log.safe_log({"Authorization": "Bearer xyz"})
    assert result == {"Authorization": "***"}


def test_safe_log_redacts_token_suffix_keys() -> None:
    """Keys ending in _token, _secret, _password, _key are redacted; _key_id is preserved."""
    data = {
        "API_TOKEN": "secret",
        "cloudflare_token": "cftoken",
        "MY_SECRET": "s3cr3t",
        "DB_PASSWORD": "pass123",
        "SOME_KEY": "keyval",
        "API_KEY_ID": "safe-id",  # allowlisted suffix — must NOT be redacted
    }
    result = log.safe_log(data)
    assert result["API_TOKEN"] == "***"
    assert result["cloudflare_token"] == "***"
    assert result["MY_SECRET"] == "***"
    assert result["DB_PASSWORD"] == "***"
    assert result["SOME_KEY"] == "***"
    assert result["API_KEY_ID"] == "safe-id"  # preserved


def test_safe_log_redacts_bearer_in_string_value() -> None:
    """Bearer token in a string value is scrubbed to ***."""
    data = {"args": "curl -H 'Authorization: Bearer sk-abc123'"}
    result = log.safe_log(data)
    assert "Bearer" not in result["args"]
    assert "sk-abc123" not in result["args"]
    assert "***" in result["args"]


def test_safe_log_redacts_sk_pat_hk_tok_prefixed_strings() -> None:
    """Each of the four secret prefixes is scrubbed from string values."""
    for prefix in ("sk-", "pat-", "hk-", "tok-"):
        value = f"token is {prefix}abc1234567890xyz here"
        result = log.safe_log(value)
        assert f"{prefix}abc1234567890xyz" not in result, f"Prefix {prefix} not scrubbed"
        assert "***" in result


def test_safe_log_on_unknown_type_returns_unchanged() -> None:
    """safe_log on an arbitrary object returns it unchanged without raising."""

    class _Arbitrary:
        pass

    obj = _Arbitrary()
    result = log.safe_log(obj)
    assert result is obj


def test_safe_log_recurses_into_nested_dicts() -> None:
    """Nested dict keys are also redacted."""
    data = {"outer": {"inner_token": "secret"}}
    result = log.safe_log(data)
    assert result["outer"]["inner_token"] == "***"


def test_safe_log_recurses_into_lists() -> None:
    """List elements are recursed into."""
    data = [{"Authorization": "Bearer token123"}, "plain"]
    result = log.safe_log(data)
    assert result[0]["Authorization"] == "***"
    assert result[1] == "plain"


# ---------------------------------------------------------------------------
# Event round-trip and JSON validity
# ---------------------------------------------------------------------------


def test_session_writes_start_and_end_events(tmp_path: Path) -> None:
    """session() emits exactly session.start and session.end with same session ID."""
    project_root = _make_project(tmp_path)
    with log.session("test-composer", "landing", start=project_root) as sid:
        pass

    events = _read_log_lines(project_root)
    assert len(events) == 2
    assert events[0]["type"] == "session.start"
    assert events[1]["type"] == "session.end"
    assert events[0]["session"] == sid
    assert events[1]["session"] == sid
    assert events[1]["duration_ms"] >= 0


def test_invoke_writes_invoke_and_complete_events(tmp_path: Path) -> None:
    """invoke() within a session emits skill.invoke and skill.complete."""
    project_root = _make_project(tmp_path)
    with log.session("test-composer", "landing", start=project_root) as sid:
        with log.invoke("awf-test-skill", args={"region": "fsn1"}):
            pass

    events = _read_log_lines(project_root)
    skill_events = [e for e in events if e["type"] in ("skill.invoke", "skill.complete")]
    assert len(skill_events) == 2
    assert skill_events[0]["type"] == "skill.invoke"
    assert skill_events[1]["type"] == "skill.complete"
    assert skill_events[0]["skill"] == "awf-test-skill"
    assert skill_events[1]["skill"] == "awf-test-skill"
    assert skill_events[0]["session"] == sid
    assert skill_events[1]["session"] == sid


def test_event_is_valid_jsonl(tmp_path: Path) -> None:
    """All events in the log are valid JSON lines."""
    project_root = _make_project(tmp_path)
    with log.session("test-composer", "landing", start=project_root):
        with log.invoke("skill-a"):
            log.api("hetzner", "POST", "/servers", 201, "srv_123")
        log.gate("ns_swap", "Manual NS change required")
        log.error("something broke", hint="check logs")
        log.note("manual note")

    log_path = project_root / LOG_DIRNAME / LOG_FILENAME
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            parsed = json.loads(line)  # raises if invalid
            assert "ts" in parsed
            assert "type" in parsed


# ---------------------------------------------------------------------------
# ContextVar threading
# ---------------------------------------------------------------------------


def test_session_id_inherited_by_nested_invoke(tmp_path: Path) -> None:
    """session, invoke, and api all share the same session field."""
    project_root = _make_project(tmp_path)
    with log.session("test-composer", "landing", start=project_root) as sid:
        with log.invoke("inner-skill"):
            log.api("cloudflare", "GET", "/zones", 200)

    events = _read_log_lines(project_root)
    # All events (session.start, skill.invoke, api.call, skill.complete, session.end)
    # must share the same session ID
    for event in events:
        assert event["session"] == sid, f"Event {event['type']} has wrong session"


def test_context_var_reset_after_session_exit(tmp_path: Path) -> None:
    """After session exits, _current_session_id is reset to None."""
    project_root = _make_project(tmp_path)
    with log.session("test-composer", "landing", start=project_root):
        pass
    # Test-internal access to ContextVar — acceptable per plan_003 design note
    assert _current_session_id.get() is None


def test_atomic_skill_outside_session_mints_one_event_session(tmp_path: Path) -> None:
    """api() called outside any session auto-mints a non-empty session ULID."""
    project_root = _make_project(tmp_path)
    # Manually set project root so the event goes to the project log
    tok = log._current_project_root.set(project_root)
    try:
        log.api("cloudflare", "GET", "/zones", 200)
    finally:
        log._current_project_root.reset(tok)

    events = _read_log_lines(project_root)
    assert len(events) == 1
    assert events[0]["session"]  # non-empty auto-minted ULID
    assert len(events[0]["session"]) == 26  # ULID is 26 chars


def test_api_call_outside_invoke_has_no_skill_key(tmp_path: Path) -> None:
    """api() called outside any invoke context emits an event with no 'skill' key (absent, not null)."""
    project_root = _make_project(tmp_path)
    with log.session("test-composer", "landing", start=project_root):
        log.api("cloudflare", "GET", "/zones", 200)

    events = _read_log_lines(project_root)
    api_event = next(e for e in events if e["type"] == "api.call")
    assert "skill" not in api_event  # absent, not null


def test_summary_mutators_outside_session_do_not_raise(tmp_path: Path) -> None:
    """api(), gate(), and error() called outside any session do not raise."""
    project_root = _make_project(tmp_path)
    tok = log._current_project_root.set(project_root)
    try:
        # Should not raise even though _current_summary is None
        log.api("cf", "GET", "/zones", 200)
        log.gate("my-gate", "reason")
        log.error("boom")
    finally:
        log._current_project_root.reset(tok)

    events = _read_log_lines(project_root)
    assert len(events) == 3


# ---------------------------------------------------------------------------
# Concurrency / durability (spec AC1)
# ---------------------------------------------------------------------------


def _worker_write(args: tuple[str, int]) -> None:
    """Worker function for concurrency test: emit N api.call events."""
    project_root_str, n = args
    project_root = Path(project_root_str)
    tok = log._current_project_root.set(project_root)
    try:
        for _ in range(n):
            log.api("hetzner", "GET", "/servers", 200)
    finally:
        log._current_project_root.reset(tok)


def test_1000_concurrent_writes_produce_1000_valid_lines(tmp_path: Path) -> None:
    """1000 concurrent appends produce 1000 valid JSON lines, no interleaving."""
    project_root = _make_project(tmp_path)
    n_workers = 8
    events_per_worker = 125  # 8 * 125 = 1000

    with multiprocessing.Pool(n_workers) as pool:
        pool.map(
            _worker_write,
            [(str(project_root), events_per_worker)] * n_workers,
        )

    log_path = project_root / LOG_DIRNAME / LOG_FILENAME
    lines = _read_raw_lines(log_path)
    assert len(lines) == 1000, f"Expected 1000 lines, got {len(lines)}"
    for line in lines:
        parsed = json.loads(line)  # raises if malformed / interleaved
        assert parsed["type"] == "api.call"


def test_oversize_event_uses_flock(tmp_path: Path) -> None:
    """An event with >4 KB data payload is written via flock and lands on disk."""
    project_root = _make_project(tmp_path)
    # Build a data payload that serialises to >4096 bytes
    large_value = "x" * 5000
    with log.session("test-composer", "landing", start=project_root):
        log.note(large_value)

    events = _read_log_lines(project_root)
    note_event = next(e for e in events if e["type"] == "note")
    assert note_event["data"]["text"] == large_value


# ---------------------------------------------------------------------------
# Best-effort / never-raises (spec AC3)
# ---------------------------------------------------------------------------


def test_log_write_to_readonly_file_warns_and_does_not_raise(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If the log file is read-only, a stderr warning is emitted and no exception raised."""
    project_root = _make_project(tmp_path)
    log_path = project_root / LOG_DIRNAME / LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    log_path.chmod(0o400)

    try:
        tok = log._current_project_root.set(project_root)
        try:
            log.api("cf", "GET", "/zones", 200)  # must not raise
        finally:
            log._current_project_root.reset(tok)
        captured = capsys.readouterr()
        assert "warn:" in captured.err
    finally:
        # Restore write permissions so tmp_path cleanup can delete the file
        log_path.chmod(0o644)


def test_invoke_args_are_redacted_on_disk(tmp_path: Path) -> None:
    """invoke() with a secret in args writes *** to disk (AC2)."""
    project_root = _make_project(tmp_path)
    with log.session("test-composer", "landing", start=project_root):
        with log.invoke("deploy-skill", args={"token": "sk-abc12345678901"}):
            pass

    events = _read_log_lines(project_root)
    invoke_event = next(e for e in events if e["type"] == "skill.invoke")
    assert invoke_event["data"]["args"]["token"] == "***"


# ---------------------------------------------------------------------------
# Central index (spec AC5)
# ---------------------------------------------------------------------------


def test_session_end_appends_to_central_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """session() exit appends one line to the central sessions.jsonl index."""
    user_dir = tmp_path / "user-awf"
    monkeypatch.setattr(awf_home_module, "user_config_dir", lambda: user_dir)

    project_root = _make_project(tmp_path)
    with log.session("test-composer", "landing", start=project_root) as sid:
        log.api("cf", "GET", "/zones", 200)

    index_path = user_dir / SESSIONS_INDEX_FILENAME
    assert index_path.exists()
    lines = _read_raw_lines(index_path)
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["session_id"] == sid
    assert entry["composer"] == "test-composer"
    assert entry["target"] == "landing"
    assert "events_count" in entry
    assert "gates_hit" in entry


def test_two_sessions_append_two_index_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two completed sessions produce two lines in the central index."""
    user_dir = tmp_path / "user-awf"
    monkeypatch.setattr(awf_home_module, "user_config_dir", lambda: user_dir)

    project_root = _make_project(tmp_path)
    with log.session("composer-a", "landing", start=project_root):
        pass
    with log.session("composer-b", "landing", start=project_root):
        pass

    index_path = user_dir / SESSIONS_INDEX_FILENAME
    lines = _read_raw_lines(index_path)
    assert len(lines) == 2


# ---------------------------------------------------------------------------
# state_change cap behaviour
# ---------------------------------------------------------------------------


def test_state_change_embeds_small_diff_verbatim(tmp_path: Path) -> None:
    """Small before/after dicts are embedded verbatim; diff summary present."""
    project_root = _make_project(tmp_path)
    tok = log._current_project_root.set(project_root)
    try:
        log.state_change(
            file=".awf/project.json",
            key="",
            before={},
            after={"foo": "bar"},
        )
    finally:
        log._current_project_root.reset(tok)

    events = _read_log_lines(project_root)
    event = next(e for e in events if e["type"] == "state.change")
    assert event["data"]["before"] == {}
    assert event["data"]["after"] == {"foo": "bar"}
    assert event["data"]["diff"]["added"] == ["foo"]
    assert "before_hash" not in event["data"]


def test_state_change_caps_oversize_side_with_hash(tmp_path: Path) -> None:
    """A before dict >2 KB is replaced with before_hash and before_pointer."""
    project_root = _make_project(tmp_path)
    # Build a dict that serialises to >2048 bytes
    large_before = {f"key_{i}": "v" * 50 for i in range(50)}  # ~3 KB
    tok = log._current_project_root.set(project_root)
    try:
        log.state_change(
            file=".awf/project.json",
            key="",
            before=large_before,
            after={},
        )
    finally:
        log._current_project_root.reset(tok)

    events = _read_log_lines(project_root)
    event = next(e for e in events if e["type"] == "state.change")
    assert "before" not in event["data"]
    assert event["data"]["before_hash"].startswith("sha256:")
    assert event["data"]["before_pointer"]  # non-empty
    # Small after side is still embedded verbatim
    assert event["data"]["after"] == {}


def test_state_change_diff_summary_always_present(tmp_path: Path) -> None:
    """diff key-lists are present even when one side is hashed."""
    project_root = _make_project(tmp_path)
    large_before = {f"key_{i}": "v" * 50 for i in range(50)}
    tok = log._current_project_root.set(project_root)
    try:
        log.state_change(
            file=".awf/project.json",
            key="",
            before=large_before,
            after={"new_key": "val"},
        )
    finally:
        log._current_project_root.reset(tok)

    events = _read_log_lines(project_root)
    event = next(e for e in events if e["type"] == "state.change")
    diff = event["data"]["diff"]
    assert "added" in diff
    assert "removed" in diff
    assert "changed" in diff


# ---------------------------------------------------------------------------
# Backwards compat with plan 001 (regression guard)
# ---------------------------------------------------------------------------


def test_state_py_shim_emits_through_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ProjectAnchor.save() emits a state.change event through lib.log.

    This is the contract guard with plan 001: if the signature drifts,
    this test will fail before anything else catches it.
    """
    from lib.state import ProjectAnchor

    awf_dir = tmp_path / ".awf"
    awf_dir.mkdir()
    anchor_data = {
        "awf_version": "0.1.0",
        "domain": "example.com",
        "slug": "example",
        "stage": "landing",
        "created": "2026-05-31T20:00:00Z",
        "has": {"passport": False, "infra": False, "kamal": False, "content": False},
    }
    anchor_path = awf_dir / "project.json"
    anchor_path.write_text(json.dumps(anchor_data), encoding="utf-8")
    (tmp_path / "passport.json").write_text('{"domain":"example.com"}', encoding="utf-8")

    # Set project root so log events land in our tmp project
    tok = log._current_project_root.set(tmp_path)
    try:
        anchor = ProjectAnchor.load(start=tmp_path)
        anchor.save()
    finally:
        log._current_project_root.reset(tok)

    events = _read_log_lines(tmp_path)
    sc_events = [e for e in events if e["type"] == "state.change"]
    assert len(sc_events) == 1
    data = sc_events[0]["data"]
    assert data["file"] == str(anchor_path)
    assert data["key"] == ""
    assert "before" in data or "before_hash" in data
    assert "after" in data or "after_hash" in data


# ---------------------------------------------------------------------------
# intent gating
# ---------------------------------------------------------------------------


def test_intent_silent_by_default(tmp_path: Path) -> None:
    """intent() emits nothing when AWF_LOG_INTENTS is unset and dry-run is off."""
    project_root = _make_project(tmp_path)
    tok = log._current_project_root.set(project_root)
    try:
        log.intent("provision server", "€5/mo")
    finally:
        log._current_project_root.reset(tok)

    events = _read_log_lines(project_root)
    assert not any(e["type"] == "intent" for e in events)


def test_intent_emits_under_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """intent() emits an event when AWF_LOG_INTENTS=1 is set."""
    monkeypatch.setenv("AWF_LOG_INTENTS", "1")
    project_root = _make_project(tmp_path)
    tok = log._current_project_root.set(project_root)
    try:
        log.intent("provision server", "€5/mo")
    finally:
        log._current_project_root.reset(tok)

    events = _read_log_lines(project_root)
    intent_events = [e for e in events if e["type"] == "intent"]
    assert len(intent_events) == 1


def test_intent_emits_under_set_dry_run(tmp_path: Path) -> None:
    """intent() emits an event when set_dry_run(True) is active.

    Teardown is handled by the module-level _reset_dry_run autouse fixture.
    """
    log.set_dry_run(True)
    project_root = _make_project(tmp_path)
    tok = log._current_project_root.set(project_root)
    try:
        log.intent("provision server", "€5/mo")
    finally:
        log._current_project_root.reset(tok)

    events = _read_log_lines(project_root)
    intent_events = [e for e in events if e["type"] == "intent"]
    assert len(intent_events) == 1


# ---------------------------------------------------------------------------
# error and gate interaction with session summary
# ---------------------------------------------------------------------------


def test_error_marks_session_result_fail(tmp_path: Path) -> None:
    """error() within a session marks the session.end data.summary as 'fail'."""
    project_root = _make_project(tmp_path)
    with log.session("test-composer", "landing", start=project_root):
        log.error("something went wrong")

    events = _read_log_lines(project_root)
    end_event = next(e for e in events if e["type"] == "session.end")
    assert end_event["data"]["summary"] == "fail"


def test_gate_increments_gates_hit_counter(tmp_path: Path) -> None:
    """Two gate() calls within a session result in gates_hit == 2 on session.end."""
    project_root = _make_project(tmp_path)
    with log.session("test-composer", "landing", start=project_root):
        log.gate("gate-one", "first gate")
        log.gate("gate-two", "second gate")

    events = _read_log_lines(project_root)
    end_event = next(e for e in events if e["type"] == "session.end")
    assert end_event["data"]["gates_hit"] == 2


# ---------------------------------------------------------------------------
# Outside-project behaviour
# ---------------------------------------------------------------------------


def test_session_outside_project_falls_back_to_orphan_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """session() from a directory with no project lands in orphan-log.jsonl."""
    user_dir = tmp_path / "user-awf"
    monkeypatch.setattr(awf_home_module, "user_config_dir", lambda: user_dir)

    # tmp_path has no .awf/project.json or passport.json
    with log.session("test-composer", "landing", start=tmp_path):
        log.note("orphan note")

    orphan_path = user_dir / "orphan-log.jsonl"
    assert orphan_path.exists(), "Orphan log must exist when no project root found"
    lines = _read_raw_lines(orphan_path)
    assert len(lines) > 0
    for line in lines:
        json.loads(line)  # must be valid JSON


# ---------------------------------------------------------------------------
# log.process event (plan_007 addition)
# ---------------------------------------------------------------------------


def test_log_process_emits_correct_event_shape(tmp_path: Path) -> None:
    """log.process() emits a process.invoke event with cmd, exit_code, duration_ms, cwd."""
    project_root = _make_project(tmp_path)
    log._current_project_root.set(project_root)
    try:
        log.process(
            cmd=["kamal", "deploy"],
            exit_code=0,
            duration_ms=123,
            cwd="/tmp/my-project",
        )
    finally:
        log._current_project_root.set(None)

    events = _read_log_lines(project_root)
    process_events = [e for e in events if e.get("type") == "process.invoke"]
    assert len(process_events) == 1

    evt = process_events[0]
    assert evt["result"] == "ok"
    data = evt["data"]
    assert data["cmd"] == ["kamal", "deploy"]
    assert data["exit_code"] == 0
    assert data["duration_ms"] == 123
    assert data["cwd"] == "/tmp/my-project"


def test_log_process_non_zero_exit_result_is_fail(tmp_path: Path) -> None:
    """log.process() with exit_code != 0 sets result='fail'."""
    project_root = _make_project(tmp_path)
    log._current_project_root.set(project_root)
    try:
        log.process(
            cmd=["kamal", "setup"],
            exit_code=1,
            duration_ms=50,
            cwd="/tmp/my-project",
        )
    finally:
        log._current_project_root.set(None)

    events = _read_log_lines(project_root)
    process_events = [e for e in events if e.get("type") == "process.invoke"]
    assert len(process_events) == 1
    assert process_events[0]["result"] == "fail"
