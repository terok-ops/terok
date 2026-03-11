# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for autopilot (Level 1+2) features: terokctl run and agent config."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from terok.lib.core.projects import ProjectConfig

from terok.lib.containers.agents import (
    _generate_claude_wrapper,
    _subagents_to_json,
    _write_session_hook,
    parse_md_agent,
)
from terok.lib.containers.headless_providers import WrapperConfig
from terok.lib.containers.task_runners import (
    HeadlessRunRequest,
    task_followup_headless,
    task_run_headless,
)
from terok.lib.core.projects import load_project
from test_utils import mock_git_config, write_project


class AgentConfigProjectTests(unittest.TestCase):
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
                self.assertEqual(p.agent_config, {})

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
                self.assertIn("subagents", p.agent_config)
                self.assertEqual(p.agent_config["subagents"][0]["name"], "reviewer")

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
                self.assertTrue(Path(sa["file"]).is_absolute())
                self.assertIn("agents/reviewer.md", sa["file"])


class SubagentsToJsonTests(unittest.TestCase):
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
        self.assertIsInstance(result, dict)
        self.assertIn("reviewer", result)
        self.assertEqual(result["reviewer"]["prompt"], "You are a code reviewer.")
        self.assertEqual(result["reviewer"]["description"], "Code reviewer")
        self.assertEqual(result["reviewer"]["tools"], ["Read", "Grep"])
        self.assertEqual(result["reviewer"]["model"], "sonnet")
        # Non-Claude fields stripped
        self.assertNotIn("system_prompt", result["reviewer"])
        self.assertNotIn("name", result["reviewer"])
        self.assertNotIn("default", result["reviewer"])

    def test_default_false_excluded_without_selection(self) -> None:
        """Agents with default=False are excluded when not selected."""
        subagents = [
            {"name": "debugger", "default": False, "model": "sonnet", "system_prompt": "Debug."},
        ]
        result = json.loads(_subagents_to_json(subagents))
        self.assertEqual(result, {})

    def test_no_default_flag_excluded(self) -> None:
        """Agents without a default flag are excluded (default=False is the default)."""
        subagents = [
            {"name": "debugger", "model": "sonnet", "system_prompt": "Debug."},
        ]
        result = json.loads(_subagents_to_json(subagents))
        self.assertEqual(result, {})

    def test_selected_agents_included(self) -> None:
        """Non-default agents are included when passed in selected_agents."""
        subagents = [
            {"name": "debugger", "default": False, "model": "sonnet", "system_prompt": "Debug."},
        ]
        result = json.loads(_subagents_to_json(subagents, selected_agents=["debugger"]))
        self.assertIn("debugger", result)
        self.assertEqual(result["debugger"]["prompt"], "Debug.")

    def test_mixed_default_and_selected(self) -> None:
        """Default agents + selected non-default agents are both included."""
        subagents = [
            {"name": "reviewer", "default": True, "model": "sonnet", "system_prompt": "Review."},
            {"name": "debugger", "default": False, "model": "opus", "system_prompt": "Debug."},
            {"name": "planner", "default": False, "model": "haiku", "system_prompt": "Plan."},
        ]
        result = json.loads(_subagents_to_json(subagents, selected_agents=["debugger"]))
        self.assertIn("reviewer", result)
        self.assertIn("debugger", result)
        self.assertNotIn("planner", result)

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
            self.assertIn("reviewer", result)
            self.assertEqual(result["reviewer"]["prompt"], "You are a code reviewer.")

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
        self.assertEqual(result["advanced"]["mcpServers"], {"srv": {"command": "/bin/x"}})
        self.assertEqual(result["advanced"]["hooks"], {"onStart": "echo hi"})

    def test_missing_file_skipped(self) -> None:
        """Missing file references are skipped."""
        subagents = [{"file": "/nonexistent/agent.md", "default": True}]
        result = json.loads(_subagents_to_json(subagents))
        self.assertEqual(result, {})

    def test_agent_without_name_skipped(self) -> None:
        """Agents without a name are skipped."""
        subagents = [{"default": True, "model": "sonnet", "system_prompt": "No name."}]
        result = json.loads(_subagents_to_json(subagents))
        self.assertEqual(result, {})


class ParseMdAgentTests(unittest.TestCase):
    """Tests for parse_md_agent."""

    def test_parse_with_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            md = Path(td) / "test.md"
            md.write_text(
                "---\nname: test\ntools: [Read]\n---\nPrompt body.",
                encoding="utf-8",
            )
            result = parse_md_agent(str(md))
            self.assertEqual(result["name"], "test")
            self.assertEqual(result["prompt"], "Prompt body.")

    def test_parse_without_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            md = Path(td) / "test.md"
            md.write_text("Just a prompt.", encoding="utf-8")
            result = parse_md_agent(str(md))
            self.assertEqual(result["prompt"], "Just a prompt.")

    def test_nonexistent_file(self) -> None:
        result = parse_md_agent("/nonexistent/file.md")
        self.assertEqual(result, {})


class GenerateClaudeWrapperTests(unittest.TestCase):
    """Tests for _generate_claude_wrapper."""

    def _make_project(self) -> ProjectConfig:
        from terok.lib.core.projects import ProjectConfig

        return ProjectConfig(
            id="testproj",
            security_class="online",
            upstream_url=None,
            default_branch="main",
            root=Path("/tmp/testproj"),
            tasks_root=Path("/tmp/testproj/tasks"),
            gate_path=Path("/tmp/testproj/gate"),
            staging_root=None,
            ssh_key_name=None,
            ssh_host_dir=None,
            human_name="Test User",
            human_email="test@example.com",
        )

    def test_basic_wrapper(self) -> None:
        """Wrapper includes skip-permissions, add-dir /, and git env vars."""
        project = self._make_project()
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=False, project=project))
        self.assertIn("claude()", wrapper)
        self.assertIn("--dangerously-skip-permissions", wrapper)
        self.assertIn('--add-dir "/"', wrapper)
        self.assertIn("_terok_apply_git_identity Claude noreply@anthropic.com", wrapper)
        # Should NOT contain agents reference when has_agents=False
        self.assertNotIn("agents.json", wrapper)

    def test_wrapper_with_agents(self) -> None:
        """Wrapper includes agents.json reference when has_agents=True."""
        project = self._make_project()
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=True, project=project))
        self.assertIn("agents.json", wrapper)

    def test_wrapper_uses_terok_unrestricted_env(self) -> None:
        """Wrapper conditionally injects --dangerously-skip-permissions via TEROK_UNRESTRICTED."""
        project = self._make_project()
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=False, project=project))
        # The flag is gated by the env var check, not unconditionally injected
        self.assertIn('if [ "${TEROK_UNRESTRICTED:-}" = "1" ]; then', wrapper)
        self.assertIn("_args+=(--dangerously-skip-permissions)", wrapper)
        # --add-dir / is always present regardless of permission mode
        self.assertIn('--add-dir "/"', wrapper)

    def test_wrapper_no_model_or_mcp(self) -> None:
        """Wrapper does not contain --model, --mcp-config by default."""
        project = self._make_project()
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=True, project=project))
        self.assertNotIn("--model", wrapper)
        self.assertNotIn("--mcp-config", wrapper)
        self.assertNotIn("--max-turns", wrapper)
        # --append-system-prompt absent when has_instructions=False (default)
        self.assertNotIn("--append-system-prompt", wrapper)

    def test_wrapper_includes_append_system_prompt(self) -> None:
        """Wrapper includes --append-system-prompt when has_instructions=True."""
        project = self._make_project()
        wrapper = _generate_claude_wrapper(
            WrapperConfig(has_agents=False, project=project, has_instructions=True)
        )
        self.assertIn("--append-system-prompt", wrapper)
        self.assertIn("instructions.md", wrapper)

    def test_wrapper_timeout_support(self) -> None:
        """Wrapper parses --terok-timeout and wraps claude with timeout."""
        project = self._make_project()
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=False, project=project))
        # Wrapper should contain timeout flag parsing
        self.assertIn("--terok-timeout", wrapper)
        self.assertIn("_timeout", wrapper)
        # Wrapper should use timeout command when _timeout is set
        self.assertIn('timeout "$_timeout" claude', wrapper)
        # Wrapper should still have the non-timeout path
        self.assertIn('command claude "${_args[@]}" "$@"', wrapper)
        # Both paths should apply git identity through the shared helper
        self.assertEqual(wrapper.count("_terok_apply_git_identity Claude noreply@anthropic.com"), 2)

    def test_wrapper_resume_from_session_file(self) -> None:
        """Wrapper adds --resume from claude-session.txt when it exists."""
        project = self._make_project()
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=False, project=project))
        self.assertIn("claude-session.txt", wrapper)
        self.assertIn("--resume", wrapper)

    def test_wrapper_sets_memory_override_with_project_id(self) -> None:
        """Wrapper exports CLAUDE_COWORK_MEMORY_PATH_OVERRIDE using $PROJECT_ID."""
        project = self._make_project()
        wrapper = _generate_claude_wrapper(WrapperConfig(has_agents=False, project=project))
        self.assertIn(
            "export CLAUDE_COWORK_MEMORY_PATH_OVERRIDE="
            '"/home/dev/.claude/projects/${PROJECT_ID}-workspace/memory"',
            wrapper,
        )


class WriteSessionHookTests(unittest.TestCase):
    """Tests for _write_session_hook."""

    def test_creates_settings_with_hook(self) -> None:
        """Creates settings.json with a SessionStart hook."""
        with tempfile.TemporaryDirectory() as td:
            settings_path = Path(td) / "settings.json"
            _write_session_hook(settings_path)
            self.assertTrue(settings_path.is_file())
            data = json.loads(settings_path.read_text())
            self.assertIn("hooks", data)
            self.assertIn("SessionStart", data["hooks"])
            hooks = data["hooks"]["SessionStart"]
            self.assertEqual(len(hooks), 1)
            command = hooks[0]["hooks"][0]["command"]
            self.assertIn("session_id", command)
            self.assertIn("claude-session.txt", command)

    def test_merges_with_existing_settings(self) -> None:
        """Merges hook into existing settings.json without clobbering."""
        with tempfile.TemporaryDirectory() as td:
            settings_path = Path(td) / "settings.json"
            settings_path.write_text('{"permissions": {"allow": ["Read"]}}', encoding="utf-8")
            _write_session_hook(settings_path)
            data = json.loads(settings_path.read_text())
            # Original settings preserved
            self.assertEqual(data["permissions"], {"allow": ["Read"]})
            # Hook added
            self.assertIn("SessionStart", data["hooks"])

    def test_idempotent_hook_write(self) -> None:
        """Calling _write_session_hook twice doesn't create duplicate hooks."""
        with tempfile.TemporaryDirectory() as td:
            settings_path = Path(td) / "settings.json"
            _write_session_hook(settings_path)
            _write_session_hook(settings_path)
            data = json.loads(settings_path.read_text())
            hooks = data["hooks"]["SessionStart"]
            self.assertEqual(len(hooks), 1)

    def test_does_not_rewrite_when_hook_already_present(self) -> None:
        """If equivalent hook exists, keep existing file content unchanged."""
        with tempfile.TemporaryDirectory() as td:
            settings_path = Path(td) / "settings.json"
            original = (
                '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"python3 -c \\"import json,sys; '
                "print(json.load(sys.stdin)['session_id'])\\\" > /home/dev/.terok/claude-session.txt\"}]}]}}"
            )
            settings_path.write_text(original, encoding="utf-8")

            _write_session_hook(settings_path)

            self.assertEqual(settings_path.read_text(encoding="utf-8"), original)

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
            self.assertEqual(data["permissions"], {"allow": ["Read"]})
            self.assertIn("SessionStart", data["hooks"])

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
            self.assertEqual(len(hooks), 1)


class PrepareAgentConfigDirTests(unittest.TestCase):
    """Tests for prepare_agent_config_dir."""

    def _make_project(self) -> ProjectConfig:
        from terok.lib.core.projects import ProjectConfig

        return ProjectConfig(
            id="test-proj",
            security_class="online",
            upstream_url=None,
            default_branch="main",
            root=Path("/tmp/test"),
            tasks_root=Path(tempfile.mkdtemp()),
            gate_path=Path("/tmp/test/gate"),
            staging_root=None,
            ssh_key_name=None,
            ssh_host_dir=None,
            default_agent=None,
            human_name="Test User",
            human_email="test@example.com",
        )

    @unittest.mock.patch("terok.lib.containers.agents._write_session_hook")
    def test_prepare_agent_config_writes_instructions(self, _mock_hook: object) -> None:
        """Instructions text is written to instructions.md in agent-config dir."""
        from terok.lib.containers.agents import AgentConfigSpec, prepare_agent_config_dir

        project = self._make_project()
        task_id = "test-task-1"
        (project.tasks_root / task_id).mkdir(parents=True, exist_ok=True)

        agent_config_dir = prepare_agent_config_dir(
            AgentConfigSpec(
                project, task_id, subagents=[], instructions="Custom instructions here."
            )
        )
        instr_path = agent_config_dir / "instructions.md"
        self.assertTrue(instr_path.is_file())
        self.assertEqual(instr_path.read_text(encoding="utf-8"), "Custom instructions here.")

    @unittest.mock.patch("terok.lib.containers.agents._write_session_hook")
    def test_prepare_agent_config_default_instructions_when_none(self, _mock_hook: object) -> None:
        """Default instructions.md written when instructions is None."""
        from terok.lib.containers.agents import AgentConfigSpec, prepare_agent_config_dir

        project = self._make_project()
        task_id = "test-task-2"
        (project.tasks_root / task_id).mkdir(parents=True, exist_ok=True)

        agent_config_dir = prepare_agent_config_dir(AgentConfigSpec(project, task_id, subagents=[]))
        instr_path = agent_config_dir / "instructions.md"
        self.assertTrue(instr_path.is_file())
        content = instr_path.read_text(encoding="utf-8")
        self.assertIn("conventions", content)

    @unittest.mock.patch("terok.lib.containers.agents._write_session_hook")
    def test_wrapper_has_append_system_prompt_when_instructions(self, _mock_hook: object) -> None:
        """Claude wrapper includes --append-system-prompt when instructions are provided."""
        from terok.lib.containers.agents import AgentConfigSpec, prepare_agent_config_dir

        project = self._make_project()
        task_id = "test-task-3"
        (project.tasks_root / task_id).mkdir(parents=True, exist_ok=True)

        agent_config_dir = prepare_agent_config_dir(
            AgentConfigSpec(project, task_id, subagents=[], instructions="Test instructions.")
        )
        wrapper = (agent_config_dir / "terok-agent.sh").read_text(encoding="utf-8")
        self.assertIn("--append-system-prompt", wrapper)
        self.assertIn("instructions.md", wrapper)


class TaskRunHeadlessTests(unittest.TestCase):
    """Tests for task_run_headless."""

    def _make_project(self, base: Path, project_id: str, extra_yml: str = "") -> Path:
        config_root = base / "config"
        envs_dir = base / "envs"
        config_root.mkdir(parents=True, exist_ok=True)
        config_file = base / "config.yml"
        config_file.write_text(f"envs:\n  base_dir: {envs_dir}\n", encoding="utf-8")
        write_project(
            config_root,
            project_id,
            f"project:\n  id: {project_id}\n{extra_yml}",
        )
        return config_file

    def test_headless_creates_task_and_writes_prompt(self) -> None:
        """task_run_headless creates a task with prompt.txt in agent-config dir."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_hl")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_id = task_run_headless(
                            HeadlessRunRequest("proj_hl", "Fix the auth bug")
                        )

                    self.assertEqual(task_id, "1")

                    # Verify prompt file was written
                    agent_config_dir = state_dir / "tasks" / "proj_hl" / "1" / "agent-config"
                    self.assertTrue(agent_config_dir.is_dir())
                    prompt_file = agent_config_dir / "prompt.txt"
                    self.assertTrue(prompt_file.is_file())
                    self.assertEqual(prompt_file.read_text(), "Fix the auth bug")

    def test_headless_mounts_agent_config_dir(self) -> None:
        """task_run_headless mounts agent-config dir to /home/dev/.terok."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_mount")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_run_headless(HeadlessRunRequest("proj_mount", "test prompt"))

                    # Check the podman run command has the agent-config mount
                    cmd = run_mock.call_args[0][0]
                    cmd_str = " ".join(cmd)
                    self.assertIn("/home/dev/.terok:Z", cmd_str)

    def test_headless_generates_agent_wrapper(self) -> None:
        """task_run_headless generates terok-agent.sh in agent-config dir."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_wrap")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_run_headless(HeadlessRunRequest("proj_wrap", "test"))

                    # Verify wrapper was written
                    wrapper = (
                        state_dir / "tasks" / "proj_wrap" / "1" / "agent-config" / "terok-agent.sh"
                    )
                    self.assertTrue(wrapper.is_file())
                    content = wrapper.read_text()
                    self.assertIn("claude()", content)
                    self.assertIn("--dangerously-skip-permissions", content)

    def test_headless_writes_session_hook_settings(self) -> None:
        """task_run_headless writes shared Claude settings with SessionStart hook."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_hook")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_run_headless(HeadlessRunRequest("proj_hook", "test"))

                    settings = base / "envs" / "_claude-config" / "settings.json"
                    self.assertTrue(settings.is_file())
                    data = json.loads(settings.read_text())
                    self.assertIn("SessionStart", data["hooks"])

    def test_headless_with_default_subagents(self) -> None:
        """task_run_headless includes default subagents in agents.json."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(
                base,
                "proj_agents",
                (
                    "agent:\n"
                    "  subagents:\n"
                    "    - name: reviewer\n"
                    "      default: true\n"
                    "      system_prompt: Review code\n"
                    "    - name: debugger\n"
                    "      default: false\n"
                    "      system_prompt: Debug code\n"
                ),
            )
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_run_headless(HeadlessRunRequest("proj_agents", "test"))

                    agents_file = (
                        state_dir / "tasks" / "proj_agents" / "1" / "agent-config" / "agents.json"
                    )
                    self.assertTrue(agents_file.is_file())
                    agents_data = json.loads(agents_file.read_text())
                    # Only default agents should be included
                    self.assertIn("reviewer", agents_data)
                    self.assertNotIn("debugger", agents_data)
                    # Verify dict-keyed-by-name format
                    self.assertIsInstance(agents_data, dict)
                    self.assertEqual(agents_data["reviewer"]["prompt"], "Review code")

    def test_headless_with_agent_selection(self) -> None:
        """task_run_headless includes selected non-default agents in agents.json."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(
                base,
                "proj_sel",
                (
                    "agent:\n"
                    "  subagents:\n"
                    "    - name: reviewer\n"
                    "      default: true\n"
                    "      system_prompt: Review code\n"
                    "    - name: debugger\n"
                    "      default: false\n"
                    "      system_prompt: Debug code\n"
                ),
            )
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_run_headless(
                            HeadlessRunRequest("proj_sel", "test", agents=["debugger"])
                        )

                    agents_file = (
                        state_dir / "tasks" / "proj_sel" / "1" / "agent-config" / "agents.json"
                    )
                    self.assertTrue(agents_file.is_file())
                    agents_data = json.loads(agents_file.read_text())
                    # Both default and selected should be included
                    self.assertIn("reviewer", agents_data)
                    self.assertIn("debugger", agents_data)

    def test_headless_cli_model_max_turns_in_command(self) -> None:
        """CLI model/max_turns appear in headless bash command, not in wrapper."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_flags")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_run_headless(
                            HeadlessRunRequest(
                                "proj_flags",
                                "test",
                                model="opus",
                                max_turns=100,
                            )
                        )

                    # Model/max_turns should be in the bash command
                    cmd = run_mock.call_args[0][0]
                    bash_cmd = cmd[-1]
                    self.assertIn("--model opus", bash_cmd)
                    self.assertIn("--max-turns 100", bash_cmd)
                    # Timeout is delegated to the wrapper via --terok-timeout
                    self.assertIn("--terok-timeout", bash_cmd)

                    # But per-run flags are NOT in the wrapper
                    wrapper = (
                        state_dir / "tasks" / "proj_flags" / "1" / "agent-config" / "terok-agent.sh"
                    )
                    content = wrapper.read_text()
                    self.assertNotIn("--model", content)
                    self.assertNotIn("--max-turns", content)
                    # Wrapper DOES have timeout support
                    self.assertIn("--terok-timeout", content)

    def test_headless_container_name_uses_run_prefix(self) -> None:
        """task_run_headless names the container <project>-run-<task_id>."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_name")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_run_headless(HeadlessRunRequest("proj_name", "test"))

                    cmd = run_mock.call_args[0][0]
                    name_idx = cmd.index("--name")
                    self.assertEqual(cmd[name_idx + 1], "proj_name-run-1")

    def test_headless_metadata_updated(self) -> None:
        """task_run_headless sets mode=run and updates status on completion."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_meta")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_run_headless(HeadlessRunRequest("proj_meta", "test"))

                    meta_path = state_dir / "projects" / "proj_meta" / "tasks" / "1.yml"
                    meta = yaml.safe_load(meta_path.read_text())
                    self.assertEqual(meta["mode"], "run")
                    self.assertEqual(meta["exit_code"], 0)

    def test_headless_no_follow_mode(self) -> None:
        """task_run_headless with follow=False prints detach info."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_nf")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit"
                    ) as stream_mock,
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_run_headless(HeadlessRunRequest("proj_nf", "test", follow=False))

                    # Stream should NOT be called in no-follow mode
                    stream_mock.assert_not_called()

                    output = buffer.getvalue()
                    self.assertIn("detached", output.lower())
                    self.assertIn("proj_nf-run-1", output)

    def test_headless_uses_claude_function_in_command(self) -> None:
        """task_run_headless uses claude wrapper via --terok-timeout."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_cmd")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_run_headless(HeadlessRunRequest("proj_cmd", "test"))

                    cmd = run_mock.call_args[0][0]
                    bash_cmd = cmd[-1]
                    self.assertIn("init-ssh-and-repo.sh", bash_cmd)
                    self.assertNotIn("start-claude.sh", bash_cmd)
                    self.assertIn("--terok-timeout", bash_cmd)
                    self.assertIn("--output-format stream-json", bash_cmd)
                    self.assertIn("-p", bash_cmd)
                    # Flags are now in the wrapper, not duplicated in the command
                    self.assertNotIn("--dangerously-skip-permissions", bash_cmd)
                    self.assertNotIn('--add-dir "/"', bash_cmd)
                    self.assertNotIn("GIT_AUTHOR_NAME=Claude", bash_cmd)

    def test_headless_with_config_file_subagents(self) -> None:
        """task_run_headless reads subagents from YAML config file."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_cfgfile")
            state_dir = base / "state"

            # Create a YAML agent config file with subagents
            agent_config = base / "my-agent-config.yml"
            agent_config.write_text(
                "subagents:\n"
                "  - name: extra-agent\n"
                "    default: true\n"
                "    system_prompt: I am an extra agent\n",
                encoding="utf-8",
            )

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_run_headless(
                            HeadlessRunRequest(
                                "proj_cfgfile",
                                "test",
                                config_path=str(agent_config),
                            )
                        )

                    # Verify agents.json contains the config file agent
                    agents_file = (
                        state_dir / "tasks" / "proj_cfgfile" / "1" / "agent-config" / "agents.json"
                    )
                    self.assertTrue(agents_file.is_file())
                    agents_data = json.loads(agents_file.read_text())
                    self.assertIn("extra-agent", agents_data)
                    self.assertEqual(agents_data["extra-agent"]["prompt"], "I am an extra agent")


class TaskFollowupHeadlessTests(unittest.TestCase):
    """Tests for task_followup_headless."""

    def _make_project(self, base: Path, project_id: str) -> Path:
        config_root = base / "config"
        envs_dir = base / "envs"
        config_root.mkdir(parents=True, exist_ok=True)
        config_file = base / "config.yml"
        config_file.write_text(f"envs:\n  base_dir: {envs_dir}\n", encoding="utf-8")
        write_project(config_root, project_id, f"project:\n  id: {project_id}\n")
        return config_file

    def _create_completed_task(self, base: Path, project_id: str) -> str:
        """Create a task via task_run_headless and return the task_id."""
        config_file = self._make_project(base, project_id)
        state_dir = base / "state"

        with unittest.mock.patch.dict(
            os.environ,
            {
                "TEROK_CONFIG_DIR": str(base / "config"),
                "TEROK_STATE_DIR": str(state_dir),
                "TEROK_CONFIG_FILE": str(config_file),
            },
            clear=True,
        ):
            with (
                mock_git_config(),
                unittest.mock.patch("terok.lib.containers.task_runners.subprocess.run") as run_mock,
                unittest.mock.patch(
                    "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                ),
                unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
            ):
                run_mock.return_value = subprocess.CompletedProcess([], 0)
                buffer = StringIO()
                with redirect_stdout(buffer):
                    task_id = task_run_headless(HeadlessRunRequest(project_id, "initial prompt"))
        return task_id

    def test_followup_writes_new_prompt(self) -> None:
        """Follow-up replaces prompt.txt and archives old prompt to history."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_fu")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(base / "config.yml"),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.get_container_state",
                        side_effect=["exited", "running"],
                    ),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_followup_headless("proj_fu", task_id, "fix the remaining tests")

                    agent_cfg = state_dir / "tasks" / "proj_fu" / "1" / "agent-config"
                    prompt_file = agent_cfg / "prompt.txt"
                    history_file = agent_cfg / "prompt-history.txt"
                    content = prompt_file.read_text()
                    # prompt.txt contains ONLY the new follow-up prompt
                    self.assertEqual(content, "fix the remaining tests")
                    # Original prompt is archived in the history file
                    history = history_file.read_text()
                    self.assertIn("initial prompt", history)
                    self.assertIn("---", history)

    def test_followup_uses_podman_start(self) -> None:
        """Follow-up uses podman start, not podman run."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_start")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(base / "config.yml"),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.get_container_state",
                        side_effect=["exited", "running"],
                    ),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_followup_headless("proj_start", task_id, "continue")

                    cmd = run_mock.call_args[0][0]
                    self.assertEqual(cmd[0], "podman")
                    self.assertEqual(cmd[1], "start")

    def test_followup_rejects_non_run_mode(self) -> None:
        """Follow-up rejects tasks that aren't headless (mode != 'run')."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_mode")
            state_dir = base / "state"

            # Create a task manually with mode=cli
            from terok.lib.containers.tasks import task_new

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with mock_git_config():
                    task_id = task_new("proj_mode")
                    meta_path = state_dir / "projects" / "proj_mode" / "tasks" / f"{task_id}.yml"
                    meta = yaml.safe_load(meta_path.read_text())
                    meta["mode"] = "cli"
                    meta_path.write_text(yaml.safe_dump(meta))

                    with self.assertRaises(SystemExit) as ctx:
                        task_followup_headless("proj_mode", task_id, "test")
                    self.assertIn("not a headless task", str(ctx.exception))

    def test_followup_rejects_running_task(self) -> None:
        """Follow-up rejects tasks that are still running."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = self._make_project(base, "proj_run")
            state_dir = base / "state"

            from terok.lib.containers.tasks import task_new

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(config_file),
                },
                clear=True,
            ):
                with mock_git_config():
                    task_id = task_new("proj_run")
                    meta_path = state_dir / "projects" / "proj_run" / "tasks" / f"{task_id}.yml"
                    meta = yaml.safe_load(meta_path.read_text())
                    meta["mode"] = "run"
                    meta_path.write_text(yaml.safe_dump(meta))

                    # Container is running → follow-up should be rejected
                    with (
                        unittest.mock.patch(
                            "terok.lib.containers.task_runners.get_container_state",
                            return_value="running",
                        ),
                        self.assertRaises(SystemExit) as ctx,
                    ):
                        task_followup_headless("proj_run", task_id, "test")
                    self.assertIn("still running", str(ctx.exception))

    def test_followup_rejects_running_container(self) -> None:
        """Follow-up rejects when container is still running (stale metadata)."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_crun")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(base / "config.yml"),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.get_container_state",
                        return_value="running",
                    ),
                ):
                    with self.assertRaises(SystemExit) as ctx:
                        task_followup_headless("proj_crun", task_id, "test")
                    self.assertIn("still running", str(ctx.exception))

    def test_followup_updates_metadata(self) -> None:
        """Follow-up updates task status to running then completed."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_meta2")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(base / "config.yml"),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.get_container_state",
                        side_effect=["exited", "running"],
                    ),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit", return_value=0
                    ),
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_followup_headless("proj_meta2", task_id, "continue")

                    meta_path = state_dir / "projects" / "proj_meta2" / "tasks" / f"{task_id}.yml"
                    meta = yaml.safe_load(meta_path.read_text())
                    self.assertEqual(meta["exit_code"], 0)

    def test_followup_no_follow_mode(self) -> None:
        """Follow-up with follow=False prints detached info and skips wait."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_meta2")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(base / "config.yml"),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.get_container_state",
                        side_effect=["exited", "running"],
                    ),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.wait_for_exit"
                    ) as wait_mock,
                    unittest.mock.patch("terok.lib.containers.task_runners._print_run_summary"),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    buffer = StringIO()
                    with redirect_stdout(buffer):
                        task_followup_headless("proj_meta2", task_id, "continue", follow=False)

                    # wait_for_exit should NOT be called in no-follow mode
                    wait_mock.assert_not_called()

                    output = buffer.getvalue()
                    self.assertIn("detached", output.lower())

                    meta_path = state_dir / "projects" / "proj_meta2" / "tasks" / f"{task_id}.yml"
                    meta = yaml.safe_load(meta_path.read_text())
                    # exit_code should be cleared for the new run
                    self.assertIsNone(meta["exit_code"])

    def test_followup_container_not_found(self) -> None:
        """Follow-up raises SystemExit with 'not found' when container has been removed."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_notfound")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(base / "config.yml"),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.get_container_state",
                        return_value=None,
                    ),
                ):
                    with self.assertRaises(SystemExit) as ctx:
                        task_followup_headless("proj_notfound", task_id, "test")
                    self.assertIn("not found", str(ctx.exception))

    def test_followup_start_fails(self) -> None:
        """Follow-up raises SystemExit when container remains exited after start."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_startfail")
            state_dir = base / "state"

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(base / "config"),
                    "TEROK_STATE_DIR": str(state_dir),
                    "TEROK_CONFIG_FILE": str(base / "config.yml"),
                },
                clear=True,
            ):
                with (
                    mock_git_config(),
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.subprocess.run"
                    ) as run_mock,
                    unittest.mock.patch(
                        "terok.lib.containers.task_runners.get_container_state",
                        # first call: pre-start check (exited); second call: post-start check (still exited)
                        side_effect=["exited", "exited"],
                    ),
                ):
                    run_mock.return_value = subprocess.CompletedProcess([], 0)
                    with self.assertRaises(SystemExit) as ctx:
                        task_followup_headless("proj_startfail", task_id, "test")
                    self.assertIn("failed to start", str(ctx.exception))
