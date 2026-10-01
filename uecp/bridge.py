"""Pushing the live data set at the RDS generator.

The encoder transmits one programme service at a time, which is what a single
RDS output is. MEC 0x28 says one PSN in a data set is the main network service
and the rest are other networks, so:

    live data set's main PSN  ->  the encoder's own PI/PS/RT/PTY/...
    every other PSN           ->  an EON service (groups 14A/14B)

That maps the UECP tree onto the state dict the generator already reads, so the
DSP and scheduler need no changes at all.

Only fields the source has actually sent are written. A service that has never
received a PS leaves the existing value alone rather than blanking the output.
"""
from __future__ import annotations

import json

from .store import ProgrammeService, Store

# AF codes are 87.5 MHz + n * 100 kHz; 205 means "no AF". Codes above the band
# are list-length markers (224 + count) and are not frequencies themselves.
AF_FIRST = 0
AF_LAST = 204

# Slow labelling variants that carry the two group 1A fields (MEC 0x1A)
SLC_VARIANT_ECC = 0      # paging + Extended Country Code; ECC is the low byte
SLC_VARIANT_LIC = 3      # Language Identification Code


def in_house_wanted(state: dict) -> bool:
    """Whether group 6A should be transmitted.

    In-House data belongs to the transmitter site rather than to a programme
    service, so like Clock Time it is one setting for the whole encoder and not
    something held per data set. A UECP source has no way to send it, so it is
    the operator's own switch - the same `en_ih` the internal coder uses, with
    the same channel, date and station identifier behind it.
    """
    return bool(state.get("en_ih"))


CT_FROM_SOURCE = "source"     # only when the source has sent a time (default)
CT_FROM_LOCAL = "local"      # this encoder's own clock, as the internal coder does
CT_OFF = "off"               # never, whatever the source sends
CT_MODES = (CT_FROM_SOURCE, CT_FROM_LOCAL, CT_OFF)


def clock_time_wanted(store: Store, state: dict) -> bool:
    """Whether group 4A should be transmitted.

    Clock Time is not a data set's property - there is one clock and it applies
    to the whole encoder - so this is a global setting rather than something
    held per DSN.

    By default CT follows the source: the encoder's clock is not the source's,
    and a time with nothing behind it is exactly the sort of invented content
    UECP mode must not transmit. An operator whose source sends no clock can
    set it to run from this machine's clock instead, which is what the internal
    coder does, or turn it off outright when a source is sending a time that is
    wrong.
    """
    mode = str(state.get("uecp_ct_mode", CT_FROM_SOURCE) or CT_FROM_SOURCE).strip().lower()
    if mode == CT_FROM_LOCAL:
        return True
    if mode == CT_OFF:
        return False
    return bool(store.ct_on and store.clock.valid)


def sequence_overrides(state: dict) -> dict:
    """The operator's per-data-set group sequences, {"<dsn>": "0A 2A ..."}."""
    try:
        value = json.loads(state.get("uecp_group_sequence_by_dsn") or "{}")
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def slots_for_depth(depth: int) -> int:
    """How many slots per cycle a raw-group queue of this depth should get."""
    if depth >= 32:
        return 6
    if depth >= 12:
        return 4
    if depth >= 4:
        return 2
    return 1


def af_codes_to_mhz(af: bytes) -> list[str]:
    """Turn raw AF memory into frequencies, skipping counts and terminators."""
    out = []
    for code in af:
        if code == 0:                      # list terminator
            break
        if AF_FIRST < code <= AF_LAST:
            out.append(f"{87.5 + code * 0.1:.1f}")
    return out


# What a programme service is being used for within its data set.
ROLE_MAIN = "main"     # the station itself - its PI, PS and RadioText go to air
ROLE_EON = "eon"       # an other network, carried in groups 14A and 14B
ROLE_OFF = "off"       # present in the tree, not transmitted
ROLES = (ROLE_MAIN, ROLE_EON, ROLE_OFF)


def local_services(state: dict, dsn: int) -> dict:
    """The operator's own entries for this data set, keyed by PSN.

    A UECP source describes EON by sending the other networks as further PSNs
    in the data set, with MEC 0x28 naming which one is the main service. Plenty
    of sources never do - the one this was written against sends a single
    service - so the same structure can be filled in here instead: one entry per
    PSN, each marked as the main service, as an other network, or as neither.

    Entries are additions and overrides. A data set with none behaves exactly as
    before: whatever the source called the main service is the main service, and
    its other PSNs are the other networks.
    """
    try:
        table = json.loads(state.get("uecp_local_services") or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(table, dict):
        return {}
    out = {}
    for row in table.get(str(dsn)) or []:
        if not isinstance(row, dict):
            continue
        try:
            psn = int(row.get("psn", 0) or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= psn <= 255:
            out[psn] = row
    return out


def local_main_psn(state: dict, dsn: int) -> int | None:
    """The PSN the operator has marked as this data set's main service."""
    for psn, row in sorted(local_services(state, dsn).items()):
        if str(row.get("role", "")).lower() == ROLE_MAIN:
            return psn
    return None


def _linkage_word(link: dict) -> int:
    """Pack linkage information the way group 14A variant 12 carries it.

    Bit 15 is the Linkage Actuator, bit 14 the Extended Generic indicator,
    bit 12 the International Linkage Set, and the low twelve bits the Linkage
    Set Number.
    """
    try:
        lsn = int(str(link.get("lsn", "0") or "0"), 16) & 0x0FFF
    except ValueError:
        lsn = 0
    return (((1 if link.get("la") else 0) << 15)
            | ((1 if link.get("eg") else 0) << 14)
            | ((1 if link.get("ils") else 0) << 12)
            | lsn)


def _service_from_row(row: dict) -> dict:
    """One hand-entered other network, in the shape the EON code expects."""
    entry = {
        "pi_on": str(row.get("pi", "")).strip().upper(),
        "ps": str(row.get("ps", "")).ljust(8)[:8],
        "pty": int(row.get("pty", 0) or 0) & 0x1F,
        "tp": 1 if row.get("tp") else 0,
        "ta": 1 if row.get("ta") else 0,
        "af_list": str(row.get("af_list", "") or ""),
        "mapped_freqs": list(row.get("mapped_freqs") or []),
        "uecp_psn": int(row.get("psn", 0) or 0),
        "uecp_origin": "set here",
    }
    link = row.get("linkage")
    if isinstance(link, dict):
        entry["linkage"] = _linkage_word(link)
    if row.get("en_pin"):
        entry.update({
            "en_pin": 1,
            "pin_day": int(row.get("pin_day", 0) or 0) & 0x1F,
            "pin_hour": int(row.get("pin_hour", 0) or 0) & 0x1F,
            "pin_minute": int(row.get("pin_minute", 0) or 0) & 0x3F,
        })
    return entry


def _programme_service_from_row(row: dict) -> ProgrammeService:
    """A programme service built from an operator's own entry.

    Used when a PSN has been marked as the main service but the source has
    never sent anything for it, so there is nothing in the store to use.
    """
    svc = ProgrammeService(int(row.get("psn", 0) or 0))
    pi = str(row.get("pi", "")).strip().upper()
    if pi:
        try:
            svc.pi = int(pi, 16)
        except ValueError:
            svc.pi = None
    ps = str(row.get("ps", ""))
    if ps.strip():
        svc.ps = ps.ljust(8)[:8]
    svc.pty = int(row.get("pty", 0) or 0) & 0x1F
    svc.tp = bool(row.get("tp"))
    svc.ta = bool(row.get("ta"))
    if row.get("en_pin"):
        svc.pin = (((int(row.get("pin_day", 0) or 0) & 0x1F) << 11)
                   | ((int(row.get("pin_hour", 0) or 0) & 0x1F) << 6)
                   | (int(row.get("pin_minute", 0) or 0) & 0x3F))
    svc.touch()
    return svc


def main_service(ds, state: dict):
    """The service whose data goes to air as the station itself.

    Returns it with its PSN and, when it was built here rather than received,
    the operator's row it came from - the caller needs that for the AF list,
    which a source sends as raw memory and an operator types as frequencies.
    """
    chosen = local_main_psn(state, ds.dsn)
    if chosen is None:
        return ds.main, ds.main_psn, None
    received = ds.services.get(chosen)
    if received is not None:
        return received, chosen, None            # the source's own data wins
    row = local_services(state, ds.dsn).get(chosen)
    if row is None:
        return ds.main, ds.main_psn, None
    return _programme_service_from_row(row), chosen, row


def eon_services(store: Store, ds, state: dict) -> list:
    """Every other network for this data set.

    A PSN the source describes wins over a hand-entered copy of the same one,
    so switching a source on does not leave a stale duplicate on air beside it.
    An entry with no PI is skipped: group 14A cannot do without one, and the
    alternative is transmitting 0000.
    """
    local = local_services(state, ds.dsn)
    _, main_psn, _row = main_service(ds, state)

    services, taken = [], set()
    for psn, svc in sorted(ds.services.items()):
        if psn == main_psn or not svc.enabled or svc.pi is None:
            continue
        # A PSN the source sent is an other network unless the operator has
        # said otherwise for that PSN.
        row = local.get(psn, {})
        if str(row.get("role", ROLE_EON)).lower() != ROLE_EON:
            continue
        if not row.get("enabled", True):
            continue
        services.append(_eon_service(svc))
        taken.add(psn)

    for psn, row in sorted(local.items()):
        if psn in taken or psn == main_psn:
            continue
        if str(row.get("role", "")).lower() != ROLE_EON:
            continue
        if not row.get("enabled", True):
            continue
        entry = _service_from_row(row)
        if entry["pi_on"]:
            services.append(entry)
    return services


def _eon_service(svc: ProgrammeService) -> dict:
    """One other-network PSN in the shape the EON code already expects."""
    entry = {
        "pi_on": svc.pi_hex or "0000",
        "ps": (svc.ps or "").ljust(8)[:8],
        "pty": svc.pty or 0,
        "tp": 1 if svc.tp else 0,
        "ta": 1 if svc.ta else 0,
        "af_list": ", ".join(af_codes_to_mhz(svc.af)),
        "mapped_freqs": [],
        "uecp_psn": svc.psn,
        "uecp_origin": "from the source",
    }
    if svc.pin:
        entry.update({
            "en_pin": 1,
            "pin_day": (svc.pin >> 11) & 0x1F,
            "pin_hour": (svc.pin >> 6) & 0x1F,
            "pin_minute": svc.pin & 0x3F,
        })
    return entry


def apply_to_state(store: Store, state: dict) -> list[str]:
    """Copy the live data set into the generator's state dict.

    Returns a short list of what changed, for logging. Safe to call often - it
    only writes fields whose value actually differs.
    """
    changed = []

    def put(key, value):
        if value is None:
            return
        if state.get(key) != value:
            state[key] = value
            changed.append(key)

    # Silence every internal-coder extra first, before looking at what has been
    # received. This has to happen even with an empty store: a profile whose
    # source has never connected must not fall back on the built-in ECC, CT,
    # Long PS and the rest, which is exactly what it used to do.
    for flag in ("en_lps", "en_ptyn", "en_ert",
                 "en_ert_rtplus", "en_dab", "en_rds2", "en_ari", "en_tdc_5a",
                 "en_tdc_5b", "en_fast_tuning", "en_paging", "en_rt_plus",
                 "en_id", "en_ecc", "en_lic", "en_ct", "en_pin", "en_af",
                 "en_eon"):
        put(flag, 0)
    put("rt_cr", False)
    put("rt_centered", False)
    put("scheduler_auto", False)

    ds = store.live
    main, main_psn, main_row = (main_service(ds, state) if ds is not None
                                else (None, None, None))
    if ds is None or main is None:
        # Nothing received at all: PS only, and whatever PS the profile holds.
        manual = str(sequence_overrides(state).get(str(store.current), "") or "").strip()
        put("group_sequence", manual or "0A")
        put("uecp_group_sequence", manual)
        state["uecp_sequence_origin"] = "set here" if manual else "automatic"
        state["uecp_sequence_dsn"] = store.current
        # CT is the encoder's, not a data set's, so an operator who has asked
        # for it still gets it even before any source has connected.
        put("en_ct", 1 if clock_time_wanted(store, state) else 0)
        if in_house_wanted(state):
            put("group_sequence", manual or "0A 6A 0A")
        return changed

    if main.pi is not None:
        put("pi", main.pi_hex)
    if main.ps is not None:
        put("ps_dynamic", main.ps.rstrip())
    # What actually goes out. The decoded strings above drive the UI; these are
    # the source's own bytes, transmitted unchanged so nothing is lost to a
    # decode/re-encode round trip.
    put("uecp_ps_raw", main.ps_raw.hex().upper() if main.ps_raw else "")
    put("uecp_ptyn_raw", main.ptyn_raw.hex().upper() if main.ptyn_raw else "")
    put("uecp_long_ps_raw", main.long_ps_raw.hex().upper() if main.long_ps_raw else "")
    if main.pty is not None:
        put("pty", main.pty)
    if main.ptyn is not None:
        put("ptyn", main.ptyn.rstrip())
    if main.long_ps is not None:
        put("ps_long_32", main.long_ps)
    # These two are in the suppressed-by-default list above, so they come back
    # on only for a source that actually sends them.
    put("en_ptyn", 1 if main.ptyn else 0)
    put("en_lps", 1 if main.long_ps else 0)

    put("ta", 1 if main.ta else 0)
    put("tp", 1 if main.tp else 0)
    put("ms", 1 if main.ms else 0)
    put("di_stereo", 1 if main.di & 0x01 else 0)
    put("di_head", 1 if main.di & 0x02 else 0)
    put("di_comp", 1 if main.di & 0x04 else 0)
    put("di_dyn", 1 if main.di & 0x08 else 0)

    if main.pin is not None:
        put("en_pin", 1 if main.pin else 0)
        put("pin_day", (main.pin >> 11) & 0x1F)
        put("pin_hour", (main.pin >> 6) & 0x1F)
        put("pin_minute", main.pin & 0x3F)

    # RadioText: the generator drives its own message list, so hand it one entry
    # per RT message the source has queued.
    if main.rt:
        messages = [{
            "id": f"uecp_{i}",
            "enabled": True,
            "source_type": "manual",
            "content": m.text,
            "buffer": "AB",
            "cycles": m.transmissions or 2,
            "rt_plus_enabled": False,
        } for i, m in enumerate(main.rt)]
        put("rt_messages", json.dumps(messages))
        put("rt_text", main.rt[0].text)
        # The generator transmits these bytes as they stand: RadioText from a
        # UECP source is already RDS-encoded and already carries whatever
        # terminator the source wanted.
        put("uecp_rt_raw", json.dumps([
            {"hex": m.raw.hex().upper(), "ab": 1 if main.rt_ab else 0,
             "transmissions": m.transmissions}
            for m in main.rt if m.raw
        ]))
    else:
        # No RadioText from the source means none goes out. Leaving the
        # profile's own text in place would put the internal coder's default
        # on air the moment a 2A slot came round.
        put("uecp_rt_raw", "[]")
        put("rt_text", "")
        put("rt_messages", "[]")

    # Fields the operator has set for whichever service is the station. These
    # apply whether or not the source also sends that PSN - the source has no
    # way to send some of them, and where it does its own value is used.
    station_row = local_services(state, ds.dsn).get(main_psn) or {}

    freqs = af_codes_to_mhz(main.af)
    typed_af = str(station_row.get("af_list", "") or "").strip()
    if freqs:
        put("af_list", ", ".join(freqs))
        put("en_af", 1)
    elif typed_af:
        put("af_list", typed_af)
        put("en_af", 1)
    else:
        put("en_af", 0)

    if main.ptyn is None:
        typed_ptyn = str(station_row.get("ptyn", "") or "").strip()
        if typed_ptyn:
            put("ptyn", typed_ptyn)
            put("en_ptyn", 1)

    # Linkage: what the source sent by MEC 0x2E if it sent any, otherwise what
    # was set here. It rides in group 1A as the Linkage Actuator bit and in
    # group 14A variant 12 as the whole word.
    if main.linkage is not None:
        put("linkage_word", main.linkage & 0xFFFF)
    else:
        link = station_row.get("linkage")
        put("linkage_word", _linkage_word(link) if isinstance(link, dict) else 0)

    # Other networks: whatever the source describes as further PSNs, plus any
    # the operator has entered for this data set. The list is written every time
    # rather than only when non-empty, so removing the last one really removes
    # it instead of leaving the previous set on air.
    services = eon_services(store, ds, state)
    put("eon_services", json.dumps(services))
    put("en_eon", 1 if services else 0)

    # --- Free-format, TMC and ODA groups -----------------------------------
    # Raw groups are NOT copied into state. The generator takes them straight
    # off the store's queues as each slot comes up, which is the only way a
    # stream like TMC - several groups a second, each with its own transmission
    # count - can be paced correctly. Copying them through a JSON field here
    # would fight the generator over what has already been sent.
    put("custom_groups", "[]")

    # --- ODA announcements -------------------------------------------------
    # Only ODAs the source registered properly (MEC 0x40/0x46). One it sends as
    # a raw 3A group is already going out as that group; generating a second
    # announcement for it would duplicate it on air.
    # Field names match what the 3A generator reads: an application group type
    # code, not a group name, and "msg" rather than "message".
    odas = [{
        "enabled": True,
        "aid": f"{app.aid:04X}",
        "group_type": app.group_index,
        "msg": f"{app.message:04X}",
    } for app in sorted(store.oda.values(), key=lambda a: a.aid)
      if app.group_index and app.announced_by != "raw 3A group"]
    put("custom_oda_list", json.dumps(odas) if odas else "[]")

    # --- Group sequence ----------------------------------------------------
    # In UECP mode the encoder must transmit only what the source has given it.
    # Left on its own the internal scheduler adds its own optional groups - 4A,
    # 6A, 10A, 15A and so on - from this profile's flags, which is not what a
    # UECP source asked for. So the sequence is always explicit here: the
    # source's own (MEC 0x16) when it sent one, otherwise one built from what it
    # has actually provided.
    # A sequence sent by the source is written straight into the operator's own
    # box, replacing whatever was there. That keeps one visible source of truth:
    # what the box says is what goes out, whether a person or the source put it
    # there. An empty box means automatic.
    # MEC 0x16 is addressed to a data set, so every DSN has its own sequence -
    # and so does every manual override. They are kept per DSN and the live
    # one is mirrored into uecp_group_sequence for the box in the UI, so
    # switching data set switches the sequence with it.
    key = str(ds.dsn)
    overrides = sequence_overrides(state)
    if ds.group_sequence:
        seq = " ".join(f"{(b >> 1) & 0x0F}{'B' if b & 1 else 'A'}" for b in ds.group_sequence)
        origin = "from the source (MEC 0x16)"
        overrides[key] = seq                  # the source replaces what was set
    else:
        manual = str(overrides.get(key, "") or "").strip()
        if manual:
            seq, origin = manual, "set here"
        else:
            seq, origin = derive_sequence(store, ds, state), "automatic"
    put("uecp_group_sequence_by_dsn", json.dumps(overrides, sort_keys=True))
    put("uecp_group_sequence", overrides.get(key, ""))
    put("group_sequence", seq)
    put("scheduler_auto", False)
    state["uecp_sequence_origin"] = origin
    state["uecp_sequence_dsn"] = ds.dsn

    put("en_ct", 1 if clock_time_wanted(store, state) else 0)

    # Group 1A carries ECC and LIC, and both arrive as slow labelling codes:
    # variant 0's low byte is the Extended Country Code, variant 3's is the
    # Language Identification Code. Without them the encoder would fall back on
    # its own built-in E3/09, which is somebody else's country entirely - so 1A
    # is only enabled once the source has actually sent one.
    # They gate separately: a source that has sent variant 0 but not yet
    # variant 3 must transmit its ECC and no LIC at all, rather than pairing a
    # real ECC with the encoder's built-in language code.
    ecc = ds.slc.get(SLC_VARIANT_ECC)
    lic = ds.slc.get(SLC_VARIANT_LIC)
    if ecc is not None:
        put("ecc", f"{ecc & 0xFF:02X}")
    if lic is not None:
        put("lic", f"{lic & 0xFF:02X}")
    put("en_ecc", 1 if ecc is not None else 0)
    put("en_lic", 1 if lic is not None else 0)
    put("en_id", 1 if (ecc is not None or lic is not None) else 0)
    put("en_pin", 1 if main.pin else 0)

    # RadioText goes out exactly as received. The internal coder appends a 0x0D
    # terminator to a short RT; a UECP source has already decided what its text
    # is, so adding to it would change what the operator sent.
    put("rt_cr", False)
    put("rt_centered", False)
    return changed


def derive_sequence(store, ds, state=None) -> str:
    """A group sequence matching what the source has actually sent.

    Used when the source sends no MEC 0x16 and no manual override is set. Only
    groups we hold real data for are included, so a UECP profile with nothing
    received transmits 0A and nothing else - no ECC, no CT, no RadioText.
    """
    main = ds.main
    seq = ["0A"]                       # PS always; everything else must be earned
    if state is not None and in_house_wanted(state):
        seq += ["6A", "0A"]            # unlike 4A, in-house is sent from its slot
    if main is not None and main.rt:
        seq += ["2A", "0A"]
    if ds.slc or (main is not None and main.pin is not None):
        seq += ["1A", "0A"]
    if main is not None and main.ptyn:
        seq += ["10A", "0A"]
    if main is not None and main.long_ps:
        seq += ["15A", "0A"]
    if state is not None and eon_services(store, ds, state):
        seq += ["14A", "0A"]
    # No 4A here. Clock Time is sent by the generator on the minute boundary,
    # pre-empting the schedule, because a 4A carrying a time that is not aligned
    # to second zero would set receiver clocks wrong. A 4A slot in the sequence
    # would only ever be filled with PS.
    # Every group type with raw data waiting gets slots, and a backed-up queue
    # gets more of them. TMC and RT+ both arrive faster than one slot per cycle
    # can carry, and a queue that only grows puts minutes-old data on air. The
    # depth bands are coarse on purpose: the sequence is rebuilt whenever it
    # changes, so it should settle rather than twitch on every message.
    for index, depth in sorted(ds.in_use().items()):
        name = f"{(index >> 1) & 0x0F}{'B' if index & 1 else 'A'}"
        for _ in range(slots_for_depth(depth)):
            seq += [name, "0A"]
    if any(a.group_index and a.announced_by != "raw 3A group"
           for a in store.oda.values()):
        seq += ["3A", "0A"]
    return " ".join(seq)
