# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for headless provider registry and dispatch functions."""

import unittest
import unittest.mock
from dataclasses import FrozenInstanceError

from terok.lib.containers.agent_config import resolve_provider_value
from terok.lib.containers.headless_providers import (
    HEADLESS_PROVIDERS,
    PROVIDER_NAMES,
    apply_provider_config,
    build_headless_command,
    generate_agent_wrapper,
    generate_all_wrappers,
    get_provider,
)
from terok.lib.core.project_model import Project


def _make_project(**kwargs: object) -> Project:
    """Create a minimal Project with sensible defaults."""
    from pathlib import Path

    defaults: dict = {
        "id": "testproj",
        "security_class": "online",
        "upstream_url": None,
        "default_branch": "main",
        "root": Path("/tmp/test"),
        "tasks_root": Path("/tmp/test/tasks"),
        "gate_path": Path("/tmp/test/gate"),
        "staging_root": None,
        "ssh_key_name": None,
        "ssh_host_dir": None,
        "default_agent": None,
        "human_name": "Test User",
        "human_email": "test@example.com",
    }
    defaults.update(kwargs)
    return Project(**defaults)


class HeadlessProviderRegistryTests(unittest.TestCase):
    """Tests for the HEADLESS_PROVIDERS registry."""

    def test_all_six_providers_exist(self) -> None:
        """Registry contains exactly the six expected providers."""
        expected = {"claude", "codex", "copilot", "vibe", "blablador", "opencode"}
        self.assertEqual(set(HEADLESS_PROVIDERS.keys()), expected)

    def test_provider_names_tuple(self) -> None:
        """PROVIDER_NAMES is a tuple matching registry keys."""
        self.assertIsInstance(PROVIDER_NAMES, tuple)
        self.assertEqual(set(PROVIDER_NAMES), set(HEADLESS_PROVIDERS.keys()))

    def test_providers_are_frozen(self) -> None:
        """HeadlessProvider instances are immutable."""
        provider = HEADLESS_PROVIDERS["claude"]
        with self.assertRaises(FrozenInstanceError):
            provider.name = "changed"  # type: ignore[misc]

    def test_claude_provider_attributes(self) -> None:
        """Claude provider has expected attributes."""
        p = HEADLESS_PROVIDERS["claude"]
        self.assertEqual(p.name, "claude")
        self.assertEqual(p.binary, "claude")
        self.assertEqual(p.prompt_flag, "-p")
        self.assertTrue(p.supports_agents_json)
        self.assertTrue(p.supports_session_hook)
        self.assertTrue(p.supports_add_dir)
        self.assertTrue(p.supports_session_resume)
        self.assertEqual(p.log_format, "claude-stream-json")

    def test_codex_provider_attributes(self) -> None:
        """Codex provider has expected attributes."""
        p = HEADLESS_PROVIDERS["codex"]
        self.assertEqual(p.binary, "codex")
        self.assertEqual(p.headless_subcommand, "exec")
        self.assertEqual(p.prompt_flag, "")
        self.assertFalse(p.supports_agents_json)
        self.assertFalse(p.supports_session_resume)

    def test_copilot_provider_attributes(self) -> None:
        """Copilot provider has expected attributes."""
        p = HEADLESS_PROVIDERS["copilot"]
        self.assertEqual(p.binary, "copilot")
        self.assertEqual(p.prompt_flag, "-p")
        self.assertEqual(p.auto_approve_flags, ("--allow-all-tools",))

    def test_vibe_provider_attributes(self) -> None:
        """Vibe provider has expected attributes."""
        p = HEADLESS_PROVIDERS["vibe"]
        self.assertEqual(p.binary, "vibe")
        self.assertEqual(p.model_flag, "--agent")
        self.assertTrue(p.supports_session_resume)

    def test_blablador_provider_attributes(self) -> None:
        """Blablador provider has expected attributes."""
        p = HEADLESS_PROVIDERS["blablador"]
        self.assertEqual(p.binary, "blablador")
        self.assertIsNone(p.model_flag)
        self.assertEqual(p.continue_flag, "--continue")
        self.assertEqual(p.headless_subcommand, "run")
        self.assertEqual(p.resume_flag, "--session")
        self.assertEqual(p.session_file, "blablador-session.txt")

    def test_opencode_provider_attributes(self) -> None:
        """OpenCode provider has expected attributes."""
        p = HEADLESS_PROVIDERS["opencode"]
        self.assertEqual(p.binary, "opencode")
        self.assertEqual(p.headless_subcommand, "run")
        self.assertEqual(p.resume_flag, "--session")


class GetProviderTests(unittest.TestCase):
    """Tests for get_provider() resolution."""

    def test_explicit_name(self) -> None:
        """Explicit provider name resolves correctly."""
        project = _make_project()
        p = get_provider("codex", project)
        self.assertEqual(p.name, "codex")

    def test_none_falls_back_to_project_default(self) -> None:
        """None name uses project.default_agent."""
        project = _make_project(default_agent="copilot")
        p = get_provider(None, project)
        self.assertEqual(p.name, "copilot")

    def test_none_falls_back_to_claude(self) -> None:
        """None name with no project default resolves to claude."""
        project = _make_project(default_agent=None)
        p = get_provider(None, project)
        self.assertEqual(p.name, "claude")

    def test_invalid_name_raises_system_exit(self) -> None:
        """Unknown provider name raises SystemExit."""
        project = _make_project()
        with self.assertRaises(SystemExit) as ctx:
            get_provider("nonexistent", project)
        self.assertIn("nonexistent", str(ctx.exception))


class BuildHeadlessCommandTests(unittest.TestCase):
    """Tests for build_headless_command() per provider."""

    def test_claude_command_uses_wrapper(self) -> None:
        """Claude command uses the wrapper function with --terok-timeout."""
        p = HEADLESS_PROVIDERS["claude"]
        cmd = build_headless_command(p, timeout=1800)
        self.assertIn("claude --terok-timeout 1800", cmd)
        self.assertIn("-p", cmd)
        self.assertIn("--output-format stream-json", cmd)
        self.assertIn("--verbose", cmd)
        self.assertIn("prompt.txt", cmd)

    def test_claude_command_with_model_and_turns(self) -> None:
        """Claude command includes model and max-turns flags."""
        p = HEADLESS_PROVIDERS["claude"]
        cmd = build_headless_command(p, timeout=1800, model="opus", max_turns=50)
        self.assertIn("--model opus", cmd)
        self.assertIn("--max-turns 50", cmd)

    def test_codex_command(self) -> None:
        """Codex command uses exec subcommand and --full-auto via wrapper."""
        p = HEADLESS_PROVIDERS["codex"]
        cmd = build_headless_command(p, timeout=1800)
        self.assertIn("codex --terok-timeout 1800", cmd)
        self.assertIn("exec", cmd)
        self.assertIn("--full-auto", cmd)
        self.assertIn("prompt.txt", cmd)

    def test_codex_command_with_model(self) -> None:
        """Codex command includes --model flag."""
        p = HEADLESS_PROVIDERS["codex"]
        cmd = build_headless_command(p, timeout=1800, model="o3")
        self.assertIn("--model o3", cmd)

    def test_copilot_command(self) -> None:
        """Copilot command uses -p flag and --allow-all-tools via wrapper."""
        p = HEADLESS_PROVIDERS["copilot"]
        cmd = build_headless_command(p, timeout=900)
        self.assertIn("copilot --terok-timeout 900", cmd)
        self.assertIn("--allow-all-tools", cmd)
        self.assertIn("-p", cmd)

    def test_vibe_command(self) -> None:
        """Vibe command uses --prompt flag."""
        p = HEADLESS_PROVIDERS["vibe"]
        cmd = build_headless_command(p, timeout=1800)
        self.assertIn("vibe", cmd)
        self.assertIn("--prompt", cmd)
        self.assertIn("prompt.txt", cmd)

    def test_vibe_command_with_model(self) -> None:
        """Vibe command uses --agent for model selection."""
        p = HEADLESS_PROVIDERS["vibe"]
        cmd = build_headless_command(p, timeout=1800, model="large")
        self.assertIn("--agent large", cmd)

    def test_opencode_command(self) -> None:
        """OpenCode command uses run subcommand via wrapper."""
        p = HEADLESS_PROVIDERS["opencode"]
        cmd = build_headless_command(p, timeout=1800)
        self.assertIn("opencode --terok-timeout 1800", cmd)
        self.assertIn("run", cmd)
        self.assertIn("prompt.txt", cmd)

    def test_blablador_command(self) -> None:
        """Blablador command uses blablador binary with run subcommand."""
        p = HEADLESS_PROVIDERS["blablador"]
        cmd = build_headless_command(p, timeout=1800)
        self.assertIn("blablador", cmd)
        self.assertIn("run", cmd)
        self.assertIn("prompt.txt", cmd)

    def test_all_commands_start_with_init(self) -> None:
        """All provider commands start with init-ssh-and-repo.sh."""
        for name, p in HEADLESS_PROVIDERS.items():
            cmd = build_headless_command(p, timeout=1800)
            self.assertTrue(cmd.startswith("init-ssh-and-repo.sh"), f"{name} missing init")


class GenerateAgentWrapperTests(unittest.TestCase):
    """Tests for generate_agent_wrapper() per provider."""

    @staticmethod
    def _claude_wrapper_fn(has_agents: bool, project: object, skip_permissions: bool) -> str:
        """Stub for agents._generate_claude_wrapper used in tests."""
        from terok.lib.containers.agents import _generate_claude_wrapper

        return _generate_claude_wrapper(has_agents, project, skip_permissions)

    def test_claude_wrapper_uses_claude_function(self) -> None:
        """Claude wrapper defines a claude() function with --add-dir."""
        project = _make_project()
        p = HEADLESS_PROVIDERS["claude"]
        wrapper = generate_agent_wrapper(
            p, project, has_agents=False, claude_wrapper_fn=self._claude_wrapper_fn
        )
        self.assertIn("claude()", wrapper)
        self.assertIn("--add-dir", wrapper)
        self.assertIn("GIT_AUTHOR_NAME=Claude", wrapper)

    def test_claude_wrapper_with_agents(self) -> None:
        """Claude wrapper includes agents.json loading when has_agents=True."""
        project = _make_project()
        p = HEADLESS_PROVIDERS["claude"]
        wrapper = generate_agent_wrapper(
            p, project, has_agents=True, claude_wrapper_fn=self._claude_wrapper_fn
        )
        self.assertIn("agents.json", wrapper)

    def test_claude_wrapper_requires_fn(self) -> None:
        """Claude provider without claude_wrapper_fn raises ValueError."""
        project = _make_project()
        p = HEADLESS_PROVIDERS["claude"]
        with self.assertRaises(ValueError):
            generate_agent_wrapper(p, project, has_agents=False)

    def test_codex_wrapper(self) -> None:
        """Codex wrapper defines a codex() function with git env vars."""
        project = _make_project()
        p = HEADLESS_PROVIDERS["codex"]
        wrapper = generate_agent_wrapper(p, project, has_agents=False)
        self.assertIn("codex()", wrapper)
        self.assertIn("GIT_AUTHOR_NAME=Codex", wrapper)
        self.assertIn("GIT_AUTHOR_EMAIL=noreply@openai.com", wrapper)
        self.assertIn("model_instructions_file", wrapper)
        self.assertIn("instructions.md", wrapper)
        self.assertNotIn("--add-dir", wrapper)

    def test_generic_wrapper_has_timeout_support(self) -> None:
        """All non-Claude wrappers support --terok-timeout."""
        project = _make_project()
        for name, p in HEADLESS_PROVIDERS.items():
            if name == "claude":
                continue
            wrapper = generate_agent_wrapper(p, project, has_agents=False)
            self.assertIn("--terok-timeout", wrapper, f"{name} missing timeout support")

    def test_generic_wrapper_has_git_committer(self) -> None:
        """All wrappers set GIT_COMMITTER_NAME from project human_name."""
        project = _make_project(human_name="Alice", human_email="alice@example.com")
        for name, p in HEADLESS_PROVIDERS.items():
            kwargs: dict = {}
            if name == "claude":
                kwargs["claude_wrapper_fn"] = self._claude_wrapper_fn
            wrapper = generate_agent_wrapper(p, project, has_agents=False, **kwargs)
            self.assertIn("Alice", wrapper, f"{name} missing committer name")

    # Canonical sets of providers by session_file support.
    # Hardcoded so tests fail fast if a provider accidentally gains/loses the field.
    _SESSION_FILE_PROVIDERS = {"vibe", "opencode", "blablador"}
    _NO_SESSION_FILE_PROVIDERS = {"codex", "copilot"}  # excludes claude (own wrapper)

    def test_session_file_providers(self) -> None:
        """Verify which providers have session_file set."""
        actual = {n for n, p in HEADLESS_PROVIDERS.items() if p.session_file}
        self.assertEqual(actual, self._SESSION_FILE_PROVIDERS)

    def test_session_resume_uses_explicit_id(self) -> None:
        """Providers with session_file use --session/--resume with explicit ID."""
        project = _make_project()
        for name in self._SESSION_FILE_PROVIDERS:
            p = HEADLESS_PROVIDERS[name]
            wrapper = generate_agent_wrapper(p, project, has_agents=False)
            # Uses resume_flag with cat to read the session ID
            self.assertIn(p.resume_flag, wrapper, f"{name} missing resume flag")
            self.assertIn(
                f"cat /home/dev/.terok/{p.session_file}",
                wrapper,
                f"{name} should read session ID from file",
            )
            # Should NOT use standalone --continue
            self.assertNotIn("_resume_args+=(--continue)", wrapper, f"{name}")

    def test_session_resume_only_headless_or_bare(self) -> None:
        """Resume args are only injected in headless mode or bare interactive launch."""
        project = _make_project()
        for name in self._SESSION_FILE_PROVIDERS:
            p = HEADLESS_PROVIDERS[name]
            wrapper = generate_agent_wrapper(p, project, has_agents=False)
            # Conditional on timeout or zero args
            self.assertIn('[ -n "$_timeout" ]', wrapper, f"{name} missing timeout check")
            self.assertIn("[ $# -eq 0 ]", wrapper, f"{name} missing arg count check")

    def test_session_env_var_set(self) -> None:
        """Providers with session_file set TEROK_SESSION_FILE env var."""
        project = _make_project()
        for name in self._SESSION_FILE_PROVIDERS:
            p = HEADLESS_PROVIDERS[name]
            wrapper = generate_agent_wrapper(p, project, has_agents=False)
            self.assertIn(
                f"TEROK_SESSION_FILE=/home/dev/.terok/{p.session_file}",
                wrapper,
                f"{name} missing TEROK_SESSION_FILE",
            )

    def test_opencode_plugin_setup(self) -> None:
        """OpenCode and Blablador wrappers set up the session plugin."""
        project = _make_project()
        for name in ("opencode", "blablador"):
            p = HEADLESS_PROVIDERS[name]
            wrapper = generate_agent_wrapper(p, project, has_agents=False)
            self.assertIn("opencode-session-plugin.mjs", wrapper, f"{name} missing plugin setup")
            self.assertIn("terok-session.mjs", wrapper, f"{name} missing plugin symlink")

    def test_blablador_plugin_dir(self) -> None:
        """Blablador uses its own plugin directory (not default opencode)."""
        project = _make_project()
        p = HEADLESS_PROVIDERS["blablador"]
        wrapper = generate_agent_wrapper(p, project, has_agents=False)
        self.assertIn(".blablador/opencode/plugins", wrapper)

    def test_opencode_plugin_dir(self) -> None:
        """OpenCode uses the default config plugin directory."""
        project = _make_project()
        p = HEADLESS_PROVIDERS["opencode"]
        wrapper = generate_agent_wrapper(p, project, has_agents=False)
        self.assertIn(".config/opencode/plugins", wrapper)

    def test_vibe_session_capture(self) -> None:
        """Vibe wrapper includes post-run session capture function."""
        project = _make_project()
        p = HEADLESS_PROVIDERS["vibe"]
        wrapper = generate_agent_wrapper(p, project, has_agents=False)
        self.assertIn("_terok_capture_vibe_session", wrapper)
        self.assertIn("meta.json", wrapper)
        self.assertIn("vibe-session.txt", wrapper)

    def test_no_session_providers_skip_session_logic(self) -> None:
        """Providers without session_file do not include session resume logic."""
        project = _make_project()
        for name in self._NO_SESSION_FILE_PROVIDERS:
            p = HEADLESS_PROVIDERS[name]
            wrapper = generate_agent_wrapper(p, project, has_agents=False)
            self.assertNotIn("_resume_args", wrapper, f"{name} should not have resume args")
            self.assertNotIn("TEROK_SESSION_FILE", wrapper, f"{name} should not set session env")


class ResolveProviderValueTests(unittest.TestCase):
    """Tests for resolve_provider_value() config resolution."""

    def test_flat_string_value(self) -> None:
        """Flat string value is returned for any provider."""
        config = {"model": "opus"}
        self.assertEqual(resolve_provider_value("model", config, "claude"), "opus")
        self.assertEqual(resolve_provider_value("model", config, "codex"), "opus")

    def test_flat_int_value(self) -> None:
        """Flat int value is returned for any provider."""
        config = {"max_turns": 50}
        self.assertEqual(resolve_provider_value("max_turns", config, "claude"), 50)

    def test_per_provider_dict(self) -> None:
        """Per-provider dict returns provider-specific value."""
        config = {"model": {"claude": "opus", "codex": "o3"}}
        self.assertEqual(resolve_provider_value("model", config, "claude"), "opus")
        self.assertEqual(resolve_provider_value("model", config, "codex"), "o3")

    def test_per_provider_dict_with_default(self) -> None:
        """Per-provider dict falls back to _default for unlisted providers."""
        config = {"model": {"claude": "opus", "_default": "fast"}}
        self.assertEqual(resolve_provider_value("model", config, "claude"), "opus")
        self.assertEqual(resolve_provider_value("model", config, "codex"), "fast")

    def test_per_provider_dict_no_match(self) -> None:
        """Per-provider dict returns None when provider is not listed and no _default."""
        config = {"model": {"claude": "opus"}}
        self.assertIsNone(resolve_provider_value("model", config, "codex"))

    def test_missing_key_returns_none(self) -> None:
        """Missing key returns None."""
        self.assertIsNone(resolve_provider_value("model", {}, "claude"))

    def test_none_value_returns_none(self) -> None:
        """Explicit None value returns None."""
        config = {"model": None}
        self.assertIsNone(resolve_provider_value("model", config, "claude"))

    def test_per_provider_null_falls_back_to_default(self) -> None:
        """Explicit null for a provider falls back to _default."""
        config = {"model": {"claude": None, "_default": "fast"}}
        # null provider value → falls back to _default
        self.assertEqual(resolve_provider_value("model", config, "claude"), "fast")
        # non-null provider value is returned directly
        config2 = {"model": {"claude": "opus", "_default": "fast"}}
        self.assertEqual(resolve_provider_value("model", config2, "claude"), "opus")


class ApplyProviderConfigTests(unittest.TestCase):
    """Tests for apply_provider_config() best-effort feature mapping."""

    def test_model_from_config(self) -> None:
        """Model value is read from config when no CLI override."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {"model": "opus"})
        self.assertEqual(pcfg.model, "opus")
        self.assertEqual(pcfg.warnings, ())

    def test_model_cli_overrides_config(self) -> None:
        """CLI --model flag overrides config value."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {"model": "haiku"}, model_override="opus")
        self.assertEqual(pcfg.model, "opus")

    def test_model_per_provider(self) -> None:
        """Per-provider model dict picks the right value."""
        p = HEADLESS_PROVIDERS["codex"]
        pcfg = apply_provider_config(p, {"model": {"claude": "opus", "codex": "o3"}})
        self.assertEqual(pcfg.model, "o3")

    def test_model_unsupported_provider_warns(self) -> None:
        """Provider without model_flag gets warning and model=None."""
        p = HEADLESS_PROVIDERS["blablador"]  # model_flag is None
        pcfg = apply_provider_config(p, {"model": "big"})
        self.assertIsNone(pcfg.model)
        self.assertEqual(len(pcfg.warnings), 1)
        self.assertIn("model selection", pcfg.warnings[0])

    def test_max_turns_supported(self) -> None:
        """Provider with max_turns_flag passes through the value."""
        p = HEADLESS_PROVIDERS["claude"]  # has max_turns_flag
        pcfg = apply_provider_config(p, {"max_turns": 50})
        self.assertEqual(pcfg.max_turns, 50)
        self.assertEqual(pcfg.prompt_extra, "")

    def test_max_turns_unsupported_injects_prompt(self) -> None:
        """Provider without max_turns_flag gets prompt injection + warning."""
        p = HEADLESS_PROVIDERS["codex"]  # no max_turns_flag
        pcfg = apply_provider_config(p, {"max_turns": 30})
        self.assertIsNone(pcfg.max_turns)
        self.assertIn("30 steps", pcfg.prompt_extra)
        self.assertEqual(len(pcfg.warnings), 1)
        self.assertIn("max-turns", pcfg.warnings[0])

    def test_timeout_from_config(self) -> None:
        """Timeout is read from config when no CLI override."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {"timeout": 3600})
        self.assertEqual(pcfg.timeout, 3600)

    def test_timeout_cli_overrides_config(self) -> None:
        """CLI --timeout overrides config value."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {"timeout": 3600}, timeout_override=900)
        self.assertEqual(pcfg.timeout, 900)

    def test_timeout_default(self) -> None:
        """Missing timeout defaults to 1800."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {})
        self.assertEqual(pcfg.timeout, 1800)

    def test_subagents_warning_for_non_claude(self) -> None:
        """Non-Claude providers get warning about subagents."""
        p = HEADLESS_PROVIDERS["codex"]
        pcfg = apply_provider_config(p, {"subagents": [{"name": "test"}]})
        self.assertTrue(any("sub-agent" in w for w in pcfg.warnings))

    def test_no_subagent_warning_for_claude(self) -> None:
        """Claude provider does not get subagent warning."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {"subagents": [{"name": "test"}]})
        self.assertFalse(any("sub-agent" in w for w in pcfg.warnings))

    def test_empty_config_no_warnings(self) -> None:
        """Empty config produces no warnings."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {})
        self.assertEqual(pcfg.warnings, ())
        self.assertIsNone(pcfg.model)
        self.assertIsNone(pcfg.max_turns)

    def test_instructions_other_provider_in_prompt_extra(self) -> None:
        """Non-Claude/Codex providers get instructions prepended to prompt_extra."""
        p = HEADLESS_PROVIDERS["copilot"]
        pcfg = apply_provider_config(p, {}, instructions="Custom instructions.")
        self.assertIn("Custom instructions.", pcfg.prompt_extra)

    def test_instructions_claude_not_in_prompt_extra(self) -> None:
        """Claude provider does NOT get instructions in prompt_extra (uses wrapper)."""
        p = HEADLESS_PROVIDERS["claude"]
        pcfg = apply_provider_config(p, {}, instructions="Custom instructions.")
        self.assertNotIn("Custom instructions.", pcfg.prompt_extra)

    def test_instructions_codex_not_in_prompt_extra(self) -> None:
        """Codex provider does NOT get prompt injection (uses wrapper config)."""
        p = HEADLESS_PROVIDERS["codex"]
        pcfg = apply_provider_config(p, {}, instructions="Custom instructions.")
        self.assertNotIn("Custom instructions.", pcfg.prompt_extra)

    def test_instructions_prepended_before_other_prompt_parts(self) -> None:
        """Instructions are prepended before max-turns guidance for other providers."""
        p = HEADLESS_PROVIDERS["copilot"]  # no max_turns_flag
        pcfg = apply_provider_config(p, {"max_turns": 30}, instructions="Do the thing.")
        # Instructions should come before the max-turns guidance
        idx_instr = pcfg.prompt_extra.index("Do the thing.")
        idx_turns = pcfg.prompt_extra.index("30 steps")
        self.assertLess(idx_instr, idx_turns)


class GenerateAllWrappersTests(unittest.TestCase):
    """Tests for generate_all_wrappers() multi-provider file."""

    @staticmethod
    def _claude_wrapper_fn(has_agents: bool, project: object, skip_permissions: bool) -> str:
        """Stub for agents._generate_claude_wrapper used in tests."""
        from terok.lib.containers.agents import _generate_claude_wrapper

        return _generate_claude_wrapper(has_agents, project, skip_permissions)

    def test_all_providers_in_output(self) -> None:
        """Output contains wrapper functions for all six providers."""
        project = _make_project()
        wrapper = generate_all_wrappers(
            project, has_agents=False, claude_wrapper_fn=self._claude_wrapper_fn
        )
        for name, p in HEADLESS_PROVIDERS.items():
            self.assertIn(f"{p.binary}()", wrapper, f"Missing wrapper for {name}")

    def test_all_wrappers_have_git_committer(self) -> None:
        """All wrappers in the combined file set GIT_COMMITTER_NAME."""
        project = _make_project(human_name="Bob")
        wrapper = generate_all_wrappers(
            project, has_agents=False, claude_wrapper_fn=self._claude_wrapper_fn
        )
        # Each provider's wrapper mentions the committer name at least once
        # (actually twice per provider — if/else branches)
        self.assertGreaterEqual(wrapper.count("Bob"), len(HEADLESS_PROVIDERS))
