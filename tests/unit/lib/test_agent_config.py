# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for layered agent config resolution and presets."""

import json
import os
import tempfile
import unittest.mock
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

import pytest
from terok_agent import ConfigStack

from terok.lib.core.projects import ProjectConfig, list_presets, load_preset, load_project
from terok.lib.instrumentation.agent_config import build_agent_config_stack, resolve_agent_config
from tests.test_utils import mock_git_config, write_project
from tests.testfs import CONTAINER_INSTRUCTIONS_PATH


def _env(
    config_root: Path,
    state_root: Path,
    global_config: Path | None = None,
    xdg_config_home: Path | None = None,
) -> dict[str, str]:
    """Build env dict for test isolation.

    Always sets XDG_CONFIG_HOME to prevent leaking the host value
    (which would let real user presets pollute test results).
    """
    env: dict[str, str] = {
        "TEROK_CONFIG_DIR": str(config_root),
        "TEROK_STATE_DIR": str(state_root),
        "TEROK_CONFIG_FILE": "",
        "XDG_CONFIG_HOME": str(xdg_config_home or config_root.parent / "xdg"),
    }
    if global_config:
        env["TEROK_CONFIG_FILE"] = str(global_config)
    return env


@dataclass(frozen=True)
class AgentConfigLayout:
    """Isolated filesystem layout for agent-config resolution tests."""

    base: Path
    config_root: Path
    state_root: Path
    xdg_config_home: Path


def make_layout(base: Path) -> AgentConfigLayout:
    """Build the standard isolated config/state/XDG layout for tests."""
    return AgentConfigLayout(
        base=base,
        config_root=base / "config",
        state_root=base / "s",
        xdg_config_home=base / "xdg",
    )


def write_test_project(layout: AgentConfigLayout, project_id: str, body: str | None = None) -> None:
    """Write a test project config, defaulting to a minimal project definition."""
    write_project(
        layout.config_root,
        project_id,
        body or f"project:\n  id: {project_id}\n",
    )


def project_presets_dir(layout: AgentConfigLayout, project_id: str) -> Path:
    """Return the per-project presets directory."""
    presets_dir = layout.config_root / project_id / "presets"
    presets_dir.mkdir(parents=True, exist_ok=True)
    return presets_dir


def write_project_preset(
    layout: AgentConfigLayout,
    project_id: str,
    name: str,
    content: str,
    *,
    suffix: str = ".yml",
) -> Path:
    """Create a project-scoped preset file."""
    preset_path = project_presets_dir(layout, project_id) / f"{name}{suffix}"
    preset_path.write_text(content, encoding="utf-8")
    return preset_path


def global_presets_dir(layout: AgentConfigLayout) -> Path:
    """Return the global XDG presets directory."""
    presets_dir = layout.xdg_config_home / "terok" / "presets"
    presets_dir.mkdir(parents=True, exist_ok=True)
    return presets_dir


def write_global_preset(
    layout: AgentConfigLayout,
    name: str,
    content: str,
    *,
    suffix: str = ".yml",
) -> Path:
    """Create a global XDG preset file."""
    preset_path = global_presets_dir(layout) / f"{name}{suffix}"
    preset_path.write_text(content, encoding="utf-8")
    return preset_path


def _patched_env(
    layout: AgentConfigLayout,
    *,
    global_config: Path | None = None,
    xdg_config_home: Path | None = None,
) -> AbstractContextManager[dict[str, str]]:
    """Patch TEROK_* and XDG env vars for the given layout."""
    return unittest.mock.patch.dict(
        os.environ,
        _env(
            layout.config_root,
            layout.state_root,
            global_config,
            xdg_config_home or layout.xdg_config_home,
        ),
    )


def resolve_test_agent_config(
    layout: AgentConfigLayout,
    project_id: str,
    *,
    global_config: Path | None = None,
    preset: str | None = None,
    cli_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    """Resolve agent config inside the isolated test environment."""
    with _patched_env(layout, global_config=global_config):
        with mock_git_config():
            project = load_project(project_id)
            return resolve_agent_config(
                project_id,
                agent_config=project.agent_config,
                project_root=project.root,
                preset=preset,
                cli_overrides=cli_overrides,
            )


def list_test_presets(
    layout: AgentConfigLayout,
    project_id: str,
    *,
    xdg_config_home: Path | None = None,
) -> list[object]:
    """List presets inside the isolated test environment."""
    with _patched_env(layout, xdg_config_home=xdg_config_home):
        with mock_git_config():
            return list_presets(project_id)


def load_test_preset(
    layout: AgentConfigLayout,
    project_id: str,
    preset_name: str,
    *,
    xdg_config_home: Path | None = None,
) -> tuple[dict[str, object], Path]:
    """Load a preset inside the isolated test environment."""
    with _patched_env(layout, xdg_config_home=xdg_config_home):
        with mock_git_config():
            return load_preset(project_id, preset_name)


def load_test_project(layout: AgentConfigLayout, project_id: str) -> ProjectConfig:
    """Load a project inside the isolated test environment."""
    with _patched_env(layout):
        with mock_git_config():
            return load_project(project_id)


def build_test_agent_stack(
    layout: AgentConfigLayout,
    project_id: str,
    *,
    preset: str,
    xdg_config_home: Path | None = None,
) -> ConfigStack:
    """Build an agent config stack inside the isolated test environment."""
    with _patched_env(layout, xdg_config_home=xdg_config_home):
        with mock_git_config():
            project = load_project(project_id)
            return build_agent_config_stack(
                project_id,
                agent_config=project.agent_config,
                project_root=project.root,
                preset=preset,
            )


class TestResolveAgentConfig:
    """Tests for resolve_agent_config()."""

    def test_empty_config_all_levels(self) -> None:
        """Returns {} when no agent config at any level."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "empty")

            result = resolve_test_agent_config(layout, "empty")
            assert result == {}

    def test_project_only(self) -> None:
        """Project-level agent config is returned when no other levels."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(
                layout,
                "proj",
                "project:\n  id: proj\nagent:\n  model: sonnet\n  subagents:\n"
                "    - name: a1\n      default: true\n",
            )

            result = resolve_test_agent_config(layout, "proj")
            assert result["model"] == "sonnet"
            assert len(result["subagents"]) == 1
            assert result["subagents"][0]["name"] == "a1"

    def test_global_provides_defaults(self) -> None:
        """Global agent config provides defaults when project has none."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")

            global_cfg = layout.base / "global.yml"
            global_cfg.write_text("agent:\n  model: haiku\n  max_turns: 5\n", encoding="utf-8")

            result = resolve_test_agent_config(layout, "proj", global_config=global_cfg)
            assert result["model"] == "haiku"
            assert result["max_turns"] == 5

    def test_project_overrides_global(self) -> None:
        """Project-level config overrides global defaults."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(
                layout,
                "proj",
                "project:\n  id: proj\nagent:\n  model: opus\n",
            )

            global_cfg = layout.base / "global.yml"
            global_cfg.write_text("agent:\n  model: haiku\n  max_turns: 5\n", encoding="utf-8")

            result = resolve_test_agent_config(layout, "proj", global_config=global_cfg)
            assert result["model"] == "opus"
            assert result["max_turns"] == 5

    def test_preset_override(self) -> None:
        """Preset overrides project config."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(
                layout,
                "proj",
                "project:\n  id: proj\nagent:\n  model: sonnet\n",
            )

            write_project_preset(layout, "proj", "fast", "model: haiku\nmax_turns: 3\n")

            result = resolve_test_agent_config(layout, "proj", preset="fast")
            assert result["model"] == "haiku"
            assert result["max_turns"] == 3

    def test_cli_overrides_all(self) -> None:
        """CLI overrides take highest priority."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(
                layout,
                "proj",
                "project:\n  id: proj\nagent:\n  model: sonnet\n",
            )

            result = resolve_test_agent_config(
                layout, "proj", cli_overrides={"model": "opus", "max_turns": 99}
            )
            assert result["model"] == "opus"
            assert result["max_turns"] == 99

    def test_inherit_extends_subagents(self) -> None:
        """Preset with _inherit extends project subagents list."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(
                layout,
                "proj",
                "project:\n  id: proj\nagent:\n  subagents:\n"
                "    - name: base-agent\n      default: true\n",
            )

            write_project_preset(
                layout,
                "proj",
                "extend",
                "subagents:\n  - _inherit\n  - name: extra-agent\n    default: true\n",
            )

            result = resolve_test_agent_config(layout, "proj", preset="extend")
            names = [s["name"] for s in result["subagents"] if isinstance(s, dict)]
            assert names == ["base-agent", "extra-agent"]

    def test_project_config_without_preset(self) -> None:
        """Project agent config resolves correctly without a preset."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(
                layout,
                "proj2",
                "project:\n  id: proj2\nagent:\n  model: sonnet\n"
                "  subagents:\n    - name: sa1\n      default: true\n",
            )

            result = resolve_test_agent_config(layout, "proj2")
            assert result["model"] == "sonnet"
            assert result["subagents"][0]["name"] == "sa1"


class TestPreset:
    """Tests for list_presets() and load_preset()."""

    def test_list_presets_no_project_or_global(self) -> None:
        """No project/global presets — only bundled presets are returned."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")

            result = list_test_presets(layout, "proj")
            non_bundled = [info for info in result if info.source != "bundled"]
            assert non_bundled == []
            bundled = [info for info in result if info.source == "bundled"]
            assert len(bundled) > 0

    def test_list_presets_found(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")

            presets_dir = project_presets_dir(layout, "proj")
            write_project_preset(layout, "proj", "alpha", "model: haiku\n")
            write_project_preset(layout, "proj", "beta", "model: sonnet\n", suffix=".yaml")
            (presets_dir / "ignore.txt").write_text("not a preset\n", encoding="utf-8")

            result = list_test_presets(layout, "proj")
            project_presets = [info for info in result if info.source == "project"]
            assert [info.name for info in project_presets] == ["alpha", "beta"]

    def test_load_preset_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")

            with pytest.raises(SystemExit):
                load_test_preset(layout, "proj", "nonexistent")

    def test_load_preset_found(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")

            preset_path = write_project_preset(
                layout,
                "proj",
                "reviewer",
                "model: sonnet\nmax_turns: 10\n",
            )
            data, path = load_test_preset(layout, "proj", "reviewer")
            assert data["model"] == "sonnet"
            assert data["max_turns"] == 10
            assert path == preset_path

    def test_load_preset_yaml_extension(self) -> None:
        """Preset with .yaml extension is also found."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")

            preset_path = write_project_preset(
                layout,
                "proj",
                "alt",
                "model: opus\n",
                suffix=".yaml",
            )

            data, path = load_test_preset(layout, "proj", "alt")
            assert data["model"] == "opus"
            assert path == preset_path

    def test_presets_dir_property(self) -> None:
        """Project.presets_dir points to presets/ under project root."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            p = load_test_project(layout, "proj")
            assert p.presets_dir == p.root / "presets"


class TestPresetFileRef:
    """Tests for file references within presets."""

    def test_preset_resolves_relative_subagent_file(self) -> None:
        """Subagent file: paths in presets are resolved relative to presets dir."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")

            presets_dir = project_presets_dir(layout, "proj")
            write_project_preset(
                layout,
                "proj",
                "custom",
                "subagents:\n  - name: from-file\n    file: ./agents/reviewer.md\n",
            )

            data, _path = load_test_preset(layout, "proj", "custom")
            resolved = data["subagents"][0]["file"]
            expected = str((presets_dir / "agents" / "reviewer.md").resolve())
            assert resolved == expected

    def test_global_preset_fallback(self) -> None:
        """load_preset finds a preset in the global presets dir when not in project."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            preset_path = write_global_preset(layout, "shared", "model: haiku\nmax_turns: 2\n")
            data, path = load_test_preset(layout, "proj", "shared")
            assert data["model"] == "haiku"
            assert path == preset_path

    def test_project_preset_shadows_global(self) -> None:
        """Project preset shadows a global preset with the same name."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            write_global_preset(layout, "fast", "model: haiku\n")
            preset_path = write_project_preset(layout, "proj", "fast", "model: opus\n")
            data, path = load_test_preset(layout, "proj", "fast")
            assert data["model"] == "opus"
            assert path == preset_path

    def test_global_preset_file_resolution(self) -> None:
        """Subagent file: paths in global presets resolve relative to global presets dir."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")

            global_presets = global_presets_dir(layout)
            write_global_preset(
                layout,
                "with-file",
                "subagents:\n  - name: sa\n    file: ./agents/custom.md\n",
            )

            data, _path = load_test_preset(layout, "proj", "with-file")
            resolved = data["subagents"][0]["file"]
            expected = str((global_presets / "agents" / "custom.md").resolve())
            assert resolved == expected


class TestGlobalPresetList:
    """Tests for list_presets() with global presets."""

    def test_list_presets_includes_global(self) -> None:
        """list_presets returns global and project presets with source labels."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            write_global_preset(layout, "shared", "model: haiku\n")
            write_project_preset(layout, "proj", "local", "model: opus\n")
            result = list_test_presets(layout, "proj")
            non_bundled = {info.name: info.source for info in result if info.source != "bundled"}
            assert non_bundled == {"local": "project", "shared": "global"}

    def test_list_presets_project_shadows_global(self) -> None:
        """Project preset with same name replaces global in listing."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            write_global_preset(layout, "fast", "model: haiku\n")
            write_project_preset(layout, "proj", "fast", "model: opus\n")
            result = list_test_presets(layout, "proj")
            non_bundled = [info for info in result if info.source != "bundled"]
            assert len(non_bundled) == 1
            assert non_bundled[0].name == "fast"
            assert non_bundled[0].source == "project"


class TestGlobalPresetProvenance:
    """Tests for global preset provenance in config stack."""

    def test_global_preset_scope_label(self) -> None:
        """Config stack labels global presets as 'preset (global)'."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            write_global_preset(layout, "shared", "model: haiku\n")
            stack = build_test_agent_stack(layout, "proj", preset="shared")
            levels = [s.level for s in stack.scopes]
            assert "preset (global)" in levels

    def test_project_preset_scope_label(self) -> None:
        """Config stack labels project presets as 'preset (project)'."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            write_project_preset(layout, "proj", "fast", "model: haiku\n")
            stack = build_test_agent_stack(layout, "proj", preset="fast")
            levels = [s.level for s in stack.scopes]
            assert "preset (project)" in levels


def _any_bundled_name() -> str:
    """Return the name of any bundled preset (for tests that need a concrete name)."""
    from terok.lib.core.config import bundled_presets_dir

    bdir = bundled_presets_dir()
    for p in bdir.iterdir():
        if p.is_file() and p.suffix in (".yml", ".yaml"):
            return p.stem
    raise RuntimeError("No bundled presets found — cannot run bundled preset tests")


class TestBundledPreset:
    """Tests for bundled (shipped) presets.

    These tests are name-agnostic: they discover whatever presets happen to
    be shipped in ``resources/presets/`` rather than hardcoding specific names.
    Swap the bundled YAML files freely — only the infrastructure is tested here.
    """

    def test_bundled_presets_discoverable(self) -> None:
        """At least one bundled preset appears in list_presets."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            result = list_test_presets(layout, "proj")
            bundled = [info for info in result if info.source == "bundled"]
            assert len(bundled) > 0, "Expected at least one bundled preset"

    def test_bundled_preset_loadable(self) -> None:
        """Any bundled preset can be loaded via load_preset."""
        name = _any_bundled_name()
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            data, path = load_test_preset(layout, "proj", name)
            assert isinstance(data, dict)
            assert path.is_file()

    def test_global_shadows_bundled(self) -> None:
        """A global preset with the same name as a bundled preset wins."""
        name = _any_bundled_name()
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            preset_path = write_global_preset(
                layout,
                name,
                "model: opus\nmax_turns: 99\n",
            )
            data, path = load_test_preset(layout, "proj", name)
            assert data["model"] == "opus"
            assert data["max_turns"] == 99
            assert path == preset_path

    def test_project_shadows_bundled(self) -> None:
        """A project preset with the same name as a bundled preset wins."""
        name = _any_bundled_name()
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            preset_path = write_project_preset(layout, "proj", name, "model: haiku\n")
            data, path = load_test_preset(layout, "proj", name)
            assert data["model"] == "haiku"
            assert path == preset_path

    def test_bundled_preset_scope_label(self) -> None:
        """Config stack labels bundled presets as 'preset (bundled)'."""
        name = _any_bundled_name()
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            stack = build_test_agent_stack(layout, "proj", preset=name)
            levels = [s.level for s in stack.scopes]
            assert "preset (bundled)" in levels

    def test_shadowed_bundled_gets_correct_source(self) -> None:
        """Shadowing one bundled preset changes its source; others stay bundled."""
        name = _any_bundled_name()
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")
            write_global_preset(layout, name, "model: opus\n")
            result = list_test_presets(layout, "proj")
            by_name = {info.name: info.source for info in result}
            assert by_name[name] == "global"
            remaining_bundled = [n for n, s in by_name.items() if s == "bundled"]
            assert len(remaining_bundled) > 0


class TestInjectOpencodeInstructions:
    """Tests for _inject_opencode_instructions()."""

    def test_creates_file_if_missing(self) -> None:
        """Creates opencode.json with instructions entry and $schema if file does not exist."""
        from terok_agent.agents import _inject_opencode_instructions

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "opencode.json"
            _inject_opencode_instructions(config_path)

            assert config_path.is_file()
            data = json.loads(config_path.read_text(encoding="utf-8"))
            assert data["instructions"] == [str(CONTAINER_INSTRUCTIONS_PATH)]
            assert data["$schema"] == "https://opencode.ai/config.json"

    def test_idempotent_when_already_present(self) -> None:
        """Does not duplicate the instructions entry on repeated calls."""
        from terok_agent.agents import _inject_opencode_instructions

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "opencode.json"
            _inject_opencode_instructions(config_path)
            _inject_opencode_instructions(config_path)

            data = json.loads(config_path.read_text(encoding="utf-8"))
            assert data["instructions"] == [str(CONTAINER_INSTRUCTIONS_PATH)]

    def test_preserves_existing_instructions(self) -> None:
        """Appends to existing instructions list without removing entries."""
        from terok_agent.agents import _inject_opencode_instructions

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "opencode.json"
            config_path.write_text(
                json.dumps({"instructions": ["/some/other/file.md"]}), encoding="utf-8"
            )
            _inject_opencode_instructions(config_path)

            data = json.loads(config_path.read_text(encoding="utf-8"))
            assert data["instructions"] == [
                "/some/other/file.md",
                str(CONTAINER_INSTRUCTIONS_PATH),
            ]

    def test_preserves_existing_config_keys(self) -> None:
        """Preserves other keys in the opencode.json file."""
        from terok_agent.agents import _inject_opencode_instructions

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "opencode.json"
            config_path.write_text(
                json.dumps({"model": "test/model", "provider": {"test": {}}}),
                encoding="utf-8",
            )
            _inject_opencode_instructions(config_path)

            data = json.loads(config_path.read_text(encoding="utf-8"))
            assert data["model"] == "test/model"
            assert data["provider"] == {"test": {}}
            assert data["instructions"] == [str(CONTAINER_INSTRUCTIONS_PATH)]

    def test_creates_parent_directories(self) -> None:
        """Creates parent directories if they do not exist."""
        from terok_agent.agents import _inject_opencode_instructions

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "nested" / "dir" / "opencode.json"
            _inject_opencode_instructions(config_path)

            assert config_path.is_file()
            data = json.loads(config_path.read_text(encoding="utf-8"))
            assert data["instructions"] == [str(CONTAINER_INSTRUCTIONS_PATH)]
            assert data["$schema"] == "https://opencode.ai/config.json"

    def test_handles_invalid_json(self) -> None:
        """Overwrites file with valid config if existing JSON is invalid."""
        from terok_agent.agents import _inject_opencode_instructions

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "opencode.json"
            config_path.write_text("not valid json", encoding="utf-8")
            _inject_opencode_instructions(config_path)

            data = json.loads(config_path.read_text(encoding="utf-8"))
            assert data["instructions"] == [str(CONTAINER_INSTRUCTIONS_PATH)]
            assert data["$schema"] == "https://opencode.ai/config.json"

    def test_preserves_existing_schema(self) -> None:
        """Does not overwrite $schema if already present in existing config."""
        from terok_agent.agents import _inject_opencode_instructions

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "opencode.json"
            config_path.write_text(
                json.dumps({"$schema": "https://opencode.ai/config.json", "model": "x/y"}),
                encoding="utf-8",
            )
            _inject_opencode_instructions(config_path)

            data = json.loads(config_path.read_text(encoding="utf-8"))
            assert data["$schema"] == "https://opencode.ai/config.json"
            assert data["model"] == "x/y"


class TestValidateProjectId:
    """Tests for validate_project_id error messages."""

    def test_error_message_mentions_first_char(self) -> None:
        """Error message describes the first-character requirement."""
        from terok.lib.core.project_model import validate_project_id

        with pytest.raises(SystemExit) as ctx:
            validate_project_id("-bad")
        msg = str(ctx.value)
        assert "must start with a lowercase letter or digit" in msg

    def test_uppercase_rejected(self) -> None:
        """Uppercase letters in project ID are rejected."""
        from terok.lib.core.project_model import validate_project_id

        with pytest.raises(SystemExit) as ctx:
            validate_project_id("MyProject")
        assert "Invalid project ID" in str(ctx.value)
