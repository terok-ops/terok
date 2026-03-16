# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for agent work-status and pending-phase helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from terok.lib.containers.work_status import (
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
from terok.lib.util.yaml import dump as yaml_dump

EXPECTED_WORK_STATUSES = {
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


def write_payload(base_dir: Path, filename: str, payload: object | str) -> None:
    """Write raw text or a YAML-serializable payload into *filename* under *base_dir*."""
    text = payload if isinstance(payload, str) else yaml_dump(payload)
    (base_dir / filename).write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(
            {"status": "coding", "message": "Implementing auth"},
            WorkStatus(status="coding", message="Implementing auth"),
            id="dict-with-message",
        ),
        pytest.param("testing\n", WorkStatus(status="testing"), id="bare-string"),
        pytest.param("", WorkStatus(), id="empty-file"),
        pytest.param({"status": "done"}, WorkStatus(status="done"), id="status-only-dict"),
        pytest.param(
            {"status": "thinking-hard", "message": "Deep thoughts"},
            WorkStatus(status="thinking-hard", message="Deep thoughts"),
            id="unknown-status-preserved",
        ),
    ],
)
def test_read_work_status_parses_supported_payloads(
    tmp_path: Path,
    payload: object | str,
    expected: WorkStatus,
) -> None:
    """Supported work-status payloads round-trip into ``WorkStatus`` values."""
    write_payload(tmp_path, STATUS_FILE_NAME, payload)
    assert read_work_status(tmp_path) == expected


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("{{broken yaml", id="malformed-yaml"),
        pytest.param("42\n", id="numeric-yaml"),
        pytest.param("- item1\n- item2\n", id="list-yaml"),
        pytest.param({"status": 123, "message": ["a", "b"]}, id="non-string-fields"),
    ],
)
def test_read_work_status_returns_empty_for_invalid_payloads(
    tmp_path: Path,
    payload: object | str,
) -> None:
    """Invalid work-status payloads collapse to the empty ``WorkStatus``."""
    write_payload(tmp_path, STATUS_FILE_NAME, payload)
    assert read_work_status(tmp_path) == WorkStatus()


@pytest.mark.parametrize(
    "path_factory",
    [
        pytest.param(lambda base: base, id="missing-file"),
        pytest.param(lambda base: base / "nonexistent", id="missing-dir"),
    ],
)
def test_read_work_status_returns_empty_when_missing(
    tmp_path: Path,
    path_factory,
) -> None:
    """Missing work-status files or directories read as an empty status."""
    assert read_work_status(path_factory(tmp_path)) == WorkStatus()


@pytest.mark.parametrize(
    ("status", "message"),
    [
        pytest.param("testing", None, id="without-message"),
        pytest.param("coding", "Writing auth", id="with-message"),
    ],
)
def test_write_work_status_round_trips(
    tmp_path: Path,
    status: str,
    message: str | None,
) -> None:
    """Writing work status produces YAML that reads back to the same value."""
    write_work_status(tmp_path, status, message=message)
    assert read_work_status(tmp_path) == WorkStatus(status=status, message=message)


def test_write_work_status_overwrites_existing_value(tmp_path: Path) -> None:
    """Later work-status writes replace earlier values."""
    write_work_status(tmp_path, "coding")
    write_work_status(tmp_path, "testing")
    assert read_work_status(tmp_path) == WorkStatus(status="testing")


def test_write_work_status_clears_existing_file(tmp_path: Path) -> None:
    """Writing ``None`` removes the persisted status file."""
    write_work_status(tmp_path, "coding")
    write_work_status(tmp_path, None)
    assert read_work_status(tmp_path) == WorkStatus()
    assert not (tmp_path / STATUS_FILE_NAME).exists()


@pytest.mark.parametrize(
    "agent_config_dir",
    [
        pytest.param("tmp-path", id="existing-dir"),
        pytest.param("tmp-path/missing/parent", id="missing-parent"),
    ],
)
def test_write_work_status_clear_missing_file_is_noop(
    tmp_path: Path, agent_config_dir: str
) -> None:
    """Clearing a missing status file never creates directories or files."""
    target = tmp_path / agent_config_dir.removeprefix("tmp-path").lstrip("/")
    write_work_status(target, None)
    assert read_work_status(target) == WorkStatus()
    assert not (target / STATUS_FILE_NAME).exists()


def test_write_work_status_creates_parent_dirs(tmp_path: Path) -> None:
    """Writing work status creates missing parent directories."""
    target = tmp_path / "a" / "b" / "c"
    write_work_status(target, "done")
    assert read_work_status(target) == WorkStatus(status="done")


@pytest.mark.parametrize(
    ("status", "message", "error_message"),
    [
        pytest.param("", None, "status must be a non-empty string", id="empty-status"),
        pytest.param(123, None, "status must be a non-empty string", id="non-string-status"),
        pytest.param("testing", {"x": 1}, "message must be a string or None", id="bad-message"),
    ],
)
def test_write_work_status_rejects_invalid_values(
    tmp_path: Path,
    status: object,
    message: object,
    error_message: str,
) -> None:
    """Invalid ``write_work_status`` inputs raise a clear ``ValueError``."""
    with pytest.raises(ValueError, match=error_message):
        write_work_status(tmp_path, status, message=message)  # type: ignore[arg-type]


def test_work_status_vocabulary_matches_display_metadata() -> None:
    """The documented work-status vocabulary and display table stay in sync."""
    assert set(WORK_STATUSES) == EXPECTED_WORK_STATUSES
    assert set(WORK_STATUS_DISPLAY) == EXPECTED_WORK_STATUSES


def test_work_status_display_entries_have_labels_and_native_emoji() -> None:
    """Every display entry has a label, an emoji, and no VS16 sequences."""
    for status, info in WORK_STATUS_DISPLAY.items():
        assert info.label, f"Missing label for {status}"
        assert info.emoji, f"Missing emoji for {status}"
        assert "\ufe0f" not in info.emoji, f"VS16 found in emoji for {status}"


def test_work_status_dataclass_defaults() -> None:
    """``WorkStatus`` defaults to the empty state."""
    assert WorkStatus() == WorkStatus(status=None, message=None)


def test_work_status_dataclass_is_frozen() -> None:
    """``WorkStatus`` instances are immutable."""
    status = WorkStatus(status="coding")
    with pytest.raises(AttributeError):
        status.status = "testing"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(
            {"phase": "testing", "prompt": "Run tests"},
            PendingPhase(phase="testing", prompt="Run tests"),
            id="full-payload",
        ),
        pytest.param(
            {"phase": "coding"},
            PendingPhase(phase="coding", prompt=""),
            id="missing-prompt-defaults-empty",
        ),
    ],
)
def test_read_pending_phase_parses_supported_payloads(
    tmp_path: Path,
    payload: object | str,
    expected: PendingPhase,
) -> None:
    """Supported pending-phase payloads deserialize into ``PendingPhase``."""
    write_payload(tmp_path, PENDING_PHASE_FILE, payload)
    assert read_pending_phase(tmp_path) == expected


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("{{broken", id="malformed-yaml"),
        pytest.param({"prompt": "just a prompt"}, id="missing-phase"),
        pytest.param("bare string\n", id="non-dict"),
        pytest.param({"phase": 123, "prompt": ["x"]}, id="non-string-phase-and-prompt"),
        pytest.param({"phase": "coding", "prompt": {"nested": True}}, id="non-string-prompt"),
    ],
)
def test_read_pending_phase_returns_none_for_invalid_payloads(
    tmp_path: Path,
    payload: object | str,
) -> None:
    """Invalid pending-phase payloads are ignored."""
    write_payload(tmp_path, PENDING_PHASE_FILE, payload)
    assert read_pending_phase(tmp_path) is None


@pytest.mark.parametrize(
    "path_factory",
    [
        pytest.param(lambda base: base, id="missing-file"),
        pytest.param(lambda base: base / "nonexistent", id="missing-dir"),
    ],
)
def test_read_pending_phase_returns_none_when_missing(tmp_path: Path, path_factory) -> None:
    """Missing pending-phase files or directories read as ``None``."""
    assert read_pending_phase(path_factory(tmp_path)) is None


def test_write_pending_phase_round_trips(tmp_path: Path) -> None:
    """Writing pending phase persists a readable payload."""
    write_pending_phase(tmp_path, "reviewing", "Review changes")
    assert read_pending_phase(tmp_path) == PendingPhase("reviewing", "Review changes")


def test_write_pending_phase_creates_parent_dirs(tmp_path: Path) -> None:
    """Writing pending phase creates missing parent directories."""
    target = tmp_path / "a" / "b"
    write_pending_phase(target, "testing", "Run tests")
    assert read_pending_phase(target) == PendingPhase("testing", "Run tests")


@pytest.mark.parametrize(
    ("phase", "prompt", "error_message"),
    [
        pytest.param("", "Run tests", "phase must be a non-empty string", id="empty-phase"),
        pytest.param(123, "Run tests", "phase must be a non-empty string", id="bad-phase"),
        pytest.param("testing", {"x": 1}, "prompt must be a string", id="bad-prompt"),
    ],
)
def test_write_pending_phase_rejects_invalid_values(
    tmp_path: Path,
    phase: object,
    prompt: object,
    error_message: str,
) -> None:
    """Invalid ``write_pending_phase`` inputs raise a clear ``ValueError``."""
    with pytest.raises(ValueError, match=error_message):
        write_pending_phase(tmp_path, phase, prompt)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "prepare",
    [
        pytest.param(True, id="existing-file"),
        pytest.param(False, id="missing-file"),
    ],
)
def test_clear_pending_phase_removes_file_when_present(tmp_path: Path, prepare: bool) -> None:
    """Clearing pending phase is idempotent."""
    if prepare:
        write_pending_phase(tmp_path, "testing", "Run tests")

    clear_pending_phase(tmp_path)

    assert read_pending_phase(tmp_path) is None
    assert not (tmp_path / PENDING_PHASE_FILE).exists()


def test_pending_phase_dataclass_is_frozen() -> None:
    """``PendingPhase`` instances are immutable."""
    pending_phase = PendingPhase(phase="testing", prompt="Run tests")
    with pytest.raises(AttributeError):
        pending_phase.phase = "coding"  # type: ignore[misc]
