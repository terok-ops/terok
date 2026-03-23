# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the unified OpenCode provider script and host-side registry."""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from tests.test_utils import make_mock_http_response

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "src" / "terok" / "resources" / "scripts" / "opencode-provider"


def _load_as_module(argv0: str = "blablador") -> ModuleType:
    """Load the opencode-provider script as a module, simulating the given argv[0]."""
    loader = importlib.machinery.SourceFileLoader("opencode_provider", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_file_location("opencode_provider", SCRIPT_PATH, loader=loader)
    if spec is None:
        raise ImportError(f"Could not load spec from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    with patch("sys.argv", [argv0]):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["blablador", "kisski"])
def provider_module(request):
    """Load the opencode-provider script simulating different provider names."""
    sys.modules.pop("opencode_provider", None)
    mod = _load_as_module(request.param)
    with patch("sys.argv", [request.param]):
        yield mod, request.param
    sys.modules.pop("opencode_provider", None)


# -- Script validity ----------------------------------------------------------


class TestScriptValidity:
    """Verify the opencode-provider script is well-formed."""

    def test_valid_python(self) -> None:
        """Script parses as valid Python."""
        ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))

    def test_has_shebang(self) -> None:
        """Script starts with a Python shebang."""
        assert SCRIPT_PATH.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")

    def test_no_terok_imports(self) -> None:
        """Script must not import from the terok package (runs in containers)."""
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        assert "from terok" not in content
        assert "import terok" not in content


# -- Host-side env var collection ---------------------------------------------


class TestCollectProviderEnv:
    """Tests for the host-side collect_opencode_provider_env function."""

    def test_contains_blablador_and_kisski(self) -> None:
        """Env dict has entries for all registered OpenCode providers."""
        from terok_agent import collect_opencode_provider_env

        env = collect_opencode_provider_env()
        assert "TEROK_OC_BLABLADOR_BASE_URL" in env
        assert "TEROK_OC_BLABLADOR_DISPLAY_NAME" in env
        assert "TEROK_OC_KISSKI_BASE_URL" in env
        assert "TEROK_OC_KISSKI_DISPLAY_NAME" in env

    def test_values_match_registry(self) -> None:
        """Env var values match the HeadlessProvider registry."""
        from terok_agent import collect_opencode_provider_env

        env = collect_opencode_provider_env()
        assert env["TEROK_OC_BLABLADOR_BASE_URL"] == (
            "https://api.helmholtz-blablador.fz-juelich.de/v1"
        )
        assert env["TEROK_OC_KISSKI_PREFERRED_MODEL"] == "devstral-2-123b-instruct-2512"

    def test_to_env_all_fields_present(self) -> None:
        """OpenCodeProviderConfig.to_env() emits all required fields."""
        from terok_agent import OpenCodeProviderConfig

        cfg = OpenCodeProviderConfig(
            display_name="Test",
            base_url="https://test/v1",
            preferred_model="m1",
            fallback_model="m2",
            env_var_prefix="TEST",
            config_dir=".test",
            auth_key_url="https://test/keys",
        )
        env = cfg.to_env("test")
        assert env == {
            "TEROK_OC_TEST_BASE_URL": "https://test/v1",
            "TEROK_OC_TEST_PREFERRED_MODEL": "m1",
            "TEROK_OC_TEST_FALLBACK_MODEL": "m2",
            "TEROK_OC_TEST_DISPLAY_NAME": "Test",
            "TEROK_OC_TEST_ENV_VAR_PREFIX": "TEST",
            "TEROK_OC_TEST_CONFIG_DIR": ".test",
        }


# -- Host-side dynamic mounts and auth ----------------------------------------


class TestDynamicRegistration:
    """Tests for dynamic SharedMount and AuthProvider generation."""

    def test_shared_mounts_include_opencode_providers(self) -> None:
        """SHARED_MOUNTS includes dynamically generated provider mounts."""
        from terok.lib.orchestration.environment import SHARED_MOUNTS

        mount_keys = {m.key for m in SHARED_MOUNTS}
        assert "blablador" in mount_keys
        assert "kisski" in mount_keys

    def test_shared_mount_paths(self) -> None:
        """Provider mounts use correct host and container paths."""
        from terok.lib.orchestration.environment import SHARED_MOUNTS

        blablador = next(m for m in SHARED_MOUNTS if m.key == "blablador")
        assert blablador.host_dir_suffix == "_blablador-config"
        assert blablador.container_path == "/home/dev/.blablador"

    def test_auth_providers_include_opencode_providers(self) -> None:
        """AUTH_PROVIDERS includes dynamically generated OpenCode provider entries."""
        from terok_agent import AUTH_PROVIDERS

        assert "blablador" in AUTH_PROVIDERS
        assert "kisski" in AUTH_PROVIDERS

    def test_auth_provider_key_url(self) -> None:
        """Auth provider key URLs come from the OpenCodeProviderConfig registry."""
        from terok_agent import AUTH_PROVIDERS

        blablador = AUTH_PROVIDERS["blablador"]
        assert "helmholtz" in blablador.banner_hint.lower() or "codebase" in blablador.banner_hint


# -- Provider resolution (in-process) ----------------------------------------


class TestProviderResolution:
    """Tests for provider config resolution inside the script."""

    @pytest.mark.parametrize(
        ("name", "expected_url"),
        [
            ("blablador", "https://api.helmholtz-blablador.fz-juelich.de/v1"),
            ("kisski", "https://chat-ai.academiccloud.de/v1"),
        ],
    )
    def test_fallback_provider_base_url(self, name: str, expected_url: str) -> None:
        """With no env vars, fallback config returns correct base URL."""
        mod = _load_as_module(name)
        with patch("sys.argv", [name]):
            config = mod._resolve_provider_config()
        assert config["base_url"] == expected_url

    def test_env_var_override(self) -> None:
        """TEROK_OC_* env vars take precedence over fallbacks."""
        env_patch = {
            "TEROK_OC_BLABLADOR_BASE_URL": "https://custom.example.com/v1",
            "TEROK_OC_BLABLADOR_DISPLAY_NAME": "Custom Blablador",
            "TEROK_OC_BLABLADOR_PREFERRED_MODEL": "custom-model",
            "TEROK_OC_BLABLADOR_FALLBACK_MODEL": "fallback",
            "TEROK_OC_BLABLADOR_ENV_VAR_PREFIX": "BLABLADOR",
            "TEROK_OC_BLABLADOR_CONFIG_DIR": ".blablador",
        }
        mod = _load_as_module("blablador")
        with patch.dict("os.environ", env_patch), patch("sys.argv", ["blablador"]):
            config = mod._resolve_provider_config()
        assert config["base_url"] == "https://custom.example.com/v1"
        assert config["display_name"] == "Custom Blablador"

    def test_unknown_provider_exits(self) -> None:
        """Unknown provider name without env vars raises SystemExit."""
        mod = _load_as_module("blablador")
        with (
            patch("sys.argv", ["unknown-agent"]),
            pytest.raises(SystemExit, match="Unknown provider"),
        ):
            mod._resolve_provider_config()


# -- Non-dict JSON resilience -------------------------------------------------


class TestNonDictJsonResilience:
    """Ensure the script handles non-object JSON roots gracefully."""

    def test_load_api_key_ignores_array_config(self) -> None:
        """``config.json`` containing a JSON array returns None."""
        mod = _load_as_module("blablador")
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.json"
            cfg.write_text("[]", encoding="utf-8")
            config = {"config_dir": td, "env_var_prefix": "TEST"}
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("TEST_API_KEY", None)
                assert mod._load_api_key(config) is None

    def test_load_opencode_config_ignores_array(self) -> None:
        """``opencode.json`` containing a JSON array returns None."""
        mod = _load_as_module("blablador")
        with tempfile.TemporaryDirectory() as td:
            oc_path = Path(td) / "opencode.json"
            oc_path.write_text("[]", encoding="utf-8")
            config = {"config_dir": ".blablador"}
            with patch.object(mod, "_opencode_config_path", return_value=oc_path):
                assert mod._load_opencode_config(config) is None


# -- Model fetching -----------------------------------------------------------


class TestFetchModels:
    """Tests for model discovery from provider APIs."""

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            pytest.param(
                {"data": [{"id": "model-a"}, {"id": "model-b"}]},
                ["model-a", "model-b"],
                id="openai-data-array",
            ),
            pytest.param(
                {"models": [{"id": "x"}, {"id": "y"}]},
                ["x", "y"],
                id="alt-models-array",
            ),
            pytest.param(
                {"data": [{"id": "z"}, {"id": "a"}, {"id": "z"}]},
                ["a", "z"],
                id="deduplicates-and-sorts",
            ),
        ],
    )
    def test_success(self, provider_module, payload: dict, expected: list[str]) -> None:
        """Model fetching supports multiple response shapes."""
        mod, _ = provider_module
        with patch.object(mod.request, "urlopen", return_value=make_mock_http_response(payload)):
            assert mod._fetch_models("https://example.com/v1", "key") == expected


# -- Config build and merge ---------------------------------------------------


class TestConfigMerge:
    """Tests for opencode.json config building and merging."""

    def test_build_update_structure(self, provider_module) -> None:
        """Built config fragment has expected provider structure."""
        mod, name = provider_module
        config = mod._resolve_provider_config()
        update = mod._build_provider_update(config, "https://example.com/v1", "key", "model", None)
        assert update["$schema"] == "https://opencode.ai/config.json"
        assert update["model"] == f"{name}/model"
        assert name in update["provider"]
        assert update["permission"] == {"*": "allow"}

    def test_merge_preserves_instructions(self, provider_module) -> None:
        """Merging preserves existing instructions entries."""
        mod, _ = provider_module
        config = mod._resolve_provider_config()
        update = mod._build_provider_update(config, "https://ex.com/v1", "key", "m", None)
        existing = {"instructions": ["/tmp/instructions.md"]}
        merged = mod._merge_provider_config(existing, update, config)
        assert merged["instructions"] == ["/tmp/instructions.md"]

    def test_merge_preserves_other_providers(self, provider_module) -> None:
        """Merging keeps unrelated provider entries."""
        mod, name = provider_module
        config = mod._resolve_provider_config()
        update = mod._build_provider_update(config, "https://ex.com/v1", "key", "m", None)
        existing = {"provider": {"other": {"npm": "other-npm"}}}
        merged = mod._merge_provider_config(existing, update, config)
        assert "other" in merged["provider"]
        assert name in merged["provider"]


# -- CLI invocation -----------------------------------------------------------


class TestCLI:
    """Tests for the script's CLI behavior via subprocess."""

    def test_unknown_provider_exits(self) -> None:
        """Invoking as opencode-provider (no symlink) exits with error."""
        result = subprocess.run(
            ["python3", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "Unknown provider" in result.stderr

    def test_missing_api_key_error_message(self) -> None:
        """Missing API key produces a valid JSON example in the error."""
        mod = _load_as_module("blablador")
        with (
            patch.object(mod, "_load_api_key", return_value=None),
            patch("sys.argv", ["blablador"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()
        msg = str(exc_info.value)
        assert "BLABLADOR_API_KEY" in msg
        assert '{"api_key": "..."}' in msg

    def test_model_fallback_when_both_defaults_gone(self) -> None:
        """When both preferred and fallback models are gone, picks the first available."""
        mod = _load_as_module("blablador")
        mock_resp = make_mock_http_response(
            {"data": [{"id": "new-model-x"}, {"id": "new-model-y"}]}
        )
        with tempfile.TemporaryDirectory() as td:
            oc_config = Path(td) / "opencode" / "opencode.json"
            with (
                patch.object(mod, "_load_api_key", return_value="key"),
                patch.object(mod.request, "urlopen", return_value=mock_resp),
                patch.object(mod, "_opencode_config_path", return_value=oc_config),
                patch("subprocess.call", return_value=0),
                patch("sys.argv", ["blablador"]),
            ):
                mod.main()
            import json

            written = json.loads(oc_config.read_text(encoding="utf-8"))
            # Should have picked one of the available models, not alias-huge or alias-code
            assert written["model"].startswith("blablador/new-model-")

    def test_schema_warning_goes_to_stderr(self) -> None:
        """Schema mismatch warning is printed to stderr, not stdout."""
        mod = _load_as_module("blablador")
        with patch("sys.argv", ["blablador"]):
            config = mod._resolve_provider_config()
        update = mod._build_provider_update(config, "https://ex.com/v1", "k", "m", None)
        with patch("sys.stderr") as mock_stderr:
            mod._merge_provider_config({"$schema": "https://wrong.schema"}, update, config)
        mock_stderr.write.assert_called()

    def test_merge_preserves_foreign_model(self) -> None:
        """Merging does not overwrite a model belonging to another provider."""
        mod = _load_as_module("blablador")
        with patch("sys.argv", ["blablador"]):
            config = mod._resolve_provider_config()
        update = mod._build_provider_update(config, "https://ex.com/v1", "k", "m", None)
        existing = {"model": "openai/gpt-4"}
        merged = mod._merge_provider_config(existing, update, config)
        assert merged["model"] == "openai/gpt-4"

    def test_get_configured_options(self) -> None:
        """Options extraction returns baseURL and apiKey from existing config."""
        mod = _load_as_module("blablador")
        with patch("sys.argv", ["blablador"]):
            config = mod._resolve_provider_config()
        existing = {
            "provider": {"blablador": {"options": {"baseURL": "https://x/v1", "apiKey": "k"}}}
        }
        opts = mod._get_configured_options(config, existing)
        assert opts["baseURL"] == "https://x/v1"
        assert opts["apiKey"] == "k"

    def test_get_configured_options_missing(self) -> None:
        """Options extraction returns empty dict when provider is absent."""
        mod = _load_as_module("blablador")
        with patch("sys.argv", ["blablador"]):
            config = mod._resolve_provider_config()
        assert mod._get_configured_options(config, None) == {}
        assert mod._get_configured_options(config, {}) == {}

    def test_degraded_first_run_when_fetch_models_returns_none(self) -> None:
        """First run succeeds even when _fetch_models returns None (API unreachable)."""
        sys.modules.pop("opencode_provider", None)
        mod = _load_as_module("blablador")
        with tempfile.TemporaryDirectory() as td:
            oc_config = Path(td) / "opencode" / "opencode.json"
            with (
                patch.object(mod, "_load_api_key", return_value="fake-key"),
                patch.object(mod.request, "urlopen", side_effect=mod.error.URLError("unreachable")),
                patch.object(mod, "_opencode_config_path", return_value=oc_config),
                patch("subprocess.call", return_value=0),
                patch("sys.argv", ["blablador"]),
            ):
                result = mod.main()
            assert result == 0
            assert oc_config.exists()
        sys.modules.pop("opencode_provider", None)
