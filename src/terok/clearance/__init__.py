# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Clearance integration — clients of the terok-dbus varlink hub.

Two concerns, separate modules:

* :mod:`~.identity` — turns a podman container ID into a
  :class:`terok_dbus.ContainerIdentity` with task metadata resolved
  from terok's YAML task store.  Used by every terok-side clearance
  client (TUI screen, standalone notifier app).
* :mod:`~.notifier` — the ``terok-clearance-notifier`` entry point,
  a systemd user service that bridges hub events to
  ``org.freedesktop.Notifications`` popups on the operator's desktop.
"""

from terok.clearance.identity import IdentityResolver

__all__ = ["IdentityResolver"]
