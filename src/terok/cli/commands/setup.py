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
    p_setup.add_argument(
        "--no-desktop-entry",
        action="store_true",
        help=(
            "Skip the XDG desktop entry and icon for terok-tui.  Use on "
            "headless / server hosts with no application launcher."
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
        with_images=getattr(args, "with_images", None),
        family=getattr(args, "family", None),
    )
    return True


# ── Orchestrator ───────────────────────────────────────────────────────


def cmd_setup(
    *,
    no_desktop_entry: bool = False,
    with_images: str | None = None,
    family: str | None = None,
) -> None:
    """Install the sandbox stack + desktop entry; optionally pre-build one L0/L1 pair.

    Exits non-zero if the sandbox aggregator fails (one or more service
    phases unreachable) or if a requested image build fails.  The
    desktop entry step is non-fatal when xdg-utils is missing — the
    built-in fallback covers spec-compliant hosts and the warning is
    a WARN, not a FAIL.

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

    print()
    desktop_ok = no_desktop_entry or _ensure_desktop_entry()

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


def _ensure_desktop_entry() -> bool:
    """Install the XDG desktop entry + application icon for ``terok-tui``.

    Writes three things, each soft-failing if the user doesn't have an
    application launcher (headless SSH box, container CI image):
      1. The ``.desktop`` file with ``Exec`` templated to the operator's
         resolved ``terok-tui`` binary — desktop launchers run with a
         minimal PATH so ``~/.local/bin`` entries from ``pipx`` installs
         won't be found by name alone.
      2. The ``terok-logo.png`` at ``hicolor/256x256/apps/terok.png`` so
         GNOME / KDE / XFCE can resolve ``Icon=terok`` via the standard
         icon theme lookup.
      3. A best-effort ``update-desktop-database`` + ``gtk-update-icon-cache``
         to nudge the menu / icon caches; the launcher finds the file on
         its own, but the refresh makes it appear in the next-session
         menu without an X-server restart.
    """
    from ._desktop_entry import DesktopBackend, install_desktop_entry

    with stage_line("Desktop entry") as s:
        bin_path = shutil.which("terok-tui")
        if bin_path is None:
            # pipx installs go under ~/.local/bin which isn't on the
            # setup-run PATH everywhere; still write the entry but fall back
            # to the bare binary name so an updated PATH picks it up.
            bin_path = "terok-tui"
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
