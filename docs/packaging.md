# Packaging

terok supports two installation modes sharing the **same source of truth** for templates and scripts:

## Python Packaging (pip / Poetry)

- Provides console scripts `terokctl` and `terok`.
- Ships templates and helper scripts as package resources inside the wheel (under `src/terok/resources/`).
- Runtime loads resources via `importlib.resources` — no dependency on external paths like `/usr/share`.

## Distribution Packages (deb/rpm)

- Configuration under `/etc/terok`.
- Binaries as standard Python console entry points under `/usr/bin`.
- Writable state under `/var/lib/terok` (system) or XDG data dir (user).

For distro packages, typically no environment overrides are needed.

### Debian

Use `dh-sequence-python3` with the sdist/wheel:
- Config files go to `/etc/terok/**`
- Console scripts auto-install to `/usr/bin`
- Templates/scripts are read from Python package resources (no `/usr/share/terok` needed)

### RPM

Use `%pyproject_buildrequires` / `%pyproject_wheel` / `%pyproject_install` macros. Map config files into `%{buildroot}%{_sysconfdir}/terok`.

## pip --prefix on Debian/Ubuntu

On Debian/Ubuntu, pip uses the `posix_local` scheme which appends `/local` under the prefix:

```bash
# Correct — let pip add /local:
pip install --prefix=/virt/podman .
# Result: /virt/podman/local/bin/terok

# Wrong — don't add /local yourself:
pip install --prefix=/virt/podman/local .
# Result: /virt/podman/local/local/bin/terok
```

## TUI as Optional Extra

The TUI is an optional extra to avoid forcing upgrades to distro-managed packages:

```bash
pip install .           # Base install (no TUI)
pip install '.[tui]'    # With TUI
```

## Runtime Lookup Strategy

**Config** (first found wins):

1. `TEROK_CONFIG_FILE` (explicit file)
2. `${XDG_CONFIG_HOME:-~/.config}/terok/config.yml` (user)
3. `sys.prefix/etc/terok/config.yml` (pip/venv)
4. `/etc/terok/config.yml` (system)

**Projects**:

- System: `TEROK_CONFIG_DIR/projects` or `sys.prefix/etc/terok/projects` or `/etc/terok/projects`
- User: `${XDG_CONFIG_HOME:-~/.config}/terok/projects`

**Writable state** (tasks, gate, build):

1. `TEROK_STATE_DIR`
2. `${XDG_DATA_HOME:-~/.local/share}/terok`

Build artifacts go under `${state_root}/build/<project>/`.

The application never reads from or writes to `/usr/share` — it always uses its packaged resources.
