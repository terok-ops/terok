# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for raw YAML Pydantic models (typo detection, type validation, coercion)."""

import unittest

from pydantic import ValidationError

from terok.lib.core.yaml_schema import (
    RawGlobalConfig,
    RawProjectSection,
    RawProjectYaml,
)
from tests.testfs import (
    MOCK_BASE,
    MOCK_GATE_PATH,
    MOCK_SSH_HOST_DIR,
    MOCK_STAGING_ROOT,
    MOCK_TASKS_ROOT,
)


class RawProjectYamlTests(unittest.TestCase):
    """Tests for the top-level project.yml model."""

    def test_minimal_valid_input(self) -> None:
        """Empty dict produces all defaults."""
        raw = RawProjectYaml.model_validate({})
        self.assertEqual(raw.project.security_class, "online")
        self.assertIsNone(raw.git.upstream_url)
        self.assertEqual(raw.docker.base_image, "ubuntu:24.04")
        self.assertEqual(raw.run.shutdown_timeout, 10)
        self.assertIsNone(raw.shield.drop_on_task_run)
        self.assertIsNone(raw.shield.on_task_restart)

    def test_full_valid_input(self) -> None:
        """A complete valid project.yml parses correctly."""
        data = {
            "project": {"id": "myproj", "security_class": "gatekeeping"},
            "git": {"upstream_url": "https://example.com/repo.git", "default_branch": "main"},
            "ssh": {"key_name": "id_ed25519_myproj", "host_dir": MOCK_SSH_HOST_DIR},
            "tasks": {"root": MOCK_TASKS_ROOT},
            "gate": {"path": MOCK_GATE_PATH},
            "gatekeeping": {
                "staging_root": MOCK_STAGING_ROOT,
                "upstream_polling": {"enabled": False, "interval_minutes": 10},
                "auto_sync": {"enabled": True, "branches": ["main", "dev"]},
            },
            "run": {"shutdown_timeout": 30, "gpus": "all"},
            "shield": {"drop_on_task_run": False, "on_task_restart": "up"},
            "docker": {"base_image": "nvidia/cuda:12.0", "user_snippet_inline": "RUN echo hi"},
            "default_agent": "claude",
            "agent": {"model": "opus"},
        }
        raw = RawProjectYaml.model_validate(data)
        self.assertEqual(raw.project.id, "myproj")
        self.assertEqual(raw.project.security_class, "gatekeeping")
        self.assertEqual(raw.git.upstream_url, "https://example.com/repo.git")
        self.assertEqual(raw.ssh.key_name, "id_ed25519_myproj")
        self.assertFalse(raw.gatekeeping.upstream_polling.enabled)
        self.assertEqual(raw.gatekeeping.upstream_polling.interval_minutes, 10)
        self.assertTrue(raw.gatekeeping.auto_sync.enabled)
        self.assertEqual(raw.gatekeeping.auto_sync.branches, ["main", "dev"])
        self.assertEqual(raw.run.shutdown_timeout, 30)
        self.assertEqual(raw.run.gpus, "all")
        self.assertFalse(raw.shield.drop_on_task_run)
        self.assertEqual(raw.shield.on_task_restart, "up")
        self.assertEqual(raw.docker.base_image, "nvidia/cuda:12.0")
        self.assertEqual(raw.default_agent, "claude")

    def test_unknown_key_rejected(self) -> None:
        """Unknown top-level key raises ValidationError (typo detection)."""
        with self.assertRaises(ValidationError) as ctx:
            RawProjectYaml.model_validate({"projecct": {"id": "oops"}})
        self.assertIn("projecct", str(ctx.exception))

    def test_unknown_nested_key_rejected(self) -> None:
        """Unknown key in a nested section raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            RawProjectYaml.model_validate({"ssh": {"host_key_dir": str(MOCK_BASE)}})
        self.assertIn("host_key_dir", str(ctx.exception))

    def test_none_section_coerced_to_empty(self) -> None:
        """Top-level ``None`` section values are coerced to ``{}``."""
        raw = RawProjectYaml.model_validate({"project": None, "git": None, "ssh": None})
        self.assertEqual(raw.project.security_class, "online")
        self.assertIsNone(raw.git.upstream_url)
        self.assertIsNone(raw.ssh.key_name)

    def test_none_subsection_coerced_to_empty(self) -> None:
        """Nested None sub-sections (e.g. upstream_polling: null) get defaults."""
        raw = RawProjectYaml.model_validate(
            {"gatekeeping": {"upstream_polling": None, "auto_sync": None}}
        )
        self.assertTrue(raw.gatekeeping.upstream_polling.enabled)
        self.assertFalse(raw.gatekeeping.auto_sync.enabled)


class SecurityClassValidationTests(unittest.TestCase):
    """Tests for the security_class field validator."""

    def test_valid_online(self) -> None:
        """'online' is accepted."""
        s = RawProjectSection.model_validate({"security_class": "online"})
        self.assertEqual(s.security_class, "online")

    def test_valid_gatekeeping(self) -> None:
        """'gatekeeping' is accepted."""
        s = RawProjectSection.model_validate({"security_class": "gatekeeping"})
        self.assertEqual(s.security_class, "gatekeeping")

    def test_case_insensitive(self) -> None:
        """Security class is normalized to lowercase."""
        s = RawProjectSection.model_validate({"security_class": "  Online  "})
        self.assertEqual(s.security_class, "online")

    def test_invalid_value(self) -> None:
        """Invalid security class raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            RawProjectSection.model_validate({"security_class": "hybrid"})
        self.assertIn("hybrid", str(ctx.exception))


class ProjectIdValidationTests(unittest.TestCase):
    """Tests for the project.id field validator."""

    def test_valid_id(self) -> None:
        """Valid lowercase ID is accepted."""
        s = RawProjectSection.model_validate({"id": "my-project_1"})
        self.assertEqual(s.id, "my-project_1")

    def test_none_id(self) -> None:
        """None ID is accepted (defaults to directory name)."""
        s = RawProjectSection.model_validate({"id": None})
        self.assertIsNone(s.id)

    def test_uppercase_rejected(self) -> None:
        """Uppercase IDs are rejected."""
        with self.assertRaises(ValidationError):
            RawProjectSection.model_validate({"id": "MyProject"})

    def test_path_separator_rejected(self) -> None:
        """Path separators in IDs are rejected."""
        with self.assertRaises(ValidationError):
            RawProjectSection.model_validate({"id": "../escape"})

    def test_absolute_path_rejected(self) -> None:
        """Absolute paths as IDs are rejected."""
        with self.assertRaises(ValidationError):
            RawProjectSection.model_validate({"id": "/etc/passwd"})


class NameCategoriesTests(unittest.TestCase):
    """Tests for the NameCategories annotated type (coercion logic)."""

    def test_list_passthrough(self) -> None:
        """A list of strings passes through unchanged."""
        raw = RawProjectYaml.model_validate({"tasks": {"name_categories": ["animals", "food"]}})
        self.assertEqual(raw.tasks.name_categories, ["animals", "food"])

    def test_string_to_list(self) -> None:
        """A single string is coerced to a one-element list."""
        raw = RawProjectYaml.model_validate({"tasks": {"name_categories": "animals"}})
        self.assertEqual(raw.tasks.name_categories, ["animals"])

    def test_none_stays_none(self) -> None:
        """None stays None."""
        raw = RawProjectYaml.model_validate({"tasks": {"name_categories": None}})
        self.assertIsNone(raw.tasks.name_categories)

    def test_empty_string_becomes_none(self) -> None:
        """Empty/whitespace string becomes None."""
        raw = RawProjectYaml.model_validate({"tasks": {"name_categories": "  "}})
        self.assertIsNone(raw.tasks.name_categories)

    def test_empty_list_becomes_none(self) -> None:
        """Empty list becomes None."""
        raw = RawProjectYaml.model_validate({"tasks": {"name_categories": []}})
        self.assertIsNone(raw.tasks.name_categories)

    def test_integer_rejected(self) -> None:
        """Non-string, non-list values are rejected."""
        with self.assertRaises(ValidationError):
            RawProjectYaml.model_validate({"tasks": {"name_categories": 42}})

    def test_list_with_non_strings_rejected(self) -> None:
        """List containing non-strings is rejected."""
        with self.assertRaises(ValidationError):
            RawProjectYaml.model_validate({"tasks": {"name_categories": [1, 2]}})


class RawGlobalConfigTests(unittest.TestCase):
    """Tests for the global config.yml model."""

    def test_empty_config(self) -> None:
        """Empty dict produces all defaults."""
        cfg = RawGlobalConfig.model_validate({})
        self.assertEqual(cfg.ui.base_port, 7860)
        self.assertFalse(cfg.tui.default_tmux)
        self.assertTrue(cfg.logs.partial_streaming)
        self.assertFalse(cfg.shield.bypass_firewall_no_protection)
        self.assertTrue(cfg.shield.drop_on_task_run)
        self.assertEqual(cfg.shield.on_task_restart, "retain")
        self.assertEqual(cfg.gate_server.port, 9418)
        self.assertIsNone(cfg.default_agent)

    def test_custom_values(self) -> None:
        """Custom values are parsed correctly."""
        cfg = RawGlobalConfig.model_validate(
            {
                "ui": {"base_port": 9000},
                "tui": {"default_tmux": True},
                "logs": {"partial_streaming": False},
                "shield": {
                    "bypass_firewall_no_protection": True,
                    "drop_on_task_run": False,
                    "on_task_restart": "up",
                },
                "gate_server": {"port": 1234, "suppress_systemd_warning": True},
                "default_agent": "codex",
            }
        )
        self.assertEqual(cfg.ui.base_port, 9000)
        self.assertTrue(cfg.tui.default_tmux)
        self.assertFalse(cfg.logs.partial_streaming)
        self.assertTrue(cfg.shield.bypass_firewall_no_protection)
        self.assertFalse(cfg.shield.drop_on_task_run)
        self.assertEqual(cfg.shield.on_task_restart, "up")
        self.assertEqual(cfg.gate_server.port, 1234)
        self.assertTrue(cfg.gate_server.suppress_systemd_warning)
        self.assertEqual(cfg.default_agent, "codex")

    def test_unknown_key_rejected(self) -> None:
        """Unknown top-level key raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            RawGlobalConfig.model_validate({"uii": {"base_port": 7860}})
        self.assertIn("uii", str(ctx.exception))

    def test_unknown_nested_key_rejected(self) -> None:
        """Unknown key in a nested section raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            RawGlobalConfig.model_validate({"ui": {"basse_port": 7860}})
        self.assertIn("basse_port", str(ctx.exception))

    def test_none_sections_coerced(self) -> None:
        """None top-level sections are coerced to defaults."""
        cfg = RawGlobalConfig.model_validate({"ui": None, "tui": None, "logs": None})
        self.assertEqual(cfg.ui.base_port, 7860)
        self.assertFalse(cfg.tui.default_tmux)
        self.assertTrue(cfg.logs.partial_streaming)

    def test_git_section(self) -> None:
        """Global git section parses correctly."""
        cfg = RawGlobalConfig.model_validate(
            {"git": {"human_name": "Test User", "human_email": "test@example.com"}}
        )
        self.assertEqual(cfg.git.human_name, "Test User")
        self.assertEqual(cfg.git.human_email, "test@example.com")

    def test_global_name_categories(self) -> None:
        """Global tasks.name_categories coercion works."""
        cfg = RawGlobalConfig.model_validate({"tasks": {"name_categories": "animals"}})
        self.assertEqual(cfg.tasks.name_categories, ["animals"])

    def test_global_git_rejects_project_only_keys(self) -> None:
        """Global git section rejects project-only keys like upstream_url."""
        with self.assertRaises(ValidationError) as ctx:
            RawGlobalConfig.model_validate(
                {"git": {"upstream_url": "https://example.com/repo.git"}}
            )
        self.assertIn("upstream_url", str(ctx.exception))

    def test_shield_invalid_restart_policy_rejected(self) -> None:
        """Invalid on_task_restart value raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            RawGlobalConfig.model_validate({"shield": {"on_task_restart": "invalid"}})
        self.assertIn("on_task_restart", str(ctx.exception))

    def test_global_hooks_section(self) -> None:
        """Global hooks section parses all four hook fields."""
        cfg = RawGlobalConfig.model_validate(
            {"hooks": {"post_ready": "notify.sh", "post_stop": "cleanup.sh"}}
        )
        self.assertIsNone(cfg.hooks.pre_start)
        self.assertIsNone(cfg.hooks.post_start)
        self.assertEqual(cfg.hooks.post_ready, "notify.sh")
        self.assertEqual(cfg.hooks.post_stop, "cleanup.sh")

    def test_global_hooks_rejects_unknown_keys(self) -> None:
        """Global hooks section rejects unknown hook names."""
        with self.assertRaises(ValidationError):
            RawGlobalConfig.model_validate({"hooks": {"on_crash": "oops.sh"}})

    def test_project_run_hooks(self) -> None:
        """Project run.hooks section parses correctly."""
        raw = RawProjectYaml.model_validate(
            {"run": {"hooks": {"pre_start": "setup.sh", "post_ready": "fwd.sh"}}}
        )
        self.assertEqual(raw.run.hooks.pre_start, "setup.sh")
        self.assertEqual(raw.run.hooks.post_ready, "fwd.sh")
        self.assertIsNone(raw.run.hooks.post_stop)

    def test_project_run_hooks_none_coercion(self) -> None:
        """Project run.hooks: None is coerced to empty (default hooks)."""
        raw = RawProjectYaml.model_validate({"run": {"hooks": None}})
        self.assertIsNone(raw.run.hooks.pre_start)


class ProjectYamlValidationErrorTests(unittest.TestCase):
    """Tests for user-facing error messages from load_project() with bad YAML."""

    def test_wrong_type_for_section(self) -> None:
        """Passing a string where a section dict is expected raises ValidationError."""
        with self.assertRaises(ValidationError):
            RawProjectYaml.model_validate({"project": "not a dict"})

    def test_wrong_type_for_field(self) -> None:
        """Wrong type for a scalar field raises ValidationError."""
        with self.assertRaises(ValidationError):
            RawProjectYaml.model_validate({"run": {"shutdown_timeout": "not a number"}})

    def test_project_shield_invalid_restart_policy_rejected(self) -> None:
        """Invalid on_task_restart in project shield section raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            RawProjectYaml.model_validate({"shield": {"on_task_restart": "invalid"}})
        self.assertIn("on_task_restart", str(ctx.exception))

    def test_docker_unknown_key(self) -> None:
        """Typo in docker section key is caught."""
        with self.assertRaises(ValidationError) as ctx:
            RawProjectYaml.model_validate({"docker": {"base_imagee": "ubuntu:24.04"}})
        self.assertIn("base_imagee", str(ctx.exception))


class SharedDirFieldTests(unittest.TestCase):
    """Tests for the ``shared_dir`` top-level field in project.yml."""

    def test_omitted_defaults_to_none(self) -> None:
        """shared_dir is None by default (disabled)."""
        raw = RawProjectYaml.model_validate({})
        self.assertIsNone(raw.shared_dir)

    def test_true_accepted(self) -> None:
        """``shared_dir: true`` enables auto-created shared directory."""
        raw = RawProjectYaml.model_validate({"shared_dir": True})
        self.assertTrue(raw.shared_dir)

    def test_false_accepted(self) -> None:
        """``shared_dir: false`` disables shared directory."""
        raw = RawProjectYaml.model_validate({"shared_dir": False})
        self.assertFalse(raw.shared_dir)

    def test_path_string_accepted(self) -> None:
        """``shared_dir: /path`` passes through as string."""
        raw = RawProjectYaml.model_validate({"shared_dir": "/tmp/terok-testing/shared"})
        self.assertEqual(raw.shared_dir, "/tmp/terok-testing/shared")

    def test_null_stays_none(self) -> None:
        """``shared_dir: ~`` (YAML null) stays None."""
        raw = RawProjectYaml.model_validate({"shared_dir": None})
        self.assertIsNone(raw.shared_dir)
