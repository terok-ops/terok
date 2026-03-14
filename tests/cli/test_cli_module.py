# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import importlib


def test_cli_main_is_callable() -> None:
    module = importlib.import_module("terok.cli.main")
    assert callable(getattr(module, "main", None))
