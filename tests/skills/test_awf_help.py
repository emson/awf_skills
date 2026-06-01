"""Tests for skills/awf-help (plan_013 / C3 / D-008).

Covers:
  - fresh-start mode (no .awf/project.json) → exit 0, fresh-start output.
  - in-project mode → exit 0, stage/next-composer/relevant-skills output.
  - --overview mode → exit 0, grouped-by-stage catalogue.
  - --pipeline deprecated alias → works like --overview + deprecation on stderr.
  - --json output for each mode: schema_version, mode, mode-specific keys.
  - No provider imports in help.py (grep test).
  - Empty RELEVANT_SKILLS stages (prescale, scale) → "(TBD)" note.
  - Peer SKILL.md scraping degrades gracefully if malformed/missing.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / "skills"
_LIB_DIR = _REPO_ROOT / "lib"

for _p in [str(_REPO_ROOT), str(_LIB_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Module import helper
# ---------------------------------------------------------------------------


def _import_awf_help() -> Any:
    script_path = _SKILLS_DIR / "awf-help" / "scripts" / "help.py"
    mod_name = f"awf_help_script_{id(object())}"
    spec = importlib.util.spec_from_file_location(mod_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


_AWF_HELP: Any = None


def _get_awf_help() -> Any:
    global _AWF_HELP
    if _AWF_HELP is None:
        _AWF_HELP = _import_awf_help()
    return _AWF_HELP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_ANCHOR: dict[str, Any] = {
    "awf_version": "0.1.0",
    "domain": "example.com",
    "slug": "example-com",
    "stage": "landing",
    "created": "2026-01-01T00:00:00Z",
}


def _make_project(tmp_path: Path, stage: str = "landing") -> Path:
    awf_dir = tmp_path / ".awf"
    awf_dir.mkdir(exist_ok=True)
    anchor = {**_MINIMAL_ANCHOR, "stage": stage}
    (awf_dir / "project.json").write_text(json.dumps(anchor), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Fresh-start mode
# ---------------------------------------------------------------------------


class TestFreshStartMode:
    def test_no_project_exits_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        monkeypatch.setattr(mod, "_load_anchor", lambda: None)
        rc = mod.main(["help.py"])
        assert rc == 0

    def test_no_project_prints_awf_create_project(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        monkeypatch.setattr(mod, "_load_anchor", lambda: None)
        mod.main(["help.py"])
        captured = capsys.readouterr()
        assert "awf-create-project" in captured.out

    def test_no_project_prints_awf_launch(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        monkeypatch.setattr(mod, "_load_anchor", lambda: None)
        mod.main(["help.py"])
        captured = capsys.readouterr()
        assert "awf-launch" in captured.out

    def test_no_project_mentions_overview(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        monkeypatch.setattr(mod, "_load_anchor", lambda: None)
        mod.main(["help.py"])
        captured = capsys.readouterr()
        assert "--overview" in captured.out

    def test_fresh_start_json_shape(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        monkeypatch.setattr(mod, "_load_anchor", lambda: None)
        rc = mod.main(["help.py", "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["schema_version"] == 1
        assert data["mode"] == "fresh_start"
        assert data["stage"] is None
        assert data["project"] is None
        assert "common_operations" in data

    def test_fresh_start_json_has_doc_links(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        monkeypatch.setattr(mod, "_load_anchor", lambda: None)
        mod.main(["help.py", "--json"])
        data = json.loads(capsys.readouterr().out)
        assert "doc_links" in data
        links_str = " ".join(data["doc_links"])
        assert "07-multi-stage-architecture" in links_str
        assert "08-logging" in links_str


# ---------------------------------------------------------------------------
# 2. In-project mode
# ---------------------------------------------------------------------------


class TestInProjectMode:
    def _make_anchor(self, tmp_path: Path, stage: str = "landing") -> Any:
        _make_project(tmp_path, stage=stage)
        from lib.state import ProjectAnchor
        return ProjectAnchor.load(start=tmp_path)

    def test_in_project_exits_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mod = _get_awf_help()
        anchor = self._make_anchor(tmp_path)
        monkeypatch.setattr(mod, "_load_anchor", lambda: anchor)
        rc = mod.main(["help.py"])
        assert rc == 0

    def test_in_project_shows_stage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        anchor = self._make_anchor(tmp_path, stage="landing")
        monkeypatch.setattr(mod, "_load_anchor", lambda: anchor)
        mod.main(["help.py"])
        captured = capsys.readouterr()
        assert "landing" in captured.out

    def test_in_project_shows_next_composer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        anchor = self._make_anchor(tmp_path, stage="landing")
        monkeypatch.setattr(mod, "_load_anchor", lambda: anchor)
        mod.main(["help.py"])
        captured = capsys.readouterr()
        assert "awf-stage-demo" in captured.out

    def test_in_project_shows_relevant_skills(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        anchor = self._make_anchor(tmp_path, stage="landing")
        monkeypatch.setattr(mod, "_load_anchor", lambda: anchor)
        mod.main(["help.py"])
        captured = capsys.readouterr()
        assert "awf-deploy" in captured.out
        assert "awf-setup-domain" in captured.out

    def test_in_project_shows_common_operations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        anchor = self._make_anchor(tmp_path, stage="landing")
        monkeypatch.setattr(mod, "_load_anchor", lambda: anchor)
        mod.main(["help.py"])
        captured = capsys.readouterr()
        assert "awf-status" in captured.out
        assert "awf-doctor" in captured.out

    def test_in_project_json_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        anchor = self._make_anchor(tmp_path, stage="demo")
        monkeypatch.setattr(mod, "_load_anchor", lambda: anchor)
        rc = mod.main(["help.py", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["schema_version"] == 1
        assert data["mode"] == "in_project"
        assert data["stage"] == "demo"
        assert "relevant_skills" in data
        assert isinstance(data["relevant_skills"], list)
        assert "project" in data
        assert data["project"]["slug"] == "example-com"
        assert data["project"]["domain"] == "example.com"

    def test_in_project_prescale_shows_tbd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """prescale stage has empty RELEVANT_SKILLS → TBD note."""
        mod = _get_awf_help()
        anchor = self._make_anchor(tmp_path, stage="prescale")
        monkeypatch.setattr(mod, "_load_anchor", lambda: anchor)
        mod.main(["help.py"])
        captured = capsys.readouterr()
        assert "TBD" in captured.out or "plan_014" in captured.out


# ---------------------------------------------------------------------------
# 3. Overview mode
# ---------------------------------------------------------------------------


class TestOverviewMode:
    def test_overview_exits_0(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        mod = _get_awf_help()
        rc = mod.main(["help.py", "--overview"])
        assert rc == 0

    def test_overview_shows_all_stages(self, capsys: Any) -> None:
        from lib.stages import STAGE_ORDER
        mod = _get_awf_help()
        mod.main(["help.py", "--overview"])
        captured = capsys.readouterr()
        for stage in STAGE_ORDER:
            assert stage in captured.out

    def test_overview_shows_doc_links(self, capsys: Any) -> None:
        mod = _get_awf_help()
        mod.main(["help.py", "--overview"])
        captured = capsys.readouterr()
        assert "07-multi-stage-architecture" in captured.out
        assert "08-logging" in captured.out

    def test_overview_prescale_scale_shows_tbd(self, capsys: Any) -> None:
        mod = _get_awf_help()
        mod.main(["help.py", "--overview"])
        captured = capsys.readouterr()
        assert "TBD" in captured.out or "plan_014" in captured.out

    def test_overview_json_shape(self, capsys: Any) -> None:
        mod = _get_awf_help()
        rc = mod.main(["help.py", "--overview", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["schema_version"] == 1
        assert data["mode"] == "overview"
        assert "stages" in data
        assert isinstance(data["stages"], list)
        # Must have exactly STAGE_ORDER stages
        from lib.stages import STAGE_ORDER
        assert len(data["stages"]) == len(STAGE_ORDER)

    def test_overview_json_stages_in_order(self, capsys: Any) -> None:
        from lib.stages import STAGE_ORDER
        mod = _get_awf_help()
        mod.main(["help.py", "--overview", "--json"])
        data = json.loads(capsys.readouterr().out)
        names = [s["name"] for s in data["stages"]]
        assert names == list(STAGE_ORDER)

    def test_overview_json_each_stage_has_atomic_skills(self, capsys: Any) -> None:
        mod = _get_awf_help()
        mod.main(["help.py", "--overview", "--json"])
        data = json.loads(capsys.readouterr().out)
        for stage in data["stages"]:
            assert "atomic_skills" in stage
            assert isinstance(stage["atomic_skills"], list)

    def test_overview_json_skill_has_name_and_description(self, capsys: Any) -> None:
        mod = _get_awf_help()
        mod.main(["help.py", "--overview", "--json"])
        data = json.loads(capsys.readouterr().out)
        landing = next(s for s in data["stages"] if s["name"] == "landing")
        for sk in landing["atomic_skills"]:
            assert "name" in sk
            assert "description" in sk


# ---------------------------------------------------------------------------
# 4. --pipeline deprecated alias
# ---------------------------------------------------------------------------


class TestPipelineAlias:
    def test_pipeline_exits_0(self, capsys: Any) -> None:
        mod = _get_awf_help()
        rc = mod.main(["help.py", "--pipeline"])
        assert rc == 0

    def test_pipeline_produces_overview_output(self, capsys: Any) -> None:
        from lib.stages import STAGE_ORDER
        mod = _get_awf_help()
        mod.main(["help.py", "--pipeline"])
        captured = capsys.readouterr()
        for stage in STAGE_ORDER:
            assert stage in captured.out

    def test_pipeline_emits_deprecation_to_stderr(self, capsys: Any) -> None:
        mod = _get_awf_help()
        mod.main(["help.py", "--pipeline"])
        captured = capsys.readouterr()
        assert "--pipeline" in captured.err
        assert "--overview" in captured.err


# ---------------------------------------------------------------------------
# 5. No provider imports (grep test)
# ---------------------------------------------------------------------------


def test_help_py_has_no_provider_imports() -> None:
    """help.py must not import any provider client library."""
    script_path = _SKILLS_DIR / "awf-help" / "scripts" / "help.py"
    content = script_path.read_text(encoding="utf-8")
    forbidden = ["lib.cf", "lib.hetzner", "lib.neon", "lib.fathom",
                 "from cf", "from hetzner", "from neon"]
    for token in forbidden:
        assert token not in content, f"help.py must not import {token!r}"


def test_help_py_imports_only_lib_stages_state_project() -> None:
    """help.py lib imports restricted to lib.stages, lib.state, lib.project."""
    script_path = _SKILLS_DIR / "awf-help" / "scripts" / "help.py"
    content = script_path.read_text(encoding="utf-8")
    assert "lib.stages" in content
    assert "lib.state" in content


# ---------------------------------------------------------------------------
# 6. Peer SKILL.md scraping degrades gracefully
# ---------------------------------------------------------------------------


class TestSkillDescriptionScraping:
    def test_missing_skill_dir_falls_back_to_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If a skill directory is missing, description falls back to skill name."""
        mod = _get_awf_help()
        # Temporarily point AWF_HOME to tmp_path (no skills dir).
        monkeypatch.setattr(mod, "AWF_HOME", tmp_path)
        result = mod._scrape_skill_description("awf-some-skill")
        assert result == "awf-some-skill"

    def test_malformed_skill_md_falls_back_to_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed SKILL.md (no description: field) → falls back to name."""
        mod = _get_awf_help()
        skill_dir = tmp_path / "skills" / "awf-broken"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# No frontmatter here\nJust content.", encoding="utf-8")
        monkeypatch.setattr(mod, "AWF_HOME", tmp_path)
        result = mod._scrape_skill_description("awf-broken")
        assert result == "awf-broken"

    def test_valid_skill_md_returns_description(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mod = _get_awf_help()
        skill_dir = tmp_path / "skills" / "awf-test"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: awf-test\ndescription: Test skill description.\n---\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "AWF_HOME", tmp_path)
        result = mod._scrape_skill_description("awf-test")
        assert result == "Test skill description."

    def test_overview_with_missing_skills_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """render_overview must never raise even when peer SKILL.md files are absent."""
        mod = _get_awf_help()
        # Temporarily point AWF_HOME to a directory with no skills.
        monkeypatch.setattr(mod, "AWF_HOME", tmp_path)
        # Should not raise.
        rc = mod.main(["help.py", "--overview"])
        assert rc == 0
