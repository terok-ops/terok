# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terok-shield adapter (terok.lib.security.shield)."""

import unittest
import warnings
from unittest.mock import MagicMock, patch

from terok_shield import (
    EnvironmentCheck,
    NftNotFoundError,
    Shield,
    ShieldMode,
    ShieldNeedsSetup,
    ShieldState,
)

from constants import GATE_PORT, MOCK_CONFIG_ROOT, MOCK_TASK_DIR
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


class TestStateDir(unittest.TestCase):
    """Tests for _state_dir()."""

    def test_returns_shield_subdir(self) -> None:
        """_state_dir returns task_dir/shield."""
        result = _state_dir(MOCK_TASK_DIR)
        self.assertEqual(result, MOCK_TASK_DIR / "shield")


class TestNormalizeProfiles(unittest.TestCase):
    """Tests for _normalize_profiles()."""

    def test_string_becomes_tuple(self) -> None:
        """A single string is normalised to a one-element tuple."""
        self.assertEqual(_normalize_profiles("foo"), ("foo",))

    def test_list_becomes_tuple(self) -> None:
        """A list of strings is converted to a tuple."""
        self.assertEqual(_normalize_profiles(["a", "b"]), ("a", "b"))

    def test_tuple_passthrough(self) -> None:
        """A tuple of strings passes through unchanged."""
        self.assertEqual(_normalize_profiles(("x",)), ("x",))

    def test_invalid_type_raises(self) -> None:
        """Non-string/non-list raises TypeError."""
        with self.assertRaises(TypeError):
            _normalize_profiles(123)

    def test_non_string_item_raises(self) -> None:
        """A list containing a non-string raises TypeError."""
        with self.assertRaises(TypeError):
            _normalize_profiles(["ok", 42])


class TestProfilesDir(unittest.TestCase):
    """Tests for _profiles_dir()."""

    @patch("terok.lib.security.shield.config_root", return_value=MOCK_CONFIG_ROOT)
    def test_returns_shield_profiles_subdir(self, _mock: MagicMock) -> None:
        """_profiles_dir returns config_root/shield/profiles."""
        self.assertEqual(_profiles_dir(), MOCK_CONFIG_ROOT / "shield" / "profiles")


@patch("terok_shield.SubprocessRunner", autospec=True)
class TestMakeShield(unittest.TestCase):
    """Tests for make_shield()."""

    @patch("terok.lib.security.shield.config_root", return_value=MOCK_CONFIG_ROOT)
    @patch("terok.lib.security.shield.get_global_section", return_value={})
    @patch("terok.lib.security.shield.get_gate_server_port", return_value=GATE_PORT)
    def test_defaults(
        self, _port: MagicMock, _sec: MagicMock, _root: MagicMock, _runner: MagicMock
    ) -> None:
        """Default config uses hook mode, dev-standard profile, audit on."""
        shield = make_shield(MOCK_TASK_DIR)
        self.assertIsInstance(shield, Shield)
        cfg = shield.config
        self.assertEqual(cfg.mode, ShieldMode.HOOK)
        self.assertEqual(cfg.default_profiles, ("dev-standard",))
        self.assertEqual(cfg.loopback_ports, (GATE_PORT,))
        self.assertTrue(cfg.audit_enabled)
        self.assertEqual(cfg.state_dir, MOCK_TASK_DIR / "shield")
        self.assertEqual(cfg.profiles_dir, MOCK_CONFIG_ROOT / "shield" / "profiles")

    @patch(
        "terok.lib.security.shield.get_global_section",
        return_value={"profiles": ["custom-a", "custom-b"], "audit": False},
    )
    @patch("terok.lib.security.shield.get_gate_server_port", return_value=7777)
    def test_custom(self, _port: MagicMock, _sec: MagicMock, _runner: MagicMock) -> None:
        """Custom config values are mapped correctly."""
        cfg = make_shield(MOCK_TASK_DIR).config
        self.assertEqual(cfg.default_profiles, ("custom-a", "custom-b"))
        self.assertEqual(cfg.loopback_ports, (7777,))
        self.assertFalse(cfg.audit_enabled)

    @patch(
        "terok.lib.security.shield.get_global_section",
        return_value={"profiles": "single-profile"},
    )
    @patch("terok.lib.security.shield.get_gate_server_port", return_value=GATE_PORT)
    def test_single_profile_string(
        self, _port: MagicMock, _sec: MagicMock, _runner: MagicMock
    ) -> None:
        """A single profile string is normalised to a tuple."""
        self.assertEqual(make_shield(MOCK_TASK_DIR).config.default_profiles, ("single-profile",))

    @patch("terok.lib.security.shield.get_global_section", return_value={"profiles": 123})
    @patch("terok.lib.security.shield.get_gate_server_port", return_value=GATE_PORT)
    def test_invalid_profiles_type(
        self, _port: MagicMock, _sec: MagicMock, _runner: MagicMock
    ) -> None:
        """Non-string/non-list profiles value raises TypeError."""
        with self.assertRaises(TypeError):
            make_shield(MOCK_TASK_DIR)


class TestNftNotFoundReExport(unittest.TestCase):
    """Verify NftNotFoundError is re-exported from the shield adapter."""

    def test_re_exported(self) -> None:
        """NftNotFoundError is importable from the shield adapter module."""
        from terok.lib.security.shield import NftNotFoundError as _Err

        self.assertIs(_Err, NftNotFoundError)


class TestShieldStateReExport(unittest.TestCase):
    """Verify ShieldState is re-exported from the shield adapter."""

    def test_re_exported(self) -> None:
        """ShieldState is importable from the shield adapter module."""
        from terok.lib.security.shield import ShieldState as _State

        self.assertIs(_State, ShieldState)


class TestDown(unittest.TestCase):
    """Tests for down() delegation."""

    @patch("terok.lib.security.shield.make_shield")
    def test_delegates(self, mock_make: MagicMock) -> None:
        """down calls make_shield(task_dir) and delegates to shield.down."""
        mock_shield = MagicMock(spec=Shield)
        mock_make.return_value = mock_shield

        down("my-container", MOCK_TASK_DIR)

        mock_make.assert_called_once_with(MOCK_TASK_DIR)
        mock_shield.down.assert_called_once_with("my-container")


class TestUp(unittest.TestCase):
    """Tests for up() delegation."""

    @patch("terok.lib.security.shield.make_shield")
    def test_delegates(self, mock_make: MagicMock) -> None:
        """up calls make_shield(task_dir) and delegates to shield.up."""
        mock_shield = MagicMock(spec=Shield)
        mock_make.return_value = mock_shield

        up("my-container", MOCK_TASK_DIR)

        mock_make.assert_called_once_with(MOCK_TASK_DIR)
        mock_shield.up.assert_called_once_with("my-container")


class TestState(unittest.TestCase):
    """Tests for state() delegation."""

    @patch("terok.lib.security.shield.make_shield")
    def test_delegates(self, mock_make: MagicMock) -> None:
        """state calls make_shield(task_dir) and delegates to shield.state."""
        mock_shield = MagicMock(spec=Shield)
        mock_shield.state.return_value = ShieldState.UP
        mock_make.return_value = mock_shield

        result = state("my-container", MOCK_TASK_DIR)

        mock_make.assert_called_once_with(MOCK_TASK_DIR)
        mock_shield.state.assert_called_once_with("my-container")
        self.assertEqual(result, ShieldState.UP)


class TestPreStart(unittest.TestCase):
    """Tests for pre_start() delegation."""

    @patch("terok.lib.security.shield.make_shield")
    def test_delegates(self, mock_make: MagicMock) -> None:
        """pre_start calls make_shield(task_dir) and delegates to shield.pre_start."""
        mock_shield = MagicMock(spec=Shield)
        mock_shield.pre_start.return_value = ["--network", "hook-net"]
        mock_make.return_value = mock_shield

        result = pre_start("my-container", MOCK_TASK_DIR)

        mock_make.assert_called_once_with(MOCK_TASK_DIR)
        mock_shield.pre_start.assert_called_once_with("my-container")
        self.assertEqual(result, ["--network", "hook-net"])


class TestStatus(unittest.TestCase):
    """Tests for status()."""

    @patch("terok.lib.security.shield.get_global_section", return_value={})
    def test_default_status(self, _sec: MagicMock) -> None:
        """status() returns expected dict with defaults."""
        result = status()
        self.assertEqual(result["mode"], "hook")
        self.assertEqual(result["profiles"], ["dev-standard"])
        self.assertTrue(result["audit_enabled"])

    @patch(
        "terok.lib.security.shield.get_global_section",
        return_value={"profiles": ["custom"], "audit": False},
    )
    def test_custom_status(self, _sec: MagicMock) -> None:
        """status() reflects custom config values."""
        result = status()
        self.assertEqual(result["profiles"], ["custom"])
        self.assertFalse(result["audit_enabled"])


# ---------------------------------------------------------------------------
# bypass_firewall_no_protection — DANGEROUS TRANSITIONAL OVERRIDE
# ---------------------------------------------------------------------------

_BYPASS_PATCH = "terok.lib.security.shield.get_shield_bypass_firewall_no_protection"


@patch(_BYPASS_PATCH, return_value=True)
class TestBypassPreStart(unittest.TestCase):
    """pre_start returns [] and emits a warning when bypass is active."""

    def test_returns_empty_list(self, _bypass: MagicMock) -> None:
        """pre_start() returns no podman args when bypass is active."""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = pre_start("ctr", MOCK_TASK_DIR)
        self.assertEqual(result, [])

    def test_emits_warning(self, _bypass: MagicMock) -> None:
        """pre_start() emits the bypass warning."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            pre_start("ctr", MOCK_TASK_DIR)
        msgs = [str(w.message) for w in caught]
        self.assertTrue(any(_BYPASS_WARNING in m for m in msgs))


@patch(_BYPASS_PATCH, return_value=True)
class TestBypassDown(unittest.TestCase):
    """down() is a no-op when bypass is active."""

    @patch("terok.lib.security.shield.make_shield")
    def test_does_not_call_shield(self, mock_make: MagicMock, _bypass: MagicMock) -> None:
        """down() must not construct a Shield when bypass is active."""
        down("ctr", MOCK_TASK_DIR)
        mock_make.assert_not_called()


@patch(_BYPASS_PATCH, return_value=True)
class TestBypassUp(unittest.TestCase):
    """up() is a no-op when bypass is active."""

    @patch("terok.lib.security.shield.make_shield")
    def test_does_not_call_shield(self, mock_make: MagicMock, _bypass: MagicMock) -> None:
        """up() must not construct a Shield when bypass is active."""
        up("ctr", MOCK_TASK_DIR)
        mock_make.assert_not_called()


@patch(_BYPASS_PATCH, return_value=True)
class TestBypassState(unittest.TestCase):
    """state() returns INACTIVE when bypass is active."""

    @patch("terok.lib.security.shield.make_shield")
    def test_returns_inactive(self, mock_make: MagicMock, _bypass: MagicMock) -> None:
        """state() returns ShieldState.INACTIVE without constructing a Shield."""
        result = state("ctr", MOCK_TASK_DIR)
        self.assertEqual(result, ShieldState.INACTIVE)
        mock_make.assert_not_called()


@patch(_BYPASS_PATCH, return_value=True)
class TestBypassStatus(unittest.TestCase):
    """status() includes bypass_firewall_no_protection flag when active."""

    @patch("terok.lib.security.shield.get_global_section", return_value={})
    def test_includes_bypass_flag(self, _sec: MagicMock, _bypass: MagicMock) -> None:
        """status() output must include bypass_firewall_no_protection: True."""
        result = status()
        self.assertTrue(result["bypass_firewall_no_protection"])

    @patch("terok.lib.security.shield.get_global_section", return_value={})
    def test_still_returns_profiles(self, _sec: MagicMock, _bypass: MagicMock) -> None:
        """status() still returns profile info even when bypassed."""
        result = status()
        self.assertEqual(result["mode"], "hook")
        self.assertIn("profiles", result)


@patch(_BYPASS_PATCH, return_value=False)
class TestNoBypassStatus(unittest.TestCase):
    """status() omits bypass flag when bypass is not active."""

    @patch("terok.lib.security.shield.get_global_section", return_value={})
    def test_no_bypass_key(self, _sec: MagicMock, _bypass: MagicMock) -> None:
        """status() output must NOT contain bypass_firewall_no_protection."""
        result = status()
        self.assertNotIn("bypass_firewall_no_protection", result)


# ---------------------------------------------------------------------------
# check_environment
# ---------------------------------------------------------------------------


class TestCheckEnvironment(unittest.TestCase):
    """Tests for check_environment()."""

    @patch("terok.lib.security.shield.make_shield")
    def test_forwards_result(self, mock_make: MagicMock) -> None:
        """check_environment delegates to Shield.check_environment."""
        expected = EnvironmentCheck(ok=True, health="ok", podman_version=(5, 6, 0))
        mock_shield = MagicMock(spec=Shield)
        mock_shield.check_environment.return_value = expected
        mock_make.return_value = mock_shield

        result = check_environment()

        self.assertEqual(result, expected)
        mock_shield.check_environment.assert_called_once()

    @patch(_BYPASS_PATCH, return_value=True)
    def test_bypass_returns_synthetic(self, _bypass: MagicMock) -> None:
        """check_environment returns synthetic result when bypass is active."""
        result = check_environment()

        self.assertFalse(result.ok)
        self.assertEqual(result.health, "bypass")
        self.assertTrue(any("bypass" in i for i in result.issues))


# ---------------------------------------------------------------------------
# pre_start catching ShieldNeedsSetup
# ---------------------------------------------------------------------------


class TestPreStartShieldNeedsSetup(unittest.TestCase):
    """pre_start converts ShieldNeedsSetup to SystemExit."""

    @patch("terok.lib.security.shield.make_shield")
    def test_raises_system_exit(self, mock_make: MagicMock) -> None:
        """ShieldNeedsSetup becomes SystemExit with setup hint."""
        mock_shield = MagicMock(spec=Shield)
        mock_shield.pre_start.side_effect = ShieldNeedsSetup("hooks not installed")
        mock_make.return_value = mock_shield

        with self.assertRaises(SystemExit) as ctx:
            pre_start("ctr", MOCK_TASK_DIR)

        msg = str(ctx.exception)
        self.assertIn("hooks not installed", msg)
        self.assertIn("terokctl shield setup", msg)


# ---------------------------------------------------------------------------
# run_setup
# ---------------------------------------------------------------------------


class TestRunSetup(unittest.TestCase):
    """Tests for run_setup()."""

    @patch("terok.lib.security.shield.subprocess.run")
    def test_calls_subprocess_default(self, mock_run: MagicMock) -> None:
        """run_setup() calls python -m terok_shield setup."""
        run_setup()
        args = mock_run.call_args[0][0]
        self.assertIn("terok_shield", args)
        self.assertIn("setup", args)
        self.assertNotIn("--root", args)
        self.assertNotIn("--user", args)

    @patch("terok.lib.security.shield.subprocess.run")
    def test_calls_subprocess_root(self, mock_run: MagicMock) -> None:
        """run_setup(root=True) passes --root flag."""
        run_setup(root=True)
        args = mock_run.call_args[0][0]
        self.assertIn("--root", args)

    @patch("terok.lib.security.shield.subprocess.run")
    def test_calls_subprocess_user(self, mock_run: MagicMock) -> None:
        """run_setup(user=True) passes --user flag."""
        run_setup(user=True)
        args = mock_run.call_args[0][0]
        self.assertIn("--user", args)


# ---------------------------------------------------------------------------
# setup_hooks_direct
# ---------------------------------------------------------------------------


class TestSetupHooksDirect(unittest.TestCase):
    """Tests for setup_hooks_direct()."""

    @patch("terok.lib.security.shield.ensure_containers_conf_hooks_dir")
    @patch("terok.lib.security.shield.setup_global_hooks")
    def test_user_mode(self, mock_setup: MagicMock, mock_conf: MagicMock) -> None:
        """User-local installs to USER_HOOKS_DIR and configures containers.conf."""
        setup_hooks_direct(root=False)

        mock_setup.assert_called_once()
        # Should NOT use sudo for user mode
        _, kwargs = mock_setup.call_args
        self.assertFalse(kwargs.get("use_sudo", False))
        mock_conf.assert_called_once()

    @patch("terok.lib.security.shield.setup_global_hooks")
    def test_root_mode(self, mock_setup: MagicMock) -> None:
        """Root installs to system_hooks_dir with sudo."""
        setup_hooks_direct(root=True)

        mock_setup.assert_called_once()
        _, kwargs = mock_setup.call_args
        self.assertTrue(kwargs.get("use_sudo", False))
