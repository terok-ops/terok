# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import os
import re
import subprocess
import unittest.mock
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest
import yaml

from terok.lib.containers.environment import build_task_env_and_volumes
from terok.lib.containers.task_logs import LogViewOptions, task_logs
from terok.lib.containers.task_runners import task_run_cli, task_run_toad
from terok.lib.containers.tasks import (
    get_workspace_git_diff,
    task_delete,
    task_list,
    task_new,
)
from terok.lib.core.projects import load_project
from terok.tui.clipboard import (
    copy_to_clipboard_detailed,
    get_clipboard_helper_status,
)
from tests.test_utils import mock_git_config, parse_meta_value, project_env, write_project
from tests.testfs import CONTAINER_SSH_DIR
from tests.testnet import CONTAINER_HOSTNAME, GATE_PORT


def _gate_repo_fragment(project_id: str, *, port: int = GATE_PORT) -> str:
    """Return the host-side gate URL fragment embedded in task env vars."""
    return f"@{CONTAINER_HOSTNAME}:{port}/{project_id}.git"


def _assert_volume_mount(volumes: list[str], expected_base: str, expected_suffix: str) -> None:
    """Assert that a volume mount exists with the correct SELinux suffix.

    Args:
        volumes: List of volume mount strings
        expected_base: The base mount string without SELinux suffix
        expected_suffix: The expected SELinux suffix (e.g., ":Z" or ":z")
    """
    expected_full = f"{expected_base}{expected_suffix}"

    # Check if the expected mount exists (may have additional options like ,ro)
    found = False
    for volume in volumes:
        if volume.startswith(expected_full):
            # Check if it's either exactly the expected full string, or has additional options
            remaining = volume[len(expected_full) :]
            if not remaining or remaining.startswith(","):
                found = True
                break

    if not found:
        # For debugging, show what we actually got
        similar_mounts = [v for v in volumes if expected_base in v]
        raise AssertionError(
            f"Expected volume mount '{expected_full}' (or with additional options) not found in volumes. "
            f"Similar mounts found: {similar_mounts}"
        )


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
            assert returned_id == "1"
            meta_dir = ctx.state_dir / "projects" / project_id / "tasks"
            meta_path = meta_dir / "1.yml"
            assert meta_path.is_file()

            meta_text = meta_path.read_text(encoding="utf-8")
            assert parse_meta_value(meta_text, "task_id") == "1"
            workspace_value = parse_meta_value(meta_text, "workspace")
            assert workspace_value is not None
            assert workspace_value != ""
            workspace = Path(workspace_value)  # type: ignore[arg-type]
            assert workspace.is_dir()

            # Verify second task returns incremented ID
            second_id = task_new(project_id)
            assert second_id == "2"

            with (
                unittest.mock.patch("terok.lib.containers.tasks.subprocess.run") as run_mock,
                mock_git_config(),
            ):
                run_mock.return_value.returncode = 0
                task_delete(project_id, "1")

            assert not meta_path.exists()
            assert not workspace.exists()

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
            task_new(project_id)

            # Verify marker file exists in the workspace subdirectory
            workspace_dir = ctx.state_dir / "tasks" / project_id / "1" / "workspace-dangerous"
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
        meta = yaml.safe_load(meta_path.read_text())
        meta.update(updates)
        meta_path.write_text(yaml.safe_dump(meta))

    @staticmethod
    def _task_list_output(project_id: str, states: dict[str, str | None], **filters: str) -> str:
        """Run ``task_list`` with mocked container states and capture stdout."""
        with unittest.mock.patch(
            "terok.lib.containers.tasks.get_all_task_states",
            return_value=states,
        ):
            buf = StringIO()
            with redirect_stdout(buf):
                task_list(project_id, **filters)
        return buf.getvalue()

    @staticmethod
    def _task_row_pattern(task_id: str) -> re.Pattern[str]:
        """Return the regex pattern matching a task row for ``task_id``."""
        return re.compile(rf"(?m)^-{' ' * max(1, 4 - len(task_id))}{re.escape(task_id)}:")

    def test_task_list_no_filters(self) -> None:
        """task_list with no filters prints all tasks."""
        project_id = "proj_list"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            task_new(project_id)
            task_new(project_id)

            self._patch_task_meta(ctx, project_id, "1", mode="cli")
            self._patch_task_meta(ctx, project_id, "2", mode="web")

            output = self._task_list_output(project_id, {"1": "running", "2": "exited"})
            # Task IDs are right-aligned to 3 characters
            assert re.search(r"(?m)^- {3}1:", output)
            assert "running" in output
            assert re.search(r"(?m)^- {3}2:", output)
            assert "stopped" in output

    def test_task_list_filter_by_status(self) -> None:
        """task_list --status filters tasks by effective status."""
        project_id = "proj_filt_status"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            task_new(project_id)
            task_new(project_id)

            self._patch_task_meta(ctx, project_id, "1", mode="cli")
            self._patch_task_meta(ctx, project_id, "2", mode="cli")

            output = self._task_list_output(
                project_id, {"1": "running", "2": "exited"}, status="running"
            )
            # Task IDs are right-aligned to 3 characters
            assert re.search(r"(?m)^- {3}1:", output)
            assert "running" in output
            assert not re.search(r"(?m)^- {3}2:", output)

    def test_task_list_id_alignment(self) -> None:
        """task_list right-aligns task IDs to 3 characters."""
        project_id = "proj_align"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            # Create tasks 1 and 2 normally
            task_new(project_id)
            task_new(project_id)
            self._patch_task_meta(ctx, project_id, "1", mode="cli")
            self._patch_task_meta(ctx, project_id, "2", mode="cli")

            # Manually create a 2-digit task (10) and a 3-digit task (100)
            meta_dir = ctx.state_dir / "projects" / project_id / "tasks"
            ws_base = ctx.state_dir / "projects" / project_id / "workspaces"
            for tid, name in [("10", "double-digit"), ("100", "triple-digit")]:
                ws_dir = ws_base / tid
                ws_dir.mkdir(parents=True, exist_ok=True)
                meta = {
                    "task_id": tid,
                    "name": name,
                    "mode": "cli",
                    "workspace": str(ws_dir),
                    "web_port": None,
                }
                (meta_dir / f"{tid}.yml").write_text(yaml.safe_dump(meta))

            output = self._task_list_output(
                project_id,
                {
                    "1": "running",
                    "2": "exited",
                    "10": "running",
                    "100": "running",
                },
            )
            # 1-digit: 2 leading spaces; 2-digit: 1 leading space; 3-digit: none
            assert re.search(r"(?m)^- {3}1:", output)
            assert re.search(r"(?m)^- {3}2:", output)
            assert re.search(r"(?m)^- {2}10:", output)
            assert re.search(r"(?m)^- 100:", output)

    def test_task_list_filter_by_mode(self) -> None:
        """task_list --mode filters tasks by their mode field."""
        project_id = "proj_filt_mode"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            task_new(project_id)
            task_new(project_id)

            self._patch_task_meta(ctx, project_id, "1", mode="cli")
            self._patch_task_meta(ctx, project_id, "2", mode="web")

            output = self._task_list_output(project_id, {"2": None}, mode="web")
            assert not self._task_row_pattern("1").search(output)
            assert self._task_row_pattern("2").search(output)

    def test_task_list_filter_by_agent(self) -> None:
        """task_list --agent filters tasks by their preset field."""
        project_id = "proj_filt_agent"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            task_new(project_id)
            task_new(project_id)

            self._patch_task_meta(ctx, project_id, "1", preset="claude")
            self._patch_task_meta(ctx, project_id, "2", preset="codex")

            output = self._task_list_output(project_id, {"1": None, "2": None}, agent="claude")
            assert self._task_row_pattern("1").search(output)
            assert not self._task_row_pattern("2").search(output)

    def test_task_list_combined_filters(self) -> None:
        """task_list with multiple filters applies all of them (AND logic)."""
        project_id = "proj_filt_combo"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            task_new(project_id)
            task_new(project_id)
            task_new(project_id)

            for tid, mode in [
                ("1", "cli"),
                ("2", "web"),
                ("3", "cli"),
            ]:
                self._patch_task_meta(ctx, project_id, tid, mode=mode)

            # mode filter narrows to cli first, then status=running keeps only task 1
            output = self._task_list_output(
                project_id, {"1": "running", "3": "exited"}, status="running", mode="cli"
            )
            assert self._task_row_pattern("1").search(output)
            assert not self._task_row_pattern("2").search(output)
            assert not self._task_row_pattern("3").search(output)

    def test_task_list_no_match(self) -> None:
        """task_list prints 'No tasks found' when filters match nothing."""
        project_id = "proj_filt_none"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            task_new(project_id)

            # New task has no mode → effective status is "created", not "running"
            output = self._task_list_output(project_id, {"1": None}, status="running")
            assert "No tasks found" in output

    @unittest.mock.patch("terok.lib.containers.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.containers.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch(
        "terok.lib.security.gate_tokens.create_token", return_value="tok" * 10 + "ab"
    )
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
            gate_mounts = [v for v in volumes if "gate" in v.split(":")[0]]
            assert gate_mounts == []
            # Verify SSH is NOT mounted by default in gatekeeping mode
            ssh_mounts = [v for v in volumes if str(CONTAINER_SSH_DIR) in v]
            assert ssh_mounts == []

    @unittest.mock.patch("terok.lib.containers.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.containers.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch(
        "terok.lib.security.gate_tokens.create_token", return_value="tok" * 10 + "ab"
    )
    def test_build_task_env_gatekeeping_with_ssh(self, *_mocks) -> None:
        """Gatekeeping mode with mount_in_gatekeeping enabled should mount SSH."""
        project_id = "proj_gatekeeping_ssh"
        with project_env(
            "placeholder",
            project_id=project_id,
            with_config_file=True,
            with_gate=True,
        ) as ctx:
            ssh_dir = ctx.base / "ssh"
            ssh_dir.mkdir(parents=True, exist_ok=True)

            write_project(
                ctx.config_root,
                project_id,
                f"project:\n  id: {project_id}\n  security_class: gatekeeping\ngit:\n  default_branch: main\nssh:\n  host_dir: {ssh_dir}\n  mount_in_gatekeeping: true\n",
            )

            env, volumes = build_task_env_and_volumes(
                project=load_project(project_id),
                task_id="9",
            )

            # Verify gatekeeping behavior: CODE_REPO is http:// URL with token
            assert "http://" in env["CODE_REPO"]
            assert _gate_repo_fragment(project_id) in env["CODE_REPO"]
            # Verify SSH IS mounted when mount_in_gatekeeping is true
            _assert_volume_mount(volumes, f"{ssh_dir}:{CONTAINER_SSH_DIR}", ":z")

    @unittest.mock.patch("terok.lib.containers.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.containers.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch(
        "terok.lib.security.gate_tokens.create_token", return_value="tok" * 10 + "ab"
    )
    def test_build_task_env_online(self, *_mocks) -> None:
        project_id = "proj10"
        with project_env(
            "placeholder",
            project_id=project_id,
            with_config_file=True,
            with_gate=True,
        ) as ctx:
            ssh_dir = ctx.base / "ssh"
            ssh_dir.mkdir(parents=True, exist_ok=True)

            write_project(
                ctx.config_root,
                project_id,
                f"project:\n  id: {project_id}\n  security_class: online\ngit:\n  upstream_url: https://example.com/repo.git\n  default_branch: main\nssh:\n  host_dir: {ssh_dir}\n  mount_in_online: true\n",
            )

            env, volumes = build_task_env_and_volumes(load_project(project_id), task_id="8")
            assert env["CODE_REPO"] == "https://example.com/repo.git"
            assert env["GIT_BRANCH"] == "main"
            assert env["TEROK_GIT_AUTHORSHIP"] == "agent-human"
            assert "http://" in env["CLONE_FROM"]
            assert _gate_repo_fragment(project_id) in env["CLONE_FROM"]
            _assert_volume_mount(volumes, f"{ssh_dir}:{CONTAINER_SSH_DIR}", ":z")

    def test_build_task_env_uses_configured_git_authorship(self) -> None:
        """Task containers receive the resolved Git authorship mode."""
        project_id = "proj_authorship_env"
        with project_env(
            f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n  authorship: human-agent\n",
            project_id=project_id,
        ):
            env, _volumes = build_task_env_and_volumes(load_project(project_id), task_id="1")
            assert env["TEROK_GIT_AUTHORSHIP"] == "human-agent"

    def test_task_run_cli_colors_login_lines_when_tty(self) -> None:
        project_id = "proj_cli_color"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ):
            task_new(project_id)
            with (
                mock_git_config(),
                unittest.mock.patch(
                    "terok.lib.containers.task_runners.stream_initial_logs",
                    return_value=True,
                ),
                unittest.mock.patch(
                    "terok.lib.containers.task_runners.get_container_state",
                    side_effect=[None, "running"],  # No existing container, then alive
                ),
                unittest.mock.patch("terok.lib.containers.task_runners.subprocess.run") as run_mock,
                unittest.mock.patch(
                    "terok.lib.containers.task_runners._supports_color",
                    return_value=True,
                ),
            ):
                run_mock.return_value = subprocess.CompletedProcess([], 0)
                buffer = StringIO()
                with redirect_stdout(buffer):
                    task_run_cli(project_id, "1")

            output = buffer.getvalue()
            expected_name = f"\x1b[32m{project_id}-cli-1\x1b[0m"
            expected_enter = f"\x1b[34mpodman exec -it {project_id}-cli-1 bash\x1b[0m"
            expected_stop = f"\x1b[31mpodman stop {project_id}-cli-1\x1b[0m"
            assert expected_name in output
            assert expected_enter in output
            assert expected_stop in output

    def test_task_run_cli_does_not_add_files_before_clone(self) -> None:
        """Interactive CLI startup must not add files to workspace before init clone."""
        project_id = "proj_cli_clean_workspace"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ) as ctx:
            task_new(project_id)
            workspace_dir = ctx.state_dir / "tasks" / project_id / "1" / "workspace-dangerous"
            assert sorted(p.name for p in workspace_dir.iterdir()) == [".new-task-marker"]
            with (
                mock_git_config(),
                unittest.mock.patch(
                    "terok.lib.containers.task_runners.stream_initial_logs",
                    return_value=True,
                ),
                unittest.mock.patch(
                    "terok.lib.containers.task_runners.get_container_state",
                    side_effect=[None, "running"],
                ),
                unittest.mock.patch("terok.lib.containers.task_runners.subprocess.run") as run_mock,
            ):
                run_mock.return_value = subprocess.CompletedProcess([], 0)
                task_run_cli(project_id, "1")

            assert sorted(p.name for p in workspace_dir.iterdir()) == [".new-task-marker"]
            assert (ctx.envs_dir / "_claude-config" / "settings.json").is_file()

    def test_task_run_toad_passes_public_url(self) -> None:
        """task_run_toad must pass --public-url with the host port to toad serve."""
        project_id = "proj_toad_url"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
            with_config_file=True,
            clear_env=True,
        ):
            task_new(project_id)
            with (
                mock_git_config(),
                unittest.mock.patch(
                    "terok.lib.containers.task_runners.stream_initial_logs",
                    return_value=True,
                ),
                unittest.mock.patch(
                    "terok.lib.containers.task_runners.get_container_state",
                    return_value=None,
                ),
                unittest.mock.patch(
                    "terok.lib.containers.task_runners.is_container_running",
                    return_value=True,
                ),
                unittest.mock.patch(
                    "terok.lib.containers.task_runners.assign_web_port",
                    return_value=7861,
                ),
                unittest.mock.patch("terok.lib.containers.task_runners.subprocess.run") as run_mock,
            ):
                run_mock.return_value = subprocess.CompletedProcess([], 0)
                task_run_toad(project_id, "1")

            cmd = run_mock.call_args[0][0]
            # The last element is the bash -lc command string
            bash_cmd = cmd[-1]
            assert "--public-url http://127.0.0.1:7861" in bash_cmd
            assert "-p 8080" in bash_cmd

            # Port forwarding maps host port to container toad port
            port_idx = cmd.index("-p")
            assert cmd[port_idx + 1] == "127.0.0.1:7861:8080"

    def test_task_run_cli_already_running(self) -> None:
        """task_run_cli prints message and exits when container is already running."""
        project_id = "proj_cli_running"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            task_new(project_id)
            with (
                mock_git_config(),
                unittest.mock.patch(
                    "terok.lib.containers.task_runners.get_container_state",
                    return_value="running",
                ),
                unittest.mock.patch("terok.lib.containers.task_runners.subprocess.run") as run_mock,
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    task_run_cli(project_id, "1")

                # Verify no podman run was called
                run_mock.assert_not_called()

                # Verify message indicates already running
                output = buffer.getvalue()
                assert "already running" in output

    def test_task_run_cli_starts_stopped_container(self) -> None:
        """task_run_cli uses 'podman start' for stopped container."""
        project_id = "proj_cli_stopped"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            task_new(project_id)
            meta_dir = ctx.state_dir / "projects" / project_id / "tasks"
            meta_path = meta_dir / "1.yml"

            # Simulate task was previously run
            meta = yaml.safe_load(meta_path.read_text())
            meta["mode"] = "cli"
            meta_path.write_text(yaml.safe_dump(meta))

            with (
                mock_git_config(),
                unittest.mock.patch(
                    "terok.lib.containers.task_runners.get_container_state",
                    side_effect=["exited", "running"],  # Stopped, then alive after start
                ),
                unittest.mock.patch("terok.lib.containers.task_runners.subprocess.run") as run_mock,
            ):
                run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0)
                buffer = StringIO()
                with redirect_stdout(buffer):
                    task_run_cli(project_id, "1")

                # Verify podman start was called
                run_mock.assert_called_once()
                call_args = run_mock.call_args[0][0]
                assert call_args[:2] == ["podman", "start"]

                # Verify metadata mode is preserved
                meta = yaml.safe_load(meta_path.read_text())
                assert meta["mode"] == "cli"

    def test_get_workspace_git_diff_no_workspace(self) -> None:
        """Test get_workspace_git_diff returns None when workspace doesn't exist."""
        project_id = "proj_diff_1"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            # Try to get diff for non-existent task
            result = get_workspace_git_diff(project_id, "999")
            assert result is None

    def test_get_workspace_git_diff_no_git_repo(self) -> None:
        """Test get_workspace_git_diff returns None when workspace is not a git repo."""
        project_id = "proj_diff_2"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            task_new(project_id)
            # Workspace exists but .git directory doesn't
            result = get_workspace_git_diff(project_id, "1")
            assert result is None

    def test_get_workspace_git_diff_clean_working_tree(self) -> None:
        """Test get_workspace_git_diff returns empty string for clean working tree."""
        project_id = "proj_diff_3"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            task_new(project_id)

            # Mock subprocess.run to simulate clean git repository
            with unittest.mock.patch("terok.lib.containers.tasks.subprocess.run") as run_mock:
                mock_result = unittest.mock.Mock()
                mock_result.returncode = 0
                mock_result.stdout = ""
                run_mock.return_value = mock_result

                # Also need to mock .git existence check
                workspace_dir = ctx.state_dir / "tasks" / project_id / "1" / "workspace-dangerous"
                git_dir = workspace_dir / ".git"
                git_dir.mkdir(parents=True, exist_ok=True)

                result = get_workspace_git_diff(project_id, "1")
                assert result == ""

    def test_get_workspace_git_diff_with_changes(self) -> None:
        """Test get_workspace_git_diff returns diff output when there are changes."""
        project_id = "proj_diff_4"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            task_new(project_id)

            expected_diff = "diff --git a/file.txt b/file.txt\n+new line\n"

            with (
                mock_git_config(),
                unittest.mock.patch("terok.lib.containers.tasks.subprocess.run") as run_mock,
            ):
                mock_result = unittest.mock.Mock()
                mock_result.returncode = 0
                mock_result.stdout = expected_diff
                run_mock.return_value = mock_result

                workspace_dir = ctx.state_dir / "tasks" / project_id / "1" / "workspace-dangerous"
                git_dir = workspace_dir / ".git"
                git_dir.mkdir(parents=True, exist_ok=True)

                result = get_workspace_git_diff(project_id, "1", "HEAD")
                assert result == expected_diff

                # Verify git diff command was called correctly
                run_mock.assert_called_once()
                call_args = run_mock.call_args[0][0]
                assert call_args[0] == "git"
                assert call_args[1] == "-C"
                assert call_args[3] == "diff"
                assert call_args[4] == "HEAD"

    def test_get_workspace_git_diff_prev_commit(self) -> None:
        """Test get_workspace_git_diff with PREV option."""
        project_id = "proj_diff_5"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            task_new(project_id)

            expected_diff = "diff --git a/file.txt b/file.txt\n+previous commit change\n"

            with (
                mock_git_config(),
                unittest.mock.patch("terok.lib.containers.tasks.subprocess.run") as run_mock,
            ):
                mock_result = unittest.mock.Mock()
                mock_result.returncode = 0
                mock_result.stdout = expected_diff
                run_mock.return_value = mock_result

                workspace_dir = ctx.state_dir / "tasks" / project_id / "1" / "workspace-dangerous"
                git_dir = workspace_dir / ".git"
                git_dir.mkdir(parents=True, exist_ok=True)

                result = get_workspace_git_diff(project_id, "1", "PREV")
                assert result == expected_diff

                # Verify git command was called with HEAD~1
                run_mock.assert_called_once()
                call_args = run_mock.call_args[0][0]
                assert call_args[0] == "git"
                assert call_args[1] == "-C"
                assert call_args[3] == "diff"
                assert call_args[4] == "HEAD~1"
                assert call_args[5] == "HEAD"

    def test_get_workspace_git_diff_error(self) -> None:
        """Test get_workspace_git_diff returns None when git command fails."""
        project_id = "proj_diff_6"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ) as ctx:
            task_new(project_id)

            with unittest.mock.patch("terok.lib.containers.tasks.subprocess.run") as run_mock:
                # Simulate git command failure
                mock_result = unittest.mock.Mock()
                mock_result.returncode = 1
                run_mock.return_value = mock_result

                workspace_dir = ctx.state_dir / "tasks" / project_id / "1" / "workspace-dangerous"
                git_dir = workspace_dir / ".git"
                git_dir.mkdir(parents=True, exist_ok=True)

                result = get_workspace_git_diff(project_id, "1")
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
                    assert kwargs["capture_output"]

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

    @unittest.mock.patch("terok.lib.containers.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.containers.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch(
        "terok.lib.security.gate_tokens.create_token", return_value="tok" * 10 + "ab"
    )
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

    @unittest.mock.patch("terok.lib.containers.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.containers.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch(
        "terok.lib.security.gate_tokens.create_token", return_value="tok" * 10 + "ab"
    )
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

    @unittest.mock.patch("terok.lib.containers.environment.ensure_server_reachable")
    @unittest.mock.patch(
        "terok.lib.containers.environment.get_gate_server_port",
        return_value=GATE_PORT,
    )
    @unittest.mock.patch(
        "terok.lib.security.gate_tokens.create_token", return_value="tok" * 10 + "ab"
    )
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


class TestTaskLogs:
    """Tests for task_logs() function."""

    def _setup_task_with_mode(self, project_id, mode="run"):
        """Create a task and set its mode in metadata."""
        task_id = task_new(project_id)
        # Manually update metadata to set mode (normally done by task runners)
        from terok.lib.core.config import state_root

        meta_dir = state_root() / "projects" / project_id / "tasks"
        meta_path = meta_dir / f"{task_id}.yml"
        meta = yaml.safe_load(meta_path.read_text()) or {}
        meta["mode"] = mode
        meta_path.write_text(yaml.safe_dump(meta))
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

    def test_container_not_found_raises(self) -> None:
        """task_logs raises SystemExit when container doesn't exist."""
        with project_env(
            "project:\n  id: proj_logs3\n",
            project_id="proj_logs3",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs3", "run")
                with unittest.mock.patch(
                    "terok.lib.containers.task_logs.get_container_state", return_value=None
                ):
                    with pytest.raises(SystemExit) as cm:
                        task_logs("proj_logs3", task_id)
                    assert "does not exist" in str(cm.value)

    def test_negative_tail_raises(self) -> None:
        """task_logs raises SystemExit for negative tail value."""
        with project_env(
            "project:\n  id: proj_logs4\n",
            project_id="proj_logs4",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs4", "run")
                with unittest.mock.patch(
                    "terok.lib.containers.task_logs.get_container_state",
                    return_value="running",
                ):
                    with pytest.raises(SystemExit) as cm:
                        task_logs("proj_logs4", task_id, LogViewOptions(tail=-1))
                    assert "--tail must be >= 0" in str(cm.value)

    def test_raw_mode_exec(self) -> None:
        """task_logs in raw mode calls os.execvp."""
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

                with (
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.get_container_state",
                        return_value="exited",
                    ),
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.os.execvp", side_effect=fake_execvp
                    ),
                ):
                    with pytest.raises(SystemExit):
                        task_logs("proj_logs5", task_id, LogViewOptions(raw=True))
                    assert len(captured_args) == 1
                    assert captured_args[0][0] == "podman"
                    assert "logs" in captured_args[0][1]

    def test_raw_mode_podman_not_found(self) -> None:
        """task_logs in raw mode raises SystemExit if podman not found."""
        with project_env(
            "project:\n  id: proj_logs6\n",
            project_id="proj_logs6",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs6", "cli")
                with (
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.get_container_state",
                        return_value="exited",
                    ),
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.os.execvp",
                        side_effect=FileNotFoundError("podman"),
                    ),
                ):
                    with pytest.raises(SystemExit) as cm:
                        task_logs("proj_logs6", task_id, LogViewOptions(raw=True))
                    assert "podman not found" in str(cm.value)

    def test_formatted_mode_feeds_formatter(self) -> None:
        """task_logs in formatted mode pipes lines through formatter."""
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
                        "terok.lib.containers.task_logs.get_container_state",
                        return_value="exited",
                    ),
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.subprocess.Popen",
                        return_value=mock_proc,
                    ),
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.auto_detect_formatter",
                        return_value=mock_formatter,
                    ),
                    unittest.mock.patch("select.select") as mock_select,
                ):
                    mock_select.return_value = ([mock_proc.stdout], [], [])
                    buf = StringIO()
                    with redirect_stdout(buf):
                        task_logs("proj_logs7", task_id)

                    mock_formatter.feed_line.assert_called()
                    mock_formatter.finish.assert_called_once()

    def test_formatted_mode_podman_not_found(self) -> None:
        """task_logs in formatted mode raises SystemExit if podman not found."""
        with project_env(
            "project:\n  id: proj_logs8\n",
            project_id="proj_logs8",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs8", "run")
                with (
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.get_container_state",
                        return_value="running",
                    ),
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.subprocess.Popen",
                        side_effect=FileNotFoundError("podman"),
                    ),
                ):
                    with pytest.raises(SystemExit) as cm:
                        task_logs("proj_logs8", task_id)
                    assert "podman not found" in str(cm.value)

    def test_persisted_logs_fallback(self) -> None:
        """task_logs falls back to persisted log file when container is gone."""
        with project_env(
            "project:\n  id: proj_logs_persist\n",
            project_id="proj_logs_persist",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs_persist", "run")

                # Create persisted log file
                from terok.lib.core.config import state_root

                task_dir = Path(state_root()) / "tasks" / "proj_logs_persist" / task_id
                logs_dir = task_dir / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                log_file = logs_dir / "container.log"
                log_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

                mock_formatter = unittest.mock.Mock()

                with (
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.get_container_state",
                        return_value=None,
                    ),
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.auto_detect_formatter",
                        return_value=mock_formatter,
                    ),
                ):
                    buf = StringIO()
                    with redirect_stdout(buf):
                        task_logs("proj_logs_persist", task_id)

                    # Formatter should have been fed 3 lines
                    assert mock_formatter.feed_line.call_count == 3
                    mock_formatter.finish.assert_called_once()

    def test_persisted_logs_fallback_with_tail(self) -> None:
        """task_logs persisted fallback respects --tail option."""
        with project_env(
            "project:\n  id: proj_logs_tail\n",
            project_id="proj_logs_tail",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs_tail", "run")

                from terok.lib.core.config import state_root

                task_dir = Path(state_root()) / "tasks" / "proj_logs_tail" / task_id
                logs_dir = task_dir / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                log_file = logs_dir / "container.log"
                log_file.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

                mock_formatter = unittest.mock.Mock()

                with (
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.get_container_state",
                        return_value=None,
                    ),
                    unittest.mock.patch(
                        "terok.lib.containers.task_logs.auto_detect_formatter",
                        return_value=mock_formatter,
                    ),
                ):
                    task_logs("proj_logs_tail", task_id, LogViewOptions(tail=2))
                    assert mock_formatter.feed_line.call_count == 2

    def test_no_container_no_logs_raises(self) -> None:
        """task_logs raises when container is gone and no persisted logs exist."""
        with project_env(
            "project:\n  id: proj_logs_nolog\n",
            project_id="proj_logs_nolog",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs_nolog", "run")
                with unittest.mock.patch(
                    "terok.lib.containers.task_logs.get_container_state",
                    return_value=None,
                ):
                    with pytest.raises(SystemExit) as cm:
                        task_logs("proj_logs_nolog", task_id)
                    assert "no persisted logs found" in str(cm.value)

    def test_negative_tail_persisted_fallback_raises(self) -> None:
        """task_logs raises for negative tail even when falling back to persisted logs."""
        with project_env(
            "project:\n  id: proj_logs_negtail\n",
            project_id="proj_logs_negtail",
        ):
            with mock_git_config():
                task_id = self._setup_task_with_mode("proj_logs_negtail", "run")

                from terok.lib.core.config import state_root

                task_dir = Path(state_root()) / "tasks" / "proj_logs_negtail" / task_id
                logs_dir = task_dir / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                (logs_dir / "container.log").write_text("a\nb\n")

                with unittest.mock.patch(
                    "terok.lib.containers.task_logs.get_container_state",
                    return_value=None,
                ):
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
                meta = yaml.safe_load(meta_path.read_text()) or {}
                meta["mode"] = "run"
                meta["name"] = "test-task"
                meta["exit_code"] = 0
                meta_path.write_text(yaml.safe_dump(meta))

                # Create logs dir to simulate persisted logs
                task_dir = ctx.state_dir / "tasks" / project_id / task_id
                logs_dir = task_dir / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                (logs_dir / "container.log").write_text("log content\n")

                log_content = b"captured log output\n"

                def fake_run(cmd, *, stdout=None, stderr=None, timeout=None, **kw):
                    """Simulate podman: write to stdout file handle or no-op for rm."""
                    if stdout is not None and hasattr(stdout, "write"):
                        stdout.write(log_content)
                    result = unittest.mock.Mock()
                    result.returncode = 0
                    return result

                with unittest.mock.patch(
                    "terok.lib.containers.tasks.subprocess.run",
                    side_effect=fake_run,
                ):
                    task_delete(project_id, task_id)

                # Task should be deleted
                assert not meta_path.exists()

                # Archive should exist
                archive_dir = ctx.state_dir / "projects" / project_id / "archive"
                assert archive_dir.is_dir()
                archives = list(archive_dir.iterdir())
                assert len(archives) == 1

                archive_entry = archives[0]
                # Archive dir name contains task_id and name
                assert task_id in archive_entry.name
                assert "test-task" in archive_entry.name

                # Archive should contain task.yml
                archived_meta = archive_entry / "task.yml"
                assert archived_meta.is_file()
                archived_data = yaml.safe_load(archived_meta.read_text())
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
        ) as ctx:
            with mock_git_config():
                task_id = task_new(project_id)

                def fake_run(cmd, *, stdout=None, stderr=None, timeout=None, **kw):
                    """No-op mock for subprocess.run."""
                    result = unittest.mock.Mock()
                    result.returncode = 0
                    return result

                with unittest.mock.patch(
                    "terok.lib.containers.tasks.subprocess.run",
                    side_effect=fake_run,
                ):
                    task_delete(project_id, task_id)

                archive_dir = ctx.state_dir / "projects" / project_id / "archive"
                assert archive_dir.is_dir()
                archives = list(archive_dir.iterdir())
                assert len(archives) == 1

                # Should have task.yml but no logs subdir
                archive_entry = archives[0]
                assert (archive_entry / "task.yml").is_file()
                assert not (archive_entry / "logs").exists()

    def test_list_archived_tasks(self) -> None:
        """list_archived_tasks returns archived tasks sorted newest-first."""
        from terok.lib.containers.tasks import list_archived_tasks, tasks_archive_dir

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
                    yaml.safe_dump(
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
        from terok.lib.containers.tasks import task_archive_logs, tasks_archive_dir

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
        from terok.lib.containers.tasks import task_archive_list

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
        """capture_task_logs writes podman logs to host filesystem."""
        from terok.lib.containers.tasks import capture_task_logs

        project_id = "proj_capture1"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            with mock_git_config():
                task_id = task_new(project_id)

                log_content = b"2026-03-05T12:00:00Z stdout line\n"

                def fake_run(cmd, *, stdout=None, stderr=None, timeout=None):
                    """Write log content to stdout file handle like podman would."""
                    if stdout is not None and hasattr(stdout, "write"):
                        stdout.write(log_content)
                    result = unittest.mock.Mock()
                    result.returncode = 0
                    return result

                with unittest.mock.patch(
                    "terok.lib.containers.tasks.subprocess.run",
                    side_effect=fake_run,
                ):
                    log_file = capture_task_logs(project_id, task_id, "run")

                assert log_file is not None
                content = log_file.read_text()
                assert "stdout line" in content

    def test_capture_task_logs_podman_not_found(self) -> None:
        """capture_task_logs returns None when podman is not available."""
        from terok.lib.containers.tasks import capture_task_logs

        project_id = "proj_capture2"
        with project_env(
            f"project:\n  id: {project_id}\n",
            project_id=project_id,
        ):
            with mock_git_config():
                task_id = task_new(project_id)

                with unittest.mock.patch(
                    "terok.lib.containers.tasks.subprocess.run",
                    side_effect=FileNotFoundError("podman"),
                ):
                    result = capture_task_logs(project_id, task_id, "run")

                assert result is None
