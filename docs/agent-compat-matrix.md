# Agent Configuration Compatibility Matrix

Last verified: 2026-03-14. Re-verify quarterly and whenever an agent version
update breaks the existing integration.

Per-agent reference for permission control, instruction delivery, and ACP
integration. See [Agent Permission Mode Architecture](developer.md#agent-permission-mode-architecture)
for how `TEROK_UNRESTRICTED` drives permission mode inside containers.

**Agent priority tiers** — Tier-1: Claude, Vibe, Blablador; Tier-2: Codex,
local LLM via OpenCode; Tier-3: Copilot.

## Unrestricted Mode

| Agent | CLI flag | Env var | Config file | ACP adapter | terok uses (per-task) |
|-------|----------|---------|-------------|-------------|----------------------|
| Claude | `--dangerously-skip-permissions` | — | `permissions.defaultMode: bypassPermissions` in settings.json | `claude-code-acp` (npm) | `/etc/claude-code/managed-settings.json` |
| Vibe | `--agent auto-approve` | `VIBE_AUTO_APPROVE=true` | `auto_approve = true` in TOML | `vibe-acp` (bundled) | `VIBE_AUTO_APPROVE` env var |
| Blablador | (inherits OpenCode) | `OPENCODE_PERMISSION='{"*":"allow"}'` | `"permission": {"*":"allow"}` in opencode.json | needs wrapper (#410) | `OPENCODE_PERMISSION` env var |
| OpenCode | — | `OPENCODE_PERMISSION='{"*":"allow"}'` | `"permission": {"*":"allow"}` in opencode.json | `opencode acp` (native) | `OPENCODE_PERMISSION` env var |
| Codex | `--yolo` | — | `approval_policy` + `sandbox_mode` in config.toml | `codex-acp` (npm) | `/etc/codex/requirements.toml` |
| Copilot | `--yolo` / `--allow-all` | `COPILOT_ALLOW_ALL=true` | — (unstable) | `copilot --acp` (native) | `COPILOT_ALLOW_ALL` env var |

### Design constraint: shared volumes

`~/.claude/`, `~/.codex/`, `~/.vibe/`, `~/.config/opencode/` are shared
volume mounts (for auth and session persistence). Config written there
affects ALL tasks, not just the current one. Per-task permission mode
MUST NOT use shared-volume config files.

### What terok uses

Every agent has a mechanism that is (a) per-container and (b) read
regardless of launch path (CLI wrapper, ACP, or direct invocation):

| Agent | Per-container mechanism | Why not the alternative |
|-------|------------------------|------------------------|
| Claude | `/etc/claude-code/managed-settings.json` | `~/.claude/` is shared; managed settings have highest precedence |
| Codex | `/etc/codex/requirements.toml` | `~/.codex/` is shared; requirements have highest precedence |
| Vibe | `VIBE_AUTO_APPROVE=true` env var | pydantic-settings: env var overrides all config layers |
| OpenCode | `OPENCODE_PERMISSION` env var | merged on top of all config layers |
| Blablador | `OPENCODE_PERMISSION` env var | inherits OpenCode's mechanism |
| Copilot | `COPILOT_ALLOW_ALL=true` env var | no stable config file mechanism |

Shell wrappers additionally inject CLI flags (`--dangerously-skip-permissions`,
`--yolo`, `--agent auto-approve`, etc.) as redundant reinforcement for the
CLI path. The env vars and config files are the authoritative mechanism.

When `TEROK_UNRESTRICTED` is unset: config files are not written and env
vars are not set; agents use vendor defaults.

## Instruction Delivery

| Agent | terok mechanism | ACP notes |
|-------|----------------|-----------|
| Claude | `--append-system-prompt` (wrapper) | ACP: `CLAUDE.md` in workspace (read by SDK) |
| Vibe | `AGENTS.md` / `VIBE.md` in workspace | No CLI flag; file convention only |
| OpenCode | `instructions` array in opencode.json | Injected by terok on host |
| Codex | `AGENTS.md` in workspace | Also: `instructions` in config.toml |
| Copilot | `AGENTS.md` in workspace | Also: `.github/copilot-instructions.md` |

## Agent-Specific Notes

### Claude

Settings precedence (highest wins): managed (`/etc/claude-code/managed-settings.json`)
→ CLI args → local project (`<cwd>/.claude/settings.local.json`) → project
(`<cwd>/.claude/settings.json`) → user (`~/.claude/settings.json`). Managed
settings cannot be overridden. `~/.claude/` is a shared volume mount, so
per-task permission mode must use managed settings or CLI flags.

Valid `permissions.defaultMode` values: `default`, `acceptEdits`, `plan`,
`dontAsk`, `bypassPermissions`. `bypassPermissions` blocked for root unless
`IS_SANDBOX` is set (terok runs as uid 1000, not affected).

### Vibe

All config fields overridable via `VIBE_<FIELD_NAME>` env vars (pydantic-settings,
case-insensitive). `vibe-acp` defaults to `auto_approve=False` (unlike `-p`
mode which auto-selects the `auto-approve` agent). Env var is the reliable
cross-path mechanism.

### OpenCode / Blablador

`OPENCODE_PERMISSION` is merged on top of all config layers. No CLI flag for
permissions. `opencode run` auto-allows most operations by default (only
`doom_loop` and `external_directory` default to `ask`). Blablador uses a
separate config path (`OPENCODE_CONFIG`); for ACP, a `blablador-acp` wrapper
is needed (#410).

### Codex

`/etc/codex/requirements.toml` has highest precedence (cannot be overridden
by user config, CLI flags, or `-c` overrides). Read by both CLI and
`codex-acp`. User config at `~/.codex/config.toml` and project config at
`.codex/config.toml` are lower precedence.

### Copilot

`--yolo` / `--allow-all` grants full permissions (tools + paths + URLs).
`--allow-all-tools` is a subset (tools only). `COPILOT_ALLOW_ALL` env var
also works. All flags work with `--acp` at spawn time. No per-session ACP
permission control (upstream gap #1607).

## Sources

Check these when re-verifying.

| Agent | Primary source | Key files / docs |
|-------|---------------|-----------------|
| Claude | `github.com/zed-industries/claude-code-acp` | `src/acp-agent.ts` (permissions), `src/settings.ts` (precedence); `code.claude.com/docs/en/settings` |
| Vibe | `github.com/mistralai/mistral-vibe` | `vibe/core/config/_settings.py`, `vibe/acp/acp_agent_loop.py`, `vibe/cli/entrypoint.py` |
| OpenCode | `github.com/sst/opencode` | `packages/opencode/src/config/config.ts`, `src/flag/flag.ts`, `src/cli/cmd/acp.ts` |
| Codex | `github.com/openai/codex` | `codex-rs/config/src/lib.rs`, `codex-rs/codex-acp/src/main.rs`; also `github.com/zed-industries/codex-acp` |
| Copilot | `github.com/github/copilot` | CHANGELOG.md (v0.0.397–v1.0.5); issues #179, #307, #1020, #1607 |
| terok | This repo | `headless_providers.py`, `agents.py`, `task_runners.py`, `docs/developer.md` |
