# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ACP wrapper scripts and toad TOML patching."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from terok.lib.containers.docker import generate_dockerfiles
from terok.lib.core.config import build_root
from tests.test_utils import project_env

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "src" / "terok" / "resources" / "scripts"

# Wrappers that intercept upstream ACP adapters (terok- prefix).
TEROK_ACP_WRAPPERS = [
    "terok-claude-acp",
    "terok-codex-acp",
    "terok-opencode-acp",
    "terok-copilot-acp",
    "terok-vibe-acp",
]

# All scripts that source terok-acp-env.sh (including opencode-provider-acp).
ALL_ACP_SCRIPTS = TEROK_ACP_WRAPPERS + ["terok-acp-env.sh", "opencode-provider-acp"]


def _patch_fn(call: str) -> str:
    """Return a bash snippet defining _patch_run_command and then executing *call*.

    Extracts the real function from the toad script to keep tests in sync.
    """
    toad_src = (SCRIPTS_DIR / "toad").read_text(encoding="utf-8")
    start = toad_src.index("_patch_run_command() {")
    end = toad_src.index("\n}", start) + 2
    return toad_src[start:end] + "\n" + call


# -- Script validity ----------------------------------------------------------


class TestAcpScriptSyntax:
    """All ACP wrapper scripts must be syntactically valid bash."""

    @pytest.mark.parametrize("script", ALL_ACP_SCRIPTS)
    def test_valid_bash_syntax(self, script: str) -> None:
        """Verify the script passes bash -n (syntax check)."""
        path = SCRIPTS_DIR / script
        assert path.exists(), f"Script not found: {path}"
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}"

    @pytest.mark.parametrize("script", ALL_ACP_SCRIPTS)
    def test_has_spdx_header(self, script: str) -> None:
        """Every script has an SPDX copyright and license header."""
        content = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
        assert "SPDX-FileCopyrightText:" in content
        # REUSE-IgnoreStart
        assert "SPDX-License-Identifier: Apache-2.0" in content
        # REUSE-IgnoreEnd


class TestAcpWrapperStructure:
    """ACP wrappers follow the expected pattern."""

    @pytest.mark.parametrize("script", TEROK_ACP_WRAPPERS)
    def test_sources_shared_env(self, script: str) -> None:
        """Each wrapper sources the shared terok-acp-env.sh."""
        content = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
        assert "terok-acp-env.sh" in content

    @pytest.mark.parametrize("script", TEROK_ACP_WRAPPERS)
    def test_sets_agent_identity(self, script: str) -> None:
        """Each wrapper defines _AGENT_NAME and _AGENT_EMAIL on separate lines."""
        content = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
        assert re.search(r'^_AGENT_NAME="[^"]+"$', content, re.MULTILINE)
        assert re.search(r'^_AGENT_EMAIL="[^"]+"$', content, re.MULTILINE)

    @pytest.mark.parametrize("script", TEROK_ACP_WRAPPERS)
    def test_does_not_write_shared_dirs(self, script: str) -> None:
        """Wrappers must not write to shared config dirs (mkdir, cp, printf >)."""
        content = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
        assert "mkdir " not in content, f"{script}: must not mkdir shared dirs"
        assert " > /" not in content, f"{script}: must not write to absolute paths"
        assert " cp " not in content, f"{script}: must not copy into shared dirs"

    @pytest.mark.parametrize("script", TEROK_ACP_WRAPPERS)
    def test_execs_real_adapter(self, script: str) -> None:
        """Each wrapper ends with exec of the real ACP adapter."""
        content = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
        lines = [line.strip() for line in content.strip().splitlines() if line.strip()]
        assert lines[-1].startswith("exec "), f"{script}: last line is not exec"


class TestCodexAcpWrapper:
    """Codex ACP wrapper uses -c flags for unrestricted mode (no env var exists)."""

    def test_passes_approval_policy_via_flag(self) -> None:
        """Codex ACP uses -c approval_policy=never (no env var mechanism)."""
        content = (SCRIPTS_DIR / "terok-codex-acp").read_text(encoding="utf-8")
        assert "approval_policy=never" in content

    def test_passes_sandbox_mode_via_flag(self) -> None:
        """Codex ACP uses -c sandbox_mode=danger-full-access."""
        content = (SCRIPTS_DIR / "terok-codex-acp").read_text(encoding="utf-8")
        assert "sandbox_mode=danger-full-access" in content

    def test_flags_conditional_on_unrestricted(self) -> None:
        """Codex ACP only passes -c flags when TEROK_UNRESTRICTED is set."""
        content = (SCRIPTS_DIR / "terok-codex-acp").read_text(encoding="utf-8")
        assert "TEROK_UNRESTRICTED" in content


class TestOpenCodeProviderAcp:
    """opencode-provider-acp sources shared env for git identity."""

    def test_sources_shared_env(self) -> None:
        """Unified OpenCode provider ACP wrapper uses terok-acp-env.sh."""
        content = (SCRIPTS_DIR / "opencode-provider-acp").read_text(encoding="utf-8")
        assert "terok-acp-env.sh" in content

    def test_sets_agent_identity(self) -> None:
        """Sets _AGENT_NAME and _AGENT_EMAIL derived from provider name."""
        content = (SCRIPTS_DIR / "opencode-provider-acp").read_text(encoding="utf-8")
        assert "_AGENT_NAME=" in content
        assert "_AGENT_EMAIL=" in content

    def test_sets_opencode_config(self) -> None:
        """Sets OPENCODE_CONFIG based on provider name."""
        content = (SCRIPTS_DIR / "opencode-provider-acp").read_text(encoding="utf-8")
        assert "OPENCODE_CONFIG" in content

    def test_derives_provider_from_argv0(self) -> None:
        """Provider name is derived from the symlink name (argv[0])."""
        content = (SCRIPTS_DIR / "opencode-provider-acp").read_text(encoding="utf-8")
        assert "${0##*/}" in content


# -- Toad TOML patching -------------------------------------------------------


class TestToadAcpPatching:
    """The toad launcher patches agent TOMLs to use terok ACP wrappers."""

    def test_toad_script_has_patch_function(self) -> None:
        """The toad launcher contains _patch_run_command."""
        content = (SCRIPTS_DIR / "toad").read_text(encoding="utf-8")
        assert "_patch_run_command" in content

    @pytest.mark.parametrize(
        ("toml_file", "wrapper"),
        [
            ("claude.com.toml", "terok-claude-acp"),
            ("openai.com.toml", "terok-codex-acp"),
            ("opencode.ai.toml", "terok-opencode-acp"),
            ("copilot.github.com.toml", "terok-copilot-acp"),
            ("vibe.mistral.ai.toml", "terok-vibe-acp"),
        ],
    )
    def test_toad_patches_agent(self, toml_file: str, wrapper: str) -> None:
        """The toad launcher maps each agent TOML to the correct terok wrapper."""
        content = (SCRIPTS_DIR / "toad").read_text(encoding="utf-8")
        # Match the exact _patch_run_command call line to ensure correct pairing.
        assert re.search(
            rf'_patch_run_command "\$\{{TOAD_AGENTS_DIR\}}/{re.escape(toml_file)}"\s+"{re.escape(wrapper)}"',
            content,
        ), f"{toml_file} not mapped to {wrapper}"

    def test_patch_run_command_replaces_value(self) -> None:
        """_patch_run_command replaces the run_command line in a TOML file."""
        with tempfile.TemporaryDirectory() as td:
            toml = Path(td) / "test.toml"
            toml.write_text(
                'name = "Test"\nrun_command."*" = "original-adapter"\ndescription = "test agent"\n'
            )
            result = subprocess.run(
                ["bash", "-c", _patch_fn(f'_patch_run_command "{toml}" "terok-test-acp"')],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            content = toml.read_text()
            assert 'run_command."*" = "terok-test-acp"' in content
            assert "original-adapter" not in content
            assert 'name = "Test"' in content

    def test_patch_run_command_is_idempotent(self) -> None:
        """Patching a TOML that already uses the wrapper is a no-op."""
        with tempfile.TemporaryDirectory() as td:
            toml = Path(td) / "test.toml"
            toml.write_text('run_command."*" = "terok-test-acp"\n')
            mtime = toml.stat().st_mtime
            result = subprocess.run(
                ["bash", "-c", _patch_fn(f'_patch_run_command "{toml}" "terok-test-acp"')],
                capture_output=True,
            )
            assert result.returncode == 0
            assert toml.stat().st_mtime == mtime

    def test_patch_run_command_no_false_positive_from_help_text(self) -> None:
        """Wrapper name in help text must not prevent patching run_command."""
        with tempfile.TemporaryDirectory() as td:
            toml = Path(td) / "test.toml"
            toml.write_text(
                'run_command."*" = "original-adapter"\n'
                "\n"
                "help = '''\n"
                'Use "terok-test-acp" when running in container.\n'
                "'''\n"
            )
            result = subprocess.run(
                ["bash", "-c", _patch_fn(f'_patch_run_command "{toml}" "terok-test-acp"')],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            content = toml.read_text()
            assert 'run_command."*" = "terok-test-acp"' in content
            assert "original-adapter" not in content

    def test_patch_run_command_skips_missing_file(self) -> None:
        """Patching a nonexistent file silently returns 0."""
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing" / "path.toml"
            result = subprocess.run(
                ["bash", "-c", _patch_fn(f'_patch_run_command "{missing}" "terok-x"')],
                capture_output=True,
            )
            assert result.returncode == 0


# -- Dockerfile integration ---------------------------------------------------


def _generate_acp_dockerfile() -> tuple[str, list[str]]:
    """Generate L1 CLI Dockerfile and return content and list of staged script names."""
    yaml_text = (
        "project:\n"
        "  id: proj_acp_test\n"
        "git:\n"
        "  upstream_url: https://example.com/repo.git\n"
        "  default_branch: main\n"
    )
    with project_env(yaml_text, project_id="proj_acp_test"):
        generate_dockerfiles("proj_acp_test")
        out = build_root() / "proj_acp_test"
        dockerfile = (out / "L1.cli.Dockerfile").read_text()
        staged = [f.name for f in (out / "scripts").iterdir()]
        return dockerfile, staged


class TestAcpDockerfileIntegration:
    """ACP wrappers are included in the generated L1 CLI Dockerfile."""

    @pytest.mark.parametrize("script", TEROK_ACP_WRAPPERS + ["terok-acp-env.sh"])
    def test_dockerfile_copies_script(self, script: str) -> None:
        """Verify the L1 CLI Dockerfile references each ACP wrapper."""
        dockerfile, _ = _generate_acp_dockerfile()
        assert script in dockerfile

    @pytest.mark.parametrize("script", TEROK_ACP_WRAPPERS + ["terok-acp-env.sh"])
    def test_script_staged_in_build_context(self, script: str) -> None:
        """Verify each ACP wrapper is staged in the build context."""
        _, staged = _generate_acp_dockerfile()
        assert script in staged
