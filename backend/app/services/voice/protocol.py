"""BytePlus Seed Speech WebSocket binary framing.

Shared by the ASR and TTS clients, which speak the same wire format with different
message types on top of it. Ported directly from the "WebSocket Binary Protocol" section
of the BytePlus Seed Speech API reference.

Frame layout (all integers big-endian):

    byte 0   protocol version (4 bits) | header size in 4-byte words (4 bits)
    byte 1   message type (4 bits)     | message-type-specific flags (4 bits)
    byte 2   serialization (4 bits)    | compression (4 bits)
    byte 3   reserved (0x00)
    ...      optional fields, present per message type and flags:
               event number   (4B)  when FLAG_WITH_EVENT is set
               connect id     (4B size + bytes)   connection-scoped server events
               session id     (4B size + bytes)   session-scoped events
               sequence       (4B)  when FLAG_WITH_SEQUENCE is set
               error code     (4B)  error frames only
    ...      payload size (4B) followed by payload

The optional-field ORDER above is load-bearing: read them in the wrong order and every
field after it is garbage, which surfaces as unicode errors rather than as anything that
points at framing. Encode and decode are kept in one file so that order is stated once.
"""

import gzip
import json
import struct
from dataclasses import dataclass

PROTOCOL_VERSION = 0b0001
HEADER_WORDS = 0b0001  # header size = 1 * 4 bytes

# Message types.
CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_AUDIO_ONLY = 0b1011
SERVER_ERROR = 0b1111

# Message-type-specific flags. bit0 = a sequence number is encoded in the frame,
# bit1 = this is the last packet, bit2 = an event number is encoded in the frame.
FLAG_NONE = 0b0000
FLAG_WITH_SEQUENCE = 0b0001
FLAG_LAST_PACKET = 0b0010
FLAG_WITH_EVENT = 0b0100

# Serialization / compression nibbles.
SERIAL_RAW = 0b0000
SERIAL_JSON = 0b0001
COMPRESS_NONE = 0b0000
COMPRESS_GZIP = 0b0001


@dataclass(slots=True)
class Frame:
    """One decoded server frame. Optional fields are None when absent from the wire."""

    message_type: int
    flags: int
    serialization: int
    compression: int
    payload: bytes
    event: int | None = None
    session_id: str | None = None
    connect_id: str | None = None
    sequence: int | None = None
    error_code: int | None = None

    @property
    def is_error(self) -> bool:
        return self.message_type == SERVER_ERROR

    @property
    def is_audio(self) -> bool:
        return self.message_type == SERVER_AUDIO_ONLY

    def json(self) -> dict:
        """Decode a JSON payload, tolerating an empty one."""
        if not self.payload:
            return {}
        return json.loads(self.payload.decode("utf-8"))

    def text(self) -> str:
        return self.payload.decode("utf-8", "replace")


def _prefixed(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def encode(
    message_type: int,
    payload: bytes,
    *,
    flags: int = FLAG_NONE,
    serialization: int = SERIAL_JSON,
    compression: int = COMPRESS_GZIP,
    event: int | None = None,
    session_id: str | None = None,
    connect_id: str | None = None,
) -> bytes:
    """Build one client frame.

    `payload` is compressed here when `compression` says so, so callers hand over plain
    bytes (JSON or raw audio) and never have to remember which legs are gzipped.
    """
    if compression == COMPRESS_GZIP:
        payload = gzip.compress(payload)

    frame = bytearray(
        [
            (PROTOCOL_VERSION << 4) | HEADER_WORDS,
            (message_type << 4) | flags,
            (serialization << 4) | compression,
            0x00,
        ]
    )
    if event is not None:
        frame += struct.pack(">i", event)
    if connect_id is not None:
        frame += _prefixed(connect_id)
    if session_id is not None:
        frame += _prefixed(session_id)

    frame += struct.pack(">I", len(payload))
    frame += payload
    return bytes(frame)


def decode(raw: bytes) -> Frame:
    """Parse one server frame.

    Raises ValueError on anything that is not a frame we recognise, rather than
    returning a half-populated Frame - a caller that silently treats a malformed frame
    as "no transcript yet" hangs the interview instead of failing it.
    """
    if len(raw) < 4:
        raise ValueError(f"Seed Speech frame too short: {len(raw)} bytes")

    header_words = raw[0] & 0x0F
    message_type = raw[1] >> 4
    flags = raw[1] & 0x0F
    serialization = raw[2] >> 4
    compression = raw[2] & 0x0F

    # header_words counts 4-byte words, so a server that ever sends header extensions
    # is skipped over correctly rather than misread.
    offset = header_words * 4
    event = session_id = connect_id = sequence = error_code = None

    def take(n: int) -> bytes:
        nonlocal offset
        if offset + n > len(raw):
            raise ValueError("Seed Speech frame truncated while reading optional fields")
        chunk = raw[offset : offset + n]
        offset += n
        return chunk

    if flags & FLAG_WITH_EVENT:
        event = struct.unpack(">i", take(4))[0]
        # Connection-scoped events carry a connect id; session-scoped ones a session id.
        # Both are length-prefixed strings, so the distinction is which event it is.
        if event in _CONNECTION_EVENTS:
            size = struct.unpack(">I", take(4))[0]
            connect_id = take(size).decode("utf-8", "replace")
        elif event not in _NO_ID_EVENTS:
            size = struct.unpack(">I", take(4))[0]
            session_id = take(size).decode("utf-8", "replace")

    if flags & FLAG_WITH_SEQUENCE:
        sequence = struct.unpack(">i", take(4))[0]

    if message_type == SERVER_ERROR:
        error_code = struct.unpack(">I", take(4))[0]

    size = struct.unpack(">I", take(4))[0]
    payload = take(size) if size else b""

    if compression == COMPRESS_GZIP and payload:
        payload = gzip.decompress(payload)

    return Frame(
        message_type=message_type,
        flags=flags,
        serialization=serialization,
        compression=compression,
        payload=payload,
        event=event,
        session_id=session_id,
        connect_id=connect_id,
        sequence=sequence,
        error_code=error_code,
    )


# Event numbers that carry a connect id rather than a session id, and those that carry
# neither. Only the TTS leg uses events at all; ASR frames never set FLAG_WITH_EVENT.
_CONNECTION_EVENTS = frozenset({50, 51, 52})
_NO_ID_EVENTS = frozenset({1, 2})
