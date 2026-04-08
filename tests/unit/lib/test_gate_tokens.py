# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for gate_tokens module."""

from __future__ import annotations

import json
import tempfile
import unittest.mock
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from terok_sandbox import (
    create_token,
    revoke_token_for_task,
)
from terok_sandbox.gate.tokens import (
    _read_tokens,
    _write_tokens,
    token_file_path,
)

from tests.testfs import FAKE_TEROK_STATE_DIR, MISSING_TOKENS_PATH, NONEXISTENT_TOKENS_PATH


@contextmanager
def patched_token_file(path: Path | None = None) -> Iterator[Path]:
    """Patch ``token_file_path()`` to point at a temporary JSON file."""
    if path is not None:
        token_path = path
        with unittest.mock.patch(
            "terok_sandbox.gate.tokens.token_file_path", return_value=token_path
        ):
            yield token_path
        return

    with tempfile.TemporaryDirectory() as td:
        token_path = Path(td) / "tokens.json"
        with unittest.mock.patch(
            "terok_sandbox.gate.tokens.token_file_path", return_value=token_path
        ):
            yield token_path


def read_token_json(path: Path) -> dict[str, dict[str, str]]:
    """Load the persisted token data from disk."""
    return json.loads(path.read_text())


class TestTokenFilePath:
    """Tests for token_file_path."""

    def test_returns_path_under_state_root(self) -> None:
        from terok_sandbox import SandboxConfig

        cfg = SandboxConfig(state_dir=FAKE_TEROK_STATE_DIR)
        path = token_file_path(cfg=cfg)
        assert path == FAKE_TEROK_STATE_DIR / "gate" / "tokens.json"


class TestCreateToken:
    """Tests for create_token."""

    def test_returns_prefixed_token(self) -> None:
        with patched_token_file() as token_path:
            token = create_token("proj-a", "1")
            assert token_path.exists()
        assert token.startswith("terok-g-")
        assert len(token) == 40
        int(token.removeprefix("terok-g-"), 16)

    def test_persists_to_file(self) -> None:
        with patched_token_file() as token_path:
            token = create_token("proj-a", "1")
            data = read_token_json(token_path)
        assert data[token] == {"scope": "proj-a", "task": "1"}

    def test_multiple_tokens_coexist(self) -> None:
        with patched_token_file() as token_path:
            first = create_token("proj-a", "1")
            second = create_token("proj-b", "2")
            data = read_token_json(token_path)
        assert first != second
        assert first in data
        assert second in data


class TestRevokeToken:
    """Tests for revoke_token_for_task."""

    def test_revoke_removes_entry(self) -> None:
        with patched_token_file() as token_path:
            token = create_token("proj-a", "1")
            revoke_token_for_task("proj-a", "1")
            data = read_token_json(token_path)
        assert token not in data

    def test_revoke_nonexistent_is_noop(self) -> None:
        with patched_token_file() as token_path:
            create_token("proj-a", "1")
            revoke_token_for_task("proj-a", "99")
            data = read_token_json(token_path)
        assert len(data) == 1

    def test_revoke_on_missing_file_is_noop(self) -> None:
        with patched_token_file(MISSING_TOKENS_PATH):
            revoke_token_for_task("proj-a", "1")


class TestAtomicWrite:
    """Tests for atomic write via _write_tokens."""

    def test_write_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            token_path = Path(td) / "sub" / "dir" / "tokens.json"
            _write_tokens(token_path, {"abc": {"scope": "p", "task": "1"}})
            assert read_token_json(token_path) == {"abc": {"scope": "p", "task": "1"}}

    @pytest.mark.parametrize(
        ("path", "content", "expected"),
        [
            (Path(NONEXISTENT_TOKENS_PATH.name), None, {}),
            (Path("tokens.json"), "not json{{{", {}),
            (Path("tokens.json"), json.dumps(["not", "a", "dict"]), {}),
            (
                Path("tokens.json"),
                json.dumps(
                    {
                        "good": {"scope": "p", "task": "1"},
                        "bad_info": "not a dict",
                        "missing_task": {"scope": "p"},
                        "int_scope": {"scope": 123, "task": "1"},
                    }
                ),
                {"good": {"scope": "p", "task": "1"}},
            ),
        ],
        ids=["missing", "corrupt-json", "non-dict-json", "malformed-entries"],
    )
    def test_read_tokens_handles_invalid_inputs(
        self,
        path: Path,
        content: str | None,
        expected: dict[str, dict[str, str]],
    ) -> None:
        """Invalid token files are treated as empty or sanitized."""
        with tempfile.TemporaryDirectory() as td:
            token_path = Path(td) / path
            if content is not None:
                token_path.write_text(content)
            result = _read_tokens(token_path)
        assert result == expected

    def test_atomic_write_uses_replace(self) -> None:
        """Verify that _write_tokens uses atomic replacement semantics."""
        with tempfile.TemporaryDirectory() as td:
            token_path = Path(td) / "tokens.json"
            _write_tokens(token_path, {"t1": {"scope": "p", "task": "1"}})
            _write_tokens(token_path, {"t2": {"scope": "p", "task": "2"}})
            data = read_token_json(token_path)
            assert data == {"t2": {"scope": "p", "task": "2"}}
            assert list(Path(td).glob("*.tmp")) == []
