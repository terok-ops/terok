# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import json
import os
import tempfile
import types
import unittest.mock
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from terok_sandbox import GateStalenessInfo

from terok.lib.orchestration.tasks import _TASK_ID_CROCKFORD_4_5_RE


def mock_git_config():
    """Return a mock for _get_global_git_config that returns None (no global git config)."""
    return unittest.mock.patch("terok.lib.core.projects._get_global_git_config", return_value=None)


def write_project(root: Path, project_id: str, yaml_text: str) -> Path:
    proj_dir = root / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "project.yml").write_text(yaml_text, encoding="utf-8")
    return proj_dir


def parse_meta_value(meta_text: str, key: str) -> str | None:
    for line in meta_text.splitlines():
        if line.startswith(f"{key}:"):
            value = line.split(":", 1)[1].strip()
            return value.strip("'\"")
    return None


@contextmanager
def project_env(
    yaml_text: str,
    *,
    project_id: str = "test-proj",
    with_config_file: bool = False,
    with_gate: bool = False,
    extra_env: dict[str, str] | None = None,
    clear_env: bool = False,
) -> Iterator[types.SimpleNamespace]:
    """Create a temp project directory, write project config, and patch env vars.

    Yields a namespace with: base, config_root, state_dir, vault_dir, config_file, gate_dir.
    """
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        config_base = base / "config"
        config_root = config_base / "projects"
        state_dir = base / "state"
        vault_dir = base / "vault"
        config_root.mkdir(parents=True, exist_ok=True)

        write_project(config_root, project_id, yaml_text)

        agent_state_dir = base / "agent"
        sandbox_live = base / "sandbox-live"
        sandbox_state = base / "sandbox-state"
        env_vars: dict[str, str] = {
            "TEROK_CONFIG_DIR": str(config_base),
            "TEROK_ROOT": str(base),
            "TEROK_STATE_DIR": str(state_dir),
            "TEROK_VAULT_DIR": str(vault_dir),
            "TEROK_EXECUTOR_STATE_DIR": str(agent_state_dir),
            "TEROK_SANDBOX_LIVE_DIR": str(sandbox_live),
            "TEROK_SANDBOX_STATE_DIR": str(sandbox_state),
        }

        config_file = None
        if with_config_file:
            config_file = base / "config.yml"
            config_file.write_text(f"credentials:\n  dir: {vault_dir}\n", encoding="utf-8")
            env_vars["TEROK_CONFIG_FILE"] = str(config_file)

        gate_dir = None
        if with_gate:
            gate_dir = sandbox_state / "gate" / f"{project_id}.git"
            gate_dir.mkdir(parents=True, exist_ok=True)

        if extra_env:
            env_vars.update(extra_env)

        with unittest.mock.patch.dict(os.environ, env_vars, clear=clear_env):
            yield types.SimpleNamespace(
                base=base,
                config_root=config_root,
                state_dir=state_dir,
                vault_dir=vault_dir,
                config_file=config_file,
                gate_dir=gate_dir,
            )


def make_staleness_info(**overrides: Any) -> GateStalenessInfo:
    """Create a GateStalenessInfo with sensible defaults."""
    defaults = {
        "branch": "main",
        "gate_head": "aaa",
        "upstream_head": "bbb",
        "is_stale": True,
        "commits_behind": 1,
        "commits_ahead": 0,
        "last_checked": "now",
        "error": None,
    }
    defaults.update(overrides)
    return GateStalenessInfo(**defaults)


def assert_task_id(task_id: str | None) -> None:
    """Assert that *task_id* is a valid Crockford-format task ID.

    Format: ``[g-z minus i,l,o,u][0-9][Crockford]{3}`` — 5 chars total.
    See :mod:`terok.lib.orchestration.tasks` for the generator.
    """
    assert isinstance(task_id, str), f"Expected task ID string, got {task_id!r}"
    assert _TASK_ID_CROCKFORD_4_5_RE.fullmatch(task_id), f"Not a valid task ID: {task_id!r}"


def make_mock_http_response(data: dict[str, object]) -> unittest.mock.Mock:
    """Create a mock HTTP response that returns JSON data as a context manager."""
    mock_response = unittest.mock.Mock()
    mock_response.read.return_value = json.dumps(data).encode("utf-8")
    mock_response.__enter__ = unittest.mock.Mock(return_value=mock_response)
    mock_response.__exit__ = unittest.mock.Mock(return_value=False)
    return mock_response


def captured_runspec(agent_runner_mock: unittest.mock.Mock) -> Any:
    """Reconstruct the ``RunSpec`` from the last recorded ``launch_prepared`` call.

    Tests patch ``task_runners._agent_runner`` to observe what terok hands to
    the executor.  ``AgentRunner.launch_prepared`` takes the same fields that
    used to appear on the ``RunSpec`` directly — this helper rebuilds a
    ``RunSpec`` from the captured kwargs so existing spec-level assertions
    continue to read naturally.
    """
    from terok_sandbox import RunSpec

    kwargs = agent_runner_mock.return_value.launch_prepared.call_args.kwargs
    return RunSpec(
        container_name=kwargs["name"],
        image=kwargs["image"],
        env=kwargs["env"],
        volumes=tuple(kwargs["volumes"]),
        command=tuple(kwargs["command"]),
        task_dir=kwargs["task_dir"],
        gpu_enabled=kwargs.get("gpu", False),
        memory_limit=kwargs.get("memory"),
        cpu_limit=kwargs.get("cpus"),
        extra_args=tuple(kwargs.get("extra_args") or ()),
        unrestricted=kwargs.get("unrestricted", True),
        sealed=kwargs.get("sealed", False),
        hostname=kwargs.get("hostname"),
    )
