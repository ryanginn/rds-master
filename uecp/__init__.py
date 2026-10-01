"""UECP - Universal Encoder Communication Protocol.

An implementation of RDS Forum SPB 490 version 7.05 (February 2010).

Layers, bottom up:

    frame.py     STA/ADD/SQC/MFL/MSG/CRC/STP records, byte stuffing, CRC-16,
                 site/encoder addressing, and a stream reassembler
    mec.py       per-command field layout, taken from the spec's own Format:
                 blocks - which of DSN/PSN/MEL each command carries
    element.py   splitting a frame's message field into addressed elements,
                 plus the DSN/PSN targeting rules from sections 2.3.3/2.3.4
    store.py     the encoder state a source drives: data sets, programme
                 services, group sequences, slow labelling codes, the clock
    handler.py   applying message elements to that store
    bridge.py    putting the live data set on air - main PSN becomes the
                 encoder's own RDS, the other PSNs become EON services
    transport.py TCP listener and WebSocket client feeding the store

Typical use:

    reader = FrameReader()
    for item in reader.feed(sock.recv(4096)):
        if isinstance(item, UecpError):
            log.warning("bad frame: %s", item)
            continue
        if not me.accepts(item.address):
            continue
        for el in split(item.message):
            if isinstance(el, ElementError):
                log.warning("%s", el)
                break
            ...
"""
from .frame import (STA, STP, ESC, Address, Frame, FrameReader, UecpError,
                    crc16, decode, encode, stuff, unstuff)
from .mec import MEC_TABLE, MecSpec, lookup, name_of
from .element import (DSN_ALL, DSN_ALL_OTHER, DSN_CURRENT, PSN_MAIN, Element,
                      ElementError, build, dsn_targets, psn_targets, split)
from .store import Clock, DataSet, ProgrammeService, RtMessage, Store
from .handler import apply_element, apply_frame
from .bridge import (CT_MODES, af_codes_to_mhz, apply_to_state,
                     ROLES, clock_time_wanted, eon_services, in_house_wanted,
                     local_main_psn, local_services, main_service,
                     sequence_overrides)
from .transport import TcpListener, WebSocketClient

__all__ = [
    "STA", "STP", "ESC", "Address", "Frame", "FrameReader", "UecpError",
    "crc16", "decode", "encode", "stuff", "unstuff",
    "MEC_TABLE", "MecSpec", "lookup", "name_of",
    "DSN_ALL", "DSN_ALL_OTHER", "DSN_CURRENT", "PSN_MAIN", "Element",
    "ElementError", "build", "dsn_targets", "psn_targets", "split",
    "Clock", "DataSet", "ProgrammeService", "RtMessage", "Store",
    "apply_element", "apply_frame", "af_codes_to_mhz", "apply_to_state",
    "sequence_overrides", "CT_MODES", "clock_time_wanted",
    "in_house_wanted", "eon_services", "local_services",
    "local_main_psn", "main_service", "ROLES",
    "TcpListener", "WebSocketClient",
]
