# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import contextlib
import os
import re
import subprocess
import unittest.mock
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest

from terok.lib.core.projects import load_project
from terok.lib.domain.task_logs import LogViewOptions, task_logs
from terok.lib.orchestration.environment import build_task_env_and_volumes
from terok.lib.orchestration.task_runners import (
    _ensure_toad_token,
    _rehydrate_toad_token,
    _toad_browser_url,
    task_run_cli,
    task_run_toad,
)
from terok.lib.orchestration.tasks import (
    TaskDeleteResult,
    get_tasks,
    get_workspace_git_diff,
    task_delete,
    task_list,
    task_new,
)
from terok.lib.util.net import url_host
from terok.lib.util.yaml import dump as yaml_dump, load as yaml_load
from terok.tui.clipboard import (
    copy_to_clipboard_detailed,
    get_clipboard_helper_status,
)
from tests.test_utils import (
    assert_task_id,
    captured_runspec,
    mock_git_config,
    parse_meta_value,
    project_env,
    write_project,
)
from tests.testfs import CONTAINER_SSH_DIR
from tests.testnet import GATE_PORT


def _set_state_sequence(mock_runtime, states: list[str | None]):
    """Patch ``runtime.container(...).state`` to yield ``states`` across calls.

    Assigning ``PropertyMock`` directly to ``type(container)`` would leak to
    later tests (the descriptor lives on the shared ``MagicMock`` class), so
    we go through ``patch.object`` with ``create=True`` — the patch context
    restores whatever was there (usually nothing) on exit.
    """
    return unittest.mock.patch.object(
        type(mock_runtime.container.return_value),
        "state",
        new_callable=unittest.mock.PropertyMock,
        side_effect=iter(states).__next__,
        create=True,
    )


def _gate_repo_fragment(project_id: str, *, port: int = GATE_PORT) -> str:
    """Return the gate URL fragment embedded in task env vars.

    Intentionally mode-agnostic: socket transport builds URLs with
    ``localhost:9418`` (container-local socat bridge), tcp transport
    with ``host.containers.internal:<port>``.  We only assert on the
    repo path so these tests pass regardless of ``services.mode``.
    """
    return f":{port}/{project_id}.git"


class TestTask:
    """Tests for task lifecycle, listing filters, and task runner environment behavior."""

    def test_copy_to_clipboard_no_helpers_provides_install_hint(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):
            with unittest.mock.patch("terok.tui.clipboard.shutil.which", return_value=None):
                result = copy_to_clipboard_detailed("hello")
        assert not result.ok
        assert result.hint is not None
        assert "xclip" in result.hint or ""

    def test_copy_to_clipboard_uses_xclip_when_available(self) -> None:
        def which_side_effect(name: str):
            return "/usr/bin/xclip" if name == "xclip" else None

        with unittest.mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):
            with unittest.mock.patch(
                "terok.tui.clipboard.shutil.which", side_effect=which_side_effect
            ):
                with unittest.mock.patch("terok.tui.clipboard.subprocess.run") as run_mock:
                    run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0)
                    result = copy_to_clipboard_detailed("hello")

        assert result.ok
        assert result.method == "xclip"
        run_mock.assert_called()

    def test_task_new_and_delete(self) -> None:
        project_id = "proj8"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            returned_id = task_new(project_id)
            assert_task_id(returned_id)
            meta_dir = ctx.state_dir / "projects" / project_id / "tasks"
            meta_path = meta_dir / f"{returned_id}.yml"
            assert meta_path.is_file()

            meta_text = meta_path.read_text(encoding="utf-8")
            assert parse_meta_value(meta_text, "task_id") == returned_id
            workspace_value = parse_meta_value(meta_text, "workspace")
            assert workspace_value is not None
            assert workspace_value != ""
            workspace = Path(workspace_value)  # type: ignore[arg-type]
            assert workspace.is_dir()

            # Verify second task returns a different hex ID
            second_id = task_new(project_id)
            assert_task_id(second_id)
            assert second_id != returned_id

            with (
                unittest.mock.patch("terok_executor.AgentRunner.capture_logs", return_value=False),
                mock_git_config(),
            ):
                result = task_delete(project_id, returned_id)

            assert isinstance(result, TaskDeleteResult)
            assert not meta_path.exists()
            assert not workspace.exists()

    def test_task_new_records_created_at(self) -> None:
        """task_new writes an ISO 8601 created_at timestamp that round-trips via get_tasks.

        The TUI uses this field to sort tasks newest-first; task_id is random hex
        and carries no temporal information.
        """
        project_id = "proj_created_at"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            tid = task_new(project_id)
            meta_path = ctx.state_dir / "projects" / project_id / "tasks" / f"{tid}.yml"
            meta = yaml_load(meta_path.read_text())

            assert "created_at" in meta
            parsed = datetime.fromisoformat(meta["created_at"])
            assert parsed.tzinfo is not None, "created_at must be timezone-aware"
            assert parsed.utcoffset() == timedelta(0), "created_at must be UTC"

            [task] = [t for t in get_tasks(project_id) if t.task_id == tid]
            assert task.created_at == meta["created_at"]

    def test_task_new_creates_marker_file(self) -> None:
        """Verify that task_new() creates the .new-task-marker file.

        The marker file signals to init-ssh-and-repo.sh that this is a fresh
        task and the workspace should be reset to the latest remote HEAD.
        See the docstring in task_new() for the full protocol description.
        """
        project_id = "proj_marker"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            task_id = task_new(project_id)

            # Verify marker file exists in the workspace subdirectory
            sandbox_live = ctx.base / "sandbox-live"
            workspace_dir = sandbox_live / "tasks" / project_id / task_id / "workspace-dangerous"
            marker_path = workspace_dir / ".new-task-marker"
            assert marker_path.is_file()

            # Verify marker content explains its purpose
            marker_content = marker_path.read_text(encoding="utf-8")
            assert "reset to the latest remote HEAD" in marker_content

    @staticmethod
    def _patch_task_meta(ctx, project_id: str, tid: str, **updates) -> None:
        """Load a task's YAML metadata, apply updates, and write it back."""
        meta_dir = ctx.state_dir / "projects" / project_id / "tasks"
        meta_path = meta_dir / f"{tid}.yml"
        meta = yaml_load(meta_path.read_text())
        meta.update(updates)
        # Setting mode implies the task reached readiness (ready_at marker).
        if "mode" in updates and updates["mode"] is not None and "ready_at" not in updates:
            meta.setdefault("ready_at", "2025-01-01T00:00:00+00:00")
        meta_path.write_text(yaml_dump(meta))

    @staticmethod
    def _task_list_output(project_id: str, states: dict[str, str | None], **filters: str) -> str:
        """Run ``task_list`` with mocked container states and capture stdout."""
        with unittest.mock.patch(
            "terok.lib.orchestration.tasks.get_all_task_states",
            return_value=states,
        ):
            buf = StringIO()
            with redirect_stdout(buf):
                task_list(project_id, **filters)
        return buf.getvalue()

    @staticmethod
    def _task_row_pattern(task_id: str) -> re.Pattern[str]:
        """Return the regex pattern matching a task row for ``task_id``."""
        return re.compile(rf"(?m)^- {re.escape(task_id)}:")

    def test_task_list_no_filters(self) -> None:
        """task_list with no filters prints all tasks."""
        project_id = "proj_list"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            tid1 = task_new(project_id)
            tid2 = task_new(project_id)

            self._patch_task_meta(ctx, project_id, tid1, mode="cli")
            self._patch_task_meta(ctx, project_id, tid2, mode="web")

            output = self._task_list_output(project_id, {tid1: "running", tid2: "exited"})
            assert self._task_row_pattern(tid1).search(output)
            assert "running" in output
            assert self._task_row_pattern(tid2).search(output)
            assert "stopped" in output

    def test_task_list_filter_by_status(self) -> None:
        """task_list --status filters tasks by effective status."""
        project_id = "proj_filt_status"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            tid1 = task_new(project_id)
            tid2 = task_new(project_id)

            self._patch_task_meta(ctx, project_id, tid1, mode="cli")
            self._patch_task_meta(ctx, project_id, tid2, mode="cli")

            output = self._task_list_output(
                project_id, {tid1: "running", tid2: "exited"}, status="running"
            )
            assert self._task_row_pattern(tid1).search(output)
            assert "running" in output
            assert not self._task_row_pattern(tid2).search(output)

    def test_task_list_id_format(self) -> None:
        """task_list prints hex task IDs with no alignment padding."""
        project_id = "proj_align"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            tid1 = task_new(project_id)
            tid2 = task_new(project_id)
            self._patch_task_meta(ctx, project_id, tid1, mode="cli")
            self._patch_task_meta(ctx, project_id, tid2, mode="cli")

            output = self._task_list_output(
                project_id,
                {
                    tid1: "running",
                    tid2: "exited",
                },
            )
            # Hex IDs are printed without alignment padding
            assert self._task_row_pattern(tid1).search(output)
            assert self._task_row_pattern(tid2).search(output)

    def test_task_list_filter_by_mode(self) -> None:
        """task_list --mode filters tasks by their mode field."""
        project_id = "proj_filt_mode"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            tid1 = task_new(project_id)
            tid2 = task_new(project_id)

            self._patch_task_meta(ctx, project_id, tid1, mode="cli")
            self._patch_task_meta(ctx, project_id, tid2, mode="web")

            output = self._task_list_output(project_id, {tid2: None}, mode="web")
            assert not self._task_row_pattern(tid1).search(output)
            assert self._task_row_pattern(tid2).search(output)

    def test_task_list_filter_by_agent(self) -> None:
        """task_list --agent filters tasks by their preset field."""
        project_id = "proj_filt_agent"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            tid1 = task_new(project_id)
            tid2 = task_new(project_id)

            self._patch_task_meta(ctx, project_id, tid1, preset="claude")
            self._patch_task_meta(ctx, project_id, tid2, preset="codex")

            output = self._task_list_output(project_id, {tid1: None, tid2: None}, agent="claude")
            assert self._task_row_pattern(tid1).search(output)
            assert not self._task_row_pattern(tid2).search(output)

    def test_task_list_combined_filters(self) -> None:
        """task_list with multiple filters applies all of them (AND logic)."""
        project_id = "proj_filt_combo"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            tid1 = task_new(project_id)
            tid2 = task_new(project_id)
            tid3 = task_new(project_id)

            for tid, mode in [
                (tid1, "cli"),
                (tid2, "web"),
                (tid3, "cli"),
            ]:
                self._patch_task_meta(ctx, project_id, tid, mode=mode)

            # mode filter narrows to cli first, then status=running keeps only task 1
            output = self._task_list_output(
                project_id, {tid1: "running", tid3: "exited"}, status="running", mode="cli"
            )
            assert self._task_row_pattern(tid1).search(output)
            assert not self._task_row_pattern(tid2).search(output)
            assert not self._task_row_pattern(tid3).search(output)

    def test_task_list_no_match(self) -> None:
        """task_list prints 'No tasks found' when filters match nothing."""
        project_id = "proj_filt_none"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            tid = task_new(project_id)

            # New task has no mode → effective status is "created", not "running"
            output = self._task_list_output(project_id, {tid: None}, status="running")
            assert "No tasks found" in output

    @unittest.mock.patch("terok.lib.orchestration.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.orchestration.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch("terok_sandbox.create_token", return_value="tok" * 10 + "ab")
    def test_build_task_env_gatekeeping(self, *_mocks) -> None:
        project_id = "proj9"
        with project_env(
            f"project:\n  id: {project_id}\n  security_class: gatekeeping\ngit:\n  default_branch: main\n",
            project_id=project_id,
            with_config_file=True,
            with_gate=True,
        ):
            env, volumes = build_task_env_and_volumes(
                project=load_project(project_id),
                task_id="7",
            )

            assert "http://" in env["CODE_REPO"]
            assert _gate_repo_fragment(project_id) in env["CODE_REPO"]
            # No gate volume mount (served via gate server)
            gate_mounts = [v for v in volumes if "gate" in str(v.host_path)]
            assert gate_mounts == []
            # Verify SSH is NOT mounted by default in gatekeeping mode
            ssh_mounts = [v for v in volumes if str(CONTAINER_SSH_DIR) in v.container_path]
            assert ssh_mounts == []

    @unittest.mock.patch("terok.lib.orchestration.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.orchestration.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch("terok_sandbox.create_token", return_value="tok" * 10 + "ab")
    def test_build_task_env_gatekeeping_with_ssh(self, *_mocks) -> None:
        """Gatekeeping mode does not bind-mount SSH (keys go via SSH agent proxy)."""
        project_id = "proj_gatekeeping_ssh"
        with project_env(
            "placeholder",
            project_id=project_id,
            with_config_file=True,
            with_gate=True,
        ) as ctx:
            write_project(
                ctx.config_root,
                project_id,
                f"project:\n  id: {project_id}\n  security_class: gatekeeping\ngit:\n  default_branch: main\n",
            )

            env, volumes = build_task_env_and_volumes(
                project=load_project(project_id),
                task_id="9",
            )

            # Verify gatekeeping behavior: CODE_REPO is http:// URL with token
            assert "http://" in env["CODE_REPO"]
            assert _gate_repo_fragment(project_id) in env["CODE_REPO"]
            # Verify SSH is NOT mounted (keys are served via SSH agent proxy)
            ssh_mounts = [v for v in volumes if str(CONTAINER_SSH_DIR) in v.container_path]
            assert ssh_mounts == []

    @unittest.mock.patch("terok.lib.orchestration.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.orchestration.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch("terok_sandbox.create_token", return_value="tok" * 10 + "ab")
    def test_build_task_env_online(self, *_mocks) -> None:
        project_id = "proj10"
        with project_env(
            "placeholder",
            project_id=project_id,
            with_config_file=True,
            with_gate=True,
        ) as ctx:
            write_project(
                ctx.config_root,
                project_id,
                f"project:\n  id: {project_id}\n  security_class: online\ngit:\n  upstream_url: https://example.com/repo.git\n  default_branch: main\n",
            )

            env, volumes = build_task_env_and_volumes(load_project(project_id), task_id="8")
            assert env["CODE_REPO"] == "https://example.com/repo.git"
            assert env["GIT_BRANCH"] == "main"
            assert env["TEROK_GIT_AUTHORSHIP"] == "agent-human"
            assert "http://" in env["CLONE_FROM"]
            assert _gate_repo_fragment(project_id) in env["CLONE_FROM"]
            # SSH is NOT bind-mounted (keys are served via SSH agent proxy)
            ssh_mounts = [v for v in volumes if str(CONTAINER_SSH_DIR) in v.container_path]
            assert ssh_mounts == []

    def test_build_task_env_uses_configured_git_authorship(self) -> None:
        """Task containers receive the resolved Git authorship mode."""
        project_id = "proj_authorship_env"
        with project_env(
            f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n  authorship: human-agent\n",
            project_id=project_id,
        ):
            env, _volumes = build_task_env_and_volumes(load_project(project_id), task_id="1")
            assert env["TEROK_GIT_AUTHORSHIP"] == "human-agent"

    def test_task_run_cli_colors_login_lines_when_tty(self, mock_runtime) -> None:
        project_id = "proj_cli_color"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ):
            tid = task_new(project_id)
            cname = f"{project_id}-cli-{tid}"
            mock_runtime.container.return_value.login_command.return_value = [
                "podman",
                "exec",
                "-it",
                cname,
                "bash",
            ]
            with (
                _set_state_sequence(mock_runtime, [None, "running"]),
                mock_git_config(),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners._supports_color",
                    return_value=True,
                ),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    task_run_cli(project_id, tid)

            output = buffer.getvalue()
            cname = f"{project_id}-cli-{tid}"
            expected_name = f"\x1b[32m{cname}\x1b[0m"
            expected_enter = f"\x1b[34mpodman exec -it {cname} bash\x1b[0m"
            expected_stop = f"\x1b[31mpodman stop {cname}\x1b[0m"
            assert expected_name in output
            assert expected_enter in output
            assert expected_stop in output

    def test_task_run_cli_does_not_add_files_before_clone(self, mock_runtime) -> None:
        """Interactive CLI startup must not add files to workspace before init clone."""
        project_id = "proj_cli_clean_workspace"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ) as ctx:
            tid = task_new(project_id)
            sandbox_live = ctx.base / "sandbox-live"
            workspace_dir = sandbox_live / "tasks" / project_id / tid / "workspace-dangerous"
            assert sorted(p.name for p in workspace_dir.iterdir()) == [".new-task-marker"]
            with (
                _set_state_sequence(mock_runtime, [None, "running"]),
                mock_git_config(),
            ):
                task_run_cli(project_id, tid)

            assert sorted(p.name for p in workspace_dir.iterdir()) == [".new-task-marker"]
            agent_mounts = ctx.base / "sandbox-live" / "mounts"
            assert (agent_mounts / "_claude-config" / "settings.json").is_file()

    def test_task_run_toad_passes_public_url(self, mock_runtime) -> None:
        """task_run_toad must pass --public-url with the host port to toad serve."""
        project_id = "proj_toad_url"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ):
            tid = task_new(project_id)
            mock_runtime.container.return_value.state = None
            mock_runtime.container.return_value.running = True
            with (
                mock_git_config(),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners.assign_web_port",
                    return_value=7861,
                ),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners._agent_runner"
                ) as sandbox_factory,
            ):
                task_run_toad(project_id, tid)

            spec = captured_runspec(sandbox_factory)
            bash_cmd = spec.command[-1]
            # The in-container supervisor (``terok-toad-entry``) now owns
            # the port wiring; terok only passes --public-url through.
            assert bash_cmd.startswith("terok-toad-entry")
            assert "--public-url http://127.0.0.1:7861" in bash_cmd

            # Host publishes port 8080 (Caddy); toad listens on loopback
            # 8081 inside the container and is not published.
            extra = list(spec.extra_args)
            port_idx = extra.index("-p")
            assert extra[port_idx + 1] == "127.0.0.1:7861:8080"

    def test_task_run_toad_uses_public_host(
        self, monkeypatch: pytest.MonkeyPatch, mock_runtime
    ) -> None:
        """task_run_toad must use TEROK_PUBLIC_HOST for URLs and bind to 0.0.0.0."""
        project_id = "proj_toad_pub"
        monkeypatch.setenv("TEROK_PUBLIC_HOST", "myserver")
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ):
            # Re-apply after clear_env
            monkeypatch.setenv("TEROK_PUBLIC_HOST", "myserver")
            tid = task_new(project_id)
            mock_runtime.container.return_value.state = None
            mock_runtime.container.return_value.running = True
            with (
                mock_git_config(),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners.assign_web_port",
                    return_value=7862,
                ),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners._agent_runner"
                ) as sandbox_factory,
            ):
                task_run_toad(project_id, tid)

            spec = captured_runspec(sandbox_factory)
            bash_cmd = spec.command[-1]
            assert "--public-url http://myserver:7862" in bash_cmd

            # Port forwarding binds to 0.0.0.0 when public host is set
            extra = list(spec.extra_args)
            port_idx = extra.index("-p")
            assert extra[port_idx + 1] == "0.0.0.0:7862:8080"

    def test_task_run_cli_already_running(self, mock_runtime) -> None:
        """task_run_cli prints message and exits when container is already running."""
        project_id = "proj_cli_running"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            tid = task_new(project_id)
            mock_runtime.container.return_value.state = "running"
            with mock_git_config():
                buffer = StringIO()
                with redirect_stdout(buffer):
                    task_run_cli(project_id, tid)

                # Verify message indicates already running
                output = buffer.getvalue()
                assert "already running" in output

    def test_task_run_cli_starts_stopped_container(self, mock_runtime) -> None:
        """task_run_cli uses 'podman start' for stopped container."""
        project_id = "proj_cli_stopped"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            tid = task_new(project_id)
            meta_dir = ctx.state_dir / "projects" / project_id / "tasks"
            meta_path = meta_dir / f"{tid}.yml"

            # Simulate task was previously run
            meta = yaml_load(meta_path.read_text())
            meta["mode"] = "cli"
            meta_path.write_text(yaml_dump(meta))

            cname = f"{project_id}-cli-{tid}"
            with (
                _set_state_sequence(mock_runtime, ["exited", "running"]),
                mock_git_config(),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    task_run_cli(project_id, tid)

                # Verify container(cname).start() was called
                mock_runtime.container.assert_any_call(cname)
                mock_runtime.container.return_value.start.assert_called_once_with()

                # Verify metadata mode is preserved
                meta = yaml_load(meta_path.read_text())
                assert meta["mode"] == "cli"

    def test_get_workspace_git_diff_no_task(self) -> None:
        """Test get_workspace_git_diff returns None when task doesn't exist."""
        project_id = "proj_diff_1"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            result = get_workspace_git_diff(project_id, "999")
            assert result is None

    def test_get_workspace_git_diff_no_mode(self) -> None:
        """Test get_workspace_git_diff returns None when task has no mode set."""
        project_id = "proj_diff_2"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            tid = task_new(project_id)
            # Task exists but has never been run (mode=None)
            result = get_workspace_git_diff(project_id, tid)
            assert result is None

    def test_get_workspace_git_diff_delegates_to_container(self) -> None:
        """Test get_workspace_git_diff delegates to container_git_diff."""
        project_id = "proj_diff_3"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            tid = task_new(project_id)
            from terok.lib.orchestration.tasks import tasks_meta_dir

            meta_path = tasks_meta_dir(project_id) / f"{tid}.yml"
            meta = yaml_load(meta_path.read_text())
            meta["mode"] = "cli"
            meta_path.write_text(yaml_dump(meta))

            expected = "diff --git a/f.txt b/f.txt\n+line\n"
            with unittest.mock.patch(
                "terok.lib.orchestration.tasks.container_git_diff",
                return_value=expected,
            ) as mock_diff:
                result = get_workspace_git_diff(project_id, tid, "HEAD")
                assert result == expected
                mock_diff.assert_called_once_with(project_id, tid, "cli", "HEAD")

    def test_get_workspace_git_diff_prev_commit(self) -> None:
        """Test get_workspace_git_diff with PREV option passes HEAD~1..HEAD."""
        project_id = "proj_diff_5"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            tid = task_new(project_id)
            from terok.lib.orchestration.tasks import tasks_meta_dir

            meta_path = tasks_meta_dir(project_id) / f"{tid}.yml"
            meta = yaml_load(meta_path.read_text())
            meta["mode"] = "run"
            meta_path.write_text(yaml_dump(meta))

            expected = "diff --git a/f.txt b/f.txt\n+prev\n"
            with unittest.mock.patch(
                "terok.lib.orchestration.tasks.container_git_diff",
                return_value=expected,
            ) as mock_diff:
                result = get_workspace_git_diff(project_id, tid, "PREV")
                assert result == expected
                mock_diff.assert_called_once_with(project_id, tid, "run", "HEAD~1", "HEAD")

    def test_get_workspace_git_diff_container_failure(self) -> None:
        """Test get_workspace_git_diff returns None when container exec fails."""
        project_id = "proj_diff_6"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            tid = task_new(project_id)
            from terok.lib.orchestration.tasks import tasks_meta_dir

            meta_path = tasks_meta_dir(project_id) / f"{tid}.yml"
            meta = yaml_load(meta_path.read_text())
            meta["mode"] = "cli"
            meta_path.write_text(yaml_dump(meta))

            with unittest.mock.patch(
                "terok.lib.orchestration.tasks.container_git_diff",
                return_value=None,
            ):
                result = get_workspace_git_diff(project_id, tid)
                assert result is None

    def test_copy_to_clipboard_empty_text(self) -> None:
        """Test copy_to_clipboard_detailed returns failure for empty text."""
        result = copy_to_clipboard_detailed("")
        assert not result.ok

    def test_copy_to_clipboard_success_wl_copy(self) -> None:
        """Test copy_to_clipboard_detailed succeeds with wl-copy."""
        with unittest.mock.patch.dict(
            os.environ, {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}
        ):
            with unittest.mock.patch(
                "terok.tui.clipboard.shutil.which", return_value="/usr/bin/wl-copy"
            ):
                with unittest.mock.patch("terok.tui.clipboard.subprocess.run") as run_mock:
                    run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0)

                    result = copy_to_clipboard_detailed("test content")
                    assert result.ok

                    run_mock.assert_called_once()
                    args, kwargs = run_mock.call_args
                    assert args[0][0] == "wl-copy"
                    assert kwargs["input"] == "test content"
                    assert kwargs["check"]
                    assert kwargs["text"]
                    # wl-copy's daemon inherits stdout; piping it would
                    # hang the caller forever, so we send it to /dev/null
                    # and only keep stderr for error reporting.
                    assert kwargs["stdout"] == subprocess.DEVNULL
                    assert kwargs["stderr"] == subprocess.PIPE
                    assert kwargs["timeout"] > 0

    def test_copy_to_clipboard_fallback_to_xclip(self) -> None:
        """Test copy_to_clipboard_detailed uses xclip on X11 when available."""
        # Ensure Wayland environment variables are not set to force X11 detection
        env = {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0", "WAYLAND_DISPLAY": ""}

        with unittest.mock.patch.dict(os.environ, env, clear=False):
            with unittest.mock.patch(
                "terok.tui.clipboard.shutil.which", return_value="/usr/bin/xclip"
            ):
                with unittest.mock.patch("terok.tui.clipboard.subprocess.run") as run_mock:
                    run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0)

                    result = copy_to_clipboard_detailed("test content")
                    assert result.ok

                    run_mock.assert_called_once()
                    args, _kwargs = run_mock.call_args
                    assert args[0][0] == "xclip"

    def test_copy_to_clipboard_fallback_to_pbcopy(self) -> None:
        """Test copy_to_clipboard_detailed uses pbcopy on macOS and sets method field."""
        with unittest.mock.patch("terok.tui.clipboard.sys.platform", "darwin"):
            with unittest.mock.patch(
                "terok.tui.clipboard.shutil.which", return_value="/usr/bin/pbcopy"
            ):
                with unittest.mock.patch("terok.tui.clipboard.subprocess.run") as run_mock:
                    run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0)

                    result = copy_to_clipboard_detailed("test content")
                    assert result.ok
                    assert result.method == "pbcopy"

                    run_mock.assert_called_once()
                    args, _kwargs = run_mock.call_args
                    assert args[0][0] == "pbcopy"

    def test_copy_to_clipboard_all_fail(self) -> None:
        """Test copy_to_clipboard_detailed returns proper error when all clipboard utilities fail."""
        with unittest.mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):

            def which_side_effect(name: str):
                if name in ("xclip", "xsel"):
                    return f"/usr/bin/{name}"
                return None

            with unittest.mock.patch(
                "terok.tui.clipboard.shutil.which", side_effect=which_side_effect
            ):
                with unittest.mock.patch("terok.tui.clipboard.subprocess.run") as run_mock:
                    run_mock.side_effect = subprocess.CalledProcessError(
                        1, ["xclip"], stderr="boom"
                    )

                    result = copy_to_clipboard_detailed("test content")
                    assert not result.ok
                    assert result.error is not None
                    assert "failed" in result.error

                    assert run_mock.call_count == 2

    def test_copy_to_clipboard_helper_timeout_does_not_hang(self) -> None:
        """A helper that times out must surface as an error, not hang the caller.

        wl-copy's fork-a-daemon behaviour used to deadlock ``subprocess.run``
        when stdout was captured; stdout is now ``DEVNULL`` and the call has
        a hard timeout.  If either defence regresses the caller freezes.

        Also locks in the contract that ``hint`` stays ``None`` when a
        helper was available but failed at runtime — the user already
        has wl-clipboard, so suggesting they install it would be wrong.
        """
        with unittest.mock.patch.dict(
            os.environ, {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}
        ):
            with unittest.mock.patch(
                "terok.tui.clipboard.shutil.which", return_value="/usr/bin/wl-copy"
            ):
                with unittest.mock.patch("terok.tui.clipboard.subprocess.run") as run_mock:
                    run_mock.side_effect = subprocess.TimeoutExpired(cmd=["wl-copy"], timeout=3.0)

                    result = copy_to_clipboard_detailed("test content")
                    assert not result.ok
                    assert result.error is not None
                    assert "timed out" in result.error
                    # A misleading "install wl-clipboard" hint on a system that
                    # already has wl-clipboard would wrongly take precedence
                    # over the real timeout message at the call sites that
                    # prefer ``hint`` over ``error``.
                    assert result.hint is None

    def test_get_clipboard_helper_status_with_available_helpers(self) -> None:
        """Test get_clipboard_helper_status returns available helpers on macOS."""
        with unittest.mock.patch("terok.tui.clipboard.sys.platform", "darwin"):
            with unittest.mock.patch(
                "terok.tui.clipboard.shutil.which", return_value="/usr/bin/pbcopy"
            ):
                status = get_clipboard_helper_status()
                assert status.available
                assert "pbcopy" in status.available
                assert status.hint is None

    def test_get_clipboard_helper_status_no_helpers_wayland(self) -> None:
        """Test get_clipboard_helper_status returns hint for Wayland when no helpers available."""
        with unittest.mock.patch.dict(
            os.environ, {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}
        ):
            with unittest.mock.patch("terok.tui.clipboard.shutil.which", return_value=None):
                status = get_clipboard_helper_status()
                assert status.available == ()
                assert status.hint is not None
                assert "wl-clipboard" in status.hint

    def test_get_clipboard_helper_status_no_helpers_x11(self) -> None:
        """Test get_clipboard_helper_status returns hint for X11 when no helpers available."""
        with unittest.mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):
            with unittest.mock.patch("terok.tui.clipboard.shutil.which", return_value=None):
                status = get_clipboard_helper_status()
                assert status.available == ()
                assert status.hint is not None
                assert "xclip" in status.hint

    @unittest.mock.patch("terok.lib.orchestration.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.orchestration.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch("terok_sandbox.create_token", return_value="tok" * 10 + "ab")
    def test_build_task_env_gatekeeping_expose_external_remote_enabled(self, *_mocks) -> None:
        """Test expose_external_remote=true with upstream_url sets EXTERNAL_REMOTE_URL."""
        project_id = "proj_external_remote_enabled"
        upstream_url = "https://github.com/example/repo.git"
        with project_env(
            f"project:\n  id: {project_id}\n  security_class: gatekeeping\ngit:\n  upstream_url: {upstream_url}\n  default_branch: main\ngatekeeping:\n  expose_external_remote: true\n",
            project_id=project_id,
            with_config_file=True,
            with_gate=True,
        ):
            env, _ = build_task_env_and_volumes(
                project=load_project(project_id),
                task_id="10",
            )

            # Verify EXTERNAL_REMOTE_URL is set when expose_external_remote is enabled
            assert env["EXTERNAL_REMOTE_URL"] == upstream_url
            # Verify gatekeeping mode settings are still correct
            assert "http://" in env["CODE_REPO"]
            assert _gate_repo_fragment(project_id) in env["CODE_REPO"]

    @unittest.mock.patch("terok.lib.orchestration.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.orchestration.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch("terok_sandbox.create_token", return_value="tok" * 10 + "ab")
    def test_build_task_env_gatekeeping_expose_external_remote_disabled(self, *_mocks) -> None:
        """Test expose_external_remote=false does not set EXTERNAL_REMOTE_URL."""
        project_id = "proj_external_remote_disabled"
        upstream_url = "https://github.com/example/repo.git"
        with project_env(
            f"project:\n  id: {project_id}\n  security_class: gatekeeping\ngit:\n  upstream_url: {upstream_url}\n  default_branch: main\ngatekeeping:\n  expose_external_remote: false\n",
            project_id=project_id,
            with_config_file=True,
            with_gate=True,
        ):
            env, _ = build_task_env_and_volumes(
                project=load_project(project_id),
                task_id="11",
            )

            # Verify EXTERNAL_REMOTE_URL is NOT set when expose_external_remote is false
            assert "EXTERNAL_REMOTE_URL" not in env
            # Verify gatekeeping mode settings are still correct
            assert "http://" in env["CODE_REPO"]
            assert _gate_repo_fragment(project_id) in env["CODE_REPO"]

    @unittest.mock.patch("terok.lib.orchestration.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.orchestration.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch("terok_sandbox.create_token", return_value="tok" * 10 + "ab")
    def test_build_task_env_gatekeeping_expose_external_remote_no_upstream(self, *_mocks) -> None:
        """Test expose_external_remote=true without upstream_url does not set EXTERNAL_REMOTE_URL."""
        project_id = "proj_external_remote_no_upstream"
        with project_env(
            f"project:\n  id: {project_id}\n  security_class: gatekeeping\ngit:\n  default_branch: main\ngatekeeping:\n  expose_external_remote: true\n",
            project_id=project_id,
            with_config_file=True,
            with_gate=True,
        ):
            env, _ = build_task_env_and_volumes(
                project=load_project(project_id),
                task_id="12",
            )

            # Verify EXTERNAL_REMOTE_URL is NOT set when upstream_url is missing
            assert "EXTERNAL_REMOTE_URL" not in env
            # Verify gatekeeping mode settings are still correct
            assert "http://" in env["CODE_REPO"]
            assert _gate_repo_fragment(project_id) in env["CODE_REPO"]


class TestToadHelpers:
    """Tests for the toad-token + URL helpers that gate Caddy ingress."""

    def test_url_host_brackets_ipv6(self) -> None:
        """IPv6 literals get wrapped in square brackets, others pass through."""
        assert url_host("127.0.0.1") == "127.0.0.1"
        assert url_host("example.com") == "example.com"
        assert url_host("::1") == "[::1]"
        assert url_host("2001:db8::1") == "[2001:db8::1]"
        # Already-bracketed input is left alone (no double-bracketing).
        assert url_host("[::1]") == "[::1]"

    def test_toad_browser_url_embeds_token_and_brackets_ipv6(self) -> None:
        """The first-hit URL seeds the Caddy cookie and brackets IPv6 hosts."""
        assert _toad_browser_url("127.0.0.1", 8080, "abc") == "http://127.0.0.1:8080/?token=abc"
        assert _toad_browser_url("::1", 8080, "abc") == "http://[::1]:8080/?token=abc"

    def test_ensure_toad_token_creates_file_with_0600(self, tmp_path: Path) -> None:
        """Fresh call mints a urlsafe token, writes it 0600, returns it."""
        token = _ensure_toad_token(tmp_path)
        path = tmp_path / "toad.token"
        import stat as _stat

        assert path.read_text() == token
        assert _stat.S_IMODE(path.stat().st_mode) == 0o600
        # 32-byte urlsafe token → at least 43 chars, plain `[A-Za-z0-9_-]`.
        assert len(token) >= 43
        assert re.match(r"^[A-Za-z0-9_\-]+$", token)

    def test_ensure_toad_token_reuses_existing(self, tmp_path: Path) -> None:
        """Passing *existing* rewrites the same value (restart path)."""
        token = _ensure_toad_token(tmp_path, existing="deadbeef")
        assert token == "deadbeef"
        assert (tmp_path / "toad.token").read_text() == "deadbeef"

    def test_ensure_toad_token_replaces_symlink_without_clobbering_victim(
        self, tmp_path: Path
    ) -> None:
        """A pre-staged symlink at the token path is atomically replaced — victim untouched."""
        victim = tmp_path / "victim"
        victim.write_text("sensitive")
        (tmp_path / "toad.token").symlink_to(victim)

        token = _ensure_toad_token(tmp_path)

        # The symlink is gone, replaced by a real 0600 regular file.
        import stat as _stat

        st = (tmp_path / "toad.token").lstat()
        assert _stat.S_ISREG(st.st_mode)
        assert _stat.S_IMODE(st.st_mode) == 0o600
        assert (tmp_path / "toad.token").read_text() == token
        # Crucially, the victim's content stayed intact (no truncation).
        assert victim.read_text() == "sensitive"

    def test_ensure_toad_token_replaces_hardlink_without_clobbering_peer(
        self, tmp_path: Path
    ) -> None:
        """A pre-staged hardlink at the token path doesn't leak into the peer."""
        victim = tmp_path / "victim"
        victim.write_text("sensitive")
        os.link(victim, tmp_path / "toad.token")

        token = _ensure_toad_token(tmp_path)

        assert (tmp_path / "toad.token").read_text() == token
        # The peer file (another hardlink to the original inode) still holds
        # its own content — the atomic rename gave us a fresh inode.
        assert victim.read_text() == "sensitive"

    def test_rehydrate_toad_token_writes_file_from_metadata(self, tmp_path: Path) -> None:
        """Saved token in ``meta`` is returned and rewritten to disk."""
        project_id = "proj_rehydrate"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ):
            task_id = task_new(project_id)
            project = load_project(project_id)
            agent_cfg = project.tasks_root / str(task_id) / "agent-config"
            agent_cfg.mkdir(parents=True, exist_ok=True)

            out = _rehydrate_toad_token(
                project, task_id, {"web_token": "persisted-value"}, cname="c1"
            )
            assert out == "persisted-value"
            assert (agent_cfg / "toad.token").read_text() == "persisted-value"

    def test_rehydrate_toad_token_requires_metadata(self) -> None:
        """Missing ``web_token`` raises a clear SystemExit — tells the user to re-create."""
        project_id = "proj_no_token"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ):
            task_id = task_new(project_id)
            project = load_project(project_id)
            with pytest.raises(SystemExit, match="no saved web_token"):
                _rehydrate_toad_token(project, task_id, {}, cname="c1")


class TestResumeToadContainer:
    """End-to-end tests for the existing-container resume branch of ``task_run_toad``."""

    @staticmethod
    def _seed_toad_meta(project_id: str, task_id: str, *, port: int, token: str) -> None:
        """Preload task metadata as if a toad launch had already succeeded."""
        project = load_project(project_id)
        from terok.lib.orchestration.tasks import load_task_meta

        _, meta_path = load_task_meta(project.id, task_id, "toad")
        meta = yaml_load(meta_path.read_text(encoding="utf-8"))
        meta.update({"mode": "toad", "web_port": port, "web_token": token})
        meta_path.write_text(yaml_dump(meta))
        (project.tasks_root / str(task_id) / "agent-config").mkdir(parents=True, exist_ok=True)

    def test_resume_running_container_prints_tokenized_url(self, mock_runtime) -> None:
        """A running container short-circuits with the printed ``?token=…`` URL."""
        project_id = "proj_resume_running"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ):
            tid = task_new(project_id)
            self._seed_toad_meta(project_id, tid, port=7862, token="tok-running")
            mock_runtime.container.return_value.state = "running"
            buf = StringIO()
            with (
                mock_git_config(),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners.assign_web_port",
                    return_value=7862,
                ),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners.ensure_vault",
                ),
                redirect_stdout(buf),
            ):
                task_run_toad(project_id, tid)
            out = buf.getvalue()
            assert "is already running" in out
            assert "?token=tok-running" in out
            # Token file rehydrated even though the container was already up.
            project = load_project(project_id)
            assert (
                project.tasks_root / str(tid) / "agent-config" / "toad.token"
            ).read_text() == "tok-running"

    def test_resume_stopped_container_restarts_and_prints_url(self, mock_runtime) -> None:
        """An existing-but-stopped container is started again with the same token."""
        project_id = "proj_resume_stopped"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ):
            tid = task_new(project_id)
            self._seed_toad_meta(project_id, tid, port=7863, token="tok-stopped")
            mock_runtime.container.return_value.state = "exited"
            buf = StringIO()
            with (
                mock_git_config(),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners.assign_web_port",
                    return_value=7863,
                ),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners.ensure_vault",
                ),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners._podman_start",
                ),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners._assert_running",
                ),
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners._apply_shield_policy",
                ),
                redirect_stdout(buf),
            ):
                task_run_toad(project_id, tid)
            out = buf.getvalue()
            assert "Starting existing container" in out
            assert "Container started" in out
            assert "?token=tok-stopped" in out

    def test_resume_rejects_changed_port(self, mock_runtime) -> None:
        """If the saved port is taken by another user, fail fast."""
        project_id = "proj_resume_taken"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ):
            tid = task_new(project_id)
            self._seed_toad_meta(project_id, tid, port=7864, token="tok")
            mock_runtime.container.return_value.state = "running"
            with (
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners.assign_web_port",
                    return_value=9999,  # allocator returned a different port
                ),
                pytest.raises(SystemExit, match="no longer available"),
            ):
                task_run_toad(project_id, tid)


class TestTaskLogs:
    """Tests for task_logs() function."""

    def _setup_task_with_mode(self, project_id, mode="run"):
        """Create a task and set its mode in metadata."""
        task_id = task_new(project_id)
        # Manually update metadata to set mode (normally done by task runners)
        from terok.lib.core.paths import core_state_dir

        meta_dir = core_state_dir() / "projects" / project_id / "tasks"
        meta_path = meta_dir / f"{task_id}.yml"
        meta = yaml_load(meta_path.read_text()) or {}
        meta["mode"] = mode
        meta_path.write_text(yaml_dump(meta))
        return task_id

    def test_unknown_task_raises(self) -> None:
        """task_logs raises SystemExit for non-existent task."""
        with project_env(
            "project:\n  id: proj_logs1\n",
            project_id="proj_logs1",
        ):
            with mock_git_config():
                with pytest.raises(SystemExit) as cm:
                    task_logs("proj_logs1", "999")
                assert "Unknown task" in str(cm.value)

    def test_no_mode_raises(self) -> None:
        """task_logs raises SystemExit when task has no mode set."""
        with project_env(
            "project:\n  id: proj_logs2\n",
            project_id="proj_logs2",
        ):
            with mock_git_config():
                task_id = task_new("proj_logs2")
                with pytest.raises(SystemExit) as cm:
                    task_logs("proj_logs2", task_id)
                assert "never been run" in str(cm.value)

    def test_container_not_found_raises(self, mock_runtime) -> None:
        """task_logs raises SystemExit when container doesn't exist."""
        mock_runtime.container.return_value.state = None
        with project_env(
            "project:\n  id: proj_logs3\n",
            project_id="proj_logs3",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs3", "run")
                with pytest.raises(SystemExit) as cm:
                    task_logs("proj_logs3", task_id)
                assert "does not exist" in str(cm.value)

    def test_negative_tail_raises(self, mock_runtime) -> None:
        """task_logs raises SystemExit for negative tail value."""
        mock_runtime.container.return_value.state = "running"
        with project_env(
            "project:\n  id: proj_logs4\n",
            project_id="proj_logs4",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs4", "run")
                with pytest.raises(SystemExit) as cm:
                    task_logs("proj_logs4", task_id, LogViewOptions(tail=-1))
                assert "--tail must be >= 0" in str(cm.value)

    def test_raw_mode_exec(self, mock_runtime) -> None:
        """task_logs in raw mode calls os.execvp."""
        mock_runtime.container.return_value.state = "exited"
        with project_env(
            "project:\n  id: proj_logs5\n",
            project_id="proj_logs5",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs5", "cli")
                # os.execvp replaces the process, so mock it to raise SystemExit
                # to prevent fall-through to the formatted mode code path.
                captured_args = []

                def fake_execvp(file, args):
                    captured_args.append((file, args))
                    raise SystemExit(0)

                with unittest.mock.patch(
                    "terok.lib.domain.task_logs.os.execvp", side_effect=fake_execvp
                ):
                    with pytest.raises(SystemExit):
                        task_logs("proj_logs5", task_id, LogViewOptions(raw=True))
                    assert len(captured_args) == 1
                    assert captured_args[0][0] == "podman"
                    assert "logs" in captured_args[0][1]

    def test_raw_mode_podman_not_found(self, mock_runtime) -> None:
        """task_logs in raw mode raises SystemExit if podman not found."""
        mock_runtime.container.return_value.state = "exited"
        with project_env(
            "project:\n  id: proj_logs6\n",
            project_id="proj_logs6",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs6", "cli")
                with unittest.mock.patch(
                    "terok.lib.domain.task_logs.os.execvp",
                    side_effect=FileNotFoundError("podman"),
                ):
                    with pytest.raises(SystemExit) as cm:
                        task_logs("proj_logs6", task_id, LogViewOptions(raw=True))
                    assert "podman not found" in str(cm.value)

    def test_formatted_mode_feeds_formatter(self, mock_runtime) -> None:
        """task_logs in formatted mode pipes lines through formatter."""
        mock_runtime.container.return_value.state = "exited"
        with project_env(
            "project:\n  id: proj_logs7\n",
            project_id="proj_logs7",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs7", "run")

                # Create a mock process that returns some data then exits
                mock_proc = unittest.mock.Mock()
                mock_proc.stdout = unittest.mock.Mock()
                # First poll returns None (running), then 0 (exited), then 0 (finally block)
                mock_proc.poll = unittest.mock.Mock(side_effect=[None, 0, 0])
                # read1 returns data, then read returns remaining
                mock_proc.stdout.read1 = unittest.mock.Mock(return_value=b'{"type":"system"}\n')
                mock_proc.stdout.read = unittest.mock.Mock(return_value=b"")
                mock_proc.stdout.fileno = unittest.mock.Mock(return_value=3)
                mock_proc.stderr = unittest.mock.Mock()
                mock_proc.stderr.read = unittest.mock.Mock(return_value=b"")
                mock_proc.returncode = 0
                mock_proc.wait = unittest.mock.Mock()
                mock_proc.terminate = unittest.mock.Mock()

                mock_formatter = unittest.mock.Mock()

                with (
                    unittest.mock.patch(
                        "terok.lib.domain.task_logs.AgentRunner"
                    ) as mock_runner_cls,
                    unittest.mock.patch(
                        "terok.lib.domain.task_logs.auto_detect_formatter",
                        return_value=mock_formatter,
                    ),
                    unittest.mock.patch("select.select") as mock_select,
                ):
                    mock_runner_cls.return_value.stream_logs_process.return_value = mock_proc
                    mock_select.return_value = ([mock_proc.stdout], [], [])
                    buf = StringIO()
                    with redirect_stdout(buf):
                        task_logs("proj_logs7", task_id)

                    mock_runner_cls.return_value.stream_logs_process.assert_called_once()
                    mock_formatter.feed_line.assert_called()
                    mock_formatter.finish.assert_called_once()

    def test_formatted_mode_podman_not_found(self, mock_runtime) -> None:
        """task_logs in formatted mode raises SystemExit if podman not found."""
        mock_runtime.container.return_value.state = "running"
        with project_env(
            "project:\n  id: proj_logs8\n",
            project_id="proj_logs8",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs8", "run")
                with unittest.mock.patch(
                    "terok.lib.domain.task_logs.AgentRunner"
                ) as mock_runner_cls:
                    mock_runner_cls.return_value.stream_logs_process.side_effect = (
                        FileNotFoundError("podman")
                    )
                    with pytest.raises(SystemExit) as cm:
                        task_logs("proj_logs8", task_id)
                    assert "podman not found" in str(cm.value)

    def test_persisted_logs_fallback(self, mock_runtime) -> None:
        """task_logs falls back to persisted log file when container is gone."""
        mock_runtime.container.return_value.state = None
        with project_env(
            "project:\n  id: proj_logs_persist\n",
            project_id="proj_logs_persist",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs_persist", "run")

                # Create persisted log file
                from terok.lib.core.config import sandbox_live_dir

                task_dir = Path(sandbox_live_dir()) / "tasks" / "proj_logs_persist" / task_id
                logs_dir = task_dir / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                log_file = logs_dir / "container.log"
                log_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

                mock_formatter = unittest.mock.Mock()

                with unittest.mock.patch(
                    "terok.lib.domain.task_logs.auto_detect_formatter",
                    return_value=mock_formatter,
                ):
                    buf = StringIO()
                    with redirect_stdout(buf):
                        task_logs("proj_logs_persist", task_id)

                    # Formatter should have been fed 3 lines
                    assert mock_formatter.feed_line.call_count == 3
                    mock_formatter.finish.assert_called_once()

    def test_persisted_logs_fallback_with_tail(self, mock_runtime) -> None:
        """task_logs persisted fallback respects --tail option."""
        mock_runtime.container.return_value.state = None
        with project_env(
            "project:\n  id: proj_logs_tail\n",
            project_id="proj_logs_tail",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs_tail", "run")

                from terok.lib.core.config import sandbox_live_dir

                task_dir = Path(sandbox_live_dir()) / "tasks" / "proj_logs_tail" / task_id
                logs_dir = task_dir / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                log_file = logs_dir / "container.log"
                log_file.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

                mock_formatter = unittest.mock.Mock()

                with unittest.mock.patch(
                    "terok.lib.domain.task_logs.auto_detect_formatter",
                    return_value=mock_formatter,
                ):
                    task_logs("proj_logs_tail", task_id, LogViewOptions(tail=2))
                    assert mock_formatter.feed_line.call_count == 2

    def test_no_container_no_logs_raises(self, mock_runtime) -> None:
        """task_logs raises when container is gone and no persisted logs exist."""
        mock_runtime.container.return_value.state = None
        with project_env(
            "project:\n  id: proj_logs_nolog\n",
            project_id="proj_logs_nolog",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs_nolog", "run")
                with pytest.raises(SystemExit) as cm:
                    task_logs("proj_logs_nolog", task_id)
                assert "no persisted logs found" in str(cm.value)

    def test_negative_tail_persisted_fallback_raises(self, mock_runtime) -> None:
        """task_logs raises for negative tail even when falling back to persisted logs."""
        mock_runtime.container.return_value.state = None
        with project_env(
            "project:\n  id: proj_logs_negtail\n",
            project_id="proj_logs_negtail",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs_negtail", "run")

                from terok.lib.core.config import sandbox_live_dir

                task_dir = Path(sandbox_live_dir()) / "tasks" / "proj_logs_negtail" / task_id
                logs_dir = task_dir / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                (logs_dir / "container.log").write_text("a\nb\n")

                with pytest.raises(SystemExit) as cm:
                    task_logs("proj_logs_negtail", task_id, LogViewOptions(tail=-1))
                assert "--tail must be >= 0" in str(cm.value)


class TestTaskArchive:
    """Tests for task archival on deletion."""

    def test_task_delete_creates_archive(self) -> None:
        """task_delete archives metadata and logs before cleanup."""
        project_id = "proj_archive1"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            with mock_git_config():
                task_id = task_new(project_id)
                meta_dir = ctx.state_dir / "projects" / project_id / "tasks"
                meta_path = meta_dir / f"{task_id}.yml"

                # Set mode in metadata (simulating a task that ran)
                meta = yaml_load(meta_path.read_text()) or {}
                meta["mode"] = "run"
                meta["name"] = "test-task"
                meta["exit_code"] = 0
                meta_path.write_text(yaml_dump(meta))

                # Create logs dir to simulate persisted logs
                task_dir = ctx.state_dir / "tasks" / project_id / task_id
                logs_dir = task_dir / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                (logs_dir / "container.log").write_text("log content\n")

                log_content = "captured log output\n"

                def _fake_capture(self, cname, dest, *, timestamps=True, timeout=60.0):
                    dest.write_text(log_content)
                    return True

                with unittest.mock.patch(
                    "terok_executor.AgentRunner.capture_logs",
                    new=_fake_capture,
                ):
                    task_delete(project_id, task_id)

                # Task should be deleted
                assert not meta_path.exists()

                # Archive should exist under namespace archive tree
                from terok.lib.orchestration.tasks import tasks_archive_dir

                archive_root = tasks_archive_dir(project_id)
                assert archive_root.is_dir()
                archives = list(archive_root.iterdir())
                assert len(archives) == 1

                archive_entry = archives[0]
                # Archive dir name contains task_id and name
                assert task_id in archive_entry.name
                assert "test-task" in archive_entry.name

                # Archive should contain task.yml
                archived_meta = archive_entry / "task.yml"
                assert archived_meta.is_file()
                archived_data = yaml_load(archived_meta.read_text())
                assert archived_data["task_id"] == task_id
                assert archived_data["name"] == "test-task"

                # Archive should contain logs (captured from podman)
                archived_logs = archive_entry / "logs" / "container.log"
                assert archived_logs.is_file()
                assert archived_logs.read_text() == "captured log output\n"

    def test_task_delete_archives_without_logs(self) -> None:
        """task_delete still archives metadata even when no logs exist."""
        project_id = "proj_archive2"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            with mock_git_config():
                task_id = task_new(project_id)

                def fake_run(cmd, *, stdout=None, stderr=None, timeout=None, **kw):
                    """No-op mock for subprocess.run."""
                    result = unittest.mock.Mock()
                    result.returncode = 0
                    return result

                with unittest.mock.patch(
                    "terok_executor.AgentRunner.capture_logs",
                    return_value=True,
                ):
                    task_delete(project_id, task_id)

                from terok.lib.orchestration.tasks import tasks_archive_dir

                archive_root = tasks_archive_dir(project_id)
                assert archive_root.is_dir()
                archives = list(archive_root.iterdir())
                assert len(archives) == 1

                # Should have task.yml but no logs subdir
                archive_entry = archives[0]
                assert (archive_entry / "task.yml").is_file()
                assert not (archive_entry / "logs").exists()

    def test_list_archived_tasks(self) -> None:
        """list_archived_tasks returns archived tasks sorted newest-first."""
        from terok.lib.orchestration.tasks import list_archived_tasks, tasks_archive_dir

        project_id = "proj_archive3"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            # Create archive entries manually
            archive_root = tasks_archive_dir(project_id)
            archive_root.mkdir(parents=True, exist_ok=True)

            for i, ts in enumerate(["20260301T100000Z", "20260302T100000Z", "20260303T100000Z"]):
                entry_dir = archive_root / f"{ts}_{i + 1}_task-{i + 1}"
                entry_dir.mkdir()
                (entry_dir / "task.yml").write_text(
                    yaml_dump(
                        {
                            "task_id": str(i + 1),
                            "name": f"task-{i + 1}",
                            "mode": "run",
                            "exit_code": 0,
                        }
                    )
                )

            archived = list_archived_tasks(project_id)
            assert len(archived) == 3
            # Newest first
            assert archived[0].task_id == "3"
            assert archived[1].task_id == "2"
            assert archived[2].task_id == "1"
            assert archived[0].archived_at == "20260303T100000Z"

    def test_task_archive_logs(self) -> None:
        """task_archive_logs returns log file path for matching archive."""
        from terok.lib.orchestration.tasks import task_archive_logs, tasks_archive_dir

        project_id = "proj_archive4"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            archive_root = tasks_archive_dir(project_id)
            entry_dir = archive_root / "20260305T120000Z_1_my-task"
            logs_dir = entry_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            (logs_dir / "container.log").write_text("archived log\n")

            # Full match
            result = task_archive_logs(project_id, "20260305T120000Z_1_my-task")
            assert result is not None
            assert result.read_text() == "archived log\n"

            # Prefix match
            result = task_archive_logs(project_id, "20260305T120000Z")
            assert result is not None

            # No match
            result = task_archive_logs(project_id, "20990101")
            assert result is None

    def test_task_archive_list_empty(self) -> None:
        """task_archive_list prints message when no archives exist."""
        from terok.lib.orchestration.tasks import task_archive_list

        project_id = "proj_archive5"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            buf = StringIO()
            with redirect_stdout(buf):
                task_archive_list(project_id)
            assert "No archived tasks found" in buf.getvalue()

    def test_capture_task_logs(self) -> None:
        """capture_task_logs delegates to AgentRunner.capture_logs and
        returns the log file path when the executor reports success."""
        from terok.lib.orchestration.tasks import capture_task_logs

        project_id = "proj_capture1"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            with mock_git_config():
                task_id = task_new(project_id)

                log_content = "2026-03-05T12:00:00Z stdout line\n"

                def fake_capture(self, cname, dest, *, timestamps=True, timeout=60.0):
                    """Simulate AgentRunner.capture_logs writing to *dest*."""
                    dest.write_text(log_content)
                    return True

                with unittest.mock.patch(
                    "terok_executor.AgentRunner.capture_logs",
                    new=fake_capture,
                ):
                    log_file = capture_task_logs(project_id, task_id, "run")

                assert log_file is not None
                assert "stdout line" in log_file.read_text()

    def test_capture_task_logs_podman_not_found(self) -> None:
        """capture_task_logs returns None when the executor reports failure
        (podman missing, timeout, non-zero returncode — all surface the same
        way through AgentRunner.capture_logs)."""
        from terok.lib.orchestration.tasks import capture_task_logs

        project_id = "proj_capture2"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            with mock_git_config():
                task_id = task_new(project_id)

                with unittest.mock.patch(
                    "terok_executor.AgentRunner.capture_logs",
                    return_value=False,
                ):
                    result = capture_task_logs(project_id, task_id, "run")

                assert result is None


class TestTaskDeleteWarnings:
    """Verify that _task_delete collects per-step warnings."""

    @staticmethod
    def _make_rm_result(name: str, removed: bool, error: str | None = None):
        """Build a duck-typed ContainerRemoveResult for testing."""
        from types import SimpleNamespace

        return SimpleNamespace(name=name, removed=removed, error=error)

    def _delete_with_mocks(
        self,
        mock_runtime,
        project_id: str = "proj_warn",
        *,
        token_side_effect: BaseException | None = None,
        container_results: list | None = None,
        rmtree_side_effect: BaseException | None = None,
        unlink_side_effect: BaseException | None = None,
    ) -> TaskDeleteResult:
        """Create a task and delete it with configurable failure injections."""
        if container_results is None:
            container_results = []

        mock_runtime.force_remove.return_value = [
            self._make_rm_result(**r) for r in container_results
        ]

        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            with mock_git_config():
                task_id = task_new(project_id)

                patches = [
                    unittest.mock.patch(
                        "terok_executor.AgentRunner.capture_logs",
                        return_value=True,
                    ),
                ]
                if token_side_effect:
                    patches.append(
                        unittest.mock.patch(
                            "terok_sandbox.revoke_token_for_task",
                            side_effect=token_side_effect,
                        )
                    )
                if rmtree_side_effect:
                    patches.append(
                        unittest.mock.patch(
                            "terok.lib.orchestration.tasks.shutil.rmtree",
                            side_effect=rmtree_side_effect,
                        )
                    )

                with contextlib.ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)

                    if unlink_side_effect:
                        orig_unlink = Path.unlink

                        def _guarded_unlink(self, *a, **kw):
                            if "tasks" in str(self) and self.suffix == ".yml":
                                raise unlink_side_effect
                            return orig_unlink(self, *a, **kw)

                        stack.enter_context(
                            unittest.mock.patch.object(Path, "unlink", _guarded_unlink)
                        )

                    return task_delete(project_id, task_id)

    def test_clean_delete_returns_empty_warnings(self, mock_runtime) -> None:
        """Normal deletion returns TaskDeleteResult with no warnings."""
        result = self._delete_with_mocks(mock_runtime, project_id="proj_warn1")
        assert isinstance(result, TaskDeleteResult)
        assert result.warnings == []

    def test_token_revoke_failure_produces_warning(self, mock_runtime) -> None:
        """Failed token revoke adds a warning but deletion still completes."""
        result = self._delete_with_mocks(
            mock_runtime,
            project_id="proj_warn2",
            token_side_effect=RuntimeError("auth server down"),
        )
        assert any("Token revoke" in w for w in result.warnings)

    def test_container_rm_failure_produces_warning(self, mock_runtime) -> None:
        """Failed container removal adds a warning and keeps port claimed."""
        result = self._delete_with_mocks(
            mock_runtime,
            project_id="proj_warn3",
            container_results=[
                {"name": "proj-cli-1", "removed": True},
                {"name": "proj-web-1", "removed": False, "error": "locked"},
            ],
        )
        assert any("proj-web-1" in w and "locked" in w for w in result.warnings)
        assert not any("proj-cli-1" in w for w in result.warnings)
        assert any("Web port kept claimed" in w for w in result.warnings)

    def test_workspace_rm_failure_produces_warning(self, mock_runtime) -> None:
        """Failed workspace rmtree adds a warning."""
        result = self._delete_with_mocks(
            mock_runtime,
            project_id="proj_warn4",
            rmtree_side_effect=PermissionError("busy"),
        )
        assert any("Workspace removal" in w for w in result.warnings)

    def test_metadata_rm_failure_produces_warning(self, mock_runtime) -> None:
        """Failed metadata unlink adds a warning."""
        result = self._delete_with_mocks(
            mock_runtime,
            project_id="proj_warn5",
            unlink_side_effect=PermissionError("read-only"),
        )
        assert any("Metadata removal" in w for w in result.warnings)

    def test_multiple_failures_collected(self, mock_runtime) -> None:
        """Multiple failures from different steps all appear in warnings."""
        result = self._delete_with_mocks(
            mock_runtime,
            project_id="proj_warn6",
            token_side_effect=RuntimeError("offline"),
            container_results=[
                {"name": "c1", "removed": False, "error": "timeout"},
            ],
            rmtree_side_effect=OSError("nfs stale"),
        )
        assert len(result.warnings) >= 4
        assert any("Token" in w for w in result.warnings)
        assert any("c1" in w for w in result.warnings)
        assert any("Workspace" in w for w in result.warnings)
        assert any("Web port kept claimed" in w for w in result.warnings)
