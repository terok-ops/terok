# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for per-layer image staleness detection via build manifests."""

from __future__ import annotations

from unittest.mock import Mock, patch


class TestDetectStaleLayers:
    """Verify _detect_stale_layers identifies per-layer staleness."""

    @staticmethod
    def _make_manifest(l0_hash: str, l1_hash: str, l2_hash: str) -> dict:
        return {
            "schema": 1,
            "base_image": "ubuntu:24.04",
            "l0": {"tag": "terok-l0:ubuntu-24-04", "content_hash": l0_hash},
            "l1": {"tag": "terok-l1-cli:ubuntu-24-04", "content_hash": l1_hash},
            "l2_cli": {"tag": "test:l2-cli", "content_hash": l2_hash},
            "combined_hash": "irrelevant",
        }

    def test_all_current_returns_empty(self) -> None:
        """All layers current → empty stale list."""
        from terok.lib.domain.project_state import _detect_stale_layers

        project = Mock(id="p1", base_image="ubuntu:24.04")
        rendered = {
            "L0.Dockerfile": "FROM ubuntu:24.04",
            "L1.cli.Dockerfile": "FROM l0",
            "L2.Dockerfile": "FROM l1",
        }

        with (
            patch("terok.lib.orchestration.image.l0_content_hash", return_value="h0"),
            patch("terok.lib.orchestration.image.l1_content_hash", return_value="h1"),
            patch("terok.lib.orchestration.image.l2_content_hash", return_value="h2"),
            patch(
                "terok.lib.orchestration.image.read_build_manifest",
                return_value=self._make_manifest("h0", "h1", "h2"),
            ),
        ):
            assert _detect_stale_layers(project, rendered) == []

    def test_l1_stale_detected(self) -> None:
        """L1 hash mismatch → ["l1"] returned."""
        from terok.lib.domain.project_state import _detect_stale_layers

        project = Mock(id="p1", base_image="ubuntu:24.04")
        rendered = {
            "L0.Dockerfile": "FROM ubuntu:24.04",
            "L1.cli.Dockerfile": "FROM l0 CHANGED",
            "L2.Dockerfile": "FROM l1",
        }

        with (
            patch("terok.lib.orchestration.image.l0_content_hash", return_value="h0"),
            patch(
                "terok.lib.orchestration.image.l1_content_hash",
                return_value="h1-new",
            ),
            patch("terok.lib.orchestration.image.l2_content_hash", return_value="h2"),
            patch(
                "terok.lib.orchestration.image.read_build_manifest",
                return_value=self._make_manifest("h0", "h1-old", "h2"),
            ),
        ):
            stale = _detect_stale_layers(project, rendered)
            assert stale == ["l1"]

    def test_missing_manifest_marks_all_stale(self) -> None:
        """No manifest → all layers stale."""
        from terok.lib.domain.project_state import _detect_stale_layers

        project = Mock(id="p1", base_image="ubuntu:24.04")
        rendered = {"L0.Dockerfile": "x", "L1.cli.Dockerfile": "y", "L2.Dockerfile": "z"}

        with (
            patch("terok.lib.orchestration.image.l0_content_hash", return_value="h0"),
            patch("terok.lib.orchestration.image.l1_content_hash", return_value="h1"),
            patch("terok.lib.orchestration.image.l2_content_hash", return_value="h2"),
            patch(
                "terok.lib.orchestration.image.read_build_manifest",
                return_value=None,
            ),
        ):
            assert _detect_stale_layers(project, rendered) == ["l0", "l1", "l2"]

    def test_no_rendered_marks_all_stale(self) -> None:
        """None rendered dict → all layers stale."""
        from terok.lib.domain.project_state import _detect_stale_layers

        project = Mock(id="p1", base_image="ubuntu:24.04")
        assert _detect_stale_layers(project, None) == ["l0", "l1", "l2"]

    def test_multiple_stale_layers(self) -> None:
        """L0 + L2 stale, L1 current → ["l0", "l2"]."""
        from terok.lib.domain.project_state import _detect_stale_layers

        project = Mock(id="p1", base_image="ubuntu:24.04")
        rendered = {"L0.Dockerfile": "x", "L1.cli.Dockerfile": "y", "L2.Dockerfile": "z"}

        with (
            patch(
                "terok.lib.orchestration.image.l0_content_hash",
                return_value="h0-new",
            ),
            patch("terok.lib.orchestration.image.l1_content_hash", return_value="h1"),
            patch(
                "terok.lib.orchestration.image.l2_content_hash",
                return_value="h2-new",
            ),
            patch(
                "terok.lib.orchestration.image.read_build_manifest",
                return_value=self._make_manifest("h0-old", "h1", "h2-old"),
            ),
        ):
            stale = _detect_stale_layers(project, rendered)
            assert stale == ["l0", "l2"]


class TestStaleLayerHint:
    """Verify TUI hint selection for stale layers."""

    def test_all_stale_shows_l0_command(self) -> None:
        """All layers stale → shows L0/L1/L2 with deepest rebuild command."""
        from terok.tui.widgets.project_state import _stale_layer_hint

        assert _stale_layer_hint(["l0", "l1", "l2"]) == "L0/L1/L2 (build --full-rebuild)"

    def test_l1_only_shows_agents(self) -> None:
        """L1-only stale → L1 with --agents hint."""
        from terok.tui.widgets.project_state import _stale_layer_hint

        assert _stale_layer_hint(["l1"]) == "L1 (build --agents)"

    def test_l2_only_shows_build(self) -> None:
        """L2-only stale → L2 with plain build hint."""
        from terok.tui.widgets.project_state import _stale_layer_hint

        assert _stale_layer_hint(["l2"]) == "L2 (build)"

    def test_l0_and_l2_shows_both_labels(self) -> None:
        """L0+L2 stale → L0/L2 with --full-rebuild (deepest)."""
        from terok.tui.widgets.project_state import _stale_layer_hint

        assert _stale_layer_hint(["l0", "l2"]) == "L0/L2 (build --full-rebuild)"

    def test_empty_returns_empty(self) -> None:
        """No stale layers → empty hint."""
        from terok.tui.widgets.project_state import _stale_layer_hint

        assert _stale_layer_hint([]) == ""
