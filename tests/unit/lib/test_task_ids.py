# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for task ID generation, resolution, and container naming."""

from __future__ import annotations

import unittest.mock

import pytest

from terok.lib.core.task_display import CONTAINER_MODES, container_name
from terok.lib.orchestration.tasks import (
    _TASK_ID_BODY_CHARS,
    _TASK_ID_HEAD_CHARS,
    _TASK_ID_LEN,
    _generate_unique_id,
    normalize_task_id_input,
    resolve_task_id,
    tasks_meta_dir,
)
from terok.lib.util.yaml import dump as yaml_dump
from tests.test_utils import assert_task_id, project_env

MINIMAL_PROJECT = """
project:
  id: test-proj
  security_class: online
git:
  upstream_url: https://example.com/repo.git
"""

# Hand-picked valid IDs for fixtures — each matches the current format
# (Crockford head + digit + 3 Crockford body chars).
ID_A = "k3v8h"
ID_B = "p7fmn"
ID_AMBIGUOUS_WITH_A = "k3v82"  # shares "k3v8" with ID_A for ambiguity tests


# ---------- _generate_unique_id ----------


class TestGenerateUniqueId:
    """Tests for the internal task ID generator."""

    def test_produces_valid_task_id(self) -> None:
        """Generated IDs must match the current format."""
        result = _generate_unique_id(set())
        assert_task_id(result)

    def test_structural_positions(self) -> None:
        """Char 1 must be a Crockford head letter; char 2 must be a digit."""
        for _ in range(50):
            tid = _generate_unique_id(set())
            assert tid[0] in _TASK_ID_HEAD_CHARS
            assert tid[1].isdigit()
            assert all(c in _TASK_ID_BODY_CHARS for c in tid[2:])
            assert len(tid) == _TASK_ID_LEN

    def test_avoids_existing_ids(self) -> None:
        """Generated ID must not collide with any member of *existing*."""
        existing = {_generate_unique_id(set()) for _ in range(20)}
        new_id = _generate_unique_id(existing)
        assert new_id not in existing
        assert_task_id(new_id)

    def test_uniqueness_across_calls(self) -> None:
        """Multiple generated IDs should all be distinct."""
        ids = {_generate_unique_id(set()) for _ in range(50)}
        assert len(ids) == 50

    def test_raises_after_exhaustion(self) -> None:
        """Should raise RuntimeError if it can't find a unique ID in 100 tries."""
        with unittest.mock.patch("terok.lib.orchestration.tasks._gen_task_id") as mock_gen:
            mock_gen.return_value = ID_A
            with pytest.raises(RuntimeError, match="Failed to generate unique task ID"):
                _generate_unique_id({ID_A})


# ---------- normalize_task_id_input ----------


class TestNormalizeTaskIdInput:
    """Surface-form input sanitiser — Crockford-style I/L→1, O→0 substitutions."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("K3V8H", "k3v8h"),
            ("k3-v8h", "k3v8h"),
            ("K3-V8-H", "k3v8h"),
            ("K3VOH", "k3v0h"),  # O → 0 at body position
            ("k3vIh", "k3v1h"),  # I → 1 at body position
            ("k3vLh", "k3v1h"),  # L → 1 at body position
            ("g1V0L", "g1v01"),  # mixed case + L → 1
            ("", ""),
        ],
    )
    def test_canonical_form(self, raw: str, expected: str) -> None:
        """Normalisation collapses case, hyphens, and ambiguous Crockford letters."""
        assert normalize_task_id_input(raw) == expected


# ---------- resolve_task_id ----------


class TestResolveTaskId:
    """Tests for CLI prefix-matching task ID resolution."""

    def _write_meta(self, project_id: str, task_id: str) -> None:
        """Write a minimal task metadata file."""
        meta_dir = tasks_meta_dir(project_id)
        meta_dir.mkdir(parents=True, exist_ok=True)
        meta = {"task_id": task_id, "name": "test-task", "mode": None, "workspace": "/tmp/ws"}
        (meta_dir / f"{task_id}.yml").write_text(yaml_dump(meta))

    def test_exact_match(self) -> None:
        """Full task ID should resolve immediately."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", ID_A)
            assert resolve_task_id("test-proj", ID_A) == ID_A

    def test_prefix_match(self) -> None:
        """A unique prefix shorter than the full length should resolve to the full ID."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", ID_A)
            assert resolve_task_id("test-proj", ID_A[:4]) == ID_A

    def test_single_char_prefix(self) -> None:
        """Even a 1-char prefix resolves if it uniquely matches."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", ID_A)
            assert resolve_task_id("test-proj", ID_A[0]) == ID_A

    def test_ambiguous_prefix(self) -> None:
        """Should raise SystemExit listing the matches when prefix is ambiguous."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", ID_A)
            self._write_meta("test-proj", ID_AMBIGUOUS_WITH_A)
            with pytest.raises(SystemExit, match=f"Ambiguous task ID '{ID_A[:4]}'"):
                resolve_task_id("test-proj", ID_A[:4])

    def test_no_match(self) -> None:
        """Should raise SystemExit when no task matches the prefix."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", ID_A)
            with pytest.raises(SystemExit, match=f"No task matching '{ID_B}'"):
                resolve_task_id("test-proj", ID_B)

    def test_no_tasks_dir(self) -> None:
        """Should raise SystemExit when the project has no tasks directory."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            with pytest.raises(SystemExit, match="No tasks found"):
                resolve_task_id("test-proj", ID_A)

    def test_rejects_non_alphabet_input(self) -> None:
        """Should reject prefixes containing characters outside the alphabet."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            with pytest.raises(SystemExit, match="Invalid task ID prefix"):
                resolve_task_id("test-proj", "../../etc")

    def test_accepts_uppercase(self) -> None:
        """Uppercase input should be normalised to the canonical lowercase ID."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", ID_A)
            assert resolve_task_id("test-proj", ID_A.upper()) == ID_A

    def test_accepts_hyphenated_input(self) -> None:
        """Hyphens (Crockford group separators) should be stripped."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", ID_A)
            assert resolve_task_id("test-proj", f"{ID_A[:2]}-{ID_A[2:]}") == ID_A

    def test_accepts_ambiguous_body_letters(self) -> None:
        """``I/L → 1`` and ``O → 0`` substitutions resolve in body positions."""
        # ID_A = "k3v8h"; insert a fake ambiguous variant and check it maps back.
        tid = "g1v01"  # body positions contain '1' and '0'
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", tid)
            assert resolve_task_id("test-proj", "G1-VOL") == tid

    def test_rejects_empty_string(self) -> None:
        """Should reject an empty prefix."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            with pytest.raises(SystemExit, match="Invalid task ID prefix"):
                resolve_task_id("test-proj", "")

    def test_rejects_ambiguous_crockford_letters(self) -> None:
        """Letters outside the Crockford subset (i, l, o, u) must be rejected."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            for bad in ("i", "l", "o", "u"):
                with pytest.raises(SystemExit, match="Invalid task ID prefix"):
                    resolve_task_id("test-proj", bad)

    def test_rejects_too_long_prefix(self) -> None:
        """Should reject prefixes longer than _TASK_ID_LEN characters."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            with pytest.raises(SystemExit, match="Invalid task ID prefix"):
                resolve_task_id("test-proj", ID_A + "x")


# ---------- Legacy hex compatibility ----------


class TestLegacyHexCompat:
    """Legacy pre-0.8.0 hex task IDs remain readable with a deprecation warning."""

    LEGACY = "abcd1234"

    def _write_meta(self, project_id: str, task_id: str) -> None:
        """Write a minimal task metadata file."""
        meta_dir = tasks_meta_dir(project_id)
        meta_dir.mkdir(parents=True, exist_ok=True)
        meta = {"task_id": task_id, "name": "legacy", "mode": None, "workspace": "/tmp/ws"}
        (meta_dir / f"{task_id}.yml").write_text(yaml_dump(meta))

    def test_legacy_hex_resolves_with_deprecation(self) -> None:
        """A legacy 8-char hex task on disk should still resolve, with a DeprecationWarning."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", self.LEGACY)
            with pytest.warns(DeprecationWarning, match="pre-0.8.0 hex format"):
                assert resolve_task_id("test-proj", self.LEGACY) == self.LEGACY

    def test_legacy_hex_prefix_resolves_with_deprecation(self) -> None:
        """Legacy hex prefix resolution should also work and warn."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", self.LEGACY)
            with pytest.warns(DeprecationWarning):
                assert resolve_task_id("test-proj", "abcd") == self.LEGACY

    def test_current_format_does_not_warn(self) -> None:
        """Resolving a current-format ID must not emit a deprecation warning."""
        import warnings

        with project_env(MINIMAL_PROJECT) as _ctx:
            meta_dir = tasks_meta_dir("test-proj")
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / f"{ID_A}.yml").write_text(
                yaml_dump({"task_id": ID_A, "name": "n", "mode": None, "workspace": "/tmp/ws"})
            )
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                assert resolve_task_id("test-proj", ID_A) == ID_A


# ---------- container_name ----------


class TestContainerName:
    """Tests for the centralized container naming function."""

    def test_format(self) -> None:
        """Container name should be {project}-{mode}-{task_id}."""
        assert container_name("myproj", "cli", ID_A) == f"myproj-cli-{ID_A}"

    def test_all_modes(self) -> None:
        """Every declared mode should produce a valid container name."""
        for mode in CONTAINER_MODES:
            result = container_name("proj", mode, ID_B)
            assert result == f"proj-{mode}-{ID_B}"

    def test_container_modes_tuple(self) -> None:
        """CONTAINER_MODES must include the four known modes."""
        assert set(CONTAINER_MODES) == {"cli", "web", "run", "toad"}


# ---------- assert_task_id ----------


class TestAssertTaskId:
    """Tests for the test utility itself."""

    def test_valid_id(self) -> None:
        """Should pass for a valid Crockford-4.5 task ID."""
        assert_task_id(ID_A)

    def test_rejects_none(self) -> None:
        """Should raise AssertionError for None."""
        with pytest.raises(AssertionError, match="Expected task ID string"):
            assert_task_id(None)

    def test_rejects_short_id(self) -> None:
        """Should raise AssertionError for IDs shorter than the full length."""
        with pytest.raises(AssertionError, match="Not a valid task ID"):
            assert_task_id(ID_A[:3])

    def test_rejects_hex_id(self) -> None:
        """Legacy hex IDs must no longer pass the strict assertion."""
        with pytest.raises(AssertionError, match="Not a valid task ID"):
            assert_task_id("abcd1234")

    def test_rejects_ambiguous_letters(self) -> None:
        """IDs containing Crockford-excluded letters (i, l, o, u) must be rejected."""
        with pytest.raises(AssertionError, match="Not a valid task ID"):
            assert_task_id("g1ilo")
