# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Global test fixtures.

Auto-mocks shield helpers in task runners so existing tests do not
require a real OCI hook, nftables, or root privileges.
"""

from collections.abc import Iterator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _mock_shield_helpers() -> Iterator[None]:
    """Replace shield pre_start and down with no-ops."""
    with (
        patch(
            "terok.lib.containers.task_runners._shield_pre_start_impl",
            return_value=[],
        ),
        patch(
            "terok.lib.containers.task_runners._shield_down_impl",
        ),
    ):
        yield
