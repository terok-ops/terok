# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Storage usage summary: how much disk space terok is using.

Two modes:
- Default (``terok storage``): fast overview with per-project one-liners
- Detail (``terok storage --project <id>``): per-task breakdown with overlays
"""

from __future__ import annotations

import argparse

from ...lib.util.ansi import blue, bold, color, supports_color
from ._completers import complete_project_ids as _complete_project_ids, set_completer

# ---------------------------------------------------------------------------
# Column formatting helpers
# ---------------------------------------------------------------------------

_TABLE_WIDTH = 50


def _c(enabled: bool) -> bool:
    """Alias for the color-enabled flag threading."""
    return enabled


def _section(title: str, enabled: bool) -> str:
    """Render a section header: ``═══ Title ═════...``."""
    pad = max(0, _TABLE_WIDTH - len(title) - 5)
    bar = f"═══ {title} {'═' * pad}"
    return bold(color(bar, "35", enabled), enabled)


def _sub_header(text: str, enabled: bool) -> str:
    """Render a sub-section header like ``Images:``."""
    return bold(text, enabled)


def _size(text: str, enabled: bool) -> str:
    """Render a size value in blue."""
    return blue(text, enabled)


def _dim(text: str, enabled: bool) -> str:
    """Render secondary text in gray."""
    return color(text, "90", enabled)


def _separator(width: int, enabled: bool) -> str:
    """Render a thin separator line."""
    return _dim("─" * width, enabled)


def _heavy_separator(enabled: bool) -> str:
    """Render a heavy separator for grand totals."""
    return _dim("═" * _TABLE_WIDTH, enabled)


# ---------------------------------------------------------------------------
# CLI registration and dispatch
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``storage`` subcommand."""
    p = subparsers.add_parser("storage", help="Show storage usage summary")
    set_completer(
        p.add_argument(
            "--project",
            default=None,
            help="Show detailed per-task breakdown for one project",
        ),
        _complete_project_ids,
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Machine-readable JSON output",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the ``storage`` command.  Returns True if handled."""
    if args.cmd != "storage":
        return False
    project_id = getattr(args, "project", None)
    json_output = getattr(args, "json_output", False)
    if project_id:
        _cmd_detail(project_id, json_output=json_output)
    else:
        _cmd_overview(json_output=json_output)
    return True


# ---------------------------------------------------------------------------
# Overview mode — the fast, wide-angle view
# ---------------------------------------------------------------------------


def _cmd_overview(*, json_output: bool = False) -> None:
    """Print the global storage overview."""
    from ...lib.domain.storage import format_bytes, get_storage_overview

    overview = get_storage_overview()

    if json_output:
        _json_overview(overview)
        return

    c = _c(supports_color())

    # ── Global section ──
    print(_section("Global", c))

    # Images
    if overview.global_images:
        print(_sub_header("Images:", c))
        name_w = max(len(img.full_name) for img in overview.global_images)
        size_w = max(len(img.size) for img in overview.global_images)
        print(
            f"  {bold('NAME', c):<{name_w + 10}}  {bold('SIZE', c):>{size_w + 10}}  {bold('CREATED', c)}"
        )
        for img in overview.global_images:
            print(
                f"  {img.full_name:<{name_w}}  "
                f"{_size(f'{img.size:>{size_w}}', c)}  "
                f"{_dim(img.created, c)}"
            )
        total = format_bytes(overview.global_images_bytes)
        print(f"  {'':<{name_w}}  {_separator(size_w, c)}")
        print(f"  {'':<{name_w}}  {_size(bold(f'{total:>{size_w}}', c), c)}")

    # Shared mounts
    if overview.shared_mounts:
        print(_sub_header("Shared mounts:", c))
        label_w = max(len(m.label) for m in overview.shared_mounts)
        sizes = [format_bytes(m.bytes) for m in overview.shared_mounts]
        size_w = max(len(s) for s in sizes)
        for m, s in zip(overview.shared_mounts, sizes, strict=True):
            print(f"  {m.label:<{label_w}}  {_size(f'{s:>{size_w}}', c)}")
        total = format_bytes(overview.shared_mounts_bytes)
        print(f"  {'':<{label_w}}  {_separator(size_w, c)}")
        print(f"  {'':<{label_w}}  {_size(bold(f'{total:>{size_w}}', c), c)}")

    # ── Projects section ──
    if overview.projects:
        print(_section("Projects", c))
        pid_w = max(len(p.project_id) for p in overview.projects)
        img_sizes = [format_bytes(p.image_bytes) for p in overview.projects]
        ws_sizes = [format_bytes(p.workspace_bytes) for p in overview.projects]
        img_w = max(len(s) for s in img_sizes + ["IMAGES"])
        ws_w = max(len(s) for s in ws_sizes + ["WORKSPACES"])
        task_w = max(len(str(p.task_count)) for p in overview.projects)
        task_w = max(task_w, len("TASKS"))

        print(
            f"  {bold('PROJECT', c):<{pid_w + 10}}  "
            f"{bold('IMAGES', c):>{img_w + 10}}  "
            f"{bold('WORKSPACES', c):>{ws_w + 10}}  "
            f"{bold('TASKS', c):>{task_w + 10}}"
        )
        for p, img_s, ws_s in zip(overview.projects, img_sizes, ws_sizes, strict=True):
            print(
                f"  {p.project_id:<{pid_w}}  "
                f"{_size(f'{img_s:>{img_w}}', c)}  "
                f"{_size(f'{ws_s:>{ws_w}}', c)}  "
                f"{p.task_count:>{task_w}}"
            )

        total_img = format_bytes(sum(p.image_bytes for p in overview.projects))
        total_ws = format_bytes(sum(p.workspace_bytes for p in overview.projects))
        total_tasks = sum(p.task_count for p in overview.projects)
        print(f"  {'':<{pid_w}}  {_separator(img_w, c)}  {_separator(ws_w, c)}")
        print(
            f"  {'':<{pid_w}}  "
            f"{_size(bold(f'{total_img:>{img_w}}', c), c)}  "
            f"{_size(bold(f'{total_ws:>{ws_w}}', c), c)}  "
            f"{bold(f'{total_tasks:>{task_w}}', c)}"
        )

    # ── Grand total ──
    print(_heavy_separator(c))
    gt = format_bytes(overview.grand_total)
    label = "Grand total"
    print(f"{bold(label, c)}  {_size(bold(f'{gt:>{_TABLE_WIDTH - len(label) - 2}}', c), c)}")


# ---------------------------------------------------------------------------
# Detail mode — zooming into one project
# ---------------------------------------------------------------------------


def _cmd_detail(project_id: str, *, json_output: bool = False) -> None:
    """Print per-task storage detail for one project."""
    from ...lib.domain.storage import format_bytes, get_project_storage_detail

    detail = get_project_storage_detail(project_id)

    if json_output:
        _json_detail(detail)
        return

    c = _c(supports_color())
    print(_section(project_id, c))

    # Images
    if detail.images:
        print(_sub_header("Images:", c))
        name_w = max(len(img.full_name) for img in detail.images)
        size_w = max(len(img.size) for img in detail.images)
        for img in detail.images:
            print(f"  {img.full_name:<{name_w}}  {_size(f'{img.size:>{size_w}}', c)}")

    # Tasks with overlay sizes
    if detail.tasks:
        print(_sub_header("Tasks:", c))
        tid_w = max(len(t.task_id) for t in detail.tasks)
        ws_strs = [format_bytes(t.workspace_bytes) for t in detail.tasks]
        ws_w = max(len(s) for s in ws_strs + ["WORKSPACE"])
        ov_strs = [format_bytes(detail.overlays.get(t.task_id, 0)) for t in detail.tasks]
        ov_w = max(len(s) for s in ov_strs + ["OVERLAY"])

        print(
            f"  {bold('ID', c):<{tid_w + 10}}  "
            f"{bold('WORKSPACE', c):>{ws_w + 10}}  "
            f"{bold('OVERLAY', c):>{ov_w + 10}}"
        )
        for t, ws_s, ov_s in zip(detail.tasks, ws_strs, ov_strs, strict=True):
            print(
                f"  {t.task_id:<{tid_w}}  "
                f"{_size(f'{ws_s:>{ws_w}}', c)}  "
                f"{_size(f'{ov_s:>{ov_w}}', c)}"
            )

        total_ws = format_bytes(detail.workspace_bytes)
        total_ov = format_bytes(detail.overlay_bytes)
        print(f"  {'':<{tid_w}}  {_separator(ws_w, c)}  {_separator(ov_w, c)}")
        print(
            f"  {'':<{tid_w}}  "
            f"{_size(bold(f'{total_ws:>{ws_w}}', c), c)}  "
            f"{_size(bold(f'{total_ov:>{ov_w}}', c), c)}"
        )

    # Project total
    print(_separator(_TABLE_WIDTH, c))
    gt = format_bytes(detail.total_bytes)
    label = "Project total"
    print(f"{bold(label, c)}  {_size(bold(f'{gt:>{_TABLE_WIDTH - len(label) - 2}}', c), c)}")


# ---------------------------------------------------------------------------
# JSON output — machine-readable alternative
# ---------------------------------------------------------------------------


def _json_overview(overview: StorageOverview) -> None:  # noqa: F821
    """Emit the overview as JSON."""
    import json

    from ...lib.domain.storage import parse_image_size

    data = {
        "global": {
            "images": [
                {"name": img.full_name, "size": img.size, "bytes": parse_image_size(img.size)}
                for img in overview.global_images
            ],
            "shared_mounts": [
                {"name": m.name, "label": m.label, "bytes": m.bytes} for m in overview.shared_mounts
            ],
        },
        "projects": [
            {
                "id": p.project_id,
                "image_bytes": p.image_bytes,
                "workspace_bytes": p.workspace_bytes,
                "task_count": p.task_count,
                "total_bytes": p.total_bytes,
            }
            for p in overview.projects
        ],
        "grand_total_bytes": overview.grand_total,
    }
    print(json.dumps(data, indent=2))


def _json_detail(detail: ProjectDetail) -> None:  # noqa: F821
    """Emit the project detail as JSON."""
    import json

    from ...lib.domain.storage import parse_image_size

    data = {
        "project_id": detail.project_id,
        "images": [
            {"name": img.full_name, "size": img.size, "bytes": parse_image_size(img.size)}
            for img in detail.images
        ],
        "tasks": [
            {
                "task_id": t.task_id,
                "workspace_bytes": t.workspace_bytes,
                "agent_config_bytes": t.agent_config_bytes,
                "overlay_bytes": detail.overlays.get(t.task_id, 0),
            }
            for t in detail.tasks
        ],
        "total_bytes": detail.total_bytes,
    }
    print(json.dumps(data, indent=2))
