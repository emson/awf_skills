"""Passport schema (v1.0). The contract between skills, template, and human.

Standard library only: we use `dataclasses` rather than Pydantic so the
shared lib can be imported by any uv-script without each script having to
declare `pydantic` in its inline metadata. Validation is hand-rolled and
intentionally narrow — see docs/03-passport-contract.md.

If validation grows, lift to Pydantic and add it to every script's deps.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
import json
import re

from slug import domain_to_project_name  # noqa: I100 — sibling import via lib path


SCHEMA_VERSION_CURRENT = "1.0"
SCHEMA_VERSION_MIN = "1.0"
PASSPORT_FILENAME = "passport.json"

_DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")


@dataclass
class Faq:
    question: str
    answer: str


@dataclass
class Gate:
    completed_at: str | None = None
    # Free-form per-gate metadata (e.g. screenshot path).
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Launch:
    gates: dict[str, Gate] = field(default_factory=dict)


@dataclass
class Passport:
    schema_version: str = SCHEMA_VERSION_CURRENT
    template_version: str = "1.0"

    project_name: str = ""
    domain: str = ""
    site_url: str = ""

    site_name: str = ""
    site_hero: str = "Welcome"
    site_subtitle: str = ""
    site_description: str = ""
    category: str = ""
    tags: str = ""
    cta_button: str = "Open Link"
    email: str = ""

    nameserver_service: str = "namecheap"
    hosting_service: str = "cloudflare"

    fathom_site_id: str = ""
    indexnow_key: str = ""

    features: list[str] = field(default_factory=lambda: [""])
    faqs: list[Faq] = field(default_factory=list)

    launch: Launch = field(default_factory=Launch)

    # Cloudflare resource IDs keyed by "<TYPE>:<name>" (e.g. "A:api").
    # Written by awf-cf-dns-record; read by any skill that needs record IDs
    # for resumability without a remote lookup.  Default empty dict means
    # old passports load cleanly (no migration needed).
    cloudflare: dict = field(default_factory=dict)

    # ── construction ───────────────────────────────────────────────────

    @classmethod
    def new(cls, *, domain: str, template_version: str = "1.0") -> "Passport":
        slug = domain_to_project_name(domain)
        return cls(
            schema_version=SCHEMA_VERSION_CURRENT,
            template_version=template_version,
            project_name=slug,
            domain=domain.lower().strip(),
            site_url=f"https://{domain.lower().strip()}",
        )

    # ── load / save ────────────────────────────────────────────────────

    @classmethod
    def load(cls, project_root: Path) -> "Passport":
        path = project_root / PASSPORT_FILENAME
        data = json.loads(path.read_text(encoding="utf-8"))
        data = migrate(data)
        return _from_dict(data)

    def save(self, project_root: Path) -> None:
        path = project_root / PASSPORT_FILENAME
        path.write_text(
            json.dumps(_to_dict(self), indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # ── validation ─────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Return a list of human-readable problems. Empty list = OK."""
        problems: list[str] = []

        if not _DOMAIN_RE.match(self.domain or ""):
            problems.append(f"domain {self.domain!r} is not a valid domain")
        else:
            expected_slug = domain_to_project_name(self.domain)
            if self.project_name != expected_slug:
                problems.append(
                    f"project_name {self.project_name!r} does not match "
                    f"derived slug {expected_slug!r}"
                )
            expected_url = f"https://{self.domain}"
            if self.site_url != expected_url:
                problems.append(
                    f"site_url {self.site_url!r} should be {expected_url!r}"
                )

        if self.nameserver_service != "namecheap":
            problems.append(
                f"nameserver_service {self.nameserver_service!r} not supported (A16)"
            )
        if self.hosting_service != "cloudflare":
            problems.append(
                f"hosting_service {self.hosting_service!r} not supported (A16)"
            )

        # Soft warnings
        if not self.site_name:
            problems.append("warn: site_name is empty (run awf-generate-content)")
        if len(self.faqs) < 3:
            problems.append(f"warn: only {len(self.faqs)} FAQs (template expects ≥ 3)")

        return problems

    # ── gates ──────────────────────────────────────────────────────────

    def mark_gate(self, name: str, *, meta: dict[str, Any] | None = None) -> None:
        from datetime import datetime, timezone
        self.launch.gates[name] = Gate(
            completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            meta=meta or {},
        )

    def gate_completed(self, name: str) -> bool:
        g = self.launch.gates.get(name)
        return bool(g and g.completed_at)


# ── (de)serialisation ──────────────────────────────────────────────────


def _to_dict(p: Passport) -> dict[str, Any]:
    d = asdict(p)
    d["faqs"] = [{"question": f.question, "answer": f.answer} for f in p.faqs]
    d["launch"] = {
        "gates": {
            name: {
                "completed_at": g.completed_at,
                **({"meta": g.meta} if g.meta else {}),
            }
            for name, g in p.launch.gates.items()
        }
    }
    return d


def _from_dict(data: dict[str, Any]) -> Passport:
    raw_faqs = data.get("faqs") or []
    faqs = [Faq(question=q["question"], answer=q["answer"]) for q in raw_faqs]

    launch_data = data.get("launch") or {}
    gate_data = (launch_data.get("gates") or {}) if isinstance(launch_data, dict) else {}
    gates = {
        name: Gate(
            completed_at=(g or {}).get("completed_at"),
            meta=(g or {}).get("meta") or {},
        )
        for name, g in gate_data.items()
    }

    known = {f.name for f in Passport.__dataclass_fields__.values()}
    kwargs = {k: v for k, v in data.items() if k in known and k not in {"faqs", "launch"}}

    return Passport(faqs=faqs, launch=Launch(gates=gates), **kwargs)


# ── migrations ─────────────────────────────────────────────────────────


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Lift older passports up to SCHEMA_VERSION_CURRENT.

    Per A11: explicit step-by-step migrations, never magic. Each step
    bumps `schema_version` exactly once.
    """
    version = data.get("schema_version", "1.0")

    # Currently no migrations — v1.0 is the floor. When a v2 lands, add:
    #   if version == "1.0":
    #       data = _migrate_v1_to_v2(data)
    #       version = "2.0"

    if version > SCHEMA_VERSION_CURRENT:
        raise ValueError(
            f"passport schema_version {version!r} is newer than this "
            f"awf-skills understands ({SCHEMA_VERSION_CURRENT!r}). "
            "Update awf-skills."
        )
    return data
