"""Pydantic v2 schemas for .awf/ state files (D-003).

Three state models:
  - ProjectAnchor  → .awf/project.json   (always present from S1)
  - Infra          → .awf/infra.json     (appears at S3 promotion)
  - Shared         → ~/.config/awf/shared.json  (user-scope, lazily created)

Design note: D-003 uses the phrase "Pydantic v2 dataclasses" colloquially.
This module uses Pydantic v2 BaseModel because we need model_validate,
model_dump, ConfigDict(extra="allow") for forward-compat, and PrivateAttr
for _path stashing. pydantic.dataclasses.dataclass does not give clean
extra="allow" ergonomics or PrivateAttr support. Lead has recorded this
divergence in plan_001 Context rather than a new ADR because the substance
of D-003 (Pydantic-v2-based models with the documented JSON shapes) is
unchanged.

sibling: lib/passport.py (stdlib-only dataclass style — see that file for
the S1 passport contract; the two coexist per D-001 §5 and D-004).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr, ValidationError

from lib.project import ProjectNotFound, find_project_root

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

AWF_DIRNAME = ".awf"
PROJECT_FILENAME = "project.json"
INFRA_FILENAME = "infra.json"
SHARED_FILENAME = "shared.json"
AWF_VERSION = "0.1.0"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# ---------------------------------------------------------------------------
# Custom exception hierarchy
# ---------------------------------------------------------------------------


class StateError(RuntimeError):
    """Base exception for state-file errors."""


class StateCorruptError(StateError):
    """Malformed JSON in a state file.

    Wraps the underlying json.JSONDecodeError and reports the byte offset
    and file path in the message.
    """


class StateValidationError(StateError):
    """Schema or cross-field invariant violation.

    Wraps pydantic.ValidationError and lists loc paths and human-readable
    error strings.
    """


# ---------------------------------------------------------------------------
# Atomic-write helper
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: dict) -> None:  # type: ignore[type-arg]
    """Write *data* to *path* atomically via temp-file-and-rename.

    Creates parent directories as needed. Uses os.replace for POSIX-atomic
    rename. Lets OSError propagate (fail fast at edges; callers decide).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Log hook (import-safe shim)
# ---------------------------------------------------------------------------


def _emit_state_change(file: Path, before: dict, after: dict) -> None:  # type: ignore[type-arg]
    """Emit a state.change log event.

    Calls log.state_change with the canonical signature from 08-logging.md:
      log.state_change(file=str(file), key="", before=before, after=after)

    key="" signals a whole-file save. Per-key emissions are reserved for
    future fine-grained call sites.

    The shim does NOT compute a diff or cap sizes — that is lib/log.py's
    responsibility (plan 003, D-002).

    Import-safe: if lib.log is absent (plan 003 not yet landed), no-ops
    silently. If log.state_change raises for any reason, swallows the
    exception and emits a stderr warning (D-002: logging never raises).
    """
    try:
        from lib import log  # type: ignore[attr-defined]

        log.state_change(file=str(file), key="", before=before, after=after)
    except ImportError:
        return
    except Exception as e:
        print(f"warn: log.state_change failed: {e}", file=sys.stderr)
        return


# ---------------------------------------------------------------------------
# Shared _save_impl — used by all three model classes
# ---------------------------------------------------------------------------


def _save_impl(model: BaseModel, path: Path) -> None:
    """Shared atomic-write + log-hook logic for all state models.

    Steps:
      1. Read existing file (if any) to compute before dict; else before = {}.
      2. after = model.model_dump(mode="json").
      3. model.validate() — raises StateValidationError on failure.
      4. _atomic_write_json(path, after).
      5. _emit_state_change(path, before, after).

    Callers (ProjectAnchor.save, Infra.save, Shared.save) assert _path is
    not None then delegate here.
    """
    # 1. Read before state
    before: dict = {}  # type: ignore[type-arg]
    if path.exists():
        try:
            before = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            before = {}

    # 2. Serialise current state
    after = model.model_dump(mode="json")

    # 3. Validate cross-field invariants before touching disk
    model.validate()  # type: ignore[call-arg]

    # 4. Atomic write
    _atomic_write_json(path, after)

    # 5. Emit log event (best-effort, never raises)
    _emit_state_change(path, before, after)


# ---------------------------------------------------------------------------
# Nested models — ProjectAnchor
# ---------------------------------------------------------------------------


class HasFlags(BaseModel):
    """Feature-presence flags for a project."""

    model_config = ConfigDict(extra="allow")

    passport: bool = False
    infra: bool = False
    kamal: bool = False
    content: bool = False


# ---------------------------------------------------------------------------
# ProjectAnchor — .awf/project.json
# ---------------------------------------------------------------------------


class ProjectAnchor(BaseModel):
    """Project identity and stage anchor (.awf/project.json).

    Always present from S1. Contains identity, slug, stage, and a has:
    pointer set. Skills walk up to find the project root and read this file.
    """

    model_config = ConfigDict(extra="allow")

    awf_version: str = AWF_VERSION
    domain: str
    slug: str
    stage: Literal["landing", "demo", "mvp-play", "prescale", "scale"]
    created: str  # ISO-8601 UTC
    has: HasFlags = HasFlags()

    _path: Path | None = PrivateAttr(default=None)

    @classmethod
    def load(cls, start: Path | None = None) -> "ProjectAnchor":
        """Load from .awf/project.json by walking up from start.

        Raises:
            ProjectNotFound: if no project root is found, or if
                .awf/project.json does not exist (augmented message
                includes start path and walked directories).
            StateCorruptError: if the file contains malformed JSON.
            StateValidationError: if the data fails schema or cross-field
                validation.
        """
        start_path = start or Path.cwd()
        try:
            root = find_project_root(start)
        except ProjectNotFound as e:
            raise ProjectNotFound(
                f"No project root found from {start_path}: {e}"
            ) from e

        assert root is not None  # find_project_root raises rather than returning None
        path = root / AWF_DIRNAME / PROJECT_FILENAME
        if not path.exists():
            raise ProjectNotFound(
                f"No .awf/project.json found in {root}; "
                f"cwd was {start_path}"
            )

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise StateCorruptError(
                f"Malformed JSON at byte {e.pos} in {path}: {e.msg}"
            ) from e

        try:
            instance = cls.model_validate(data)
        except ValidationError as e:
            locs = [".".join(str(x) for x in err["loc"]) for err in e.errors()]
            raise StateValidationError(
                f"Schema validation failed for {path}: {locs}"
            ) from e

        instance.validate()
        instance._path = path
        return instance

    def save(self) -> None:
        """Write to disk atomically. Raises if _path is not set."""
        assert self._path is not None, "ProjectAnchor._path not set; use load() first"
        _save_impl(self, self._path)

    def validate(self) -> None:  # type: ignore[override]
        """Enforce cross-field invariants.

        Collects all failures before raising (does not fail-fast internally).

        Raises:
            StateValidationError: listing all invariant violations.
        """
        problems: list[str] = []

        if self.stage == "landing" and self.has.infra:
            problems.append(
                "stage='landing' requires has.infra=False (infra appears at S3)"
            )
        if self.stage == "landing" and self.has.kamal:
            problems.append(
                "stage='landing' requires has.kamal=False (kamal appears at S3)"
            )
        if not _SLUG_RE.match(self.slug):
            problems.append(
                f"slug {self.slug!r} must match [a-z0-9][a-z0-9-]*"
            )
        if not self.domain:
            problems.append("domain must be non-empty")
        elif self.domain != self.domain.lower():
            problems.append(f"domain {self.domain!r} must be lowercase")
        elif "." not in self.domain:
            problems.append(f"domain {self.domain!r} must contain a '.'")

        if problems:
            raise StateValidationError(
                f"ProjectAnchor invariant violations: {problems}"
            )


# ---------------------------------------------------------------------------
# Nested models — Infra
# ---------------------------------------------------------------------------


class Server(BaseModel):
    """A Hetzner server entry in infra.json."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    ip: str = ""
    role: str = ""
    shared: bool = False
    cost_eur_month: float = 0.0


class Hetzner(BaseModel):
    """Hetzner infra block."""

    model_config = ConfigDict(extra="allow")

    servers: list[Server] = []
    lb_id: str | None = None
    network_id: str | None = None


class Registry(BaseModel):
    """Container registry config."""

    model_config = ConfigDict(extra="allow")

    host: str = "ghcr.io"
    image: str = ""
    user: str = ""


class Neon(BaseModel):
    """Neon Postgres config."""

    model_config = ConfigDict(extra="allow")

    project_id: str = ""
    branch_id: str = ""
    branch_name: str = ""
    mode: Literal["shared-branch", "dedicated"] = "shared-branch"
    connection_secret_ref: str = "DATABASE_URL"


class Kamal(BaseModel):
    """Kamal deploy config."""

    model_config = ConfigDict(extra="allow")

    config_path: str = "config/deploy.yml"
    last_deploy_image: str | None = None


# ---------------------------------------------------------------------------
# Infra — .awf/infra.json
# ---------------------------------------------------------------------------


class Infra(BaseModel):
    """Infrastructure ledger (.awf/infra.json).

    Appears at S3 promotion. Holds resource IDs for Hetzner, Neon, and
    Kamal. Use load_or_create() for create-on-miss semantics.
    """

    model_config = ConfigDict(extra="allow")

    registry: Registry = Registry()
    hetzner: Hetzner = Hetzner()
    neon: Neon = Neon()
    kamal: Kamal = Kamal()

    _path: Path | None = PrivateAttr(default=None)

    @classmethod
    def load(cls, start: Path | None = None) -> "Infra":
        """Load from .awf/infra.json by walking up from start.

        Raises:
            ProjectNotFound: if no project root is found (augmented message).
            FileNotFoundError: if the project exists but infra.json does not
                (use load_or_create() for create-on-miss behaviour).
            StateCorruptError: if the file contains malformed JSON.
            StateValidationError: if the data fails validation.
        """
        start_path = start or Path.cwd()
        try:
            root = find_project_root(start)
        except ProjectNotFound as e:
            raise ProjectNotFound(
                f"No project root found from {start_path}: {e}"
            ) from e

        assert root is not None  # find_project_root raises rather than returning None
        path = root / AWF_DIRNAME / INFRA_FILENAME

        # Let FileNotFoundError propagate — caller uses load_or_create if needed
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except json.JSONDecodeError as e:
            raise StateCorruptError(
                f"Malformed JSON at byte {e.pos} in {path}: {e.msg}"
            ) from e

        try:
            instance = cls.model_validate(data)
        except ValidationError as e:
            locs = [".".join(str(x) for x in err["loc"]) for err in e.errors()]
            raise StateValidationError(
                f"Schema validation failed for {path}: {locs}"
            ) from e

        instance.validate()
        instance._path = path
        return instance

    @classmethod
    def load_or_create(cls, start: Path | None = None) -> "Infra":
        """Load from .awf/infra.json, or return an empty default if absent.

        The file is NOT written to disk until .save() is explicitly called
        (per A1: don't materialise empty files until something writes).

        Raises:
            ProjectNotFound: if no project root is found.
        """
        start_path = start or Path.cwd()
        try:
            root = find_project_root(start)
        except ProjectNotFound as e:
            raise ProjectNotFound(
                f"No project root found from {start_path}: {e}"
            ) from e

        assert root is not None  # find_project_root raises rather than returning None
        # Derive path before try so both branches can assign _path
        path = root / AWF_DIRNAME / INFRA_FILENAME

        try:
            instance = cls.load(start=start)
        except FileNotFoundError:
            instance = cls()

        # Assign in both branches (load success sets _path; empty default needs it)
        instance._path = path
        return instance

    def save(self) -> None:
        """Write to disk atomically. Raises if _path is not set."""
        assert self._path is not None, "Infra._path not set; use load() or load_or_create() first"
        _save_impl(self, self._path)

    def validate(self) -> None:  # type: ignore[override]
        """Enforce cross-field invariants.

        Raises:
            StateValidationError: listing all invariant violations.
        """
        problems: list[str] = []

        if self.hetzner.lb_id and not self.hetzner.servers:
            problems.append(
                "hetzner.lb_id is set but hetzner.servers is empty; "
                "a load balancer requires at least one server"
            )
        # Only enforce neon.project_id when neon branch fields are populated
        if bool(self.neon.branch_id or self.neon.branch_name) and not self.neon.project_id:
            problems.append("neon.project_id must be non-empty when neon branch fields are set")
        if bool(self.registry.user) and bool(self.registry.image):
            expected_prefix = f"{self.registry.user}/"
            if not self.registry.image.startswith(expected_prefix):
                problems.append(
                    f"registry.image {self.registry.image!r} must start with "
                    f"{expected_prefix!r} when registry.user is set"
                )

        if problems:
            raise StateValidationError(
                f"Infra invariant violations: {problems}"
            )


# ---------------------------------------------------------------------------
# Nested models — Shared
# ---------------------------------------------------------------------------


class PlayServer(BaseModel):
    """The shared play Hetzner server used across S3 projects."""

    model_config = ConfigDict(extra="allow")

    hetzner_id: str = ""
    ip: str = ""
    hostname: str = ""
    registry: str = ""
    created: str = ""


class DefaultRegistry(BaseModel):
    """Default container registry settings."""

    model_config = ConfigDict(extra="allow")

    host: str = "ghcr.io"
    user: str = ""


# ---------------------------------------------------------------------------
# Shared — ~/.config/awf/shared.json
# ---------------------------------------------------------------------------


class Shared(BaseModel):
    """User-scope shared infrastructure state (~/.config/awf/shared.json).

    Lazily created on first S3 promotion that needs shared resources.
    No load() variant — it's always optional; use load_or_create().
    """

    model_config = ConfigDict(extra="allow")

    play_server: PlayServer | None = None
    play_neon_project_id: str | None = None
    default_registry: DefaultRegistry = DefaultRegistry()

    _path: Path | None = PrivateAttr(default=None)

    @classmethod
    def load_or_create(cls) -> "Shared":
        """Load from ~/.config/awf/shared.json, or return empty default.

        Path is resolved via lib.awf_home.user_config_dir() — never
        hardcoded in state.py (A6 pattern for testability).

        The file is NOT written to disk until .save() is explicitly called
        (per A1: don't materialise empty files until something writes).
        """
        from lib import awf_home

        path = awf_home.user_config_dir() / SHARED_FILENAME

        if not path.exists():
            instance = cls()
            instance._path = path
            return instance

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise StateCorruptError(
                f"Malformed JSON at byte {e.pos} in {path}: {e.msg}"
            ) from e

        try:
            instance = cls.model_validate(data)
        except ValidationError as e:
            locs = [".".join(str(x) for x in err["loc"]) for err in e.errors()]
            raise StateValidationError(
                f"Schema validation failed for {path}: {locs}"
            ) from e

        instance.validate()
        instance._path = path
        return instance

    def save(self) -> None:
        """Write to disk atomically. Raises if _path is not set."""
        assert self._path is not None, "Shared._path not set; use load_or_create() first"
        _save_impl(self, self._path)

    def validate(self) -> None:  # type: ignore[override]
        """Enforce cross-field invariants.

        Raises:
            StateValidationError: if play_server is set but has empty sub-fields.
        """
        problems: list[str] = []

        if self.play_server is not None:
            ps = self.play_server
            for field_name in ("hetzner_id", "ip", "hostname", "registry", "created"):
                value = getattr(ps, field_name, "")
                if not value:
                    problems.append(
                        f"play_server.{field_name} must be non-empty when play_server is set"
                    )

        if problems:
            raise StateValidationError(
                f"Shared invariant violations: {problems}"
            )
