# Shared Directories

## Overview

When a task starts, terok mounts host directories into the container for workspace access, shared credentials, and SSH configuration.

## Per-Task Workspace

- Host path: `<state_root>/tasks/<project_id>/<task_id>/workspace-dangerous`
- Mounted as: `<host_dir>:/workspace:Z`
- Created automatically when the task runs (permissions `700`).
- The project repository is cloned or synced here by `init-ssh-and-repo.sh`.

> **Security warning:** The container has full write access to this directory and
> could have rewritten git hooks, checked in malicious scripts, or otherwise
> poisoned the repository. **Do not execute code or run `git` commands in this
> directory from the host.** The safer way to interact with agent work is through
> the **git gate** — a host-controlled bare repo where agents push their
> changes for human review before promotion to upstream.

## Shared Credential Directories

All shared directories are created automatically if missing. Base dir defaults to `~/.local/share/terok-credentials` (or `/var/lib/terok-credentials` if root). Override via `credentials.dir` in `config.yml` or the `TEROK_CREDENTIALS_DIR` environment variable.

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
| `_ssh-config-<project>` | `/home/dev/.ssh` | SSH keys/config (optional, per-project) |

All shared dirs use `:z` (shared SELinux label); the workspace uses `:Z` (private label).

## SSH Configuration

The SSH directory is optional — public HTTPS repos don't need it.

### Auto-Setup

```bash
terok ssh-init <project_id> [--key-type ed25519|rsa] [--key-name NAME] [--force]
```

This generates an ed25519 keypair and SSH config with:
- `IdentitiesOnly yes` and `StrictHostKeyChecking accept-new` (avoids interactive prompts)
- `IdentityFile` pointing to the generated key
- A `github.com` host section with `User git`

Use the printed `.pub` key to register a deploy key on your Git host.

### Custom SSH Config Template

```yaml
# project.yml
ssh:
  config_template: ssh_config.template  # relative or absolute path
```

Supported tokens: `{{IDENTITY_FILE}}`, `{{KEY_NAME}}`, `{{PROJECT_ID}}`

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
/workspace                    ← <state_root>/tasks/<project>/<task>/workspace-dangerous:Z
/home/dev/.codex              ← <envs_base>/_codex-config:z
/home/dev/.claude             ← <envs_base>/_claude-config:z
/home/dev/.vibe               ← <envs_base>/_vibe-config:z
/home/dev/.blablador          ← <envs_base>/_blablador-config:z
/home/dev/.config/opencode    ← <envs_base>/_opencode-config:z
/home/dev/.local/share/opencode ← <envs_base>/_opencode-data:z
/home/dev/.local/state        ← <envs_base>/_opencode-state:z
/home/dev/.ssh (optional)     ← <envs_base>/_ssh-config-<project>:z
```

Run `terok config` to see resolved paths on your system.
