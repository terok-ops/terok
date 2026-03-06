# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for gate_server module."""

import contextlib
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from terok.lib.security.gate_server import (
    GateServerStatus,
    _is_managed_git_daemon,
    ensure_server_reachable,
    get_server_status,
    install_systemd_units,
    is_daemon_running,
    is_socket_active,
    is_socket_installed,
    is_systemd_available,
    start_daemon,
    stop_daemon,
    uninstall_systemd_units,
)


class TestSystemdDetection(unittest.TestCase):
    """Tests for systemd availability detection."""

    @unittest.mock.patch("subprocess.run")
    def test_systemd_available(self, mock_run: unittest.mock.Mock) -> None:
        mock_run.return_value = unittest.mock.Mock(returncode=0)
        self.assertTrue(is_systemd_available())

    @unittest.mock.patch("subprocess.run")
    def test_systemd_available_exit_1(self, mock_run: unittest.mock.Mock) -> None:
        mock_run.return_value = unittest.mock.Mock(returncode=1)
        self.assertTrue(is_systemd_available())

    @unittest.mock.patch("subprocess.run", side_effect=FileNotFoundError)
    def test_systemd_not_available(self, _mock: unittest.mock.Mock) -> None:
        self.assertFalse(is_systemd_available())

    @unittest.mock.patch("subprocess.run")
    def test_systemd_unavailable_exit_2(self, mock_run: unittest.mock.Mock) -> None:
        mock_run.return_value = unittest.mock.Mock(returncode=2)
        self.assertFalse(is_systemd_available())


class TestSocketInstalled(unittest.TestCase):
    """Tests for socket unit file detection."""

    def test_socket_not_installed(self) -> None:
        with unittest.mock.patch(
            "terok.lib.security.gate_server._systemd_unit_dir",
            return_value=Path("/nonexistent"),
        ):
            self.assertFalse(is_socket_installed())

    def test_socket_installed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            unit_dir = Path(td)
            (unit_dir / "terok-gate.socket").write_text("[Socket]\n")
            with unittest.mock.patch(
                "terok.lib.security.gate_server._systemd_unit_dir",
                return_value=unit_dir,
            ):
                self.assertTrue(is_socket_installed())


class TestSocketActive(unittest.TestCase):
    """Tests for socket active check."""

    @unittest.mock.patch("subprocess.run")
    def test_active(self, mock_run: unittest.mock.Mock) -> None:
        mock_run.return_value = unittest.mock.Mock(stdout="active\n", returncode=0)
        self.assertTrue(is_socket_active())

    @unittest.mock.patch("subprocess.run")
    def test_inactive(self, mock_run: unittest.mock.Mock) -> None:
        mock_run.return_value = unittest.mock.Mock(stdout="inactive\n", returncode=3)
        self.assertFalse(is_socket_active())

    @unittest.mock.patch("subprocess.run", side_effect=FileNotFoundError)
    def test_no_systemctl(self, _mock: unittest.mock.Mock) -> None:
        self.assertFalse(is_socket_active())


class TestInstallUninstall(unittest.TestCase):
    """Tests for systemd unit install/uninstall."""

    @unittest.mock.patch("subprocess.run")
    def test_install_writes_files(self, mock_run: unittest.mock.Mock) -> None:
        mock_run.return_value = unittest.mock.Mock(returncode=0)
        with tempfile.TemporaryDirectory() as td:
            unit_dir = Path(td) / "systemd" / "user"
            with (
                unittest.mock.patch(
                    "terok.lib.security.gate_server._systemd_unit_dir",
                    return_value=unit_dir,
                ),
                unittest.mock.patch("terok.lib.security.gate_server._get_port", return_value=9418),
                unittest.mock.patch(
                    "terok.lib.security.gate_server._get_gate_base_path",
                    return_value=Path("/tmp/gate"),
                ),
            ):
                install_systemd_units()

            self.assertTrue((unit_dir / "terok-gate.socket").is_file())
            self.assertTrue((unit_dir / "terok-gate@.service").is_file())
            # Verify socket file contains port
            socket_content = (unit_dir / "terok-gate.socket").read_text()
            self.assertIn("0.0.0.0:9418", socket_content)
            # Verify service file contains base path
            service_content = (unit_dir / "terok-gate@.service").read_text()
            self.assertIn("/tmp/gate", service_content)

    @unittest.mock.patch("subprocess.run")
    def test_uninstall_removes_files(self, mock_run: unittest.mock.Mock) -> None:
        mock_run.return_value = unittest.mock.Mock(returncode=0)
        with tempfile.TemporaryDirectory() as td:
            unit_dir = Path(td)
            (unit_dir / "terok-gate.socket").write_text("[Socket]\n")
            (unit_dir / "terok-gate@.service").write_text("[Service]\n")

            with unittest.mock.patch(
                "terok.lib.security.gate_server._systemd_unit_dir",
                return_value=unit_dir,
            ):
                uninstall_systemd_units()

            self.assertFalse((unit_dir / "terok-gate.socket").exists())
            self.assertFalse((unit_dir / "terok-gate@.service").exists())


class TestDaemon(unittest.TestCase):
    """Tests for daemon start/stop."""

    @unittest.mock.patch("subprocess.run")
    def test_start_daemon(self, mock_run: unittest.mock.Mock) -> None:
        mock_run.return_value = unittest.mock.Mock(returncode=0)
        with tempfile.TemporaryDirectory() as td:
            with (
                unittest.mock.patch(
                    "terok.lib.security.gate_server._get_gate_base_path",
                    return_value=Path(td) / "gate",
                ),
                unittest.mock.patch(
                    "terok.lib.security.gate_server._pid_file",
                    return_value=Path(td) / "gate-server.pid",
                ),
            ):
                start_daemon(port=9999)

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertIn("--port=9999", cmd)
            self.assertIn("--listen=0.0.0.0", cmd)

    @unittest.mock.patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git"))
    def test_start_daemon_failure(self, _mock: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as td:
            with (
                unittest.mock.patch(
                    "terok.lib.security.gate_server._get_gate_base_path",
                    return_value=Path(td) / "gate",
                ),
                unittest.mock.patch(
                    "terok.lib.security.gate_server._pid_file",
                    return_value=Path(td) / "gate-server.pid",
                ),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    start_daemon(port=9999)

    def test_stop_daemon_no_pidfile(self) -> None:
        with unittest.mock.patch(
            "terok.lib.security.gate_server._pid_file",
            return_value=Path("/nonexistent/pid"),
        ):
            stop_daemon()  # Should not raise

    @unittest.mock.patch("terok.lib.security.gate_server._is_managed_git_daemon", return_value=True)
    def test_stop_daemon_with_pidfile(self, _mock_check: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as td:
            pidfile = Path(td) / "gate-server.pid"
            pidfile.write_text("99999\n")
            with (
                unittest.mock.patch(
                    "terok.lib.security.gate_server._pid_file",
                    return_value=pidfile,
                ),
                unittest.mock.patch("os.kill") as mock_kill,
            ):
                stop_daemon()
                mock_kill.assert_called_once_with(99999, unittest.mock.ANY)
            self.assertFalse(pidfile.exists())

    @unittest.mock.patch(
        "terok.lib.security.gate_server._is_managed_git_daemon", return_value=False
    )
    def test_stop_daemon_stale_pid_not_killed(self, _mock_check: unittest.mock.Mock) -> None:
        """Stop removes PID file even if the process is not our daemon."""
        with tempfile.TemporaryDirectory() as td:
            pidfile = Path(td) / "gate-server.pid"
            pidfile.write_text("99999\n")
            with (
                unittest.mock.patch(
                    "terok.lib.security.gate_server._pid_file",
                    return_value=pidfile,
                ),
                unittest.mock.patch("os.kill") as mock_kill,
            ):
                stop_daemon()
                mock_kill.assert_not_called()
            self.assertFalse(pidfile.exists())


class TestIsDaemonRunning(unittest.TestCase):
    """Tests for is_daemon_running."""

    def test_no_pidfile(self) -> None:
        with unittest.mock.patch(
            "terok.lib.security.gate_server._pid_file",
            return_value=Path("/nonexistent/pid"),
        ):
            self.assertFalse(is_daemon_running())

    @unittest.mock.patch("terok.lib.security.gate_server._is_managed_git_daemon", return_value=True)
    def test_stale_pid(self, _mock_check: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as td:
            pidfile = Path(td) / "gate-server.pid"
            pidfile.write_text("99999\n")
            with (
                unittest.mock.patch(
                    "terok.lib.security.gate_server._pid_file",
                    return_value=pidfile,
                ),
                unittest.mock.patch("os.kill", side_effect=ProcessLookupError),
            ):
                self.assertFalse(is_daemon_running())

    @unittest.mock.patch("terok.lib.security.gate_server._is_managed_git_daemon", return_value=True)
    def test_valid_pid(self, _mock_check: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as td:
            pidfile = Path(td) / "gate-server.pid"
            pidfile.write_text(f"{os.getpid()}\n")
            with unittest.mock.patch(
                "terok.lib.security.gate_server._pid_file",
                return_value=pidfile,
            ):
                self.assertTrue(is_daemon_running())

    def test_not_our_daemon(self) -> None:
        """PID exists but is not a git daemon — should return False."""
        with tempfile.TemporaryDirectory() as td:
            pidfile = Path(td) / "gate-server.pid"
            pidfile.write_text(f"{os.getpid()}\n")
            with (
                unittest.mock.patch(
                    "terok.lib.security.gate_server._pid_file",
                    return_value=pidfile,
                ),
                unittest.mock.patch(
                    "terok.lib.security.gate_server._is_managed_git_daemon",
                    return_value=False,
                ),
            ):
                self.assertFalse(is_daemon_running())


class TestIsManagedGitDaemon(unittest.TestCase):
    """Tests for _is_managed_git_daemon."""

    def test_no_proc_entry(self) -> None:
        self.assertFalse(_is_managed_git_daemon(999999999))

    def test_current_process_is_not_git_daemon(self) -> None:
        # The current process is python, not git daemon
        self.assertFalse(_is_managed_git_daemon(os.getpid()))

    def _check_cmdline(self, cmdline: bytes, pid_file: Path | None = None) -> bool:
        """Write *cmdline* to a temp file and call ``_is_managed_git_daemon``."""
        with tempfile.TemporaryDirectory() as td:
            fake_cmdline = Path(td) / "cmdline"
            fake_cmdline.write_bytes(cmdline)
            patches = [
                unittest.mock.patch(
                    "terok.lib.security.gate_server.Path",
                    return_value=fake_cmdline,
                ),
            ]
            if pid_file is not None:
                patches.append(
                    unittest.mock.patch(
                        "terok.lib.security.gate_server._pid_file",
                        return_value=pid_file,
                    )
                )
            with contextlib.ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                return _is_managed_git_daemon(12345)

    def test_matches_managed_daemon(self) -> None:
        """Cmdline with git daemon and our PID file returns True."""
        pid_file = Path("/run/user/1000/terok/gate-server.pid")
        cmdline = b"git\x00daemon\x00--listen=127.0.0.1\x00--pid-file=" + str(pid_file).encode()
        self.assertTrue(self._check_cmdline(cmdline, pid_file))

    def test_rejects_different_pid_file(self) -> None:
        """Git daemon with a different --pid-file is not ours."""
        cmdline = b"git\x00daemon\x00--listen=127.0.0.1\x00--pid-file=/other/pid"
        self.assertFalse(self._check_cmdline(cmdline, Path("/run/user/1000/terok/gate-server.pid")))

    def test_rejects_non_git_process(self) -> None:
        """Process that is not git at all returns False."""
        cmdline = b"python3\x00-m\x00pytest"
        self.assertFalse(self._check_cmdline(cmdline, Path("/run/user/1000/terok/gate-server.pid")))

    def test_matches_full_path_git(self) -> None:
        """Cmdline with full path like /usr/bin/git is recognized."""
        pid_file = Path("/run/user/1000/terok/gate-server.pid")
        cmdline = b"/usr/bin/git\x00daemon\x00--pid-file=" + str(pid_file).encode()
        self.assertTrue(self._check_cmdline(cmdline, pid_file))


class TestGetServerStatus(unittest.TestCase):
    """Tests for get_server_status."""

    @unittest.mock.patch("terok.lib.security.gate_server.is_daemon_running", return_value=False)
    @unittest.mock.patch("terok.lib.security.gate_server.is_socket_installed", return_value=False)
    @unittest.mock.patch("terok.lib.security.gate_server._get_port", return_value=9418)
    def test_none(self, *_mocks: unittest.mock.Mock) -> None:
        status = get_server_status()
        self.assertEqual(status, GateServerStatus(mode="none", running=False, port=9418))

    @unittest.mock.patch("terok.lib.security.gate_server.is_socket_active", return_value=True)
    @unittest.mock.patch("terok.lib.security.gate_server.is_socket_installed", return_value=True)
    @unittest.mock.patch("terok.lib.security.gate_server._get_port", return_value=9418)
    def test_systemd_active(self, *_mocks: unittest.mock.Mock) -> None:
        status = get_server_status()
        self.assertEqual(status, GateServerStatus(mode="systemd", running=True, port=9418))

    @unittest.mock.patch("terok.lib.security.gate_server.is_daemon_running", return_value=False)
    @unittest.mock.patch("terok.lib.security.gate_server.is_socket_active", return_value=False)
    @unittest.mock.patch("terok.lib.security.gate_server.is_socket_installed", return_value=True)
    @unittest.mock.patch("terok.lib.security.gate_server._get_port", return_value=9418)
    def test_systemd_inactive(self, *_mocks: unittest.mock.Mock) -> None:
        status = get_server_status()
        self.assertEqual(status, GateServerStatus(mode="systemd", running=False, port=9418))

    @unittest.mock.patch("terok.lib.security.gate_server.is_daemon_running", return_value=True)
    @unittest.mock.patch("terok.lib.security.gate_server.is_socket_active", return_value=False)
    @unittest.mock.patch("terok.lib.security.gate_server.is_socket_installed", return_value=True)
    @unittest.mock.patch("terok.lib.security.gate_server._get_port", return_value=9418)
    def test_daemon_fallback_when_socket_inactive(self, *_mocks: unittest.mock.Mock) -> None:
        """Daemon fallback is detected even when systemd units are installed."""
        status = get_server_status()
        self.assertEqual(status, GateServerStatus(mode="daemon", running=True, port=9418))

    @unittest.mock.patch("terok.lib.security.gate_server.is_daemon_running", return_value=True)
    @unittest.mock.patch("terok.lib.security.gate_server.is_socket_installed", return_value=False)
    @unittest.mock.patch("terok.lib.security.gate_server._get_port", return_value=9418)
    def test_daemon_running(self, *_mocks: unittest.mock.Mock) -> None:
        status = get_server_status()
        self.assertEqual(status, GateServerStatus(mode="daemon", running=True, port=9418))


class TestEnsureServerReachable(unittest.TestCase):
    """Tests for ensure_server_reachable."""

    @unittest.mock.patch(
        "terok.lib.security.gate_server.get_server_status",
        return_value=GateServerStatus(mode="daemon", running=True, port=9418),
    )
    def test_passes_when_running(self, _mock: unittest.mock.Mock) -> None:
        ensure_server_reachable()  # Should not raise

    @unittest.mock.patch("terok.lib.security.gate_server.is_systemd_available", return_value=True)
    @unittest.mock.patch(
        "terok.lib.security.gate_server.get_server_status",
        return_value=GateServerStatus(mode="none", running=False, port=9418),
    )
    def test_raises_when_not_running_systemd(self, *_mocks: unittest.mock.Mock) -> None:
        with self.assertRaises(SystemExit) as ctx:
            ensure_server_reachable()
        self.assertIn("gate-server install", str(ctx.exception))

    @unittest.mock.patch("terok.lib.security.gate_server.is_systemd_available", return_value=False)
    @unittest.mock.patch(
        "terok.lib.security.gate_server.get_server_status",
        return_value=GateServerStatus(mode="none", running=False, port=9418),
    )
    def test_raises_when_not_running_no_systemd(self, *_mocks: unittest.mock.Mock) -> None:
        with self.assertRaises(SystemExit) as ctx:
            ensure_server_reachable()
        self.assertIn("gate-server start", str(ctx.exception))
