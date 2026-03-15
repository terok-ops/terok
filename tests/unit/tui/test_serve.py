# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terok-web serve entry point."""

from __future__ import annotations

import argparse
import sys
from unittest import mock

import pytest

from terok.tui.serve import _valid_port, main


class TestValidPort:
    """Tests for port validation."""

    @pytest.mark.parametrize("value", ["1", "80", "8566", "65535"])
    def test_accepts_valid_ports(self, value: str) -> None:
        """Valid port numbers are returned as integers."""
        assert _valid_port(value) == int(value)

    @pytest.mark.parametrize("value", ["0", "-1", "65536", "99999"])
    def test_rejects_out_of_range(self, value: str) -> None:
        """Out-of-range port numbers raise ArgumentTypeError with descriptive message."""
        with pytest.raises(argparse.ArgumentTypeError, match="must be between 1 and 65535"):
            _valid_port(value)

    @pytest.mark.parametrize("value", ["abc", "", "12.5"])
    def test_rejects_non_integer(self, value: str) -> None:
        """Non-integer strings raise ArgumentTypeError with descriptive message."""
        with pytest.raises(argparse.ArgumentTypeError, match="must be an integer"):
            _valid_port(value)


class TestMain:
    """Tests for the main entry point."""

    def test_missing_textual_serve_exits(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When textual-serve is not installed, main prints guidance and exits."""
        monkeypatch.setitem(sys.modules, "textual_serve", None)
        monkeypatch.setitem(sys.modules, "textual_serve.server", None)
        with pytest.raises(SystemExit, match="1"):
            main()
        captured = capsys.readouterr()
        assert "textual-serve" in captured.err
        assert "pip install textual-serve" in captured.err

    def test_server_created_with_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Server is instantiated with default host and port when no args given."""
        mock_server_instance = mock.MagicMock()
        mock_server_cls = mock.MagicMock(return_value=mock_server_instance)

        server_mod = mock.MagicMock()
        server_mod.Server = mock_server_cls

        monkeypatch.setitem(sys.modules, "textual_serve", mock.MagicMock())
        monkeypatch.setitem(sys.modules, "textual_serve.server", server_mod)
        monkeypatch.setattr("sys.argv", ["terok-web"])

        main()

        mock_server_cls.assert_called_once_with("terok", host="localhost", port=8566)
        mock_server_instance.serve.assert_called_once()

    def test_server_created_with_custom_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Server respects --host and --port arguments."""
        mock_server_instance = mock.MagicMock()
        mock_server_cls = mock.MagicMock(return_value=mock_server_instance)

        server_mod = mock.MagicMock()
        server_mod.Server = mock_server_cls

        monkeypatch.setitem(sys.modules, "textual_serve", mock.MagicMock())
        monkeypatch.setitem(sys.modules, "textual_serve.server", server_mod)
        monkeypatch.setattr("sys.argv", ["terok-web", "--host", "0.0.0.0", "--port", "9000"])

        main()

        mock_server_cls.assert_called_once_with("terok", host="0.0.0.0", port=9000)
        mock_server_instance.serve.assert_called_once()
