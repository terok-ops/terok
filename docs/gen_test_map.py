# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""MkDocs gen-files hook for the integration test map."""

import mkdocs_gen_files
from mkdocs_terok.test_map import TestMapConfig, generate_test_map

_DIR_ORDER = ("cli", "projects", "tasks", "setup", "gate", "launch", "containers")

config = TestMapConfig(dir_order=_DIR_ORDER, show_markers=False)
with mkdocs_gen_files.open("test_map.md", "w") as f:
    f.write(generate_test_map(config=config))
