# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for agent instruction resolution module."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from terok_agent import bundled_default_instructions, resolve_instructions
from terok_agent.instructions import has_custom_instructions

from tests.testfs import NONEXISTENT_PROJECT_ROOT, WORKSPACE_ROOT

DEFAULT_INSTRUCTIONS = bundled_default_instructions()


def resolve_with_project_file(
    config: dict[str, object],
    *,
    file_text: str | None = None,
) -> str:
    """Resolve instructions with an optional ``instructions.md`` project file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        if file_text is not None:
            (root / "instructions.md").write_text(file_text, encoding="utf-8")
        return resolve_instructions(config, "claude", project_root=root)


class TestBundledDefault:
    """Tests for bundled default instructions."""

    def test_bundled_default_exists(self) -> None:
        assert isinstance(DEFAULT_INSTRUCTIONS, str)
        assert len(DEFAULT_INSTRUCTIONS) > 100
        assert "terok" in DEFAULT_INSTRUCTIONS

    def test_bundled_default_contains_key_sections(self) -> None:
        assert f"{WORKSPACE_ROOT}/" in DEFAULT_INSTRUCTIONS
        assert "sudo" in DEFAULT_INSTRUCTIONS
        assert "git" in DEFAULT_INSTRUCTIONS.lower()
        assert "Classifying internal files by project" in DEFAULT_INSTRUCTIONS


class TestResolveInstructions:
    """Tests for resolve_instructions()."""

    @pytest.mark.parametrize(
        ("config", "provider", "expected"),
        [
            ({"instructions": "Do the thing."}, "claude", "Do the thing."),
            (
                {
                    "instructions": {
                        "claude": "Claude instructions",
                        "codex": "Codex instructions",
                        "_default": "Default instructions",
                    }
                },
                "claude",
                "Claude instructions",
            ),
            (
                {
                    "instructions": {
                        "claude": "Claude instructions",
                        "codex": "Codex instructions",
                        "_default": "Default instructions",
                    }
                },
                "codex",
                "Codex instructions",
            ),
            (
                {
                    "instructions": {
                        "claude": "Claude instructions",
                        "_default": "Default instructions",
                    }
                },
                "vibe",
                "Default instructions",
            ),
            (
                {"instructions": {"claude": "_inherit", "codex": "Codex custom"}},
                "claude",
                DEFAULT_INSTRUCTIONS,
            ),
            (
                {"instructions": {"claude": "_inherit", "codex": "Codex custom"}},
                "codex",
                "Codex custom",
            ),
            ({}, "claude", DEFAULT_INSTRUCTIONS),
            ({"instructions": None}, "claude", DEFAULT_INSTRUCTIONS),
            (
                {"instructions": ["First part.", "Second part.", "Third part."]},
                "claude",
                "First part.\n\nSecond part.\n\nThird part.",
            ),
            (
                {"instructions": ["_inherit", "Extra text."]},
                "claude",
                f"{DEFAULT_INSTRUCTIONS}\n\nExtra text.",
            ),
            (
                {"instructions": ["Before.", "_inherit", "After."]},
                "claude",
                f"Before.\n\n{DEFAULT_INSTRUCTIONS}\n\nAfter.",
            ),
            ({"instructions": ["Custom only."]}, "claude", "Custom only."),
            ({"instructions": "_inherit"}, "claude", DEFAULT_INSTRUCTIONS),
            ({"instructions": []}, "claude", ""),
        ],
        ids=[
            "flat-string",
            "provider-claude",
            "provider-codex",
            "provider-default",
            "bare-inherit-claude",
            "bare-inherit-codex",
            "missing-key",
            "null",
            "list-joined",
            "list-inherit-prefix",
            "list-inherit-middle",
            "list-custom-only",
            "bare-inherit-string",
            "empty-list",
        ],
    )
    def test_instruction_resolution(
        self,
        config: dict[str, object],
        provider: str,
        expected: str,
    ) -> None:
        assert resolve_instructions(config, provider) == expected

    def test_per_provider_dict_list_with_inherit(self) -> None:
        config = {"instructions": {"claude": ["_inherit", "Team policy."]}}
        assert resolve_instructions(config, "claude") == f"{DEFAULT_INSTRUCTIONS}\n\nTeam policy."

    def test_per_provider_dict_no_match_returns_bundled(self) -> None:
        assert (
            resolve_instructions({"instructions": {"claude": "Claude only"}}, "codex")
            == DEFAULT_INSTRUCTIONS
        )


class TestFileAppend:
    """Tests for standalone instructions.md file append behavior."""

    @pytest.mark.parametrize(
        ("config", "file_text", "expected"),
        [
            ({}, None, DEFAULT_INSTRUCTIONS),
            ({}, "Project notes.", f"{DEFAULT_INSTRUCTIONS}\n\nProject notes."),
            (
                {"instructions": ["_inherit"]},
                "File content.",
                f"{DEFAULT_INSTRUCTIONS}\n\nFile content.",
            ),
            (
                {"instructions": ["_inherit", "Extra YAML."]},
                "File text.",
                f"{DEFAULT_INSTRUCTIONS}\n\nExtra YAML.\n\nFile text.",
            ),
            ({"instructions": ["custom only"]}, None, "custom only"),
            ({"instructions": ["custom only"]}, "File.", "custom only\n\nFile."),
            ({"instructions": []}, "Only file.", "Only file."),
            ({"instructions": "flat string"}, "Appended.", "flat string\n\nAppended."),
            ({"instructions": "base"}, "", "base"),
            ({"instructions": "base"}, "  \n  \n  ", "base"),
        ],
        ids=[
            "default-no-file",
            "default-plus-file",
            "inherit-plus-file",
            "inherit-extra-plus-file",
            "custom-only-no-file",
            "custom-only-plus-file",
            "empty-list-plus-file",
            "flat-string-plus-file",
            "empty-file-ignored",
            "whitespace-file-ignored",
        ],
    )
    def test_file_append_behavior(
        self,
        config: dict[str, object],
        file_text: str | None,
        expected: str,
    ) -> None:
        assert resolve_with_project_file(config, file_text=file_text) == expected

    def test_no_project_root_skips_file(self) -> None:
        assert resolve_instructions({"instructions": "base"}, "claude", project_root=None) == "base"


class TestHasCustomInstructions:
    """Tests for has_custom_instructions()."""

    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            ({"instructions": "Custom"}, True),
            ({}, False),
            ({"instructions": None}, False),
            ({"instructions": {"claude": "Custom"}}, True),
            ({"instructions": ["Part 1", "Part 2"]}, True),
        ],
        ids=["string", "absent", "none", "dict", "list"],
    )
    def test_has_custom_instructions_from_config(
        self,
        config: dict[str, object],
        expected: bool,
    ) -> None:
        assert has_custom_instructions(config) is expected

    def test_true_with_instructions_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "instructions.md").write_text("Content", encoding="utf-8")
            assert has_custom_instructions({}, project_root=root)

    @pytest.mark.parametrize(
        "project_root", [NONEXISTENT_PROJECT_ROOT, None], ids=["empty-root", "none"]
    )
    def test_false_without_yaml_or_file(self, project_root: Path | None) -> None:
        if project_root is not None:
            with tempfile.TemporaryDirectory() as tmpdir:
                assert not has_custom_instructions({}, project_root=Path(tmpdir))
        else:
            assert not has_custom_instructions({}, project_root=None)
