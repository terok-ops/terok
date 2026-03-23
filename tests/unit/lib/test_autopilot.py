# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for autopilot (Level 1+2) features: terokctl run and agent config."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest.mock
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from terok.lib.util.yaml import dump as yaml_dump, load as yaml_load
from tests.testfs import (
    CONTAINER_CLAUDE_MEMORY_OVERRIDE,
    CONTAINER_CLAUDE_SESSION_PATH,
    CONTAINER_TEROK_MOUNT_Z,
    FAKE_PROJECT_GATE_DIR,
    FAKE_PROJECT_ROOT,
    NONEXISTENT_AGENT_PATH,
    NONEXISTENT_FILE_PATH,
)

if TYPE_CHECKING:
    from terok.lib.core.projects import ProjectConfig

from terok_agent import WrapperConfig, parse_md_agent
from terok_agent.agents import (
    _generate_claude_wrapper,
    _subagents_to_json,
    _write_session_hook,
)

from terok.lib.core.projects import load_project
from terok.lib.orchestration.task_runners import (
    HeadlessRunRequest,
    task_followup_headless,
    task_run_headless,
)
from tests.test_utils import mock_git_config, write_project


@dataclass
class TaskRunnerResult:
    """Captured result from a mocked headless task/follow-up invocation."""

    output: str
    run_mock: unittest.mock.Mock
    wait_mock: unittest.mock.Mock
    task_id: str | None = None


def make_project_config(
    *,
    project_id: str,
    root: Path,
    tasks_root: Path,
    gate_path: Path,
    default_agent: str | None = None,
) -> ProjectConfig:
    """Build a ProjectConfig for wrapper/agent-config unit tests."""
    from terok.lib.core.projects import ProjectConfig

    return ProjectConfig(
        id=project_id,
        security_class="online",
        upstream_url=None,
        default_branch="main",
        root=root,
        tasks_root=tasks_root,
        gate_path=gate_path,
        staging_root=None,
        ssh_key_name=None,
        ssh_host_dir=None,
        default_agent=default_agent,
        human_name="Test User",
        human_email="test@example.com",
    )


def runner_env_vars(base: Path, config_file: Path) -> dict[str, str]:
    """Build TEROK_* env vars for task runner tests."""
    return {
        "TEROK_CONFIG_DIR": str(base / "config"),
        "TEROK_STATE_DIR": str(base / "state"),
        "TEROK_CONFIG_FILE": str(config_file),
    }


def task_paths(state_dir: Path, project_id: str, task_id: str = "1") -> tuple[Path, Path]:
    """Return ``(agent_config_dir, meta_path)`` for a task."""
    return (
        state_dir / "tasks" / project_id / task_id / "agent-config",
        state_dir / "projects" / project_id / "tasks" / f"{task_id}.yml",
    )


DEFAULT_SUBAGENTS_YAML = (
    "agent:\n"
    "  subagents:\n"
    "    - name: reviewer\n"
    "      default: true\n"
    "      system_prompt: Review code\n"
    "    - name: debugger\n"
    "      default: false\n"
    "      system_prompt: Debug code\n"
)


def write_runner_project(base: Path, project_id: str, extra_yml: str = "") -> Path:
    """Write a minimal project config and matching global config file for task runners."""
    config_root = base / "config"
    envs_dir = base / "envs"
    config_root.mkdir(parents=True, exist_ok=True)
    config_file = base / "config.yml"
    config_file.write_text(f"envs:\n  base_dir: {envs_dir}\n", encoding="utf-8")
    write_project(config_root, project_id, f"project:\n  id: {project_id}\n{extra_yml}")
    return config_file


def read_task_agents(state_dir: Path, project_id: str, task_id: str = "1") -> dict[str, object]:
    """Load ``agents.json`` for a task."""
    return json.loads((task_paths(state_dir, project_id, task_id)[0] / "agents.json").read_text())


def read_task_meta(state_dir: Path, project_id: str, task_id: str = "1") -> dict[str, object]:
    """Load task metadata YAML for a task."""
    return yaml_load(task_paths(state_dir, project_id, task_id)[1].read_text())


def prepare_agent_config(
    project: ProjectConfig,
    task_id: str,
    *,
    instructions: str | None = None,
) -> Path:
    """Build an agent-config directory for the given task."""
    from terok_agent import AgentConfigSpec, prepare_agent_config_dir

    (project.tasks_root / task_id).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        return prepare_agent_config_dir(
            AgentConfigSpec(
                project.tasks_root,
                task_id,
                subagents=[],
                instructions=instructions,
                default_agent=project.default_agent,
                envs_base_dir=Path(td),
            )
        )


def run_headless_request(
    base: Path,
    config_file: Path,
    request: HeadlessRunRequest,
) -> TaskRunnerResult:
    """Run ``task_run_headless`` with the standard patched test harness."""
    with unittest.mock.patch.dict(os.environ, runner_env_vars(base, config_file), clear=True):
        with (
            mock_git_config(),
            unittest.mock.patch("terok.lib.orchestration.task_runners.subprocess.run") as run_mock,
            unittest.mock.patch(
                "terok.lib.orchestration.task_runners.wait_for_exit", return_value=0
            ) as wait_mock,
            unittest.mock.patch("terok.lib.orchestration.task_runners._print_run_summary"),
        ):
            run_mock.return_value = subprocess.CompletedProcess([], 0)
            buffer = StringIO()
            with redirect_stdout(buffer):
                task_id = task_run_headless(request)
    return TaskRunnerResult(
        task_id=task_id,
        output=buffer.getvalue(),
        run_mock=run_mock,
        wait_mock=wait_mock,
    )


def run_followup_request(
    base: Path,
    project_id: str,
    task_id: str,
    prompt: str,
    *,
    container_state: list[str | None] | str | None,
    follow: bool = True,
) -> TaskRunnerResult:
    """Run ``task_followup_headless`` with the standard patched success harness."""
    with unittest.mock.patch.dict(
        os.environ, runner_env_vars(base, base / "config.yml"), clear=True
    ):
        with (
            mock_git_config(),
            unittest.mock.patch("terok.lib.orchestration.task_runners.subprocess.run") as run_mock,
            unittest.mock.patch(
                "terok.lib.orchestration.task_runners.get_container_state",
                side_effect=container_state if isinstance(container_state, list) else None,
                return_value=None if isinstance(container_state, list) else container_state,
            ),
            unittest.mock.patch(
                "terok.lib.orchestration.task_runners.wait_for_exit", return_value=0
            ) as wait_mock,
            unittest.mock.patch("terok.lib.orchestration.task_runners._print_run_summary"),
        ):
            run_mock.return_value = subprocess.CompletedProcess([], 0)
            buffer = StringIO()
            with redirect_stdout(buffer):
                task_followup_headless(project_id, task_id, prompt, follow=follow)
    return TaskRunnerResult(output=buffer.getvalue(), run_mock=run_mock, wait_mock=wait_mock)


class TestAgentConfigProject:
    """Tests for agent config parsing in projects.py."""

    def test_agent_config_empty_when_absent(self) -> None:
        """Project has agent_config={} when not configured."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_root = base / "config"
            write_project(config_root, "proj_noagent", "project:\n  id: proj_noagent\n")

            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_root), "TEROK_STATE_DIR": str(base / "s")},
            ):
                with mock_git_config():
                    p = load_project("proj_noagent")
                assert p.agent_config == {}

    def test_agent_config_parsed_as_dict(self) -> None:
        """Project parses agent: section as a dict."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_root = base / "config"

            write_project(
                config_root,
                "proj_agent",
                (
                    "project:\n  id: proj_agent\nagent:\n  subagents:\n"
                    "    - name: reviewer\n      default: true\n"
                    "      system_prompt: Review code\n"
                ),
            )

            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_root), "TEROK_STATE_DIR": str(base / "s")},
            ):
                with mock_git_config():
                    p = load_project("proj_agent")
                assert "subagents" in p.agent_config
                assert p.agent_config["subagents"][0]["name"] == "reviewer"

    def test_agent_config_resolves_subagent_file_paths(self) -> None:
        """Project resolves relative file: paths in subagents."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_root = base / "config"

            write_project(
                config_root,
                "proj_sa",
                ("project:\n  id: proj_sa\nagent:\n  subagents:\n    - file: agents/reviewer.md\n"),
            )

            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_root), "TEROK_STATE_DIR": str(base / "s")},
            ):
                with mock_git_config():
                    p = load_project("proj_sa")
                # File path should be resolved to absolute
                sa = p.agent_config["subagents"][0]
                assert Path(sa["file"]).is_absolute()
                assert "agents/reviewer.md" in sa["file"]


class TestSubagentsToJson:
    """Tests for _subagents_to_json (dict output keyed by agent name)."""

    def test_inline_definition_default_true(self) -> None:
        """Inline sub-agent with default=True is included, output is dict keyed by name."""
        subagents = [
            {
                "name": "reviewer",
                "description": "Code reviewer",
                "tools": ["Read", "Grep"],
                "model": "sonnet",
                "default": True,
                "system_prompt": "You are a code reviewer.",
            }
        ]
        result = json.loads(_subagents_to_json(subagents))
        assert isinstance(result, dict)
        assert "reviewer" in result
        assert result["reviewer"]["prompt"] == "You are a code reviewer."
        assert result["reviewer"]["description"] == "Code reviewer"
        assert result["reviewer"]["tools"] == ["Read", "Grep"]
        assert result["reviewer"]["model"] == "sonnet"
        # Non-Claude fields stripped
        assert "system_prompt" not in result["reviewer"]
        assert "name" not in result["reviewer"]
        assert "default" not in result["reviewer"]

    def test_default_false_excluded_without_selection(self) -> None:
        """Agents with default=False are excluded when not selected."""
        subagents = [
            {"name": "debugger", "default": False, "model": "sonnet", "system_prompt": "Debug."},
        ]
        result = json.loads(_subagents_to_json(subagents))
        assert result == {}

    def test_no_default_flag_excluded(self) -> None:
        """Agents without a default flag are excluded (default=False is the default)."""
        subagents = [
            {"name": "debugger", "model": "sonnet", "system_prompt": "Debug."},
        ]
        result = json.loads(_subagents_to_json(subagents))
        assert result == {}

    def test_selected_agents_included(self) -> None:
        """Non-default agents are included when passed in selected_agents."""
        subagents = [
            {"name": "debugger", "default": False, "model": "sonnet", "system_prompt": "Debug."},
        ]
        result = json.loads(_subagents_to_json(subagents, selected_agents=["debugger"]))
        assert "debugger" in result
        assert result["debugger"]["prompt"] == "Debug."

    def test_mixed_default_and_selected(self) -> None:
        """Default agents + selected non-default agents are both included."""
        subagents = [
            {"name": "reviewer", "default": True, "model": "sonnet", "system_prompt": "Review."},
            {"name": "debugger", "default": False, "model": "opus", "system_prompt": "Debug."},
            {"name": "planner", "default": False, "model": "haiku", "system_prompt": "Plan."},
        ]
        result = json.loads(_subagents_to_json(subagents, selected_agents=["debugger"]))
        assert "reviewer" in result
        assert "debugger" in result
        assert "planner" not in result

    def test_file_reference_with_default(self) -> None:
        """File references with default flag are handled correctly."""
        with tempfile.TemporaryDirectory() as td:
            md_file = Path(td) / "reviewer.md"
            md_file.write_text(
                "---\nname: reviewer\ndescription: Code reviewer\n"
                "tools: [Read, Grep]\nmodel: sonnet\n---\n"
                "You are a code reviewer.\n",
                encoding="utf-8",
            )
            subagents = [{"file": str(md_file), "default": True}]
            result = json.loads(_subagents_to_json(subagents))
            assert "reviewer" in result
            assert result["reviewer"]["prompt"] == "You are a code reviewer."

    def test_passthrough_native_claude_fields(self) -> None:
        """Native Claude fields like mcpServers, hooks are passed through."""
        subagents = [
            {
                "name": "advanced",
                "default": True,
                "model": "sonnet",
                "mcpServers": {"srv": {"command": "/bin/x"}},
                "hooks": {"onStart": "echo hi"},
                "system_prompt": "Advanced agent.",
            }
        ]
        result = json.loads(_subagents_to_json(subagents))
        assert result["advanced"]["mcpServers"] == {"srv": {"command": "/bin/x"}}
        assert result["advanced"]["hooks"] == {"onStart": "echo hi"}

    def test_missing_file_skipped(self) -> None:
        """Missing file references are skipped."""
        subagents = [{"file": str(NONEXISTENT_AGENT_PATH), "default": True}]
        result = json.loads(_subagents_to_json(subagents))
        assert result == {}

    def test_agent_without_name_skipped(self) -> None:
        """Agents without a name are skipped."""
        subagents = [{"default": True, "model": "sonnet", "system_prompt": "No name."}]
        result = json.loads(_subagents_to_json(subagents))
        assert result == {}


class TestParseMdAgent:
    """Tests for parse_md_agent."""

    def test_parse_with_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            md = Path(td) / "test.md"
            md.write_text(
                "---\nname: test\ntools: [Read]\n---\nPrompt body.",
                encoding="utf-8",
            )
            result = parse_md_agent(str(md))
            assert result["name"] == "test"
            assert result["prompt"] == "Prompt body."

    def test_parse_without_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            md = Path(td) / "test.md"
            md.write_text("Just a prompt.", encoding="utf-8")
            result = parse_md_agent(str(md))
            assert result["prompt"] == "Just a prompt."

    def test_nonexistent_file(self) -> None:
        result = parse_md_agent(str(NONEXISTENT_FILE_PATH))
        assert result == {}


class TestGenerateClaudeWrapper:
    """Tests for _generate_claude_wrapper."""

    def test_basic_wrapper(self) -> None:
        """Wrapper includes add-dir / and git env vars."""
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=False))
        assert "claude()" in wrapper
        assert "--dangerously-skip-permissions" not in wrapper
        assert '--add-dir "/"' in wrapper
        assert "_terok_apply_git_identity Claude noreply@anthropic.com" in wrapper
        # Should NOT contain agents reference when has_agents=False
        assert "agents.json" not in wrapper

    def test_wrapper_with_agents(self) -> None:
        """Wrapper includes agents.json reference when has_agents=True."""
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=True))
        assert "agents.json" in wrapper

    def test_wrapper_does_not_inject_permission_flags(self) -> None:
        """Wrapper relies on managed settings, not permission CLI flags."""
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=False))
        assert "TEROK_UNRESTRICTED" not in wrapper
        assert "--dangerously-skip-permissions" not in wrapper
        # --add-dir / is always present regardless of permission mode
        assert '--add-dir "/"' in wrapper

    def test_wrapper_no_model_or_mcp(self) -> None:
        """Wrapper does not contain --model, --mcp-config by default."""
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=True))
        assert "--model" not in wrapper
        assert "--mcp-config" not in wrapper
        assert "--max-turns" not in wrapper
        # --append-system-prompt absent when has_instructions=False (default)
        assert "--append-system-prompt" not in wrapper

    def test_wrapper_includes_append_system_prompt(self) -> None:
        """Wrapper includes --append-system-prompt when has_instructions=True."""
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=False, has_instructions=True))
        assert "--append-system-prompt" in wrapper
        assert "instructions.md" in wrapper

    def test_wrapper_timeout_support(self) -> None:
        """Wrapper parses --terok-timeout and wraps claude with timeout."""
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=False))
        # Wrapper should contain timeout flag parsing
        assert "--terok-timeout" in wrapper
        assert "_timeout" in wrapper
        # Wrapper should use timeout command when _timeout is set
        assert 'timeout "$_timeout" claude' in wrapper
        # Wrapper should still have the non-timeout path
        assert 'command claude "${_args[@]}" "$@"' in wrapper
        # Both paths should apply git identity through the shared helper
        assert wrapper.count("_terok_apply_git_identity Claude noreply@anthropic.com") == 2

    def test_wrapper_resume_from_session_file(self) -> None:
        """Wrapper adds --resume from claude-session.txt when it exists."""
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=False))
        assert "claude-session.txt" in wrapper
        assert "--resume" in wrapper

    def test_wrapper_sets_memory_override_with_project_id(self) -> None:
        """Wrapper exports CLAUDE_COWORK_MEMORY_PATH_OVERRIDE using $PROJECT_ID."""
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=False))
        assert (
            "export CLAUDE_COWORK_MEMORY_PATH_OVERRIDE="
            f'"{CONTAINER_CLAUDE_MEMORY_OVERRIDE}"' in wrapper
        )


class TestWriteSessionHook:
    """Tests for _write_session_hook."""

    def test_creates_settings_with_hook(self) -> None:
        """Creates settings.json with a SessionStart hook."""
        with tempfile.TemporaryDirectory() as td:
            settings_path = Path(td) / "settings.json"
            _write_session_hook(settings_path)
            assert settings_path.is_file()
            data = json.loads(settings_path.read_text())
            assert "hooks" in data
            assert "SessionStart" in data["hooks"]
            hooks = data["hooks"]["SessionStart"]
            assert len(hooks) == 1
            command = hooks[0]["hooks"][0]["command"]
            assert "session_id" in command
            assert "claude-session.txt" in command

    def test_merges_with_existing_settings(self) -> None:
        """Merges hook into existing settings.json without clobbering."""
        with tempfile.TemporaryDirectory() as td:
            settings_path = Path(td) / "settings.json"
            settings_path.write_text('{"permissions": {"allow": ["Read"]}}', encoding="utf-8")
            _write_session_hook(settings_path)
            data = json.loads(settings_path.read_text())
            # Original settings preserved
            assert data["permissions"] == {"allow": ["Read"]}
            # Hook added
            assert "SessionStart" in data["hooks"]

    def test_idempotent_hook_write(self) -> None:
        """Calling _write_session_hook twice doesn't create duplicate hooks."""
        with tempfile.TemporaryDirectory() as td:
            settings_path = Path(td) / "settings.json"
            _write_session_hook(settings_path)
            _write_session_hook(settings_path)
            data = json.loads(settings_path.read_text())
            hooks = data["hooks"]["SessionStart"]
            assert len(hooks) == 1

    def test_does_not_rewrite_when_hook_already_present(self) -> None:
        """If equivalent hook exists, keep existing file content unchanged."""
        with tempfile.TemporaryDirectory() as td:
            settings_path = Path(td) / "settings.json"
            hook_command = (
                "python3 -c \"import json,sys; print(json.load(sys.stdin)['session_id'])\""
                f" > {CONTAINER_CLAUDE_SESSION_PATH}"
            )
            original = json.dumps(
                {
                    "hooks": {
                        "SessionStart": [{"hooks": [{"type": "command", "command": hook_command}]}]
                    }
                },
                separators=(",", ":"),
            )
            settings_path.write_text(original, encoding="utf-8")

            _write_session_hook(settings_path)

            assert settings_path.read_text(encoding="utf-8") == original

    def test_handles_non_dict_hooks_shape(self) -> None:
        """Recovers if hooks shape is invalid and still writes SessionStart hook."""
        with tempfile.TemporaryDirectory() as td:
            settings_path = Path(td) / "settings.json"
            settings_path.write_text(
                '{"hooks": "invalid", "permissions": {"allow": ["Read"]}}',
                encoding="utf-8",
            )

            _write_session_hook(settings_path)

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            assert data["permissions"] == {"allow": ["Read"]}
            assert "SessionStart" in data["hooks"]

    def test_concurrent_writes_keep_single_valid_hook(self) -> None:
        """Concurrent writes keep settings valid and avoid duplicate SessionStart entries."""
        with tempfile.TemporaryDirectory() as td:
            settings_path = Path(td) / "settings.json"
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(_write_session_hook, settings_path) for _ in range(48)]
                for future in futures:
                    future.result()

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            hooks = data["hooks"]["SessionStart"]
            assert len(hooks) == 1


class TestPrepareAgentConfigDir:
    """Tests for prepare_agent_config_dir."""

    @staticmethod
    def _prepare_project(tasks_root: Path):
        """Create a minimal project config for ``prepare_agent_config`` tests."""
        return make_project_config(
            project_id="test-proj",
            root=FAKE_PROJECT_ROOT,
            tasks_root=tasks_root,
            gate_path=FAKE_PROJECT_GATE_DIR,
        )

    @unittest.mock.patch("terok_agent.agents._write_session_hook")
    def test_prepare_agent_config_writes_instructions(
        self,
        _mock_hook: object,
        tmp_path: Path,
    ) -> None:
        """Instructions text is written to instructions.md in agent-config dir."""
        project = self._prepare_project(tmp_path / "tasks")
        agent_config_dir = prepare_agent_config(
            project, "test-task-1", instructions="Custom instructions here."
        )
        instr_path = agent_config_dir / "instructions.md"
        assert instr_path.is_file()
        assert instr_path.read_text(encoding="utf-8") == "Custom instructions here."

    @unittest.mock.patch("terok_agent.agents._write_session_hook")
    def test_prepare_agent_config_default_instructions_when_none(
        self,
        _mock_hook: object,
        tmp_path: Path,
    ) -> None:
        """Default instructions.md written when instructions is None."""
        project = self._prepare_project(tmp_path / "tasks")
        agent_config_dir = prepare_agent_config(project, "test-task-2")
        instr_path = agent_config_dir / "instructions.md"
        assert instr_path.is_file()
        content = instr_path.read_text(encoding="utf-8")
        assert "conventions" in content

    @unittest.mock.patch("terok_agent.agents._write_session_hook")
    def test_wrapper_has_append_system_prompt_when_instructions(
        self,
        _mock_hook: object,
        tmp_path: Path,
    ) -> None:
        """Claude wrapper includes --append-system-prompt when instructions are provided."""
        project = self._prepare_project(tmp_path / "tasks")
        agent_config_dir = prepare_agent_config(
            project, "test-task-3", instructions="Test instructions."
        )
        wrapper = (agent_config_dir / "terok-agent.sh").read_text(encoding="utf-8")
        assert "--append-system-prompt" in wrapper
        assert "instructions.md" in wrapper


class TestTaskRunHeadless:
    """Tests for task_run_headless."""

    def test_headless_creates_task_and_writes_prompt(self) -> None:
        """task_run_headless creates a task with prompt.txt in agent-config dir."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_hl"),
                HeadlessRunRequest("proj_hl", "Fix the auth bug"),
            )

            assert result.task_id == "1"
            agent_config_dir, _meta_path = task_paths(state_dir, "proj_hl")
            prompt_file = agent_config_dir / "prompt.txt"
            assert prompt_file.is_file()
            assert prompt_file.read_text() == "Fix the auth bug"

    def test_headless_mounts_agent_config_dir(self) -> None:
        """task_run_headless mounts agent-config dir to /home/dev/.terok."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_mount"),
                HeadlessRunRequest("proj_mount", "test prompt"),
            )
            assert CONTAINER_TEROK_MOUNT_Z in " ".join(result.run_mock.call_args[0][0])

    def test_headless_generates_agent_wrapper(self) -> None:
        """task_run_headless generates terok-agent.sh in agent-config dir."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            run_headless_request(
                base,
                write_runner_project(base, "proj_wrap"),
                HeadlessRunRequest("proj_wrap", "test"),
            )

            wrapper = task_paths(state_dir, "proj_wrap")[0] / "terok-agent.sh"
            assert wrapper.is_file()
            content = wrapper.read_text()
            assert "claude()" in content
            assert "--dangerously-skip-permissions" not in content

    def test_headless_writes_session_hook_settings(self) -> None:
        """task_run_headless writes shared Claude settings with SessionStart hook."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            run_headless_request(
                base,
                write_runner_project(base, "proj_hook"),
                HeadlessRunRequest("proj_hook", "test"),
            )

            settings = base / "envs" / "_claude-config" / "settings.json"
            assert settings.is_file()
            assert "SessionStart" in json.loads(settings.read_text())["hooks"]

    def test_headless_with_default_subagents(self) -> None:
        """task_run_headless includes default subagents in agents.json."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            run_headless_request(
                base,
                write_runner_project(base, "proj_agents", DEFAULT_SUBAGENTS_YAML),
                HeadlessRunRequest("proj_agents", "test"),
            )

            agents_data = read_task_agents(state_dir, "proj_agents")
            assert isinstance(agents_data, dict)
            assert "reviewer" in agents_data
            assert "debugger" not in agents_data
            assert agents_data["reviewer"]["prompt"] == "Review code"

    def test_headless_with_agent_selection(self) -> None:
        """task_run_headless includes selected non-default agents in agents.json."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            run_headless_request(
                base,
                write_runner_project(base, "proj_sel", DEFAULT_SUBAGENTS_YAML),
                HeadlessRunRequest("proj_sel", "test", agents=["debugger"]),
            )

            agents_data = read_task_agents(state_dir, "proj_sel")
            assert "reviewer" in agents_data
            assert "debugger" in agents_data

    def test_headless_cli_model_max_turns_in_command(self) -> None:
        """CLI model/max_turns appear in headless bash command, not in wrapper."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_flags"),
                HeadlessRunRequest("proj_flags", "test", model="opus", max_turns=100),
            )

            bash_cmd = result.run_mock.call_args[0][0][-1]
            assert "--model opus" in bash_cmd
            assert "--max-turns 100" in bash_cmd
            assert "--terok-timeout" in bash_cmd

            wrapper = task_paths(state_dir, "proj_flags")[0] / "terok-agent.sh"
            content = wrapper.read_text()
            assert "--model" not in content
            assert "--max-turns" not in content
            assert "--terok-timeout" in content

    def test_headless_container_name_uses_run_prefix(self) -> None:
        """task_run_headless names the container <project>-run-<task_id>."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_name"),
                HeadlessRunRequest("proj_name", "test"),
            )
            cmd = result.run_mock.call_args[0][0]
            assert cmd[cmd.index("--name") + 1] == "proj_name-run-1"

    def test_headless_metadata_updated(self) -> None:
        """task_run_headless sets mode=run and updates status on completion."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            run_headless_request(
                base,
                write_runner_project(base, "proj_meta"),
                HeadlessRunRequest("proj_meta", "test"),
            )

            meta = read_task_meta(state_dir, "proj_meta")
            assert meta["mode"] == "run"
            assert meta["exit_code"] == 0

    def test_headless_no_follow_mode(self) -> None:
        """task_run_headless with follow=False prints detach info."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_nf"),
                HeadlessRunRequest("proj_nf", "test", follow=False),
            )

            result.wait_mock.assert_not_called()
            assert "detached" in result.output.lower()
            assert "proj_nf-run-1" in result.output

    def test_headless_uses_claude_function_in_command(self) -> None:
        """task_run_headless uses claude wrapper via --terok-timeout."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_cmd"),
                HeadlessRunRequest("proj_cmd", "test"),
            )
            bash_cmd = result.run_mock.call_args[0][0][-1]
            assert "init-ssh-and-repo.sh" in bash_cmd
            assert "start-claude.sh" not in bash_cmd
            assert "--terok-timeout" in bash_cmd
            assert "--output-format stream-json" in bash_cmd
            assert "-p" in bash_cmd
            assert "--dangerously-skip-permissions" not in bash_cmd
            assert '--add-dir "/"' not in bash_cmd
            assert "GIT_AUTHOR_NAME=Claude" not in bash_cmd

    def test_headless_with_config_file_subagents(self) -> None:
        """task_run_headless reads subagents from YAML config file."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            agent_config = base / "my-agent-config.yml"
            agent_config.write_text(
                "subagents:\n"
                "  - name: extra-agent\n"
                "    default: true\n"
                "    system_prompt: I am an extra agent\n",
                encoding="utf-8",
            )

            run_headless_request(
                base,
                write_runner_project(base, "proj_cfgfile"),
                HeadlessRunRequest("proj_cfgfile", "test", config_path=str(agent_config)),
            )

            agents_data = read_task_agents(state_dir, "proj_cfgfile")
            assert "extra-agent" in agents_data
            assert agents_data["extra-agent"]["prompt"] == "I am an extra agent"


class TestTaskFollowupHeadless:
    """Tests for task_followup_headless."""

    def _create_completed_task(self, base: Path, project_id: str) -> str:
        """Create a task via task_run_headless and return the task_id."""
        result = run_headless_request(
            base,
            write_runner_project(base, project_id),
            HeadlessRunRequest(project_id, "initial prompt"),
        )
        assert result.task_id is not None
        return result.task_id

    def test_followup_writes_new_prompt(self) -> None:
        """Follow-up replaces prompt.txt and archives old prompt to history."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            task_id = self._create_completed_task(base, "proj_fu")

            run_followup_request(
                base,
                "proj_fu",
                task_id,
                "fix the remaining tests",
                container_state=["exited", "running"],
            )

            agent_cfg, _meta_path = task_paths(state_dir, "proj_fu")
            assert (agent_cfg / "prompt.txt").read_text() == "fix the remaining tests"
            history = (agent_cfg / "prompt-history.txt").read_text()
            assert "initial prompt" in history
            assert "---" in history

    def test_followup_uses_podman_start(self) -> None:
        """Follow-up uses podman start, not podman run."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_start")
            result = run_followup_request(
                base, "proj_start", task_id, "continue", container_state=["exited", "running"]
            )
            cmd = result.run_mock.call_args[0][0]
            assert cmd[0] == "podman"
            assert cmd[1] == "start"

    def test_followup_rejects_non_run_mode(self) -> None:
        """Follow-up rejects tasks that aren't headless (mode != 'run')."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            config_file = write_runner_project(base, "proj_mode")

            from terok.lib.orchestration.tasks import task_new

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, config_file), clear=True
            ):
                with mock_git_config():
                    task_id = task_new("proj_mode")
                    _agent_cfg, meta_path = task_paths(state_dir, "proj_mode", task_id)
                    meta = yaml_load(meta_path.read_text())
                    meta["mode"] = "cli"
                    meta_path.write_text(yaml_dump(meta))

                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_mode", task_id, "test")
                    assert "not a headless task" in str(ctx.value)

    def test_followup_rejects_running_task(self) -> None:
        """Follow-up rejects tasks that are still running."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            config_file = write_runner_project(base, "proj_run")

            from terok.lib.orchestration.tasks import task_new

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, config_file), clear=True
            ):
                with mock_git_config():
                    task_id = task_new("proj_run")
                    _agent_cfg, meta_path = task_paths(state_dir, "proj_run", task_id)
                    meta = yaml_load(meta_path.read_text())
                    meta["mode"] = "run"
                    meta_path.write_text(yaml_dump(meta))

                    with (
                        unittest.mock.patch(
                            "terok.lib.orchestration.task_runners.get_container_state",
                            return_value="running",
                        ),
                        pytest.raises(SystemExit) as ctx,
                    ):
                        task_followup_headless("proj_run", task_id, "test")
                    assert "still running" in str(ctx.value)

    def test_followup_rejects_running_container(self) -> None:
        """Follow-up rejects when container is still running (stale metadata)."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_crun")

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, base / "config.yml"), clear=True
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.orchestration.task_runners.get_container_state",
                        return_value="running",
                    ),
                ):
                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_crun", task_id, "test")
                    assert "still running" in str(ctx.value)

    def test_followup_updates_metadata(self) -> None:
        """Follow-up updates task status to running then completed."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            task_id = self._create_completed_task(base, "proj_meta2")

            run_followup_request(
                base, "proj_meta2", task_id, "continue", container_state=["exited", "running"]
            )

            meta = read_task_meta(state_dir, "proj_meta2", task_id)
            assert meta["exit_code"] == 0

    def test_followup_no_follow_mode(self) -> None:
        """Follow-up with follow=False prints detached info and skips wait."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"
            task_id = self._create_completed_task(base, "proj_meta2")

            result = run_followup_request(
                base,
                "proj_meta2",
                task_id,
                "continue",
                container_state=["exited", "running"],
                follow=False,
            )

            result.wait_mock.assert_not_called()
            assert "detached" in result.output.lower()

            meta = read_task_meta(state_dir, "proj_meta2", task_id)
            assert meta["exit_code"] is None

    def test_followup_container_not_found(self) -> None:
        """Follow-up raises SystemExit with 'not found' when container has been removed."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_notfound")

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, base / "config.yml"), clear=True
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.orchestration.task_runners.get_container_state",
                        return_value=None,
                    ),
                ):
                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_notfound", task_id, "test")
                    assert "not found" in str(ctx.value)

    def test_followup_start_fails(self) -> None:
        """Follow-up raises SystemExit when container remains exited after start."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_startfail")

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, base / "config.yml"), clear=True
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.orchestration.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.orchestration.task_runners.get_container_state",
                        side_effect=["exited", "exited"],
                    ),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_startfail", task_id, "test")
                    assert "failed to start" in str(ctx.value)
