"""Tests for the applied-state inventory projection (lib/inventory.py, D-012)."""

from __future__ import annotations

import json
from pathlib import Path

from lib import inventory


def _mk_project(root: Path) -> None:
    """A project with a passport (CF zone gate, fathom, DNS records) + infra."""
    (root / ".awf").mkdir(parents=True)
    passport = {
        "project_name": "example-com",
        "domain": "example.com",
        "fathom_site_id": "FAID9",
        "cloudflare": {"CNAME:example.com": "rec_aaa", "A:www": "rec_bbb"},
        "launch": {"gates": {"domain_setup": {"meta": {"zone_id": "ZONE123"}}}},
    }
    (root / "passport.json").write_text(json.dumps(passport), encoding="utf-8")
    infra = {
        "neon": {"project_id": "np_1", "branch_id": "br_1"},
        "hetzner": {"servers": [{"id": "srv_9", "role": "web"}], "lb_id": "lb_2"},
        "kamal": {"last_deploy_image": "ghcr.io/x:1"},
    }
    (root / ".awf" / "infra.json").write_text(json.dumps(infra), encoding="utf-8")


def _mk_log(root: Path) -> None:
    events = [
        # precise: api.call names the neon project id directly
        {"type": "api.call", "ts": "2026-06-01T10:00:00Z", "skill": "awf-neon-project",
         "session": "S1", "data": {"provider": "neon", "resource_id": "np_1"}},
        # after-state match: setup-domain's save wrote the zone id into the passport
        {"type": "state.change", "ts": "2026-06-02T10:00:00Z", "skill": "awf-setup-domain",
         "session": "S2", "data": {"file": "passport.json", "after": {"launch": {"gates": {"domain_setup": {"meta": {"zone_id": "ZONE123"}}}}}}},
        # after-state match: setup-analytics' later save wrote the fathom id
        {"type": "state.change", "ts": "2026-06-03T10:00:00Z", "skill": "awf-setup-analytics",
         "session": "S3", "data": {"file": "passport.json", "after": {"fathom_site_id": "FAID9"}}},
    ]
    (root / ".awf" / "log.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )


def test_extract_resources_covers_all_providers(tmp_path):
    root = tmp_path / "proj"
    _mk_project(root)
    records = inventory.extract_resources(root)
    got = {(r.provider, r.resource_type, r.resource_id) for r in records}
    assert ("cloudflare", "zone", "ZONE123") in got
    assert ("cloudflare", "dns_record", "rec_aaa") in got
    assert ("cloudflare", "dns_record", "rec_bbb") in got
    assert ("fathom", "site", "FAID9") in got
    assert ("neon", "project", "np_1") in got
    assert ("neon", "branch", "br_1") in got
    assert ("hetzner", "server", "srv_9") in got
    assert ("hetzner", "load_balancer", "lb_2") in got
    assert ("kamal", "image", "ghcr.io/x:1") in got
    # all carry the project identity
    assert all(r.project == "example-com" for r in records)


def test_provenance_precise_and_per_resource(tmp_path):
    root = tmp_path / "proj"
    _mk_project(root)
    _mk_log(root)
    by_id = {r.resource_id: r for r in inventory.extract_resources(root)}
    # precise: neon project id named directly in an api.call
    assert by_id["np_1"].applied_by_skill == "awf-neon-project"
    assert by_id["np_1"].session == "S1"
    # per-resource via after-state match: zone attributed to setup-domain...
    assert by_id["ZONE123"].applied_by_skill == "awf-setup-domain"
    assert by_id["ZONE123"].last_applied == "2026-06-02T10:00:00Z"
    # ...and fathom to setup-analytics, NOT to the globally-latest state.change
    assert by_id["FAID9"].applied_by_skill == "awf-setup-analytics"
    assert by_id["FAID9"].session == "S3"


def test_missing_state_files_yield_nothing(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    assert inventory.extract_resources(root) == []


def test_discover_and_rebuild_via_sessions_index(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    proj = tmp_path / "proj"
    _mk_project(proj)
    # sessions index points at the project; a second line points at a vanished path
    sessions = [
        {"session_id": "S1", "project_slug": "example-com", "project_path": str(proj)},
        {"session_id": "S2", "project_slug": "gone", "project_path": str(tmp_path / "vanished")},
    ]
    (config_dir / inventory.SESSIONS_INDEX_FILENAME).write_text(
        "\n".join(json.dumps(s) for s in sessions) + "\n", encoding="utf-8"
    )

    roots = inventory.discover_project_roots(config_dir)
    assert roots == [proj]  # vanished path filtered out

    records = inventory.rebuild_inventory(config_dir)
    assert (config_dir / inventory.INVENTORY_FILENAME).is_file()
    assert any(r.resource_id == "ZONE123" for r in records)

    # round-trips through the cache
    loaded = inventory.load_inventory(config_dir)
    assert {r.resource_id for r in loaded} == {r.resource_id for r in records}


def test_rebuild_dedups_sessions_and_extra_roots(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    proj = tmp_path / "proj"
    _mk_project(proj)
    (config_dir / inventory.SESSIONS_INDEX_FILENAME).write_text(
        json.dumps({"project_path": str(proj)}) + "\n", encoding="utf-8"
    )
    # proj appears in BOTH the index and extra_roots — must not double-count
    records = inventory.rebuild_inventory(config_dir, extra_roots=[proj])
    zone_records = [r for r in records if r.resource_id == "ZONE123"]
    assert len(zone_records) == 1


def test_where_full_and_prefix(tmp_path):
    root = tmp_path / "proj"
    _mk_project(root)
    records = inventory.extract_resources(root)
    assert [r.resource_id for r in inventory.where(records, "ZONE123")] == ["ZONE123"]
    assert {r.resource_id for r in inventory.where(records, "rec_")} == {"rec_aaa", "rec_bbb"}
    assert inventory.where(records, "nonexistent") == []
