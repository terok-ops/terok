# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terok-shield adapter (terok.lib.security.shield)."""

import unittest
from unittest.mock import MagicMock, patch

from terok_shield import NftNotFoundError, Shield, ShieldMode, ShieldState

from constants import GATE_PORT, MOCK_CONFIG_ROOT, MOCK_TASK_DIR
from terok.lib.security.shield import (
    _normalize_profiles,
    _profiles_dir,
    _state_dir,
    down,
    make_shield,
    pre_start,
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
