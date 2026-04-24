# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Wire protocol between ``terok-askpass`` and the TUI askpass listener.

Newline-delimited UTF-8 JSON — one object per line, both directions.
Passphrases are JSON strings so any newlines or quotes inside them are
escaped; we never need binary framing.

Shape:

- helper → TUI: ``{"request_id": "<uuid>", "prompt": "<ssh-prompt>"}``
- TUI → helper (accept): ``{"request_id": "<uuid>", "answer": "<pass>"}``
- TUI → helper (cancel): ``{"request_id": "<uuid>", "cancel": true}``

``request_id`` lets a listener in principle multiplex, though today the
TUI pops one modal at a time and a second request waits for the first
to resolve.

The module has no I/O.  :func:`encode` turns a dict into the wire
bytes, :func:`decode` turns a line of wire bytes back into a dict,
and :func:`parse_request` / :func:`parse_reply` validate the decoded
dicts on the receiving side.  Callers adapt the bytes to whatever
transport they have — a blocking ``socket.recv`` loop in the helper,
an ``asyncio.StreamReader.readline`` in the service.
"""

from __future__ import annotations

import json
import uuid
from typing import Any


class AskpassProtocolError(ValueError):
    """Raised on a malformed frame — missing keys, wrong types, bad JSON."""


def encode(obj: dict[str, Any]) -> bytes:
    """Serialise *obj* to a newline-terminated UTF-8 JSON frame."""
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def decode(line: bytes) -> dict[str, Any]:
    """Parse a single UTF-8 JSON line; strip the trailing ``\\n`` if present.

    Raises :class:`AskpassProtocolError` on malformed input rather than
    the stdlib :class:`json.JSONDecodeError` / :class:`UnicodeDecodeError`,
    so callers have one exception type to catch.
    """
    try:
        text = line.decode("utf-8").rstrip("\n")
        obj = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AskpassProtocolError(f"malformed frame: {exc}") from exc
    if not isinstance(obj, dict):
        raise AskpassProtocolError(f"frame is not a JSON object: {type(obj).__name__}")
    return obj


def make_request(prompt: str, *, request_id: str | None = None) -> dict[str, Any]:
    """Build a helper → TUI request.  *request_id* defaults to a fresh uuid4."""
    return {"request_id": request_id or uuid.uuid4().hex, "prompt": prompt}


def make_answer(request_id: str, answer: str) -> dict[str, Any]:
    """Build a TUI → helper accept reply."""
    return {"request_id": request_id, "answer": answer}


def make_cancel(request_id: str) -> dict[str, Any]:
    """Build a TUI → helper cancel reply — helper exits non-zero."""
    return {"request_id": request_id, "cancel": True}


def parse_request(frame: dict[str, Any]) -> tuple[str, str]:
    """Validate a helper → TUI request frame, return ``(request_id, prompt)``."""
    request_id = frame.get("request_id")
    prompt = frame.get("prompt")
    if not isinstance(request_id, str) or not request_id:
        raise AskpassProtocolError("request missing non-empty 'request_id'")
    if not isinstance(prompt, str):
        raise AskpassProtocolError("request missing string 'prompt'")
    return request_id, prompt


def parse_reply(frame: dict[str, Any]) -> tuple[str, str | None]:
    """Validate a TUI → helper reply, return ``(request_id, answer_or_None)``.

    ``answer_or_None`` is ``None`` for cancel, the passphrase string for
    accept.  An empty string is a valid passphrase (some keys have none)
    — only the explicit ``cancel: true`` marker aborts the helper.

    Cancel and answer are mutually exclusive: a frame that carries both
    is ambiguous (which wins?) and almost certainly a sender bug, so
    raise rather than silently picking one — the :func:`make_cancel` /
    :func:`make_answer` factories are the only sanctioned way to build
    a reply on our side.
    """
    request_id = frame.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise AskpassProtocolError("reply missing non-empty 'request_id'")
    is_cancel = frame.get("cancel") is True
    has_answer = "answer" in frame
    if is_cancel and has_answer:
        raise AskpassProtocolError(
            "reply has both 'cancel: true' and 'answer' — exactly one is allowed"
        )
    if is_cancel:
        return request_id, None
    answer = frame.get("answer")
    if not isinstance(answer, str):
        raise AskpassProtocolError("reply missing 'cancel' or string 'answer'")
    return request_id, answer
