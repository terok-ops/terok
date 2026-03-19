# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
#
# terok-in-Docker — run terok inside a Docker container with nested
# rootless Podman.  Intended for local evaluation only.
#
# Quick start:
#   docker build -t terok-in-docker .
#   docker run -d --privileged --network host --name terok terok-in-docker
#   # open http://localhost:8566  •  docker logs terok → gate admin token
#
# Full documentation: docs/docker.md
#

FROM quay.io/podman/stable

LABEL maintainer="terok maintainers"
LABEL org.opencontainers.image.description="terok with nested rootless Podman — try terok without installing Podman on the host"
LABEL org.opencontainers.image.source="https://github.com/terok-ai/terok"

# ── 1. System packages ──────────────────────────────────────────
RUN dnf install -y \
        python3 \
        python3-pip \
        python3-devel \
        git \
        nftables \
    && dnf clean all

# ── 2. Install terok from local source tree ──────────────────────
COPY . /opt/terok-src
RUN pip install --break-system-packages poetry-dynamic-versioning \
    && cd /opt/terok-src \
    && git init && git add -A \
    && git -c user.name=build -c user.email=build@localhost commit -m init \
    && git tag v0.0.0 \
    && pip install --break-system-packages . \
    && rm -rf /opt/terok-src

# ── 3. Prepare terok directories and shell completions ────────────
# Switch to the image's podman user (uid 1000) for all user-space
# setup — directories are created with correct ownership naturally.
USER podman
RUN mkdir -p \
        ~/.config/terok \
        ~/.config/containers \
        ~/.local/share/terok/gate \
        ~/.cache/terok \
        ~/.cache/containers \
    && printf '%s\n' \
        'unqualified-search-registries = ["docker.io"]' \
        > ~/.config/containers/registries.conf \
    && terokctl completions install --shell bash
USER root

# ── 4. Inline entrypoint ──────────────────────────────────────────
# Runs as root (the image default): fixes bind-mount ownership,
# then drops to the podman user via su -m (preserving env vars).
RUN printf '%s\n' '#!/bin/sh' \
        'set -e' \
        'for d in /home/podman/.config/terok /home/podman/.local/share/terok /home/podman/.local/share/terok/gate /home/podman/.local/share/containers; do' \
        '    mkdir -p "$d"; chown -R podman:podman "$d"' \
        'done' \
        'if [ $# -gt 0 ]; then exec su -m podman -s /bin/sh -c '"'"'exec "$@"'"'"' sh "$@"; fi' \
        'if [ -z "$TEROK_GATE_ADMIN_TOKEN" ]; then' \
        '    TEROK_GATE_ADMIN_TOKEN=$(python3 -c "import namer; print(namer.generate(separator=\"-\", category=sorted(namer.list_categories())))")' \
        '    export TEROK_GATE_ADMIN_TOKEN' \
        'fi' \
        'echo ""' \
        'echo "══════════════════════════════════════════════════"' \
        'echo "  Gate admin token: $TEROK_GATE_ADMIN_TOKEN"' \
        'echo "  git clone http://$TEROK_GATE_ADMIN_TOKEN@localhost:9418/<project>.git"' \
        'echo "══════════════════════════════════════════════════"' \
        'echo ""' \
        'su -m podman -s /bin/sh -c "terokctl gate-server start"' \
        'exec su -m podman -s /bin/sh -c "exec terok-web --host 0.0.0.0 --port 8566 ${TEROK_PUBLIC_URL:+--public-url \"$TEROK_PUBLIC_URL\"}"' \
        > /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

WORKDIR /home/podman
EXPOSE 8566 9418 7860-7880

ENV HOME=/home/podman
ENV TEROK_GATE_BIND=0.0.0.0

ENTRYPOINT ["docker-entrypoint.sh"]
