# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import importlib
import unittest


class CliModuleTests(unittest.TestCase):
    def test_cli_main_is_callable(self) -> None:
        module = importlib.import_module("terok.cli.main")
        self.assertTrue(callable(getattr(module, "main", None)))
