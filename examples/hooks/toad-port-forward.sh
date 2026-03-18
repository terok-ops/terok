#!/bin/sh
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
#
# Example hook: forward toad web port via socat.
#
# Usage in config.yml or project.yml:
#   hooks:
#     post_ready: /path/to/toad-port-forward.sh
#     post_stop: /path/to/toad-port-forward.sh
#
# Requires: socat

PID_FILE="/tmp/terok-socat-${TEROK_TASK_ID}.pid"

case "$TEROK_HOOK" in
    post_ready)
        [ "$TEROK_TASK_MODE" = "toad" ] || exit 0
        [ -n "$TEROK_WEB_PORT" ] || exit 0
        socat TCP-LISTEN:"$TEROK_WEB_PORT",fork,reuseaddr TCP:127.0.0.1:"$TEROK_WEB_PORT" &
        echo $! > "$PID_FILE"
        ;;
    post_stop)
        if [ -f "$PID_FILE" ]; then
            kill "$(cat "$PID_FILE")" 2>/dev/null
            rm -f "$PID_FILE"
        fi
        ;;
esac
