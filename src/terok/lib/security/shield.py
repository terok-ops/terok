# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for terok-shield egress firewall.

Creates per-task :class:`Shield` instances from the terok global config.
Each task gets its own ``state_dir`` under ``{task_dir}/shield/``.
"""

import tempfile
import warnings
from pathlib import Path

from terok_shield import (
    USER_HOOKS_DIR,
    EnvironmentCheck,  # noqa: F401 — re-exported
    NftNotFoundError,  # noqa: F401 — re-exported
    Shield,
    ShieldConfig,
    ShieldMode,
    ShieldNeedsSetup,  # noqa: F401 — re-exported
    ShieldState,  # noqa: F401 — re-exported
    ensure_containers_conf_hooks_dir,
    setup_global_hooks,
    system_hooks_dir,
)

from ..core.config import (
    get_gate_server_port,
    get_global_section,
    get_shield_bypass_firewall_no_protection,
)
from ..core.paths import config_root

_DEFAULT_PROFILES = ("dev-standard",)

# Short hint appended to CLI/TUI messages when the shield is weakened.
SHIELD_SECURITY_HINT = "See: https://terok-ai.github.io/terok/shield-security/"

# DANGEROUS TRANSITIONAL OVERRIDE — will be removed once terok-shield
# supports all target podman versions (see terok-shield#71, #101).
_BYPASS_WARNING = (
    "WARNING: shield.bypass_firewall_no_protection is set — "
    "the egress firewall is DISABLED.  Containers have unrestricted "
    "network access.  Remove this setting once your podman version "
    "is compatible with terok-shield."
)


def _state_dir(task_dir: Path) -> Path:
    """Return the per-task shield state directory."""
    return task_dir / "shield"


def _profiles_dir() -> Path:
    """Return the terok-managed shield profiles directory.

    Custom ``.txt`` allowlist files placed here are visible to all
    terok-managed Shield instances.  This is separate from the
    standalone ``terok-shield`` CLI's own config directory.
    """
    return config_root() / "shield" / "profiles"


def _normalize_profiles(raw: object) -> tuple[str, ...]:
    """Normalize a profiles config value to a tuple of strings.

    Raises:
        TypeError: If *raw* is not a string or list of strings.
    """
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if not isinstance(item, str):
                raise TypeError(
                    f"shield.profiles must be a list of strings, "
                    f"but found {type(item).__name__}: {item!r}"
                )
        return tuple(raw)
    raise TypeError(
        f"shield.profiles must be a string or a list of strings, "
        f"but found {type(raw).__name__}: {raw!r}"
    )


def make_shield(task_dir: Path) -> Shield:
    """Construct a per-task :class:`Shield` from the terok global config.

    Reads the ``shield:`` section of the global config and builds a
    :class:`ShieldConfig` with ``state_dir`` scoped to *task_dir*.

    The ``Shield`` constructor validates that the ``nft`` binary is
    available on the host and raises :class:`~terok_shield.NftNotFoundError`
    if it is missing.
    """
    sec = get_global_section("shield")
    profiles = _normalize_profiles(sec.get("profiles", _DEFAULT_PROFILES))

    config = ShieldConfig(
        state_dir=_state_dir(task_dir),
        mode=ShieldMode.HOOK,
        default_profiles=profiles,
        loopback_ports=(get_gate_server_port(),),
        audit_enabled=bool(sec.get("audit", True)),
        profiles_dir=_profiles_dir(),
    )
    return Shield(config)


def pre_start(container: str, task_dir: Path) -> list[str]:
    """Return extra ``podman run`` args for egress firewalling.

    Returns an empty list (no firewall args) when the dangerous
    ``bypass_firewall_no_protection`` override is active.

    Raises :class:`SystemExit` with setup instructions when the
    podman environment requires one-time hook installation.
    """
    if get_shield_bypass_firewall_no_protection():
        warnings.warn(_BYPASS_WARNING, stacklevel=2)
        return []
    try:
        return make_shield(task_dir).pre_start(container)
    except ShieldNeedsSetup as exc:
        raise SystemExit(f"{exc}\n\nRun 'terokctl shield setup' to install global hooks.") from None


def down(container: str, task_dir: Path, *, allow_all: bool = False) -> None:
    """Set shield to bypass mode (allow egress) for a running container.

    When *allow_all* is True, also permits private-range (RFC 1918) traffic.
    """
    if get_shield_bypass_firewall_no_protection():
        return
    make_shield(task_dir).down(container, allow_all=allow_all)


def up(container: str, task_dir: Path) -> None:
    """Set shield to deny-all mode for a running container."""
    if get_shield_bypass_firewall_no_protection():
        return
    make_shield(task_dir).up(container)


def state(container: str, task_dir: Path) -> ShieldState:
    """Return the live shield state for a running container.

    Queries actual nft state even when ``bypass_firewall_no_protection``
    is set, because containers started *before* bypass was enabled may
    still have active firewall rules.
    """
    return make_shield(task_dir).state(container)


def status() -> dict:
    """Return shield status dict from the global config.

    This reads the terok config directly rather than constructing a
    :class:`Shield`, because ``Shield.status()`` returns *available*
    profiles (filesystem scan) while terok needs *configured* profiles.
    """
    bypassed = get_shield_bypass_firewall_no_protection()
    sec = get_global_section("shield")
    profiles = _normalize_profiles(sec.get("profiles", _DEFAULT_PROFILES))
    result: dict = {
        "mode": "hook",
        "profiles": list(profiles),
        "audit_enabled": bool(sec.get("audit", True)),
    }
    if bypassed:
        # DANGEROUS TRANSITIONAL OVERRIDE — surface prominently in status output
        result["bypass_firewall_no_protection"] = True
    return result


def check_environment() -> EnvironmentCheck:
    """Check the podman environment for shield compatibility.

    Constructs a temporary :class:`Shield` and calls
    :meth:`Shield.check_environment`.  Returns a synthetic
    :class:`EnvironmentCheck` with bypass info when the dangerous
    ``bypass_firewall_no_protection`` override is active.
    """
    if get_shield_bypass_firewall_no_protection():
        return EnvironmentCheck(
            ok=False,
            health="bypass",
            issues=["bypass_firewall_no_protection is set — egress firewall disabled"],
        )
    with tempfile.TemporaryDirectory() as tmp:
        return make_shield(Path(tmp)).check_environment()


def run_setup(*, root: bool = False, user: bool = False) -> None:
    """Install global OCI hooks for podman < 5.6.0.

    Checks the environment first — if podman >= 5.6.0 uses per-container
    hooks natively, prints a message and returns without installing anything.

    Raises :class:`SystemExit` when neither ``--root`` nor ``--user`` is given.
    """
    env = check_environment()
    if env.hooks == "per-container":
        print(
            f"Podman {'.'.join(str(v) for v in env.podman_version)} uses per-task hooks natively.\n"
            "Global hook setup is not needed."
        )
        return
    if not root and not user:
        raise SystemExit(
            "Specify --root (system-wide, uses sudo) or --user (user-local).\n"
            "  terokctl shield setup --root   # /etc/containers/oci/hooks.d\n"
            "  terokctl shield setup --user   # ~/.local/share/containers/oci/hooks.d"
        )
    setup_hooks_direct(root=root)


def setup_hooks_direct(*, root: bool = False) -> None:
    """Install global hooks via the terok-shield Python API (no subprocess).

    Suitable for TUI callers that need direct control.  Installs hooks
    to the system directory (with sudo) when *root* is True, otherwise
    to the user directory.
    """
    if root:
        target = system_hooks_dir()
        setup_global_hooks(target, use_sudo=True)
    else:
        target = Path(USER_HOOKS_DIR).expanduser()
        setup_global_hooks(target)
        ensure_containers_conf_hooks_dir(target)
