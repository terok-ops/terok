# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
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

    class _StubObject:
        """Capture constructor args for lightweight Textual test doubles."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._stub_args = args
            self._stub_kwargs = kwargs

    def on(*args: Any, **kwargs: Any) -> Callable[..., Any]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            return fn

        return decorator

    def work(*args: Any, **kwargs: Any) -> Callable[..., Any]:
        """Stub for ``textual.work`` — pass the function through unchanged.

        The real decorator wraps the coroutine in a worker so
        ``push_screen_wait`` has an async context; tests that import
        TUI modules via stubs don't run the body, so identity is fine.
        """
        # Support both ``@work`` (direct call with the function) and
        # ``@work(exclusive=True)`` (configured call returning a decorator).
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            return fn

        return decorator

    textual.on = on
    textual.work = work

    events_mod = types.ModuleType("textual.events")

    class Key:
        pass

    events_mod.Key = Key

    screen_mod = types.ModuleType("textual.screen")

    class ModalScreen(_StubObject):
        @classmethod
        def __class_getitem__(cls, item: type) -> type:
            return cls

    class Screen(_StubObject):
        @classmethod
        def __class_getitem__(cls, item: type) -> type:
            return cls

    screen_mod.ModalScreen = ModalScreen
    screen_mod.Screen = Screen

    app_mod = types.ModuleType("textual.app")

    class App(_StubObject):
        def get_system_commands(self, _screen: Any) -> Any:
            return iter(())

    class ComposeResult:
        pass

    class SystemCommand(tuple):
        """Stub for Textual's SystemCommand named tuple."""

        def __new__(
            cls, title: str, help: str, callback: Any, discover: bool = True
        ) -> "SystemCommand":
            instance = super().__new__(cls, (title, help, callback, discover))
            return instance

        @property
        def title(self) -> str:
            return self[0]

        @property
        def help(self) -> str:
            return self[1]

        @property
        def callback(self) -> Any:
            return self[2]

        @property
        def discover(self) -> bool:
            return self[3]

    app_mod.App = App
    app_mod.ComposeResult = ComposeResult
    app_mod.SystemCommand = SystemCommand

    containers_mod = types.ModuleType("textual.containers")

    class _ContextStub(_StubObject):
        def __enter__(self) -> "_ContextStub":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

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

    class Button(_StubObject):
        class Pressed(_StubObject):
            """Stub button event that only captures construction args."""

    class Footer:
        pass

    class Header:
        pass

    class ListItem(_StubObject):
        """Stub list item that only captures construction args."""

    class ListView(_StubObject):
        class Selected(_StubObject):
            """Stub selection event that only captures construction args."""

        class Highlighted(_StubObject):
            """Stub highlight event that only captures construction args."""

    class Static(_StubObject):
        """Stub static widget that only captures construction args."""

    class OptionList(_StubObject):
        class OptionSelected(_StubObject):
            """Stub option-selected event that only captures construction args."""

        class OptionHighlighted(_StubObject):
            """Stub option-highlighted event that only captures construction args."""

    class TextArea(_StubObject):
        text: str
        has_focus: bool

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.text = ""
            self.has_focus = False

        def focus(self) -> None:
            self.has_focus = True

    class SelectionList(_StubObject):
        _items: tuple[object, ...]
        selected: list[object]
        has_focus: bool

        def __init__(self, *items: object, **kwargs: object) -> None:
            super().__init__(*items, **kwargs)
            self._items = items
            self.selected = []
            self.has_focus = False

        def focus(self) -> None:
            self.has_focus = True

        @classmethod
        def __class_getitem__(cls, item: type) -> type:
            return cls

    class RichLog(_StubObject):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.auto_scroll = kwargs.get("auto_scroll", True)
            self.entries: list[Any] = []

        def write(self, content: Any) -> None:
            self.entries.append(content)

        def clear(self) -> None:
            self.entries.clear()

    class Input(_StubObject):
        """Stub input widget that only captures construction args."""

        class Submitted(_StubObject):
            """Stub input submitted event."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.value = kwargs.get("value", "")

        def focus(self) -> None:
            pass

    class Select(_StubObject):
        """Stub select widget that only captures construction args."""

        BLANK = ""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.value = kwargs.get("value", self.BLANK)

        @classmethod
        def __class_getitem__(cls, item: type) -> type:
            return cls

    class Label(_StubObject):
        """Stub label widget that only captures construction args."""

    widgets_mod.Button = Button
    widgets_mod.Input = Input
    widgets_mod.Footer = Footer
    widgets_mod.Header = Header
    widgets_mod.Label = Label
    widgets_mod.ListItem = ListItem
    widgets_mod.ListView = ListView
    widgets_mod.Static = Static
    widgets_mod.OptionList = OptionList
    widgets_mod.TextArea = TextArea
    widgets_mod.SelectionList = SelectionList
    widgets_mod.Select = Select
    widgets_mod.RichLog = RichLog

    option_list_mod = types.ModuleType("textual.widgets.option_list")

    class Option(_StubObject):
        """Stub option that only captures construction args."""

    option_list_mod.Option = Option

    message_mod = types.ModuleType("textual.message")

    class Message:
        pass

    message_mod.Message = Message

    worker_mod = types.ModuleType("textual.worker")

    class Worker:
        class StateChanged(_StubObject):
            """Stub worker state-changed event that only captures args."""

    class WorkerState:
        SUCCESS = "success"
        ERROR = "error"
        CANCELLED = "cancelled"

    worker_mod.Worker = Worker
    worker_mod.WorkerState = WorkerState

    binding_mod = types.ModuleType("textual.binding")

    class Binding(_StubObject):
        """Stub binding that only captures construction args."""

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
    """Clear terok.tui modules and import the given modules with Textual stubs."""
    if stubs is None:
        stubs = build_textual_stubs()
    real_find_spec = importlib.util.find_spec

    def _find_spec(name: str, *a: Any, **kw: Any) -> Any:
        if name == "textual":
            return mock.Mock()
        return real_find_spec(name, *a, **kw)

    with mock.patch("importlib.util.find_spec", side_effect=_find_spec):
        with mock.patch.dict(sys.modules, stubs):
            for mod_name in tuple(sys.modules):
                if mod_name.startswith("terok.tui"):
                    sys.modules.pop(mod_name, None)
            return [importlib.import_module(name) for name in module_names]


def import_fresh(
    stubs: dict[str, types.ModuleType] | None = None,
) -> tuple[types.ModuleType, types.ModuleType, types.ModuleType]:
    """Clear terok.tui modules and reimport with stubs.

    Returns (screens, widgets, app) module tuple.
    """
    screens, widgets, app = _import_with_stubs(
        stubs, "terok.tui.screens", "terok.tui.widgets", "terok.tui.app"
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
    return app_mod, app_mod.TerokTUI


def import_log_viewer(
    stubs: dict[str, types.ModuleType] | None = None,
) -> types.ModuleType:
    """Import log_viewer module with stubs."""
    return _import_with_stubs(stubs, "terok.tui.log_viewer")[0]


def make_key_event(key_str: str) -> mock.Mock:
    """Create a mock key event with the given key string."""
    event = mock.Mock()
    event.key = key_str
    return event
