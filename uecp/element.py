"""UECP message field: splitting a frame's MSG into addressed message elements.

Reference: SPB 490 v7.05 sections 2.3.1 to 2.3.4.

One element is

    MEC [DSN] [PSN] [MEL] [MED...]

with the optional fields present only for the commands that declare them - see
mec.py, which carries that per-command layout straight from the spec.
"""
from __future__ import annotations

from dataclasses import dataclass

from .mec import MecSpec, lookup, name_of

# Data Set Number targets, section 2.3.3 Table 5
DSN_CURRENT = 0x00        # the current data set
DSN_ALL_OTHER = 0xFE      # every data set except the current one
DSN_ALL = 0xFF            # every data set

# Programme Service Number targets, section 2.3.4 Table 6
PSN_MAIN = 0x00           # main service of the addressed data set(s)


def dsn_targets(dsn: int, data_set: int, current: int) -> bool:
    """Does an element addressed `dsn` apply to data set `data_set`?

    `current` is the data set presently selected by MEC 0x1C. The spec gives
    four cases and they are all distinct - in particular 0xFE means "all data
    sets except the current one", which is easy to miss and cannot be collapsed
    into the 0xFF wildcard.
    """
    if dsn == DSN_CURRENT:
        return data_set == current
    if dsn == DSN_ALL:
        return True
    if dsn == DSN_ALL_OTHER:
        return data_set != current
    return dsn == data_set


def psn_targets(psn: int, service: int, main: int = 1) -> bool:
    """Does an element addressed `psn` apply to programme service `service`?

    PSN 0 is not a wildcard: it means the main service of the addressed data
    set. `main` says which PSN that is.
    """
    if psn == PSN_MAIN:
        return service == main
    return psn == service


@dataclass(frozen=True)
class Element:
    """One decoded message element."""
    mec: int
    data: bytes
    dsn: int | None = None
    psn: int | None = None
    spec: MecSpec | None = None

    @property
    def name(self) -> str:
        return self.spec.name if self.spec else name_of(self.mec)

    @property
    def known(self) -> bool:
        return self.spec is not None

    def targets(self, data_set: int, service: int, current: int, main: int = 1) -> bool:
        """True when this element should be applied to the given data set/service.

        An element with no DSN field is global (the clock, for instance) and
        applies everywhere; likewise one with no PSN applies to every service in
        a matching data set.
        """
        if self.dsn is not None and not dsn_targets(self.dsn, data_set, current):
            return False
        if self.psn is not None and not psn_targets(self.psn, service, main):
            return False
        return True

    def __str__(self) -> str:
        addr = ""
        if self.dsn is not None:
            addr = f" DSN={self.dsn}"
            if self.psn is not None:
                addr += f" PSN={self.psn}"
        return f"{self.name} (0x{self.mec:02X}){addr}, {len(self.data)} byte(s)"


class ElementError(Exception):
    """An element that cannot be parsed, with the offset it was found at."""

    def __init__(self, message: str, offset: int) -> None:
        super().__init__(f"{message} at offset {offset}")
        self.offset = offset


def split(message: bytes, *, strict: bool = False):
    """Walk a MSG field, yielding Element objects.

    Stops at the first element it cannot size, because without a length there is
    no way to find where the next element starts - continuing would emit
    nonsense. Yields an ElementError in that case (or raises it when strict).

    Unknown MECs are unsizeable for exactly that reason: the length rules are
    per-command, not generic.
    """
    pos, end = 0, len(message)
    while pos < end:
        mec = message[pos]
        spec = lookup(mec)
        if spec is None:
            err = ElementError(f"unknown MEC 0x{mec:02X}, cannot determine length", pos)
            if strict:
                raise err
            yield err
            return

        header = spec.header_len
        if pos + header > end:
            err = ElementError(f"{spec.name} header truncated", pos)
            if strict:
                raise err
            yield err
            return

        cursor = pos + 1
        dsn = psn = None
        if spec.dsn:
            dsn = message[cursor]
            cursor += 1
        if spec.psn:
            psn = message[cursor]
            cursor += 1

        if spec.variable:
            mel = message[cursor]
            cursor += 1
            data_end = cursor + mel
            if data_end > end:
                err = ElementError(f"{spec.name} MEL={mel} runs past the message", pos)
                if strict:
                    raise err
                yield err
                return
            # Real-world quirk carried over from the QN8066 reference: some
            # encoders set RT's MEL to the text length only, omitting the MED
            # config byte, leaving exactly one byte unaccounted for. A lone
            # trailing byte can never be a real element (the shortest possible
            # header is 1 byte for a MEC we would then fail to size anyway, and
            # every addressed command needs 3+), so absorbing it is safe.
            if mec == 0x0A and end - data_end == 1:
                data_end = end
            data = message[cursor:data_end]
        else:
            data_end = cursor + spec.length
            if data_end > end:
                err = ElementError(
                    f"{spec.name} needs {spec.length} data byte(s), only "
                    f"{end - cursor} present", pos)
                if strict:
                    raise err
                yield err
                return
            data = message[cursor:data_end]

        yield Element(mec, data, dsn, psn, spec)
        pos = data_end


def build(mec: int, data: bytes = b"", dsn: int | None = None,
          psn: int | None = None) -> bytes:
    """Build a single message element. Mainly for tests and bi-directional replies."""
    spec = lookup(mec)
    if spec is None:
        raise ValueError(f"unknown MEC 0x{mec:02X}")
    out = bytearray([mec])
    if spec.dsn:
        out.append((dsn or 0) & 0xFF)
    if spec.psn:
        out.append((psn or 0) & 0xFF)
    if spec.variable:
        if len(data) > 0xFC:
            raise ValueError(f"{spec.name} data is {len(data)} bytes, max 252")
        out.append(len(data))
    elif len(data) != spec.length:
        raise ValueError(
            f"{spec.name} takes exactly {spec.length} data byte(s), got {len(data)}")
    out += data
    return bytes(out)
