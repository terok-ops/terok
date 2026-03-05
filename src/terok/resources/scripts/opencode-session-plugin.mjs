// SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
//
// SPDX-License-Identifier: Apache-2.0

// OpenCode plugin that captures the session ID for terok session resume.
// Writes the session ID to the path specified in TEROK_SESSION_FILE env var.
//
// Event schema for session.created:
//   { type: "session.created", properties: { info: { id: "ses_..." } } }
export const terokSession = async () => ({
  event: async ({ event }) => {
    if (event.type === "session.created") {
      const file = process.env.TEROK_SESSION_FILE;
      const sessionID = event.properties?.info?.id;
      if (file && sessionID) {
        try {
          const fs = await import("node:fs");
          fs.writeFileSync(file, sessionID + "\n");
        } catch {
          // Best-effort capture; ignore write failures.
        }
      }
    }
  },
});
