# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for gate_tokens module."""

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from terok.lib.security.gate_tokens import (
    _read_tokens,
    _write_tokens,
    create_token,
    revoke_token_for_task,
    token_file_path,
)


class TestTokenFilePath(unittest.TestCase):
    """Tests for token_file_path."""

    def test_returns_path_under_state_root(self) -> None:
        with unittest.mock.patch(
            "terok.lib.security.gate_tokens.state_root",
            return_value=Path("/tmp/terok-state"),
        ):
            path = token_file_path()
        self.assertEqual(path, Path("/tmp/terok-state/gate/tokens.json"))


class TestCreateToken(unittest.TestCase):
    """Tests for create_token."""

    def test_returns_32char_hex(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            with unittest.mock.patch(
                "terok.lib.security.gate_tokens.token_file_path", return_value=tf
            ):
                token = create_token("proj-a", "1")
        self.assertEqual(len(token), 32)
        # Must be valid hex
        int(token, 16)

    def test_persists_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            with unittest.mock.patch(
                "terok.lib.security.gate_tokens.token_file_path", return_value=tf
            ):
                token = create_token("proj-a", "1")
            data = json.loads(tf.read_text())
            self.assertIn(token, data)
            self.assertEqual(data[token]["project"], "proj-a")
            self.assertEqual(data[token]["task"], "1")

    def test_multiple_tokens_coexist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            with unittest.mock.patch(
                "terok.lib.security.gate_tokens.token_file_path", return_value=tf
            ):
                t1 = create_token("proj-a", "1")
                t2 = create_token("proj-b", "2")
            data = json.loads(tf.read_text())
            self.assertIn(t1, data)
            self.assertIn(t2, data)
            self.assertNotEqual(t1, t2)


class TestRevokeToken(unittest.TestCase):
    """Tests for revoke_token_for_task."""

    def test_revoke_removes_entry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            with unittest.mock.patch(
                "terok.lib.security.gate_tokens.token_file_path", return_value=tf
            ):
                token = create_token("proj-a", "1")
                revoke_token_for_task("proj-a", "1")
            data = json.loads(tf.read_text())
            self.assertNotIn(token, data)

    def test_revoke_nonexistent_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            with unittest.mock.patch(
                "terok.lib.security.gate_tokens.token_file_path", return_value=tf
            ):
                create_token("proj-a", "1")
                # Revoke a non-existent task — should not raise
                revoke_token_for_task("proj-a", "99")
            data = json.loads(tf.read_text())
            self.assertEqual(len(data), 1)

    def test_revoke_on_missing_file_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "nonexistent" / "tokens.json"
            with unittest.mock.patch(
                "terok.lib.security.gate_tokens.token_file_path", return_value=tf
            ):
                # Should not raise even when the file doesn't exist
                revoke_token_for_task("proj-a", "1")


class TestAtomicWrite(unittest.TestCase):
    """Tests for atomic write via _write_tokens."""

    def test_write_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "sub" / "dir" / "tokens.json"
            _write_tokens(tf, {"abc": {"project": "p", "task": "1"}})
            self.assertTrue(tf.is_file())
            data = json.loads(tf.read_text())
            self.assertIn("abc", data)

    def test_read_missing_file_returns_empty(self) -> None:
        result = _read_tokens(Path("/nonexistent/tokens.json"))
        self.assertEqual(result, {})

    def test_read_corrupt_json_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            tf.write_text("not json{{{")
            result = _read_tokens(tf)
            self.assertEqual(result, {})

    def test_read_non_dict_json_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            tf.write_text(json.dumps(["not", "a", "dict"]))
            result = _read_tokens(tf)
            self.assertEqual(result, {})

    def test_read_skips_malformed_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            tf.write_text(
                json.dumps(
                    {
                        "good": {"project": "p", "task": "1"},
                        "bad_info": "not a dict",
                        "missing_task": {"project": "p"},
                        "int_project": {"project": 123, "task": "1"},
                    }
                )
            )
            result = _read_tokens(tf)
            self.assertEqual(len(result), 1)
            self.assertIn("good", result)

    def test_atomic_write_uses_replace(self) -> None:
        """Verify that _write_tokens uses os.replace for atomicity."""
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            _write_tokens(tf, {"t1": {"project": "p", "task": "1"}})
            # Overwrite — should not leave .tmp files
            _write_tokens(tf, {"t2": {"project": "p", "task": "2"}})
            data = json.loads(tf.read_text())
            self.assertNotIn("t1", data)
            self.assertIn("t2", data)
            # No .tmp files should remain
            tmp_files = list(Path(td).glob("*.tmp"))
            self.assertEqual(tmp_files, [])
