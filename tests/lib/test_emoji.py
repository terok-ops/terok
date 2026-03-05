# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the emoji display-width utility."""

import unittest

from rich.cells import cell_len

from terok.lib.util.emoji import is_emoji_enabled, render_emoji, set_emoji_enabled


class _FakeInfo:
    """Minimal object satisfying the EmojiInfo protocol."""

    def __init__(self, emoji: str, label: str) -> None:
        self.emoji = emoji
        self.label = label


def _emoji_is_width_2(emoji: str) -> bool:
    """Return True if *emoji* is natively 2 cells wide (no padding needed)."""
    return cell_len(emoji) == 2


class TestRenderEmoji(unittest.TestCase):
    """Verify render_emoji returns the emoji from info objects."""

    def setUp(self):
        """Ensure emoji mode is enabled for each test."""
        set_emoji_enabled(True)

    def tearDown(self):
        """Reset emoji mode after each test."""
        set_emoji_enabled(True)

    def test_returns_emoji_from_info(self):
        """render_emoji returns the emoji attribute."""
        info = _FakeInfo("\U0001f680", "rocket")
        self.assertEqual(render_emoji(info), "\U0001f680")

    def test_empty_emoji_returns_empty(self):
        """Empty emoji string produces empty output."""
        info = _FakeInfo("", "nothing")
        self.assertEqual(render_emoji(info), "")

    def test_all_status_emojis_are_exactly_width_2(self):
        """All status emojis used by the project are exactly 2 cells wide."""
        from terok.lib.containers.task_display import STATUS_DISPLAY

        for status, info in STATUS_DISPLAY.items():
            self.assertTrue(
                _emoji_is_width_2(info.emoji),
                f"Status emoji for {status!r} must be natively 2 cells wide",
            )

    def test_all_mode_emojis_are_exactly_width_2(self):
        """All mode emojis used by the project are exactly 2 cells wide."""
        from terok.lib.containers.task_display import MODE_DISPLAY

        for mode, info in MODE_DISPLAY.items():
            self.assertTrue(
                _emoji_is_width_2(info.emoji),
                f"Mode emoji for {mode!r} must be natively 2 cells wide",
            )

    def test_all_backend_emojis_are_exactly_width_2(self):
        """All web backend emojis are exactly 2 cells wide."""
        from terok.lib.containers.task_display import WEB_BACKEND_DEFAULT, WEB_BACKEND_DISPLAY

        for backend, info in WEB_BACKEND_DISPLAY.items():
            self.assertTrue(
                _emoji_is_width_2(info.emoji),
                f"Backend emoji for {backend!r} must be natively 2 cells wide",
            )
        self.assertTrue(_emoji_is_width_2(WEB_BACKEND_DEFAULT.emoji))

    def test_all_security_class_emojis_are_exactly_width_2(self):
        """All security class emojis are exactly 2 cells wide."""
        from terok.lib.containers.task_display import SECURITY_CLASS_DISPLAY

        for key, badge in SECURITY_CLASS_DISPLAY.items():
            self.assertTrue(
                _emoji_is_width_2(badge.emoji),
                f"Security class emoji for {key!r} must be natively 2 cells wide",
            )

    def test_all_gpu_emojis_are_exactly_width_2(self):
        """All GPU display emojis are exactly 2 cells wide."""
        from terok.lib.containers.task_display import GPU_DISPLAY

        for key, badge in GPU_DISPLAY.items():
            self.assertTrue(
                _emoji_is_width_2(badge.emoji),
                f"GPU emoji for {key!r} must be natively 2 cells wide",
            )

    def test_all_work_status_emojis_are_exactly_width_2(self):
        """All work status emojis are exactly 2 cells wide."""
        from terok.lib.containers.work_status import WORK_STATUS_DISPLAY

        for key, info in WORK_STATUS_DISPLAY.items():
            self.assertTrue(
                _emoji_is_width_2(info.emoji),
                f"Work status emoji for {key!r} must be natively 2 cells wide",
            )


class TestNoEmojiMode(unittest.TestCase):
    """Verify render_emoji returns text labels when emoji mode is disabled."""

    def setUp(self):
        """Disable emoji mode for these tests."""
        set_emoji_enabled(False)

    def tearDown(self):
        """Re-enable emoji mode after tests."""
        set_emoji_enabled(True)

    def test_is_emoji_enabled_false(self):
        """is_emoji_enabled reflects the current state."""
        self.assertFalse(is_emoji_enabled())

    def test_no_emoji_returns_label(self):
        """With emoji disabled, returns [label]."""
        info = _FakeInfo("\U0001f680", "rocket")
        self.assertEqual(render_emoji(info), "[rocket]")

    def test_no_emoji_empty_label_returns_empty(self):
        """With emoji disabled and empty label, returns empty string."""
        info = _FakeInfo("\U0001f680", "")
        self.assertEqual(render_emoji(info), "")

    def test_set_emoji_enabled_toggle(self):
        """Toggling emoji mode changes render_emoji behavior."""
        info = _FakeInfo("\U0001f680", "rocket")
        set_emoji_enabled(True)
        self.assertTrue(is_emoji_enabled())
        self.assertEqual(render_emoji(info), "\U0001f680")

        set_emoji_enabled(False)
        self.assertFalse(is_emoji_enabled())
        self.assertEqual(render_emoji(info), "[rocket]")

    def test_all_status_display_has_labels(self):
        """All STATUS_DISPLAY entries have non-empty labels for no-emoji mode."""
        from terok.lib.containers.task_display import STATUS_DISPLAY

        for status, info in STATUS_DISPLAY.items():
            self.assertTrue(
                info.label,
                f"STATUS_DISPLAY[{status!r}] must have a non-empty label for --no-emoji mode",
            )

    def test_all_mode_display_has_labels(self):
        """All MODE_DISPLAY entries have labels (empty is OK for None mode)."""
        from terok.lib.containers.task_display import MODE_DISPLAY

        for mode, info in MODE_DISPLAY.items():
            if mode is not None:
                self.assertTrue(
                    info.label,
                    f"MODE_DISPLAY[{mode!r}] must have a non-empty label for --no-emoji mode",
                )

    def test_all_backend_display_has_labels(self):
        """All WEB_BACKEND_DISPLAY entries have non-empty labels."""
        from terok.lib.containers.task_display import WEB_BACKEND_DISPLAY

        for backend, info in WEB_BACKEND_DISPLAY.items():
            self.assertTrue(
                info.label,
                f"WEB_BACKEND_DISPLAY[{backend!r}] must have a non-empty label",
            )

    def test_all_security_class_display_has_labels(self):
        """All SECURITY_CLASS_DISPLAY entries have non-empty labels."""
        from terok.lib.containers.task_display import SECURITY_CLASS_DISPLAY

        for key, badge in SECURITY_CLASS_DISPLAY.items():
            self.assertTrue(
                badge.label,
                f"SECURITY_CLASS_DISPLAY[{key!r}] must have a non-empty label",
            )

    def test_all_gpu_display_has_labels(self):
        """All GPU_DISPLAY entries have non-empty labels."""
        from terok.lib.containers.task_display import GPU_DISPLAY

        for key, badge in GPU_DISPLAY.items():
            self.assertTrue(
                badge.label,
                f"GPU_DISPLAY[{key!r}] must have a non-empty label",
            )

    def test_all_work_status_display_has_labels(self):
        """All WORK_STATUS_DISPLAY entries have non-empty labels."""
        from terok.lib.containers.work_status import WORK_STATUS_DISPLAY

        for key, info in WORK_STATUS_DISPLAY.items():
            self.assertTrue(
                info.label,
                f"WORK_STATUS_DISPLAY[{key!r}] must have a non-empty label",
            )


if __name__ == "__main__":
    unittest.main()
