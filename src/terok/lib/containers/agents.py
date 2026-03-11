# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Agent configuration: parsing, filtering, and wrapper generation.

Handles .md frontmatter parsing, sub-agent JSON conversion for Claude's
``--agents`` flag, and the ``terok-agent.sh`` wrapper function that
sets up git identity and CLI flags inside task containers.
"""

import json
import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..core.config import get_envs_base_dir
from ..core.projects import ProjectConfig
from ..util.fs import ensure_dir, ensure_dir_writable
from .headless_providers import WrapperConfig

# TODO: future — support global agent definitions in terok-config.yml (agent.subagents).
# When implemented, global subagents would be merged with per-project subagents before
# filtering by default/selected. Use a generic merge approach that can be reused across
# different agent runtimes (Claude, Codex, OpenCode, etc.).


def parse_md_agent(file_path: str) -> dict:
    """Parse a .md file with YAML frontmatter into an agent dict.

    Expected format:
        ---
        name: agent-name
        description: ...
        tools: [Read, Grep]
        model: sonnet
        ---
        System prompt body...
    """
    path = Path(file_path)
    if not path.is_file():
        return {}
    content = path.read_text(encoding="utf-8")
    # Split YAML frontmatter from body
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1]) or {}
            if not isinstance(frontmatter, dict):
                frontmatter = {}
            body = parts[2].strip()
            frontmatter["prompt"] = body
            return frontmatter
    # No frontmatter: treat entire file as prompt
    return {"prompt": content.strip()}


# All native Claude agent fields to pass through to --agents JSON.
_CLAUDE_AGENT_FIELDS = frozenset(
    {
        "description",
        "tools",
        "disallowedTools",
        "model",
        "permissionMode",
        "mcpServers",
        "hooks",
        "maxTurns",
        "skills",
        "memory",
        "background",
        "isolation",
    }
)


def _subagents_to_json(
    subagents: list[dict],
    selected_agents: list[str] | None = None,
) -> str:
    """Convert sub-agent list to JSON dict string for --agents flag.

    Filters to include agents where default=True plus any agents whose
    name appears in selected_agents. Output is a JSON dict keyed by
    agent name (the format expected by Claude's --agents flag).

    - file: refs are parsed from .md YAML frontmatter + body
    - Inline defs: system_prompt -> prompt, pass through native Claude fields
    - Strips non-Claude fields: default, name (name becomes the dict key)
    """
    result: dict[str, dict] = {}
    selected = set(selected_agents) if selected_agents else set()

    for sa in subagents:
        # Resolve file references first
        if "file" in sa:
            agent = parse_md_agent(sa["file"])
            if not agent:
                continue
            # Merge the default flag from the YAML definition
            if "default" in sa:
                agent["default"] = sa["default"]
        else:
            agent = dict(sa)  # shallow copy

        name = agent.get("name")
        if not name:
            continue  # skip agents without a name

        # Filter: include if default=True OR if name in selected_agents
        is_default = agent.get("default", False)
        if not is_default and name not in selected:
            continue

        # Build the output entry
        entry: dict = {}
        for field in _CLAUDE_AGENT_FIELDS:
            if field in agent:
                entry[field] = agent[field]
        # Map system_prompt -> prompt
        if "system_prompt" in agent:
            entry["prompt"] = agent["system_prompt"]
        elif "prompt" in agent:
            entry["prompt"] = agent["prompt"]

        result[name] = entry

    return json.dumps(result)


def _generate_claude_wrapper(cfg: WrapperConfig) -> str:
    """Generate the terok-agent.sh wrapper function content for Claude.

    Always includes git env vars. Conditionally includes
    --dangerously-skip-permissions, --add-dir /, and --agents.

    The --add-dir / flag gives Claude full filesystem access inside the
    container. The container itself is the security boundary (Podman
    isolation), so restricting file access within it is unnecessary and
    actively harmful — agents need to read/write ~/.claude, /tmp, etc.

    Supports ``--terok-timeout <N>`` as the first argument to wrap the
    claude invocation with ``timeout N``.  This allows headless mode to
    use the same wrapper as interactive sessions (the wrapper is the
    single source of truth for CLI flags and git env vars).

    Model, max_turns, and other per-run flags are NOT included here —
    they are passed directly in the headless command or by the user
    in interactive mode.
    """
    author_name = shlex.quote("Claude")
    author_email = shlex.quote("noreply@anthropic.com")

    lines = [
        "# Generated by terok",
        "claude() {",
        '    local _timeout=""',
        "    # Extract terok-specific flags (must come before claude flags)",
        "    while [[ $# -gt 0 ]]; do",
        '        case "$1" in',
        '            --terok-timeout) _timeout="$2"; shift 2 ;;',
        "            *) break ;;",
        "        esac",
        "    done",
        "    local _args=()",
        "    [ -r /usr/local/share/terok/terok-git-identity.sh ] && \\",
        "        . /usr/local/share/terok/terok-git-identity.sh",
    ]

    # Auto-approve: inject --dangerously-skip-permissions when TEROK_UNRESTRICTED=1.
    # Same env-var mechanism as all other providers (see _generate_generic_wrapper).
    lines.append('    if [ "${TEROK_UNRESTRICTED:-}" = "1" ]; then')
    lines.append("        _args+=(--dangerously-skip-permissions)")
    lines.append("    fi")

    # Give Claude unrestricted filesystem access inside the container.
    # The Podman container itself provides isolation — no need for an
    # additional sandbox layer within it.
    lines.append('    _args+=(--add-dir "/")')

    if cfg.has_agents:
        lines.append("    [ -f /home/dev/.terok/agents.json ] && \\")
        lines.append('        _args+=(--agents "$(cat /home/dev/.terok/agents.json)")')

    if cfg.has_instructions:
        lines.append("    [ -f /home/dev/.terok/instructions.md ] && \\")
        lines.append(
            '        _args+=(--append-system-prompt "$(cat /home/dev/.terok/instructions.md)")'
        )

    # Resume previous session if session file exists (written by SessionStart hook)
    lines.append("    [ -s /home/dev/.terok/claude-session.txt ] && \\")
    lines.append('        _args+=(--resume "$(cat /home/dev/.terok/claude-session.txt)")')

    # Git env vars and exec — with optional timeout
    lines.append('    if [ -n "$_timeout" ]; then')
    lines.append("        (")
    lines.append(f"            _terok_apply_git_identity {author_name} {author_email}")
    lines.append(
        "            export CLAUDE_COWORK_MEMORY_PATH_OVERRIDE="
        '"/home/dev/.claude/projects/${PROJECT_ID}-workspace/memory"'
    )
    lines.append('            timeout "$_timeout" claude "${_args[@]}" "$@"')
    lines.append("        )")
    lines.append("    else")
    lines.append("        (")
    lines.append(f"            _terok_apply_git_identity {author_name} {author_email}")
    lines.append(
        "            export CLAUDE_COWORK_MEMORY_PATH_OVERRIDE="
        '"/home/dev/.claude/projects/${PROJECT_ID}-workspace/memory"'
    )
    lines.append('            command claude "${_args[@]}" "$@"')
    lines.append("        )")
    lines.append("    fi")
    lines.append("}")

    return "\n".join(lines) + "\n"


def _write_session_hook(settings_path: Path) -> None:
    """Write a Claude project settings file with a SessionStart hook.

    ``settings_path`` currently points at the shared Claude config mount
    (``<envs>/_claude-config/settings.json``), so this function must be
    idempotent across many task launches/projects.

    The hook captures the session ID to ``/home/dev/.terok/claude-session.txt``
    on every session start.  That path is in the per-task ``agent-config`` mount,
    so session IDs remain task-local even though the hook definition is shared.
    The wrapper reads this file to add ``--resume`` on subsequent invocations,
    enabling session continuity across container restarts.

    If the settings file already exists, the hook config is merged into it
    (preserving any existing settings).

    Updates are serialized with an inter-process file lock and persisted via
    atomic replace to avoid clobbering concurrent task launches.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - fcntl is unavailable on some platforms.
        fcntl = None  # type: ignore[assignment]

    hook_command = (
        "python3 -c \"import json,sys; print(json.load(sys.stdin)['session_id'])\""
        " > /home/dev/.terok/claude-session.txt"
    )
    hook_entry = {"hooks": [{"type": "command", "command": hook_command}]}
    lock_path = settings_path.with_suffix(settings_path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            if settings_path.is_file():
                try:
                    loaded = json.loads(settings_path.read_text(encoding="utf-8"))
                    existing = loaded if isinstance(loaded, dict) else {}
                except (json.JSONDecodeError, OSError):
                    existing = {}
            else:
                existing = {}

            changed = False

            hooks_obj = existing.get("hooks")
            if hooks_obj is None or not isinstance(hooks_obj, dict):
                hooks_obj = {}
                existing["hooks"] = hooks_obj
                changed = True

            session_hooks_obj = hooks_obj.get("SessionStart")
            if session_hooks_obj is None or not isinstance(session_hooks_obj, list):
                session_hooks_obj = []
                hooks_obj["SessionStart"] = session_hooks_obj
                changed = True

            session_hooks: list[object] = session_hooks_obj

            # Idempotent across equivalent forms: skip append if an existing SessionStart
            # command already writes session_id to claude-session.txt.
            hook_present = False
            for item in session_hooks:
                if item == hook_entry:
                    hook_present = True
                    break
                if not isinstance(item, dict):
                    continue
                nested = item.get("hooks")
                if not isinstance(nested, list):
                    continue
                for nested_item in nested:
                    if not isinstance(nested_item, dict):
                        continue
                    if nested_item.get("command") == hook_command:
                        hook_present = True
                        break
                if hook_present:
                    break

            if not hook_present:
                session_hooks.append(hook_entry)
                changed = True

            if changed:
                tmp_path: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        "w",
                        encoding="utf-8",
                        dir=settings_path.parent,
                        delete=False,
                    ) as tmp_file:
                        tmp_file.write(json.dumps(existing, indent=2) + "\n")
                        tmp_path = Path(tmp_file.name)
                    os.replace(tmp_path, settings_path)
                finally:
                    if tmp_path is not None and tmp_path.exists():
                        tmp_path.unlink()
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _inject_opencode_instructions(config_path: Path) -> None:
    """Inject the instructions file path into an opencode.json config.

    Ensures the ``"instructions"`` key is a list containing the container-local
    path ``"/home/dev/.terok/instructions.md"``.  If the file does not exist it
    is created with the required ``$schema`` key.  If the instructions entry is already present the
    file is left untouched (idempotent).

    Uses the same inter-process file lock + atomic-replace pattern as
    :func:`_write_session_hook` for concurrency safety.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - fcntl is unavailable on some platforms.
        fcntl = None  # type: ignore[assignment]

    instr_path = "/home/dev/.terok/instructions.md"
    _SCHEMA_URL = "https://opencode.ai/config.json"

    lock_path = config_path.with_suffix(config_path.suffix + ".lock")
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            if config_path.is_file():
                try:
                    loaded = json.loads(config_path.read_text(encoding="utf-8"))
                    existing = loaded if isinstance(loaded, dict) else {}
                except (json.JSONDecodeError, OSError):
                    existing = {}
            else:
                existing = {}

            # Ensure the $schema key is always present for a valid opencode.json.
            existing.setdefault("$schema", _SCHEMA_URL)

            instructions = existing.get("instructions")
            if isinstance(instructions, list) and instr_path in instructions:
                return  # already present

            if isinstance(instructions, list):
                instructions.append(instr_path)
            else:
                existing["instructions"] = [instr_path]

            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w",
                    encoding="utf-8",
                    dir=config_path.parent,
                    delete=False,
                ) as tmp_file:
                    tmp_file.write(json.dumps(existing, indent=2) + "\n")
                    tmp_path = Path(tmp_file.name)
                os.replace(tmp_path, config_path)
            finally:
                if tmp_path is not None and tmp_path.exists():
                    tmp_path.unlink()
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class AgentConfigSpec:
    """Groups parameters for preparing an agent-config directory."""

    project: ProjectConfig
    task_id: str
    subagents: tuple[dict, ...]
    selected_agents: tuple[str, ...] | None = None
    prompt: str | None = None
    provider: str = "claude"
    instructions: str | None = None

    def __post_init__(self) -> None:
        """Coerce mutable sequences to tuples for true immutability."""
        if isinstance(self.subagents, list):
            object.__setattr__(self, "subagents", tuple(self.subagents))
        if isinstance(self.selected_agents, list):
            object.__setattr__(self, "selected_agents", tuple(self.selected_agents))


def prepare_agent_config_dir(spec: AgentConfigSpec) -> Path:
    """Create and populate the agent-config directory for a task.

    Writes:
    - terok-agent.sh (always) — wrapper functions with git env vars
    - agents.json (only when provider supports it and sub-agents are non-empty)
    - prompt.txt (if prompt given, headless only)
    - instructions.md (always) — custom instructions or a neutral default
    - <envs>/_claude-config/settings.json — SessionStart hook (Claude only)
    - opencode.json entries — ``instructions`` path injected into shared
      OpenCode and Blablador configs

    Args:
        spec: All agent-config parameters bundled in an :class:`AgentConfigSpec`.

    Returns the agent_config_dir path.
    """
    from .headless_providers import get_provider as _get_provider

    resolved = _get_provider(spec.provider, spec.project)

    task_dir = spec.project.tasks_root / str(spec.task_id)
    agent_config_dir = task_dir / "agent-config"
    ensure_dir(agent_config_dir)

    # Build agents JSON — only for providers that support --agents (Claude)
    has_agents = False
    if resolved.supports_agents_json and spec.subagents:
        agents_json = _subagents_to_json(spec.subagents, spec.selected_agents)
        agents_dict = json.loads(agents_json)
        if agents_dict:  # non-empty dict
            (agent_config_dir / "agents.json").write_text(agents_json, encoding="utf-8")
            has_agents = True
    elif spec.subagents or spec.selected_agents:
        import warnings

        warnings.warn(
            f"{resolved.label} does not support sub-agents (--agents); "
            f"sub-agent definitions will be ignored.",
            stacklevel=2,
        )

    # Write instructions file — always present so opencode.json `instructions`
    # references never point to a missing file.  When no custom instructions
    # are configured, a neutral default is used.
    _DEFAULT_INSTRUCTIONS = "Follow the project's coding conventions and existing patterns."

    has_instructions = bool(spec.instructions)
    instructions_text = spec.instructions or _DEFAULT_INSTRUCTIONS
    (agent_config_dir / "instructions.md").write_text(instructions_text, encoding="utf-8")

    # Inject instructions path into opencode.json configs on the host so
    # both opencode and blablador discover them natively (works for both
    # interactive and headless modes).
    envs_base = get_envs_base_dir()
    _inject_opencode_instructions(envs_base / "_opencode-config" / "opencode.json")
    _inject_opencode_instructions(envs_base / "_blablador-config" / "opencode" / "opencode.json")

    # Write shell wrapper functions for ALL providers so interactive CLI users
    # can invoke any agent (each provider gets its own shell function).
    from .headless_providers import generate_all_wrappers

    def _claude_wrapper_with_instructions(cfg: WrapperConfig) -> str:
        """Wrap _generate_claude_wrapper with the resolved has_instructions flag."""
        return _generate_claude_wrapper(
            WrapperConfig(
                has_agents=cfg.has_agents,
                project=cfg.project,
                has_instructions=has_instructions,
            )
        )

    wrapper = generate_all_wrappers(
        spec.project,
        has_agents,
        claude_wrapper_fn=_claude_wrapper_with_instructions,
    )
    (agent_config_dir / "terok-agent.sh").write_text(wrapper, encoding="utf-8")

    # Write SessionStart hook — only for providers that support it (Claude)
    if resolved.supports_session_hook:
        shared_claude_dir = get_envs_base_dir() / "_claude-config"
        ensure_dir_writable(shared_claude_dir, "_claude-config")
        _write_session_hook(shared_claude_dir / "settings.json")

    # Prompt (headless only)
    if spec.prompt is not None:
        (agent_config_dir / "prompt.txt").write_text(spec.prompt, encoding="utf-8")

    return agent_config_dir
