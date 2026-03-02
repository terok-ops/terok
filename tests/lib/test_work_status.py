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
    STATUS_FILE_NAME,
    WORK_STATUS_DISPLAY,
    WORK_STATUSES,
    WorkStatus,
    read_work_status,
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

    def test_list_yaml_returns_empty(self):
        (self.tmp_dir / STATUS_FILE_NAME).write_text("- item1\n- item2\n")
        ws = read_work_status(self.tmp_dir)
        self.assertIsNone(ws.status)


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
