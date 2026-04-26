# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Global bootstrap: ``terok setup`` installs host services.

Thin wrapper over :func:`terok_executor.ensure_sandbox_ready` — the
executor-level composer that generates vault routes from the agent
roster and then runs the sandbox aggregator (shield hooks + reader +
vault + gate + clearance hub/verdict/notifier with a full
stop → uninstall → install → verify cycle per service).  On top,
terok adds its own desktop-entry install for ``terok-tui``.  Safe
to re-run; every phase is idempotent.

Base images are **not** built by default: each project declares its
own ``image.base_image`` in ``project.yml`` (ubuntu, fedora,
nvidia/cuda, …), so at setup time there's nothing sensible to
pre-build.  L0/L1 build happens lazily on first ``terok task run``
keyed by the project's declared base, or explicitly via
``terok project init``.  The ``--with-images=<BASE>`` flag is the
expert escape hatch for operators who *know* a fleet-wide base
image and want to pay the build cost once up front.

Per-project operations live under the ``project`` group in
:mod:`project.py`; :func:`cmd_project_init` stays here because
``project.py`` (and its tests) import it.
"""

from __future__ import annotations

import argparse
import shutil
import sys

from terok_executor import AUTH_PROVIDERS
from terok_sandbox import bold, red, stage_line, yellow

from ...lib.core.projects import load_project
from ...lib.domain.facade import (
    build_images,
    generate_dockerfiles,
    maybe_pause_for_ssh_key_registration,
    provision_ssh_key,
    summarize_ssh_init,
)
from ...lib.domain.project import make_git_gate

# ── CLI wiring ─────────────────────────────────────────────────────────


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``setup`` top-level command."""
    p_setup = subparsers.add_parser(
        "setup",
        help="Global bootstrap: install shield, vault, gate, clearance + desktop entry",
        description=(
            "Idempotent host-level setup.  Delegates the service stack "
            "(shield + vault + gate + clearance) to the sandbox aggregator "
            "via ``terok_executor.ensure_sandbox_ready``; installs the XDG "
            "desktop entry for ``terok-tui`` on top.  Safe to re-run."
        ),
    )
    desktop_group = p_setup.add_mutually_exclusive_group()
    desktop_group.add_argument(
        "--no-desktop-entry",
        action="store_true",
        help=(
            "Skip the XDG desktop entry and icon for terok-tui.  Equivalent to "
            "``tui.desktop_entry: skip`` for one run."
        ),
    )
    desktop_group.add_argument(
        "--install-desktop-entry",
        action="store_true",
        help=(
            "Force-install the XDG desktop entry, falling back to the built-in "
            "writer when xdg-utils is missing.  Equivalent to "
            "``tui.desktop_entry: install`` for one run."
        ),
    )
    p_setup.add_argument(
        "--with-images",
        metavar="BASE_IMAGE",
        default=None,
        help=(
            "Pre-build L0/L1 for BASE_IMAGE (e.g. ``ubuntu:24.04``, "
            "``fedora:43``, ``nvidia/cuda:12.6.0-runtime-ubuntu24.04``).  "
            "Normally images build lazily on first ``terok task run`` or "
            "``terok project init``, keyed by each project's declared "
            "base — so this flag is only useful when you *know* your "
            "fleet will use one base and want to pay the cost once up "
            "front.  No default: omit the flag to skip the build."
        ),
    )
    p_setup.add_argument(
        "--family",
        default=None,
        help=(
            "Package family override for ``--with-images`` (``deb`` or "
            "``rpm``).  Auto-detected from the base image name otherwise."
        ),
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``setup``.  Returns True if handled."""
    if args.cmd != "setup":
        return False
    cmd_setup(
        no_desktop_entry=getattr(args, "no_desktop_entry", False),
        install_desktop_entry=getattr(args, "install_desktop_entry", False),
        with_images=getattr(args, "with_images", None),
        family=getattr(args, "family", None),
    )
    return True


# ── Orchestrator ───────────────────────────────────────────────────────


def cmd_setup(
    *,
    no_desktop_entry: bool = False,
    install_desktop_entry: bool = False,
    with_images: str | None = None,
    family: str | None = None,
) -> None:
    """Install the sandbox stack + desktop entry; optionally pre-build one L0/L1 pair.

    Exits non-zero if the sandbox aggregator fails (one or more service
    phases unreachable) or if a requested image build fails.  The
    desktop entry step is non-fatal when xdg-utils is missing — the
    built-in fallback covers spec-compliant hosts and the warning is
    a WARN, not a FAIL.

    Desktop-entry policy resolution: ``--no-desktop-entry`` /
    ``--install-desktop-entry`` (mutually exclusive) override the
    config key ``tui.desktop_entry`` (``auto`` / ``skip`` / ``install``,
    default ``auto``) for a single run.

    When ``with_images`` is ``None`` (the default) the L0/L1 build is
    *skipped entirely* — image-build decisions are per-project and
    happen on first ``terok task run`` / ``terok project init``.  Pass
    a base image (e.g. ``"ubuntu:24.04"``) to eagerly build once up
    front for a known fleet.  ``family`` overrides package-family
    detection for that build.
    """
    from terok_executor import ensure_sandbox_ready

    print(bold("\nSetting up terok host services\n"))

    sandbox_failed = False
    try:
        ensure_sandbox_ready()
    except SystemExit as exc:
        sandbox_failed = True
        if exc.code:
            print(bold(red(f"Sandbox aggregator reported failures (exit {exc.code}).")))

    images_failed = False
    if with_images and not sandbox_failed:
        # Skip the (slow) image build when the service stack is already
        # broken — the user needs to fix setup before anything that
        # depends on images will be useful anyway.
        images_failed = not _run_image_build(base=with_images, family=family)

    desktop_policy = _resolve_desktop_policy(
        no_desktop_entry=no_desktop_entry,
        install_desktop_entry=install_desktop_entry,
    )
    desktop_ok = _ensure_desktop_entry(policy=desktop_policy)
    _ensure_shell_completions()

    print()
    if not sandbox_failed and not images_failed and desktop_ok:
        print(bold("Setup complete."))
    elif sandbox_failed:
        print(bold(red("Setup failed — see service stage lines above.")))
    elif images_failed:
        print(bold(red("Image build failed — see above.")))
    else:
        print(bold(yellow("Desktop entry install reported errors (see above).")))

    providers = ", ".join(AUTH_PROVIDERS)
    print(
        f"\nNext steps:\n"
        f"  terok auth <provider>                      Host-wide auth ({providers})\n"
        f"  terok project wizard                       Create your first project\n"
        f"  terok task run <project>                   Start a CLI task (attaches on TTY)\n"
    )

    if sandbox_failed or images_failed:
        sys.exit(1)


# ── Image factory phase (delegates to terok-executor) ─────────────────


def _run_image_build(*, base: str, family: str | None) -> bool:
    """Build L0 + L1 base images via the executor's public factory.

    Returns ``False`` on :class:`BuildError` so ``cmd_setup`` can
    surface a single aggregate "setup failed" line instead of the
    factory crashing the whole command halfway through.  Non-build
    ``SystemExit`` from deeper code paths (e.g. a missing Dockerfile
    resource) is also absorbed for the same reason.
    """
    from terok_executor import BuildError, build_base_images

    with stage_line("Base images (L0/L1)") as s:
        try:
            build_base_images(base_image=base, family=family)
        except BuildError as exc:
            s.fail(str(exc))
            return False
        except SystemExit:
            s.fail("factory exited unexpectedly")
            return False
        s.ok("built")
        return True


# ── Desktop entry phase (terok-specific, not in sandbox aggregator) ───


def _resolve_desktop_policy(*, no_desktop_entry: bool, install_desktop_entry: bool) -> str:
    """Resolve the effective desktop-entry policy for this run.

    CLI flags win over the config key — argparse already enforces that
    the two flags are mutually exclusive.  Falls through to
    ``tui.desktop_entry`` (default ``"auto"``) when neither flag is set.
    """
    from ...lib.core.config import get_tui_desktop_entry

    if no_desktop_entry:
        return "skip"
    if install_desktop_entry:
        return "install"
    return get_tui_desktop_entry()


def _ensure_desktop_entry(*, policy: str) -> bool:
    """Apply the resolved desktop-entry *policy* — silent skip, hint, or install.

    Three branches keyed off *policy*:

    - ``skip`` → emit nothing.  The operator knows what they asked for.
    - ``auto`` without ``xdg-utils`` → skip with a single WARN stage line
      naming both escape hatches (``--install-desktop-entry`` for one
      run, ``tui.desktop_entry: skip`` to silence permanently).
      Headless servers usually want this default.
    - ``auto`` with ``xdg-utils``, or ``install`` → run the install,
      reporting the actually-used backend.  The fallback writer kicks
      in for ``install`` on a host without xdg-utils.
    """
    if policy == "skip":
        return True

    from ._desktop_entry import (
        DesktopBackend,
        install_desktop_entry,
        xdg_utils_available,
    )

    print()
    if policy == "auto" and not xdg_utils_available():
        with stage_line("Desktop entry") as s:
            s.warn(
                "skipped — xdg-utils not installed.  Run "
                "``terok setup --install-desktop-entry`` to install via the "
                "built-in fallback, or set ``tui.desktop_entry: skip`` in "
                "config.yml to silence this hint."
            )
        return True

    with stage_line("Desktop entry") as s:
        # pipx installs go under ~/.local/bin which isn't on the setup-run
        # PATH everywhere; fall back to the bare name so an updated PATH
        # picks it up at launcher time.
        bin_path = shutil.which("terok-tui") or "terok-tui"
        try:
            backend = install_desktop_entry(bin_path)
        except Exception as exc:  # noqa: BLE001
            s.fail(str(exc))
            return False
        if backend is DesktopBackend.XDG_UTILS:
            s.ok("installed")
        else:
            # The fallback writes the right XDG paths on spec-compliant
            # hosts but skips desktop-file-install validation and can't
            # cover DE-specific layout drift.  We also land here when
            # xdg-utils *is* on PATH but its install calls failed (DEBUG
            # log carries the detail) — "missing or failed" covers both.
            s.warn(
                "installed via built-in fallback — xdg-utils missing or failed; "
                "install it for standard XDG registration"
            )
        return True


# ── Shell completions phase (best-effort, skipped silently if installed) ──


def _ensure_shell_completions() -> None:
    """Install shell completions for the detected shell, best-effort.

    Idempotent: silently skips when completions are already installed.
    On a host where ``$SHELL`` is unset or points at an unsupported
    shell the install is skipped with a one-line hint pointing the
    operator at ``terok completions install --shell <name>``.
    """
    import os

    from .completions import _SHELLS, _install_completions, is_completion_installed

    if is_completion_installed():
        return
    print()
    print(bold("Installing shell completions"))

    # Inline the shell detection from completions._detect_shell so we
    # don't have to swallow its SystemExit-on-failure here.
    shell_path = os.environ.get("SHELL", "")
    shell = os.path.basename(shell_path)
    if shell not in _SHELLS:
        print(yellow(f"Shell completions skipped: cannot detect from $SHELL={shell_path!r}"))
        print("Install manually: terok completions install --shell <bash|zsh|fish>")
        return
    try:
        # Positional, not ``shell=…``: bandit's B604 heuristic flags any
        # function call carrying a ``shell`` kwarg (mistaking it for
        # ``subprocess.run(..., shell=True)``); the underlying API takes
        # bash/zsh/fish, not a shell-mode flag.
        _install_completions(shell)
    except Exception as exc:  # noqa: BLE001
        print(yellow(f"Shell completions skipped: {exc}"))


# ── Per-project setup ──────────────────────────────────────────────────


def cmd_project_init(project_id: str) -> None:
    """Full project setup: ssh-init, generate, build, gate-sync.

    The final gate-sync step is skipped when ``gate.enabled`` is false in
    project.yml — the container fetches directly from upstream (or runs
    with an empty workspace) instead of through a host-side mirror.  This
    is the escape hatch for hosts that cannot reach the remote (firewall
    blocking SSH, corporate proxy, offline laptop) while the container's
    network path still works.
    """
    print("==> Initializing SSH...")
    summarize_ssh_init(provision_ssh_key(project_id))
    maybe_pause_for_ssh_key_registration(project_id)

    print("==> Generating Dockerfiles...")
    generate_dockerfiles(project_id)

    print("==> Building images...")
    build_images(project_id)

    project = load_project(project_id)
    if not project.gate_enabled:
        print("==> Gate disabled by project.yml — skipping gate-sync.")
        return

    print("==> Syncing git gate...")
    res = make_git_gate(project).sync()
    if not res["success"]:
        raise SystemExit(f"Gate sync failed: {', '.join(res['errors'])}")
    print(f"Gate ready at {res['path']}")
