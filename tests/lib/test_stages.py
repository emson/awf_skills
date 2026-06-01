"""Tests for lib/stages.py — stage-keyed catalog module.

Covers:
  - STAGE_ORDER key parity with NEXT_COMPOSERS, RELEVANT_SKILLS.
  - Closed-set integrity: all values in STAGE_REQUIREMENTS and
    SKILL_REQUIREMENTS are valid SUBSYSTEMS ids.
  - next_composer("scale") is None (terminal stage).
  - stage_subsystems("landing") returns the exact curated order.
  - Helper KeyError on unknown input.
  - All four helper functions return correct types.
"""

from __future__ import annotations

import pytest

# Put lib/ on path so we can import lib.stages directly in tests.
import sys
from pathlib import Path

_AWF_HOME = Path(__file__).resolve().parents[3]
if str(_AWF_HOME / "lib") not in sys.path:
    sys.path.insert(0, str(_AWF_HOME / "lib"))
if str(_AWF_HOME) not in sys.path:
    sys.path.insert(0, str(_AWF_HOME))

from stages import (  # noqa: E402
    STAGE_ORDER,
    NEXT_COMPOSERS,
    NEXT_HINTS,
    RELEVANT_SKILLS,
    SUBSYSTEMS,
    STAGE_REQUIREMENTS,
    SKILL_REQUIREMENTS,
    next_composer,
    relevant_skills,
    stage_subsystems,
    skill_subsystems,
)


# ---------------------------------------------------------------------------
# Key parity tests
# ---------------------------------------------------------------------------


def test_stage_order_is_tuple() -> None:
    assert isinstance(STAGE_ORDER, tuple)
    assert len(STAGE_ORDER) > 0


def test_next_composers_keys_match_stage_order() -> None:
    assert set(NEXT_COMPOSERS.keys()) == set(STAGE_ORDER)


def test_relevant_skills_keys_match_stage_order() -> None:
    assert set(RELEVANT_SKILLS.keys()) == set(STAGE_ORDER)


def test_next_hints_keys_match_stage_order() -> None:
    assert set(NEXT_HINTS.keys()) == set(STAGE_ORDER)


def test_stage_requirements_keys_match_stage_order() -> None:
    assert set(STAGE_REQUIREMENTS.keys()) == set(STAGE_ORDER)


# ---------------------------------------------------------------------------
# Closed-set integrity
# ---------------------------------------------------------------------------


def test_stage_requirements_values_in_subsystems() -> None:
    for stage, subs in STAGE_REQUIREMENTS.items():
        for sub in subs:
            assert sub in SUBSYSTEMS, (
                f"STAGE_REQUIREMENTS[{stage!r}] contains unknown subsystem {sub!r}"
            )


def test_skill_requirements_values_in_subsystems() -> None:
    for skill, subs in SKILL_REQUIREMENTS.items():
        for sub in subs:
            assert sub in SUBSYSTEMS, (
                f"SKILL_REQUIREMENTS[{skill!r}] contains unknown subsystem {sub!r}"
            )


# ---------------------------------------------------------------------------
# Terminal stage
# ---------------------------------------------------------------------------


def test_next_composer_scale_is_none() -> None:
    assert next_composer("scale") is None


# ---------------------------------------------------------------------------
# Order assertion for stage_subsystems (C3 carry-note / T4)
# ---------------------------------------------------------------------------


def test_stage_subsystems_landing_exact_order() -> None:
    """stage_subsystems('landing') returns the curated sequence in order.

    This pins the order used by awf-doctor human output (T4 decision).
    """
    result = stage_subsystems("landing")
    assert result == [
        "cloudflare",
        "namecheap",
        "fathom",
        "gsc",
        "bing",
        "git",
        "node",
        "wrangler",
    ]


def test_stage_subsystems_returns_list() -> None:
    for stage in STAGE_ORDER:
        result = stage_subsystems(stage)
        assert isinstance(result, list)


def test_stage_subsystems_mvp_play() -> None:
    result = stage_subsystems("mvp-play")
    assert result == ["hetzner", "neon", "ghcr", "ssh", "kamal_cli", "git"]


# ---------------------------------------------------------------------------
# Helper: KeyError on unknown input
# ---------------------------------------------------------------------------


def test_next_composer_unknown_raises_key_error() -> None:
    with pytest.raises(KeyError):
        next_composer("nonexistent-stage")


def test_relevant_skills_unknown_raises_key_error() -> None:
    with pytest.raises(KeyError):
        relevant_skills("nonexistent-stage")


def test_stage_subsystems_unknown_raises_key_error() -> None:
    with pytest.raises(KeyError):
        stage_subsystems("nonexistent-stage")


def test_skill_subsystems_unknown_raises_key_error() -> None:
    with pytest.raises(KeyError):
        skill_subsystems("nonexistent-skill")


# ---------------------------------------------------------------------------
# Helper return types
# ---------------------------------------------------------------------------


def test_next_composer_returns_str_or_none() -> None:
    result = next_composer("landing")
    assert isinstance(result, str)
    assert result == "awf-stage-demo"


def test_relevant_skills_returns_list() -> None:
    result = relevant_skills("landing")
    assert isinstance(result, list)
    assert len(result) > 0


def test_skill_subsystems_empty_list_for_no_preflight() -> None:
    """Skills with no preflight return empty list (not KeyError)."""
    result = skill_subsystems("awf-app-secret-set")
    assert result == []


def test_skill_subsystems_kamal_deploy() -> None:
    result = skill_subsystems("awf-kamal-deploy")
    assert "kamal_cli" in result
    assert "ssh" in result
    assert "ghcr" in result
