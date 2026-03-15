# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest
from tui_test_helpers import import_app


def test_tui_main_is_callable() -> None:
    app_module, _ = import_app()
    assert callable(getattr(app_module, "main", None))


def test_tmux_configuration_integration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that the TUI module can import and use the tmux configuration function."""
    from terok.lib.core.config import get_tui_default_tmux

    monkeypatch.delenv("TEROK_CONFIG_FILE", raising=False)
    assert not get_tui_default_tmux()

    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text("tui:\n  default_tmux: true\n", encoding="utf-8")

    monkeypatch.setenv("TEROK_CONFIG_FILE", str(cfg_path))
    assert get_tui_default_tmux()
