# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared test constants: IP addresses, ports, and URLs."""

LOCALHOST = "127.0.0.1"
"""Loopback address used for bind/connect in tests."""

CONTAINER_HOSTNAME = "host.containers.internal"
"""Hostname Podman exposes for reaching services on the host."""

TEST_IP = "198.51.100.42"
"""RFC 5737 TEST-NET-2 address for mock returns."""

TEST_IP_RFC5737 = "203.0.113.42"
"""RFC 5737 TEST-NET-3 address for firewall allow/deny tests."""

ALLOWED_TARGET_IPS = ["1.1.1.1", "1.0.0.1"]
"""Cloudflare anycast pair used for real egress integration tests."""

ALLOWED_TARGET_DOMAIN = "one.one.one.one"
"""Cloudflare DNS hostname that resolves to ``ALLOWED_TARGET_IPS``."""

ALLOWED_TARGET_HTTP = "http://1.1.1.1/"
"""HTTP target used for real egress reachability checks in integration tests."""

SLIRP_GATEWAY = "10.0.2.2"
"""Default slirp4netns gateway address."""

PASTA_HOST_LOOPBACK_MAP = "169.254.1.2"
"""pasta --map-host-loopback address: container traffic to this link-local
address is translated by pasta to 127.0.0.1 on the host."""

HOST_ALIAS_PASTA = f"{CONTAINER_HOSTNAME}:{PASTA_HOST_LOOPBACK_MAP}"
"""Podman --add-host value for pasta mode (via --map-host-loopback)."""

HOST_ALIAS_LOOPBACK = HOST_ALIAS_PASTA
"""Alias for backwards compatibility — points to pasta host alias."""

HOST_ALIAS_SLIRP = f"{CONTAINER_HOSTNAME}:{SLIRP_GATEWAY}"
"""Podman --add-host value for slirp4netns mode."""

GATE_PORT = 9418
"""Default gate server port."""

FAKE_PEER_PORT = 12345
"""Arbitrary port for fake client_address tuples."""

LOCALHOST_PEER = (LOCALHOST, FAKE_PEER_PORT)
"""Fake peer address for HTTP handler tests."""

TEST_EGRESS_URL = "http://example.com"
"""URL used in egress filtering tests."""

EGRESS_DOMAIN = "example.com"
"""Domain name used in egress filtering tests."""

TEST_UPSTREAM_URL = "https://example.com/source.git"
"""Upstream URL used in project-derivation integration tests."""

EXAMPLE_UPSTREAM_URL = "https://example.com/demo.git"
"""Upstream URL used in task-lifecycle integration tests."""


def localhost_url(port: int) -> str:
    """Build an ``http://127.0.0.1:{port}/`` URL."""
    return f"http://{LOCALHOST}:{port}/"


def gate_repo_url(project_id: str, token: str, *, port: int = GATE_PORT) -> str:
    """Build an authenticated host-side gate repository URL."""
    return f"http://{token}@{CONTAINER_HOSTNAME}:{port}/{project_id}.git"
