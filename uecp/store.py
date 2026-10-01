"""The encoder state a UECP source drives: data sets and programme services.

Model, from SPB 490 v7.05:

    Store
      site / encoder address, clock, on-air flags
      current data set              (MEC 0x1C selects it)
      Data Set 1..253               (DSN)
        group sequence, variant sequence, slow labelling codes
        main PSN + other PSNs       (MEC 0x28 assigns which is which)
        Programme Service 1..255    (PSN)
          PI, PS, RT, PTY, PTYN, PIN, TA/TP, MS, DI, AF, Long PS, linkage

MEC 0x28 is explicit that one PSN in a data set is the main network service and
the rest are other networks for EON. That is the whole reason this is a tree and
not a flat set of fields - the old implementation collapsed it onto a single
PS/RT, so only one service could ever exist.

Nothing here touches the audio path. Applying a frame only mutates this store;
pushing it at the RDS generator is the bridge's job.
"""
from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field

from .element import Element, ElementError, split
from .frame import Address, Frame

# Shared with app.py: RDS has its own character set and reading UECP text with
# any other one turns accented letters into rubbish.
from rds_charset import rds_bytes_to_text

MAX_DATA_SET = 253        # DSN 1..253 address a specific set; 254/255 are wildcards
MAX_RT_LEN = 64
MAX_PS_LEN = 8
MAX_LONG_PS = 32
MAX_AF = 256
MAX_FREE_FORMAT = 64      # queued raw groups per group type, per data set

# A one-shot raw group describes a moment: RT+ tags belong to the RadioText
# that was current when they were sent, and a TMC set to the traffic situation
# then. Transmitting one long after it arrived is worse than dropping it - the
# tags would point into a song that has already finished - so anything that has
# waited this long is discarded instead of going to air late.
STALE_AFTER = 20.0        # seconds

# How long a group type keeps its place in the sequence after the last message
# for it arrived. Long enough to bridge the gap between a source's messages,
# short enough that a group it has stopped sending is dropped from the
# sequence rather than left occupying a slot for ever.
GROUP_IN_USE_FOR = 60.0   # seconds

# The generator thread pops raw groups while transport threads push them in, so
# every queue operation takes this. It is held for a list operation and nothing
# else - no I/O, no allocation of consequence - which is what the real-time
# audio path needs.
QUEUE_LOCK = threading.RLock()
MAX_TRANSPARENT = 64      # queued TDC/IH/EWS/TMC payloads
MAX_PAGING = 64           # decoded paging calls kept for the monitor

# How much of the encoder's structure the monitor lists even when nothing has
# arrived for it. A source only tells us about the data sets and services it
# actually uses, but an operator needs to see the shape of the thing - the way
# a 2wcom ARCOS lists Dataset 1..n each with its programme services - so the
# empty ones are shown as placeholders rather than left out.
MONITOR_DATA_SETS = 8
MONITOR_SERVICES = 18


def _rds_text(raw: bytes) -> str:
    """RDS/EBU bytes to Unicode, for display and for the UI.

    What goes to air is the original bytes, not this - see the `*_raw` fields.
    This is the readable version: the RDS character set decoded properly, with
    the 0x0D terminator and its padding dropped.
    """
    return rds_bytes_to_text(raw)


@dataclass
class RtMessage:
    """One RadioText message in a service's buffer (MEC 0x0A).

    `raw` is what the source actually sent, already in the RDS character set
    including its own terminator. That is what goes on air, byte for byte;
    `text` exists only so the monitor has something to show.
    """
    text: str
    transmissions: int = 0      # 0 = repeat indefinitely
    toggle_ab: bool = False
    raw: bytes = b""


@dataclass
class ProgrammeService:
    """One PSN: everything a single RDS service carries."""
    psn: int
    enabled: bool = True

    pi: int | None = None
    ps: str | None = None
    pty: int | None = None
    ptyn: str | None = None
    pin: int | None = None
    long_ps: str | None = None

    # The text exactly as the source sent it, already in the RDS character set.
    # This is what goes to air; the decoded strings above are for display only,
    # so nothing is lost re-encoding a character the table cannot round-trip.
    ps_raw: bytes = b""
    ptyn_raw: bytes = b""
    long_ps_raw: bytes = b""

    ta: bool = False
    tp: bool = False
    ms: bool = True            # 1 = music
    di: int = 0                # bit0 stereo, bit1 artificial head, bit2 compressed, bit3 dynamic PTY

    rt: list[RtMessage] = field(default_factory=list)
    rt_ab: bool = False

    af: bytearray = field(default_factory=bytearray)     # raw AF memory, MEC 0x13
    eon_af: bytearray = field(default_factory=bytearray)  # MEC 0x14
    linkage: int | None = None
    eon_elements: int = 0xFFFF                            # MEC 0x3F bitmap

    updated_at: float = 0.0

    def touch(self) -> None:
        self.updated_at = time.time()

    @property
    def pi_hex(self) -> str:
        return f"{self.pi:04X}" if self.pi is not None else ""

    @property
    def rt_text(self) -> str:
        return self.rt[0].text if self.rt else ""

    def summary(self) -> dict:
        return {
            "psn": self.psn, "enabled": self.enabled, "pi": self.pi_hex,
            "ps": self.ps or "", "pty": self.pty, "ptyn": self.ptyn or "",
            "rt": self.rt_text, "rt_count": len(self.rt),
            "ta": self.ta, "tp": self.tp, "ms": self.ms, "di": self.di,
            "pin": self.pin, "long_ps": self.long_ps or "",
            "af_bytes": len(self.af), "updated_at": self.updated_at,
            "empty": self.updated_at == 0.0,
        }


@dataclass
class FreeFormatGroup:
    """One queued raw group (MEC 0x24, MEC 0x30 TMC, or ODA data)."""
    group_index: int          # (type << 1) | version, same packing UECP uses
    b2_tail: int              # the 5 low bits of block 2
    b3: int
    b4: int
    cyclic: bool = False      # keep re-sending, rather than once and discard
    transmissions: int = 1    # how many times a non-cyclic set still goes out
    urgent: bool = False      # MEC 0x30's "extremely urgent" TMC flag
    source: str = ""          # what put it here, for the monitor
    queued_at: float = field(default_factory=time.time)

    @property
    def group_name(self) -> str:
        return f"{(self.group_index >> 1) & 0x0F}{'B' if self.group_index & 1 else 'A'}"


@dataclass
class OdaApplication:
    """An ODA registered by MEC 0x40/0x46: which group carries this AID."""
    aid: int
    group_index: int = 0
    message: int = 0          # the Group 3A message bits
    timeout: int = 0          # ODA data input timeout, minutes (0 = off)
    updated_at: float = 0.0
    # How we learned about it. Sources often send their ODA table as raw 3A
    # groups instead of MEC 0x40; those are already going out as raw groups, so
    # the encoder must not also generate its own 3A for them.
    announced_by: str = "MEC 0x40"

    @property
    def group_name(self) -> str:
        return f"{(self.group_index >> 1) & 0x0F}{'B' if self.group_index & 1 else 'A'}"


@dataclass
class DataSet:
    """One DSN: a set of programme services plus the encoder settings for them."""
    dsn: int
    main_psn: int = 1
    services: dict[int, ProgrammeService] = field(default_factory=dict)

    # Raw groups waiting to go out, keyed by group index. MEC 0x24 fills these,
    # and so does ODA data once its AID resolves to a group.
    free_format: dict[int, list] = field(default_factory=dict)

    group_sequence: list[int] = field(default_factory=list)      # MEC 0x16
    ext_group_sequence: dict[int, list[int]] = field(default_factory=dict)  # MEC 0x38
    variant_sequence: dict[int, list[int]] = field(default_factory=dict)    # MEC 0x29
    slc: dict[int, int] = field(default_factory=dict)            # MEC 0x1A: variant -> 12 bits

    # When each group type last had something queued. The sequence uses this
    # rather than the queue depth alone: a source sending one group every few
    # seconds empties its buffer between arrivals, and dropping the slot the
    # moment it goes empty would rewrite the group sequence twice a second and
    # leave the next arrival with nowhere to go.
    last_queued: dict = field(default_factory=dict)

    # Raw groups thrown away for having waited past STALE_AFTER. A health
    # figure for the operator, not part of the RDS data.
    dropped_stale: int = 0

    def service(self, psn: int, create: bool = True) -> ProgrammeService | None:
        svc = self.services.get(psn)
        if svc is None and create:
            svc = ProgrammeService(psn)
            self.services[psn] = svc
        return svc

    @property
    def main(self) -> ProgrammeService | None:
        return self.services.get(self.main_psn)

    @property
    def others(self) -> list[ProgrammeService]:
        """Every PSN that is not the main service: the EON other networks."""
        return [s for psn, s in sorted(self.services.items()) if psn != self.main_psn]

    def queue_group(self, ff) -> int:
        """Queue a raw group for transmission; returns the new queue depth."""
        with QUEUE_LOCK:
            self.last_queued[ff.group_index] = time.time()
            bucket = self.free_format.setdefault(ff.group_index, [])
            # An urgent TMC set goes to the front: that is the whole point of
            # the flag, and a backed-up buffer must not delay it.
            if getattr(ff, "urgent", False):
                bucket.insert(0, ff)
                del bucket[MAX_FREE_FORMAT:]
            else:
                bucket.append(ff)
                del bucket[:-MAX_FREE_FORMAT]
            return len(bucket)

    def clear_group(self, index: int) -> None:
        """Empty one group type's buffer (buffer configuration 11)."""
        with QUEUE_LOCK:
            self.free_format.pop(index, None)

    def take_group(self, index: int):
        """The next raw group to transmit for this group index, or None.

        Called from the generator thread on every scheduled group, so it does
        the least possible work: one list operation under QUEUE_LOCK.

        A cyclic set is rotated and kept. A set with a transmission count is
        also rotated, so several queued messages share the slot fairly, and is
        dropped once it has gone out as many times as the source asked for.
        """
        with QUEUE_LOCK:
            bucket = self.free_format.get(index)
            if not bucket:
                return None
            # Drop anything that has been waiting too long to still be true.
            now = time.time()
            while bucket and not bucket[0].cyclic and now - bucket[0].queued_at > STALE_AFTER:
                self.dropped_stale += 1
                bucket.pop(0)
            if not bucket:
                self.free_format.pop(index, None)
                return None
            ff = bucket.pop(0)
            if ff.cyclic:
                bucket.append(ff)
            else:
                ff.transmissions -= 1
                if ff.transmissions > 0:
                    bucket.append(ff)
            if not bucket:
                self.free_format.pop(index, None)
            return ff

    def pending(self) -> dict:
        """Queue depth per group index, for the sequence and the monitor."""
        with QUEUE_LOCK:
            return {i: len(q) for i, q in self.free_format.items() if q}

    def in_use(self) -> dict:
        """Group index -> current queue depth, for everything still in use.

        A group counts as in use while data has arrived for it recently, even
        if its buffer happens to be empty this instant, so its slot in the
        sequence survives the gaps between messages.
        """
        with QUEUE_LOCK:
            depths = {i: len(q) for i, q in self.free_format.items() if q}
            cutoff = time.time() - GROUP_IN_USE_FOR
            for index, at in self.last_queued.items():
                if at >= cutoff:
                    depths.setdefault(index, 0)
            return depths

    def summary(self) -> dict:
        return {
            "dsn": self.dsn, "main_psn": self.main_psn, "empty": False,
            "services": [s.summary() for _, s in sorted(self.services.items())],
            "group_sequence": list(self.group_sequence),
            "slc": dict(self.slc),
            "variant_sequence": {k: list(v) for k, v in self.variant_sequence.items()},
            "free_format": {
                f"{(gi >> 1) & 0x0F}{'B' if gi & 1 else 'A'}": len(q)
                for gi, q in sorted(self.free_format.items()) if q
            },
        }


@dataclass
class Clock:
    """MEC 0x0D/0x09: a global clock, not addressed to any data set."""
    year: int = 0
    month: int = 0
    day: int = 0
    hour: int = 0
    minute: int = 0
    second: int = 0
    centisecond: int = 0
    offset: int = 0            # raw 6-bit UECP offset: bit5 sign, bits4..0 half-hours
    received_at: float = 0.0

    @property
    def valid(self) -> bool:
        return self.received_at > 0

    def __str__(self) -> str:
        if not self.valid:
            return "not received"
        sign = "-" if self.offset & 0x20 else "+"
        half = self.offset & 0x1F
        return (f"{self.year:04d}-{self.month:02d}-{self.day:02d} "
                f"{self.hour:02d}:{self.minute:02d}:{self.second:02d} UTC "
                f"{sign}{half // 2}:{'30' if half % 2 else '00'}")


class Store:
    """Everything a UECP source has told us."""

    def __init__(self, site: int = 0, encoder: int = 0, log_size: int = 200) -> None:
        self.address = Address(site, encoder)
        self.data_sets: dict[int, DataSet] = {}
        self.current = 1
        self.clock = Clock()
        self.rds_on = True          # MEC 0x1E
        self.ct_on = True           # MEC 0x19
        self.rds_level: int | None = None   # MEC 0x0E
        self.rds_phase: int | None = None   # MEC 0x22

        # ODA applications, keyed by AID (MEC 0x40/0x46)
        self.oda: dict[int, OdaApplication] = {}
        # Transparent data channels: TDC / IH / EWS / TMC payloads as received
        self.transparent: dict[str, list] = {}
        # Decoded paging and EPP calls, newest first
        self.paging: list = []

        # Which commands this encoder will act on. None means all of them,
        # which is the default; a set restricts it. Every encoder of this kind
        # has the feature - a studio link should not be able to rewrite the PI
        # or the AF list just because it can reach the port.
        self.allowed_mecs: set | None = None

        # Health counters, so the UI can show whether anything is arriving.
        self.frames = 0
        self.elements_blocked = 0
        self.frames_rejected = 0
        self.elements = 0
        self.elements_ignored = 0
        self.errors = 0
        self.last_frame_at = 0.0
        self.last_error = ""
        self.log: deque = deque(maxlen=log_size)

    # -- structure ---------------------------------------------------------

    def data_set(self, dsn: int, create: bool = True) -> DataSet | None:
        ds = self.data_sets.get(dsn)
        if ds is None and create:
            ds = DataSet(dsn)
            self.data_sets[dsn] = ds
        return ds

    @property
    def live(self) -> DataSet | None:
        """The data set currently on air (MEC 0x1C)."""
        return self.data_sets.get(self.current)

    def take_group(self, index: int):
        """The live data set's next raw group for this index, or None.

        The generator calls this for every group it builds, which is why it
        lives on the store rather than making the caller find the data set.
        """
        ds = self.live
        return ds.take_group(index) if ds is not None else None

    def oda_group(self, aid: int) -> int:
        """The group index announced for an AID, or 0 if we have not seen one."""
        app = self.oda.get(aid)
        return app.group_index if app is not None else 0

    def matching_sets(self, dsn: int | None) -> list[DataSet]:
        """Data sets an element addressed `dsn` applies to, creating as needed.

        Section 2.3.3: 0 = current, 1-253 = specific, 254 = all but current,
        255 = all. For the two wildcards we only touch sets that already exist -
        inventing every one of 253 on a broadcast would be nonsense.

        `dsn is None` is different: the command has no DSN field at all, which
        means it applies to every data set rather than to the current one.
        """
        if dsn is None:
            return list(self.data_sets.values()) or [self.data_set(self.current)]
        if dsn == 0x00:
            return [self.data_set(self.current)]
        if dsn == 0xFF:
            return list(self.data_sets.values()) or [self.data_set(self.current)]
        if dsn == 0xFE:
            return [ds for n, ds in self.data_sets.items() if n != self.current]
        if 1 <= dsn <= MAX_DATA_SET:
            return [self.data_set(dsn)]
        return []

    def matching_services(self, ds: DataSet, psn: int | None) -> list[ProgrammeService]:
        """Services an element addressed `psn` applies to.

        Section 2.3.4: PSN 0 means the main service of the data set - it is not a
        wildcard, which the previous implementation got wrong.
        """
        if psn is None:
            return list(ds.services.values()) or [ds.service(ds.main_psn)]
        if psn == 0x00:
            return [ds.service(ds.main_psn)]
        return [ds.service(psn)]

    # -- reporting ---------------------------------------------------------

    def tree(self) -> list:
        """Every data set and service the monitor lists, used or not.

        Placeholders carry a service's default values and are flagged `empty`,
        so the UI can show the structure without pretending data arrived.
        """
        def blank(psn: int) -> dict:
            row = ProgrammeService(psn).summary()
            row["empty"] = True
            return row

        out = []
        for dsn in range(1, max([MONITOR_DATA_SETS] + list(self.data_sets)) + 1):
            ds = self.data_sets.get(dsn)
            if ds is None:
                out.append({
                    "dsn": dsn, "main_psn": 1, "empty": True,
                    "services": [blank(p) for p in range(1, MONITOR_SERVICES + 1)],
                    "group_sequence": [], "slc": {}, "free_format": {},
                })
                continue
            row = ds.summary()
            have = {svc["psn"] for svc in row["services"]}
            top = max([MONITOR_SERVICES] + list(ds.services))
            row["services"].extend(blank(p) for p in range(1, top + 1) if p not in have)
            row["services"].sort(key=lambda svc: svc["psn"])
            out.append(row)
        return out

    def note(self, text: str) -> None:
        self.log.appendleft({"at": time.time(), "text": text})

    def summary(self) -> dict:
        return {
            "site": self.address.site, "encoder": self.address.encoder,
            "current": self.current,
            "data_sets": [ds.summary() for _, ds in sorted(self.data_sets.items())],
            "tree": self.tree(),
            "clock": str(self.clock),
            "rds_on": self.rds_on, "ct_on": self.ct_on,
            "oda": [
                {"aid": f"{a.aid:04X}", "group": a.group_name,
                 "message": a.message, "timeout": a.timeout,
                 "announced_by": a.announced_by}
                for a in sorted(self.oda.values(), key=lambda x: x.aid)
            ],
            "queued_groups": [
                {"group": f"{(i >> 1) & 0x0F}{'B' if i & 1 else 'A'}", "waiting": n}
                for i, n in sorted((self.live.pending() if self.live else {}).items())
            ],
            "transparent": {k: len(v) for k, v in sorted(self.transparent.items()) if v},
            "dropped_stale": sum(ds.dropped_stale for ds in self.data_sets.values()),
            "paging": list(self.paging)[:20],
            "frames": self.frames, "frames_rejected": self.frames_rejected,
            "elements": self.elements, "elements_ignored": self.elements_ignored,
            "elements_blocked": self.elements_blocked,
            "allowed_mecs": (None if self.allowed_mecs is None
                             else sorted(self.allowed_mecs)),
            "errors": self.errors, "last_error": self.last_error,
            "last_frame_at": self.last_frame_at,
            "log": list(self.log)[:60],
        }
