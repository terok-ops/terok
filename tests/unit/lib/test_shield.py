# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terok-shield adapter (``terok_sandbox.shield``)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from terok_sandbox import (
    SandboxConfig,
    check_environment,
    down,
    make_shield,
    pre_start,
    run_setup,
    setup_hooks_direct,
    state,
    status,
    up,
)
from terok_sandbox.shield import _BYPASS_WARNING
from terok_shield import (
    USER_HOOKS_DIR,
    EnvironmentCheck,
    NftNotFoundError,
    Shield,
    ShieldMode,
    ShieldNeedsSetup,
    ShieldState,
)

from tests.testfs import MOCK_BASE, MOCK_CONFIG_ROOT, MOCK_TASK_DIR
from tests.testnet import GATE_PORT

CUSTOM_GATE_PORT = GATE_PORT + 1


def make_mock_shield(
    *,
    shield_state: ShieldState = ShieldState.UP,
    pre_start_args: list[str] | None = None,
) -> MagicMock:
    """Create a mock ``Shield`` instance with useful defaults."""
    mock_shield = MagicMock(spec=Shield)
    mock_shield.state.return_value = shield_state
    mock_shield.pre_start.return_value = (
        ["--network", "hook-net"] if pre_start_args is None else pre_start_args
    )
    return mock_shield


@pytest.mark.parametrize(
    ("cfg_kwargs", "expected_profiles", "expected_port", "audit_enabled"),
    [
        pytest.param(
            {"gate_port": GATE_PORT, "proxy_port": 18731, "ssh_agent_port": 18732},
            ("dev-standard",),
            GATE_PORT,
            True,
            id="defaults",
        ),
        pytest.param(
            {
                "shield_profiles": ("custom-a", "custom-b"),
                "shield_audit": False,
                "gate_port": CUSTOM_GATE_PORT,
                "proxy_port": 18731,
                "ssh_agent_port": 18732,
            },
            ("custom-a", "custom-b"),
            CUSTOM_GATE_PORT,
            False,
            id="custom-values",
        ),
        pytest.param(
            {
                "shield_profiles": ("single-profile",),
                "gate_port": GATE_PORT,
                "proxy_port": 18731,
                "ssh_agent_port": 18732,
            },
            ("single-profile",),
            GATE_PORT,
            True,
            id="single-profile",
        ),
    ],
)
def test_make_shield_maps_config_to_shield_config(
    cfg_kwargs: dict[str, object],
    expected_profiles: tuple[str, ...],
    expected_port: int,
    audit_enabled: bool,
) -> None:
    """SandboxConfig values are translated into the per-task ``ShieldConfig``."""
    cfg = SandboxConfig(config_dir=MOCK_CONFIG_ROOT, **cfg_kwargs)
    with (
        patch("terok_shield.run.SubprocessRunner", autospec=True),
        patch("terok_sandbox.paths.namespace_config_root", return_value=MOCK_CONFIG_ROOT),
    ):
        shield = make_shield(MOCK_TASK_DIR, cfg=cfg)

    assert isinstance(shield, Shield)
    config = shield.config
    assert config.mode == ShieldMode.HOOK
    assert config.default_profiles == expected_profiles
    assert config.loopback_ports == (expected_port, cfg.proxy_port, cfg.ssh_agent_port)
    assert config.audit_enabled is audit_enabled
    assert config.state_dir == MOCK_TASK_DIR / "shield"
    assert config.profiles_dir == MOCK_CONFIG_ROOT / "shield" / "profiles"


def test_nft_not_found_is_reexported() -> None:
    """``NftNotFoundError`` is re-exported from the adapter module."""
    from terok_sandbox import NftNotFoundError as error_type

    assert error_type is NftNotFoundError


def test_shield_state_is_reexported() -> None:
    """``ShieldState`` is re-exported from the adapter module."""
    from terok_sandbox import ShieldState as shield_state_type

    assert shield_state_type is ShieldState


@pytest.mark.parametrize(
    ("func", "method_name", "expected"),
    [
        pytest.param(down, "down", None, id="down"),
        pytest.param(up, "up", None, id="up"),
        pytest.param(state, "state", ShieldState.UP, id="state"),
        pytest.param(pre_start, "pre_start", ["--network", "hook-net"], id="pre-start"),
    ],
)
@patch("terok_sandbox.shield.make_shield")
def test_shield_functions_delegate_to_per_task_shield(
    mock_make: MagicMock,
    func: Callable[..., object],
    method_name: str,
    expected: object,
) -> None:
    """The thin wrapper functions delegate to the corresponding ``Shield`` methods."""
    mock_shield = make_mock_shield()
    mock_make.return_value = mock_shield

    result = func("my-container", MOCK_TASK_DIR)

    mock_make.assert_called_once_with(MOCK_TASK_DIR, None)
    if method_name == "down":
        getattr(mock_shield, method_name).assert_called_once_with("my-container", allow_all=False)
    else:
        getattr(mock_shield, method_name).assert_called_once_with("my-container")
    if expected is not None:
        assert result == expected


@patch("terok_sandbox.shield.make_shield")
def test_shield_down_allow_all(mock_make: MagicMock) -> None:
    """The ``down`` wrapper passes ``allow_all=True`` when requested."""
    mock_shield = make_mock_shield()
    mock_make.return_value = mock_shield

    down("my-container", MOCK_TASK_DIR, allow_all=True)

    mock_make.assert_called_once_with(MOCK_TASK_DIR, None)
    mock_shield.down.assert_called_once_with("my-container", allow_all=True)


def test_status_defaults() -> None:
    """Status reflects the default configured shield state."""
    cfg = SandboxConfig(gate_port=9418, proxy_port=18731, ssh_agent_port=18732)
    assert status(cfg=cfg) == {
        "mode": "hook",
        "profiles": ["dev-standard"],
        "audit_enabled": True,
    }


def test_status_custom_config() -> None:
    """Status reflects custom configured profiles and audit settings."""
    cfg = SandboxConfig(shield_profiles=("custom",), shield_audit=False)
    assert status(cfg=cfg) == {
        "mode": "hook",
        "profiles": ["custom"],
        "audit_enabled": False,
    }


@pytest.mark.parametrize("func", [down, up], ids=["down", "up"])
@patch("terok_sandbox.shield.make_shield")
def test_bypass_makes_down_and_up_noops(
    mock_make: MagicMock,
    func: Callable[..., object],
) -> None:
    """Bypass mode makes the up/down wrapper functions no-ops."""
    cfg = SandboxConfig(shield_bypass=True)
    func("ctr", MOCK_TASK_DIR, cfg=cfg)
    mock_make.assert_not_called()


def test_bypass_pre_start_returns_empty_with_warning() -> None:
    """Bypass mode returns no pre-start Podman args and warns loudly."""
    cfg = SandboxConfig(shield_bypass=True)
    with pytest.warns(UserWarning) as caught:
        assert pre_start("ctr", MOCK_TASK_DIR, cfg=cfg) == []
    assert any(_BYPASS_WARNING in str(item.message) for item in caught)


@patch("terok_sandbox.shield.make_shield")
def test_bypass_state_still_queries_real_shield(
    mock_make: MagicMock,
) -> None:
    """State lookup still queries the real shield to handle pre-bypass containers."""
    cfg = SandboxConfig(shield_bypass=True)
    mock_make.return_value = make_mock_shield(shield_state=ShieldState.UP)
    assert state("ctr", MOCK_TASK_DIR, cfg=cfg) == ShieldState.UP
    mock_make.assert_called_once_with(MOCK_TASK_DIR, cfg)


@pytest.mark.parametrize(
    ("bypass_enabled", "expected_key"),
    [
        pytest.param(True, True, id="bypass-active"),
        pytest.param(False, False, id="bypass-disabled"),
    ],
)
def test_status_includes_bypass_flag_only_when_active(
    bypass_enabled: bool,
    expected_key: bool,
) -> None:
    """Status output surfaces the dangerous bypass flag only when it is active."""
    cfg = SandboxConfig(shield_bypass=bypass_enabled)
    result = status(cfg=cfg)
    assert ("bypass_firewall_no_protection" in result) is expected_key
    assert result["mode"] == "hook"
    assert "profiles" in result


@patch("terok_sandbox.shield.make_shield")
def test_check_environment_forwards_result(mock_make: MagicMock) -> None:
    """Environment checking delegates to ``Shield.check_environment``."""
    expected = EnvironmentCheck(ok=True, health="ok", podman_version=(5, 6, 0))
    mock_shield = make_mock_shield()
    mock_shield.check_environment.return_value = expected
    mock_make.return_value = mock_shield

    assert check_environment() == expected
    mock_shield.check_environment.assert_called_once()


def test_check_environment_bypass_returns_synthetic_result() -> None:
    """Bypass mode surfaces a synthetic degraded environment result."""
    cfg = SandboxConfig(shield_bypass=True)
    result = check_environment(cfg=cfg)
    assert not result.ok
    assert result.health == "bypass"
    assert any("bypass" in issue for issue in result.issues)


@patch("terok_sandbox.shield.make_shield")
def test_pre_start_converts_shield_needs_setup_to_system_exit(mock_make: MagicMock) -> None:
    """``ShieldNeedsSetup`` is converted into a diagnostic SystemExit."""
    mock_shield = make_mock_shield()
    mock_shield.pre_start.side_effect = ShieldNeedsSetup("hooks not installed")
    mock_make.return_value = mock_shield

    with pytest.raises(SystemExit, match="hooks not installed"):
        pre_start("ctr", MOCK_TASK_DIR)


@pytest.mark.parametrize(
    ("kwargs", "expected_call"),
    [
        pytest.param({}, None, id="missing-flags"),
        pytest.param({"user": True}, {"root": False}, id="user-setup"),
        pytest.param({"root": True}, {"root": True}, id="root-setup"),
    ],
)
def test_run_setup(
    kwargs: dict[str, bool],
    expected_call: dict[str, bool] | None,
) -> None:
    """Shield setup handles usage, user, and root installation paths."""
    with patch("terok_sandbox.shield.setup_hooks_direct") as mock_direct:
        if expected_call is None:
            with pytest.raises(SystemExit, match="--root"):
                run_setup(**kwargs)
            mock_direct.assert_not_called()
        else:
            run_setup(**kwargs)
            mock_direct.assert_called_once_with(**expected_call)


@pytest.mark.parametrize(
    ("root", "expected_use_sudo", "should_configure_user_hooks"),
    [
        pytest.param(False, False, True, id="user-mode"),
        pytest.param(True, True, False, id="root-mode"),
    ],
)
@patch("terok_sandbox.shield.system_hooks_dir")
@patch("terok_sandbox.shield.ensure_containers_conf_hooks_dir")
@patch("terok_sandbox.shield.setup_global_hooks")
def test_setup_hooks_direct(
    mock_setup: MagicMock,
    mock_conf: MagicMock,
    mock_system_hooks_dir: MagicMock,
    root: bool,
    expected_use_sudo: bool,
    should_configure_user_hooks: bool,
) -> None:
    """Hook installation chooses the correct target and user/root post-processing."""
    expected_target = (MOCK_BASE / "system-hooks") if root else Path(USER_HOOKS_DIR).expanduser()
    mock_system_hooks_dir.return_value = expected_target

    setup_hooks_direct(root=root)

    args, kwargs = mock_setup.call_args
    assert args == (expected_target,)
    assert kwargs.get("use_sudo", False) is expected_use_sudo
    if should_configure_user_hooks:
        mock_conf.assert_called_once_with(expected_target)
    else:
        mock_conf.assert_not_called()
