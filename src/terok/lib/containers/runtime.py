# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Container runtime gateway wrapping the Podman CLI.

Provides module-level functions for container lifecycle operations
(naming, state queries, GPU args, log streaming, etc.).
"""

import subprocess
from collections.abc import Callable
from typing import Any

from ..core.projects import ProjectConfig, load_project

# ---------- Constants ----------

CONTAINER_MODES = ("cli", "web", "run")


# ---------- Public functions ----------


def container_name(project_id: str, mode: str, task_id: str) -> str:
    """Return the canonical container name for a task."""
    return f"{project_id}-{mode}-{task_id}"


def get_project_container_states(project_id: str) -> dict[str, str]:
    """Return ``{container_name: state}`` for all containers matching *project_id*.

    Uses a single ``podman ps -a`` call with a name filter instead of
    per-container ``podman inspect`` calls.  Returns an empty dict when
    podman is unavailable.
    """
    try:
        out = subprocess.check_output(
            [
                "podman",
                "ps",
                "-a",
                "--filter",
                f"name=^{project_id}-",
                "--format",
                "{{.Names}} {{.State}}",
                "--no-trunc",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}

    result: dict[str, str] = {}
    for line in out.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            result[parts[0]] = parts[1].lower()
    return result


def get_container_state(cname: str) -> str | None:
    """Return container state ('running', 'exited', ...) or ``None`` if not found."""
    try:
        out = subprocess.check_output(
            ["podman", "inspect", "-f", "{{.State.Status}}", cname],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out.lower() if out else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def is_container_running(cname: str) -> bool:
    """Return ``True`` if the named container is currently running."""
    try:
        out = subprocess.check_output(
            ["podman", "inspect", "-f", "{{.State.Running}}", cname],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return out.lower() == "true"


def stop_task_containers(project: Any, task_id: str) -> None:
    """Best-effort ``podman rm -f`` of all mode containers for a task.

    Ignores all errors so that task deletion succeeds even when podman is
    absent or the containers are already gone.
    """
    from ..util.logging_utils import _log_debug

    names = [container_name(project.id, mode, task_id) for mode in CONTAINER_MODES]
    for name in names:
        try:
            _log_debug(f"stop_containers: podman rm -f {name} (start)")
            subprocess.run(
                ["podman", "rm", "-f", name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _log_debug(f"stop_containers: podman rm -f {name} (done)")
        except Exception:
            pass


def gpu_run_args(project: "ProjectConfig") -> list[str]:
    """Return additional ``podman run`` args to enable NVIDIA GPU if configured."""
    import yaml

    enabled = False
    try:
        proj_cfg = yaml.safe_load((project.root / "project.yml").read_text()) or {}
        run_cfg = proj_cfg.get("run", {}) or {}
        gpus = run_cfg.get("gpus")
        if isinstance(gpus, str):
            enabled = gpus.lower() == "all"
        elif isinstance(gpus, bool):
            enabled = gpus
    except Exception:
        enabled = False

    if not enabled:
        return []

    return [
        "--device",
        "nvidia.com/gpu=all",
        "-e",
        "NVIDIA_VISIBLE_DEVICES=all",
        "-e",
        "NVIDIA_DRIVER_CAPABILITIES=all",
    ]


def stream_initial_logs(
    container_name: str,
    timeout_sec: float | None,
    ready_check: Callable[[str], bool],
) -> bool:
    """Stream logs until ready marker is seen or timeout.

    Returns ``True`` if the ready marker was found, ``False`` on timeout.
    """
    import select
    import sys
    import threading
    import time

    from ..util.logging_utils import _log_debug

    holder: list[bool] = [False]
    stop_event = threading.Event()
    proc_holder: list[subprocess.Popen | None] = [None]

    def _stream_logs() -> None:
        """Follow container logs in a thread, setting *holder[0]* on ready."""
        try:
            proc = subprocess.Popen(
                ["podman", "logs", "-f", container_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            proc_holder[0] = proc
            start_time = time.time()
            buf = b""

            while not stop_event.is_set():
                if timeout_sec is not None and time.time() - start_time >= timeout_sec:
                    break
                if proc.poll() is not None:
                    remaining = proc.stdout.read()
                    if remaining:
                        buf += remaining
                    break
                try:
                    ready, _, _ = select.select([proc.stdout], [], [], 0.2)
                    if not ready:
                        continue
                    chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else b""
                    if not chunk:
                        continue
                    buf += chunk
                except Exception as exc:
                    _log_debug(f"_stream_initial_logs read error: {exc}")
                    break

                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if line:
                        print(line, file=sys.stdout, flush=True)
                        if ready_check(line):
                            holder[0] = True
                            proc.terminate()
                            return

            if buf:
                line = buf.decode("utf-8", errors="replace").strip()
                if line:
                    print(line, file=sys.stdout, flush=True)
                    if ready_check(line):
                        holder[0] = True

            proc.terminate()
        except Exception as exc:
            _log_debug(f"_stream_initial_logs error: {exc}")

    stream_thread = threading.Thread(target=_stream_logs)
    stream_thread.start()
    stream_thread.join(timeout_sec)

    if stream_thread.is_alive():
        stop_event.set()
        proc = proc_holder[0]
        if proc is not None:
            proc.terminate()
        stream_thread.join(timeout=5)

    return holder[0]


def wait_for_exit(cname: str, timeout_sec: float | None = None) -> int:
    """Wait for a container to exit and return its exit code.

    Returns 124 on timeout, 1 if podman is not found.
    """
    try:
        proc = subprocess.run(
            ["podman", "wait", cname],
            check=False,
            capture_output=True,
            timeout=timeout_sec,
        )
        stdout = proc.stdout.decode().strip() if isinstance(proc.stdout, bytes) else proc.stdout
        if stdout:
            return int(stdout)
        return proc.returncode
    except subprocess.TimeoutExpired:
        return 124
    except (FileNotFoundError, ValueError):
        return 1


def get_task_container_state(project_id: str, task_id: str, mode: str | None) -> str | None:
    """Get actual container state for a task (TUI helper)."""
    if not mode:
        return None
    try:
        project = load_project(project_id)
    except (SystemExit, ValueError):
        return None
    cname = container_name(project.id, mode, task_id)
    return get_container_state(cname)
