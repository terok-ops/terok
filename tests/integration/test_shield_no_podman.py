# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tier 1 integration tests: real terok_shield library, mocked subprocess.

These tests exercise the real shield code paths — profile composition,
DNS caching, network mode detection, podman arg generation — while
mocking only the single subprocess gateway (``terok_shield.run.run``)
and terok config helpers that are unavailable in test.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from terok_shield import ShieldConfig, ShieldMode, compose_profiles, list_profiles

from constants import GATE_PORT, HOST_ALIAS_LOOPBACK, HOST_ALIAS_SLIRP, TEST_IP

from .conftest import mock_run_factory

# ── Helpers ────────────────────────────────────────────────


def _pre_start_with_mocks(
    container: str,
    config: ShieldConfig,
    *,
    rootless_mode: str = "pasta",
    euid: int = 1000,
) -> list[str]:
    """Call ``shield_pre_start`` with subprocess + config mocks in place."""
    from terok_shield import shield_pre_start

    mock = mock_run_factory(rootless_mode)
    with (
        patch("terok_shield.run.run", side_effect=mock),
        patch("terok_shield.mode_hook.run_cmd", side_effect=mock),
        patch("terok_shield.dns.dig", return_value=[TEST_IP]),
        patch("os.geteuid", return_value=euid),
    ):
        return shield_pre_start(container, config=config)


# ── TestPreStartIntegration ────────────────────────────────


class TestPreStartIntegration:
    """Real pre_start through shield library with mocked subprocess."""

    def test_pasta_with_loopback_port(
        self, installed_hooks: dict[str, Path], shield_config: ShieldConfig
    ) -> None:
        """Pasta mode includes port-forwarding, loopback host, annotations, and cap-drops."""
        args = _pre_start_with_mocks("test-ctr", shield_config)

        assert "--network" in args
        network_val = args[args.index("--network") + 1]
        assert network_val.startswith("pasta:")
        assert f"-T,{GATE_PORT}" in network_val

        assert "--add-host" in args
        host_val = args[args.index("--add-host") + 1]
        assert host_val == HOST_ALIAS_LOOPBACK

        assert "--annotation" in args
        ann_idx = args.index("--annotation")
        assert "terok.shield.profiles=dev-standard" in args[ann_idx + 1]

        assert "--hooks-dir" in args
        assert "--cap-drop" in args
        cap_drops = [args[i + 1] for i, v in enumerate(args) if v == "--cap-drop"]
        assert "NET_ADMIN" in cap_drops
        assert "NET_RAW" in cap_drops

        assert "--security-opt" in args
        secopt_idx = args.index("--security-opt")
        assert args[secopt_idx + 1] == "no-new-privileges"

    def test_slirp4netns(
        self, installed_hooks: dict[str, Path], shield_config: ShieldConfig
    ) -> None:
        """Slirp mode uses allow_host_loopback and 10.0.2.2 gateway."""
        args = _pre_start_with_mocks("test-ctr", shield_config, rootless_mode="slirp4netns")

        network_val = args[args.index("--network") + 1]
        assert network_val == "slirp4netns:allow_host_loopback=true"

        host_val = args[args.index("--add-host") + 1]
        assert host_val == HOST_ALIAS_SLIRP

    def test_multiple_loopback_ports(self, installed_hooks: dict[str, Path]) -> None:
        """Multiple loopback ports each get a -T flag in pasta arg."""
        config = ShieldConfig(
            mode=ShieldMode.HOOK,
            default_profiles=("dev-standard",),
            loopback_ports=(GATE_PORT, 8080),
        )
        args = _pre_start_with_mocks("test-ctr", config)
        network_val = args[args.index("--network") + 1]
        assert f"-T,{GATE_PORT}" in network_val
        assert "-T,8080" in network_val

    def test_no_loopback_ports(self, installed_hooks: dict[str, Path]) -> None:
        """No loopback ports yields bare 'pasta:' without -T flags."""
        config = ShieldConfig(
            mode=ShieldMode.HOOK,
            default_profiles=("dev-standard",),
            loopback_ports=(),
        )
        args = _pre_start_with_mocks("test-ctr", config)
        network_val = args[args.index("--network") + 1]
        assert network_val == "pasta:"

    def test_rootful_no_network_args(self, installed_hooks: dict[str, Path]) -> None:
        """Root mode omits --network and --add-host but keeps annotations and cap-drops."""
        config = ShieldConfig(
            mode=ShieldMode.HOOK,
            default_profiles=("dev-standard",),
            loopback_ports=(GATE_PORT,),
        )
        args = _pre_start_with_mocks("test-ctr", config, euid=0)
        assert "--network" not in args
        assert "--add-host" not in args
        assert "--annotation" in args
        assert "--cap-drop" in args

    def test_writes_resolved_cache(
        self, installed_hooks: dict[str, Path], shield_config: ShieldConfig
    ) -> None:
        """Pre-start writes a .resolved cache file for the container."""
        _pre_start_with_mocks("test-ctr", shield_config)
        resolved_file = installed_hooks["resolved"] / "test-ctr.resolved"
        assert resolved_file.is_file()
        content = resolved_file.read_text()
        assert content.strip()  # should contain at least one IP

    def test_creates_audit_log(
        self, installed_hooks: dict[str, Path], shield_config: ShieldConfig
    ) -> None:
        """Pre-start with audit_enabled writes a .jsonl log file."""
        _pre_start_with_mocks("test-ctr", shield_config)
        log_file = installed_hooks["logs"] / "test-ctr.jsonl"
        assert log_file.is_file()
        entries = [json.loads(line) for line in log_file.read_text().splitlines() if line.strip()]
        assert any(e["action"] == "setup" for e in entries)

    def test_no_audit_when_disabled(self, installed_hooks: dict[str, Path]) -> None:
        """Pre-start with audit_enabled=False creates no log file."""
        config = ShieldConfig(
            mode=ShieldMode.HOOK,
            default_profiles=("dev-standard",),
            loopback_ports=(GATE_PORT,),
            audit_enabled=False,
        )
        _pre_start_with_mocks("test-ctr-noaudit", config)
        log_file = installed_hooks["logs"] / "test-ctr-noaudit.jsonl"
        assert not log_file.exists()

    def test_hook_not_installed_raises(
        self, shield_env: dict[str, Path], shield_config: ShieldConfig
    ) -> None:
        """Pre-start without installed hooks raises RuntimeError."""
        with pytest.raises(RuntimeError, match="hook not installed"):
            _pre_start_with_mocks("test-ctr", shield_config)


# ── TestShieldSetupIntegration ────────────────────────────


class TestShieldSetupIntegration:
    """Tests for shield setup through the terok adapter."""

    def test_setup_creates_hook_files(self, shield_env: dict[str, Path]) -> None:
        """setup() installs hook JSON and entrypoint script."""
        from terok_shield import shield_setup

        config = ShieldConfig(mode=ShieldMode.HOOK, default_profiles=("dev-standard",))
        shield_setup(config=config)

        hooks_dir = shield_env["hooks"]
        assert (hooks_dir / "terok-shield-createRuntime.json").is_file()
        assert (hooks_dir / "terok-shield-poststop.json").is_file()

        entrypoint = shield_env["state"] / "terok-shield-hook"
        assert entrypoint.is_file()

    def test_setup_idempotent(self, shield_env: dict[str, Path]) -> None:
        """Calling setup() twice causes no error and produces the same files."""
        from terok_shield import shield_setup

        config = ShieldConfig(mode=ShieldMode.HOOK, default_profiles=("dev-standard",))
        shield_setup(config=config)
        shield_setup(config=config)

        hooks_dir = shield_env["hooks"]
        assert (hooks_dir / "terok-shield-createRuntime.json").is_file()


# ── TestShieldStatusIntegration ───────────────────────────


class TestShieldStatusIntegration:
    """Tests for shield_status with real library."""

    def test_status_returns_real_dict(self, shield_env: dict[str, Path]) -> None:
        """status() returns a dict with expected keys and real profile data."""
        from terok_shield import shield_status

        config = ShieldConfig(mode=ShieldMode.HOOK, default_profiles=("dev-standard",))
        result = shield_status(config=config)

        assert isinstance(result, dict)
        assert "mode" in result
        assert "profiles" in result
        assert "audit_enabled" in result
        assert "log_files" in result
        assert result["mode"] == "hook"
        assert "dev-standard" in result["profiles"]


# ── TestProfilesIntegration ──────────────────────────────


class TestProfilesIntegration:
    """Tests for profile listing and composition with real bundled files."""

    def test_list_profiles_includes_bundled(self, shield_env: dict[str, Path]) -> None:
        """list_profiles() finds bundled dev-standard profile."""
        profiles = list_profiles()
        assert "dev-standard" in profiles

    def test_compose_profiles_returns_domains(self, shield_env: dict[str, Path]) -> None:
        """compose_profiles with dev-standard returns non-empty domain list."""
        entries = compose_profiles(["dev-standard"])
        assert len(entries) > 0
        assert all(isinstance(e, str) for e in entries)


# ── TestAuditLogIntegration ──────────────────────────────


class TestAuditLogIntegration:
    """Tests for audit log reading with real library functions."""

    def test_logs_reads_jsonl(self, shield_env: dict[str, Path]) -> None:
        """tail_log reads and parses .jsonl files from the logs directory."""
        from terok_shield import tail_log

        log_file = shield_env["logs"] / "test-audit-ctr.jsonl"
        entries = [
            {"ts": "2025-01-01T00:00:00+00:00", "container": "test-audit-ctr", "action": "setup"},
            {
                "ts": "2025-01-01T00:00:01+00:00",
                "container": "test-audit-ctr",
                "action": "allowed",
                "dest": TEST_IP,
            },
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        result = list(tail_log("test-audit-ctr", n=5))
        assert len(result) == 2
        assert result[0]["action"] == "setup"
        assert result[1]["dest"] == TEST_IP

    def test_get_log_containers_finds_logs(self, shield_env: dict[str, Path]) -> None:
        """list_log_files returns container names from existing .jsonl files."""
        from terok_shield import list_log_files

        for name in ("ctr-alpha", "ctr-beta"):
            (shield_env["logs"] / f"{name}.jsonl").write_text("{}\n")

        result = list_log_files()
        assert "ctr-alpha" in result
        assert "ctr-beta" in result


# ── TestTaskRunnerShieldIntegration ──────────────────────


class TestTaskRunnerShieldIntegration:
    """Verify the full path from _run_container through real shield."""

    def test_run_container_includes_shield_args(self, installed_hooks: dict[str, Path]) -> None:
        """_run_container() injects real shield args into the podman command."""
        captured_cmd: list[str] = []

        def capture_run(cmd: list[str], **_kwargs) -> None:
            captured_cmd.extend(cmd)

        mock = mock_run_factory("pasta")
        with (
            patch("terok_shield.run.run", side_effect=mock),
            patch("terok_shield.mode_hook.run_cmd", side_effect=mock),
            patch("terok_shield.dns.dig", return_value=[TEST_IP]),
            patch("os.geteuid", return_value=1000),
            patch("terok.lib.security.shield.get_global_section", return_value={}),
            patch("terok.lib.security.shield.get_gate_server_port", return_value=GATE_PORT),
            patch("subprocess.run", side_effect=capture_run),
            patch(
                "terok.lib.containers.task_runners._podman_userns_args",
                return_value=[],
            ),
            patch(
                "terok.lib.containers.task_runners.gpu_run_args",
                return_value=[],
            ),
        ):
            from terok.lib.containers.task_runners import _run_container
            from terok.lib.core.projects import ProjectConfig

            project = MagicMock(spec=ProjectConfig)

            _run_container(
                cname="integ-test-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
            )

        assert "--network" in captured_cmd
        assert "--annotation" in captured_cmd
        assert "--cap-drop" in captured_cmd
        assert any("terok.shield.profiles" in a for a in captured_cmd)

    def test_run_container_shield_failure_propagates(self, shield_env: dict[str, Path]) -> None:
        """_run_container raises when hooks are not installed."""
        mock = mock_run_factory("pasta")
        with (
            patch("terok_shield.run.run", side_effect=mock),
            patch("terok_shield.mode_hook.run_cmd", side_effect=mock),
            patch("terok_shield.dns.dig", return_value=[TEST_IP]),
            patch("os.geteuid", return_value=1000),
            patch("terok.lib.security.shield.get_global_section", return_value={}),
            patch("terok.lib.security.shield.get_gate_server_port", return_value=GATE_PORT),
            patch("subprocess.run"),
            patch(
                "terok.lib.containers.task_runners._podman_userns_args",
                return_value=[],
            ),
            patch(
                "terok.lib.containers.task_runners.gpu_run_args",
                return_value=[],
            ),
        ):
            from terok.lib.containers.task_runners import _run_container
            from terok.lib.core.projects import ProjectConfig

            project = MagicMock(spec=ProjectConfig)

            with pytest.raises(RuntimeError, match="hook not installed"):
                _run_container(
                    cname="integ-test-ctr",
                    image="alpine:latest",
                    env={},
                    volumes=[],
                    project=project,
                )
