# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terok-shield adapter (``terok.lib.security.shield``)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from terok_shield import (
    USER_HOOKS_DIR,
    EnvironmentCheck,
    NftNotFoundError,
    Shield,
    ShieldMode,
    ShieldNeedsSetup,
    ShieldState,
)

from terok.lib.security.shield import (
    _BYPASS_WARNING,
    _normalize_profiles,
    _profiles_dir,
    _state_dir,
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
from tests.testfs import MOCK_BASE, MOCK_CONFIG_ROOT, MOCK_TASK_DIR
from tests.testnet import GATE_PORT

_BYPASS_PATCH = "terok.lib.security.shield.get_shield_bypass_firewall_no_protection"
CUSTOM_GATE_PORT = GATE_PORT + 1


@pytest.fixture(autouse=True)
def _bypass_disabled_by_default() -> Iterator[None]:
    """Keep normal-path shield tests deterministic unless explicitly overridden."""
    with patch(_BYPASS_PATCH, return_value=False):
        yield


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


def test_state_dir_returns_shield_subdir() -> None:
    """Per-task shield state lives under ``task_dir/shield``."""
    assert _state_dir(MOCK_TASK_DIR) == MOCK_TASK_DIR / "shield"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("foo", ("foo",), id="single-string"),
        pytest.param(["a", "b"], ("a", "b"), id="list"),
        pytest.param(("x",), ("x",), id="tuple"),
    ],
)
def test_normalize_profiles_success(raw: object, expected: tuple[str, ...]) -> None:
    """String, list, and tuple profile configs normalize to tuples."""
    assert _normalize_profiles(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(123, id="invalid-type"),
        pytest.param(["ok", 42], id="invalid-list-item"),
    ],
)
def test_normalize_profiles_rejects_invalid_values(raw: object) -> None:
    """Unsupported profile config values raise ``TypeError``."""
    with pytest.raises(TypeError):
        _normalize_profiles(raw)


@patch("terok.lib.security.shield.config_root", return_value=MOCK_CONFIG_ROOT)
def test_profiles_dir_returns_shield_profiles_subdir(_mock: MagicMock) -> None:
    """Shared shield profiles live under ``config_root()/shield/profiles``."""
    assert _profiles_dir() == MOCK_CONFIG_ROOT / "shield" / "profiles"


@pytest.mark.parametrize(
    ("global_section", "expected_profiles", "expected_port", "audit_enabled"),
    [
        pytest.param({}, ("dev-standard",), GATE_PORT, True, id="defaults"),
        pytest.param(
            {"profiles": ["custom-a", "custom-b"], "audit": False},
            ("custom-a", "custom-b"),
            CUSTOM_GATE_PORT,
            False,
            id="custom-values",
        ),
        pytest.param(
            {"profiles": "single-profile"},
            ("single-profile",),
            GATE_PORT,
            True,
            id="single-profile",
        ),
    ],
)
def test_make_shield_maps_config_to_shield_config(
    global_section: dict[str, object],
    expected_profiles: tuple[str, ...],
    expected_port: int,
    audit_enabled: bool,
) -> None:
    """Global shield config is translated into the per-task ``ShieldConfig``."""
    with (
        patch("terok_shield.SubprocessRunner", autospec=True),
        patch("terok.lib.security.shield.config_root", return_value=MOCK_CONFIG_ROOT),
        patch("terok.lib.security.shield.get_global_section", return_value=global_section),
        patch("terok.lib.security.shield.get_gate_server_port", return_value=expected_port),
    ):
        shield = make_shield(MOCK_TASK_DIR)

    assert isinstance(shield, Shield)
    config = shield.config
    assert config.mode == ShieldMode.HOOK
    assert config.default_profiles == expected_profiles
    assert config.loopback_ports == (expected_port,)
    assert config.audit_enabled is audit_enabled
    assert config.state_dir == MOCK_TASK_DIR / "shield"
    assert config.profiles_dir == MOCK_CONFIG_ROOT / "shield" / "profiles"


def test_make_shield_rejects_invalid_profiles() -> None:
    """Invalid ``shield.profiles`` values fail fast."""
    with (
        patch("terok_shield.SubprocessRunner", autospec=True),
        patch("terok.lib.security.shield.get_global_section", return_value={"profiles": 123}),
        patch("terok.lib.security.shield.get_gate_server_port", return_value=GATE_PORT),
        pytest.raises(TypeError),
    ):
        make_shield(MOCK_TASK_DIR)


def test_nft_not_found_is_reexported() -> None:
    """``NftNotFoundError`` is re-exported from the adapter module."""
    from terok.lib.security.shield import NftNotFoundError as error_type

    assert error_type is NftNotFoundError


def test_shield_state_is_reexported() -> None:
    """``ShieldState`` is re-exported from the adapter module."""
    from terok.lib.security.shield import ShieldState as shield_state_type

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
@patch("terok.lib.security.shield.make_shield")
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

    mock_make.assert_called_once_with(MOCK_TASK_DIR)
    if method_name == "down":
        getattr(mock_shield, method_name).assert_called_once_with("my-container", allow_all=False)
    else:
        getattr(mock_shield, method_name).assert_called_once_with("my-container")
    if expected is not None:
        assert result == expected


@patch("terok.lib.security.shield.make_shield")
def test_shield_down_allow_all(mock_make: MagicMock) -> None:
    """The ``down`` wrapper passes ``allow_all=True`` when requested."""
    mock_shield = make_mock_shield()
    mock_make.return_value = mock_shield

    down("my-container", MOCK_TASK_DIR, allow_all=True)

    mock_make.assert_called_once_with(MOCK_TASK_DIR)
    mock_shield.down.assert_called_once_with("my-container", allow_all=True)


@patch("terok.lib.security.shield.get_global_section", return_value={})
def test_status_defaults(_sec: MagicMock) -> None:
    """Status reflects the default configured shield state."""
    assert status() == {
        "mode": "hook",
        "profiles": ["dev-standard"],
        "audit_enabled": True,
    }


@patch(
    "terok.lib.security.shield.get_global_section",
    return_value={"profiles": ["custom"], "audit": False},
)
def test_status_custom_config(_sec: MagicMock) -> None:
    """Status reflects custom configured profiles and audit settings."""
    assert status() == {
        "mode": "hook",
        "profiles": ["custom"],
        "audit_enabled": False,
    }


@pytest.mark.parametrize("func", [down, up], ids=["down", "up"])
@patch(_BYPASS_PATCH, return_value=True)
@patch("terok.lib.security.shield.make_shield")
def test_bypass_makes_down_and_up_noops(
    mock_make: MagicMock,
    _bypass: MagicMock,
    func: Callable[..., object],
) -> None:
    """Bypass mode makes the up/down wrapper functions no-ops."""
    func("ctr", MOCK_TASK_DIR)
    mock_make.assert_not_called()


@patch(_BYPASS_PATCH, return_value=True)
def test_bypass_pre_start_returns_empty_with_warning(_bypass: MagicMock) -> None:
    """Bypass mode returns no pre-start Podman args and warns loudly."""
    with pytest.warns(UserWarning) as caught:
        assert pre_start("ctr", MOCK_TASK_DIR) == []
    assert any(_BYPASS_WARNING in str(item.message) for item in caught)


@patch(_BYPASS_PATCH, return_value=True)
@patch("terok.lib.security.shield.make_shield")
def test_bypass_state_still_queries_real_shield(
    mock_make: MagicMock,
    _bypass: MagicMock,
) -> None:
    """State lookup still queries the real shield to handle pre-bypass containers."""
    mock_make.return_value = make_mock_shield(shield_state=ShieldState.UP)
    assert state("ctr", MOCK_TASK_DIR) == ShieldState.UP
    mock_make.assert_called_once_with(MOCK_TASK_DIR)


@pytest.mark.parametrize(
    ("bypass_enabled", "expected_key"),
    [
        pytest.param(True, True, id="bypass-active"),
        pytest.param(False, False, id="bypass-disabled"),
    ],
)
@patch("terok.lib.security.shield.get_global_section", return_value={})
def test_status_includes_bypass_flag_only_when_active(
    _sec: MagicMock,
    bypass_enabled: bool,
    expected_key: bool,
) -> None:
    """Status output surfaces the dangerous bypass flag only when it is active."""
    with patch(_BYPASS_PATCH, return_value=bypass_enabled):
        result = status()
    assert ("bypass_firewall_no_protection" in result) is expected_key
    assert result["mode"] == "hook"
    assert "profiles" in result


@patch("terok.lib.security.shield.make_shield")
def test_check_environment_forwards_result(mock_make: MagicMock) -> None:
    """Environment checking delegates to ``Shield.check_environment``."""
    expected = EnvironmentCheck(ok=True, health="ok", podman_version=(5, 6, 0))
    mock_shield = make_mock_shield()
    mock_shield.check_environment.return_value = expected
    mock_make.return_value = mock_shield

    assert check_environment() == expected
    mock_shield.check_environment.assert_called_once()


@patch(_BYPASS_PATCH, return_value=True)
def test_check_environment_bypass_returns_synthetic_result(_bypass: MagicMock) -> None:
    """Bypass mode surfaces a synthetic degraded environment result."""
    result = check_environment()
    assert not result.ok
    assert result.health == "bypass"
    assert any("bypass" in issue for issue in result.issues)


@patch("terok.lib.security.shield.make_shield")
def test_pre_start_converts_shield_needs_setup_to_system_exit(mock_make: MagicMock) -> None:
    """``ShieldNeedsSetup`` is converted into a user-facing setup hint."""
    mock_shield = make_mock_shield()
    mock_shield.pre_start.side_effect = ShieldNeedsSetup("hooks not installed")
    mock_make.return_value = mock_shield

    with pytest.raises(SystemExit, match="terokctl shield setup"):
        pre_start("ctr", MOCK_TASK_DIR)


@pytest.mark.parametrize(
    ("environment", "kwargs", "expected_call", "expected_message"),
    [
        pytest.param(
            EnvironmentCheck(hooks="not-installed", needs_setup=True),
            {},
            None,
            "--root",
            id="missing-flags",
        ),
        pytest.param(
            EnvironmentCheck(hooks="per-container", podman_version=(5, 8, 0)),
            {},
            None,
            "per-task",
            id="per-container-hooks",
        ),
        pytest.param(
            EnvironmentCheck(hooks="not-installed", needs_setup=True),
            {"user": True},
            {"root": False},
            None,
            id="user-setup",
        ),
        pytest.param(
            EnvironmentCheck(hooks="not-installed", needs_setup=True),
            {"root": True},
            {"root": True},
            None,
            id="root-setup",
        ),
    ],
)
def test_run_setup(
    environment: EnvironmentCheck,
    kwargs: dict[str, bool],
    expected_call: dict[str, bool] | None,
    expected_message: str | None,
) -> None:
    """Shield setup handles usage, no-op, user, and root installation paths."""
    with (
        patch("terok.lib.security.shield.check_environment", return_value=environment),
        patch("terok.lib.security.shield.setup_hooks_direct") as mock_direct,
        patch("builtins.print") as mock_print,
    ):
        if expected_call is None and environment.hooks != "per-container":
            with pytest.raises(SystemExit, match=expected_message or ""):
                run_setup(**kwargs)
            mock_direct.assert_not_called()
            mock_print.assert_not_called()
            return

        run_setup(**kwargs)

    if expected_call is None:
        mock_direct.assert_not_called()
        printed = " ".join(str(call) for call in mock_print.call_args_list)
        assert expected_message is not None and expected_message in printed.lower()
    else:
        mock_direct.assert_called_once_with(**expected_call)


@pytest.mark.parametrize(
    ("root", "expected_use_sudo", "should_configure_user_hooks"),
    [
        pytest.param(False, False, True, id="user-mode"),
        pytest.param(True, True, False, id="root-mode"),
    ],
)
@patch("terok.lib.security.shield.system_hooks_dir")
@patch("terok.lib.security.shield.ensure_containers_conf_hooks_dir")
@patch("terok.lib.security.shield.setup_global_hooks")
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
