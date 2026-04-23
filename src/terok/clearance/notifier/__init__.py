# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``terok-clearance-notifier`` — bridges hub events to desktop popups.

Separate systemd user service (``terok-clearance-notifier.service``)
from the hub (``terok-clearance-hub.service``) so the hub stays
UI-agnostic —
headless hosts (CI, servers) run the hub without pulling in a
desktop-notifier dependency, and desktops get richer rendering with
terok's task-aware identity resolution.

This package intentionally keeps ``__init__`` empty — no re-export of
:mod:`.app`.  ``python -m terok.clearance.notifier.app`` under systemd
imports the parent package before executing the target module as
``__main__``; a re-export would put ``app`` in ``sys.modules`` first,
and runpy would then log its ``found in sys.modules after import of
package … but prior to execution`` warning into the journal every
notifier start.
"""
