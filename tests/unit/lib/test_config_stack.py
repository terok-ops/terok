# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the generic config stack engine."""

from __future__ import annotations

import copy
import json
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest
from terok_agent import (
    ConfigScope,
    ConfigStack,
    deep_merge,
    load_json_scope,
    load_yaml_scope,
)

from terok.lib.util.yaml import dump as yaml_dump
from tests.testfs import NONEXISTENT_CONFIG_JSON, NONEXISTENT_CONFIG_YAML


@pytest.mark.parametrize(
    ("base", "override", "expected"),
    [
        ({"a": 1, "b": 2}, {"b": 3, "c": 4}, {"a": 1, "b": 3, "c": 4}),
        ({"x": {"a": 1, "b": 2}}, {"x": {"b": 3, "c": 4}}, {"x": {"a": 1, "b": 3, "c": 4}}),
        ({"a": 1, "b": 2, "c": 3}, {"b": None}, {"a": 1, "c": 3}),
        ({"x": {"a": 1, "b": 2}}, {"x": {"a": None}}, {"x": {"b": 2}}),
        ({"items": [1, 2, 3]}, {"items": [4, 5]}, {"items": [4, 5]}),
        ({"items": ["a", "b"]}, {"items": ["_inherit", "c"]}, {"items": ["a", "b", "c"]}),
        ({"items": ["a", "b"]}, {"items": ["c", "_inherit"]}, {"items": ["c", "a", "b"]}),
        ({"items": ["b"]}, {"items": ["a", "_inherit", "c"]}, {"items": ["a", "b", "c"]}),
        (
            {"x": {"a": 1, "b": 2}},
            {"x": {"_inherit": True, "c": 3}},
            {"x": {"a": 1, "b": 2, "c": 3}},
        ),
        (
            {"x": {"a": 1, "b": 2}},
            {"x": {"_inherit": True, "b": 9, "c": 3}},
            {"x": {"a": 1, "b": 9, "c": 3}},
        ),
        ({"x": {"a": 1, "b": 2}}, {"x": {"c": 3}}, {"x": {"a": 1, "b": 2, "c": 3}}),
        (
            {"a": 1, "b": [1, 2], "c": {"x": 1}},
            {"a": "_inherit", "b": "_inherit", "c": "_inherit"},
            {"a": 1, "b": [1, 2], "c": {"x": 1}},
        ),
        ({"a": 1}, {"a": "_inherit", "b": "_inherit"}, {"a": 1}),
        ({}, {"a": 1}, {"a": 1}),
        ({"a": 1}, {}, {"a": 1}),
        ({}, {}, {}),
        ({"x": {"a": 1}}, {"x": "flat"}, {"x": "flat"}),
        ({"x": "flat"}, {"x": {"a": 1}}, {"x": {"a": 1}}),
        (
            {"a": {"b": {"c": {"d": 1, "e": 2}}}},
            {"a": {"b": {"c": {"e": 3, "f": 4}}}},
            {"a": {"b": {"c": {"d": 1, "e": 3, "f": 4}}}},
        ),
    ],
    ids=[
        "simple-override",
        "nested-merge",
        "delete-key",
        "delete-nested-key",
        "replace-list",
        "inherit-list-prefix",
        "inherit-list-suffix",
        "inherit-list-middle",
        "inherit-dict-keep-parent",
        "inherit-dict-override-parent",
        "recursive-merge",
        "bare-inherit-keep-base",
        "bare-inherit-drop-missing",
        "empty-base",
        "empty-override",
        "both-empty",
        "scalar-over-dict",
        "dict-over-scalar",
        "deeply-nested",
    ],
)
def test_deep_merge(base: dict, override: dict, expected: dict) -> None:
    """deep_merge handles overrides, deletions, inheritance, and recursion."""
    base_before = copy.deepcopy(base)
    override_before = copy.deepcopy(override)
    assert deep_merge(base, override) == expected
    assert base == base_before
    assert override == override_before


class TestConfigStack:
    """Tests for ConfigScope and ConfigStack."""

    def test_single_scope(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("base", None, {"a": 1}))
        assert stack.resolve() == {"a": 1}

    def test_multi_level_chaining(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("global", None, {"a": 1, "b": 1}))
        stack.push(ConfigScope("project", None, {"b": 2, "c": 2}))
        stack.push(ConfigScope("cli", None, {"c": 3, "d": 3}))
        assert stack.resolve() == {"a": 1, "b": 2, "c": 3, "d": 3}

    def test_section_resolution(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("global", None, {"agent": {"model": "haiku"}, "other": 1}))
        stack.push(ConfigScope("project", None, {"agent": {"model": "sonnet", "turns": 5}}))
        assert stack.resolve_section("agent") == {"model": "sonnet", "turns": 5}

    def test_section_resolution_missing_section(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("global", None, {"other": 1}))
        assert stack.resolve_section("agent") == {}

    def test_empty_stack(self) -> None:
        assert ConfigStack().resolve() == {}

    def test_scope_with_none_deletion(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("base", None, {"a": 1, "b": 2}))
        stack.push(ConfigScope("override", None, {"b": None}))
        assert stack.resolve() == {"a": 1}

    def test_scopes_property(self) -> None:
        stack = ConfigStack()
        scopes = [ConfigScope("a", None, {}), ConfigScope("b", None, {})]
        for scope in scopes:
            stack.push(scope)
        assert stack.scopes == scopes

    def test_scopes_property_is_copy(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("a", None, {}))
        scopes = stack.scopes
        scopes.append(ConfigScope("b", None, {}))
        assert len(stack.scopes) == 1


@pytest.mark.parametrize(
    ("loader", "suffix", "content", "expected"),
    [
        (load_yaml_scope, ".yml", yaml_dump({"key": "value"}), {"key": "value"}),
        (load_json_scope, ".json", json.dumps({"key": "value"}), {"key": "value"}),
        (load_yaml_scope, ".yml", "", {}),
        (load_json_scope, ".json", "{}", {}),
    ],
    ids=["yaml", "json", "yaml-empty", "json-empty-object"],
)
def test_scope_loaders(
    loader: Callable[[str, Path], ConfigScope],
    suffix: str,
    content: str,
    expected: dict[str, object],
) -> None:
    """YAML/JSON scope loaders read files and normalize empty inputs."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / f"test{suffix}"
        path.write_text(content, encoding="utf-8")
        scope = loader("test", path)
    assert scope.level == "test"
    assert scope.source == path
    assert scope.data == expected


@pytest.mark.parametrize(
    ("loader", "path"),
    [
        (load_yaml_scope, NONEXISTENT_CONFIG_YAML),
        (load_json_scope, NONEXISTENT_CONFIG_JSON),
    ],
    ids=["yaml-missing", "json-missing"],
)
def test_scope_loaders_missing_files(
    loader: Callable[[str, Path], ConfigScope],
    path: Path,
) -> None:
    """Missing config files are treated as empty scopes."""
    assert loader("missing", path).data == {}
