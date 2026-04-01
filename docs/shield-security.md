# Shield Security Guide

The **shield** is an egress firewall that restricts outbound network access
from task containers using nftables OCI hooks.  It is provided by
[terok-shield](https://github.com/terok-ai/terok-shield) and integrated
into terok through the
[terok-sandbox](https://github.com/terok-ai/terok-sandbox) runtime layer.
This page explains **what you lose** when the shield is weakened or absent.

---

## Shield States

| State | Meaning | Risk |
|-------|---------|------|
| **up** (deny-all) | Only allowlisted IPs/domains and the gate server are reachable | Low — intended production state |
| **down** (bypass) | All egress traffic is allowed; audit logging still active | High — see [Shield Down](#shield-down-bypass-mode) |
| **disabled** / missing | No firewall hooks installed at all; no audit logging | Highest — see [Shield Disabled](#shield-disabled-or-missing) |

---

## Shield Down (Bypass Mode)

When the shield is **down** — whether via `terok shield down`, the TUI
toggle, or the `shield.drop_on_task_run` config — the nftables rules switch
to allow-all but the OCI hook infrastructure remains in place.  **Audit
logging continues.**

### What you lose protection against

**Secrets exfiltration.**
A compromised or prompt-injected agent can send API keys, tokens, SSH
private keys, or any other secrets mounted in the container to arbitrary
external endpoints.  With the shield *up*, outbound connections are limited
to explicitly allowlisted domains, making bulk exfiltration far harder.

**Prompt injection surface.**
Without egress restrictions, an agent can fetch arbitrary content from the
internet — including attacker-controlled pages designed to inject malicious
instructions.  The shield limits which domains can serve content to the
agent, dramatically reducing this attack vector.

**Internal network exposure.**
Containers running without egress filtering can scan and attack hosts on
private networks (RFC 1918 ranges: `10.0.0.0/8`, `172.16.0.0/12`,
`192.168.0.0/16`).  If the host is connected to a corporate LAN, VPN, or
cloud VPC, a compromised agent gains lateral movement capability.  The
shield blocks RFC 1918 destinations by default.

**Unrestricted downloads.**
Agents can download and execute arbitrary binaries, install packages from
untrusted sources, or pull container images — all without audit trail
filtering.  This enables supply-chain attacks where the agent is tricked
into installing backdoored dependencies.

### What you keep

- **Audit logging** — connection attempts are still logged to
  `{task_dir}/shield/audit.jsonl`.
- **OCI hook infrastructure** — the shield can be raised again at any time
  via `terok shield up` or the TUI.
- **Gate server** — the git gate still directs agent pushes to the
  host-side mirror for human review (in gatekeeping mode), though it
  does not prevent outbound network connections on its own.

---

## Shield Disabled or Missing

When the shield is **disabled** via the
`shield.bypass_firewall_no_protection` global config option, or when
terok-shield is not installed or cannot run (e.g. because `nft` is missing
or the podman version is incompatible), **no OCI hooks are installed at
all**.  This is the most dangerous state.

!!! danger "You lose everything listed above, plus:"

    **No audit logging.**
    Without the OCI hook, no connection data is recorded.  You have zero
    visibility into what the container accessed on the network.  Post-incident
    forensics become significantly harder.

    **No ability to raise the shield.**
    The `terok shield up` command and TUI toggle have no effect — there
    are no nftables rules to activate.  The only way to restore protection is
    to remove the bypass config, fix the underlying podman/nft issue, and
    start a new task.

### When is this acceptable?

The `bypass_firewall_no_protection` option exists **only** as a transitional
escape hatch for users whose podman version is incompatible with the
OCI-hook-based shield.  It will be removed once terok-shield supports all
target podman versions.

Set it only if:

- Your podman version does not support `--hooks-dir` reliably
  (see [terok-shield#71](https://github.com/terok-ai/terok-shield/issues/71),
  [terok-shield#101](https://github.com/terok-ai/terok-shield/issues/101))
- You understand and accept the risks above
- You are not working with sensitive credentials or private networks

```yaml
# ~/.config/terok/config.yml — DANGEROUS, remove ASAP
shield:
  bypass_firewall_no_protection: true
```

---

## Mitigations When Shield is Down or Missing

If you must operate without the shield, consider these compensating controls:

1. **Use gatekeeping mode** — even without the shield, the git gate
   directs agent pushes to the host-side mirror instead of upstream.
   This is a configuration default, not a hard barrier — see
   [Security Modes](git-gate-and-security-modes.md) for details.
2. **Protect credentials** — SSH keys are served via the agent proxy
   (never mounted). Avoid placing raw API tokens in shared config dirs.
3. **Monitor container traffic externally** — use host-level firewall
   rules or network monitoring tools.
4. **Limit task duration** — shorter tasks reduce the window of exposure.
5. **Review agent output carefully** — check for unexpected network
   activity in the task logs.

---

## Related

- [Security Modes](git-gate-and-security-modes.md) — git gate and
  online/gatekeeping modes
- [Container Layers](container-layers.md) — how containers are built
- [terok-shield](https://github.com/terok-ai/terok-shield) — the egress
  firewall library
