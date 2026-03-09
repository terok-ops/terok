# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Global test fixtures.

Auto-patches the shield adapter in task_runners and runtime so that unit tests
don't need a real terok-shield OCI hook installed.
"""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _mock_shield_in_task_runners():
    """Prevent real shield calls during container lifecycle tests."""
    with (
        patch(
            "terok.lib.containers.task_runners._shield_pre_start",
            return_value=["--network", "pasta:-T,9418"],
        ),
        patch("terok.lib.containers.task_runners._shield_post_start"),
    ):
        yield
