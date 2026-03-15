# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Generate the CI workflow map page for MkDocs."""

import sys
from pathlib import Path

import mkdocs_gen_files

sys.path.insert(0, str(Path(__file__).parent))

import ci_map  # noqa: E402

report = ci_map.generate_ci_map()
with mkdocs_gen_files.open("ci_map.md", "w") as f:
    f.write(report)
