# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for version and branch detection functionality."""

from __future__ import annotations

import json
import sys
from importlib.metadata import version as installed_version
from unittest import mock

import pytest


def test_version_attribute_exists() -> None:
    """Test that ``__version__`` exists and is a non-empty string."""
    import terok

    assert hasattr(terok, "__version__")
    assert isinstance(terok.__version__, str)
    assert terok.__version__


def test_version_uses_importlib_metadata() -> None:
    """Test that version can be retrieved from importlib.metadata."""
    pkg_version = installed_version("terok")
    assert isinstance(pkg_version, str)
    assert pkg_version


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("0.4.0", "0.4.0", id="plain-release"),
        pytest.param("0.4.0.post3.dev0+gabcdef", "0.4.0", id="post-dev-local"),
        pytest.param("1.2.3rc1", "1.2.3", id="pre-release"),
        pytest.param("unknown", "unknown", id="non-semver"),
        pytest.param("1.2", "1.2", id="two-segment"),
    ],
)
def test_base_version(value: str, expected: str) -> None:
    """``base_version`` strips suffixes but keeps non-semver strings."""
    from terok.lib.core.version import base_version

    assert base_version(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("0.4.0", "0.4.0", id="release"),
        pytest.param("0.4.0.post3.dev0+gabcdef", "0.4.0+", id="past-release"),
        pytest.param("1.0.0.dev1", "1.0.0+", id="dev-version"),
        pytest.param("unknown", "unknown", id="unknown"),
    ],
)
def test_short_version(value: str, expected: str) -> None:
    """``short_version`` preserves releases and adds ``+`` past them."""
    from terok.lib.core.version import short_version

    assert short_version(value) == expected


@pytest.mark.parametrize(
    ("version", "branch", "expected"),
    [
        pytest.param("1.2.3", "feature-branch", "1.2.3 [feature-branch]", id="with-branch"),
        pytest.param("1.2.3", None, "1.2.3", id="without-branch"),
    ],
)
def test_format_version_string(version: str, branch: str | None, expected: str) -> None:
    """``format_version_string`` appends the branch only when present."""
    from terok.lib.core.version import format_version_string

    assert format_version_string(version, branch) == expected


def test_get_version_info_returns_tuple() -> None:
    """``get_version_info`` returns a ``(version, branch)`` tuple."""
    from terok.lib.core.version import get_version_info

    version, branch = get_version_info()
    assert isinstance(version, str)
    assert branch is None or isinstance(branch, str)


def test_get_version_info_without_branch_data() -> None:
    """Git failures should leave branch detection as ``None``."""
    from terok.lib.core.version import get_version_info

    with (
        mock.patch("terok.lib.core.version.subprocess.run", side_effect=FileNotFoundError),
        mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None),
    ):
        _, branch = get_version_info()

    assert branch is None


def _mock_distribution(
    *, text: str | None = None, side_effect: Exception | None = None
) -> mock.Mock:
    dist = mock.MagicMock()
    if side_effect is not None:
        dist.read_text.side_effect = side_effect
    else:
        dist.read_text.return_value = text
    return dist


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        pytest.param(
            {"vcs_info": {"requested_revision": "feature/foo"}},
            "feature/foo",
            id="requested-revision",
        ),
        pytest.param({"vcs_info": {"commit_id": "abc123"}}, "abc123", id="commit-id-fallback"),
        pytest.param(
            {"vcs_info": {"requested_revision": "feature/foo", "commit_id": "abc123"}},
            "feature/foo",
            id="priority-order",
        ),
        pytest.param({"vcs_info": None}, None, id="null-vcs-info"),
        pytest.param({"vcs_info": "not a dict"}, None, id="non-dict-vcs-info"),
        pytest.param(
            {"vcs_info": {"requested_revision": 123}}, None, id="requested-revision-number"
        ),
        pytest.param(
            {"vcs_info": {"requested_revision": None}}, None, id="requested-revision-null"
        ),
        pytest.param({"vcs_info": {"commit_id": {"sha": "abc"}}}, None, id="commit-id-object"),
        pytest.param({"vcs_info": {"requested_revision": ""}}, None, id="requested-revision-empty"),
        pytest.param({"vcs_info": {"commit_id": ""}}, None, id="commit-id-empty"),
        pytest.param(
            {"vcs_info": {"requested_revision": "   "}}, None, id="requested-revision-whitespace"
        ),
        pytest.param({"vcs_info": {"commit_id": "  \t\n  "}}, None, id="commit-id-whitespace"),
        pytest.param(
            {"vcs_info": {"requested_revision": "  feature/foo  "}},
            "feature/foo",
            id="whitespace-trimmed",
        ),
    ],
)
def test_get_pep610_revision_from_json(direct_url: dict[str, object], expected: str | None) -> None:
    """``_get_pep610_revision`` handles valid and invalid JSON payloads."""
    from terok.lib.core.version import _get_pep610_revision

    dist = _mock_distribution(text=json.dumps(direct_url))
    with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
        assert _get_pep610_revision() == expected


def test_get_pep610_revision_handles_malformed_json() -> None:
    """Malformed ``direct_url.json`` should return ``None``."""
    from terok.lib.core.version import _get_pep610_revision

    dist = _mock_distribution(text="not valid json {")
    with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
        assert _get_pep610_revision() is None


@pytest.mark.parametrize(
    "side_effect",
    [
        pytest.param(FileNotFoundError(), id="file-not-found"),
        pytest.param(PermissionError(), id="permission-error"),
        pytest.param(UnicodeDecodeError("utf-8", b"", 0, 1, "test"), id="unicode-decode-error"),
    ],
)
def test_get_pep610_revision_handles_read_errors(side_effect: Exception) -> None:
    """Read failures should produce ``None`` instead of bubbling up."""
    from terok.lib.core.version import _get_pep610_revision

    dist = _mock_distribution(side_effect=side_effect)
    with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
        assert _get_pep610_revision() is None


def _mock_git_run(
    *,
    in_repo: bool = True,
    branch: str = "main",
    exact_tag: str | None = None,
) -> mock.Mock:
    def _run(*args, **kwargs):
        cmd = args[0]
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = ""

        if "rev-parse" in cmd:
            result.returncode = 0 if in_repo else 1
            result.stdout = "true\n" if in_repo else ""
        elif "branch" in cmd and "--show-current" in cmd:
            result.stdout = f"{branch}\n"
        elif "describe" in cmd and "--exact-match" in cmd:
            if exact_tag is None:
                result.returncode = 1
            else:
                result.stdout = f"{exact_tag}\n"
        return result

    return _run


def test_git_detection_with_branch() -> None:
    """Development installs should surface the current branch name."""
    from terok.lib.core.version import get_version_info

    with (
        mock.patch(
            "terok.lib.core.version.subprocess.run",
            side_effect=_mock_git_run(branch="feature/test-branch"),
        ),
        mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None),
        mock.patch("terok.lib.core.version.Path.exists", return_value=True),
    ):
        _, branch = get_version_info()

    assert branch == "feature/test-branch"


def test_git_detection_suppresses_tagged_release() -> None:
    """Tagged releases should suppress branch display."""
    from terok.lib.core.version import get_version_info

    with (
        mock.patch(
            "terok.lib.core.version.subprocess.run",
            side_effect=_mock_git_run(branch="main", exact_tag="v1.2.3"),
        ),
        mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None),
        mock.patch("terok.lib.core.version.Path.exists", return_value=True),
    ):
        _, branch = get_version_info()

    assert branch is None


def test_git_detection_not_in_git_repository() -> None:
    """Without a git repo, branch detection should return ``None``."""
    from terok.lib.core.version import get_version_info

    with (
        mock.patch(
            "terok.lib.core.version.subprocess.run",
            side_effect=_mock_git_run(in_repo=False),
        ),
        mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None),
        mock.patch("terok.lib.core.version.Path.exists", return_value=True),
    ):
        _, branch = get_version_info()

    assert branch is None


def test_git_detection_no_pyproject() -> None:
    """Without ``pyproject.toml``, live git detection is skipped entirely."""
    from terok.lib.core.version import get_version_info

    with (
        mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None),
        mock.patch("terok.lib.core.version.Path.exists", return_value=False),
        mock.patch("terok.lib.core.version.subprocess.run") as mock_run,
    ):
        _, branch = get_version_info()

    assert branch is None
    mock_run.assert_not_called()


def test_git_detection_empty_branch_name() -> None:
    """Detached HEAD state should yield ``None`` for the branch name."""
    from terok.lib.core.version import get_version_info

    with (
        mock.patch(
            "terok.lib.core.version.subprocess.run",
            side_effect=_mock_git_run(branch=""),
        ),
        mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None),
        mock.patch("terok.lib.core.version.Path.exists", return_value=True),
    ):
        _, branch = get_version_info()

    assert branch is None


@pytest.mark.parametrize(
    "side_effect",
    [
        pytest.param(__import__("subprocess").TimeoutExpired(cmd="git", timeout=1), id="timeout"),
        pytest.param(FileNotFoundError("git not found"), id="git-missing"),
    ],
)
def test_git_detection_errors_return_none(side_effect: Exception) -> None:
    """Operational git errors should be handled gracefully."""
    from terok.lib.core.version import get_version_info

    with (
        mock.patch("terok.lib.core.version.subprocess.run", side_effect=side_effect),
        mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None),
        mock.patch("terok.lib.core.version.Path.exists", return_value=True),
    ):
        _, branch = get_version_info()

    assert branch is None


def test_pep610_takes_priority_over_git() -> None:
    """PEP 610 metadata should win over live git detection."""
    from terok.lib.core.version import get_version_info

    with (
        mock.patch("terok.lib.core.version._get_pep610_revision", return_value="vcs-branch"),
        mock.patch("terok.lib.core.version.Path.exists", return_value=True),
        mock.patch("terok.lib.core.version.subprocess.run") as mock_run,
    ):
        _, branch = get_version_info()

    assert branch == "vcs-branch"
    mock_run.assert_not_called()


def run_cli_version() -> mock.Mock:
    """Run ``terokctl --version`` with the current interpreter."""
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "terok.cli.main", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_version_flag() -> None:
    """``terokctl --version`` should succeed and print version info."""
    result = run_cli_version()
    assert result.returncode == 0
    assert "terok" in result.stdout
    assert any(char.isdigit() for char in result.stdout)


def test_cli_version_matches_module_version() -> None:
    """CLI version output should match the module formatter."""
    from terok.lib.core.version import format_version_string, get_version_info

    version, branch = get_version_info()
    expected_version_str = format_version_string(version, branch)

    result = run_cli_version()
    assert expected_version_str in result.stdout
