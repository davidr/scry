"""Tests for the remote protocol framing and constants."""

import pytest

from scry.remote import protocol
from scry.remote.protocol import LIST_BEGIN, LIST_END, MAX_RESPONSE_BYTES, ProtocolError, frame, unframe


def test_frame_unframe_roundtrip():
    payload = {"proto": 1, "tabs": [{"id": "3", "name": "a b"}]}
    assert unframe(frame(payload).encode()) == payload


def test_unframe_ignores_noise_outside_sentinels():
    data = b"Welcome to host\nLast login: now\n" + frame({"a": 1}).encode() + b"logout banner\n"
    assert unframe(data) == {"a": 1}


def test_unframe_ignores_invalid_utf8_noise():
    data = b"\xff\xfe motd\n" + frame({"a": 1}).encode() + b"\xc3\n"
    assert unframe(data) == {"a": 1}


def test_unframe_missing_begin_raises():
    with pytest.raises(ProtocolError, match="no framed response"):
        unframe(b"just a banner\n")


def test_unframe_missing_end_raises():
    cut = frame({"a": 1}).rstrip("\n").rsplit("\n", 1)[0] + "\n"
    assert LIST_END not in cut
    with pytest.raises(ProtocolError, match="no framed response"):
        unframe(cut.encode())


def test_unframe_invalid_json_raises():
    data = f"{LIST_BEGIN}\nnot json\n{LIST_END}\n".encode()
    with pytest.raises(ProtocolError, match="invalid JSON"):
        unframe(data)


def test_unframe_non_dict_json_raises():
    data = f"{LIST_BEGIN}\n[1, 2]\n{LIST_END}\n".encode()
    with pytest.raises(ProtocolError, match="invalid JSON"):
        unframe(data)


def test_unframe_oversize_raises():
    with pytest.raises(ProtocolError, match="response too large"):
        unframe(b"x" * (MAX_RESPONSE_BYTES + 1))


def test_exit_codes_distinct_and_clear_of_reserved():
    names = [
        "EXIT_FAILURE",
        "EXIT_STALE",
        "EXIT_TAB_GONE",
        "EXIT_NO_SESSION",
        "EXIT_CONFIG",
        "EXIT_IDENTITY",
        "EXIT_PROTO",
    ]
    codes = [getattr(protocol, n) for n in names]
    assert len(set(codes)) == 7
    for code in codes:
        assert code not in {0, 1, 2, 126, 127, 255}
        assert code < 128
