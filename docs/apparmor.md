# AppArmor & terok

terok ships **optional** AppArmor profiles for the host-side
daemons (gate, vault) so a compromise of either is bounded by
policy rather than the user's UID. Coverage parallels the SELinux
confined-domain layer documented in [`selinux.md`](selinux.md);
pick the backend your distro uses — both are loaded by the same
command:

```bash
python -m terok.tools.hardening install
```

Runs as your user, prompts for `sudo` once internally, and shells
out only for `apparmor_parser -r` and writing
`/etc/apparmor.d/`. Skipped cleanly on hosts where AppArmor isn't
the active LSM.

## What it does

1. Installs `terok-gate` and `terok-vault` profiles to
   `/etc/apparmor.d/`.
2. Loads them via `apparmor_parser -r`.
3. Drops `*.service.d/hardening-mac.conf` under the user's
   `~/.config/systemd/user/` setting `AppArmorProfile=` for each
   terok unit.
4. Restarts active terok units so the new profile attaches.

Profiles ship in **complain mode** (declared via
`flags=(complain)` in the profile header) — denials log without
blocking. Flip to enforce manually with `aa-enforce
/etc/apparmor.d/terok-gate /etc/apparmor.d/terok-vault` once
you've watched `journalctl -k -g apparmor` and seen no spurious
denials.

## Why named profiles + systemd attachment?

AppArmor attaches profiles by **executable path**. terok daemons
run inside a Python venv installed by `pipx`, so the actually-
exec'd binary the kernel sees is the python interpreter
(`/usr/bin/python3`), not a stable terok-shaped path. Anchoring
profiles to `python3` would either confine *all* of python (too
wide) or rely on argv matching that AppArmor doesn't natively
support.

Instead the profiles are **named** (no path attachment in the
header) and systemd's `AppArmorProfile=<name>` directive does the
attachment after fork. The kernel sees the named profile already
attached when the service process starts.

Caveat: the profile only attaches when the service is launched
**via systemd**. Direct `python -m …` invocations bypass it.

## Removing

```bash
python -m terok.tools.hardening remove
```

Unloads the profiles via `apparmor_parser -R`, removes them from
`/etc/apparmor.d/`, deletes the systemd drop-ins, and restarts
the units.

## Status

`python -m terok.tools.hardening status` prints which profiles
are loaded.  Sickbay does NOT show an AppArmor row — hardening is
out-of-band tooling, not part of the daily setup surface.

## Complementary layer: systemd-native hardening

terok unit templates also carry systemd-level directives
(`ProtectSystem=`, `PrivateTmp=`, `NoNewPrivileges=`,
`RestrictAddressFamilies=`, …). Those are independent of AppArmor
and stack with it — the systemd layer constrains capabilities,
syscalls, and FS reach via the kernel; the AppArmor layer adds
path-based access control on top.
