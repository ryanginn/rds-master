"""UECP Message Element Code table.

Derived from RDS Forum SPB 490 version 7.05 (February 2010), section 3 -
every command's own Format: block gives which optional fields it carries and,
for fixed-length commands, exactly how many MED bytes follow.

Field layout of one message element (spec section 2.3.1):
    MEC [DSN] [PSN] [MEL] [MED...]

DSN, PSN and MEL are each present only for the commands that declare them, so a
decoder cannot walk the element stream generically - it has to know, per MEC,
what shape to expect. That is what this table is for.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MecSpec:
    """How to parse one message element."""
    code: int
    name: str
    cls: str          # rds | oda | transparent | paging | clock | adjust |
                      # control | bidirectional | specific
    dsn: bool         # a Data Set Number byte follows the MEC
    psn: bool         # a Programme Service Number byte follows the DSN
    variable: bool    # a MEL byte gives the data length
    length: int       # fixed MED byte count when not variable
    note: str = ""

    @property
    def header_len(self) -> int:
        """Bytes before the data: MEC plus whichever optional fields apply."""
        return 1 + int(self.dsn) + int(self.psn) + int(self.variable)


MEC_TABLE: dict[int, MecSpec] = {
    0x01: MecSpec(0x01, 'PI', 'rds', True, True, False, 2),
    0x02: MecSpec(0x02, 'PS', 'rds', True, True, False, 8),
    0x03: MecSpec(0x03, 'TA/TP', 'rds', True, True, False, 1),
    0x04: MecSpec(0x04, 'DI/PTYI', 'rds', True, True, False, 1),
    0x05: MecSpec(0x05, 'MS', 'rds', True, True, False, 1),
    0x06: MecSpec(0x06, 'PIN', 'rds', True, True, False, 2),
    0x07: MecSpec(0x07, 'PTY', 'rds', True, True, False, 1),
    0x08: MecSpec(0x08, 'Paging call, numeric message (10 digits)', 'paging', False, False, False, 8),
    0x09: MecSpec(0x09, 'Real time clock correction', 'clock', False, False, False, 2),
    0x0A: MecSpec(0x0A, 'RT', 'rds', True, True, True, 0, 'Some encoders set MEL to the text length only, omitting the MED config byte; the decoder tolerates one trailing byte beyond MEL for this reason.'),
    0x0B: MecSpec(0x0B, 'PSN enable/disable', 'control', True, False, True, 0),
    0x0C: MecSpec(0x0C, 'Paging call without message', 'paging', False, False, False, 3),
    0x0D: MecSpec(0x0D, 'Real time clock', 'clock', False, False, False, 8, 'Carries no DSN/PSN - it is a single global clock set, not addressed to a data set.'),
    0x0E: MecSpec(0x0E, 'RDS level', 'adjust', False, False, False, 2),
    0x10: MecSpec(0x10, 'International paging, functions message', 'paging', False, False, False, 8),
    0x11: MecSpec(0x11, 'International paging, numeric message (15 digits)', 'paging', False, False, False, 7),
    0x12: MecSpec(0x12, 'Transmitter network group designation', 'paging', True, False, False, 1),
    0x13: MecSpec(0x13, 'AF', 'rds', True, True, True, 0),
    0x14: MecSpec(0x14, 'EON-AF', 'rds', True, True, True, 0),
    0x15: MecSpec(0x15, 'EON-TA control', 'control', False, False, False, 2),
    0x16: MecSpec(0x16, 'Group sequence', 'control', True, False, True, 0),
    0x17: MecSpec(0x17, 'Request message', 'bidirectional', False, False, True, 0),
    0x18: MecSpec(0x18, 'Message acknowledgment', 'bidirectional', False, False, False, 2),
    0x19: MecSpec(0x19, 'CT on/off', 'clock', False, False, False, 1),
    0x1A: MecSpec(0x1A, 'Slow labelling codes', 'rds', True, False, False, 2, 'DSN-addressed but has no PSN. Variant code is in bits 6..4 of the first data byte.'),
    0x1B: MecSpec(0x1B, 'Paging call, alphanumeric message (80 chars)', 'paging', False, False, True, 0),
    0x1C: MecSpec(0x1C, 'Data set select', 'control', False, False, False, 1),
    0x1D: MecSpec(0x1D, 'Reference input select', 'control', False, False, False, 1),
    0x1E: MecSpec(0x1E, 'RDS on/off', 'adjust', False, False, False, 1),
    0x20: MecSpec(0x20, 'Paging call, numeric message (18 digits)', 'paging', False, False, False, 8),
    0x22: MecSpec(0x22, 'RDS phase', 'adjust', False, False, False, 2),
    0x23: MecSpec(0x23, 'Site address', 'control', False, False, False, 3),
    0x24: MecSpec(0x24, 'Free-format group', 'transparent', False, False, False, 6, 'Fixed 6 bytes: group type/version, buffer config + block 2 (5 bits), blocks 3 and 4.'),
    0x25: MecSpec(0x25, 'IH', 'transparent', False, False, False, 6),
    0x26: MecSpec(0x26, 'TDC', 'transparent', False, False, True, 0),
    0x27: MecSpec(0x27, 'Encoder address', 'control', False, False, False, 2),
    0x28: MecSpec(0x28, 'Make PSN list', 'control', True, False, True, 0),
    0x29: MecSpec(0x29, 'Group variant code sequence', 'control', True, False, True, 0),
    0x2A: MecSpec(0x2A, 'TA control', 'control', False, False, False, 2),
    0x2B: MecSpec(0x2B, 'EWS', 'transparent', False, False, False, 5),
    0x2C: MecSpec(0x2C, 'Communication mode', 'control', False, False, False, 1),
    0x2D: MecSpec(0x2D, 'Manufacturer specific command', 'specific', False, False, True, 0),
    0x2E: MecSpec(0x2E, 'Linkage information', 'rds', True, True, False, 2),
    0x30: MecSpec(0x30, 'TMC', 'transparent', False, False, True, 0),
    0x31: MecSpec(0x31, 'EPP transmitter information', 'paging', True, False, False, 5),
    0x32: MecSpec(0x32, 'EPP call without message', 'paging', False, False, False, 4),
    0x33: MecSpec(0x33, 'EPP call, alphanumeric message', 'paging', False, False, True, 0),
    0x34: MecSpec(0x34, 'EPP call, variable numeric message', 'paging', False, False, True, 0),
    0x35: MecSpec(0x35, 'EPP call, variable functions message', 'paging', False, False, True, 0),
    0x38: MecSpec(0x38, 'Extended group sequence', 'control', True, False, True, 0),
    0x3A: MecSpec(0x3A, 'Encoder access right', 'control', False, False, False, 3),
    0x3B: MecSpec(0x3B, 'Comms port configuration - mode', 'control', False, False, False, 2),
    0x3C: MecSpec(0x3C, 'Comms port configuration - speed', 'control', False, False, False, 2),
    0x3D: MecSpec(0x3D, 'Comms port configuration - timeout', 'control', False, False, False, 2),
    0x3E: MecSpec(0x3E, 'PTYN', 'rds', True, True, False, 8),
    0x3F: MecSpec(0x3F, 'EON elements enable/disable', 'control', True, True, False, 2),
    0x40: MecSpec(0x40, 'ODA configuration and short message', 'oda', False, False, False, 7, 'Fixed 7 bytes: app group type, AID, buffer config, message, ODA input timeout.'),
    0x41: MecSpec(0x41, 'ODA identification group usage sequence', 'oda', True, False, True, 0),
    0x42: MecSpec(0x42, 'ODA free-format group', 'oda', False, False, False, 7),
    0x43: MecSpec(0x43, 'ODA relative priority group sequence', 'oda', False, False, True, 0),
    0x44: MecSpec(0x44, 'ODA burst mode control', 'oda', False, False, False, 2),
    0x45: MecSpec(0x45, 'ODA spinning wheel timing control', 'oda', False, False, False, 4),
    0x46: MecSpec(0x46, 'ODA data', 'oda', False, False, True, 0, 'Three layouts selected by MEL: 5 = short message, 8 = group type A, 6 = group type B.'),
    0x47: MecSpec(0x47, 'ODA data command access right', 'oda', False, False, False, 4),
    0x48: MecSpec(0x48, 'DAB dynamic label command', 'specific', False, False, True, 0),
    0xAA: MecSpec(0xAA, 'DAB dynamic label message (DL)', 'specific', False, False, True, 0),
    0xDA: MecSpec(0xDA, 'DVB-S Rass radio screen show', 'specific', False, False, True, 0),

    # --- Non-standard extensions -------------------------------------------
    # Long PS appears nowhere in SPB 490 v7.05 (it postdates the spec), but is
    # in use by real encoders and is implemented by the QN8066 reference with
    # the layout below. Accepted here for interoperability, not conformance.
    0x21: MecSpec(0x21, 'Long PS', 'rds', True, True, True, 0,
                  'Not in SPB 490 v7.05 - vendor extension. MEL is exactly the text length, with no leading config byte (unlike RT).'),
}


def lookup(code: int) -> MecSpec | None:
    """Spec for a MEC, or None if we do not know how to parse it."""
    return MEC_TABLE.get(code)


def name_of(code: int) -> str:
    spec = MEC_TABLE.get(code)
    return spec.name if spec else f"unknown (0x{code:02X})"
