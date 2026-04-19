# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tier 1 integration tests: real terok_shield library, mock runner.

These tests exercise the real shield code paths — profile composition,
DNS caching, network mode detection, podman arg generation — using the
Shield class with an injected ``MockRunner`` (no subprocess calls).

Uses the per-task Shield class API (state_dir from ShieldConfig).
"""

from unittest.mock import patch

import pytest

terok_shield = pytest.importorskip("terok_shield")
Shield = terok_shield.Shield
ShieldConfig = terok_shield.ShieldConfig
ShieldMode = terok_shield.ShieldMode

from tests.testnet import (
    GATE_PORT,
    HOST_ALIAS_LOOPBACK,
    HOST_ALIAS_SLIRP,
    PASTA_HOST_LOOPBACK_MAP,
)

from .conftest import MockRunner
from .helpers import TerokShieldIntegrationEnv

pytestmark = pytest.mark.needs_host_features

# ── Helpers ────────────────────────────────────────────────


def _make_shield(
    config: ShieldConfig,
    *,
    rootless_mode: str = "pasta",
) -> Shield:
    """Create a Shield with MockRunner."""
    runner = MockRunner(rootless_mode)
    return Shield(config, runner=runner)


def _pre_start_with_mocks(
    container: str,
    config: ShieldConfig,
    *,
    rootless_mode: str = "pasta",
    euid: int = 1000,
) -> list[str]:
    """Call ``shield.pre_start`` with a mock runner.

    Patches ``has_global_hooks`` so mock-based tests don't depend on
    real hook filesystem state.
    """
    shield = _make_shield(config, rootless_mode=rootless_mode)
    with (
        patch("os.geteuid", return_value=euid),
        patch("terok_shield.hooks.mode.has_global_hooks", return_value=True),
    ):
        return shield.pre_start(container)


# ── TestPreStartIntegration ────────────────────────────────


class TestPreStartIntegration:
    """Real pre_start through Shield class with mock runner."""

    def test_pasta_with_loopback_port(self, shield_config: ShieldConfig) -> None:
        """Pasta mode includes --map-host-loopback, host alias, annotations, and cap-drops."""
        args = _pre_start_with_mocks("test-ctr", shield_config)

        assert "--network" in args
        network_val = args[args.index("--network") + 1]
        assert network_val.startswith("pasta:")
        assert "--map-host-loopback" in network_val
        assert PASTA_HOST_LOOPBACK_MAP in network_val

        assert "--add-host" in args
        host_val = args[args.index("--add-host") + 1]
        assert host_val == HOST_ALIAS_LOOPBACK

        assert "--annotation" in args
        ann_idx = args.index("--annotation")
        assert "terok.shield.profiles=dev-standard" in args[ann_idx + 1]

        # Global hooks mode: --hooks-dir gated to podman >= 99 (per-container
        # hooks don't survive restart even on 5.8.0, see PR#123)
        assert "--hooks-dir" not in args
        assert "--cap-drop" in args
        cap_drops = [args[i + 1] for i, v in enumerate(args) if v == "--cap-drop"]
        assert "NET_ADMIN" in cap_drops
        assert "NET_RAW" in cap_drops

    def test_slirp4netns(self, shield_config: ShieldConfig) -> None:
        """Slirp mode uses allow_host_loopback and 10.0.2.2 gateway."""
        args = _pre_start_with_mocks("test-ctr", shield_config, rootless_mode="slirp4netns")

        network_val = args[args.index("--network") + 1]
        assert network_val == "slirp4netns:allow_host_loopback=true"

        host_val = args[args.index("--add-host") + 1]
        assert host_val == HOST_ALIAS_SLIRP

    def test_multiple_loopback_ports(self, shield_env: TerokShieldIntegrationEnv) -> None:
        """Multiple loopback ports use a single --map-host-loopback (not per-port)."""
        config = ShieldConfig(
            state_dir=shield_env.state_dir,
            mode=ShieldMode.HOOK,
            default_profiles=("dev-standard",),
            loopback_ports=(GATE_PORT, 8080),
        )
        args = _pre_start_with_mocks("test-ctr", config)
        network_val = args[args.index("--network") + 1]
        assert "--map-host-loopback" in network_val
        assert PASTA_HOST_LOOPBACK_MAP in network_val

    def test_no_loopback_ports(self, shield_env: TerokShieldIntegrationEnv) -> None:
        """No loopback ports yields bare 'pasta:' without -T flags."""
        config = ShieldConfig(
            state_dir=shield_env.state_dir,
            mode=ShieldMode.HOOK,
            default_profiles=("dev-standard",),
            loopback_ports=(),
        )
        args = _pre_start_with_mocks("test-ctr", config)
        network_val = args[args.index("--network") + 1]
        assert network_val.startswith("pasta")

    def test_rootful_no_network_args(self, shield_env: TerokShieldIntegrationEnv) -> None:
        """Root mode omits --network and --add-host but keeps annotations and cap-drops."""
        config = ShieldConfig(
            state_dir=shield_env.state_dir,
            mode=ShieldMode.HOOK,
            default_profiles=("dev-standard",),
            loopback_ports=(GATE_PORT,),
        )
        args = _pre_start_with_mocks("test-ctr", config, euid=0)
        assert "--network" not in args
        assert "--add-host" not in args
        assert "--annotation" in args
        assert "--cap-drop" in args


# ── TestShieldStatusIntegration ───────────────────────────


class TestShieldStatusIntegration:
    """Tests for Shield.status() with real library."""

    def test_status_returns_real_dict(self, shield: Shield) -> None:
        """status() returns a dict with expected keys and real profile data."""
        result = shield.status()

        assert isinstance(result, dict)
        assert "mode" in result
        assert "profiles" in result
        assert "audit_enabled" in result
        assert result["mode"] == "hook"
        assert "dev-standard" in result["profiles"]


# ── TestProfilesIntegration ──────────────────────────────


class TestProfilesIntegration:
    """Tests for profile listing and composition with real bundled files."""

    def test_list_profiles_includes_bundled(self, shield: Shield) -> None:
        """profiles_list() finds bundled dev-standard profile."""
        profiles = shield.profiles_list()
        assert "dev-standard" in profiles

    def test_compose_profiles_returns_domains(self, shield: Shield) -> None:
        """compose_profiles with dev-standard returns non-empty domain list."""
        entries = shield.compose_profiles(["dev-standard"])
        assert len(entries) > 0
        assert all(isinstance(e, str) for e in entries)


# ── TestSandboxRunShieldIntegration ──────────────────────


class TestSandboxRunShieldIntegration:
    """Verify the full path from Sandbox.run() through real shield.

    Now that _run_container() delegates to Sandbox.run(), these tests
    exercise the sandbox executor directly with real shield pre_start.
    """

    def test_sandbox_run_includes_shield_args(self, shield_env: TerokShieldIntegrationEnv) -> None:
        """Sandbox.run() injects real shield args into the podman command."""
        from terok_sandbox import RunSpec, Sandbox

        captured_cmd: list[str] = []

        def capture_run(cmd: list[str], **_kwargs) -> None:
            captured_cmd.extend(cmd)

        task_dir = shield_env.task_dir
        spec = RunSpec(
            container_name="integ-test-ctr",
            image="alpine:latest",
            env={},
            volumes=(),
            command=(),
            task_dir=task_dir,
            unrestricted=False,
        )

        with (
            patch("terok_sandbox.paths.state_root", return_value=shield_env.state_dir),
            patch("os.geteuid", return_value=1000),
            patch("subprocess.run", side_effect=capture_run),
            patch(
                "terok_sandbox.shield.make_shield",
                return_value=Shield(
                    ShieldConfig(
                        state_dir=shield_env.state_dir,
                        mode=ShieldMode.HOOK,
                        default_profiles=("dev-standard",),
                        loopback_ports=(GATE_PORT,),
                    ),
                    runner=MockRunner(),
                ),
            ),
            patch("terok_shield.hooks.mode.has_global_hooks", return_value=True),
        ):
            sandbox = Sandbox()
            sandbox.run(spec)

        assert "--network" in captured_cmd
        assert "--annotation" in captured_cmd
        assert "--cap-drop" in captured_cmd
        assert any("terok.shield.profiles" in a for a in captured_cmd)

        # Restricted mode (unrestricted=False) → no-new-privileges
        secopt_indices = [i for i, v in enumerate(captured_cmd) if v == "--security-opt"]
        secopt_values = [captured_cmd[i + 1] for i in secopt_indices]
        assert "no-new-privileges" in secopt_values

    def test_unrestricted_skips_no_new_privileges(
        self, shield_env: TerokShieldIntegrationEnv
    ) -> None:
        """Unrestricted containers must NOT set no-new-privileges (sudo needed)."""
        from terok_sandbox import RunSpec, Sandbox

        captured_cmd: list[str] = []

        def capture_run(cmd: list[str], **_kwargs) -> None:
            captured_cmd.extend(cmd)

        task_dir = shield_env.task_dir
        spec = RunSpec(
            container_name="integ-test-ctr",
            image="alpine:latest",
            env={},
            volumes=(),
            command=(),
            task_dir=task_dir,
            unrestricted=True,
        )

        with (
            patch("os.geteuid", return_value=1000),
            patch("subprocess.run", side_effect=capture_run),
            patch("terok_sandbox.shield.pre_start", return_value=[]),
        ):
            sandbox = Sandbox()
            sandbox.run(spec)

        assert "--security-opt" not in captured_cmd

    def _run_bypass_spec(
        self, shield_env: TerokShieldIntegrationEnv, network_mode: str
    ) -> list[str]:
        """Helper: run Sandbox.run with bypass active and given network mode."""
        from terok_sandbox import RunSpec, Sandbox, SandboxConfig

        captured_cmd: list[str] = []

        def capture_run(cmd: list[str], **_kwargs) -> None:
            captured_cmd.extend(cmd)

        task_dir = shield_env.task_dir
        spec = RunSpec(
            container_name="bypass-test-ctr",
            image="alpine:latest",
            env={},
            volumes=(),
            command=(),
            task_dir=task_dir,
        )
        sandbox = Sandbox(config=SandboxConfig(shield_bypass=True))

        with (
            patch("os.geteuid", return_value=1000),
            patch("subprocess.run", side_effect=capture_run),
            patch(
                "terok_sandbox.runtime.podman._detect_rootless_network_mode",
                return_value=network_mode,
            ),
            # Shield must NOT be called at all when bypass is active
            patch(
                "terok_sandbox.shield.pre_start",
                side_effect=AssertionError("shield must not be called"),
            ),
        ):
            sandbox.run(spec)

        return captured_cmd

    def test_bypass_uses_pasta_networking(self, shield_env: TerokShieldIntegrationEnv) -> None:
        """Bypass on pasta: injects --network=pasta:--map-host-loopback and --add-host."""
        cmd = self._run_bypass_spec(shield_env, "pasta")
        assert any("--map-host-loopback" in c for c in cmd)
        assert any(PASTA_HOST_LOOPBACK_MAP in c for c in cmd)
        assert "--add-host" in cmd
        host_idx = cmd.index("--add-host")
        assert cmd[host_idx + 1] == HOST_ALIAS_LOOPBACK
        # No shield args
        assert "--annotation" not in cmd
        assert "--cap-drop" not in cmd

    def test_bypass_uses_slirp4netns_networking(
        self, shield_env: TerokShieldIntegrationEnv
    ) -> None:
        """Bypass on slirp4netns: injects --network=slirp4netns:... and --add-host."""
        cmd = self._run_bypass_spec(shield_env, "slirp4netns")
        assert "slirp4netns:allow_host_loopback=true" in cmd
        assert "--add-host" in cmd
        host_idx = cmd.index("--add-host")
        assert cmd[host_idx + 1] == HOST_ALIAS_SLIRP
        # No shield args
        assert "--annotation" not in cmd
        assert "--cap-drop" not in cmd
