# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for version and branch detection functionality."""

import json
import unittest
from unittest import mock


class VersionDetectionTests(unittest.TestCase):
    """Test version detection in __init__.py."""

    def test_version_attribute_exists(self) -> None:
        """Test that __version__ attribute exists and is a string."""
        import terok

        self.assertTrue(hasattr(terok, "__version__"))
        self.assertIsInstance(terok.__version__, str)
        self.assertNotEqual(terok.__version__, "")

    def test_version_uses_importlib_metadata(self) -> None:
        """Test that version can be retrieved from importlib.metadata."""
        from importlib.metadata import version

        # This should work when the package is installed
        pkg_version = version("terok")
        self.assertIsInstance(pkg_version, str)
        self.assertNotEqual(pkg_version, "")


class BaseVersionTests(unittest.TestCase):
    """Test base_version() helper."""

    def test_plain_release(self) -> None:
        """Plain release version is returned as-is."""
        from terok.lib.core.version import base_version

        self.assertEqual(base_version("0.4.0"), "0.4.0")

    def test_post_dev_local(self) -> None:
        """Post/dev/local suffixes are stripped."""
        from terok.lib.core.version import base_version

        self.assertEqual(base_version("0.4.0.post3.dev0+gabcdef"), "0.4.0")

    def test_pre_release(self) -> None:
        """Pre-release suffix is stripped."""
        from terok.lib.core.version import base_version

        self.assertEqual(base_version("1.2.3rc1"), "1.2.3")

    def test_non_semver_fallback(self) -> None:
        """Non-semver string is returned unchanged."""
        from terok.lib.core.version import base_version

        self.assertEqual(base_version("unknown"), "unknown")

    def test_two_segment(self) -> None:
        """Two-segment version without patch is returned unchanged."""
        from terok.lib.core.version import base_version

        self.assertEqual(base_version("1.2"), "1.2")


class ShortVersionTests(unittest.TestCase):
    """Test short_version() helper."""

    def test_at_release(self) -> None:
        """At a tagged release, returns plain version."""
        from terok.lib.core.version import short_version

        self.assertEqual(short_version("0.4.0"), "0.4.0")

    def test_past_release(self) -> None:
        """Past a release, returns version with trailing +."""
        from terok.lib.core.version import short_version

        self.assertEqual(short_version("0.4.0.post3.dev0+gabcdef"), "0.4.0+")

    def test_dev_version(self) -> None:
        """Dev version gets trailing +."""
        from terok.lib.core.version import short_version

        self.assertEqual(short_version("1.0.0.dev1"), "1.0.0+")

    def test_unknown_passthrough(self) -> None:
        """Non-semver 'unknown' is returned unchanged."""
        from terok.lib.core.version import short_version

        self.assertEqual(short_version("unknown"), "unknown")


class VersionModuleTests(unittest.TestCase):
    """Test terok.lib.core.version module."""

    def test_format_version_string_with_branch(self) -> None:
        """Test format_version_string with a branch name."""
        from terok.lib.core.version import format_version_string

        result = format_version_string("1.2.3", "feature-branch")
        self.assertEqual(result, "1.2.3 [feature-branch]")

    def test_format_version_string_without_branch(self) -> None:
        """Test format_version_string without a branch name."""
        from terok.lib.core.version import format_version_string

        result = format_version_string("1.2.3", None)
        self.assertEqual(result, "1.2.3")

    def test_get_version_info_returns_tuple(self) -> None:
        """Test that get_version_info returns a tuple of (version, branch)."""
        from terok.lib.core.version import get_version_info

        result = get_version_info()
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        version, branch = result
        self.assertIsInstance(version, str)
        self.assertTrue(branch is None or isinstance(branch, str))

    def test_get_version_info_without_branch_data(self) -> None:
        """Test get_version_info when no branch data is available."""
        # Mock subprocess to fail git detection (simulating tarball install)
        with mock.patch("terok.lib.core.version.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")

            from terok.lib.core.version import get_version_info

            with mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None):
                _, branch = get_version_info()
            # Branch should be None when PEP 610 is absent and git detection fails
            self.assertIsNone(branch)


class Pep610Tests(unittest.TestCase):
    """Test PEP 610 direct_url.json handling."""

    def test_pep610_requested_revision(self) -> None:
        """Use requested_revision when present."""
        from terok.lib.core.version import _get_pep610_revision

        direct_url = json.dumps({"vcs_info": {"requested_revision": "feature/foo"}})
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertEqual(_get_pep610_revision(), "feature/foo")

    def test_pep610_commit_id_fallback(self) -> None:
        """Fallback to commit_id when requested_revision is missing."""
        from terok.lib.core.version import _get_pep610_revision

        direct_url = json.dumps({"vcs_info": {"commit_id": "abc123"}})
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertEqual(_get_pep610_revision(), "abc123")

    def test_pep610_priority_order(self) -> None:
        """Requested_revision takes priority over commit_id."""
        from terok.lib.core.version import _get_pep610_revision

        direct_url = json.dumps(
            {"vcs_info": {"requested_revision": "feature/foo", "commit_id": "abc123"}}
        )
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            # Should return requested_revision, not commit_id
            self.assertEqual(_get_pep610_revision(), "feature/foo")

    def test_pep610_malformed_json(self) -> None:
        """Handle malformed JSON in direct_url.json."""
        from terok.lib.core.version import _get_pep610_revision

        dist = mock.MagicMock()
        dist.read_text.return_value = "not valid json {"

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

    def test_pep610_vcs_info_null(self) -> None:
        """Handle vcs_info being null."""
        from terok.lib.core.version import _get_pep610_revision

        direct_url = json.dumps({"vcs_info": None})
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

    def test_pep610_vcs_info_not_dict(self) -> None:
        """Handle vcs_info being a non-dict value."""
        from terok.lib.core.version import _get_pep610_revision

        direct_url = json.dumps({"vcs_info": "not a dict"})
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

    def test_pep610_requested_revision_not_string(self) -> None:
        """Handle requested_revision being a non-string value."""
        from terok.lib.core.version import _get_pep610_revision

        # Test with number
        direct_url = json.dumps({"vcs_info": {"requested_revision": 123}})
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

        # Test with null
        direct_url = json.dumps({"vcs_info": {"requested_revision": None}})
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

    def test_pep610_commit_id_not_string(self) -> None:
        """Handle commit_id being a non-string value."""
        from terok.lib.core.version import _get_pep610_revision

        # Test with object
        direct_url = json.dumps({"vcs_info": {"commit_id": {"sha": "abc"}}})
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

    def test_pep610_empty_string_requested_revision(self) -> None:
        """Handle empty string for requested_revision."""
        from terok.lib.core.version import _get_pep610_revision

        direct_url = json.dumps({"vcs_info": {"requested_revision": ""}})
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

    def test_pep610_empty_string_commit_id(self) -> None:
        """Handle empty string for commit_id."""
        from terok.lib.core.version import _get_pep610_revision

        direct_url = json.dumps({"vcs_info": {"commit_id": ""}})
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

    def test_pep610_whitespace_only_requested_revision(self) -> None:
        """Handle whitespace-only string for requested_revision."""
        from terok.lib.core.version import _get_pep610_revision

        direct_url = json.dumps({"vcs_info": {"requested_revision": "   "}})
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

    def test_pep610_whitespace_only_commit_id(self) -> None:
        """Handle whitespace-only string for commit_id."""
        from terok.lib.core.version import _get_pep610_revision

        direct_url = json.dumps({"vcs_info": {"commit_id": "  \t\n  "}})
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

    def test_pep610_whitespace_trimmed(self) -> None:
        """Verify that whitespace is trimmed from revision strings."""
        from terok.lib.core.version import _get_pep610_revision

        direct_url = json.dumps({"vcs_info": {"requested_revision": "  feature/foo  "}})
        dist = mock.MagicMock()
        dist.read_text.return_value = direct_url

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertEqual(_get_pep610_revision(), "feature/foo")

    def test_pep610_file_not_found(self) -> None:
        """Handle missing direct_url.json file."""
        from terok.lib.core.version import _get_pep610_revision

        dist = mock.MagicMock()
        dist.read_text.side_effect = FileNotFoundError()

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

    def test_pep610_permission_error(self) -> None:
        """Handle permission error when reading direct_url.json."""
        from terok.lib.core.version import _get_pep610_revision

        dist = mock.MagicMock()
        dist.read_text.side_effect = PermissionError()

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())

    def test_pep610_unicode_decode_error(self) -> None:
        """Handle unicode decode error when reading direct_url.json."""
        from terok.lib.core.version import _get_pep610_revision

        dist = mock.MagicMock()
        dist.read_text.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "test")

        with mock.patch("terok.lib.core.version.metadata.distribution", return_value=dist):
            self.assertIsNone(_get_pep610_revision())


class LiveGitDetectionTests(unittest.TestCase):
    """Test live git detection (Strategy 2) for development mode."""

    def test_git_detection_with_branch(self) -> None:
        """Test successful git branch detection in development mode."""
        from terok.lib.core.version import get_version_info

        # Mock subprocess to simulate git detection
        def mock_run(*args, **kwargs):
            cmd = args[0]
            result = mock.MagicMock()
            result.returncode = 0

            if "rev-parse" in cmd:
                result.stdout = "true\n"
            elif "branch" in cmd and "--show-current" in cmd:
                result.stdout = "feature/test-branch\n"
            elif "describe" in cmd and "--exact-match" in cmd:
                result.returncode = 1  # Not at a tag
                result.stdout = ""

            return result

        with mock.patch("terok.lib.core.version.subprocess.run", side_effect=mock_run):
            with mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None):
                # Mock pyproject.toml exists
                with mock.patch("terok.lib.core.version.Path.exists", return_value=True):
                    _, branch = get_version_info()
                    self.assertEqual(branch, "feature/test-branch")

    def test_git_detection_suppresses_tagged_release(self) -> None:
        """Test that branch name is suppressed when HEAD is at a version tag."""
        from terok.lib.core.version import get_version_info

        def mock_run(*args, **kwargs):
            cmd = args[0]
            result = mock.MagicMock()
            result.returncode = 0

            if "rev-parse" in cmd:
                result.stdout = "true\n"
            elif "branch" in cmd and "--show-current" in cmd:
                result.stdout = "main\n"
            elif "describe" in cmd and "--exact-match" in cmd:
                # At a version tag
                result.stdout = "v1.2.3\n"

            return result

        with mock.patch("terok.lib.core.version.subprocess.run", side_effect=mock_run):
            with mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None):
                with mock.patch("terok.lib.core.version.Path.exists", return_value=True):
                    _, branch = get_version_info()
                    # Branch should be None at a tagged release
                    self.assertIsNone(branch)

    def test_git_detection_not_in_git_repository(self) -> None:
        """Test behavior when pyproject.toml exists but not in a git repository."""
        from terok.lib.core.version import get_version_info

        def mock_run(*args, **kwargs):
            result = mock.MagicMock()
            result.returncode = 1  # git rev-parse fails
            result.stdout = ""
            return result

        with mock.patch("terok.lib.core.version.subprocess.run", side_effect=mock_run):
            with mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None):
                with mock.patch("terok.lib.core.version.Path.exists", return_value=True):
                    _, branch = get_version_info()
                    # Branch should be None when not in a git repo
                    self.assertIsNone(branch)

    def test_git_detection_no_pyproject(self) -> None:
        """Test that git detection is skipped when pyproject.toml doesn't exist."""
        from terok.lib.core.version import get_version_info

        # This should prevent git detection from even being attempted
        with mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None):
            with mock.patch("terok.lib.core.version.Path.exists", return_value=False):
                # Should not call subprocess at all
                with mock.patch("terok.lib.core.version.subprocess.run") as mock_run:
                    _, branch = get_version_info()
                    # Branch should be None
                    self.assertIsNone(branch)
                    # Git should not have been invoked
                    mock_run.assert_not_called()

    def test_git_detection_empty_branch_name(self) -> None:
        """Test behavior when git returns an empty branch name."""
        from terok.lib.core.version import get_version_info

        def mock_run(*args, **kwargs):
            cmd = args[0]
            result = mock.MagicMock()
            result.returncode = 0

            if "rev-parse" in cmd:
                result.stdout = "true\n"
            elif "branch" in cmd and "--show-current" in cmd:
                result.stdout = "\n"  # Empty branch name (detached HEAD)

            return result

        with mock.patch("terok.lib.core.version.subprocess.run", side_effect=mock_run):
            with mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None):
                with mock.patch("terok.lib.core.version.Path.exists", return_value=True):
                    _, branch = get_version_info()
                    # Branch should be None for empty branch name
                    self.assertIsNone(branch)

    def test_git_detection_timeout_error(self) -> None:
        """Test that git timeout errors are handled gracefully."""
        import subprocess

        from terok.lib.core.version import get_version_info

        with mock.patch("terok.lib.core.version.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=1)

            with mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None):
                with mock.patch("terok.lib.core.version.Path.exists", return_value=True):
                    _, branch = get_version_info()
                    # Should handle timeout gracefully
                    self.assertIsNone(branch)

    def test_git_detection_git_not_available(self) -> None:
        """Test behavior when git command is not available."""
        from terok.lib.core.version import get_version_info

        with mock.patch("terok.lib.core.version.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")

            with mock.patch("terok.lib.core.version._get_pep610_revision", return_value=None):
                with mock.patch("terok.lib.core.version.Path.exists", return_value=True):
                    _, branch = get_version_info()
                    # Should handle git not available gracefully
                    self.assertIsNone(branch)

    def test_pep610_takes_priority_over_git(self) -> None:
        """Test that PEP 610 metadata takes priority over live git detection."""
        from terok.lib.core.version import get_version_info

        # Even with pyproject.toml present, PEP 610 should win
        with mock.patch("terok.lib.core.version._get_pep610_revision", return_value="vcs-branch"):
            with mock.patch("terok.lib.core.version.Path.exists", return_value=True):
                with mock.patch("terok.lib.core.version.subprocess.run") as mock_run:
                    _, branch = get_version_info()
                    # Should use PEP 610 revision
                    self.assertEqual(branch, "vcs-branch")
                    # Git should not have been invoked
                    mock_run.assert_not_called()


class CLIVersionTests(unittest.TestCase):
    """Test CLI --version flag."""

    def test_cli_version_flag(self) -> None:
        """Test that terokctl --version outputs version info."""
        import subprocess

        result = subprocess.run(
            ["python", "-m", "terok.cli.main", "--version"],
            capture_output=True,
            text=True,
        )
        # --version exits with code 0
        self.assertEqual(result.returncode, 0)
        # Output should contain "terok" and version number
        self.assertIn("terok", result.stdout)
        # Should have some version-like string
        self.assertRegex(result.stdout, r"\d+\.\d+")

    def test_cli_version_matches_module_version(self) -> None:
        """Test that CLI --version matches the module version."""
        import subprocess

        from terok.lib.core.version import format_version_string, get_version_info

        version, branch = get_version_info()
        expected_version_str = format_version_string(version, branch)

        result = subprocess.run(
            ["python", "-m", "terok.cli.main", "--version"],
            capture_output=True,
            text=True,
        )
        self.assertIn(expected_version_str, result.stdout)


if __name__ == "__main__":
    unittest.main()
