# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the askpass wire protocol.

The protocol is pure data — no sockets, no asyncio — so these tests
exercise encode/decode and the two validating parsers directly.
"""

from __future__ import annotations

import json

import pytest

from terok.tui import askpass_protocol as proto


class TestEncodeDecode:
    """Round-trip encoding and decoding of JSON frames."""

    def test_encode_appends_newline(self) -> None:
        """Frames are newline-terminated so ``readline`` fits the wire format."""
        raw = proto.encode({"ok": True})
        assert raw.endswith(b"\n")
        assert raw.count(b"\n") == 1

    def test_roundtrip_preserves_strings_with_special_chars(self) -> None:
        """Passphrases with newlines, quotes, and unicode survive the round-trip."""
        tricky = 'p@ss\nword "with" quotes — ünicode'
        frame = {"answer": tricky, "request_id": "abc"}
        decoded = proto.decode(proto.encode(frame))
        assert decoded == frame

    def test_decode_accepts_missing_trailing_newline(self) -> None:
        """``readline`` on an unclosed socket returns data without the newline."""
        raw = json.dumps({"k": "v"}).encode("utf-8")  # no trailing newline
        assert proto.decode(raw) == {"k": "v"}

    def test_decode_rejects_non_json(self) -> None:
        """Garbage on the wire raises [`AskpassProtocolError`][]."""
        with pytest.raises(proto.AskpassProtocolError):
            proto.decode(b"not json at all\n")

    def test_decode_rejects_non_object_json(self) -> None:
        """A bare JSON string or array isn't a valid frame."""
        with pytest.raises(proto.AskpassProtocolError):
            proto.decode(b'"just a string"\n')
        with pytest.raises(proto.AskpassProtocolError):
            proto.decode(b"[1, 2, 3]\n")

    def test_decode_rejects_invalid_utf8(self) -> None:
        """Non-UTF8 bytes also surface as the module's error type."""
        with pytest.raises(proto.AskpassProtocolError):
            proto.decode(b"\xff\xfe\n")


class TestBuilders:
    """Request/answer/cancel factory helpers."""

    def test_make_request_generates_unique_ids(self) -> None:
        """Two calls without an explicit id produce different uuids."""
        a = proto.make_request("prompt 1")
        b = proto.make_request("prompt 2")
        assert a["request_id"] != b["request_id"]
        assert a["prompt"] == "prompt 1"

    def test_make_request_accepts_explicit_id(self) -> None:
        """Tests can pin the id to make assertions deterministic."""
        req = proto.make_request("p", request_id="fixed-id")
        assert req == {"request_id": "fixed-id", "prompt": "p"}

    def test_make_answer_and_cancel_shapes(self) -> None:
        """Accept has ``answer``, cancel has ``cancel: true`` — no overlap."""
        assert proto.make_answer("rid", "pass") == {"request_id": "rid", "answer": "pass"}
        assert proto.make_cancel("rid") == {"request_id": "rid", "cancel": True}


class TestParseRequest:
    """Validate helper → TUI requests."""

    def test_valid_request_parses(self) -> None:
        """Well-formed request returns the id and prompt tuple."""
        assert proto.parse_request({"request_id": "x", "prompt": "p"}) == ("x", "p")

    def test_missing_request_id_rejected(self) -> None:
        """No id means we can't correlate the reply — refuse."""
        with pytest.raises(proto.AskpassProtocolError, match="request_id"):
            proto.parse_request({"prompt": "p"})

    def test_empty_request_id_rejected(self) -> None:
        """An empty string id is as useless as a missing one."""
        with pytest.raises(proto.AskpassProtocolError, match="request_id"):
            proto.parse_request({"request_id": "", "prompt": "p"})

    def test_missing_prompt_rejected(self) -> None:
        """No prompt means the modal would have nothing to show."""
        with pytest.raises(proto.AskpassProtocolError, match="prompt"):
            proto.parse_request({"request_id": "x"})


class TestParseReply:
    """Validate TUI → helper replies."""

    def test_valid_answer_parses(self) -> None:
        """``answer`` comes back as the tuple's second element."""
        assert proto.parse_reply({"request_id": "x", "answer": "pass"}) == ("x", "pass")

    def test_cancel_returns_none_answer(self) -> None:
        """``cancel: true`` surfaces as ``(id, None)`` to the helper."""
        assert proto.parse_reply({"request_id": "x", "cancel": True}) == ("x", None)

    def test_empty_passphrase_is_valid(self) -> None:
        """Some keys really do have empty passphrases — don't reject them."""
        assert proto.parse_reply({"request_id": "x", "answer": ""}) == ("x", "")

    def test_cancel_false_is_not_cancel(self) -> None:
        """Only the literal ``true`` marker cancels; ``false`` just needs an answer."""
        with pytest.raises(proto.AskpassProtocolError):
            proto.parse_reply({"request_id": "x", "cancel": False})

    def test_neither_answer_nor_cancel_rejected(self) -> None:
        """A reply must commit to one of the two shapes."""
        with pytest.raises(proto.AskpassProtocolError):
            proto.parse_reply({"request_id": "x"})

    def test_both_cancel_and_answer_rejected(self) -> None:
        """A reply with both ``cancel: true`` and ``answer`` is ambiguous.

        Parser must raise rather than silently picking one — otherwise
        a sender bug that duplicates both fields would be hidden at
        the receiver, and the helper's decision (exit 0 vs non-zero)
        would depend on field-ordering quirks.
        """
        with pytest.raises(proto.AskpassProtocolError, match="both"):
            proto.parse_reply({"request_id": "x", "cancel": True, "answer": "pass"})
