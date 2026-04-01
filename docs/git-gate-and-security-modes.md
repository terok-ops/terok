# Git Gate and Security Modes

The **git gate** is a host-side bare mirror of the upstream repository,
managed by [terok-sandbox](https://github.com/terok-ai/terok-sandbox).
It serves two purposes depending on the project's security mode:

- **Online mode** — performance accelerator for cloning (the gate is not used for security; containers talk to upstream directly)
- **Gatekeeping mode** — the default git origin for containers, directing agent pushes to the gate instead of upstream

## Three Layers: Upstream → Gate → Tasks

```text
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
- If a local gate exists, the initial clone is seeded from the gate server's HTTP endpoint for speed, then `origin` is repointed to upstream
- Security relies on normal upstream auth (deploy key permissions, branch protections), not the gate

## Gatekeeping Mode

Agent pushes are **directed to the gate** rather than upstream:

- `CODE_REPO` = the gate server's HTTP endpoint (e.g., `http://host:port/project.git`)
- The container's default `origin` points to the gate, not upstream
- Humans review and promote changes from the gate to upstream

!!! note "What the gate does and does not guarantee"

    The gate controls which remote is configured as `origin`. It does
    **not** technically prevent the agent from adding other remotes or
    making outbound network connections. If the agent knows an upstream
    URL (e.g. from git history or environment), it could attempt to
    reach it — the same way any online actor could attempt to access a
    public repository.

    The gate is most effective when combined with:

    - **The shield** (egress firewall) — blocks outbound connections
      to hosts not on the allowlist
    - **Per-project SSH keys** — a convenience feature for accessing
      private repos from containers. These are ordinary deploy keys
      that only work if the user registers them on the upstream host;
      they do not add security on their own.

### Options

**SSH agent access** (when SSH key is registered via `terok ssh-init`):
The SSH agent proxy is available even in gatekeeping mode. Useful for repos with private submodules. Ensure the key has no write access to upstream — otherwise the agent could push to upstream despite the gate configuration.

**External remote** (`gatekeeping.expose_external_remote: true`):
Add the upstream URL as a remote named `external` in the container's git config. This is "relaxed gatekeeping" — the agent can see and interact with upstream, but `origin` still points to the gate. Use this when the agent needs to pull from upstream but you still want pushes to go through human review via the gate.

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

1. `terok ssh-init <project>` — generate a per-project SSH key (optional for public HTTPS repos). The key is only useful if the user registers it as a deploy key on the upstream remote.
2. `terok gate-sync <project>` — initialize or update the gate mirror (`--force-reinit` to recreate)
3. Run tasks — online containers clone from gate then talk to upstream; gatekeeping containers use the gate as their default origin

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

| Mode | Container's default origin | Gate role |
|------|---------------------------|-----------|
| **Online** | Upstream directly | Performance accelerator (clone seed only) |
| **Gatekeeping** | Gate | Review checkpoint — agent pushes to gate, humans promote to upstream |
| **Relaxed gatekeeping** | Gate (+ upstream as `external` remote) | Review checkpoint with upstream visibility |

!!! tip "Defence in depth"

    The gate works best as one layer in a defence-in-depth strategy:
    gate (push destination control) + shield (egress firewall).
    No single layer is sufficient on its own.
