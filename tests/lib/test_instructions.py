# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for agent instruction resolution module."""

import tempfile
import unittest
from pathlib import Path

from terok.lib.containers.instructions import (
    bundled_default_instructions,
    has_custom_instructions,
    resolve_instructions,
)


class BundledDefaultTests(unittest.TestCase):
    """Tests for bundled default instructions."""

    def test_bundled_default_exists(self) -> None:
        """Bundled default instructions are non-empty and readable."""
        text = bundled_default_instructions()
        self.assertIsInstance(text, str)
        self.assertTrue(len(text) > 100)
        self.assertIn("terok", text)

    def test_bundled_default_contains_key_sections(self) -> None:
        """Bundled default mentions workspace, git, sudo, and project classification."""
        text = bundled_default_instructions()
        self.assertIn("/workspace/", text)
        self.assertIn("sudo", text)
        self.assertIn("git", text.lower())
        self.assertIn("Classifying internal files by project", text)


class ResolveInstructionsTests(unittest.TestCase):
    """Tests for resolve_instructions()."""

    def test_flat_string(self) -> None:
        """Flat string instructions are returned as-is."""
        config = {"instructions": "Do the thing."}
        result = resolve_instructions(config, "claude")
        self.assertEqual(result, "Do the thing.")

    def test_per_provider_dict(self) -> None:
        """Per-provider dict selects the right provider value."""
        config = {
            "instructions": {
                "claude": "Claude instructions",
                "codex": "Codex instructions",
                "_default": "Default instructions",
            }
        }
        self.assertEqual(resolve_instructions(config, "claude"), "Claude instructions")
        self.assertEqual(resolve_instructions(config, "codex"), "Codex instructions")

    def test_per_provider_dict_fallback_to_default(self) -> None:
        """Per-provider dict falls back to _default for unknown provider."""
        config = {
            "instructions": {
                "claude": "Claude instructions",
                "_default": "Default instructions",
            }
        }
        self.assertEqual(resolve_instructions(config, "vibe"), "Default instructions")

    def test_per_provider_dict_list_with_inherit(self) -> None:
        """Provider-specific list supports _inherit splicing."""
        config = {"instructions": {"claude": ["_inherit", "Team policy."]}}
        default = bundled_default_instructions()
        self.assertEqual(
            resolve_instructions(config, "claude"),
            f"{default}\n\nTeam policy.",
        )

    def test_per_provider_dict_bare_inherit_returns_default(self) -> None:
        """Bare _inherit string in per-provider dict returns bundled default."""
        config = {"instructions": {"claude": "_inherit", "codex": "Codex custom"}}
        default = bundled_default_instructions()
        self.assertEqual(resolve_instructions(config, "claude"), default)
        self.assertEqual(resolve_instructions(config, "codex"), "Codex custom")

    def test_per_provider_dict_no_match_returns_bundled(self) -> None:
        """Per-provider dict with no match and no _default returns bundled default."""
        config = {"instructions": {"claude": "Claude only"}}
        result = resolve_instructions(config, "codex")
        # Should fall back to bundled default
        self.assertIn("terok", result)

    def test_fallback_to_default(self) -> None:
        """Absent key returns bundled default."""
        config = {}
        result = resolve_instructions(config, "claude")
        self.assertIn("terok", result)

    def test_null_uses_default(self) -> None:
        """Explicit None value returns bundled default."""
        config = {"instructions": None}
        result = resolve_instructions(config, "claude")
        self.assertIn("terok", result)

    def test_list_joined(self) -> None:
        """List of strings is joined with double newlines."""
        config = {"instructions": ["First part.", "Second part.", "Third part."]}
        result = resolve_instructions(config, "claude")
        self.assertEqual(result, "First part.\n\nSecond part.\n\nThird part.")

    def test_list_with_inherit_splices_default(self) -> None:
        """_inherit sentinel is replaced with bundled default in lists."""
        config = {"instructions": ["_inherit", "Extra text."]}
        result = resolve_instructions(config, "claude")
        default = bundled_default_instructions()
        self.assertEqual(result, f"{default}\n\nExtra text.")

    def test_list_inherit_in_middle(self) -> None:
        """_inherit in the middle of a list splices default at that position."""
        config = {"instructions": ["Before.", "_inherit", "After."]}
        result = resolve_instructions(config, "claude")
        default = bundled_default_instructions()
        self.assertEqual(result, f"Before.\n\n{default}\n\nAfter.")

    def test_list_without_inherit(self) -> None:
        """List without _inherit contains no bundled default."""
        config = {"instructions": ["Custom only."]}
        result = resolve_instructions(config, "claude")
        self.assertEqual(result, "Custom only.")
        self.assertNotIn("terok", result)

    def test_bare_inherit_string_returns_default(self) -> None:
        """Bare _inherit string is treated as absent (returns bundled default)."""
        config = {"instructions": "_inherit"}
        result = resolve_instructions(config, "claude")
        default = bundled_default_instructions()
        self.assertEqual(result, default)

    def test_empty_list(self) -> None:
        """Empty list produces empty string (no default)."""
        config = {"instructions": []}
        result = resolve_instructions(config, "claude")
        self.assertEqual(result, "")


class FileAppendTests(unittest.TestCase):
    """Tests for standalone instructions.md file append behavior."""

    def test_absent_yaml_absent_file_returns_default(self) -> None:
        """No YAML + no file = bundled default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = resolve_instructions({}, "claude", project_root=Path(tmpdir))
            self.assertIn("terok", result)

    def test_absent_yaml_with_file_appends(self) -> None:
        """No YAML + file = bundled default + file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "instructions.md").write_text("Project notes.", encoding="utf-8")
            result = resolve_instructions({}, "claude", project_root=root)
            default = bundled_default_instructions()
            self.assertTrue(result.startswith(default))
            self.assertTrue(result.endswith("Project notes."))

    def test_inherit_list_with_file(self) -> None:
        """YAML ["_inherit"] + file = bundled default + file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "instructions.md").write_text("File content.", encoding="utf-8")
            config = {"instructions": ["_inherit"]}
            result = resolve_instructions(config, "claude", project_root=root)
            default = bundled_default_instructions()
            self.assertTrue(result.startswith(default))
            self.assertTrue(result.endswith("File content."))

    def test_inherit_plus_extra_plus_file(self) -> None:
        """YAML ["_inherit", "extra"] + file = default + extra + file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "instructions.md").write_text("File text.", encoding="utf-8")
            config = {"instructions": ["_inherit", "Extra YAML."]}
            result = resolve_instructions(config, "claude", project_root=root)
            default = bundled_default_instructions()
            self.assertIn(default, result)
            self.assertIn("Extra YAML.", result)
            self.assertTrue(result.endswith("File text."))

    def test_custom_only_no_file(self) -> None:
        """YAML ["custom only"] + no file = custom only (no default)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"instructions": ["custom only"]}
            result = resolve_instructions(config, "claude", project_root=Path(tmpdir))
            self.assertEqual(result, "custom only")

    def test_custom_only_with_file(self) -> None:
        """YAML ["custom only"] + file = custom + file (no default)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "instructions.md").write_text("File.", encoding="utf-8")
            config = {"instructions": ["custom only"]}
            result = resolve_instructions(config, "claude", project_root=root)
            self.assertEqual(result, "custom only\n\nFile.")

    def test_empty_list_with_file(self) -> None:
        """YAML [] + file = file only."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "instructions.md").write_text("Only file.", encoding="utf-8")
            config = {"instructions": []}
            result = resolve_instructions(config, "claude", project_root=root)
            self.assertEqual(result, "Only file.")

    def test_flat_string_with_file(self) -> None:
        """YAML flat string + file = string + file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "instructions.md").write_text("Appended.", encoding="utf-8")
            config = {"instructions": "flat string"}
            result = resolve_instructions(config, "claude", project_root=root)
            self.assertEqual(result, "flat string\n\nAppended.")

    def test_empty_file_not_appended(self) -> None:
        """An empty instructions.md file is not appended."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "instructions.md").write_text("", encoding="utf-8")
            config = {"instructions": "base"}
            result = resolve_instructions(config, "claude", project_root=root)
            self.assertEqual(result, "base")

    def test_whitespace_only_file_not_appended(self) -> None:
        """Whitespace-only instructions.md is not appended."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "instructions.md").write_text("  \n  \n  ", encoding="utf-8")
            config = {"instructions": "base"}
            result = resolve_instructions(config, "claude", project_root=root)
            self.assertEqual(result, "base")

    def test_no_project_root_skips_file(self) -> None:
        """When project_root is None, no file append occurs."""
        config = {"instructions": "base"}
        result = resolve_instructions(config, "claude", project_root=None)
        self.assertEqual(result, "base")


class HasCustomInstructionsTests(unittest.TestCase):
    """Tests for has_custom_instructions()."""

    def test_true_when_present(self) -> None:
        """Returns True when instructions key is present."""
        self.assertTrue(has_custom_instructions({"instructions": "Custom"}))

    def test_false_when_absent(self) -> None:
        """Returns False when instructions key is absent."""
        self.assertFalse(has_custom_instructions({}))

    def test_false_when_none(self) -> None:
        """Returns False when instructions is explicitly None."""
        self.assertFalse(has_custom_instructions({"instructions": None}))

    def test_true_for_dict_form(self) -> None:
        """Returns True for per-provider dict form."""
        self.assertTrue(has_custom_instructions({"instructions": {"claude": "Custom"}}))

    def test_true_for_list_form(self) -> None:
        """Returns True for list form."""
        self.assertTrue(has_custom_instructions({"instructions": ["Part 1", "Part 2"]}))

    def test_true_with_instructions_file(self) -> None:
        """Returns True when instructions.md exists even without YAML key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "instructions.md").write_text("Content", encoding="utf-8")
            self.assertTrue(has_custom_instructions({}, project_root=root))

    def test_false_no_yaml_no_file(self) -> None:
        """Returns False when no YAML key and no file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(has_custom_instructions({}, project_root=Path(tmpdir)))

    def test_false_no_project_root(self) -> None:
        """Returns False when no YAML key and project_root is None."""
        self.assertFalse(has_custom_instructions({}, project_root=None))


if __name__ == "__main__":
    unittest.main()
