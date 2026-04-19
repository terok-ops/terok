# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for version and branch detection functionality."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from importlib.metadata import version as installed_version
from unittest import mock

import pytest


def test_version_attribute_exists() -> None:
    """The package exports a non-empty ``__version__`` string."""
    import terok

    assert isinstance(terok.__version__, str)
    assert terok.__version__


def test_version_uses_importlib_metadata() -> None:
    """The installed distribution exposes a non-empty version string."""
    assert installed_version("terok")


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
    """``base_version`` strips suffixes but preserves non-semver inputs."""
    from terok.lib.core.version import base_version

    assert base_version(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("0.4.0", "0.4.0", id="release"),
        pytest.param("0.7.4.post4.dev0+549a07a", "0.7.4.post4", id="dynver-post-release"),
        pytest.param("0.4.0.post3+gabcdef", "0.4.0.post3", id="post-without-dev"),
        pytest.param("1.0.0.dev1", "1.0.0.dev1", id="dev-preserved"),
        pytest.param("1.0.0.dev1+gabcdef", "1.0.0.dev1", id="dev-with-local-stripped"),
        pytest.param("1.2.3rc1", "1.2.3rc1", id="rc-preserved"),
        pytest.param("unknown", "unknown", id="unknown"),
    ],
)
def test_short_version(value: str, expected: str) -> None:
    """``short_version`` strips only the git-hash local segment and the redundant ``.devN`` that dynver appends to ``.postN``."""
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
    """``format_version_string`` only appends the branch when one is present."""
    from terok.lib.core.version import format_version_string

    assert format_version_string(version, branch) == expected


def test_get_version_info_returns_tuple() -> None:
    """``get_version_info`` returns a ``(version, branch)`` tuple."""
    from terok.lib.core.version import get_version_info

    version, branch = get_version_info()
    assert isinstance(version, str)
    assert branch is None or isinstance(branch, str)


@contextmanager
def patched_version_detection(
    *,
    pep610: str | None,
    pyproject_exists: bool,
    git_side_effect=None,
):
    """Patch the version-detection environment for one test."""
    with (
        mock.patch("terok.lib.core.version._get_pep610_revision", return_value=pep610),
        mock.patch("terok.lib.core.version.Path.exists", return_value=pyproject_exists),
        mock.patch(
            "terok.lib.core.version.subprocess.run", side_effect=git_side_effect
        ) as mock_run,
    ):
        yield mock_run


def distribution_mock(
    *,
    text: str | None = None,
    side_effect: Exception | None = None,
) -> mock.Mock:
    """Build a fake ``importlib.metadata`` distribution object."""
    dist = mock.MagicMock()
    if side_effect is None:
        dist.read_text.return_value = text
    else:
        dist.read_text.side_effect = side_effect
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
            {"vcs_info": {"requested_revision": 123}},
            None,
            id="requested-revision-number",
        ),
        pytest.param(
            {"vcs_info": {"requested_revision": None}}, None, id="requested-revision-null"
        ),
        pytest.param({"vcs_info": {"commit_id": {"sha": "abc"}}}, None, id="commit-id-object"),
        pytest.param({"vcs_info": {"requested_revision": ""}}, None, id="requested-revision-empty"),
        pytest.param({"vcs_info": {"commit_id": ""}}, None, id="commit-id-empty"),
        pytest.param(
            {"vcs_info": {"requested_revision": "   "}},
            None,
            id="requested-revision-whitespace",
        ),
        pytest.param(
            {"vcs_info": {"commit_id": "  \t\n  "}},
            None,
            id="commit-id-whitespace",
        ),
        pytest.param(
            {"vcs_info": {"requested_revision": "  feature/foo  "}},
            "feature/foo",
            id="whitespace-trimmed",
        ),
    ],
)
def test_get_pep610_revision_from_json(direct_url: dict[str, object], expected: str | None) -> None:
    """``_get_pep610_revision`` handles valid and invalid ``direct_url.json`` payloads."""
    from terok.lib.core.version import _get_pep610_revision

    with mock.patch(
        "terok.lib.core.version.metadata.distribution",
        return_value=distribution_mock(text=json.dumps(direct_url)),
    ):
        assert _get_pep610_revision() == expected


@pytest.mark.parametrize(
    "distribution_side_effect",
    [
        pytest.param("not valid json {", id="malformed-json"),
        pytest.param(FileNotFoundError(), id="file-not-found"),
        pytest.param(PermissionError(), id="permission-error"),
        pytest.param(UnicodeDecodeError("utf-8", b"", 0, 1, "test"), id="unicode-decode-error"),
    ],
)
def test_get_pep610_revision_handles_invalid_data(distribution_side_effect: object) -> None:
    """Malformed content and read errors both yield ``None``."""
    from terok.lib.core.version import _get_pep610_revision

    distribution = (
        distribution_mock(text=distribution_side_effect)
        if isinstance(distribution_side_effect, str)
        else distribution_mock(side_effect=distribution_side_effect)
    )
    with mock.patch("terok.lib.core.version.metadata.distribution", return_value=distribution):
        assert _get_pep610_revision() is None


def mock_git_run(
    *,
    in_repo: bool = True,
    branch: str = "main",
    exact_tag: str | None = None,
) -> mock.Mock:
    """Build a ``subprocess.run`` side effect for git-version detection."""

    def _run(*args, **kwargs):
        cmd = args[0]
        result = mock.MagicMock(returncode=0, stdout="")
        if "rev-parse" in cmd:
            result.returncode = 0 if in_repo else 1
            result.stdout = "true\n" if in_repo else ""
        elif "branch" in cmd and "--show-current" in cmd:
            result.stdout = f"{branch}\n"
        elif "describe" in cmd and "--exact-match" in cmd and exact_tag is None:
            result.returncode = 1
        elif "describe" in cmd and "--exact-match" in cmd:
            result.stdout = f"{exact_tag}\n"
        return result

    return _run


@pytest.mark.parametrize(
    ("git_side_effect", "pep610", "pyproject_exists", "expected_branch", "expect_git_calls"),
    [
        pytest.param(
            mock_git_run(branch="feature/test-branch"),
            None,
            True,
            "feature/test-branch",
            True,
            id="git-branch",
        ),
        pytest.param(
            mock_git_run(branch="main", exact_tag="v1.2.3"),
            None,
            True,
            None,
            True,
            id="tagged-release",
        ),
        pytest.param(mock_git_run(in_repo=False), None, True, None, True, id="not-in-repo"),
        pytest.param(mock_git_run(branch=""), None, True, None, True, id="empty-branch"),
        pytest.param(FileNotFoundError("git not found"), None, True, None, True, id="git-missing"),
        pytest.param(
            subprocess.TimeoutExpired(cmd="git", timeout=1),
            None,
            True,
            None,
            True,
            id="git-timeout",
        ),
        pytest.param(None, None, False, None, False, id="no-pyproject"),
        pytest.param(None, "vcs-branch", True, "vcs-branch", False, id="pep610-priority"),
    ],
)
def test_get_version_info_branch_detection(
    git_side_effect,
    pep610: str | None,
    pyproject_exists: bool,
    expected_branch: str | None,
    expect_git_calls: bool,
) -> None:
    """Live git branch detection obeys PEP 610, git errors, and pyproject presence."""
    from terok.lib.core.version import get_version_info

    with patched_version_detection(
        pep610=pep610,
        pyproject_exists=pyproject_exists,
        git_side_effect=git_side_effect,
    ) as mock_run:
        _, branch = get_version_info()

    assert branch == expected_branch
    if expect_git_calls:
        assert mock_run.called
    else:
        mock_run.assert_not_called()


def run_cli_version() -> subprocess.CompletedProcess[str]:
    """Run ``terok --version`` with the current interpreter."""
    return subprocess.run(
        [sys.executable, "-m", "terok.cli.main", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_version_flag() -> None:
    """``terok --version`` succeeds and prints version information."""
    result = run_cli_version()
    assert result.returncode == 0
    assert "terok" in result.stdout
    assert any(char.isdigit() for char in result.stdout)


def test_cli_version_matches_module_version() -> None:
    """CLI version output matches the module-level formatter."""
    from terok.lib.core.version import format_version_string, get_version_info

    version, branch = get_version_info()
    assert format_version_string(version, branch) in run_cli_version().stdout
