"""Central applied-state inventory — a projection over project state + logs.

D-012. Answers "what has been applied, how, and where" across every project,
without standing infrastructure. The journal (``.awf/log.jsonl``) records *what
happened*; the state files (``passport.json`` / ``.awf/infra.json``) record
*what is true now*. This module joins them into one resource-grained,
cross-project view written to ``$AWF_CONFIG_DIR/inventory.jsonl``.

Design (the Terraform lesson): the per-project state files are the **source of
truth**; this inventory is a **rebuildable cache**. ``rebuild_inventory`` re-scans
the known projects from scratch, so a stale/corrupt index or a moved/deleted
project is never fatal — you just rebuild.

Why state files, not only the log: ``api.call`` events are emitted unevenly
(neon/hetzner/namecheap do, cloudflare/fathom do not), so the log alone would
miss the most common S1 resources. The state files hold every resource ID
regardless of provider instrumentation; the log supplies provenance (which skill
applied it, when, in which session) on a best-effort basis.

Standard library only (like passport.py), so any uv-script can import it.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

INVENTORY_FILENAME = "inventory.jsonl"
SESSIONS_INDEX_FILENAME = "sessions.jsonl"


@dataclass
class InventoryRecord:
    """One applied resource, keyed by (project_path, provider, resource_type, resource_id)."""

    project: str
    project_path: str
    provider: str
    resource_type: str
    resource_id: str
    name: str = ""               # e.g. the DNS record "TYPE:host", else ""
    applied_by_skill: str = ""   # provenance (best-effort, from the log)
    last_applied: str = ""       # ISO ts of the most recent touch (best-effort)
    session: str = ""            # session ULID that last touched it

    def key(self) -> tuple[str, str, str, str]:
        return (self.project_path, self.provider, self.resource_type, self.resource_id)


# ── Declarative extraction maps ─────────────────────────────────────────────
# Each entry: (dotted path into the state dict, provider, resource_type).
# Adding a new tracked resource is a one-line change here.

_INFRA_SCALARS: list[tuple[str, str, str]] = [
    ("neon.project_id", "neon", "project"),
    ("neon.branch_id", "neon", "branch"),
    ("hetzner.lb_id", "hetzner", "load_balancer"),
    ("hetzner.network_id", "hetzner", "network"),
    ("kamal.last_deploy_image", "kamal", "image"),
]

_PASSPORT_SCALARS: list[tuple[str, str, str]] = [
    ("fathom_site_id", "fathom", "site"),
    # Cloudflare zone id is stashed on the domain_setup gate by awf-setup-domain.
    ("launch.gates.domain_setup.meta.zone_id", "cloudflare", "zone"),
]


def _get_dotted(d: Any, path: str) -> Any:
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ── Per-project extraction ──────────────────────────────────────────────────


def _records_from_state(
    project: str, project_root: Path
) -> list[InventoryRecord]:
    """Pull current resource IDs from passport.json + .awf/infra.json."""
    out: list[InventoryRecord] = []
    ppath = str(project_root)

    passport = _read_json(project_root / "passport.json")
    for dotted, provider, rtype in _PASSPORT_SCALARS:
        rid = _get_dotted(passport, dotted)
        if rid:
            out.append(InventoryRecord(project, ppath, provider, rtype, str(rid)))

    # Cloudflare DNS records live in passport["cloudflare"] as {"TYPE:host": id}.
    cf = passport.get("cloudflare")
    if isinstance(cf, dict):
        for name, rid in cf.items():
            if rid:
                out.append(
                    InventoryRecord(project, ppath, "cloudflare", "dns_record", str(rid), name=str(name))
                )

    infra = _read_json(project_root / ".awf" / "infra.json")
    for dotted, provider, rtype in _INFRA_SCALARS:
        rid = _get_dotted(infra, dotted)
        if rid:
            out.append(InventoryRecord(project, ppath, provider, rtype, str(rid)))

    # Hetzner servers are a list of {id, ip, role}.
    servers = _get_dotted(infra, "hetzner.servers")
    if isinstance(servers, list):
        for s in servers:
            if isinstance(s, dict) and s.get("id"):
                out.append(
                    InventoryRecord(
                        project, ppath, "hetzner", "server", str(s["id"]),
                        name=str(s.get("role", "")),
                    )
                )

    return out


class _Provenance:
    """Best-effort "who applied this resource, when" derived from one project log.

    Resolution order for a resource id:
      1. the latest ``api.call`` event naming that exact ``resource_id`` (the
         precise signal, for providers that instrument api.call), else
      2. the latest ``state.change`` whose written *after*-state contains the id
         (correct per-resource attribution for providers that don't — e.g.
         cloudflare's zone landed in the passport that awf-setup-domain saved), else
      3. the latest ``state.change`` in the project at all (a weak fallback,
         e.g. when the save was large enough to be hash-pointered, not inlined).
    """

    def __init__(self) -> None:
        self._by_rid: dict[str, dict[str, str]] = {}
        self._state_changes: list[tuple[str, dict[str, str], str]] = []  # (ts, prov, after_text)
        self._latest: dict[str, str] = {"skill": "", "ts": "", "session": ""}

    def resolve(self, rid: str) -> dict[str, str]:
        if rid in self._by_rid:
            return self._by_rid[rid]
        best: dict[str, str] | None = None
        for ts, prov, after_text in self._state_changes:
            if rid in after_text and (best is None or ts >= best["ts"]):
                best = prov
        return best or self._latest


def _provenance_from_log(project_root: Path) -> _Provenance:
    """Scan the project log once and build a `_Provenance` resolver."""
    prov_index = _Provenance()
    log_path = project_root / ".awf" / "log.jsonl"
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return prov_index

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        etype = ev.get("type")
        ts = ev.get("ts", "")
        prov = {"skill": ev.get("skill") or "", "ts": ts, "session": ev.get("session") or ""}
        if etype == "api.call":
            rid = (ev.get("data") or {}).get("resource_id")
            if rid and ts >= prov_index._by_rid.get(str(rid), {}).get("ts", ""):
                prov_index._by_rid[str(rid)] = prov
        elif etype == "state.change":
            after = (ev.get("data") or {}).get("after")
            after_text = json.dumps(after, ensure_ascii=False) if after is not None else ""
            prov_index._state_changes.append((ts, prov, after_text))
            if ts >= prov_index._latest["ts"]:
                prov_index._latest = prov
    return prov_index


def extract_resources(project_root: Path, project: str | None = None) -> list[InventoryRecord]:
    """All applied resources for one project, with best-effort provenance."""
    project_root = Path(project_root)
    if project is None:
        passport = _read_json(project_root / "passport.json")
        project = passport.get("project_name") or project_root.name

    records = _records_from_state(project, project_root)
    prov_index = _provenance_from_log(project_root)
    for rec in records:
        prov = prov_index.resolve(rec.resource_id)
        rec.applied_by_skill = prov["skill"]
        rec.last_applied = prov["ts"]
        rec.session = prov["session"]
    return records


# ── Cross-project discovery + rebuild ───────────────────────────────────────


def discover_project_roots(config_dir: Path) -> list[Path]:
    """Existing project roots named in the cross-project sessions index.

    Paths in the index are hints (08-logging.md): we keep only those that still
    exist and look like a project (a passport.json or an .awf/ dir).
    """
    roots: list[Path] = []
    seen: set[str] = set()
    index_path = Path(config_dir) / SESSIONS_INDEX_FILENAME
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return roots
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        p = entry.get("project_path") or ""
        if not p or p in seen:
            continue
        seen.add(p)
        root = Path(p)
        if root.exists() and ((root / "passport.json").is_file() or (root / ".awf").is_dir()):
            roots.append(root)
    return roots


def rebuild_inventory(
    config_dir: Path,
    extra_roots: Iterable[Path] = (),
) -> list[InventoryRecord]:
    """Re-scan known projects from scratch and rewrite inventory.jsonl.

    Source of truth is always the per-project state; this only refreshes the
    cache. ``extra_roots`` lets a caller include the current project even if it
    has no session in the index yet.
    """
    config_dir = Path(config_dir)
    roots: list[Path] = []
    seen: set[str] = set()
    for root in [*discover_project_roots(config_dir), *map(Path, extra_roots)]:
        rp = str(root.resolve())
        if rp not in seen and root.exists():
            seen.add(rp)
            roots.append(root)

    records: list[InventoryRecord] = []
    for root in roots:
        records.extend(extract_resources(root))

    write_inventory(config_dir, records)
    return records


def write_inventory(config_dir: Path, records: list[InventoryRecord]) -> None:
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / INVENTORY_FILENAME
    try:
        with path.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(asdict(rec), separators=(",", ":"), ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"warn: inventory write failed: {e}", file=sys.stderr)


def load_inventory(config_dir: Path) -> list[InventoryRecord]:
    path = Path(config_dir) / INVENTORY_FILENAME
    out: list[InventoryRecord] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(InventoryRecord(**json.loads(raw)))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def where(records: list[InventoryRecord], resource_id: str) -> list[InventoryRecord]:
    """Reverse lookup: which project(s) a resource id belongs to.

    Matches a full or partial (prefix) resource id, since cloud IDs are long.
    """
    rid = resource_id.strip()
    return [r for r in records if r.resource_id == rid or r.resource_id.startswith(rid)]
