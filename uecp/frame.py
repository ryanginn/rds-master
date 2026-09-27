"""UECP data link layer: framing, byte stuffing and CRC.

Reference: RDS Forum SPB 490 version 7.05 (February 2010), section 2.2.

Wire format of one record:

    STA   ADD[2]   SQC   MFL   MSG[MFL]   CRC[2]   STP
    0xFE                                            0xFF

Everything between ADD and CRC inclusive is byte-stuffed, so 0xFE and 0xFF
appear only as the start and stop bytes and framing can always be recovered.
"""
from __future__ import annotations

from dataclasses import dataclass

STA = 0xFE
STP = 0xFF
ESC = 0xFD

# Section 2.2.9, Table 3. The escaped byte is the original minus 0xFD, so the
# pairs are FD 00, FD 01, FD 02 for FD, FE, FF respectively.
_STUFF = {0xFD: 0x00, 0xFE: 0x01, 0xFF: 0x02}
_UNSTUFF = {v: k for k, v in _STUFF.items()}

MAX_FRAME = 1024          # generous; a legal record cannot exceed ~520 stuffed bytes


class UecpError(Exception):
    """A frame that cannot be decoded."""


def crc16(data: bytes) -> int:
    """CRC-16/CCITT over data, with the final inversion UECP requires.

    Appendix 1: polynomial x^16 + x^12 + x^5 + 1 (0x1021), preset to all ones,
    and the result is ones-complemented before transmission.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc ^ 0xFFFF


def stuff(data: bytes) -> bytes:
    """Escape 0xFD/0xFE/0xFF so they cannot be mistaken for framing bytes."""
    out = bytearray()
    for byte in data:
        if byte in _STUFF:
            out.append(ESC)
            out.append(_STUFF[byte])
        else:
            out.append(byte)
    return bytes(out)


def unstuff(data: bytes) -> bytes:
    """Reverse stuff(). A trailing lone escape byte is dropped."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        byte = data[i]
        if byte == ESC:
            if i + 1 >= n:
                break                      # dangling escape at end of frame
            nxt = data[i + 1]
            # Spec pairs are FD 00/01/02. Tolerate anything else by restoring
            # ESC + byte rather than dropping data on the floor.
            out.append(_UNSTUFF.get(nxt, ESC))
            if nxt not in _UNSTUFF:
                out.append(nxt)
            i += 2
            continue
        out.append(byte)
        i += 1
    return bytes(out)


@dataclass(frozen=True)
class Address:
    """UECP address: 10-bit site address, 6-bit encoder address (section 1.1)."""
    site: int
    encoder: int

    @classmethod
    def from_word(cls, word: int) -> "Address":
        return cls((word >> 6) & 0x03FF, word & 0x3F)

    @property
    def word(self) -> int:
        return ((self.site & 0x03FF) << 6) | (self.encoder & 0x3F)

    def accepts(self, other: "Address") -> bool:
        """True when a frame addressed `other` is for us.

        Site 0 and encoder 0 are wildcards on both sides: a frame addressed to
        site 0 goes to every site, and an encoder configured with site 0 answers
        to every site. The same applies independently to the encoder address, so
        an unconfigured encoder (0/0) accepts everything.
        """
        site_ok = other.site == 0 or self.site == 0 or other.site == self.site
        enc_ok = other.encoder == 0 or self.encoder == 0 or other.encoder == self.encoder
        return site_ok and enc_ok

    def __str__(self) -> str:
        return f"site {self.site} / encoder {self.encoder}"


@dataclass(frozen=True)
class Frame:
    """One decoded UECP record."""
    address: Address
    sqc: int                 # sequence counter; 0 means "not used"
    message: bytes           # the MSG field, still to be split into elements

    def __str__(self) -> str:
        return f"UECP frame from {self.address}, SQC={self.sqc}, {len(self.message)} msg byte(s)"


def decode(raw_inner: bytes) -> Frame:
    """Decode the bytes captured between STA and STP.

    Raises UecpError with a specific reason; callers log and drop the frame.
    """
    data = unstuff(raw_inner)
    if len(data) < 3:
        raise UecpError(f"frame too short ({len(data)} bytes after de-stuffing)")

    body, crc_rx = data[:-2], (data[-2] << 8) | data[-1]
    crc_calc = crc16(body)
    if crc_rx != crc_calc:
        raise UecpError(f"CRC mismatch (got 0x{crc_rx:04X}, computed 0x{crc_calc:04X})")

    if len(body) < 4:
        raise UecpError(f"header truncated ({len(body)} bytes, need ADD+SQC+MFL)")

    address = Address.from_word((body[0] << 8) | body[1])
    sqc = body[2]
    mfl = body[3]
    message = body[4:4 + mfl]
    if len(message) < mfl:
        # A short read is worth surfacing: the sender's MFL disagrees with what
        # actually arrived, which usually means a truncated or corrupt record.
        raise UecpError(f"MFL says {mfl} message bytes, only {len(message)} present")
    return Frame(address, sqc, message)


def encode(address: Address, sqc: int, message: bytes) -> bytes:
    """Build a complete record, ready to put on the wire."""
    if len(message) > 255:
        raise UecpError(f"message field is {len(message)} bytes, max 255")
    word = address.word
    body = bytes([(word >> 8) & 0xFF, word & 0xFF, sqc & 0xFF, len(message)]) + message
    body += crc16(body).to_bytes(2, "big")
    return bytes([STA]) + stuff(body) + bytes([STP])


class FrameReader:
    """Reassembles frames from an arbitrarily chunked byte stream.

    Feed it whatever arrives from a socket; it yields one Frame per complete
    record. Bytes outside a STA/STP pair are discarded, so a reader that joins
    mid-record resynchronises at the next STA rather than emitting garbage.
    """

    def __init__(self, max_frame: int = MAX_FRAME) -> None:
        self._buf = bytearray()
        self._in_frame = False
        self._max = max_frame
        self.dropped = 0          # frames discarded; useful as a health signal

    def feed(self, chunk: bytes):
        """Yield (Frame | UecpError) for each complete record in the stream."""
        for byte in chunk:
            if byte == STA:
                if self._in_frame:
                    self.dropped += 1       # unterminated record, restart here
                self._in_frame = True
                self._buf.clear()
                continue
            if not self._in_frame:
                continue                    # noise between records
            if byte == STP:
                self._in_frame = False
                raw = bytes(self._buf)
                self._buf.clear()
                try:
                    yield decode(raw)
                except UecpError as exc:
                    self.dropped += 1
                    yield exc
                continue
            if len(self._buf) >= self._max:
                self._in_frame = False      # runaway record; wait for the next STA
                self._buf.clear()
                self.dropped += 1
                continue
            self._buf.append(byte)
