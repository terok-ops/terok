# Git Gate and Security Modes

The **git gate** is a host-side bare mirror of the upstream repository. It serves two purposes depending on the project's security mode:

- **Online mode** — performance accelerator for cloning (containers still talk to upstream directly)
- **Gatekeeping mode** — the only git endpoint containers can access (upstream is air-gapped behind human review)

## Three Layers: Upstream → Gate → Tasks

```
Upstream (GitHub, GitLab, etc.)
       │
       ▼
Local gate (host-side mirror: STATE_ROOT/gate/<project>.git)
       │
       ▼
Task working copy (/workspace inside container)
```

Each task gets its own isolated repo, seeded from either the upstream (online) or the gate (gatekeeping).

---

## Online Mode

The agent behaves like a normal developer:

- `CODE_REPO` points to the **upstream URL**
- Container can push branches directly to upstream
- If a local gate exists, the initial clone is seeded from it (`CLONE_FROM=file:///git-gate/gate.git`) for speed, then `origin` is repointed to upstream
- Security comes from normal upstream auth, not the gate

## Gatekeeping Mode

Agent changes **cannot** reach upstream directly:

- `CODE_REPO` = `file:///git-gate/gate.git`
- Container never sees upstream URLs or credentials
- Humans promote changes from the gate to upstream

### Options

**SSH mount** (`ssh.mount_in_gatekeeping: true`):
Mount SSH credentials while still enforcing the gate-only model. Useful for repos with private submodules. Ensure the key has no write access to upstream.

**External remote** (`gatekeeping.expose_external_remote: true`):
Add the upstream URL as a remote named `external` in the container's git config. This is "relaxed gatekeeping" — the container knows about upstream but pushes to the gate by default.

**Upstream polling** (`gatekeeping.upstream_polling`):
TUI periodically checks if upstream has new commits using `git ls-remote` (cheap, refs only). Shows a notification when the gate is behind.

```yaml
gatekeeping:
  upstream_polling:
    enabled: true           # default: true
    interval_minutes: 5     # default: 5
```

**Auto-sync** (`gatekeeping.auto_sync`):
Automatically sync gate branches when upstream changes are detected. Opt-in (default: disabled).

```yaml
gatekeeping:
  auto_sync:
    enabled: false
    branches:
      - main
      - dev
```

---

## Gate Lifecycle

1. `terokctl ssh-init <project>` — generate SSH keys (optional for public HTTPS repos)
2. `terokctl gate-sync <project>` — initialize or update the gate mirror (`--force-reinit` to recreate)
3. Run tasks — online containers clone from gate then talk to upstream; gatekeeping containers talk only to the gate

---

## Configuration Example

```yaml
project:
  id: "myproject"
  security_class: "gatekeeping"

git:
  upstream_url: "git@github.com:org/repo.git"
  default_branch: "main"

ssh:
  key_name: "id_ed25519_myproject"
  # mount_in_gatekeeping: true

gatekeeping:
  expose_external_remote: true

  upstream_polling:
    enabled: true
    interval_minutes: 5

  auto_sync:
    enabled: false
    branches:
      - main
```

---

## Mental Model

| Mode | Container sees | Gate role |
|------|---------------|-----------|
| **Online** | Upstream directly | Performance accelerator |
| **Gatekeeping** | Gate only | Security boundary + communication channel |
| **Relaxed gatekeeping** | Gate + upstream as `external` remote | Security boundary with upstream awareness |
