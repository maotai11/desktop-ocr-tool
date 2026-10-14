"""Bounded binary protocol. No pickle, shell command, or native S-expression."""
from dataclasses import dataclass
import math
import struct

MAGIC = b'ZHW1'
VERSION = 1
START, READY, RECOGNIZE, RESULT, STOP = 1, 2, 3, 4, 5
HEADER = struct.Struct('<4sBBHQI')
MAX_STROKES = 128
MAX_POINTS_PER_STROKE = 512
MAX_POINTS = 16384
CANVAS_SIZE = 1000
NBEST = 10
MAX_LABEL_BYTES = 15
MAX_REQUEST_BYTES = 2 + MAX_STROKES * 2 + MAX_POINTS * 4
MAX_REPLY_BYTES = 4 + NBEST * (1 + MAX_LABEL_BYTES + 4)


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    kind: int
    generation: int
    payload: bytes


def frame(kind, generation, payload=b''):
    if kind not in (START, READY, RECOGNIZE, RESULT, STOP):
        raise ProtocolError('Unknown message type')
    if type(generation) is not int or not 0 <= generation < 2 ** 64:
        raise ProtocolError('Invalid generation')
    if len(payload) > MAX_REQUEST_BYTES:
        raise ProtocolError('Oversized message')
    return HEADER.pack(MAGIC, VERSION, kind, 0, generation, len(payload)) + payload


def encode_strokes(strokes):
    # Materialize only already-bounded lists/tuples, never consume an iterator
    # that could be infinite or allocate before its count is checked.
    if not isinstance(strokes, (tuple, list)) or not 1 <= len(strokes) <= MAX_STROKES:
        raise ProtocolError('Invalid stroke count')
    output = bytearray(struct.pack('<H', len(strokes)))
    total = 0
    for stroke in strokes:
        if not isinstance(stroke, (tuple, list)) or not 1 <= len(stroke) <= MAX_POINTS_PER_STROKE:
            raise ProtocolError('Invalid point count')
        total += len(stroke)
        if total > MAX_POINTS:
            raise ProtocolError('Too many points')
        output.extend(struct.pack('<H', len(stroke)))
        for point in stroke:
            if not isinstance(point, (tuple, list)) or len(point) != 2:
                raise ProtocolError('Invalid point')
            if any(type(value) is not int or not 0 <= value <= CANVAS_SIZE for value in point):
                raise ProtocolError('Invalid coordinate')
            output.extend(struct.pack('<HH', *point))
    return bytes(output)


class ReplyDecoder:
    """Incremental bounded parser; a partial frame never resets a deadline."""
    def __init__(self):
        self._buffer = bytearray()

    def feed(self, chunk):
        # The caller caps every QProcess read and its total request output.
        if len(chunk) + len(self._buffer) > HEADER.size + MAX_REPLY_BYTES:
            raise ProtocolError('Oversized helper output')
        self._buffer.extend(chunk)
        if len(self._buffer) < HEADER.size:
            return None
        magic, version, kind, reserved, generation, length = HEADER.unpack_from(self._buffer)
        if (magic != MAGIC or version != VERSION or reserved != 0
                or kind not in (READY, RESULT) or length > MAX_REPLY_BYTES):
            raise ProtocolError('Invalid helper header')
        size = HEADER.size + length
        if len(self._buffer) < size:
            return None
        if len(self._buffer) != size:
            raise ProtocolError('Unexpected additional helper output')
        reply = Frame(kind, generation, bytes(self._buffer[HEADER.size:]))
        self._buffer.clear()
        return reply


def decode_candidates(payload, alphabet):
    if len(payload) < 4 or len(payload) > MAX_REPLY_BYTES:
        raise ProtocolError('Invalid reply length')
    status, count = struct.unpack_from('<HH', payload)
    if status not in (0, 1, 2, 3) or count > NBEST or (status != 0 and count != 0):
        raise ProtocolError('Invalid reply status')
    offset = 4
    result = []
    seen = set()
    for _ in range(count):
        if offset >= len(payload):
            raise ProtocolError('Truncated label')
        length = payload[offset]
        offset += 1
        if not 1 <= length <= MAX_LABEL_BYTES or offset + length + 4 > len(payload):
            raise ProtocolError('Invalid label length')
        try:
            label = payload[offset:offset + length].decode('utf-8', errors='strict')
        except UnicodeError as exc:
            raise ProtocolError('Invalid candidate UTF-8') from exc
        offset += length
        score, = struct.unpack_from('<f', payload, offset)
        offset += 4
        if label not in alphabet or label in seen or not math.isfinite(score):
            raise ProtocolError('Invalid candidate')
        seen.add(label)
        result.append(label)  # A ranking score is deliberately not a probability.
    if offset != len(payload):
        raise ProtocolError('Trailing reply bytes')
    return status, tuple(result)
