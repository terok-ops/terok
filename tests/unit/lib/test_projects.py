# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for project loading and listing helpers."""

from __future__ import annotations

import os
import tempfile
import unittest.mock
from pathlib import Path

import pytest

from terok.lib.core.config import build_dir, make_sandbox_config, sandbox_live_dir
from terok.lib.core.projects import BrokenProject, discover_projects, list_projects, load_project
from terok.lib.domain.project_state import get_project_state
from tests.test_utils import project_env, write_project


def project_yaml(
    project_id: str,
    *,
    security_class: str | None = None,
    authorship: str | None = None,
    shield_drop_on_task_run: bool | None = None,
    shield_on_task_restart: str | None = None,
    timezone: str | None = None,
    ssh_use_personal: bool | None = None,
) -> str:
    """Build project YAML for tests with optional sections."""
    lines = ["project:", f"  id: {project_id}"]
    if security_class is not None:
        lines.append(f"  security_class: {security_class}")
    lines += ["git:", "  upstream_url: https://example.com/repo.git"]
    if authorship is not None:
        lines.append(f"  authorship: {authorship}")
    shield_lines: list[str] = []
    if shield_drop_on_task_run is not None:
        shield_lines.append(f"  drop_on_task_run: {str(shield_drop_on_task_run).lower()}")
    if shield_on_task_restart is not None:
        shield_lines.append(f"  on_task_restart: {shield_on_task_restart}")
    if shield_lines:
        lines += ["shield:", *shield_lines]
    if timezone is not None:
        lines += ["run:", f"  timezone: {timezone}"]
    if ssh_use_personal is not None:
        lines += ["ssh:", f"  use_personal: {str(ssh_use_personal).lower()}"]
    return "\n".join(lines) + "\n"


class TestProject:
    """Tests for project loading/listing."""

    def test_load_project_gatekeeping_defaults(self) -> None:
        project_id = "proj1"
        with project_env(
            project_yaml(project_id, security_class="gatekeeping"),
            project_id=project_id,
        ):
            project = load_project(project_id)
            assert project.id == project_id
            assert project.security_class == "gatekeeping"
            assert project.tasks_root == (sandbox_live_dir() / "tasks" / project_id).resolve()
            assert (
                project.gate_path
                == (make_sandbox_config().gate_base_path / f"{project_id}.git").resolve()
            )
            assert project.staging_root == (build_dir() / project_id).resolve()
            assert project.git_authorship == "agent-human"

    @pytest.mark.parametrize(
        ("project_id", "yaml_text", "config_text", "expected"),
        [
            (
                "proj-authorship",
                project_yaml("proj-authorship", authorship="human-agent"),
                None,
                "human-agent",
            ),
            (
                "proj-global-authorship",
                project_yaml("proj-global-authorship"),
                "git:\n  authorship: human\n",
                "human",
            ),
        ],
        ids=["project-authorship", "global-authorship"],
    )
    def test_git_authorship_resolution(
        self,
        project_id: str,
        yaml_text: str,
        config_text: str | None,
        expected: str,
    ) -> None:
        with project_env(yaml_text, project_id=project_id) as ctx:
            if config_text is None:
                project = load_project(project_id)
            else:
                config_file = ctx.base / "config.yml"
                config_file.write_text(config_text, encoding="utf-8")
                with unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(config_file)}):
                    project = load_project(project_id)
        assert project.git_authorship == expected

    @pytest.mark.parametrize(
        ("project_id", "yaml_text", "config_text", "expected"),
        [
            pytest.param(
                "ssh-default", project_yaml("ssh-default"), None, False, id="default-false"
            ),
            pytest.param(
                "ssh-project-on",
                project_yaml("ssh-project-on", ssh_use_personal=True),
                None,
                True,
                id="project-yaml-on",
            ),
            pytest.param(
                "ssh-global-on",
                project_yaml("ssh-global-on"),
                "ssh:\n  use_personal: true\n",
                True,
                id="global-config-on",
            ),
            pytest.param(
                "ssh-project-overrides-global",
                project_yaml("ssh-project-overrides-global", ssh_use_personal=False),
                "ssh:\n  use_personal: true\n",
                False,
                id="project-overrides-global",
            ),
        ],
    )
    def test_ssh_use_personal_resolution(
        self,
        project_id: str,
        yaml_text: str,
        config_text: str | None,
        expected: bool,
    ) -> None:
        """``ssh.use_personal`` resolves through the layered config tiers.

        Order (lowest → highest, applied at load time):
        global ``config.yml`` ssh section → ``project.yml`` ssh section.
        The CLI override ``--use-personal-ssh`` sits one tier above
        this and is applied in :func:`make_git_gate`, not here.

        Sandbox owns both the schema (``RawSSHSection``) and the
        global-tier reader (``gate_use_personal_ssh_default``); terok
        composes the project layer via ``_build_project_config``.
        """
        with project_env(yaml_text, project_id=project_id) as ctx:
            if config_text is None:
                project = load_project(project_id)
            else:
                config_file = ctx.base / "config.yml"
                config_file.write_text(config_text, encoding="utf-8")
                with unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(config_file)}):
                    project = load_project(project_id)
        assert project.ssh_use_personal is expected

    def test_load_project_invalid_git_authorship_raises(self) -> None:
        with project_env(
            project_yaml("proj-bad-authorship", authorship="mystery-mode"),
            project_id="proj-bad-authorship",
        ):
            with pytest.raises(SystemExit, match="git.authorship"):
                load_project("proj-bad-authorship")

    def test_list_projects_prefers_user(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            system_config = base / "system"
            system_projects = system_config / "projects"
            user_projects = base / "user" / "terok" / "projects"
            system_projects.mkdir(parents=True, exist_ok=True)
            user_projects.mkdir(parents=True, exist_ok=True)

            write_project(
                system_projects,
                "proj2",
                project_yaml("proj2").replace("example.com", "system.example"),
            )
            write_project(
                user_projects, "proj2", project_yaml("proj2").replace("example.com", "user.example")
            )

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(system_config),
                    "XDG_CONFIG_HOME": str(base / "user"),
                },
            ):
                projects = list_projects()
        assert len(projects) == 1
        assert projects[0].upstream_url == "https://user.example/repo.git"
        assert projects[0].root == (user_projects / "proj2").resolve()

    def test_gatekeeping_with_gate_disabled_rejected_at_load(self) -> None:
        """gatekeeping *is* the gate-enforced mode; disabling the gate is incoherent."""
        yaml = (
            "project:\n"
            "  id: bad\n"
            "  security_class: gatekeeping\n"
            "git:\n"
            "  upstream_url: https://example.com/r.git\n"
            "gate:\n"
            "  enabled: false\n"
        )
        with project_env(yaml, project_id="bad"):
            with pytest.raises(SystemExit, match="gatekeeping"):
                load_project("bad")

    def test_gate_enabled_defaults_true(self) -> None:
        """Projects without an explicit gate section keep the old default (enabled)."""
        yaml = "project:\n  id: p\ngit:\n  upstream_url: https://example.com/r.git\n"
        with project_env(yaml, project_id="p"):
            project = load_project("p")
        assert project.gate_enabled is True

    def test_gate_disabled_loads_in_online_mode(self) -> None:
        """online + gate.enabled: false is the supported disabled-gate shape."""
        yaml = (
            "project:\n"
            "  id: hostless\n"
            "git:\n"
            "  upstream_url: git@github.com:user/repo.git\n"
            "gate:\n"
            "  enabled: false\n"
        )
        with project_env(yaml, project_id="hostless"):
            project = load_project("hostless")
        assert project.gate_enabled is False
        assert project.upstream_url == "git@github.com:user/repo.git"

    def test_discover_projects_splits_valid_and_broken(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """discover_projects returns (valid, broken) without touching stderr (#565)."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_base = base / "config"
            projects_root = config_base / "projects"
            write_project(
                projects_root,
                "good",
                "project:\n  id: good\ngit:\n  upstream_url: https://example.com/good.git\n",
            )
            write_project(projects_root, "bad", "project:\n  id: bad\n  foo: [invalid\n")
            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_base), "XDG_CONFIG_HOME": str(base / "empty")},
            ):
                valid, broken = discover_projects()

        assert [p.id for p in valid] == ["good"]
        assert [bp.id for bp in broken] == ["bad"]
        bp = broken[0]
        assert isinstance(bp, BrokenProject)
        assert bp.config_path == (projects_root / "bad" / "project.yml")
        assert bp.error  # message is populated and non-empty

        # discover_projects is the TUI-facing entrypoint; ``list_projects``
        # owns the CLI-side stderr warning.  If this ever starts printing
        # directly, the TUI would get duplicate noise on top of its toast.
        captured = capsys.readouterr()
        assert captured.err.strip() == ""

    def test_list_projects_skips_malformed_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_base = base / "config"
            projects_root = config_base / "projects"
            write_project(
                projects_root,
                "good",
                "project:\n  id: good\ngit:\n  upstream_url: https://example.com/good.git\n",
            )
            write_project(projects_root, "bad", "project:\n  id: bad\n  foo: [invalid\n")
            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_base), "XDG_CONFIG_HOME": str(base / "empty")},
            ):
                projects = list_projects()
        assert len(projects) == 1
        assert projects[0].id == "good"

    def test_list_projects_sanitizes_control_chars_in_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Error messages stripped of ANSI/control bytes to prevent TTY-escape spoofing."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_base = base / "config"
            projects_root = config_base / "projects"
            # A healthy project proves the broken one was skipped (not
            # that list_projects crashed outright).
            write_project(
                projects_root,
                "good",
                "project:\n  id: good\ngit:\n  upstream_url: https://example.com/good.git\n",
            )
            # YAML with a string value containing ANSI escape sequences and
            # a null byte — the parser may surface these unchanged in its
            # error message if the config is otherwise malformed.
            write_project(
                projects_root,
                "evil",
                'project:\n  id: evil\n  foo: "\x1b[31mPWNED\x1b[0m\x00" broken\n',
            )
            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_base), "XDG_CONFIG_HOME": str(base / "empty")},
            ):
                result = list_projects()
        # Skip-and-continue: 'evil' is dropped, 'good' survives.
        ids = {p.id for p in result}
        assert ids == {"good"}
        err = capsys.readouterr().err
        assert "warning: skipping broken project 'evil'" in err
        assert "\x1b" not in err
        assert "\x00" not in err

    def test_load_project_malformed_yaml(self) -> None:
        malformed = "project:\n  id: bad-yaml\n  foo: [invalid yaml\n"
        with project_env(malformed, project_id="bad-yaml"):
            with pytest.raises(SystemExit, match="Failed to read"):
                load_project("bad-yaml")

    def test_load_project_catches_non_yaml_exceptions(self) -> None:
        """Parser internal crashes (e.g. ruamel.yaml ``IndexError``) become SystemExit.

        Users have tripped ruamel.yaml's scanner into ``IndexError`` with
        inputs that *look* syntactically valid but hit a reader edge
        case.  Without a broad catch, the exception would bubble all the
        way up to the Textual keypress handler and take down the TUI —
        instead the file becomes a "broken project" visible in the list.

        The assertion checks the *full* shape of the surfaced message
        — the "Failed to read" prefix **and** the original exception's
        type and details — so future refactors can't silently drop the
        parser diagnostics on the floor.
        """
        import terok.lib.core.projects as projects_mod

        def _raise_index_error(text: str) -> object:
            raise IndexError("string index out of range")

        with project_env(project_yaml("weird"), project_id="weird"):
            with unittest.mock.patch.object(
                projects_mod, "_yaml_load", side_effect=_raise_index_error
            ):
                with pytest.raises(SystemExit) as exc_info:
                    load_project("weird")
        message = str(exc_info.value)
        assert "Failed to read" in message
        assert "IndexError" in message
        assert "string index out of range" in message
        # Chained cause is preserved so `__cause__` gives debuggers the
        # full traceback of the underlying scanner crash.
        assert isinstance(exc_info.value.__cause__, IndexError)

    @pytest.mark.parametrize(
        ("project_id", "yaml_text", "expected"),
        [
            ("proj-shield-default", project_yaml("proj-shield-default"), True),
            (
                "proj-shield-drop",
                project_yaml("proj-shield-drop", shield_drop_on_task_run=True),
                True,
            ),
            (
                "proj-shield-no-drop",
                project_yaml("proj-shield-no-drop", shield_drop_on_task_run=False),
                False,
            ),
        ],
        ids=["default", "enabled", "disabled"],
    )
    def test_shield_drop_on_task_run(
        self,
        project_id: str,
        yaml_text: str,
        expected: bool,
    ) -> None:
        """Project-level drop_on_task_run overrides global default."""
        with project_env(yaml_text, project_id=project_id):
            assert load_project(project_id).shield_drop_on_task_run is expected

    @pytest.mark.parametrize(
        ("project_id", "yaml_text", "expected"),
        [
            ("proj-restart-default", project_yaml("proj-restart-default"), "retain"),
            (
                "proj-restart-up",
                project_yaml("proj-restart-up", shield_on_task_restart="up"),
                "up",
            ),
        ],
        ids=["default-retain", "explicit-up"],
    )
    def test_shield_on_task_restart(
        self,
        project_id: str,
        yaml_text: str,
        expected: str,
    ) -> None:
        """Project-level on_task_restart overrides global default."""
        with project_env(yaml_text, project_id=project_id):
            assert load_project(project_id).shield_on_task_restart == expected

    def test_shared_dir_true_resolves_to_tasks_root(self) -> None:
        """``shared_dir: true`` resolves to tasks_root/_shared."""
        yaml_text = project_yaml("proj-shared") + "shared_dir: true\n"
        with project_env(yaml_text, project_id="proj-shared"):
            project = load_project("proj-shared")
        assert project.shared_dir is not None
        assert project.shared_dir.name == "_shared"
        assert project.shared_dir.parent == project.tasks_root

    def test_shared_dir_path_resolves_absolute(self) -> None:
        """``shared_dir: /path`` resolves to an absolute Path."""
        yaml_text = project_yaml("proj-shared-path") + "shared_dir: /tmp/terok-testing/custom\n"
        with project_env(yaml_text, project_id="proj-shared-path"):
            project = load_project("proj-shared-path")
        assert project.shared_dir == Path("/tmp/terok-testing/custom")

    def test_shared_dir_relative_path_rejected(self) -> None:
        """Relative path in shared_dir raises SystemExit."""
        yaml_text = project_yaml("proj-shared-rel") + "shared_dir: relative/path\n"
        with project_env(yaml_text, project_id="proj-shared-rel"):
            with pytest.raises(SystemExit, match="absolute path"):
                load_project("proj-shared-rel")

    def test_shared_dir_omitted_is_none(self) -> None:
        """Omitting ``shared_dir`` leaves it None (disabled)."""
        with project_env(project_yaml("proj-no-shared"), project_id="proj-no-shared"):
            project = load_project("proj-no-shared")
        assert project.shared_dir is None

    def test_timezone_from_run_section(self) -> None:
        """``run.timezone`` in project.yml surfaces on ``ProjectConfig.timezone``."""
        with project_env(
            project_yaml("proj-tz", timezone="Europe/Prague"),
            project_id="proj-tz",
        ):
            assert load_project("proj-tz").timezone == "Europe/Prague"

    def test_timezone_omitted_is_none(self) -> None:
        """Without ``run.timezone`` the field is ``None`` — terok-executor will follow the host."""
        with project_env(project_yaml("proj-no-tz"), project_id="proj-no-tz"):
            assert load_project("proj-no-tz").timezone is None

    def test_get_project_state(self, mock_runtime) -> None:
        project_id = "proj3"
        with project_env(
            project_yaml(project_id), project_id=project_id, with_config_file=True
        ) as env:
            stage_dir = build_dir() / project_id
            stage_dir.mkdir(parents=True, exist_ok=True)
            for name in ("L0.Dockerfile", "L1.cli.Dockerfile", "L1.ui.Dockerfile", "L2.Dockerfile"):
                (stage_dir / name).write_text("", encoding="utf-8")

            gate_dir = make_sandbox_config().gate_base_path / f"{project_id}.git"
            gate_dir.mkdir(parents=True, exist_ok=True)

            mock_runtime.image.return_value.exists.return_value = True
            # SSH "ready" check now hits the vault DB — stub the probe directly.
            with (
                unittest.mock.patch(
                    "terok.lib.core.projects._get_global_git_config", return_value=None
                ),
                unittest.mock.patch(
                    "terok.lib.domain.project_state._scope_has_vault_key",
                    return_value=True,
                ),
            ):
                state = get_project_state(project_id, gate_commit_provider=lambda _pid: None)
            _ = env  # silence unused; tmp-env drives config resolution

        assert state == {
            "dockerfiles": True,
            "dockerfiles_old": True,
            "images": True,
            "images_old": True,
            "stale_layers": ["l0", "l1", "l2"],
            "ssh": True,
            "gate": True,
            "gate_last_commit": None,
        }


class TestShareSshKeyAssignments:
    """Source's vault key assignments are shared with the derived scope."""

    @staticmethod
    def _patch_vault_db(db):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            yield db

        return unittest.mock.patch("terok.lib.domain.facade.vault_db", _cm)

    def test_delegates_to_db_assign(self) -> None:
        """Every assignment on the source scope becomes an assignment on the new scope."""
        from terok.lib.domain.facade import _share_ssh_key_assignments

        row_a = unittest.mock.MagicMock(id=11)
        row_b = unittest.mock.MagicMock(id=22)
        db = unittest.mock.MagicMock()
        db.list_ssh_keys_for_scope.return_value = [row_a, row_b]
        with self._patch_vault_db(db):
            _share_ssh_key_assignments("alpha", "beta")
        db.list_ssh_keys_for_scope.assert_called_once_with("alpha")
        assert [c.args for c in db.assign_ssh_key.call_args_list] == [
            ("beta", 11),
            ("beta", 22),
        ]

    def test_missing_source_entry_is_noop(self) -> None:
        """No assignments on source — derived project is left unregistered."""
        from terok.lib.domain.facade import _share_ssh_key_assignments

        db = unittest.mock.MagicMock()
        db.list_ssh_keys_for_scope.return_value = []
        with self._patch_vault_db(db):
            _share_ssh_key_assignments("alpha", "beta")
        db.assign_ssh_key.assert_not_called()
