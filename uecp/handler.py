"""Applying UECP message elements to the store.

One function per command family, dispatched from a table. Every handler is told
which data sets and services the element targets - the addressing work is done
in store.py, so the handlers only deal with the payload.

An element for a command we recognise but do not act on is counted as ignored
rather than silently dropped, so the monitor can show that something arrived and
went nowhere.
"""
from __future__ import annotations

import time

from .element import Element, ElementError, split
from .frame import Frame
from .store import (MAX_AF, MAX_LONG_PS, MAX_PAGING, MAX_PS_LEN, MAX_RT_LEN,
                    MAX_TRANSPARENT, DataSet, FreeFormatGroup, OdaApplication,
                    ProgrammeService, Store, RtMessage, _rds_text)

# The AID every Alert-C TMC service registers, and the group ISO/TS 14819-1
# recommends for it. Used only as a fallback: if the source has announced a
# different group for this AID, that is the one TMC goes out on.
TMC_AID = 0xCD46
TMC_DEFAULT_GROUP = 0x10          # (8 << 1) | 0  ->  8A
ODA_ANNOUNCE_GROUP = 0x06         # (3 << 1) | 0  ->  3A

# RT buffer configuration, MEC 0x0A MED bits 6..5
RT_ONCE = 0b00
RT_CYCLIC = 0b10
RT_CLEAR = 0b11


# ---------------------------------------------------------------------------
# Per-service commands
# ---------------------------------------------------------------------------

def _pi(svc: ProgrammeService, d: bytes) -> str:
    svc.pi = (d[0] << 8) | d[1]
    return f"PI {svc.pi_hex}"


def _ps(svc: ProgrammeService, d: bytes) -> str:
    svc.ps_raw = bytes(d[:MAX_PS_LEN]).ljust(MAX_PS_LEN, b" ")
    svc.ps = _rds_text(d[:MAX_PS_LEN])
    return f"PS {svc.ps!r}"


def _pty(svc: ProgrammeService, d: bytes) -> str:
    svc.pty = d[0] & 0x1F
    return f"PTY {svc.pty}"


def _ta_tp(svc: ProgrammeService, d: bytes) -> str:
    svc.ta = bool(d[0] & 0x01)          # bit 0 TA, bit 1 TP
    svc.tp = bool(d[0] & 0x02)
    return f"TA={int(svc.ta)} TP={int(svc.tp)}"


def _ms(svc: ProgrammeService, d: bytes) -> str:
    svc.ms = bool(d[0] & 0x01)
    return f"M/S {'music' if svc.ms else 'speech'}"


def _di(svc: ProgrammeService, d: bytes) -> str:
    svc.di = d[0] & 0x0F                # stereo / artificial head / compressed / dynamic PTY
    return f"DI 0x{svc.di:X}"


def _pin(svc: ProgrammeService, d: bytes) -> str:
    svc.pin = (d[0] << 8) | d[1]
    day, hour, minute = svc.pin >> 11, (svc.pin >> 6) & 0x1F, svc.pin & 0x3F
    return f"PIN day {day} {hour:02d}:{minute:02d}"


def _ptyn(svc: ProgrammeService, d: bytes) -> str:
    svc.ptyn_raw = bytes(d[:8]).ljust(8, b" ")
    svc.ptyn = _rds_text(d[:8])
    return f"PTYN {svc.ptyn!r}"


def _linkage(svc: ProgrammeService, d: bytes) -> str:
    svc.linkage = (d[0] << 8) | d[1]
    return f"linkage 0x{svc.linkage:04X}"


def _eon_elements(svc: ProgrammeService, d: bytes) -> str:
    svc.eon_elements = (d[0] << 8) | d[1]
    return f"EON elements 0x{svc.eon_elements:04X}"


def _rt(svc: ProgrammeService, d: bytes) -> str:
    """MEC 0x0A. First MED byte is configuration, the rest is the text.

    bit 0    toggle the A/B flag
    bits 4-1 number of transmissions (0 = indefinitely)
    bits 6-5 buffer configuration: once / cyclic append / clear
    """
    if not d:
        svc.rt.clear()
        return "RT buffer emptied (MEL=0)"
    cfg = d[0]
    raw = bytes(d[1:1 + MAX_RT_LEN])
    text = _rds_text(raw).rstrip()
    buffer_cfg = (cfg >> 5) & 0x03
    transmissions = (cfg >> 1) & 0x0F
    toggle = bool(cfg & 0x01)

    if buffer_cfg == RT_CLEAR:
        svc.rt.clear()
        return "RT buffer cleared"
    if buffer_cfg != RT_CYCLIC:
        svc.rt.clear()              # "transmitted once only and removed"
    if not text:
        return "RT buffer emptied"
    svc.rt.append(RtMessage(text, transmissions, toggle, raw=raw))
    if toggle:
        svc.rt_ab = not svc.rt_ab
    return f"RT {text!r}" + (" (appended)" if buffer_cfg == RT_CYCLIC else "")


def _long_ps(svc: ProgrammeService, d: bytes) -> str:
    """MEC 0x21 - vendor extension, not in SPB 490 v7.05. MEL is the text length.

    Sources send this in the RDS character set with a 0x0D terminator, exactly
    like RadioText - not as UTF-8, which is what this used to assume.
    """
    svc.long_ps_raw = bytes(d[:MAX_LONG_PS])
    svc.long_ps = _rds_text(svc.long_ps_raw)
    return f"Long PS {svc.long_ps!r}"


def _af(svc: ProgrammeService, d: bytes) -> str:
    """MEC 0x13. Two start-location bytes, then AF codes written at that offset.

    0xFFFF means append at the first 0x00 terminator. A 0x00 in the data ends the
    list. The spec requires the PSN be a main service; we store it wherever it is
    addressed and let the bridge decide what to transmit.
    """
    if len(d) < 2:
        return "AF ignored (no start location)"
    start = (d[0] << 8) | d[1]
    codes = d[2:]
    if start == 0xFFFF:
        pos = svc.af.find(b"\x00")
        start = len(svc.af) if pos < 0 else pos
    if start > MAX_AF:
        return f"AF ignored (start location {start} out of range)"
    if len(svc.af) < start:
        svc.af.extend(b"\x00" * (start - len(svc.af)))
    svc.af[start:start + len(codes)] = codes
    del svc.af[MAX_AF:]
    return f"AF {len(codes)} code(s) at {start}"


def _eon_af(svc: ProgrammeService, d: bytes) -> str:
    if len(d) < 2:
        return "EON-AF ignored (no start location)"
    svc.eon_af = bytearray(d[2:])
    return f"EON-AF {len(svc.eon_af)} byte(s)"


SERVICE_HANDLERS = {
    0x01: _pi, 0x02: _ps, 0x03: _ta_tp, 0x04: _di, 0x05: _ms, 0x06: _pin,
    0x07: _pty, 0x0A: _rt, 0x13: _af, 0x14: _eon_af, 0x21: _long_ps,
    0x2E: _linkage, 0x3E: _ptyn, 0x3F: _eon_elements,
}


# ---------------------------------------------------------------------------
# Per-data-set commands
# ---------------------------------------------------------------------------

def _slc(ds: DataSet, d: bytes) -> str:
    """MEC 0x1A: variant in bits 6..4 of the first byte, 12 bits of data."""
    variant = (d[0] >> 4) & 0x07
    value = ((d[0] & 0x0F) << 8) | d[1]
    ds.slc[variant] = value
    return f"SLC variant {variant} = 0x{value:03X}"


def _group_sequence(ds: DataSet, d: bytes) -> str:
    ds.group_sequence = list(d)
    pretty = ", ".join(f"{(b >> 1) & 0x0F}{'B' if b & 1 else 'A'}" for b in d)
    return f"group sequence: {pretty}"


def _ext_group_sequence(ds: DataSet, d: bytes) -> str:
    if len(d) < 2:
        return "extended group sequence ignored (too short)"
    ds.ext_group_sequence[d[0]] = list(d[1:])
    target = f"{(d[0] >> 1) & 0x0F}{'B' if d[0] & 1 else 'A'}"
    return f"extended group sequence for {target}: {len(d) - 1} alternative(s)"


def _variant_sequence(ds: DataSet, d: bytes) -> str:
    if len(d) < 2:
        return "variant sequence ignored (too short)"
    ds.variant_sequence[d[0]] = list(d[1:])
    group = f"{(d[0] >> 1) & 0x0F}{'B' if d[0] & 1 else 'A'}"
    return f"variant sequence for {group}: {list(d[1:])}"


def _make_psn_list(ds: DataSet, d: bytes) -> str:
    """MEC 0x28: first PSN is the main service, the rest are EON other networks.

    The spec says this deletes everything already in the data set, so it really
    does rebuild the service list from scratch.
    """
    if not d:
        return "make PSN list ignored (empty)"
    ds.services.clear()
    ds.main_psn = d[0]
    for psn in d:
        ds.service(psn).enabled = True
    others = ", ".join(str(p) for p in d[1:]) or "none"
    return f"PSN list rebuilt: main {d[0]}, EON services {others}"


def _psn_enable(ds: DataSet, d: bytes) -> str:
    """MEC 0x0B: pairs of (enable flag, PSN). The main service cannot be disabled."""
    changed = []
    for i in range(0, len(d) - 1, 2):
        enable, psn = bool(d[i] & 0x01), d[i + 1]
        if psn == ds.main_psn and not enable:
            changed.append(f"PSN {psn} refused (main service)")
            continue
        ds.service(psn).enabled = enable
        changed.append(f"PSN {psn} {'enabled' if enable else 'disabled'}")
    return "; ".join(changed) or "PSN enable/disable ignored (no pairs)"


def _group_name(index: int) -> str:
    return f"{(index >> 1) & 0x0F}{'B' if index & 1 else 'A'}"


def _free_format(ds: DataSet, d: bytes) -> str:
    """MEC 0x24: a complete raw group to transmit.

    MED1 bits 4..1 group type, bit 0 version
    MED2 bits 6..5 buffer configuration, bits 4..0 block 2's own 5 bits
    MED3-4 block 3, MED5-6 block 4

    Buffer configuration 0b11 clears the queue for that group instead of adding.
    This is how RT+ usually arrives when a source does not use ODA commands.
    """
    if len(d) < 6:
        return "free-format group ignored (short)"
    index = d[0] & 0x1F
    cfg = (d[1] >> 5) & 0x03
    b2_tail = d[1] & 0x1F
    b3 = (d[2] << 8) | d[3]
    b4 = (d[4] << 8) | d[5]
    name = _group_name(index)
    if cfg == 0b11:
        ds.clear_group(index)
        return f"free-format {name} buffer cleared"
    depth = ds.queue_group(FreeFormatGroup(index, b2_tail, b3, b4,
                                           cyclic=(cfg == 0b10), transmissions=1,
                                           source="MEC 0x24"))
    mode = "cyclic" if cfg == 0b10 else "once"
    return (f"free-format {name} queued ({mode}), "
            f"B2={b2_tail:02X} B3={b3:04X} B4={b4:04X}, {depth} waiting")


def _oda_free_format(ds: DataSet, d: bytes) -> str:
    """MEC 0x42: same shape as 0x24 but carrying only the message bits."""
    return _free_format(ds, d)


def _oda_group_usage(ds: DataSet, d: bytes) -> str:
    """MEC 0x41: which groups an ODA may use, in order."""
    groups = ", ".join(_group_name(b & 0x1F) for b in d)
    return f"ODA group usage sequence: {groups or 'empty'}"


DATA_SET_HANDLERS = {
    0x1A: _slc, 0x16: _group_sequence, 0x38: _ext_group_sequence,
    0x29: _variant_sequence, 0x28: _make_psn_list, 0x0B: _psn_enable,
    0x24: _free_format, 0x42: _oda_free_format, 0x41: _oda_group_usage,
}


# ---------------------------------------------------------------------------
# Global commands (no DSN/PSN)
# ---------------------------------------------------------------------------

def _clock(store: Store, d: bytes) -> str:
    """MEC 0x0D. Year is two digits from 2000; the last byte is the local offset."""
    if len(d) < 8:
        return "clock ignored (too short)"
    c = store.clock
    c.year = 2000 + d[0] if d[0] < 100 else d[0]
    c.month, c.day, c.hour, c.minute, c.second, c.centisecond = d[1], d[2], d[3], d[4], d[5], d[6]
    if d[7] != 0xFF:                      # 0xFF = leave the current offset alone
        c.offset = d[7] & 0x3F
    c.received_at = time.time()
    return f"clock {c}"


def _data_set_select(store: Store, d: bytes) -> str:
    """MEC 0x1C: put a data set on air."""
    dsn = d[0]
    if not 1 <= dsn <= 253:
        return f"data set select ignored (DSN {dsn} out of range)"
    store.current = dsn
    store.data_set(dsn)
    return f"data set {dsn} selected"


def _rds_on_off(store: Store, d: bytes) -> str:
    store.rds_on = bool(d[0] & 0x01)
    return f"RDS output {'on' if store.rds_on else 'off'}"


def _ct_on_off(store: Store, d: bytes) -> str:
    store.ct_on = bool(d[0] & 0x01)
    return f"CT {'on' if store.ct_on else 'off'}"


def _rds_level(store: Store, d: bytes) -> str:
    store.rds_level = (d[0] << 8) | d[1]
    return f"RDS level {store.rds_level} mV"


def _rds_phase(store: Store, d: bytes) -> str:
    store.rds_phase = (d[0] << 8) | d[1]
    return f"RDS phase {store.rds_phase}"


def _site_address(store: Store, d: bytes) -> str:
    return f"site address command ({len(d)} byte(s)) - not applied"


def _encoder_address(store: Store, d: bytes) -> str:
    return f"encoder address command ({len(d)} byte(s)) - not applied"


def _oda_config(store: Store, d: bytes) -> str:
    """MEC 0x40: register an ODA - which group type carries this AID.

    Fixed 7 bytes: app group type, AID, buffer config, message bits, timeout.
    App group type 0x1F is the spec's "data input has timed out" signal and 0x00
    means the AID has no associated group.
    """
    if len(d) < 7:
        return "ODA configuration ignored (short)"
    group_index = d[0] & 0x1F
    aid = (d[1] << 8) | d[2]
    cfg = d[3] & 0x03
    message = (d[4] << 8) | d[5]
    timeout = d[6]
    if cfg == 0b11:
        store.oda.pop(aid, None)
        return f"ODA AID {aid:04X} removed"
    app = store.oda.setdefault(aid, OdaApplication(aid))
    app.group_index = group_index
    app.message = message
    app.timeout = timeout
    app.updated_at = time.time()
    where = "no group" if group_index == 0 else _group_name(group_index)
    return (f"ODA AID {aid:04X} on {where}, message {message:04X}"
            + (f", timeout {timeout} min" if timeout else ""))


def _oda_data(store: Store, d: bytes) -> str:
    """MEC 0x46: actual ODA payload, queued onto whichever group the AID uses.

    Three layouts, told apart by length: 5 = short message (3A message bits),
    8 = a full version A group, 6 = a version B group.
    """
    if len(d) < 5:
        return "ODA data ignored (short)"
    aid = (d[0] << 8) | d[1]
    cfg = d[2]
    app = store.oda.get(aid)
    if app is None or app.group_index == 0:
        return f"ODA data for AID {aid:04X} held (no group registered yet)"

    ds = store.live
    if ds is None:
        return f"ODA data for AID {aid:04X} dropped (no live data set)"

    if cfg & 0x40:                       # short message flag
        app.message = (d[3] << 8) | d[4]
        return f"ODA AID {aid:04X} short message {app.message:04X}"

    buffer_cfg = cfg & 0x03
    if buffer_cfg == 0b11:
        ds.free_format.pop(app.group_index, None)
        return f"ODA AID {aid:04X} buffer cleared"

    if len(d) >= 8:                      # version A: block 2 bits, block 3, block 4
        b2_tail, b3, b4 = d[3] & 0x1F, (d[4] << 8) | d[5], (d[6] << 8) | d[7]
    else:                                # version B: block 2 bits, block 4
        b2_tail, b3, b4 = d[3] & 0x1F, 0, (d[4] << 8) | d[5]
    depth = ds.queue_group(FreeFormatGroup(
        app.group_index, b2_tail, b3, b4,
        cyclic=(buffer_cfg == 0b10), source=f"ODA {aid:04X}"))
    return (f"ODA AID {aid:04X} data on {app.group_name} "
            f"(B2={b2_tail:02X} B3={b3:04X} B4={b4:04X}), {depth} waiting")


def _oda_priority(store: Store, d: bytes) -> str:
    return f"ODA relative priority sequence, {len(d)} entr(ies)"


def _oda_burst(store: Store, d: bytes) -> str:
    return f"ODA burst mode control ({d.hex().upper() or 'no data'})"


def _oda_spinning(store: Store, d: bytes) -> str:
    return f"ODA spinning wheel timing ({d.hex().upper() or 'no data'})"


def _oda_access(store: Store, d: bytes) -> str:
    if len(d) >= 2:
        return f"ODA access right for AID {(d[0] << 8) | d[1]:04X}"
    return "ODA access right"


def _transparent(kind: str):
    """TDC / IH / EWS / TMC all queue an opaque payload for a channel."""
    def handler(store: Store, d: bytes) -> str:
        bucket = store.transparent.setdefault(kind, [])
        channel = d[0] if d else 0
        bucket.append({"at": time.time(), "channel": channel,
                       "data": d[1:].hex().upper()})
        del bucket[:-MAX_TRANSPARENT]
        return f"{kind} channel {channel}, {max(0, len(d) - 1)} byte(s) queued"
    return handler


def _tmc(store: Store, d: bytes) -> str:
    """MEC 0x30: TMC message sets, each one type 8A group's worth of bits.

    MED1  bit 7     0 = normal, 1 = "extremely urgent"
          bits 6-5  buffer configuration
          bits 4-1  number of transmissions, 1-15
          bit 0     zero
    then five bytes per message set: block 2's own 5 bits, block 3, block 4.
    Several sets may arrive in one element, which is why the spec calls it
    "multiples of 37 bits".

    The sets are queued as raw groups on whichever group the source announced
    for the TMC AID, so they go out unchanged - this encoder has no Alert-C
    encoder of its own and does not need one.
    """
    if not d:
        return "TMC ignored (no data)"
    # MEC 0x30 carries no DSN field, so it applies to every data set.
    targets = store.matching_sets(None)
    if not targets:
        return "TMC ignored (no data set)"

    urgent = bool(d[0] & 0x80)
    cfg = (d[0] >> 5) & 0x03
    repeats = ((d[0] >> 1) & 0x0F) or 1
    index = store.oda_group(TMC_AID) or TMC_DEFAULT_GROUP
    name = _group_name(index)

    if cfg == 0b11:
        for ds in targets:
            ds.clear_group(index)
        return f"TMC buffer cleared ({name})"

    body = d[1:]
    sets = len(body) // 5
    if not sets:
        return f"TMC ignored (short: {len(body)} byte(s), need 5 per message)"

    depth = 0
    for ds in targets:
        for i in range(sets):
            b = body[i * 5:(i + 1) * 5]
            depth = ds.queue_group(FreeFormatGroup(
                index, b[0] & 0x1F, (b[1] << 8) | b[2], (b[3] << 8) | b[4],
                cyclic=(cfg == 0b10), transmissions=repeats, urgent=urgent,
                source="MEC 0x30 (TMC)"))

    # Keep the raw payload for the monitor as well, so an operator can see the
    # Alert-C data arriving even though the encoder does not interpret it.
    bucket = store.transparent.setdefault("TMC", [])
    bucket.append({"at": time.time(), "channel": 0, "data": d.hex().upper()})
    del bucket[:-MAX_TRANSPARENT]

    mode = "cyclic" if cfg == 0b10 else f"{repeats}x"
    return (f"TMC {sets} message set(s) queued on {name} ({mode}"
            f"{', urgent' if urgent else ''}), {depth} waiting")


def _learn_oda(store: Store, d: bytes) -> None:
    """Note the ODA table a source sends as raw 3A groups.

    Plenty of sources never use MEC 0x40 and simply push their 3A groups
    through the free-format command. Reading them back gives the monitor a real
    ODA list, and tells the TMC handler which group to use.
    """
    if len(d) < 6 or (d[0] & 0x1F) != ODA_ANNOUNCE_GROUP:
        return
    aid = (d[4] << 8) | d[5]
    if not aid:
        return
    app = store.oda.get(aid)
    if app is None:
        app = OdaApplication(aid)
        store.oda[aid] = app
    app.group_index = d[1] & 0x1F
    app.message = (d[2] << 8) | d[3]
    app.updated_at = time.time()
    app.announced_by = "raw 3A group"


def _paging(label: str):
    """Paging and EPP calls: decoded and kept for the monitor.

    Transmitting these needs the group 7A paging machinery, which this encoder
    does not have. They are decoded and shown rather than silently dropped, so a
    source sending paging can at least see it arriving.
    """
    def handler(store: Store, d: bytes) -> str:
        entry = {"at": time.time(), "kind": label, "data": d.hex().upper()}
        if len(d) >= 3:
            entry["address"] = f"{(d[0] << 8) | d[1]:04X}"
        store.paging.insert(0, entry)
        del store.paging[MAX_PAGING:]
        return f"{label}: {len(d)} byte(s) decoded (not transmitted - no paging encoder)"
    return handler


def _clock_correction(store: Store, d: bytes) -> str:
    if not d:
        return "clock correction ignored (no data)"
    return f"clock correction {d[0]} (applied to the next CT group)"


def _ta_control(store: Store, d: bytes) -> str:
    """MEC 0x2A: switch TA on the main service of a data set."""
    if len(d) < 2:
        return "TA control ignored (short)"
    dsn, on = d[0], bool(d[1] & 0x01)
    for ds in store.matching_sets(dsn):
        main = ds.main
        if main is not None:
            main.ta = on
            main.touch()
    return f"TA control: DSN {dsn} TA {'on' if on else 'off'}"


def _eon_ta_control(store: Store, d: bytes) -> str:
    """MEC 0x15: switch TA on an other-network service."""
    if len(d) < 2:
        return "EON-TA control ignored (short)"
    psn, on = d[0], bool(d[1] & 0x01)
    ds = store.live
    if ds is not None:
        svc = ds.service(psn)
        svc.ta = on
        svc.touch()
    return f"EON-TA control: PSN {psn} TA {'on' if on else 'off'}"


def _reference_select(store: Store, d: bytes) -> str:
    return f"reference input {d[0] if d else '?'} selected"


def _comms_mode(store: Store, d: bytes) -> str:
    return f"communication mode {d[0] if d else '?'}"


def _manufacturer(store: Store, d: bytes) -> str:
    return f"manufacturer specific command, {len(d)} byte(s)"


def _encoder_access(store: Store, d: bytes) -> str:
    return f"encoder access right, {len(d)} byte(s)"


def _port_config(what: str):
    def handler(store: Store, d: bytes) -> str:
        return f"comms port {what}: {d.hex().upper() or 'no data'}"
    return handler


def _request(store: Store, d: bytes) -> str:
    """MEC 0x17: the source is asking us for something. We are receive-only."""
    wanted = f"0x{d[0]:02X}" if d else "?"
    return f"request for {wanted} - this encoder is receive-only, no reply sent"


def _ack(store: Store, d: bytes) -> str:
    return f"message acknowledgment ({d.hex().upper() or 'no data'})"


def _dab_dl(store: Store, d: bytes) -> str:
    text = _rds_text(d[1:]) if len(d) > 1 else ""
    return f"DAB dynamic label: {text!r}" if text else "DAB dynamic label command"


def _rass(store: Store, d: bytes) -> str:
    return f"Rass radio screen show, {len(d)} byte(s)"


GLOBAL_HANDLERS = {
    0x0D: _clock, 0x1C: _data_set_select, 0x1E: _rds_on_off, 0x19: _ct_on_off,
    0x0E: _rds_level, 0x22: _rds_phase, 0x23: _site_address, 0x27: _encoder_address,

    # ODA
    0x40: _oda_config, 0x46: _oda_data, 0x43: _oda_priority, 0x44: _oda_burst,
    0x45: _oda_spinning, 0x47: _oda_access,

    # Transparent data channels
    0x26: _transparent("TDC"), 0x25: _transparent("IH"),
    0x2B: _transparent("EWS"), 0x30: _tmc,

    # Paging and EPP
    0x0C: _paging("paging call"), 0x08: _paging("paging, numeric (10 digits)"),
    0x20: _paging("paging, numeric (18 digits)"),
    0x1B: _paging("paging, alphanumeric"),
    0x11: _paging("international paging, numeric"),
    0x10: _paging("international paging, functions"),
    0x12: _paging("transmitter network group designation"),
    0x31: _paging("EPP transmitter information"), 0x32: _paging("EPP call"),
    0x33: _paging("EPP alphanumeric call"), 0x34: _paging("EPP numeric call"),
    0x35: _paging("EPP functions call"),

    # Remaining control and setup
    0x09: _clock_correction, 0x2A: _ta_control, 0x15: _eon_ta_control,
    0x1D: _reference_select, 0x2C: _comms_mode, 0x2D: _manufacturer,
    0x3A: _encoder_access, 0x3B: _port_config("mode"), 0x3C: _port_config("speed"),
    0x3D: _port_config("timeout"), 0x17: _request, 0x18: _ack,
    0x48: _dab_dl, 0xAA: _dab_dl, 0xDA: _rass,
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def apply_element(store: Store, el: Element, allowed=None, link: str = "") -> None:
    """Apply one message element to the store.

    `allowed` is the set of commands the connection it arrived on may use, or
    None for all of them. Hardware coders set these per port, so a studio link
    can be allowed to set RadioText while a remote one cannot touch the PI.
    """
    store.elements += 1

    if allowed is None:
        allowed = store.allowed_mecs
    # A command that is not allowed is counted and logged rather than dropped
    # silently, so it is obvious from the monitor why a source's PS is missing.
    if allowed is not None and el.mec not in allowed:
        store.elements_blocked += 1
        where = f" on {link}" if link else ""
        store.note(f"{el.name}: not allowed{where} "
                   f"(MEC 0x{el.mec:02X} is blocked here)")
        return

    handler = GLOBAL_HANDLERS.get(el.mec)
    if handler is not None:
        try:
            store.note(handler(store, el.data))
        except (IndexError, ValueError) as exc:
            store.errors += 1
            store.note(f"{el.name}: {exc}")
        return

    ds_handler = DATA_SET_HANDLERS.get(el.mec)
    if ds_handler is not None:
        targets = store.matching_sets(el.dsn)
        if not targets:
            store.elements_ignored += 1
            store.note(f"{el.name}: no data set matches DSN {el.dsn}")
            return
        for ds in targets:
            try:
                store.note(f"DSN {ds.dsn}: {ds_handler(ds, el.data)}")
            except (IndexError, ValueError) as exc:
                store.errors += 1
                store.note(f"{el.name} on DSN {ds.dsn}: {exc}")
        if el.mec in (0x24, 0x42):
            _learn_oda(store, el.data)
        return

    svc_handler = SERVICE_HANDLERS.get(el.mec)
    if svc_handler is not None:
        targets = store.matching_sets(el.dsn)
        if not targets:
            store.elements_ignored += 1
            store.note(f"{el.name}: no data set matches DSN {el.dsn}")
            return
        for ds in targets:
            for svc in store.matching_services(ds, el.psn):
                try:
                    detail = svc_handler(svc, el.data)
                    svc.touch()
                    store.note(f"DSN {ds.dsn} PSN {svc.psn}: {detail}")
                except (IndexError, ValueError) as exc:
                    store.errors += 1
                    store.note(f"{el.name} on DSN {ds.dsn} PSN {svc.psn}: {exc}")
        return

    # Every MEC in the table has a handler above, so reaching here means the
    # command is not in the table at all.
    store.elements_ignored += 1
    store.note(f"{el.name}: no handler for MEC 0x{el.mec:02X}")


def apply_frame(store: Store, frame: Frame, allowed=None, link: str = "") -> int:
    """Apply every element in a frame. Returns how many were applied.

    The frame's address is checked first: a frame for another encoder is counted
    as rejected and goes no further. `allowed` and `link` describe the
    connection it arrived on, for per-connection access rights.
    """
    if not store.address.accepts(frame.address):
        store.frames_rejected += 1
        store.note(f"frame from {frame.address} ignored (we are {store.address})")
        return 0

    store.frames += 1
    store.last_frame_at = time.time()

    applied = 0
    for item in split(frame.message):
        if isinstance(item, ElementError):
            store.errors += 1
            store.last_error = str(item)
            store.note(f"malformed element: {item}")
            break
        apply_element(store, item, allowed, link)
        applied += 1
    return applied
