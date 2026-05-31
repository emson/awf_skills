"""Template resolution and overlay (A10).

Templates live at `$AWF_HOME/templates/<name>-v<version>/`, each with:

  - `template.json`     — `{name, version, passport_schema, description}`
  - `preserve-list.txt` — globs (one per line) of files to keep when
                          overlaying a newer template onto an existing
                          project.
  - the actual SvelteKit project tree.

Versioning is semver-ish on the directory suffix (`v1`, `v1.1`, `v2`).
We resolve "the latest" by sorting on the version triple.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path


TEMPLATES_DIRNAME = "templates"
TEMPLATE_JSON = "template.json"
PRESERVE_LIST = "preserve-list.txt"


class TemplateNotFound(RuntimeError):
    pass


class TemplateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Template:
    path: Path                      # absolute path to the template dir
    name: str                       # e.g. "landing-page"
    version: str                    # semver-ish, e.g. "1.0", "2.1"
    passport_schema: str            # required passport schema_version
    description: str = ""

    @property
    def preserve_globs(self) -> list[str]:
        f = self.path / PRESERVE_LIST
        if not f.is_file():
            return []
        return [
            line.strip()
            for line in f.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]


# ── Discovery ──────────────────────────────────────────────────────────


_VERSION_RE = re.compile(r"^(?P<base>.+?)-v(?P<ver>[0-9]+(?:\.[0-9]+)*)$")


def _version_tuple(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in s.split("."))


def list_templates(awf_home: Path) -> list[Template]:
    """Discover all templates under `$AWF_HOME/templates/` that have a
    valid `template.json`. Returns a list sorted by (name, version)
    ascending."""
    root = awf_home / TEMPLATES_DIRNAME
    if not root.is_dir():
        return []
    found: list[Template] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        m = _VERSION_RE.match(d.name)
        if not m:
            continue
        meta_path = d / TEMPLATE_JSON
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        found.append(Template(
            path=d.resolve(),
            name=meta.get("name") or m.group("base"),
            version=meta.get("version") or m.group("ver"),
            passport_schema=meta.get("passport_schema", "1.0"),
            description=meta.get("description", ""),
        ))
    found.sort(key=lambda t: (t.name, _version_tuple(t.version)))
    return found


def latest_template(awf_home: Path, name: str = "landing-page") -> Template:
    """Return the highest-versioned template matching `name`."""
    candidates = [t for t in list_templates(awf_home) if t.name == name]
    if not candidates:
        raise TemplateNotFound(
            f"No templates found at {awf_home / TEMPLATES_DIRNAME}/ matching name={name!r}. "
            f"Add one — see {awf_home / TEMPLATES_DIRNAME / 'README.md'}."
        )
    return candidates[-1]


def resolve_template(awf_home: Path, *, name_or_dir: str | None = None) -> Template:
    """Look up a template by either its exact directory name (e.g.
    `landing-page-v1`) or its `name` (latest version wins)."""
    if name_or_dir is None:
        return latest_template(awf_home)
    explicit = awf_home / TEMPLATES_DIRNAME / name_or_dir
    if explicit.is_dir():
        for t in list_templates(awf_home):
            if t.path == explicit.resolve():
                return t
        raise TemplateError(f"{explicit} exists but lacks a valid {TEMPLATE_JSON}")
    # Else treat as a name and find the latest version.
    return latest_template(awf_home, name=name_or_dir)


# ── Overlay ────────────────────────────────────────────────────────────


@dataclass
class FileChange:
    """One file's status relative to applying the template."""
    relpath: str
    kind: str  # "create" | "modify" | "unchanged" | "preserved"


def _relpaths(root: Path) -> list[str]:
    """All file paths under `root` as POSIX relative strings."""
    out: list[str] = []
    for p in root.rglob("*"):
        if p.is_file():
            out.append(p.relative_to(root).as_posix())
    return out


def _is_preserved(relpath: str, globs: list[str]) -> bool:
    """Match against the preserve-list using fnmatch (POSIX glob)."""
    posix = relpath.replace("\\", "/")
    for g in globs:
        # Support directory-style globs like "src/lib/posts/**".
        # fnmatch doesn't know `**` natively but `pathlib` PurePath.match
        # is too restrictive; convert `**` to a wildcard.
        pat = g.replace("**", "*")
        if fnmatch.fnmatch(posix, pat):
            return True
        # Match any file under a directory entry like "src/lib/posts".
        if posix == g or posix.startswith(g.rstrip("/") + "/"):
            return True
    return False


def plan_overlay(
    template: Template,
    project_root: Path,
) -> list[FileChange]:
    """Compute what would happen if `template` were applied to
    `project_root`. Pure — no side effects.

    Files in the template are classified:
      - `preserved` — matches preserve-list AND already exists in project
      - `create`    — not in project yet
      - `modify`    — present and differs
      - `unchanged` — present and identical
    """
    template_files = _relpaths(template.path)
    # Skip the template's own metadata files.
    template_files = [
        f for f in template_files
        if f not in (TEMPLATE_JSON, PRESERVE_LIST)
    ]
    globs = template.preserve_globs
    changes: list[FileChange] = []

    for rel in template_files:
        src = template.path / rel
        dst = project_root / rel
        if _is_preserved(rel, globs) and dst.exists():
            changes.append(FileChange(rel, "preserved"))
            continue
        if not dst.exists():
            changes.append(FileChange(rel, "create"))
            continue
        # Compare bytes.
        if dst.is_file() and src.read_bytes() == dst.read_bytes():
            changes.append(FileChange(rel, "unchanged"))
        else:
            changes.append(FileChange(rel, "modify"))
    return changes


def apply_overlay(
    template: Template,
    project_root: Path,
    *,
    dry_run: bool = False,
) -> list[FileChange]:
    """Plan + (optionally) apply. Returns the same list as `plan_overlay`.

    Mutates the project tree only when `dry_run=False`. Skipped files
    (`preserved`, `unchanged`) are no-ops; `create` and `modify` copy
    the file from template → project, creating parent dirs as needed.
    """
    changes = plan_overlay(template, project_root)
    if dry_run:
        return changes
    for ch in changes:
        if ch.kind not in ("create", "modify"):
            continue
        src = template.path / ch.relpath
        dst = project_root / ch.relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return changes
