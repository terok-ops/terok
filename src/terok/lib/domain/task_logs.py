# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task log viewing and streaming.

Provides the ``task_logs`` function for viewing formatted container logs.
Split from ``tasks.py`` to isolate log streaming, signal handling, and
formatter selection from task metadata management.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from terok_sandbox import get_container_state

from ..core.projects import load_project
from ..orchestration.tasks import container_name, tasks_meta_dir
from ..util.yaml import load as _yaml_load
from .log_format import auto_detect_formatter


@dataclass(frozen=True)
class LogViewOptions:
    """Display options for task log viewing."""

    follow: bool = False
    """Follow live output (``-f``)."""

    raw: bool = False
    """Bypass formatting, show raw podman output."""

    tail: int | None = None
    """Show only the last N lines."""

    streaming: bool = True
    """Enable partial streaming (typewriter effect) for supported formatters."""


def task_logs(
    project_id: str,
    task_id: str,
    options: LogViewOptions | None = None,
) -> None:
    """View formatted logs for a task container.

    Works on both running and exited containers (podman logs supports both).

    Args:
        project_id: The project ID.
        task_id: The task ID.
        options: Display options (follow, raw, tail, streaming).
    """
    if options is None:
        options = LogViewOptions()
    import select
    import signal

    project = load_project(project_id)
    meta_dir = tasks_meta_dir(project.id)
    meta_path = meta_dir / f"{task_id}.yml"
    if not meta_path.is_file():
        raise SystemExit(f"Unknown task {task_id}")
    meta = _yaml_load(meta_path.read_text(encoding="utf-8")) or {}

    mode = meta.get("mode")
    if not mode:
        raise SystemExit(
            f"Task {task_id} has never been run (no mode set). "
            f"Start it first via 'terok task run-cli {project_id} {task_id}'."
        )

    # Validate --tail early so both live and persisted paths behave consistently
    if options.tail is not None and options.tail < 0:
        raise SystemExit("--tail must be >= 0")

    cname = container_name(project.id, mode, task_id)

    # Verify container exists (running or exited)
    state = get_container_state(cname)
    if state is None:
        # Fall back to persisted log files on the host
        task_dir = project.tasks_root / str(task_id)
        log_file = task_dir / "logs" / "container.log"
        if log_file.is_file():
            _show_persisted_logs(
                log_file,
                tail=options.tail,
                streaming=options.streaming,
                mode=mode,
                provider=meta.get("provider"),
            )
            return
        raise SystemExit(
            f"Container {cname} does not exist and no persisted logs found. "
            f"Run 'terok task restart {project_id} {task_id}' first."
        )

    # Build podman logs command
    cmd = ["podman", "logs"]
    if options.follow:
        cmd.append("-f")
    if options.tail is not None:
        cmd.extend(["--tail", str(options.tail)])
    cmd.append(cname)

    if options.raw:
        # Raw mode: exec podman directly, no formatting
        try:
            os.execvp(cmd[0], cmd)
        except FileNotFoundError:
            raise SystemExit("podman not found; please install podman")

    # Formatted mode: pipe through formatter
    provider = meta.get("provider")
    formatter = auto_detect_formatter(mode, streaming=options.streaming, provider=provider)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        raise SystemExit("podman not found; please install podman")

    # Handle Ctrl+C gracefully
    interrupted = False
    original_sigint = signal.getsignal(signal.SIGINT)

    def _sigint_handler(signum, frame):
        """Set the interrupted flag on Ctrl+C."""
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        buf = b""
        while not interrupted:
            if proc.poll() is not None:
                # Process exited — drain remaining output
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
            except (OSError, ValueError):
                break

            # Process complete lines
            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="replace")
                formatter.feed_line(line)

        # Flush any trailing partial line
        if buf:
            line = buf.decode("utf-8", errors="replace")
            if line.strip():
                formatter.feed_line(line)
    finally:
        signal.signal(signal.SIGINT, original_sigint)
        stderr_output = b""
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        try:
            stderr_output = proc.stderr.read() or b""
        except (OSError, ValueError):
            pass
        formatter.finish()

    # Report podman errors if process failed and wasn't interrupted
    if not interrupted and proc.returncode and proc.returncode != 0:
        stderr_text = stderr_output.decode("utf-8", errors="replace").strip()
        if stderr_text:
            print(f"Warning: podman logs exited with code {proc.returncode}: {stderr_text}")

    if interrupted:
        print()


def _show_persisted_logs(
    log_file: Path,
    *,
    tail: int | None = None,
    streaming: bool = True,
    mode: str | None = None,
    provider: str | None = None,
) -> None:
    """Display logs from a persisted log file on disk.

    Applies the same formatter pipeline as live container logs so output
    is consistent whether reading from podman or from the host filesystem.
    Streams the file line-by-line to avoid loading the entire log into memory.
    """
    from collections import deque

    formatter = auto_detect_formatter(mode, streaming=streaming, provider=provider)

    with log_file.open("r", encoding="utf-8", errors="replace") as f:
        if tail is not None and tail > 0:
            for line in deque((ln.rstrip("\n") for ln in f), maxlen=tail):
                formatter.feed_line(line)
        elif tail == 0:
            pass  # tail=0 means show nothing
        else:
            for line in f:
                formatter.feed_line(line.rstrip("\n"))
    formatter.finish()
