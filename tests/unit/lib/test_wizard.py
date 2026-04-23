# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the interactive new-project wizard."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from terok.lib.domain.wizards.new_project import (
    BASES,
    QUESTIONS,
    SECURITY_CLASSES,
    Question,
    _slugify_project_id,
    _validate_project_id,
    collect_wizard_inputs,
    generate_config,
    render_project_yaml,
    run_wizard,
    validate_answer,
    write_project_yaml,
)
from tests.testfs import mock_wizard_project_file


def wizard_values(
    *,
    security_class: str = "online",
    base: str = "fedora",
    project_id: str = "test-proj",
    upstream_url: str = "https://github.com/user/repo.git",
    default_branch: str = "main",
    user_snippet: str = "",
) -> dict[str, object]:
    """Build a wizard value dict with sensible defaults."""
    return {
        "security_class": security_class,
        "base": base,
        "project_id": project_id,
        "upstream_url": upstream_url,
        "default_branch": default_branch,
        "user_snippet": user_snippet,
    }


@pytest.mark.parametrize(
    ("project_id", "valid"),
    [
        pytest.param("myproject", True, id="simple"),
        pytest.param("my-project", True, id="hyphen"),
        pytest.param("my_project", True, id="underscore"),
        pytest.param("proj123", True, id="digits"),
        pytest.param("my-project_2", True, id="mixed-lowercase"),
        pytest.param("My-Project_2", False, id="uppercase"),
        pytest.param("", False, id="empty"),
        pytest.param("my project", False, id="spaces"),
        pytest.param("my@project", False, id="special-chars"),
        pytest.param("-myproject", False, id="starts-with-hyphen"),
        pytest.param("_myproject", False, id="starts-with-underscore"),
    ],
)
def test_validate_project_id(project_id: str, valid: bool) -> None:
    """Project ID validation accepts only the supported slug-like IDs."""
    error = _validate_project_id(project_id)
    assert (error is None) is valid


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        pytest.param(
            ["1", "1", "myproj", "https://example.com/r.git", "main", "n"],
            wizard_values(project_id="myproj", upstream_url="https://example.com/r.git"),
            id="collect-all-values",
        ),
        pytest.param(
            ["2", "1", "gkproj", "git@host:r.git", "", "n"],
            wizard_values(
                security_class="gatekeeping",
                project_id="gkproj",
                upstream_url="git@host:r.git",
                default_branch="",
            ),
            id="gatekeeping-selection",
        ),
        pytest.param(
            ["1", "2", "proj", "https://example.com/r.git", "", "n"],
            wizard_values(
                base="nvidia",
                project_id="proj",
                upstream_url="https://example.com/r.git",
                default_branch="",
            ),
            id="empty-default-branch",
        ),
        pytest.param(
            ["1", "2", "proj", "https://example.com/r.git", "dev", "n"],
            wizard_values(
                base="nvidia",
                project_id="proj",
                upstream_url="https://example.com/r.git",
                default_branch="dev",
            ),
            id="custom-branch",
        ),
        pytest.param(
            ["1", "1", "!!!", "good-id", "https://example.com/r.git", "main", "n"],
            wizard_values(project_id="good-id", upstream_url="https://example.com/r.git"),
            id="retry-invalid-project-id",
        ),
        pytest.param(
            ["1", "1", "proj", "", "main", "n"],
            wizard_values(project_id="proj", upstream_url=""),
            id="empty-upstream-url-accepted",
        ),
    ],
)
def test_collect_wizard_inputs_success(
    inputs: list[str],
    expected: dict[str, object],
) -> None:
    """Wizard input collection retries invalid inputs and returns normalized values."""
    with patch("builtins.input", side_effect=inputs):
        assert collect_wizard_inputs() == expected


@pytest.mark.parametrize(
    "side_effect",
    [
        pytest.param(["invalid"], id="invalid-mode"),
        pytest.param(["0"], id="mode-below-range"),
        pytest.param(["9"], id="mode-above-range"),
        pytest.param(["1", "invalid"], id="invalid-base"),
        pytest.param(["1", "9"], id="base-above-range"),
        pytest.param(KeyboardInterrupt, id="ctrl-c"),
        pytest.param(EOFError, id="eof"),
    ],
)
def test_collect_wizard_inputs_cancellation_paths(
    side_effect: list[str] | type[BaseException],
) -> None:
    """Invalid menu selection or user cancellation returns ``None``."""
    with patch("builtins.input", side_effect=side_effect):
        assert collect_wizard_inputs() is None


def test_collect_wizard_inputs_lowercases_project_id() -> None:
    """Uppercase project IDs are normalised with a friendly note."""
    with (
        patch(
            "builtins.input",
            side_effect=["1", "1", "MyProject", "https://example.com/r.git", "main", "n"],
        ),
        patch("builtins.print") as mock_print,
    ):
        result = collect_wizard_inputs()

    assert result == wizard_values(project_id="myproject", upstream_url="https://example.com/r.git")
    printed = [" ".join(str(arg) for arg in call.args) for call in mock_print.call_args_list]
    assert any("normalised to 'myproject'" in line for line in printed)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("terok", "terok", id="already-valid-passthrough"),
        pytest.param("My Project", "my-project", id="spaces-to-hyphens"),
        pytest.param("MyProject", "myproject", id="camelcase-lowercased"),
        pytest.param("proj!!@#", "proj", id="special-chars-dropped"),
        pytest.param("foo   ---   bar", "foo-bar", id="runs-collapsed"),
        pytest.param("   edgy   ", "edgy", id="surrounding-whitespace-stripped"),
        pytest.param("-_proj_-", "proj", id="leading-and-trailing-punctuation-trimmed"),
        pytest.param("!!!", "", id="nothing-salvageable"),
        pytest.param("terok pages", "terok-pages", id="the-field-report-case"),
    ],
)
def test_slugify_project_id(raw: str, expected: str) -> None:
    """``_slugify_project_id`` meets users halfway without silently mangling intent."""
    assert _slugify_project_id(raw) == expected


def generate_into_tmp(values: dict[str, object]) -> tuple[str, str, str]:
    """Generate a project config into a temporary user-projects root and return path metadata."""
    with tempfile.TemporaryDirectory() as td:
        with patch("terok.lib.domain.wizards.new_project.user_projects_dir", return_value=Path(td)):
            config_path = generate_config(values)
            return (
                config_path.name,
                config_path.parent.name,
                config_path.read_text(encoding="utf-8"),
            )


@pytest.mark.parametrize(
    ("values", "expected_snippets"),
    [
        pytest.param(
            wizard_values(),
            [
                'id: "test-proj"',
                "https://github.com/user/repo.git",
                'default_branch: "main"',
                'security_class: "online"',
            ],
            id="online-default",
        ),
        pytest.param(
            wizard_values(
                security_class="gatekeeping",
                project_id="gk-proj",
                upstream_url="git@github.com:user/repo.git",
                default_branch="dev",
                user_snippet="RUN apt-get update",
            ),
            [
                'security_class: "gatekeeping"',
                'default_branch: "dev"',
                "RUN apt-get update",
                "gatekeeping:",
            ],
            id="gatekeeping-default",
        ),
        pytest.param(
            wizard_values(
                base="nvidia",
                project_id="gpu-proj",
                upstream_url="https://example.com/r.git",
            ),
            ["gpus: all", "nvcr.io/nvidia/"],
            id="online-nvidia",
        ),
        pytest.param(
            wizard_values(
                base="fedora",
                project_id="fedora-proj",
                upstream_url="https://example.com/r.git",
            ),
            ['base_image: "fedora:43"', 'security_class: "online"'],
            id="online-fedora",
        ),
        pytest.param(
            wizard_values(
                base="podman",
                project_id="podman-proj",
                upstream_url="https://example.com/r.git",
            ),
            ['base_image: "quay.io/podman/stable:latest"'],
            id="online-podman",
        ),
    ],
)
def test_generate_config_templates(values: dict[str, object], expected_snippets: list[str]) -> None:
    """Generated configs include the expected template-specific content."""
    config_name, project_dir_name, content = generate_into_tmp(values)
    assert config_name == "project.yml"
    assert project_dir_name == values["project_id"]
    for snippet in expected_snippets:
        assert snippet in content


def test_generate_config_replaces_all_placeholders() -> None:
    """All template placeholders are rendered away for every (mode, base) pair."""
    for sec_slug, _ in SECURITY_CLASSES:
        for base_slug, _ in BASES:
            _, _, content = generate_into_tmp(
                wizard_values(
                    security_class=sec_slug,
                    base=base_slug,
                    project_id=f"proj-{sec_slug}-{base_slug}",
                    upstream_url="https://example.com/r.git",
                    user_snippet="RUN echo hi",
                )
            )
            for placeholder in (
                "{{PROJECT_ID}}",
                "{{UPSTREAM_URL}}",
                "{{DEFAULT_BRANCH}}",
                "{{USER_SNIPPET}}",
            ):
                assert placeholder not in content, f"{sec_slug}-{base_slug}: {placeholder}"


@pytest.mark.parametrize(
    (
        "collect_result",
        "user_answers",
        "has_init_fn",
        "editor_success",
        "expect_init",
        "expect_result",
    ),
    [
        pytest.param(
            wizard_values(project_id="proj1", upstream_url="https://example.com/r.git"),
            ["y", "y"],
            True,
            True,
            True,
            mock_wizard_project_file("proj1"),
            id="edit-and-init",
        ),
        pytest.param(
            wizard_values(project_id="proj2", upstream_url="https://example.com/r.git"),
            ["n", "n"],
            True,
            True,
            False,
            mock_wizard_project_file("proj2"),
            id="skip-edit-and-init",
        ),
        pytest.param(
            wizard_values(project_id="proj3", upstream_url="https://example.com/r.git"),
            ["n"],
            False,
            True,
            False,
            mock_wizard_project_file("proj3"),
            id="no-init-fn",
        ),
        pytest.param(
            wizard_values(project_id="proj4", upstream_url="https://example.com/r.git"),
            KeyboardInterrupt,
            False,
            True,
            False,
            mock_wizard_project_file("proj4"),
            id="cancel-after-generate",
        ),
        pytest.param(None, [], False, True, False, None, id="collect-cancelled"),
    ],
)
def test_run_wizard(
    collect_result: dict[str, object] | None,
    user_answers: list[str] | type[BaseException],
    has_init_fn: bool,
    editor_success: bool,
    expect_init: bool,
    expect_result: Path | None,
) -> None:
    """Wizard orchestration handles edit/init prompts and cancellation paths."""
    init_fn = Mock() if has_init_fn else None
    with (
        patch(
            "terok.lib.domain.wizards.new_project.collect_wizard_inputs",
            return_value=collect_result,
        ),
        patch(
            "terok.lib.domain.wizards.new_project.generate_config", return_value=expect_result
        ) as mock_generate_config,
        patch(
            "terok.lib.domain.wizards.new_project.open_in_editor", return_value=editor_success
        ) as mock_editor,
        patch("builtins.input", side_effect=user_answers),
    ):
        result = run_wizard(init_fn=init_fn)

    assert result == expect_result
    if collect_result is None:
        mock_generate_config.assert_not_called()
        mock_editor.assert_not_called()
        return

    mock_generate_config.assert_called_once_with(collect_result)

    if user_answers is KeyboardInterrupt:
        mock_editor.assert_not_called()
    else:
        assert mock_editor.call_count == (
            0 if user_answers and user_answers[0] in {"n", "no"} else 1
        )
    if expect_init:
        init_fn.assert_called_once_with(collect_result["project_id"])
    elif init_fn is not None:
        init_fn.assert_not_called()


# ---------------------------------------------------------------------------
# validate_answer — spec surface shared by the CLI loop and the TUI modal.
# Parametrised so presenter tests can lean on this as the source of truth
# for per-field behaviour.
# ---------------------------------------------------------------------------


def _q(key: str) -> Question:
    """Look up the declared question for *key* — fails fast on drift."""
    for q in QUESTIONS:
        if q.key == key:
            return q
    raise AssertionError(f"No question with key {key!r} in QUESTIONS")


class TestValidateAnswer:
    """validate_answer covers every branch a presenter would need to handle."""

    def test_choice_accepts_declared_slug(self) -> None:
        """A raw slug from choices passes through unchanged."""
        value, err = validate_answer(_q("security_class"), "online")
        assert value == "online"
        assert err is None

    def test_required_rejects_empty(self) -> None:
        """A required question refuses empty input with the standard message."""
        value, err = validate_answer(_q("project_id"), "")
        assert err is not None
        assert "required" in err

    def test_optional_accepts_empty(self) -> None:
        """An optional question (upstream_url) is fine with an empty answer."""
        value, err = validate_answer(_q("upstream_url"), "")
        assert value == ""
        assert err is None

    def test_transform_runs_before_validation(self) -> None:
        """Slugify + lowercase on project_id normalises before the regex check fires."""
        value, err = validate_answer(_q("project_id"), "MyProject")
        assert value == "myproject"
        assert err is None

    def test_transform_slugifies_spaces_and_specials(self) -> None:
        """Whitespace turns into hyphens and stray punctuation is dropped."""
        value, err = validate_answer(_q("project_id"), "My Fancy Project!!")
        assert value == "my-fancy-project"
        assert err is None

    def test_transform_collapses_hyphen_runs(self) -> None:
        """Consecutive delimiters collapse into one hyphen."""
        value, err = validate_answer(_q("project_id"), "foo   ---   bar")
        assert value == "foo-bar"
        assert err is None

    def test_validator_surfaces_error(self) -> None:
        """A slug that can't be salvaged by slugification surfaces the regex error."""
        # Only punctuation — nothing in the allowed alphabet survives.
        value, err = validate_answer(_q("project_id"), "!!!")
        assert err is not None
        assert "Invalid project ID" in err or "required" in err

    def test_editor_kind_accepts_arbitrary_text(self) -> None:
        """Editor-style questions have no validator; any string goes through."""
        snippet = "RUN apt-get update && apt-get install -y ripgrep"
        value, err = validate_answer(_q("user_snippet"), snippet)
        assert value == snippet
        assert err is None

    def test_surrounding_whitespace_is_stripped(self) -> None:
        """Leading/trailing whitespace is removed before validation and transform."""
        value, err = validate_answer(_q("project_id"), "  MyProj  ")
        assert value == "myproj"
        assert err is None

    def test_all_whitespace_answer_counts_as_empty_for_required(self) -> None:
        """``'   '`` → required field rejected the same way an empty string is."""
        value, err = validate_answer(_q("project_id"), "   ")
        assert value == ""
        assert err is not None
        assert "required" in err

    def test_unknown_choice_slug_is_rejected(self) -> None:
        """Defensive check: a bogus slug for a choice question returns an error."""
        value, err = validate_answer(_q("security_class"), "bogus")
        assert err is not None
        assert "must be one of" in err

    def test_whitespace_only_optional_text_accepted_as_empty(self) -> None:
        """Optional text field: spaces collapse to empty and pass through cleanly."""
        value, err = validate_answer(_q("upstream_url"), "   ")
        assert value == ""
        assert err is None


# ---------------------------------------------------------------------------
# render_project_yaml / write_project_yaml — TUI-only rendering helpers that
# need the same template resolution as generate_config.
# ---------------------------------------------------------------------------


class TestRenderAndWrite:
    """The two-halves split of generate_config used by the TUI review path."""

    def test_render_project_yaml_matches_generate_output(self) -> None:
        """In-memory render must equal the file generate_config writes."""
        values = wizard_values(project_id="renderp", upstream_url="https://example.com/r.git")
        rendered = render_project_yaml(values)
        with tempfile.TemporaryDirectory() as td:
            with patch(
                "terok.lib.domain.wizards.new_project.user_projects_dir", return_value=Path(td)
            ):
                path = generate_config(values)
            assert path.read_text(encoding="utf-8") == rendered

    def test_write_project_yaml_refuses_overwrite_by_default(self) -> None:
        """A second write without ``overwrite=True`` leaves the original in place."""
        with (
            tempfile.TemporaryDirectory() as td,
            patch("terok.lib.domain.wizards.new_project.user_projects_dir", return_value=Path(td)),
        ):
            first = write_project_yaml("scratch", "first: true\n")
            second = write_project_yaml("scratch", "second: true\n")
            assert first == second
            assert first.read_text() == "first: true\n"

    def test_write_project_yaml_overwrite_true_replaces_contents(self) -> None:
        """``overwrite=True`` replaces the contents — used by the TUI review path."""
        with (
            tempfile.TemporaryDirectory() as td,
            patch("terok.lib.domain.wizards.new_project.user_projects_dir", return_value=Path(td)),
        ):
            write_project_yaml("scratch", "first: true\n")
            path = write_project_yaml("scratch", "second: true\n", overwrite=True)
            assert path.read_text() == "second: true\n"


# ---------------------------------------------------------------------------
# QUESTIONS registry — ordering and shape invariants the presenters rely on.
# ---------------------------------------------------------------------------


class TestQuestionsRegistry:
    """Guard against accidental drift in the wizard vocabulary."""

    def test_declared_keys_unique(self) -> None:
        keys = [q.key for q in QUESTIONS]
        assert len(keys) == len(set(keys))

    def test_first_two_are_choice_questions(self) -> None:
        """Template filename is ``{security}-{base}.yml`` — both must be choice."""
        assert QUESTIONS[0].key == "security_class"
        assert QUESTIONS[0].kind == "choice"
        assert QUESTIONS[1].key == "base"
        assert QUESTIONS[1].kind == "choice"

    def test_every_choice_has_non_empty_options(self) -> None:
        for q in QUESTIONS:
            if q.kind == "choice":
                assert q.choices, f"{q.key} has empty choices"
