# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for gate_server module."""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import unittest.mock
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from terok_sandbox import (
    GateServerStatus,
    check_units_outdated,
    ensure_server_reachable,
    get_server_status,
    install_systemd_units,
    is_daemon_running,
    is_systemd_available,
    start_daemon,
    stop_daemon,
    uninstall_systemd_units,
)
from terok_sandbox.gate_server import (
    _UNIT_VERSION,
    _installed_unit_version,
    _is_managed_server,
    is_socket_active,
    is_socket_installed,
)

from tests.testfs import FAKE_GATE_DIR, FAKE_STATE_DIR, NONEXISTENT_DIR
from tests.testnet import GATE_PORT, LOCALHOST

GATE_BASE_PATH = FAKE_GATE_DIR
STATE_ROOT_PATH = FAKE_STATE_DIR
MISSING_PATH = NONEXISTENT_DIR
SYSTEMD_SOCKET = "terok-gate.socket"
SYSTEMD_SERVICE = "terok-gate@.service"
VERSION_STAMP = f"# terok-gate-version: {_UNIT_VERSION}"


def make_status(
    mode: str = "none", *, running: bool = False, port: int = GATE_PORT
) -> GateServerStatus:
    """Create a gate-server status object for tests."""
    return GateServerStatus(mode=mode, running=running, port=port)


def make_run_result(*, returncode: int, stdout: str = "") -> unittest.mock.Mock:
    """Create a mock ``subprocess.run`` result."""
    return unittest.mock.Mock(returncode=returncode, stdout=stdout)


@contextmanager
def patched_unit_dir(files: dict[str, str] | None = None) -> Iterator[Path]:
    """Create a temporary systemd unit dir and patch gate-server to use it."""
    with tempfile.TemporaryDirectory() as td:
        unit_dir = Path(td)
        for name, content in (files or {}).items():
            (unit_dir / name).write_text(content)
        with unittest.mock.patch(
            "terok_sandbox.gate_server._systemd_unit_dir",
            return_value=unit_dir,
        ):
            yield unit_dir


@contextmanager
def patched_install_env(unit_dir: Path) -> Iterator[None]:
    """Patch the standard paths used by install/uninstall/start tests."""
    with (
        unittest.mock.patch(
            "terok_sandbox.gate_server._systemd_unit_dir",
            return_value=unit_dir,
        ),
        unittest.mock.patch("terok_sandbox.gate_server._get_port", return_value=GATE_PORT),
        unittest.mock.patch(
            "terok_sandbox.gate_server._get_gate_base_path",
            return_value=GATE_BASE_PATH,
        ),
        unittest.mock.patch(
            "terok_sandbox.config._state_root",
            return_value=STATE_ROOT_PATH,
        ),
    ):
        yield


@contextmanager
def patched_daemon_paths(base: Path) -> Iterator[Path]:
    """Patch daemon-related runtime paths under a temp directory."""
    pid_file = base / "gate-server.pid"
    with (
        unittest.mock.patch(
            "terok_sandbox.gate_server._get_gate_base_path",
            return_value=base / "gate",
        ),
        unittest.mock.patch(
            "terok_sandbox.gate_server._pid_file",
            return_value=pid_file,
        ),
        unittest.mock.patch(
            "terok_sandbox.config._state_root",
            return_value=base,
        ),
    ):
        yield pid_file


def write_pid_file(base: Path, pid: int | str = 99999) -> Path:
    """Write a PID file in ``base`` and return its path."""
    pid_file = base / "gate-server.pid"
    pid_file.write_text(f"{pid}\n")
    return pid_file


def unit_file_contents(version: int | None = _UNIT_VERSION) -> dict[str, str]:
    """Build socket/service contents with an optional version stamp."""
    prefix = "" if version is None else f"# terok-gate-version: {version}\n"
    return {
        SYSTEMD_SOCKET: f"{prefix}[Socket]\nListenStream={LOCALHOST}:{GATE_PORT}\n",
        SYSTEMD_SERVICE: f"{prefix}[Service]\nExecStart=/usr/local/bin/terok-gate\n",
    }


def assert_contains_all(text: str, expected: tuple[str, ...]) -> None:
    """Assert that all fragments in ``expected`` appear in ``text``."""
    for fragment in expected:
        assert fragment in text


class TestUnitVersion:
    """Tests for _UNIT_VERSION."""

    def test_unit_version_is_3(self) -> None:
        assert _UNIT_VERSION == 3


class TestSystemdDetection:
    """Tests for systemd availability detection."""

    @pytest.mark.parametrize(
        ("returncode", "expected"),
        [(0, True), (1, True), (2, False)],
        ids=["ok", "acceptable-nonzero", "unavailable"],
    )
    @unittest.mock.patch("subprocess.run")
    def test_systemd_availability_from_return_code(
        self,
        mock_run: unittest.mock.Mock,
        returncode: int,
        expected: bool,
    ) -> None:
        mock_run.return_value = make_run_result(returncode=returncode)
        assert is_systemd_available() is expected

    @unittest.mock.patch("subprocess.run", side_effect=FileNotFoundError)
    def test_systemd_not_available_when_missing(self, _mock: unittest.mock.Mock) -> None:
        assert not is_systemd_available()


class TestSocketInstalled:
    """Tests for socket unit file detection."""

    def test_socket_not_installed(self) -> None:
        with unittest.mock.patch(
            "terok_sandbox.gate_server._systemd_unit_dir",
            return_value=MISSING_PATH,
        ):
            assert not is_socket_installed()

    def test_socket_installed(self) -> None:
        with patched_unit_dir({SYSTEMD_SOCKET: "[Socket]\n"}):
            assert is_socket_installed()


class TestSocketActive:
    """Tests for socket active check."""

    @pytest.mark.parametrize(
        ("stdout", "returncode", "expected"),
        [("active\n", 0, True), ("inactive\n", 3, False)],
        ids=["active", "inactive"],
    )
    @unittest.mock.patch("subprocess.run")
    def test_socket_active_from_systemctl(
        self,
        mock_run: unittest.mock.Mock,
        stdout: str,
        returncode: int,
        expected: bool,
    ) -> None:
        mock_run.return_value = make_run_result(returncode=returncode, stdout=stdout)
        assert is_socket_active() is expected

    @unittest.mock.patch("subprocess.run", side_effect=FileNotFoundError)
    def test_socket_inactive_without_systemctl(self, _mock: unittest.mock.Mock) -> None:
        assert not is_socket_active()


class TestInstallUninstall:
    """Tests for systemd unit install/uninstall."""

    @unittest.mock.patch("subprocess.run")
    @unittest.mock.patch("shutil.which", return_value="/usr/local/bin/terok-gate")
    def test_install_writes_files(
        self,
        _mock_which: unittest.mock.Mock,
        mock_run: unittest.mock.Mock,
    ) -> None:
        mock_run.return_value = make_run_result(returncode=0)
        with tempfile.TemporaryDirectory() as td:
            unit_dir = Path(td) / "systemd" / "user"
            with patched_install_env(unit_dir):
                install_systemd_units()

            socket_content = (unit_dir / SYSTEMD_SOCKET).read_text()
            service_content = (unit_dir / SYSTEMD_SERVICE).read_text()
            assert (unit_dir / SYSTEMD_SOCKET).is_file()
            assert (unit_dir / SYSTEMD_SERVICE).is_file()
            assert_contains_all(socket_content, (f"{LOCALHOST}:{GATE_PORT}", VERSION_STAMP))
            assert_contains_all(
                service_content,
                (
                    "ExecStart=/usr/local/bin/terok-gate",
                    str(GATE_BASE_PATH),
                    "--token-file=",
                    VERSION_STAMP,
                ),
            )

    @unittest.mock.patch("subprocess.run")
    @unittest.mock.patch("shutil.which", return_value=None)
    def test_install_fails_without_binary(
        self,
        _mock_which: unittest.mock.Mock,
        _mock_run: unittest.mock.Mock,
    ) -> None:
        with pytest.raises(SystemExit, match="terok-gate"):
            install_systemd_units()

    @unittest.mock.patch("subprocess.run")
    def test_uninstall_removes_files(self, mock_run: unittest.mock.Mock) -> None:
        mock_run.return_value = make_run_result(returncode=0)
        with patched_unit_dir(unit_file_contents()):
            uninstall_systemd_units()
            assert not is_socket_installed()


class TestDaemon:
    """Tests for daemon start/stop."""

    @unittest.mock.patch("subprocess.run")
    def test_start_daemon(self, mock_run: unittest.mock.Mock) -> None:
        mock_run.return_value = make_run_result(returncode=0)
        with tempfile.TemporaryDirectory() as td:
            with patched_daemon_paths(Path(td)):
                start_daemon(port=9999)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "terok-gate"
        assert "--port=9999" in cmd
        assert "--detach" in cmd
        assert any("--base-path=" in arg for arg in cmd)
        assert any("--token-file=" in arg for arg in cmd)

    @unittest.mock.patch(
        "subprocess.run", side_effect=subprocess.CalledProcessError(1, "terok-gate")
    )
    def test_start_daemon_failure(self, _mock: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patched_daemon_paths(Path(td)):
                with pytest.raises(subprocess.CalledProcessError):
                    start_daemon(port=9999)

    def test_stop_daemon_no_pidfile(self) -> None:
        with unittest.mock.patch(
            "terok_sandbox.gate_server._pid_file",
            return_value=MISSING_PATH / "pid",
        ):
            stop_daemon()

    @pytest.mark.parametrize(
        ("managed", "should_kill"),
        [(True, True), (False, False)],
        ids=["managed", "stale"],
    )
    def test_stop_daemon_with_pidfile(self, managed: bool, should_kill: bool) -> None:
        """Stop removes PID files and only kills managed daemons."""
        with tempfile.TemporaryDirectory() as td:
            pid_file = write_pid_file(Path(td))
            with (
                unittest.mock.patch(
                    "terok_sandbox.gate_server._pid_file",
                    return_value=pid_file,
                ),
                unittest.mock.patch(
                    "terok_sandbox.gate_server._is_managed_server",
                    return_value=managed,
                ),
                unittest.mock.patch("os.kill") as mock_kill,
            ):
                stop_daemon()
            assert mock_kill.called is should_kill
            if should_kill:
                mock_kill.assert_called_once_with(99999, unittest.mock.ANY)
            assert not pid_file.exists()


class TestIsDaemonRunning:
    """Tests for is_daemon_running."""

    def test_no_pidfile(self) -> None:
        with unittest.mock.patch(
            "terok_sandbox.gate_server._pid_file",
            return_value=MISSING_PATH / "pid",
        ):
            assert not is_daemon_running()

    @pytest.mark.parametrize(
        ("pid", "managed", "kill_side_effect", "expected"),
        [
            (99999, True, ProcessLookupError, False),
            (os.getpid(), True, None, True),
            (os.getpid(), False, None, False),
        ],
        ids=["stale-pid", "valid-managed-pid", "not-our-daemon"],
    )
    def test_pidfile_states(
        self,
        pid: int,
        managed: bool,
        kill_side_effect: type[BaseException] | None,
        expected: bool,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            pid_file = write_pid_file(Path(td), pid)
            patches = [
                unittest.mock.patch(
                    "terok_sandbox.gate_server._pid_file",
                    return_value=pid_file,
                ),
                unittest.mock.patch(
                    "terok_sandbox.gate_server._is_managed_server",
                    return_value=managed,
                ),
                unittest.mock.patch("os.kill", side_effect=kill_side_effect),
            ]

            with contextlib.ExitStack() as stack:
                for patcher in patches:
                    stack.enter_context(patcher)
                assert is_daemon_running() is expected


class TestIsManagedServer:
    """Tests for _is_managed_server."""

    def test_no_proc_entry(self) -> None:
        assert not _is_managed_server(999999999)

    def test_current_process_is_not_gate_server(self) -> None:
        assert not _is_managed_server(os.getpid())

    def _check_cmdline(self, cmdline: bytes, pid_file: Path | None = None) -> bool:
        """Write *cmdline* to a temp file and call ``_is_managed_server``."""
        with tempfile.TemporaryDirectory() as td:
            fake_cmdline = Path(td) / "cmdline"
            fake_cmdline.write_bytes(cmdline)
            patches = [
                unittest.mock.patch(
                    "terok_sandbox.gate_server.Path",
                    return_value=fake_cmdline,
                ),
            ]
            if pid_file is not None:
                patches.append(
                    unittest.mock.patch(
                        "terok_sandbox.gate_server._pid_file",
                        return_value=pid_file,
                    )
                )
            with contextlib.ExitStack() as stack:
                for patcher in patches:
                    stack.enter_context(patcher)
                return _is_managed_server(12345)

    def test_matches_managed_server(self) -> None:
        pid_file = Path("/run/user/1000/terok/gate-server.pid")
        cmdline = (
            b"terok-gate\x00--base-path="
            + str(GATE_BASE_PATH).encode()
            + b"\x00--pid-file="
            + str(pid_file).encode()
        )
        assert self._check_cmdline(cmdline, pid_file)

    def test_rejects_different_pid_file(self) -> None:
        cmdline = (
            b"terok-gate\x00--base-path="
            + str(GATE_BASE_PATH).encode()
            + b"\x00--pid-file=/other/pid"
        )
        assert not self._check_cmdline(cmdline, Path("/run/user/1000/terok/gate-server.pid"))

    def test_rejects_unrelated_process(self) -> None:
        assert not self._check_cmdline(
            b"python3\x00-m\x00pytest",
            Path("/run/user/1000/terok/gate-server.pid"),
        )


class TestGetServerStatus:
    """Tests for get_server_status."""

    @pytest.mark.parametrize(
        ("socket_installed", "socket_active", "daemon_running", "expected"),
        [
            (False, False, False, make_status("none", running=False)),
            (True, True, False, make_status("systemd", running=True)),
            (True, False, False, make_status("systemd", running=False)),
            (True, False, True, make_status("daemon", running=True)),
            (False, False, True, make_status("daemon", running=True)),
        ],
        ids=[
            "no-server",
            "systemd-active",
            "systemd-inactive",
            "daemon-fallback",
            "daemon-only",
        ],
    )
    def test_status_modes(
        self,
        socket_installed: bool,
        socket_active: bool,
        daemon_running: bool,
        expected: GateServerStatus,
    ) -> None:
        with (
            unittest.mock.patch(
                "terok_sandbox.gate_server.is_socket_installed",
                return_value=socket_installed,
            ),
            unittest.mock.patch(
                "terok_sandbox.gate_server.is_socket_active",
                return_value=socket_active,
            ),
            unittest.mock.patch(
                "terok_sandbox.gate_server.is_daemon_running",
                return_value=daemon_running,
            ),
            unittest.mock.patch("terok_sandbox.gate_server._get_port", return_value=GATE_PORT),
        ):
            assert get_server_status() == expected


class TestEnsureServerReachable:
    """Tests for ensure_server_reachable."""

    @pytest.mark.parametrize(
        ("status", "systemd_available", "unit_version", "error_match"),
        [
            (make_status("daemon", running=True), True, _UNIT_VERSION, None),
            (make_status("none", running=False), True, _UNIT_VERSION, "gate-server install"),
            (make_status("none", running=False), False, _UNIT_VERSION, "gate-server start"),
            (make_status("systemd", running=True), True, 0, "outdated"),
            (make_status("systemd", running=True), True, None, "unversioned"),
            (make_status("systemd", running=True), True, _UNIT_VERSION, None),
        ],
        ids=[
            "daemon-running",
            "stopped-with-systemd",
            "stopped-without-systemd",
            "outdated-units",
            "unversioned-units",
            "current-units",
        ],
    )
    def test_reachability(
        self,
        status: GateServerStatus,
        systemd_available: bool,
        unit_version: int | None,
        error_match: str | None,
    ) -> None:
        with (
            unittest.mock.patch(
                "terok_sandbox.gate_server.get_server_status",
                return_value=status,
            ),
            unittest.mock.patch(
                "terok_sandbox.gate_server.is_systemd_available",
                return_value=systemd_available,
            ),
            unittest.mock.patch(
                "terok_sandbox.gate_server._installed_unit_version",
                return_value=unit_version,
            ),
        ):
            if error_match is None:
                ensure_server_reachable()
            else:
                with pytest.raises(SystemExit, match=error_match):
                    ensure_server_reachable()


class TestInstalledUnitVersion:
    """Tests for _installed_unit_version."""

    def test_no_file(self) -> None:
        with unittest.mock.patch(
            "terok_sandbox.gate_server._systemd_unit_dir",
            return_value=MISSING_PATH,
        ):
            assert _installed_unit_version() is None

    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            ({SYSTEMD_SOCKET: "# terok-gate-version: 42\n[Socket]\n"}, 42),
            ({SYSTEMD_SOCKET: f"[Socket]\nListenStream={LOCALHOST}:{GATE_PORT}\n"}, None),
        ],
        ids=["stamped", "missing-stamp"],
    )
    def test_reads_version_from_socket(self, files: dict[str, str], expected: int | None) -> None:
        with patched_unit_dir(files):
            assert _installed_unit_version() is expected


class TestCheckUnitsOutdated:
    """Tests for check_units_outdated."""

    @pytest.mark.parametrize(
        ("socket_installed", "version", "expected"),
        [
            (False, _UNIT_VERSION, None),
            (True, _UNIT_VERSION, None),
            (True, 1, "outdated"),
            (True, None, "unversioned"),
        ],
        ids=["no-socket", "current", "outdated", "unversioned"],
    )
    def test_outdated_message(
        self,
        socket_installed: bool,
        version: int | None,
        expected: str | None,
    ) -> None:
        with (
            unittest.mock.patch(
                "terok_sandbox.gate_server.is_socket_installed",
                return_value=socket_installed,
            ),
            unittest.mock.patch(
                "terok_sandbox.gate_server._installed_unit_version",
                return_value=version,
            ),
        ):
            result = check_units_outdated()
        if expected is None:
            assert result is None
        else:
            assert result is not None
            assert expected in result
            assert "gate-server install" in result
