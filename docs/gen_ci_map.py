# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Generate the CI workflow map page for MkDocs."""

import mkdocs_gen_files
from mkdocs_terok.ci_map import generate_ci_map

with mkdocs_gen_files.open("ci_map.md", "w") as f:
    f.write(generate_ci_map())
