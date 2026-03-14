# Agent Configuration Compatibility Matrix

Last verified: 2026-03-14

This document tracks how each supported AI agent CLI handles configuration
relevant to terok's container integration. It covers both direct CLI invocation
(via shell wrappers in `terok-agent.sh`) and ACP invocation (via Toad or future
ACP clients).

See [Agent Permission Mode Architecture](developer.md#agent-permission-mode-architecture)
for how terok's `TEROK_UNRESTRICTED` env var drives permission mode inside
containers.

**Agent priority tiers** — Tier-1 (primary targets): Claude, Vibe, Blablador;
Tier-2: Codex, local LLM via OpenCode; Tier-3: Copilot.

## Quick Reference

| Agent | Unrestricted CLI | Unrestricted env var | Unrestricted config | ACP adapter | ACP unrestricted mechanism |
|-------|-----------------|---------------------|--------------------|----|---------------------------|
| Claude | `--dangerously-skip-permissions` | — | `permissions.defaultMode` in settings.json | `claude-code-acp` (npm) | `/etc/claude-code/managed-settings.json` |
| Vibe | `--agent auto-approve` | `VIBE_AUTO_APPROVE=true` | `auto_approve = true` in TOML | `vibe-acp` (bundled) | same env var / config |
| Blablador | (inherits OpenCode) | `OPENCODE_PERMISSION='{"*":"allow"}'` | `"permission": {"*":"allow"}` in opencode.json | needs wrapper (#410) | same env var / config |
| OpenCode | — | `OPENCODE_PERMISSION='{"*":"allow"}'` | `"permission": {"*":"allow"}` in opencode.json | `opencode acp` (native) | same env var / config |
| Codex | `--full-auto` or `--yolo` | — | `approval_policy` + `sandbox_mode` in config.toml | `codex-acp` (npm) | `-c` flags or `/etc/codex/config.toml` |
| Copilot | `--allow-all-tools` or `--yolo` | — | unstable (`permissions.allow` bug) | `copilot --acp` (native) | spawn flags (`--yolo` with `--acp`) |

### Current terok integration status

The `TEROK_UNRESTRICTED` env var drives per-task permission mode. Shell wrappers
check it at runtime and inject provider-specific flags/env vars. This covers the
**direct CLI path**. The **ACP path** (via Toad) is not yet covered — agents
launched by Toad do not see `TEROK_UNRESTRICTED` and use their own defaults.

| Agent | terok wrapper | `auto_approve_flags` | `auto_approve_env` | ACP covered? |
|-------|-------------|---------------------|-------------------|--------------|
| Claude | `--dangerously-skip-permissions` | Yes | — | **No** |
| Vibe | `--auto-approve` | Yes | — | **No** (env var not set) |
| Blablador | (via OpenCode) | — | `OPENCODE_PERMISSION` | **Partially** (env var works if set) |
| OpenCode | (via OpenCode) | — | `OPENCODE_PERMISSION` | **Partially** (env var works if set) |
| Codex | `--dangerously-bypass-approvals-and-sandbox` | Yes | — | **No** |
| Copilot | `--allow-all-tools` | Yes | — | **No** |

## Detailed Agent Profiles

### Claude Code

**Binary:** `claude`
**ACP adapter:** `@zed-industries/claude-code-acp` (npm, `claude-code-acp`)
**Config dir:** `~/.claude/` (override: `CLAUDE_CONFIG_DIR`)
**Tier:** 1

#### Unrestricted mode

| Mechanism | Details | Per-task? | CLI? | ACP? |
|-----------|---------|-----------|------|------|
| CLI flag | `--dangerously-skip-permissions` | Yes (per-invocation) | Yes | No |
| CLI flag | `--permission-mode bypassPermissions` | Yes (per-invocation) | Yes | No |
| User settings | `~/.claude/settings.json` → `permissions.defaultMode` | No (shared mount) | Yes | Yes |
| Project settings | `<cwd>/.claude/settings.json` or `.local.json` | Invades git repo | Yes | Yes |
| Managed settings | `/etc/claude-code/managed-settings.json` | Per-container | Yes | Yes |

Settings precedence (highest wins):

1. **Managed settings** (`/etc/claude-code/managed-settings.json`) — cannot be overridden
2. CLI arguments
3. Local project settings (`<cwd>/.claude/settings.local.json`)
4. Shared project settings (`<cwd>/.claude/settings.json`)
5. User settings (`~/.claude/settings.json`)

Valid `permissions.defaultMode` values: `default`, `acceptEdits`, `plan`,
`dontAsk`, `bypassPermissions` (aliases: `bypass`). Case-insensitive.

Root check: `bypassPermissions` is blocked when running as root (uid 0) unless
`IS_SANDBOX` env var is set. terok runs as `dev` (uid 1000), not affected.

Managed-only settings (only effective in managed-settings.json):
`disableBypassPermissionsMode`, `allowManagedPermissionRulesOnly`,
`allowManagedHooksOnly`, `allowManagedMcpServersOnly`.

**ACP gap:** The ACP adapter reads `permissions.defaultMode` from the settings
stack. Since `~/.claude/settings.json` is shared across tasks (mounted volume),
it cannot carry per-task permission mode. The managed-settings.json at `/etc/`
is per-container (each task = own container) and has highest precedence, making
it the correct mechanism for ACP.

#### System instructions

| Mechanism | Details | CLI? | ACP? |
|-----------|---------|------|------|
| CLI flag | `--append-system-prompt "text"` | Yes | No |
| CLI flag | `--append-system-prompt-file ./file` | Yes | No |
| CLI flag | `--system-prompt "text"` (replaces default) | Yes | No |
| File convention | `CLAUDE.md` in project tree | Yes | Yes |
| ACP `_meta` | `_meta.systemPrompt` string or `{append: "text"}` | No | Yes |

#### Headless/prompt delivery

| Flag | Details |
|------|---------|
| `-p "prompt"` | Print mode (non-interactive) |
| `--resume` / `-r` | Resume session by ID |
| `--continue` / `-c` | Continue most recent session |
| Stdin | `cat file \| claude -p "explain"` |
| `--output-format stream-json` | Structured output |
| `--max-turns N` | Limit agentic turns |
| `--max-budget-usd N` | Spending cap |

---

### Mistral Vibe

**Binary:** `vibe`
**ACP adapter:** `vibe-acp` (bundled entry point in `mistral-vibe` package)
**Config dir:** `~/.vibe/` (override: `VIBE_HOME`)
**Tier:** 1

#### Unrestricted mode

| Mechanism | Details | Per-task? | CLI? | ACP? |
|-----------|---------|-----------|------|------|
| Agent selection | `--agent auto-approve` | Yes (per-invocation) | Yes | No |
| Programmatic mode | `-p` auto-selects `auto-approve` agent | Yes | Yes | N/A |
| Env var | `VIBE_AUTO_APPROVE=true` | Yes (container env) | Yes | Yes |
| Config (TOML) | `auto_approve = true` in `~/.vibe/config.toml` | No (shared if mounted) | Yes | Yes |
| Project config | `.vibe/config.toml` (in trusted folders) | Invades repo | Yes | Yes |
| Runtime toggle | `Shift+Tab` in interactive TUI | Session-level | Yes | N/A |

Config loading precedence (highest wins):

1. Constructor kwargs / init_settings
2. `VIBE_*` environment variables
3. TOML config file (project-level if trusted, else user-level)

All `VibeConfig` fields are overridable via `VIBE_<FIELD_NAME>` env vars
(case-insensitive, via pydantic-settings).

**ACP note:** `vibe-acp` defaults to the `DEFAULT` agent (`auto_approve=False`),
unlike `-p` mode which auto-selects the `auto-approve` agent.
`VIBE_AUTO_APPROVE=true` env var is the reliable cross-path mechanism.

#### System instructions

| Mechanism | Details | CLI? | ACP? |
|-----------|---------|------|------|
| Config field | `system_prompt_id` in config.toml | Yes | Yes |
| Env var | `VIBE_SYSTEM_PROMPT_ID=my_prompt` | Yes | Yes |
| Custom prompts | `~/.vibe/prompts/<id>.md` or `.vibe/prompts/<id>.md` | Yes | Yes |
| File convention | `AGENTS.md`, `VIBE.md`, `.vibe.md` in project root | Yes | Yes |

No `--system-prompt` or `--append-system-prompt` CLI flag exists.

#### Headless/prompt delivery

| Flag | Details |
|------|---------|
| `-p "prompt"` | Programmatic mode (auto-selects `auto-approve` agent) |
| Stdin | `echo "text" \| vibe -p` |
| `--max-turns N` | Limit turns |
| `--max-price DOLLARS` | Cost limit |
| `--output text\|json\|streaming` | Output format |
| `--resume` / `--continue` | Session resume |

---

### OpenCode / Blablador

**Binary:** `opencode` (Blablador: `blablador` wrapper → `opencode`)
**ACP adapter:** `opencode acp` (native subcommand)
**Config dir:** `~/.config/opencode/` (override: `OPENCODE_CONFIG`)
**Blablador config:** `~/.blablador/opencode/opencode.json` (via `OPENCODE_CONFIG`)
**Tier:** 1 (Blablador), 2 (OpenCode standalone)

#### Unrestricted mode

| Mechanism | Details | Per-task? | CLI? | ACP? |
|-----------|---------|-----------|------|------|
| Env var | `OPENCODE_PERMISSION='{"*":"allow"}'` | Yes (container env) | Yes | Yes |
| Config (JSON) | `"permission": {"*": "allow"}` in opencode.json | Per config scope | Yes | Yes |
| Inline config | `OPENCODE_CONFIG_CONTENT='{"permission":"allow"}'` | Yes (env var) | Yes | Yes |
| Managed config | `/etc/opencode/` on Linux | Per-container | Yes | Yes |

No CLI flag for unrestricted mode. `opencode run` auto-rejects all permission
requests by default. `OPENCODE_PERMISSION` is applied after all config layers
via `mergeDeep`.

Config loading precedence (highest wins):

1. Managed config (`/etc/opencode/` on Linux)
2. Account/org config (OpenCode Console)
3. `OPENCODE_CONFIG_CONTENT` env var
4. `.opencode/` directories (project + global)
5. Project config (`opencode.json` via `findUp`)
6. `OPENCODE_CONFIG` env var (custom path)
7. Global config (`~/.config/opencode/opencode.json`)

Then `OPENCODE_PERMISSION` is merged on top of the final result.

Valid permission values: `"allow"`, `"ask"`, `"deny"`. Can be global (`"*"`)
or per-tool (`"bash"`, `"edit"`, `"read"`, etc.) with pattern sub-rules.

**Blablador note:** The `blablador` wrapper sets `OPENCODE_CONFIG` to point at
`~/.blablador/opencode/opencode.json` which already contains
`"permission": {"*": "allow"}`. For ACP, a `blablador-acp` wrapper would need
to do the same. See #410.

#### System instructions

| Mechanism | Details | CLI? | ACP? |
|-----------|---------|------|------|
| Config array | `"instructions": ["path", "glob", "url"]` in opencode.json | Yes | Yes |
| Env var | `OPENCODE_CONFIG_CONTENT` with instructions array | Yes | Yes |
| File convention | `AGENTS.md`, `CLAUDE.md`, `CONTEXT.md` via findUp | Yes | Yes |
| Global files | `~/.config/opencode/AGENTS.md`, `~/.claude/CLAUDE.md` | Yes | Yes |

Instructions from multiple config layers are concatenated (not replaced).

#### Headless/prompt delivery

| Flag | Details |
|------|---------|
| `opencode run "prompt"` | Non-interactive mode |
| Stdin | Content piped when not a TTY |
| `--model provider/model` | Model selection |
| `--session ID` / `--continue` / `--fork` | Session management |
| `--format default\|json` | Output format |

---

### OpenAI Codex

**Binary:** `codex`
**ACP adapter:** `@zed-industries/codex-acp` (npm)
**Config dir:** `~/.codex/` (override: `CODEX_HOME`)
**Tier:** 2

#### Unrestricted mode

| Mechanism | Details | Per-task? | CLI? | ACP? |
|-----------|---------|-----------|------|------|
| CLI flag | `--full-auto` (sandbox=workspace-write + approval=on-request) | Yes | Yes | No |
| CLI flag | `--yolo` (sandbox=danger-full-access + approval=never) | Yes | Yes | No |
| CLI flag | `-a never -s danger-full-access` | Yes | Yes | No |
| `-c` override | `-c approval_policy=never -c sandbox_mode=danger-full-access` | Yes | **Yes** |
| User config | `~/.codex/config.toml` | No (shared if mounted) | Yes | Yes |
| System config | `/etc/codex/config.toml` | Per-container | Yes | Yes |
| Project config | `.codex/config.toml` (requires `trust_level = "trusted"`) | Invades repo | Yes | Yes |
| Requirements | `/etc/codex/requirements.toml` | Per-container | Yes | Yes |

`approval_policy` values: `"untrusted"`, `"on-failure"` (deprecated),
`"on-request"` (default), `"never"`.

`sandbox_mode` values: `"read-only"` (default), `"workspace-write"`,
`"danger-full-access"`.

Config precedence (lowest to highest): MDM → system (`/etc/codex/config.toml`)
→ user (`~/.codex/config.toml`) → project (`.codex/config.toml`, trust
required) → `-c` session flags.

Enterprise: `/etc/codex/requirements.toml` can **restrict** which policies are
allowed (e.g., `allowed_approval_policies = ["on-request"]` prevents `never`).

**ACP note:** `codex-acp` uses the same `CliConfigOverrides` parser — it
accepts `-c approval_policy=never -c sandbox_mode=danger-full-access`. With
these flags, no permission requests are sent to the ACP client. The adapter
also reads the full config stack including `/etc/codex/config.toml`.

No env vars control approval/sandbox behaviour.

#### System instructions

| Mechanism | Details | CLI? | ACP? |
|-----------|---------|------|------|
| Config (TOML) | `instructions` — appended as context | Yes | Yes |
| Config (TOML) | `developer_instructions` — developer role message | Yes | Yes |
| Config (TOML) | `model_instructions_file` — replaces base (discouraged) | Yes | Yes |
| `-c` override | `-c model_instructions_file=/path/to/file` | Yes | Yes |
| File convention | `AGENTS.md` files in project tree | Yes | Yes |

#### Headless/prompt delivery

| Flag | Details |
|------|---------|
| `codex exec "prompt"` | Non-interactive mode |
| `--json` | JSONL output |
| `--output-last-message FILE` | Write final message to file |
| `--add-dir DIR` | Additional writable directories |
| `--ephemeral` | No session persistence |

---

### GitHub Copilot

**Binary:** `copilot`
**ACP adapter:** Native (`copilot --acp --stdio`)
**Config dir:** `~/.copilot/` (override: `COPILOT_HOME` or `--config-dir`)
**Tier:** 3

#### Unrestricted mode

| Mechanism | Details | Per-task? | CLI? | ACP? |
|-----------|---------|-----------|------|------|
| CLI flag | `--allow-all-tools` | Yes | Yes | **Yes** (with `--acp`) |
| CLI flag | `--allow-all` / `--yolo` | Yes | Yes | **Yes** (with `--acp`) |
| Granular | `--allow-tool='shell(git)'`, `--deny-tool=TYPE` | Yes | Yes | Yes |
| Config file | `permissions.allow` in config.json | **Unstable** (bug: cleared on startup) | — | — |
| SDK default | SDK `PermissionHandler.ApproveAll` | N/A | No | Yes (SDK only) |

No env vars control permission mode. No stable config file mechanism exists
(feature requests #179 and #307 still open upstream).

**ACP note:** `copilot --acp` accepts permission flags at spawn time (confirmed
v0.0.400). Per-session permission control via ACP protocol is NOT supported
(gap tracked in upstream issue #1607). The Copilot SDK defaults to `approveAll`.

`-p` (prompt mode) auto-denies all permissions — must combine with
`--allow-all-tools`.

Enterprise: org admins can disable Copilot CLI entirely but cannot set granular
permission policies.

#### System instructions

| Mechanism | Details | CLI? | ACP? |
|-----------|---------|------|------|
| Repo-level | `.github/copilot-instructions.md` | Yes | Yes |
| Path-specific | `.github/instructions/**/*.instructions.md` | Yes | Yes |
| File convention | `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` in project root | Yes | Yes |
| User-level | `~/.copilot/copilot-instructions.md` | Yes | Yes |
| User-level dir | `~/.copilot/instructions/*.instructions.md` | Yes | Yes |
| Custom agents | `~/.copilot/agents/` or `.github/agents/` | Yes | Yes |
| Env var | `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` | Yes | Yes |

#### Headless/prompt delivery

| Flag | Details |
|------|---------|
| `-p "prompt"` | Non-interactive (auto-denies permissions!) |
| `-p "prompt" --allow-all-tools` | Non-interactive with permissions |
| `--output-format json` | Structured JSONL output |
| `--model`, `--agent NAME` | Model/agent selection |
| `--resume` / `--continue` | Session management |

---

## Cross-Cutting Analysis

### ACP permission gap and recommended solution

terok's `TEROK_UNRESTRICTED` env var drives CLI wrapper permission mode
per-task. ACP adapters (launched by Toad) do not use the wrappers and thus
bypass this mechanism. Each agent needs a separate ACP-compatible mechanism
that is per-container (= per-task):

| Agent | Best ACP mechanism | How it works |
|-------|-------------------|--------------|
| Claude | `/etc/claude-code/managed-settings.json` | Highest-precedence config, per-container filesystem |
| Vibe | `VIBE_AUTO_APPROVE=true` container env var | pydantic-settings reads `VIBE_*` env vars |
| OpenCode/Blablador | `OPENCODE_PERMISSION` container env var | Applied after all config layers |
| Codex | `/etc/codex/config.toml` | System config, per-container filesystem |
| Copilot | Spawn flags (`--yolo` with `--acp`) | Only mechanism available |

For Tier-1 agents (Claude, Vibe, Blablador/OpenCode), non-CLI mechanisms exist
that work for both direct invocation and ACP. For Codex, the system config file
works. For Copilot, CLI flags are the only option.

### Unified per-task permission approach

Each task runs in its own Podman container, so per-container = per-task:

**When `TEROK_UNRESTRICTED=1`** (container env var set by host):

1. Shell wrappers inject CLI flags (existing, works today)
2. ACP mechanisms should also be activated:
   - Write `/etc/claude-code/managed-settings.json` with `bypassPermissions`
   - Set `VIBE_AUTO_APPROVE=true` in container env
   - Set `OPENCODE_PERMISSION='{"*":"allow"}'` in container env (already done via `auto_approve_env`)
   - Write `/etc/codex/config.toml` with `approval_policy = "never"` + `sandbox_mode = "danger-full-access"`
   - Toad should spawn Copilot with `--allow-all-tools`

**When `TEROK_UNRESTRICTED` is unset** (restricted mode):

- Wrappers omit flags (existing, works today)
- ACP: omit managed-settings files, omit env vars, Toad spawns without flags
- Agents use their vendor defaults (prompt for each action)

### Instruction delivery summary

| Agent | terok mechanism | Notes |
|-------|----------------|-------|
| Claude | `--append-system-prompt` (wrapper) | ACP: via `_meta.systemPrompt.append` or `CLAUDE.md` |
| Vibe | `AGENTS.md` / `VIBE.md` in workspace | No CLI flag; config or file convention only |
| OpenCode | `instructions` array in opencode.json | Injected by terok on host side |
| Codex | `AGENTS.md` in workspace | `-c` overrides work for ACP too |
| Copilot | `AGENTS.md` in workspace | `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` env var |
