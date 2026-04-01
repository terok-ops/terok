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

    Yields a namespace with: base, config_root, state_dir, credentials_dir, config_file, gate_dir.
    """
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        config_base = base / "config"
        config_root = config_base / "projects"
        state_dir = base / "state"
        credentials_dir = base / "credentials"
        config_root.mkdir(parents=True, exist_ok=True)

        write_project(config_root, project_id, yaml_text)

        agent_state_dir = base / "agent"
        env_vars: dict[str, str] = {
            "TEROK_CONFIG_DIR": str(config_base),
            "TEROK_STATE_DIR": str(state_dir),
            "TEROK_CREDENTIALS_DIR": str(credentials_dir),
            "TEROK_AGENT_STATE_DIR": str(agent_state_dir),
        }

        config_file = None
        if with_config_file:
            config_file = base / "config.yml"
            config_file.write_text(f"credentials:\n  dir: {credentials_dir}\n", encoding="utf-8")
            env_vars["TEROK_CONFIG_FILE"] = str(config_file)

        gate_dir = None
        if with_gate:
            gate_dir = state_dir / "gate" / f"{project_id}.git"
            gate_dir.mkdir(parents=True, exist_ok=True)

        if extra_env:
            env_vars.update(extra_env)

        with unittest.mock.patch.dict(os.environ, env_vars, clear=clear_env):
            yield types.SimpleNamespace(
                base=base,
                config_root=config_root,
                state_dir=state_dir,
                credentials_dir=credentials_dir,
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


def make_mock_http_response(data: dict[str, object]) -> unittest.mock.Mock:
    """Create a mock HTTP response that returns JSON data as a context manager."""
    mock_response = unittest.mock.Mock()
    mock_response.read.return_value = json.dumps(data).encode("utf-8")
    mock_response.__enter__ = unittest.mock.Mock(return_value=mock_response)
    mock_response.__exit__ = unittest.mock.Mock(return_value=False)
    return mock_response
