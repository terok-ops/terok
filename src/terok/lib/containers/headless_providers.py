# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Headless (autopilot) provider registry for multi-agent support.

Defines a frozen dataclass per provider and a registry dict, following the
same pattern as ``AuthProvider`` in ``security/auth.py``.  Dispatch functions
resolve the active provider, build the headless CLI command, and generate the
per-provider shell wrapper.

Instruction delivery
~~~~~~~~~~~~~~~~~~~~
Custom instructions are delivered via a provider-specific channel:

- **Claude**: ``--append-system-prompt`` flag (injected by the wrapper).
- **Codex**: ``model_instructions_file`` config (``-c`` flag in the wrapper).
- **OpenCode / Blablador**: ``"instructions"`` array in ``opencode.json``
  pointing to ``/home/dev/.terok/instructions.md`` (injected on the host by
  :func:`~terok.lib.containers.agents._inject_opencode_instructions`).
- **Other providers** (Copilot, Vibe, …): best-effort prompt prepending
  via ``prompt_extra`` in :class:`ProviderConfig`.

The instructions file is always written (with a neutral default when no
custom text is configured) so that config-file references never dangle.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..core.project_model import ProjectConfig


@dataclass(frozen=True)
class HeadlessProvider:
    """Describes how to run one AI agent in headless (autopilot) mode."""

    name: str
    """Short key used in CLI dispatch (e.g. ``"claude"``, ``"codex"``)."""

    label: str
    """Human-readable display name (e.g. ``"Claude"``, ``"Codex"``)."""

    binary: str
    """CLI binary name (e.g. ``"claude"``, ``"codex"``, ``"opencode"``)."""

    git_author_name: str
    """AI identity name for Git author/committer policy application."""

    git_author_email: str
    """AI identity email for Git author/committer policy application."""

    # -- Headless command construction --

    headless_subcommand: str | None
    """Subcommand for headless mode (e.g. ``"exec"`` for codex, ``"run"`` for opencode).

    ``None`` means the binary uses flags only (e.g. ``claude -p``).
    """

    prompt_flag: str
    """Flag for passing the prompt.

    ``"-p"`` for flag-based, ``""`` for positional (after subcommand).
    """

    auto_approve_flags: tuple[str, ...]
    """Flags to enable fully autonomous execution (injected when unrestricted)."""

    auto_approve_env: dict[str, str]
    """Environment variables to set for fully autonomous execution (injected when unrestricted)."""

    output_format_flags: tuple[str, ...]
    """Flags for structured output (e.g. ``("--output-format", "stream-json")``)."""

    model_flag: str | None
    """Flag for model override (``"--model"``, ``"--agent"``, or ``None``)."""

    max_turns_flag: str | None
    """Flag for maximum turns (``"--max-turns"`` or ``None``)."""

    verbose_flag: str | None
    """Flag for verbose output (``"--verbose"`` or ``None``)."""

    # -- Session support --

    supports_session_resume: bool
    """Whether the provider supports resuming a previous session."""

    resume_flag: str | None
    """Flag to resume a session (e.g. ``"--resume"``, ``"--session"``)."""

    continue_flag: str | None
    """Flag to continue a session (e.g. ``"--continue"``)."""

    session_file: str | None
    """Filename in ``/home/dev/.terok/`` for stored session ID.

    Providers that capture session IDs via plugin or post-run parsing set this
    to a filename (e.g. ``"opencode-session.txt"``).  Providers with their own
    hook mechanism (Claude) or no session support set this to ``None``.
    """

    # -- Claude-specific capabilities --

    supports_agents_json: bool
    """Whether the provider supports ``--agents`` JSON (Claude only)."""

    supports_session_hook: bool
    """Whether the provider supports SessionStart hooks (Claude only)."""

    supports_add_dir: bool
    """Whether the provider supports ``--add-dir "/"`` (Claude only)."""

    # -- Log formatting --

    log_format: str
    """Log format identifier: ``"claude-stream-json"`` or ``"plain"``."""


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

HEADLESS_PROVIDERS: dict[str, HeadlessProvider] = {
    "claude": HeadlessProvider(
        name="claude",
        label="Claude",
        binary="claude",
        git_author_name="Claude",
        git_author_email="noreply@anthropic.com",
        headless_subcommand=None,
        prompt_flag="-p",
        auto_approve_flags=("--dangerously-skip-permissions",),
        auto_approve_env={},
        output_format_flags=("--output-format", "stream-json"),
        model_flag="--model",
        max_turns_flag="--max-turns",
        verbose_flag="--verbose",
        supports_session_resume=True,
        resume_flag="--resume",
        continue_flag=None,
        session_file=None,
        supports_agents_json=True,
        supports_session_hook=True,
        supports_add_dir=True,
        log_format="claude-stream-json",
    ),
    "codex": HeadlessProvider(
        name="codex",
        label="Codex",
        binary="codex",
        git_author_name="Codex",
        git_author_email="noreply@openai.com",
        headless_subcommand="exec",
        prompt_flag="",
        auto_approve_flags=("--dangerously-bypass-approvals-and-sandbox",),
        auto_approve_env={},
        output_format_flags=(),
        model_flag="--model",
        max_turns_flag=None,
        verbose_flag=None,
        supports_session_resume=False,
        resume_flag=None,
        continue_flag=None,
        session_file=None,
        supports_agents_json=False,
        supports_session_hook=False,
        supports_add_dir=False,
        log_format="plain",
    ),
    "copilot": HeadlessProvider(
        name="copilot",
        label="GitHub Copilot",
        binary="copilot",
        git_author_name="Copilot",
        git_author_email="noreply@github.com",
        headless_subcommand=None,
        prompt_flag="-p",
        auto_approve_flags=("--allow-all-tools",),
        auto_approve_env={},
        output_format_flags=(),
        model_flag="--model",
        max_turns_flag=None,
        verbose_flag=None,
        supports_session_resume=False,
        resume_flag=None,
        continue_flag=None,
        session_file=None,
        supports_agents_json=False,
        supports_session_hook=False,
        supports_add_dir=False,
        log_format="plain",
    ),
    "vibe": HeadlessProvider(
        name="vibe",
        label="Mistral Vibe",
        binary="vibe",
        git_author_name="Vibe",
        git_author_email="noreply@mistral.ai",
        headless_subcommand=None,
        prompt_flag="--prompt",
        auto_approve_flags=("--auto-approve",),
        auto_approve_env={},
        output_format_flags=(),
        model_flag="--agent",
        max_turns_flag="--max-turns",
        verbose_flag=None,
        supports_session_resume=True,
        resume_flag="--resume",
        continue_flag="--continue",
        session_file="vibe-session.txt",
        supports_agents_json=False,
        supports_session_hook=False,
        supports_add_dir=False,
        log_format="plain",
    ),
    "blablador": HeadlessProvider(
        name="blablador",
        label="Blablador",
        binary="blablador",
        git_author_name="Blablador",
        git_author_email="noreply@hzdr.de",
        headless_subcommand="run",
        prompt_flag="",
        auto_approve_flags=(),
        auto_approve_env={"OPENCODE_PERMISSION": '{"*":"allow"}'},
        output_format_flags=(),
        model_flag=None,
        max_turns_flag=None,
        verbose_flag=None,
        supports_session_resume=True,
        resume_flag="--session",
        continue_flag="--continue",
        session_file="blablador-session.txt",
        supports_agents_json=False,
        supports_session_hook=False,
        supports_add_dir=False,
        log_format="plain",
    ),
    "opencode": HeadlessProvider(
        name="opencode",
        label="OpenCode",
        binary="opencode",
        git_author_name="OpenCode",
        git_author_email="noreply@opencode.ai",
        headless_subcommand="run",
        prompt_flag="",
        auto_approve_flags=(),
        auto_approve_env={"OPENCODE_PERMISSION": '{"*":"allow"}'},
        output_format_flags=(),
        model_flag="--model",
        max_turns_flag=None,
        verbose_flag=None,
        supports_session_resume=True,
        resume_flag="--session",
        continue_flag="--continue",
        session_file="opencode-session.txt",
        supports_agents_json=False,
        supports_session_hook=False,
        supports_add_dir=False,
        log_format="plain",
    ),
}

#: Valid provider names for CLI argument validation.
PROVIDER_NAMES: tuple[str, ...] = tuple(HEADLESS_PROVIDERS.keys())


def get_provider(name: str | None, project: ProjectConfig) -> HeadlessProvider:
    """Resolve a provider name to a ``HeadlessProvider``.

    Resolution order:
      1. Explicit *name* if given
      2. ``project.default_agent``
      3. ``"claude"`` (ultimate fallback)

    Raises ``SystemExit`` if the resolved name is not in the registry.
    """
    resolved = name or project.default_agent or "claude"
    provider = HEADLESS_PROVIDERS.get(resolved)
    if provider is None:
        valid = ", ".join(sorted(HEADLESS_PROVIDERS))
        raise SystemExit(f"Unknown headless provider {resolved!r}. Valid providers: {valid}")
    return provider


@dataclass(frozen=True)
class ProviderConfig:
    """Resolved per-run config for a headless provider.

    Produced by :func:`apply_provider_config` after best-effort feature mapping.
    """

    model: str | None
    """Model override for providers that support it, else ``None``."""

    max_turns: int | None
    """Max turns for providers that support it, else ``None``."""

    timeout: int
    """Effective timeout in seconds."""

    prompt_extra: str
    """Extra text to append to the prompt (best-effort feature analogues)."""

    warnings: tuple[str, ...]
    """Warnings about unsupported features (for user display)."""


@dataclass(frozen=True)
class CLIOverrides:
    """CLI flag overrides for a headless agent run."""

    model: str | None = None
    """Explicit ``--model`` from CLI (takes precedence over config)."""

    max_turns: int | None = None
    """Explicit ``--max-turns`` from CLI."""

    timeout: int | None = None
    """Explicit ``--timeout`` from CLI."""

    instructions: str | None = None
    """Resolved instructions text. Delivery is provider-aware."""


@dataclass(frozen=True)
class WrapperConfig:
    """Groups parameters for generating the Claude shell wrapper."""

    has_agents: bool
    project: ProjectConfig
    has_instructions: bool = False


def apply_provider_config(
    provider: HeadlessProvider,
    config: dict,
    overrides: CLIOverrides | None = None,
) -> ProviderConfig:
    """Resolve config values for a provider with best-effort feature mapping.

    CLI flag overrides take precedence over config values.  When the provider
    lacks a feature, an analogue is used where possible (e.g. injecting
    max-turns guidance into the prompt), and a warning is emitted for
    features that have no analogue.

    Args:
        config: Merged agent config dict (from :func:`resolve_agent_config`).
        overrides: CLI flag overrides (model, max_turns, timeout, instructions).
    """
    if overrides is None:
        overrides = CLIOverrides()
    from ..containers.agent_config import resolve_provider_value

    warnings: list[str] = []
    prompt_parts: list[str] = []

    # --- Model ---
    cfg_model = resolve_provider_value("model", config, provider.name)
    model = overrides.model or (str(cfg_model) if cfg_model is not None else None)
    if model and not provider.model_flag:
        warnings.append(
            f"{provider.label} does not support model selection; ignoring model={model!r}"
        )
        model = None

    # --- Max turns ---
    cfg_turns = resolve_provider_value("max_turns", config, provider.name)
    max_turns_raw = overrides.max_turns if overrides.max_turns is not None else cfg_turns
    max_turns: int | None = int(max_turns_raw) if max_turns_raw is not None else None
    if max_turns is not None and not provider.max_turns_flag:
        # Best-effort: inject into prompt as guidance
        prompt_parts.append(f"Important: complete this task in no more than {max_turns} steps.")
        warnings.append(
            f"{provider.label} does not support --max-turns; "
            f"added guidance to prompt instead ({max_turns} steps)"
        )
        max_turns = None

    # --- Timeout ---
    cfg_timeout = resolve_provider_value("timeout", config, provider.name)
    timeout = (
        overrides.timeout
        if overrides.timeout is not None
        else (int(cfg_timeout) if cfg_timeout is not None else 1800)
    )

    # --- Subagents (warning only — filtering is handled elsewhere) ---
    subagents = config.get("subagents")
    if subagents and not provider.supports_agents_json:
        warnings.append(
            f"{provider.label} does not support sub-agents (--agents); "
            f"sub-agent definitions will be ignored"
        )

    # --- Instructions ---
    # Claude receives instructions via --append-system-prompt in the wrapper.
    # Codex receives instructions via -c model_instructions_file=... in the wrapper.
    # OpenCode and Blablador receive instructions via opencode.json `instructions`
    # array (injected by prepare_agent_config_dir).
    # Remaining providers get best-effort prompt prepending.
    instructions = overrides.instructions
    if instructions and provider.name not in {"claude", "codex", "opencode", "blablador"}:
        prompt_parts.insert(0, instructions)

    return ProviderConfig(
        model=model,
        max_turns=max_turns,
        timeout=timeout,
        prompt_extra="\n".join(prompt_parts),
        warnings=tuple(warnings),
    )


def build_headless_command(
    provider: HeadlessProvider,
    *,
    timeout: int,
    model: str | None = None,
    max_turns: int | None = None,
) -> str:
    """Assemble the bash command string for a headless agent run.

    The command assumes:
    - ``init-ssh-and-repo.sh`` has already set up the workspace
    - The prompt is in ``/home/dev/.terok/prompt.txt``
    - For Claude, the ``claude()`` wrapper function is sourced via bash -l

    Returns a bash command string suitable for ``["bash", "-lc", cmd]``.
    """
    if provider.name == "claude":
        return _build_claude_command(provider, timeout=timeout, model=model, max_turns=max_turns)
    return _build_generic_command(provider, timeout=timeout, model=model, max_turns=max_turns)


def _build_claude_command(
    provider: HeadlessProvider,
    *,
    timeout: int,
    model: str | None,
    max_turns: int | None,
) -> str:
    """Build the headless command for Claude using the wrapper function."""
    # Claude uses the claude() wrapper from terok-agent.sh which handles
    # --dangerously-skip-permissions, --add-dir, --agents, git env, and timeout
    flags = ""
    if model:
        flags += f" --model {shlex.quote(model)}"
    if max_turns:
        flags += f" --max-turns {int(max_turns)}"

    return (
        f"init-ssh-and-repo.sh &&"
        f" claude --terok-timeout {timeout}"
        f" -p "
        '"$(cat /home/dev/.terok/prompt.txt)"'
        f"{flags} --output-format stream-json --verbose"
    )


def _build_generic_command(
    provider: HeadlessProvider,
    *,
    timeout: int,
    model: str | None,
    max_turns: int | None,
) -> str:
    """Build the headless command for non-Claude providers.

    Uses the shell wrapper function (e.g. ``codex()``) instead of invoking the
    binary directly, so that git env vars and session resume logic from
    ``terok-agent.sh`` are applied.  The wrapper parses ``--terok-timeout``
    to wrap the actual invocation with ``timeout``.
    """
    parts = ["init-ssh-and-repo.sh &&"]

    # Call the wrapper function (sourced via bash -l from profile.d);
    # it handles git identity env vars and session resume args.
    parts.append(provider.binary)
    parts.append("--terok-timeout")
    parts.append(str(int(timeout)))

    # Subcommand (e.g. "exec" for codex, "run" for opencode)
    if provider.headless_subcommand:
        parts.append(provider.headless_subcommand)

    # Auto-approve flags are injected by the wrapper function based on
    # TEROK_UNRESTRICTED env var — not here.  See _generate_generic_wrapper().

    # Model
    if model and provider.model_flag:
        parts.append(provider.model_flag)
        parts.append(shlex.quote(model))

    # Max turns
    if max_turns and provider.max_turns_flag:
        parts.append(provider.max_turns_flag)
        parts.append(str(int(max_turns)))

    # Output format
    for flag in provider.output_format_flags:
        parts.append(flag)

    # Verbose
    if provider.verbose_flag:
        parts.append(provider.verbose_flag)

    # Prompt — flag-based or positional
    if provider.prompt_flag:
        parts.append(provider.prompt_flag)
    parts.append('"$(cat /home/dev/.terok/prompt.txt)"')

    return " ".join(parts)


def generate_agent_wrapper(
    provider: HeadlessProvider,
    project: ProjectConfig,
    has_agents: bool,
    *,
    claude_wrapper_fn: Callable[[WrapperConfig], str] | None = None,
) -> str:
    """Generate the shell wrapper function content for a single provider.

    For Claude, uses *claude_wrapper_fn* (which should be
    ``agents._generate_claude_wrapper``) to produce the full wrapper with
    ``--dangerously-skip-permissions``, ``--add-dir /``, ``--agents``, and
    session resume support.  The function is passed in by the caller to
    avoid a circular import between this module and ``agents``.

    For other providers, produces a simpler wrapper that sets git env vars
    and delegates to the binary.  Instructions are delivered via
    ``opencode.json`` (OpenCode/Blablador), ``model_instructions_file``
    (Codex), or ``--append-system-prompt`` (Claude) — not via the wrapper.

    Args:
        claude_wrapper_fn: ``(cfg: WrapperConfig) -> str``.
            Required when ``provider.name == "claude"``.

    See also :func:`generate_all_wrappers` which produces wrappers for every
    registered provider in one file.
    """
    if provider.name == "claude":
        if claude_wrapper_fn is None:
            raise ValueError("claude_wrapper_fn is required for Claude provider")
        return claude_wrapper_fn(WrapperConfig(has_agents=has_agents, project=project))

    return _generate_generic_wrapper(provider, project)


def generate_all_wrappers(
    project: ProjectConfig,
    has_agents: bool,
    *,
    claude_wrapper_fn: Callable[[WrapperConfig], str] | None = None,
) -> str:
    """Generate shell wrappers for **all** registered providers in one file.

    The output file contains a shell function per provider (``claude()``,
    ``codex()``, ``vibe()``, etc.), each with correct git env vars, timeout
    support, and session resume logic.  This allows interactive CLI users to
    invoke any agent regardless of which provider was configured as default.

    Args:
        claude_wrapper_fn: Required — produces the Claude wrapper.
    """
    sections: list[str] = []
    for provider in HEADLESS_PROVIDERS.values():
        section = generate_agent_wrapper(
            provider,
            project,
            has_agents,
            claude_wrapper_fn=claude_wrapper_fn,
        )
        sections.append(section)
    return "\n".join(sections)


def _generate_generic_wrapper(provider: HeadlessProvider, project: ProjectConfig) -> str:
    """Generate a shell wrapper for non-Claude providers.

    Sets git identity env vars and wraps the binary with optional timeout
    support (``--terok-timeout``), matching the Claude wrapper's interface.

    Session resume logic (for providers with ``session_file``):

    - An OpenCode plugin (or post-run parse for Vibe) captures the session
      ID to ``/home/dev/.terok/<session_file>``.
    - Resume args (``--session <id>`` or ``--resume <id>``) are injected
      only in headless mode (``--terok-timeout`` present) or on bare
      interactive launch (no user args).
    - When the user passes their own arguments, passthrough is transparent
      — no resume args are injected.
    """
    author_name = shlex.quote(provider.git_author_name)
    author_email = shlex.quote(provider.git_author_email)
    binary = provider.binary

    lines = [
        "# Generated by terok",
        f"{binary}() {{",
        '    local _timeout=""',
        "    # Extract terok-specific flags (must come before agent flags)",
        "    while [[ $# -gt 0 ]]; do",
        '        case "$1" in',
        '            --terok-timeout) _timeout="$2"; shift 2 ;;',
        "            *) break ;;",
        "        esac",
        "    done",
        "    [ -r /usr/local/share/terok/terok-git-identity.sh ] && \\",
        "        . /usr/local/share/terok/terok-git-identity.sh",
    ]

    # Auto-approve flags and env vars, injected when TEROK_UNRESTRICTED=1.
    if provider.auto_approve_flags or provider.auto_approve_env:
        lines.append("    local _approve_args=()")
        lines.append('    if [ "${TEROK_UNRESTRICTED:-}" = "1" ]; then')
        for flag in provider.auto_approve_flags:
            lines.append(f"        _approve_args+=({shlex.quote(flag)})")
        for k, v in provider.auto_approve_env.items():
            lines.append(f"        export {k}={shlex.quote(v)}")
        lines.append("    fi")

    # OpenCode session plugin setup for opencode/blablador.
    if provider.session_file and provider.name in {"opencode", "blablador"}:
        plugin_dir = (
            "$HOME/.blablador/opencode/plugins"
            if provider.name == "blablador"
            else "$HOME/.config/opencode/plugins"
        )
        lines.append("    # Ensure OpenCode session plugin is installed")
        lines.append("    local _plugin_src=/usr/local/share/terok/opencode-session-plugin.mjs")
        lines.append(f"    local _plugin_dir={plugin_dir}")
        lines.append('    if [ -f "$_plugin_src" ]; then')
        lines.append('        mkdir -p "$_plugin_dir"')
        lines.append('        ln -sf "$_plugin_src" "$_plugin_dir/terok-session.mjs"')
        lines.append("    fi")

    # Session resume support for providers with session_file.
    # Resume args are only injected in headless mode (--terok-timeout present)
    # or on bare interactive launch (no user args — convenience for container
    # re-entry).  When the user provides their own args, passthrough is
    # transparent.
    session_path = f"/home/dev/.terok/{provider.session_file}" if provider.session_file else None
    if session_path and provider.resume_flag:
        lines.append("    local _resume_args=()")
        lines.append(f"    if [ -s {session_path} ] && \\")
        lines.append('       { [ -n "$_timeout" ] || [ $# -eq 0 ]; }; then')
        lines.append(f'        _resume_args+=({provider.resume_flag} "$(cat {session_path})")')
        lines.append("    fi")

    # Codex supports model_instructions_file in config; this injects the
    # mounted /home/dev/.terok/instructions.md into startup context for both
    # interactive CLI and headless exec runs.
    if provider.name == "codex":
        lines.append("    local _instr_args=()")
        lines.append("    [ -f /home/dev/.terok/instructions.md ] && \\")
        lines.append(
            "        _instr_args+=(-c "
            "'model_instructions_file=\"/home/dev/.terok/instructions.md\"')"
        )

    # Vibe session capture helper (no plugin system — parse logs post-run).
    if provider.name == "vibe" and session_path:
        lines.append("    _terok_capture_vibe_session() {")
        lines.append('        python3 -c "')
        lines.append("import json, os, glob")
        lines.append(
            "files = sorted(glob.glob(os.path.expanduser("
            "'~/.vibe/logs/session/session_*/meta.json')),"
        )
        lines.append("               key=os.path.getmtime, reverse=True)")
        lines.append("if files:")
        lines.append("    with open(files[0]) as f:")
        lines.append("        sid = json.load(f).get('session_id', '')")
        lines.append("    if sid:")
        lines.append("        print(sid)")
        lines.append(f'" > {session_path} 2>/dev/null || true')
        lines.append("    }")

    # Git env vars and exec — with optional timeout (headless mode)
    lines.append('    if [ -n "$_timeout" ]; then')
    lines.append("        (")
    lines.append(f"            _terok_apply_git_identity {author_name} {author_email}")
    if session_path:
        lines.append(f"            export TEROK_SESSION_FILE={session_path}")

    # Build the extra-args expansions that sit between the binary and "$@".
    has_approve = bool(provider.auto_approve_flags or provider.auto_approve_env)
    _extra_expansions: list[str] = []
    if has_approve:
        _extra_expansions.append('"${_approve_args[@]}"')
    if session_path and provider.resume_flag:
        _extra_expansions.append('"${_resume_args[@]}"')
    if provider.name == "codex":
        _extra_expansions.append('"${_instr_args[@]}"')
    extra = (" " + " ".join(_extra_expansions)) if _extra_expansions else ""

    lines.append(f'        timeout "$_timeout" {binary}{extra} "$@"')

    # Post-run: capture vibe session ID
    if provider.name == "vibe" and session_path:
        lines.append("        local _rc=$?; _terok_capture_vibe_session; return $_rc")

    lines.append("        )")

    # Interactive mode (no timeout)
    lines.append("    else")
    lines.append("        (")
    lines.append(f"            _terok_apply_git_identity {author_name} {author_email}")
    # Set session file env var for bare interactive launch only
    if session_path:
        lines.append(f"            export TEROK_SESSION_FILE={session_path}")

    lines.append(f'        command {binary}{extra} "$@"')

    lines.append("        )")
    lines.append("    fi")
    lines.append("}")

    return "\n".join(lines) + "\n"
