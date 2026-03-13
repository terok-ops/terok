# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

# Vulture whitelist — false positives that are actually required.
# Signal handler parameters are mandated by the signal.signal() API.
signum  # noqa
frame  # noqa
# Re-exported from terok_shield for public API consumers.
NftNotFoundError  # noqa
ShieldState  # noqa
