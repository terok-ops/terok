# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terok-shield adapter (terok.lib.security.shield)."""

import unittest
from unittest.mock import MagicMock, patch

from terok_shield import ShieldConfig, ShieldMode

from terok.lib.security.shield import (
    allow,
    deny,
    get_log_containers,
    get_profiles,
    get_shield_config,
    logs,
    pre_start,
    resolve,
    rules,
    setup,
    status,
)


TEST_IP = "1.2.3.4"


class TestGetShieldConfig(unittest.TestCase):
    """Tests for get_shield_config()."""

    @patch("terok.lib.security.shield.get_global_section", return_value={})
    @patch("terok.lib.security.shield.get_gate_server_port", return_value=9418)
    def test_defaults(self, _port: MagicMock, _sec: MagicMock) -> None:
        """Default config uses hook mode, dev-standard profile, audit on."""
        cfg = get_shield_config()
        assert cfg.mode == ShieldMode.HOOK
        assert cfg.default_profiles == ("dev-standard",)
        assert cfg.gate_port == 9418
        assert cfg.audit_enabled is True
        assert cfg.audit_log_allowed is True

    @patch(
        "terok.lib.security.shield.get_global_section",
        return_value={
            "profiles": ["custom-a", "custom-b"],
            "audit": False,
            "audit_log_allowed": False,
        },
    )
    @patch("terok.lib.security.shield.get_gate_server_port", return_value=7777)
    def test_custom(self, _port: MagicMock, _sec: MagicMock) -> None:
        """Custom config values are mapped correctly."""
        cfg = get_shield_config()
        assert cfg.default_profiles == ("custom-a", "custom-b")
        assert cfg.gate_port == 7777
        assert cfg.audit_enabled is False
        assert cfg.audit_log_allowed is False

    @patch(
        "terok.lib.security.shield.get_global_section",
        return_value={"profiles": "single-profile"},
    )
    @patch("terok.lib.security.shield.get_gate_server_port", return_value=9418)
    def test_single_profile_string(self, _port: MagicMock, _sec: MagicMock) -> None:
        """A single profile string is normalised to a tuple."""
        cfg = get_shield_config()
        assert cfg.default_profiles == ("single-profile",)

    @patch(
        "terok.lib.security.shield.get_global_section",
        return_value={"profiles": 123},
    )
    @patch("terok.lib.security.shield.get_gate_server_port", return_value=9418)
    def test_invalid_profiles_type(self, _port: MagicMock, _sec: MagicMock) -> None:
        """Non-string/non-list profiles value raises TypeError."""
        with self.assertRaises(TypeError):
            get_shield_config()


class TestPreStart(unittest.TestCase):
    """Tests for pre_start() delegation."""

    @patch("terok.lib.security.shield.shield_pre_start", return_value=["--network", "hook-net"])
    @patch("terok.lib.security.shield.get_shield_config")
    def test_delegates(self, mock_cfg: MagicMock, mock_pre: MagicMock) -> None:
        """pre_start passes container name and config to shield_pre_start."""
        cfg = ShieldConfig(
            mode=ShieldMode.HOOK,
            default_profiles=("dev-standard",),
            gate_port=9418,
        )
        mock_cfg.return_value = cfg
        result = pre_start("my-container")
        mock_pre.assert_called_once_with("my-container", config=cfg)
        assert result == ["--network", "hook-net"]


class TestManagementWrappers(unittest.TestCase):
    """Tests for thin management wrappers."""

    @patch("terok.lib.security.shield.shield_setup")
    @patch("terok.lib.security.shield.get_shield_config")
    def test_setup(self, mock_cfg: MagicMock, mock_setup: MagicMock) -> None:
        """setup() delegates to shield_setup with config."""
        cfg = MagicMock(spec=ShieldConfig)
        mock_cfg.return_value = cfg
        setup()
        mock_setup.assert_called_once_with(config=cfg)

    @patch("terok.lib.security.shield.shield_status", return_value={"installed": True})
    @patch("terok.lib.security.shield.get_shield_config")
    def test_status(self, mock_cfg: MagicMock, mock_st: MagicMock) -> None:
        """status() delegates to shield_status."""
        cfg = MagicMock(spec=ShieldConfig)
        mock_cfg.return_value = cfg
        result = status()
        mock_st.assert_called_once_with(config=cfg)
        assert result == {"installed": True}

    @patch("terok.lib.security.shield.shield_allow", return_value=["allowed 1.2.3.4"])
    @patch("terok.lib.security.shield.get_shield_config")
    def test_allow(self, mock_cfg: MagicMock, mock_allow: MagicMock) -> None:
        """allow() delegates to shield_allow."""
        cfg = MagicMock(spec=ShieldConfig)
        mock_cfg.return_value = cfg
        result = allow("ctr", TEST_IP)
        mock_allow.assert_called_once_with("ctr", TEST_IP, config=cfg)
        assert result == ["allowed 1.2.3.4"]

    @patch("terok.lib.security.shield.shield_deny", return_value=["denied 1.2.3.4"])
    @patch("terok.lib.security.shield.get_shield_config")
    def test_deny(self, mock_cfg: MagicMock, mock_deny: MagicMock) -> None:
        """deny() delegates to shield_deny."""
        cfg = MagicMock(spec=ShieldConfig)
        mock_cfg.return_value = cfg
        result = deny("ctr", TEST_IP)
        mock_deny.assert_called_once_with("ctr", TEST_IP, config=cfg)
        assert result == ["denied 1.2.3.4"]

    @patch("terok.lib.security.shield.shield_rules", return_value="table inet shield {}")
    @patch("terok.lib.security.shield.get_shield_config")
    def test_rules(self, mock_cfg: MagicMock, mock_rules: MagicMock) -> None:
        """rules() delegates to shield_rules."""
        cfg = MagicMock(spec=ShieldConfig)
        mock_cfg.return_value = cfg
        result = rules("ctr")
        mock_rules.assert_called_once_with("ctr", config=cfg)
        assert result == "table inet shield {}"

    @patch("terok.lib.security.shield.shield_resolve", return_value=[TEST_IP])
    @patch("terok.lib.security.shield.get_shield_config")
    def test_resolve(self, mock_cfg: MagicMock, mock_resolve: MagicMock) -> None:
        """resolve() delegates to shield_resolve."""
        cfg = MagicMock(spec=ShieldConfig)
        mock_cfg.return_value = cfg
        result = resolve("ctr")
        mock_resolve.assert_called_once_with("ctr", config=cfg)
        assert result == [TEST_IP]

    @patch("terok.lib.security.shield.tail_log", return_value=iter([{"action": "allow"}]))
    def test_logs(self, mock_tail: MagicMock) -> None:
        """logs() delegates to tail_log."""
        result = list(logs("ctr", n=10))
        mock_tail.assert_called_once_with("ctr", n=10)
        assert result == [{"action": "allow"}]

    @patch("terok.lib.security.shield.list_log_files", return_value=["ctr-a", "ctr-b"])
    def test_get_log_containers(self, mock_list: MagicMock) -> None:
        """get_log_containers() delegates to list_log_files."""
        result = get_log_containers()
        mock_list.assert_called_once()
        assert result == ["ctr-a", "ctr-b"]

    @patch("terok.lib.security.shield.list_profiles", return_value=["dev-standard", "dev-strict"])
    def test_get_profiles(self, mock_list: MagicMock) -> None:
        """get_profiles() delegates to list_profiles."""
        result = get_profiles()
        mock_list.assert_called_once()
        assert result == ["dev-standard", "dev-strict"]
