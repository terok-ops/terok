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

    def test_returns_none_on_whitespace_only(self) -> None:
        assert load("   \n\n  ") is None

    def test_nested_structure(self) -> None:
        text = "parent:\n  child: 42\n  list:\n    - a\n    - b\n"
        result = load(text)
        assert result["parent"]["child"] == 42
        assert result["parent"]["list"] == ["a", "b"]

    def test_raises_yaml_error_on_malformed(self) -> None:
        with pytest.raises(YAMLError):
            load("{{invalid yaml::")

    def test_plain_list(self) -> None:
        result = load("- one\n- two\n- three\n")
        assert result == ["one", "two", "three"]

    def test_scalar_value(self) -> None:
        assert load("42") == 42

    def test_boolean_values(self) -> None:
        data = load("flag: true\nother: false\n")
        assert data["flag"] is True
        assert data["other"] is False

    def test_multiline_string(self) -> None:
        text = "desc: |\n  line one\n  line two\n"
        data = load(text)
        assert "line one" in data["desc"]
        assert "line two" in data["desc"]

    def test_unicode_content(self) -> None:
        data = load("name: Jíří\nemoji: 🚀\n")
        assert data["name"] == "Jíří"
        assert data["emoji"] == "🚀"


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

    def test_flow_style_preserves_quotes(self) -> None:
        """Flow-style emitter must preserve quotes like the default emitter."""
        data = load("key: 'quoted value'\n")
        result = dump(data, default_flow_style=True)
        assert "'quoted value'" in result or '"quoted value"' in result

    def test_dumps_none(self) -> None:
        result = dump(None)
        assert "null" in result

    def test_dumps_list(self) -> None:
        result = dump(["a", "b", "c"])
        assert "- a" in result
        assert "- b" in result

    def test_dumps_nested_dict(self) -> None:
        data = {"outer": {"inner": "value"}}
        result = dump(data)
        assert "outer:" in result
        assert "inner: value" in result

    def test_trailing_newline(self) -> None:
        """Dump output ends with a newline (standard YAML convention)."""
        result = dump({"key": "value"})
        assert result.endswith("\n")


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

    def test_quoted_strings_preserved(self) -> None:
        """Quoted strings survive a round-trip without losing their quotes."""
        text = "single: 'hello'\ndouble: \"world\"\n"
        data = load(text)
        result = dump(data)
        assert "'hello'" in result
        assert '"world"' in result

    def test_add_key_preserves_existing(self) -> None:
        """Adding a new key does not disturb existing comments or order."""
        text = "# config\nalpha: 1\nbeta: 2  # important\n"
        data = load(text)
        data["gamma"] = 3
        result = dump(data)
        assert "# config" in result
        assert "# important" in result
        assert "gamma: 3" in result
        # Original key order maintained
        lines = [ln.split(":")[0].strip() for ln in result.splitlines() if ":" in ln]
        assert lines.index("alpha") < lines.index("beta") < lines.index("gamma")

    def test_remove_key_preserves_rest(self) -> None:
        """Removing a key preserves comments on remaining keys."""
        text = "keep: 1  # stay\nremove: 2\nother: 3  # also stay\n"
        data = load(text)
        del data["remove"]
        result = dump(data)
        assert "# stay" in result
        assert "# also stay" in result
        assert "remove" not in result

    def test_or_empty_fallback_returns_plain_dict(self) -> None:
        """``load("") or {}`` returns a plain dict (the common fallback pattern)."""
        result = load("") or {}
        assert result == {}
        assert isinstance(result, dict)

    def test_multiple_round_trips_stable(self) -> None:
        """Data survives multiple load → dump cycles without drift."""
        text = "# header\nname: test  # project name\nversion: 1\n"
        data = load(text)
        for _ in range(5):
            text = dump(data)
            data = load(text)
        assert "# header" in text
        assert "# project name" in text
        assert "name: test" in text


class TestYAMLError:
    """Tests for ``YAMLError`` re-export."""

    def test_yaml_error_is_exception(self) -> None:
        assert issubclass(YAMLError, Exception)

    def test_yaml_error_catchable(self) -> None:
        """YAMLError can be caught in except clauses."""
        with pytest.raises(YAMLError):
            load(":\n  :\n    - :\n  bad: [")

    def test_yaml_error_on_duplicate_keys(self) -> None:
        """ruamel.yaml round-trip mode raises on duplicate keys by default."""
        with pytest.raises(YAMLError):
            load("key: 1\nkey: 2\n")
