# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for agent work-status reading."""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from luskctl.lib.containers.work_status import (
    PENDING_PHASE_FILE,
    STATUS_FILE_NAME,
    WORK_STATUS_DISPLAY,
    WORK_STATUSES,
    PendingPhase,
    WorkStatus,
    clear_pending_phase,
    read_pending_phase,
    read_work_status,
    write_pending_phase,
    write_work_status,
)


class TestReadWorkStatus(unittest.TestCase):
    """Tests for read_work_status()."""

    def setUp(self):
        """Create a temporary directory for each test."""
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        """Remove the temporary directory after each test."""
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_valid_yaml_dict(self):
        (self.tmp_dir / STATUS_FILE_NAME).write_text(
            yaml.safe_dump({"status": "coding", "message": "Implementing auth"})
        )
        ws = read_work_status(self.tmp_dir)
        self.assertEqual(ws.status, "coding")
        self.assertEqual(ws.message, "Implementing auth")

    def test_bare_string(self):
        (self.tmp_dir / STATUS_FILE_NAME).write_text("testing\n")
        ws = read_work_status(self.tmp_dir)
        self.assertEqual(ws.status, "testing")
        self.assertIsNone(ws.message)

    def test_empty_file(self):
        (self.tmp_dir / STATUS_FILE_NAME).write_text("")
        ws = read_work_status(self.tmp_dir)
        self.assertIsNone(ws.status)
        self.assertIsNone(ws.message)

    def test_missing_dir(self):
        ws = read_work_status(self.tmp_dir / "nonexistent")
        self.assertIsNone(ws.status)
        self.assertIsNone(ws.message)

    def test_missing_file(self):
        ws = read_work_status(self.tmp_dir)
        self.assertIsNone(ws.status)
        self.assertIsNone(ws.message)

    def test_malformed_yaml(self):
        (self.tmp_dir / STATUS_FILE_NAME).write_text("{{broken yaml")
        ws = read_work_status(self.tmp_dir)
        self.assertIsNone(ws.status)
        self.assertIsNone(ws.message)

    def test_status_only_dict(self):
        (self.tmp_dir / STATUS_FILE_NAME).write_text(yaml.safe_dump({"status": "done"}))
        ws = read_work_status(self.tmp_dir)
        self.assertEqual(ws.status, "done")
        self.assertIsNone(ws.message)

    def test_unknown_status_preserved(self):
        (self.tmp_dir / STATUS_FILE_NAME).write_text(
            yaml.safe_dump({"status": "thinking-hard", "message": "Deep thoughts"})
        )
        ws = read_work_status(self.tmp_dir)
        self.assertEqual(ws.status, "thinking-hard")
        self.assertEqual(ws.message, "Deep thoughts")

    def test_numeric_yaml_returns_empty(self):
        (self.tmp_dir / STATUS_FILE_NAME).write_text("42\n")
        ws = read_work_status(self.tmp_dir)
        self.assertIsNone(ws.status)
        self.assertIsNone(ws.message)

    def test_list_yaml_returns_empty(self):
        (self.tmp_dir / STATUS_FILE_NAME).write_text("- item1\n- item2\n")
        ws = read_work_status(self.tmp_dir)
        self.assertIsNone(ws.status)
        self.assertIsNone(ws.message)

    def test_non_string_status_and_message_normalized(self):
        (self.tmp_dir / STATUS_FILE_NAME).write_text(
            yaml.safe_dump({"status": 123, "message": ["a", "b"]})
        )
        ws = read_work_status(self.tmp_dir)
        self.assertIsNone(ws.status)
        self.assertIsNone(ws.message)


class TestWorkStatusVocabulary(unittest.TestCase):
    """Tests for WORK_STATUSES and WORK_STATUS_DISPLAY consistency."""

    def test_all_statuses_have_display(self):
        for status in WORK_STATUSES:
            self.assertIn(status, WORK_STATUS_DISPLAY, f"Missing display for {status}")

    def test_all_display_have_status(self):
        for status in WORK_STATUS_DISPLAY:
            self.assertIn(status, WORK_STATUSES, f"Display entry without status: {status}")

    def test_vocabulary_completeness(self):
        expected = {
            "planning",
            "coding",
            "testing",
            "debugging",
            "reviewing",
            "documenting",
            "done",
            "blocked",
            "error",
        }
        self.assertEqual(set(WORK_STATUSES.keys()), expected)

    def test_display_has_emoji_and_label(self):
        for status, info in WORK_STATUS_DISPLAY.items():
            self.assertTrue(info.label, f"Empty label for {status}")
            self.assertTrue(info.emoji, f"Empty emoji for {status}")
            self.assertNotIn("\ufe0f", info.emoji, f"VS16 found in emoji for {status}")


class TestWorkStatusDataclass(unittest.TestCase):
    """Tests for WorkStatus dataclass."""

    def test_defaults(self):
        ws = WorkStatus()
        self.assertIsNone(ws.status)
        self.assertIsNone(ws.message)

    def test_frozen(self):
        ws = WorkStatus(status="coding")
        with self.assertRaises(AttributeError):
            ws.status = "testing"  # type: ignore[misc]


class TestWriteWorkStatus(unittest.TestCase):
    """Tests for write_work_status()."""

    def setUp(self):
        """Create a temporary directory for each test."""
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        """Remove the temporary directory after each test."""
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_creates_file(self):
        write_work_status(self.tmp_dir, "testing")
        ws = read_work_status(self.tmp_dir)
        self.assertEqual(ws.status, "testing")
        self.assertIsNone(ws.message)

    def test_creates_file_with_message(self):
        write_work_status(self.tmp_dir, "coding", message="Writing auth")
        ws = read_work_status(self.tmp_dir)
        self.assertEqual(ws.status, "coding")
        self.assertEqual(ws.message, "Writing auth")

    def test_overwrites_existing(self):
        write_work_status(self.tmp_dir, "coding")
        write_work_status(self.tmp_dir, "testing")
        ws = read_work_status(self.tmp_dir)
        self.assertEqual(ws.status, "testing")

    def test_clears_on_none(self):
        write_work_status(self.tmp_dir, "coding")
        write_work_status(self.tmp_dir, None)
        ws = read_work_status(self.tmp_dir)
        self.assertIsNone(ws.status)
        self.assertFalse((self.tmp_dir / STATUS_FILE_NAME).exists())

    def test_clears_missing_file_is_noop(self):
        write_work_status(self.tmp_dir, None)
        self.assertFalse((self.tmp_dir / STATUS_FILE_NAME).exists())

    def test_clear_does_not_create_parent_dirs(self):
        missing = self.tmp_dir / "does" / "not" / "exist"
        self.assertFalse(missing.exists())
        write_work_status(missing, None)
        self.assertFalse(missing.exists())

    def test_creates_parent_dirs(self):
        nested = self.tmp_dir / "a" / "b" / "c"
        write_work_status(nested, "done")
        ws = read_work_status(nested)
        self.assertEqual(ws.status, "done")


class TestPendingPhase(unittest.TestCase):
    """Tests for pending-phase I/O."""

    def setUp(self):
        """Create a temporary directory for each test."""
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        """Remove the temporary directory after each test."""
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_read_valid(self):
        (self.tmp_dir / PENDING_PHASE_FILE).write_text(
            yaml.safe_dump({"phase": "testing", "prompt": "Run tests"})
        )
        pp = read_pending_phase(self.tmp_dir)
        self.assertIsNotNone(pp)
        self.assertEqual(pp.phase, "testing")
        self.assertEqual(pp.prompt, "Run tests")

    def test_read_missing(self):
        self.assertIsNone(read_pending_phase(self.tmp_dir))

    def test_read_missing_dir(self):
        self.assertIsNone(read_pending_phase(self.tmp_dir / "nonexistent"))

    def test_read_malformed(self):
        (self.tmp_dir / PENDING_PHASE_FILE).write_text("{{broken")
        self.assertIsNone(read_pending_phase(self.tmp_dir))

    def test_read_no_phase_key(self):
        (self.tmp_dir / PENDING_PHASE_FILE).write_text(yaml.safe_dump({"prompt": "just a prompt"}))
        self.assertIsNone(read_pending_phase(self.tmp_dir))

    def test_read_non_dict(self):
        (self.tmp_dir / PENDING_PHASE_FILE).write_text("bare string\n")
        self.assertIsNone(read_pending_phase(self.tmp_dir))

    def test_read_missing_prompt_defaults_empty(self):
        (self.tmp_dir / PENDING_PHASE_FILE).write_text(yaml.safe_dump({"phase": "coding"}))
        pp = read_pending_phase(self.tmp_dir)
        self.assertIsNotNone(pp)
        self.assertEqual(pp.phase, "coding")
        self.assertEqual(pp.prompt, "")

    def test_read_non_string_phase_and_prompt(self):
        (self.tmp_dir / PENDING_PHASE_FILE).write_text(
            yaml.safe_dump({"phase": 123, "prompt": ["x"]})
        )
        self.assertIsNone(read_pending_phase(self.tmp_dir))

    def test_read_non_string_prompt_only(self):
        (self.tmp_dir / PENDING_PHASE_FILE).write_text(
            yaml.safe_dump({"phase": "coding", "prompt": {"nested": True}})
        )
        self.assertIsNone(read_pending_phase(self.tmp_dir))

    def test_write_and_read(self):
        write_pending_phase(self.tmp_dir, "reviewing", "Review changes")
        pp = read_pending_phase(self.tmp_dir)
        self.assertIsNotNone(pp)
        self.assertEqual(pp.phase, "reviewing")
        self.assertEqual(pp.prompt, "Review changes")

    def test_write_rejects_empty_phase(self):
        with self.assertRaises(ValueError):
            write_pending_phase(self.tmp_dir, "", "Run tests")

    def test_write_rejects_non_string_phase(self):
        with self.assertRaises(ValueError):
            write_pending_phase(self.tmp_dir, 123, "Run tests")  # type: ignore[arg-type]

    def test_write_rejects_non_string_prompt(self):
        with self.assertRaises(ValueError):
            write_pending_phase(self.tmp_dir, "testing", {"x": 1})  # type: ignore[arg-type]

    def test_write_creates_parent_dirs(self):
        nested = self.tmp_dir / "a" / "b"
        write_pending_phase(nested, "testing", "Run tests")
        pp = read_pending_phase(nested)
        self.assertIsNotNone(pp)
        self.assertEqual(pp.phase, "testing")

    def test_clear(self):
        write_pending_phase(self.tmp_dir, "testing", "Run tests")
        clear_pending_phase(self.tmp_dir)
        self.assertIsNone(read_pending_phase(self.tmp_dir))
        self.assertFalse((self.tmp_dir / PENDING_PHASE_FILE).exists())

    def test_clear_missing_is_noop(self):
        clear_pending_phase(self.tmp_dir)
        self.assertFalse((self.tmp_dir / PENDING_PHASE_FILE).exists())

    def test_frozen(self):
        pp = PendingPhase(phase="testing", prompt="Run tests")
        with self.assertRaises(AttributeError):
            pp.phase = "coding"  # type: ignore[misc]
