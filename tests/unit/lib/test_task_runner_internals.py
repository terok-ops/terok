# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for task_runners internal helpers and the _run_container delegation.

Covers the utility functions (_str_to_bool, _podman_start, _apply_shield_policy)
and the RunSpec delegation path through _run_container.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from terok.lib.orchestration.task_runners import (
    _apply_unrestricted_env,
    _run_container,
    _str_to_bool,
)
from tests.testfs import MOCK_TASK_DIR

# ── _str_to_bool ─────────────────────────────────────────


class TestStrToBool:
    """Verify strict config-value coercion."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, True),
            (False, False),
            ("true", True),
            ("True", True),
            ("yes", True),
            ("1", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("off", False),
            ("OFF", False),
            (1, True),
            (0, False),
        ],
        ids=[
            "bool-true",
            "bool-false",
            "str-true",
            "str-True",
            "str-yes",
            "str-1",
            "str-false",
            "str-False",
            "str-0",
            "str-no",
            "str-off",
            "str-OFF",
            "int-1",
            "int-0",
        ],
    )
    def test_coercion(self, value: object, expected: bool) -> None:
        """Each value coerces to the expected boolean."""
        assert _str_to_bool(value) is expected


# ── _podman_start ─────────────────────────────────────────


class TestPodmanStart:
    """Verify _podman_start error handling."""

    _PATCH = "terok.lib.orchestration.task_runners.container_start"

    def test_raises_on_missing_podman(self) -> None:
        """FileNotFoundError becomes SystemExit with install hint."""
        from terok.lib.orchestration.task_runners import _podman_start

        with (
            patch(self._PATCH, side_effect=FileNotFoundError),
            pytest.raises(SystemExit, match="podman not found"),
        ):
            _podman_start("test-ctr")

    def test_raises_on_start_failure(self) -> None:
        """Non-zero returncode with stderr becomes SystemExit."""
        from terok.lib.orchestration.task_runners import _podman_start

        result = subprocess.CompletedProcess(args=[], returncode=1, stderr="container not found")
        with (
            patch(self._PATCH, return_value=result),
            pytest.raises(SystemExit, match="container not found"),
        ):
            _podman_start("test-ctr")

    def test_raises_on_start_failure_empty_stderr(self) -> None:
        """Non-zero returncode with empty stderr still raises SystemExit."""
        from terok.lib.orchestration.task_runners import _podman_start

        result = subprocess.CompletedProcess(args=[], returncode=125, stderr="")
        with (
            patch(self._PATCH, return_value=result),
            pytest.raises(SystemExit),
        ):
            _podman_start("test-ctr")


# ── _apply_shield_policy ─────────────────────────────────


class TestApplyShieldPolicy:
    """Verify shield policy logic for creation and restart."""

    def _make_project(self, *, drop: bool = True, on_restart: str = "retain") -> MagicMock:
        """Return a mock ProjectConfig with shield fields set."""
        p = MagicMock()
        p.shield_drop_on_task_run = drop
        p.shield_on_task_restart = on_restart
        return p

    def test_fresh_skips_when_drop_disabled(self, tmp_path: Path) -> None:
        """No shield_down call when drop_on_task_run is False."""
        from terok.lib.orchestration.task_runners import _apply_shield_policy

        project = self._make_project(drop=False)
        with patch(
            "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
            return_value=False,
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=False)
        assert (tmp_path / "shield_desired_state").read_text().strip() == "up"

    def test_fresh_drops_and_persists(self, tmp_path: Path) -> None:
        """Fresh creation with drop=True calls shield_down and writes state."""
        from terok.lib.orchestration.task_runners import _apply_shield_policy

        project = self._make_project(drop=True)
        with (
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners._shield_down_impl") as mock_down,
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=False)
        mock_down.assert_called_once_with("ctr", tmp_path)
        assert (tmp_path / "shield_desired_state").read_text().strip() == "down"

    def test_skips_when_bypass_active(self) -> None:
        """No-op when shield bypass is globally active."""
        from terok.lib.orchestration.task_runners import _apply_shield_policy

        project = self._make_project(drop=True)
        with patch(
            "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
            return_value=True,
        ):
            _apply_shield_policy(project, "ctr", MOCK_TASK_DIR, is_restart=False)

    def test_restart_retain_restores_down(self, tmp_path: Path) -> None:
        """Restart with retain policy restores a saved 'down' state."""
        from terok.lib.orchestration.task_runners import _apply_shield_policy

        (tmp_path / "shield_desired_state").write_text("down\n")
        project = self._make_project(on_restart="retain")
        with (
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners._shield_down_impl") as mock_down,
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)
        mock_down.assert_called_once_with("ctr", tmp_path, allow_all=False)

    def test_restart_retain_restores_down_all(self, tmp_path: Path) -> None:
        """Restart with retain policy restores a saved 'down_all' state."""
        from terok.lib.orchestration.task_runners import _apply_shield_policy

        (tmp_path / "shield_desired_state").write_text("down_all\n")
        project = self._make_project(on_restart="retain")
        with (
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners._shield_down_impl") as mock_down,
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)
        mock_down.assert_called_once_with("ctr", tmp_path, allow_all=True)

    def test_restart_retain_noop_when_up(self, tmp_path: Path) -> None:
        """Restart with retain + saved 'up' does nothing (hook already applied UP)."""
        from terok.lib.orchestration.task_runners import _apply_shield_policy

        (tmp_path / "shield_desired_state").write_text("up\n")
        project = self._make_project(on_restart="retain")
        with (
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners._shield_down_impl") as mock_down,
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)
        mock_down.assert_not_called()

    def test_restart_up_policy_noop(self, tmp_path: Path) -> None:
        """Restart with 'up' policy never calls shield_down."""
        from terok.lib.orchestration.task_runners import _apply_shield_policy

        (tmp_path / "shield_desired_state").write_text("down\n")
        project = self._make_project(on_restart="up")
        with (
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners._shield_down_impl") as mock_down,
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)
        mock_down.assert_not_called()

    def test_warns_on_failure(self) -> None:
        """Emits a warning when shield_down raises during fresh creation."""
        from terok.lib.orchestration.task_runners import _apply_shield_policy

        project = self._make_project(drop=True)
        with (
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch(
                "terok.lib.orchestration.task_runners._shield_down_impl",
                side_effect=RuntimeError("nft missing"),
            ),
            pytest.warns(match="shield drop"),
        ):
            _apply_shield_policy(project, "ctr", MOCK_TASK_DIR, is_restart=False)

    def test_restart_retain_noop_when_no_file(self, tmp_path: Path) -> None:
        """Restart with retain + no persisted state file does nothing."""
        from terok.lib.orchestration.task_runners import _apply_shield_policy

        project = self._make_project(on_restart="retain")
        with (
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners._shield_down_impl") as mock_down,
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)
        mock_down.assert_not_called()

    def test_restart_retain_warns_on_restore_failure(self, tmp_path: Path) -> None:
        """Restart with retain emits a warning when shield restore fails."""
        from terok.lib.orchestration.task_runners import _apply_shield_policy

        (tmp_path / "shield_desired_state").write_text("down\n")
        project = self._make_project(on_restart="retain")
        with (
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch(
                "terok.lib.orchestration.task_runners._shield_down_impl",
                side_effect=RuntimeError("nft not found"),
            ),
            pytest.warns(match="shield restore"),
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)

    def test_restart_unknown_policy_raises(self, tmp_path: Path) -> None:
        """Unknown on_task_restart value raises ValueError."""
        from terok.lib.orchestration.task_runners import _apply_shield_policy

        project = self._make_project(on_restart="bogus")
        with (
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            pytest.raises(ValueError, match="Unknown shield.on_task_restart"),
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)


# ── _maybe_deny_anthropic_api ────────────────────────────


class TestMaybeDenyAnthropicApi:
    """Verify shield deny for api.anthropic.com when Claude OAuth is proxied."""

    def test_noop_when_not_proxied(self) -> None:
        """No-op when Claude OAuth is not proxied."""
        from terok.lib.orchestration.task_runners import _maybe_deny_anthropic_api

        with patch(
            "terok.lib.core.config.is_claude_oauth_proxied",
            return_value=False,
        ):
            _maybe_deny_anthropic_api("ctr", MOCK_TASK_DIR)

    def test_calls_shield_deny_when_proxied(self, tmp_path: Path) -> None:
        """Calls shield.deny('api.anthropic.com') when Claude OAuth is proxied."""
        from terok.lib.orchestration.task_runners import _maybe_deny_anthropic_api

        mock_shield = MagicMock()
        with (
            patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=True),
            patch("terok_sandbox.make_shield", return_value=mock_shield),
        ):
            _maybe_deny_anthropic_api("ctr", tmp_path)

        mock_shield.deny.assert_called_once_with("ctr", "api.anthropic.com")

    def test_warns_on_failure(self) -> None:
        """Emits a warning when shield.deny raises."""
        from terok.lib.orchestration.task_runners import _maybe_deny_anthropic_api

        with (
            patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=True),
            patch("terok_sandbox.make_shield", side_effect=RuntimeError("nft missing")),
            pytest.warns(match="shield deny api.anthropic.com"),
        ):
            _maybe_deny_anthropic_api("ctr", MOCK_TASK_DIR)


# ── _run_container ────────────────────────────────────────


class TestRunContainer:
    """Verify _run_container builds a correct RunSpec and delegates."""

    def _make_project(self) -> MagicMock:
        """Return a mock ProjectConfig for _run_container."""
        from terok.lib.core.project_model import ProjectConfig

        p = MagicMock(spec=ProjectConfig)
        p.gpu_enabled = False
        p.root = MOCK_TASK_DIR
        p.isolation = "shared"
        p.is_sealed = False
        p.memory_limit = None
        p.cpu_limit = None
        p.nested_containers = False
        return p

    def test_builds_runspec_and_delegates(self) -> None:
        """_run_container constructs a RunSpec and calls sandbox.run()."""
        from terok_sandbox import VolumeSpec

        vol = VolumeSpec(Path("/a"), "/b")
        project = self._make_project()
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=False),
        ):
            _run_container(
                cname="test-ctr",
                image="alpine:latest",
                env={"FOO": "bar"},
                volumes=[vol],
                project=project,
                task_dir=MOCK_TASK_DIR,
                command=["bash", "-lc", "echo hi"],
            )

        sandbox_factory.return_value.run.assert_called_once()
        spec = sandbox_factory.return_value.run.call_args[0][0]
        assert spec.container_name == "test-ctr"
        assert spec.image == "alpine:latest"
        assert spec.env == {"FOO": "bar"}
        assert spec.volumes == (vol,)
        assert spec.command == ("bash", "-lc", "echo hi")
        assert spec.task_dir == MOCK_TASK_DIR
        assert spec.gpu_enabled is False
        assert spec.unrestricted is False  # FOO doesn't have TEROK_UNRESTRICTED

    def test_unrestricted_flag_from_env(self) -> None:
        """unrestricted is True when TEROK_UNRESTRICTED is in env."""
        project = self._make_project()
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=False),
        ):
            _run_container(
                cname="test-ctr",
                image="alpine:latest",
                env={"TEROK_UNRESTRICTED": "1"},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = sandbox_factory.return_value.run.call_args[0][0]
        assert spec.unrestricted is True

    def test_gpu_flag_from_project(self) -> None:
        """gpu_enabled is derived from has_gpu(project)."""
        project = self._make_project()
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=True),
        ):
            _run_container(
                cname="gpu-ctr",
                image="nvidia:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = sandbox_factory.return_value.run.call_args[0][0]
        assert spec.gpu_enabled is True

    def test_extra_args_and_command(self) -> None:
        """extra_args and command are converted to tuples in RunSpec."""
        project = self._make_project()
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=False),
        ):
            _run_container(
                cname="ctr",
                image="img:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
                extra_args=["-p", "8080:80"],
                command=["bash", "-lc", "toad --serve"],
            )

        spec = sandbox_factory.return_value.run.call_args[0][0]
        assert spec.extra_args == ("-p", "8080:80")
        assert spec.command == ("bash", "-lc", "toad --serve")

    def test_resource_limits_from_project(self) -> None:
        """memory_limit and cpu_limit flow from ProjectConfig to RunSpec."""
        project = self._make_project()
        project.memory_limit = "4g"
        project.cpu_limit = "2.0"
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=False),
        ):
            _run_container(
                cname="rl-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = sandbox_factory.return_value.run.call_args[0][0]
        assert spec.memory_limit == "4g"
        assert spec.cpu_limit == "2.0"

    def test_resource_limits_default_none(self) -> None:
        """Resource limits are None when project has no limits set."""
        project = self._make_project()
        project.memory_limit = None
        project.cpu_limit = None
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=False),
        ):
            _run_container(
                cname="ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = sandbox_factory.return_value.run.call_args[0][0]
        assert spec.memory_limit is None
        assert spec.cpu_limit is None

    def test_gpu_config_error_becomes_system_exit(self) -> None:
        """GpuConfigError from sandbox.run() is surfaced as SystemExit."""
        from terok_sandbox import GpuConfigError

        project = self._make_project()
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=True),
        ):
            sandbox_factory.return_value.run.side_effect = GpuConfigError("CDI broken")
            with pytest.raises(SystemExit, match="CDI broken"):
                _run_container(
                    cname="gpu-ctr",
                    image="nvidia:latest",
                    env={},
                    volumes=[],
                    project=project,
                    task_dir=MOCK_TASK_DIR,
                )

    def test_hooks_forwarded(self) -> None:
        """LifecycleHooks are passed through to sandbox.run()."""
        from terok_sandbox import LifecycleHooks

        hooks = LifecycleHooks(pre_start=lambda: None)
        project = self._make_project()
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=False),
        ):
            _run_container(
                cname="ctr",
                image="img",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
                hooks=hooks,
            )

        assert sandbox_factory.return_value.run.call_args.kwargs["hooks"] is hooks

    def test_none_command_becomes_empty_tuple(self) -> None:
        """command=None results in an empty tuple in the RunSpec."""
        project = self._make_project()
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch(
                "terok.lib.orchestration.task_runners.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=False),
        ):
            _run_container(
                cname="ctr",
                image="img",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
                command=None,
            )

        spec = sandbox_factory.return_value.run.call_args[0][0]
        assert spec.command == ()

    def test_sealed_flag_propagated(self) -> None:
        """sealed=True when project.is_sealed is True."""
        project = self._make_project()
        project.isolation = "sealed"
        project.is_sealed = True

        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=False),
        ):
            _run_container(
                cname="sealed-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = sandbox_factory.return_value.run.call_args[0][0]
        assert spec.sealed is True

    def test_shared_flag_default(self) -> None:
        """sealed=False when project uses default shared isolation."""
        project = self._make_project()
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=False),
        ):
            _run_container(
                cname="shared-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = sandbox_factory.return_value.run.call_args[0][0]
        assert spec.sealed is False

    def test_nested_containers_adds_selinux_and_fuse_flags(self) -> None:
        """run.nested_containers=true appends label=nested + /dev/fuse."""
        project = self._make_project()
        project.nested_containers = True
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=False),
        ):
            _run_container(
                cname="nested-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
                extra_args=["-p", "127.0.0.1:8080:8080"],
            )

        spec = sandbox_factory.return_value.run.call_args[0][0]
        # Caller-supplied flags come first, project-derived flags append.
        assert "--security-opt" in spec.extra_args
        assert "label=nested" in spec.extra_args
        assert "--device" in spec.extra_args
        assert "/dev/fuse" in spec.extra_args
        assert "-p" in spec.extra_args
        assert "127.0.0.1:8080:8080" in spec.extra_args

    def test_nested_containers_default_adds_nothing(self) -> None:
        """run.nested_containers=false (default) leaves extra_args untouched."""
        project = self._make_project()  # nested_containers defaults False
        with (
            patch("terok.lib.orchestration.task_runners._sandbox") as sandbox_factory,
            patch("terok.lib.orchestration.task_runners.has_gpu", return_value=False),
        ):
            _run_container(
                cname="plain-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = sandbox_factory.return_value.run.call_args[0][0]
        assert "label=nested" not in spec.extra_args
        assert "/dev/fuse" not in spec.extra_args


# ── _apply_unrestricted_env ───────────────────────────────


class TestApplyUnrestrictedEnv:
    """Verify unrestricted env injection."""

    def test_sets_flag_and_auto_approve(self) -> None:
        """Injects TEROK_UNRESTRICTED and all agent auto-approve vars."""
        from terok_executor import collect_all_auto_approve_env

        env: dict[str, str] = {}
        _apply_unrestricted_env(env)

        assert env["TEROK_UNRESTRICTED"] == "1"
        # Every canonical auto-approve key from the registry must be present
        expected = collect_all_auto_approve_env()
        for key, value in expected.items():
            assert env[key] == value, f"missing or wrong auto-approve key {key}"
