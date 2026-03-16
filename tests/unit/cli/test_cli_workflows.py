# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for higher-level CLI workflow shortcuts."""

from __future__ import annotations

import unittest.mock
from collections.abc import Callable

import pytest

from tests.testfs import FAKE_GATE_DIR


def _patch_init_steps[T](func: Callable[..., T]) -> Callable[..., T]:
    """Apply project-init step mocks to a test method.

    Mock args are injected as: mock_ssh_cls, mock_pause, mock_gen, mock_build, mock_gate_cls,
    mock_load.
    """
    func = unittest.mock.patch("terok.cli.commands.setup.SSHManager")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.maybe_pause_for_ssh_key_registration")(
        func
    )
    func = unittest.mock.patch("terok.cli.commands.setup.generate_dockerfiles")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.build_images")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.GitGate")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.load_project")(func)
    return func


def run_main(argv: list[str]) -> None:
    """Run the CLI entrypoint with a patched ``sys.argv``."""
    from terok.cli.main import main

    with unittest.mock.patch("sys.argv", argv):
        main()


class TestProjectInit:
    """Tests for the project-init convenience command."""

    @_patch_init_steps
    def test_cmd_project_init_calls_four_steps(
        self, mock_ssh_cls, mock_pause, mock_gen, mock_build, mock_gate_cls, mock_load
    ) -> None:
        mock_gate_cls.return_value.sync.return_value = {"success": True, "path": str(FAKE_GATE_DIR)}

        from terok.cli.commands.setup import cmd_project_init

        cmd_project_init("myproj")

        mock_ssh_cls.return_value.init.assert_called_once()
        mock_pause.assert_called_once_with("myproj")
        mock_gen.assert_called_once_with("myproj")
        mock_build.assert_called_once_with("myproj")
        mock_gate_cls.return_value.sync.assert_called_once()

    @_patch_init_steps
    def test_cmd_project_init_calls_in_order(
        self, mock_ssh_cls, mock_pause, mock_gen, mock_build, mock_gate_cls, mock_load
    ) -> None:
        call_order: list[str] = []
        mock_ssh_cls.return_value.init.side_effect = lambda **kw: call_order.append("ssh")
        mock_pause.side_effect = lambda *a, **kw: call_order.append("pause")
        mock_gen.side_effect = lambda *a, **kw: call_order.append("generate")
        mock_build.side_effect = lambda *a, **kw: call_order.append("build")
        mock_gate_cls.return_value.sync.side_effect = lambda **kw: (
            call_order.append("gate"),
            {"success": True, "path": str(FAKE_GATE_DIR)},
        )[-1]

        from terok.cli.commands.setup import cmd_project_init

        cmd_project_init("proj1")

        assert call_order == ["ssh", "pause", "generate", "build", "gate"]

    @_patch_init_steps
    def test_cmd_project_init_gate_failure_raises(
        self, mock_ssh_cls, mock_pause, mock_gen, mock_build, mock_gate_cls, mock_load
    ) -> None:
        mock_gate_cls.return_value.sync.return_value = {
            "success": False,
            "errors": ["no upstream_url"],
        }

        from terok.cli.commands.setup import cmd_project_init

        with pytest.raises(SystemExit, match="Gate sync failed"):
            cmd_project_init("badproj")


class TestSshPause:
    """Tests for the SSH key registration pause helper."""

    @unittest.mock.patch("terok.lib.facade.load_project")
    @unittest.mock.patch("builtins.input", return_value="")
    def test_pauses_for_ssh_upstream(self, mock_input, mock_load) -> None:
        from terok.lib.facade import maybe_pause_for_ssh_key_registration

        for upstream in ("git@github.com:org/repo.git", "ssh://github.com/org/repo.git"):
            mock_input.reset_mock()
            mock_load.return_value = unittest.mock.Mock(upstream_url=upstream)
            maybe_pause_for_ssh_key_registration("sshproj")
            mock_input.assert_called_once_with("Press Enter once the key is registered... ")

    @unittest.mock.patch("terok.lib.facade.load_project")
    @unittest.mock.patch("builtins.input", return_value="")
    def test_no_pause_for_https_upstream(self, mock_input, mock_load) -> None:
        from terok.lib.facade import maybe_pause_for_ssh_key_registration

        mock_load.return_value = unittest.mock.Mock(upstream_url="https://github.com/org/repo.git")
        maybe_pause_for_ssh_key_registration("httpsproj")
        mock_input.assert_not_called()

    @_patch_init_steps
    def test_project_init_continues_after_pause(
        self, mock_ssh_cls, mock_pause, mock_gen, mock_build, mock_gate_cls, mock_load
    ) -> None:
        mock_gate_cls.return_value.sync.return_value = {"success": True, "path": str(FAKE_GATE_DIR)}

        from terok.cli.commands.setup import cmd_project_init

        cmd_project_init("sshproj")

        mock_ssh_cls.return_value.init.assert_called_once()
        mock_pause.assert_called_once_with("sshproj")
        mock_gen.assert_called_once_with("sshproj")
        mock_build.assert_called_once_with("sshproj")
        mock_gate_cls.return_value.sync.assert_called_once()


class TestTaskStart:
    """Tests for task-start and related shorthand commands."""

    @pytest.mark.parametrize(
        ("argv", "task_id", "runner_path", "expected_call"),
        [
            (
                ["terok", "task", "start", "proj1"],
                "42",
                "terok.cli.commands.task.task_run_cli",
                ("proj1", "42", {"agents": None, "preset": None, "unrestricted": None}),
            ),
            (
                ["terok", "task", "start", "proj1", "--toad"],
                "10",
                "terok.cli.commands.task.task_run_toad",
                ("proj1", "10", {"agents": None, "preset": None, "unrestricted": None}),
            ),
        ],
        ids=["cli-mode", "toad-mode"],
    )
    def test_task_start_dispatch(
        self,
        argv: list[str],
        task_id: str,
        runner_path: str,
        expected_call: tuple[str, str, dict[str, object]],
    ) -> None:
        with (
            unittest.mock.patch(
                "terok.cli.commands.task.task_new", return_value=task_id
            ) as mock_new,
            unittest.mock.patch(runner_path) as mock_runner,
        ):
            run_main(argv)
        project_id, expected_task_id, kwargs = expected_call
        mock_new.assert_called_once_with(project_id, name=None)
        mock_runner.assert_called_once_with(project_id, expected_task_id, **kwargs)

    @pytest.mark.parametrize(
        ("argv", "patch_target", "expected_call"),
        [
            (
                ["terok", "project-init", "myproj"],
                "terok.cli.commands.setup.cmd_project_init",
                ("myproj",),
            ),
            (
                ["terok", "login", "proj1", "1"],
                "terok.cli.commands.task.task_login",
                ("proj1", "1"),
            ),
        ],
        ids=["project-init-dispatch", "login-dispatch"],
    )
    def test_simple_dispatch_commands(
        self,
        argv: list[str],
        patch_target: str,
        expected_call: tuple[str, ...],
    ) -> None:
        with unittest.mock.patch(patch_target) as mock_fn:
            run_main(argv)
        mock_fn.assert_called_once_with(*expected_call)
