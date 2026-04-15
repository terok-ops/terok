# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""End-to-end story tests for the credential proxy pipeline.

Each story exercises a complete user workflow: auth → DB → proxy → env.
These tests run in the matrix runner's disposable containers where a
real proxy daemon can be started.

Stories use real sqlite DBs, real aiohttp proxy servers (via TestServer),
and real route configs generated from the YAML roster.
"""

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from terok_sandbox import CredentialDB

pytestmark = pytest.mark.needs_credential_proxy


# ── Mock upstream ─────────────────────────────────────────


def _make_upstream() -> web.Application:
    """Create a mock upstream that echoes auth headers."""

    async def echo(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "auth": request.headers.get("Authorization", ""),
                "x_api_key": request.headers.get("x-api-key", ""),
                "path": request.path,
                "query": request.query_string,
            }
        )

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", echo)
    return app


# ── Stories ───────────────────────────────────────────────


@pytest.mark.asyncio
class TestStoryAuthThroughProxy:
    """Story: user auths a provider, proxy injects real credentials."""

    async def test_api_key_auth_to_proxy_forwarding(self, tmp_path: Path) -> None:
        """API key stored → phantom token → proxy → real key in upstream."""
        import aiohttp
        from terok_sandbox.credentials.proxy.server import _build_app

        # 1. Store credential (simulates terok-executor auth vibe --api-key ...)
        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "vibe", {"type": "api_key", "key": "real-mistral-key"})
        phantom = db.create_proxy_token("proj", "task-1", "default", "vibe")
        db.close()

        # 2. Write routes (simulates roster.generate_routes_json())
        routes_path = tmp_path / "routes.json"
        routes_path.write_text(
            json.dumps(
                {
                    "vibe": {
                        "upstream": "http://will-be-replaced",
                        "auth_header": "Authorization",
                        "auth_prefix": "Bearer ",
                    }
                }
            )
        )

        # 3. Start mock upstream
        upstream = TestServer(_make_upstream())
        await upstream.start_server()
        try:
            # Rewrite routes with real upstream port
            routes_path.write_text(
                json.dumps(
                    {
                        "vibe": {
                            "upstream": f"http://127.0.0.1:{upstream.port}",
                            "auth_header": "Authorization",
                            "auth_prefix": "Bearer ",
                        }
                    }
                )
            )

            # 4. Start proxy
            proxy_app = _build_app(str(db_path), str(routes_path))
            proxy_server = TestServer(proxy_app)
            await proxy_server.start_server()
            try:
                # 5. Make request with phantom token
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"http://127.0.0.1:{proxy_server.port}/vibe/v1/chat/completions",
                        headers={"Authorization": f"Bearer {phantom}"},
                        json={"model": "mistral-small-latest"},
                    ) as resp:
                        assert resp.status == 200
                        body = await resp.json()

                # 6. Verify: upstream saw the REAL key, not the phantom
                assert body["auth"] == "Bearer real-mistral-key"
                assert phantom not in body["auth"]
            finally:
                await proxy_server.close()
        finally:
            await upstream.close()

    async def test_oauth_auth_to_proxy_forwarding(self, tmp_path: Path) -> None:
        """OAuth token stored → phantom → proxy → Bearer token in upstream."""
        import aiohttp
        from terok_sandbox.credentials.proxy.server import _build_app

        # 1. Store OAuth credential
        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential(
            "default",
            "claude",
            {
                "type": "oauth",
                "access_token": "sk-ant-oat-real-token",
                "refresh_token": "rt-refresh",
            },
        )
        phantom = db.create_proxy_token("proj", "task-1", "default", "claude")
        db.close()

        # 2. Routes with dynamic auth
        upstream = TestServer(_make_upstream())
        await upstream.start_server()
        try:
            routes_path = tmp_path / "routes.json"
            routes_path.write_text(
                json.dumps(
                    {
                        "claude": {
                            "upstream": f"http://127.0.0.1:{upstream.port}",
                            "auth_header": "dynamic",
                        }
                    }
                )
            )

            # 3. Proxy
            proxy_app = _build_app(str(db_path), str(routes_path))
            proxy_server = TestServer(proxy_app)
            await proxy_server.start_server()
            try:
                # 4. Request
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"http://127.0.0.1:{proxy_server.port}/claude/v1/messages",
                        headers={
                            "Authorization": f"Bearer {phantom}",
                            "anthropic-beta": "oauth-2025-04-20",
                        },
                        json={"model": "claude-3-haiku-20240307"},
                    ) as resp:
                        assert resp.status == 200
                        body = await resp.json()

                # OAuth → Authorization: Bearer (not x-api-key)
                assert body["auth"] == "Bearer sk-ant-oat-real-token"
                assert body["x_api_key"] == ""
            finally:
                await proxy_server.close()
        finally:
            await upstream.close()


@pytest.mark.asyncio
class TestStoryTokenRevocation:
    """Story: task ends, phantom token revoked, proxy rejects."""

    async def test_revoked_phantom_rejected(self, tmp_path: Path) -> None:
        """After revocation, the same phantom token gets 401."""
        import aiohttp
        from terok_sandbox.credentials.proxy.server import _build_app

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "vibe", {"type": "api_key", "key": "k"})
        phantom = db.create_proxy_token("proj", "task-1", "default", "vibe")

        upstream = TestServer(_make_upstream())
        await upstream.start_server()
        try:
            routes_path = tmp_path / "routes.json"
            routes_path.write_text(
                json.dumps({"vibe": {"upstream": f"http://127.0.0.1:{upstream.port}"}})
            )

            proxy_app = _build_app(str(db_path), str(routes_path))
            proxy_server = TestServer(proxy_app)
            await proxy_server.start_server()
            try:
                # Works before revocation
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{proxy_server.port}/vibe/v1/models",
                        headers={"Authorization": f"Bearer {phantom}"},
                    ) as resp:
                        assert resp.status == 200

                # Revoke
                db.revoke_proxy_tokens("proj", "task-1")
                db.close()

                # Rejected after revocation
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{proxy_server.port}/vibe/v1/models",
                        headers={"Authorization": f"Bearer {phantom}"},
                    ) as resp:
                        assert resp.status == 401
            finally:
                await proxy_server.close()
        finally:
            await upstream.close()
