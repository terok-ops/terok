# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Shared test helpers for TUI tests that need Textual stubs."""

import importlib
import importlib.util
import sys
import types
from collections.abc import Callable
from typing import Any
from unittest import mock


def build_textual_stubs() -> dict[str, types.ModuleType]:
    """Build stub modules for textual so we can import TUI code without it."""
    textual = types.ModuleType("textual")

    def on(*args: Any, **kwargs: Any) -> Callable[..., Any]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            return fn

        return decorator

    textual.on = on

    events_mod = types.ModuleType("textual.events")

    class Key:
        pass

    events_mod.Key = Key

    screen_mod = types.ModuleType("textual.screen")

    class ModalScreen:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        @classmethod
        def __class_getitem__(cls, item: type) -> type:
            return cls

    class Screen:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        @classmethod
        def __class_getitem__(cls, item: type) -> type:
            return cls

    screen_mod.ModalScreen = ModalScreen
    screen_mod.Screen = Screen

    app_mod = types.ModuleType("textual.app")

    class App:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class ComposeResult:
        pass

    app_mod.App = App
    app_mod.ComposeResult = ComposeResult

    containers_mod = types.ModuleType("textual.containers")

    class _ContextStub:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "_ContextStub":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            pass

    class Horizontal(_ContextStub):
        pass

    class Vertical(_ContextStub):
        pass

    class VerticalScroll(_ContextStub):
        pass

    containers_mod.Horizontal = Horizontal
    containers_mod.Vertical = Vertical
    containers_mod.VerticalScroll = VerticalScroll

    widgets_mod = types.ModuleType("textual.widgets")

    class Button:
        class Pressed:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class Footer:
        pass

    class Header:
        pass

    class ListItem:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class ListView:
        class Selected:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        class Highlighted:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class Static:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class OptionList:
        class OptionSelected:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        class OptionHighlighted:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class TextArea:
        text: str

        def __init__(self, *args: object, **kwargs: object) -> None:
            self.text = ""

        def focus(self) -> None:
            pass

    class SelectionList:
        _items: tuple[object, ...]
        selected: list[object]

        def __init__(self, *items: object, **kwargs: object) -> None:
            self._items = items
            self.selected = []

        def focus(self) -> None:
            pass

        @classmethod
        def __class_getitem__(cls, item: type) -> type:
            return cls

    class RichLog:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.auto_scroll = kwargs.get("auto_scroll", True)

        def write(self, content: Any) -> None:
            pass

        def clear(self) -> None:
            pass

    widgets_mod.Button = Button
    widgets_mod.Footer = Footer
    widgets_mod.Header = Header
    widgets_mod.ListItem = ListItem
    widgets_mod.ListView = ListView
    widgets_mod.Static = Static
    widgets_mod.OptionList = OptionList
    widgets_mod.TextArea = TextArea
    widgets_mod.SelectionList = SelectionList
    widgets_mod.RichLog = RichLog

    option_list_mod = types.ModuleType("textual.widgets.option_list")

    class Option:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    option_list_mod.Option = Option

    message_mod = types.ModuleType("textual.message")

    class Message:
        pass

    message_mod.Message = Message

    worker_mod = types.ModuleType("textual.worker")

    class Worker:
        class StateChanged:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        pass

    class WorkerState:
        SUCCESS = "success"
        ERROR = "error"

    worker_mod.Worker = Worker
    worker_mod.WorkerState = WorkerState

    binding_mod = types.ModuleType("textual.binding")

    class Binding:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    binding_mod.Binding = Binding

    textual.events = events_mod
    textual.screen = screen_mod

    return {
        "textual": textual,
        "textual.events": events_mod,
        "textual.screen": screen_mod,
        "textual.app": app_mod,
        "textual.containers": containers_mod,
        "textual.widgets": widgets_mod,
        "textual.widgets.option_list": option_list_mod,
        "textual.message": message_mod,
        "textual.worker": worker_mod,
        "textual.binding": binding_mod,
    }


def _import_with_stubs(
    stubs: dict[str, types.ModuleType] | None,
    *module_names: str,
) -> list[types.ModuleType]:
    """Clear luskctl.tui modules and import the given modules with Textual stubs."""
    if stubs is None:
        stubs = build_textual_stubs()
    real_find_spec = importlib.util.find_spec

    def _find_spec(name: str, *a: Any, **kw: Any) -> Any:
        if name == "textual":
            return mock.Mock()
        return real_find_spec(name, *a, **kw)

    with mock.patch("importlib.util.find_spec", side_effect=_find_spec):
        with mock.patch.dict(sys.modules, stubs):
            for mod_name in list(sys.modules):
                if mod_name.startswith("luskctl.tui"):
                    sys.modules.pop(mod_name, None)
            return [importlib.import_module(name) for name in module_names]


def import_fresh(
    stubs: dict[str, types.ModuleType] | None = None,
) -> tuple[types.ModuleType, types.ModuleType, types.ModuleType]:
    """Clear luskctl.tui modules and reimport with stubs.

    Returns (screens, widgets, app) module tuple.
    """
    screens, widgets, app = _import_with_stubs(
        stubs, "luskctl.tui.screens", "luskctl.tui.widgets", "luskctl.tui.app"
    )
    return screens, widgets, app


def import_screens(
    stubs: dict[str, types.ModuleType] | None = None,
) -> tuple[types.ModuleType, types.ModuleType]:
    """Import screens and widgets modules with stubs."""
    screens, widgets, _ = import_fresh(stubs)
    return screens, widgets


def import_widgets(
    stubs: dict[str, types.ModuleType] | None = None,
) -> types.ModuleType:
    """Import widgets module with stubs."""
    _, widgets, _ = import_fresh(stubs)
    return widgets


def import_app(
    stubs: dict[str, types.ModuleType] | None = None,
) -> tuple[types.ModuleType, type]:
    """Import app module with stubs and return (app_mod, AppClass)."""
    _, _, app_mod = import_fresh(stubs)
    return app_mod, app_mod.LuskTUI


def import_log_viewer(
    stubs: dict[str, types.ModuleType] | None = None,
) -> types.ModuleType:
    """Import log_viewer module with stubs."""
    return _import_with_stubs(stubs, "luskctl.tui.log_viewer")[0]


def make_key_event(key_str: str) -> mock.Mock:
    """Create a mock key event with the given key string."""
    event = mock.Mock()
    event.key = key_str
    return event
