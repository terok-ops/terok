#!/bin/sh
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
#
# Example hook: log task lifecycle events.
#
# Usage in config.yml or project.yml:
#   hooks:
#     pre_start: /path/to/task-notify.sh
#     post_start: /path/to/task-notify.sh
#     post_ready: /path/to/task-notify.sh
#     post_stop: /path/to/task-notify.sh

echo "[hook] $TEROK_HOOK: project=$TEROK_PROJECT_ID task=$TEROK_TASK_ID mode=$TEROK_TASK_MODE container=$TEROK_CONTAINER_NAME${TEROK_WEB_PORT:+ port=$TEROK_WEB_PORT}" >&2
