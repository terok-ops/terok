# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Blablador wrapper script and Dockerfile integration."""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from terok.lib.containers.docker import generate_dockerfiles
from terok.lib.core.config import build_root
from test_utils import make_mock_http_response, project_env
from testfs import CONTAINER_INSTRUCTIONS_PATH

BLABLADOR_BASE_URL = "https://api.helmholtz-blablador.fz-juelich.de/v1"
TEST_API_KEY = "test-api-key"


def blablador_script_path() -> Path:
    """Return the path to the Blablador wrapper script."""
    return (
        Path(__file__).parent.parent.parent
        / "src"
        / "terok"
        / "resources"
        / "scripts"
        / "blablador"
    )


def load_blablador_module():
    """Load the wrapper script as a Python module."""
    script_path = blablador_script_path()
    loader = importlib.machinery.SourceFileLoader("blablador", str(script_path))
    spec = importlib.util.spec_from_file_location("blablador", script_path, loader=loader)
    if spec is None:
        raise ImportError(f"Could not load spec from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["blablador"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def blablador_module():
    """Load the Blablador script as an isolated module for one test."""
    sys.modules.pop("blablador", None)
    module = load_blablador_module()
    yield module
    sys.modules.pop("blablador", None)


def build_update(
    blablador_module,
    *,
    base_url: str = BLABLADOR_BASE_URL,
    api_key: str = TEST_API_KEY,
    model: str = "alias-huge",
    models: list[str] | None = None,
) -> dict:
    """Create a Blablador config fragment using the script's real helper."""
    return blablador_module._build_blablador_update(base_url, api_key, model, models or [model])


def l1_cli_artifacts(project_id: str) -> tuple[str, bool, str]:
    """Generate L1 CLI artifacts and return Dockerfile, wrapper presence, and hilfe text."""
    yaml_text = (
        f"project:\n  id: {project_id}\n"
        "git:\n"
        "  upstream_url: https://example.com/repo.git\n"
        "  default_branch: main\n"
    )
    with project_env(yaml_text, project_id=project_id):
        generate_dockerfiles(project_id)
        out_dir = build_root() / project_id
        return (
            (out_dir / "L1.cli.Dockerfile").read_text(encoding="utf-8"),
            (out_dir / "scripts" / "blablador").is_file(),
            (out_dir / "scripts" / "hilfe").read_text(encoding="utf-8"),
        )


def seed_opencode_config(base_dir: Path, content: dict) -> Path:
    """Write a seeded OpenCode config and return its path."""
    config_path = base_dir / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(content), encoding="utf-8")
    return config_path


def test_script_is_valid_python() -> None:
    """The wrapper script parses as valid Python source."""
    ast.parse(blablador_script_path().read_text(encoding="utf-8"))


def test_script_has_shebang() -> None:
    """The wrapper script starts with a Python shebang."""
    assert blablador_script_path().read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(
            {
                "data": [
                    {"id": "model-1", "object": "model"},
                    {"id": "model-2", "object": "model"},
                    {"id": "model-3", "object": "model"},
                ]
            },
            ["model-1", "model-2", "model-3"],
            id="data-array",
        ),
        pytest.param(
            {"models": [{"id": "custom-model-1"}, {"id": "custom-model-2"}]},
            ["custom-model-1", "custom-model-2"],
            id="models-array",
        ),
        pytest.param(
            {
                "data": [
                    {"id": "zebra-model"},
                    {"id": "alpha-model"},
                    {"id": "zebra-model"},
                    {"id": "beta-model"},
                ]
            },
            ["alpha-model", "beta-model", "zebra-model"],
            id="deduplicates-and-sorts",
        ),
    ],
)
def test_fetch_models_success(
    blablador_module, payload: dict[str, object], expected: list[str]
) -> None:
    """Model fetching supports both response shapes and normalizes the returned IDs."""
    with patch("blablador.request.urlopen", return_value=make_mock_http_response(payload)):
        assert blablador_module._fetch_models(BLABLADOR_BASE_URL, TEST_API_KEY) == expected


def test_fetch_models_returns_none_on_error(blablador_module) -> None:
    """API failures return ``None`` instead of bubbling exceptions up."""
    with patch(
        "blablador.request.urlopen",
        side_effect=blablador_module.error.URLError("Connection failed"),
    ):
        assert blablador_module._fetch_models(BLABLADOR_BASE_URL, TEST_API_KEY) is None


@pytest.mark.parametrize(
    ("model", "models"),
    [
        pytest.param("test-model", ["test-model", "other-model"], id="basic"),
        pytest.param("alias-code", ["alias-code", "other-model"], id="json-structure"),
    ],
)
def test_build_blablador_update(blablador_module, model: str, models: list[str]) -> None:
    """The generated config fragment has the expected OpenCode provider structure."""
    config = build_update(
        blablador_module,
        base_url=BLABLADOR_BASE_URL,
        api_key="test-api-key-456",
        model=model,
        models=models,
    )
    parsed = json.loads(json.dumps(config, indent=2))
    provider = parsed["provider"]["blablador"]

    assert parsed["$schema"] == "https://opencode.ai/config.json"
    assert parsed["model"] == f"blablador/{model}"
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["name"] == "Helmholtz Blablador"
    assert provider["options"]["baseURL"] == BLABLADOR_BASE_URL
    assert provider["options"]["apiKey"] == "test-api-key-456"
    assert parsed["permission"]["*"] == "allow"
    for model_id in models:
        assert model_id in provider["models"]


@pytest.mark.parametrize(
    ("project_id", "dockerfile_snippets", "hilfe_snippets"),
    [
        pytest.param(
            "proj_blablador_test",
            ["COPY scripts/blablador /usr/local/bin/blablador"],
            [],
            id="installs-wrapper",
        ),
        pytest.param(
            "proj_blablador_list_test",
            ["blablador"],
            ["Helmholtz Blablador"],
            id="blablador-in-agents-list",
        ),
        pytest.param(
            "proj_opencode_test",
            ["opencode.ai/install"],
            [],
            id="installs-opencode",
        ),
        pytest.param(
            "proj_opencode_list_test",
            ["opencode"],
            ["OpenCode CLI"],
            id="opencode-in-agents-list",
        ),
    ],
)
def test_l1_cli_dockerfile_integration(
    project_id: str,
    dockerfile_snippets: list[str],
    hilfe_snippets: list[str],
) -> None:
    """Generated CLI artifacts advertise the expected Blablador/OpenCode integrations."""
    dockerfile, _, hilfe = l1_cli_artifacts(project_id)
    for snippet in dockerfile_snippets:
        assert snippet in dockerfile
    for snippet in hilfe_snippets:
        assert snippet in hilfe


def test_l1_cli_blablador_script_copied() -> None:
    """Dockerfile generation copies the wrapper script into the build context."""
    _, script_present, _ = l1_cli_artifacts("proj_script_copy_test")
    assert script_present


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        pytest.param(
            {
                "provider": {
                    "blablador": {
                        "models": {
                            "model-a": {"name": "Model A"},
                            "model-b": {"name": "Model B"},
                        }
                    }
                }
            },
            {"model-a", "model-b"},
            id="configured-models",
        ),
        pytest.param(None, set(), id="none"),
        pytest.param({}, set(), id="missing-provider"),
        pytest.param({"provider": {}}, set(), id="empty-provider"),
    ],
)
def test_get_configured_models(blablador_module, config: dict | None, expected: set[str]) -> None:
    """Configured-model extraction handles missing config and valid provider maps."""
    assert blablador_module._get_configured_models(config) == expected


@pytest.mark.parametrize(
    ("seed", "expected"),
    [
        pytest.param(None, None, id="missing-file"),
        pytest.param(
            {"model": "blablador/test", "provider": {"blablador": {}}},
            {"model": "blablador/test", "provider": {"blablador": {}}},
            id="parsed-json",
        ),
    ],
)
def test_load_opencode_config(blablador_module, seed: dict | None, expected: dict | None) -> None:
    """OpenCode config loading returns parsed JSON or ``None`` when missing."""
    with tempfile.TemporaryDirectory() as td:
        config_path = Path(td) / "opencode.json"
        if seed is not None:
            config_path.write_text(json.dumps(seed), encoding="utf-8")
        with patch.object(blablador_module, "_opencode_config_path", return_value=config_path):
            assert blablador_module._load_opencode_config() == expected


def test_write_opencode_config_creates_directories(blablador_module) -> None:
    """Writing OpenCode config creates parent directories when needed."""
    with tempfile.TemporaryDirectory() as td:
        config_path = Path(td) / "nested" / "dir" / "opencode.json"
        with patch.object(blablador_module, "_opencode_config_path", return_value=config_path):
            blablador_module._write_opencode_config({"test": "config"})

        assert config_path.exists()
        assert json.loads(config_path.read_text()) == {"test": "config"}


def test_opencode_config_path_under_blablador_dir(blablador_module) -> None:
    """Blablador uses its own OpenCode config directory under ``~/.blablador``."""
    config_path = blablador_module._opencode_config_path()
    home = Path.home()
    assert config_path.resolve().is_relative_to((home / ".blablador").resolve())
    assert config_path.name == "opencode.json"
    assert config_path != home / ".config" / "opencode" / "opencode.json"


def test_main_passes_opencode_config_env(blablador_module) -> None:
    """Running the wrapper passes ``OPENCODE_CONFIG`` to the spawned OpenCode process."""
    mock_response = make_mock_http_response({"data": [{"id": "alias-huge"}]})
    with tempfile.TemporaryDirectory() as td:
        config_path = Path(td) / "opencode" / "opencode.json"
        with (
            patch.object(blablador_module, "_load_api_key", return_value="fake-key"),
            patch("blablador.request.urlopen", return_value=mock_response),
            patch.object(blablador_module, "_opencode_config_path", return_value=config_path),
            patch("blablador.subprocess.call", return_value=0) as mock_call,
            patch("sys.argv", ["blablador"]),
        ):
            blablador_module.main()

    env = mock_call.call_args.kwargs["env"]
    assert env["OPENCODE_CONFIG"] == str(config_path)


def test_options_refresh_when_fetch_fails(blablador_module) -> None:
    """Credential/base-URL changes still rewrite config when model fetch fails."""
    with tempfile.TemporaryDirectory() as td:
        config_path = Path(td) / "opencode" / "opencode.json"
        old_config = build_update(
            blablador_module,
            base_url="https://old-url/v1",
            api_key="old-key",
            model="alias-huge",
            models=["alias-huge", "alias-code"],
        )
        seed_opencode_config(Path(td), old_config)

        with (
            patch.object(blablador_module, "_load_api_key", return_value="new-key"),
            patch(
                "blablador.request.urlopen",
                side_effect=blablador_module.error.URLError("unreachable"),
            ),
            patch.object(blablador_module, "_opencode_config_path", return_value=config_path),
            patch("blablador.subprocess.call", return_value=0),
            patch("sys.argv", ["blablador"]),
        ):
            blablador_module.main()

        updated = json.loads(config_path.read_text(encoding="utf-8"))
        options = updated["provider"]["blablador"]["options"]
        assert options["apiKey"] == "new-key"
        assert {"alias-huge", "alias-code"} <= set(updated["provider"]["blablador"]["models"])


@pytest.mark.parametrize(
    ("existing", "expected_permission", "expected_model"),
    [
        pytest.param(
            {"instructions": [str(CONTAINER_INSTRUCTIONS_PATH)]},
            {"*": "allow"},
            "blablador/alias-huge",
            id="preserves-instructions",
        ),
        pytest.param(
            {"permission": {"Bash(*)": "deny"}},
            {"Bash(*)": "deny"},
            "blablador/alias-huge",
            id="preserves-existing-permission",
        ),
        pytest.param(
            {"model": "other-provider/custom-model"},
            {"*": "allow"},
            "other-provider/custom-model",
            id="preserves-non-blablador-model",
        ),
        pytest.param(
            {"model": "blablador/old-model"},
            {"*": "allow"},
            "blablador/alias-huge",
            id="updates-blablador-model",
        ),
    ],
)
def test_merge_blablador_config_core_behaviour(
    blablador_module,
    existing: dict,
    expected_permission: dict[str, str],
    expected_model: str,
) -> None:
    """Config merging preserves unrelated settings while refreshing the Blablador provider."""
    merged = blablador_module._merge_blablador_config(
        existing,
        build_update(blablador_module, base_url="https://api.example.com/v1", api_key="key"),
    )
    assert merged["permission"] == expected_permission
    assert merged["model"] == expected_model
    if "instructions" in existing:
        assert merged["instructions"] == existing["instructions"]


def test_merge_preserves_other_providers(blablador_module) -> None:
    """Merging Blablador config keeps unrelated provider entries intact."""
    merged = blablador_module._merge_blablador_config(
        {"provider": {"other-provider": {"npm": "other-npm", "models": {}}}},
        build_update(blablador_module, base_url="https://api.example.com/v1", api_key="key"),
    )
    assert {"other-provider", "blablador"} <= set(merged["provider"])


def test_merge_updates_blablador_provider(blablador_module) -> None:
    """Merging replaces the Blablador provider section wholesale."""
    merged = blablador_module._merge_blablador_config(
        {
            "provider": {
                "blablador": {"npm": "old-npm", "models": {"old-model": {"name": "old"}}},
            }
        },
        build_update(
            blablador_module,
            base_url="https://api.example.com/v1",
            api_key="new-key",
        ),
    )
    provider = merged["provider"]["blablador"]
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["options"]["apiKey"] == "new-key"


@pytest.mark.parametrize(
    ("schema", "should_warn"),
    [
        pytest.param("https://example.com/wrong.json", True, id="schema-mismatch"),
        pytest.param("https://opencode.ai/config.json", False, id="schema-match"),
    ],
)
def test_merge_schema_warning(blablador_module, schema: str, should_warn: bool) -> None:
    """Schema mismatches print a warning and are overwritten to the expected value."""
    with patch("builtins.print") as mock_print:
        merged = blablador_module._merge_blablador_config(
            {"$schema": schema},
            build_update(blablador_module, base_url="https://api.example.com/v1", api_key="key"),
        )

    assert mock_print.called is should_warn
    assert merged["$schema"] == "https://opencode.ai/config.json"
    if should_warn:
        assert "unexpected $schema" in mock_print.call_args.args[0]


def test_main_preserves_instructions_on_update(blablador_module) -> None:
    """Updating config through ``main()`` preserves existing ``instructions`` entries."""
    mock_response = make_mock_http_response({"data": [{"id": "alias-huge"}]})

    with tempfile.TemporaryDirectory() as td:
        config_path = seed_opencode_config(
            Path(td),
            {
                "instructions": [str(CONTAINER_INSTRUCTIONS_PATH)],
                "provider": {
                    "blablador": {
                        "options": {"baseURL": "https://old/v1", "apiKey": "old"},
                        "models": {},
                    }
                },
            },
        )
        with (
            patch.object(blablador_module, "_load_api_key", return_value="new-key"),
            patch("blablador.request.urlopen", return_value=mock_response),
            patch.object(blablador_module, "_opencode_config_path", return_value=config_path),
            patch("blablador.subprocess.call", return_value=0),
            patch("sys.argv", ["blablador"]),
        ):
            blablador_module.main()

        updated = json.loads(config_path.read_text(encoding="utf-8"))
    assert updated["instructions"] == [str(CONTAINER_INSTRUCTIONS_PATH)]
    assert updated["provider"]["blablador"]["options"]["apiKey"] == "new-key"
