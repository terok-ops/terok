# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for higher-level CLI workflow shortcuts."""

from __future__ import annotations

import unittest.mock
from collections.abc import Callable

import pytest

from tests.testfs import FAKE_GATE_DIR


@pytest.fixture(autouse=True)
def _bypass_setup_verdict_gate():
    """Skip the stamp-based gate — covered separately in ``test_cli_task_verdict_gate.py``.

    Workflow tests assert the command-dispatch shape downstream of the
    gate; they run in a stamp-free tmp env where the real gate would
    always raise exit 3 before dispatch ever happens.
    """
    with unittest.mock.patch("terok.cli.commands.task._setup_verdict_or_exit"):
        yield


def _patch_init_steps[T](func: Callable[..., T]) -> Callable[..., T]:
    """Apply project-init step mocks to a test method.

    Mock args are injected as: mock_provision, mock_summarize, mock_pause, mock_gen,
    mock_build, mock_gate_cls, mock_load.
    """
    func = unittest.mock.patch("terok.cli.commands.setup.provision_ssh_key")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.summarize_ssh_init")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.maybe_pause_for_ssh_key_registration")(
        func
    )
    func = unittest.mock.patch("terok.cli.commands.setup.generate_dockerfiles")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.build_images")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.make_git_gate")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.load_project")(func)
    return func


def run_main(argv: list[str]) -> None:
    """Run the CLI entrypoint with a patched ``sys.argv``."""
    from terok.cli.main import main

    with unittest.mock.patch("sys.argv", argv):
        main()


_FAKE_SSH_INIT_RESULT = {
    "key_id": 42,
    "key_type": "ed25519",
    "fingerprint": "fp",
    "comment": "c",
    "public_line": "ssh-ed25519 AAAA c",
}


class TestProjectInit:
    """Tests for the project-init convenience command."""

    @_patch_init_steps
    def test_cmd_project_init_calls_four_steps(
        self,
        mock_provision,
        mock_summarize,
        mock_pause,
        mock_gen,
        mock_build,
        mock_gate_cls,
        mock_load,
    ) -> None:
        mock_gate_cls.return_value.sync.return_value = {"success": True, "path": str(FAKE_GATE_DIR)}
        mock_provision.return_value = _FAKE_SSH_INIT_RESULT

        from terok.cli.commands.setup import cmd_project_init

        cmd_project_init("myproj")

        mock_provision.assert_called_once_with("myproj")
        mock_summarize.assert_called_once_with(_FAKE_SSH_INIT_RESULT)
        mock_pause.assert_called_once_with("myproj")
        mock_gen.assert_called_once_with("myproj")
        mock_build.assert_called_once_with("myproj")
        mock_gate_cls.return_value.sync.assert_called_once()

    @_patch_init_steps
    def test_cmd_project_init_calls_in_order(
        self,
        mock_provision,
        mock_summarize,
        mock_pause,
        mock_gen,
        mock_build,
        mock_gate_cls,
        mock_load,
    ) -> None:
        call_order: list[str] = []
        mock_provision.side_effect = lambda *a, **kw: (
            call_order.append("ssh"),
            _FAKE_SSH_INIT_RESULT,
        )[-1]
        mock_summarize.side_effect = lambda *a, **kw: call_order.append("summarize")
        mock_pause.side_effect = lambda *a, **kw: call_order.append("pause")
        mock_gen.side_effect = lambda *a, **kw: call_order.append("generate")
        mock_build.side_effect = lambda *a, **kw: call_order.append("build")
        mock_gate_cls.return_value.sync.side_effect = lambda **kw: (
            call_order.append("gate"),
            {"success": True, "path": str(FAKE_GATE_DIR)},
        )[-1]

        from terok.cli.commands.setup import cmd_project_init

        cmd_project_init("proj1")

        assert call_order == ["ssh", "summarize", "pause", "generate", "build", "gate"]

    @_patch_init_steps
    def test_cmd_project_init_gate_failure_raises(
        self,
        mock_provision,
        mock_summarize,
        mock_pause,
        mock_gen,
        mock_build,
        mock_gate_cls,
        mock_load,
    ) -> None:
        mock_provision.return_value = _FAKE_SSH_INIT_RESULT
        mock_gate_cls.return_value.sync.return_value = {
            "success": False,
            "errors": ["no upstream_url"],
        }

        from terok.cli.commands.setup import cmd_project_init

        with pytest.raises(SystemExit, match="Gate sync failed"):
            cmd_project_init("badproj")

    @_patch_init_steps
    def test_cmd_project_init_skips_gate_sync_when_disabled(
        self,
        mock_provision,
        mock_summarize,
        mock_pause,
        mock_gen,
        mock_build,
        mock_gate_cls,
        mock_load,
    ) -> None:
        """``gate.enabled: false`` short-circuits after build — no sync attempted."""
        mock_provision.return_value = _FAKE_SSH_INIT_RESULT
        mock_load.return_value = unittest.mock.Mock(gate_enabled=False)

        from terok.cli.commands.setup import cmd_project_init

        cmd_project_init("noproj")

        mock_build.assert_called_once_with("noproj")
        mock_gate_cls.assert_not_called()
        mock_gate_cls.return_value.sync.assert_not_called()


class TestCliSshInit:
    """Tests for the ``project ssh-init`` CLI command."""

    @unittest.mock.patch("terok.cli.commands.project.summarize_ssh_init")
    @unittest.mock.patch("terok.cli.commands.project.provision_ssh_key")
    def test_ssh_init_delegates_to_facade(self, mock_provision, mock_summarize) -> None:
        """dispatch → provision_ssh_key(project_id, **defaults) → summarize_ssh_init(result)."""
        import argparse

        mock_provision.return_value = _FAKE_SSH_INIT_RESULT

        from terok.cli.commands.project import dispatch

        args = argparse.Namespace(
            cmd="project",
            project_cmd="ssh-init",
            project_id="proj",
            key_type="ed25519",
            comment=None,
            force=False,
        )
        assert dispatch(args) is True
        mock_provision.assert_called_once_with(
            "proj", key_type="ed25519", comment=None, force=False
        )
        mock_summarize.assert_called_once_with(_FAKE_SSH_INIT_RESULT)


class TestSshPause:
    """Tests for the SSH key registration pause helper."""

    @unittest.mock.patch("terok.lib.domain.facade.load_project")
    @unittest.mock.patch("builtins.input", return_value="")
    def test_pauses_for_ssh_upstream(self, mock_input, mock_load) -> None:
        from terok.lib.domain.facade import maybe_pause_for_ssh_key_registration

        for upstream in ("git@github.com:org/repo.git", "ssh://github.com/org/repo.git"):
            mock_input.reset_mock()
            mock_load.return_value = unittest.mock.Mock(upstream_url=upstream)
            maybe_pause_for_ssh_key_registration("sshproj")
            mock_input.assert_called_once_with("Press Enter once the key is registered... ")

    @unittest.mock.patch("terok.lib.domain.facade.load_project")
    @unittest.mock.patch("builtins.input", return_value="")
    def test_no_pause_for_https_upstream(self, mock_input, mock_load) -> None:
        from terok.lib.domain.facade import maybe_pause_for_ssh_key_registration

        mock_load.return_value = unittest.mock.Mock(upstream_url="https://github.com/org/repo.git")
        maybe_pause_for_ssh_key_registration("httpsproj")
        mock_input.assert_not_called()

    @_patch_init_steps
    def test_project_init_continues_after_pause(
        self,
        mock_provision,
        mock_summarize,
        mock_pause,
        mock_gen,
        mock_build,
        mock_gate_cls,
        mock_load,
    ) -> None:
        mock_gate_cls.return_value.sync.return_value = {"success": True, "path": str(FAKE_GATE_DIR)}
        mock_provision.return_value = _FAKE_SSH_INIT_RESULT

        from terok.cli.commands.setup import cmd_project_init

        cmd_project_init("sshproj")

        mock_provision.assert_called_once_with("sshproj")
        mock_summarize.assert_called_once_with(_FAKE_SSH_INIT_RESULT)
        mock_pause.assert_called_once_with("sshproj")
        mock_gen.assert_called_once_with("sshproj")
        mock_build.assert_called_once_with("sshproj")
        mock_gate_cls.return_value.sync.assert_called_once()


class TestTaskRunInteractive:
    """``task run --mode cli|toad`` creates a new task and invokes the runner."""

    @pytest.mark.parametrize(
        ("argv", "task_id", "runner_path", "expected_call"),
        [
            (
                ["terok", "task", "run", "proj1"],
                "42",
                "terok.cli.commands.task.task_run_cli",
                ("proj1", "42", {"agents": None, "preset": None, "unrestricted": None}),
            ),
            (
                ["terok", "task", "run", "proj1", "--mode", "toad"],
                "10",
                "terok.cli.commands.task.task_run_toad",
                ("proj1", "10", {"agents": None, "preset": None, "unrestricted": None}),
            ),
        ],
        ids=["default-cli-mode", "toad-mode"],
    )
    def test_task_run_interactive_dispatch(
        self,
        argv: list[str],
        task_id: str,
        runner_path: str,
        expected_call: tuple[str, str, dict[str, object]],
    ) -> None:
        # --no-attach keeps the CLI test deterministic regardless of TTY
        # state in the pytest harness.
        argv = [*argv, "--no-attach"]
        with (
            unittest.mock.patch("terok.cli.commands.task.project_image_exists", return_value=True),
            unittest.mock.patch(
                "terok.cli.commands.task.task_new", return_value=task_id
            ) as mock_new,
            unittest.mock.patch("terok.cli.commands.task.task_login") as mock_task_login,
            unittest.mock.patch(runner_path) as mock_runner,
        ):
            run_main(argv)
        project_id, expected_task_id, kwargs = expected_call
        mock_new.assert_called_once_with(project_id, name=None)
        mock_runner.assert_called_once_with(project_id, expected_task_id, **kwargs)
        # --no-attach must suppress the login exec in every interactive mode.
        mock_task_login.assert_not_called()

    @pytest.mark.parametrize(
        ("argv", "patch_target", "expected_call"),
        [
            (
                ["terok", "project", "init", "myproj"],
                "terok.cli.commands.project.cmd_project_init",
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
        with (
            unittest.mock.patch(
                "terok.cli.commands.task.resolve_task_id", side_effect=lambda _pid, tid: tid
            ),
            unittest.mock.patch(patch_target) as mock_fn,
        ):
            run_main(argv)
        mock_fn.assert_called_once_with(*expected_call)
