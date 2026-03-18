# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task lifecycle hook execution and tracking.

Runs user-configured shell commands at task lifecycle points on the host.
Hook commands receive task context via environment variables.  Tracks
which hooks have fired in task metadata so sickbay can detect and
reconcile missed hooks (e.g. post_stop after an unclean shutdown).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from ..util.yaml import dump as _yaml_dump, load as _yaml_load

logger = logging.getLogger(__name__)

_HOOK_TIMEOUT = 30  # seconds for post_stop timeout

#: Hooks that fire during the task lifecycle.
HOOK_NAMES = ("pre_start", "post_start", "post_ready", "post_stop")


def _build_hook_env(
    project_id: str,
    task_id: str,
    mode: str,
    cname: str,
    hook_name: str,
    *,
    web_port: int | None = None,
    task_dir: Path | None = None,
) -> dict[str, str]:
    """Build the environment dict passed to hook commands."""
    env = {
        **os.environ,
        "TEROK_HOOK": hook_name,
        "TEROK_PROJECT_ID": project_id,
        "TEROK_TASK_ID": str(task_id),
        "TEROK_TASK_MODE": mode,
        "TEROK_CONTAINER_NAME": cname,
    }
    if web_port is not None:
        env["TEROK_WEB_PORT"] = str(web_port)
    if task_dir is not None:
        env["TEROK_TASK_DIR"] = str(task_dir)
    return env


def _record_hook(meta_path: Path, hook_name: str) -> None:
    """Append *hook_name* to the ``hooks_fired`` list in task metadata."""
    if not meta_path.is_file():
        return
    try:
        meta = _yaml_load(meta_path.read_text()) or {}
        fired = meta.get("hooks_fired") or []
        if hook_name not in fired:
            fired.append(hook_name)
        meta["hooks_fired"] = fired
        meta_path.write_text(_yaml_dump(meta))
    except Exception:
        logger.warning("failed to record hook %s in %s", hook_name, meta_path, exc_info=True)


def run_hook(
    hook_name: str,
    command: str | None,
    *,
    project_id: str,
    task_id: str,
    mode: str,
    cname: str,
    web_port: int | None = None,
    task_dir: Path | None = None,
    meta_path: Path | None = None,
) -> None:
    """Execute a lifecycle hook command if configured.

    The command is run via ``sh -c`` with task context in environment
    variables.  Errors are logged as warnings — hooks must not break the
    task lifecycle.

    If *meta_path* is provided, the hook name is recorded in the task's
    ``hooks_fired`` metadata list (even when *command* is None — the hook
    point was reached, so it counts as "fired").
    """
    # Always record that this hook point was reached, even if no command
    if meta_path:
        _record_hook(meta_path, hook_name)

    if not command:
        return

    env = _build_hook_env(
        project_id,
        task_id,
        mode,
        cname,
        hook_name,
        web_port=web_port,
        task_dir=task_dir,
    )

    logger.debug("hook %s: running %r", hook_name, command)

    timeout = _HOOK_TIMEOUT if hook_name == "post_stop" else None
    try:
        result = subprocess.run(
            ["sh", "-c", command],
            env=env,
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            logger.debug("hook %s stdout: %s", hook_name, result.stdout.rstrip())
        if result.stderr:
            logger.debug("hook %s stderr: %s", hook_name, result.stderr.rstrip())
    except subprocess.TimeoutExpired:
        logger.warning("hook %s timed out after %ds", hook_name, _HOOK_TIMEOUT)
    except Exception:
        logger.warning("hook %s failed", hook_name, exc_info=True)
