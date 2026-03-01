# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Blablador wrapper script and Dockerfile integration.

These tests verify that:
1. The blablador wrapper script is syntactically correct Python
2. The L1 CLI Dockerfile includes the blablador alias
3. The blablador alias has proper git author/committer configuration
"""

import ast
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from luskctl.lib.containers.docker import generate_dockerfiles
from luskctl.lib.core.config import build_root
from test_utils import make_mock_http_response, project_env


def get_blablador_script_path() -> Path:
    """Get the path to the blablador wrapper script."""
    return (
        Path(__file__).parent.parent.parent
        / "src"
        / "luskctl"
        / "resources"
        / "scripts"
        / "blablador"
    )


def load_blablador_module():
    """Load the blablador script as a Python module."""
    script_path = get_blablador_script_path()

    # Create a custom loader for scripts without .py extension
    loader = importlib.machinery.SourceFileLoader("blablador", str(script_path))
    spec = importlib.util.spec_from_file_location("blablador", script_path, loader=loader)
    if spec is None:
        raise ImportError(f"Could not load spec from {script_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["blablador"] = module
    spec.loader.exec_module(module)
    return module


class BlabladorScriptTests(unittest.TestCase):
    """Tests for the blablador wrapper script."""

    def setUp(self) -> None:
        self.script_path = get_blablador_script_path()
        self.assertTrue(
            self.script_path.exists(),
            f"Blablador script must exist at {self.script_path}. "
            "This is a required artifact for the feature.",
        )
        # Clean up any previously loaded blablador module for test isolation
        if "blablador" in sys.modules:
            del sys.modules["blablador"]

    def tearDown(self) -> None:
        # Clean up the blablador module after each test
        if "blablador" in sys.modules:
            del sys.modules["blablador"]

    def test_script_is_valid_python(self) -> None:
        """Verify that the blablador script is syntactically valid Python."""
        source = self.script_path.read_text(encoding="utf-8")
        # This will raise SyntaxError if the script is invalid
        ast.parse(source)

    def test_script_has_shebang(self) -> None:
        """Verify the script has a proper Python shebang."""
        content = self.script_path.read_text(encoding="utf-8")
        self.assertTrue(
            content.startswith("#!/usr/bin/env python3"),
            "Script should start with #!/usr/bin/env python3",
        )

    def test_fetch_models_with_data_array(self) -> None:
        """Test _fetch_models with OpenAI-compatible 'data' array response."""
        blablador = load_blablador_module()

        mock_response = make_mock_http_response(
            {
                "data": [
                    {"id": "model-1", "object": "model"},
                    {"id": "model-2", "object": "model"},
                    {"id": "model-3", "object": "model"},
                ]
            }
        )

        with unittest.mock.patch("blablador.request.urlopen", return_value=mock_response):
            models = blablador._fetch_models(
                "https://api.helmholtz-blablador.fz-juelich.de/v1", "test-api-key"
            )

        self.assertEqual(models, ["model-1", "model-2", "model-3"])

    def test_fetch_models_with_models_array(self) -> None:
        """Test _fetch_models with alternative 'models' array response format."""
        blablador = load_blablador_module()

        mock_response = make_mock_http_response(
            {"models": [{"id": "custom-model-1"}, {"id": "custom-model-2"}]}
        )

        with unittest.mock.patch("blablador.request.urlopen", return_value=mock_response):
            models = blablador._fetch_models(
                "https://api.helmholtz-blablador.fz-juelich.de/v1", "test-api-key"
            )

        self.assertEqual(models, ["custom-model-1", "custom-model-2"])

    def test_fetch_models_deduplicates_and_sorts(self) -> None:
        """Test that _fetch_models deduplicates and sorts model IDs."""
        blablador = load_blablador_module()

        mock_response = make_mock_http_response(
            {
                "data": [
                    {"id": "zebra-model"},
                    {"id": "alpha-model"},
                    {"id": "zebra-model"},  # duplicate
                    {"id": "beta-model"},
                ]
            }
        )

        with unittest.mock.patch("blablador.request.urlopen", return_value=mock_response):
            models = blablador._fetch_models(
                "https://api.helmholtz-blablador.fz-juelich.de/v1", "test-api-key"
            )

        self.assertEqual(models, ["alpha-model", "beta-model", "zebra-model"])

    def test_fetch_models_returns_none_on_error(self) -> None:
        """Test that _fetch_models returns None on API error."""
        blablador = load_blablador_module()

        with unittest.mock.patch(
            "blablador.request.urlopen",
            side_effect=blablador.error.URLError("Connection failed"),
        ):
            result = blablador._fetch_models(
                "https://api.helmholtz-blablador.fz-juelich.de/v1", "test-api-key"
            )

        self.assertIsNone(result)

    def test_build_config_structure(self) -> None:
        """Test that _build_config generates correct OpenCode configuration."""
        blablador = load_blablador_module()

        config = blablador._build_config(
            base_url="https://api.helmholtz-blablador.fz-juelich.de/v1",
            api_key="test-key-123",
            model="test-model",
            models=["test-model", "other-model"],
        )

        # Verify schema and model
        self.assertEqual(config["$schema"], "https://opencode.ai/config.json")
        self.assertEqual(config["model"], "blablador/test-model")

        # Verify provider configuration
        self.assertIn("blablador", config["provider"])
        provider = config["provider"]["blablador"]
        self.assertEqual(provider["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(provider["name"], "Helmholtz Blablador")
        self.assertEqual(
            provider["options"]["baseURL"], "https://api.helmholtz-blablador.fz-juelich.de/v1"
        )
        self.assertEqual(provider["options"]["apiKey"], "test-key-123")

        # Verify models map includes both models
        self.assertIn("test-model", provider["models"])
        self.assertIn("other-model", provider["models"])

        # Verify permission is set to allow all
        self.assertEqual(config["permission"]["*"], "allow")


class BlabladorDockerfileTests(unittest.TestCase):
    """Tests for Blablador integration in the L1 CLI Dockerfile."""

    def test_l1_cli_has_blablador_binary(self) -> None:
        """Verify that the L1 CLI Dockerfile installs the blablador binary."""
        yaml_text = (
            "project:\n"
            "  id: proj_blablador_test\n"
            "git:\n"
            "  upstream_url: https://example.com/repo.git\n"
            "  default_branch: main\n"
        )
        with project_env(yaml_text, project_id="proj_blablador_test") as _env:
            generate_dockerfiles("proj_blablador_test")
            out_dir = build_root() / "proj_blablador_test"
            l1_cli = out_dir / "L1.cli.Dockerfile"

            content = l1_cli.read_text(encoding="utf-8")

            # Verify blablador binary is installed (wrapper function is in zz-luskctl-project.sh)
            self.assertIn("COPY scripts/blablador /usr/local/bin/blablador", content)

    def test_l1_cli_blablador_in_agents_list(self) -> None:
        """Verify blablador appears in the available agents list."""
        yaml_text = (
            "project:\n"
            "  id: proj_blablador_list_test\n"
            "git:\n"
            "  upstream_url: https://example.com/repo.git\n"
            "  default_branch: main\n"
        )
        with project_env(yaml_text, project_id="proj_blablador_list_test") as _env:
            generate_dockerfiles("proj_blablador_list_test")
            out_dir = build_root() / "proj_blablador_list_test"
            l1_cli = out_dir / "L1.cli.Dockerfile"

            content = l1_cli.read_text(encoding="utf-8")

            # Verify blablador is listed with description
            self.assertIn("blablador", content)
            self.assertIn("Helmholtz Blablador", content)

    def test_l1_cli_opencode_installed(self) -> None:
        """Verify that OpenCode CLI is installed in the L1 CLI image."""
        yaml_text = (
            "project:\n"
            "  id: proj_opencode_test\n"
            "git:\n"
            "  upstream_url: https://example.com/repo.git\n"
            "  default_branch: main\n"
        )
        with project_env(yaml_text, project_id="proj_opencode_test") as _env:
            generate_dockerfiles("proj_opencode_test")
            out_dir = build_root() / "proj_opencode_test"
            l1_cli = out_dir / "L1.cli.Dockerfile"

            content = l1_cli.read_text(encoding="utf-8")

            # Verify OpenCode installation (runs as dev user)
            self.assertIn("opencode.ai/install", content)

    def test_l1_cli_blablador_script_copied(self) -> None:
        """Verify the blablador wrapper script is copied to the image."""
        yaml_text = (
            "project:\n"
            "  id: proj_script_copy_test\n"
            "git:\n"
            "  upstream_url: https://example.com/repo.git\n"
            "  default_branch: main\n"
        )
        with project_env(yaml_text, project_id="proj_script_copy_test") as _env:
            generate_dockerfiles("proj_script_copy_test")
            out_dir = build_root() / "proj_script_copy_test"

            # Verify scripts directory exists and blablador is there
            scripts_dir = out_dir / "scripts"
            self.assertTrue(scripts_dir.is_dir())

            blablador_script = scripts_dir / "blablador"
            self.assertTrue(
                blablador_script.is_file(), f"blablador script not found in {scripts_dir}"
            )


class BlabladorPersistentConfigTests(unittest.TestCase):
    """Tests for persistent configuration management."""

    def setUp(self) -> None:
        if "blablador" in sys.modules:
            del sys.modules["blablador"]

    def tearDown(self) -> None:
        if "blablador" in sys.modules:
            del sys.modules["blablador"]

    def test_get_configured_models_extracts_model_ids(self) -> None:
        """Test that _get_configured_models extracts model IDs from config."""
        blablador = load_blablador_module()

        config = {
            "provider": {
                "blablador": {
                    "models": {
                        "model-a": {"name": "Model A"},
                        "model-b": {"name": "Model B"},
                    }
                }
            }
        }

        models = blablador._get_configured_models(config)
        self.assertEqual(models, {"model-a", "model-b"})

    def test_get_configured_models_returns_empty_for_no_config(self) -> None:
        """Test that _get_configured_models returns empty set for None config."""
        blablador = load_blablador_module()
        self.assertEqual(blablador._get_configured_models(None), set())

    def test_get_configured_models_returns_empty_for_missing_provider(self) -> None:
        """Test that _get_configured_models handles missing provider."""
        blablador = load_blablador_module()
        self.assertEqual(blablador._get_configured_models({}), set())
        self.assertEqual(blablador._get_configured_models({"provider": {}}), set())

    def test_load_opencode_config_returns_none_for_missing_file(self) -> None:
        """Test that _load_opencode_config returns None if file doesn't exist."""
        blablador = load_blablador_module()

        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td)
            with unittest.mock.patch.object(
                blablador, "_opencode_config_path", return_value=fake_home / "nonexistent.json"
            ):
                result = blablador._load_opencode_config()
                self.assertIsNone(result)

    def test_load_opencode_config_returns_parsed_json(self) -> None:
        """Test that _load_opencode_config returns parsed config."""
        blablador = load_blablador_module()

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "opencode.json"
            config_data = {"model": "blablador/test", "provider": {"blablador": {}}}
            config_path.write_text(json.dumps(config_data))

            with unittest.mock.patch.object(
                blablador, "_opencode_config_path", return_value=config_path
            ):
                result = blablador._load_opencode_config()
                self.assertEqual(result, config_data)

    def test_write_opencode_config_creates_directories(self) -> None:
        """Test that _write_opencode_config creates parent directories."""
        blablador = load_blablador_module()

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "nested" / "dir" / "opencode.json"

            with unittest.mock.patch.object(
                blablador, "_opencode_config_path", return_value=config_path
            ):
                blablador._write_opencode_config({"test": "config"})
                self.assertTrue(config_path.exists())
                self.assertEqual(json.loads(config_path.read_text()), {"test": "config"})


class BlabladorConfigTests(unittest.TestCase):
    """Tests for Blablador configuration structure."""

    def test_config_json_structure(self) -> None:
        """Test that _build_config generates valid and correctly structured config."""
        blablador = load_blablador_module()

        # Call the actual _build_config function
        config = blablador._build_config(
            base_url="https://api.helmholtz-blablador.fz-juelich.de/v1",
            api_key="test-api-key-456",
            model="alias-code",
            models=["alias-code", "other-model"],
        )

        # Verify it's valid JSON by serializing and deserializing
        json_str = json.dumps(config, indent=2)
        parsed = json.loads(json_str)

        # Assert on key fields from the actual implementation
        self.assertEqual(parsed["$schema"], "https://opencode.ai/config.json")
        self.assertEqual(parsed["model"], "blablador/alias-code")
        self.assertIn("blablador", parsed["provider"])
        self.assertEqual(parsed["provider"]["blablador"]["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(parsed["provider"]["blablador"]["name"], "Helmholtz Blablador")
        self.assertEqual(
            parsed["provider"]["blablador"]["options"]["baseURL"],
            "https://api.helmholtz-blablador.fz-juelich.de/v1",
        )
        self.assertEqual(parsed["provider"]["blablador"]["options"]["apiKey"], "test-api-key-456")
        self.assertEqual(parsed["permission"]["*"], "allow")

        # Verify model map includes the models we passed
        self.assertIn("alias-code", parsed["provider"]["blablador"]["models"])
        self.assertIn("other-model", parsed["provider"]["blablador"]["models"])


if __name__ == "__main__":
    unittest.main()
