# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task lifecycle hook execution and tracking.

Runs user-configured shell commands at task lifecycle points on the host.
Hook commands receive task context via environment variables.  Tracks
which hooks have fired in task metadata so sickbay can detect and
reconcile missed hooks (e.g. post_stop after an unclean shutdown).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec B404 — hooks execute user-configured commands by design
from pathlib import Path

logger = logging.getLogger(__name__)

_STARTUP_HOOK_TIMEOUT = 120  # seconds for pre_start / post_start / post_ready
_STOP_HOOK_TIMEOUT = 30  # seconds for post_stop

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
    """Append *hook_name* to the ``hooks_fired`` list in task metadata.

    The path may be the canonical JSON file (post-migration) or a
    leftover YAML file from an older install that no read has touched
    yet — both shapes are tolerated so the post-stop hook can record
    itself even when the task was created before the JSON migration
    landed.  After a YAML record, the file rotates to JSON.
    """
    if not meta_path.is_file():
        return
    try:
        text = meta_path.read_text(encoding="utf-8")
        meta = _decode_meta(text, meta_path.suffix)
        fired = meta.get("hooks_fired") or []
        if hook_name not in fired:
            fired.append(hook_name)
        meta["hooks_fired"] = fired
        json_path = meta_path.with_suffix(".json")
        json_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        if meta_path.suffix == ".yml":
            meta_path.unlink(missing_ok=True)
    except Exception:
        logger.warning("failed to record hook %s in %s", hook_name, meta_path, exc_info=True)


def _decode_meta(text: str, suffix: str) -> dict:
    """Parse task meta text — JSON for ``.json``, ruamel for legacy ``.yml``.

    Kept inline (rather than importing the canonical reader from
    ``tasks.py``) so this module stays at its tach layer; ``tasks.py``
    imports ``run_hook`` from here, so the reverse import would build a
    cycle and tach correctly rejects it.
    """
    if suffix == ".yml":
        from ..util.yaml import load as _yaml_load

        raw = _yaml_load(text) or {}
        # Ruamel hands back ``CommentedMap``; convert to a plain dict.
        return _plain(raw)
    return json.loads(text) if text.strip() else {}


def _plain(obj: object) -> object:
    """Recursively unwrap commented containers to plain dict/list."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


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

    timeout = _STOP_HOOK_TIMEOUT if hook_name == "post_stop" else _STARTUP_HOOK_TIMEOUT
    try:
        result = subprocess.run(  # nosec B603 B607
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
        logger.warning("hook %s timed out after %ds", hook_name, timeout)
    except Exception:
        logger.warning("hook %s failed", hook_name, exc_info=True)
