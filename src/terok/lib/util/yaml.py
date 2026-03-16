# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Centralised YAML I/O — round-trip mode everywhere.

**Facade** over ``ruamel.yaml``'s ceremony-heavy ``YAML()`` class: callers get
a minimal ``load`` / ``dump`` / ``YAMLError`` surface instead of instance
creation, typ selection, stream management, and config attributes.

**Adapter** from ruamel.yaml's stream-oriented API to the string-based
convention used throughout the codebase (``path.read_text()`` → ``load(text)``
→ modify → ``dump(data)`` → ``path.write_text(text)``).

``CommentedMap`` is a ``dict`` subclass — ``isinstance(x, dict)``, ``.get()``,
``x["key"]``, ``.setdefault()`` all work transparently.  Pydantic v2
``model_validate()`` accepts dict subclasses, so read-side validation is
unchanged.
"""

from __future__ import annotations

from io import StringIO
from typing import Any

from ruamel.yaml import YAML, YAMLError  # noqa: F401 — re-exported

__all__ = ["load", "dump", "YAMLError"]

_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True


def load(text: str) -> Any:
    """Round-trip load from a YAML string, preserving comments and order."""
    return _yaml.load(text)


def dump(data: Any, *, default_flow_style: bool = False) -> str:
    """Round-trip dump to a YAML string, preserving comments and order.

    Key order is preserved (insertion order for new dicts, original order for
    round-tripped data).  ``sort_keys`` is always ``False`` — the caller never
    needs to pass it.
    """
    emitter = _yaml
    if default_flow_style:
        emitter = YAML(typ="rt")
        emitter.default_flow_style = True
    buf = StringIO()
    emitter.dump(data, buf)
    return buf.getvalue()
