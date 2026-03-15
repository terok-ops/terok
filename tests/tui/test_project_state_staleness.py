# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

from unittest import mock

import pytest
from tui_test_helpers import build_textual_stubs, import_fresh

from test_utils import make_staleness_info
from testnet import TEST_UPSTREAM_URL


@pytest.mark.parametrize("security_class", ["online", "gatekeeping"])
def test_staleness_checked_for_online_and_gatekeeping(security_class: str) -> None:
    stubs = build_textual_stubs()
    _, _, app = import_fresh(stubs)

    staleness = make_staleness_info(commits_behind=1)
    project = mock.Mock()
    project.security_class = security_class
    project.upstream_url = TEST_UPSTREAM_URL

    state = {"gate": True}

    mock_gate = mock.Mock()
    mock_gate.compare_vs_upstream.return_value = staleness
    mock_gate.last_commit.return_value = "abc123"

    with mock.patch.object(app, "load_project", return_value=project):
        with mock.patch.object(app, "get_project_state", return_value=state):
            with mock.patch.object(app, "GitGate", return_value=mock_gate):
                result = app.TerokTUI._load_project_state(mock.Mock(), "proj1")

    mock_gate.compare_vs_upstream.assert_called_once()
    assert result.project_id == "proj1"
    assert result.project == project
    assert result.state == state
    assert result.staleness == staleness
    assert result.error is None
