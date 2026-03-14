# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from terok.ui_utils.editor import _resolve_editor, open_in_editor


def _which_for(*available: str):
    available_set = set(available)
    return lambda cmd: cmd if cmd in available_set else None


@pytest.mark.parametrize(
    ("editor", "which_side_effect", "expected"),
    [
        pytest.param(
            "/usr/bin/custom-editor",
            _which_for("/usr/bin/custom-editor"),
            "/usr/bin/custom-editor",
            id="prefers-editor-env",
        ),
        pytest.param("", _which_for("nano"), "nano", id="falls-back-to-nano"),
        pytest.param("", _which_for("vi"), "vi", id="falls-back-to-vi"),
        pytest.param("   ", _which_for("nano"), "nano", id="ignores-whitespace-editor"),
        pytest.param("nonexistent", _which_for("nano"), "nano", id="invalid-editor-env-falls-back"),
    ],
)
def test_resolve_editor_prefers_env_then_fallbacks(
    monkeypatch,
    editor: str,
    which_side_effect,
    expected: str,
) -> None:
    monkeypatch.setenv("EDITOR", editor)
    with patch("shutil.which", side_effect=which_side_effect):
        assert _resolve_editor() == expected


def test_resolve_editor_returns_none_when_no_editor(monkeypatch) -> None:
    monkeypatch.setenv("EDITOR", "")
    with patch("shutil.which", return_value=None):
        assert _resolve_editor() is None


@patch("terok.ui_utils.editor._resolve_editor", return_value="nano")
@patch("subprocess.run")
def test_open_in_editor_returns_true_on_success(
    mock_run,
    _mock_resolve,
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yml"
    path.write_text("x", encoding="utf-8")

    assert open_in_editor(path)
    mock_run.assert_called_once_with(["nano", str(path)], check=True)


@patch("terok.ui_utils.editor._resolve_editor", return_value=None)
def test_open_in_editor_returns_false_without_editor(
    _mock_resolve,
    tmp_path: Path,
    capsys,
) -> None:
    path = tmp_path / "config.yml"
    path.write_text("x", encoding="utf-8")

    assert not open_in_editor(path)
    err = capsys.readouterr().err
    assert "EDITOR" in err
    assert err


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(subprocess.CalledProcessError(1, "nano"), id="called-process-error"),
        pytest.param(FileNotFoundError(), id="file-not-found"),
    ],
)
@patch("terok.ui_utils.editor._resolve_editor", return_value="nano")
def test_open_in_editor_returns_false_on_launch_failure(
    _mock_resolve,
    error: Exception,
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yml"
    path.write_text("x", encoding="utf-8")

    with patch("subprocess.run", side_effect=error):
        assert not open_in_editor(path)
