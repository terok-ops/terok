# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.testcli import run_cli


def make_config_layout(tmp_path: Path, gate_port: int) -> SimpleNamespace:
    """Create a filesystem layout used by the ``terok config`` tests.

    *gate_port* is the ``gate_server.port`` value written to the synthetic
    ``global.yml``.  ``make_sandbox_config()`` resolves that port through
    the sandbox port registry, which does a real ``bind()`` probe — so the
    test needs a value it can actually bind.  Pass an ephemeral port from
    the ``unused_tcp_port`` fixture to keep the test host-independent.
    """
    global_cfg = tmp_path / "global.yml"
    global_cfg.write_text(f"gate_server:\n  port: {gate_port}\n", encoding="utf-8")

    user_root = tmp_path / "user-projects"
    system_root = tmp_path / "system-projects"
    state_root = tmp_path / "state"
    build_root = tmp_path / "build"
    envs_root = tmp_path / "envs"
    for path in (user_root, system_root, state_root, build_root, envs_root):
        path.mkdir(parents=True, exist_ok=True)

    resources_root = tmp_path / "pkg"
    templates_dir = resources_root / "resources" / "templates"
    scripts_dir = resources_root / "resources" / "scripts"
    templates_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (templates_dir / "l0.template").write_text("", encoding="utf-8")
    (scripts_dir / "script.sh").write_text("", encoding="utf-8")

    project_root = tmp_path / "proj-alpha"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "project.yml").write_text("project:\n  id: alpha\n", encoding="utf-8")
    build_file = build_root / "alpha" / "L0.Dockerfile"
    build_file.parent.mkdir(parents=True, exist_ok=True)
    build_file.write_text("", encoding="utf-8")

    return SimpleNamespace(
        global_cfg=global_cfg,
        user_root=user_root,
        system_root=system_root,
        state_root=state_root,
        build_root=build_root,
        envs_root=envs_root,
        resources_root=resources_root,
        templates_dir=templates_dir,
        project_root=project_root,
    )


@contextmanager
def patch_config_command(layout: SimpleNamespace) -> Iterator[None]:
    """Patch the ``terok config`` command to use the temporary test layout."""
    with ExitStack() as stack:
        # Intentional: clear the environment so config discovery is driven solely by the
        # temporary TEROK_CONFIG_FILE path, keeping output deterministic across hosts.
        stack.enter_context(
            patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(layout.global_cfg)}, clear=True)
        )
        stack.enter_context(patch("terok.cli.commands.info._supports_color", return_value=True))
        stack.enter_context(
            patch("terok.cli.commands.info._global_config_path", return_value=layout.global_cfg)
        )
        stack.enter_context(
            patch(
                "terok.cli.commands.info._global_config_search_paths",
                return_value=[layout.global_cfg],
            )
        )
        stack.enter_context(
            patch("terok.cli.commands.info._vault_dir", return_value=layout.envs_root)
        )
        stack.enter_context(
            patch("terok.cli.commands.info._user_projects_dir", return_value=layout.user_root)
        )
        stack.enter_context(
            patch("terok.cli.commands.info._projects_dir", return_value=layout.system_root)
        )
        stack.enter_context(
            patch("terok.cli.commands.info._core_state_dir", return_value=layout.state_root)
        )
        stack.enter_context(
            patch("terok.cli.commands.info._build_dir", return_value=layout.build_root)
        )
        stack.enter_context(
            patch(
                "terok.cli.commands.info.list_projects",
                return_value=[SimpleNamespace(id="alpha", root=layout.project_root)],
            )
        )
        stack.enter_context(
            patch("terok.cli.commands.info.resources.files", return_value=layout.resources_root)
        )
        yield


def run_import(file_path: Path, envs_root: Path) -> None:
    """Invoke ``terok config import-opencode`` through a temporary config file."""
    config_file = envs_root.parent / "config.yml"
    config_file.write_text(f"credentials:\n  dir: {envs_root}\n", encoding="utf-8")
    with patch.dict(
        os.environ,
        {"TEROK_CONFIG_FILE": str(config_file), "TEROK_VAULT_DIR": str(envs_root)},
    ):
        run_cli("config", "import-opencode", str(file_path))


def test_config_command_color_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], unused_tcp_port: int
) -> None:
    """The config command prints the expected colorized layout details."""
    layout = make_config_layout(tmp_path, gate_port=unused_tcp_port)

    with patch_config_command(layout):
        run_cli("config", "paths")

    output = capsys.readouterr().out
    assert "\x1b[32myes\x1b[0m" in output
    assert "\x1b[35malpha\x1b[0m" in output
    assert f"\x1b[90m{layout.project_root / 'project.yml'}\x1b[0m" in output
    assert f"\x1b[90m{layout.templates_dir}\x1b[0m" in output
    assert "\x1b[90mscript.sh\x1b[0m" in output
    assert f"- TEROK_CONFIG_FILE=\x1b[90m{layout.global_cfg}\x1b[0m" in output
    assert (
        f"- State dir: \x1b[90m{layout.state_root}\x1b[0m (exists: \x1b[32myes\x1b[0m)"
    ) in output


def test_import_valid_json_copies_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Importing a valid OpenCode config copies it into the envs root."""
    envs_root = tmp_path / "envs"
    envs_root.mkdir()
    source = tmp_path / "my-opencode.json"
    source.write_text(json.dumps({"model": "test/model"}), encoding="utf-8")

    run_import(source, envs_root)

    dest = envs_root / "_opencode-config" / "opencode.json"
    assert dest.is_file()
    assert json.loads(dest.read_text(encoding="utf-8"))["model"] == "test/model"
    assert "Imported" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("filename", "content", "expected_message"),
    [
        pytest.param("bad.json", "not json", "Cannot read config", id="invalid-json"),
        pytest.param("nope.json", None, "File not found", id="missing-file"),
        pytest.param("array.json", "[1, 2, 3]", "expected a JSON object", id="non-object-json"),
    ],
)
def test_import_rejects_invalid_configs(
    tmp_path: Path,
    filename: str,
    content: str | None,
    expected_message: str,
) -> None:
    """Invalid OpenCode config payloads fail with actionable errors."""
    envs_root = tmp_path / "envs"
    envs_root.mkdir()
    source = tmp_path / filename
    if content is not None:
        source.write_text(content, encoding="utf-8")

    with pytest.raises(SystemExit, match=expected_message):
        run_import(source, envs_root)


class TestConfigDispatch:
    """The ``config`` group routes each subcommand to the right handler."""

    def test_ignores_non_config(self) -> None:
        """Dispatch returns False for commands outside the config group."""
        import argparse

        from terok.cli.commands.info import dispatch

        assert dispatch(argparse.Namespace(cmd="task")) is False

    def test_paths_invokes_print_config(self) -> None:
        """``config paths`` routes to ``_print_config``."""
        import argparse

        from terok.cli.commands.info import dispatch

        args = argparse.Namespace(cmd="config", config_cmd="paths")
        with patch("terok.cli.commands.info._print_config") as mock:
            assert dispatch(args) is True
        mock.assert_called_once()

    def test_resolved_invokes_config_resolved(self) -> None:
        """``config resolved`` forwards project_id and preset to the handler."""
        import argparse

        from terok.cli.commands.info import dispatch

        args = argparse.Namespace(
            cmd="config", config_cmd="resolved", project_id="myproj", preset="team"
        )
        with patch("terok.cli.commands.info._cmd_config_resolved") as mock:
            assert dispatch(args) is True
        mock.assert_called_once_with("myproj", "team")

    def test_resolved_defaults_preset_to_none(self) -> None:
        """``config resolved`` without --preset passes None through."""
        import argparse

        from terok.cli.commands.info import dispatch

        args = argparse.Namespace(cmd="config", config_cmd="resolved", project_id="p")
        with patch("terok.cli.commands.info._cmd_config_resolved") as mock:
            assert dispatch(args) is True
        mock.assert_called_once_with("p", None)

    def test_import_opencode_invokes_importer(self) -> None:
        """``config import-opencode`` routes to the importer with the file path."""
        import argparse

        from terok.cli.commands.info import dispatch

        args = argparse.Namespace(cmd="config", config_cmd="import-opencode", file="/tmp/oc.json")
        with patch("terok.cli.commands.info._cmd_import_opencode") as mock:
            assert dispatch(args) is True
        mock.assert_called_once_with("/tmp/oc.json")
