# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Web port allocation for task containers.

Scans existing task metadata to find used ports and allocates the next
free port starting from the configured UI base port.
"""

import socket

from ..core.config import get_ui_base_port, state_dir
from ..util.yaml import load as _yaml_load

_LOCALHOST = "127.0.0.1"


def _is_port_free(port: int) -> bool:
    """Return True if *port* can be bound on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((_LOCALHOST, port))
        except OSError:
            return False
    return True


def _collect_all_web_ports() -> set[int]:
    """Scan all task metadata files and return the set of assigned web ports."""
    # Scan all task metas for any project
    root = state_dir() / "projects"
    ports: set[int] = set()
    if not root.is_dir():
        return ports
    for proj_dir in root.iterdir():
        tdir = proj_dir / "tasks"
        if not tdir.is_dir():
            continue
        for f in tdir.glob("*.yml"):
            try:
                meta = _yaml_load(f.read_text()) or {}
            except Exception as exc:
                from ..util.logging_utils import log_warning

                log_warning(f"Skipping malformed task metadata during port scan: {f}: {exc}")
                continue
            port = meta.get("web_port")
            if isinstance(port, int):
                ports.add(port)
    return ports


def assign_web_port() -> int:
    """Find a free web port starting from the configured UI base port.

    Scans up to 200 successive ports (base_port through base_port + 199),
    skipping ports already recorded in task metadata or currently bound.
    Raises SystemExit if no free port is found.
    """
    used = _collect_all_web_ports()
    base = get_ui_base_port()
    port = base
    max_tries = 200
    tries = 0
    while tries < max_tries:
        if port not in used and _is_port_free(port):
            return port
        port += 1
        tries += 1
    raise SystemExit("No free web ports available")
