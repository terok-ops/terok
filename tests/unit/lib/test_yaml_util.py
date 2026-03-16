# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the centralised YAML I/O wrapper (ruamel.yaml round-trip)."""

from __future__ import annotations

import pytest

from terok.lib.util.yaml import YAMLError, dump, load


class TestLoad:
    """Tests for ``load``."""

    def test_plain_dict(self) -> None:
        assert load("key: value") == {"key": "value"}

    def test_returns_none_on_empty(self) -> None:
        assert load("") is None

    def test_nested_structure(self) -> None:
        text = "parent:\n  child: 42\n  list:\n    - a\n    - b\n"
        result = load(text)
        assert result["parent"]["child"] == 42
        assert result["parent"]["list"] == ["a", "b"]

    def test_raises_yaml_error_on_malformed(self) -> None:
        with pytest.raises(YAMLError):
            load("{{invalid yaml::")


class TestDump:
    """Tests for ``dump``."""

    def test_plain_dict(self) -> None:
        result = dump({"key": "value"})
        assert "key: value" in result

    def test_key_order_preserved(self) -> None:
        data = {"zebra": 1, "alpha": 2, "middle": 3}
        result = dump(data)
        lines = [ln for ln in result.strip().splitlines() if ln.strip()]
        keys = [ln.split(":")[0] for ln in lines]
        assert keys == ["zebra", "alpha", "middle"]

    def test_default_flow_style(self) -> None:
        data = {"items": [1, 2, 3]}
        result = dump(data, default_flow_style=True)
        assert "{" in result or "[" in result


class TestRoundTrip:
    """Tests for comment preservation across load → modify → dump."""

    def test_comments_preserved(self) -> None:
        text = "# top comment\nkey: value  # inline\nother: 42\n"
        data = load(text)
        data["other"] = 99
        result = dump(data)
        assert "# top comment" in result
        assert "# inline" in result
        assert "other: 99" in result

    def test_key_order_from_source(self) -> None:
        text = "zebra: 1\nalpha: 2\n"
        data = load(text)
        result = dump(data)
        lines = [ln for ln in result.strip().splitlines() if ln.strip()]
        keys = [ln.split(":")[0] for ln in lines]
        assert keys == ["zebra", "alpha"]

    def test_dict_isinstance(self) -> None:
        """CommentedMap is a dict subclass — isinstance works."""
        data = load("key: value")
        assert isinstance(data, dict)

    def test_dict_operations(self) -> None:
        """CommentedMap supports standard dict operations."""
        data = load("key: value")
        assert data.get("key") == "value"
        assert data.get("missing", "default") == "default"
        data.setdefault("new", 123)
        assert data["new"] == 123
