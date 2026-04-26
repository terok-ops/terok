# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for :meth:`Project.acp_endpoints` — discovery surface for ``terok acp list``."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from terok_executor import ACPEndpointStatus

from terok.lib.domain.project import (
    ACPEndpoint,
    _read_bound_agent,
    _task_has_any_authed_agent,
)


class _FakeMeta:
    """Minimal stand-in for :class:`TaskMeta` — only the fields the helpers read."""

    def __init__(self, mode: str | None = None) -> None:
        self.mode = mode


class _FakeTask:
    """Minimal stand-in for :class:`Task` — exposes the public API surface
    (``id``, ``mode``, ``meta``) the listing helpers consume.  Mirrors the
    real :class:`terok.lib.domain.task.Task`'s shape; ``task_id`` is
    intentionally absent so accidental ``task.task_id`` reads regress."""

    def __init__(self, task_id: str, mode: str | None = None) -> None:
        self.id = task_id
        self.mode = mode
        self.meta = _FakeMeta(mode=mode)


class TestReadBoundAgent:
    """Daemon's ``.bound`` JSON sidecar drives the ``bound_agent`` field."""

    def test_returns_none_when_file_missing(self, tmp_path: Path, monkeypatch) -> None:
        """No sidecar file ⇒ ``None`` (daemon hasn't written it yet)."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        assert _read_bound_agent("proj", "task-1") is None

    def test_returns_agent_name_from_json(self, tmp_path: Path, monkeypatch) -> None:
        """A well-formed sidecar yields the agent name."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        bound_dir = tmp_path / "terok" / "acp" / "proj"
        bound_dir.mkdir(parents=True)
        (bound_dir / "task-1.bound").write_text(json.dumps({"agent": "claude"}))
        assert _read_bound_agent("proj", "task-1") == "claude"

    def test_tolerates_malformed_json(self, tmp_path: Path, monkeypatch) -> None:
        """Partial / corrupt JSON during atomic-replace returns ``None``."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        bound_dir = tmp_path / "terok" / "acp" / "proj"
        bound_dir.mkdir(parents=True)
        (bound_dir / "task-1.bound").write_text("not-json{")
        assert _read_bound_agent("proj", "task-1") is None

    def test_tolerates_unexpected_shape(self, tmp_path: Path, monkeypatch) -> None:
        """JSON without an ``agent`` string field returns ``None``."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        bound_dir = tmp_path / "terok" / "acp" / "proj"
        bound_dir.mkdir(parents=True)
        (bound_dir / "task-1.bound").write_text(json.dumps({"foo": "bar"}))
        assert _read_bound_agent("proj", "task-1") is None


class TestTaskHasAnyAuthedAgent:
    """Auth-intersect-image classification for ``ready`` vs ``unsupported``."""

    def test_intersection_yields_true(self) -> None:
        """Image declares an authed agent → endpoint is ``ready``."""
        with mock.patch(
            "terok.lib.domain.project._image_agents_for_task",
            return_value={"claude", "codex"},
        ):
            task = _FakeTask("task-1", mode="cli")
            assert (
                _task_has_any_authed_agent(
                    "proj", task, {"claude"}, sandbox=mock.Mock(), label_cache={}
                )
                is True
            )

    def test_disjoint_yields_false(self) -> None:
        """Image's agents and the authed set don't overlap → ``unsupported``."""
        with mock.patch(
            "terok.lib.domain.project._image_agents_for_task",
            return_value={"vibe"},
        ):
            task = _FakeTask("task-1", mode="cli")
            assert (
                _task_has_any_authed_agent(
                    "proj", task, {"claude"}, sandbox=mock.Mock(), label_cache={}
                )
                is False
            )

    def test_empty_image_label_yields_false(self) -> None:
        """No agents in the image label ⇒ surface as ``unsupported``."""
        with mock.patch(
            "terok.lib.domain.project._image_agents_for_task",
            return_value=set(),
        ):
            task = _FakeTask("task-1", mode="cli")
            assert (
                _task_has_any_authed_agent(
                    "proj", task, {"claude"}, sandbox=mock.Mock(), label_cache={}
                )
                is False
            )


class TestACPEndpointDataclass:
    """The ``ACPEndpoint`` value object is a frozen dataclass."""

    def test_construction_minimal(self, tmp_path: Path) -> None:
        """All fields are positional/keyword and survive equality."""
        ep1 = ACPEndpoint(
            project_id="p",
            task_id="t",
            socket_path=tmp_path / "x.sock",
            status=ACPEndpointStatus.READY,
        )
        ep2 = ACPEndpoint(
            project_id="p",
            task_id="t",
            socket_path=tmp_path / "x.sock",
            status=ACPEndpointStatus.READY,
        )
        assert ep1 == ep2
        assert ep1.bound_agent is None

    def test_with_bound_agent(self, tmp_path: Path) -> None:
        """``bound_agent`` is set only when the daemon has bound a session."""
        ep = ACPEndpoint(
            project_id="p",
            task_id="t",
            socket_path=tmp_path / "x.sock",
            status=ACPEndpointStatus.ACTIVE,
            bound_agent="claude",
        )
        assert ep.status is ACPEndpointStatus.ACTIVE
        assert ep.bound_agent == "claude"
