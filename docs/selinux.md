# SELinux & terok

terok ships **two** independent SELinux policy layers, both optional
to the operator:

1. **`terok_socket` — connectto allow rule** *(required for socket
   transport on enforcing hosts)*. Lets rootless `container_t`
   reach the host-side service sockets.  Without it, the default
   container policy denies the connection on Fedora / RHEL / CentOS
   in the default `services.mode: socket`.

2. **`terok_gate` / `terok_vault` — confined process domains**
   *(opt-in defense-in-depth)*. Per-service domains so a compromise
   of either daemon is bounded by SELinux policy rather than the
   user's UID. Currently shipped in **permissive** for the soak
   window — denials log without blocking — so installing them is
   safe on production hosts.

Both layers are loaded by one command:

```bash
python -m terok.tools.hardening install
```

Runs as your user, prompts for `sudo` once internally, and shells
out to `sudo` only for `semodule -i` / `semanage permissive`.

## What changes per distro

### Non-SELinux distros (Ubuntu, Debian, Arch, Alpine, …)

Nothing extra. `terok setup` installs socket-mode units, services
bind Unix sockets, containers mount them with `:z`, and everything
works. For optional MAC hardening on AppArmor distros see
[`apparmor.md`](apparmor.md) — same `python -m terok.tools.hardening install`
covers both backends.

### SELinux distros in permissive mode

Same as above. Sockets bind normally, the default container-SELinux
policy covers the flow.

### SELinux distros in enforcing mode (Fedora, RHEL, CentOS, …)

By default SELinux blocks `container_t → unconfined_t` `connectto`
on Unix sockets (see [Dan Walsh][1] / [Podman #23972][2]). To let
rootless Podman containers reach terok's host-side sockets, terok
ships `terok_socket` — a narrowly targeted policy module that
carves out this single exception.

`terok setup` on an enforcing host without it will print:

```text
SELinux policy   WARN (policy NOT installed)
                 install: python -m terok.tools.hardening install
```

After the install, sickbay shows `ok (terok_socket_t installed,
binding functional)` and task containers can reach the gate /
vault sockets.

## Optional confined domains

Beyond the connectto allow-rule, terok ships per-service confined
process domains that bound what the gate and vault daemons can do
even if their code is compromised. Same install command — once
loaded, systemd unit drop-ins
(`*.service.d/hardening-mac.conf`) attach the daemons to their
domains via `SELinuxContext=`.

Soak posture: domains start in **permissive** so denials log
without blocking. A future commit will flip them to enforcing once
the AVC trail is quiet.

`terok sickbay` reports the connectto rule via the `SELinux
policy` row.  Status of the optional confined domains is
out-of-band — `python -m terok.tools.hardening status` prints
which domains are loaded.

## Removing

```bash
python -m terok.tools.hardening remove
```

Removes both layers (connectto allow-rule and confined domains)
and tears down the systemd drop-ins.

## Complementary layer: systemd-native hardening

terok also enables systemd-level constraints (`ProtectSystem=`,
`PrivateTmp=`, `NoNewPrivileges=`, `RestrictAddressFamilies=`, …)
baked directly into the unit templates. That layer is independent
of the SELinux modules — they stack rather than substitute. When
both are active, the systemd directives constrain
capabilities/syscalls/FS reach via the kernel, and the SELinux
domains add label-based access control on top.

## Opting out: TCP transport

If you can't or don't want to install the connectto allow rule —
shared host without root, locked-down distro image — set:

```yaml
# ~/.config/terok/config.yml
services:
  mode: tcp
```

Falls back to TCP-loopback transport. Works on any distro, zero
extra setup.

> TCP services mode is being deprecated. The hook/pasta socket
> path (which the connectto allow rule unblocks) is the long-term
> default — easier to harden, no port-collision risk, no
> off-loopback exposure on multi-user hosts.

[1]: https://danwalsh.livejournal.com/78643.html
[2]: https://github.com/containers/podman/discussions/23972
