# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for task-name sanitizing, generation, and persistence."""

from __future__ import annotations

import re
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import pytest

from terok.lib.containers.tasks import (
    TASK_NAME_MAX_LEN,
    _default_categories_for_project,
    _resolve_name_categories,
    generate_task_name,
    get_tasks,
    sanitize_task_name,
    task_new,
    task_rename,
    validate_task_name,
)
from terok.lib.util.yaml import load as yaml_load
from tests.test_utils import project_env

SLUG_PATTERN = r"^[a-z]+-[a-z0-9]+$"


def project_yaml(project_id: str, *, name_categories: list[str] | None = None) -> str:
    """Build a minimal project config with optional task name categories."""
    yaml_text = f"project:\n  id: {project_id}\n"
    if name_categories:
        yaml_text += "tasks:\n  name_categories:\n"
        yaml_text += "".join(f"    - {category}\n" for category in name_categories)
    return yaml_text


def task_meta_name(ctx, project_id: str, task_id: str = "1") -> str:
    """Return the persisted task name from task metadata."""
    meta_path = ctx.state_dir / "projects" / project_id / "tasks" / f"{task_id}.yml"
    return yaml_load(meta_path.read_text())["name"]


def create_task_and_get_name(project_id: str, *, explicit_name: str | None = None) -> str:
    """Create one task in a temporary project env and return its resulting task name."""
    with project_env(project_yaml(project_id), project_id=project_id):
        task_new(project_id, name=explicit_name)
        tasks = get_tasks(project_id)
        assert len(tasks) == 1
        return tasks[0].name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(None, None, id="none"),
        pytest.param("", None, id="empty"),
        pytest.param("   ", None, id="whitespace-only"),
        pytest.param("fix auth bug", "fix-auth-bug", id="spaces-to-hyphens"),
        pytest.param("fix_auth_bug", "fix_auth_bug", id="underscores-preserved"),
        pytest.param("fix@auth#bug!", "fixauthbug", id="special-chars-stripped"),
        pytest.param("Fix-Auth-Bug", "fix-auth-bug", id="lowercased"),
        pytest.param("fix---auth---bug", "fix-auth-bug", id="collapse-hyphens"),
        pytest.param("fix-bug-", "fix-bug", id="trailing-hyphen-stripped"),
        pytest.param("-fix-bug", "-fix-bug", id="leading-hyphen-preserved"),
        pytest.param("  Fix__Auth  Bug!! ", "fix__auth-bug", id="mixed-transform"),
        pytest.param("@#$%^&", None, id="only-special-chars"),
        pytest.param("42", "42", id="numeric"),
    ],
)
def test_sanitize_task_name(raw: str | None, expected: str | None) -> None:
    """Raw task names are normalized into slug-like identifiers."""
    assert sanitize_task_name(raw) == expected


def test_sanitize_task_name_truncates_to_max_length() -> None:
    """Very long task names are truncated to the configured maximum length."""
    assert len(sanitize_task_name("a" * 100)) == TASK_NAME_MAX_LEN


@pytest.mark.parametrize(
    ("name", "expected_error_fragment"),
    [
        pytest.param("fix-auth-bug", None, id="valid-slug"),
        pytest.param("fix_auth_bug", None, id="valid-underscore"),
        pytest.param("42", None, id="valid-numeric"),
        pytest.param("-fix-bug", "hyphen", id="leading-hyphen-rejected"),
    ],
)
def test_validate_task_name(name: str, expected_error_fragment: str | None) -> None:
    """Only leading-hyphen task names are rejected after sanitization."""
    error = validate_task_name(name)
    if expected_error_fragment is None:
        assert error is None
    else:
        assert error is not None
        assert expected_error_fragment in error


def test_generate_task_name_outputs_non_empty_slug() -> None:
    """Generated task names are non-empty slug strings."""
    name = generate_task_name()
    assert isinstance(name, str)
    assert re.search(SLUG_PATTERN, name)


@pytest.mark.parametrize(
    ("project_id", "explicit_name", "expected", "pattern"),
    [
        pytest.param("proj_name1", "Fix Auth Bug", "fix-auth-bug", None, id="explicit-sanitized"),
        pytest.param("proj_name2", None, None, SLUG_PATTERN, id="generated-default"),
    ],
)
def test_task_new_assigns_expected_name(
    project_id: str,
    explicit_name: str | None,
    expected: str | None,
    pattern: str | None,
) -> None:
    """New tasks use either the explicit sanitized name or an auto-generated slug."""
    name = create_task_and_get_name(project_id, explicit_name=explicit_name)
    if expected is not None:
        assert name == expected
    else:
        assert re.search(pattern or "", name)


@pytest.mark.parametrize("bad_name", ["@#$", "-my-task"], ids=["invalid", "leading-hyphen"])
def test_task_new_rejects_invalid_names(bad_name: str) -> None:
    """Invalid task names fail before writing task metadata."""
    project_id = "proj_name_invalid"
    with project_env(project_yaml(project_id), project_id=project_id) as ctx:
        with pytest.raises(SystemExit):
            task_new(project_id, name=bad_name)
        assert not (ctx.state_dir / "projects" / project_id / "tasks" / "1.yml").exists()


def test_task_new_prints_name() -> None:
    """Task creation output includes the resolved task name."""
    project_id = "proj_name3"
    with project_env(project_yaml(project_id), project_id=project_id):
        output = StringIO()
        with redirect_stdout(output):
            task_new(project_id, name="my-task")
    assert "my-task" in output.getvalue()


@pytest.mark.parametrize(
    ("initial_name", "new_name", "expected", "raises"),
    [
        pytest.param("old-name", "new-name", "new-name", False, id="rename"),
        pytest.param("old-name", "My New Name", "my-new-name", False, id="rename-sanitized"),
        pytest.param("old-name", "@#$%", None, True, id="rename-invalid"),
        pytest.param("old-name", "-badname", None, True, id="rename-leading-hyphen"),
    ],
)
def test_task_rename_updates_or_rejects(
    initial_name: str,
    new_name: str,
    expected: str | None,
    raises: bool,
) -> None:
    """Task renaming persists the sanitized value or rejects invalid names."""
    project_id = "proj_rename"
    with project_env(project_yaml(project_id), project_id=project_id) as ctx:
        task_new(project_id, name=initial_name)
        if raises:
            with pytest.raises(SystemExit):
                task_rename(project_id, "1", new_name)
        else:
            task_rename(project_id, "1", new_name)
            assert task_meta_name(ctx, project_id) == expected


def test_task_rename_unknown_task_raises() -> None:
    """Renaming an unknown task raises ``SystemExit``."""
    project_id = "proj_rename_unknown"
    with project_env(project_yaml(project_id), project_id=project_id):
        with pytest.raises(SystemExit):
            task_rename(project_id, "999", "new-name")


def test_get_tasks_loads_name() -> None:
    """Task loading populates ``TaskMeta.name`` from YAML."""
    project_id = "proj_load_name"
    assert create_task_and_get_name(project_id, explicit_name="test-task") == "test-task"


def test_default_categories_for_project_are_valid_and_deterministic() -> None:
    """Hash-based default categories are valid and stable per project ID."""
    import namer

    categories = _default_categories_for_project("stable-proj")
    assert len(categories) == 3
    assert set(categories) <= set(namer.list_categories())
    assert categories == _default_categories_for_project("stable-proj")
    assert categories != _default_categories_for_project("project-beta")


@pytest.mark.parametrize(
    ("project_categories", "global_categories", "expected"),
    [
        pytest.param(
            ["animals", "food"], ["music", "sports"], ["animals", "food"], id="project-override"
        ),
        pytest.param(None, ["music", "sports"], ["music", "sports"], id="global-fallback"),
        pytest.param(None, None, None, id="hash-fallback"),
    ],
)
def test_resolve_name_categories(
    project_categories: list[str] | None,
    global_categories: list[str] | None,
    expected: list[str] | None,
) -> None:
    """Project config wins, then global config, then the deterministic hash fallback."""
    project_id = "proj_categories"
    with (
        project_env(
            project_yaml(project_id, name_categories=project_categories), project_id=project_id
        ),
        patch("terok.lib.core.config.get_task_name_categories", return_value=global_categories),
    ):
        categories = _resolve_name_categories(project_id)

    assert categories == (expected or _default_categories_for_project(project_id))


@pytest.mark.parametrize("project_id", [None, "proj_gen_cat"], ids=["no-project", "project-aware"])
def test_generate_task_name_with_optional_project_id(project_id: str | None) -> None:
    """Task-name generation works with and without project-aware category resolution."""
    if project_id is None:
        name = generate_task_name()
    else:
        with project_env(
            project_yaml(project_id, name_categories=["animals"]), project_id=project_id
        ):
            name = generate_task_name(project_id)

    assert isinstance(name, str)
    assert re.search(SLUG_PATTERN, name)


def test_task_new_uses_project_categories() -> None:
    """Task creation passes the project ID through for category resolution."""
    project_id = "proj_new_cat"
    with project_env(project_yaml(project_id), project_id=project_id):
        task_new(project_id)
        tasks = get_tasks(project_id)

    assert len(tasks) == 1
    assert tasks[0].name is not None
    assert re.search(SLUG_PATTERN, tasks[0].name)
