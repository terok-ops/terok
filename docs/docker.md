# Running terok in Docker

> **Experimental — not audited for security.**
> This mode is intended for local evaluation only.
> Do not expose terok-in-Docker to the public internet or use it in production.
> For production use, install terok natively on a host with Podman.

terok-in-Docker runs the full terok stack — web TUI, gate server, and
rootless Podman — inside a single Docker container.  This lets you try
terok without installing Podman on your host.

## Quick start

```bash
docker build -t terok-in-docker .

docker run -d --privileged --network host \
  --name terok \
  terok-in-docker
```

Open <http://localhost:8566> in your browser.
Run `docker logs terok` to see the gate admin token.

`--privileged` is required for nested rootless Podman (user namespaces
and cgroup delegation).  `--network host` is the simplest setup: all
ports bind directly to the host with no `-p` mapping needed.

## Bridge networking

If you prefer Docker's default bridge network, map ports explicitly.
Note: agent web task ports (7860+) won't be reachable from the host
unless you also map their range.

```bash
docker run -d --privileged \
  -p 8566:8566 -p 9418:9418 -p 7860-7880:7860-7880 \
  --name terok \
  terok-in-docker
```

## Persistent state

By default, all state is lost when the container is removed.  Mount
volumes to preserve terok config, task state, and Podman images/containers:

```bash
docker run -d --privileged --network host \
  -v ~/terok-in-docker/config:/home/podman/.config/terok \
  -v ~/terok-in-docker/share:/home/podman/.local/share/terok \
  -v ~/terok-in-docker/containers:/home/podman/.local/share/containers \
  --name terok \
  terok-in-docker
```

| Mount | Persists |
|-------|----------|
| `.config/terok` | Projects, presets, global config |
| `.local/share/terok` | Task metadata, gate state, workspaces |
| `.local/share/containers` | Podman images and containers (avoids re-pulling/rebuilding) |

The entrypoint automatically fixes ownership of mounted directories.

## LAN / reverse proxy access

All ports bind to `0.0.0.0` by default, so toad and gate are
LAN-reachable out of the box with `--network host`.

To make the TUI's WebSocket links and toad URLs display the correct
external address, set `TEROK_PUBLIC_URL` and optionally
`TEROK_PUBLIC_HOST`:

```bash
-e TEROK_PUBLIC_URL=http://myserver:8566
-e TEROK_PUBLIC_HOST=myserver
```

Behind nginx with TLS:

```bash
-e TEROK_PUBLIC_URL=https://terok.example.com
-e TEROK_PUBLIC_HOST=terok.example.com
```

## Git gate access from host

The gate server (port 9418) lets the host clone/pull/push repos managed
by terok.  A random admin token is generated at startup and printed to
`docker logs`.

```bash
git clone http://<token>@localhost:9418/myproject.git
```

For a stable token across restarts:

```bash
-e TEROK_GATE_ADMIN_TOKEN=mysecret
```

## Interactive shell

To get a shell instead of the web TUI:

```bash
docker run -it --privileged --network host --name terok \
  terok-in-docker bash
```

To exec into a running container:

```bash
docker exec -it -u podman terok bash
```

The `-u podman` is required because the container starts as root (to fix
bind-mount ownership) and then drops to `podman` internally.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `TEROK_PUBLIC_URL` | Browser-facing WebSocket URL for the web TUI; also sets toad subpath URLs when behind a reverse proxy |
| `TEROK_PUBLIC_HOST` | Hostname/IP used in toad URLs and bind address (defaults to `0.0.0.0` in Docker) |
| `TEROK_GATE_ADMIN_TOKEN` | Stable gate admin token (auto-generated if unset) |
| `TEROK_GATE_BIND` | Gate bind address (defaults to `0.0.0.0` in Docker) |

## Known limitations

**Toad web access from LAN:** Toad containers run inside nested
rootless Podman, which uses pasta for port forwarding.  Pasta only
forwards connections arriving on `127.0.0.1`, so toad is reachable
from the Docker host but not from other LAN machines.  The web TUI
and gate server are unaffected (they run directly in the Docker
container's process space).  A future reverse-proxy integration
(nginx) will resolve this.

## Architecture

```text
┌─ Docker (host) ────────────────────────────────────────────┐
│  terok-in-docker container                                  │
│  ├─ terok-web (TUI served on :8566)                        │
│  ├─ Podman (rootless, uid 1000, fuse-overlayfs)            │
│  │  ├─ agent-container-1                                   │
│  │  ├─ agent-container-2                                   │
│  │  └─ ...                                                 │
│  └─ terok config + state in /home/podman/.config|.local/   │
└─────────────────────────────────────────────────────────────┘
```
