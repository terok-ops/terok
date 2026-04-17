# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for project wizard YAML templates."""

from importlib import resources
from importlib.resources.abc import Traversable

import pytest

from terok.lib.domain.wizards.new_project import BASES, SECURITY_CLASSES
from terok.lib.util.template_utils import render_template

TEMPLATE_DIR: Traversable = resources.files("terok") / "resources" / "templates" / "projects"
EXPECTED_TEMPLATES: list[str] = [
    f"{sec_slug}-{base_slug}.yml" for sec_slug, _ in SECURITY_CLASSES for base_slug, _ in BASES
]
REQUIRED_PLACEHOLDERS: list[str] = [
    "{{PROJECT_ID}}",
    "{{UPSTREAM_URL}}",
    "{{DEFAULT_BRANCH}}",
    "{{USER_SNIPPET}}",
]


def template_text(name: str) -> str:
    """Read a wizard template from the package resources."""
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


class TestWizardTemplates:
    """Tests for project wizard YAML templates."""

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATES)
    def test_all_template_files_exist(self, name: str) -> None:
        assert (TEMPLATE_DIR / name).is_file()

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATES)
    def test_templates_contain_required_placeholders(self, name: str) -> None:
        content = template_text(name)
        for placeholder in REQUIRED_PLACEHOLDERS:
            assert placeholder in content, f"{name} missing placeholder {placeholder}"

    @pytest.mark.parametrize(
        ("name", "expected_fragments"),
        [
            ("online-ubuntu.yml", ['security_class: "online"', "ubuntu:24.04"]),
            ("online-nvidia.yml", ['security_class: "online"', "nvcr.io/nvidia/", "gpus: all"]),
            (
                "gatekeeping-ubuntu.yml",
                ['security_class: "gatekeeping"', "ubuntu:24.04", "gatekeeping:"],
            ),
            (
                "gatekeeping-nvidia.yml",
                [
                    'security_class: "gatekeeping"',
                    "nvcr.io/nvidia/",
                    "gpus: all",
                    "gatekeeping:",
                    "expose_external_remote:",
                ],
            ),
        ],
    )
    def test_template_variants_contain_expected_fragments(
        self,
        name: str,
        expected_fragments: list[str],
    ) -> None:
        content = template_text(name)
        for fragment in expected_fragments:
            assert fragment in content

    def test_render_template_replaces_all_placeholders(self) -> None:
        traversable = TEMPLATE_DIR / "online-ubuntu.yml"
        variables = {
            "PROJECT_ID": "my-project",
            "UPSTREAM_URL": "https://github.com/user/repo.git",
            "DEFAULT_BRANCH": "main",
            "USER_SNIPPET": "RUN apt-get update",
        }
        with resources.as_file(traversable) as path:
            rendered = render_template(path, variables)
        assert 'id: "my-project"' in rendered
        assert 'upstream_url: "https://github.com/user/repo.git"' in rendered
        assert 'default_branch: "main"' in rendered
        assert "RUN apt-get update" in rendered
        for placeholder in REQUIRED_PLACEHOLDERS:
            assert placeholder not in rendered
