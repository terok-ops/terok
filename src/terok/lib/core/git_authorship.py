# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Helpers for terok's configurable Git authorship policy."""

DEFAULT_GIT_AUTHORSHIP = "agent-human"
"""Default Git authorship mode for task containers."""

VALID_GIT_AUTHORSHIP_MODES: tuple[str, ...] = (
    "agent-human",
    "human-agent",
    "agent",
    "human",
)
"""Supported values for ``git.authorship`` in config files."""


def normalize_git_authorship(value: object) -> str:
    """Validate and normalize a ``git.authorship`` config value.

    ``None`` or an empty string fall back to [`DEFAULT_GIT_AUTHORSHIP`][].
    Raises [`SystemExit`][] for invalid values so project loading can fail
    with a clear configuration error.
    """
    if value is None:
        return DEFAULT_GIT_AUTHORSHIP

    if not isinstance(value, str):
        valid = ", ".join(VALID_GIT_AUTHORSHIP_MODES)
        raise SystemExit(f"Invalid git.authorship value: expected a string.\nValid values: {valid}")

    normalized = value.strip().lower()
    if not normalized:
        return DEFAULT_GIT_AUTHORSHIP

    if normalized in VALID_GIT_AUTHORSHIP_MODES:
        return normalized

    valid = ", ".join(VALID_GIT_AUTHORSHIP_MODES)
    raise SystemExit(f"Invalid git.authorship value {value!r}.\nValid values: {valid}")
