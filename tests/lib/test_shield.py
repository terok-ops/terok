# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the shield adapter module."""

import unittest
import unittest.mock

from terok_shield import ShieldConfig, ShieldMode

from terok.lib.security.shield import (
    allow,
    deny,
    get_log_containers,
    get_profiles,
    get_shield_config,
    logs,
    post_start,
    pre_start,
    pre_stop,
    rules,
    setup,
    status,
)


class TestGetShieldConfig(unittest.TestCase):
    """Tests for get_shield_config()."""

    @unittest.mock.patch("terok.lib.security.shield.get_gate_server_port", return_value=9418)
    @unittest.mock.patch("terok.lib.security.shield.get_global_section", return_value={})
    def test_defaults(self, _mock_section: unittest.mock.Mock, _mock_port: unittest.mock.Mock):
        """Empty config section produces sane defaults."""
        cfg = get_shield_config()
        self.assertIsInstance(cfg, ShieldConfig)
        self.assertEqual(cfg.mode, ShieldMode.HOOK)
        self.assertEqual(cfg.default_profiles, ("dev-standard",))
        self.assertEqual(cfg.gate_port, 9418)
        self.assertTrue(cfg.audit_enabled)
        self.assertTrue(cfg.audit_log_allowed)

    @unittest.mock.patch("terok.lib.security.shield.get_gate_server_port", return_value=1234)
    @unittest.mock.patch(
        "terok.lib.security.shield.get_global_section",
        return_value={
            "mode": "bridge",
            "profiles": ["base", "dev-python"],
            "audit": {"enabled": False, "log_allowed": False},
        },
    )
    def test_custom_config(self, _mock_section: unittest.mock.Mock, _mock_port: unittest.mock.Mock):
        """Config section is correctly mapped to ShieldConfig."""
        cfg = get_shield_config()
        self.assertEqual(cfg.mode, ShieldMode.BRIDGE)
        self.assertEqual(cfg.default_profiles, ("base", "dev-python"))
        self.assertEqual(cfg.gate_port, 1234)
        self.assertFalse(cfg.audit_enabled)
        self.assertFalse(cfg.audit_log_allowed)

    @unittest.mock.patch("terok.lib.security.shield.get_gate_server_port", return_value=9418)
    @unittest.mock.patch(
        "terok.lib.security.shield.get_global_section",
        return_value={"mode": "nonsense"},
    )
    def test_invalid_mode_raises(
        self, _mock_section: unittest.mock.Mock, _mock_port: unittest.mock.Mock
    ):
        """Invalid mode string raises SystemExit."""
        with self.assertRaises(SystemExit):
            get_shield_config()

    @unittest.mock.patch("terok.lib.security.shield.get_gate_server_port", return_value=9418)
    @unittest.mock.patch(
        "terok.lib.security.shield.get_global_section",
        return_value={"profiles": "not-a-list"},
    )
    def test_non_list_profiles_uses_default(
        self, _mock_section: unittest.mock.Mock, _mock_port: unittest.mock.Mock
    ):
        """Non-list profiles value falls back to default."""
        cfg = get_shield_config()
        self.assertEqual(cfg.default_profiles, ("dev-standard",))


class TestLifecycleWrappers(unittest.TestCase):
    """Tests for pre_start / post_start / pre_stop delegation."""

    @unittest.mock.patch("terok.lib.security.shield.shield_pre_start")
    @unittest.mock.patch(
        "terok.lib.security.shield.get_shield_config",
        return_value=ShieldConfig(),
    )
    def test_pre_start_delegates(self, _mock_cfg: unittest.mock.Mock, mock_fn: unittest.mock.Mock):
        """pre_start passes container name and config to terok_shield."""
        mock_fn.return_value = ["--network", "pasta:-T,9418"]
        result = pre_start("mycontainer")
        mock_fn.assert_called_once_with("mycontainer", config=ShieldConfig())
        self.assertEqual(result, ["--network", "pasta:-T,9418"])

    @unittest.mock.patch("terok.lib.security.shield.shield_post_start")
    @unittest.mock.patch(
        "terok.lib.security.shield.get_shield_config",
        return_value=ShieldConfig(),
    )
    def test_post_start_delegates(self, _mock_cfg: unittest.mock.Mock, mock_fn: unittest.mock.Mock):
        """post_start delegates to terok_shield."""
        post_start("mycontainer")
        mock_fn.assert_called_once_with("mycontainer", config=ShieldConfig())

    @unittest.mock.patch("terok.lib.security.shield.shield_pre_stop")
    @unittest.mock.patch(
        "terok.lib.security.shield.get_shield_config",
        return_value=ShieldConfig(),
    )
    def test_pre_stop_delegates(self, _mock_cfg: unittest.mock.Mock, mock_fn: unittest.mock.Mock):
        """pre_stop delegates to terok_shield."""
        pre_stop("mycontainer")
        mock_fn.assert_called_once_with("mycontainer", config=ShieldConfig())


class TestManagementWrappers(unittest.TestCase):
    """Tests for setup / status / allow / deny / rules / logs."""

    @unittest.mock.patch("terok.lib.security.shield.shield_setup")
    @unittest.mock.patch(
        "terok.lib.security.shield.get_shield_config",
        return_value=ShieldConfig(),
    )
    def test_setup_delegates(self, _mock_cfg: unittest.mock.Mock, mock_fn: unittest.mock.Mock):
        """setup() calls shield_setup with config."""
        setup()
        mock_fn.assert_called_once_with(config=ShieldConfig())

    @unittest.mock.patch("terok.lib.security.shield.shield_status")
    @unittest.mock.patch(
        "terok.lib.security.shield.get_shield_config",
        return_value=ShieldConfig(),
    )
    def test_status_delegates(self, _mock_cfg: unittest.mock.Mock, mock_fn: unittest.mock.Mock):
        """status() returns dict from shield_status."""
        mock_fn.return_value = {
            "mode": "hook",
            "profiles": [],
            "audit_enabled": True,
            "log_files": [],
        }
        result = status()
        self.assertEqual(result["mode"], "hook")

    @unittest.mock.patch("terok.lib.security.shield.shield_allow")
    @unittest.mock.patch(
        "terok.lib.security.shield.get_shield_config",
        return_value=ShieldConfig(),
    )
    def test_allow_delegates(self, _mock_cfg: unittest.mock.Mock, mock_fn: unittest.mock.Mock):
        """allow() forwards container and target."""
        mock_fn.return_value = ["1.2.3.4"]
        result = allow("ctr", "example.com")
        mock_fn.assert_called_once_with("ctr", "example.com", config=ShieldConfig())
        self.assertEqual(result, ["1.2.3.4"])

    @unittest.mock.patch("terok.lib.security.shield.shield_deny")
    @unittest.mock.patch(
        "terok.lib.security.shield.get_shield_config",
        return_value=ShieldConfig(),
    )
    def test_deny_delegates(self, _mock_cfg: unittest.mock.Mock, mock_fn: unittest.mock.Mock):
        """deny() forwards container and target."""
        mock_fn.return_value = ["1.2.3.4"]
        result = deny("ctr", "1.2.3.4")
        self.assertEqual(result, ["1.2.3.4"])

    @unittest.mock.patch("terok.lib.security.shield.shield_rules")
    @unittest.mock.patch(
        "terok.lib.security.shield.get_shield_config",
        return_value=ShieldConfig(),
    )
    def test_rules_delegates(self, _mock_cfg: unittest.mock.Mock, mock_fn: unittest.mock.Mock):
        """rules() returns nft output."""
        mock_fn.return_value = "table inet shield { ... }"
        self.assertEqual(rules("ctr"), "table inet shield { ... }")

    @unittest.mock.patch("terok.lib.security.shield.tail_log")
    def test_logs_delegates(self, mock_fn: unittest.mock.Mock):
        """logs() yields audit entries for a container."""
        mock_fn.return_value = iter([{"ts": "2026-01-01", "action": "setup"}])
        entries = list(logs("ctr", n=10))
        mock_fn.assert_called_once_with("ctr", n=10)
        self.assertEqual(len(entries), 1)

    def test_logs_none_container_returns_empty(self):
        """logs(None) returns empty iterator."""
        self.assertEqual(list(logs(None)), [])

    @unittest.mock.patch("terok.lib.security.shield.list_log_files")
    def test_get_log_containers(self, mock_fn: unittest.mock.Mock):
        """get_log_containers() returns list from list_log_files."""
        mock_fn.return_value = ["ctr-a", "ctr-b"]
        self.assertEqual(get_log_containers(), ["ctr-a", "ctr-b"])

    @unittest.mock.patch("terok.lib.security.shield.list_profiles")
    def test_get_profiles(self, mock_fn: unittest.mock.Mock):
        """get_profiles() returns list from list_profiles."""
        mock_fn.return_value = ["base", "dev-standard"]
        self.assertEqual(get_profiles(), ["base", "dev-standard"])
