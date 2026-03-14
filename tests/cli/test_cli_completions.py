# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the completions CLI subcommand."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from terok.cli.commands import completions


@pytest.fixture()
def patch_completion_locations(monkeypatch):
    """Return a helper that replaces completion search locations for one test."""

    def _apply(
        *,
        bash: tuple[Path, ...] = (),
        zsh: tuple[Path, ...] = (),
        fish: tuple[Path, ...] = (),
        rc: tuple[Path, ...] = (),
    ) -> None:
        monkeypatch.setattr(completions, "_BASH_COMPLETION_DIRS", bash)
        monkeypatch.setattr(completions, "_ZSH_COMPLETION_DIRS", zsh)
        monkeypatch.setattr(completions, "_FISH_COMPLETION_DIRS", fish)
        monkeypatch.setattr(completions, "_SHELL_RC_FILES", rc)

    return _apply


@pytest.mark.parametrize(
    ("shell", "expected"),
    [
        pytest.param("/bin/bash", "bash", id="bash"),
        pytest.param("/usr/bin/zsh", "zsh", id="zsh"),
        pytest.param("/usr/bin/fish", "fish", id="fish"),
    ],
)
def test_detect_shell_returns_supported_shell(
    monkeypatch,
    shell: str,
    expected: str,
) -> None:
    monkeypatch.setenv("SHELL", shell)
    assert completions._detect_shell() == expected


@pytest.mark.parametrize(
    "shell",
    [pytest.param("/bin/tcsh", id="unsupported"), pytest.param(None, id="missing")],
)
def test_detect_shell_rejects_unknown_shell(monkeypatch, shell: str | None) -> None:
    if shell is None:
        monkeypatch.delenv("SHELL", raising=False)
    else:
        monkeypatch.setenv("SHELL", shell)

    with pytest.raises(SystemExit):
        completions._detect_shell()


@patch("terok.cli.commands.completions.shellcode", return_value="# completion")
def test_install_completions_writes_to_requested_target(
    _mock_shellcode,
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    target = tmp_path / "nested" / "dir" / "terokctl"
    monkeypatch.setattr(
        completions,
        "_INSTALL_TARGETS",
        {"bash": target, "zsh": Path("/unused"), "fish": Path("/unused")},
    )

    completions._install_completions("bash")

    assert target.is_file()
    assert "# completion" in target.read_text(encoding="utf-8")
    assert str(target) in capsys.readouterr().out


@patch("terok.cli.commands.completions.shellcode", return_value="# comp")
def test_install_completions_auto_detects_shell(
    _mock_shellcode,
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "terokctl.fish"
    monkeypatch.setattr(
        completions,
        "_INSTALL_TARGETS",
        {"bash": Path("/unused"), "zsh": Path("/unused"), "fish": target},
    )
    monkeypatch.setattr(completions, "_detect_shell", lambda: "fish")

    completions._install_completions(None)

    assert target.is_file()


def test_is_completion_installed_returns_false_when_nothing_found(
    patch_completion_locations,
) -> None:
    patch_completion_locations()
    assert not completions.is_completion_installed()


@pytest.mark.parametrize(
    ("attr", "filename"),
    [
        pytest.param("bash", "terokctl", id="bash-autoload"),
        pytest.param("zsh", "_terokctl", id="zsh-autoload"),
        pytest.param("fish", "terokctl.fish", id="fish-autoload"),
    ],
)
def test_is_completion_installed_detects_autoload_files(
    patch_completion_locations,
    tmp_path: Path,
    attr: str,
    filename: str,
) -> None:
    (tmp_path / filename).write_text("# comp", encoding="utf-8")
    patch_completion_locations(**{attr: (tmp_path,)})

    assert completions.is_completion_installed()


def test_is_completion_installed_detects_rc_marker(
    patch_completion_locations,
    tmp_path: Path,
) -> None:
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("# register-python-argcomplete terokctl\n", encoding="utf-8")
    patch_completion_locations(rc=(rc_file,))

    assert completions.is_completion_installed()
