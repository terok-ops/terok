# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Context-managed access to the shared vault [`CredentialDB`][]."""

from __future__ import annotations

from contextlib import contextmanager


@contextmanager
def vault_db():
    """Open the shared vault [`CredentialDB`][] and close it on exit."""
    from terok_sandbox import CredentialDB

    from ..core.config import make_sandbox_config

    db = CredentialDB(make_sandbox_config().db_path)
    try:
        yield db
    finally:
        db.close()


__all__ = ["vault_db"]
