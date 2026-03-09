# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Global test fixtures.

Auto-mocks ``_shield_pre_start`` in task runners so existing tests
do not require a real OCI hook or root privileges.
"""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _mock_shield_pre_start():
    """Replace shield pre_start with a no-op returning empty args."""
    with patch(
        "terok.lib.containers.task_runners._shield_pre_start",
        return_value=[],
    ):
        yield
