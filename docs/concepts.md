# Concepts

This page explains the core ideas behind terok — why containerized agents
exist, what problems they solve, and how terok's architecture maps to
real-world workflows.

---

## The Problem: Agents Need Access, Access Creates Risk

AI coding agents need to read code, run tools, install packages, and push
commits. Giving an agent direct access to your machine is the fastest way
to get work done — but it is also the fastest way to lose control:

- The agent can read and exfiltrate secrets (SSH keys, API tokens, cloud
  credentials) from the host filesystem.
- A prompt-injection attack can turn the agent into an attacker with full
  access to your network.
- A misunderstood instruction can delete files, force-push branches, or
  modify system configuration.
- Multiple agents working in parallel can step on each other's changes.

The alternative — copy-pasting code back and forth in a chat window — is
safe but painfully slow, and the agent cannot run tests, lint, or interact
with the real development environment.

terok exists in the space between these two extremes.

---

## The Spectrum of Agent Autonomy

There is no single "right" level of agent access. The appropriate level
depends on how much you trust the agent, the sensitivity of the codebase,
and how much friction you are willing to tolerate.

```mermaid
graph LR
    A["<b>Chat Window</b><br/>Copy-paste snippets<br/>Agent sees nothing"] --- B["<b>Restricted Agent</b><br/>Read-only access<br/>No network, no push"] --- C["<b>Gatekept Agent</b><br/>Full workspace<br/>Pushes to gate only"] --- D["<b>Online Agent</b><br/>Full workspace<br/>Pushes to upstream"] --- E["<b>Unrestricted Local</b><br/>Full machine access<br/>Your SSH keys"]

    style A fill:#e8f5e9,stroke:#2e7d32,color:#000
    style B fill:#e8f5e9,stroke:#2e7d32,color:#000
    style C fill:#fff9c4,stroke:#f9a825,color:#000
    style D fill:#fff3e0,stroke:#e65100,color:#000
    style E fill:#ffebee,stroke:#c62828,color:#000
```

| Level | Agent can | Agent cannot | Use case |
|-------|-----------|-------------|----------|
| **Chat window** | Read pasted snippets | See files, run tools, push code | Quick questions, small edits |
| **Restricted** | Read code, run tests | Write to git, access network | Code review, analysis |
| **Gatekept** (terok default) | Edit code, push to gate | Push to upstream, access arbitrary hosts | Autonomous development with human review |
| **Online** | Edit code, push to upstream | Escape the container | Trusted agents, CI-like workflows |
| **Unrestricted local** | Everything on the machine | Nothing is off-limits | Dangerous — no isolation at all |

terok provides the **gatekept** and **online** levels, with configurable
options to tune the exact trade-off within each.

---

## Architecture Overview

Every terok deployment has the same core components. The arrows show how
code flows between them:

```mermaid
graph TB
    subgraph HOST ["Host Machine"]
        subgraph TEROK ["terok"]
            CLI["terokctl<br/><i>CLI</i>"]
            TUI["terok<br/><i>TUI</i>"]
        end
        GATE["Git Gate<br/><i>bare mirror repo</i>"]
        SHIELD["Shield<br/><i>egress firewall</i>"]
        SSH_KEYS["SSH Keys<br/><i>per-project</i>"]
        SHARED["Shared Dirs<br/><i>credentials, config</i>"]
    end

    UPSTREAM["Upstream<br/><i>GitHub / GitLab</i>"]

    subgraph TASK_A ["Task Container A"]
        AGENT_A["Agent<br/><i>Claude, Codex, etc.</i>"]
        WORKSPACE_A["/workspace<br/><i>full repo clone</i>"]
    end

    subgraph TASK_B ["Task Container B"]
        AGENT_B["Agent<br/><i>Claude, Codex, etc.</i>"]
        WORKSPACE_B["/workspace<br/><i>full repo clone</i>"]
    end

    UPSTREAM -- "sync (SSH)" --> GATE
    GATE -- "clone (HTTP)" --> WORKSPACE_A
    GATE -- "clone (HTTP)" --> WORKSPACE_B
    WORKSPACE_A -- "push" --> GATE
    WORKSPACE_B -- "push" --> GATE
    GATE -- "promote (human)" --> UPSTREAM
    SHARED -. "mount" .-> TASK_A
    SHARED -. "mount" .-> TASK_B
    SHIELD -. "nftables hooks" .-> TASK_A
    SHIELD -. "nftables hooks" .-> TASK_B
    CLI --> TASK_A
    CLI --> TASK_B
    TUI --> TASK_A
    TUI --> TASK_B
```

---

## Core Concepts

### Projects

A **project** is a configuration that maps to a single upstream git
repository. It defines:

- The upstream URL and default branch
- The security mode (online or gatekeeping)
- SSH key configuration
- Agent settings (provider, model, instructions)
- Shield allowlists

Projects are stored as YAML files under
`~/.config/terok/projects/<id>/project.yml`. A project can have many tasks
running simultaneously, all against the same upstream repo.

### Tasks

A **task** is a single unit of work inside a project. Each task gets:

- Its own **Podman container** — fully isolated from other tasks
- Its own **workspace directory** — a fresh clone of the repo on a
  dedicated branch
- Its own **git branch** — so multiple agents can work in parallel
  without conflicts

Tasks are the primary unit of lifecycle management: you create, start,
stop, follow up on, and archive tasks.

### Task Workspace

The workspace is the task's private copy of the codebase, mounted at
`/workspace` inside the container. On the host it lives at:

```
~/.local/share/terok/tasks/<project>/<task_id>/workspace-dangerous/
```

The `-dangerous` suffix is a deliberate reminder: this directory contains
whatever the agent produces, which may include malicious content. The
container has full read-write access to its own workspace, but cannot see
other tasks' workspaces.

---

## The Git Gate

The **git gate** is a host-side bare mirror of the upstream repository. It
sits between the containers and the real remote, acting as either a
performance accelerator or a security checkpoint depending on the mode.

### How code flows through the gate

```mermaid
sequenceDiagram
    participant U as Upstream<br/>(GitHub)
    participant G as Git Gate<br/>(host mirror)
    participant T as Task Container<br/>(/workspace)
    participant H as Human<br/>(reviewer)

    Note over U,G: Initial sync (terokctl gate-sync)
    U->>G: git clone --mirror (SSH)

    Note over G,T: Task creation
    G->>T: git clone (HTTP, fast local)

    Note over T: Agent works...
    T->>T: edit, commit, test

    Note over T,G: Agent pushes (gatekeeping)
    T->>G: git push origin feature-branch

    Note over G,H: Human review
    H->>G: inspect changes (git log, diff)
    H->>U: git push upstream feature-branch
```

### Gate in online mode

In online mode, the gate is optional. When present, it speeds up the
initial clone (local HTTP is faster than fetching from GitHub), but the
container's `origin` remote is repointed to upstream after cloning. The
agent pushes directly to GitHub.

### Gate in gatekeeping mode

In gatekeeping mode, the gate is the **only** remote the container can
push to. The container's `origin` points to the gate server's HTTP
endpoint. All changes must pass through human review before reaching
upstream.

!!! tip "The gate is not a hard barrier"
    The gate controls which remote is configured as `origin`. It does not
    physically prevent the agent from adding other remotes. To enforce
    the boundary, combine the gate with the **shield** (egress firewall)
    to block outbound network access.

---

## Security Modes

### Online mode

```mermaid
graph LR
    T["Task Container"] -- "push (SSH)" --> U["Upstream<br/>GitHub"]
    G["Git Gate"] -. "clone seed<br/>(optional)" .-> T
    U -- "sync" --> G

    style T fill:#fff3e0,stroke:#e65100,color:#000
    style U fill:#e3f2fd,stroke:#1565c0,color:#000
    style G fill:#f5f5f5,stroke:#9e9e9e,color:#000
```

- Container has SSH keys mounted (by default)
- Agent can push branches directly to upstream
- Gate is a performance optimisation only
- Security relies on upstream branch protections and deploy-key scoping
- No human review checkpoint

**Use when:** you trust the agent, the deploy key has limited permissions,
and upstream has branch protection rules.

### Gatekeeping mode

```mermaid
graph LR
    T["Task Container"] -- "push (HTTP)" --> G["Git Gate"]
    G -. "promote<br/>(human)" .-> U["Upstream<br/>GitHub"]
    U -- "sync (SSH)" --> G

    style T fill:#fff9c4,stroke:#f9a825,color:#000
    style G fill:#e8f5e9,stroke:#2e7d32,color:#000
    style U fill:#e3f2fd,stroke:#1565c0,color:#000
```

- Container has no SSH keys (by default)
- Agent can only push to the gate
- Human reviews changes before promoting to upstream
- Network egress blocked by the shield (deny-all default)

**Use when:** you want human review of every change, or when working
with sensitive codebases.

### Gatekeeping options

| Option | Effect |
|--------|--------|
| `ssh.mount_in_gatekeeping: true` | Mount SSH keys even in gatekeeping. Useful for private submodules. **Risk:** if the key has write access to upstream, the agent could bypass the gate. |
| `gatekeeping.expose_external_remote: true` | Add upstream as a read-only `external` remote. The agent can pull from upstream but `origin` still points to the gate. |
| `gatekeeping.auto_sync` | Automatically update the gate when upstream changes are detected. |

---

## SSH Keys and Who Knows What

SSH keys control access to private git repositories. terok generates a
separate key per project and carefully controls where it is mounted.

```mermaid
graph TB
    subgraph HOST ["Host"]
        SSH["Per-project SSH key<br/><code>~/.local/share/terok/envs/<br/>_ssh-config-&lt;project&gt;/</code>"]
        GATE["Git Gate"]
    end

    UPSTREAM["Upstream<br/>(GitHub)"]

    subgraph ONLINE ["Online Task"]
        AGENT_ON["Agent"]
        MOUNT_ON["~/.ssh<br/><i>key mounted</i>"]
    end

    subgraph GATEKEPT ["Gatekept Task"]
        AGENT_GK["Agent"]
        NO_MOUNT["~/.ssh<br/><i>not mounted</i>"]
    end

    SSH -- "used by gate sync" --> GATE
    GATE -- "SSH" --> UPSTREAM
    SSH -. "mounted" .-> MOUNT_ON
    AGENT_ON -- "push via SSH" --> UPSTREAM
    AGENT_GK -- "push via HTTP" --> GATE

    style ONLINE fill:#fff3e0,stroke:#e65100,color:#000
    style GATEKEPT fill:#fff9c4,stroke:#f9a825,color:#000
    style SSH fill:#e8eaf6,stroke:#283593,color:#000
```

| Mode | Container has SSH key? | Agent can reach upstream? |
|------|----------------------|--------------------------|
| **Online** | Yes (default) | Yes — push via SSH |
| **Gatekeeping** | No (default) | No — push only to gate via HTTP |
| **Gatekeeping + SSH** | Yes (opt-in) | Potentially — depends on key permissions |

The gate itself always has access to the SSH key (it needs it to sync with
upstream). The key question is whether the **container** also has it.

---

## The Shield (Egress Firewall)

The **shield** is an nftables-based egress firewall that restricts outbound
network connections from containers. It works via Podman OCI hooks — rules
are applied automatically when containers start.

```mermaid
graph LR
    subgraph CONTAINER ["Task Container"]
        AGENT["Agent"]
    end

    subgraph ALLOWED ["Allowed"]
        GATE_PORT["Gate Server<br/><i>localhost:9418</i>"]
        ALLOW["Allowlisted hosts<br/><i>api.anthropic.com, etc.</i>"]
    end

    subgraph BLOCKED ["Blocked"]
        RANDOM["Random hosts<br/><i>evil.example.com</i>"]
        INTERNAL["Internal network<br/><i>10.0.0.0/8</i>"]
    end

    AGENT -- "✓" --> GATE_PORT
    AGENT -- "✓" --> ALLOW
    AGENT -- "✗" --> RANDOM
    AGENT -- "✗" --> INTERNAL

    style ALLOWED fill:#e8f5e9,stroke:#2e7d32,color:#000
    style BLOCKED fill:#ffebee,stroke:#c62828,color:#000
```

| Shield state | Outbound traffic | Audit logging | Risk |
|-------------|------------------|---------------|------|
| **Up** (deny-all) | Allowlisted only | Yes | Low |
| **Down** (bypass) | All allowed | Yes | High |
| **Disabled** | All allowed | No | Highest |

The shield mitigates:

- **Secrets exfiltration** — a compromised agent cannot send your API keys
  to an external server
- **Prompt injection** — the agent cannot fetch attacker-controlled content
  from arbitrary URLs
- **Internal network scanning** — RFC 1918 ranges are blocked by default
- **Supply-chain attacks** — the agent cannot install packages from
  untrusted sources

See the [Shield Security](shield-security.md) page for a complete
threat model.

---

## Defence in Depth

No single security layer is sufficient. terok combines multiple
independent layers, each covering different attack vectors:

```mermaid
graph TB
    subgraph L1 ["Layer 1: Container Isolation"]
        ISO["Podman rootless<br/>no-new-privileges<br/>SELinux labels<br/>User namespace mapping"]
    end

    subgraph L2 ["Layer 2: Git Gate"]
        GATE2["Push destination control<br/>Human review checkpoint<br/>Per-task auth tokens"]
    end

    subgraph L3 ["Layer 3: Shield"]
        SHIELD2["Egress firewall (nftables)<br/>Deny-all default<br/>Domain allowlisting<br/>Audit logging"]
    end

    subgraph L4 ["Layer 4: Credential Scoping"]
        CRED["Per-project SSH keys<br/>No SSH in gatekeeping (default)<br/>Deploy key permissions"]
    end

    subgraph L5 ["Layer 5: Human Review"]
        HUMAN["Gate promotion<br/>Branch protections<br/>PR review"]
    end

    L1 --> L2 --> L3 --> L4 --> L5

    style L1 fill:#e8eaf6,stroke:#283593,color:#000
    style L2 fill:#e8f5e9,stroke:#2e7d32,color:#000
    style L3 fill:#fff9c4,stroke:#f9a825,color:#000
    style L4 fill:#fce4ec,stroke:#880e4f,color:#000
    style L5 fill:#f3e5f5,stroke:#6a1b9a,color:#000
```

| Attack vector | Gate | Shield | Container isolation | Credential scoping |
|---------------|------|--------|--------------------|--------------------|
| Push malicious code to upstream | Blocks (gatekeeping) | — | — | Deploy key perms |
| Exfiltrate secrets over network | — | Blocks | — | No keys mounted |
| Escape to host filesystem | — | — | Blocks (rootless, namespaces) | — |
| Scan internal network | — | Blocks (RFC 1918) | — | — |
| Tamper with other tasks | — | — | Blocks (separate containers) | — |
| Prompt injection via internet | — | Blocks (domain allowlist) | — | — |

---

## Shared Directories

Some configuration and credentials need to be shared across tasks. terok
mounts two kinds of shared directories into containers:

### Global shared directories

These are shared by **all tasks across all projects** and contain
agent credentials and configuration:

```
~/.local/share/terok/envs/
├── _claude-config/    → /home/dev/.claude      (Claude Code)
├── _codex-config/     → /home/dev/.codex       (Codex)
├── _vibe-config/      → /home/dev/.vibe        (Mistral Vibe)
├── _gh-config/        → /home/dev/.config/gh   (GitHub CLI)
└── ...
```

These directories persist across container restarts and task
recreation. When you log in to an agent provider in one container, the
credentials are available in all future containers.

### Per-project directories

SSH configuration is scoped per project:

```
~/.local/share/terok/envs/
└── _ssh-config-myproject/  → /home/dev/.ssh    (project SSH keys)
```

Each project has its own SSH key, generated by `terokctl ssh-init`.

### Task-private directories

The workspace itself is private to each task:

```
~/.local/share/terok/tasks/<project>/<task_id>/
├── workspace-dangerous/  → /workspace          (repo clone)
├── agent-config/                                (agent state)
└── shield/                                      (firewall audit logs)
```

No other task can see or modify another task's workspace.

---

## Multi-Task Parallel Work

One of terok's core use cases is running multiple agents in parallel
against the same repository, each working on a separate branch:

```mermaid
graph TB
    GATE["Git Gate<br/><i>bare mirror of upstream</i>"]

    subgraph T1 ["Task 1: Fix auth bug"]
        A1["Claude"]
        B1["branch: fix/auth-bug"]
    end

    subgraph T2 ["Task 2: Add pagination"]
        A2["Codex"]
        B2["branch: feat/pagination"]
    end

    subgraph T3 ["Task 3: Update docs"]
        A3["Vibe"]
        B3["branch: docs/api-reference"]
    end

    GATE --> T1
    GATE --> T2
    GATE --> T3
    T1 -- "push" --> GATE
    T2 -- "push" --> GATE
    T3 -- "push" --> GATE
```

Each task:

- Runs in its own container — agents cannot interfere with each other
- Works on its own branch — no merge conflicts during development
- Has its own shield rules — network restrictions are per-container
- Can use a different agent provider — mix Claude, Codex, and Vibe in
  the same project

---

## IDE and Local Development Integration

terok containers are not opaque boxes. You can interact with task
workspaces from your local IDE or terminal through the git gate:

```mermaid
sequenceDiagram
    participant IDE as Local IDE
    participant G as Git Gate<br/>(host)
    participant T as Task Container

    Note over IDE,T: You want to see what the agent did
    IDE->>G: git fetch (local, fast)
    IDE->>IDE: Review agent's branch

    Note over IDE,T: You want to collaborate
    IDE->>G: git push (your changes)
    T->>G: git pull (picks up your changes)

    Note over IDE,T: Agent is done
    T->>G: git push (final result)
    IDE->>G: git fetch
    IDE->>IDE: Review, then promote to upstream
```

The gate is a standard git repository. Any git client — your IDE,
`git` CLI, or a GUI tool — can interact with it using normal git
operations.

---

## Comparison: terok vs. Alternatives

| Capability | Chat window | Agent on bare metal | Docker-based tools | **terok** |
|------------|-------------|--------------------|--------------------|-----------|
| Agent runs tests | No | Yes | Yes | Yes |
| Agent installs packages | No | Yes (risky) | Yes | Yes |
| Agent pushes to GitHub | No | Yes (risky) | Varies | Configurable |
| Parallel agents | No | Manual | Varies | Built-in |
| Human review checkpoint | N/A | No | Varies | Gate (gatekeeping) |
| Egress firewall | N/A | No | Rare | Shield |
| No root/daemon required | N/A | N/A | Docker needs daemon | Podman rootless |
| Multi-vendor agents | N/A | One at a time | Usually one | Claude, Codex, Copilot, Vibe, Blablador |
| Per-task branch isolation | N/A | Manual | Varies | Automatic |

---

## Next Steps

- [Getting Started](usage.md) — set up your first project and run a task
- [Security Modes](git-gate-and-security-modes.md) — detailed
  online vs. gatekeeping configuration
- [Shield Security](shield-security.md) — egress firewall threat model
- [Container Layers](container-layers.md) — how container images are built
- [Shared Directories](shared-dirs.md) — volume mounts reference
