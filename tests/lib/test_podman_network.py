# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for Podman rootless network detection and args."""

import json
import subprocess
import unittest
import unittest.mock

from terok.lib.util.podman import _detect_rootless_network_mode, _podman_network_args


class TestDetectRootlessNetworkMode(unittest.TestCase):
    """Tests for _detect_rootless_network_mode."""

    def setUp(self) -> None:
        _detect_rootless_network_mode.cache_clear()

    def tearDown(self) -> None:
        _detect_rootless_network_mode.cache_clear()

    @unittest.mock.patch("subprocess.check_output")
    def test_detects_pasta(self, mock_output: unittest.mock.Mock) -> None:
        """rootlessNetworkCmd: 'pasta' returns 'pasta'."""
        mock_output.return_value = json.dumps({"host": {"rootlessNetworkCmd": "pasta"}}).encode()
        self.assertEqual(_detect_rootless_network_mode(), "pasta")

    @unittest.mock.patch("subprocess.check_output")
    def test_detects_slirp4netns(self, mock_output: unittest.mock.Mock) -> None:
        """rootlessNetworkCmd: 'slirp4netns' returns 'slirp4netns'."""
        mock_output.return_value = json.dumps(
            {"host": {"rootlessNetworkCmd": "slirp4netns"}}
        ).encode()
        self.assertEqual(_detect_rootless_network_mode(), "slirp4netns")

    @unittest.mock.patch("subprocess.check_output")
    def test_fallback_podman5(self, mock_output: unittest.mock.Mock) -> None:
        """No rootlessNetworkCmd on Podman 5+ returns 'pasta'."""
        mock_output.return_value = json.dumps(
            {"host": {}, "version": {"Version": "5.2.1"}}
        ).encode()
        self.assertEqual(_detect_rootless_network_mode(), "pasta")

    @unittest.mock.patch("subprocess.check_output")
    def test_fallback_podman4(self, mock_output: unittest.mock.Mock) -> None:
        """No rootlessNetworkCmd on Podman 4.x returns 'slirp4netns'."""
        mock_output.return_value = json.dumps(
            {"host": {}, "version": {"Version": "4.9.3"}}
        ).encode()
        self.assertEqual(_detect_rootless_network_mode(), "slirp4netns")

    @unittest.mock.patch("subprocess.check_output", side_effect=FileNotFoundError)
    def test_podman_not_found(self, _mock: unittest.mock.Mock) -> None:
        """FileNotFoundError (no podman) returns 'unknown'."""
        self.assertEqual(_detect_rootless_network_mode(), "unknown")

    @unittest.mock.patch(
        "subprocess.check_output",
        side_effect=subprocess.CalledProcessError(1, "podman"),
    )
    def test_podman_error(self, _mock: unittest.mock.Mock) -> None:
        """CalledProcessError returns 'unknown'."""
        self.assertEqual(_detect_rootless_network_mode(), "unknown")


class TestPodmanNetworkArgs(unittest.TestCase):
    """Tests for _podman_network_args."""

    @unittest.mock.patch(
        "terok.lib.util.podman._detect_rootless_network_mode",
        return_value="slirp4netns",
    )
    @unittest.mock.patch("os.geteuid", return_value=1000)
    def test_slirp4netns_returns_flags(
        self, _euid: unittest.mock.Mock, _mode: unittest.mock.Mock
    ) -> None:
        """slirp4netns mode returns network flags with allow_host_loopback."""
        args = _podman_network_args()
        self.assertEqual(len(args), 4)
        self.assertIn("slirp4netns:allow_host_loopback=true", args[1])
        self.assertIn("--add-host", args)
        self.assertIn("host.containers.internal:10.0.2.2", args)

    @unittest.mock.patch(
        "terok.lib.util.podman._detect_rootless_network_mode",
        return_value="pasta",
    )
    @unittest.mock.patch("os.geteuid", return_value=1000)
    def test_pasta_returns_network_and_add_host(
        self, _euid: unittest.mock.Mock, _mode: unittest.mock.Mock
    ) -> None:
        """pasta mode returns --network pasta:-T,<port> and --add-host to loopback."""
        args = _podman_network_args(gate_port=9418)
        self.assertEqual(len(args), 4)
        self.assertIn("--network", args)
        self.assertIn("pasta:-T,9418", args)
        self.assertIn("--add-host", args)
        self.assertIn("host.containers.internal:127.0.0.1", args)

    @unittest.mock.patch(
        "terok.lib.util.podman._detect_rootless_network_mode",
        return_value="pasta",
    )
    @unittest.mock.patch("os.geteuid", return_value=1000)
    def test_pasta_custom_port(self, _euid: unittest.mock.Mock, _mode: unittest.mock.Mock) -> None:
        """pasta mode uses the provided gate_port in -T option."""
        args = _podman_network_args(gate_port=9999)
        self.assertIn("pasta:-T,9999", args)

    @unittest.mock.patch("os.geteuid", return_value=0)
    def test_root_returns_empty(self, _euid: unittest.mock.Mock) -> None:
        """Root user returns empty list regardless of network mode."""
        self.assertEqual(_podman_network_args(), [])
