"""Applied-revisions — a per-project change history projected from the log.

D-012 (part D). Where lib/inventory.py answers "what is applied now," this
answers "how did it get there" as a numbered history, the way `helm history`
lists a release's revisions. Each ``state.change`` event in a project's
``.awf/log.jsonl`` (every passport / infra save, since D-011) becomes one
applied-revision: a numbered snapshot with the skill, session, timestamp, and a
summary of which top-level keys were added / removed / changed.

This is the groundwork for rollback — it deliberately does NOT perform rollback.
Un-applying a resource is provider-specific (deleting a Cloudflare zone is not
deleting a Neon branch) and is out of scope until a concrete need appears
(D-012). What this gives today: "project X is at revision 7; here is what each
revision changed, and which skill/session produced it."

Standard library only (like passport.py / inventory.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

LOG_RELPATH = (".awf", "log.jsonl")


@dataclass
class Revision:
    """One numbered applied-state change for a project."""

    n: int
    ts: str
    skill: str
    session: str
    file: str                       # basename of the changed state file
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"+{len(self.added)}")
        if self.removed:
            parts.append(f"-{len(self.removed)}")
        if self.changed:
            parts.append(f"~{len(self.changed)}")
        return " ".join(parts) or "(no field change)"

    def touched(self) -> list[str]:
        """All top-level keys this revision touched, for a one-line preview."""
        return sorted({*self.added, *self.removed, *self.changed})


def project_revisions(project_root: Path) -> list[Revision]:
    """Number every ``state.change`` in the project log, oldest = revision 1.

    Ordering follows the append-only log (chronological by construction). A
    save whose diff is empty still counts as a revision — the save happened.
    """
    log_path = Path(project_root).joinpath(*LOG_RELPATH)
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    out: list[Revision] = []
    n = 0
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "state.change":
            continue
        n += 1
        data = ev.get("data") or {}
        diff = data.get("diff") or {}
        file_field = data.get("file") or ""
        out.append(
            Revision(
                n=n,
                ts=ev.get("ts", ""),
                skill=ev.get("skill") or "",
                session=ev.get("session") or "",
                file=Path(file_field).name if file_field else "",
                added=list(diff.get("added") or []),
                removed=list(diff.get("removed") or []),
                changed=list(diff.get("changed") or []),
            )
        )
    return out


def get_revision(project_root: Path, n: int) -> Revision | None:
    for rev in project_revisions(project_root):
        if rev.n == n:
            return rev
    return None


def as_dict(rev: Revision) -> dict[str, Any]:
    return asdict(rev)


# ── Project resolution (slug → root) ────────────────────────────────────────


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_project_root(
    config_dir: Path,
    name_or_path: str | None,
    cwd: Path | None = None,
) -> Path | None:
    """Resolve a project reference to its root directory.

    Accepts an explicit path, a project slug (matched against the sessions
    index / passport project_name or the directory name), or ``None`` to mean
    "the current project" (walk up from ``cwd``).
    """
    if name_or_path:
        p = Path(name_or_path).expanduser()
        if p.is_dir() and ((p / "passport.json").is_file() or (p / ".awf").is_dir()):
            return p
        # treat as a slug — search the known projects from the sessions index
        from lib import inventory  # local import: avoid import cycle at module load

        for root in inventory.discover_project_roots(config_dir):
            passport = _read_json(root / "passport.json")
            if passport.get("project_name") == name_or_path or root.name == name_or_path:
                return root
        return None

    # No name: the current project.
    start = Path(cwd) if cwd is not None else Path.cwd()
    cur = start.resolve()
    for cand in [cur, *cur.parents]:
        if (cand / ".awf" / "project.json").is_file() or (cand / "passport.json").is_file():
            return cand
    return None
