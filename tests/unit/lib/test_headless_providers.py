# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Andreas Knüpfer
# SPDX-License-Identifier: Apache-2.0

"""Tests for headless provider registry and dispatch functions."""

from dataclasses import FrozenInstanceError

import pytest
from terok_agent import (
    HEADLESS_PROVIDERS,
    PROVIDER_NAMES,
    CLIOverrides,
    apply_provider_config,
    build_headless_command,
    collect_all_auto_approve_env,
    get_provider,
    resolve_provider_value,
)
from terok_agent.agents import _generate_claude_wrapper
from terok_agent.headless_providers import generate_agent_wrapper, generate_all_wrappers

from tests.testfs import CONTAINER_TEROK_DIR


def _provider_wrapper(
    name: str,
    *,
    has_agents: bool = False,
) -> str:
    """Generate a wrapper for a provider under test."""
    kwargs = {"claude_wrapper_fn": _generate_claude_wrapper} if name == "claude" else {}
    return generate_agent_wrapper(
        HEADLESS_PROVIDERS[name],
        has_agents=has_agents,
        **kwargs,
    )


def _all_wrappers(*, has_agents: bool = False) -> str:
    """Generate the combined multi-provider wrapper file."""
    return generate_all_wrappers(
        has_agents=has_agents,
        claude_wrapper_fn=_generate_claude_wrapper,
    )


class TestHeadlessProviderRegistry:
    """Tests for the HEADLESS_PROVIDERS registry."""

    def test_all_seven_providers_exist(self) -> None:
        """Registry contains exactly the seven expected providers."""
        expected = {"claude", "codex", "copilot", "vibe", "blablador", "opencode", "kisski"}
        assert set(HEADLESS_PROVIDERS.keys()) == expected

    def test_provider_names_tuple(self) -> None:
        """PROVIDER_NAMES is a tuple matching registry keys."""
        assert isinstance(PROVIDER_NAMES, tuple)
        assert set(PROVIDER_NAMES) == set(HEADLESS_PROVIDERS.keys())

    def test_providers_are_frozen(self) -> None:
        """HeadlessProvider instances are immutable."""
        provider = HEADLESS_PROVIDERS["claude"]
        with pytest.raises(FrozenInstanceError):
            provider.name = "changed"  # type: ignore[misc]

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            (
                "claude",
                {
                    "name": "claude",
                    "binary": "claude",
                    "prompt_flag": "-p",
                    "supports_agents_json": True,
                    "supports_session_hook": True,
                    "supports_add_dir": True,
                    "supports_session_resume": True,
                    "log_format": "claude-stream-json",
                },
            ),
            (
                "codex",
                {
                    "binary": "codex",
                    "headless_subcommand": "exec",
                    "prompt_flag": "",
                    "supports_agents_json": False,
                    "supports_session_resume": False,
                },
            ),
            (
                "copilot",
                {
                    "binary": "copilot",
                    "prompt_flag": "-p",
                    "auto_approve_env": {"COPILOT_ALLOW_ALL": "true"},
                },
            ),
            (
                "vibe",
                {
                    "binary": "vibe",
                    "model_flag": "--agent",
                    "auto_approve_env": {"VIBE_AUTO_APPROVE": "true"},
                    "supports_session_resume": True,
                },
            ),
            (
                "blablador",
                {
                    "binary": "blablador",
                    "model_flag": None,
                    "continue_flag": "--continue",
                    "headless_subcommand": "run",
                    "resume_flag": "--session",
                    "session_file": "blablador-session.txt",
                },
            ),
            (
                "kisski",
                {
                    "binary": "kisski",
                    "model_flag": None,
                    "continue_flag": "--continue",
                    "headless_subcommand": "run",
                    "resume_flag": "--session",
                    "session_file": "kisski-session.txt",
                },
            ),
            (
                "opencode",
                {
                    "binary": "opencode",
                    "headless_subcommand": "run",
                    "resume_flag": "--session",
                },
            ),
        ],
    )
    def test_provider_attributes(self, name: str, expected: dict[str, object]) -> None:
        """Each provider exposes the expected provider-specific attributes."""
        provider = HEADLESS_PROVIDERS[name]
        for attr_name, expected_value in expected.items():
            assert getattr(provider, attr_name) == expected_value


class TestGetProvider:
    """Tests for get_provider() resolution."""

    def test_explicit_name(self) -> None:
        """Explicit provider name resolves correctly."""
        p = get_provider("codex")
        assert p.name == "codex"

    def test_none_falls_back_to_project_default(self) -> None:
        """None name uses default_agent."""
        p = get_provider(None, default_agent="copilot")
        assert p.name == "copilot"

    def test_none_falls_back_to_claude(self) -> None:
        """None name with no default resolves to claude."""
        p = get_provider(None, default_agent=None)
        assert p.name == "claude"

    def test_invalid_name_raises_system_exit(self) -> None:
        """Unknown provider name raises SystemExit."""
        with pytest.raises(SystemExit) as ctx:
            get_provider("nonexistent")
        assert "nonexistent" in str(ctx.value)


class TestBuildHeadlessCommand:
    """Tests for build_headless_command() per provider."""

    @pytest.mark.parametrize(
        ("name", "kwargs", "present", "absent"),
        [
            (
                "claude",
                {"timeout": 1800},
                ["claude --terok-timeout 1800", "-p", "--output-format stream-json", "--verbose"],
                [],
            ),
            (
                "claude",
                {"timeout": 1800, "model": "opus", "max_turns": 50},
                ["--model opus", "--max-turns 50"],
                [],
            ),
            (
                "codex",
                {"timeout": 1800},
                ["codex --terok-timeout 1800", "exec", "prompt.txt"],
                ["--full-auto", "--yolo"],
            ),
            ("codex", {"timeout": 1800, "model": "o3"}, ["--model o3"], []),
            (
                "copilot",
                {"timeout": 900},
                ["copilot --terok-timeout 900", "-p"],
                ["COPILOT_ALLOW_ALL"],
            ),
            ("vibe", {"timeout": 1800}, ["vibe", "--prompt", "prompt.txt"], []),
            ("vibe", {"timeout": 1800, "model": "large"}, ["--agent large"], []),
            ("opencode", {"timeout": 1800}, ["opencode --terok-timeout 1800", "run"], []),
            ("blablador", {"timeout": 1800}, ["blablador", "run"], []),
        ],
    )
    def test_provider_commands(
        self, name: str, kwargs: dict[str, object], present: list[str], absent: list[str]
    ) -> None:
        """Each provider command renders the expected fragments."""
        command = build_headless_command(HEADLESS_PROVIDERS[name], **kwargs)
        for snippet in [*present, "prompt.txt"]:
            assert snippet in command
        for snippet in absent:
            assert snippet not in command

    def test_all_commands_start_with_init(self) -> None:
        """All provider commands start with init-ssh-and-repo.sh."""
        for _name, p in HEADLESS_PROVIDERS.items():
            cmd = build_headless_command(p, timeout=1800)
            assert cmd.startswith("init-ssh-and-repo.sh")


class TestGenerateAgentWrapper:
    """Tests for generate_agent_wrapper() per provider."""

    def test_claude_wrapper_uses_claude_function(self) -> None:
        """Claude wrapper defines a claude() function with --add-dir."""
        wrapper = _provider_wrapper("claude")
        assert "claude()" in wrapper
        assert "--add-dir" in wrapper
        assert "_terok_apply_git_identity Claude noreply@anthropic.com" in wrapper

    def test_claude_wrapper_with_agents(self) -> None:
        """Claude wrapper includes agents.json loading when has_agents=True."""
        wrapper = _provider_wrapper("claude", has_agents=True)
        assert "agents.json" in wrapper

    def test_claude_wrapper_requires_fn(self) -> None:
        """Claude provider without claude_wrapper_fn raises ValueError."""
        p = HEADLESS_PROVIDERS["claude"]
        with pytest.raises(ValueError):
            generate_agent_wrapper(p, has_agents=False)

    def test_codex_wrapper(self) -> None:
        """Codex wrapper defines a codex() function with git env vars."""
        wrapper = _provider_wrapper("codex")
        assert "codex()" in wrapper
        assert "_terok_apply_git_identity Codex noreply@openai.com" in wrapper
        assert "model_instructions_file" in wrapper
        assert "instructions.md" in wrapper
        assert "--add-dir" not in wrapper

    def test_generic_wrapper_has_timeout_support(self) -> None:
        """All non-Claude wrappers support --terok-timeout."""
        for name in HEADLESS_PROVIDERS:
            if name == "claude":
                continue
            wrapper = _provider_wrapper(name)
            assert "--terok-timeout" in wrapper, f"{name} missing timeout support"

    def test_generic_wrapper_uses_authorship_helper(self) -> None:
        """All wrappers use the shared Git authorship helper."""
        for name in HEADLESS_PROVIDERS:
            wrapper = _provider_wrapper(name)
            assert "_terok_apply_git_identity" in wrapper, f"{name} missing helper call"

    # Canonical sets of providers by session_file support.
    # Hardcoded so tests fail fast if a provider accidentally gains/loses the field.
    _SESSION_FILE_PROVIDERS = {"vibe", "opencode", "blablador", "kisski"}
    _NO_SESSION_FILE_PROVIDERS = {"codex", "copilot"}  # excludes claude (own wrapper)

    def test_session_file_providers(self) -> None:
        """Verify which providers have session_file set."""
        actual = {n for n, p in HEADLESS_PROVIDERS.items() if p.session_file}
        assert actual == self._SESSION_FILE_PROVIDERS

    def test_session_resume_uses_explicit_id(self) -> None:
        """Providers with session_file use --session/--resume with explicit ID."""
        for name in self._SESSION_FILE_PROVIDERS:
            p = HEADLESS_PROVIDERS[name]
            wrapper = _provider_wrapper(name)
            assert p.resume_flag in wrapper, f"{name} missing resume flag"
            assert f"cat {CONTAINER_TEROK_DIR}/{p.session_file}" in wrapper, (
                f"{name} should read session ID from file"
            )
            assert "_resume_args+=(--continue)" not in wrapper, f"{name}"

    def test_session_resume_only_headless_or_bare(self) -> None:
        """Resume args are only injected in headless mode or bare interactive launch."""
        for name in self._SESSION_FILE_PROVIDERS:
            wrapper = _provider_wrapper(name)
            assert '[ -n "$_timeout" ]' in wrapper, f"{name} missing timeout check"
            assert "[ $# -eq 0 ]" in wrapper, f"{name} missing arg count check"

    def test_session_env_var_set(self) -> None:
        """Providers with session_file set TEROK_SESSION_FILE env var."""
        for name in self._SESSION_FILE_PROVIDERS:
            p = HEADLESS_PROVIDERS[name]
            wrapper = _provider_wrapper(name)
            assert f"TEROK_SESSION_FILE={CONTAINER_TEROK_DIR}/{p.session_file}" in wrapper, (
                f"{name} missing TEROK_SESSION_FILE"
            )

    def test_opencode_plugin_setup(self) -> None:
        """OpenCode, Blablador and KISSKI wrappers set up the session plugin."""
        for name in ("opencode", "blablador", "kisski"):
            wrapper = _provider_wrapper(name)
            assert "opencode-session-plugin.mjs" in wrapper, f"{name} missing plugin setup"
            assert "terok-session.mjs" in wrapper, f"{name} missing plugin symlink"

    @pytest.mark.parametrize(
        ("name", "plugin_dir"),
        [
            ("blablador", ".blablador/opencode/plugins"),
            ("kisski", ".kisski/opencode/plugins"),
            ("opencode", ".config/opencode/plugins"),
        ],
    )
    def test_provider_plugin_dir(self, name: str, plugin_dir: str) -> None:
        """Each plugin-backed provider uses the expected plugin directory."""
        assert plugin_dir in _provider_wrapper(name)

    def test_vibe_session_capture(self) -> None:
        """Vibe wrapper includes post-run session capture function."""
        wrapper = _provider_wrapper("vibe")
        assert "_terok_capture_vibe_session" in wrapper
        assert "meta.json" in wrapper
        assert "vibe-session.txt" in wrapper

    def test_no_session_providers_skip_session_logic(self) -> None:
        """Providers without session_file do not include session resume logic."""
        for name in self._NO_SESSION_FILE_PROVIDERS:
            wrapper = _provider_wrapper(name)
            assert "_resume_args" not in wrapper, f"{name} should not have resume args"
            assert "TEROK_SESSION_FILE" not in wrapper, f"{name} should not set session env"

    def test_wrapper_auto_approve_logic_matches_provider_capabilities(self) -> None:
        """Only flag-based providers render TEROK_UNRESTRICTED approval logic in wrappers."""
        for name, p in HEADLESS_PROVIDERS.items():
            if name == "claude":
                continue
            wrapper = _provider_wrapper(name)
            if p.auto_approve_flags:
                assert "TEROK_UNRESTRICTED" in wrapper, f"{name} should check TEROK_UNRESTRICTED"
                for flag in p.auto_approve_flags:
                    assert flag in wrapper, f"{name} should render {flag}"
            else:
                assert "TEROK_UNRESTRICTED" not in wrapper, f"{name} should not gate env vars"
                assert "_approve_args" not in wrapper, f"{name} should not build approval args"

    def test_codex_auto_approve_flag(self) -> None:
        """Codex uses the ``--yolo`` auto-approve flag."""
        p = HEADLESS_PROVIDERS["codex"]
        assert p.auto_approve_flags == ("--yolo",)

    def test_opencode_auto_approve_env(self) -> None:
        """OpenCode, Blablador and KISSKI use OPENCODE_PERMISSION env var with correct payload."""
        for name in ("opencode", "blablador", "kisski"):
            p = HEADLESS_PROVIDERS[name]
            assert "OPENCODE_PERMISSION" in p.auto_approve_env, f"{name}"
            assert p.auto_approve_env["OPENCODE_PERMISSION"] == '{"*":"allow"}', (
                f"{name} should grant all permissions"
            )
            assert p.auto_approve_flags == (), f"{name} should have no CLI flags"

    def test_collect_all_auto_approve_env(self) -> None:
        """The merged auto-approve env map contains all provider env vars."""
        merged = collect_all_auto_approve_env()
        assert merged["OPENCODE_PERMISSION"] == '{"*":"allow"}'
        assert merged["VIBE_AUTO_APPROVE"] == "true"
        assert merged["COPILOT_ALLOW_ALL"] == "true"

    def test_vibe_auto_approve_env(self) -> None:
        """Vibe uses the ``VIBE_AUTO_APPROVE`` env var."""
        p = HEADLESS_PROVIDERS["vibe"]
        assert p.auto_approve_env.get("VIBE_AUTO_APPROVE") == "true"

    def test_copilot_auto_approve_env(self) -> None:
        """Copilot uses the ``COPILOT_ALLOW_ALL`` env var."""
        p = HEADLESS_PROVIDERS["copilot"]
        assert p.auto_approve_env.get("COPILOT_ALLOW_ALL") == "true"

    def test_claude_has_no_auto_approve_env(self) -> None:
        """Claude uses managed settings rather than wrapper/container env vars."""
        assert HEADLESS_PROVIDERS["claude"].auto_approve_env == {}

    def test_opencode_wrapper_does_not_export_permission_env(self) -> None:
        """OpenCode/Blablador/KISSKI wrappers rely on container env, not inline exports."""
        for name in ("opencode", "blablador", "kisski"):
            wrapper = _provider_wrapper(name)
            assert "OPENCODE_PERMISSION" not in wrapper, f"{name} should not export env vars"

    def test_auto_approve_not_in_headless_command(self) -> None:
        """Auto-approve flags and env vars are not injected by the command builder."""
        for name, p in HEADLESS_PROVIDERS.items():
            cmd = build_headless_command(p, timeout=1800)
            for flag in p.auto_approve_flags:
                assert flag not in cmd, f"{name}: {flag} should not be in command"
            for key in p.auto_approve_env:
                assert key not in cmd, f"{name}: {key} env should stay in wrapper"


class TestResolveProviderValue:
    """Tests for resolve_provider_value() config resolution."""

    def test_flat_string_value(self) -> None:
        """Flat string value is returned for any provider."""
        config = {"model": "opus"}
        assert resolve_provider_value("model", config, "claude") == "opus"
        assert resolve_provider_value("model", config, "codex") == "opus"

    def test_flat_int_value(self) -> None:
        """Flat int value is returned for any provider."""
        config = {"max_turns": 50}
        assert resolve_provider_value("max_turns", config, "claude") == 50

    def test_per_provider_dict(self) -> None:
        """Per-provider dict returns provider-specific value."""
        config = {"model": {"claude": "opus", "codex": "o3"}}
        assert resolve_provider_value("model", config, "claude") == "opus"
        assert resolve_provider_value("model", config, "codex") == "o3"

    def test_per_provider_dict_with_default(self) -> None:
        """Per-provider dict falls back to _default for unlisted providers."""
        config = {"model": {"claude": "opus", "_default": "fast"}}
        assert resolve_provider_value("model", config, "claude") == "opus"
        assert resolve_provider_value("model", config, "codex") == "fast"

    def test_per_provider_dict_no_match(self) -> None:
        """Per-provider dict returns None when provider is not listed and no _default."""
        config = {"model": {"claude": "opus"}}
        assert resolve_provider_value("model", config, "codex") is None

    def test_missing_key_returns_none(self) -> None:
        """Missing key returns None."""
        assert resolve_provider_value("model", {}, "claude") is None

    def test_none_value_returns_none(self) -> None:
        """Explicit None value returns None."""
        config = {"model": None}
        assert resolve_provider_value("model", config, "claude") is None

    def test_per_provider_null_falls_back_to_default(self) -> None:
        """Explicit null for a provider falls back to _default."""
        config = {"model": {"claude": None, "_default": "fast"}}
        # null provider value → falls back to _default
        assert resolve_provider_value("model", config, "claude") == "fast"
        # non-null provider value is returned directly
        config2 = {"model": {"claude": "opus", "_default": "fast"}}
        assert resolve_provider_value("model", config2, "claude") == "opus"


class TestApplyProviderConfig:
    """Tests for apply_provider_config() best-effort feature mapping."""

    def test_model_from_config(self) -> None:
        """Model value is read from config when no CLI override."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {"model": "opus"})
        assert pcfg.model == "opus"
        assert pcfg.warnings == ()

    def test_model_cli_overrides_config(self) -> None:
        """CLI --model flag overrides config value."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {"model": "haiku"}, CLIOverrides(model="opus"))
        assert pcfg.model == "opus"

    def test_model_per_provider(self) -> None:
        """Per-provider model dict picks the right value."""
        p = HEADLESS_PROVIDERS["codex"]
        pcfg = apply_provider_config(p, {"model": {"claude": "opus", "codex": "o3"}})
        assert pcfg.model == "o3"

    def test_model_unsupported_provider_warns(self) -> None:
        """Provider without model_flag gets warning and model=None."""
        p = HEADLESS_PROVIDERS["blablador"]  # model_flag is None
        pcfg = apply_provider_config(p, {"model": "big"})
        assert pcfg.model is None
        assert len(pcfg.warnings) == 1
        assert "model selection" in pcfg.warnings[0]

    def test_max_turns_supported(self) -> None:
        """Provider with max_turns_flag passes through the value."""
        p = HEADLESS_PROVIDERS["claude"]  # has max_turns_flag
        pcfg = apply_provider_config(p, {"max_turns": 50})
        assert pcfg.max_turns == 50
        assert pcfg.prompt_extra == ""

    def test_max_turns_unsupported_injects_prompt(self) -> None:
        """Provider without max_turns_flag gets prompt injection + warning."""
        p = HEADLESS_PROVIDERS["codex"]  # no max_turns_flag
        pcfg = apply_provider_config(p, {"max_turns": 30})
        assert pcfg.max_turns is None
        assert "30 steps" in pcfg.prompt_extra
        assert len(pcfg.warnings) == 1
        assert "max-turns" in pcfg.warnings[0]

    def test_timeout_from_config(self) -> None:
        """Timeout is read from config when no CLI override."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {"timeout": 3600})
        assert pcfg.timeout == 3600

    def test_timeout_cli_overrides_config(self) -> None:
        """CLI --timeout overrides config value."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {"timeout": 3600}, CLIOverrides(timeout=900))
        assert pcfg.timeout == 900

    def test_timeout_default(self) -> None:
        """Missing timeout defaults to 1800."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {})
        assert pcfg.timeout == 1800

    def test_subagents_warning_for_non_claude(self) -> None:
        """Non-Claude providers get warning about subagents."""
        p = HEADLESS_PROVIDERS["codex"]
        pcfg = apply_provider_config(p, {"subagents": [{"name": "test"}]})
        assert any("sub-agent" in w for w in pcfg.warnings)

    def test_no_subagent_warning_for_claude(self) -> None:
        """Claude provider does not get subagent warning."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {"subagents": [{"name": "test"}]})
        assert not any("sub-agent" in w for w in pcfg.warnings)

    def test_empty_config_no_warnings(self) -> None:
        """Empty config produces no warnings."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {})
        assert pcfg.warnings == ()
        assert pcfg.model is None
        assert pcfg.max_turns is None

    def test_instructions_other_provider_in_prompt_extra(self) -> None:
        """Non-Claude/Codex providers get instructions prepended to prompt_extra."""
        p = HEADLESS_PROVIDERS["copilot"]
        pcfg = apply_provider_config(p, {}, CLIOverrides(instructions="Custom instructions."))
        assert "Custom instructions." in pcfg.prompt_extra

    def test_instructions_claude_not_in_prompt_extra(self) -> None:
        """Claude provider does NOT get instructions in prompt_extra (uses wrapper)."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {}, CLIOverrides(instructions="Custom instructions."))
        assert "Custom instructions." not in pcfg.prompt_extra

    def test_instructions_codex_not_in_prompt_extra(self) -> None:
        """Codex provider does NOT get prompt injection (uses wrapper config)."""
        p = HEADLESS_PROVIDERS["codex"]
        pcfg = apply_provider_config(p, {}, CLIOverrides(instructions="Custom instructions."))
        assert "Custom instructions." not in pcfg.prompt_extra

    def test_instructions_opencode_not_in_prompt_extra(self) -> None:
        """OpenCode does NOT get prompt injection (uses opencode.json instructions)."""
        p = HEADLESS_PROVIDERS["opencode"]
        pcfg = apply_provider_config(p, {}, CLIOverrides(instructions="Custom instructions."))
        assert "Custom instructions." not in pcfg.prompt_extra

    def test_instructions_blablador_not_in_prompt_extra(self) -> None:
        """Blablador does NOT get prompt injection (uses opencode.json instructions)."""
        p = HEADLESS_PROVIDERS["blablador"]
        pcfg = apply_provider_config(p, {}, CLIOverrides(instructions="Custom instructions."))
        assert "Custom instructions." not in pcfg.prompt_extra

    def test_instructions_prepended_before_other_prompt_parts(self) -> None:
        """Instructions are prepended before max-turns guidance for other providers."""
        p = HEADLESS_PROVIDERS["copilot"]  # no max_turns_flag
        pcfg = apply_provider_config(
            p, {"max_turns": 30}, CLIOverrides(instructions="Do the thing.")
        )
        # Instructions should come before the max-turns guidance
        idx_instr = pcfg.prompt_extra.index("Do the thing.")
        idx_turns = pcfg.prompt_extra.index("30 steps")
        assert idx_instr < idx_turns


class TestGenerateAllWrappers:
    """Tests for generate_all_wrappers() multi-provider file."""

    def test_all_providers_in_output(self) -> None:
        """Output contains wrapper functions for all six providers."""
        wrapper = _all_wrappers()
        for name, p in HEADLESS_PROVIDERS.items():
            assert f"{p.binary}()" in wrapper, f"Missing wrapper for {name}"

    def test_all_wrappers_use_authorship_helper(self) -> None:
        """All wrappers in the combined file use the shared helper."""
        wrapper = _all_wrappers()
        for name, provider in HEADLESS_PROVIDERS.items():
            start = wrapper.index(f"{provider.binary}() {{")
            end = wrapper.find("\n# Generated by terok\n", start + 1)
            section = wrapper[start:] if end == -1 else wrapper[start:end]
            assert "_terok_apply_git_identity" in section, f"Missing authorship helper in {name}"

    def test_all_wrappers_valid_bash_syntax(self) -> None:
        """Combined wrapper output passes bash -n syntax check."""
        import subprocess

        wrapper = _all_wrappers(has_agents=True)
        result = subprocess.run(["bash", "-n"], input=wrapper, capture_output=True, text=True)
        assert result.returncode == 0, f"bash syntax error:\n{result.stderr}"
