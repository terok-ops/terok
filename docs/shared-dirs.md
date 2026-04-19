# Shared Directories

## Overview

When a task starts, terok mounts host directories into the container for workspace access and shared agent configuration.

## Per-Task Workspace

- Host path: `<state_dir>/tasks/<project_id>/<task_id>/workspace-dangerous`
- Mounted as: `<host_dir>:/workspace:Z`
- Created automatically when the task runs (permissions `700`).
- The project repository is cloned or synced here by `init-ssh-and-repo.sh`.

> **Security warning:** The container has full write access to this directory and
> could have rewritten git hooks, checked in malicious scripts, or otherwise
> poisoned the repository. **Do not execute code or run `git` commands in this
> directory from the host.** The safer way to interact with agent work is through
> the **git gate** — a host-controlled bare repo where agents push their
> changes for human review before promotion to upstream.

## Shared Agent Configuration Directories

These directories are bind-mounted into every task container so that agents and
tools find their config on startup.  They are created automatically
on first task launch.  The base dir defaults to
`<sandbox_live_dir>/mounts` (override via `TEROK_SANDBOX_LIVE_DIR`).

> **Trust boundary:** Mount directories are intentionally separated from the
> credentials store (`~/.local/share/terok/credentials/`) since containers have
> read-write access to mounts and could potentially poison them.

| Host Dir | Container Mount | Purpose |
|----------|----------------|---------|
| `_codex-config` | `/home/dev/.codex` | Codex credentials |
| `_claude-config` | `/home/dev/.claude` | Claude Code credentials (`CLAUDE_CONFIG_DIR` is set) |
| `_vibe-config` | `/home/dev/.vibe` | Mistral Vibe credentials |
| `_blablador-config` | `/home/dev/.blablador` | Blablador credentials (includes isolated OpenCode config via `OPENCODE_CONFIG`) |
| `_opencode-config` | `/home/dev/.config/opencode` | Plain OpenCode config (use `terok config import-opencode`) |
| `_opencode-data` | `/home/dev/.local/share/opencode` | OpenCode data/caches (shared by Blablador and plain OpenCode) |
| `_opencode-state` | `/home/dev/.local/state` | OpenCode/Bun state (shared by both) |
| `_gh-config` | `/home/dev/.config/gh` | GitHub CLI config |
| `_glab-config` | `/home/dev/.config/glab-cli` | GitLab CLI config |

All shared dirs use `:z` (shared SELinux label); the workspace uses `:Z` (private label).

> **Note:** SSH keys are **not** mounted into containers.  The vault's
> SSH signer serves keys over TCP — private keys never enter the
> container.  See `terok project ssh-init` for key generation.

## SSH Key Management

SSH keys are generated and stored on the **host only** — they are served to
containers via the vault's SSH signer (TCP-based, phantom-token
authenticated).  Public HTTPS repos don't need SSH setup at all.

### Setup

```bash
terok project ssh-init <project_id> [--key-type ed25519|rsa] [--key-name NAME] [--force]
```

This generates an ed25519 keypair stored at `<state_dir>/ssh-keys/<project_id>/`
and registers it in `ssh-keys.json` for the vault SSH signer.

Use the printed `.pub` key to register a deploy key on your Git host.

### Custom SSH Config Template

```yaml
# project.yml
ssh:
  config_template: ssh_config.template  # relative or absolute path
```

Supported tokens: `{{IDENTITY_FILE}}`, `{{KEY_NAME}}`, `{{PROJECT_ID}}`

## Vault

The vault holds real API keys and OAuth tokens on the host
and injects **phantom tokens** into containers.  When a container makes
an API call, the proxy intercepts the phantom token and swaps it for the
real credential — the actual secret never enters any container.

### Per-task vs shared tokens

| Auth method | Token scope | Notes |
|-------------|------------|-------|
| API key (all providers) | Per-task | Each task gets a unique phantom token that is revoked when the task ends. |
| OAuth (Codex, etc.) | Per-task | Same per-task lifecycle as API keys. |
| **Claude OAuth** | **Shared** | Uses a static marker token in `.credentials.json` instead of a per-task phantom. All tasks share the same lookup key. |

Claude OAuth is shared because Claude Code determines the subscription
plan display ("Claude Max", "Claude Pro", etc.) by reading `accessToken`
from `~/.claude/.credentials.json`.  If the token comes from the
`CLAUDE_CODE_OAUTH_TOKEN` environment variable instead, Claude Code
always shows "Claude API" regardless of subscription metadata.  The
static marker in the file works around this limitation.

**Implications of shared Claude OAuth:**

- The credential applies to **all tasks** in the default credential set
  — you cannot have per-project or per-task Claude OAuth credentials.
- The static marker token is not revoked between tasks.  The real OAuth
  token is still protected by the proxy and refreshed automatically.
- API key auth for Claude (`terok project auth claude` → option 2) remains
  per-task and is unaffected.

## Git Identity

terok configures git author/committer identities to distinguish AI-generated commits. The mapping is controlled by `git.authorship` in `project.yml` or global `config.yml`:

| Mode | Author | Committer |
|------|--------|-----------|
| `agent-human` (default) | AI agent | Human |
| `human-agent` | Human | AI agent |
| `human` | Human | Human |
| `agent` | AI agent | AI agent |

Each agent wrapper supplies its own AI identity (e.g. `Claude <noreply@anthropic.com>`, `Codex <noreply@openai.com>`, `Mistral Vibe <vibe@mistral.ai>`), allowing multiple agents to coexist in the same container.

Human credentials are resolved in order:
1. Per-project `git.human_name` / `git.human_email` in `project.yml`
2. Global `git.human_name` / `git.human_email` in `config.yml`
3. `git config --global user.name` / `user.email`
4. `Nobody <nobody@localhost>`

Agent email addresses are GitHub-recognized and display with avatars in commit history.

## Quick Reference

```text
/workspace                    ← <state_dir>/tasks/<project>/<task>/workspace-dangerous:Z
/home/dev/.codex              ← <mounts_dir>/_codex-config:z
/home/dev/.claude             ← <mounts_dir>/_claude-config:z
/home/dev/.vibe               ← <mounts_dir>/_vibe-config:z
/home/dev/.blablador          ← <mounts_dir>/_blablador-config:z
/home/dev/.config/opencode    ← <mounts_dir>/_opencode-config:z
/home/dev/.local/share/opencode ← <mounts_dir>/_opencode-data:z
/home/dev/.local/state        ← <mounts_dir>/_opencode-state:z
```

Run `terok config` to see resolved paths on your system.
