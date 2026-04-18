# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""End-to-end story tests for the SSH signer.

Each story exercises a complete user workflow: generate SSH keys → register
in ssh-keys.json → start SSH signer server → connect with phantom token →
list identities → sign data → verify signature.

Stories use real sqlite DBs, real asyncio TCP servers, real ed25519 keys,
and real cryptographic operations.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from terok_sandbox import CredentialDB
from terok_sandbox.vault.ssh_signer import (
    SSH_AGENT_IDENTITIES_ANSWER,
    SSH_AGENT_SIGN_RESPONSE,
    SSH_AGENTC_REQUEST_IDENTITIES,
    SSH_AGENTC_SIGN_REQUEST,
    _pack_string,
    _unpack_string,
    start_ssh_signer,
)

pytestmark = pytest.mark.needs_vault


# ── Helpers ──────────────────────────────────────────────


def _generate_test_keypair(directory: Path) -> tuple[Path, Path, bytes]:
    """Generate a real ed25519 keypair and return (priv_path, pub_path, pub_blob)."""
    key = Ed25519PrivateKey.generate()
    priv_pem = key.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption())
    pub_raw = key.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)

    priv_path = directory / "id_ed25519_testproj"
    pub_path = directory / "id_ed25519_testproj.pub"
    priv_path.write_bytes(priv_pem)
    pub_path.write_text(f"{pub_raw.decode()} terok testproj\n")

    pub_blob = base64.b64decode(pub_raw.decode().split()[1])
    return priv_path, pub_path, pub_blob


def _handshake(token: str) -> bytes:
    """Build a phantom-token handshake prefix."""
    raw = token.encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def _msg(msg_type: int, payload: bytes = b"") -> bytes:
    """Build an SSH agent protocol message."""
    body = bytes([msg_type]) + payload
    return struct.pack(">I", len(body)) + body


async def _recv(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read one SSH agent response message."""
    raw_len = await reader.readexactly(4)
    (msg_len,) = struct.unpack(">I", raw_len)
    body = await reader.readexactly(msg_len)
    return body[0], body[1:]


# ── Stories ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestStorySSHSignerSigning:
    """Story: container signs git data via SSH signer using host-side keys."""

    async def test_full_sign_flow(self, tmp_path: Path) -> None:
        """ssh-init → register key → agent server → identity → sign → verify."""
        ssh_dir = tmp_path / "ssh-keys" / "testproj"
        ssh_dir.mkdir(parents=True)
        priv_path, pub_path, pub_blob = _generate_test_keypair(ssh_dir)

        # 1. Register key in ssh-keys.json (simulates `terok ssh-init`)
        keys_json = tmp_path / "ssh-keys.json"
        keys_json.write_text(
            json.dumps({"testproj": [{"private_key": str(priv_path), "public_key": str(pub_path)}]})
        )

        # 2. Create phantom token (simulates environment.py at task launch)
        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        phantom = db.create_token("testproj", "task-1", "testproj", "ssh")
        db.close()

        # 3. Start SSH agent server (simulates credential proxy daemon)
        server = await start_ssh_signer(str(db_path), str(keys_json), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            # 4. Connect with phantom token (simulates socat bridge from container)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)

            writer.write(_handshake(phantom))
            await writer.drain()

            # 5. List identities — should return the project's public key
            writer.write(_msg(SSH_AGENTC_REQUEST_IDENTITIES))
            await writer.drain()

            msg_type, payload = await _recv(reader)
            assert msg_type == SSH_AGENT_IDENTITIES_ANSWER
            (nkeys,) = struct.unpack_from(">I", payload, 0)
            assert nkeys == 1
            returned_blob, off = _unpack_string(memoryview(payload), 4)
            assert returned_blob == pub_blob
            comment, _ = _unpack_string(memoryview(payload), off)
            assert comment == b"terok testproj"

            # 6. Sign data — simulates what ssh/git does during push
            data_to_sign = b"session-id-and-challenge-data-from-sshd"
            sign_payload = (
                _pack_string(pub_blob)
                + _pack_string(data_to_sign)
                + struct.pack(">I", 0)  # flags=0
            )
            writer.write(_msg(SSH_AGENTC_SIGN_REQUEST, sign_payload))
            await writer.drain()

            msg_type, payload = await _recv(reader)
            assert msg_type == SSH_AGENT_SIGN_RESPONSE

            sig_blob, _ = _unpack_string(memoryview(payload), 0)
            algo, off = _unpack_string(memoryview(sig_blob), 0)
            assert algo == b"ssh-ed25519"
            raw_sig, _ = _unpack_string(memoryview(sig_blob), off)

            # 7. Verify signature with the public key
            pub_raw = pub_path.read_text().strip().split()[1]
            from cryptography.hazmat.primitives.serialization import load_ssh_public_key

            real_pub = load_ssh_public_key(f"ssh-ed25519 {pub_raw}".encode())
            real_pub.verify(raw_sig, data_to_sign)  # raises on failure

            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()


@pytest.mark.asyncio
class TestStorySSHSignerTokenRevocation:
    """Story: task ends, phantom token revoked, SSH signer rejects."""

    async def test_revoked_token_rejected(self, tmp_path: Path) -> None:
        """After token revocation, the SSH signer closes the connection."""
        ssh_dir = tmp_path / "ssh-keys" / "proj"
        ssh_dir.mkdir(parents=True)
        priv_path, pub_path, _ = _generate_test_keypair(ssh_dir)

        keys_json = tmp_path / "ssh-keys.json"
        keys_json.write_text(
            json.dumps({"proj": [{"private_key": str(priv_path), "public_key": str(pub_path)}]})
        )

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        phantom = db.create_token("proj", "task-1", "proj", "ssh")

        server = await start_ssh_signer(str(db_path), str(keys_json), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            # Works before revocation
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(_handshake(phantom))
            writer.write(_msg(SSH_AGENTC_REQUEST_IDENTITIES))
            await writer.drain()
            msg_type, _ = await _recv(reader)
            assert msg_type == SSH_AGENT_IDENTITIES_ANSWER
            writer.close()
            await writer.wait_closed()

            # Revoke all tokens for this task
            db.revoke_tokens("proj", "task-1")
            db.close()

            # Rejected after revocation — server closes connection
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(_handshake(phantom))
            writer.write(_msg(SSH_AGENTC_REQUEST_IDENTITIES))
            await writer.drain()
            data = await reader.read(1024)
            assert data == b""

            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()


class TestStorySSHSignerEnvWiring:
    """Story: environment builder wires SSH signer env vars into containers."""

    def test_ssh_signer_env_vars_injected(self, tmp_path: Path) -> None:
        """When project has SSH keys, phantom token and port are in container env."""
        from terok.lib.orchestration.environment import _vault_env_and_volumes

        # Set up DB with a regular credential (so proxy path is exercised)
        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "claude", {"type": "api_key", "key": "sk"})
        db.close()

        # Write ssh-keys.json with the project's key registered (list format)
        keys_json = tmp_path / "ssh-keys.json"
        keys_json.write_text(
            json.dumps({"myproj": [{"private_key": "/keys/id", "public_key": "/keys/id.pub"}]})
        )

        sock_path = tmp_path / "proxy.sock"
        sock_path.touch()

        project = MagicMock()
        project.id = "myproj"

        with (
            patch(
                "terok_sandbox.credentials.lifecycle.is_daemon_running",
                return_value=True,
            ),
            patch("terok_sandbox.ensure_vault_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.get_vault_transport", return_value="direct"),
        ):
            mock_cfg = mock_cfg_fn.return_value
            mock_cfg.db_path = db_path
            mock_cfg.vault_socket_path = sock_path
            mock_cfg.token_broker_port = 18731
            mock_cfg.ssh_keys_json_path = keys_json
            mock_cfg.ssh_signer_port = 18732

            env, _ = _vault_env_and_volumes(project, "task-1")

        # SSH signer token should be injected
        assert "TEROK_SSH_SIGNER_TOKEN" in env
        assert env["TEROK_SSH_SIGNER_TOKEN"].startswith("terok-p-")
        assert env["TEROK_SSH_SIGNER_PORT"] == "18732"
        # Regular vault vars also present
        assert "ANTHROPIC_API_KEY" in env

    def test_no_ssh_keys_no_ssh_env(self, tmp_path: Path) -> None:
        """When project has no SSH keys, no SSH signer env vars are set."""
        from terok.lib.orchestration.environment import _vault_env_and_volumes

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "claude", {"type": "api_key", "key": "sk"})
        db.close()

        sock_path = tmp_path / "proxy.sock"
        sock_path.touch()

        project = MagicMock()
        project.id = "no-ssh-project"

        with (
            patch(
                "terok_sandbox.credentials.lifecycle.is_daemon_running",
                return_value=True,
            ),
            patch("terok_sandbox.ensure_vault_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.get_vault_transport", return_value="direct"),
        ):
            mock_cfg = mock_cfg_fn.return_value
            mock_cfg.db_path = db_path
            mock_cfg.vault_socket_path = sock_path
            mock_cfg.token_broker_port = 18731
            mock_cfg.ssh_keys_json_path = tmp_path / "ssh-keys.json"  # doesn't exist
            mock_cfg.ssh_signer_port = 18732

            env, _ = _vault_env_and_volumes(project, "task-1")

        assert "TEROK_SSH_SIGNER_TOKEN" not in env
        assert "TEROK_SSH_SIGNER_PORT" not in env
