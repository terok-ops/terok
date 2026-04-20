# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for autopilot (Level 1+2) features: terok task run and agent config."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest.mock
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from terok.lib.util.yaml import dump as yaml_dump, load as yaml_load

if TYPE_CHECKING:
    from terok_sandbox import RunSpec

    from terok.lib.core.projects import ProjectConfig


from terok.lib.core.projects import load_project
from terok.lib.orchestration.task_runners import (
    HeadlessRunRequest,
    task_followup_headless,
    task_run_headless,
)
from tests.test_utils import assert_task_id, captured_runspec, mock_git_config, write_project


@dataclass
class TaskRunnerResult:
    """Captured result from a mocked headless task/follow-up invocation."""

    output: str
    run_mock: unittest.mock.Mock
    wait_mock: unittest.mock.Mock
    task_id: str | None = None

    @property
    def last_spec(self) -> RunSpec:
        """Return the ``RunSpec`` reconstructed from the most recent
        ``AgentRunner.launch_prepared(...)`` call captured by the mocked
        ``_agent_runner`` factory, rebuilt via
        :func:`tests.test_utils.captured_runspec`."""
        return captured_runspec(self.run_mock)


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
        "TEROK_EXECUTOR_STATE_DIR": str(base / "agent"),
        "TEROK_CONFIG_FILE": str(config_file),
        "TEROK_SANDBOX_LIVE_DIR": str(base / "sandbox-live"),
        "TEROK_SANDBOX_STATE_DIR": str(base / "sandbox-state"),
    }


def task_paths(base: Path, project_id: str, task_id: str = "1") -> tuple[Path, Path]:
    """Return ``(agent_config_dir, meta_path)`` for a task.

    agent_config_dir lives under sandbox-live (container-writable),
    meta_path lives under state (terok-core metadata).
    """
    sandbox_live = base / "sandbox-live"
    state = base / "state"
    return (
        sandbox_live / "tasks" / project_id / task_id / "agent-config",
        state / "projects" / project_id / "tasks" / f"{task_id}.yml",
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
    config_base = base / "config"
    projects_root = config_base / "projects"
    envs_dir = base / "envs"
    projects_root.mkdir(parents=True, exist_ok=True)
    config_file = base / "config.yml"
    config_file.write_text(f"credentials:\n  dir: {envs_dir}\n", encoding="utf-8")
    write_project(projects_root, project_id, f"project:\n  id: {project_id}\n{extra_yml}")
    return config_file


def read_task_agents(base: Path, project_id: str, task_id: str = "1") -> dict[str, object]:
    """Load ``agents.json`` for a task."""
    return json.loads((task_paths(base, project_id, task_id)[0] / "agents.json").read_text())


def read_task_meta(base: Path, project_id: str, task_id: str = "1") -> dict[str, object]:
    """Load task metadata YAML for a task."""
    return yaml_load(task_paths(base, project_id, task_id)[1].read_text())


def prepare_agent_config(
    project: ProjectConfig,
    task_id: str,
    *,
    instructions: str | None = None,
) -> Path:
    """Build an agent-config directory for the given task."""
    from terok_executor import AgentConfigSpec, prepare_agent_config_dir

    (project.tasks_root / task_id).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        return prepare_agent_config_dir(
            AgentConfigSpec(
                project.tasks_root,
                task_id,
                subagents=[],
                instructions=instructions,
                default_agent=project.default_agent,
                mounts_base=Path(td),
            )
        )


def run_headless_request(
    base: Path,
    config_file: Path,
    request: HeadlessRunRequest,
) -> TaskRunnerResult:
    """Run ``task_run_headless`` with the standard patched test harness.

    Relies on the ``mock_runtime`` autouse fixture to intercept container
    operations; container.wait() already returns 0 by default.
    """
    from terok.lib.core.runtime import get_runtime

    wait_mock = get_runtime().container.return_value.wait
    with unittest.mock.patch.dict(os.environ, runner_env_vars(base, config_file), clear=True):
        with (
            mock_git_config(),
            unittest.mock.patch(
                "terok.lib.orchestration.task_runners._agent_runner"
            ) as sandbox_factory,
            unittest.mock.patch("terok.lib.orchestration.task_runners._print_run_summary"),
        ):
            buffer = StringIO()
            with redirect_stdout(buffer):
                task_id = task_run_headless(request)
    return TaskRunnerResult(
        task_id=task_id,
        output=buffer.getvalue(),
        run_mock=sandbox_factory,
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
    from terok.lib.core.runtime import get_runtime

    runtime = get_runtime()
    container = runtime.container.return_value
    # ``start()`` is fire-and-forget now (raises on failure); default mock returns None.
    start_mock = container.start
    wait_mock = container.wait  # already returns 0 via fixture default

    state_patch: contextlib.AbstractContextManager
    if isinstance(container_state, list):
        state_patch = unittest.mock.patch.object(
            type(container),
            "state",
            new_callable=unittest.mock.PropertyMock,
            side_effect=iter(container_state).__next__,
            create=True,
        )
    else:
        container.state = container_state
        state_patch = contextlib.nullcontext()

    with (
        state_patch,
        unittest.mock.patch.dict(
            os.environ, runner_env_vars(base, base / "config.yml"), clear=True
        ),
        mock_git_config(),
        unittest.mock.patch("terok.lib.orchestration.task_runners._print_run_summary"),
    ):
        buffer = StringIO()
        with redirect_stdout(buffer):
            task_followup_headless(project_id, task_id, prompt, follow=follow)
    return TaskRunnerResult(output=buffer.getvalue(), run_mock=start_mock, wait_mock=wait_mock)


def _spec_volumes(result: TaskRunnerResult) -> tuple:
    """Extract VolumeSpec tuple from the RunSpec captured by the sandbox mock."""
    return result.last_spec.volumes


def _spec_command(result: TaskRunnerResult) -> tuple[str, ...]:
    """Extract command from the RunSpec captured by the sandbox mock."""
    return result.last_spec.command


def _spec_container_name(result: TaskRunnerResult) -> str:
    """Extract container_name from the RunSpec captured by the sandbox mock."""
    return result.last_spec.container_name


class TestAgentConfigProject:
    """Tests for agent config parsing in projects.py."""

    def test_agent_config_empty_when_absent(self) -> None:
        """Project has agent_config={} when not configured."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_root = base / "config"
            projects_root = config_root / "projects"
            write_project(projects_root, "proj_noagent", "project:\n  id: proj_noagent\n")

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
            projects_root = config_root / "projects"

            write_project(
                projects_root,
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
            projects_root = config_root / "projects"

            write_project(
                projects_root,
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


class TestTaskRunHeadless:
    """Tests for task_run_headless."""

    def test_headless_creates_task_and_writes_prompt(self) -> None:
        """task_run_headless creates a task with prompt.txt in agent-config dir."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_hl"),
                HeadlessRunRequest("proj_hl", "Fix the auth bug"),
            )

            assert_task_id(result.task_id)
            agent_config_dir, _meta_path = task_paths(base, "proj_hl", result.task_id)
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
            assert any(
                v.container_path == "/home/dev/.terok" and v.sharing == "private"
                for v in result.last_spec.volumes
            )

    def test_headless_generates_agent_wrapper(self) -> None:
        """task_run_headless generates terok-executor.sh in agent-config dir."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            result = run_headless_request(
                base,
                write_runner_project(base, "proj_wrap"),
                HeadlessRunRequest("proj_wrap", "test"),
            )

            wrapper = task_paths(base, "proj_wrap", result.task_id)[0] / "terok-executor.sh"
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

            settings = base / "sandbox-live" / "mounts" / "_claude-config" / "settings.json"
            assert settings.is_file()
            assert "SessionStart" in json.loads(settings.read_text())["hooks"]

    def test_headless_with_default_subagents(self) -> None:
        """task_run_headless includes default subagents in agents.json."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            result = run_headless_request(
                base,
                write_runner_project(base, "proj_agents", DEFAULT_SUBAGENTS_YAML),
                HeadlessRunRequest("proj_agents", "test"),
            )

            agents_data = read_task_agents(base, "proj_agents", result.task_id)
            assert isinstance(agents_data, dict)
            assert "reviewer" in agents_data
            assert "debugger" not in agents_data
            assert agents_data["reviewer"]["prompt"] == "Review code"

    def test_headless_with_agent_selection(self) -> None:
        """task_run_headless includes selected non-default agents in agents.json."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            result = run_headless_request(
                base,
                write_runner_project(base, "proj_sel", DEFAULT_SUBAGENTS_YAML),
                HeadlessRunRequest("proj_sel", "test", agents=["debugger"]),
            )

            agents_data = read_task_agents(base, "proj_sel", result.task_id)
            assert "reviewer" in agents_data
            assert "debugger" in agents_data

    def test_headless_cli_model_max_turns_in_command(self) -> None:
        """CLI model/max_turns appear in headless bash command, not in wrapper."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            result = run_headless_request(
                base,
                write_runner_project(base, "proj_flags"),
                HeadlessRunRequest("proj_flags", "test", model="opus", max_turns=100),
            )

            bash_cmd = result.last_spec.command[-1]
            assert "--model opus" in bash_cmd
            assert "--max-turns 100" in bash_cmd
            assert "--terok-timeout" in bash_cmd

            wrapper = task_paths(base, "proj_flags", result.task_id)[0] / "terok-executor.sh"
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
            assert_task_id(result.task_id)
            assert result.last_spec.container_name == f"proj_name-run-{result.task_id}"

    def test_headless_metadata_updated(self) -> None:
        """task_run_headless sets mode=run and updates status on completion."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            result = run_headless_request(
                base,
                write_runner_project(base, "proj_meta"),
                HeadlessRunRequest("proj_meta", "test"),
            )

            meta = read_task_meta(base, "proj_meta", result.task_id)
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
            assert_task_id(result.task_id)
            assert f"proj_nf-run-{result.task_id}" in result.output

    def test_headless_uses_claude_function_in_command(self) -> None:
        """task_run_headless uses claude wrapper via --terok-timeout."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_cmd"),
                HeadlessRunRequest("proj_cmd", "test"),
            )
            bash_cmd = result.last_spec.command[-1]
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

            agent_config = base / "my-agent-config.yml"
            agent_config.write_text(
                "subagents:\n"
                "  - name: extra-agent\n"
                "    default: true\n"
                "    system_prompt: I am an extra agent\n",
                encoding="utf-8",
            )

            result = run_headless_request(
                base,
                write_runner_project(base, "proj_cfgfile"),
                HeadlessRunRequest("proj_cfgfile", "test", config_path=str(agent_config)),
            )

            agents_data = read_task_agents(base, "proj_cfgfile", result.task_id)
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

            task_id = self._create_completed_task(base, "proj_fu")

            run_followup_request(
                base,
                "proj_fu",
                task_id,
                "fix the remaining tests",
                container_state=["exited", "running"],
            )

            agent_cfg, _meta_path = task_paths(base, "proj_fu", task_id)
            assert (agent_cfg / "prompt.txt").read_text() == "fix the remaining tests"
            history = (agent_cfg / "prompt-history.txt").read_text()
            assert "initial prompt" in history
            assert "---" in history

    def test_followup_uses_container_start(self, mock_runtime) -> None:
        """Follow-up starts the task's container via the runtime."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_start")
            result = run_followup_request(
                base, "proj_start", task_id, "continue", container_state=["exited", "running"]
            )
            result.run_mock.assert_called_once()
            mock_runtime.container.assert_any_call(f"proj_start-run-{task_id}")

    def test_followup_rejects_non_run_mode(self) -> None:
        """Follow-up rejects tasks that aren't headless (mode != 'run')."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            config_file = write_runner_project(base, "proj_mode")

            from terok.lib.orchestration.tasks import task_new

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, config_file), clear=True
            ):
                with mock_git_config():
                    task_id = task_new("proj_mode")
                    _agent_cfg, meta_path = task_paths(base, "proj_mode", task_id)
                    meta = yaml_load(meta_path.read_text())
                    meta["mode"] = "cli"
                    meta_path.write_text(yaml_dump(meta))

                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_mode", task_id, "test")
                    assert "not a headless task" in str(ctx.value)

    def test_followup_rejects_running_task(self, mock_runtime) -> None:
        """Follow-up rejects tasks that are still running."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            config_file = write_runner_project(base, "proj_run")

            from terok.lib.orchestration.tasks import task_new

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, config_file), clear=True
            ):
                with mock_git_config():
                    task_id = task_new("proj_run")
                    _agent_cfg, meta_path = task_paths(base, "proj_run", task_id)
                    meta = yaml_load(meta_path.read_text())
                    meta["mode"] = "run"
                    meta_path.write_text(yaml_dump(meta))

                    mock_runtime.container.return_value.state = "running"
                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_run", task_id, "test")
                    assert "still running" in str(ctx.value)

    def test_followup_rejects_running_container(self, mock_runtime) -> None:
        """Follow-up rejects when container is still running (stale metadata)."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_crun")
            mock_runtime.container.return_value.state = "running"

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, base / "config.yml"), clear=True
            ):
                with mock_git_config():
                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_crun", task_id, "test")
                    assert "still running" in str(ctx.value)

    def test_followup_updates_metadata(self) -> None:
        """Follow-up updates task status to running then completed."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            task_id = self._create_completed_task(base, "proj_meta2")

            run_followup_request(
                base, "proj_meta2", task_id, "continue", container_state=["exited", "running"]
            )

            meta = read_task_meta(base, "proj_meta2", task_id)
            assert meta["exit_code"] == 0

    def test_followup_no_follow_mode(self, mock_runtime) -> None:
        """Follow-up with follow=False prints detached info and skips wait."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            task_id = self._create_completed_task(base, "proj_meta2")
            # Clear wait-call history from the setup phase so the assertion
            # below only sees calls made by the follow-up itself.
            mock_runtime.container.return_value.wait.reset_mock()

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

            meta = read_task_meta(base, "proj_meta2", task_id)
            assert meta["exit_code"] is None

    def test_followup_container_not_found(self, mock_runtime) -> None:
        """Follow-up raises SystemExit with 'not found' when container has been removed."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_notfound")
            mock_runtime.container.return_value.state = None

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, base / "config.yml"), clear=True
            ):
                with mock_git_config():
                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_notfound", task_id, "test")
                    assert "not found" in str(ctx.value)

    def test_followup_start_fails(self, mock_runtime) -> None:
        """Follow-up raises SystemExit when container remains exited after start."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_startfail")
            container_mock = mock_runtime.container.return_value

            with (
                unittest.mock.patch.object(
                    type(container_mock),
                    "state",
                    new_callable=unittest.mock.PropertyMock,
                    side_effect=iter(["exited", "exited"]).__next__,
                    create=True,
                ),
                unittest.mock.patch.dict(
                    os.environ, runner_env_vars(base, base / "config.yml"), clear=True
                ),
                mock_git_config(),
            ):
                with pytest.raises(SystemExit) as ctx:
                    task_followup_headless("proj_startfail", task_id, "test")
                assert "failed to start" in str(ctx.value)

    def test_sealed_followup_uses_inject_prompt(self, mock_runtime) -> None:
        """Sealed projects inject the follow-up prompt via podman cp, not host files."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            config_file = write_runner_project(
                base, "proj_sealed", extra_yml="  isolation: sealed\n"
            )

            # Create a completed headless task
            result = run_headless_request(
                base,
                config_file,
                HeadlessRunRequest("proj_sealed", "initial prompt"),
            )
            task_id = result.task_id
            assert task_id is not None

            container_mock = mock_runtime.container.return_value

            with (
                unittest.mock.patch.object(
                    type(container_mock),
                    "state",
                    new_callable=unittest.mock.PropertyMock,
                    side_effect=iter(["exited", "running"]).__next__,
                    create=True,
                ),
                unittest.mock.patch.dict(
                    os.environ, runner_env_vars(base, config_file), clear=True
                ),
                mock_git_config(),
                unittest.mock.patch("terok.lib.orchestration.task_runners._print_run_summary"),
                unittest.mock.patch("terok_sandbox.Sandbox") as mock_sandbox_cls,
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    task_followup_headless("proj_sealed", task_id, "sealed follow-up")

                    # inject_prompt should have been called via Sandbox.copy_to
                    mock_sandbox_cls.return_value.copy_to.assert_called_once()
                    call_args = mock_sandbox_cls.return_value.copy_to.call_args[0]
                    assert call_args[2] == "/home/dev/.terok/prompt.txt"

                    # prompt.txt on host should NOT have been updated (sealed skips it)
                    agent_cfg = (
                        base / "sandbox-live" / "tasks" / "proj_sealed" / task_id / "agent-config"
                    )
                    if (agent_cfg / "prompt.txt").exists():
                        # If file exists, it should still contain the original prompt
                        assert (agent_cfg / "prompt.txt").read_text() == "initial prompt"
