# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for :class:`terok.clearance.identity.IdentityResolver`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from terok_dbus import ContainerIdentity
from terok_sandbox import ContainerInfo

from terok.clearance.identity import (
    ANNOTATION_PROJECT,
    ANNOTATION_TASK,
    IdentityResolver,
)


def _info(
    *,
    container_id: str = "fa0905d97a1c",
    name: str = "sandbox-alpha",
    project: str = "warp-core",
    task_id: str = "t42",
    extras: dict[str, str] | None = None,
) -> ContainerInfo:
    """Build a ContainerInfo stand-in with the terok annotations pre-filled."""
    annotations: dict[str, str] = {
        ANNOTATION_PROJECT: project,
        ANNOTATION_TASK: task_id,
    }
    if extras:
        annotations.update(extras)
    if not project:
        annotations.pop(ANNOTATION_PROJECT)
    if not task_id:
        annotations.pop(ANNOTATION_TASK)
    return ContainerInfo(
        container_id=container_id, name=name, state="running", annotations=annotations
    )


class TestIdentityResolver:
    """Identity resolver composes sandbox inspect + task metadata."""

    def test_unknown_container_returns_empty(self) -> None:
        """Missing podman metadata → empty identity; caller falls back to the ID."""
        inspector = MagicMock(return_value=ContainerInfo())
        assert IdentityResolver(inspector)("ghost") == ContainerIdentity()

    def test_terok_container_resolves_full_triple(self) -> None:
        """Project + task_id annotations + task_name from meta store."""
        inspector = MagicMock(return_value=_info())
        with patch(
            "terok.clearance.identity.load_task_meta",
            return_value=({"name": "build"}, None),
        ):
            identity = IdentityResolver(inspector)("fa0905d97a1c")
        assert identity == ContainerIdentity(
            container_name="sandbox-alpha",
            project="warp-core",
            task_id="t42",
            task_name="build",
        )

    def test_standalone_container_has_name_only(self) -> None:
        """No terok annotations → container-name-only identity."""
        inspector = MagicMock(
            return_value=ContainerInfo(
                container_id="standalone",
                name="my-util",
                state="running",
                annotations={},
            )
        )
        identity = IdentityResolver(inspector)("standalone")
        assert identity == ContainerIdentity(container_name="my-util")

    def test_task_meta_missing_falls_back_to_triple_without_name(self) -> None:
        """A vanished task_meta file doesn't poison the popup — just drop task_name."""
        inspector = MagicMock(return_value=_info())
        with patch(
            "terok.clearance.identity.load_task_meta",
            side_effect=SystemExit("Unknown task t42"),
        ):
            identity = IdentityResolver(inspector)("fa0905d97a1c")
        assert identity.project == "warp-core"
        assert identity.task_id == "t42"
        assert identity.task_name == ""

    def test_task_meta_exception_falls_back_gracefully(self) -> None:
        """A YAML parse error or I/O glitch falls through to empty task_name."""
        inspector = MagicMock(return_value=_info())
        with patch(
            "terok.clearance.identity.load_task_meta",
            side_effect=RuntimeError("yaml corrupted"),
        ):
            identity = IdentityResolver(inspector)("fa0905d97a1c")
        assert identity.task_name == ""
        assert identity.project == "warp-core"

    def test_non_string_task_name_coerces_to_empty(self) -> None:
        """Operator-corrupted meta (e.g. ``name: 42``) renders without a label."""
        inspector = MagicMock(return_value=_info())
        with patch(
            "terok.clearance.identity.load_task_meta",
            return_value=({"name": 42}, None),
        ):
            identity = IdentityResolver(inspector)("fa0905d97a1c")
        assert identity.task_name == ""

    def test_partial_annotations_doesnt_trigger_meta_lookup(self) -> None:
        """``project`` without ``task_id`` (or vice versa) stays name-only."""
        inspector = MagicMock(return_value=_info(task_id=""))
        with patch("terok.clearance.identity.load_task_meta") as meta:
            identity = IdentityResolver(inspector)("fa0905d97a1c")
        meta.assert_not_called()
        assert identity.project == "warp-core"
        assert identity.task_id == ""
        assert identity.task_name == ""
