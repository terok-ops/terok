# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Utility functions for logging.

Provides best-effort file-based logging and structured stderr warnings.
All functions are exception-safe — they never raise or affect callers.
"""

import sys

LOG_FILENAME = "terok.log"
"""Filename for the best-effort terok library log (written under ``state_root()``)."""


def _log(message: str, *, level: str = "DEBUG") -> None:
    """Append a timestamped line to the terok library log.

    Best-effort, exception-safe: any IO error is silently ignored so this
    function never raises or affects callers.

    Writes to ``state_root()/terok.log``.
    """
    try:
        import time

        from ..core.paths import state_root

        log_path = state_root() / LOG_FILENAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{level}] {message}\n")
    except Exception:  # nosec B110 — intentionally silent; logging must never disrupt callers
        pass


def _log_debug(message: str) -> None:
    """Append a DEBUG line to the terok library log.

    Convenience wrapper — see :func:`_log` for details.
    """
    _log(message, level="DEBUG")


def log_warning(message: str) -> None:
    """Append a WARNING line to the terok library log.

    Convenience wrapper — see :func:`_log` for details.
    """
    _log(message, level="WARNING")


def warn_user(component: str, message: str) -> None:
    """Print a structured warning to stderr and log it.

    Format::

        Warning [terok]: Config file ... is malformed. Using defaults.

    Exception-safe: never raises.  Should be used when a recoverable
    problem occurs that the user needs to know about (e.g. malformed
    config files, missing credentials, silent fallbacks).
    """
    try:
        print(f"Warning [{component}]: {message}", file=sys.stderr)
        log_warning(f"[{component}] {message}")
    except Exception:  # nosec B110 — intentionally silent; user warnings must never raise
        pass
