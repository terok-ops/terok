# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for emoji rendering and display metadata hygiene."""

from __future__ import annotations

from collections.abc import Generator, Iterable

import pytest
from rich.cells import cell_len

from terok.lib.containers.task_display import (
    GPU_DISPLAY,
    MODE_DISPLAY,
    SECURITY_CLASS_DISPLAY,
    STATUS_DISPLAY,
)
from terok.lib.containers.work_status import WORK_STATUS_DISPLAY
from terok.lib.util.emoji import EmojiInfo, is_emoji_enabled, render_emoji, set_emoji_enabled


class _FakeInfo:
    """Minimal object satisfying the ``EmojiInfo`` protocol."""

    def __init__(self, emoji: str, label: str) -> None:
        self.emoji = emoji
        self.label = label


EMOJI_COLLECTIONS = [
    pytest.param("status", STATUS_DISPLAY.values(), id="status"),
    pytest.param("mode", MODE_DISPLAY.values(), id="mode"),
    pytest.param("security-class", SECURITY_CLASS_DISPLAY.values(), id="security-class"),
    pytest.param("gpu", GPU_DISPLAY.values(), id="gpu"),
    pytest.param("work-status", WORK_STATUS_DISPLAY.values(), id="work-status"),
]

LABEL_COLLECTIONS = [
    pytest.param("status", STATUS_DISPLAY.values(), id="status"),
    pytest.param("security-class", SECURITY_CLASS_DISPLAY.values(), id="security-class"),
    pytest.param("gpu", GPU_DISPLAY.values(), id="gpu"),
    pytest.param("work-status", WORK_STATUS_DISPLAY.values(), id="work-status"),
]


@pytest.fixture(autouse=True)
def reset_emoji_mode() -> Generator[None, None, None]:
    """Reset emoji rendering mode before and after each test."""
    set_emoji_enabled(True)
    yield
    set_emoji_enabled(True)


def is_width_two(emoji: str) -> bool:
    """Return whether *emoji* occupies exactly two cells."""
    return cell_len(emoji) == 2


@pytest.mark.parametrize(
    ("enabled", "info", "expected"),
    [
        pytest.param(True, _FakeInfo("🚀", "rocket"), "🚀", id="enabled"),
        pytest.param(True, _FakeInfo("", "nothing"), "", id="enabled-empty-emoji"),
        pytest.param(False, _FakeInfo("🚀", "rocket"), "[rocket]", id="disabled"),
        pytest.param(False, _FakeInfo("🚀", ""), "", id="disabled-empty-label"),
    ],
)
def test_render_emoji_respects_global_mode(enabled: bool, info: _FakeInfo, expected: str) -> None:
    """Emoji rendering returns either the emoji or a text fallback."""
    set_emoji_enabled(enabled)
    assert is_emoji_enabled() is enabled
    assert render_emoji(info) == expected


def test_set_emoji_enabled_toggles_global_state() -> None:
    """The public toggle API updates the observable global state."""
    assert is_emoji_enabled()
    set_emoji_enabled(False)
    assert not is_emoji_enabled()


@pytest.mark.parametrize(("name", "infos"), EMOJI_COLLECTIONS)
def test_project_display_emojis_are_natively_two_cells(
    name: str,
    infos: Iterable[EmojiInfo],
) -> None:
    """Every registered project emoji is natively two cells wide."""
    assert all(is_width_two(info.emoji) for info in infos), name
    assert all("\ufe0f" not in info.emoji for info in infos), name


@pytest.mark.parametrize(("name", "infos"), LABEL_COLLECTIONS)
def test_display_entries_have_non_empty_labels(
    name: str,
    infos: Iterable[EmojiInfo],
) -> None:
    """Collections used in ``--no-emoji`` mode expose non-empty labels."""
    assert all(info.label for info in infos), name


def test_mode_display_entries_have_labels_except_placeholder() -> None:
    """Only the unset placeholder mode may omit a no-emoji label."""
    for mode, info in MODE_DISPLAY.items():
        assert info.label or mode is None
