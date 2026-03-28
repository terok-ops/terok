# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""End-to-end story tests for the credential proxy pipeline.

Each story exercises a complete user workflow: auth → DB → proxy → env.
These tests run in the matrix runner's disposable containers where a
real proxy daemon can be started.

Stories use real sqlite DBs, real aiohttp proxy servers (via TestServer),
and real route configs generated from the YAML registry.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        from terok_sandbox.credential_proxy.server import _build_app

        # 1. Store credential (simulates terok-agent auth vibe --api-key ...)
        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "vibe", {"type": "api_key", "key": "real-mistral-key"})
        phantom = db.create_proxy_token("proj", "task-1", "default", "vibe")
        db.close()

        # 2. Write routes (simulates registry.generate_routes_json())
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
        from terok_sandbox.credential_proxy.server import _build_app

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
        from terok_sandbox.credential_proxy.server import _build_app

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


class TestStoryEnvWiring:
    """Story: environment builder produces correct container env."""

    def test_full_env_assembly(self, tmp_path: Path) -> None:
        """build_task_env_and_volumes includes proxy env when proxy is running."""
        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "claude", {"type": "api_key", "key": "sk"})
        db.store_credential("default", "gh", {"type": "oauth_token", "token": "ghp"})
        db.close()

        sock_path = tmp_path / "proxy.sock"
        sock_path.touch()

        project = MagicMock()
        project.id = "myproject"

        with (
            patch(
                "terok_sandbox.credential_proxy_lifecycle.is_daemon_running",
                return_value=True,
            ),
            patch("terok_sandbox.SandboxConfig") as mock_cfg_cls,
        ):
            mock_cfg = mock_cfg_cls.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = sock_path

            env, volumes = _credential_proxy_env_and_volumes(project, "task-42")

        # All stored providers get phantom tokens
        assert "ANTHROPIC_API_KEY" in env
        assert "ANTHROPIC_BASE_URL" in env
        # TCP transport — no socket mount needed
        assert volumes == []
        # Phantom token is a 32-char hex string
        assert len(env["ANTHROPIC_API_KEY"]) == 32
