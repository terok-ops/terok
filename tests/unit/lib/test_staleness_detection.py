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

    def test_malformed_manifest_entry_marks_layer_stale(self) -> None:
        """Manifest with non-dict layer entry → that layer is stale."""
        from terok.lib.domain.project_state import _detect_stale_layers

        project = Mock(id="p1", base_image="ubuntu:24.04")
        rendered = {"L0.Dockerfile": "x", "L1.cli.Dockerfile": "y", "L2.Dockerfile": "z"}

        # l1 entry is a string instead of a dict
        manifest = self._make_manifest("h0", "h1", "h2")
        manifest["l1"] = "not-a-dict"

        with (
            patch("terok.lib.orchestration.image.l0_content_hash", return_value="h0"),
            patch("terok.lib.orchestration.image.l1_content_hash", return_value="h1"),
            patch("terok.lib.orchestration.image.l2_content_hash", return_value="h2"),
            patch(
                "terok.lib.orchestration.image.read_build_manifest",
                return_value=manifest,
            ),
        ):
            stale = _detect_stale_layers(project, rendered)
            assert "l1" in stale
            assert "l0" not in stale  # l0 matches, still current

    def test_missing_manifest_key_marks_layer_stale(self) -> None:
        """Manifest missing a layer key entirely → that layer is stale."""
        from terok.lib.domain.project_state import _detect_stale_layers

        project = Mock(id="p1", base_image="ubuntu:24.04")
        rendered = {"L0.Dockerfile": "x", "L1.cli.Dockerfile": "y", "L2.Dockerfile": "z"}

        manifest = self._make_manifest("h0", "h1", "h2")
        del manifest["l0"]

        with (
            patch("terok.lib.orchestration.image.l0_content_hash", return_value="h0"),
            patch("terok.lib.orchestration.image.l1_content_hash", return_value="h1"),
            patch("terok.lib.orchestration.image.l2_content_hash", return_value="h2"),
            patch(
                "terok.lib.orchestration.image.read_build_manifest",
                return_value=manifest,
            ),
        ):
            stale = _detect_stale_layers(project, rendered)
            assert stale == ["l0"]  # only l0 is stale (missing from manifest)

    def test_import_error_falls_back_to_all_stale(self) -> None:
        """ImportError during hash computation → all layers stale."""
        from terok.lib.domain.project_state import _detect_stale_layers

        project = Mock(id="p1", base_image="ubuntu:24.04")
        rendered = {"L0.Dockerfile": "x", "L1.cli.Dockerfile": "y", "L2.Dockerfile": "z"}

        with patch(
            "terok.lib.orchestration.image.l0_content_hash",
            side_effect=ImportError("missing"),
        ):
            assert _detect_stale_layers(project, rendered) == ["l0", "l1", "l2"]


class TestIsTaskImageOld:
    """Verify is_task_image_old return values."""

    def test_returns_false_when_all_current(self) -> None:
        """Running task with current manifest → False."""
        from terok.lib.domain.project_state import is_task_image_old

        task = Mock(mode="cli", task_id="42")

        with (
            patch("terok.lib.domain.project_state.is_container_running", return_value=True),
            patch("terok.lib.domain.project_state.container_image", return_value="sha256:abc123"),
            patch("terok.lib.domain.project_state._container_name", return_value="c1"),
            patch(
                "terok.lib.orchestration.image.render_all_dockerfiles",
                return_value={"L0.Dockerfile": "x", "L1.cli.Dockerfile": "y", "L2.Dockerfile": "z"},
            ),
            patch(
                "terok.lib.domain.project_state.load_project",
                return_value=Mock(base_image="ubuntu:24.04"),
            ),
            patch(
                "terok.lib.domain.project_state._detect_stale_layers",
                return_value=[],
            ),
        ):
            result = is_task_image_old("proj1", task)

        assert result is False

    def test_returns_true_when_stale(self) -> None:
        """Running task with stale L1 → True."""
        from terok.lib.domain.project_state import is_task_image_old

        task = Mock(mode="cli", task_id="42")

        with (
            patch("terok.lib.domain.project_state.is_container_running", return_value=True),
            patch("terok.lib.domain.project_state.container_image", return_value="sha256:abc123"),
            patch("terok.lib.domain.project_state._container_name", return_value="c1"),
            patch(
                "terok.lib.orchestration.image.render_all_dockerfiles",
                return_value={"L0.Dockerfile": "x", "L1.cli.Dockerfile": "y", "L2.Dockerfile": "z"},
            ),
            patch(
                "terok.lib.domain.project_state.load_project",
                return_value=Mock(base_image="ubuntu:24.04"),
            ),
            patch(
                "terok.lib.domain.project_state._detect_stale_layers",
                return_value=["l1"],
            ),
        ):
            result = is_task_image_old("proj1", task)

        assert result is True

    def test_returns_none_for_non_cli_task(self) -> None:
        """Non-CLI task mode → None (not applicable)."""
        from terok.lib.domain.project_state import is_task_image_old

        task = Mock(mode="web", task_id="42")
        assert is_task_image_old("proj1", task) is None

    def test_returns_none_for_none_project(self) -> None:
        """None project_id → None."""
        from terok.lib.domain.project_state import is_task_image_old

        task = Mock(mode="cli", task_id="42")
        assert is_task_image_old(None, task) is None

    def test_falls_back_to_label_check_on_exception(self) -> None:
        """When manifest approach fails, falls back to L2 label comparison."""
        from terok.lib.domain.project_state import is_task_image_old

        task = Mock(mode="cli", task_id="42")

        with (
            patch("terok.lib.domain.project_state.is_container_running", return_value=True),
            patch("terok.lib.domain.project_state.container_image", return_value="sha256:abc123"),
            patch("terok.lib.domain.project_state._container_name", return_value="c1"),
            # Make manifest approach fail at load_project (inside is_task_image_old)
            patch(
                "terok.lib.domain.project_state.load_project",
                side_effect=RuntimeError("boom"),
            ),
            # Fallback: build_context_hash succeeds
            patch(
                "terok.lib.orchestration.image.build_context_hash",
                return_value="current-hash",
            ),
            # Label on image doesn't match → stale
            patch(
                "terok.lib.domain.project_state.image_labels",
                return_value={"terok.build_context_hash": "old-hash"},
            ),
        ):
            result = is_task_image_old("proj1", task)

        assert result is True  # "old-hash" != "current-hash"


class TestStaleLayerHint:
    """Verify TUI hint selection for stale layers."""

    def test_all_stale(self) -> None:
        """All layers stale → L0/L1/L2."""
        from terok.tui.widgets.project_state import _stale_layer_hint

        assert _stale_layer_hint(["l0", "l1", "l2"]) == "L0/L1/L2"

    def test_l1_only(self) -> None:
        """L1-only stale → L1."""
        from terok.tui.widgets.project_state import _stale_layer_hint

        assert _stale_layer_hint(["l1"]) == "L1"

    def test_l2_only(self) -> None:
        """L2-only stale → L2."""
        from terok.tui.widgets.project_state import _stale_layer_hint

        assert _stale_layer_hint(["l2"]) == "L2"

    def test_l0_and_l2(self) -> None:
        """L0+L2 stale → L0/L2."""
        from terok.tui.widgets.project_state import _stale_layer_hint

        assert _stale_layer_hint(["l0", "l2"]) == "L0/L2"

    def test_empty_returns_empty(self) -> None:
        """No stale layers → empty string."""
        from terok.tui.widgets.project_state import _stale_layer_hint

        assert _stale_layer_hint([]) == ""
