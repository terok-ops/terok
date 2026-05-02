# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for task lifecycle hooks."""

from __future__ import annotations

import subprocess
import unittest.mock
from pathlib import Path

import pytest

from terok.lib.orchestration.hooks import _build_hook_env, _record_hook, run_hook


class TestBuildHookEnv:
    """Tests for _build_hook_env helper."""

    def test_basic_env(self) -> None:
        """Verify core environment variables are set."""
        env = _build_hook_env("proj", "1", "toad", "proj-toad-1", "post_ready")
        assert env["TEROK_HOOK"] == "post_ready"
        assert env["TEROK_PROJECT_ID"] == "proj"
        assert env["TEROK_TASK_ID"] == "1"
        assert env["TEROK_TASK_MODE"] == "toad"
        assert env["TEROK_CONTAINER_NAME"] == "proj-toad-1"
        assert "TEROK_WEB_PORT" not in env

    def test_with_web_port(self) -> None:
        """Verify TEROK_WEB_PORT is set when web_port is given."""
        env = _build_hook_env("p", "2", "toad", "c", "post_ready", web_port=7861)
        assert env["TEROK_WEB_PORT"] == "7861"

    def test_with_task_dir(self, tmp_path: Path) -> None:
        """Verify TEROK_TASK_DIR is set when task_dir is given."""
        env = _build_hook_env("p", "3", "cli", "c", "post_start", task_dir=tmp_path)
        assert env["TEROK_TASK_DIR"] == str(tmp_path)

    def test_inherits_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the hook env inherits the host process environment."""
        monkeypatch.setenv("MY_CUSTOM_VAR", "hello")
        env = _build_hook_env("p", "1", "cli", "c", "post_start")
        assert env["MY_CUSTOM_VAR"] == "hello"


class TestRecordHook:
    """Tests for _record_hook metadata tracking."""

    def test_record_hook_writes_to_metadata(self, tmp_path: Path) -> None:
        """Verify _record_hook appends hook_name to hooks_fired list."""
        import json

        meta_path = tmp_path / "1.json"
        meta_path.write_text(json.dumps({"task_id": "1", "mode": "cli"}, indent=2))

        _record_hook(meta_path, "post_start")

        meta = json.loads(meta_path.read_text() or "{}")
        assert meta["hooks_fired"] == ["post_start"]

    def test_record_hook_appends_without_duplicates(self, tmp_path: Path) -> None:
        """Verify _record_hook doesn't duplicate existing entries."""
        import json

        meta_path = tmp_path / "1.json"
        meta_path.write_text(json.dumps({"task_id": "1", "hooks_fired": ["post_start"]}, indent=2))

        _record_hook(meta_path, "post_start")
        _record_hook(meta_path, "post_ready")

        meta = json.loads(meta_path.read_text() or "{}")
        assert meta["hooks_fired"] == ["post_start", "post_ready"]

    def test_record_hook_skips_missing_file(self, tmp_path: Path) -> None:
        """Verify _record_hook is a no-op when the metadata file doesn't exist."""
        meta_path = tmp_path / "nonexistent.json"
        _record_hook(meta_path, "post_start")  # should not raise

    def test_record_hook_migrates_yaml_then_keeps_recording(self, tmp_path: Path) -> None:
        """A caller pinning the legacy ``.yml`` Path keeps recording after migration.

        Regression: the first call rotates ``.yml`` → ``.json`` and unlinks
        the YAML.  Without resolving to the canonical ``.json`` first, every
        later call (``post_start`` → ``post_ready`` → ``post_stop``) would hit
        the existence check on the now-gone YAML and silently drop the hook
        from ``hooks_fired``.
        """
        import json

        from terok.lib.util.yaml import dump as yaml_dump

        # Caller hands in a stale YAML path — the typical pre_start/post_start
        # call site captures meta_path before any migration has happened.
        yaml_path = tmp_path / "1.yml"
        yaml_path.write_text(yaml_dump({"task_id": "1", "mode": "cli"}))

        _record_hook(yaml_path, "pre_start")  # migrates to JSON, unlinks YAML
        _record_hook(yaml_path, "post_start")  # would no-op without the fix
        _record_hook(yaml_path, "post_ready")
        _record_hook(yaml_path, "post_stop")

        # YAML is gone, JSON is canonical.
        assert not yaml_path.exists()
        json_path = tmp_path / "1.json"
        assert json_path.is_file()
        meta = json.loads(json_path.read_text())
        assert meta["hooks_fired"] == ["pre_start", "post_start", "post_ready", "post_stop"]

    def test_record_hook_writes_atomically(self, tmp_path: Path) -> None:
        """``_record_hook`` stages to ``*.tmp`` and ``os.replace`` — no truncated JSON.

        An interrupted record must never leave a partial file behind.  Force
        a failure during the temp-file write and assert the canonical JSON
        is unchanged (would otherwise be truncated under the old in-place
        ``write_text`` pattern).
        """
        import json
        from unittest import mock

        meta_path = tmp_path / "1.json"
        original = {"task_id": "1", "hooks_fired": ["pre_start"]}
        meta_path.write_text(json.dumps(original, indent=2))

        original_write_text = Path.write_text

        def _fail_on_tmp(self: Path, *args, **kwargs):  # noqa: ANN001, ANN202
            if self.suffix == ".tmp":
                raise OSError("disk full")
            return original_write_text(self, *args, **kwargs)

        with mock.patch.object(Path, "write_text", _fail_on_tmp):
            _record_hook(meta_path, "post_start")  # logs + swallows OSError

        # Original content intact — no torn write.
        assert json.loads(meta_path.read_text()) == original


class TestRunHook:
    """Tests for run_hook execution."""

    def test_none_command_is_noop(self) -> None:
        """A None command should be a silent no-op."""
        run_hook(
            "post_start",
            None,
            project_id="p",
            task_id="1",
            mode="cli",
            cname="c",
        )

    def test_empty_string_is_noop(self) -> None:
        """An empty string command should be a silent no-op."""
        run_hook(
            "post_start",
            "",
            project_id="p",
            task_id="1",
            mode="cli",
            cname="c",
        )

    def test_command_is_executed(self) -> None:
        """Verify a hook command is executed via sh -c with correct env."""
        with unittest.mock.patch("terok.lib.orchestration.hooks.subprocess.run") as mock_run:
            run_hook(
                "post_start",
                "echo hello",
                project_id="proj",
                task_id="1",
                mode="cli",
                cname="proj-cli-1",
            )

            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == ["sh", "-c", "echo hello"]
            env = args[1]["env"]
            assert env["TEROK_HOOK"] == "post_start"
            assert env["TEROK_PROJECT_ID"] == "proj"

    def test_post_stop_has_timeout(self) -> None:
        """Verify post_stop hooks have a 30s timeout."""
        with unittest.mock.patch("terok.lib.orchestration.hooks.subprocess.run") as mock_run:
            run_hook(
                "post_stop",
                "cleanup.sh",
                project_id="p",
                task_id="1",
                mode="cli",
                cname="c",
            )
            assert mock_run.call_args[1]["timeout"] == 30

    def test_pre_start_has_startup_timeout(self) -> None:
        """Verify pre_start hooks use the startup timeout (120s)."""
        with unittest.mock.patch("terok.lib.orchestration.hooks.subprocess.run") as mock_run:
            run_hook(
                "pre_start",
                "setup.sh",
                project_id="p",
                task_id="1",
                mode="cli",
                cname="c",
            )
            assert mock_run.call_args[1]["timeout"] == 120

    def test_post_start_has_startup_timeout(self) -> None:
        """Verify post_start hooks use the startup timeout (120s)."""
        with unittest.mock.patch("terok.lib.orchestration.hooks.subprocess.run") as mock_run:
            run_hook(
                "post_start",
                "setup.sh",
                project_id="p",
                task_id="1",
                mode="cli",
                cname="c",
            )
            assert mock_run.call_args[1]["timeout"] == 120

    def test_web_port_passed_to_env(self) -> None:
        """Verify web_port is forwarded to the hook environment."""
        with unittest.mock.patch("terok.lib.orchestration.hooks.subprocess.run") as mock_run:
            run_hook(
                "post_ready",
                "fwd.sh",
                project_id="p",
                task_id="1",
                mode="toad",
                cname="c",
                web_port=7861,
            )
            env = mock_run.call_args[1]["env"]
            assert env["TEROK_WEB_PORT"] == "7861"

    def test_failure_does_not_raise(self) -> None:
        """Verify hook failures are swallowed (logged, not raised)."""
        with unittest.mock.patch(
            "terok.lib.orchestration.hooks.subprocess.run",
            side_effect=OSError("boom"),
        ):
            run_hook(
                "post_start",
                "fail.sh",
                project_id="p",
                task_id="1",
                mode="cli",
                cname="c",
            )

    def test_timeout_does_not_raise(self) -> None:
        """Verify hook timeouts are swallowed (logged, not raised)."""
        with unittest.mock.patch(
            "terok.lib.orchestration.hooks.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30),
        ):
            run_hook(
                "post_stop",
                "slow.sh",
                project_id="p",
                task_id="1",
                mode="cli",
                cname="c",
            )

    def test_run_hook_with_meta_path_records(self, tmp_path: Path) -> None:
        """Verify run_hook records the hook name in metadata when meta_path is given."""
        import json

        meta_path = tmp_path / "1.json"
        meta_path.write_text(json.dumps({"task_id": "1", "mode": "cli"}, indent=2))

        with unittest.mock.patch("terok.lib.orchestration.hooks.subprocess.run"):
            run_hook(
                "post_start",
                "echo hi",
                project_id="p",
                task_id="1",
                mode="cli",
                cname="c",
                meta_path=meta_path,
            )

        meta = json.loads(meta_path.read_text() or "{}")
        assert "post_start" in meta["hooks_fired"]

    def test_run_hook_records_even_without_command(self, tmp_path: Path) -> None:
        """Verify run_hook records even when command is None (hook point reached)."""
        import json

        meta_path = tmp_path / "1.json"
        meta_path.write_text(json.dumps({"task_id": "1", "mode": "cli"}, indent=2))

        run_hook(
            "post_ready",
            None,
            project_id="p",
            task_id="1",
            mode="cli",
            cname="c",
            meta_path=meta_path,
        )

        meta = json.loads(meta_path.read_text() or "{}")
        assert "post_ready" in meta["hooks_fired"]
