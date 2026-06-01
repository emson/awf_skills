"""Stage-keyed catalog: single source of truth for stage→X mappings.

Consumed by: awf-help, awf-doctor, awf-status.

Constants
---------
STAGE_ORDER          Canonical stage sequence (tuple, insertion order matters).
NEXT_COMPOSERS       Stage → next composer skill name (or None for terminal).
NEXT_HINTS           Stage → one-line hint for the "to advance" block.
RELEVANT_SKILLS      Stage → list of atomic skills meaningful at that stage.
SUBSYSTEMS           Closed frozenset of valid subsystem ids (doctor uses these).
STAGE_REQUIREMENTS   Stage → ordered list of subsystem ids (order is semantic —
                     doctor and help use it as-is for human-readable output).
SKILL_REQUIREMENTS   Skill name → list of subsystem ids (empty → no preflight).

Helpers
-------
next_composer(stage)     → str | None
relevant_skills(stage)   → list[str]
stage_subsystems(stage)  → list[str]   (preserves STAGE_REQUIREMENTS order)
skill_subsystems(skill)  → list[str]

Errors
------
All helpers raise KeyError on unknown input so typos fail loudly.
Doctor catches and re-raises as exit-2.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Stage order
# ---------------------------------------------------------------------------

STAGE_ORDER: Final[tuple[str, ...]] = (
    "landing",
    "demo",
    "mvp-play",
    "prescale",
    "scale",
)

# ---------------------------------------------------------------------------
# Next composer map
# ---------------------------------------------------------------------------

NEXT_COMPOSERS: Final[dict[str, str | None]] = {
    "landing":  "awf-stage-demo",
    "demo":     "awf-stage-mvp-play",
    "mvp-play": "awf-stage-prescale",   # not yet built
    "prescale": "awf-stage-scale",       # not yet built
    "scale":    None,                    # terminal stage
}

# ---------------------------------------------------------------------------
# Hints for the "to advance" block (human readable)
# ---------------------------------------------------------------------------

NEXT_HINTS: Final[dict[str, str]] = {
    "landing":  "promote to demo stage when ready",
    "demo":     "promote to mvp-play stage when ready",
    "mvp-play": "promote to prescale stage when ready",
    "prescale": "promote to scale stage when ready",
    "scale":    "at terminal stage; operate via atomic skills",
}

# ---------------------------------------------------------------------------
# Relevant atomic skills per stage
# Composer skills (awf-stage-*) intentionally excluded — they're the
# next-composer, not "relevant at this stage".
# ---------------------------------------------------------------------------

RELEVANT_SKILLS: Final[dict[str, list[str]]] = {
    "landing": [
        "awf-init",
        "awf-create-project",
        "awf-setup-domain",
        "awf-setup-analytics",
        "awf-setup-nameservers",
        "awf-generate-content",
        "awf-review-passport",
        "awf-install",
        "awf-deploy",
        "awf-setup-gsc",
        "awf-verify-gsc",
        "awf-submit-bing",
    ],
    "demo": [
        "awf-init",
        "awf-create-project",
        "awf-setup-domain",
        "awf-setup-analytics",
        "awf-setup-nameservers",
        "awf-generate-content",
        "awf-review-passport",
        "awf-install",
        "awf-deploy",
        "awf-setup-gsc",
        "awf-verify-gsc",
        "awf-submit-bing",
        "awf-update-template",
    ],
    "mvp-play": [
        "awf-shared-infra-get",
        "awf-app-dockerize",
        "awf-neon-branch",
        "awf-app-secret-set",
        "awf-kamal-config",
        "awf-cf-dns-record",
        "awf-kamal-setup",
        "awf-kamal-deploy",
    ],
    "prescale": [],   # TBD; plan_014+ populates
    "scale":    [],   # TBD; plan_015+ populates
}

# ---------------------------------------------------------------------------
# Subsystem ids — closed set; tests enforce membership.
# ---------------------------------------------------------------------------

SUBSYSTEMS: Final[frozenset[str]] = frozenset({
    "cloudflare",
    "namecheap",
    "fathom",
    "gsc",
    "bing",
    "hetzner",
    "neon",
    "ghcr",
    "ssh",
    "kamal_cli",
    "google_oauth",
    "git",
    "node",
    "wrangler",
})

# ---------------------------------------------------------------------------
# Stage requirements
# NOTE: order is semantic — doctor and help use it as-is for human output.
# ---------------------------------------------------------------------------

STAGE_REQUIREMENTS: Final[dict[str, list[str]]] = {
    "landing":  ["cloudflare", "namecheap", "fathom", "gsc", "bing", "git", "node", "wrangler"],
    "demo":     ["cloudflare", "namecheap", "fathom", "gsc", "bing", "git", "node", "wrangler"],
    "mvp-play": ["hetzner", "neon", "ghcr", "ssh", "kamal_cli", "git"],
    "prescale": ["hetzner", "neon", "ghcr", "ssh", "kamal_cli", "git"],
    "scale":    ["hetzner", "neon", "ghcr", "ssh", "kamal_cli", "git"],
}

# ---------------------------------------------------------------------------
# Per-skill subsystem map.
# Skills not listed fall through to "no specific preflight required".
# ---------------------------------------------------------------------------

SKILL_REQUIREMENTS: Final[dict[str, list[str]]] = {
    "awf-setup-domain":      ["cloudflare"],
    "awf-setup-nameservers": ["namecheap"],
    "awf-setup-analytics":   ["fathom"],
    "awf-setup-gsc":         ["google_oauth", "cloudflare"],
    "awf-verify-gsc":        ["google_oauth"],
    "awf-submit-bing":       ["bing"],
    "awf-deploy":            ["wrangler", "node"],
    "awf-install":           ["node"],
    "awf-hetzner-provision": ["hetzner", "ssh"],
    "awf-neon-provision":    ["neon"],
    "awf-neon-branch":       ["neon"],
    "awf-kamal-config":      ["kamal_cli"],
    "awf-kamal-setup":       ["kamal_cli", "ssh", "ghcr"],
    "awf-kamal-deploy":      ["kamal_cli", "ssh", "ghcr"],
    "awf-cf-dns-record":     ["cloudflare"],
    "awf-app-secret-set":    [],   # local; no preflight
    "awf-app-dockerize":     [],   # local; no preflight
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def next_composer(stage: str) -> str | None:
    """Return the next-stage composer skill name, or None for terminal stage.

    Raises KeyError on unknown stage.
    """
    return NEXT_COMPOSERS[stage]


def relevant_skills(stage: str) -> list[str]:
    """Return the list of atomic skills relevant at the given stage.

    Raises KeyError on unknown stage.
    """
    return RELEVANT_SKILLS[stage]


def stage_subsystems(stage: str) -> list[str]:
    """Return the ordered list of subsystem ids for a stage.

    Order is semantic (matches STAGE_REQUIREMENTS insertion order).
    Raises KeyError on unknown stage.
    """
    return STAGE_REQUIREMENTS[stage]


def skill_subsystems(skill: str) -> list[str]:
    """Return the list of subsystem ids required by a skill.

    Returns an empty list for skills with no preflight requirement.
    Raises KeyError for skills not in SKILL_REQUIREMENTS.
    """
    return SKILL_REQUIREMENTS[skill]
